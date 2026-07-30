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
from collections import defaultdict

llm = chat_llm(max_tokens=5000)

embeddings = embeddings_client()

# drivers_db = FAISS.load_local(
#     "vector_dbs/cost_drivers_faiss",
#     embeddings,
#     allow_dangerous_deserialization=True
# )

drivers_db = FAISS.load_local(
    "new_vector_dbs/cost_drivers_faiss",
    embeddings,
    allow_dangerous_deserialization=True
)

# drivers_db.merge_from(new_drivers_db)

cost_drivers_retriever = drivers_db.as_retriever(
    search_type="similarity",
    search_kwargs={
        "k": 20
    }
)

## A helper function for retrieving the historical activities and their cost drivers


def return_structured_cost_drivers(query):
    results = cost_drivers_retriever.invoke(query)

    projects = {}

    for res in results:
        project = res.metadata.get("Project", "Unknown Project")
        desc = res.metadata.get("Project Description", "")
        drivers = res.metadata.get("Cost Drivers", [])

        if project not in projects:
            projects[project] = {
                "description": desc,
                "cost_drivers": set()
            }

        # normalize slightly to reduce duplicates
        normalized = [d.strip() for d in drivers]
        projects[project]["cost_drivers"].update(normalized)

    text_output = []

    for project, data in projects.items():
        text_output.append(f"Project: {project}")
        text_output.append(f"Project Description: {data['description']}")
        text_output.append("Cost Drivers:")

        for d in sorted(data["cost_drivers"]):
            text_output.append(f"- {d}")

        text_output.append("")  # spacing between projects

    return "\n".join(text_output)


## The agent that takes the identifies the cost drivers from the activity description
def cost_drivers_identifier(activity_description, resource_plan=None, prompt_log_path=None, prompt_log_section=None):
    from structure_formatter import format_resource_plan
    historical_activities = return_structured_cost_drivers(activity_description)

    plan_block = format_resource_plan(resource_plan)
    if plan_block:
        plan_block = f"\n{plan_block}\n\nThe cost drivers you identify must cover the resources in the plan above.\n"

    # The structure of the response
    class CostDriver(BaseModel):
        cost_driver_name: str = Field(..., description="Name of the cost driver")
        justification: str = Field(..., description="Why this cost driver applies")
        materiality: str = Field(
            "medium",
            description="'high' if this is a leading cost of the activity, 'medium' if it matters, "
                        "'low' if minor. Low-materiality drivers may be dropped, so be honest.",
        )

    class CostDriverResponse(BaseModel):
        items: List[CostDriver]

    cost_driver_llm = llm.with_structured_output(CostDriverResponse)


    prompt = f"""
        You are Design to cost analyst for the supply chain team at Vodafone Egypt,
        An activity has cost drivers (Such as Employment, Transportation), 
        and each cost driver has cost components (Such as Engineer, Team Leader, Car),
        and each cost components has cost inputs (Such as Net Salary, Salary Taxes, Driver Salary).

        You will be given an activity description and your role is to identify the Cost Drivers for this activity.

        An example of Cost Drivers for historical activities are:
        {historical_activities}

        These examples show the STYLE of cost drivers. They are not a checklist — do not reproduce
        an item just because a historical project had it.
        You should give a justification for each cost driver of why it's required for this activity.
        {plan_block}
        {parsimony_rules("cost drivers", CAPS["drivers"])}
        Activity Description is:
        {activity_description}
        """

    if prompt_log_path and prompt_log_section:
        with open(prompt_log_path, "a", encoding="utf-8") as f:
            f.write(f"{prompt_log_section}\n[Prompt]\n{prompt}\n---\n")

    response = cost_driver_llm.invoke(prompt)
    cost_drivers = [dict(item) for item in response.items]

    cost_drivers_names = [driver['cost_driver_name'] for driver in cost_drivers]

    return cost_drivers, cost_drivers_names


if __name__ == "__main__":
    activity = "The service involves the deployment and commissioning of outdoor telecom equipment shelters, including prefabricated cabins, power backup systems, and environmental control units. The scope covers site preparation, transportation of shelters and equipment, mechanical and electrical installation, integration with existing network infrastructure, and on-site testing. The service also includes manpower, lifting equipment, safety measures, and coordination with local authorities to ensure compliance and timely activation."
    cost_drivers, cost_drivers_names = cost_drivers_identifier(activity)
    for driver in cost_drivers:
        print("Cost Driver:", driver['cost_driver_name'])
        print("Justification:", driver['justification'])
        print("\n")