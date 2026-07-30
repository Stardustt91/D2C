"""
Unified structure formatter for displaying the cost breakdown at any stage of estimation.
This module provides a single function that can format the structure regardless of which stage
of estimation we're at (cost drivers, components, inputs, or final costs).
"""

def format_structure(updated_structure=None, all_cost_drivers=None, all_cost_components=None, all_cost_inputs=None, all_cost_parameters=None):
    """
    Format the cost structure at any stage of estimation.

    Parameters:
    -----------
    updated_structure : dict
        The current structure containing cost_drivers, their components, inputs, parameters, and costs
    all_cost_drivers : list
        List of all cost driver names that have been identified
    all_cost_components : list of tuples
        List of (driver_name, component_name) tuples for all components identified
    all_cost_inputs : list of tuples
        List of (driver_name, component_name, input_name) tuples for all inputs identified
    all_cost_parameters : list of tuples, optional
        List of (driver_name, component_name, input_name, parameter_name) for all parameters identified

    Returns:
    --------
    str : Formatted structure (no justifications): cost driver: cost component: component quantity:
          cost input: cost input quantity: cost parameter: formula: cost
    
    Examples:
    ---------
    Stage 1 - After cost drivers identified:
        cost driver: cost component: component quantity: cost input: cost input quantity: cost parameter: formula: cost
        Shelters: Not yet estimated: Not yet estimated: Not yet estimated: Not yet estimated: Not yet estimated: Not yet estimated
        Transportation: Not yet estimated: Not yet estimated: Not yet estimated: Not yet estimated: Not yet estimated: Not yet estimated
    
    Stage 2 - After components identified for first driver:
        Shelters: Prefabricated Cabins: 1: Not yet estimated: Not yet estimated: Not yet estimated: Not yet estimated
        Shelters: Installation Hardware: 1: Not yet estimated: Not yet estimated: Not yet estimated: Not yet estimated
        Transportation: Not yet estimated: Not yet estimated: Not yet estimated: Not yet estimated: Not yet estimated: Not yet estimated
    
    Stage 4 - After cost estimated for first input:
        Shelters: Prefabricated Cabins: 1: Prefabricated Cabin: 1: Not yet estimated: Not yet estimated: 180,000 EGP
    """
    # Header: cost driver → cost component → cost input → cost parameter → cost (no justifications)
    formatted_lines = [
        "cost driver: cost component: component quantity: cost input: cost input quantity: cost parameter: formula: cost"
    ]

    # Initialize defaults
    all_cost_drivers = all_cost_drivers or []
    all_cost_components = all_cost_components or []
    all_cost_inputs = all_cost_inputs or []
    all_cost_parameters = all_cost_parameters or []
    
    # Build lookup structures from updated_structure
    structure_data = {}
    if updated_structure and updated_structure.get('cost_drivers'):
        for driver in updated_structure['cost_drivers']:
            driver_name = driver.get('cost_driver_name')
            structure_data[driver_name] = {
                'components': {}
            }
            
            for component in driver.get('cost_components', []):
                component_name = component.get('cost_component_name')
                structure_data[driver_name]['components'][component_name] = {
                    'quantity': component.get('quantity'),
                    'quantity_justification': component.get('quantity_justification'),
                    'inputs': {}
                }
                
                for cost_input in component.get('cost_inputs', []):
                    input_name = cost_input.get('cost_input_name')
                    monthly_cost = cost_input.get('monthly_cost_egp', 0)
                    is_percentage = cost_input.get('is_percentage', False)
                    percentage_value = cost_input.get('percentage_value')
                    input_quantity = cost_input.get('quantity')
                    input_qty_just = cost_input.get('quantity_justification')
                    cost_params = cost_input.get('cost_parameters') or []
                    formula = cost_input.get('formula') or ""

                    structure_data[driver_name]['components'][component_name]['inputs'][input_name] = {
                        'monthly_cost': monthly_cost,
                        'is_percentage': is_percentage,
                        'percentage_value': percentage_value,
                        'quantity': input_quantity,
                        'quantity_justification': input_qty_just,
                        'cost_parameters': cost_params,
                        'formula': formula,
                    }
    
    # Build a map of which components belong to which driver
    driver_to_components = {}
    for driver_name, component_name in all_cost_components:
        if driver_name not in driver_to_components:
            driver_to_components[driver_name] = []
        driver_to_components[driver_name].append(component_name)
    
    # Build a map of which inputs belong to which (driver, component)
    component_to_inputs = {}
    for driver_name, component_name, input_name in all_cost_inputs:
        key = (driver_name, component_name)
        if key not in component_to_inputs:
            component_to_inputs[key] = []
        component_to_inputs[key].append(input_name)

    # Build a map of which parameters belong to which (driver, component, input)
    input_to_parameters = {}
    for driver_name, component_name, input_name, param_name in all_cost_parameters:
        key = (driver_name, component_name, input_name)
        if key not in input_to_parameters:
            input_to_parameters[key] = []
        input_to_parameters[key].append(param_name)
    
    # Process each cost driver
    for driver_name in all_cost_drivers:
        # Check if this driver has components
        if driver_name not in driver_to_components:
            # Stage 1: Driver identified, no components/inputs/parameters yet
            formatted_lines.append(
                f"{driver_name}: Not yet estimated: Not yet estimated: Not yet estimated: Not yet estimated: Not yet estimated: Not yet estimated: Not yet estimated"
            )
        else:
            # Driver has components
            components_for_driver = driver_to_components[driver_name]
            
            for component_name in components_for_driver:
                key = (driver_name, component_name)
                
                # Check if this component has inputs
                if key not in component_to_inputs:
                    # Stage 2: Component identified, no inputs yet
                    comp_data = structure_data.get(driver_name, {}).get('components', {}).get(component_name, {})
                    comp_qty = comp_data.get('quantity')
                    comp_qty_str = "Not yet estimated" if comp_qty is None else str(comp_qty)

                    formatted_lines.append(
                        f"{driver_name}: {component_name}: {comp_qty_str}: "
                        f"Not yet estimated: Not yet estimated: Not yet estimated: Not yet estimated: Not yet estimated"
                    )
                else:
                    # Component has inputs
                    inputs_for_component = component_to_inputs[key]

                    # Get component-level quantity (same for all its inputs)
                    comp_data = structure_data.get(driver_name, {}).get('components', {}).get(component_name, {})
                    comp_qty = comp_data.get('quantity')
                    comp_qty_str = "Not yet estimated" if comp_qty is None else str(comp_qty)

                    for input_name in inputs_for_component:
                        input_qty_str = "Not yet estimated"
                        cost_str = "Not yet estimated"
                        formula_str = "Not yet estimated"
                        param_list = input_to_parameters.get((driver_name, component_name, input_name), [])

                        if (driver_name in structure_data and
                            component_name in structure_data[driver_name]['components'] and
                            input_name in structure_data[driver_name]['components'][component_name]['inputs']):

                            cost_data = structure_data[driver_name]['components'][component_name]['inputs'][input_name]
                            monthly_cost = cost_data['monthly_cost']
                            input_qty = cost_data.get('quantity')
                            formula_str = cost_data.get('formula') or ("Not yet estimated" if not param_list else "")

                            if input_qty is not None:
                                input_qty_str = str(input_qty)

                            if monthly_cost > 0:
                                if cost_data['is_percentage'] and cost_data['percentage_value']:
                                    cost_str = f"{cost_data['percentage_value']}% → {monthly_cost:,.0f} EGP"
                                else:
                                    cost_str = f"{monthly_cost:,.0f} EGP"

                            if formula_str == "" and param_list:
                                formula_str = "Not yet estimated"
                            if formula_str == "" and not param_list:
                                formula_str = "Not yet estimated"

                        if param_list:
                            for param_name in param_list:
                                formatted_lines.append(
                                    f"{driver_name}: {component_name}: {comp_qty_str}: "
                                    f"{input_name}: {input_qty_str}: {param_name}: {formula_str}: {cost_str}"
                                )
                        else:
                            formatted_lines.append(
                                f"{driver_name}: {component_name}: {comp_qty_str}: "
                                f"{input_name}: {input_qty_str}: Not yet estimated: {formula_str}: {cost_str}"
                            )
    
    return "\n".join(formatted_lines)


def format_structure_for_component_estimation(all_cost_drivers, structure_so_far, current_driver_name):
    """
    Scoped structure for component estimation.

    Shows ALL cost drivers, and for each driver any cost components that have been
    estimated so far (names only, no quantities or justifications).
    """
    all_cost_drivers = all_cost_drivers or []
    if not all_cost_drivers:
        return "cost driver: cost component\n(no drivers yet)"

    driver_to_components = {}
    if structure_so_far and structure_so_far.get("cost_drivers"):
        for dr in structure_so_far["cost_drivers"]:
            dname = dr.get("cost_driver_name")
            for comp in dr.get("cost_components", []):
                cname = comp.get("cost_component_name")
                if cname:
                    driver_to_components.setdefault(dname, []).append(cname)

    lines = ["cost driver: cost component"]
    for dname in all_cost_drivers:
        comps = driver_to_components.get(dname, [])
        if not comps:
            lines.append(f"{dname}: (no components yet)")
        else:
            for cname in comps:
                lines.append(f"{dname}: {cname}")

    return "\n".join(lines)


def format_structure_for_input_estimation(structure_so_far, current_driver_name, current_component_name):
    """
    Scoped structure for cost input estimation.

    Shows ALL cost drivers, their estimated cost components, and for each component
    any cost inputs that have been estimated so far (names only).
    """
    if not structure_so_far or not structure_so_far.get("cost_drivers"):
        return "cost driver: cost component: cost input\n(no structure yet)"

    lines = ["cost driver: cost component: cost input"]
    for dr in structure_so_far.get("cost_drivers", []):
        dname = dr.get("cost_driver_name")
        components = dr.get("cost_components", []) or []
        if not components:
            lines.append(f"{dname}: (no components yet): (no inputs yet)")
            continue
        for comp in components:
            cname = comp.get("cost_component_name")
            inputs = comp.get("cost_inputs", []) or []
            if not inputs:
                lines.append(f"{dname}: {cname}: (no inputs yet)")
            else:
                for inp in inputs:
                    iname = inp.get("cost_input_name")
                    if iname:
                        lines.append(f"{dname}: {cname}: {iname}")

    return "\n".join(lines)


def format_structure_for_cost_estimation(structure_so_far, current_driver_name, current_component_name):
    """
    Scoped structure for cost (value) estimation: current cost component only.
    Shows "cost component: cost input: cost" for each input under the current component.
    """
    if not structure_so_far or not structure_so_far.get("cost_drivers"):
        return "(No structure yet)"
    for dr in structure_so_far["cost_drivers"]:
        if dr.get("cost_driver_name") != current_driver_name:
            continue
        for comp in dr.get("cost_components", []):
            if comp.get("cost_component_name") != current_component_name:
                continue
            lines = ["cost component: cost input: cost"]
            for inp in comp.get("cost_inputs", []):
                iname = inp.get("cost_input_name", "")
                cost = inp.get("monthly_cost_egp")
                cost_str = f"{cost:,.0f} EGP" if (cost is not None and cost > 0) else "Not yet estimated"
                lines.append(f"{current_component_name}: {iname}: {cost_str}")
            return "\n".join(lines)
    return f"{current_component_name}: (no inputs yet)"


def format_structure_for_parameter_estimation(structure_so_far, current_driver_name, current_component_name, current_input_name):
    """
    Scoped structure for cost parameter estimation.

    Shows ALL cost drivers, their components, inputs, and (where available)
    cost parameters that have been estimated so far (names only).
    """
    if not structure_so_far or not structure_so_far.get("cost_drivers"):
        return "cost driver: cost component: cost input: cost parameter\n(no structure yet)"

    lines = ["cost driver: cost component: cost input: cost parameter"]
    for dr in structure_so_far.get("cost_drivers", []):
        dname = dr.get("cost_driver_name")
        for comp in dr.get("cost_components", []) or []:
            cname = comp.get("cost_component_name")
            for inp in comp.get("cost_inputs", []) or []:
                iname = inp.get("cost_input_name")
                params = inp.get("cost_parameters") or []
                if not params:
                    lines.append(f"{dname}: {cname}: {iname}: (no parameters yet)")
                else:
                    for p in params:
                        pname = p.get("parameter_name")
                        if pname:
                            lines.append(f"{dname}: {cname}: {iname}: {pname}")

    return "\n".join(lines)


def format_activity_facts(activity_facts):
    """
    Format the structured answers collected by the description chat (activity facts)
    into a labelled block for agent prompts. Returns "" when no facts are available.
    """
    if not activity_facts or not isinstance(activity_facts, dict):
        return ""
    lines = []
    for key, value in activity_facts.items():
        if value is None or str(value).strip() == "":
            continue
        label = str(key).replace("_", " ").strip().capitalize()
        lines.append(f"- {label}: {str(value).strip()}")
    if not lines:
        return ""
    return "STRUCTURED ACTIVITY FACTS (captured directly from the user — numbers here are authoritative):\n" + "\n".join(lines)


def format_resource_plan(resource_plan):
    """
    Format the resourcing & scaling plan into an authoritative block for agent prompts.

    The plan is the single source of truth for quantities: downstream agents must
    take counts/durations from here instead of inventing them.
    Returns "" when no plan is available.
    """
    if not resource_plan or not isinstance(resource_plan, dict):
        return ""

    lines = ["RESOURCE & SCALING PLAN (authoritative quantities — do NOT invent counts; use these values):"]

    vd = resource_plan.get("volume_driver") or {}
    if vd:
        vd_val = vd.get("value")
        vd_val_str = f"{vd_val:g}" if isinstance(vd_val, (int, float)) else str(vd_val)
        lines.append(f"- Volume driver: {vd_val_str} {vd.get('unit') or vd.get('name', '')}".rstrip())
        if vd.get("derivation"):
            lines.append(f"    (basis: {vd['derivation']})")

    dur = resource_plan.get("duration_months")
    wd = resource_plan.get("working_days_per_month")
    if dur is not None:
        dur_line = f"- Project duration: {dur:g} months" if isinstance(dur, (int, float)) else f"- Project duration: {dur} months"
        if wd:
            dur_line += f" ({wd:g} working days/month)" if isinstance(wd, (int, float)) else f" ({wd} working days/month)"
        lines.append(dur_line)

    crews = resource_plan.get("crews") or {}
    if crews.get("count") is not None:
        lines.append(f"- Parallel crews: {crews['count']}" + (f" — {crews['composition']}" if crews.get("composition") else ""))
        if crews.get("derivation"):
            lines.append(f"    (basis: {crews['derivation']})")

    thr = resource_plan.get("throughput") or {}
    if thr.get("value") is not None:
        thr_val = thr.get("value")
        thr_val_str = f"{thr_val:g}" if isinstance(thr_val, (int, float)) else str(thr_val)
        lines.append(f"- Throughput: {thr_val_str} {thr.get('unit', '')}".rstrip())
        if thr.get("derivation"):
            lines.append(f"    (basis: {thr['derivation']})")

    resources = resource_plan.get("resources") or []
    if resources:
        lines.append("- Planned resources (scaling class explains HOW each quantity scales):")
        for r in resources:
            qty = r.get("quantity")
            qty_str = f"{qty:g}" if isinstance(qty, (int, float)) else str(qty)
            line = f"    * {r.get('resource_name', '?')}: {qty_str} {r.get('unit', '')}".rstrip()
            line += f" [{r.get('scaling_class', 'unclassified')}, confidence: {r.get('confidence', 'n/a')}"
            if r.get("source"):
                line += f", source: {r['source']}"
            line += "]"
            lines.append(line)
            if r.get("derivation"):
                lines.append(f"        basis: {r['derivation']}")
            if r.get("justification"):
                lines.append(f"        justification: {r['justification']}")

    assumptions = resource_plan.get("assumptions") or []
    if assumptions:
        lines.append("- Assumptions:")
        for a in assumptions:
            lines.append(f"    * {a}")

    return "\n".join(lines)


SCALING_CLASS_DEFINITIONS = """SCALING CLASS DEFINITIONS:
- per_unit_of_volume: quantity scales 1:1 (or a fixed ratio) with the volume driver
  (e.g. site permits, per-site consumables, per-site acceptance tests).
- capacity_pool: a shared pool sized by throughput/crews, NOT by the volume driver
  (e.g. trucks, cranes, test instruments, crew vans — 3 trucks can serve 40 sites).
- time_based: quantity fixed, cost scales with duration only (e.g. project manager, site office rent).
- fixed_one_time: one-time regardless of volume or duration (e.g. regulatory license, mobilization fee)."""


if __name__ == "__main__":
    """Test the formatter at different stages"""
    
    print("=" * 80)
    print("STAGE 1: Cost drivers identified")
    print("=" * 80)
    all_drivers = ["Shelters", "Transportation", "Installation Labor", "Site Preparation"]
    structure_stage1 = {"cost_drivers": []}
    print(format_structure(structure_stage1, all_drivers, [], []))
    print()
    
    print("=" * 80)
    print("STAGE 2: Components identified for first driver (Shelters)")
    print("=" * 80)
    all_components = [
        ("Shelters", "Prefabricated Cabins"),
        ("Shelters", "Installation Hardware"),
        ("Shelters", "Environmental Control Units")
    ]
    structure_stage2 = {
        "cost_drivers": [
            {
                "cost_driver_name": "Shelters",
                "cost_components": [
                    {"cost_component_name": "Prefabricated Cabins", "cost_inputs": []},
                    {"cost_component_name": "Installation Hardware", "cost_inputs": []},
                    {"cost_component_name": "Environmental Control Units", "cost_inputs": []}
                ]
            }
        ]
    }
    print(format_structure(structure_stage2, all_drivers, all_components, []))
    print()
    
    print("=" * 80)
    print("STAGE 3: Inputs identified for first component (Prefabricated Cabins)")
    print("=" * 80)
    all_inputs = [
        ("Shelters", "Prefabricated Cabins", "Prefabricated Cabin"),
        ("Shelters", "Prefabricated Cabins", "Transportation Cost for Prefabricated Cabin"),
        ("Shelters", "Prefabricated Cabins", "Installation Hardware for Prefabricated Cabin")
    ]
    structure_stage3 = {
        "cost_drivers": [
            {
                "cost_driver_name": "Shelters",
                "cost_components": [
                    {
                        "cost_component_name": "Prefabricated Cabins",
                        "cost_inputs": [
                            {"cost_input_name": "Prefabricated Cabin", "monthly_cost_egp": 0, "is_percentage": False, "percentage_value": None},
                            {"cost_input_name": "Transportation Cost for Prefabricated Cabin", "monthly_cost_egp": 0, "is_percentage": False, "percentage_value": None},
                            {"cost_input_name": "Installation Hardware for Prefabricated Cabin", "monthly_cost_egp": 0, "is_percentage": False, "percentage_value": None}
                        ]
                    },
                    {"cost_component_name": "Installation Hardware", "cost_inputs": []},
                    {"cost_component_name": "Environmental Control Units", "cost_inputs": []}
                ]
            }
        ]
    }
    print(format_structure(structure_stage3, all_drivers, all_components, all_inputs))
    print()
    
    print("=" * 80)
    print("STAGE 4: Cost estimated for first input (Prefabricated Cabin)")
    print("=" * 80)
    structure_stage4 = {
        "cost_drivers": [
            {
                "cost_driver_name": "Shelters",
                "cost_components": [
                    {
                        "cost_component_name": "Prefabricated Cabins",
                        "cost_inputs": [
                            {"cost_input_name": "Prefabricated Cabin", "monthly_cost_egp": 180000, "is_percentage": False, "percentage_value": None},
                            {"cost_input_name": "Transportation Cost for Prefabricated Cabin", "monthly_cost_egp": 0, "is_percentage": False, "percentage_value": None},
                            {"cost_input_name": "Installation Hardware for Prefabricated Cabin", "monthly_cost_egp": 0, "is_percentage": False, "percentage_value": None}
                        ]
                    },
                    {"cost_component_name": "Installation Hardware", "cost_inputs": []},
                    {"cost_component_name": "Environmental Control Units", "cost_inputs": []}
                ]
            }
        ]
    }
    print(format_structure(structure_stage4, all_drivers, all_components, all_inputs))
    print()
