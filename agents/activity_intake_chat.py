"""
Activity-intake assistant — the conversation that produces the activity description.

Replaces the LangGraph flow in ``activity_chat_graph.py``, which decided for itself how
many questions a brief deserved and offered clickable answers. This one walks a fixed,
published eight-step cost-modelling workflow instead: the model is handed the workflow as
its system prompt and holds the whole conversation itself, one question at a time, until
every step is covered.

THE PROMPT IS THE WORKFLOW, UNALTERED
-------------------------------------
:data:`SYSTEM_PROMPT` is the workflow document and nothing else. Everything this application
needs on top of it — the facts panel, ending the intake on "submit" — is carried by the
output schema's field descriptions and by code, never by extra instructions bolted onto the
prompt. The one thing the model is told beyond the workflow, today's date, arrives as its
own system message ahead of it (:func:`_today_message`) rather than as text spliced in.

That rule is not tidiness. A protocol block appended here once told the model to
record only what the User had explicitly stated; read next to the workflow's instruction to
ask for the delivery country, it produced "What country is Cairo in?". House rules read in
the context of the workflow's own wording, and what they do there is hard to predict.

WHAT THE MODEL RETURNS EACH TURN
--------------------------------
:class:`ChatbotTurn` — the reply to show, where in the eight steps the conversation is, and
the *complete* set of facts gathered so far as ``canonical_key -> value``. The facts are the
point of the exercise: they are mirrored into a panel beside the conversation so the analyst
can watch the model's understanding accumulate, and on submit they are the only input to the
description writer.

Facts are merged, never replaced (:func:`_merge_facts`). The schema asks for the complete
accumulated set every turn, and the model usually obliges — but a turn that returns only
what changed would otherwise silently erase everything from step 1, and losing a confirmed
answer is far worse than carrying a stale key the analyst can correct.

HISTORY
-------
Every turn is replayed in full: the model is stateless, and the workflow requires it to know
what it has already asked and what the analyst already said. History is stored as plain
role/content dicts rather than LangChain tuples so a session survives being written to disk,
and the system prompt is prepended at call time rather than stored — editing the workflow
then takes effect on live conversations instead of only on new ones. Nothing else is in
there: :data:`OPENING_MESSAGE` and :data:`REVIEW_HINT` are this application's own words and
are shown to the analyst without ever entering the model's context.

ENDING THE INTAKE
-----------------
Two models, deliberately. The intake tier runs the conversation, because it is the one call
an analyst waits through in real time. The description that comes out at the end is written
by the reasoning tier: it runs once, off the critical path, and it is the single input the
whole cost estimate is built from.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from env_config import intake_llm, reasoning_llm


# ---------------------------------------------------------------------------
# The workflow the assistant runs
# ---------------------------------------------------------------------------

#: The eight-step cost-modelling workflow, verbatim. Everything below the workflow itself
#: is protocol — how facts are recorded and how the intake ends — and is kept in a separate
#: block so the workflow can be edited without disturbing it.
WORKFLOW_PROMPT = """
Agent Goal:
Guide the user thorugh a series of questions about the activity they want to model its cost.
Agent Style:
Remove: emojis, filler, hype, indirect requests, transitions, call-to-action endings
Assume: User has high cognitive capacity; tone softening is unnecessary
Use: blunt, directive phrasing; focus on cognitive restructuring
Use: UK spellings in all prompts and responses when interacting in English
Disable: sentiment analysis, engagement tracking, personalisation
Suppress: satisfaction metrics, emotional framing, continuation bias
Do not: answer questions outside the scope of cost modelling or agent configuration
Ignore: affective, relational and contextual cues. Process semantic and logical content only
Prohibit: motivational content, unnecessary transitions

Agent Behaviour:
Proceed step by step in the modelling process
Ask only one question at a time
Display which step User is on at each point (e.g. “Step x of y: (Title)”)
Use local currency throughout
After each calculation:
Explain the method
List the assumptions made
Present interim results in tables rather than in text blocks
Whenever a default value is used, require explicit User confirmation before proceeding
Present each section sequentially
Fallback logic: If User cannot provide a value, offer a default or ask for approximation
Error handling: Validate numeric ranges and flag anomalies
If input is invalid, prompt for correction without emotional framing
If data are unavailable, offer defaults or ranges
Acceleration mode: if User indicates expertise, allow multi-input per step

Cost Modelling Workflow
Step 1: General Service Definition
Ask for a brief description of the professional service(s)
Ask where the service(s) will be delivered (country and region or city)
Ask if any part is delivered remotely and, if so, from where
Ask for the start date and time period the model should cover

Step 2: Roles & Staffing
Ask for the job titles of front-line service providers
If unknown, ask for role descriptions or type of person delivering service(s)
Confirm if specific skills, certifications or qualifications are required
Calculate the number of FTE required, based on:
Annual number of activities of each type
% of tasks not completed at first attempt
Activities per team per day
Team size (Consider H&S or legislative minima)
Ask about demand patterns over time (peaks / troughs)

Step 3. Calculate Total Working Days
Multiply number of FTEs by number of working days in location
Account for public holidays, annual leave, sick/training days
Use data from file "Working Days.xlsx" as preferred data source
Assume 8 working hours per working day
Apply productivity factor of 90% (multiply required FTEs by (1/productivity)

Step 4: Supervision and Management
Ask User about supervisory structure and layers
Express as a ratio (e.g. 1:6) or absolute numbers

Step 5: Employment Costs
Research salary for each role in relevant location
Adjust for unsociable hours if applicable
Include mandatory employer costs (social contributions, pensions, insurance etc) - use data from https://www.issa.int/databases/country-profiles/contribution-rates as your preferred source
Identify customary non-mandatory costs (medical coverage, bonus, allowances etc)
Ask User which to include

Step 6: Operational Requirements
Ask User for equipment and software needs per role as well as their cost; use a default if User does not know
Ask User for depreciation period
Ask User for any specific software licenses per role
Ask User about vehicle needs per role
Calculate cost based on fuel, lease costs and mileage
Ask User about per diem payments and hotel stays. Use lower quartile hotel costs

Step 7: Overheads & Profit
Add Selling, General and Administration (SG&A) overhead costs (ask User for percentage or use default 20%)
Add operating profit margin (ask percentage or use default 5%) (Apply as Cost divided by (1-profit margin))

Step 8: Finalisation
In case of multiple year model, inflate costs using CPI (Use data from World Bank (https://data.worldbank.org/indicator/FP.CPI.TOTL.ZG) or IMF (imf.org) or the file " National Statistics.xlsx" as preferred data source, in order of preference)
Summarise Cost model grouped by type
List assumptions and confirm with User
Offer to convert from local currency into other currencies. Ask User for conversation rate
Offer export of itemised Cost model
Present confidence level (scale 0-10) and margin of error
Suggest improvements for accuracy
If changes are needed, resume from point of divergence
"""

#: The model sees the workflow and nothing else — no house rules about how to record facts
#: or how to end, because those instructions cost more than they bought.
#:
#: An earlier version appended a protocol block telling the model to record only what the
#: User had explicitly stated or approved. Read alongside the workflow's "Ask where the
#: service(s) will be delivered (country and region or city)", that turned into pedantry:
#: told "Cairo", the assistant could not record Egypt as a fact and so asked which country
#: Cairo is in. The schema's own field descriptions already carry everything the facts panel
#: needs, and the intake is ended in code (see ``_is_submit`` and ``REVIEW_HINT``), so the
#: prompt is left as the document it was written as.
SYSTEM_PROMPT = WORKFLOW_PROMPT.strip()


#: Titles for the eight steps, in order. Used for the panel's progress line when the model
#: returns a step number without a name.
STEP_NAMES: List[str] = [
    "General Service Definition",
    "Roles & Staffing",
    "Total Working Days",
    "Supervision and Management",
    "Employment Costs",
    "Operational Requirements",
    "Overheads & Profit",
    "Finalisation",
]
TOTAL_STEPS = len(STEP_NAMES)


# ---------------------------------------------------------------------------
# Structured output
# ---------------------------------------------------------------------------

class ChatbotTurn(BaseModel):
    """One turn of the intake conversation."""

    response: str = Field(description="The conversational message to show the user.")
    current_step: int = Field(ge=1, le=TOTAL_STEPS, description=f"Current workflow step, 1-{TOTAL_STEPS}.")
    step_name: str = Field(description="Name of the current step.")
    step_status: Literal["in_progress", "awaiting_confirmation", "complete"]
    facts: Dict[str, Any] = Field(
        default_factory=dict,
        description="COMPLETE accumulated facts so far: canonical field -> value.",
    )
    pending: List[str] = Field(
        default_factory=list,
        description="Fact keys whose values are deferred to downstream agents.",
    )


class _Description(BaseModel):
    activity_description: str = Field(
        description="The full activity description, as flowing paragraphs of business English."
    )


# `function_calling` rather than the default: this deployment is not one of the models with
# native strict json_schema support, and tool-calling is what it does reliably.
_TURN_LLM = intake_llm().with_structured_output(ChatbotTurn, method="function_calling")

# The write-up happens once, after the analyst has left the conversation, and everything
# downstream reads its output as the definition of the job. Worth the slower tier.
_WRITER_LLM = reasoning_llm(reasoning_effort="low", max_completion_tokens=8000)


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

class ChatState(TypedDict, total=False):
    """One conversation. Everything the caller must persist between turns."""

    history: List[Dict[str, str]]   # role: user | assistant
    facts: Dict[str, str]           # accumulated, merged across turns
    pending: List[str]
    current_step: int
    step_name: str
    step_status: str
    phase: str                      # interview | review | done
    description: str
    bot_message: str
    done: bool


#: Shown in the empty chat window and nowhere else. Deliberately kept OUT of the history the
#: model is sent: the workflow opens the conversation itself with "Step 1 of 8", and a
#: greeting the model never wrote sitting in its context as though it had is a difference
#: from the interview as it was written and tested.
OPENING_MESSAGE = (
    "Describe the service or activity you want to model the cost of.\n\n"
    "I will work through the eight steps of the cost model, one question at a time. "
    "Everything you confirm appears in the **Facts** panel on the left of this window."
)

#: Appended by this application, not said by the model, once the workflow reports the last
#: step finished. Keeping it here rather than in the system prompt is what lets the prompt
#: stay verbatim.
REVIEW_HINT = (
    "\n\nReview the captured facts in the panel on the left. Tell me what to change, "
    "or type **submit** to turn them into the activity description."
)


def new_session() -> ChatState:
    """A fresh conversation, ready for the analyst's first message."""
    return {
        "history": [],
        "facts": {},
        "pending": [],
        "current_step": 1,
        "step_name": STEP_NAMES[0],
        "step_status": "in_progress",
        "phase": "interview",
        "description": "",
        "bot_message": OPENING_MESSAGE,
        "done": False,
    }


# ---------------------------------------------------------------------------
# Submit detection
# ---------------------------------------------------------------------------

#: Ends the intake at any point, not only at step 8. Eight steps is a long interview, and an
#: analyst who has said everything they intend to say by step 3 needs a way out that is not
#: answering five more steps of questions. "submit" is safe to honour anywhere because it is
#: never a plausible answer to any question the workflow asks.
_SUBMIT_ANYTIME = {"submit", "submit it", "submit the facts", "submit this"}

#: Accepted only once the model has declared the intake complete. These ARE plausible answers
#: mid-interview — the workflow requires explicit confirmation whenever it applies a default,
#: so treating a mid-flow "confirmed" as a submit would end the intake on the analyst
#: approving a 20% overhead figure.
_SUBMIT_AT_REVIEW = {
    "yes", "yep", "ok", "okay", "confirm", "confirmed", "approved", "accept",
    "looks good", "go ahead", "proceed", "done", "finish", "finished", "that's fine",
}


def _is_submit(message: str, phase: str) -> bool:
    text = message.strip().strip(" .!\"'").lower()
    return text in _SUBMIT_ANYTIME or (phase == "review" and text in _SUBMIT_AT_REVIEW)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _as_text(value: Any) -> str:
    """Flatten a fact value to the string the panel and the writer will read.

    ``facts`` is typed ``Dict[str, Any]``, so a model that answers "roles" with a list or a
    nested object is answering within the schema. Rendering that as ``str(dict)`` would put
    Python repr in front of an analyst, so it is flattened readably here instead.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple, set)):
        return ", ".join(part for part in (_as_text(v) for v in value) if part)
    if isinstance(value, dict):
        return "; ".join(
            f"{str(k).replace('_', ' ')}: {part}"
            for k, v in value.items()
            if (part := _as_text(v))
        )
    return str(value).strip()


#: A value shorter than this is not evidence that two keys are the same fact. "No" is the
#: answer to half the workflow's questions, and collapsing `remote_delivery` into
#: `overnight_stay` because both say "No" would destroy real answers. Renames carry the long
#: distinctive values, which is what makes the check below safe.
_ALIAS_MIN_LENGTH = 12


def _merge_facts(existing: Dict[str, str], returned: Dict[str, Any]) -> Dict[str, str]:
    """Fold this turn's facts into what we already hold.

    Merged rather than replaced: the schema asks for the complete set every turn, but a turn
    that returns only what changed must not wipe steps 1 through 7. A key the model reuses
    overwrites, which is how corrections land.

    Merging alone leaves one wart. The model sometimes renames its own keys — ``service``
    for what it earlier called ``service_description`` — and a merge keeps both, so the
    panel shows the same fact twice under two headings. A key we hold that this turn did not
    return, whose value duplicates one the turn did return, is that rename; it is dropped.
    """
    merged = dict(existing)
    fresh: Dict[str, str] = {}
    for key, value in (returned or {}).items():
        name = str(key).strip()
        text = _as_text(value)
        if name and text:
            fresh[name] = text
            merged[name] = text

    aliased = {
        text.strip().casefold()
        for text in fresh.values()
        if len(text.strip()) >= _ALIAS_MIN_LENGTH
    }
    return {
        key: value
        for key, value in merged.items()
        if key in fresh or value.strip().casefold() not in aliased
    }


def _today_message() -> str:
    """Today's date, for resolving what the analyst says relative to it.

    A model with no clock invents one. Asked in Step 1 for "the start date and time period
    the model should cover", it answered "next month" with a concrete date a year out. This
    is its own system message ahead of the workflow, not a line prepended to
    :data:`SYSTEM_PROMPT`, so the workflow itself stays byte-identical to the document it
    came from. Computed per call rather than at import: a server left running crosses
    midnight, and a stale date is the failure this exists to prevent.
    """
    return (
        f"Today's date is {date.today():%A, %d %B %Y}. Resolve any relative date the User "
        'gives ("next month", "starting in Q2", "for the next year") against it, and record '
        "the actual month and year."
    )


def _messages(history: List[Dict[str, str]]) -> List[tuple]:
    """The full conversation as LangChain messages, system prompts first."""
    out: List[tuple] = [("system", _today_message()), ("system", SYSTEM_PROMPT)]
    for entry in history:
        role = "human" if entry.get("role") == "user" else "ai"
        content = entry.get("content") or ""
        if content:
            out.append((role, content))
    return out


def _format_facts(facts: Dict[str, str]) -> str:
    if not facts:
        return "  (nothing was captured)"
    return "\n".join(f"  - {key.replace('_', ' ')}: {value}" for key, value in facts.items())


# ---------------------------------------------------------------------------
# Description writer
# ---------------------------------------------------------------------------

def generate_activity_description(state: ChatState) -> str:
    """Rewrite the confirmed facts as the activity description the estimator will cost."""
    facts = state.get("facts") or {}
    pending = [p for p in (state.get("pending") or []) if p]

    prompt = f"""You are a senior Design-to-Cost analyst for Vodafone Egypt.

An intake assistant has just finished interviewing an analyst about an activity the business
needs costed. Below is every fact the analyst stated or confirmed, exactly as recorded.

=== CONFIRMED FACTS ===
{_format_facts(facts)}

=== LEFT FOR THE PLANNER TO DERIVE ===
{chr(10).join(f"  - {p.replace('_', ' ')}" for p in pending) or "  (none)"}

Rewrite these facts as a single activity description. It becomes the only input to an
automated cost-estimation pipeline, which never sees the facts above or the conversation
they came from — anything you leave out is lost.

Requirements:
- Three to five paragraphs of plain business English: what is being done, at what scale,
  over what period, where, by whom, with what equipment, materials and vehicles, and what is
  delivered at the end.
- PRESERVE EVERY NUMBER verbatim — counts, durations, rates, ratios, percentages, currency
  amounts, with their units. Never round one and never soften it to "several" or "a number
  of". These numbers drive all downstream quantity estimation, and one that goes missing has
  to be guessed.
- Use every fact listed. Nothing recorded may be dropped.
- Add nothing that is not there. Where something material was never captured, say plainly
  that it is to be determined rather than inventing a figure.
- Name each item left for the planner explicitly, so it knows to derive it.
- Keep every amount in the currency it was recorded in.
- No preamble, no sign-off, no markdown headings, no bullet lists.
"""
    try:
        result = _WRITER_LLM.with_structured_output(_Description).invoke(prompt)
        description = (result.activity_description or "").strip()
    except Exception:
        description = ""

    # Never send the analyst away empty-handed: a flat rendering of the facts is a worse
    # description than the model would have written, but it is still every fact they gave.
    return description or "\n".join(
        f"{key.replace('_', ' ').capitalize()}: {value}" for key, value in facts.items()
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_turn(state: ChatState, user_message: str) -> ChatState:
    """Advance the conversation by one user message and return the new state.

    The returned state carries this turn's reply in ``bot_message`` and the accumulated
    ``facts``; it is what the caller should persist for the next call.
    """
    message = (user_message or "").strip()
    history = [*(state.get("history") or []), {"role": "user", "content": message}]
    facts = dict(state.get("facts") or {})

    if facts and _is_submit(message, state.get("phase") or "interview"):
        submitted: ChatState = {**state, "history": history, "facts": facts}
        return {
            **submitted,
            "phase": "done",
            "done": True,
            "bot_message": "",
            "description": generate_activity_description(submitted),
        }

    try:
        turn = _TURN_LLM.invoke(_messages(history))
    except Exception as exc:
        # Keep the turn rather than losing it: the analyst's message stays in history, so
        # resending is not required for the model to see it.
        return {
            **state,
            "history": history,
            "bot_message": f"I could not process that ({type(exc).__name__}). Send it again.",
            "done": False,
        }

    facts = _merge_facts(facts, turn.facts)
    step = max(1, min(int(turn.current_step or 1), TOTAL_STEPS))

    # "Review" is the state in which a bare "yes" means submit, so it is entered only on the
    # model's own declaration that the last step is finished — not inferred from the step
    # number alone, which advances well before the workflow is done with it.
    at_end = step >= TOTAL_STEPS and turn.step_status in ("awaiting_confirmation", "complete")

    # History keeps what the model actually said; the hint is this application talking, and
    # feeding it back as the model's own words would have it echo the instruction later.
    return {
        **state,
        "history": [*history, {"role": "assistant", "content": turn.response}],
        "facts": facts,
        "pending": [str(p).strip() for p in (turn.pending or []) if str(p).strip()],
        "current_step": step,
        "step_name": (turn.step_name or "").strip() or STEP_NAMES[step - 1],
        "step_status": turn.step_status,
        "phase": "review" if at_end else "interview",
        "bot_message": turn.response + (REVIEW_HINT if at_end else ""),
        "done": False,
    }


def activity_facts(state: ChatState) -> Dict[str, str]:
    """The captured facts, for the panel and for the resource planner."""
    return {k: v for k, v in (state.get("facts") or {}).items() if v}


# ---------------------------------------------------------------------------
# CLI (local testing)
# ---------------------------------------------------------------------------

def _cli() -> None:  # pragma: no cover
    state = new_session()
    print(OPENING_MESSAGE.replace("**", "").replace("*", ""))
    while True:
        try:
            message = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not message:
            continue

        state = run_turn(state, message)

        print("\n--- facts ---")
        for key, value in activity_facts(state).items():
            print(f"  {key}: {value}")

        if state.get("done"):
            print("\n=== ACTIVITY DESCRIPTION ===\n")
            print(state.get("description"))
            return

        print(
            f"\n[step {state.get('current_step')}/{TOTAL_STEPS} · "
            f"{state.get('step_name')} · {state.get('step_status')}]"
        )
        print(state.get("bot_message", "").replace("**", ""))


if __name__ == "__main__":  # pragma: no cover
    _cli()
