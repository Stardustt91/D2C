"""
Evidence data model for the cost estimation engine.

The atomic unit of this engine is the PriceObservation: one price, found in one
place, for one described item, on one date. Everything downstream — currency and
vintage normalization, SKU comparability, robust aggregation, the abstention gate
and the cache — operates on these records rather than on a single opaque number.

Nothing in this module performs I/O or calls an LLM; it is pure data definition
so it can be imported and tested without network access.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# Vocabularies
# --------------------------------------------------------------------------

#: How a resource's price responds to Egyptian macro conditions. Drives which
#: escalation series is applied when an old quote is brought to present value:
#: imported goods track the USD/EGP rate, local services track domestic wage and
#: price inflation, and the two diverged sharply over 2022-2024.
ImportIntensity = Literal["imported", "mixed", "local"]

#: How quickly a price goes stale. Sets the cache TTL and the vintage penalty.
VolatilityClass = Literal["high", "medium", "low"]

#: What kind of page a price came from, in rough descending order of how much a
#: procurement analyst would trust it.
SourceType = Literal[
    "vendor_quote",      # a supplier's own published price or quotation
    "tender_award",      # public tender / contract award record
    "statistical",       # CAPMAS, central bank, industry body, salary survey
    "marketplace",       # listing sites, classifieds
    "news_article",      # journalism quoting a price
    "aggregator",        # price comparison / directory sites
    "forum",             # forums, social posts, Q&A
    "unknown",
]

#: Result of comparing an observation's item against the spec we asked for.
MatchGrade = Literal["exact", "equivalent", "analogous", "unrelated"]

#: Outcome of the abstention gate.
EstimateStatus = Literal[
    "estimated",             # enough qualifying evidence to stand behind a number
    "insufficient_evidence", # searched, found nothing that qualifies
    "needs_analyst_input",   # found conflicting or ambiguous evidence a human must resolve
    "user_set",              # an analyst typed the number in, overriding the engine
]

#: How the published number was arrived at.
#:
#: The status alone no longer says: a flagged estimate now still carries a figure,
#: so that a downstream formula has something to work with, and this field is what
#: distinguishes a well-evidenced median from a placeholder taken off the nearest
#: comparable item.
ValueBasis = Literal[
    "weighted_median",              # the normal path: robust median of qualifying evidence
    "median_of_disagreeing_sources",# sources contradict each other; median shown pending review
    "closest_match",                # too little qualifying evidence; nearest comparable item's price
    "internal_benchmark",           # no web evidence; figure taken from internal historical records
    "model_judgement",              # no evidence anywhere; the model's own benchmark, unsourced
    "user_override",                # value supplied by an analyst
    "none",                         # no number at all
]

#: Which normalization step produced an adjustment factor.
AdjustmentKind = Literal[
    "currency",   # foreign currency -> EGP at the rate on the quote date
    "vintage",    # historical price -> present value via an escalation series
    "unit",       # source's unit basis -> the parameter's declared unit
    "scope",      # add or strip VAT / installation / warranty / delivery
    "quantity",   # bulk or tiered pricing -> single-unit basis
]

# Trust weights by source type. Used as one factor in the aggregation weight;
# deliberately a gentle spread rather than an order of magnitude, because a
# well-specified marketplace listing can beat a vague vendor page.
SOURCE_TRUST: Dict[str, float] = {
    "vendor_quote": 1.00,
    "tender_award": 0.95,
    "statistical": 0.90,
    "marketplace": 0.70,
    "news_article": 0.55,
    "aggregator": 0.50,
    "forum": 0.30,
    "unknown": 0.40,
}

# Multipliers applied to an observation's weight based on how well its item
# matches the spec. "unrelated" is zero — such observations are excluded, never
# merely down-weighted.
MATCH_WEIGHT: Dict[str, float] = {
    "exact": 1.00,
    "equivalent": 0.85,
    "analogous": 0.40,
    "unrelated": 0.00,
}


# --------------------------------------------------------------------------
# Item specification
# --------------------------------------------------------------------------


class ItemSpec(BaseModel):
    """
    A structured description of the thing being priced, resolved from the free-text
    cost parameter name before any searching happens.

    Serves two jobs: it seeds the query fan-out, and it is the reference that each
    retrieved observation is graded against for comparability.
    """

    canonical_name: str = Field(
        ..., description="Clearest, most standard name for this item in English, without brand names"
    )
    category: str = Field(
        ..., description="Broad procurement category, e.g. 'heavy vehicle rental', 'civil labour', 'telecom hardware'"
    )
    attributes: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Price-driving specifications as key/value pairs, e.g. {'capacity': '25 ton', "
            "'seniority': 'senior', 'power': '10 kVA'}. Only include attributes that materially "
            "change price."
        ),
    )
    required_unit: str = Field(
        ..., description="The pricing unit the estimate must be returned in, e.g. 'EGP per truck per day'"
    )
    region: str = Field(default="Egypt", description="Geography the price should apply to")
    import_intensity: ImportIntensity = Field(
        default="mixed",
        description=(
            "'imported' if the price is set in hard currency (equipment, hardware), "
            "'local' if set in EGP by domestic supply (labour, local services), 'mixed' otherwise"
        ),
    )
    volatility_class: VolatilityClass = Field(
        default="medium", description="How quickly this price goes stale"
    )
    synonyms: List[str] = Field(
        default_factory=list, description="Alternative names and Egyptian Arabic terms for the same item"
    )
    exclusions: List[str] = Field(
        default_factory=list,
        description=(
            "Similar-sounding items that would be the WRONG match, e.g. for a 25-ton crane: "
            "['50 ton crane', 'crane purchase price']"
        ),
    )
    source_parameter_name: str = Field(
        default="",
        description=(
            "The cost parameter name this spec was resolved from. Not model output — set by "
            "the caller, and used as the cache identity because it comes from the persisted "
            "structure and therefore does not drift between runs."
        ),
    )

    # Words that describe the *measurement* rather than the item, so that
    # "Mobile Crane Hire" and "Mobile Crane Daily Hire Rate" resolve to the same
    # cached evidence.
    _NAME_NOISE = {
        "cost", "costs", "price", "prices", "pricing", "rate", "rates", "fee", "fees",
        "charge", "charges", "daily", "monthly", "weekly", "annual", "yearly", "hourly",
        "per", "unit", "total", "average", "estimated", "of", "for", "the", "a", "an", "and",
    }

    def name_tokens(self) -> List[str]:
        """
        The item's identity as a sorted token set, with measurement words removed.

        Used for cache lookup rather than the raw name, because the model phrases the
        same item differently on each run and an exact-string key would almost never
        hit — which is the whole failure the cache exists to avoid.
        """
        words = re.findall(r"[a-z0-9]+", (self.canonical_name or "").lower())
        return sorted({w for w in words if w not in self._NAME_NOISE and len(w) > 1})

    def dimensions(self) -> Dict[str, float]:
        """
        Numeric specifications drawn from the name and attributes.

        These are the stable, price-driving facts. Attribute KEYS drift between runs
        ("capacity" / "lifting capacity" / "operating weight"), so identity is taken
        from the numbers and their physical units instead of the labels.
        """
        # Attributes are scanned before the name: they are the structured field, so
        # when the two disagree ("25 ton crane" named, capacity 50 ton declared) the
        # explicit attribute is the one to trust.
        blob = " ".join([f"{k} {v}" for k, v in self.attributes.items()] + [self.canonical_name]).lower()
        found: Dict[str, float] = {}
        for pattern, dimension in _DIMENSION_PATTERNS:
            match = re.search(pattern, blob)
            if match and dimension not in found:
                try:
                    found[dimension] = float(match.group(1))
                except ValueError:
                    continue
        return found

    def unit_period(self) -> str:
        """The time basis of the required unit — day, month, one-time and so on."""
        text = (self.required_unit or "").lower()
        for token in ("hour", "day", "week", "month", "quarter", "year", "annual"):
            if token in text:
                return "year" if token == "annual" else token
        return "one-time"

    def cache_key(self) -> str:
        """
        Exact identity for the cache fast path.

        Derived from the caller-supplied parameter name rather than from anything the
        model produced. Every model-generated field drifts between runs — the item was
        variously named "Mobile Crane Hire" and "Mobile Crane Daily Hire Rate", and its
        capacity arrived as "capacity", "lifting capacity" and "operating weight" —
        so a key built from those never matched and the cache saved nothing. The
        parameter name comes from the persisted structure and is stable by
        construction, which is exactly what a key needs to be.

        Cross-project reuse, where parameter names genuinely differ, is handled by the
        similarity fallback in pricing_cache rather than by this key.
        """
        words = re.findall(r"[a-z0-9]+", (self.source_parameter_name or self.canonical_name or "").lower())
        identity = sorted({w for w in words if w not in self._NAME_NOISE and len(w) > 1})
        parts = ["-".join(identity), self.unit_period(), _slug(self.region)]
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]

    def describe(self) -> str:
        """One-line human description, used in prompts and logs."""
        bits = [self.canonical_name]
        if self.attributes:
            bits.append("(" + ", ".join(f"{k}: {v}" for k, v in sorted(self.attributes.items())) + ")")
        bits.append(f"in {self.region}")
        bits.append(f"priced as {self.required_unit}")
        return " ".join(bits)


def _slug(text: str) -> str:
    """Lowercase, collapse non-alphanumerics — for building stable cache keys."""
    return re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")


# Physical specifications that identify an item regardless of how its attribute
# labels are worded. Kept here rather than in pricing_match so that both the cache
# key and comparability grading read from one definition.
_DIMENSION_PATTERNS = [
    (r"(\d+(?:\.\d+)?)\s*(?:ton|tonne)\b", "capacity_ton"),
    (r"(\d+(?:\.\d+)?)\s*kva\b", "power_kva"),
    (r"(\d+(?:\.\d+)?)\s*kwh\b", "energy_kwh"),
    (r"(\d+(?:\.\d+)?)\s*kw\b", "power_kw"),
    (r"(\d+(?:\.\d+)?)\s*(?:m2|sqm)\b", "area_sqm"),
    (r"(\d+(?:\.\d+)?)\s*(?:m3)\b", "volume_m3"),
    (r"(\d+(?:\.\d+)?)\s*ah\b", "battery_ah"),
]


# --------------------------------------------------------------------------
# Observations
# --------------------------------------------------------------------------


class ScopeFlags(BaseModel):
    """
    What a quoted price does and does not include.

    Every field is tri-state: True, False, or None for "the source does not say".
    Unknown is a genuinely different case from False and must not be collapsed into
    it — assuming a silent source excludes VAT would bias every such estimate low.
    """

    vat_included: Optional[bool] = Field(None, description="Does the price include VAT? None if unstated")
    installation_included: Optional[bool] = Field(None, description="Does it include installation/commissioning?")
    warranty_included: Optional[bool] = Field(None, description="Does it include warranty or maintenance cover?")
    delivery_included: Optional[bool] = Field(None, description="Does it include delivery/transport to site?")


class PriceObservation(BaseModel):
    """
    One price, as found, before any adjustment. Fields record what the source
    actually said — normalization never mutates this record, it produces a
    NormalizedObservation alongside it, so the raw evidence stays inspectable.
    """

    raw_value: float = Field(..., description="The price exactly as quoted, in the source's own currency and unit")
    currency: str = Field(..., description="ISO currency code of the quoted price, e.g. 'EGP', 'USD'")
    unit_basis: str = Field(
        ..., description="What the price buys, as stated by the source, e.g. 'per day', 'per month', 'one-time'"
    )
    item_description: str = Field(
        ..., description="How the source describes the item, copied as closely as possible"
    )
    evidence_quote: str = Field(
        ...,
        description=(
            "The verbatim sentence or table row from the page containing this price. Used to verify "
            "the citation actually supports the number."
        ),
    )
    source_url: str = Field(..., description="URL the price was found on")
    source_type: SourceType = Field(default="unknown", description="What kind of page this is")
    quote_date: Optional[date] = Field(
        None, description="Date the price was quoted or the page published. None if the page gives no date."
    )
    scope: ScopeFlags = Field(default_factory=ScopeFlags)
    quantity_tier: Optional[float] = Field(
        None, description="If the price is for a bulk quantity, how many units. None or 1 for single-unit pricing."
    )
    region: Optional[str] = Field(None, description="Geography this price applies to, if the source says")
    query: Optional[str] = Field(None, description="Which fan-out query surfaced this observation")

    @property
    def source_domain(self) -> str:
        m = re.match(r"https?://([^/]+)", self.source_url or "")
        return (m.group(1).lower().removeprefix("www.") if m else "").strip()

    def fingerprint(self) -> str:
        """
        Identity for deduplication. Two observations are the same if they report the
        same value and currency from the same domain — the same price reprinted on
        several pages of one site should count once, not five times.
        """
        raw = f"{self.source_domain}|{round(float(self.raw_value), 4)}|{self.currency.upper()}|{_slug(self.unit_basis)}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class Adjustment(BaseModel):
    """One normalization step, recorded so the estimate can be audited and replayed."""

    kind: AdjustmentKind
    factor: float = Field(..., description="Multiplicative factor applied to the value")
    reason: str = Field(..., description="Human-readable explanation, including any rate or index used")
    value_before: float
    value_after: float


class NormalizedObservation(BaseModel):
    """
    An observation converted onto the common basis — EGP, present value, the
    parameter's declared unit, single-unit quantity, VAT-exclusive — together with
    the full adjustment chain and its comparability grade.
    """

    observation: PriceObservation
    value_egp: float = Field(..., description="Value after all adjustments, in the spec's required unit")
    adjustments: List[Adjustment] = Field(default_factory=list)
    match_grade: MatchGrade = Field(default="analogous")
    match_reason: str = Field(default="")
    citation_verified: bool = Field(
        default=False, description="True if the quoted number was found in the fetched page text"
    )
    normalization_failed: Optional[str] = Field(
        None, description="Set when an adjustment could not be applied; such observations are excluded"
    )

    @property
    def is_usable(self) -> bool:
        return (
            self.normalization_failed is None
            and self.match_grade != "unrelated"
            and self.value_egp > 0
        )

    def weight(self, today: Optional[date] = None) -> float:
        """
        Aggregation weight: source trust x match quality x recency.

        Recency decays gently — a two-year-old vendor quote that has been escalated
        for inflation is still evidence, just weaker than a current one. Undated
        observations are treated as moderately old rather than discarded, since on
        the Egyptian web most pages carry no date at all.
        """
        trust = SOURCE_TRUST.get(self.observation.source_type, 0.4)
        match = MATCH_WEIGHT.get(self.match_grade, 0.0)

        today = today or datetime.now(timezone.utc).date()
        qd = self.observation.quote_date
        if qd is None:
            recency = 0.6
        else:
            age_years = max((today - qd).days, 0) / 365.25
            recency = 1.0 / (1.0 + 0.35 * age_years)

        w = trust * match * recency
        if self.citation_verified:
            w *= 1.25
        return max(w, 0.0)


# --------------------------------------------------------------------------
# Result
# --------------------------------------------------------------------------


class ConfidenceBasis(BaseModel):
    """
    The measurable inputs behind the confidence label.

    Recorded separately from the label so that a reviewer can see why the engine
    was confident, rather than taking a self-reported adjective on trust.
    """

    n_observations: int = 0
    n_qualifying: int = 0
    n_exact_or_equivalent: int = 0
    n_distinct_domains: int = 0
    n_citations_verified: int = 0
    relative_spread: Optional[float] = Field(
        None, description="Interquartile range divided by the median — dimensionless measure of disagreement"
    )
    median_age_years: Optional[float] = None
    notes: List[str] = Field(default_factory=list)


class CostEstimate(BaseModel):
    """
    The engine's answer for one cost parameter.

    A non-'estimated' status no longer means no number. The gate still decides
    whether the evidence is good enough to stand behind, but when it is not, a
    provisional figure is published alongside the flag — the median of the
    disagreeing sources, or the price of the closest comparable item — so that the
    formula above it can still produce a total and the analyst has something to
    correct rather than a blank.

    What must never happen is a provisional figure that looks settled, so `status`
    and `value_basis` always travel with the value, and 0.0 is still never used to
    mean "we don't know".
    """

    status: EstimateStatus
    value_egp: Optional[float] = None
    value_basis: ValueBasis = "none"
    unit: str = ""
    low_egp: Optional[float] = Field(None, description="25th percentile of qualifying observations")
    high_egp: Optional[float] = Field(None, description="75th percentile of qualifying observations")
    confidence: Literal["high", "medium", "low", "none"] = "none"
    confidence_basis: ConfidenceBasis = Field(default_factory=ConfidenceBasis)
    justification: str = ""
    citations: List[str] = Field(default_factory=list)
    observations: List[NormalizedObservation] = Field(default_factory=list)
    queries_used: List[str] = Field(default_factory=list)
    spec: Optional[ItemSpec] = None
    internal_reference_egp: Optional[float] = Field(
        None, description="Comparable value from the internal historical database, normalized the same way"
    )
    warnings: List[str] = Field(default_factory=list)
    cache_hit: bool = False
    estimated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_structure_fields(self) -> Dict[str, Any]:
        """
        Project onto the keys the existing UI, exporter and formula evaluator read.

        `monthly_cost_egp` keeps its legacy name and its legacy meaning of "the value
        in the parameter's declared unit" — the cost-input formula still does the
        conversion to monthly. It is None only when the engine found nothing at all
        to base a figure on, so downstream code can still tell the difference between
        no answer and zero; when it is a provisional figure, `estimate_status` and
        `estimate_basis` say so and the UI flags it.
        """
        return {
            "monthly_cost_egp": self.value_egp,
            "cost_justification": self.justification,
            "source_urls": list(self.citations),
            "estimate_status": self.status,
            "estimate_basis": self.value_basis,
            "confidence": self.confidence,
            "confidence_basis": self.confidence_basis.model_dump(),
            "value_range_egp": (
                [self.low_egp, self.high_egp] if self.low_egp is not None and self.high_egp is not None else None
            ),
            "evidence_count": self.confidence_basis.n_qualifying,
            "estimate_warnings": list(self.warnings),
            "cost_from_internal_database": (
                f"{self.internal_reference_egp:,.0f} EGP ({self.unit})"
                if self.internal_reference_egp is not None
                else "Nothing found"
            ),
        }
