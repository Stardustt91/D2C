"""
Activity intake — the conversation that produces the activity description.

    from activity_intake import new_session, run_turn, turn_view

    state = new_session()
    state = run_turn(state, "we need 24x7 NOC monitoring for 50 network elements")
    view = turn_view(state)      # everything the UI renders this turn
    ...
    state["description"]         # the output, once view["done"] is True

Three functions and a state dict. ``state`` is plain JSON — dicts, lists, strings — so a
session is written to the session store between turns and read back by whichever worker
gets the next request. Nothing in it is alive.

WHAT COMES BACK EVERY TURN
--------------------------
:func:`turn_view` is the whole interface. Beyond the reply it carries:

    facts          every fact so far, grouped by the stage that captured it, each tagged
                   with whether the analyst stated it or the interview defaulted it
    new_fact_keys  what moved this turn, so the panel can show the analyst their last
                   sentence landing rather than making them re-read twenty rows
    classification the category, the cost driver family and the model spine — the routing
                   decision the rest of the interview hangs on, and the one thing worth
                   correcting early because correcting it late means starting over
    progress       the stages this conversation will actually run, given that spine

WHERE THE MEMORY LIVES
----------------------
Three places, and they degrade differently.

``history`` is the transcript and answers "what did I just tell you?". It is replayed to the
model each turn but window-trimmed, because a long interview should not grow its own latency
turn by turn.

``facts`` is the extracted understanding, and it is what survives that trimming. A fact
outlives the message that produced it, gets corrected in place when the analyst changes
their mind, and is the only thing the description is written from.

``asked`` is the ledger of topics already put to the analyst. It exists because the
alternative failed: a model that knows a topic is unanswered will reach for it again as the
most material thing left, and being asked the same question in fresh wording is what makes
these conversations feel like they are not listening.
"""

from __future__ import annotations

from typing import Any, Dict

from .agendas import AGENDAS
from .graph import GRAPH, stage_plan
from .knowledge import SPINE_NAMES
from .state import Fact, IntakeState, facts_panel
from .writer import REVIEW_OPTIONS

__all__ = [
    "OPENING_MESSAGE",
    "REVIEW_OPTIONS",
    "Fact",
    "IntakeState",
    "new_session",
    "run_turn",
    "turn_view",
    "activity_facts",
    "GRAPH",
]


OPENING_MESSAGE = (
    "Tell me about the service you need costed — a sentence or a full brief, whatever you "
    "have.\n\n"
    "I will work out what kind of service it is and ask only the questions that actually "
    "change its cost, so this is usually four or five exchanges rather than a "
    "questionnaire. Everything I pick up appears in the **Facts** panel as we go, and at "
    "the end I will write the description the estimator works from."
)

#: Ends the interview wherever it has got to. An analyst who has said everything they intend
#: to say by the second stage needs a way out that is not answering three more stages of
#: questions, and these phrases are never a plausible answer to anything the interview asks.
_WRAP_UP = {
    "submit", "submit it", "write it", "write it now", "write the description",
    "just write it", "that's enough", "thats enough", "enough", "go ahead and write it",
    "finish", "done asking", "no more questions", "skip the rest", "wrap up",
}


def _wants_to_finish(message: str, state: IntakeState) -> bool:
    """Whether to stop asking and write the description now.

    Requires a classification: jumping to the writer before the service has been placed
    produces a description with no spine, no unit of measure and no driver list, which is
    worse than the two more questions it saved.
    """
    if not state.get("spine") or state.get("phase") == "review":
        return False
    return message.strip().strip(" .!\"'").lower() in _WRAP_UP


def new_session() -> IntakeState:
    """A fresh conversation, ready for the analyst's first message."""
    return {
        "history": [],
        "facts": {},
        "asked": [],
        "turn": 0,
        "category_code": "",
        "category_name": "",
        "family_code": "",
        "family_name": "",
        "spine": "",
        "secondary_spines": [],
        "spine_queue": [],
        "commercial_model": "",
        "purpose": "",
        "stage": "classify",
        "stage_label": AGENDAS["classify"].label,
        "completed_stages": [],
        "stage_questions": 0,
        "phase": "interview",
        "description": "",
        "edit_request": "",
        "bot_message": OPENING_MESSAGE,
        "suggestions": [],
        "new_fact_keys": [],
        "done": False,
    }


def run_turn(state: IntakeState, user_message: str) -> IntakeState:
    """Advance the conversation by one message and return the new state.

    The returned state carries this turn's reply in ``bot_message`` and is what the caller
    persists for the next call. Several nodes may run inside one call — a stage that
    finishes hands straight on to the next — and their replies arrive joined.
    """
    message = (user_message or "").strip()
    history = [*(state.get("history") or []), {"role": "user", "content": message}]

    turn_input: IntakeState = {
        **state,
        "user_message": message,
        "history": history,
        # Reset before the reducers run, so this turn's reply and highlights start empty
        # rather than joining onto the last turn's.
        "bot_message": "",
        "new_fact_keys": [],
        "suggestions": [],
    }

    if _wants_to_finish(message, state):
        turn_input["stage"] = "write"

    result = dict(GRAPH.invoke(turn_input))
    result["turn"] = state.get("turn", 0) + 1

    if reply := result.get("bot_message"):
        result["history"] = [*result.get("history", []), {"role": "assistant", "content": reply}]

    # Only ever an input; carrying it forward would have the next turn read it as new.
    result.pop("user_message", None)
    return result  # type: ignore[return-value]


def turn_view(state: IntakeState) -> Dict[str, Any]:
    """Everything the UI renders for this turn."""
    plan = stage_plan(state)
    stage = state.get("stage") or "classify"
    spine = state.get("spine") or ""

    return {
        "reply": state.get("bot_message") or "",
        "suggestions": list(state.get("suggestions") or []),
        "done": bool(state.get("done")),
        "phase": state.get("phase") or "interview",
        "description": state.get("description") or "",
        "facts": facts_panel(state.get("facts") or {}),
        "new_fact_keys": list(state.get("new_fact_keys") or []),
        "classification": {
            "category_code": state.get("category_code") or "",
            "category_name": state.get("category_name") or "",
            "family_code": state.get("family_code") or "",
            "family_name": state.get("family_name") or "",
            "spine": spine,
            "spine_name": SPINE_NAMES.get(spine, ""),
            "secondary_spines": list(state.get("secondary_spines") or []),
        },
        "progress": {
            "stage": stage,
            "stage_label": state.get("stage_label") or "",
            "completed": list(state.get("completed_stages") or []),
            "plan": plan,
            "position": plan.index(stage) + 1 if stage in plan else len(plan),
            "total": len(plan),
        },
    }


def activity_facts(state: IntakeState) -> Dict[str, str]:
    """The captured facts as flat ``key -> value``, for the downstream planner.

    The unit travels with the value, because the two are one fact: a productivity factor
    reaching the planner as "90" rather than "90 %" is a number it has to guess the meaning
    of, and it will guess.

    Provenance is dropped here on purpose: this is the block downstream prompts label
    authoritative, and a default arriving under that label stops being visible as a default.
    Defaults reach the estimator through the description's assumption section instead, where
    they are marked as what they are. Use ``state["facts"]`` when you want the full record.
    """
    return {
        key: f"{fact['value']} {fact.get('unit') or ''}".strip()
        for key, fact in (state.get("facts") or {}).items()
        if fact.get("value")
    }
