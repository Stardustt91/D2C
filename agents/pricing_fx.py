"""
Dated exchange rates and escalation series for Egypt.

Two separate problems get solved here, and conflating them is the mistake the
previous engine made:

  1. CONVERSION — a price quoted in USD today becomes EGP at today's rate.
  2. ESCALATION — a price quoted at some point in the past becomes a present-day
     price. Which series applies depends on what sets the price. An imported
     generator quoted in EGP in 2020 tracks the exchange rate; a local labourer's
     day rate quoted in EGP in 2020 tracks domestic inflation. Over 2022-2024 those
     two series diverged by more than a factor of two, so applying one to both
     would be badly wrong in opposite directions.

The correct treatment for a foreign-currency quote is therefore to escalate it in
its OWN currency (modest, ~2-3%/yr) and then convert at today's rate — not to
convert at the old rate and then apply Egyptian inflation, which double-counts the
devaluation.

Rate history is embedded rather than fetched. Historical FX endpoints are paid on
most providers, and an embedded table is reproducible, auditable and works offline,
which matters more here than the last decimal place. Values are period averages
around the managed-rate regimes and published devaluation dates; they are accurate
to a percent or two, not to the piastre. Override via D2C_FX_TABLE if you have a
better series.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import requests

from env_config import exchangerate_key

# --------------------------------------------------------------------------
# USD/EGP history
# --------------------------------------------------------------------------

# (effective_from, EGP per 1 USD). The pound was managed between devaluations, so
# a step function models it better than interpolation would.
USD_EGP_ANCHORS: List[Tuple[date, float]] = [
    (date(2014, 1, 1), 6.95),
    (date(2015, 1, 1), 7.15),
    (date(2015, 7, 1), 7.73),
    (date(2016, 3, 14), 8.88),   # March 2016 devaluation
    (date(2016, 11, 3), 15.80),  # float
    (date(2017, 1, 1), 18.50),
    (date(2017, 7, 1), 17.90),
    (date(2018, 1, 1), 17.75),
    (date(2019, 1, 1), 17.90),
    (date(2019, 7, 1), 16.60),
    (date(2020, 1, 1), 15.90),
    (date(2021, 1, 1), 15.70),
    (date(2022, 1, 1), 15.70),
    (date(2022, 3, 21), 18.30),  # March 2022 devaluation
    (date(2022, 10, 27), 24.50), # October 2022 devaluation
    (date(2023, 1, 11), 29.70),  # January 2023 devaluation
    (date(2023, 6, 1), 30.90),
    (date(2024, 3, 6), 47.60),   # March 2024 float
    (date(2024, 9, 1), 48.50),
    (date(2025, 1, 1), 50.50),
    (date(2025, 7, 1), 49.50),
    (date(2026, 1, 1), 50.00),
]

# Egypt CPI as an index (2014 = 100), built from annual headline inflation.
# Used to escalate locally-priced items.
_EGYPT_ANNUAL_INFLATION: Dict[int, float] = {
    2015: 0.111,
    2016: 0.102,
    2017: 0.235,
    2018: 0.209,
    2019: 0.139,
    2020: 0.057,
    2021: 0.052,
    2022: 0.139,
    2023: 0.339,
    2024: 0.283,
    2025: 0.150,   # estimate
    2026: 0.120,   # estimate
}

# Inflation in the quote's own currency, for escalating foreign-currency prices.
_FOREIGN_ANNUAL_INFLATION = 0.028

# Used only when the live rate API is unreachable. Logged loudly when it engages,
# because a silently stale rate is worse than a visibly missing one.
FALLBACK_EGP_RATES: Dict[str, float] = {
    "EGP": 1.0,
    "USD": 50.0,
    "EUR": 55.0,
    "GBP": 64.0,
    "SAR": 13.3,
    "AED": 13.6,
    "CNY": 7.0,
    "JPY": 0.33,
}

_LIVE_RATE_TTL_SECONDS = 3600
_rate_cache: Dict[str, Tuple[float, datetime, str]] = {}
_rate_lock = threading.Lock()

#: Empty when no key is configured, in which case the live lookup below is skipped
#: and the embedded fallback table is used instead.
_EXCHANGE_API_KEY = exchangerate_key()


def _load_override_table() -> Optional[List[Tuple[date, float]]]:
    """Allow the embedded FX series to be replaced with a better one via env var."""
    path = os.environ.get("D2C_FX_TABLE")
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        table = [(date.fromisoformat(k), float(v)) for k, v in raw.items()]
        return sorted(table, key=lambda t: t[0])
    except Exception as exc:  # pragma: no cover - operator error path
        print(f"[pricing_fx] could not load D2C_FX_TABLE from {path}: {exc}")
        return None


def egp_per_usd_on(when: date) -> float:
    """EGP per 1 USD on a given date, from the embedded step series."""
    table = _load_override_table() or USD_EGP_ANCHORS
    rate = table[0][1]
    for eff_from, value in table:
        if when >= eff_from:
            rate = value
        else:
            break
    return rate


def egypt_cpi_on(when: date) -> float:
    """
    Egypt CPI index (2014 = 100) on a given date, interpolated within the year so
    that two quotes from the same year are not treated as identical.
    """
    index = 100.0
    for year in sorted(_EGYPT_ANNUAL_INFLATION):
        if year > when.year:
            break
        rate = _EGYPT_ANNUAL_INFLATION[year]
        if year < when.year:
            index *= 1.0 + rate
        else:
            fraction = (when.timetuple().tm_yday - 1) / 365.25
            index *= (1.0 + rate) ** fraction
    return index


def today() -> date:
    return datetime.now(timezone.utc).date()


# --------------------------------------------------------------------------
# Live conversion
# --------------------------------------------------------------------------


def live_rate_to_egp(currency: str) -> Tuple[float, str]:
    """
    Current EGP per 1 unit of `currency`, with the provenance of the rate.

    Returns (rate, note). The note records whether the rate came from the API, a
    process cache, or the hardcoded fallback, and is carried into the adjustment
    record so an estimate can be traced back to the exact rate that produced it.
    """
    code = (currency or "EGP").upper().strip()
    if code in ("EGP", ""):
        return 1.0, "no conversion (already EGP)"

    now = datetime.now(timezone.utc)
    with _rate_lock:
        cached = _rate_cache.get(code)
        if cached and (now - cached[1]).total_seconds() < _LIVE_RATE_TTL_SECONDS:
            return cached[0], cached[2]

    note: str
    rate: Optional[float] = None
    if not _EXCHANGE_API_KEY:
        print("[pricing_fx] EXCHANGERATE_API_KEY not set; using fallback rate table")
    else:
        try:
            url = f"https://v6.exchangerate-api.com/v6/{_EXCHANGE_API_KEY}/pair/{code}/EGP"
            response = requests.get(url, timeout=10)
            data = response.json()
            candidate = data.get("conversion_rate")
            if candidate and float(candidate) > 0:
                rate = float(candidate)
                note = f"live rate {code}/EGP = {rate:.4f} on {now:%Y-%m-%d}"
        except Exception as exc:
            print(f"[pricing_fx] live rate lookup failed for {code}: {exc}")

    if rate is None:
        rate = FALLBACK_EGP_RATES.get(code)
        if rate is None:
            rate = FALLBACK_EGP_RATES["USD"]
            note = (
                f"FALLBACK rate used: {code} is not in the fallback table, substituted USD "
                f"= {rate} EGP. Treat this estimate as unreliable."
            )
            print(f"[pricing_fx] WARNING unknown currency {code}; substituted USD fallback")
        else:
            note = f"FALLBACK rate used: {code}/EGP = {rate} (live API unavailable)"
            print(f"[pricing_fx] WARNING using fallback rate for {code}: {rate}")

    with _rate_lock:
        _rate_cache[code] = (rate, now, note)
    return rate, note


# --------------------------------------------------------------------------
# Escalation
# --------------------------------------------------------------------------


def escalation_factor(
    quote_date: date,
    import_intensity: str = "mixed",
    currency: str = "EGP",
    as_of: Optional[date] = None,
) -> Tuple[float, str]:
    """
    Factor bringing a price quoted on `quote_date` to present value, plus a
    human-readable explanation of which series was used and why.

    For foreign-currency quotes the price is escalated in its own currency, since
    the subsequent conversion at today's rate already captures every devaluation
    that happened in between. Applying Egyptian inflation here as well would count
    the devaluation twice.
    """
    as_of = as_of or today()
    if quote_date >= as_of:
        return 1.0, "quote is current; no escalation applied"

    years = (as_of - quote_date).days / 365.25
    code = (currency or "EGP").upper().strip()

    if code not in ("EGP", ""):
        factor = (1.0 + _FOREIGN_ANNUAL_INFLATION) ** years
        return factor, (
            f"escalated {years:.1f}y in {code} at {_FOREIGN_ANNUAL_INFLATION:.1%}/yr "
            f"(x{factor:.3f}); devaluation is captured by converting at today's rate"
        )

    fx_now = egp_per_usd_on(as_of)
    fx_then = egp_per_usd_on(quote_date)
    fx_factor = fx_now / fx_then if fx_then > 0 else 1.0

    cpi_now = egypt_cpi_on(as_of)
    cpi_then = egypt_cpi_on(quote_date)
    cpi_factor = cpi_now / cpi_then if cpi_then > 0 else 1.0

    if import_intensity == "imported":
        return fx_factor, (
            f"imported item escalated {years:.1f}y by USD/EGP movement "
            f"({fx_then:.2f} to {fx_now:.2f}, x{fx_factor:.3f})"
        )
    if import_intensity == "local":
        return cpi_factor, (
            f"local item escalated {years:.1f}y by Egypt CPI "
            f"({cpi_then:.0f} to {cpi_now:.0f}, x{cpi_factor:.3f})"
        )

    blended = (fx_factor * cpi_factor) ** 0.5
    return blended, (
        f"mixed item escalated {years:.1f}y by the geometric mean of FX (x{fx_factor:.3f}) "
        f"and CPI (x{cpi_factor:.3f}) = x{blended:.3f}"
    )


def looks_like_unconverted_foreign(value_egp: float, plausible_low: float, plausible_high: float) -> Optional[str]:
    """
    Detect the classic unit-error signatures.

    A value that is wrong by almost exactly the USD/EGP rate is nearly always a
    foreign price that never got converted; being wrong by ~12 or ~30 usually means
    an annual or monthly figure landed in a field expecting monthly or daily. These
    three account for most large errors in practice, and they are cheap to spot.
    """
    if value_egp <= 0 or plausible_low <= 0 or plausible_high <= 0:
        return None

    fx = egp_per_usd_on(today())
    for factor, label in ((fx, f"USD/EGP rate ({fx:.0f})"), (12.0, "12 (annual vs monthly)"), (30.0, "30 (monthly vs daily)")):
        if plausible_low <= value_egp / factor <= plausible_high:
            return f"value is about {factor:.0f}x the plausible band — likely an unconverted figure off by {label}"
        if plausible_low <= value_egp * factor <= plausible_high:
            return f"value is about 1/{factor:.0f} of the plausible band — likely a unit conversion applied in the wrong direction ({label})"
    return None
