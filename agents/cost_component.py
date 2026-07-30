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
from structure_formatter import format_structure, format_structure_for_component_estimation

llm = chat_llm(max_tokens=5000)

embeddings = embeddings_client()

# components_db = FAISS.load_local(
#     "vector_dbs/cost_components_faiss",
#     embeddings,
#     allow_dangerous_deserialization=True
# )

components_db = FAISS.load_local(
    "new_vector_dbs/cost_components_faiss",
    embeddings,
    allow_dangerous_deserialization=True
)

# components_db.merge_from(new_components_db)

cost_components_retriever = components_db.as_retriever(
    search_type="similarity",
    search_kwargs={
        "k": 20
    }
)

## A helper function for retrieving the historical activities and their cost components
from collections import defaultdict

def return_structured_cost_components(activity_description, cost_driver):
    query = f"Activity Description: {activity_description}, Cost Driver: {cost_driver}"
    results = cost_components_retriever.invoke(query)

    # key = (project, project description)
    projects = defaultdict(lambda: defaultdict(set))

    project_info = {}

    for res in results:
        meta = res.metadata
        
        project = meta.get("Project", "Unknown Project")
        desc = meta.get("Project Description", "")
        driver = meta.get("Cost Driver", "")
        components = meta.get("Cost Components", [])

        # store project info (description)
        project_info[project] = desc

        # add components to the driver under the project
        for comp in components:
            projects[project][driver].add(comp.strip())

    # build output text
    text_output = []

    for project, drivers in projects.items():
        text_output.append(f"Project: {project}")
        text_output.append(f"Project Description: {project_info[project]}")
        
        for driver, comps in sorted(drivers.items()):
            text_output.append(f"For Cost Driver {driver}, Cost Components are:")
            for c in sorted(comps):
                text_output.append(f"  - {c}")
        text_output.append("")  # spacing between projects

    return "\n".join(text_output)




def cost_components_identifier(activity_description, cost_driver, structure_so_far=None, all_cost_drivers=None, resource_plan=None, prompt_log_path=None, prompt_log_section=None):
    from structure_formatter import format_resource_plan
    class CostComponent(BaseModel):
        cost_component_name: str = Field(..., description="Name of the cost component")
        justification: str = Field(..., description="Why this cost component applies")
        materiality: str = Field(
            "medium",
            description="'high' if this is a leading cost of the driver, 'medium' if it matters, "
                        "'low' if minor. Low-materiality components may be dropped, so be honest.",
        )


    class CostComponentResponse(BaseModel):
        items: List[CostComponent]

    historical_components = return_structured_cost_components(activity_description, cost_driver)
    
    cost_component_llm = llm.with_structured_output(CostComponentResponse)

    # Scoped structure for component estimation: first driver → list of drivers; later drivers → driver: component for already-estimated drivers
    all_cost_drivers = all_cost_drivers or []
    formatted_structure = format_structure_for_component_estimation(all_cost_drivers, structure_so_far, cost_driver)
    structure_context = "\n\nIMPORTANT: Below is the structure identified so far (relevant to this step only). Use this to:\n"
    structure_context += "1. Maintain consistency with components already identified for other cost drivers\n"
    structure_context += "2. Avoid duplicating similar components across different cost drivers\n"
    structure_context += "3. Ensure your components complement the existing structure\n\n"
    structure_context += f"{formatted_structure}\n"

    _plan_block = format_resource_plan(resource_plan)
    if _plan_block:
        _plan_block += ("\n\nAlign components with the plan: shared pools are ONE component sized by the pool "
                        "(e.g. 'Light Trucks' as a pool of 3), never one component per unit of volume.")

    prompt = f"""
    You are Design to cost analyst for the supply chain team at Vodafone Egypt,
    An activity has cost drivers (Such as Employment, Transportation), 
    and each cost driver has cost components (Such as Engineer, Team Leader, Car),
    and each cost components has cost inputs (Such as Net Salary, Salary Taxes, Driver Salary).

    You will be given an activity description and a cost driver and your role is to identify the Cost Components for this cost driver of this activity.

    An example of Cost Components for historical activities are:
    {historical_components}

    These examples show the STYLE of cost components. They are not a checklist — do not reproduce
    an item just because a historical project had it.
    You should give a justification for each cost component of why it's required for this activity.
    {structure_context}
    {_plan_block}
    {parsimony_rules("cost components", CAPS["components"])}
    Activity Description is:
    {activity_description}

    You should identify the Cost Components for the {cost_driver} cost driver.
    Make sure your identified components are appropriate for the {cost_driver} cost driver and complement the existing structure.

    IMPORTANT: Do NOT estimate quantities for cost components. Quantities (counts, durations, etc.) are
    not computed at this step; they will be baked directly into the formula at the cost-parameter step.
    Only return the cost component name and justification.
    """

    if prompt_log_path and prompt_log_section:
        with open(prompt_log_path, "a", encoding="utf-8") as f:
            f.write(f"{prompt_log_section}\n[Prompt]\n{prompt}\n---\n")

    response = cost_component_llm.invoke(prompt)
    cost_components = [dict(item) for item in response.items]
    # Default quantity=1 (no LLM estimation); user can override in UI and formulas bake in actual counts.
    for component in cost_components:
        component.setdefault('quantity', 1)
    cost_components_names = [component['cost_component_name'] for component in cost_components]

    # Update structure_so_far with the new cost components
    # Create a deep copy to avoid modifying the original
    import copy
    updated_structure = copy.deepcopy(structure_so_far) if structure_so_far else {"cost_drivers": []}
    
    # Find the current cost driver in the structure and update it with components
    current_driver_found = False
    for driver in updated_structure.get('cost_drivers', []):
        if driver.get('cost_driver_name') == cost_driver:
            driver['cost_components'] = cost_components
            current_driver_found = True
            break
    
    # If the cost driver is not in the structure yet, add it
    if not current_driver_found:
        updated_structure['cost_drivers'].append({
            'cost_driver_name': cost_driver,
            'cost_components': cost_components
        })

    return cost_components, cost_components_names, updated_structure

if __name__ == "__main__":
    cost_drivers = ['Employment', 'Transportation', 'Tools', 'Accommodation', 'Margin', 'Risk Calculation']
    structure = None
    for cost_driver in cost_drivers:
        c, cn, structure = cost_components_identifier("Telecom installation", cost_driver, structure, cost_drivers)
        # Build all_cost_components for formatting
        all_cost_components = []
        if structure and structure.get('cost_drivers'):
            for driver in structure['cost_drivers']:
                driver_name = driver.get('cost_driver_name')
                for comp in driver.get('cost_components', []):
                    comp_name = comp.get('cost_component_name')
                    all_cost_components.append((driver_name, comp_name))
        print(f"Formatted Structure:\n{format_structure(structure, cost_drivers, all_cost_components, [])}")
        print("---------------------------")