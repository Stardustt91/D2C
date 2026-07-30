"""
Decide whether a retrieved price is a price for the item we actually asked about.

Graded in three tiers so that cost tracks difficulty:

  1. Hard exclusions and numeric-specification conflicts, decided arithmetically.
     A "50 ton crane" is not evidence for a 25 ton crane no matter how similar the
     wording, and this is exactly the case embeddings get wrong — cosine similarity
     between two crane descriptions differing only in tonnage is very high.
  2. Lexical overlap, which settles the obvious matches and obvious misses.
  3. A single batched LLM judgement for whatever is left in the middle.

Grades carry different consequences downstream: exact and equivalent observations
form the estimate, analogous ones are heavily down-weighted and force a confidence
cap, and unrelated ones are dropped outright rather than merely discounted.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Sequence, Tuple

from pricing_models import ItemSpec, MatchGrade, NormalizedObservation

_STOPWORDS = {
    "the", "a", "an", "of", "for", "and", "or", "in", "on", "at", "to", "with",
    "per", "price", "prices", "cost", "costs", "rate", "rates", "egp", "usd",
    "egypt", "egyptian", "new", "used", "sale", "buy", "best",
}

# Dimensions where a numeric mismatch is decisive rather than suggestive.
_SPEC_UNIT_PATTERNS = [
    (r"(\d+(?:\.\d+)?)\s*(ton|tonne|t)\b", "capacity_ton"),
    (r"(\d+(?:\.\d+)?)\s*(kva)\b", "power_kva"),
    (r"(\d+(?:\.\d+)?)\s*(kw)\b", "power_kw"),
    (r"(\d+(?:\.\d+)?)\s*(kwh)\b", "energy_kwh"),
    (r"(\d+(?:\.\d+)?)\s*(m2|sqm|square met[er]+)\b", "area_sqm"),
    (r"(\d+(?:\.\d+)?)\s*(m3|cubic met[er]+)\b", "volume_m3"),
    (r"(\d+(?:\.\d+)?)\s*(meter|metre|m)\b", "length_m"),
    (r"(\d+(?:\.\d+)?)\s*(ah)\b", "battery_ah"),
    (r"(\d+(?:\.\d+)?)\s*(v|volt)\b", "voltage_v"),
]


_ARABIC_RANGE = r"؀-ۿݐ-ݿ"


def _tokens(text: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in _STOPWORDS and len(t) > 1}


def _is_mostly_arabic(text: str) -> bool:
    """
    True when a description is written in Arabic script rather than Latin.

    Lexical overlap cannot compare an Arabic description against an English item
    name, so these are handed to the LLM judge instead of being scored. Without
    this check they score zero overlap and are rejected as unrelated — which would
    throw away precisely the Egyptian supplier and classified pages the Arabic
    queries were added to find.
    """
    arabic = len(re.findall(f"[{_ARABIC_RANGE}]", text or ""))
    latin = len(re.findall(r"[a-zA-Z]", text or ""))
    return arabic > 0 and arabic >= latin


def _extract_dimensions(text: str) -> Dict[str, float]:
    """Pull numeric specifications out of free text, keyed by physical dimension."""
    found: Dict[str, float] = {}
    lowered = (text or "").lower()
    for pattern, dimension in _SPEC_UNIT_PATTERNS:
        match = re.search(pattern, lowered)
        if match and dimension not in found:
            try:
                found[dimension] = float(match.group(1))
            except ValueError:
                continue
    return found


def _spec_dimensions(spec: ItemSpec) -> Dict[str, float]:
    blob = " ".join([spec.canonical_name] + [f"{k} {v}" for k, v in spec.attributes.items()])
    return _extract_dimensions(blob)


def deterministic_grade(spec: ItemSpec, description: str) -> Tuple[Optional[MatchGrade], str]:
    """
    Settle the cases that do not need a model.

    Returns (grade, reason), or (None, reason) when the decision should be deferred
    to the LLM judge.
    """
    desc_lower = (description or "").lower()
    if not desc_lower.strip():
        return "unrelated", "observation carries no item description"

    # Explicit exclusions named in the spec.
    for excluded in spec.exclusions:
        if excluded and excluded.lower().strip() in desc_lower:
            return "unrelated", f"description matches an excluded item: {excluded!r}"

    # Numeric specification conflicts are decisive.
    spec_dims = _spec_dimensions(spec)
    obs_dims = _extract_dimensions(desc_lower)
    for dimension, wanted in spec_dims.items():
        got = obs_dims.get(dimension)
        if got is None or wanted <= 0:
            continue
        ratio = got / wanted
        if ratio < 0.6 or ratio > 1.67:
            return "unrelated", (
                f"specification conflict on {dimension}: wanted {wanted:g}, source describes {got:g}"
            )
        if not (0.9 <= ratio <= 1.11):
            return "analogous", (
                f"{dimension} differs: wanted {wanted:g}, source describes {got:g} — comparable but not the same item"
            )

    # Arabic descriptions cannot be scored against an English item name, so they go
    # to the judge with the numeric checks above already applied.
    if _is_mostly_arabic(description):
        return None, "Arabic description — deferred to comparability judgement"

    # Lexical overlap against the canonical name and its synonyms.
    name_tokens = _tokens(spec.canonical_name)
    synonym_tokens = set().union(*[_tokens(s) for s in spec.synonyms]) if spec.synonyms else set()
    wanted_tokens = name_tokens | synonym_tokens
    if not wanted_tokens:
        return None, "no lexical basis for comparison"

    desc_tokens = _tokens(desc_lower)
    if not desc_tokens:
        return "unrelated", "description has no comparable terms"

    covered = len(name_tokens & desc_tokens) / max(len(name_tokens), 1)
    broad = len(wanted_tokens & desc_tokens) / max(len(wanted_tokens), 1)

    if covered >= 0.85 and spec_dims and all(d in obs_dims for d in spec_dims):
        return "exact", f"all specification dimensions match and {covered:.0%} of the item name is present"
    if covered == 0.0 and broad == 0.0:
        return "unrelated", "no overlap with the item name or any of its synonyms"
    return None, f"lexical overlap {covered:.0%} on the name, {broad:.0%} including synonyms — needs judgement"


# --------------------------------------------------------------------------
# Geography
# --------------------------------------------------------------------------

_EGYPT_DOMAIN_HINTS = (".eg", ".com.eg", ".org.eg", ".net.eg")
_EGYPT_TEXT_HINTS = (
    "egypt", "egyptian", "cairo", "alexandria", "giza", "مصر", "القاهرة", "الاسكندرية", "الإسكندرية",
)
_FOREIGN_TEXT_HINTS = (
    "united states", "usa", "u.s.", "united kingdom", "dubai", "u.a.e", "uae", "abu dhabi",
    "saudi", "riyadh", "qatar", "doha", "kuwait", "india", "pakistan", "nigeria", "kenya",
    "south africa", "australia", "canada", "europe",
)
_FOREIGN_TLDS = (
    ".us", ".co.uk", ".uk", ".ae", ".sa", ".qa", ".kw", ".in", ".pk", ".de", ".fr",
    ".es", ".it", ".au", ".ca", ".za", ".ng", ".ke",
)


def region_signal(spec: ItemSpec, observation) -> str:
    """
    Classify an observation as 'in_region', 'foreign' or 'unknown' relative to the spec.

    Currency is the strongest available signal: a price quoted in EGP is almost
    always an Egyptian price, and a price for the same work quoted in dollars on a
    site that never mentions Egypt is almost always a different market. That matters
    because labour and hire rates differ several-fold between markets, so treating a
    US crane rate as evidence for a Cairo one is not a small error.
    """
    if (spec.region or "").strip().lower() not in ("egypt", "eg", ""):
        return "unknown"  # only Egypt is modelled here

    domain = (observation.source_domain or "").lower()
    text = f"{observation.item_description} {observation.evidence_quote} {observation.region or ''}".lower()
    currency = (observation.currency or "").upper().strip()

    # Order matters. An explicit mention of Egypt is the only signal strong enough
    # to override a foreign domain, and a foreign domain outranks the currency:
    # a Gulf site quoting a figure in EGP is more often a mislabelled conversion
    # than a genuine Egyptian quote, and including one silently moves the estimate
    # to another market. Losing a real local price to this rule costs an
    # abstention, which is the cheaper mistake.
    if any(hint in text for hint in _EGYPT_TEXT_HINTS):
        return "in_region"
    if any(domain.endswith(hint) for hint in _EGYPT_DOMAIN_HINTS):
        return "in_region"
    if any(domain.endswith(tld) for tld in _FOREIGN_TLDS):
        return "foreign"
    if any(hint in text for hint in _FOREIGN_TEXT_HINTS):
        return "foreign"
    if currency == "EGP":
        return "in_region"
    # Priced in a foreign currency with nothing tying it to Egypt.
    if currency and currency != "EGP":
        return "foreign"
    return "unknown"


def apply_region_preference(observations: Sequence[NormalizedObservation], spec: ItemSpec) -> None:
    """
    Prefer in-region evidence, and fall back to foreign benchmarks only when there
    is none.

    When local prices exist, foreign ones are excluded outright rather than merely
    down-weighted — a handful of US quotes will otherwise dominate a weighted median
    and drag the estimate to a different market's price level. When no local price
    can be found, foreign evidence is kept but capped at 'analogous' and labelled,
    so the estimate is visibly a cross-market benchmark rather than a local quote.
    """
    signals = [(norm, region_signal(spec, norm.observation)) for norm in observations]
    has_local = any(
        signal == "in_region" and norm.match_grade != "unrelated" and norm.normalization_failed is None
        for norm, signal in signals
    )

    for norm, signal in signals:
        if signal != "foreign":
            continue
        if has_local:
            norm.match_grade = "unrelated"
            norm.match_reason = (
                f"excluded: priced in a different market ({norm.observation.source_domain or 'foreign source'}) "
                f"while Egyptian evidence is available"
            )
        elif norm.match_grade in ("exact", "equivalent"):
            norm.match_grade = "analogous"
            norm.match_reason = (
                f"{norm.match_reason} | capped to analogous: this is a foreign-market benchmark, "
                f"no Egyptian price was found"
            ).strip(" |")


_JUDGE_PROMPT = """You are a procurement analyst checking whether retrieved prices are prices for the item actually being sourced.

ITEM REQUIRED:
{spec}

Attributes that matter for price:
{attributes}

Items that would be the WRONG match:
{exclusions}

For each candidate below, decide how well the priced item matches the required item:
- "exact": the same item at the same specification
- "equivalent": a different brand or supplier, but interchangeable for costing
- "analogous": related and informative, but a different specification, scope or class
- "unrelated": a different item, or the price is for something else entirely (rental vs purchase, a part vs the whole, a different market)

Be strict. A price for a similar item at a different capacity, tier or scope is "analogous", not "equivalent". A price for a different thing entirely is "unrelated" — do not stretch to make it fit.

CANDIDATES:
{candidates}

Return a JSON array with one object per candidate, in the same order, each with:
  "index": the candidate number
  "grade": one of exact | equivalent | analogous | unrelated
  "reason": one short sentence naming the specific attribute that decided it

Return only the JSON array."""


def grade_observations(
    observations: Sequence[NormalizedObservation],
    spec: ItemSpec,
    llm=None,
) -> List[NormalizedObservation]:
    """
    Assign a match grade to every observation, mutating them in place and returning
    the same list.

    The LLM is optional: without it, deterministic grading stands and anything
    undecided is treated as "analogous", which keeps the observation in play but
    heavily down-weighted. That keeps the whole module testable offline.
    """
    undecided: List[int] = []

    for index, norm in enumerate(observations):
        grade, reason = deterministic_grade(spec, norm.observation.item_description)
        if grade is not None:
            norm.match_grade = grade
            norm.match_reason = reason
            continue
        undecided.append(index)
        norm.match_grade = "analogous"
        norm.match_reason = reason

    if not undecided or llm is None:
        apply_region_preference(observations, spec)
        return list(observations)

    candidates = "\n".join(
        f"{i + 1}. {observations[idx].observation.item_description.strip()[:300]}"
        f"  [priced at {observations[idx].observation.raw_value:g} "
        f"{observations[idx].observation.currency} {observations[idx].observation.unit_basis}]"
        for i, idx in enumerate(undecided)
    )
    prompt = _JUDGE_PROMPT.format(
        spec=spec.describe(),
        attributes=json.dumps(spec.attributes, ensure_ascii=False) if spec.attributes else "(none specified)",
        exclusions="\n".join(f"- {e}" for e in spec.exclusions) if spec.exclusions else "(none specified)",
        candidates=candidates,
    )

    try:
        response = llm.invoke(prompt)
        content = getattr(response, "content", response)
        verdicts = _parse_verdicts(str(content))
    except Exception as exc:
        print(f"[pricing_match] LLM grading failed, keeping deterministic grades: {exc}")
        apply_region_preference(observations, spec)
        return list(observations)

    valid_grades = {"exact", "equivalent", "analogous", "unrelated"}
    for verdict in verdicts:
        try:
            position = int(verdict.get("index", 0)) - 1
            grade = str(verdict.get("grade", "")).strip().lower()
        except (TypeError, ValueError):
            continue
        if not (0 <= position < len(undecided)) or grade not in valid_grades:
            continue
        target = observations[undecided[position]]
        target.match_grade = grade  # type: ignore[assignment]
        target.match_reason = str(verdict.get("reason", "")).strip() or target.match_reason

    # Applied last, so it overrides whatever the judge concluded about the item
    # itself: an exact item match in the wrong market is still the wrong price.
    apply_region_preference(observations, spec)
    return list(observations)


def _parse_verdicts(content: str) -> List[dict]:
    """Pull the JSON array out of a model response that may be fenced or prefaced."""
    text = content.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        parsed = json.loads(text[start : end + 1])
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []
