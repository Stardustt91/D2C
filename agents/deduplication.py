"""
LLM-based deduplication for each level of the D2C cost hierarchy.

Three functions, called after each parallel estimation step:
  - deduplicate_components : removes components duplicated across drivers
  - deduplicate_inputs     : removes inputs duplicated within the same component
  - deduplicate_parameters : removes parameters duplicated within an input,
                             and parameters whose concept is already a sibling
                             cost input in the same component

Strategy: code first finds exact-name duplicates and marks them as MANDATORY to
resolve in the prompt, so the LLM cannot hedge on obvious cases. The LLM then
also finds any additional semantic duplicates.
"""

from typing import List
from collections import defaultdict
from pydantic import BaseModel, Field
from env_config import chat_llm
import copy

llm = chat_llm(max_tokens=4000)


# ---------------------------------------------------------------------------
# Structure formatters (plain text, used in prompts)
# ---------------------------------------------------------------------------

def _fmt_components(structure: dict) -> str:
    lines = []
    for dr in structure.get("cost_drivers", []):
        lines.append(f"Cost Driver: {dr.get('cost_driver_name', '')}")
        comps = dr.get("cost_components", [])
        if comps:
            for c in comps:
                lines.append(f"  - Component: {c.get('cost_component_name', '')}")
        else:
            lines.append("  (no components)")
    return "\n".join(lines)


def _fmt_inputs(structure: dict) -> str:
    lines = []
    for dr in structure.get("cost_drivers", []):
        lines.append(f"Cost Driver: {dr.get('cost_driver_name', '')}")
        for comp in dr.get("cost_components", []):
            lines.append(f"  Cost Component: {comp.get('cost_component_name', '')}")
            inputs = comp.get("cost_inputs", [])
            if inputs:
                for inp in inputs:
                    lines.append(f"    - Input: {inp.get('cost_input_name', '')}")
            else:
                lines.append("    (no inputs)")
    return "\n".join(lines)


def _fmt_parameters(structure: dict) -> str:
    lines = []
    for dr in structure.get("cost_drivers", []):
        lines.append(f"Cost Driver: {dr.get('cost_driver_name', '')}")
        for comp in dr.get("cost_components", []):
            lines.append(f"  Cost Component: {comp.get('cost_component_name', '')}")
            for inp in comp.get("cost_inputs", []):
                lines.append(f"    Cost Input: {inp.get('cost_input_name', '')}")
                params = inp.get("cost_parameters", [])
                if params:
                    for p in params:
                        lines.append(f"      - Parameter: {p.get('parameter_name', '')}")
                else:
                    lines.append("      (no parameters)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pydantic output models
# ---------------------------------------------------------------------------

class ComponentToDelete(BaseModel):
    driver_name: str = Field(..., description="Exact driver name as it appears in the structure")
    component_name: str = Field(..., description="Exact component name to delete")
    reason: str = Field(..., description="Why this is a duplicate and which other entry it duplicates")


class ComponentDeduplicationResult(BaseModel):
    components_to_delete: List[ComponentToDelete] = Field(
        default_factory=list,
        description="Components to remove. Empty list if no duplicates found.",
    )


class InputToDelete(BaseModel):
    driver_name: str = Field(..., description="Exact driver name")
    component_name: str = Field(..., description="Exact component name")
    input_name: str = Field(..., description="Exact input name to delete")
    reason: str = Field(..., description="Why this input is redundant within its component")


class InputDeduplicationResult(BaseModel):
    inputs_to_delete: List[InputToDelete] = Field(
        default_factory=list,
        description="Inputs to remove. Empty list if no duplicates found.",
    )


class ParameterToDelete(BaseModel):
    driver_name: str = Field(..., description="Exact driver name")
    component_name: str = Field(..., description="Exact component name")
    input_name: str = Field(..., description="Exact input name that contains the parameter")
    parameter_name: str = Field(..., description="Exact parameter name to delete")
    reason: str = Field(..., description="Why this parameter is redundant")


class ParameterDeduplicationResult(BaseModel):
    parameters_to_delete: List[ParameterToDelete] = Field(
        default_factory=list,
        description="Parameters to remove. Empty list if no redundancies found.",
    )


# ---------------------------------------------------------------------------
# Helpers: code-side exact-duplicate detection
# ---------------------------------------------------------------------------

def _find_exact_component_duplicates(structure: dict) -> str:
    """Return a formatted string listing components whose names appear under more than one driver."""
    locations: dict = defaultdict(list)
    for dr in structure.get("cost_drivers", []):
        dname = dr.get("cost_driver_name", "")
        for comp in dr.get("cost_components", []):
            cname = comp.get("cost_component_name", "")
            locations[cname.lower().strip()].append((dname, cname))

    dupes = [(entries[0][1], entries) for entries in locations.values() if len(entries) > 1]
    if not dupes:
        return ""

    lines = [
        "MANDATORY — The following components have IDENTICAL names under multiple drivers.",
        "You MUST delete them from all but the single most appropriate driver.",
        "",
    ]
    for canonical, entries in dupes:
        lines.append(f'Component "{canonical}" appears under:')
        for dname, cname in entries:
            lines.append(f'  - Driver: "{dname}"')
    return "\n".join(lines)


def _find_exact_input_duplicates(structure: dict) -> str:
    """Return formatted string of inputs whose names repeat within the same component."""
    blocks = []
    for dr in structure.get("cost_drivers", []):
        dname = dr.get("cost_driver_name", "")
        for comp in dr.get("cost_components", []):
            cname = comp.get("cost_component_name", "")
            seen: dict = defaultdict(list)
            for inp in comp.get("cost_inputs", []):
                iname = inp.get("cost_input_name", "")
                seen[iname.lower().strip()].append(iname)
            dupes = [(entries[0], entries) for entries in seen.values() if len(entries) > 1]
            for canonical, entries in dupes:
                blocks.append(
                    f'  Input "{canonical}" appears {len(entries)} times under '
                    f'component "{cname}" (driver "{dname}") — keep one, delete the rest.'
                )
    if not blocks:
        return ""
    return (
        "MANDATORY — The following inputs are exact duplicates within the same component:\n"
        + "\n".join(blocks)
    )


def _find_exact_parameter_duplicates(structure: dict) -> str:
    """Return formatted string of parameters that repeat within the same input."""
    blocks = []
    for dr in structure.get("cost_drivers", []):
        dname = dr.get("cost_driver_name", "")
        for comp in dr.get("cost_components", []):
            cname = comp.get("cost_component_name", "")
            for inp in comp.get("cost_inputs", []):
                iname = inp.get("cost_input_name", "")
                seen: dict = defaultdict(list)
                for p in inp.get("cost_parameters", []):
                    pname = p.get("parameter_name", "")
                    seen[pname.lower().strip()].append(pname)
                dupes = [(entries[0], entries) for entries in seen.values() if len(entries) > 1]
                for canonical, entries in dupes:
                    blocks.append(
                        f'  Parameter "{canonical}" appears {len(entries)} times under '
                        f'input "{iname}" / component "{cname}" / driver "{dname}".'
                    )
    if not blocks:
        return ""
    return (
        "MANDATORY — The following parameters are exact duplicates within the same input:\n"
        + "\n".join(blocks)
    )


# ---------------------------------------------------------------------------
# Deduplication functions
# ---------------------------------------------------------------------------

def deduplicate_components(structure: dict, activity_description: str) -> dict:
    """
    Remove cost components that represent the same cost concept but appear under
    multiple drivers. Keeps the instance that best fits its parent driver.
    """
    formatted = _fmt_components(structure)
    mandatory_section = _find_exact_component_duplicates(structure)

    mandatory_block = ""
    if mandatory_section:
        mandatory_block = f"""
=== EXACT DUPLICATES (MUST RESOLVE — do not skip any of these) ===
{mandatory_section}
===================================================================

"""

    prompt = f"""You are reviewing a cost structure for a Design-to-Cost (D2C) analysis at Vodafone Egypt.
The structure was produced by parallel AI agents, so the same cost concept may have been assigned to multiple cost drivers.

Activity Description:
{activity_description}

Full structure (drivers and their components):
{formatted}
{mandatory_block}
Your task has two parts:

PART 1 — MANDATORY (exact duplicates listed above):
For every component listed in the EXACT DUPLICATES section, you MUST include a deletion entry.
Decide which single driver is the best semantic fit and delete the component from ALL other drivers.
A component named "X" under a driver also named "X" (or very similar) is almost always the correct home — delete it from the other driver(s).

PART 2 — SEMANTIC duplicates (your own analysis):
Also look for components that represent the same real-world cost even if their names differ slightly.
Apply the same rule: keep the best-fit instance, delete from the others.

Important:
- Components with similar names under DIFFERENT drivers are NOT automatically duplicates unless they represent the exact same cost.
- If genuinely unsure whether two components are the same, do NOT delete either.
- Return the exact driver_name and component_name (as written in the structure above) for each entry to DELETE."""

    dedup_llm = llm.with_structured_output(ComponentDeduplicationResult)
    try:
        result = dedup_llm.invoke(prompt)
    except Exception:
        return structure

    updated = copy.deepcopy(structure)
    for item in result.components_to_delete:
        for dr in updated.get("cost_drivers", []):
            if dr.get("cost_driver_name", "").strip() == item.driver_name.strip():
                dr["cost_components"] = [
                    c for c in dr.get("cost_components", [])
                    if c.get("cost_component_name", "").strip() != item.component_name.strip()
                ]
                break
    return updated


def deduplicate_inputs(structure: dict, activity_description: str) -> dict:
    """
    Remove cost inputs that are redundant within the same cost component.
    Same input name under DIFFERENT components is intentional and is NOT removed.
    """
    formatted = _fmt_inputs(structure)
    mandatory_section = _find_exact_input_duplicates(structure)

    mandatory_block = ""
    if mandatory_section:
        mandatory_block = f"""
=== EXACT DUPLICATES (MUST RESOLVE) ===
{mandatory_section}
========================================

"""

    prompt = f"""You are reviewing a cost structure for a Design-to-Cost (D2C) analysis at Vodafone Egypt.
The structure was produced by parallel AI agents, so the same cost input may appear more than once inside a single component.

Activity Description:
{activity_description}

Full structure (drivers → components → inputs):
{formatted}
{mandatory_block}
Your task has two parts:

PART 1 — MANDATORY (exact duplicates listed above):
For every input listed in the EXACT DUPLICATES section, keep one occurrence and delete the rest.

PART 2 — SEMANTIC duplicates (your own analysis):
Look for inputs that represent the same real-world cost within the SAME component, even if names differ slightly.
Example: "Fuel Allowance" and "Fuel Cost" both under "Light Truck" — one is redundant.

Critical rules:
- ONLY flag inputs as duplicates if they are within the SAME (driver, component) pair.
- "Gross Salary" under "Engineer" and "Gross Salary" under "Team Leader" are DIFFERENT components — do NOT delete either.
- If genuinely unsure, do NOT delete.
- Return the exact driver_name, component_name, and input_name (as written above) for each entry to DELETE."""

    dedup_llm = llm.with_structured_output(InputDeduplicationResult)
    try:
        result = dedup_llm.invoke(prompt)
    except Exception:
        return structure

    updated = copy.deepcopy(structure)
    for item in result.inputs_to_delete:
        for dr in updated.get("cost_drivers", []):
            if dr.get("cost_driver_name", "").strip() == item.driver_name.strip():
                for comp in dr.get("cost_components", []):
                    if comp.get("cost_component_name", "").strip() == item.component_name.strip():
                        comp["cost_inputs"] = [
                            i for i in comp.get("cost_inputs", [])
                            if i.get("cost_input_name", "").strip() != item.input_name.strip()
                        ]
                        break
                break
    return updated


def deduplicate_parameters(structure: dict, activity_description: str) -> dict:
    """
    Remove cost parameters that are:
      (a) duplicated within the same cost input, OR
      (b) already captured as a sibling cost input within the same component.

    Example of (b): "Bonus" is both a cost input AND a parameter under "Gross Salary"
    within the same component → delete the parameter; it's already counted separately.
    """
    formatted = _fmt_parameters(structure)
    mandatory_section = _find_exact_parameter_duplicates(structure)

    # Also build a cross-level hint: for each component, list its inputs so the
    # LLM can spot parameters that duplicate a sibling input
    cross_level_hint = []
    for dr in structure.get("cost_drivers", []):
        dname = dr.get("cost_driver_name", "")
        for comp in dr.get("cost_components", []):
            cname = comp.get("cost_component_name", "")
            input_names = [i.get("cost_input_name", "") for i in comp.get("cost_inputs", [])]
            if len(input_names) > 1:
                cross_level_hint.append(
                    f'  Component "{cname}" (driver "{dname}") has inputs: '
                    + ", ".join(f'"{n}"' for n in input_names)
                )
    cross_block = ""
    if cross_level_hint:
        cross_block = (
            "\nFor reference — sibling inputs per component "
            "(a parameter that matches one of these within the same component is redundant):\n"
            + "\n".join(cross_level_hint)
            + "\n"
        )

    mandatory_block = ""
    if mandatory_section:
        mandatory_block = f"""
=== EXACT DUPLICATES (MUST RESOLVE) ===
{mandatory_section}
========================================

"""

    prompt = f"""You are reviewing a cost structure for a Design-to-Cost (D2C) analysis at Vodafone Egypt.
The structure was produced by parallel AI agents, so redundant cost parameters may exist.

Activity Description:
{activity_description}

Full structure (drivers → components → inputs → parameters):
{formatted}
{cross_block}{mandatory_block}
Your task: identify cost parameters to DELETE for either reason below.

REASON A — Duplicate within the same cost input (MANDATORY if listed above):
The same parameter concept appears more than once under the same cost input → delete extras.

REASON B — Already captured as a sibling cost input in the same component:
A parameter under one input represents a concept that is ALREADY a separate cost input
within the SAME component.
Example: "Bonus" is a cost input AND also a parameter under "Gross Salary" in the same component
→ delete the parameter "Bonus" from "Gross Salary" (it is already counted as its own input).
Do NOT apply this across different components.

Rules:
- Only flag parameters that truly overlap with a sibling INPUT in the same component.
- Do NOT remove a parameter because it matches an input in a DIFFERENT component.
- If genuinely unsure, do NOT delete.
- Return the exact driver_name, component_name, input_name, and parameter_name (as written above) for each entry to DELETE."""

    dedup_llm = llm.with_structured_output(ParameterDeduplicationResult)
    try:
        result = dedup_llm.invoke(prompt)
    except Exception:
        return structure

    updated = copy.deepcopy(structure)
    for item in result.parameters_to_delete:
        for dr in updated.get("cost_drivers", []):
            if dr.get("cost_driver_name", "").strip() == item.driver_name.strip():
                for comp in dr.get("cost_components", []):
                    if comp.get("cost_component_name", "").strip() == item.component_name.strip():
                        for inp in comp.get("cost_inputs", []):
                            if inp.get("cost_input_name", "").strip() == item.input_name.strip():
                                inp["cost_parameters"] = [
                                    p for p in inp.get("cost_parameters", [])
                                    if p.get("parameter_name", "").strip() != item.parameter_name.strip()
                                ]
                                break
                        break
                break
    return updated
