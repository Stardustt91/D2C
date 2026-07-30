from typing import List, Dict, Any, Optional, Tuple
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
from pydantic import BaseModel, Field
from env_config import reasoning_llm, embeddings_client, tavily_key
from langchain_classic.vectorstores import FAISS
from langchain_classic.tools.retriever import create_retriever_tool
from langchain_community.tools import DuckDuckGoSearchResults
from langchain_classic.tools import Tool
import requests
from tavily import TavilyClient
import copy
from structure_formatter import format_structure, format_structure_for_cost_estimation
import pricing_engine
from pricing_fx import live_rate_to_egp
from pricing_normalize import parse_unit

llm = reasoning_llm(reasoning_effort="low", max_completion_tokens=12000)

embeddings = embeddings_client()


cost_db = FAISS.load_local("new_vector_dbs/cost_value_faiss", embeddings, allow_dangerous_deserialization=True)
cost_retriever = cost_db.as_retriever(
    search_type="similarity",
    search_kwargs={
        "k": 10
    }
)

def return_structured_cost(activity_description, cost_driver, cost_component, cost_input):
    results = cost_retriever.invoke(f"{activity_description} {cost_driver} {cost_component} {cost_input}")
    output = ""
    for res in results:
        output += res.metadata['retrieve_text']
        output += "--------------------"
    return output



def calculator(expression: str) -> float:
    """
    Evaluate a mathematical expression safely.
    Example: "4500 * 1.15 + 300"
    Returns: float result
    """
    try:
        # Clean the expression
        expression = expression.strip()
        
        # Replace common text patterns with operators
        expression = expression.replace('×', '*').replace('÷', '/')
        
        # Remove any currency symbols or units
        expression = expression.replace('EGP', '').replace('$', '').replace(',', '')
        
        # Safety check: only allow numbers, operators, parentheses, and decimal points
        import re
        if not re.match(r'^[\d\s\+\-\*/\(\)\.\^]+$', expression):
            raise ValueError(f"Invalid characters in expression: {expression}")
        
        # Replace ^ with ** for exponentiation
        expression = expression.replace('^', '**')
        
        # Evaluate safely (restricted namespace)
        result = eval(expression, {"__builtins__": {}}, {})

        return float(result)
    except Exception as e:
        # Deliberately raises rather than returning 0.0. A calculator that answers
        # zero when it fails makes a broken formula indistinguishable from a
        # genuinely free line item, and the zero then propagates silently into the
        # total. Callers decide how to handle the failure.
        print(f"Calculator error with expression '{expression}': {str(e)}")
        raise ValueError(f"could not evaluate expression {expression!r}: {e}") from e

def tax_calculator(net_salary: float) -> float:
    """
    Calculate the tax for a given net salary.
    """
    annual_salary = net_salary * 12
    if annual_salary <= 40000:
        return (annual_salary * 0.0) / 12
    elif annual_salary <= 55000:
        return (annual_salary * 0.1) / 12
    elif annual_salary <= 70000:
        return (annual_salary * 0.15) / 12
    elif annual_salary <= 200000:
        return (annual_salary * 0.2) / 12
    else:
        return (annual_salary * 0.25) / 12

def convert_to_egp_currency(from_currency: str, amount: float) -> float:
    """Convert a monetary amount from any foreign currency to Egyptian Pounds (EGP) using a live exchange rate (with hardcoded fallbacks if the API is unavailable).

    Delegates to pricing_fx, which caches the live rate for an hour and keeps one
    fallback table. This module used to hold a second table with different numbers,
    so the same USD price converted differently depending on which path priced it.
    """
    rate, _note = live_rate_to_egp(from_currency)
    return float(amount) * rate


#: Days in an average month, matching pricing_normalize so that a rate converted
#: here and one converted inside the evidence engine agree.
MONTH_DAYS = 30.4375


def to_monthly_egp(value: float, currency: str, period: str) -> Tuple[float, str]:
    """
    Convert a figure quoted in any currency over any period into EGP per month.

    Both conversions are arithmetic, so neither belongs in a prompt. Asking a model
    for "the monthly cost in EGP" when the page quotes "100-150 USD/day" makes the
    estimate depend on whether the model happens to know today's rate — and the
    honest ones refuse, which is how a perfectly good USD day rate ended up
    reported as no evidence at all. The model now reports the number as the page
    states it, and this function does the rest.

    Returns (monthly_egp, note), the note recording both steps for the audit trail.
    """
    steps: List[str] = []
    amount = float(value)

    code = (currency or "EGP").upper().strip() or "EGP"
    rate, fx_note = live_rate_to_egp(code)
    if code not in ("EGP", ""):
        amount *= rate
        steps.append(f"{value:,.2f} {code} x {rate:.4f} = {amount:,.2f} EGP ({fx_note})")

    unit = parse_unit(period or "")
    if unit.period and unit.days:
        factor = MONTH_DAYS / unit.days
        if abs(factor - 1.0) > 1e-9:
            before = amount
            amount *= factor
            steps.append(
                f"{before:,.2f} EGP per {unit.period} x {factor:.4f} = {amount:,.2f} EGP per month"
            )
    elif unit.one_time:
        steps.append("one-time cost; carried across as-is with no period conversion")
    else:
        steps.append(f"no pricing period stated in {period!r}; assumed the figure is already monthly")

    return amount, "; ".join(steps) if steps else "no conversion needed"


def evaluate_formula_for_input(inp: dict) -> Optional[float]:
    """
    Evaluate a cost input's formula from its parameters' values.

    Returns the monthly cost in EGP, or None when the input cannot be costed. None
    is a real answer here: if any parameter the formula depends on has no estimate,
    the input has no estimate either, and substituting zero for the missing piece
    would quietly understate the total rather than reporting the gap.

    Sets 'formula_error' on the input describing what stopped the evaluation, so the
    UI can show the gap instead of a misleading number.
    """
    import re

    formula = (inp.get("formula") or "").strip()
    params = inp.get("cost_parameters") or []
    inp.pop("formula_error", None)

    def _fail(reason: str) -> None:
        inp["formula_error"] = reason
        return None

    unestimated = [
        (p.get("parameter_name") or "unnamed").strip()
        for p in params
        if p.get("monthly_cost_egp") is None
    ]

    # Without a formula, a single parameter is the cost and several are summed.
    if not formula:
        if unestimated:
            return _fail(
                "no estimate available for: " + ", ".join(unestimated)
            )
        values = [float(p["monthly_cost_egp"]) for p in params if p.get("monthly_cost_egp") is not None]
        if not values:
            return _fail("no cost parameters carry a value")
        return values[0] if len(values) == 1 else sum(values)

    # With a formula, only the parameters it actually references need values.
    referenced = set(re.findall(r"\[([^\]]+)\]", formula))
    expr = formula
    for p in params:
        name = (p.get("parameter_name") or "").strip()
        if not name or name not in referenced:
            continue
        value = p.get("monthly_cost_egp")
        if value is None:
            return _fail(f"formula needs '{name}' but it has no estimate")
        expr = expr.replace(f"[{name}]", str(float(value)))

    leftover = re.findall(r"\[([^\]]+)\]", expr)
    if leftover:
        # Previously these were replaced with 0 and the formula evaluated anyway,
        # which produced a confident number from an incomplete formula.
        return _fail(
            "formula references parameters that do not exist: " + ", ".join(sorted(set(leftover)))
        )

    try:
        return calculator(expr)
    except Exception as exc:
        return _fail(f"formula could not be evaluated: {exc}")


def _estimate_single_parameter(
    activity_description: str,
    cost_driver: str,
    cost_component: str,
    cost_input: str,
    parameter_name: str,
    structure_so_far: dict,
) -> Optional[dict]:
    """
    Estimate one cost parameter in its DECLARED PRICING UNIT, delegating to the
    evidence pipeline in pricing_engine:

        resolve spec -> plan queries -> search -> extract observations ->
        normalize -> grade matches -> verify citations -> aggregate -> gate

    The value is returned in the parameter's declared unit, not monthly — the
    cost-input formula still performs the conversion, so converting here would
    double-count.

    The engine is permitted to say it is unsure. When it is, it still returns a
    provisional figure — the median of disagreeing sources, or the closest
    comparable item's price — and 'estimate_status' / 'estimate_basis' carry the
    caveat through to the UI. monthly_cost_egp is None only when nothing at all was
    found, so "no answer" stays distinguishable from a genuine zero in the formula
    evaluator. The key keeps its legacy name for compatibility with the exporter and
    the front end.
    """
    # Declared pricing unit for this parameter, set at the cost-parameter step.
    param_unit = "EGP per month"
    for _dr in (structure_so_far or {}).get("cost_drivers", []):
        if _dr.get("cost_driver_name") != cost_driver:
            continue
        for _comp in _dr.get("cost_components", []):
            if _comp.get("cost_component_name") != cost_component:
                continue
            for _inp in _comp.get("cost_inputs", []):
                if _inp.get("cost_input_name") != cost_input:
                    continue
                for _p in _inp.get("cost_parameters") or []:
                    if (_p.get("parameter_name") or "").strip() == parameter_name:
                        param_unit = (_p.get("unit") or "EGP per month").strip()

    # Internal historical records are retrieved as a cross-check only. They are no
    # longer hidden from the estimate the way the previous prompt did — the engine
    # uses them to flag disagreement with the web evidence, which is informative in
    # both directions.
    internal_records_text = ""
    try:
        internal_records_text = return_structured_cost(
            activity_description, cost_driver, cost_component, cost_input
        )
    except Exception as exc:
        print(f"[_estimate_single_parameter] internal cost lookup failed for {parameter_name!r}: {exc}")

    try:
        estimate = pricing_engine.estimate_parameter(
            activity_description=activity_description,
            cost_driver=cost_driver,
            cost_component=cost_component,
            cost_input=cost_input,
            parameter_name=parameter_name,
            required_unit=param_unit,
            internal_records_text=internal_records_text,
        )
        fields = estimate.to_structure_fields()
        result_status = estimate.status
        result_confidence = estimate.confidence
        result_value = estimate.value_egp
    except Exception as exc:
        # A crash must not silently become a number. Report it as an abstention
        # with the reason attached.
        print(f"[_estimate_single_parameter] engine failed for {parameter_name!r}: {exc}")
        message = f"Cost estimation failed for this parameter: {exc}"
        fields = {
            "monthly_cost_egp": None,
            "cost_justification": message,
            "source_urls": [],
            "estimate_status": "insufficient_evidence",
            "estimate_basis": "none",
            "confidence": "none",
            "confidence_basis": {},
            "value_range_egp": None,
            "evidence_count": 0,
            "estimate_warnings": [message],
            "cost_from_internal_database": "Nothing found",
        }
        result_status = "insufficient_evidence"
        result_confidence = "none"
        result_value = None

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
                for p in inp.get("cost_parameters") or []:
                    if (p.get("parameter_name") or "").strip() != parameter_name:
                        continue
                    p.update(fields)
                    return {
                        "structure_so_far": updated,
                        "parameter_name": parameter_name,
                        "monthly_cost_egp": result_value,
                        "justification": fields.get("cost_justification", ""),
                        "source_urls": fields.get("source_urls", []),
                        "confidence": result_confidence,
                        "estimate_status": result_status,
                        "cost_from_internal_database": fields.get("cost_from_internal_database", "Nothing found"),
                    }
    return None


# def cost_estimation_from_historical_costs(activity_description, cost_driver, cost_component, cost_input):
#     class CostInput(BaseModel):
#         cost_input_name: str = Field(..., description="Name of the cost input")
#         cost: int = Field(..., description="Cost of the cost input")
#         currency: str = Field(..., description="Currency of the cost input")
#         justification: str = Field(..., description="Why this cost for this cost input and how you calculated it")

#     historical_cost = return_structured_cost(activity_description, cost_driver, cost_component)

#     prompt = f"""You are a cost analyst for the supply chain team at Vodafone Egypt,
#     You are given an activity description, cost driver and cost component, your role is to estimate the cost of the cost input for this activity.

#     Activity Description is:
#     {activity_description}

#     Historical Cost for similar activities are:
#     {historical_cost}

#     You should give a justification for the cost, how you calculated it.
#     Use the historical cost for similar activities to help you identify the cost inputs.
#     If the historical costs are not helpful, return 0 as the cost.
#     If there are multiple costs for the same cost input, you can calculate a weighted average of the costs based on similar activities.

#     Cost Driver is:
#     {cost_driver}

#     Cost Component is:
#     {cost_component}

#     Cost Input to estimate is:
#     {cost_input}
#     """
#     cost_llm = llm.with_structured_output(CostInput)
#     response = cost_llm.invoke(prompt)
#     return response.cost_input_name, response.cost, response.currency, response.justification

# if __name__ == "__main__":
#     print(cost_estimation_from_historical_costs("The service involves the deployment and commissioning of outdoor telecom equipment shelters, including prefabricated cabins, power backup systems, and environmental control units. The scope covers site preparation, transportation of shelters and equipment, mechanical and electrical installation, integration with existing network infrastructure, and on-site testing. The service also includes manpower, lifting equipment, safety measures, and coordination with local authorities to ensure compliance and timely activation.", "Employment", "Engineer", "Net Salary"))

tavily_client = TavilyClient(api_key=tavily_key())

# Initialize DuckDuckGo search
ddg_search = DuckDuckGoSearchResults(num_results=5, output_format="list")


def _cost_estimation_from_internet_legacy(activity_description, cost_driver, cost_component, cost_input, structure_so_far=None):
    """Legacy path: parameter-level estimation when input has cost_parameters, else SearchQueries+Tavily for input."""
    class SearchQueries(BaseModel):
        query_1: str = Field(..., description="First search query to find pricing information (in English)")
        query_1_reasoning: str = Field(..., description="Why this first search query is appropriate")
        query_2: str = Field(..., description="Second search query - Egyptian Arabic translation of the first query")
        query_2_reasoning: str = Field(..., description="Why the Egyptian Arabic translation helps find local pricing")
        query_3: str = Field(..., description="Third search query with different angle/keywords (in English)")
        query_3_reasoning: str = Field(..., description="Why this third search query is appropriate")
    
    class CostEstimation(BaseModel):
        cost_input_name: str = Field(..., description="Name of the cost input")
        # The price is reported AS THE SOURCE STATES IT, and converted afterwards by
        # to_monthly_egp(). Asking the model for an EGP monthly figure directly made
        # every foreign-currency quote depend on the model knowing today's rate;
        # a truck priced at "100-150 USD/day" came back as no estimate at all,
        # because the model correctly refused to invent an exchange rate.
        value_as_quoted: Optional[float] = Field(
            None,
            description=(
                "The price as the source states it, in the source's own currency and period, with NO "
                "conversion of any kind. If the source gives a range, use the midpoint. "
                "Return null only if no source gives a usable price at all."
            ),
        )
        quoted_currency: str = Field(
            "EGP", description="ISO code of the currency the price above is in, e.g. 'EGP', 'USD', 'EUR'"
        )
        quoted_period: str = Field(
            "per month",
            description=(
                "The period the price above covers, as the source states it: 'per hour', 'per day', "
                "'per week', 'per month', 'per year', or 'one-time'."
            ),
        )
        monthly_cost_egp: Optional[float] = Field(None, description="Leave null. Computed from the fields above; do not fill this in.")
        is_percentage: bool = Field(..., description="True if this cost input is a percentage (e.g., taxes, insurance, medical), False otherwise")
        percentage_value: Optional[float] = Field(None, description="If is_percentage is True, the percentage value (e.g., 14 for 14%)")
        sharing: int = Field(1, description="Cost sharing factor - how many entities/projects share this cost. Default is 1 (no sharing). If this cost is shared across multiple projects/sites/entities, specify the number. The final cost per project = cost / sharing.")
        justification: str = Field(..., description="How the cost was estimated based on the search results or calculated from other costs. Write in plain text. Cite sources with their name and URL. Show calculations clearly. Do NOT use markdown formatting like ** or []().")
        source_urls: List[str] = Field(default_factory=list, description="List of ALL source URLs from the search results that you used to derive this estimate (exact URLs)")
        confidence: str = Field(..., description="Confidence level: 'high', 'medium', or 'low' based on search result quality")

    # If this input has cost_parameters, estimate each parameter then compute input cost from formula
    structure_so_far = structure_so_far or {"cost_drivers": []}
    for driver in structure_so_far.get("cost_drivers", []):
        if driver.get("cost_driver_name") != cost_driver:
            continue
        for component in driver.get("cost_components", []):
            if component.get("cost_component_name") != cost_component:
                continue
            for inp in component.get("cost_inputs", []):
                if inp.get("cost_input_name") != cost_input:
                    continue
                params = inp.get("cost_parameters") or []
                if params:
                    # Estimate each cost parameter, then evaluate formula for input cost
                    updated = copy.deepcopy(structure_so_far)
                    for p in params:
                        pname = (p.get("parameter_name") or "").strip()
                        if not pname:
                            continue
                        result = _estimate_single_parameter(
                            activity_description, cost_driver, cost_component, cost_input, pname,
                            updated
                        )
                        if result and result.get("structure_so_far") is not None:
                            updated = result["structure_so_far"]
                    # Re-find the input in updated and compute its cost from the formula.
                    # A None result means at least one parameter had no defensible
                    # estimate, so the input is left uncosted and the reason is
                    # carried on 'formula_error' rather than being flattened to zero.
                    computed_input_cost = None
                    formula_error = None
                    for dr in updated.get("cost_drivers", []):
                        if dr.get("cost_driver_name") != cost_driver:
                            continue
                        for comp in dr.get("cost_components", []):
                            if comp.get("cost_component_name") != cost_component:
                                continue
                            for inp2 in comp.get("cost_inputs", []):
                                if inp2.get("cost_input_name") != cost_input:
                                    continue
                                computed_input_cost = evaluate_formula_for_input(inp2)
                                inp2["monthly_cost_egp"] = computed_input_cost
                                formula_error = inp2.get("formula_error")
                                break
                            break
                        break

                    unresolved = [
                        (p.get("parameter_name") or "unnamed")
                        for p in params
                        if p.get("monthly_cost_egp") is None
                    ]
                    if computed_input_cost is None:
                        justification = (
                            "Not costed: " + (formula_error or "no value could be derived from the cost parameters")
                        )
                    else:
                        justification = "Cost derived from cost parameters and formula."

                    return {
                        "cost_input_name": cost_input,
                        "monthly_cost_egp": computed_input_cost,
                        "is_percentage": False,
                        "percentage_value": None,
                        "sharing": inp.get("sharing", 1),
                        "justification": justification,
                        "source_urls": [],
                        "confidence": "none" if computed_input_cost is None else "medium",
                        "estimate_status": (
                            "insufficient_evidence" if computed_input_cost is None else "estimated"
                        ),
                        "unestimated_parameters": unresolved,
                        "structure_so_far": updated,
                    }
                break
        break

    # No cost_parameters or input not found: estimate cost input directly (original behavior)
    # Build the lineage context for the LLM
    lineage_context = f"""
    Activity Description: {activity_description}
    Cost Driver: {cost_driver}
    Cost Component: {cost_component}
    Cost Input: {cost_input}
    """
    
    # Scoped structure for cost estimation: current cost component only (inputs and their costs)
    costs_estimated_so_far = format_structure_for_cost_estimation(structure_so_far, cost_driver, cost_component)
    
    # Step 1: Generate 3 search queries (English, Egyptian Arabic, English)
    query_llm = llm.with_structured_output(SearchQueries)
    
    query_prompt = f"""You are an expert Procurement Search Strategist. Your goal is to find pricing data in Egypt.
        
        CONTEXT:
        - Activity: {activity_description} (Use ONLY for context, usually NOT for the search string)
        - Cost Category: {cost_component}
        - ITEM TO PRICE: {cost_input}
        
        CRITICAL RULES FOR QUERY GENERATION:
        1. **STRIP CONTEXT:** Do NOT include the "Activity Description" or "Vodafone" in the search query. Vendors do not list prices based on your internal project names.
        2. **Keep it simple:** A query like "Diesel generator 500kva price Egypt 2025" works. "Diesel generator for telecom tower maintenance activity price" FAILS.
        3. **Date Strategy:** We are in 2026. Prioritize "2026", but "2025" is acceptable for recent historical data if 2026 is scarce.
        4. **Language:** English for queries 1 & 3, Egyptian Arabic for query 2.

        YOUR TASK: Generate 3 distinct queries:

        ---
        QUERY 1 - The "Direct Item" Search (English)
        Target: The specific cost input name in English.
        Formula: [Item Name] + "price" + "Egypt" + [Year]
        Example: "Cisco 2960 switch price Egypt 2026"
        ---
        QUERY 2 - Egyptian Arabic Translation
        Target: Translate Query 1 to Egyptian Arabic for local market search.
        Formula: Translate the item name and key terms to Egyptian Arabic (العامية المصرية)
        Example: "سعر سويتش سيسكو 2960 مصر 2026" or "سعر جهاز توجيه سيسكو في مصر"
        Note: Use natural Egyptian Arabic that local suppliers and merchants would understand.
        Include terms like: سعر (price), مصر (Egypt), تكلفة (cost)
        ---
        QUERY 3 - The "Source/Catalog" Search (English)
        Target: The category or list containing the item.
        Formula: [Cost Component/Category] + "supplier price list" OR "rates pdf" + "Egypt"
        Example: "IT Network equipment price list Egypt 2026 pdf"
        ---

        Generate the 3 queries now for: {cost_input}"""
    
    try:
        queries_response = query_llm.invoke(query_prompt)
        search_queries = [queries_response.query_1, queries_response.query_2, queries_response.query_3]
        print(f"\n[Generated Search Queries]")
        print(f"Query 1 (English): {queries_response.query_1}")
        print(f"Reasoning: {queries_response.query_1_reasoning}")
        print(f"Query 2 (Egyptian Arabic): {queries_response.query_2}")
        print(f"Reasoning: {queries_response.query_2_reasoning}")
        print(f"Query 3 (English): {queries_response.query_3}")
        print(f"Reasoning: {queries_response.query_3_reasoning}")
    except Exception as e:
        print(f"\n[Error generating search queries]: {str(e)}")
        # Fallback: create simple search queries (3 queries)
        search_queries = [
            f"{cost_input} Egypt 2026 price cost",
            f"سعر {cost_input} مصر 2026",  # Arabic fallback
            f"{cost_component} price list Egypt 2026"
        ]
        print(f"Using fallback search queries: {search_queries}")
    
    # Step 2: Execute both queries and collect all results
    all_results_text = ""
    all_results_list = []
    search_engines_used = []
    
    for query_idx, search_query in enumerate(search_queries, 1):
        print(f"\n[Executing Query {query_idx}]: {search_query}")
        
        # Try Tavily first
        tavily_success = False
        print("  Trying Tavily search...")
        try:
            tavily_response = tavily_client.search(
                query=search_query,
                max_results=10,
                include_raw_content=False
            )
            
            if 'results' in tavily_response and tavily_response['results']:
                print(f"  Tavily returned {len(tavily_response['results'])} results")
                for idx, result in enumerate(tavily_response['results'][:5], 1):
                    title = result.get('title', 'No title')
                    url = result.get('url', 'No URL')
                    content = result.get('content', 'No content')
                    all_results_text += f"\n--- Query {query_idx}, Result {idx} (Tavily) ---\n"
                    all_results_text += f"Title: {title}\n"
                    all_results_text += f"URL: {url}\n"
                    all_results_text += f"Content: {content}\n"
                    all_results_list.append({
                        'query_num': query_idx,
                        'title': title,
                        'url': url,
                        'content': content,
                        'source': 'Tavily'
                    })
                tavily_success = True
                if 'Tavily' not in search_engines_used:
                    search_engines_used.append('Tavily')
        except Exception as e:
            print(f"  Tavily search failed: {str(e)}")
        
        # If Tavily failed, use DuckDuckGo as fallback
        if not tavily_success:
            print("  Trying DuckDuckGo search...")
            try:
                ddg_results = ddg_search.invoke(search_query)
                
                if ddg_results and len(ddg_results) > 0:
                    print(f"  DuckDuckGo returned {len(ddg_results)} results")
                    for idx, result in enumerate(ddg_results[:5], 1):
                        title = result.get('title', 'No title')
                        url = result.get('link', 'No URL')
                        snippet = result.get('snippet', 'No content')
                        all_results_text += f"\n--- Query {query_idx}, Result {idx} (DuckDuckGo) ---\n"
                        all_results_text += f"Title: {title}\n"
                        all_results_text += f"URL: {url}\n"
                        all_results_text += f"Content: {snippet}\n"
                        all_results_list.append({
                            'query_num': query_idx,
                            'title': title,
                            'url': url,
                            'content': snippet,
                            'source': 'DuckDuckGo'
                        })
                    if 'DuckDuckGo' not in search_engines_used:
                        search_engines_used.append('DuckDuckGo')
            except Exception as e:
                print(f"  DuckDuckGo search failed: {str(e)}")
    
    # Step 3: Feed all results directly to the cost estimation agent with tools
    if all_results_list:
        print(f"\n[Cost Estimation] Processing {len(all_results_list)} total search results...")
        
        # Define tools for the agent
        calculator_tool = Tool(
            name="calculator",
            func=calculator,
            description="""Evaluate a mathematical expression and return the numeric result. 
            CRITICAL: Use this tool for ALL calculations including:
            - Averaging ranges: (1500 + 5000) / 2
            - Division: 410247 / 12
            - Multiplication: 34000 * 0.14
            - Complex expressions: ((2000 + 2500) / 2) * 50
            
            Input parameter: 'expression' (string)
            Example: calculator(expression='4500 * 1.15 + 300')
            Returns: float number"""
        )
        
        tax_calculator_tool = Tool(
            name="tax_calculator",
            func=tax_calculator,
            description="""Calculate the monthly tax for a given net monthly salary in EGP using Egypt's tax brackets.
            
            Input parameter: 'net_salary' (float)
            Example: tax_calculator(net_salary=34000)
            Returns: monthly tax amount in EGP"""
        )
        
        convert_currency_tool = Tool(
            name="convert_to_egp_currency",
            func=convert_to_egp_currency,
            description="""Convert an amount from any foreign currency to Egyptian Pounds (EGP) using LIVE exchange rates from exchangerate-api.com.
            CRITICAL: ALWAYS use this tool for currency conversion - DO NOT estimate conversion rates yourself.
            
            Input parameters:
            - 'from_currency' (string): Currency code like 'USD', 'EUR', 'GBP', 'SAR'
            - 'amount' (float): Amount to convert
            
            Example: convert_to_egp_currency(from_currency='USD', amount=1000)
            Returns: amount in EGP using live exchange rate"""
        )
        
        tools = [calculator_tool, tax_calculator_tool, convert_currency_tool]
        
        # Bind tools to LLM
        llm_with_tools = llm.bind_tools(tools)
        
        estimation_prompt = f"""You are a cost analyst for Vodafone Egypt. Estimate the monthly cost in EGP based on web search results.
        Each activity has cost drivers, each cost driver has cost components, each cost component has cost inputs.

        Here's the lineage of the cost input to estimate:
        {lineage_context}

        Complete structure with costs estimated so far (you can reference these for calculations):
        {costs_estimated_so_far}

        Search Results from {len(search_queries)} queries using {', '.join(search_engines_used)}:
        {all_results_text}

        AVAILABLE TOOLS (YOU MUST USE THESE FOR CALCULATIONS):
        - calculator(expression): Evaluate mathematical expressions
          * REQUIRED for ANY calculation: averaging, division, multiplication, etc.
          * Example: calculator(expression="(1500 + 5000) / 2") returns 3250
          * Example: calculator(expression="410247 / 12") returns 34187.25
          * Example: calculator(expression="34000 * 0.14") returns 4760
          
        - tax_calculator(net_salary): Calculate monthly tax for a given net monthly salary in EGP
          * Example: tax_calculator(net_salary=34000) returns the tax amount
          
        - convert_to_egp_currency(from_currency, amount): Convert amount to EGP using LIVE exchange rates
          * CRITICAL: NEVER estimate conversion rates - ALWAYS use this tool
          * Example: convert_to_egp_currency(from_currency="USD", amount=2500) returns EGP amount
          * Example: convert_to_egp_currency(from_currency="EUR", amount=1000) returns EGP amount
          * Works for: USD, EUR, GBP, SAR, AED, and all major currencies

        CRITICAL INSTRUCTIONS FOR ESTIMATION:
        1. YOU MUST USE THE TOOLS - Do not estimate calculations or conversions mentally
           - For ANY arithmetic → USE calculator tool
           - For ANY currency conversion → USE convert_to_egp_currency tool
           - For salary taxes → USE tax_calculator tool
           
        2. REPORT THE PRICE AS THE SOURCE STATES IT — DO NOT CONVERT ANYTHING:
           - value_as_quoted: the number as printed on the page, no conversion at all
           - quoted_currency: the currency it is printed in ('USD', 'EGP', 'EUR', ...)
           - quoted_period: the period it covers ('per day', 'per month', 'per year', 'one-time')
           - Example: a page saying "100-150 USD/day" → value_as_quoted=125, quoted_currency='USD',
             quoted_period='per day'. That is a COMPLETE answer. Do NOT try to work out what it is
             in EGP per month, and never withhold a price because you do not know the exchange rate.
           - Currency conversion at today's live rate and period conversion to monthly are done
             for you AFTER you answer, in code. A foreign-currency price is perfectly good evidence.

        3. If the searches genuinely contain no price for this item, leave value_as_quoted null and
           explain in the justification what you searched for and what was missing. That is different
           from a price you would rather not convert — a foreign price still counts as found.

        4. If you cannot find specific Egypt 2026 data:
           - Use data from similar markets (Middle East, North Africa, developing countries)
           - If you find a RANGE (e.g., "1,500 to 5,000"), report the midpoint as value_as_quoted
           - Use comparable data and adjust for Egypt's market conditions

        5. USE THE DATA YOU FIND - Don't arbitrarily reduce values from search results

        6. If you find multiple values in the same currency and period, report their median

        7. SHARING FACTOR: If this cost is shared across multiple projects/sites/entities, set sharing > 1.
           For example, if one engineer works on 3 sites simultaneously, sharing = 3.
           Default is 1 (no sharing).

        Your task:
        
        STEP 1: Determine if this is PERCENTAGE-BASED or ABSOLUTE cost
        
        IF PERCENTAGE-BASED (like taxes, insurance, social insurance, medical):
        - Set is_percentage = True
        - Extract the percentage value from search results (e.g., 14 for 14%)
        - If you can find a base cost in "Costs estimated so far" (like Net Salary), use calculator to
          compute the resulting amount and report it as value_as_quoted with quoted_currency='EGP'
          and quoted_period='per month'
        - Example: If Salary Taxes is 14% and Net Salary is EGP 34,000, use calculator("34000 * 0.14") = 4,760

        IF ABSOLUTE COST (like salary, fee, rent):
        - Set is_percentage = False
        - Report the price exactly as the source prints it, with its currency and period
        - USE THE ACTUAL VALUES FROM SEARCH RESULTS

        STEP 2: Report and justify
        - "EGP 410,247 annually" → value_as_quoted=410247, quoted_currency='EGP', quoted_period='per year'
        - "100 to 150 USD per day" → value_as_quoted=125, quoted_currency='USD', quoted_period='per day'
        - Several comparable sources → report their median, in one currency and one period
        - DO NOT arbitrarily reduce the values you found
        
        STEP 3: Provide clear justification with proper source citation (PLAIN TEXT FORMAT)
        - WRITE IN PLAIN TEXT. Do NOT use markdown syntax (no **, no [](), no bullet markers).
        - Use simple line breaks and clear sentences.
        - Example format:
            "Source: According to Source Name (https://example.com), the cost is X EGP.
            
            Calculation: Daily cost is Y EGP. Monthly cost = Y × 30 = X EGP.
            
            Reasoning: Average of range from multiple sources."
        
        - CITE YOUR SOURCES PROPERLY:
          * If from internet source: "According to Source Name (URL), the cost is [value]."
          * Example: "According to Module X Solutions (https://modulexsolutions.com/...), the shelter cost ranges from EGP 10,000 to 15,000."
          * If multiple sources: cite each one clearly.
          * If using your knowledge: "According to market analysis for Egypt, the estimated cost is..."
        
        - Show your calculation clearly:
          * "Range: EGP 1,500 to 5,000"
          * "Average: (1500 + 5000) / 2 = EGP 3,250 monthly"
        
        - IMPORTANT: Set source_urls to a list of ALL URLs from the search results that you used (exact URLs from the Result blocks)
        
        REMEMBER:
        - Report the price AS QUOTED, with its own currency and period. Conversion happens in code.
        - ALWAYS provide an estimate - use ranges, similar markets, or reasonable assumptions
        - Use the actual values from search results
        - ALWAYS cite sources in your justification

        Now analyze the search results and report the price found for: {cost_input}"""
        
        try:
            # Use agent executor pattern for tool usage
            messages = [HumanMessage(content=estimation_prompt)]
            max_iterations = 10
            
            for iteration in range(max_iterations):
                response = llm_with_tools.invoke(messages)
                messages.append(response)
                
                # Check if there are tool calls
                if not response.tool_calls:
                    # No more tool calls, extract the final response
                    break
                
                # Execute tool calls
                for tool_call in response.tool_calls:
                    tool_name = tool_call['name']
                    tool_args = tool_call['args']
                    
                    # Find and execute the tool
                    tool_result = None
                    for tool in tools:
                        if tool.name == tool_name:
                            try:
                                if tool_name == "calculator":
                                    tool_result = tool.func(tool_args.get('expression', ''))
                                elif tool_name == "tax_calculator":
                                    tool_result = tool.func(tool_args.get('net_salary', 0))
                                elif tool_name == "convert_to_egp_currency":
                                    tool_result = tool.func(
                                        tool_args.get('from_currency', 'USD'),
                                        tool_args.get('amount', 0)
                                    )
                                print(f"  Tool used: {tool_name}({tool_args}) = {tool_result}")
                            except Exception as e:
                                tool_result = f"Error: {str(e)}"
                                print(f"  Tool error: {tool_name} failed: {str(e)}")
                            break
                    
                    # Add tool result to messages
                    messages.append(ToolMessage(
                        content=str(tool_result),
                        tool_call_id=tool_call['id']
                    ))
            
            # Now get structured output from the final response
            final_prompt = f"""Based on the analysis and calculations, provide the final cost estimation in the required format.

            {estimation_prompt}
            
            Previous analysis and tool usage results:
            {messages[-1].content if hasattr(messages[-1], 'content') else 'See tool results above'}
            
            Provide the final structured estimation."""
            
            estimation_llm = llm.with_structured_output(CostEstimation)
            estimation_response = estimation_llm.invoke(final_prompt)

            # Convert what the model reported into EGP per month. This replaces the
            # old reflection pass, which existed to catch period and currency
            # mistakes by asking a second model to re-check the first one's mental
            # arithmetic. Doing the arithmetic here instead removes the mistakes
            # rather than auditing them, and costs one fewer LLM call.
            if estimation_response.value_as_quoted is not None:
                monthly, conversion_note = to_monthly_egp(
                    estimation_response.value_as_quoted,
                    estimation_response.quoted_currency,
                    estimation_response.quoted_period,
                )
                estimation_response.monthly_cost_egp = monthly
                estimation_response.justification += (
                    f"\n\nConversion to EGP per month: {conversion_note}."
                )
                print(f"  Converted {estimation_response.value_as_quoted} "
                      f"{estimation_response.quoted_currency} {estimation_response.quoted_period} "
                      f"-> {monthly:,.0f} EGP/month")
            else:
                estimation_response.monthly_cost_egp = None
                print(f"  No price reported for {cost_input}")

        except Exception as e:
            print(f"\nError during cost estimation: {str(e)}")
            print(f"Error type: {type(e).__name__}")
            
            # Return fallback response
            return {
            'cost_input_name': cost_input,
            'monthly_cost_egp': 0.0,
            'is_percentage': False,
            'percentage_value': None,
            'sharing': 1,
            'justification': f"Cost estimation failed due to an error: {type(e).__name__}. Search queries attempted: {', '.join(search_queries)}",
            'source_urls': [],
            'confidence': 'low',
            'search_queries_used': search_queries,
            'search_engine_used': ', '.join(search_engines_used) if search_engines_used else 'None',
            'structure_so_far': structure_so_far
        }
        
        # Update structure_so_far with the new cost
        updated_structure = copy.deepcopy(structure_so_far) if structure_so_far else {"cost_drivers": []}
        
        # Find and update the cost input in the structure
        for driver in updated_structure.get('cost_drivers', []):
            if driver.get('cost_driver_name') == cost_driver:
                for component in driver.get('cost_components', []):
                    if component.get('cost_component_name') == cost_component:
                        for cost_input_item in component.get('cost_inputs', []):
                            if cost_input_item.get('cost_input_name') == cost_input:
                                cost_input_item['monthly_cost_egp'] = estimation_response.monthly_cost_egp
                                cost_input_item['is_percentage'] = estimation_response.is_percentage
                                cost_input_item['percentage_value'] = estimation_response.percentage_value
                                cost_input_item['sharing'] = estimation_response.sharing
                                cost_input_item['cost_justification'] = estimation_response.justification
                                _urls = getattr(estimation_response, 'source_urls', None) or []
                                if not _urls and getattr(estimation_response, 'source_url', None):
                                    _urls = [estimation_response.source_url]
                                cost_input_item['source_urls'] = _urls if isinstance(_urls, list) else [_urls]
                                cost_input_item['confidence'] = estimation_response.confidence
                                break
        
        _urls = getattr(estimation_response, 'source_urls', None) or []
        if not _urls and getattr(estimation_response, 'source_url', None):
            _urls = [estimation_response.source_url]
        if not isinstance(_urls, list):
            _urls = [_urls]
        return {
            'cost_input_name': estimation_response.cost_input_name,
            'monthly_cost_egp': estimation_response.monthly_cost_egp,
            'is_percentage': estimation_response.is_percentage,
            'percentage_value': estimation_response.percentage_value,
            'sharing': estimation_response.sharing,
            'justification': estimation_response.justification,
            'source_urls': _urls,
            'confidence': estimation_response.confidence,
            'search_queries_used': search_queries,
            'search_engine_used': ', '.join(search_engines_used) if search_engines_used else 'None',
            'structure_so_far': updated_structure
        }
    else:
        # No results found from any query
        return {
            'cost_input_name': cost_input,
            'monthly_cost_egp': 0.0,
            'is_percentage': False,
            'percentage_value': None,
            'sharing': 1,
            'justification': f"Unable to find pricing information from 3 search queries (English, Egyptian Arabic, English) with Tavily and DuckDuckGo. Search queries used: {', '.join(search_queries)}",
            'source_urls': [],
            'confidence': 'low',
            'search_queries_used': search_queries,
            'search_engine_used': 'None',
            'structure_so_far': structure_so_far
        }

from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent
from langgraph.errors import GraphRecursionError
from tavily import TavilyClient
import os, json

tavily_client = TavilyClient(api_key=tavily_key())

@tool
def tavily_search(query: str) -> str:
    """
    Search the web for cost-related information.
    Use specific, targeted queries to find prices, rates, and benchmarks.
    """
    response = tavily_client.search(
        query=query,
        max_results=10,
        include_raw_content=False,
        search_depth="advanced",
        include_answer=True,
    )

    parts = []
    if response.get("answer"):
        parts.append(f"DIRECT ANSWER: {response['answer']}")

    for i, r in enumerate(response.get("results", []), 1):
        parts.append(
            f"\nResult {i}: {r.get('title', '')}\n"
            f"URL: {r.get('url', '')}\n"
            f"Content: {r.get('content', '')[:400]}"
        )

    return "\n".join(parts) if parts else "No results found."

@tool
def convert_to_egp_currency_tool(from_currency: str, amount: float) -> float:
    """
    Convert a currency amount to Egyptian Pounds (EGP).
    """
    return convert_to_egp_currency(from_currency, amount)


@tool
def calculate(expression: str) -> str:
    """
    Evaluate a mathematical expression. Use this for unit conversions,
    percentage calculations, currency conversions, or any arithmetic.
    
    Examples:
    - "45000 * 0.15"          → salary tax at 15%
    - "50000 * 1.14"          → add 14% VAT
    - "350 * 160"             → hourly rate * hours per month
    - "12000 / 0.78"          → net to gross conversion
    """
    try:
        result = eval(expression, {"__builtins__": {}}, {k: v for k, v in vars(__import__('math')).items() if not k.startswith('_')})
        return f"Result: {result}"
    except Exception as e:
        return f"Calculation error: {e}"


class FallbackQuery(BaseModel):
    query: str = Field(..., description="Single best web search query in English to price this cost input in Egypt")


class SimpleCostEstimation(BaseModel):
    # Reported as the source states it and converted afterwards by to_monthly_egp().
    # This path has no tools at all, so asking it for EGP per month meant asking a
    # model with no exchange rate to produce one — it either refused or guessed.
    value_as_quoted: Optional[float] = Field(
        None,
        description=(
            "The price exactly as the evidence states it, in its own currency and period, with NO "
            "conversion. Midpoint if a range is given. Null only if the evidence contains no price."
        ),
    )
    quoted_currency: str = Field("EGP", description="ISO code of the currency above, e.g. 'EGP', 'USD'")
    quoted_period: str = Field(
        "per month",
        description="Period the price covers: 'per hour', 'per day', 'per week', 'per month', 'per year' or 'one-time'",
    )
    is_percentage: bool = Field(..., description="True if this cost is a percentage of another base cost")
    percentage_value: Optional[float] = Field(
        None, description="If is_percentage is True, the percentage value (e.g., 14 for 14%)"
    )
    sharing: int = Field(
        1,
        description="Cost sharing factor - how many projects/entities share this cost. 1 means no sharing.",
    )
    confidence: str = Field(..., description="Confidence level: 'low', 'medium', or 'high'")
    reasoning: str = Field(..., description="Plain-text explanation of how the cost was derived from the data")


class PercentageEstimation(BaseModel):
    percentage_value: float = Field(..., description="Percentage value between 0 and 100")
    confidence: str = Field(..., description="Confidence level: 'low', 'medium', or 'high'")
    reasoning: str = Field(..., description="Plain-text explanation of how the percentage was inferred from the data")


def _find_base_cost_for_percentage(
    structure_so_far: Optional[dict],
    cost_driver: str,
    cost_component: str,
):
    """Find a likely base monthly cost (e.g. Net Salary) under the same driver/component."""
    if not structure_so_far or not structure_so_far.get("cost_drivers"):
        return None, None

    for driver in structure_so_far.get("cost_drivers", []):
        if driver.get("cost_driver_name") != cost_driver:
            continue
        for comp in driver.get("cost_components", []):
            if comp.get("cost_component_name") != cost_component:
                continue
            for ci in comp.get("cost_inputs", []):
                name = (ci.get("cost_input_name") or "").lower()
                val = ci.get("monthly_cost_egp") or 0
                # Prefer explicit "Net Salary"; otherwise, generic "salary" that isn't itself a tax/insurance
                if val and (
                    "net salary" in name
                    or ("salary" in name and "tax" not in name and "taxes" not in name and "insurance" not in name)
                ):
                    try:
                        return ci, float(val)
                    except (TypeError, ValueError):
                        continue

    return None, None


def _fallback_cost_estimate_with_tavily(
    activity_description: str,
    cost_driver: str,
    cost_component: str,
    cost_input: str,
    structure_so_far: Optional[dict] = None,
) -> dict:
    """
    Simpler, non-REACT fallback that:
    1) Asks the LLM for a single best search query
    2) Calls Tavily with include_answer=True
    3) Asks the LLM (structured) to turn that into a monthly EGP estimate.
    """
    # ---- Step 1: get a focused search query ---------------------------------
    query_text = f"{cost_input} price Egypt 2026"
    try:
        query_llm = llm.with_structured_output(FallbackQuery)
        q_prompt = f"""
You are a procurement search strategist for Vodafone Egypt.

Activity description (for context only):
{activity_description}

We need to estimate the monthly cost in EGP for this cost input in Egypt:
- Cost driver: {cost_driver}
- Cost component: {cost_component}
- Cost input: {cost_input}

Your task: propose ONE concise English web search query that is most likely
to return relevant pricing information for this cost input in Egypt
around 2025–2026. Do NOT include 'Vodafone' or internal project wording.
Focus on vendor terms like 'price', 'salary', 'rate', etc.
"""
        q_resp = query_llm.invoke(q_prompt)
        if q_resp and getattr(q_resp, "query", None):
            query_text = q_resp.query.strip()
    except Exception:
        # Keep the simple default query_text
        pass

    # ---- Step 2: Tavily search with include_answer --------------------------
    answer = ""
    source_urls: List[str] = []
    first_snippet = ""
    try:
        tv_resp = tavily_client.search(
            query=query_text,
            max_results=10,
            include_raw_content=False,
            search_depth="advanced",
            include_answer=True,
        )
        answer = tv_resp.get("answer") or ""
        results = tv_resp.get("results") or []
        for r in results:
            u = r.get("url")
            if u and u not in source_urls:
                source_urls.append(u)
        if results:
            first_snippet = (results[0].get("content") or "")[:500]
    except Exception as e:
        answer = ""
        first_snippet = f"Tavily search failed: {e}"

    if not answer and not first_snippet:
        # Nothing useful came back from Tavily
        return {
            "cost_input_name": cost_input,
            "monthly_cost_egp": 0.0,
            "is_percentage": False,
            "percentage_value": None,
            "sharing": 1,
            "justification": (
                "Fallback Tavily search returned no useful pricing information for this cost input. "
                f"Query used: '{query_text}'."
            ),
            "source_urls": [],
            "confidence": "low",
            "structure_so_far": structure_so_far,
        }

    search_context = f"Search query used: {query_text}\n\n"
    if answer:
        search_context += f"DIRECT ANSWER FROM TAVILY:\n{answer}\n\n"
    if first_snippet:
        search_context += f"TOP RESULT SNIPPET:\n{first_snippet}\n\n"

    # ---- Step 3: turn Tavily data into a numeric estimate -------------------
    try:
        est_llm = llm.with_structured_output(SimpleCostEstimation)
        est_prompt = f"""
You are a Design-to-Cost estimation expert for Vodafone Egypt.

We need a monthly cost in EGP for this specific cost input:
- Cost driver: {cost_driver}
- Cost component: {cost_component}
- Cost input: {cost_input}
- Country: Egypt

You are given pricing evidence from a Tavily web search:
{search_context}

INSTRUCTIONS:
- Report the price EXACTLY as the evidence states it: value_as_quoted in its own currency
  (quoted_currency) over its own period (quoted_period). Do NOT convert anything.
  A price in USD, EUR or per-day is a perfectly good answer — currency conversion at today's
  live rate and the conversion to a monthly figure are done for you afterwards, in code.
  Never withhold a price because you do not know the exchange rate.
- If a range is given, report its midpoint.
- If the cost is clearly a percentage of a base cost (e.g. tax, insurance), set is_percentage=True and
  percentage_value accordingly, otherwise keep is_percentage=False.
- If there is no hint of sharing, keep sharing=1.
- Only report a value the evidence above actually contains. If it contains no price at all,
  leave value_as_quoted null and say so plainly in the reasoning — the caller treats that as
  an abstention, not as a cost of zero.
"""
        est = est_llm.invoke(est_prompt)
    except Exception as e:
        return {
            "cost_input_name": cost_input,
            "monthly_cost_egp": 0.0,
            "is_percentage": False,
            "percentage_value": None,
            "sharing": 1,
            "justification": (
                "Fallback estimation failed while interpreting Tavily results. "
                f"Error: {e}. Query used: '{query_text}'."
            ),
            "source_urls": list(source_urls),
            "confidence": "low",
            "structure_so_far": structure_so_far,
        }

    justification = est.reasoning.strip()
    if est.value_as_quoted is not None and float(est.value_as_quoted) > 0:
        monthly_cost, conversion_note = to_monthly_egp(
            est.value_as_quoted, est.quoted_currency, est.quoted_period
        )
        justification += f"\n\nConversion to EGP per month: {conversion_note}."
    else:
        # None, not 0.0: the evidence held no price, which is not the same as the
        # thing being free, and a zero here would be summed into the total as one.
        monthly_cost = None

    result_payload = {
        "cost_input_name": cost_input,
        "monthly_cost_egp": monthly_cost,
        "is_percentage": bool(est.is_percentage),
        "percentage_value": est.percentage_value,
        "sharing": est.sharing or 1,
        "justification": justification,
        "source_urls": list(source_urls),
        "confidence": est.confidence or "low",
        "estimate_status": "estimated" if monthly_cost is not None else "insufficient_evidence",
    }

    # Update structure_so_far in the same way as the main path
    updated_structure = copy.deepcopy(structure_so_far) if structure_so_far else {"cost_drivers": []}
    for driver in updated_structure.get("cost_drivers", []):
        if driver.get("cost_driver_name") != cost_driver:
            continue
        for component in driver.get("cost_components", []):
            if component.get("cost_component_name") != cost_component:
                continue
            for ci in component.get("cost_inputs", []):
                if ci.get("cost_input_name") != cost_input:
                    continue
                ci["monthly_cost_egp"] = result_payload["monthly_cost_egp"]
                ci["is_percentage"] = result_payload["is_percentage"]
                ci["percentage_value"] = result_payload["percentage_value"]
                ci["sharing"] = result_payload["sharing"]
                ci["cost_justification"] = result_payload["justification"]
                ci["source_urls"] = result_payload["source_urls"]
                ci["confidence"] = result_payload["confidence"]
                ci["estimate_status"] = result_payload["estimate_status"]
                break

    return {**result_payload, "structure_so_far": updated_structure}


def cost_estimation_from_internet(activity_description, cost_driver, cost_component, cost_input, structure_so_far=None, prompt_log_path=None, prompt_log_section=None):
    """
    Estimate cost: when the cost input has cost_parameters, use legacy parameter-level
    estimation (each parameter gets search+LLM, then formula). Otherwise use REACT + Tavily fallback.
    Returns a dict with at least: cost_input_name, monthly_cost_egp, justification, source_urls, structure_so_far.
    """
    structure_so_far = structure_so_far or {"cost_drivers": []}
    for driver in structure_so_far.get("cost_drivers", []):
        if driver.get("cost_driver_name") != cost_driver:
            continue
        for comp in driver.get("cost_components", []):
            if comp.get("cost_component_name") != cost_component:
                continue
            for inp in comp.get("cost_inputs", []):
                if inp.get("cost_input_name") != cost_input:
                    continue
                if inp.get("cost_parameters"):
                    return _cost_estimation_from_internet_legacy(
                        activity_description, cost_driver, cost_component, cost_input, structure_so_far
                    )
                break
            break
        break

    agent = create_react_agent(llm, tools=[tavily_search, convert_to_egp_currency_tool, calculate])

    # Build a small context string showing sibling inputs under the same driver/component
    structure = ""
    if structure_so_far and structure_so_far.get("cost_drivers"):
        target_driver = next(
            (d for d in structure_so_far["cost_drivers"] if d.get("cost_driver_name") == cost_driver),
            None,
        )
        if target_driver:
            target_component = next(
                (c for c in target_driver.get("cost_components", []) if c.get("cost_component_name") == cost_component),
                None,
            )
            if target_component:
                for i in target_component.get("cost_inputs", []):
                    structure += f'cost input name: {i.get("cost_input_name", "")}\n'
                    monthly = i.get("monthly_cost_egp")
                    if not monthly:  # catches None and 0
                        structure += "Cost not estimated yet\n"
                    else:
                        structure += f"estimated monthly cost egp: {monthly} EGP/month\n"

    
    user_message = (
        f"Please estimate the following cost input:\n\n"
        f"- Activity: {activity_description}\n"
        f"- Cost Driver: {cost_driver}\n"
        f"- Cost Component: {cost_component}\n"
        f"- Cost Input: {cost_input}\n"
        f"- Country/Region: Egypt\n\n"
        "Search iteratively until confident, then return ONLY the JSON estimate."
    )

    SYSTEM_PROMPT = f"""You are a Design-to-Cost estimation expert. Your job is to estimate
        the monetary cost of a specific **cost input** within a project cost structure.

        Cost hierarchy: Activity → Cost Driver → Cost Component → Cost Input

        Structure of other cost inputs with the same cost driver/cost component:
        {structure}

        You have access to the following tools:
        - tavily_search: Search the web for current market prices, salaries, and rates.
        - calculate: Evaluate any mathematical expression (percentages, multiplications, etc.).
        - convert_to_egp_currency: Convert a value from any currency to EGP.

        Your iterative workflow:
        1. Analyze the cost input. If any already-estimated cost inputs are relevant to its calculation, use them directly instead of searching (e.g. Salary Taxes should be calculated from the already-estimated Net Salary, not searched independently).
        2. Use tavily_search to find any missing data you still need (rates, prices, benchmarks).
        3. Evaluate results — refine and search again if needed (up to 4 rounds).
        4. Use calculate for any arithmetic the evidence itself requires.
        5. When confident, output ONLY the JSON estimate.

        REPORT THE PRICE AS YOU FOUND IT. Put the number in "estimated_value" in the
        currency and period the source actually uses, and name them in "currency" and
        "period". Currency conversion at today's live rate and conversion to a monthly
        figure are applied to your answer afterwards, in code — so a price in USD per day
        is a complete answer. NEVER refuse to report a price because you do not know the
        exchange rate, and never convert it yourself.

        {{
        "cost_input": "name of the cost input",
        "estimated_value": "number, exactly as the source states it",
        "currency": "ISO code of that number, e.g. EGP or USD",
        "period": "per hour | per day | per week | per month | per year | one-time",
        "confidence": "low|medium|high",
        "source_summary": "brief note on data sources",
        "source_urls": ["url1", "url2", ...],
        "reasoning": "how you derived the estimate"
        }}
        """

    if prompt_log_path and prompt_log_section:
        combined = f"System:\n{SYSTEM_PROMPT}\n\nUser:\n{user_message}"
        with open(prompt_log_path, "a", encoding="utf-8") as f:
            f.write(f"{prompt_log_section}\n[Prompt]\n{combined}\n---\n")

    # Limit REACT depth to keep cost bounded. The agent can still choose to stop
    # earlier when it is confident. On recursion limit, fall back to simpler Tavily flow.
    try:
        result = agent.invoke(
            {"messages": [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=user_message)]},
            config={"recursion_limit": 15},
        )
        final_text = result["messages"][-1].content
    except GraphRecursionError:
        return _fallback_cost_estimate_with_tavily(
            activity_description, cost_driver, cost_component, cost_input, structure_so_far
        )

    parsed = None
    try:
        start = final_text.find("{")
        end = final_text.rfind("}") + 1
        if start != -1 and end > start:
            parsed = json.loads(final_text[start:end])
    except json.JSONDecodeError:
        parsed = None

    # If we successfully parsed a JSON estimate, normalise it to the legacy shape
    if parsed:
        # Safely coerce the numeric value
        raw_value = parsed.get("estimated_value")
        try:
            quoted_value = float(str(raw_value).replace(",", "").strip())
        except (TypeError, ValueError, AttributeError):
            quoted_value = 0.0

        # If REACT produced a non-positive value, treat it as failure and fall back
        if quoted_value <= 0:
            return _fallback_cost_estimate_with_tavily(
                activity_description, cost_driver, cost_component, cost_input, structure_so_far
            )

        # The agent reports the price in the source's own currency and period; the
        # conversion is arithmetic and is done here rather than trusted to the model.
        monthly_cost, conversion_note = to_monthly_egp(
            quoted_value,
            parsed.get("currency") or "EGP",
            parsed.get("period") or parsed.get("unit") or "per month",
        )

        _urls = parsed.get("source_urls")
        if not _urls and parsed.get("source_url"):
            _urls = [parsed["source_url"]]
        if not isinstance(_urls, list):
            _urls = [_urls] if _urls else []
        result_payload = {
            "cost_input_name": parsed.get("cost_input", cost_input),
            "monthly_cost_egp": monthly_cost,
            # For now, treat all REACT estimates as absolute (non‑percentage) costs
            "is_percentage": False,
            "percentage_value": None,
            "sharing": 1,
            "justification": (parsed.get("reasoning") or "").strip()
            + ("\n\nSource summary: " + parsed.get("source_summary", "").strip()
               if parsed.get("source_summary") else "")
            + f"\n\nConversion to EGP per month: {conversion_note}.",
            "source_urls": _urls,
            "confidence": parsed.get("confidence", "unknown"),
        }

        # Mirror the legacy behaviour: update structure_so_far in-place (via a copy)
        updated_structure = copy.deepcopy(structure_so_far) if structure_so_far else {"cost_drivers": []}
        for driver in updated_structure.get("cost_drivers", []):
            if driver.get("cost_driver_name") != cost_driver:
                continue
            for component in driver.get("cost_components", []):
                if component.get("cost_component_name") != cost_component:
                    continue
                for ci in component.get("cost_inputs", []):
                    if ci.get("cost_input_name") != cost_input:
                        continue
                    ci["monthly_cost_egp"] = result_payload["monthly_cost_egp"]
                    ci["is_percentage"] = result_payload["is_percentage"]
                    ci["percentage_value"] = result_payload["percentage_value"]
                    ci["sharing"] = result_payload["sharing"]
                    ci["cost_justification"] = result_payload["justification"]
                    ci["source_urls"] = result_payload["source_urls"]
                    ci["confidence"] = result_payload["confidence"]
                    break

        return {**result_payload, "structure_so_far": updated_structure}

    # If REACT final message was unparseable JSON, fall back to the simpler Tavily flow
    return _fallback_cost_estimate_with_tavily(
        activity_description, cost_driver, cost_component, cost_input, structure_so_far
    )