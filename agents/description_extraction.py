from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field
from env_config import chat_llm


# The same chat deployment the other agents use; see agents/env_config.py
llm = chat_llm(max_tokens=2000)


# ---------------------------------------------------------------------------
# Activity-type suggestion table
# Each key is a canonical activity type; each value maps point_id -> suggestion.
# ---------------------------------------------------------------------------
ACTIVITY_SUGGESTIONS: Dict[str, Dict[str, str]] = {
    "Poc 2/Poc 3 Installation": {
        "phases": "Hardware installation → Integration → Acceptance",
        "project_type": "Service",
        "final_result": "Fully functioning Node installed & integrated to network",
        "cost_contributors": "Duration of this activity in days",
        "people_required": "Yes – engineers and technicians",
        "preferred_tiering": "Inside Cairo / Outside Cairo",
        "transportation": "Yes",
        "overnight_stay": "Yes – in case activity is done outside Cairo",
        "tools_equipment_assets": "Laptop, safety tools, technical tools",
        "materials_consumables": "No",
        "turnkey_pricing": "Per activity",
    },
    "Drive Test": {
        "phases": "Hardware installation → Integration → Acceptance",
        "project_type": "Service",
        "final_result": "Report for certain KPIs for site",
        "cost_contributors": "Distance covered per day & location of the sites",
        "people_required": "Yes – engineers",
        "preferred_tiering": "Regional",
        "transportation": "Yes",
        "overnight_stay": "Yes – in case activity is done outside Cairo",
        "tools_equipment_assets": "Laptop, license",
        "materials_consumables": "No",
        "turnkey_pricing": "Per activity",
    },
    "Telecom Services": {
        "phases": "Hardware installation → Integration → Acceptance",
        "project_type": "Service",
        "final_result": "Mobile site fully installed, accepted and carrying traffic properly",
        "cost_contributors": "Number of days to finish installation",
        "people_required": "Yes – engineers, technicians",
        "preferred_tiering": "Regional",
        "transportation": "Yes",
        "overnight_stay": "Yes – in case activity is done outside Cairo",
        "tools_equipment_assets": "Laptop, safety tools, technical tools",
        "materials_consumables": "Cables, connectors",
        "turnkey_pricing": "Per activity",
    },
    "Civil Services": {
        "phases": "Site acquisition → Site preparation → Foundation → Tower erection",
        "project_type": "Service",
        "final_result": "Mobile site civil part delivered & accepted and ready for telecom installation",
        "cost_contributors": "Type of site (Rooftop / Greenfield), structure type, building region",
        "people_required": "Yes – engineers, labour, technicians",
        "preferred_tiering": (
            "Commercial mode (lump-sum / itemised) and regional distribution: "
            "0–100 km / 100–400 km / 400–800 km / over 800 km"
        ),
        "transportation": "Yes",
        "overnight_stay": "Yes – in case distance is more than 100 km",
        "tools_equipment_assets": "Trucks, telescopic cranes, rooftop cranes, safety tools",
        "materials_consumables": "Concrete, steel, sand, bricks, cement, gravel, painting, isolation, black steel, galvanisation",
        "turnkey_pricing": "Lump sum",
    },
    "Fleet & Buses": {
        "phases": "No phases",
        "project_type": "Service",
        "final_result": "Completed trips with agreed KPIs",
        "cost_contributors": "Number of trips",
        "people_required": "Yes – drivers",
        "preferred_tiering": "Regional",
        "transportation": "Yes",
        "overnight_stay": "No",
        "tools_equipment_assets": "Buses, normal cars",
        "materials_consumables": "Fuel",
        "turnkey_pricing": "Per activity",
    },
    "Software Development": {
        "phases": "Design → Development → Testing",
        "project_type": "Software",
        "final_result": "Delivered software working properly with agreed KPIs",
        "cost_contributors": "Features required in delivery, complexity, customisation",
        "people_required": "Yes – front-end developer, back-end developer, testers, project manager",
        "preferred_tiering": "Tailored to order / On-shelf",
        "transportation": "No",
        "overnight_stay": "No",
        "tools_equipment_assets": "Development, deployment, and testing tools",
        "materials_consumables": "No",
        "turnkey_pricing": "Per activity",
    },
    "Learning & Development": {
        "phases": "Training → Certification Exam",
        "project_type": "Service",
        "final_result": "Trained / certified participants",
        "cost_contributors": "Number of participants, instructor level, course level",
        "people_required": "Yes – instructors",
        "preferred_tiering": "Certificate type",
        "transportation": "No",
        "overnight_stay": "No",
        "tools_equipment_assets": "Training equipment, training material",
        "materials_consumables": "Printed material",
        "turnkey_pricing": "Per activity",
    },
    "Booth Activation": {
        "phases": "Design → Fabrication → Installation",
        "project_type": "Service",
        "final_result": "Installed booth matching design & specs",
        "cost_contributors": "Booth size, number of events",
        "people_required": "Yes – designers, installers",
        "preferred_tiering": "Size (Small / Medium / Big)",
        "transportation": "Yes",
        "overnight_stay": "Yes",
        "tools_equipment_assets": "Fabrication tools",
        "materials_consumables": "Wood, metal, graphics",
        "turnkey_pricing": "Per activity",
    },
    "Media Production": {
        "phases": "Pre-production → Shoot → Edit",
        "project_type": "Service",
        "final_result": "Delivered media content",
        "cost_contributors": "Number of shooting days",
        "people_required": "Yes – crew, director",
        "preferred_tiering": "Size (Mega / Medium / Small)",
        "transportation": "Yes",
        "overnight_stay": "Yes",
        "tools_equipment_assets": "Cameras, lighting",
        "materials_consumables": "Props, consumables",
        "turnkey_pricing": "Per activity",
    },
}

# Ordered list of canonical activity-type labels (used for display/matching)
ACTIVITY_TYPES: List[str] = list(ACTIVITY_SUGGESTIONS.keys())

# Points that may stay unanswered without blocking description generation.
# crew_structure / shared_resources are asked once but "to be derived" is a valid answer.
OPTIONAL_POINT_IDS = {"phases"}


REQUIRED_POINTS: List[Dict[str, str]] = [
    {
        "id": "activity_overview",
        "label": "Activity overview",
        "description": "The user's initial description of the activity. Used as context; no separate question.",
        "example": "Roll out 50 new LTE sites in Cairo and Giza.",
    },
    {
        "id": "activity_type",
        "label": "Activity type",
        "description": (
            "The category / type of the activity. Must be one of: "
            + ", ".join(ACTIVITY_TYPES)
            + ". This is used to pre-fill suggestions for the remaining questions."
        ),
        "example": "Telecom Services",
    },
    {
        "id": "project_duration",
        "label": "Project duration",
        "description": (
            "The total duration of the project (with units, e.g. months, weeks). "
            "This is used later to compute recurring costs inside formulas."
        ),
        "example": "3 months (January to March 2026).",
    },
    {
        "id": "volume_driver",
        "label": "Volume driver & size",
        "description": (
            "The main volume driver of the activity and its NUMERIC size (e.g. number of sites, km of fiber, "
            "number of trips, number of participants). Always capture the number — it anchors all downstream "
            "quantity estimation."
        ),
        "example": "40 sites in Greater Cairo.",
    },
    {
        "id": "crew_structure",
        "label": "Crews / parallel teams",
        "description": (
            "How many parallel crews/teams will execute the work, and their composition (roles and counts per crew). "
            "If the user doesn't know, record 'to be derived by the planner' — that is a valid answer; never re-ask."
        ),
        "example": "3 crews, each with 1 senior RF engineer, 2 technicians and 1 rigger.",
    },
    {
        "id": "shared_resources",
        "label": "Shared resource pools",
        "description": (
            "Resources shared across the whole activity rather than needed per unit of volume — vehicles, cranes, "
            "test kits — WITH their counts. This prevents wrong per-unit scaling (e.g. one truck per site). "
            "If the user doesn't know the counts, record 'to be derived by the planner' — valid answer; never re-ask."
        ),
        "example": "3 light trucks and 2 crew vans serve all 40 sites; one crane shared by all crews.",
    },
    {
        "id": "phases",
        "label": "Phases (optional)",
        "description": "Whether the service has multiple phases; if yes, list them. Optional—user may say no or skip.",
        "example": "Design → Site survey → Installation → Integration → Testing → Handover.",
    },
    {
        "id": "project_type",
        "label": "Project type",
        "description": "Type of project: Hardware, Software, or Service.",
        "example": "Hardware and service (site rollout with installation and commissioning).",
    },
    {
        "id": "final_result",
        "label": "Final result",
        "description": "What is delivered or achieved at the end of the service (final deliverable, outcome).",
        "example": "50 accepted LTE sites optimized and handed over to operations.",
    },
    {
        "id": "cost_contributors",
        "label": "Main cost contributors",
        "description": "Main factors or contributors that drive or affect the cost of the project.",
        "example": "Labour, equipment rental, materials, transport, accommodation.",
    },
    {
        "id": "people_required",
        "label": "People required",
        "description": "Whether people are required to execute the service; if yes, list roles or types.",
        "example": "1 Senior Network Engineer, 2 Field Engineers, 1 Driver.",
    },
    {
        "id": "preferred_tiering",
        "label": "Preferred tiering",
        "description": "Any preferred tiering or segmentation for the project (e.g. by region, by site type).",
        "example": "Tier by urban vs remote sites; or: no specific tiering.",
    },
    {
        "id": "transportation",
        "label": "Transportation",
        "description": "Whether transportation is required to execute the service.",
        "example": "Daily car transportation between sites and office; no heavy trucks required.",
    },
    {
        "id": "overnight_stay",
        "label": "Overnight away from base",
        "description": "Whether execution requires staying overnight away from base; if yes, mention details.",
        "example": "Hotel accommodation required for remote sites; none for Cairo sites.",
    },
    {
        "id": "tools_equipment_assets",
        "label": "Tools, equipment, or assets",
        "description": "Whether tools, equipment, or assets are needed; mention which if yes.",
        "example": "Drive test kits, laptops with optimization licenses, cranes for antenna tilting.",
    },
    {
        "id": "materials_consumables",
        "label": "Materials or consumables",
        "description": "Whether materials or consumables are used; mention which if yes.",
        "example": "Fiber optic cables, connectors, mounting hardware; no consumables.",
    },
    {
        "id": "turnkey_pricing",
        "label": "Turnkey / pricing methodology",
        "description": "Whether the service is turnkey (lump sum); if not, explain the pricing methodology used.",
        "example": "Turnkey lump sum per site; or: time and materials with daily rates per role.",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_suggestions_for_activity_type(activity_type: Optional[str]) -> Dict[str, str]:
    """Return the suggestion dict for a given activity type, or {} if not found."""
    if not activity_type:
        return {}
    # Exact match first
    if activity_type in ACTIVITY_SUGGESTIONS:
        return ACTIVITY_SUGGESTIONS[activity_type]
    # Case-insensitive fallback
    lower = activity_type.lower()
    for key, val in ACTIVITY_SUGGESTIONS.items():
        if key.lower() == lower:
            return val
    return {}


def build_suggestions_hint(
    answers: Dict[str, Optional[str]], point_id: str
) -> str:
    """
    If the user has already chosen an activity_type, return a suggestion hint
    string for the given point_id (e.g. 'Suggested answer: …').
    Returns '' if no suggestion is available.
    """
    activity_type = answers.get("activity_type")
    suggestions = get_suggestions_for_activity_type(activity_type)
    suggestion = suggestions.get(point_id)
    if suggestion:
        return f"Suggested answer based on activity type: {suggestion}"
    return ""


def _format_answers_for_llm(answers: Dict[str, Optional[str]]) -> str:
    """Format the current answers in a readable way for the LLM."""
    lines: List[str] = []
    for point in REQUIRED_POINTS:
        pid = point["id"]
        label = point["label"]
        val = answers.get(pid)
        if val:
            lines.append(f"{label}: {val}")
        else:
            lines.append(f"{label}: (not answered yet)")
    return "\n".join(lines)


def _format_suggestions_for_llm(answers: Dict[str, Optional[str]]) -> str:
    """
    Format the suggestion hints for the LLM so it can include them when asking
    questions. Returns '' if no activity type is set.
    """
    activity_type = answers.get("activity_type")
    suggestions = get_suggestions_for_activity_type(activity_type)
    if not suggestions:
        return ""
    lines = [f"Suggested answers for activity type '{activity_type}':"]
    for point in REQUIRED_POINTS:
        pid = point["id"]
        if pid in suggestions:
            lines.append(f"  - {point['label']}: {suggestions[pid]}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class CoverageDecision(BaseModel):
    done: bool = Field(
        ...,
        description="True if all required points are sufficiently covered to write a detailed activity description.",
    )
    next_point_id: Optional[str] = Field(
        None,
        description="The id of the next point that needs clarification (one of: "
        + ", ".join(p["id"] for p in REQUIRED_POINTS)
        + "), or null if done is true.",
    )
    question: Optional[str] = Field(
        None,
        description=(
            "A single, natural conversational question to ask the user next. "
            "If a suggestion is available for this point, include it in the question "
            "as a bracketed hint, e.g. '[Suggested: …]'. If done is true, this should be null."
        ),
    )
    suggestion_hint: Optional[str] = Field(
        None,
        description=(
            "If a suggested answer exists for next_point_id based on the activity type, "
            "reproduce it here verbatim so the UI can display it as a pre-filled suggestion. "
            "Null if no suggestion or if done."
        ),
    )


class _ExtractedPoint(BaseModel):
    """Single point extracted from user message."""

    point_id: str = Field(description="One of the point ids from REQUIRED_POINTS (e.g. project_type, final_result).")
    answer: Optional[str] = Field(description="The extracted or summarized answer text for this point.")


class ExtractedAnswers(BaseModel):
    """List of points the user addressed in their message, with extracted answers."""

    points: List[_ExtractedPoint] = Field(
        default_factory=list,
        description=(
            "List of points that the user clearly addressed in their last message, "
            "with the extracted answer for each. Only include points they explicitly or clearly mentioned."
        ),
    )


# ---------------------------------------------------------------------------
# Core agent functions
# ---------------------------------------------------------------------------

def extract_answers_from_message(
    current_answers: Dict[str, Optional[str]],
    user_message: str,
    last_asked_point_id: Optional[str] = None,
    last_suggestion_hint: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """
    Parse the user's message and extract answers for any of the required points they addressed.
    Returns a new answers dict with merged updates.

    last_asked_point_id: the point_id the bot most recently asked about.
    last_suggestion_hint: the suggestion the bot showed for that point (if any).
    These allow short confirmations like "yes" or "ok" to be resolved correctly.
    """
    if not user_message.strip():
        return dict(current_answers)

    # --- Fast-path: handle simple confirmations without an LLM call ---
    CONFIRM_WORDS = {"yes", "yeah", "yep", "ok", "okay", "sure", "correct",
                     "confirmed", "confirm", "right", "yup", "true", "agreed"}
    msg_lower = user_message.strip().lower()

    if msg_lower in CONFIRM_WORDS and last_asked_point_id and last_suggestion_hint:
        # User confirmed the suggestion for the last asked point
        updated = dict(current_answers)
        updated[last_asked_point_id] = last_suggestion_hint
        return updated

    extract_llm = llm.with_structured_output(ExtractedAnswers)
    points_list = "\n".join(
        f"- {p['id']}: {p['label']} — {p['description']}"
        for p in REQUIRED_POINTS
        if p["id"] != "activity_overview"
    )

    activity_types_str = "\n".join(f"  - {t}" for t in ACTIVITY_TYPES)

    # Context about the most recent question (helps LLM resolve short answers)
    last_question_context = ""
    if last_asked_point_id:
        label = next((p["label"] for p in REQUIRED_POINTS if p["id"] == last_asked_point_id), last_asked_point_id)
        last_question_context = f"\nThe bot's most recent question was about: {label} (id: {last_asked_point_id})."
        if last_suggestion_hint:
            last_question_context += f" The suggested answer shown to the user was: \"{last_suggestion_hint}\"."
        last_question_context += (
            "\nIf the user's message is a short confirmation (e.g. 'yes', 'ok', 'correct', 'that's right'), "
            "treat it as confirming the suggested answer for that point."
        )

    prompt = f"""
You are parsing a user's message in a Design-to-Cost activity description chat.

Points we need to collect (ids for reference):
{points_list}

Valid activity types for the 'activity_type' point:
{activity_types_str}

Current state of answers (what we already have):
{_format_answers_for_llm(current_answers)}
{last_question_context}

User's latest message:
"{user_message}"

Task:
- From the user's message, extract answers ONLY for points they clearly addressed.
- For the 'activity_type' point, map what the user says to the closest valid activity type from the list above.
- Use a short, clear summary (preserve important details).
- Do not invent answers for points they did not mention.
- Do not include activity_overview.
- If the user says "no" or "none" or "not applicable", still record it (e.g. answer "No" or "Not required").
- IMPORTANT: If the user's message is a confirmation of the suggested answer (e.g. "yes", "ok", "correct"),
  record the suggested answer text as the answer for the last asked point.
"""

    result = extract_llm.invoke(prompt)
    updated = dict(current_answers)
    for item in result.points or []:
        pid = item.point_id
        value = item.answer
        if pid in updated and value is not None and str(value).strip():
            updated[pid] = str(value).strip()
    return updated


def decide_next_question(answers: Dict[str, Optional[str]]) -> CoverageDecision:
    """
    Decide whether we have enough information and, if not, what to ask next.
    Suggestions are shown as hints in questions but are NOT treated as answers.
    """
    decision_llm = llm.with_structured_output(CoverageDecision)

    answered_ids = {pid for pid, val in answers.items() if val}
    unanswered_ids = [p["id"] for p in REQUIRED_POINTS if not answers.get(p["id"])]

    points_description = "\n".join(
        f"- {p['id']} ({p['label']}): {p['description']}"
        for p in REQUIRED_POINTS
    )

    activity_types_str = ", ".join(ACTIVITY_TYPES)
    suggestions_block = _format_suggestions_for_llm(answers)

    answered_summary = "\n".join(
        f"  ANSWERED — {p['label']} ({p['id']}): {answers.get(p['id'])}"
        for p in REQUIRED_POINTS
        if answers.get(p["id"])
    ) or "  (none yet)"

    unanswered_summary = "\n".join(
        f"  NEEDS ANSWER — {p['label']} ({p['id']})"
        for p in REQUIRED_POINTS
        if not answers.get(p["id"])
    ) or "  (all answered)"

    prompt = f"""
You are a friendly, conversational assistant helping to build an activity description for a Design-to-Cost analysis in Vodafone Egypt.

Points we need to collect:
{points_description}

Valid activity types: {activity_types_str}

=== CURRENT STATE ===
{answered_summary}

{unanswered_summary}
=== END STATE ===

{f"Suggested answers for the unanswered points (propose these in your questions, do NOT treat them as already answered):{chr(10)}{suggestions_block}" if suggestions_block else ""}

Your goal:
- Look ONLY at the NEEDS ANSWER list. Those are the only points you may ask about.
- ANSWERED points have been confirmed by the user — do NOT ask about them again.
- If the NEEDS ANSWER list is empty (or only contains the optional "phases"), set done = true.
- Otherwise set done = false and ask ONE natural question about the most important missing point.
- PRIORITIZE volume_driver early — its numeric value anchors all quantity estimation.
- For crew_structure and shared_resources: make clear the user can answer "not sure" and the planner will derive
  it. These answers prevent wrong scaling (e.g. assuming one truck per site). You may combine both in one question.

Guidelines:
- Be conversational. Do not sound like a form.
- When a suggestion exists, weave it naturally into the question:
  Good: "What are the main cost contributors? Is it the type of site (Rooftop / Greenfield), structure type, and building region — or something else?"
  Bad:  "What are the main cost contributors? [Suggested: ...]"
- Populate suggestion_hint with the verbatim suggestion text for the UI.
- You may combine 1-2 closely related UNANSWERED points in one question when natural.
- Never choose activity_overview as next_point_id.
- Keep questions short and easy to answer.
"""

    decision = decision_llm.invoke(prompt)

    # Hard guard: never re-ask an already-answered point
    if decision.next_point_id and decision.next_point_id in answered_ids:
        remaining = [pid for pid in unanswered_ids if pid != "activity_overview"]
        if not remaining or set(remaining) <= OPTIONAL_POINT_IDS:
            return CoverageDecision(done=True, next_point_id=None, question=None, suggestion_hint=None)
        remaining_str = ", ".join(remaining)
        retry_prompt = (
            prompt
            + f"\n\n[CORRECTION: You chose '{decision.next_point_id}' which is already ANSWERED. "
            f"You MUST pick from this unanswered list instead: {remaining_str}]"
        )
        decision = decision_llm.invoke(retry_prompt)

    return decision


# ---------------------------------------------------------------------------
# Description generation
# ---------------------------------------------------------------------------

class DescriptionOutput(BaseModel):
    activity_description: str = Field(
        ...,
        description="A detailed, coherent paragraph-level description of the activity "
        "that can be used as input for cost analysis.",
    )


def generate_activity_description(answers: Dict[str, Optional[str]]) -> str:
    """Generate a detailed activity description from the collected answers."""
    generator_llm = llm.with_structured_output(DescriptionOutput)

    prompt = f"""
You are a senior Design-to-Cost analyst for Vodafone Egypt.
Using the structured answers below, write a detailed, coherent description of the activity.

Answers:
{_format_answers_for_llm(answers)}

Requirements for the description:
- Use the activity overview as the base and weave in all collected answers naturally.
- Include: activity type, project type (hardware/software/service), project duration, volume driver with its size, final result/deliverable, main cost contributors, people required (if any), crews/parallel teams, shared resource pools, preferred tiering (if any).
- If phases were given, mention them in a natural way.
- Mention transportation, overnight stay, tools/equipment/assets, materials/consumables, and pricing methodology (turnkey or other) as relevant.
- CRITICAL — PRESERVE EVERY NUMBER: every numeric value in the answers (volume size, counts of people, crews,
  vehicles, equipment, durations) MUST appear verbatim in the description. Never drop, round, or generalize a
  number ("several trucks" is WRONG if the user said "3 trucks"). These numbers drive quantity estimation
  downstream; losing one forces the system to guess it later.
- If crews or shared resource pools are 'to be derived', say so explicitly so the planner knows to derive them.
- Write 1–3 rich paragraphs in clear business English suitable for cost estimation.
"""

    result = generator_llm.invoke(prompt)
    return result.activity_description


# ---------------------------------------------------------------------------
# Public API (used by the UI / calling code)
# ---------------------------------------------------------------------------

def check_initial_description(initial_description: str) -> Tuple[Dict[str, Optional[str]], CoverageDecision]:
    """
    Process the user's very first message: extract activity_overview AND any
    other points they mentioned (e.g. they may just type "Civil Services"),
    then decide the next question.

    Returns (answers, decision) so the caller can persist the answers dict.
    """
    answers: Dict[str, Optional[str]] = {p["id"]: None for p in REQUIRED_POINTS}
    if initial_description:
        answers["activity_overview"] = initial_description
        # Extract activity_type (and any other points) from the first message.
        # NOTE: We intentionally do NOT pre-fill suggestion answers here.
        # Suggestions are only shown as hints in questions; answers are only
        # recorded when the user explicitly confirms or provides them.
        answers = extract_answers_from_message(answers, initial_description)
    decision = decide_next_question(answers)
    return (answers, decision)


def process_user_message(
    answers: Dict[str, Optional[str]],
    user_message: str,
    last_asked_point_id: Optional[str] = None,
    last_suggestion_hint: Optional[str] = None,
) -> Tuple[Dict[str, Optional[str]], CoverageDecision]:
    """
    Process the user's message: extract any answers they provided (resolving
    short confirmations using last_asked_point_id / last_suggestion_hint),
    merge into the answers dict, then decide the next question or if we're done.
    Returns (updated_answers, decision).

    Parameters
    ----------
    answers               : current accumulated answers dict
    user_message          : what the user just typed
    last_asked_point_id   : the point_id the bot most recently asked about
    last_suggestion_hint  : the suggestion text shown for that point (if any)
    """
    updated = extract_answers_from_message(
        answers, user_message, last_asked_point_id, last_suggestion_hint
    )
    decision = decide_next_question(updated)
    return (updated, decision)


def update_answer_and_check(
    answers: Dict[str, Optional[str]],
    point_id: str,
    answer: str,
) -> CoverageDecision:
    """
    Backward-compatible wrapper. Prefer process_user_message for full conversational flow.
    """
    updated, decision = process_user_message(answers, answer, point_id)
    answers.update(updated)
    return decision


def get_activity_type_options() -> List[str]:
    """Return the list of valid activity types (for UI dropdowns / quick-reply buttons)."""
    return list(ACTIVITY_TYPES)


def get_suggestion_for_point(activity_type: str, point_id: str) -> Optional[str]:
    """
    Return the pre-filled suggestion for a specific point given an activity type.
    Returns None if not found.
    """
    suggestions = get_suggestions_for_activity_type(activity_type)
    return suggestions.get(point_id)


def apply_activity_type_suggestions(
    answers: Dict[str, Optional[str]], activity_type: str, overwrite: bool = False
) -> Dict[str, Optional[str]]:
    """
    Pre-fill all unanswered points with suggestions for the given activity type.
    If overwrite=True, also replaces already-answered points.
    Returns the updated answers dict.
    """
    suggestions = get_suggestions_for_activity_type(activity_type)
    updated = dict(answers)
    updated["activity_type"] = activity_type
    for pid, suggestion in suggestions.items():
        if overwrite or not updated.get(pid):
            updated[pid] = suggestion
    return updated


def get_initial_greeting() -> str:
    """Return the initial greeting message for the chatflow."""
    activity_list = "\n".join(f"  • {t}" for t in ACTIVITY_TYPES)
    return (
        "Welcome to the Cost Estimation Assistant!\n\n"
        "I'll have a short conversation with you to build a clear activity description for cost estimation. "
        "You can answer in your own words—if one message covers several points, that's fine.\n\n"
        "**To start, describe the activity you want to estimate costs for.**\n\n"
        "For example:\n"
        "- \"Roll out 50 new LTE sites in Cairo and Giza\"\n"
        "- \"Optimize network performance for 100 existing sites\"\n"
        "- \"Install fiber optic cables across 20 km\"\n\n"
        f"We support the following activity types:\n{activity_list}\n\n"
        "I'll ask a few natural follow-up questions until we have everything we need."
    )


def format_final_review_message(activity_description: str) -> str:
    """Format the message asking user to review/edit the generated description."""
    return (
        f"📝 **Generated Activity Description:**\n\n{activity_description}\n\n"
        "---\n\n"
        "**Does this look good?**\n\n"
        "• Type **'submit'** or **'yes'** to proceed with cost estimation\n"
        "• Type **'edit'** to modify the description\n"
        "• Or type your corrections directly, and I'll update it"
    )


# ---------------------------------------------------------------------------
# CLI loop (for local testing)
# ---------------------------------------------------------------------------

def run_description_extraction_chat() -> None:
    """Simple CLI chatbot loop for description extraction."""
    answers: Dict[str, Optional[str]] = {p["id"]: None for p in REQUIRED_POINTS}

    print("Activity Description Extraction Chatbot")
    print("I will ask you a few questions to fully understand the activity.\n")
    print(
        "To start, please briefly describe the activity in your own words "
        "(what is being done and for what purpose?):"
    )
    first_answer = input("> ").strip()
    if first_answer:
        # Use check_initial_description so activity_type is extracted immediately
        answers, decision = check_initial_description(first_answer)
    else:
        decision = decide_next_question(answers)

    # Track what the bot last asked about so "yes" can be resolved
    last_asked_point_id: Optional[str] = None
    last_suggestion_hint: Optional[str] = None

    # Handle the decision from the first message before entering the loop
    if decision.done:
        print("\nThank you. I now have enough information to write the full activity description.\n")
        description = generate_activity_description(answers)
        print("=== Detailed Activity Description ===\n")
        print(description)
        print("\n====================================\n")
        return

    while True:
        decision = decide_next_question(answers)

        if decision.done:
            print("\nThank you. I now have enough information to write the full activity description.\n")
            description = generate_activity_description(answers)
            print("=== Detailed Activity Description ===\n")
            print(description)
            print("\n====================================\n")
            break

        if not decision.next_point_id or not decision.question:
            print("\nI need a bit more detail about the activity. Could you elaborate further?")
            user_answer = input("> ").strip()
            if user_answer:
                existing = answers.get("activity_overview") or ""
                answers["activity_overview"] = (existing + "\n" + user_answer).strip()
            continue

        # Remember what we just asked
        last_asked_point_id = decision.next_point_id
        last_suggestion_hint = decision.suggestion_hint

        print(f"\n{decision.question}")

        if decision.suggestion_hint:
            print(f"  [Suggested: {decision.suggestion_hint}]")
            print("  (Press Enter to accept the suggestion, or type your own answer)")

        user_answer = input("> ").strip()

        # Accept suggestion if user just pressed Enter and a hint is available
        if not user_answer and last_suggestion_hint and last_asked_point_id:
            answers[last_asked_point_id] = last_suggestion_hint
            print(f"  ✓ Accepted suggestion: {last_suggestion_hint}")
            last_asked_point_id = None
            last_suggestion_hint = None
            continue

        if user_answer:
            updated_answers, decision = process_user_message(
                answers, user_answer, last_asked_point_id, last_suggestion_hint
            )
            answers = updated_answers
            last_asked_point_id = None
            last_suggestion_hint = None
            if decision.done:
                print("\nThank you. I now have enough information to write the full activity description.\n")
                description = generate_activity_description(answers)
                print("=== Detailed Activity Description ===\n")
                print(description)
                print("\n====================================\n")
                break
        else:
            print("No answer provided—we'll ask again or move on.")


if __name__ == "__main__":
    run_description_extraction_chat()