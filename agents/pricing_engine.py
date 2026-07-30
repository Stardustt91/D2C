"""
The cost estimation engine.

Replaces the single "search results blob in, one float out" prompt with an
evidence pipeline:

    resolve spec -> plan queries -> search -> extract observations ->
    normalize -> grade matches -> verify citations -> aggregate -> gate

Every stage produces inspectable intermediate data, and the final stage is allowed
to decline. `estimate_parameter` never raises and never invents a number: when the
evidence does not support one it returns a CostEstimate whose status says so.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

import pricing_cache
from pricing_aggregate import aggregate, verify_citations
from pricing_config import (
    ALLOW_MODEL_JUDGEMENT,
    MAX_PAGE_CHARS,
    MAX_QUERIES,
    PAGES_PER_EXTRACTION_CALL,
    RESULTS_PER_QUERY,
    USE_CACHE,
    get_llm,
    get_tavily,
)
from pricing_match import grade_observations
from pricing_models import (
    CostEstimate,
    ItemSpec,
    PriceObservation,
    ScopeFlags,
)
from pricing_normalize import normalize_observation, normalization_warnings

# --------------------------------------------------------------------------
# LLM-facing schemas
#
# Kept separate from the domain models in pricing_models: structured output is
# more reliable with flat, primitive-typed fields, so dates arrive as strings and
# attribute maps as lists of pairs, and are converted here.
# --------------------------------------------------------------------------


class _Attribute(BaseModel):
    name: str = Field(..., description="Attribute name, e.g. 'capacity'")
    value: str = Field(..., description="Attribute value, e.g. '25 ton'")


class _SpecDraft(BaseModel):
    canonical_name: str = Field(..., description="Standard English name for the item, no brand names")
    category: str = Field(..., description="Procurement category")
    attributes: List[_Attribute] = Field(
        default_factory=list, description="Only specifications that materially change the price"
    )
    import_intensity: str = Field(
        ..., description="'imported' if priced in hard currency, 'local' if set by domestic supply, else 'mixed'"
    )
    volatility_class: str = Field(..., description="'high', 'medium' or 'low' — how fast this price goes stale")
    synonyms: List[str] = Field(default_factory=list, description="Other names, including Egyptian Arabic terms")
    exclusions: List[str] = Field(
        default_factory=list, description="Similar items that would be the WRONG match"
    )


class _QueryItem(BaseModel):
    query: str = Field(..., description="The search query text")
    axis: str = Field(..., description="Which axis this varies: specificity, language, geography, source_type or unit")


class _QueryPlan(BaseModel):
    queries: List[_QueryItem] = Field(..., description="Search queries varying along different axes")


class _RawObservation(BaseModel):
    raw_value: float = Field(..., description="The price exactly as quoted, no conversion")
    currency: str = Field(..., description="ISO code of the quoted currency, e.g. EGP, USD")
    unit_basis: str = Field(..., description="What the price buys, as the source states it: 'per day', 'one-time', ...")
    item_description: str = Field(..., description="How the source describes the item priced")
    evidence_quote: str = Field(..., description="Verbatim sentence or row from the page containing this price")
    source_url: str = Field(..., description="Exact URL this price came from")
    source_type: str = Field(
        ..., description="vendor_quote, tender_award, statistical, marketplace, news_article, aggregator, forum or unknown"
    )
    quote_date: str = Field(
        ..., description="Date of the price as YYYY-MM-DD, or just YYYY if only the year is known, or 'unknown'"
    )
    vat_included: str = Field(..., description="'yes', 'no' or 'unknown'")
    installation_included: str = Field(..., description="'yes', 'no' or 'unknown'")
    warranty_included: str = Field(..., description="'yes', 'no' or 'unknown'")
    delivery_included: str = Field(..., description="'yes', 'no' or 'unknown'")
    quantity_tier: float = Field(1.0, description="How many units this price covers; 1 for single-unit pricing")


class _ObservationBatch(BaseModel):
    observations: List[_RawObservation] = Field(
        default_factory=list, description="Every distinct price found. Empty list if the pages contain no prices."
    )


# --------------------------------------------------------------------------
# Stage 0 — resolve the item spec
# --------------------------------------------------------------------------

_SPEC_PROMPT = """You are a procurement analyst for a telecom operator in Egypt, preparing to source one specific item.

Turn the cost parameter below into a precise item specification that can be searched for and matched against.

Activity context: {activity}
Cost driver: {driver}
Cost component: {component}
Cost input: {cost_input}
Cost parameter to price: {parameter}
Required pricing unit: {unit}

Guidance:
- canonical_name: what a supplier would call this, in standard English, no brand names.
- attributes: ONLY specifications that materially change the price (capacity, power rating, seniority, grade). Do not pad this out.
- import_intensity: what sets the price, not what the asset is made of. Use 'imported' only when the buyer is purchasing goods priced in hard currency (buying a generator, a shelter, network hardware). Use 'local' for labour, local services, permits and regulated fees. RENTAL AND HIRE OF EQUIPMENT PERFORMED IN EGYPT IS 'mixed' OR 'local' EVEN WHEN THE MACHINE ITSELF IS IMPORTED, because the rate is set by local operators, local fuel and local wages. This decides which inflation series ages old prices, so it matters.
- volatility_class: 'high' for FX-linked goods and fuel, 'low' for local labour and regulated fees, 'medium' otherwise.
- exclusions: name the specific similar items that would be a WRONG match — different capacity, rental instead of purchase, a part instead of the whole unit. Be concrete.

Return the specification."""


def resolve_spec(
    activity_description: str,
    cost_driver: str,
    cost_component: str,
    cost_input: str,
    parameter_name: str,
    required_unit: str,
    llm=None,
) -> ItemSpec:
    """
    Build a structured item specification from the free-text parameter name.

    Falls back to a minimal spec built from the parameter name if the model call
    fails, so a transient error degrades the estimate rather than aborting it.
    """
    llm = llm or get_llm()
    prompt = _SPEC_PROMPT.format(
        activity=(activity_description or "")[:1200],
        driver=cost_driver,
        component=cost_component,
        cost_input=cost_input,
        parameter=parameter_name,
        unit=required_unit,
    )

    try:
        draft = llm.with_structured_output(_SpecDraft).invoke(prompt)
    except Exception as exc:
        print(f"[pricing_engine] spec resolution failed for {parameter_name!r}: {exc}")
        return ItemSpec(
            canonical_name=parameter_name,
            category=cost_component or "general",
            required_unit=required_unit,
            source_parameter_name=parameter_name,
        )

    intensity = (draft.import_intensity or "mixed").strip().lower()
    if intensity not in ("imported", "local", "mixed"):
        intensity = "mixed"
    volatility = (draft.volatility_class or "medium").strip().lower()
    if volatility not in ("high", "medium", "low"):
        volatility = "medium"

    return ItemSpec(
        canonical_name=draft.canonical_name or parameter_name,
        category=draft.category or (cost_component or "general"),
        attributes={a.name.strip(): a.value.strip() for a in draft.attributes if a.name and a.value},
        required_unit=required_unit,
        region="Egypt",
        import_intensity=intensity,  # type: ignore[arg-type]
        volatility_class=volatility,  # type: ignore[arg-type]
        synonyms=[s for s in draft.synonyms if s and s.strip()][:8],
        exclusions=[e for e in draft.exclusions if e and e.strip()][:8],
        source_parameter_name=parameter_name,
    )


# --------------------------------------------------------------------------
# Stage 1 — plan the query fan-out
# --------------------------------------------------------------------------

_QUERY_PROMPT = """You are a procurement search strategist finding real prices for an item in Egypt.

ITEM: {spec}
Category: {category}
Also known as: {synonyms}

Write {n} web search queries that vary along DIFFERENT axes, so they surface different pages rather than reformulations of one search:

- specificity: one very specific query naming the exact specification, and one broader category query
- language: at least two in Egyptian Arabic, since local supplier and classified pages are rarely in English
- geography: mostly Egypt, but include one Gulf or wider regional query as a fallback benchmark
- source_type: aim at least one at supplier or vendor price lists, and one at tenders, salary surveys or statistical bulletins
- unit: vary the period asked about (daily / monthly / annual), since pages tend to quote one and not the others

Rules:
- Never include "Vodafone" or any internal project wording.
- Use the words buyers and sellers actually use, not procurement jargon.
- Include the current year where a fresh price matters.

Return exactly {n} queries."""


def plan_queries(spec: ItemSpec, llm=None, n: int = MAX_QUERIES) -> List[str]:
    """Generate the fan-out. Falls back to a fixed set covering the same axes."""
    llm = llm or get_llm()
    year = datetime.now(timezone.utc).year
    try:
        plan = llm.with_structured_output(_QueryPlan).invoke(
            _QUERY_PROMPT.format(
                spec=spec.describe(),
                category=spec.category,
                synonyms=", ".join(spec.synonyms) if spec.synonyms else "(none)",
                n=n,
            )
        )
        queries = [q.query.strip() for q in plan.queries if q.query and q.query.strip()]
    except Exception as exc:
        print(f"[pricing_engine] query planning failed for {spec.canonical_name!r}: {exc}")
        queries = []

    if not queries:
        name = spec.canonical_name
        queries = [
            f"{name} price Egypt {year}",
            f"سعر {name} مصر {year}",
            f"{spec.category} price list Egypt {year}",
            f"{name} rental rate Egypt",
            f"{name} price UAE Saudi Arabia {year}",
        ]

    deduped: List[str] = []
    for query in queries:
        if query.lower() not in {d.lower() for d in deduped}:
            deduped.append(query)
    return deduped[:n]


# --------------------------------------------------------------------------
# Stage 2 — search and fetch
# --------------------------------------------------------------------------


def run_searches(queries: List[str]) -> Tuple[List[dict], Dict[str, str]]:
    """
    Execute the fan-out.

    Returns the deduplicated result records and a URL -> page text map used later
    to verify that cited pages really contain the numbers attributed to them.
    Raw page content is requested because prices sit in tables that snippets cut off.
    """
    client = get_tavily()
    results: List[dict] = []
    page_texts: Dict[str, str] = {}
    seen_urls: set = set()

    for query in queries:
        try:
            response = client.search(
                query=query,
                max_results=RESULTS_PER_QUERY,
                include_raw_content=True,
                search_depth="advanced",
            )
        except Exception as exc:
            print(f"[pricing_engine] search failed for {query!r}: {exc}")
            continue

        for item in response.get("results") or []:
            url = (item.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            body = item.get("raw_content") or item.get("content") or ""
            page_texts[url] = body
            results.append(
                {
                    "url": url,
                    "title": item.get("title") or "",
                    "text": body[:MAX_PAGE_CHARS],
                    "query": query,
                }
            )

    return results, page_texts


# --------------------------------------------------------------------------
# Stage 3 — extract structured observations
# --------------------------------------------------------------------------

_EXTRACTION_PROMPT = """You are extracting price evidence from web pages for a procurement analyst.

ITEM BEING SOURCED: {spec}
Wrong matches to watch for: {exclusions}

Below are web pages. Extract EVERY distinct price that appears, whether or not it is for the exact item — a later step decides what is comparable. Your job is faithful extraction, not judgement.

STRICT RULES:
- Report the price EXACTLY as quoted. Do not convert currency, do not convert periods, do not adjust for anything.
- evidence_quote must be copied VERBATIM from the page and must contain the number. If you cannot copy such a sentence, do not report that price at all.
- MULTI-ITEM PAGES: many supplier pages are price lists covering several models. Emit ONE observation per priced row, and take item_description from THAT ROW ONLY. Never describe a price using specifications that belong to a different row — a 5,000 EGP rate sitting next to a 25 ton model must not be described as a 500 ton model just because that appears elsewhere on the page. If you cannot tell which item a price belongs to, skip it.
- item_description and evidence_quote must refer to the same item. If the description mentions a capacity, rating or model, that detail must come from the same row or sentence as the price.
- source_url must be the URL of the page the price came from, copied exactly.
- quote_date: use a date stated on the page. If only a year is given, give the year. If the page gives no date at all, write "unknown". Never guess a date.
- For vat_included, installation_included, warranty_included, delivery_included: answer "unknown" unless the page actually says. "unknown" is the correct and expected answer most of the time.
- If a page contains no prices, simply report nothing for it.
- Never invent, infer, or estimate a price. An empty result is a valid and useful answer.

PAGES:
{pages}"""


def _parse_date(text: str) -> Optional[date]:
    """
    Lenient date parsing. A bare year becomes mid-year, which halves the worst-case
    error against picking January.
    """
    value = (text or "").strip().lower()
    if not value or value in ("unknown", "n/a", "none", "null"):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        pass
    match = re.search(r"(19|20)\d{2}", value)
    if match:
        year = int(match.group(0))
        if 1990 <= year <= datetime.now(timezone.utc).year + 1:
            return date(year, 7, 1)
    return None


def _parse_tristate(text: str) -> Optional[bool]:
    value = (text or "").strip().lower()
    if value in ("yes", "true", "included", "y"):
        return True
    if value in ("no", "false", "excluded", "n"):
        return False
    return None


_VALID_SOURCE_TYPES = {
    "vendor_quote", "tender_award", "statistical", "marketplace",
    "news_article", "aggregator", "forum", "unknown",
}


def extract_observations(spec: ItemSpec, results: List[dict], llm=None) -> List[PriceObservation]:
    """
    Convert pages into structured price observations, batched to bound prompt size.

    Observations whose URL is not among the pages actually supplied are dropped:
    that is the model reaching for a source it was not given.
    """
    if not results:
        return []

    llm = llm or get_llm()
    observations: List[PriceObservation] = []
    supplied_urls = {r["url"] for r in results}

    for start in range(0, len(results), PAGES_PER_EXTRACTION_CALL):
        batch = results[start : start + PAGES_PER_EXTRACTION_CALL]
        pages = "\n\n".join(
            f"--- PAGE {i + 1} ---\nURL: {r['url']}\nTitle: {r['title']}\nContent:\n{r['text']}"
            for i, r in enumerate(batch)
        )
        prompt = _EXTRACTION_PROMPT.format(
            spec=spec.describe(),
            exclusions=", ".join(spec.exclusions) if spec.exclusions else "(none specified)",
            pages=pages,
        )

        try:
            extracted = llm.with_structured_output(_ObservationBatch).invoke(prompt)
        except Exception as exc:
            print(f"[pricing_engine] extraction failed for a page batch: {exc}")
            continue

        query_by_url = {r["url"]: r["query"] for r in batch}
        for raw in extracted.observations or []:
            try:
                value = float(raw.raw_value)
            except (TypeError, ValueError):
                continue
            if value <= 0 or not raw.source_url:
                continue
            if raw.source_url not in supplied_urls:
                print(f"[pricing_engine] dropped observation citing an unsupplied URL: {raw.source_url}")
                continue
            if looks_like_specification_number(value, raw.item_description):
                print(
                    f"[pricing_engine] dropped {value:g} as a specification misread from "
                    f"{raw.item_description[:60]!r}"
                )
                continue

            source_type = (raw.source_type or "unknown").strip().lower()
            if source_type not in _VALID_SOURCE_TYPES:
                source_type = "unknown"

            observations.append(
                PriceObservation(
                    raw_value=value,
                    currency=(raw.currency or "EGP").upper().strip()[:5],
                    unit_basis=raw.unit_basis or "",
                    item_description=raw.item_description or "",
                    evidence_quote=raw.evidence_quote or "",
                    source_url=raw.source_url,
                    source_type=source_type,  # type: ignore[arg-type]
                    quote_date=_parse_date(raw.quote_date),
                    scope=ScopeFlags(
                        vat_included=_parse_tristate(raw.vat_included),
                        installation_included=_parse_tristate(raw.installation_included),
                        warranty_included=_parse_tristate(raw.warranty_included),
                        delivery_included=_parse_tristate(raw.delivery_included),
                    ),
                    quantity_tier=float(raw.quantity_tier) if raw.quantity_tier else 1.0,
                    region=spec.region,
                    query=query_by_url.get(raw.source_url),
                )
            )

    return _dedupe_observations(observations)


_SPEC_NUMBER = re.compile(
    r"(\d+(?:\.\d+)?)\s*(ton|tonne|kva|kw|kwh|m2|m3|meter|metre|ah|volt|v|hp|litre|liter|seat|kg)\b",
    re.IGNORECASE,
)


def looks_like_specification_number(value: float, description: str) -> bool:
    """
    True when the "price" is really a specification lifted from the item's own name.

    A 25 ton crane extracted as costing 25 EGP per day is the recurring version of
    this: the model latches onto the most prominent number near the item. Prices
    almost never coincide exactly with the capacity figure, so an exact match is
    strong evidence of a misread, and a nonsense low value would otherwise drag a
    median down without ever looking anomalous enough to trip outlier rejection.
    """
    for match in _SPEC_NUMBER.finditer(description or ""):
        try:
            if abs(float(match.group(1)) - value) < 1e-6:
                return True
        except ValueError:
            continue
    return False


def _dedupe_observations(observations: List[PriceObservation]) -> List[PriceObservation]:
    """
    Collapse the same price reported from the same domain more than once.

    Without this, one supplier page reachable at several URLs would look like
    independent corroboration and inflate confidence.
    """
    seen: Dict[str, PriceObservation] = {}
    for observation in observations:
        key = observation.fingerprint()
        if key not in seen:
            seen[key] = observation
    return list(seen.values())


# --------------------------------------------------------------------------
# Internal historical reference
# --------------------------------------------------------------------------

_EGP_AMOUNT = re.compile(r"(\d[\d,\s]{2,})(?:\.\d+)?\s*(?:egp|جنيه|le\b)", re.IGNORECASE)


def extract_internal_reference(text: str) -> Optional[float]:
    """
    Pull a representative EGP figure out of the internal historical records.

    Deterministic regex plus a median rather than another model call: this value is
    only ever used as a sanity cross-check, so it does not warrant the latency, and
    a median is robust to the odd stray number picked up from surrounding prose.
    """
    if not text:
        return None
    values: List[float] = []
    for match in _EGP_AMOUNT.finditer(text):
        cleaned = re.sub(r"[,\s]", "", match.group(1))
        try:
            value = float(cleaned)
        except ValueError:
            continue
        if value > 0:
            values.append(value)
    if not values:
        return None
    values.sort()
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2


# --------------------------------------------------------------------------
# Main entry
# --------------------------------------------------------------------------


def estimate_parameter(
    activity_description: str,
    cost_driver: str,
    cost_component: str,
    cost_input: str,
    parameter_name: str,
    required_unit: str = "EGP per month",
    internal_records_text: str = "",
    llm=None,
    use_cache: bool = USE_CACHE,
) -> CostEstimate:
    """
    Estimate one cost parameter from web evidence, in its declared pricing unit.

    Never raises. The result always says how much weight it can bear, through a
    fallback ladder that degrades one rung at a time rather than jumping from a
    confident number to a blank cell:

        weighted median of qualifying evidence      (estimated)
        median of disagreeing sources               (needs_analyst_input)
        price of the closest comparable item        (insufficient_evidence)
        internal historical benchmark               (insufficient_evidence)
        the model's own benchmark, unsourced        (insufficient_evidence)

    Only the first is a settled figure. Everything below it is flagged in
    `status` and `value_basis`, shows a badge in the UI and carries a caveat into
    the exports. The bottom rung can be switched off with D2C_ALLOW_MODEL_JUDGEMENT=0,
    in which case such parameters come back with no number at all.
    """
    llm = llm or get_llm()
    warnings: List[str] = []

    spec = resolve_spec(
        activity_description, cost_driver, cost_component, cost_input,
        parameter_name, required_unit, llm=llm,
    )

    # Extracted up front so it is available to the fallback ladder even on the
    # paths that never reach aggregation.
    internal_reference = extract_internal_reference(internal_records_text)

    observations: List[PriceObservation] = []
    queries: List[str] = []
    page_texts: Dict[str, str] = {}
    cache_hit = False

    if use_cache:
        cached = pricing_cache.get(spec)
        if cached is not None:
            observations, queries, fetched_at = cached
            cache_hit = True
            age_days = (datetime.now(timezone.utc) - fetched_at).days
            warnings.append(
                f"evidence reused from the price cache, collected {age_days} day(s) ago and "
                f"re-normalized against current rates"
            )

    if not cache_hit:
        queries = plan_queries(spec, llm=llm)
        results, page_texts = run_searches(queries)
        if not results:
            return last_resort(
                _no_evidence(spec, queries, "No search results were returned for any query."),
                spec, internal_reference, llm=llm,
            )
        observations = extract_observations(spec, results, llm=llm)
        if use_cache:
            pricing_cache.put(spec, observations, queries)

    if not observations:
        return last_resort(
            _no_evidence(
                spec, queries,
                f"Searched {len(queries)} queries but no page contained a quotable price for "
                f"{spec.describe()}.",
            ),
            spec, internal_reference, llm=llm,
        )

    normalized = [normalize_observation(o, spec) for o in observations]
    grade_observations(normalized, spec, llm=llm)
    if page_texts:
        verify_citations(normalized, page_texts)
    else:
        warnings.append("citations were not re-verified because evidence came from the cache")

    warnings.extend(normalization_warnings(normalized))

    estimate = aggregate(
        normalized,
        spec,
        queries_used=queries,
        internal_reference_egp=internal_reference,
        extra_warnings=warnings,
    )
    estimate.cache_hit = cache_hit
    return last_resort(estimate, spec, internal_reference, llm=llm)


# --------------------------------------------------------------------------
# Fallback ladder: the rungs below the evidence pipeline
# --------------------------------------------------------------------------


class _Judgement(BaseModel):
    value: float = Field(
        ..., description="Best benchmark figure in EGP, in the requested unit. 0 if you truly have no basis."
    )
    low: float = Field(0.0, description="Low end of a plausible range in EGP, 0 if unknown")
    high: float = Field(0.0, description="High end of a plausible range in EGP, 0 if unknown")
    basis: str = Field(..., description="What the figure is based on, in one or two plain sentences")


_JUDGEMENT_PROMPT = """You are a senior procurement analyst at a telecom operator in Egypt.

A web search for the item below found no usable price. Rather than leave the line blank, give your
best benchmark figure from your own knowledge of the Egyptian market, so an analyst has a starting
point to correct.

ITEM: {spec}
Required unit: {unit}
Category: {category}
Today: {today}

Rules:
- Answer in EGP, in exactly the required unit. Do not answer in any other currency or period.
- Egypt has had heavy inflation and devaluation. A price you remember from before 2022 is far too
  low today; reason forward to a present-day Egyptian price.
- Give the plausible range as well as the central figure.
- State plainly what your figure is based on.
- If you genuinely have no basis for this item, return 0 and say so. A blank is better than a number
  with nothing behind it."""


def judge_from_model(spec: ItemSpec, llm=None) -> Optional[_Judgement]:
    """Ask the model for a benchmark figure. Returns None if it declines or fails."""
    llm = llm or get_llm()
    try:
        judgement = llm.with_structured_output(_Judgement).invoke(
            _JUDGEMENT_PROMPT.format(
                spec=spec.describe(),
                unit=spec.required_unit,
                category=spec.category,
                today=datetime.now(timezone.utc).date().isoformat(),
            )
        )
    except Exception as exc:
        print(f"[pricing_engine] model judgement failed for {spec.canonical_name!r}: {exc}")
        return None
    if not judgement or not judgement.value or float(judgement.value) <= 0:
        return None
    return judgement


def last_resort(
    estimate: CostEstimate,
    spec: ItemSpec,
    internal_reference_egp: Optional[float],
    llm=None,
) -> CostEstimate:
    """
    Fill in a figure when the evidence pipeline came back with none.

    Internal history is tried first: it is a real number from a real project, even
    though its unit basis cannot be verified against the parameter's declared unit.
    Below that sits the model's own benchmark, which has no source at all and says
    so. Both are marked in `value_basis` and neither changes the status away from
    'insufficient_evidence' — the point is to give the analyst somewhere to start,
    not to pretend the search succeeded.
    """
    if estimate.value_egp is not None:
        return estimate

    if internal_reference_egp and internal_reference_egp > 0:
        estimate.value_egp = float(internal_reference_egp)
        estimate.value_basis = "internal_benchmark"
        estimate.confidence = "low"
        estimate.warnings.append(
            f"no web evidence; using {internal_reference_egp:,.0f} EGP from internal historical "
            f"records as a placeholder — its unit basis could not be checked against "
            f"'{spec.required_unit}'"
        )
        estimate.justification = (
            f"PROVISIONAL — no usable web evidence was found. {internal_reference_egp:,.0f} EGP is "
            f"shown as a placeholder, taken from internal historical records for comparable work. "
            f"Its unit basis is unverified against the required unit ({spec.required_unit}), so "
            f"check it before use.\n\n" + estimate.justification
        )
        return estimate

    if not ALLOW_MODEL_JUDGEMENT:
        return estimate

    judgement = judge_from_model(spec, llm=llm)
    if judgement is None:
        return estimate

    estimate.value_egp = float(judgement.value)
    estimate.value_basis = "model_judgement"
    estimate.confidence = "low"
    if judgement.low > 0 and judgement.high > 0:
        estimate.low_egp = float(judgement.low)
        estimate.high_egp = float(judgement.high)
    estimate.warnings.append(
        "figure is an unsourced model estimate, not evidence — no price was found for this item "
        "on the web or in internal records"
    )
    estimate.justification = (
        f"UNSOURCED ESTIMATE — no price for this item was found on the web or in internal records. "
        f"{judgement.value:,.0f} EGP ({spec.required_unit}) is a benchmark figure from the model's own "
        f"knowledge, with NO source behind it. Basis given: {judgement.basis.strip()} "
        f"Treat it as a starting point to correct, not as an estimate.\n\n" + estimate.justification
    )
    return estimate


def _no_evidence(spec: ItemSpec, queries: List[str], message: str) -> CostEstimate:
    """
    The one case that genuinely has no number: nothing was retrieved, so there is not
    even a closest comparable item to fall back on.
    """
    return CostEstimate(
        status="insufficient_evidence",
        value_egp=None,
        value_basis="none",
        unit=spec.required_unit,
        confidence="none",
        justification=message,
        queries_used=queries,
        spec=spec,
        warnings=[message],
    )
