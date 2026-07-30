"""
Bring raw price observations onto a single comparable basis.

Five independent adjustments are applied in a fixed order, each recorded as an
Adjustment with its factor and reason so the whole chain can be replayed and
audited:

    currency -> vintage -> unit basis -> scope -> quantity

The canonical basis is: Egyptian pounds, present value, the parameter's declared
unit, single-unit quantity, VAT-exclusive.

A deliberate restraint: VAT is adjusted arithmetically because the rate is a known
14%, but installation, warranty and delivery are NOT silently adjusted, because
there is no defensible universal factor for them. They are flagged instead, which
downgrades the observation's weight and surfaces a warning. Inventing a plausible
looking multiplier there would manufacture false precision.
"""

from __future__ import annotations

import re
from datetime import date
from typing import List, Optional, Tuple

from pricing_fx import escalation_factor, live_rate_to_egp, today
from pricing_models import (
    Adjustment,
    ItemSpec,
    NormalizedObservation,
    PriceObservation,
)

EGYPT_VAT_RATE = 0.14

# Length of each pricing period in days. An hour is taken as an eighth of a
# working day, which is the convention the resource planner already uses.
_PERIOD_DAYS = {
    "hour": 1.0 / 8.0,
    "day": 1.0,
    "week": 7.0,
    "month": 30.4375,
    "quarter": 91.3125,
    "year": 365.25,
}

_PERIOD_SYNONYMS = {
    "hour": "hour", "hourly": "hour", "hr": "hour", "ساعة": "hour",
    "day": "day", "daily": "day", "diem": "day", "يوم": "day",
    "week": "week", "weekly": "week", "أسبوع": "week",
    "month": "month", "monthly": "month", "mo": "month", "شهر": "month",
    "quarter": "quarter", "quarterly": "quarter",
    "year": "year", "yearly": "year", "annual": "year", "annum": "year", "pa": "year", "سنة": "year",
}

_ONE_TIME_TOKENS = {
    "one-time", "one time", "onetime", "once", "lump sum", "lumpsum",
    "capex", "purchase", "outright", "each", "per unit", "per item", "per piece", "unit price",
}


class ParsedUnit:
    """A pricing unit broken into the parts that matter for conversion."""

    def __init__(self, raw: str):
        self.raw = raw or ""
        text = self.raw.lower().strip()
        self.one_time = any(tok in text for tok in _ONE_TIME_TOKENS)
        self.period: Optional[str] = None
        for token in re.findall(r"[a-z؀-ۿ]+", text):
            mapped = _PERIOD_SYNONYMS.get(token)
            if mapped:
                self.period = mapped
                # Keep scanning: in "EGP per truck per day" the period is the last
                # time word, and in "annual price per month" the author means month.
        if self.period:
            self.one_time = False

    @property
    def days(self) -> Optional[float]:
        return _PERIOD_DAYS.get(self.period) if self.period else None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ParsedUnit(raw={self.raw!r}, period={self.period}, one_time={self.one_time})"


def parse_unit(raw: str) -> ParsedUnit:
    return ParsedUnit(raw)


def _apply(
    adjustments: List[Adjustment],
    kind: str,
    value: float,
    factor: float,
    reason: str,
) -> float:
    """Apply a factor, recording it. Factors of exactly 1 are not recorded as noise."""
    after = value * factor
    if abs(factor - 1.0) > 1e-9:
        adjustments.append(
            Adjustment(kind=kind, factor=factor, reason=reason, value_before=value, value_after=after)
        )
    return after


def normalize_observation(
    obs: PriceObservation,
    spec: ItemSpec,
    as_of: Optional[date] = None,
) -> NormalizedObservation:
    """
    Convert one observation onto the canonical basis.

    Never raises: an observation that cannot be normalized comes back with
    `normalization_failed` set and is excluded from aggregation rather than
    silently contributing a wrong number.
    """
    as_of = as_of or today()
    adjustments: List[Adjustment] = []
    warnings: List[str] = []
    value = float(obs.raw_value)

    if value <= 0:
        return NormalizedObservation(
            observation=obs,
            value_egp=0.0,
            adjustments=[],
            normalization_failed="quoted value is zero or negative",
        )

    # --- 1. Vintage, in the quote's own currency ---------------------------
    # Done before conversion: a foreign price is escalated in its own currency,
    # then converted at today's rate, so the devaluation is counted exactly once.
    if obs.quote_date is not None:
        factor, reason = escalation_factor(
            obs.quote_date,
            import_intensity=spec.import_intensity,
            currency=obs.currency,
            as_of=as_of,
        )
        value = _apply(adjustments, "vintage", value, factor, reason)
    else:
        warnings.append("source gives no date; no escalation applied and the observation is down-weighted")

    # --- 2. Currency -------------------------------------------------------
    code = (obs.currency or "EGP").upper().strip()
    if code not in ("EGP", ""):
        rate, note = live_rate_to_egp(code)
        if rate <= 0:
            return NormalizedObservation(
                observation=obs,
                value_egp=0.0,
                adjustments=adjustments,
                normalization_failed=f"no usable exchange rate for {code}",
            )
        value = _apply(adjustments, "currency", value, rate, note)

    # --- 3. Unit basis -----------------------------------------------------
    source_unit = parse_unit(obs.unit_basis)
    target_unit = parse_unit(spec.required_unit)

    if source_unit.one_time != target_unit.one_time and (source_unit.period or target_unit.period):
        return NormalizedObservation(
            observation=obs,
            value_egp=0.0,
            adjustments=adjustments,
            normalization_failed=(
                f"cannot convert a {'one-time' if source_unit.one_time else 'recurring'} price "
                f"({obs.unit_basis!r}) to a {'one-time' if target_unit.one_time else 'recurring'} "
                f"unit ({spec.required_unit!r}); these measure different things"
            ),
        )

    if source_unit.period and target_unit.period and source_unit.period != target_unit.period:
        src_days = source_unit.days or 1.0
        tgt_days = target_unit.days or 1.0
        factor = tgt_days / src_days
        value = _apply(
            adjustments,
            "unit",
            value,
            factor,
            f"converted {source_unit.period} rate to {target_unit.period} rate (x{factor:.4f})",
        )
        # Long period to short period crosses a commercial discount boundary:
        # monthly hire rates are cheaper per day than daily hire rates, so a
        # straight division understates the daily cost.
        if src_days > tgt_days * 3:
            warnings.append(
                f"converted a {source_unit.period} rate down to a {target_unit.period} rate; "
                f"longer-period rates are usually discounted, so this may understate the true "
                f"{target_unit.period} price"
            )
    elif target_unit.period and not source_unit.period and not source_unit.one_time:
        warnings.append(
            f"source does not state a pricing period; assumed it is already {target_unit.period}-based"
        )

    # --- 4. Scope ----------------------------------------------------------
    if obs.scope.vat_included is True:
        value = _apply(
            adjustments,
            "scope",
            value,
            1.0 / (1.0 + EGYPT_VAT_RATE),
            f"removed {EGYPT_VAT_RATE:.0%} Egyptian VAT to reach a VAT-exclusive basis",
        )
    elif obs.scope.vat_included is None:
        warnings.append("source does not say whether VAT is included")

    for flag, label in (
        (obs.scope.installation_included, "installation"),
        (obs.scope.warranty_included, "warranty"),
        (obs.scope.delivery_included, "delivery"),
    ):
        if flag is True:
            warnings.append(
                f"quoted price bundles {label}; no deduction applied because there is no defensible "
                f"generic factor, so this observation may sit above a bare-equipment price"
            )

    # --- 5. Quantity tier --------------------------------------------------
    tier = obs.quantity_tier
    if tier and tier > 1:
        value = _apply(
            adjustments,
            "quantity",
            value,
            1.0 / float(tier),
            f"divided a bulk price covering {tier:g} units to reach a single-unit basis",
        )
        warnings.append(
            f"price was quoted for {tier:g} units; bulk rates are usually discounted, so the "
            f"single-unit cost may be higher"
        )

    normalized = NormalizedObservation(
        observation=obs,
        value_egp=value,
        adjustments=adjustments,
    )
    if warnings:
        normalized.match_reason = " | ".join(warnings)
    return normalized


def normalization_warnings(observations: List[NormalizedObservation]) -> List[str]:
    """Collect distinct normalization caveats across a set of observations."""
    seen: List[str] = []
    for obs in observations:
        for part in (obs.match_reason or "").split(" | "):
            part = part.strip()
            if part and part not in seen:
                seen.append(part)
    return seen


def scope_uncertainty_penalty(obs: PriceObservation) -> float:
    """
    Multiplier reflecting how much of a price's scope is unknown.

    An observation whose page states VAT treatment and what is bundled is worth
    more than one that leaves all of it implicit, even when both report the same
    number. This feeds the aggregation weight rather than the value itself.
    """
    unknown = sum(
        1
        for flag in (
            obs.scope.vat_included,
            obs.scope.installation_included,
            obs.scope.warranty_included,
            obs.scope.delivery_included,
        )
        if flag is None
    )
    return 1.0 - 0.08 * unknown
