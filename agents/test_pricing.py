"""
Offline tests for the deterministic parts of the pricing engine.

No network, no LLM, no API keys. Everything exercised here is arithmetic or logic,
which is exactly the part that fails silently in production — a wrong escalation
factor or a broken outlier filter still produces a confident-looking number.

Run with:  python agents/test_pricing.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Point the cache at a throwaway file before pricing_cache resolves its path.
_TEST_DB = Path(tempfile.gettempdir()) / "d2c_price_cache_test.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
os.environ["D2C_PRICE_CACHE"] = str(_TEST_DB)

import pricing_cache  # noqa: E402
from pricing_aggregate import (  # noqa: E402
    aggregate,
    closest_observation,
    derive_confidence,
    build_confidence_basis,
    reject_outliers,
    relative_spread,
    verify_citation,
    weighted_quantile,
)
from pricing_fx import (  # noqa: E402
    egp_per_usd_on,
    egypt_cpi_on,
    escalation_factor,
    looks_like_unconverted_foreign,
)
from pricing_match import (  # noqa: E402
    apply_region_preference,
    deterministic_grade,
    region_signal,
)
from pricing_models import (  # noqa: E402
    ItemSpec,
    NormalizedObservation,
    PriceObservation,
    ScopeFlags,
)
from pricing_normalize import normalize_observation, parse_unit  # noqa: E402

_FAILURES: list = []
_PASSES = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global _PASSES
    if condition:
        _PASSES += 1
    else:
        _FAILURES.append(f"{label}{(' — ' + detail) if detail else ''}")


def approx(a: float, b: float, tolerance: float = 0.02) -> bool:
    return abs(a - b) <= tolerance * max(abs(b), 1e-9)


def spec(**overrides) -> ItemSpec:
    base = dict(
        canonical_name="25 ton mobile crane rental",
        category="heavy equipment hire",
        attributes={"capacity": "25 ton"},
        required_unit="EGP per crane per day",
        import_intensity="mixed",
        volatility_class="medium",
        synonyms=["crane hire", "ونش"],
        exclusions=["50 ton crane", "crane purchase price"],
    )
    base.update(overrides)
    return ItemSpec(**base)


def obs(**overrides) -> PriceObservation:
    base = dict(
        raw_value=5000.0,
        currency="EGP",
        unit_basis="per day",
        item_description="25 ton mobile crane daily hire in Cairo",
        evidence_quote="Daily hire for a 25 ton mobile crane is 5,000 EGP.",
        source_url="https://example-supplier.com.eg/cranes",
        source_type="vendor_quote",
        quote_date=date(2026, 1, 15),
    )
    base.update(overrides)
    return PriceObservation(**base)


# --------------------------------------------------------------------------
# Exchange rates and escalation
# --------------------------------------------------------------------------

def test_fx_history() -> None:
    check(approx(egp_per_usd_on(date(2021, 6, 1)), 15.70), "FX 2021 rate")
    check(approx(egp_per_usd_on(date(2023, 3, 1)), 29.70), "FX after Jan 2023 devaluation")
    check(approx(egp_per_usd_on(date(2024, 6, 1)), 47.60), "FX after Mar 2024 float")
    check(
        egp_per_usd_on(date(2022, 1, 1)) < egp_per_usd_on(date(2022, 12, 1)),
        "FX steps up across the 2022 devaluations",
    )


def test_cpi_monotonic() -> None:
    check(egypt_cpi_on(date(2020, 1, 1)) < egypt_cpi_on(date(2024, 1, 1)), "CPI rises over time")
    check(
        egypt_cpi_on(date(2023, 1, 1)) < egypt_cpi_on(date(2023, 12, 1)),
        "CPI interpolates within a year",
    )


def test_escalation_imported_tracks_fx() -> None:
    factor, reason = escalation_factor(
        date(2021, 1, 1), import_intensity="imported", currency="EGP", as_of=date(2026, 1, 1)
    )
    expected = egp_per_usd_on(date(2026, 1, 1)) / egp_per_usd_on(date(2021, 1, 1))
    check(approx(factor, expected), "imported EGP price escalates by FX", f"got {factor:.3f}, want {expected:.3f}")
    check("USD/EGP" in reason, "imported escalation explains itself")


def test_escalation_local_tracks_cpi() -> None:
    factor, _ = escalation_factor(
        date(2021, 1, 1), import_intensity="local", currency="EGP", as_of=date(2026, 1, 1)
    )
    expected = egypt_cpi_on(date(2026, 1, 1)) / egypt_cpi_on(date(2021, 1, 1))
    check(approx(factor, expected), "local EGP price escalates by CPI", f"got {factor:.3f}")


def test_escalation_diverges_by_intensity() -> None:
    imported, _ = escalation_factor(date(2021, 1, 1), "imported", "EGP", as_of=date(2026, 1, 1))
    local, _ = escalation_factor(date(2021, 1, 1), "local", "EGP", as_of=date(2026, 1, 1))
    check(
        abs(imported - local) > 0.3,
        "imported and local escalation differ materially",
        f"imported x{imported:.2f} vs local x{local:.2f}",
    )


def test_escalation_foreign_currency_is_modest() -> None:
    # A USD price must NOT be escalated by Egyptian inflation: the later conversion
    # at today's rate already carries the devaluation.
    factor, _ = escalation_factor(date(2021, 1, 1), "imported", "USD", as_of=date(2026, 1, 1))
    check(1.0 < factor < 1.25, "USD price escalates only by USD inflation", f"got x{factor:.3f}")


def test_unit_error_signatures() -> None:
    fx = egp_per_usd_on(date(2026, 1, 1))
    check(
        looks_like_unconverted_foreign(5000 * fx, 3000, 8000) is not None,
        "detects an unconverted USD figure",
    )
    check(
        looks_like_unconverted_foreign(5000 * 12, 3000, 8000) is not None,
        "detects an annual figure in a monthly field",
    )
    check(looks_like_unconverted_foreign(5000, 3000, 8000) is None, "clean value raises no signature")


# --------------------------------------------------------------------------
# Unit parsing and normalization
# --------------------------------------------------------------------------

def test_unit_parsing() -> None:
    check(parse_unit("EGP per truck per day").period == "day", "parses trailing period")
    check(parse_unit("EGP per person per month").period == "month", "parses month")
    check(parse_unit("EGP one-time").one_time is True, "recognises one-time")
    check(parse_unit("EGP per year").period == "year", "parses annual")
    check(parse_unit("EGP one-time").period is None, "one-time has no period")


# These three isolate one adjustment each, so the observations are left undated:
# a dated observation would also pick up vintage escalation and the assertion would
# be testing two things at once.

def test_normalize_period_conversion() -> None:
    result = normalize_observation(
        obs(raw_value=150000.0, unit_basis="per month", quote_date=None), spec()
    )
    check(result.normalization_failed is None, "monthly to daily conversion succeeds")
    check(approx(result.value_egp, 150000.0 / 30.4375), "monthly rate converts to daily", f"got {result.value_egp:.0f}")
    check(any(a.kind == "unit" for a in result.adjustments), "unit adjustment recorded")
    check("discounted" in (result.match_reason or ""), "warns that long-period rates are discounted")


def test_normalize_vat_removed() -> None:
    result = normalize_observation(
        obs(raw_value=5700.0, quote_date=None, scope=ScopeFlags(vat_included=True)), spec()
    )
    check(approx(result.value_egp, 5700.0 / 1.14), "VAT stripped to reach VAT-exclusive basis")
    check(any(a.kind == "scope" for a in result.adjustments), "scope adjustment recorded")


def test_normalize_quantity_tier() -> None:
    result = normalize_observation(obs(raw_value=50000.0, quote_date=None, quantity_tier=10), spec())
    check(approx(result.value_egp, 5000.0), "bulk price divided to single unit")
    check("bulk" in (result.match_reason or ""), "warns about bulk discounting")


def test_normalize_rejects_incompatible_units() -> None:
    result = normalize_observation(obs(unit_basis="one-time purchase"), spec())
    check(result.normalization_failed is not None, "one-time price rejected for a per-day unit")
    check(not result.is_usable, "rejected observation is unusable")


def test_normalize_records_full_chain() -> None:
    result = normalize_observation(
        obs(raw_value=6000.0, unit_basis="per month", quote_date=date(2022, 1, 1),
            scope=ScopeFlags(vat_included=True)),
        spec(import_intensity="local"),
    )
    kinds = {a.kind for a in result.adjustments}
    check({"vintage", "unit", "scope"} <= kinds, "vintage, unit and scope all recorded", str(kinds))
    for adjustment in result.adjustments:
        check(bool(adjustment.reason.strip()), f"{adjustment.kind} adjustment carries a reason")


def test_undated_observation_is_flagged_not_escalated() -> None:
    result = normalize_observation(obs(quote_date=None), spec())
    check(not any(a.kind == "vintage" for a in result.adjustments), "undated price is not escalated")
    check("no date" in (result.match_reason or ""), "undated price is flagged")


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------

def test_match_rejects_excluded_item() -> None:
    grade, _ = deterministic_grade(spec(), "50 ton crane daily hire Cairo")
    check(grade == "unrelated", "explicit exclusion rejected", f"got {grade}")


def test_match_rejects_capacity_conflict() -> None:
    grade, reason = deterministic_grade(
        spec(exclusions=[]), "Mobile crane 80 ton for hire per day"
    )
    check(grade == "unrelated", "large capacity conflict rejected", f"got {grade}: {reason}")


def test_match_flags_near_capacity_as_analogous() -> None:
    grade, _ = deterministic_grade(spec(exclusions=[]), "Mobile crane 30 ton daily hire")
    check(grade == "analogous", "nearby capacity graded analogous, not equivalent", f"got {grade}")


def test_match_rejects_unrelated_text() -> None:
    grade, _ = deterministic_grade(spec(), "Office stationery bundle price list")
    check(grade == "unrelated", "unrelated item rejected", f"got {grade}")


def test_region_signal() -> None:
    check(region_signal(spec(), obs(currency="EGP")) == "in_region", "EGP price reads as Egyptian")
    check(
        region_signal(spec(), obs(currency="USD", source_url="https://cranes.com.eg/x")) == "in_region",
        "Egyptian domain reads as Egyptian",
    )
    check(
        region_signal(spec(), obs(currency="USD", item_description="crane hire in Cairo")) == "in_region",
        "explicit Egypt mention reads as Egyptian",
    )
    check(
        region_signal(spec(), obs(currency="USD", source_url="https://bigge.com/rates",
                                  item_description="25 ton crane rental", evidence_quote="300 USD per day"))
        == "foreign",
        "dollar price with no Egyptian tie reads as foreign",
    )
    check(
        region_signal(
            spec(),
            obs(currency="USD", source_url="https://cranes.co.uk/x",
                item_description="25 ton mobile crane daily hire",
                evidence_quote="Daily hire is 300 per day."),
        ) == "foreign",
        "foreign TLD reads as foreign",
    )
    # An explicit Egypt mention outranks a foreign TLD: a UK-registered site with an
    # Egyptian branch page is still quoting an Egyptian price.
    check(
        region_signal(spec(), obs(currency="USD", source_url="https://cranes.co.uk/egypt",
                                  item_description="crane hire Cairo Egypt")) == "in_region",
        "Egypt mention outranks a foreign TLD",
    )


def test_region_preference_excludes_foreign_when_local_exists() -> None:
    local = NormalizedObservation(
        observation=obs(currency="EGP", raw_value=5000, source_url="https://clcegy.com/p"),
        value_egp=5000.0, match_grade="equivalent",
    )
    foreign = NormalizedObservation(
        observation=obs(currency="USD", raw_value=600, source_url="https://bigge.com/p",
                        item_description="25 ton crane rental", evidence_quote="600 USD per day"),
        value_egp=30000.0, match_grade="exact",
    )
    apply_region_preference([local, foreign], spec())
    check(local.match_grade == "equivalent", "local observation untouched")
    check(foreign.match_grade == "unrelated", "foreign excluded when local evidence exists", foreign.match_grade)
    check("different market" in foreign.match_reason, "exclusion reason names the cause")


def test_region_preference_keeps_foreign_when_nothing_local() -> None:
    foreign_a = NormalizedObservation(
        observation=obs(currency="USD", raw_value=600, source_url="https://bigge.com/p",
                        item_description="25 ton crane rental", evidence_quote="600 USD per day"),
        value_egp=30000.0, match_grade="exact",
    )
    foreign_b = NormalizedObservation(
        observation=obs(currency="USD", raw_value=800, source_url="https://riggingforce.com/p",
                        item_description="25 ton crane rental", evidence_quote="800 USD per day"),
        value_egp=40000.0, match_grade="exact",
    )
    apply_region_preference([foreign_a, foreign_b], spec())
    check(foreign_a.match_grade == "analogous", "foreign kept but capped when nothing local", foreign_a.match_grade)
    check("foreign-market benchmark" in foreign_a.match_reason, "capped observation is labelled")


def test_region_preference_survives_end_to_end_in_aggregate() -> None:
    # The failure this guards against: a handful of US quotes outvoting the local
    # ones and moving the estimate to a different market's price level.
    local = [
        NormalizedObservation(
            observation=obs(currency="EGP", raw_value=v, source_url=f"https://s{i}.com.eg/p"),
            value_egp=float(v), match_grade="equivalent", citation_verified=True,
        )
        for i, v in enumerate([5000, 5500, 4800])
    ]
    foreign = [
        NormalizedObservation(
            observation=obs(currency="USD", raw_value=600, source_url=f"https://us{i}.com/p",
                            item_description="crane rental", evidence_quote="600 USD per day"),
            value_egp=30000.0, match_grade="exact", citation_verified=True,
        )
        for i in range(3)
    ]
    everything = local + foreign
    apply_region_preference(everything, spec())
    result = aggregate(everything, spec())
    check(result.status == "estimated", "estimate produced", result.status)
    check(
        result.value_egp is not None and 4500 <= result.value_egp <= 6000,
        "median stays at the Egyptian price level",
        f"got {result.value_egp}",
    )


def test_arabic_description_is_deferred_not_rejected() -> None:
    # Regression: Arabic descriptions used to score zero lexical overlap and be
    # graded "unrelated", discarding exactly the Egyptian pages the Arabic queries
    # were written to surface.
    grade, reason = deterministic_grade(spec(), "ونش رافعة متحركة 25 طن للايجار اليومي في القاهرة")
    check(grade is None, "Arabic description is deferred to the judge, not rejected", f"got {grade}: {reason}")
    check("Arabic" in reason, "deferral reason names the cause")


def test_arabic_description_still_respects_exclusions() -> None:
    grade, _ = deterministic_grade(
        spec(exclusions=["ونش 50 طن"]), "ونش 50 طن للايجار في القاهرة"
    )
    check(grade == "unrelated", "an Arabic description matching an exclusion is still rejected", f"got {grade}")


def test_foreign_tld_outranks_egp_currency() -> None:
    # A Gulf site quoting EGP is more often a mislabelled conversion than a real
    # Egyptian quote; excluding it costs an abstention rather than a wrong number.
    signal = region_signal(
        spec(),
        obs(currency="EGP", source_url="https://jugnootransport.ae/cranes",
            item_description="25 ton crane hire", evidence_quote="7,500 EGP per day"),
    )
    check(signal == "foreign", "foreign TLD outranks an EGP price tag", signal)


def test_match_defers_ambiguous_to_judge() -> None:
    grade, _ = deterministic_grade(spec(attributes={}, exclusions=[]), "Crane services available, rates on request")
    check(grade in (None, "analogous", "unrelated"), "ambiguous case handled without crashing")


# --------------------------------------------------------------------------
# Aggregation, confidence, gate
# --------------------------------------------------------------------------

def normalized(value: float, domain: str, grade: str = "exact", verified: bool = True) -> NormalizedObservation:
    return NormalizedObservation(
        observation=obs(raw_value=value, source_url=f"https://{domain}/page"),
        value_egp=value,
        match_grade=grade,
        citation_verified=verified,
    )


def test_weighted_quantile() -> None:
    pairs = [(100.0, 1.0), (200.0, 1.0), (300.0, 1.0)]
    check(weighted_quantile(pairs, 0.5) == 200.0, "median of three equal-weight values")
    heavy = [(100.0, 10.0), (200.0, 1.0), (300.0, 1.0)]
    check(weighted_quantile(heavy, 0.5) == 100.0, "weighting moves the median")


def test_outlier_rejection_is_symmetric_in_log_space() -> None:
    values = [5000, 5200, 4900, 5100, 5050, 500000]
    kept, rejected = reject_outliers([normalized(v, f"d{i}.com") for i, v in enumerate(values)])
    check(len(rejected) == 1, "high outlier rejected", f"rejected {len(rejected)}")
    check(rejected and rejected[0].value_egp == 500000, "the extreme value is the one rejected")

    low = [5000, 5200, 4900, 5100, 5050, 50]
    kept_low, rejected_low = reject_outliers([normalized(v, f"e{i}.com") for i, v in enumerate(low)])
    check(len(rejected_low) == 1, "low outlier rejected symmetrically", f"rejected {len(rejected_low)}")


def test_small_samples_are_not_trimmed() -> None:
    kept, rejected = reject_outliers([normalized(5000, "a.com"), normalized(9000, "b.com")])
    check(not rejected, "no rejection below four observations")
    check(len(kept) == 2, "both observations kept")


def test_gate_flags_single_source_but_still_prices_it() -> None:
    result = aggregate([normalized(5000, "only.com", grade="analogous")], spec())
    check(result.status == "insufficient_evidence", "one analogous observation stays flagged", result.status)
    check(result.value_egp == 5000.0, "closest comparable item supplies a placeholder", str(result.value_egp))
    check(result.value_basis == "closest_match", "basis records where the number came from", result.value_basis)
    check(result.confidence == "low", "a placeholder never earns more than low confidence", result.confidence)
    check("PROVISIONAL" in result.justification, "justification leads with the caveat")


def test_closest_match_prefers_the_better_graded_item() -> None:
    pool = [
        normalized(9000, "far.com", grade="analogous", verified=True),
        normalized(5200, "near.com", grade="exact"),
        normalized(400, "wrong.com", grade="unrelated"),
    ]
    pick = closest_observation(pool)
    check(pick is not None and pick.value_egp == 5200.0, "exact match wins over a heavier analogous one",
          str(pick.value_egp if pick else None))

    only_weak = [normalized(400, "wrong.com", grade="unrelated"),
                 normalized(7000, "maybe.com", grade="analogous")]
    weak_pick = closest_observation(only_weak)
    check(weak_pick is not None and weak_pick.value_egp == 7000.0, "analogous beats unrelated")

    broken = NormalizedObservation(
        observation=obs(), value_egp=0.0, normalization_failed="incompatible units"
    )
    check(closest_observation([broken]) is None, "an unconvertible price is never used as a placeholder")


def test_thin_evidence_falls_back_to_closest_item() -> None:
    # Two analogous observations from one domain: below the domain threshold and
    # with no strong match, so the gate declines — but a number still comes back.
    thin = [normalized(4800, "same.com", grade="analogous"),
            normalized(5300, "same.com", grade="analogous")]
    result = aggregate(thin, spec())
    check(result.status == "insufficient_evidence", "thin evidence stays flagged", result.status)
    check(result.value_egp in (4800.0, 5300.0), "placeholder is one of the observed prices",
          str(result.value_egp))
    check(
        any("closest comparable item" in w for w in result.warnings),
        "the fallback is called out in the warnings",
    )


def test_gate_abstains_when_nothing_usable() -> None:
    bad = NormalizedObservation(
        observation=obs(), value_egp=0.0, normalization_failed="incompatible units"
    )
    result = aggregate([bad], spec())
    check(result.status == "insufficient_evidence", "abstains when all evidence fails normalization")
    check(result.value_egp is None, "no number produced when normalization failed for everything")
    check(result.value_basis == "none", "basis says there is no number")
    check(any("incompatible units" in w for w in result.warnings), "explains what was discarded")


def test_gate_flags_contradictory_evidence() -> None:
    wide = [normalized(1000, "a.com"), normalized(90000, "b.com"), normalized(3000, "c.com"),
            normalized(120000, "d.com")]
    result = aggregate(wide, spec())
    check(result.status == "needs_analyst_input", "wide disagreement escalates to a human", result.status)
    check(result.value_egp is not None, "median still published so the formula can run")
    check(
        result.value_basis == "median_of_disagreeing_sources",
        "basis marks the number as a disputed median",
        result.value_basis,
    )
    check(
        result.low_egp is not None and result.high_egp is not None
        and result.low_egp <= result.value_egp <= result.high_egp,
        "median sits inside the reported range",
    )
    check(result.confidence == "low", "disputed evidence caps confidence at low", result.confidence)
    check("NEEDS REVIEW" in result.justification, "justification leads with the disagreement")


def test_gate_publishes_on_good_evidence() -> None:
    good = [normalized(5000, "a.com"), normalized(5200, "b.com"), normalized(4900, "c.com"),
            normalized(5100, "d.com")]
    result = aggregate(good, spec())
    check(result.status == "estimated", "publishes when evidence agrees", result.status)
    check(result.value_egp is not None and 4800 <= result.value_egp <= 5300, "median is sensible")
    check(result.confidence == "high", "tight multi-source evidence earns high confidence", result.confidence)
    check(len(result.citations) == 4, "all sources cited")
    check("Evidence" in result.justification, "justification includes the audit trail")


def test_confidence_downgrades_on_single_domain() -> None:
    same_domain = [normalized(5000, "a.com"), normalized(5100, "a.com"), normalized(4950, "a.com"),
                   normalized(5050, "a.com")]
    result = aggregate(same_domain, spec())
    check(result.confidence in ("low", "none"), "single domain caps confidence", result.confidence)
    check(
        any("single domain" in n for n in result.confidence_basis.notes),
        "reason for the downgrade is recorded",
    )


def test_confidence_downgrades_without_strong_match() -> None:
    analogous = [normalized(5000, f"{c}.com", grade="analogous") for c in "abcd"]
    result = aggregate(analogous, spec())
    check(result.confidence == "low", "analogous-only evidence caps confidence at low", result.confidence)


def test_internal_disagreement_is_flagged() -> None:
    good = [normalized(5000, c + ".com") for c in "abcd"]
    result = aggregate(good, spec(), internal_reference_egp=20000.0)
    check(
        any("internal historical cost" in w for w in result.warnings),
        "large gap against internal history is flagged",
    )


def test_unrelated_observations_are_excluded_not_downweighted() -> None:
    mixed = [normalized(5000, "a.com"), normalized(5100, "b.com"),
             normalized(999999, "c.com", grade="unrelated")]
    result = aggregate(mixed, spec())
    check(result.status == "estimated", "estimate still produced", result.status)
    check(result.value_egp is not None and result.value_egp < 10000, "unrelated value excluded from the median")


# --------------------------------------------------------------------------
# Citation verification
# --------------------------------------------------------------------------

def test_citation_verification() -> None:
    page = "Our daily hire rate for a 25 ton crane is 5,000 EGP including operator."
    check(verify_citation(obs(raw_value=5000), page), "matches a comma-formatted number")
    check(verify_citation(obs(raw_value=5000.0), "rate is 5000 EGP"), "matches a plain number")
    check(not verify_citation(obs(raw_value=5000), "rate is 8,200 EGP"), "rejects a number not on the page")
    check(not verify_citation(obs(raw_value=5000), ""), "rejects an empty page")


def test_citation_tolerates_rounding() -> None:
    check(verify_citation(obs(raw_value=5000), "price 5001 EGP"), "tolerates sub-percent rounding")
    check(not verify_citation(obs(raw_value=5000), "price 5600 EGP"), "does not tolerate a real difference")


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------

def test_cache_round_trip() -> None:
    s = spec()
    pricing_cache.put(s, [obs(), obs(raw_value=5500, source_url="https://other.com/x")], ["q1", "q2"])
    fetched = pricing_cache.get(s)
    check(fetched is not None, "cache returns stored evidence")
    if fetched:
        observations, queries, _ = fetched
        check(len(observations) == 2, "both observations round-tripped")
        check(queries == ["q1", "q2"], "queries round-tripped")
        check(observations[0].quote_date == date(2026, 1, 15), "dates survive serialisation")


def test_cache_key_survives_llm_phrasing_drift() -> None:
    # The bug this guards: on the same input the model produced "Mobile Crane Hire"
    # and "Mobile Crane Daily Hire Rate", with capacity arriving as "capacity",
    # "lifting capacity" and "operating weight". A key built from any of those never
    # hit. The key now comes from the caller's parameter name, which cannot drift.
    param = "Mobile crane daily hire rate"
    a = spec(canonical_name="Mobile Crane Hire", attributes={"capacity": "25 ton"},
             source_parameter_name=param)
    b = spec(canonical_name="Mobile Crane Daily Hire Rate",
             attributes={"operating weight": "40 ton"}, source_parameter_name=param)
    check(a.cache_key() == b.cache_key(), "drifting model output still shares one cache key")

    other_param = spec(source_parameter_name="Excavator daily hire rate")
    check(a.cache_key() != other_param.cache_key(), "a different parameter is a different key")

    other_unit = spec(source_parameter_name=param, required_unit="EGP per crane per month")
    check(a.cache_key() != other_unit.cache_key(), "a different unit period is a different key")

    # Measurement words must not split the key.
    check(
        spec(source_parameter_name="Crane hire cost").cache_key()
        == spec(source_parameter_name="Crane hire rate").cache_key(),
        "'cost' and 'rate' phrasings share a key",
    )


def test_cache_similarity_fallback() -> None:
    stored = spec(canonical_name="Mobile Crane Hire", attributes={"capacity": "25 ton"})
    pricing_cache.put(stored, [obs()], ["q"])

    # Same item, different wording and a synonym the token key alone would miss.
    lookup = spec(canonical_name="Mobile Crane Rental Cost", attributes={"capacity": "25 ton"})
    check(pricing_cache.get(lookup) is not None, "similar wording hits via the fallback lookup")

    wrong_size = spec(canonical_name="Mobile Crane Rental Cost", attributes={"capacity": "80 ton"})
    check(pricing_cache.get(wrong_size) is None, "a conflicting specification never hits")

    unrelated = spec(canonical_name="Diesel Generator Hire", attributes={})
    check(pricing_cache.get(unrelated) is None, "an unrelated item does not hit")


def test_cache_key_ignores_model_generated_fields() -> None:
    # Model-generated fields are deliberately excluded from the key: they drift, and
    # a key that moves is a key that never hits. Distinguishing genuinely different
    # specifications is the similarity fallback's job (see the conflict check there),
    # not the exact key's.
    a = spec()
    check(a.cache_key() == spec().cache_key(), "identical specs share a key")
    check(
        a.cache_key() == spec(attributes={"capacity": "50 ton"}).cache_key(),
        "drifting attributes do not move the key",
    )
    check(
        a.cache_key() == spec(category="something else entirely").cache_key(),
        "a drifting category does not move the key",
    )
    check(
        a.cache_key() != spec(required_unit="EGP per crane per month").cache_key(),
        "a different required unit means a different key",
    )
    check(
        a.cache_key() != spec(region="United Arab Emirates").cache_key(),
        "a different region means a different key",
    )


def test_cache_miss_for_unknown_spec() -> None:
    check(pricing_cache.get(spec(canonical_name="something never stored")) is None, "unknown spec misses")


def test_cache_invalidates_on_engine_version_change() -> None:
    # Guards that run during extraction do not run again on a cache hit, so a fix
    # that rejects a bad observation must also invalidate evidence collected before
    # the fix existed.
    s = spec(canonical_name="versioned item")
    pricing_cache.put(s, [obs()], ["q"])
    check(pricing_cache.get(s) is not None, "evidence is readable at the current version")

    original = pricing_cache.ENGINE_VERSION
    try:
        pricing_cache.ENGINE_VERSION = original + 1
        check(pricing_cache.get(s) is None, "evidence from an older engine version is a miss")
    finally:
        pricing_cache.ENGINE_VERSION = original
    check(pricing_cache.get(s) is not None, "evidence is readable again once the version matches")


# --------------------------------------------------------------------------
# Formula evaluation
#
# Imported lazily: cost_estimation loads a FAISS index at import time, so these
# are skipped rather than failing the suite when the vector stores are absent.
# --------------------------------------------------------------------------

def _formula_module():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import cost_estimation

    return cost_estimation


def _cost_input(formula: str, params: list) -> dict:
    return {
        "cost_input_name": "Crane hire",
        "formula": formula,
        "cost_parameters": [
            {"parameter_name": name, "monthly_cost_egp": value} for name, value in params
        ],
    }


def test_formula_evaluation() -> None:
    try:
        module = _formula_module()
    except Exception as exc:
        print(f"  (skipped formula tests: {exc})")
        return

    evaluate = module.evaluate_formula_for_input

    good = _cost_input("[Daily Rate] * 22", [("Daily Rate", 5000.0)])
    check(evaluate(good) == 110000.0, "formula evaluates from parameter values")
    check("formula_error" not in good, "successful evaluation leaves no error")

    missing = _cost_input("[Daily Rate] * 22", [("Daily Rate", None)])
    check(evaluate(missing) is None, "abstained parameter makes the input uncostable")
    check("formula_error" in missing, "reason recorded for the gap")
    check(
        "Daily Rate" in missing.get("formula_error", ""),
        "the error names the missing parameter",
        missing.get("formula_error", ""),
    )

    dangling = _cost_input("[Daily Rate] * [Working Days]", [("Daily Rate", 5000.0)])
    check(evaluate(dangling) is None, "formula referencing a non-existent parameter does not evaluate")
    check(
        "Working Days" in dangling.get("formula_error", ""),
        "the error names the missing reference",
        dangling.get("formula_error", ""),
    )

    single = _cost_input("", [("Monthly Fee", 8000.0)])
    check(evaluate(single) == 8000.0, "single parameter without a formula is the cost")

    summed = _cost_input("", [("A", 100.0), ("B", 250.0)])
    check(evaluate(summed) == 350.0, "several parameters without a formula are summed")

    partial = _cost_input("", [("A", 100.0), ("B", None)])
    check(evaluate(partial) is None, "a partial parameter set does not silently sum to a lower number")

    # Regression: the previous implementation replaced a zero result with the sum of
    # the parameters, so a legitimately free line item silently acquired a cost.
    genuinely_free = _cost_input("[Rate] * 0", [("Rate", 5000.0)])
    check(evaluate(genuinely_free) == 0.0, "a formula that really evaluates to zero returns zero")


def test_specification_misread_guard() -> None:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from pricing_engine import looks_like_specification_number
    except Exception as exc:
        print(f"  (skipped specification guard test: {exc})")
        return

    check(
        looks_like_specification_number(25, "25 Ton All Terrain Crane"),
        "a price equal to the stated capacity is caught as a misread",
    )
    check(
        looks_like_specification_number(10, "10 kVA diesel generator"),
        "a price equal to the stated power rating is caught",
    )
    check(
        not looks_like_specification_number(4000, "25 Ton All Terrain Crane"),
        "a real price alongside a capacity is kept",
    )
    check(
        not looks_like_specification_number(25, "crane hire daily rate"),
        "no specification in the description means no rejection",
    )


def test_quoted_price_converts_without_the_model() -> None:
    """
    The failure this guards against: a truck priced at 100-150 USD/day came back as
    "no estimate", because the model was asked for EGP per month and would not
    invent an exchange rate. Conversion is arithmetic and belongs here, not in a prompt.
    """
    try:
        module = _formula_module()
    except Exception as exc:
        print(f"  (skipped conversion tests: {exc})")
        return

    # The rate is read back out of the function itself rather than asserted, so the
    # test checks the arithmetic without pinning today's exchange rate.
    rate, _ = module.to_monthly_egp(1.0, "USD", "per month")
    check(rate > 10, "a USD rate is available (live or fallback), not 1:1", f"{rate:.2f}")

    monthly, note = module.to_monthly_egp(125.0, "USD", "per day")
    expected = 125.0 * rate * module.MONTH_DAYS
    check(approx(monthly, expected), "USD per day becomes EGP per month", f"{monthly:,.0f}")
    check("USD" in note and "per month" in note, "the conversion is spelled out in the note", note)

    same, _ = module.to_monthly_egp(9000.0, "EGP", "per month")
    check(same == 9000.0, "an EGP monthly figure passes through untouched", str(same))

    annual, _ = module.to_monthly_egp(120000.0, "EGP", "per year")
    check(approx(annual, 120000.0 * module.MONTH_DAYS / 365.25), "annual becomes monthly", f"{annual:,.0f}")

    one_time, note_once = module.to_monthly_egp(50000.0, "EGP", "one-time")
    check(one_time == 50000.0, "a one-time cost is not spread over a month")
    check("one-time" in note_once, "the note says no period conversion was applied")

    unstated, note_unstated = module.to_monthly_egp(7000.0, "EGP", "")
    check(unstated == 7000.0, "an unstated period is assumed monthly rather than dropped")
    check("assumed" in note_unstated, "and the assumption is recorded", note_unstated)


def test_calculator_raises_instead_of_returning_zero() -> None:
    try:
        module = _formula_module()
    except Exception as exc:
        print(f"  (skipped calculator test: {exc})")
        return

    try:
        module.calculator("not an expression")
        check(False, "calculator raises on invalid input")
    except ValueError:
        check(True, "calculator raises on invalid input")
    except Exception as exc:
        check(False, "calculator raises ValueError specifically", type(exc).__name__)

    check(module.calculator("5000 * 22") == 110000.0, "calculator still evaluates valid expressions")


# --------------------------------------------------------------------------

def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    for test in tests:
        try:
            test()
        except Exception as exc:  # a crash is a failure, not an abort
            _FAILURES.append(f"{test.__name__} raised {type(exc).__name__}: {exc}")

    print(f"\n{_PASSES} checks passed across {len(tests)} tests")
    if _FAILURES:
        print(f"{len(_FAILURES)} FAILED:\n")
        for failure in _FAILURES:
            print(f"  x {failure}")
        return 1
    print("all green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
