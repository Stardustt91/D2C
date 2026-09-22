"""
The interview, as a LangGraph state machine.

WHY A GRAPH AND NOT A SCRIPT
----------------------------
The version this replaces handed the model an eight-step workflow and let it run the whole
conversation itself. Every analyst got all eight steps, in order, whatever they were buying.
Asked to cost a celebrity endorsement, it would dutifully work out how many FTE the campaign
needed — which the Cost Driver Library names as the commonest failure in this domain:
"running a full FTE build-up on a celebrity endorsement produces a confident, meaningless
number."

So the shape of the interview is now a routing decision, taken once, from a classification:

    START ─→ classify ─→ scope ─┬─→ labour ───────┐
                                ├─→ deliverable ──┤
                                ├─→ transaction ──┼─→ commercials ─→ write ─→ END
                                ├─→ rights ───────┤
                                └─→ passthrough ──┘

    (resume) ─→ review ─→ revise | write | END

Exactly one of the five middle nodes runs — or two, when a deal genuinely mixes spines, in
which case the second is interviewed on roughly half the budget rather than in full. A
rights deal never sees the staffing questions. A labour service never sees the five price
multipliers. That is the whole point, and it is the difference between an interview of five
exchanges and one of thirty.

The second kind of skipping is inside each node. A stage reports itself finished when its
essentials are answered or explicitly defaulted, so a brief that already fixes scale, period
and geography passes through ``scope`` without a single question. Both kinds together are
what "it depends on the user's answer" means here.

ONE TURN IN, ONE TURN OUT
-------------------------
The graph is a single-turn transducer: one invocation consumes one user message and returns
the next bot message plus the updated state, which the caller persists. Several nodes may
run within that one turn — a stage finishing hands straight on to the next, and their
replies are joined by the ``bot_message`` reducer — so the analyst reads "Got that. Now,
about where the work happens…" rather than a dead acknowledgement followed by a silence they
have to break.

There is deliberately no checkpointer and no ``interrupt()``. The state is the memory: plain
dicts and lists, serialisable, written to the session store between turns and read back by
whichever worker gets the next request. That keeps the graph directly testable and
insensitive to langgraph's version-to-version interrupt semantics.

Nodes return only what they changed. With a reducer on ``bot_message`` a node that returned
the whole state would feed its own stale reply back through the join.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from . import knowledge as kb
from .agendas import AGENDAS, BENCHMARKS, SPINE_AGENDAS, planned_stages
from .llms import STRUCTURED_METHOD, conversation_llm
from .state import IntakeState, format_facts, format_history, merge_facts, today_line
from .writer import review_node, revise_node, write_node

#: The Skill Specification caps a turn at four questions. Three is the working ceiling here
#: and two the norm: this interview asks fewer, better-targeted questions than the workflow
#: those prompts were written for, because classification has already removed the ones that
#: do not apply. Four short questions in one message also reads as a form, and the thing
#: being replaced failed partly for feeling like one.
MAX_QUESTIONS_PER_TURN = 3

#: Hard ceiling across the whole interview, independent of the per-stage budgets. Reached
#: only if every stage spends its full allowance, which a real conversation does not.
MAX_QUESTIONS = 18


# ---------------------------------------------------------------------------
# Structured output
# ---------------------------------------------------------------------------

class Extracted(BaseModel):
    """One fact, as the model reports it."""

    key: str = Field(
        description="snake_case canonical name for what this fact is: 'site_count', "
        "'service_window', 'contract_term_months', 'exclusivity'. Reuse an existing key "
        "exactly when correcting or refining something already captured — a new spelling of "
        "the same fact shows the analyst the same row twice."
    )
    label: str = Field(description="Short human-readable name for the facts panel, e.g. 'Site count'.")
    value: str = Field(
        description="The value. Preserve every number exactly as the analyst wrote it, with "
        "its unit. Never round one and never soften it to 'several'."
    )
    unit: str = Field(default="", description="Unit, where one applies and is not already in the value.")
    source: Literal["user", "default", "inferred"] = Field(
        default="user",
        description="user = they stated it, including where you have only restated it or "
        "converted its units ('3 year contract' recorded as 36 months is still theirs). "
        "default = they could not answer and accepted, or you applied, a stated default. "
        "inferred = you concluded it from what they said rather than being told. Be honest: "
        "this decides whether the description states it as fact or files it as an assumption "
        "to be validated.",
    )


class StageTurn(BaseModel):
    """One turn inside one stage of the interview."""

    facts: List[Extracted] = Field(
        default_factory=list,
        description="Everything learned from the analyst's latest message, plus any default "
        "you are applying because they could not answer.",
    )
    topics_raised: List[str] = Field(
        default_factory=list,
        description="snake_case name for each topic you are asking about in this reply. "
        "Name the subject, not the wording, so two phrasings of the same question get the "
        "same name. Empty when you are not asking anything.",
    )
    stage_complete: bool = Field(
        description="True when this stage's essentials are answered or explicitly "
        "defaulted and nothing else material to THIS service is open. Also true if the "
        "analyst asks to skip ahead."
    )
    reply: str = Field(
        description="What to say. When stage_complete is false: acknowledge what they just "
        "told you in a clause, then ask. When stage_complete is true: ONE short sentence "
        "acknowledging, with no question and no summary — the next stage speaks immediately "
        "after you, and two greetings in one message reads as a stutter."
    )
    suggestions: List[str] = Field(
        default_factory=list,
        description="Three to five short, concrete, clickable answers to the question you "
        "just asked, specific to this service — realistic values or ranges, even when the "
        "answer is a number. Empty when you asked nothing. Do not offer a 'not sure' "
        "choice; the interface adds one.",
    )


class Classification(BaseModel):
    """Stage 0. The routing decision the rest of the interview hangs on."""

    confident: bool = Field(
        description="True when you can place this service in the tree well enough to commit "
        "to a spine. False ONLY when the brief is genuinely ambiguous between subcategories "
        "carrying DIFFERENT spines, or names something with no home in the tree. Ambiguity "
        "between two subcategories that share a spine is not a reason to ask — the interview "
        "would be identical either way."
    )
    category_code: str = Field(default="", description="The L2 code from the tree, e.g. '2.3'. Empty if nothing fits.")
    category_name: str = Field(default="", description="The L2 subcategory name.")
    family_code: str = Field(
        default="",
        description="The Cost Driver Library family, e.g. 'C6'. Where the category has no "
        "family of its own, give the NEAREST family rather than inventing drivers, and say "
        "so in your reply.",
    )
    spine: str = Field(
        default="",
        description="labour | deliverable | transaction | rights | passthrough. Used only "
        "as a fallback where neither the category nor the family carries one.",
    )
    secondary_spines: List[str] = Field(
        default_factory=list,
        description="Only for a deal that genuinely mixes spines — a sponsorship with its "
        "own activation crew, an agency retainer with pass-through media buying. Name the "
        "SECOND block's spine. Leave empty for the overwhelmingly common single-spine deal: "
        "a wrong entry here adds a whole stage of questions nobody needed.",
    )
    commercial_model: str = Field(
        default="",
        description="T&M, fixed price, unit rate, managed service, or staff augmentation, "
        "where the brief says or clearly implies it. Empty otherwise.",
    )
    purpose: str = Field(
        default="",
        description="should-cost, budget, bid evaluation, make-vs-buy, rate card, or "
        "renewal, where stated. Empty otherwise.",
    )
    facts: List[Extracted] = Field(default_factory=list, description="Facts stated in the brief.")
    reply: str = Field(
        description="When confident: one or two sentences naming the classification and the "
        "spine in plain words, inviting correction, asking nothing — the next stage asks the "
        "first question immediately after you. When not confident: the one question that "
        "would settle it."
    )
    suggestions: List[str] = Field(
        default_factory=list, description="Candidate subcategories, when asking. Empty when confident."
    )


# ---------------------------------------------------------------------------
# Prompt fragments
# ---------------------------------------------------------------------------

PERSONA = """
You are the intake analyst for the Design-to-Cost tool used by Supply Chain Management at
Vodafone Egypt. Someone brings you a service the business needs costed, and your job is to
understand it well enough that a downstream estimator can build a defensible bottom-up
should-cost model from your write-up alone. The estimator never sees this conversation.

How you talk:
- Like a competent colleague, not a form. Short sentences. Acknowledge what they said before
  asking the next thing, in a clause, not a paragraph.
- One line on why an input matters, before you ask for it. People answer better when they
  know what turns on the answer.
- UK spellings. No emoji, no hype, no "great question", no closing pep talk.
- Mirror their language. If they write in Arabic, reply in Arabic.
- If they volunteer three things at once, take all three and skip the questions you no
  longer need. If they say they do not know, give a concrete default, record it as a
  default, say what you assumed in half a sentence, and move on. Never stall on a missing
  input.

What you never ask:
Never ask what the service costs, what the budget is, or what a day rate ought to be.
Working that out is the entire purpose of the system this feeds, and an analyst who already
knew would not be here. You ask about STRUCTURE and QUANTITY: how much work, over what
period, where, on what coverage pattern, on what fee basis, under what licence terms, who
supplies what. If they volunteer a rate, a quote or a benchmark, record it gratefully — it
is evidence — but never make them produce one to get past a question.
""".strip()


def _classification_block(state: IntakeState) -> str:
    """What the interview already knows about what it is interviewing about."""
    spine = state.get("spine") or ""
    family = kb.family(state.get("family_code") or "")
    lines = [
        f"Service category: {state.get('category_code', '')} {state.get('category_name', '')}".strip(),
        f"Cost driver family: {state.get('family_code', '')} {state.get('family_name', '')}".strip(),
        f"Model spine: {kb.SPINE_NAMES.get(spine, spine)} — {kb.SPINE_DESCRIPTIONS.get(spine, '')}",
    ]
    if family:
        lines.append(f"Unit of measure: {family.unit}")
        lines.append(f"Cost drivers the library lists for this family:\n  {family.drivers}")
        if family.structure:
            lines.append(f"Typical cost structure: {family.structure}")
    if secondary := state.get("secondary_spines"):
        lines.append(
            "This deal mixes spines. Second block: "
            + ", ".join(kb.SPINE_NAMES.get(s, s) for s in secondary)
        )
    if bench := BENCHMARKS.get(spine):
        lines.append(f"Bands the estimator will validate against: {bench}")
    return "\n".join(line for line in lines if line.strip())


def _slug(text: str) -> str:
    return (text or "").strip().lower().replace(" ", "_")


# ---------------------------------------------------------------------------
# Stage 0 — classification
# ---------------------------------------------------------------------------

def classify_node(state: IntakeState) -> Dict[str, Any]:
    """Place the service in the tree, and with it choose the interview."""
    agenda = AGENDAS["classify"]
    asked = list(state.get("asked") or [])

    prompt = f"""{PERSONA}

{today_line()}

=== YOUR TASK THIS TURN ===
{agenda.brief}

=== THE LOCAL SERVICES CATEGORY TREE ===
Each line is an L2 subcategory: code, name, its L1 parent, the model spine it is costed on,
its Cost Driver Library family, and example service lines.

{kb.TAXONOMY_INDEX}

=== WHAT THE SPINES MEAN ===
{chr(10).join(f"- {kb.SPINE_NAMES[s]}: {d}" for s, d in kb.SPINE_DESCRIPTIONS.items())}

Choosing the spine is the single most consequential decision in this conversation, because
it decides which questions the analyst is asked for the rest of it. Getting it wrong is the
commonest failure in this domain: a full headcount build-up run over a celebrity endorsement
produces a confident number that means nothing.

=== EGYPT ===
{kb.EGYPT_NOTES}

=== CONVERSATION SO FAR ===
{format_history(state.get("history") or [])}

=== ALREADY CAPTURED ===
{format_facts(state.get("facts") or {})}

Topics already put to them: {", ".join(asked) or "(none)"}

=== THEIR LATEST MESSAGE ===
"{(state.get("user_message") or "").strip()}"

Classify it. Extract what they actually stated, preserving every number verbatim.

If you can place it, say so in one or two sentences — what you are treating it as, how it
will be costed, and that they should correct you if that is wrong — and ask nothing at all.
The next stage asks the first real question immediately after your sentence.
"""
    try:
        result = conversation_llm().with_structured_output(
            Classification, method=STRUCTURED_METHOD
        ).invoke(prompt)
    except Exception as exc:
        return _stalled(exc)

    facts, touched = merge_facts(state.get("facts") or {}, result.facts, stage="classify")

    if not result.confident:
        return {
            "facts": facts,
            "new_fact_keys": touched,
            "asked": [*asked, "service_classification"],
            "stage": "classify",
            "stage_label": agenda.label,
            "stage_questions": state.get("stage_questions", 0) + 1,
            "bot_message": result.reply,
            "suggestions": list(result.suggestions or []),
        }

    spine = kb.resolve_spine(result.category_code, result.family_code) or result.spine
    if spine not in SPINE_AGENDAS:
        spine = "labour"

    # A secondary spine doubles a stage, so it is taken only when it names a real second
    # block: not the primary spine repeated, and not a label that is not a spine at all.
    secondary = [
        s for s in dict.fromkeys(result.secondary_spines or [])
        if s in SPINE_AGENDAS and s != spine
    ][:1]

    family = kb.family(result.family_code)
    category = kb.category(result.category_code)

    return {
        "facts": facts,
        "new_fact_keys": touched,
        "category_code": result.category_code,
        "category_name": result.category_name or (category.l2 if category else ""),
        "family_code": (result.family_code or "").upper(),
        "family_name": family.name if family else "",
        "spine": spine,
        "secondary_spines": secondary,
        "spine_queue": [spine, *secondary],
        "commercial_model": result.commercial_model,
        "purpose": result.purpose,
        "stage": "scope",
        "stage_label": AGENDAS["scope"].label,
        "completed_stages": [*(state.get("completed_stages") or []), "classify"],
        "stage_questions": 0,
        "bot_message": result.reply,
        "suggestions": [],
    }


# ---------------------------------------------------------------------------
# Stages 1 to 3 — one node per agenda, all the same function
# ---------------------------------------------------------------------------

def _interview(state: IntakeState, stage: str) -> Dict[str, Any]:
    """Run one turn of one stage.

    Every stage below classification works identically — what differs is the agenda in the
    prompt and the budget it runs on. Building them from one function rather than five keeps
    the behaviour that matters (never re-ask; default rather than stall; finish early)
    defined once, while the graph still shows five distinct nodes.
    """
    agenda = AGENDAS[stage]
    asked = list(state.get("asked") or [])
    spent = state.get("stage_questions", 0)
    remaining = agenda.budget - spent

    # The budget is enforced here rather than asked of the model, which would negotiate with
    # it. The call still happens on the last exchange — the analyst's message has to be read
    # whatever else is true — but the stage ends afterwards regardless of what comes back,
    # and whatever is still open becomes a default the description states plainly.
    must_finish = remaining <= 1 or len(asked) >= MAX_QUESTIONS

    closing = (
        "\nTHIS IS YOUR LAST EXCHANGE IN THIS STAGE. Take what they said, apply a concrete "
        "default to anything still open and record it as a default, and finish the stage.\n"
        if must_finish
        else f"\nYou have {remaining} more exchange(s) in this stage.\n"
    )

    prompt = f"""{PERSONA}

{today_line()}

=== WHAT THIS SERVICE IS ===
{_classification_block(state)}

=== EGYPT ===
{kb.EGYPT_NOTES}

=== THE STAGE YOU ARE IN: {agenda.label} ===
{agenda.brief}

This stage may not finish while any of these is neither answered nor explicitly defaulted:
{", ".join(agenda.essentials)}

=== CONVERSATION SO FAR ===
{format_history(state.get("history") or [])}

=== ALREADY CAPTURED — never ask for any of this again ===
{format_facts(state.get("facts") or {})}

=== TOPICS ALREADY PUT TO THEM — every one is CLOSED, in any rewording ===
{", ".join(asked) or "(none)"}
A topic they declined is settled by the default recorded against it. Asking again in fresh
words reads as not having listened — and that includes smuggling a closed question into a
new one under a different name. If they did not answer it, that is your answer: default it.

=== HOW MUCH ROOM IS LEFT ==={closing}
You may ask at most {MAX_QUESTIONS_PER_TURN} questions in this reply. Prefer two.

=== WHEN TO FINISH ===
Set stage_complete as soon as the essentials above are settled and the next thing you would
ask is something an experienced estimator could reasonably default. That is usually after
one or two exchanges, not when the agenda runs out. The agenda lists what could matter for
this KIND of service; most of it will not matter for this one. Every extra question is one
the analyst did not need to answer, and the interview this replaces was abandoned for asking
them.

A blanket instruction — "use the defaults", "whatever is typical", "you decide", "skip the
rest" — applies to EVERYTHING still open in this stage, not only to the question you just
asked. Record the defaults with real values and finish the stage.

=== THEIR LATEST MESSAGE ===
"{(state.get("user_message") or "").strip()}"

Take what they said, then either ask what is still material or declare the stage finished.
Anything you choose not to ask about, record as a default with a real value, so the
description states it rather than leaving the estimator to guess.
"""
    try:
        result = conversation_llm().with_structured_output(
            StageTurn, method=STRUCTURED_METHOD
        ).invoke(prompt)
    except Exception as exc:
        return _stalled(exc)

    facts, touched = merge_facts(state.get("facts") or {}, result.facts, stage=stage)

    if result.stage_complete or must_finish:
        reply = result.reply

        # On a forced finish the model sometimes asks anyway. Its reply then carries a
        # question the stage is about to close over, and the next node asks its own question
        # straight afterwards — two questions in one message, one of which will never be
        # answered. ``topics_raised`` is the model's own admission that it asked.
        if must_finish and result.topics_raised and not result.stage_complete:
            reply = ""

        # A stage that never asked anything has nothing to acknowledge: the message it just
        # read was answering the PREVIOUS stage's question, which that stage already replied
        # to. Without this, "use the defaults for the rest" closes three stages at once and
        # the analyst gets three stacked variations on "Understood; I have applied the
        # defaults", which reads as a stutter rather than as progress.
        if spent == 0:
            reply = ""

        return _advance(state, stage, reply=reply, facts=facts, touched=touched)

    raised = [s for t in (result.topics_raised or []) if (s := _slug(t))]
    return {
        "facts": facts,
        "new_fact_keys": touched,
        "asked": [*asked, *(t for t in raised if t not in asked)],
        "stage": stage,
        "stage_label": agenda.label,
        "stage_questions": spent + 1,
        "bot_message": result.reply,
        "suggestions": list(result.suggestions or []),
    }


def _advance(state: IntakeState, stage: str, *, reply: str, facts, touched) -> Dict[str, Any]:
    """Close the current stage and hand the turn to whatever comes next.

    ``stage`` is set to the next node's name here rather than by the edge, because the edge
    reads it back: the router and the state stay one fact rather than two that can disagree.
    """
    queue = list(state.get("spine_queue") or [])
    if stage in SPINE_AGENDAS and queue and queue[0] == stage:
        queue = queue[1:]

    nxt = "write" if stage == "commercials" else (queue[0] if queue else "commercials")

    # A secondary spine is a second block of one deal, not a second interview: it opens with
    # part of its budget already spent, because the deal's centre of gravity was covered by
    # the primary. Starting it at zero would double the length of a mixed-deal intake.
    start = 0
    if nxt in SPINE_AGENDAS and nxt != state.get("spine"):
        budget = AGENDAS[nxt].budget
        start = budget - max(1, budget // 2)

    return {
        "facts": facts,
        "new_fact_keys": touched,
        "spine_queue": queue,
        "stage": nxt,
        "stage_label": AGENDAS[nxt].label if nxt in AGENDAS else "Writing the description",
        "completed_stages": [*(state.get("completed_stages") or []), stage],
        "stage_questions": start,
        "bot_message": reply,
        "suggestions": [],
    }


def _stalled(exc: Exception) -> Dict[str, Any]:
    """Keep the turn rather than losing it.

    The analyst's message is already in history, so the model sees it when they retry; they
    do not have to type it again.
    """
    return {
        "bot_message": f"I could not process that ({type(exc).__name__}). Send it again.",
        "suggestions": [],
    }


def _make_node(stage: str):
    def node(state: IntakeState) -> Dict[str, Any]:
        return _interview(state, stage)

    node.__name__ = f"{stage}_node"
    node.__doc__ = f"One turn of the {AGENDAS[stage].label!r} stage."
    return node


def _make_router(stage: str):
    """After a stage node: hand on if it finished, otherwise wait for the analyst.

    A node that asked a question left ``stage`` pointing at itself; a node that finished
    moved it on. So "did it finish" and "where does the turn go" are one question, asked
    once, of the state rather than of a separate flag that could disagree with it.
    """

    def route(state: IntakeState) -> str:
        nxt = state.get("stage") or stage
        return "end" if nxt == stage else nxt

    route.__name__ = f"after_{stage}"
    return route


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

#: Every node the entry router may dispatch to, which is every node a persisted session can
#: legitimately resume into.
_ENTRY_NODES = ("classify", "scope", *SPINE_AGENDAS, "commercials", "write", "review")

def _onward(stage: str) -> Dict[str, str]:
    """Where a finished stage may hand on to, and nowhere else.

    ``scope`` hands to whichever spine the classification chose, so it needs an edge to all
    five even though only one will ever be taken in a given conversation. A spine node hands
    to the second spine of a mixed deal or to ``commercials``, never to itself — a node that
    finished has moved the stage on, and a node that did not has ended the turn. Listing
    only the reachable targets keeps the compiled graph readable, which matters because the
    routing is the design.
    """
    if stage == "commercials":
        targets = ["write"]
    elif stage == "scope":
        targets = list(SPINE_AGENDAS) + ["commercials"]
    else:
        targets = [s for s in SPINE_AGENDAS if s != stage] + ["commercials"]
    return {name: name for name in targets}


def _entry(state: IntakeState) -> str:
    """Which node owns this turn."""
    if state.get("phase") == "review":
        return "review"
    stage = state.get("stage") or "classify"
    return stage if stage in _ENTRY_NODES else "classify"


def _after_classify(state: IntakeState) -> str:
    return "end" if state.get("stage") == "classify" else "scope"


def _after_review(state: IntakeState) -> str:
    return {"revise": "revise", "write": "write"}.get(state.get("stage") or "", "end")


def build_graph():
    builder = StateGraph(IntakeState)

    builder.add_node("classify", classify_node)
    for stage in ("scope", *SPINE_AGENDAS, "commercials"):
        builder.add_node(stage, _make_node(stage))
    builder.add_node("write", write_node)
    builder.add_node("review", review_node)
    builder.add_node("revise", revise_node)

    builder.add_conditional_edges(START, _entry, {name: name for name in _ENTRY_NODES})
    builder.add_conditional_edges("classify", _after_classify, {"scope": "scope", "end": END})

    for stage in ("scope", *SPINE_AGENDAS, "commercials"):
        builder.add_conditional_edges(stage, _make_router(stage), {**_onward(stage), "end": END})

    builder.add_edge("write", END)
    builder.add_edge("revise", END)
    builder.add_conditional_edges(
        "review", _after_review, {"revise": "revise", "write": "write", "end": END}
    )

    return builder.compile()


GRAPH = build_graph()


def stage_plan(state: IntakeState) -> List[str]:
    """The stages this conversation will run, for the progress indicator.

    Computed from the spine, so the analyst is shown the length of the interview they are
    actually getting rather than "step 2 of 8" when six of the eight will never run.
    """
    return planned_stages(state.get("spine") or "", tuple(state.get("secondary_spines") or ()))
