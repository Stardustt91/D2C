"""
Workflow test: activity_description → cost drivers → cost components → cost inputs → cost parameters → [cost].

Runs the full design-to-cost pipeline and optionally estimates cost.
Saves results as CSV and a structure-at-each-step text file.
"""

from __future__ import annotations

import csv
import os
from typing import List, Tuple, Any, Optional

from cost_driver import cost_drivers_identifier
from cost_component import cost_components_identifier
from cost_inputs import cost_inputs_identifier
from cost_parameter import cost_parameters_identifier, apply_cost_parameters_to_structure, validate_formula_units
from cost_estimation import cost_estimation_from_internet
from resource_planner import resource_plan_identifier
from structure_formatter import format_structure, format_resource_plan


# Maximum number of entities to estimate at each level (drivers, components per driver, inputs per component, parameters per input)
MAX_ENTITIES_PER_LEVEL = 4

CSV_COLUMNS = [
    "cost_driver",
    "cost_driver_justification",
    "cost_component",
    "cost_component_justification",
    "cost_component_quantity",
    "cost_input",
    "cost_input_justification",
    "cost_input_quantity",
    "cost_parameter",
    "cost_parameter_justification",
    "formula",
    "cost",
    "cost_status",
    "cost_justification",
    "cost_source",
]


def _fmt_status(status: str, basis: str, has_cost: bool) -> str:
    """
    How much weight the exported figure can bear.

    The engine publishes a number even when it is not confident — a median of
    disagreeing sources, the closest comparable item, an internal benchmark, or its
    own unsourced judgement — so the CSV needs a column saying which, or a
    placeholder reads exactly like an estimate.
    Mirrors fmtStatus() in d2c_app/export.js.
    """
    if status == "needs_analyst_input":
        return "PROVISIONAL - sources disagree, median shown" if has_cost else "Not estimated - sources disagree"
    if status == "insufficient_evidence":
        if not has_cost:
            return "Not estimated - no usable evidence"
        if basis == "model_judgement":
            return "UNSOURCED - model estimate, no evidence found"
        if basis == "internal_benchmark":
            return "PROVISIONAL - internal historical records only"
        return "PROVISIONAL - closest comparable item"
    if status == "user_set":
        return "Set by analyst"
    return ""


def _build_all_cost_parameters(structure: dict) -> List[Tuple[str, str, str, str]]:
    out = []
    for driver in structure.get("cost_drivers", []):
        dname = driver.get("cost_driver_name")
        for comp in driver.get("cost_components", []):
            cname = comp.get("cost_component_name")
            for inp in comp.get("cost_inputs", []):
                iname = inp.get("cost_input_name")
                for p in inp.get("cost_parameters") or []:
                    pname = p.get("parameter_name")
                    if pname:
                        out.append((dname, cname, iname, pname))
    return out


def _build_all_cost_drivers(structure: dict) -> List[str]:
    return [d.get("cost_driver_name") for d in structure.get("cost_drivers", []) if d.get("cost_driver_name")]


def _build_all_cost_components(structure: dict) -> List[Tuple[str, str]]:
    out = []
    for driver in structure.get("cost_drivers", []):
        dname = driver.get("cost_driver_name")
        for comp in driver.get("cost_components", []):
            cname = comp.get("cost_component_name")
            if cname:
                out.append((dname, cname))
    return out


def _build_all_cost_inputs(structure: dict) -> List[Tuple[str, str, str]]:
    out = []
    for driver in structure.get("cost_drivers", []):
        dname = driver.get("cost_driver_name")
        for comp in driver.get("cost_components", []):
            cname = comp.get("cost_component_name")
            for inp in comp.get("cost_inputs", []):
                iname = inp.get("cost_input_name")
                if iname:
                    out.append((dname, cname, iname))
    return out


def run_workflow(
    activity_description: str,
    estimate_cost: bool = False,
    output_dir: str = ".",
    csv_basename: str = "workflow_results",
    structure_basename: str = "workflow_structure",
    prompt_log_basename: Optional[str] = "workflow_prompts",
) -> Tuple[str, str]:
    """
    Run the full pipeline: drivers → components → inputs → parameters → [cost].

    If estimate_cost is False, stops after estimating cost parameters.
    If prompt_log_basename is not None, writes each step's prompt to {output_dir}/{prompt_log_basename}.txt.
    Returns (path_to_csv, path_to_structure_txt).
    """
    structure_steps: List[str] = []
    structure_so_far: Optional[dict] = None
    all_drivers: List[str] = []
    cost_drivers_with_justification: List[dict] = []

    prompt_log_path: Optional[str] = None
    if prompt_log_basename is not None:
        os.makedirs(output_dir, exist_ok=True)
        prompt_log_path = os.path.join(output_dir, f"{prompt_log_basename}.txt")
        with open(prompt_log_path, "w", encoding="utf-8") as _:
            pass  # truncate at start

    # --- Step 0: Resourcing & scaling plan (authoritative quantities for all later steps) ---
    print("[Step 0] Deriving the resourcing & scaling plan...")
    resource_plan = resource_plan_identifier(
        activity_description,
        prompt_log_path=prompt_log_path,
        prompt_log_section="When deriving the resource plan:",
    )
    print(format_resource_plan(resource_plan))
    structure_steps.append("=== STEP 0: Resourcing & scaling plan ===\n")
    structure_steps.append(format_resource_plan(resource_plan))
    structure_steps.append("\n")

    # --- Step 1: Cost drivers (limit to MAX_ENTITIES_PER_LEVEL) ---
    print("\n[Step 1] Estimating cost drivers...")
    cost_drivers_with_justification, all_drivers = cost_drivers_identifier(
        activity_description,
        resource_plan=resource_plan,
        prompt_log_path=prompt_log_path,
        prompt_log_section="When estimating the cost drivers:",
    )
    cost_drivers_with_justification = cost_drivers_with_justification[:MAX_ENTITIES_PER_LEVEL]
    all_drivers = all_drivers[:MAX_ENTITIES_PER_LEVEL]
    print(f"  Cost drivers: {', '.join(all_drivers)}")
    structure_so_far = {"resource_plan": resource_plan, "cost_drivers": [{"cost_driver_name": d["cost_driver_name"], "cost_components": []} for d in cost_drivers_with_justification]}
    # Add justification to structure for CSV
    for d in structure_so_far["cost_drivers"]:
        for orig in cost_drivers_with_justification:
            if orig["cost_driver_name"] == d["cost_driver_name"]:
                d["justification"] = orig.get("justification", "")
                break

    structure_steps.append("=== STEP 1: After estimating cost drivers ===\n")
    structure_steps.append(format_structure(structure_so_far, all_drivers, [], [], []))
    structure_steps.append("\n")

    all_components: List[Tuple[str, str]] = []

    # --- Step 2: Cost components per driver ---
    print("\n[Step 2] Estimating cost components...")
    for driver in cost_drivers_with_justification:
        dname = driver["cost_driver_name"]
        print(f"  Currently estimating cost driver: {dname}")
        _, comp_names, structure_so_far = cost_components_identifier(
            activity_description, dname, structure_so_far, all_drivers,
            resource_plan=resource_plan,
            prompt_log_path=prompt_log_path,
            prompt_log_section=f"When estimating the cost components for the cost driver {dname}:",
        )
        # Limit to MAX_ENTITIES_PER_LEVEL components per driver
        for d in structure_so_far.get("cost_drivers", []):
            if d.get("cost_driver_name") == dname and d.get("cost_components"):
                d["cost_components"] = d["cost_components"][:MAX_ENTITIES_PER_LEVEL]
                comp_names = [c.get("cost_component_name") for c in d["cost_components"] if c.get("cost_component_name")]
                break
        print(f"    Cost components: {', '.join(comp_names)}")
        all_components = _build_all_cost_components(structure_so_far)

    # Preserve driver justifications (structure from identifier is a deep copy)
    for d in structure_so_far.get("cost_drivers", []):
        for orig in cost_drivers_with_justification:
            if orig["cost_driver_name"] == d.get("cost_driver_name"):
                d["justification"] = orig.get("justification", "")
                break

    structure_steps.append("=== STEP 2: After estimating cost components ===\n")
    structure_steps.append(format_structure(structure_so_far, all_drivers, all_components, [], []))
    structure_steps.append("\n")

    all_inputs: List[Tuple[str, str, str]] = []

    # --- Step 3: Cost inputs per component ---
    print("\n[Step 3] Estimating cost inputs...")
    for dr in structure_so_far.get("cost_drivers", []):
        dname = dr.get("cost_driver_name")
        for comp in dr.get("cost_components", []):
            cname = comp.get("cost_component_name")
            print(f"  Currently estimating: {dname} > {cname}")
            _, input_names, structure_so_far = cost_inputs_identifier(
                activity_description, dname, cname, structure_so_far,
                all_cost_drivers=all_drivers, all_cost_components=all_components,
                resource_plan=resource_plan,
                prompt_log_path=prompt_log_path,
                prompt_log_section=f"When estimating the cost inputs for the cost component {cname} for the cost driver {dname}:",
            )
            # Limit to MAX_ENTITIES_PER_LEVEL inputs per component
            for dr in structure_so_far.get("cost_drivers", []):
                if dr.get("cost_driver_name") != dname:
                    continue
                for comp in dr.get("cost_components", []):
                    if comp.get("cost_component_name") == cname and comp.get("cost_inputs"):
                        comp["cost_inputs"] = comp["cost_inputs"][:MAX_ENTITIES_PER_LEVEL]
                        input_names = [i.get("cost_input_name") for i in comp["cost_inputs"] if i.get("cost_input_name")]
                        break
                else:
                    continue
                break
            print(f"    Cost inputs: {', '.join(input_names)}")
            all_inputs = _build_all_cost_inputs(structure_so_far)

    structure_steps.append("=== STEP 3: After estimating cost inputs ===\n")
    structure_steps.append(format_structure(structure_so_far, all_drivers, all_components, all_inputs, []))
    structure_steps.append("\n")

    all_parameters: List[Tuple[str, str, str, str]] = []

    # --- Step 4: Cost parameters per cost input ---
    print("\n[Step 4] Estimating cost parameters...")
    for dr in structure_so_far.get("cost_drivers", []):
        dname = dr.get("cost_driver_name")
        for comp in dr.get("cost_components", []):
            cname = comp.get("cost_component_name")
            for inp in comp.get("cost_inputs", []):
                iname = inp.get("cost_input_name")
                print(f"  Currently estimating: {dname} > {cname} > {iname}")
                params_list, formula, formula_justification = cost_parameters_identifier(
                    activity_description, dname, cname, iname, structure_so_far,
                    resource_plan=resource_plan,
                    prompt_log_path=prompt_log_path,
                    prompt_log_section=f"When estimating the cost parameters for the cost input {iname} (component {cname}, driver {dname}):",
                )
                # Limit to MAX_ENTITIES_PER_LEVEL parameters per input
                params_list = params_list[:MAX_ENTITIES_PER_LEVEL]
                structure_so_far = apply_cost_parameters_to_structure(
                    structure_so_far, dname, cname, iname, params_list, formula, formula_justification
                )
                param_names = [p.get("parameter_name") for p in params_list if p.get("parameter_name")]
                print(f"    Cost parameters: {', '.join(param_names)}")
                for p in params_list:
                    pname = p.get("parameter_name")
                    if pname:
                        all_parameters.append((dname, cname, iname, pname))

    # Deterministic unit/formula consistency check (warnings only)
    for dr in structure_so_far.get("cost_drivers", []):
        for comp in dr.get("cost_components", []):
            for inp in comp.get("cost_inputs", []):
                warnings = validate_formula_units(inp)
                inp["unit_warnings"] = warnings
                for w in warnings:
                    print(f"  [unit warning] {dr.get('cost_driver_name')} > {comp.get('cost_component_name')} > {inp.get('cost_input_name')}: {w}")

    structure_steps.append("=== STEP 4: After estimating cost parameters ===\n")
    structure_steps.append(format_structure(structure_so_far, all_drivers, all_components, all_inputs, all_parameters))
    structure_steps.append("\n")

    if estimate_cost:
        # --- Step 5: Cost estimation per cost input ---
        print("\n[Step 5] Estimating cost...")
        for dr in structure_so_far.get("cost_drivers", []):
            dname = dr.get("cost_driver_name")
            for comp in dr.get("cost_components", []):
                cname = comp.get("cost_component_name")
                for inp in comp.get("cost_inputs", []):
                    iname = inp.get("cost_input_name")
                    print(f"  Currently estimating cost for: {dname} > {cname} > {iname}")
                    result = cost_estimation_from_internet(
                        activity_description, dname, cname, iname, structure_so_far,
                        prompt_log_path=prompt_log_path,
                        prompt_log_section=f"When estimating the cost for the cost input {iname} (component {cname}, driver {dname}):",
                    )
                    if result and result.get("structure_so_far"):
                        structure_so_far = result["structure_so_far"]

        structure_steps.append("=== STEP 5: After estimating cost ===\n")
        structure_steps.append(format_structure(structure_so_far, all_drivers, all_components, all_inputs, all_parameters))
        structure_steps.append("\n")

    # --- Flatten to CSV rows ---
    rows: List[dict] = []
    for dr in structure_so_far.get("cost_drivers", []):
        dname = dr.get("cost_driver_name")
        d_just = dr.get("justification", "")
        for comp in dr.get("cost_components", []):
            cname = comp.get("cost_component_name")
            c_just = comp.get("justification", "")
            c_qty = comp.get("quantity")
            c_qty_str = str(c_qty) if c_qty is not None else ""
            for inp in comp.get("cost_inputs", []):
                iname = inp.get("cost_input_name")
                i_just = inp.get("justification", "")
                i_qty = inp.get("quantity")
                i_qty_str = str(i_qty) if i_qty is not None else ""
                formula = inp.get("formula", "")
                params = inp.get("cost_parameters") or []
                # Cost at parameter level when we have parameters; otherwise at input level
                def _fmt_cost(val):
                    if val is None:
                        return ""
                    try:
                        n = float(val)
                        return f"{n:,.0f} EGP" if n > 0 else ""
                    except (TypeError, ValueError):
                        return ""

                def _fmt_source(obj):
                    if obj is None:
                        return ""
                    if isinstance(obj, list):
                        return " | ".join(str(x).strip() for x in obj if x)
                    return str(obj).strip() if obj else ""

                inp_monthly = inp.get("monthly_cost_egp")
                inp_cost_str = _fmt_cost(inp_monthly)
                inp_cost_just = inp.get("cost_justification") or ""
                inp_source = _fmt_source(inp.get("source_urls") or inp.get("source_url"))

                if params:
                    for p in params:
                        pname = p.get("parameter_name") or ""
                        p_just = p.get("justification") or ""
                        p_monthly = p.get("monthly_cost_egp")
                        p_cost_str = _fmt_cost(p_monthly)
                        p_cost_just = p.get("cost_justification") or ""
                        p_source = _fmt_source(p.get("source_urls") or p.get("source_url"))
                        rows.append({
                            "cost_driver": dname,
                            "cost_driver_justification": d_just,
                            "cost_component": cname,
                            "cost_component_justification": c_just,
                            "cost_component_quantity": c_qty_str,
                            "cost_input": iname,
                            "cost_input_justification": i_just,
                            "cost_input_quantity": i_qty_str,
                            "cost_parameter": pname,
                            "cost_parameter_justification": p_just,
                            "formula": formula,
                            "cost": p_cost_str,
                            "cost_status": _fmt_status(
                                p.get("estimate_status") or "", p.get("estimate_basis") or "", bool(p_cost_str)
                            ),
                            "cost_justification": p_cost_just,
                            "cost_source": p_source,
                        })
                else:
                    rows.append({
                        "cost_driver": dname,
                        "cost_driver_justification": d_just,
                        "cost_component": cname,
                        "cost_component_justification": c_just,
                        "cost_component_quantity": c_qty_str,
                        "cost_input": iname,
                        "cost_input_justification": i_just,
                        "cost_input_quantity": i_qty_str,
                        "cost_parameter": "",
                        "cost_parameter_justification": "",
                        "formula": "",
                        "cost": inp_cost_str,
                        "cost_status": _fmt_status(
                            inp.get("estimate_status") or "", inp.get("estimate_basis") or "", bool(inp_cost_str)
                        ),
                        "cost_justification": inp_cost_just,
                        "cost_source": inp_source,
                    })

    # --- Write CSV ---
    os.makedirs(output_dir, exist_ok=True)
    csv_path = os.path.join(output_dir, f"{csv_basename}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        w.writerows(rows)

    # --- Write structure text ---
    txt_path = os.path.join(output_dir, f"{structure_basename}.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("Structure at each step of the workflow\n")
        f.write("=" * 60 + "\n\n")
        f.write("\n".join(structure_steps))

    print(f"\nDone. CSV saved: {csv_path}")
    print(f"Structure saved: {txt_path}")
    if prompt_log_path:
        print(f"Prompt log saved: {prompt_log_path}")
    return csv_path, txt_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run design-to-cost workflow test")
    parser.add_argument("activity_description", type=str, help="Activity description to estimate")
    parser.add_argument("--estimate-cost", action="store_true", help="If set, estimate cost; otherwise stop at cost parameters")
    parser.add_argument("--output-dir", type=str, default=".", help="Directory for CSV and structure text output")
    parser.add_argument("--csv-name", type=str, default="workflow_results", help="Base name for CSV file (without .csv)")
    parser.add_argument("--structure-name", type=str, default="workflow_structure", help="Base name for structure text file (without .txt)")
    parser.add_argument("--prompt-log", type=str, default="workflow_prompts", help="Base name for prompt log file (without .txt). Use empty string to disable.")
    args = parser.parse_args()

    run_workflow(
        args.activity_description,
        estimate_cost=args.estimate_cost,
        output_dir=args.output_dir,
        csv_basename=args.csv_name,
        structure_basename=args.structure_name,
        prompt_log_basename=args.prompt_log or None,
    )
