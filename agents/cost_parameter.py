"""
Cost parameter identifier: for each cost input, retrieve or infer cost parameters and formula
using the vector DB new_vector_dbs/cost_parameters_faiss.

Metadata expected from the vector DB (see chunking_new_docs.ipynb):
  Project, D2C, Project Description, Cost Driver, Cost Component, Cost Input,
  Cost Parameters (dict name -> "value EGP" or list of names), Formula (str or null).
"""
from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel, Field
from estimation_scope import (
    CAPS,
    ESTIMABILITY_RULES,
    filter_estimable_parameters,
    parsimony_rules,
    prune_to_cap,
)
from env_config import reasoning_llm, embeddings_client
from langchain_classic.vectorstores import FAISS
from structure_formatter import (
    format_structure_for_parameter_estimation,
    format_resource_plan,
    SCALING_CLASS_DEFINITIONS,
)
import copy
import re
from collections import defaultdict

llm = reasoning_llm(reasoning_effort="low", max_completion_tokens=12000)

embeddings = embeddings_client()

# parameters_db = FAISS.load_local(
#     "vector_dbs/cost_parameters_faiss",
#     embeddings,
#     allow_dangerous_deserialization=True
# )

parameters_db = FAISS.load_local(
    "new_vector_dbs/cost_parameters_faiss",
    embeddings,
    allow_dangerous_deserialization=True,
)

cost_parameters_retriever = parameters_db.as_retriever(
    search_type="similarity",
    search_kwargs={"k": 20},
)


def _parse_egp_value(s: Any) -> Optional[float]:
    """Parse '1500 EGP' or '1500' to float. Returns None if not parseable."""
    if s is None:
        return None
    s = str(s).strip().replace(",", "")
    m = re.search(r"[\d.]+", s)
    if m:
        try:
            return float(m.group())
        except ValueError:
            pass
    return None


def _metadata_get(md: dict, *keys: str) -> Any:
    for k in keys:
        if md.get(k) is not None and md.get(k) != "":
            return md.get(k)
    return None


from collections import defaultdict
import math

def return_structured_cost_parameters(activity_description: str, cost_driver: str, cost_component: str, cost_input: str) -> str:
    query = f"Activity Description: {activity_description}, Cost Driver: {cost_driver}, Cost Component: {cost_component}, Cost Input: {cost_input}"
    results = cost_parameters_retriever.invoke(query)

    # key = (Project, Cost Driver, Cost Component, Cost Input) -> (params_text, formula)
    projects = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    project_info = {}

    for res in results:
        meta = res.metadata or {}

        def _s(v, default=""):
            if v is None:
                return default
            if isinstance(v, float) and math.isnan(v):
                return default
            return str(v).strip()

        project = _s(meta.get("Project"), "Unknown Project")
        d2c = _s(meta.get("D2C"))
        desc = _s(meta.get("Project Description"))
        driver = _s(meta.get("Cost Driver"))
        component = _s(meta.get("Cost Component"))
        inp = _s(meta.get("Cost Input", meta.get("Cost input")))
        cp_raw = meta.get("Cost Parameters", meta.get("Cost parameters", {}))
        formula_raw = meta.get("Formula")

        # store project info
        project_info[project] = {"description": desc, "d2c": d2c}

        if cp_raw is not None or (formula_raw and str(formula_raw).strip() and not (isinstance(formula_raw, float) and math.isnan(formula_raw))):
            # process Cost Parameters
            params_str = ""
            if isinstance(cp_raw, dict):
                params_str = ", ".join(str(p).strip() for p in cp_raw.keys() if p)
            elif isinstance(cp_raw, (list, tuple)):
                params_str = ", ".join(
                    str(p).strip() if isinstance(p, str) else str(p.get("parameter_name", p.get("name", p))).strip()
                    for p in cp_raw if p
                )
            elif cp_raw is not None:
                params_str = str(cp_raw).strip()

            # process Formula
            formula_str = ""
            if formula_raw and str(formula_raw).strip() and not (isinstance(formula_raw, float) and math.isnan(formula_raw)):
                formula_str = str(formula_raw).strip()

            projects[project][driver][(component, inp)] = (params_str, formula_str)

    # build output text
    text_output = []

    for project, drivers in projects.items():
        proj_info = project_info.get(project, {})
        text_output.append(f"Project: {project}")
        if proj_info.get("d2c"):
            text_output.append(f"D2C: {proj_info['d2c']}")
        text_output.append(f"Project Description: {proj_info.get('description', '')}")
        
        for driver, components in sorted(drivers.items()):
            text_output.append(f"For Cost Driver {driver}:")
            for (comp, inp), (params_str, formula_str) in sorted(components.items()):
                text_output.append(f"  Cost Component {comp}, Cost Input {inp}:")
                text_output.append(f"    Cost Parameters: {params_str or 'N/A'}")
                if formula_str:
                    text_output.append(f"    Formula: {formula_str}")
        text_output.append("")  # spacing between projects

    return "\n".join(text_output)


## The agent that identifies cost parameters (and formula) for a cost input
def cost_parameters_identifier(
    activity_description: str,
    cost_driver: str,
    cost_component: str,
    cost_input: str,
    structure_so_far: Optional[dict] = None,
    resource_plan: Optional[dict] = None,
    prompt_log_path: Optional[str] = None,
    prompt_log_section: Optional[str] = None,
) -> tuple:
    """
    For a given cost input, return (cost_parameters_list, formula, formula_justification).

    - cost_parameters_list: list of {"parameter_name": str, "unit": str, "monthly_cost_egp": float|None, "justification": str}
    - formula: str evaluating to the MONTHLY cost in EGP, e.g. "3*[Daily Rental Rate]*22"

    Numeric literals in the formula must come from the resource plan (counts, working days,
    duration) or explicit numbers in the activity description — never invented here.
    """


    historical_parameters = return_structured_cost_parameters(
        activity_description, cost_driver, cost_component, cost_input
    )

    class CostParameterItem(BaseModel):
        parameter_name: str = Field(..., description="Name of the cost parameter")
        unit: str = Field(
            ...,
            description="Pricing unit and basis of this parameter in EGP, e.g. 'EGP per truck per day', "
                        "'EGP per person per month', 'EGP per site one-time', 'EGP per month'. The cost "
                        "estimator will price the parameter in EXACTLY this unit; the formula does all "
                        "time/quantity conversion.",
        )
        justification: str = Field(
            "",
            description="Why this cost parameter is needed for this cost input (e.g. 'Net Salary is the base monthly pay before taxes').",
        )
        materiality: str = Field(
            "medium",
            description="'high' if this dominates the cost input, 'medium' if it matters, 'low' if minor. "
                        "Low-materiality parameters may be dropped, so be honest.",
        )
        price_source: str = Field(
            "",
            description="Where a real price for this would be found — e.g. 'equipment rental company price list', "
                        "'Egyptian salary survey', 'published tariff'. If you cannot name a plausible source, "
                        "this is not a cost parameter and belongs in the formula or should be left out.",
        )

    class CostParametersResponse(BaseModel):
        cost_parameters: List[CostParameterItem] = Field(
            ..., description="List of cost parameter names with unit and justification"
        )
        formula: str = Field(
            "",
            description="Formula that evaluates to the MONTHLY cost in EGP for this cost input, combining "
                        "[Parameter Name] placeholders with numeric literals taken from the resource plan. "
                        "Example: 3*[Daily Rental Rate]*22 — 3 trucks (from plan) x daily rate x 22 working days. "
                        "The number of parameters does NOT decide this: a SINGLE parameter still needs a formula "
                        "whenever a count applies (e.g. 3*[Daily Rental Rate]*22) OR its unit is not already "
                        "monthly (e.g. [Annual License]/12, [Daily Rate]*22, 40*[Permit Fee]/3). Leave it empty "
                        "ONLY when there is exactly one parameter already priced 'EGP per month' with no count "
                        "applying (e.g. one office's monthly rent).",
        )
        formula_justification: str = Field(
            "",
            description="Maps EVERY numeric literal in the formula to its source in the resource plan or activity "
                        "description (e.g. '3 = truck pool size from the resource plan; 22 = working days per month'). "
                        "Also states why each parameter is recurring vs one-time.",
        )

    cost_parameter_llm = llm.with_structured_output(CostParametersResponse)

    structure_block = ""
    if structure_so_far:
        structure_context = format_structure_for_parameter_estimation(
            structure_so_far, cost_driver, cost_component, cost_input
        )
        if structure_context:
            structure_block = f"\n\nStructure so far (current component only):\n{structure_context}\n"

    plan_block = format_resource_plan(resource_plan)
    if plan_block:
        plan_block = f"\n\n{plan_block}\n\n{SCALING_CLASS_DEFINITIONS}\n"
        plan_rules = """QUANTITY RULES (MANDATORY — the resource plan above is the single source of truth for counts):
- Every numeric literal in the formula MUST be traceable to the resource plan (resource quantities, crew count,
  working days per month, duration) or to an explicit number in the activity description. NEVER invent a count.
- For a capacity_pool resource (trucks, cranes, test kits...): use the POOL quantity from the plan. NEVER use
  the volume driver value (e.g. use 3 trucks, NOT 40 because there are 40 sites).
- For a per_unit_of_volume resource: use the volume driver value (or the plan's stated ratio x volume).
- For a time_based resource: use its fixed quantity from the plan.
- For a fixed_one_time item: it occurs once; amortize as shown below."""
    else:
        plan_rules = """QUANTITY RULES:
- No resource plan is available. Use ONLY quantities explicitly stated in the activity description. If a count
  is not stated, choose a conservative value and clearly flag the assumption in formula_justification.
- NEVER default a shared resource's count to the volume driver value (e.g. a 40-site project does not need
  40 trucks — a small shared pool serves all sites)."""

    prompt = f"""You are a Design-to-Cost analyst for the supply chain team at Vodafone Egypt.
An activity has cost drivers, cost components, and cost inputs. Each cost input may be broken down into cost parameters and a formula.

You will be given an activity description, cost driver, cost component, and cost input. Your role is to identify the Cost Parameters for this cost input and, if needed, a formula that combines them.

An example of Cost Parameters (and formula) for historical activities are:
{historical_parameters}

These examples show the STYLE of parameters/formulas. Their numeric literals reflect the scale of THOSE projects —
do not copy their counts into this project.
You should give a brief justification for each cost parameter.
{parsimony_rules("cost parameters", CAPS["parameters"])}
{ESTIMABILITY_RULES}
{structure_block}{plan_block}
Activity Description is:
{activity_description}

Cost Driver: {cost_driver}
Cost Component: {cost_component}
Cost Input: {cost_input}

Your task:
1. List each sub-item as a cost parameter with its PRICING UNIT and a brief justification.
2. Build a formula from [Parameter Name] placeholders and numeric literals that evaluates to the MONTHLY cost
   in EGP. THE NUMBER OF PARAMETERS DOES NOT DECIDE WHETHER A FORMULA IS NEEDED — a single parameter usually
   STILL needs one:
   - If a count/quantity from the resource plan applies (e.g. 3 trucks, 40 sites, 5 crew), the formula
     multiplies by that count — e.g. 3*[Daily Rental Rate]*22 — even with only ONE parameter.
   - If the parameter's unit is NOT already monthly (per day, per year, one-time), the formula converts it to
     monthly — e.g. [Annual License]/12, [Daily Rate]*22, 40*[Permit Fee]/3 — even with only ONE parameter.
   - Leave the formula EMPTY only in the narrow case of exactly one parameter that is already priced
     'EGP per month' AND has no count applying (e.g. one office's monthly rent). In that case return exactly
     one cost parameter named like the cost input with unit 'EGP per month', an empty formula, and an empty
     formula_justification.

{plan_rules}

UNIT CONVENTIONS (MANDATORY — the formula's result must be the MONTHLY cost in EGP):
- Each parameter will be priced in exactly the unit you declare. The FORMULA does all conversion to monthly:
  * unit 'EGP per X per day'   -> multiply by the working days per month (e.g. 22): count*[Rate]*22
  * unit 'EGP per X per month' -> multiply by count only: count*[Rate]
  * unit 'EGP per X per year'  -> divide by 12: count*[Rate]/12
  * one-time units ('EGP one-time', 'EGP per site one-time') -> amortize over the project duration in months:
    total one-time cost / duration_months (e.g. 40*[Permit Fee]/3 for 40 per-site permits over 3 months).
- Do NOT multiply recurring monthly parameters by the project duration — the result must stay per-month.
- Never convert a daily rate to monthly inside the parameter itself AND multiply by days in the formula —
  that double-counts. Declare the natural unit and convert exactly once, in the formula.

  Example: truck pool of 3 (from plan), daily rental priced per truck per day, 22 working days/month:
    cost_parameters: [{{"parameter_name": "Daily Rental Rate", "unit": "EGP per truck per day"}},
                      {{"parameter_name": "Fuel Cost", "unit": "EGP per truck per month"}}]
    formula: 3*[Daily Rental Rate]*22 + 3*[Fuel Cost]
    formula_justification: "3 = light truck pool size from the resource plan (capacity_pool — the trucks are
    shared across all 40 sites, so the site count is NOT used); 22 = working days per month from the plan.
    Daily Rental Rate is per truck per day so it is multiplied by 22 working days; Fuel Cost is already
    monthly per truck so it is only multiplied by the 3-truck pool."

3. ALWAYS provide a formula_justification whenever the formula contains numeric literals, mapping each literal
   to its source (resource plan entry or activity description sentence).

Return the list of cost parameters (each with parameter_name, unit and justification), the formula, and the
formula_justification."""

    if prompt_log_path and prompt_log_section:
        with open(prompt_log_path, "a", encoding="utf-8") as f:
            f.write(f"{prompt_log_section}\n[Prompt]\n{prompt}\n---\n")

    try:
        resp = cost_parameter_llm.invoke(prompt)
        cost_parameters_list = [
            {
                "parameter_name": p.parameter_name.strip(),
                "unit": (getattr(p, "unit", None) or "EGP per month").strip(),
                "monthly_cost_egp": None,
                "justification": (getattr(p, "justification", None) or "").strip(),
                "materiality": (getattr(p, "materiality", None) or "medium").strip().lower(),
                "price_source": (getattr(p, "price_source", None) or "").strip(),
            }
            for p in resp.cost_parameters
        ]
        formula = (resp.formula or "").strip()
        formula_justification = (getattr(resp, "formula_justification", "") or "").strip()
    except Exception:
        cost_parameters_list = [{"parameter_name": cost_input, "unit": "EGP per month", "monthly_cost_egp": None, "justification": ""}]
        formula = ""
        formula_justification = ""

    cost_parameters_list, dropped = apply_scope_to_parameters(cost_parameters_list, formula)
    if dropped:
        notes = "; ".join(f"{d['parameter_name']} ({d.get('removal_reason', 'out of scope')})" for d in dropped)
        formula_justification = (
            f"{formula_justification}\n\nDropped as not priceable: {notes}".strip()
        )

    return cost_parameters_list, formula, formula_justification


def apply_scope_to_parameters(parameters: List[dict], formula: str = "") -> Tuple[List[dict], List[dict]]:
    """
    Trim a cost input's parameters to the ones worth pricing.

    Parameters referenced by the formula are never dropped, even when they look
    derived. Removing one would leave a dangling placeholder and make the whole cost
    input uncostable, which is a worse outcome than carrying a parameter that the
    estimator will probably abstain on. The prompt is what discourages creating them;
    this pass is the safety net for the ones that slip through unreferenced.

    Returns (kept, dropped).
    """
    referenced = {name.strip().lower() for name in re.findall(r"\[([^\]]+)\]", formula or "")}

    def _name(parameter: dict) -> str:
        return (parameter.get("parameter_name") or "").strip().lower()

    protected = [p for p in parameters if _name(p) in referenced]
    candidates = [p for p in parameters if _name(p) not in referenced]

    # When the formula already protects a usable parameter, an unpriceable leftover
    # should simply go rather than being rescued to avoid an empty list.
    estimable, dropped = filter_estimable_parameters(candidates, allow_empty=bool(protected))

    # Formula-referenced parameters count against the budget, so a long formula
    # cannot smuggle the level past its cap.
    budget = max(CAPS["parameters"] - len(protected), 0)
    kept = prune_to_cap(estimable, budget)

    kept_names = {_name(p) for p in kept}
    for parameter in estimable:
        if _name(parameter) not in kept_names:
            parameter.setdefault("removal_reason", "trimmed to keep the breakdown small")
            dropped.append(parameter)

    final = protected + kept
    if not final:
        # Never leave a cost input with nothing to price; keep the first parameter and
        # let the estimator decide whether it can find a value.
        final = parameters[:1]
        dropped = [d for d in dropped if _name(d) != _name(final[0])] if final else dropped

    return final, dropped


def validate_formula_units(inp: dict) -> List[str]:
    """
    Deterministic sanity check that a cost input's formula is consistent with its
    parameters' declared pricing units. Returns a list of human-readable warnings
    (empty when everything looks consistent). Warnings never block — they surface
    in the UI so the analyst can review.
    """
    warnings: List[str] = []
    formula = (inp.get("formula") or "").strip()
    params = inp.get("cost_parameters") or []

    def _literals_multiplying(placeholder: str, expr: str) -> List[float]:
        """Collect numeric literals that appear in the same product term as the placeholder."""
        lits: List[float] = []
        for term in re.split(r"[+\-]", expr):
            if placeholder in term:
                lits.extend(float(m) for m in re.findall(r"(?<![\w.])(\d+(?:\.\d+)?)", term))
        return lits

    for p in params:
        name = (p.get("parameter_name") or "").strip()
        unit = (p.get("unit") or "").lower()
        if not name or not unit:
            continue
        placeholder = f"[{name}]"
        one_time = "one-time" in unit or "one time" in unit or "once" in unit

        if not formula:
            if ("day" in unit or "year" in unit or "annual" in unit or one_time) and len(params) == 1:
                warnings.append(
                    f"Parameter '{name}' is priced '{p.get('unit')}' but there is no formula converting it to a monthly cost."
                )
            continue

        if placeholder not in formula:
            warnings.append(f"Parameter '{name}' does not appear in the formula.")
            continue

        lits = _literals_multiplying(placeholder, formula)
        if "day" in unit and not one_time:
            if not any(15 <= v <= 31 for v in lits):
                warnings.append(
                    f"'{name}' is a daily rate ('{p.get('unit')}') but its formula term has no working-days "
                    f"multiplier (expected a literal between 15 and 31)."
                )
        if ("year" in unit or "annual" in unit) and not one_time:
            if "/12" not in formula.replace(" ", ""):
                warnings.append(
                    f"'{name}' is an annual rate ('{p.get('unit')}') but the formula does not divide by 12."
                )
        if one_time and "/" not in formula:
            warnings.append(
                f"'{name}' is a one-time cost ('{p.get('unit')}') but the formula does not amortize it "
                f"over the project duration (no division found)."
            )

    return warnings


def apply_cost_parameters_to_structure(
    structure_so_far: dict,
    cost_driver: str,
    cost_component: str,
    cost_input: str,
    cost_parameters_list: List[dict],
    formula: str,
    formula_justification: str = "",
) -> dict:
    """Attach cost_parameters, formula and formula_justification to the given cost input in a deep copy of structure."""
    updated = copy.deepcopy(structure_so_far)
    for dr in updated.get("cost_drivers", []):
        if dr.get("cost_driver_name") != cost_driver:
            continue
        for comp in dr.get("cost_components", []):
            if comp.get("cost_component_name") != cost_component:
                continue
            for inp in comp.get("cost_inputs", []):
                if inp.get("cost_input_name") != cost_input:
                    continue
                inp["cost_parameters"] = cost_parameters_list
                inp["formula"] = formula
                inp["formula_justification"] = formula_justification
                return updated
    return updated


if __name__ == "__main__":
    activity_description = "Telecom installation in jan, feb and march"
    cost_driver = "Employment"
    cost_component = "Engineer"
    cost_input = "Gross Salary"
    structure_so_far = {
        "cost_drivers": [
            {
                "cost_driver_name": "Employment",
                "cost_components": [
                    {
                        "cost_component_name": "Engineer",
                        "cost_inputs": [{"cost_input_name": "Gross Salary", "cost_parameters": None, "formula": ""}],
                    }
                ],
            }
        ]
    }
    params_list, formula, formula_justification = cost_parameters_identifier(
        activity_description, cost_driver, cost_component, cost_input, structure_so_far
    )
    print("Cost parameters:", [p["parameter_name"] for p in params_list])
    print("Formula:", formula)
    print("Formula justification:", formula_justification)
