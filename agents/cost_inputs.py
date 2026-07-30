from typing import List, Dict, Any, Optional
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
from pydantic import BaseModel, Field
from estimation_scope import CAPS, parsimony_rules
from env_config import chat_llm, embeddings_client
from langchain_classic.vectorstores import FAISS
from langchain_classic.tools.retriever import create_retriever_tool
from langchain_community.tools import DuckDuckGoSearchResults
from langchain_classic.tools import Tool
import requests
from tavily import TavilyClient
from structure_formatter import format_structure, format_structure_for_input_estimation

llm = chat_llm(max_tokens=5000)

embeddings = embeddings_client()

# cost_inputs_db = FAISS.load_local(
#     "vector_dbs/cost_inputs_faiss",
#     embeddings,
#     allow_dangerous_deserialization=True
# )

cost_inputs_db = FAISS.load_local(
    "new_vector_dbs/cost_inputs_faiss",
    embeddings,
    allow_dangerous_deserialization=True
)

# cost_inputs_db.merge_from(new_cost_inputs_db)

cost_inputs_retriever = cost_inputs_db.as_retriever(
    search_type="similarity",
    search_kwargs={
        "k": 20
    }
)

## A helper function for retrieving the historical activities and their cost inputs
from collections import defaultdict

def safe_str(val):
    """Convert a value to a stripped string, safely ignoring None or NaN."""
    if isinstance(val, str):
        return val.strip()
    return ""  # default for None, NaN, or other types

def return_structured_cost_inputs(activity_description, cost_driver, cost_input):
    query = f"Activity Description: {activity_description}, Cost Driver: {cost_driver}, Cost Input: {cost_input}"
    results = cost_inputs_retriever.invoke(query)

    # key = (Project, Project Description)
    projects = defaultdict(lambda: defaultdict(lambda: defaultdict(set)))
    project_info = {}

    for res in results:
        meta = res.metadata

        project = meta.get("Project", "Unknown Project")
        desc = meta.get("Project Description", "")
        driver = meta.get("Cost Driver", "")
        component = meta.get("Cost Component", "")
        inputs = meta.get("Cost Inputs", [])

        project_info[project] = desc

        # add cost inputs under the component and driver
        for inp in inputs:
            inp_str = safe_str(inp)
            if inp_str:  # skip empty/null values
                projects[project][driver][component].add(inp_str)

    # build text output
    text_output = []

    for project, drivers in projects.items():
        text_output.append(f"Project: {project}")
        text_output.append(f"Project Description: {project_info[project]}")
        
        for driver, components in sorted(drivers.items()):
            text_output.append(f"For Cost Driver {driver}:")
            for comp, inputs in sorted(components.items()):
                text_output.append(f"  Cost Component {comp}, Cost Inputs:")
                for inp in sorted(inputs):
                    text_output.append(f"    - {inp}")
        text_output.append("")  # spacing between projects

    return "\n".join(text_output)


def cost_inputs_identifier(activity_description, cost_driver, cost_component, structure_so_far=None, all_cost_drivers=None, all_cost_components=None, resource_plan=None, prompt_log_path=None, prompt_log_section=None):
    from structure_formatter import format_resource_plan
    class CostInputs(BaseModel):
        cost_input_name: str = Field(..., description="Name of the cost input")
        justification: str = Field(..., description="Why this cost input applies")
        materiality: str = Field(
            "medium",
            description="'high' if this is a leading cost of the component, 'medium' if it matters, "
                        "'low' if minor. Low-materiality inputs may be dropped, so be honest.",
        )


    class CostInputsResponse(BaseModel):
        items: List[CostInputs]

    historical_inputs = return_structured_cost_inputs(activity_description, cost_driver, cost_component)
    
    cost_inputs_llm = llm.with_structured_output(CostInputsResponse)

    # Scoped structure for input estimation: current driver only; first component → driver + components; later → component: input for already-estimated components
    formatted_structure = format_structure_for_input_estimation(structure_so_far, cost_driver, cost_component)
    structure_context = "\n\nIMPORTANT: Below is the structure identified so far for this cost driver (relevant to this step only). Use this to:\n"
    structure_context += "1. Maintain consistency with cost inputs already identified for other cost components in this driver\n"
    structure_context += "2. Avoid duplicating similar cost inputs across different cost components\n"
    structure_context += "3. Ensure your cost inputs complement the existing structure\n\n"
    structure_context += f"{formatted_structure}\n"

    _plan_block = format_resource_plan(resource_plan)
    if _plan_block:
        _plan_block += ("\n\nKeep cost inputs consistent with the plan: a pooled resource gets pool-level inputs "
                        "(e.g. 'Truck Rental' for the 3-truck pool), not per-volume-unit inputs.")

    prompt = f"""
    You are Design to cost analyst for the supply chain team at Vodafone Egypt,
    An activity has cost drivers (Such as Employment, Transportation), 
    and each cost driver has cost components (Such as Engineer, Team Leader, Car),
    and each cost components has cost inputs (Such as Gross Salary, Car Rental).

    You will be given an activity description and a cost driver and a cost component and your role is to identify the Cost Inputs for this cost component of this cost driver of this activity.

    An example of Cost Inputs for historical activities are:
    {historical_inputs}

    These examples show the STYLE of cost inputs. They are not a checklist — do not reproduce
    an item just because a historical project had it.
    You should give a justification for each cost inputs of why it's required for this activity.
    {structure_context}
    {_plan_block}
    {parsimony_rules("cost inputs", CAPS["inputs"])}
    Activity Description is:
    {activity_description}

    You should identify the Cost Inputs for the {cost_component} cost component of the {cost_driver} cost driver.
    Make sure your identified cost inputs are appropriate for the {cost_component} cost component and complement the existing structure.

    IMPORTANT: Do NOT estimate quantities (durations, recurring counts) for cost inputs. Quantities are
    not computed at this step; they will be baked directly into the formula at the cost-parameter step.
    Only return the cost input name and justification.
    """

    if prompt_log_path and prompt_log_section:
        with open(prompt_log_path, "a", encoding="utf-8") as f:
            f.write(f"{prompt_log_section}\n[Prompt]\n{prompt}\n---\n")

    response = cost_inputs_llm.invoke(prompt)
    cost_inputs = [dict(item) for item in response.items]
    cost_inputs_names = [input['cost_input_name'] for input in cost_inputs]

    # Initialize cost inputs with default estimation values
    for cost_input in cost_inputs:
        cost_input['monthly_cost_egp'] = 0
        cost_input['is_percentage'] = False
        cost_input['percentage_value'] = None
        cost_input['sharing'] = 1
        # Default quantity=1 (no LLM estimation); actual durations/counts are embedded in the formula.
        cost_input.setdefault('quantity', 1)

    # Update structure_so_far with the new cost inputs
    # Create a deep copy to avoid modifying the original
    import copy
    updated_structure = copy.deepcopy(structure_so_far) if structure_so_far else {"cost_drivers": []}
    
    # Find the current cost driver in the structure
    driver_found = False
    for driver in updated_structure.get('cost_drivers', []):
        if driver.get('cost_driver_name') == cost_driver:
            driver_found = True
            # Find the current cost component in the driver's components
            component_found = False
            for component in driver.get('cost_components', []):
                if component.get('cost_component_name') == cost_component:
                    component['cost_inputs'] = cost_inputs
                    component_found = True
                    break
            
            # If component not found, add it
            if not component_found:
                if 'cost_components' not in driver:
                    driver['cost_components'] = []
                driver['cost_components'].append({
                    'cost_component_name': cost_component,
                    'cost_inputs': cost_inputs
                })
            break
    
    # If driver not found, add it with the component and inputs
    if not driver_found:
        updated_structure['cost_drivers'].append({
            'cost_driver_name': cost_driver,
            'cost_components': [{
                'cost_component_name': cost_component,
                'cost_inputs': cost_inputs
            }]
        })

    return cost_inputs, cost_inputs_names, updated_structure

if __name__ == "__main__":
    try:
        from .cost_component import cost_components_identifier
    except ImportError:
        from cost_component import cost_components_identifier
    
    activity_description = "Telecom installation in jan, feb and march"
    cost_drivers = ['Employment', 'Transportation', 'Tools', 'Accommodation', 'Margin', 'Risk Calculation']
    
    # First, run cost_components_identifier for all cost drivers
    print("=" * 60)
    print("STEP 1: Identifying Cost Components for all Cost Drivers")
    print("=" * 60)
    
    structure_so_far = None
    all_cost_driver_names = cost_drivers
    
    for cost_driver in cost_drivers:
        print(f"\nProcessing Cost Driver: {cost_driver}")
        cost_components, cost_components_names, structure_so_far = cost_components_identifier(
            activity_description,
            cost_driver,
            structure_so_far,
            all_cost_driver_names
        )
        print(f"  Identified Components: {cost_components_names}")
    
    print("\n" + "=" * 60)
    print("STEP 2: Identifying Cost Inputs for all Cost Components")
    print("=" * 60)
    
    # Build all_cost_components list from structure_so_far
    all_cost_components = []
    for driver in structure_so_far.get('cost_drivers', []):
        driver_name = driver.get('cost_driver_name')
        for comp in driver.get('cost_components', []):
            comp_name = comp.get('cost_component_name')
            all_cost_components.append((driver_name, comp_name))
    
    # Initialize structure for cost inputs
    inputs_structure_so_far = {
        "cost_drivers": []
    }
    
    # Now run cost_inputs_identifier for each component
    for driver in structure_so_far.get('cost_drivers', []):
        driver_name = driver.get('cost_driver_name')
        for component in driver.get('cost_components', []):
            component_name = component.get('cost_component_name')
            
            print(f"\nProcessing: {driver_name} -> {component_name}")
            cost_inputs, cost_inputs_names, inputs_structure_so_far = cost_inputs_identifier(
                activity_description,
                driver_name,
                component_name,
                inputs_structure_so_far,
                all_cost_drivers=all_cost_driver_names,
                all_cost_components=all_cost_components
            )
            print(f"  Identified Cost Inputs: {cost_inputs_names}")
    
    # Build all_cost_inputs for final formatting
    all_cost_inputs_final = []
    if inputs_structure_so_far and inputs_structure_so_far.get('cost_drivers'):
        for driver in inputs_structure_so_far['cost_drivers']:
            driver_name = driver.get('cost_driver_name')
            for comp in driver.get('cost_components', []):
                comp_name = comp.get('cost_component_name')
                for inp in comp.get('cost_inputs', []):
                    inp_name = inp.get('cost_input_name')
                    all_cost_inputs_final.append((driver_name, comp_name, inp_name))
    
    print("\n" + "=" * 60)
    print("FINAL STRUCTURE:")
    print("=" * 60)
    print(format_structure(inputs_structure_so_far, all_cost_driver_names, all_cost_components, all_cost_inputs_final))
