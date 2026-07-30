"""
Resource planner: derives the resourcing & scaling plan for an activity BEFORE any
cost structure is generated.

This is the step that decides "3 trucks for 40 sites, not 40 trucks". It produces:
  - volume driver (name + value), project duration, working days/month
  - crew count + composition, throughput
  - a resource table where every quantity has a scaling class and a written derivation

Downstream agents (cost drivers/components/inputs/parameters) receive this plan as
the authoritative source of quantities: formula literals must come from here, never
be invented at the formula step.

The plan runs once per project, so it is worth a stronger model: set
AZURE_OPENAI_PLANNER_DEPLOYMENT / _ENDPOINT / _API_KEY to point it at a frontier
deployment; it falls back to the shared reasoning deployment otherwise.
"""

from typing import List, Optional

from pydantic import BaseModel, Field

try:
    from typing import Literal
except ImportError:  # pragma: no cover
    from typing_extensions import Literal

from env_config import optional, reasoning_llm

from structure_formatter import format_resource_plan, format_activity_facts, SCALING_CLASS_DEFINITIONS
from norms import format_norms_block

# Any AZURE_OPENAI_PLANNER_* variable that is set overrides the shared reasoning tier
# for this agent alone; anything left unset falls back to it.
planner_llm = reasoning_llm(
    deployment=optional("AZURE_OPENAI_PLANNER_DEPLOYMENT") or None,
    endpoint=optional("AZURE_OPENAI_PLANNER_ENDPOINT") or None,
    api_key=optional("AZURE_OPENAI_PLANNER_API_KEY") or None,
    reasoning_effort=optional("AZURE_OPENAI_PLANNER_REASONING_EFFORT", default="low"),
    max_completion_tokens=12000,
)

MAX_PLAN_RESOURCES = 20

SCALING_CLASSES = ("per_unit_of_volume", "capacity_pool", "time_based", "fixed_one_time")


# ---------------------------------------------------------------------------
# Pydantic schema. Field order matters: structured output is generated left to
# right, so putting `derivation` BEFORE `quantity` gives the model a scratchpad
# to reason in before it commits to a number.
# ---------------------------------------------------------------------------

class VolumeDriver(BaseModel):
    name: str = Field(..., description="What the volume driver counts, e.g. 'sites', 'km of fiber', 'trips'")
    derivation: str = Field(..., description="Where this value comes from in the activity description")
    value: float = Field(..., description="Numeric size of the volume driver, e.g. 40")
    unit: str = Field(..., description="Unit label, e.g. 'sites'")


class CrewPlan(BaseModel):
    derivation: str = Field(
        ...,
        description="Reasoning for the crew count: throughput needed to finish the volume within the duration, "
                    "constraints from the description (working hours, access), and any historical norms used.",
    )
    count: int = Field(..., description="Number of parallel crews/teams executing the work")
    composition: str = Field(..., description="Roles per crew, with counts, e.g. '1 senior RF engineer, 2 technicians, 1 rigger'")


class ThroughputPlan(BaseModel):
    derivation: str = Field(..., description="How this throughput was derived (norms, description, assumption)")
    value: float = Field(..., description="Numeric throughput value, e.g. 0.5")
    unit: str = Field(..., description="Throughput unit, e.g. 'sites per crew per day'")


class PlannedResource(BaseModel):
    resource_name: str = Field(..., description="Name of the resource, e.g. 'Light truck', 'RF engineer', 'Spectrum analyzer'")
    scaling_class: Literal["per_unit_of_volume", "capacity_pool", "time_based", "fixed_one_time"] = Field(
        ..., description="How the quantity scales. Classify BEFORE deciding the quantity."
    )
    derivation: str = Field(
        ...,
        description="Arithmetic + reasoning for the quantity, referencing the volume driver, crews, throughput, "
                    "norms, or an explicit number in the description. E.g. '1 truck per crew x 3 crews = 3'.",
    )
    quantity: float = Field(..., description="Planned quantity, consistent with the derivation above")
    unit: str = Field(..., description="Unit for the quantity, e.g. 'trucks', 'people', 'kits'")
    confidence: Literal["low", "medium", "high"] = Field(
        ..., description="'high' if the number is explicit in the description, 'medium' if derived from norms/throughput, 'low' if assumed"
    )
    source: Literal["historical", "estimated"] = Field(
        ...,
        description="Where this resource's quantity comes from: 'historical' if it is grounded in the historical "
                    "norms library or the historical projects shown above (name which one in the justification); "
                    "'estimated' if you derived or assumed it yourself because no historical basis applied.",
    )
    justification: str = Field(
        ...,
        description="A self-contained justification a reviewer can read on its own: why this resource is needed, "
                    "why THIS quantity, why this unit, and why this scaling class. State plainly whether the "
                    "number came from historical data (name the norm/project) or was estimated.",
    )


class ResourcePlanResponse(BaseModel):
    volume_driver: VolumeDriver
    duration_months: float = Field(..., description="Total project duration in months")
    working_days_per_month: float = Field(22, description="Working days per month used for daily-rate conversions (default 22)")
    crews: CrewPlan
    throughput: ThroughputPlan
    resources: List[PlannedResource] = Field(..., description="All major human, vehicle, equipment and one-time resources")
    assumptions: List[str] = Field(default_factory=list, description="Explicit assumptions made where the description was silent")


class ResourceIssue(BaseModel):
    resource_name: str = Field(..., description="Resource the issue is about")
    problem: str = Field(..., description="What is wrong with the current quantity or scaling class")
    suggested_scaling_class: Optional[str] = Field(None, description="Corrected scaling class if it should change")
    suggested_quantity: Optional[float] = Field(None, description="Corrected quantity if it should change")


class CritiqueResponse(BaseModel):
    issues: List[ResourceIssue] = Field(
        default_factory=list,
        description="Only genuine sizing/classification errors. Empty list when the plan is sound.",
    )


def _to_dict(model) -> dict:
    try:
        return model.model_dump()
    except AttributeError:
        return model.dict()


# ---------------------------------------------------------------------------
# Deterministic critic: catches the classic failure mode cheaply.
# ---------------------------------------------------------------------------

def deterministic_plan_flags(plan: dict) -> List[str]:
    flags = []
    vd = plan.get("volume_driver") or {}
    try:
        vol = float(vd.get("value"))
    except (TypeError, ValueError):
        vol = None
    for r in plan.get("resources", []):
        qty = r.get("quantity")
        cls = r.get("scaling_class")
        name = r.get("resource_name", "?")
        if qty is None:
            continue
        if vol and vol > 3 and cls in ("capacity_pool", "time_based", "fixed_one_time") and abs(float(qty) - vol) < 1e-9:
            flags.append(
                f"'{name}' is classified as {cls} but its quantity ({qty:g}) equals the volume driver value ({vol:g}) — "
                f"this is the classic 'one truck per site' scaling error. Re-derive it from crews/throughput."
            )
        if cls == "fixed_one_time" and qty and float(qty) > 5:
            flags.append(f"'{name}' is fixed_one_time but quantity is {qty:g} — verify it is genuinely one-time.")
        if not (r.get("derivation") or "").strip():
            flags.append(f"'{name}' has no derivation for its quantity.")
    return flags


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def resource_plan_identifier(
    activity_description: str,
    activity_facts: Optional[dict] = None,
    prompt_log_path: Optional[str] = None,
    prompt_log_section: Optional[str] = None,
) -> dict:
    """
    Derive the resourcing & scaling plan for an activity.

    Returns a plain dict (the schema of ResourcePlanResponse) with an extra
    "review_notes" list describing what the critic pass flagged and fixed.
    """
    norms_block = format_norms_block()
    facts_block = format_activity_facts(activity_facts)

    # Historical formulas give the model concrete examples of how past projects
    # were resourced (even if their literals are scale-specific).
    historical_block = ""
    try:
        from cost_parameter import return_structured_cost_parameters
        historical_block = return_structured_cost_parameters(activity_description, "", "", "")
    except Exception:
        historical_block = ""

    structured_planner = planner_llm.with_structured_output(ResourcePlanResponse)

    prompt = f"""You are a senior resource planner for the supply chain team at Vodafone Egypt, preparing a
Design-to-Cost estimation. BEFORE any costs are estimated, you must produce the RESOURCING & SCALING PLAN:
how many of each resource the project actually needs, and how each quantity scales.

{SCALING_CLASS_DEFINITIONS}

THE CARDINAL RULE: never size a shared resource by the volume driver. A project with 40 sites does NOT need
40 trucks — trucks are a capacity pool sized by the number of crews working in parallel (typically 1 truck
per crew, so 3 crews -> 3 trucks). The same applies to cranes, test instruments, crew vans, laptops, etc.

YOUR METHOD (follow in order):
1. Identify the volume driver and its numeric value from the description.
2. Identify the project duration in months.
3. Derive the number of parallel crews: how much work must be done per day/week to finish the volume within
   the duration, given a realistic throughput (use historical norms below if available, otherwise state your
   assumed throughput explicitly).
4. FIRST classify each resource into a scaling class, THEN derive its quantity:
   - per_unit_of_volume -> quantity = ratio x volume driver value
   - capacity_pool      -> quantity derived from crews/throughput (NEVER from the volume driver)
   - time_based         -> a fixed headcount/quantity for the duration (e.g. 1 project manager)
   - fixed_one_time     -> usually 1
5. If the description states an explicit count for a resource, use it VERBATIM with confidence 'high'.
6. Cover all major resource types mentioned or implied: personnel roles (with counts), vehicles, lifting
   equipment, tools/instruments, permits/licenses, and any accommodation needs.
7. Every derivation must show its arithmetic (e.g. '1 truck per crew x 3 crews = 3').
8. For every resource, write a plain-language `justification` (why the resource is needed, why THIS quantity,
   why this unit, why this scaling class) and set its `source` to 'historical' when the quantity is grounded
   in the historical norms/projects above (name which one in the justification), or 'estimated' when you
   derived or assumed it yourself.

{norms_block if norms_block else "No historical norms library is available; state your assumed ratios explicitly in derivations."}

Historical projects and their cost formulas (context only — their literal numbers reflect THEIR scale, not yours;
transfer the ratios/logic, not the raw counts):
{historical_block if historical_block.strip() else "(none retrieved)"}

{facts_block}

Activity Description:
{activity_description}

Produce the complete resource plan now. Limit the resource table to the {MAX_PLAN_RESOURCES} most cost-relevant resources."""

    if prompt_log_path and prompt_log_section:
        with open(prompt_log_path, "a", encoding="utf-8") as f:
            f.write(f"{prompt_log_section}\n[Prompt]\n{prompt}\n---\n")

    response = structured_planner.invoke(prompt)
    plan = _to_dict(response)
    plan["resources"] = plan.get("resources", [])[:MAX_PLAN_RESOURCES]

    # --- Critic pass: deterministic flags + LLM review, then one revision round ---
    review_notes: List[str] = []
    det_flags = deterministic_plan_flags(plan)
    review_notes.extend(det_flags)

    critic_llm = planner_llm.with_structured_output(CritiqueResponse)
    critic_prompt = f"""You are reviewing a resourcing plan for logical sizing errors before it is used for cost estimation.

{SCALING_CLASS_DEFINITIONS}

COMMON ERRORS TO CHECK:
1. A shared/pooled resource (truck, crane, test kit, van) whose quantity equals the volume driver value —
   e.g. 40 trucks for 40 sites. Pools must be sized by crews/throughput.
2. Personnel counts inconsistent with the crew plan (e.g. 3 crews of 4 people but only 5 field staff total).
3. Wrong scaling class (e.g. a per-site permit classified as fixed_one_time, or a shared crane as per_unit_of_volume).
4. Throughput that cannot finish the volume within the stated duration (crews x throughput x working days < volume).
5. Quantities with no arithmetic in their derivation.

Automated checks already flagged:
{chr(10).join('- ' + f for f in det_flags) if det_flags else '(nothing)'}

Activity Description:
{activity_description}

Current plan:
{format_resource_plan(plan)}

Report ONLY genuine errors (with corrected quantity/class where possible). If the plan is sound, return an empty issues list."""

    try:
        critique = critic_llm.invoke(critic_prompt)
        issues = critique.issues or []
    except Exception:
        issues = []

    if issues or det_flags:
        issue_lines = [f"- {i.resource_name}: {i.problem}"
                       + (f" Suggested class: {i.suggested_scaling_class}." if i.suggested_scaling_class else "")
                       + (f" Suggested quantity: {i.suggested_quantity:g}." if i.suggested_quantity is not None else "")
                       for i in issues]
        review_notes.extend(f"{i.resource_name}: {i.problem}" for i in issues)

        revision_prompt = f"""{prompt}

A reviewer found problems in your previous plan. Previous plan:
{format_resource_plan(plan)}

Reviewer findings (MUST be addressed):
{chr(10).join(issue_lines) if issue_lines else chr(10).join('- ' + f for f in det_flags)}

Produce the FULL corrected resource plan now (complete schema, not just the fixed rows)."""
        try:
            revised = structured_planner.invoke(revision_prompt)
            plan = _to_dict(revised)
            plan["resources"] = plan.get("resources", [])[:MAX_PLAN_RESOURCES]
            remaining = deterministic_plan_flags(plan)
            if remaining:
                review_notes.append("Still flagged after revision: " + "; ".join(remaining))
            else:
                review_notes.append("All flagged issues were corrected in a revision pass.")
        except Exception as e:
            review_notes.append(f"Revision pass failed ({e}); plan kept as originally generated.")

    plan["review_notes"] = review_notes
    return plan


if __name__ == "__main__":
    import json as _json
    activity = (
        "Installation and commissioning of new 5G telecom equipment across 40 existing mobile network "
        "sites in Greater Cairo over 3 months. Light trucks for material delivery and crew transport "
        "vehicles for daily site visits; lifting equipment, spectrum analyzers, laptops with vendor "
        "integration software; project managers, senior RF engineers, field technicians, riggers."
    )
    plan = resource_plan_identifier(activity)
    print(_json.dumps(plan, indent=2, ensure_ascii=False))
    print("\n--- Formatted for downstream prompts ---\n")
    print(format_resource_plan(plan))
