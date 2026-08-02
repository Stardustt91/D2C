"""
Activity-description assistant — a LangGraph state machine.

Replaces the old fixed-questionnaire flow (``description_extraction.py``), which walked
every user through the same list of required points and pre-filled "recommended answers"
looked up from a table of known activity types. That worked only for activities already
in the table, and it asked fifteen questions whether or not the answers mattered.

This agent knows nothing about historical activities and carries no list of required
points. It opens with one question — *describe your activity* — and from there decides
for itself what else it needs, if anything.

HOW LONG THE INTERVIEW RUNS
---------------------------
Length is set by how specific the analyst's opening brief was, not by a fixed number. On
the first turn the agent rates the brief — sparse, partial or detailed — and commits to a
question total for the whole conversation: roughly five to eight for a one-liner, none to
two for a brief that already fixes scale, period, scope and who supplies what. That total
is stored in ``question_budget`` and carried unchanged thereafter, because re-deriving it
each turn lets it drift upward as facts accumulate, and an interview whose length keeps
being renegotiated mid-way is what makes these conversations feel interminable.

Within the budget the agent spends questions on what moves the estimate most, and stops
early when the material gaps run out. :data:`MAX_QUESTIONS` only bounds the worst case.

Everything not asked about still gets a concrete default: each gap the agent identifies
carries the value it would assume, and those flow into the description's assumptions
section. A gap silently dropped is worse than one assumed wrongly — the analyst can
correct a stated assumption in seconds, but cannot correct one they never saw.

Assumptions are never laundered into facts. What the user actually said lands in
``facts`` and flows on as ``activity_facts``, which downstream prompts label as
authoritative. What the agent assumed lands in ``assumptions`` and is written into the
description under an explicit heading, so the planner — and the analyst reading the
export — can tell the two apart.

TURN MODEL
----------
``understand -> draft``, plus ``review`` once a draft is on the table. Reading the reply
and deciding the next question are one LLM call, not two: they share all their context, so
splitting them bought nothing but the latency the analyst waits through between pressing
send and seeing the next question.

The graph is a single-turn transducer: one invocation consumes one user message and
returns the next bot message plus the updated state, which the caller persists. It
deliberately does not use a checkpointer with ``interrupt()`` — the HTTP layer already
holds per-session state, and the explicit-state form keeps the graph directly testable
and insensitive to langgraph's version-to-version interrupt semantics.
"""

from __future__ import annotations

import os
from typing import Dict, List, Literal, Optional

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from env_config import chat_llm, optional, reasoning_llm


#: Ceiling on follow-up questions, not a target. The interview's real length comes from
#: :data:`BUDGET_BY_SPECIFICITY`; this only bounds the worst case.
MAX_QUESTIONS = int(optional("D2C_CHAT_MAX_QUESTIONS", default="8"))

#: How many questions a brief earns, by how much it already says. The model classifies the
#: brief into one of three buckets and the number is looked up here rather than asked for
#: directly: picking a count is a judgement gpt-4o-mini does badly — asked for one it
#: returned 5 for a one-line brief and 5 for a full spec alike — while sorting a brief into
#: sparse/partial/detailed is a classification it does reliably. Keeping the number on this
#: side also makes the interview's length a tunable rather than a model whim.
BUDGET_BY_SPECIFICITY = {"sparse": 6, "partial": 4, "detailed": 1}

#: Always appended to a single-select question. Guaranteeing the escape hatch in code
#: rather than asking the model to remember it is what keeps sessions short: the analyst
#: can always decline a question instead of inventing an answer to get past it.
DEFER_OPTION = "Not sure — make a sensible assumption"

#: Offered alongside the draft so confirming is one click.
REVIEW_OPTIONS = ["Looks good — use this description", "I'd like to change something"]


# The per-turn call runs on the cheap tier, because it runs between the analyst pressing
# send and the next question appearing. On the reasoning tier that wait was long enough to
# read as the assistant being stuck, and the judgement it buys is not worth it here: the
# hard part — how long the interview should run — is bounded in code either way.
# Drafting keeps the reasoning tier. It happens once, off the critical path of the
# conversation, and it is the output the whole estimate is built on.
_parse_llm = chat_llm(max_tokens=3000)
_think_llm = reasoning_llm(reasoning_effort="low", max_completion_tokens=6000)


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------

class ChatState(TypedDict, total=False):
    """One conversation. Everything the caller must persist between turns."""

    # --- inputs for this turn ---
    user_message: str
    selected_options: List[str]

    # --- accumulated across turns ---
    history: List[Dict[str, str]]
    facts: Dict[str, str]          # user-stated only; becomes activity_facts
    assumptions: List[str]         # agent-supplied; never presented as fact
    gap_defaults: List[str]        # latest assessor defaults, the raw material for the draft
    asked_questions: List[str]     # every question put to the analyst, verbatim
    asked_topics: List[str]        # the subject of each, slugged, to bar repeats
    questions_asked: int
    question_budget: int           # total this brief warrants; set once, on the first turn
    stop_reason: str               # why the interview ended; diagnostics for tuning
    phase: str                     # intake | questioning | review | done
    description: str
    edit_request: str
    last_question: str

    # --- outputs of this turn ---
    bot_message: str
    options: List[str]
    multi_select: bool
    done: bool


# ---------------------------------------------------------------------------
# Structured-output schemas
# ---------------------------------------------------------------------------

class _Fact(BaseModel):
    slot: str = Field(
        description="snake_case name for what this fact is, e.g. 'volume_driver', "
        "'project_duration', 'geography', 'scope_boundary'. Reuse an existing slot "
        "name when updating something already captured."
    )
    value: str = Field(description="The fact as the user stated it. Keep every number verbatim.")


class _Gap(BaseModel):
    unknown: str = Field(description="What is still unknown.")
    default_assumption: str = Field(
        description="The specific value you would assume if you do NOT ask. Never a hedge — "
        "a real number or a real choice."
    )
    impact: Literal["structural", "moderate", "minor"] = Field(
        description="structural = the analyst has not told you and being wrong adds or removes "
        "a whole category of cost, or moves the total materially. moderate = affects the number "
        "but the estimate survives a sensible default. minor = noise."
    )


class _Understanding(BaseModel):
    """One call per turn: read the reply, re-read the brief, decide what to ask next.

    Extraction and assessment were two calls against two deployments until the round trip
    made the assistant visibly slow to ask its first question. They share all their
    context, so splitting them bought nothing but latency.
    """

    # Field order is the reasoning order. Facts first so the rest is judged against what
    # is actually known; the budget before the gaps so the gap list is drawn up against a
    # decided appetite rather than the budget rationalised afterwards from the list.
    facts: List[_Fact] = Field(
        default_factory=list,
        description="Facts the analyst actually stated or clearly implied in their latest "
        "message. Never infer beyond what they said.",
    )
    deferred: bool = Field(
        default=False,
        description="True if the analyst declined to answer and asked you to assume "
        "(e.g. 'not sure', 'you decide', 'whatever is typical').",
    )
    brief_specificity: Literal["sparse", "partial", "detailed"] = Field(
        description="How much of the cost shape the analyst's OPENING brief pinned down. "
        "sparse = names the activity and essentially nothing else ('we need to install some "
        "new mobile sites'). partial = gives some of scale, period or location but leaves the "
        "rest open ('roll out 30 LTE sites in Greater Cairo' — a count and a place, but no "
        "period and no scope). detailed = scale, period, location, scope and who supplies "
        "what are all stated. Judge the FIRST message only, never what you have learned since."
    )
    gaps: List[_Gap] = Field(
        default_factory=list,
        description="Everything still unknown that bears on cost, each with the default you "
        "would assume if you never got to ask about it.",
    )
    question: Optional[str] = Field(
        None,
        description="ONE short, plain-language question opening a topic not yet put to the "
        "analyst. Never about cost, price, budget or rates — the system exists to work those "
        "out. Null only when nothing material is left open.",
    )
    topic: Optional[str] = Field(
        None,
        description="snake_case name for what the question is about — 'volume', 'duration', "
        "'geography', 'scope_boundary', 'who_supplies_equipment'. Name the subject, not the "
        "wording, so two phrasings of the same question get the same name. Must differ from "
        "every topic already asked.",
    )
    options: List[str] = Field(
        default_factory=list,
        description="REQUIRED whenever there is a question: 3 to 5 concrete clickable answers, "
        "specific to this activity. For a 'how many' or 'how long' question give realistic "
        "ranges or typical values (e.g. '10-25 sites', '3 months') — never leave this empty on "
        "the grounds that the answer is a number. Omit any 'other'/'not sure' choice; the "
        "interface adds that itself.",
    )
    multi_select: bool = Field(
        default=False,
        description="True only when several options can genuinely apply at once.",
    )


class _Description(BaseModel):
    # The two halves are kept apart and stitched together by the caller. Asking for one
    # field holding both the narrative and its assumptions section loses the section:
    # given a dedicated `assumptions` field the model files them there and writes a clean
    # body, which is reasonable of it and leaves the analyst reading a draft that commits
    # to specifics without saying which of them anyone actually stated.
    activity_description: str = Field(
        description="The narrative body ONLY — two or three paragraphs. Do NOT include an "
        "assumptions section, heading, or list; those go in the field below."
    )
    assumptions: List[str] = Field(
        default_factory=list,
        description="Every assumption the description relies on, each a complete sentence a "
        "reviewer can accept or correct on its own.",
    )


class _ReviewIntent(BaseModel):
    intent: Literal["confirm", "edit", "clarify"] = Field(
        description="confirm = accept the description as-is. edit = they told you what to "
        "change, apply it directly. clarify = they want a change but you need to ask "
        "something before you can make it."
    )
    edit_request: str = Field(
        default="", description="What they want changed, in their terms. Empty when confirming."
    )


# ---------------------------------------------------------------------------
# Shared prompt fragments
# ---------------------------------------------------------------------------

_ROLE = (
    "You are the intake assistant for a Design-to-Cost estimator used by the supply chain "
    "team at Vodafone Egypt. Analysts bring you an activity they need costed — telecom "
    "rollouts, civil works, fleet, training, media, software, anything the business buys. "
    "You know nothing about their past projects and you must not pretend otherwise."
)

_QUESTION_POLICY = f"""
HOW MANY QUESTIONS TO ASK

The length of the interview tracks how much the analyst has already told you. A one-line
brief leaves the whole shape of the cost open and deserves a real conversation; a brief
that already fixes scale, period, scope and who supplies what deserves almost none. Your
job is to classify the OPENING brief honestly — sparse, partial or detailed — and the
number of questions it earns follows from that:

  sparse  — names the activity and little else          -> {BUDGET_BY_SPECIFICITY['sparse']} questions
  partial — scale or period given, much still open      -> {BUDGET_BY_SPECIFICITY['partial']} questions
  detailed— scale, period, scope, location, supply set  -> {BUDGET_BY_SPECIFICITY['detailed']} question

Classify what they FIRST brought you, not how much you have learned since; the allowance
is set once, on the opening brief, and does not shrink as answers come in.

While that allowance lasts, keep asking — ask a question every turn. Leave the question
empty ONLY when you genuinely have nothing left worth asking; running out of allowance is
handled for you, so do not stop early to be polite.

Spend the questions on what moves the estimate most:
  - The magnitude that scales the work, and the period it runs over.
  - What is actually being bought: which parts of the job are in the price and which are
    already provided, when the brief leaves that ambiguous.
  - Where the work happens, when travel, access or accommodation are real costs.
  - Anything else whose answer would add or remove an entire category of cost.
This is not a checklist to walk — anything the brief already answers is closed. Do not
manufacture a gap the brief has already settled, and never re-ask something in new words.

NEVER ask about money. Not cost, not price, not budget, not day rates, not cost per unit.
Working those out from the description is the entire purpose of the system this feeds; an
analyst who already knew the cost would not be here. Ask about the physical shape of the
work — how much of it, for how long, where, and who supplies what — and never its price.

Each question must open a DIFFERENT topic from every one already put to them. A topic that
was asked is finished whether they answered it or waved it through: if they declined, the
assumption recorded against it is the answer, and asking again in fresh wording reads as
not having listened.

NEVER re-ask a topic listed as already raised, in any rephrasing. If the analyst declined
it, that is a settled answer: assume it and move on.
""".strip()


def _format_facts(facts: Dict[str, str]) -> str:
    if not facts:
        return "  (nothing captured yet)"
    return "\n".join(f"  - {k}: {v}" for k, v in facts.items() if v)


def _format_history(history: List[Dict[str, str]], limit: int = 12) -> str:
    if not history:
        return "  (no messages yet)"
    recent = history[-limit:]
    return "\n".join(f"  {m['role']}: {m['content']}" for m in recent)


#: Heading the assumptions are filed under, in the draft and in the estimator's activity
#: field. Downstream agents read the description as one blob, so the marker is what keeps
#: "we assumed 4 crews" distinguishable from "the analyst told us 4 crews".
ASSUMPTIONS_HEADING = "Assumptions to be validated:"


def _compose_description(body: str, assumptions: List[str]) -> str:
    """Stitch the narrative and its assumptions into the final text.

    Done here rather than in the prompt so the section cannot go missing: the analyst is
    accepting these assumptions by confirming the draft, and one that never made it onto
    the page is one nobody agreed to.
    """
    body = (body or "").strip()
    listed = [a.strip() for a in assumptions if a and a.strip()]
    # Idempotent: the fallback paths re-compose text that was already composed on an
    # earlier turn, and a second heading would read as a second set of assumptions.
    if not listed or ASSUMPTIONS_HEADING in body:
        return body
    bullets = "\n".join(f"- {a}" for a in listed)
    return f"{body}\n\n{ASSUMPTIONS_HEADING}\n{bullets}"


def _with_defer(options: List[str], multi_select: bool) -> List[str]:
    """Append the escape hatch unless the model already offered one.

    Applied to multi-select too: declining has to be available on every question, or the
    analyst's only way past one they cannot answer is to invent an answer.
    """
    lowered = [o.lower() for o in options]
    if any("not sure" in o or "don't know" in o or "you decide" in o for o in lowered):
        return list(options)
    return [*options, DEFER_OPTION]


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def understand_node(state: ChatState) -> ChatState:
    """Read the analyst's reply, then decide what — if anything — to ask next."""
    message = (state.get("user_message") or "").strip()
    facts = dict(state.get("facts") or {})
    assumptions = list(state.get("assumptions") or [])
    already = list(state.get("asked_questions") or [])
    asked_topics = list(state.get("asked_topics") or [])
    asked = state.get("questions_asked", 0)
    budget = state.get("question_budget")

    if asked >= MAX_QUESTIONS:
        return {"phase": "drafting", "facts": facts, "assumptions": assumptions}

    # The escape hatch is settled in code before the model sees the turn: the analyst told
    # us to assume, so the topic is closed whatever the model would otherwise make of it.
    if message == DEFER_OPTION and state.get("last_question"):
        assumptions.append(f"Not specified by the analyst: {state['last_question']}")

    # Listed as settled rather than merely "already asked". A question the analyst waved
    # through leaves its gap objectively open, so a model told only that it was asked will
    # reach for it again as the most material thing left; told that it is closed and how,
    # it moves on.
    raised_block = "\n".join(
        f"  - [{t or 'unnamed'}] {q}" for t, q in zip([*asked_topics, ""] , already)
    ) or "  (nothing yet)"
    topics_block = ", ".join(asked_topics) or "(none)"
    declined_block = "\n".join(f"  - {a}" for a in assumptions) or "  (none)"
    budget_line = (
        f"This brief was rated as earning {budget} question(s) in total; {max(0, budget - asked)} remain. "
        "Ask one now unless nothing is left worth asking."
        if budget is not None
        else "This is your first assessment — classify the opening brief's specificity."
    )

    prompt = f"""{_ROLE}

{_QUESTION_POLICY}

=== CONVERSATION SO FAR ===
{_format_history(state.get("history") or [])}

=== FACTS ALREADY CAPTURED ===
{_format_facts(facts)}

=== TOPICS ALREADY PUT TO THE ANALYST — every one is CLOSED ===
{raised_block}

Topic names already used: {topics_block}

=== TOPICS THEY DECLINED, NOW SETTLED BY ASSUMPTION — do not revisit ===
{declined_block}

Your `topic` must be a name that is NOT in the used list, and the question behind it must
be genuinely different — not one of the above reworded. If everything material is covered
by those lists plus the facts, leave the question empty.

=== BUDGET ===
Questions asked so far: {asked}. Ceiling: {MAX_QUESTIONS}.
{budget_line}

Their latest message:
  "{message}"

First extract what they actually stated in that message, preserving every number exactly
as written — those numbers anchor all downstream quantity estimation, and one that gets
dropped or rounded has to be guessed later. Do not infer, and do not record your own
assumptions as facts.

Then classify how specific their OPENING brief was, list what remains unknown with the
default you would assume for each, and ask ONE question resolving the most material gap
not yet raised.

Write the question the way a colleague would say it out loud — short, specific to this
activity, no jargon, no numbering, no preamble. ALWAYS offer 3 to 5 concrete options, even
when the answer is a number: give realistic ranges or typical values. The analyst can
always type something else instead, but they should never be forced to.
"""
    structured = _parse_llm.with_structured_output(_Understanding)
    try:
        result = structured.invoke(prompt)

        # A topic the analyst waved through stays objectively open, so the model's first
        # pick is often one it has already asked — it is the most material thing left.
        # Naming the collision and asking again recovers a real question most of the time;
        # treating the first repeat as "nothing left to ask" instead ended six-question
        # interviews after two, with geography and scope never raised.
        remaining = (budget if budget is not None else MAX_QUESTIONS) - asked
        if (result.topic or "").strip().lower() in asked_topics and remaining > 0:
            result = structured.invoke(
                prompt
                + f"\n\n[CORRECTION: '{result.topic}' has already been asked and is closed. "
                f"Topics already used: {topics_block}. Pick a genuinely different topic that "
                "is material to the cost, or leave the question empty if there truly is none.]"
            )
    except Exception:
        # Never lose the turn: keep the raw text so nothing the analyst said is dropped,
        # and draft from what we have rather than stalling them.
        if message and message != DEFER_OPTION:
            facts.setdefault("additional_detail", message)
        return {"phase": "drafting", "facts": facts, "assumptions": assumptions}

    for item in result.facts or []:
        slot = (item.slot or "").strip()
        value = (item.value or "").strip()
        if slot and value:
            facts[slot] = value

    if result.deferred and state.get("last_question") and message != DEFER_OPTION:
        assumptions.append(f"Not specified by the analyst: {state['last_question']}")

    # Fixed on the first assessment from the opening brief, and carried thereafter.
    # Re-deriving it every turn lets it drift as facts accumulate, and an interview whose
    # length keeps being renegotiated mid-way is what makes these feel interminable.
    if budget is None:
        budget = min(BUDGET_BY_SPECIFICITY.get(result.brief_specificity, 4), MAX_QUESTIONS)

    # Deciding not to ask about something is only half the job — the default that made the
    # question unnecessary has to survive into the description, or the analyst gets a vague
    # draft and the planner gets nothing to work from. These are that record. Recomputed
    # whole each turn, so a gap the analyst has since answered simply stops appearing.
    gap_defaults = [
        f"{gap.unknown} — assumed: {gap.default_assumption}"
        for gap in (result.gaps or [])
        if (gap.default_assumption or "").strip()
    ]

    common = {
        "facts": facts,
        "assumptions": assumptions,
        "question_budget": budget,
        "gap_defaults": gap_defaults,
    }

    # The budget is the whole stopping rule, alongside the model running out of things
    # worth asking. Filtering additionally on gap impact was tried and vetoed every
    # question: on a one-line brief the model duly listed fifteen gaps and rated every one
    # of them merely 'moderate', so a budget of five bought an interview of zero.
    #
    # A repeat counts as running out: if the best thing the model can produce is something
    # already put to the analyst, there is nothing further worth their time, and drafting
    # now is better than spending the rest of the budget rewording one question.
    # Repeats are caught on the model's own name for the topic rather than on the wording.
    # Comparing question text was tried and was wrong in both directions at once: it let
    # "how long will each session last" through against "what is the expected duration of
    # each session", while cutting a six-question interview off after one on a pair that
    # merely shared some nouns. A topic slug is the thing actually being compared.
    topic = (result.topic or "").strip().lower()
    if asked >= budget:
        stop_reason = "budget_spent"
    elif not result.question:
        stop_reason = "nothing_left_to_ask"
    elif topic and topic in asked_topics:
        stop_reason = "would_repeat_earlier_question"
    else:
        stop_reason = ""

    if stop_reason:
        return {**common, "phase": "drafting", "stop_reason": stop_reason}

    options = _with_defer(list(result.options or []), result.multi_select)
    return {
        **common,
        "phase": "questioning",
        "questions_asked": asked + 1,
        "asked_questions": [*already, result.question],
        "asked_topics": [*asked_topics, topic] if topic else asked_topics,
        "bot_message": result.question,
        "options": options,
        "multi_select": bool(result.multi_select),
        "last_question": result.question,
        "done": False,
    }


def draft_node(state: ChatState) -> ChatState:
    """Write the description and hand it to the analyst to confirm."""
    facts = state.get("facts") or {}
    prior_assumptions = state.get("assumptions") or []
    gap_defaults = state.get("gap_defaults") or []
    edit_request = (state.get("edit_request") or "").strip()
    previous = (state.get("description") or "").strip()

    revision_block = ""
    if edit_request and previous:
        revision_block = f"""
You are REVISING a description the analyst has already seen:

--- current description ---
{previous}
--- end ---

They asked for this change:
  "{edit_request}"

Apply it. Leave everything else they did not object to intact.
"""

    prompt = f"""{_ROLE}

Write the activity description that will be fed into the cost estimator.

=== CONVERSATION ===
{_format_history(state.get("history") or [], limit=20)}

=== FACTS THE ANALYST STATED (authoritative) ===
{_format_facts(facts)}

=== GAPS YOU CHOSE NOT TO ASK ABOUT, AND THE DEFAULT YOU SETTLED ON ===
{chr(10).join(f"  - {a}" for a in [*gap_defaults, *prior_assumptions]) or "  (none)"}
{revision_block}
Requirements:
- Two or three paragraphs of plain business English describing what is being done, at
  what scale, over what period, where, and what is delivered at the end.
- PRESERVE EVERY NUMBER the analyst gave, verbatim. Never round one, never soften it to
  "several" or "a number of". These numbers drive quantity estimation downstream, and one
  that goes missing has to be guessed.
- Carry EVERY gap listed above into the assumptions section, and commit to the specific
  default named there. Add any further assumption the description relies on. You chose not
  to ask about these, so writing them down is the only thing standing between the estimator
  and a number nobody can trace — a draft that assumes nothing is worse than one that
  assumes wrongly, because a stated assumption can be corrected in seconds.
- Each assumption must be a real number or a real choice, never a hedge like "as required"
  or "to be confirmed".
- Anything not stated by the analyst goes in the assumptions field and NOWHERE else —
  never write an assumption into the body as though they said it.
- The body itself carries no assumptions heading or list; it is stitched on afterwards.
- No preamble, no sign-off, no markdown headings.
"""
    try:
        result = _think_llm.with_structured_output(_Description).invoke(prompt)
        body = (result.activity_description or "").strip()
        assumptions = [a.strip() for a in (result.assumptions or []) if a and a.strip()]
    except Exception:
        body = previous or "\n".join(f"{k.replace('_', ' ')}: {v}" for k, v in facts.items())
        assumptions = list(prior_assumptions)

    description = _compose_description(body, assumptions)

    return {
        "phase": "review",
        "description": description,
        "assumptions": assumptions,
        "edit_request": "",
        "bot_message": (
            "Here's the description I'd use for the estimate:\n\n"
            f"{description}\n\n"
            "Does this look right? Confirm it and I'll drop it into the activity field, "
            "or tell me what to change."
        ),
        "options": list(REVIEW_OPTIONS),
        "multi_select": False,
        "done": False,
    }


def review_node(state: ChatState) -> ChatState:
    """Classify the analyst's verdict on the draft."""
    message = (state.get("user_message") or "").strip()
    lowered = message.lower().strip(" .!")

    # Fast paths, no LLM: the two chips and the obvious one-word replies.
    if message == REVIEW_OPTIONS[0] or lowered in {
        "yes", "yep", "ok", "okay", "confirm", "confirmed", "submit", "proceed",
        "good", "looks good", "perfect", "go ahead", "use it",
    }:
        return {"phase": "done", "done": True, "bot_message": "", "options": []}

    if message == REVIEW_OPTIONS[1]:
        return {
            "phase": "review",
            "done": False,
            "bot_message": "What would you like to change?",
            "options": [],
            "multi_select": False,
        }

    prompt = f"""{_ROLE}

The analyst is reviewing this draft description:

--- draft ---
{state.get("description") or ""}
--- end ---

They replied:
  "{message}"

Decide what they want:
- confirm: they accept it as it stands.
- edit: they told you what to change and you can apply it without asking anything.
- clarify: they want a change but you need a fact from them first to make it.

Prefer 'edit' over 'clarify' whenever the change is applicable from what they just said —
asking again when you already have enough to act wastes their time.
"""
    try:
        verdict = _parse_llm.with_structured_output(_ReviewIntent).invoke(prompt)
    except Exception:
        verdict = _ReviewIntent(intent="edit", edit_request=message)

    if verdict.intent == "confirm":
        return {"phase": "done", "done": True, "bot_message": "", "options": []}

    if verdict.intent == "clarify":
        return {"phase": "clarifying", "edit_request": verdict.edit_request or message}

    return {"phase": "drafting", "edit_request": verdict.edit_request or message}


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def _entry(state: ChatState) -> str:
    return "review" if state.get("phase") == "review" else "understand"


def _after_understand(state: ChatState) -> str:
    return "draft" if state.get("phase") == "drafting" else "end"


def _after_review(state: ChatState) -> str:
    phase = state.get("phase")
    if phase == "drafting":
        return "draft"
    if phase == "clarifying":
        return "understand"
    return "end"


def _build_graph():
    builder = StateGraph(ChatState)
    builder.add_node("understand", understand_node)
    builder.add_node("draft", draft_node)
    builder.add_node("review", review_node)

    builder.add_conditional_edges(START, _entry, {"understand": "understand", "review": "review"})
    builder.add_conditional_edges("understand", _after_understand, {"draft": "draft", "end": END})
    builder.add_edge("draft", END)
    builder.add_conditional_edges(
        "review", _after_review, {"draft": "draft", "understand": "understand", "end": END}
    )
    return builder.compile()


GRAPH = _build_graph()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

OPENING_MESSAGE = (
    "Hi — I'll help you turn an activity into a description the estimator can cost.\n\n"
    "**Would you describe the project or activity you need estimated?**\n\n"
    "Whatever you have is fine — a sentence or a full brief. I'll only ask follow-ups "
    "where a wrong guess would actually change the cost, and I'll state anything I assume."
)


def new_session() -> ChatState:
    """A fresh conversation state, ready for the first user message."""
    return {
        "history": [],
        "facts": {},
        "assumptions": [],
        "gap_defaults": [],
        "asked_questions": [],
        "asked_topics": [],
        "questions_asked": 0,
        "question_budget": None,
        "phase": "intake",
        "description": "",
        "edit_request": "",
        "last_question": "",
        "bot_message": "",
        "options": [],
        "multi_select": False,
        "done": False,
    }


def run_turn(
    state: ChatState,
    user_message: str,
    selected_options: Optional[List[str]] = None,
) -> ChatState:
    """Advance the conversation by one user message and return the new state.

    The returned state carries this turn's reply in ``bot_message`` / ``options`` and is
    what the caller should persist for the next call.
    """
    history = list(state.get("history") or [])
    history.append({"role": "user", "content": user_message})

    turn_input: ChatState = {
        **state,
        "user_message": user_message,
        "selected_options": selected_options or [],
        "history": history,
        "bot_message": "",
        "options": [],
        "multi_select": False,
    }

    result = GRAPH.invoke(turn_input)

    if result.get("bot_message"):
        result["history"] = [*result.get("history", []), {"role": "assistant", "content": result["bot_message"]}]
    return result


def activity_facts(state: ChatState) -> Dict[str, str]:
    """The user-stated facts, for the resource planner. Assumptions are excluded by
    design — downstream prompts label this block authoritative, and an assumption that
    arrives under that label stops being visible as an assumption."""
    return {k: v for k, v in (state.get("facts") or {}).items() if v}


# ---------------------------------------------------------------------------
# CLI (local testing)
# ---------------------------------------------------------------------------

def _cli() -> None:  # pragma: no cover
    state = new_session()
    print(OPENING_MESSAGE.replace("**", ""))
    while True:
        try:
            message = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not message:
            continue

        state = run_turn(state, message)

        if state.get("done"):
            print("\n=== FINAL DESCRIPTION ===\n")
            print(state.get("description"))
            print("\n=== FACTS ===")
            for key, value in activity_facts(state).items():
                print(f"  {key}: {value}")
            return

        print(f"\n{state.get('bot_message', '').replace('**', '')}")
        for index, option in enumerate(state.get("options") or [], 1):
            print(f"   [{index}] {option}")


if __name__ == "__main__":  # pragma: no cover
    _cli()
