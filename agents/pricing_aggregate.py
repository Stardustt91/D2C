"""
Turn a set of normalized observations into an estimate — or into an honest refusal.

Three things happen here that the previous engine did not do at all:

  * Outliers are rejected in LOG space. Prices are multiplicative, not additive: a
    quote at ten times the going rate and one at a tenth are equally suspicious,
    but a linear z-score only flags the first. Log space treats them symmetrically.

  * Citations are verified against the page text before they are published. The
    model no longer gets to assert that a URL supports a number.

  * Confidence is computed from countable properties of the evidence — how many
    independent domains agreed, how tightly, how recently, how well matched — not
    asked of the model, whose self-reported confidence is known to be poorly
    calibrated.

The gate at the end is allowed to answer "I don't know for sure". When it does, it
still hands back the best figure it has — the median of the disagreeing sources, or
the price of the closest comparable item — carrying a status that says exactly how
much that figure is worth. Flagging a weak number is useful; withholding it just
moves the guess to whoever fills the blank.
"""

from __future__ import annotations

import math
import re
import statistics
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

from pricing_fx import today
from pricing_models import (
    ConfidenceBasis,
    CostEstimate,
    EstimateStatus,
    ItemSpec,
    NormalizedObservation,
)

# --- Gate thresholds. Tunable; deliberately in one place. ------------------

#: Minimum well-matched observations before a number may be published.
MIN_QUALIFYING_OBSERVATIONS = 2
#: Minimum independent domains, so one site quoted twice is not "agreement".
MIN_DISTINCT_DOMAINS = 2
#: Above this relative spread the evidence is contradictory rather than noisy,
#: and a median would paper over a real disagreement.
MAX_RELATIVE_SPREAD = 1.50
#: Modified z-score beyond which a log-space value is treated as an outlier.
OUTLIER_Z_THRESHOLD = 3.5
#: Relative gap between the web estimate and internal history worth flagging.
INTERNAL_DISAGREEMENT_THRESHOLD = 0.60


# --------------------------------------------------------------------------
# Citation verification
# --------------------------------------------------------------------------


def _normalize_digits(text: str) -> str:
    """Strip thousands separators so '1,500' and '1 500' both match '1500'."""
    return re.sub(r"(?<=\d)[,\s ](?=\d)", "", text or "")


#: How far a number on the page may sit from the claimed value and still count as
#: the same figure. Covers rounding and formatting differences between the quote
#: and the extraction, but not a genuinely different price.
CITATION_TOLERANCE = 0.005


def verify_citation(observation, page_text: str) -> bool:
    """
    Confirm the page actually contains the number attributed to it.

    Every number on the page is parsed and compared numerically. Substring matching
    is deliberately avoided: searching for "5000" inside raw text also hits "45000"
    and "50007", and any abbreviation shortcut ("5" for 5,000) matches almost
    everything. Comparing parsed values sidesteps that entirely while still
    tolerating thousands separators and trailing decimals.
    """
    if not page_text:
        return False

    value = float(observation.raw_value)
    if value <= 0:
        return False

    haystack = _normalize_digits(page_text)
    for token in re.findall(r"\d+(?:\.\d+)?", haystack):
        try:
            found = float(token)
        except ValueError:
            continue
        if found > 0 and abs(found - value) / value <= CITATION_TOLERANCE:
            return True
    return False


def verify_citations(
    observations: Sequence[NormalizedObservation],
    page_texts: Dict[str, str],
) -> None:
    """Mark each observation according to whether its source page supports it."""
    for norm in observations:
        text = page_texts.get(norm.observation.source_url, "")
        norm.citation_verified = verify_citation(norm.observation, text)


# --------------------------------------------------------------------------
# Robust statistics
# --------------------------------------------------------------------------


def weighted_quantile(pairs: Sequence[Tuple[float, float]], q: float) -> Optional[float]:
    """
    Weighted quantile of (value, weight) pairs, q in [0, 1].

    Used for both the central estimate (q=0.5) and the reported band (0.25/0.75).
    """
    usable = [(v, w) for v, w in pairs if w > 0 and v > 0]
    if not usable:
        return None
    usable.sort(key=lambda pair: pair[0])
    total = sum(w for _, w in usable)
    if total <= 0:
        return None
    target = q * total
    cumulative = 0.0
    for value, weight in usable:
        cumulative += weight
        if cumulative >= target:
            return value
    return usable[-1][0]


def reject_outliers(
    observations: Sequence[NormalizedObservation],
) -> Tuple[List[NormalizedObservation], List[NormalizedObservation]]:
    """
    Split observations into kept and rejected using a log-space modified z-score.

    Below four observations nothing is rejected: with three points there is no
    reliable way to tell an outlier from a small sample, and discarding one of them
    would do more harm than keeping it.
    """
    usable = [o for o in observations if o.is_usable]
    if len(usable) < 4:
        return list(usable), []

    logs = [math.log(o.value_egp) for o in usable]
    median_log = statistics.median(logs)
    deviations = [abs(x - median_log) for x in logs]
    mad = statistics.median(deviations)

    if mad <= 1e-9:
        spread = statistics.pstdev(logs) if len(logs) > 1 else 0.0
        if spread <= 1e-9:
            return list(usable), []
        scores = [abs(x - median_log) / spread for x in logs]
        limit = 3.0
    else:
        scores = [0.6745 * abs(x - median_log) / mad for x in logs]
        limit = OUTLIER_Z_THRESHOLD

    kept, rejected = [], []
    for observation, score in zip(usable, scores):
        (kept if score <= limit else rejected).append(observation)

    # Never reject so much that nothing is left to estimate from.
    if len(kept) < 2:
        return list(usable), []
    return kept, rejected


def relative_spread(pairs: Sequence[Tuple[float, float]]) -> Optional[float]:
    """Interquartile range over the median — dimensionless, so it is comparable
    across parameters priced in wildly different magnitudes."""
    p25 = weighted_quantile(pairs, 0.25)
    p50 = weighted_quantile(pairs, 0.50)
    p75 = weighted_quantile(pairs, 0.75)
    if p25 is None or p50 is None or p75 is None or p50 <= 0:
        return None
    return (p75 - p25) / p50


# --------------------------------------------------------------------------
# Confidence
# --------------------------------------------------------------------------


def build_confidence_basis(
    kept: Sequence[NormalizedObservation],
    all_observations: Sequence[NormalizedObservation],
    spread: Optional[float],
    as_of: Optional[date] = None,
) -> ConfidenceBasis:
    as_of = as_of or today()
    domains = {o.observation.source_domain for o in kept if o.observation.source_domain}
    strong = [o for o in kept if o.match_grade in ("exact", "equivalent")]

    ages = [
        (as_of - o.observation.quote_date).days / 365.25
        for o in kept
        if o.observation.quote_date is not None
    ]

    return ConfidenceBasis(
        n_observations=len(all_observations),
        n_qualifying=len(kept),
        n_exact_or_equivalent=len(strong),
        n_distinct_domains=len(domains),
        n_citations_verified=sum(1 for o in kept if o.citation_verified),
        relative_spread=spread,
        median_age_years=statistics.median(ages) if ages else None,
    )


def derive_confidence(basis: ConfidenceBasis) -> Tuple[str, List[str]]:
    """
    Map measured evidence properties onto a confidence label, with the reasons.

    Every downgrade is explained, so a reviewer can see which specific weakness
    capped the estimate rather than having to trust an adjective.
    """
    notes: List[str] = []

    if basis.n_qualifying == 0:
        return "none", ["no qualifying observations"]

    level = 3  # 3 high, 2 medium, 1 low

    if basis.n_qualifying < 2:
        level = min(level, 1)
        notes.append("only one qualifying observation")
    elif basis.n_qualifying < 4:
        level = min(level, 2)
        notes.append(f"{basis.n_qualifying} qualifying observations; 4 or more supports high confidence")

    if basis.n_distinct_domains < 2:
        level = min(level, 1)
        notes.append("all evidence comes from a single domain")
    elif basis.n_distinct_domains < 3:
        level = min(level, 2)
        notes.append(f"{basis.n_distinct_domains} independent sources")

    if basis.n_exact_or_equivalent == 0:
        level = min(level, 1)
        notes.append("no exact or equivalent item match; estimate rests on analogous items only")
    elif basis.n_exact_or_equivalent < 2:
        level = min(level, 2)
        notes.append("only one exact or equivalent item match")

    if basis.relative_spread is not None:
        if basis.relative_spread > 0.80:
            level = min(level, 1)
            notes.append(f"sources disagree widely (interquartile spread {basis.relative_spread:.0%} of the median)")
        elif basis.relative_spread > 0.35:
            level = min(level, 2)
            notes.append(f"moderate disagreement between sources (spread {basis.relative_spread:.0%})")

    if basis.median_age_years is not None and basis.median_age_years > 3:
        level = min(level, 2)
        notes.append(f"evidence is old (median age {basis.median_age_years:.1f} years) and relies on escalation")

    if basis.n_citations_verified == 0:
        level = min(level, 2)
        notes.append("no citation could be verified against its source page")

    return {3: "high", 2: "medium", 1: "low"}[level], notes


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


def closest_observation(
    observations: Sequence[NormalizedObservation],
    as_of: Optional[date] = None,
) -> Optional[NormalizedObservation]:
    """
    The single observation whose item sits nearest the spec: best match grade first,
    then heaviest weight.

    This is the fallback used when the gate refuses to publish an aggregate. An
    analyst who asked what something costs is better served by the price of the
    closest thing actually found — clearly labelled as that — than by an empty cell
    that also stops every formula above it from evaluating. Observations that failed
    normalization are still excluded: their number is not in the right unit or
    currency and would be wrong rather than merely uncertain.
    """
    candidates = [
        o for o in observations
        if o.normalization_failed is None and o.value_egp and o.value_egp > 0
    ]
    if not candidates:
        return None
    rank = {"exact": 0, "equivalent": 1, "analogous": 2, "unrelated": 3}
    return min(candidates, key=lambda o: (rank.get(o.match_grade, 3), -o.weight(as_of)))


def describe_observation(observation: NormalizedObservation) -> str:
    """One line naming what an observation actually priced, for a caveat or a log."""
    raw = observation.observation
    return (
        f'"{(raw.item_description or "unnamed item").strip()[:120]}" '
        f"({raw.source_domain or 'unknown source'}, {observation.match_grade} match, "
        f"quoted {raw.raw_value:,.0f} {raw.currency} {raw.unit_basis}, "
        f"{raw.quote_date.isoformat() if raw.quote_date else 'undated'})"
    )


def aggregate(
    observations: Sequence[NormalizedObservation],
    spec: ItemSpec,
    queries_used: Optional[List[str]] = None,
    internal_reference_egp: Optional[float] = None,
    extra_warnings: Optional[List[str]] = None,
    as_of: Optional[date] = None,
) -> CostEstimate:
    """
    Produce an estimate, and say how much weight it can bear.

    Three outcomes, and only the first is a settled number:

      * `estimated` — enough qualifying, agreeing evidence to stand behind the
        weighted median.
      * `needs_analyst_input` — plenty of evidence, but it contradicts itself. The
        median is still published, because the middle of a disputed range is a
        better working figure than nothing, and the flag says it is disputed.
      * `insufficient_evidence` — too little survived matching and normalization to
        aggregate. The price of the closest comparable item found is published as a
        placeholder, again with the flag.

    A figure only goes out with no number at all when nothing usable was retrieved.
    """
    as_of = as_of or today()
    warnings: List[str] = list(extra_warnings or [])
    all_observations = list(observations)

    unusable = [o for o in all_observations if not o.is_usable]
    for observation in unusable:
        if observation.normalization_failed:
            warnings.append(
                f"discarded {observation.observation.source_domain or 'a source'}: "
                f"{observation.normalization_failed}"
            )

    kept, rejected = reject_outliers(all_observations)
    for observation in rejected:
        warnings.append(
            f"rejected {observation.value_egp:,.0f} EGP from "
            f"{observation.observation.source_domain or 'an unknown source'} as a statistical outlier"
        )

    pairs = [(o.value_egp, o.weight(as_of)) for o in kept]
    spread = relative_spread(pairs)
    basis = build_confidence_basis(kept, all_observations, spread, as_of)

    def _decline(status: EstimateStatus, message: str) -> CostEstimate:
        """
        Report a gate failure — with the closest comparable price attached when one
        exists, so the gap is visible without being a dead end.
        """
        # Prefer what survived matching and outlier rejection; only when nothing did
        # is the raw pool worth scraping, and then even an item graded unrelated is
        # more informative than a blank — as long as the caveat names what it was.
        fallback = closest_observation(kept or all_observations, as_of)
        citations = _ordered_citations(kept)

        if fallback is None:
            basis.notes = [message]
            return CostEstimate(
                status=status,
                value_egp=None,
                value_basis="none",
                unit=spec.required_unit,
                confidence="none",
                confidence_basis=basis,
                justification=message,
                citations=citations,
                observations=all_observations,
                queries_used=queries_used or [],
                spec=spec,
                internal_reference_egp=internal_reference_egp,
                warnings=warnings,
            )

        placeholder = (
            f"PROVISIONAL — not enough evidence to estimate this properly. "
            f"{fallback.value_egp:,.0f} EGP ({spec.required_unit}) is shown as a placeholder: it is the "
            f"price of the closest comparable item found, {describe_observation(fallback)}. "
            f"Confirm or replace it before the number is used."
        )
        warnings.append(
            f"no defensible estimate; using the closest comparable item "
            f"({fallback.value_egp:,.0f} EGP from {fallback.observation.source_domain or 'an unknown source'}) "
            f"as a placeholder"
        )
        basis.notes = [message, "value taken from the closest comparable item, not from an aggregate"]

        url = fallback.observation.source_url
        if url and url not in citations:
            citations.insert(0, url)

        return CostEstimate(
            status=status,
            value_egp=fallback.value_egp,
            value_basis="closest_match",
            unit=spec.required_unit,
            confidence="low",
            confidence_basis=basis,
            justification=placeholder + "\n\n" + message,
            citations=citations,
            observations=all_observations,
            queries_used=queries_used or [],
            spec=spec,
            internal_reference_egp=internal_reference_egp,
            warnings=warnings,
        )

    strong = [o for o in kept if o.match_grade in ("exact", "equivalent")]
    domains = {o.observation.source_domain for o in kept if o.observation.source_domain}

    if not kept:
        return _decline(
            "insufficient_evidence",
            f"No usable price evidence was found for {spec.describe()}. "
            f"{len(all_observations)} candidate prices were retrieved but none survived item matching "
            f"and unit normalization.",
        )

    if len(kept) < MIN_QUALIFYING_OBSERVATIONS or len(domains) < MIN_DISTINCT_DOMAINS:
        if not strong:
            return _decline(
                "insufficient_evidence",
                f"Only {len(kept)} qualifying observation(s) from {len(domains)} source(s) were found for "
                f"{spec.describe()}, and none is an exact or equivalent match. This is below the "
                f"threshold for publishing a number.",
            )
        warnings.append(
            f"estimate rests on {len(kept)} observation(s) from {len(domains)} source(s) — thin evidence"
        )

    if spread is not None and spread > MAX_RELATIVE_SPREAD:
        # The sources contradict each other, so the median is not something to stand
        # behind — but it is the least-bad single figure available, and withholding
        # it only pushed the same guess onto whoever fills the blank. It goes out
        # with the disagreement stated first, and the status stays 'needs_analyst_input'.
        median = weighted_quantile(pairs, 0.50)
        message = (
            f"Sources disagree too widely to settle on a figure for {spec.describe()}: the "
            f"interquartile range is {spread:.0%} of the median across {len(kept)} observations. "
            f"The candidate values are likely measuring different scopes or specifications."
        )
        if median is None or median <= 0:
            return _decline("needs_analyst_input", message)

        warnings.append(
            f"sources disagree widely (interquartile spread {spread:.0%} of the median); the median is "
            f"shown as a provisional figure and needs analyst review"
        )
        confidence, notes = derive_confidence(basis)
        basis.notes = notes

        return CostEstimate(
            status="needs_analyst_input",
            value_egp=median,
            value_basis="median_of_disagreeing_sources",
            unit=spec.required_unit,
            low_egp=weighted_quantile(pairs, 0.25),
            high_egp=weighted_quantile(pairs, 0.75),
            confidence=confidence,  # type: ignore[arg-type]
            confidence_basis=basis,
            justification=(
                f"NEEDS REVIEW — {message}\n\nThe median of those observations, "
                f"{median:,.0f} EGP ({spec.required_unit}), is shown as a provisional figure so the "
                f"estimate can proceed; check which scope is the right one before relying on it.\n\n"
                + build_justification(median, spec, kept, basis, confidence)
            ),
            citations=_ordered_citations(kept),
            observations=all_observations,
            queries_used=queries_used or [],
            spec=spec,
            internal_reference_egp=internal_reference_egp,
            warnings=warnings,
        )

    value = weighted_quantile(pairs, 0.50)
    if value is None or value <= 0:
        return _decline("insufficient_evidence", "No positive value could be derived from the evidence.")

    confidence, notes = derive_confidence(basis)
    basis.notes = notes

    if internal_reference_egp and internal_reference_egp > 0:
        gap = abs(value - internal_reference_egp) / max(internal_reference_egp, 1e-9)
        if gap > INTERNAL_DISAGREEMENT_THRESHOLD:
            warnings.append(
                f"web evidence ({value:,.0f} EGP) differs from internal historical cost "
                f"({internal_reference_egp:,.0f} EGP) by {gap:.0%} — check for a specification or "
                f"scope mismatch before accepting"
            )

    return CostEstimate(
        status="estimated",
        value_egp=value,
        value_basis="weighted_median",
        unit=spec.required_unit,
        low_egp=weighted_quantile(pairs, 0.25),
        high_egp=weighted_quantile(pairs, 0.75),
        confidence=confidence,  # type: ignore[arg-type]
        confidence_basis=basis,
        justification=build_justification(value, spec, kept, basis, confidence),
        citations=_ordered_citations(kept),
        observations=all_observations,
        queries_used=queries_used or [],
        spec=spec,
        internal_reference_egp=internal_reference_egp,
        warnings=warnings,
    )


def _ordered_citations(kept: Sequence[NormalizedObservation]) -> List[str]:
    """Cited sources, verified ones first, deduplicated, preserving order."""
    ordered = sorted(kept, key=lambda o: (not o.citation_verified, -o.weight()))
    seen: List[str] = []
    for observation in ordered:
        url = observation.observation.source_url
        if url and url not in seen:
            seen.append(url)
    return seen


def build_justification(
    value: float,
    spec: ItemSpec,
    kept: Sequence[NormalizedObservation],
    basis: ConfidenceBasis,
    confidence: str,
) -> str:
    """
    Write the audit trail: how many sources, what they said, what was adjusted.

    Composed from the evidence rather than generated, so it cannot describe a
    derivation that did not happen.
    """
    lines = [
        f"**{value:,.0f} EGP** ({spec.required_unit}), taken as the weighted median of "
        f"{basis.n_qualifying} normalized observation(s) from {basis.n_distinct_domains} independent source(s).",
        "",
        f"Confidence: **{confidence}**." + (f" {' ; '.join(basis.notes)}." if basis.notes else ""),
        "",
        "**Evidence**",
    ]

    for observation in sorted(kept, key=lambda o: -o.weight())[:6]:
        raw = observation.observation
        date_text = raw.quote_date.isoformat() if raw.quote_date else "undated"
        verified = "verified" if observation.citation_verified else "unverified"
        lines.append(
            f"- {observation.value_egp:,.0f} EGP after adjustment "
            f"(quoted {raw.raw_value:,.0f} {raw.currency} {raw.unit_basis}, {date_text}, "
            f"{raw.source_domain or 'unknown source'}, {observation.match_grade} match, {verified})"
        )

    adjustments = [
        f"{a.kind}: {a.reason}"
        for observation in kept
        for a in observation.adjustments
    ]
    if adjustments:
        lines.extend(["", "**Adjustments applied**"])
        for adjustment in _dedupe(adjustments)[:8]:
            lines.append(f"- {adjustment}")

    return "\n".join(lines)


def _dedupe(items: Sequence[str]) -> List[str]:
    seen: List[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen
