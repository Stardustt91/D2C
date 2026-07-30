"""
Keeps the cost breakdown small, material, and actually priceable.

Two problems this solves.

FIRST — the breakdown grew without limit. Every generator prompt invited the model
to "feel free to add new items you think are necessary", nothing asked for the
*smallest* set that explains the cost, and each level multiplied the one below it.
A single run produced 480 cost parameters. Beyond the review burden, each parameter
costs a live search fan-out, so the breadth is also the dominant cost driver of a
run.

SECOND — many generated parameters could never be priced. Percentages of other
parameters (taxes, insurance, markup), allocations, contingencies and catch-all
"miscellaneous" lines have no market price, so searching for them wastes a fan-out
and returns either an abstention or a spurious match. Those belong in the formula
as arithmetic, not in the breakdown as things to go and look up.

Everything here is deterministic and offline, so the rules can be tested and tuned
without running the model.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Iterable, List, Literal, Optional, Sequence, Tuple

# --------------------------------------------------------------------------
# Breadth budget
# --------------------------------------------------------------------------

#: How much detail the breakdown should carry. Set D2C_SCOPE_PROFILE to change it.
#: The caps multiply, so a small change at the top has a large effect at the bottom:
#: the 'detailed' profile permits roughly seven times the parameters of 'lean'.
SCOPE_PROFILES: Dict[str, Dict[str, int]] = {
    "lean":     {"drivers": 5, "components": 3, "inputs": 3, "parameters": 2},
    "standard": {"drivers": 6, "components": 4, "inputs": 4, "parameters": 3},
    "detailed": {"drivers": 8, "components": 5, "inputs": 5, "parameters": 4},
}

_PROFILE = os.environ.get("D2C_SCOPE_PROFILE", "lean").strip().lower()
if _PROFILE not in SCOPE_PROFILES:
    print(f"[estimation_scope] unknown D2C_SCOPE_PROFILE {_PROFILE!r}; falling back to 'lean'")
    _PROFILE = "lean"

CAPS = dict(SCOPE_PROFILES[_PROFILE])

# Individual overrides win over the profile.
for _level, _env in (
    ("drivers", "D2C_MAX_DRIVERS"),
    ("components", "D2C_MAX_COMPONENTS"),
    ("inputs", "D2C_MAX_INPUTS"),
    ("parameters", "D2C_MAX_PARAMETERS"),
):
    _raw = os.environ.get(_env)
    if _raw and _raw.isdigit() and int(_raw) > 0:
        CAPS[_level] = int(_raw)

PROFILE_NAME = _PROFILE


def worst_case_parameter_count() -> int:
    """Upper bound on parameters for the active profile — the number that gets searched."""
    return CAPS["drivers"] * CAPS["components"] * CAPS["inputs"] * CAPS["parameters"]


# --------------------------------------------------------------------------
# Materiality
# --------------------------------------------------------------------------

Materiality = Literal["high", "medium", "low"]

_MATERIALITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _materiality_rank(item: Any) -> int:
    value = ""
    if isinstance(item, dict):
        value = str(item.get("materiality", "") or "")
    else:
        value = str(getattr(item, "materiality", "") or "")
    return _MATERIALITY_ORDER.get(value.strip().lower(), 1)


def prune_to_cap(items: Sequence[Any], cap: int) -> List[Any]:
    """
    Trim a level to its cap, keeping the most material items.

    Ordering is stable within a materiality band, so the model's own sequencing is
    preserved among equals. Sorting before truncating matters: the previous code
    sliced the raw list, which discarded whatever happened to be generated last
    regardless of how important it was.
    """
    if cap <= 0 or len(items) <= cap:
        return list(items)
    ordered = sorted(enumerate(items), key=lambda pair: (_materiality_rank(pair[1]), pair[0]))
    kept_indices = {index for index, _ in ordered[:cap]}
    return [item for index, item in enumerate(items) if index in kept_indices]


def drop_immaterial(items: Sequence[Any], keep_at_least: int = 1) -> List[Any]:
    """
    Remove items the model itself marked 'low', provided something material remains.

    A breakdown made entirely of low-materiality lines is more likely to be a
    mis-labelling than a genuinely trivial cost, so at least `keep_at_least` items
    always survive.
    """
    material = [item for item in items if _materiality_rank(item) < 2]
    if len(material) >= keep_at_least:
        return material
    return list(items[:keep_at_least]) if items else []


# --------------------------------------------------------------------------
# Estimability
# --------------------------------------------------------------------------

EstimationMethod = Literal["market_price", "derived", "internal_benchmark", "not_estimable"]

#: Names too vague to price at all. Nothing can be searched for "miscellaneous".
_VAGUE_TERMS = (
    "miscellaneous", "misc", "other costs", "other cost", "others", "sundry", "sundries",
    "various", "general expenses", "general expense", "unforeseen", "buffer", "reserve",
    "additional costs", "additional cost", "etc",
)

#: A concrete pricing unit is required. Without one there is nothing to price *in*,
#: and the estimator cannot check what it got back.
_UNIT_TOKENS = ("per ", "one-time", "one time", "each", "lump")

#: Trailing words that describe the measurement rather than the thing, stripped
#: before taking the head noun so "Salary Tax Rate" resolves to "tax".
_GENERIC_SUFFIXES = {
    "cost", "costs", "price", "prices", "rate", "rates", "amount", "amounts",
    "value", "values", "charge", "charges", "expense", "expenses", "total",
    "monthly", "daily", "annual", "yearly", "egp", "per", "month", "day", "year",
}


def _head_noun(name: str) -> str:
    """
    The noun a parameter name is really about.

    Whether a term makes something derived depends on its position, not its
    presence: "Salary Taxes" is a tax, while "Tax Consultant Fee" is a fee for a
    service that a firm will happily quote. Matching on the head noun distinguishes
    the two, where a substring or even a whole-word match cannot.
    """
    tokens = re.findall(r"[a-z]+", (name or "").lower().replace("-", ""))
    while tokens and tokens[-1] in _GENERIC_SUFFIXES:
        tokens.pop()
    return tokens[-1] if tokens else ""


#: Head nouns that mean "computed from another number".
_DERIVED_HEAD_NOUNS = {
    "tax", "taxes", "vat", "insurance", "markup", "margin", "overhead", "contingency",
    "escalation", "inflation", "percentage", "percent", "allocation", "provision",
    "commission", "profit", "share", "surcharge", "levy",
}


def classify_parameter(
    name: str,
    unit: str = "",
    justification: str = "",
) -> Tuple[EstimationMethod, str]:
    """
    Decide how — or whether — a cost parameter can be priced.

    Returns (method, reason).

    - 'market_price'       a real thing with a market price; the estimator searches for it
    - 'derived'            a function of another parameter; belongs in the formula
    - 'not_estimable'      too vague to price; should not be in the breakdown at all
    - 'internal_benchmark' priceable only from internal historical records

    The derived test looks at the head noun rather than anywhere in the string,
    because position is what carries the meaning: "Salary Taxes" is a tax, while
    "Tax Consultant Fee" is a professional service a firm will quote for.
    """
    text = f"{name} {justification}".lower()
    unit_text = (unit or "").lower().strip()
    lowered_name = (name or "").lower()

    for term in _VAGUE_TERMS:
        if term in lowered_name:
            return "not_estimable", (
                f"'{name}' is a catch-all with no market price; fold it into a named line item "
                f"or express it as a percentage in the formula"
            )

    if _head_noun(name) in _DERIVED_HEAD_NOUNS:
        return "derived", (
            f"'{name}' is computed from another value rather than quoted by a supplier; "
            f"express it in the formula (e.g. [Base] * 1.14) instead of pricing it separately"
        )

    if "%" in lowered_name or re.search(r"\b\d+\s*%", text):
        return "derived", f"'{name}' is expressed as a percentage; it belongs in the formula"

    if not unit_text or not any(token in unit_text for token in _UNIT_TOKENS):
        return "not_estimable", (
            f"'{name}' has no concrete pricing unit ({unit!r}); without one there is nothing to "
            f"price it in and no way to check the result"
        )

    return "market_price", f"'{name}' names a purchasable item priced in {unit}"


def filter_estimable_parameters(
    parameters: Sequence[dict],
    allow_empty: bool = False,
) -> Tuple[List[dict], List[dict]]:
    """
    Split parameters into those worth pricing and those that should not be searched.

    Returns (keep, removed). Removed entries carry 'removal_reason' and
    'estimation_method' so the UI can explain the omission rather than silently
    dropping something the analyst expected to see.

    Unless `allow_empty`, at least one parameter survives: a cost input with no
    parameters cannot be costed at all, which is worse than carrying one the
    estimator may abstain on. Callers that already hold a usable parameter elsewhere
    — such as one protected by a formula reference — pass allow_empty=True so a
    genuine catch-all is not resurrected for no reason.
    """
    keep: List[dict] = []
    removed: List[dict] = []

    for parameter in parameters:
        method, reason = classify_parameter(
            parameter.get("parameter_name", ""),
            parameter.get("unit", ""),
            parameter.get("justification", ""),
        )
        enriched = dict(parameter)
        enriched["estimation_method"] = method
        if method == "market_price":
            keep.append(enriched)
        else:
            enriched["removal_reason"] = reason
            removed.append(enriched)

    if not keep and removed and not allow_empty:
        rescued = dict(removed[0])
        rescued["estimation_method"] = "market_price"
        rescued.pop("removal_reason", None)
        return [rescued], removed[1:]

    return keep, removed


# --------------------------------------------------------------------------
# Shared prompt language
#
# Held here so all four generators say the same thing. When they drifted apart
# previously, each level independently decided how much detail was appropriate and
# the products multiplied.
# --------------------------------------------------------------------------


def parsimony_rules(level: str, cap: int) -> str:
    """Instruction block asking for the smallest set that explains the cost."""
    return f"""
PARSIMONY (MANDATORY — fewer, larger line items are better):
- Return AT MOST {cap} {level}. Returning fewer is better and is not a failure.
- Return the SMALLEST set that explains the cost of this activity. Do not enumerate
  every conceivable item; group related small costs into one named line.
- Include an item only if leaving it out would visibly change the total. If it is
  plausibly under about 5% of the cost of its parent, merge it into a larger line
  instead of listing it separately.
- Do NOT split something into parts that are always bought together. One line for a
  thing that is procured as one thing.
- Never add an item just because a historical example contained it. The examples show
  style, not a checklist to reproduce.
- Assign materiality to each item: 'high' if it is a leading cost, 'medium' if it
  matters, 'low' if it is minor. Be honest — low-materiality items may be dropped.
"""


ESTIMABILITY_RULES = """
PRICEABILITY (MANDATORY — every cost parameter must be something a price can be found for):
- A cost parameter must name a THING A SUPPLIER SELLS or A PERSON IS PAID, quoted in a
  concrete unit. If you cannot imagine a vendor page, price list, tender or salary survey
  showing this number, it is not a cost parameter.
- Do NOT create parameters for values computed from other values. Taxes, VAT, insurance,
  social contributions, overhead, markup, contingency and escalation are all arithmetic:
  put them in the FORMULA instead. Write [Net Salary] * 1.14, not a separate 'VAT' parameter.
- Do NOT create catch-all parameters: 'Miscellaneous', 'Other costs', 'Sundries',
  'Contingency', 'Buffer'. Either name the actual cost or leave it out.
- Every parameter needs a concrete pricing unit such as 'EGP per truck per day',
  'EGP per person per month' or 'EGP per site one-time'. A parameter you cannot give a
  unit to is one you cannot price.
- Prefer one parameter priced in a clear unit over several parameters that each need
  assumptions to combine.
"""


def scope_summary() -> str:
    """One-line description of the active budget, for logs and the UI."""
    return (
        f"scope profile '{PROFILE_NAME}': at most {CAPS['drivers']} drivers x "
        f"{CAPS['components']} components x {CAPS['inputs']} inputs x {CAPS['parameters']} "
        f"parameters (worst case {worst_case_parameter_count()} priced parameters)"
    )
