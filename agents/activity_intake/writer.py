"""
The activity description, and the conversation the analyst has with it.

This is the output of the whole exercise. Everything downstream — cost components,
quantities, the estimate itself — is built from this text and never sees the interview that
produced it, so anything left out here is lost and anything invented here is costed.

WHAT THE MODEL WRITES AND WHAT THE CODE WRITES
----------------------------------------------
The model writes the narrative and the assumption list. Everything else is composed in
:func:`compose` from the classification: the spine, the unit of measure, the formula the
estimator will apply, and the benchmark bands it should validate against. Those are facts
about the method rather than about this activity, they are already correct in
``knowledge.py`` and ``agendas.py``, and a model asked to restate them will eventually
restate one of them wrongly. Composing them in code also makes the assumption section
impossible to lose, which is the failure that matters: the analyst accepts the description,
and with it every default the interview applied on their behalf. A default that never made
it onto the page is one nobody agreed to.

THE FOUR THINGS THE ANALYST CAN DO WITH IT
------------------------------------------
    submit    accept it as it stands; the interview is over
    edit      "change the period to 24 months" — a targeted change, everything else intact
    rewrite   throw the prose away and write it again, optionally with a steer
    discuss   "why did you assume 90% productivity?" — answered without touching the text

Only the first three exist in most review loops, and the fourth is the one analysts
actually want. A description carrying twenty facts and eight assumptions raises questions
before it raises objections, and an interface whose only answers are "accept" or "change
it" turns every question into an edit nobody wanted.

``edit`` also extracts facts. An analyst correcting a description is usually telling you
something new — "make it 40 sites" is a fact, not a formatting note — and if the facts panel
does not move, the correction lives only in prose that the next rewrite will discard.
"""

from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field

from . import knowledge as kb
from .agendas import BENCHMARKS, SPINE_NOTES
from .llms import STRUCTURED_METHOD, conversation_llm, writer_llm
from .state import (
    IntakeState,
    covered_by,
    defaults_used,
    format_facts,
    format_history,
    merge_facts,
)

#: ``writer_llm()`` is the first draft and any full rewrite. It runs once, at the end, off
#: the critical path of the conversation, and everything downstream is built from what it
#: produces.
#:
#: ``conversation_llm()`` reads the analyst's verdict, answers their questions, and applies
#: targeted edits. The edit sits on the writer tier in no sensible design: it is a local
#: change to text that already exists, the analyst is watching the spinner, and the
#: reasoning deployment takes around twenty-five seconds to make it. A full rewrite still
#: goes to the writer, because that is a fresh draft rather than an amendment.
#:
#: Both are called rather than bound to a module-level name so that importing this file
#: needs no credentials; see the note in ``llms.py``.

#: Heading the assumptions are filed under. Downstream agents read the description as one
#: blob, so this marker is what keeps "we assumed 90% productivity" distinguishable from
#: "the analyst told us 90% productivity".
ASSUMPTIONS_HEADING = "Assumptions to be validated:"

#: Offered with every draft, so each of the four is one click rather than a sentence the
#: analyst has to compose.
REVIEW_OPTIONS = [
    "Submit this description",
    "I want to change something",
    "Rewrite it",
    "I have a question about it",
]


class _Draft(BaseModel):
    """The two halves the model is responsible for.

    Kept apart and stitched together afterwards. Asked for one field holding both the
    narrative and its assumptions, the model files the assumptions into the prose and writes
    a clean body — reasonable of it, and it leaves the analyst reading a draft that commits
    to specifics without saying which of them anyone actually stated.
    """

    narrative: str = Field(
        description="The body ONLY: flowing paragraphs of business English. No assumptions "
        "section, no headings, no bullet lists — those are added afterwards."
    )
    assumptions: List[str] = Field(
        default_factory=list,
        description="Every assumption the description relies on, each a complete sentence a "
        "reviewer can accept or correct on its own, each committing to a real number or a "
        "real choice rather than hedging with 'as required' or 'to be confirmed'.",
    )


class _ReviewIntent(BaseModel):
    """What the analyst wants done with the draft, and the answer if they only asked."""

    intent: Literal["submit", "edit", "rewrite", "discuss"] = Field(
        description="submit = accept it as it stands. edit = they told you what to change "
        "and you can apply it directly. rewrite = they want the whole thing written again, "
        "perhaps differently ('too long', 'start over', 'more detail on the geography'). "
        "discuss = they asked a question about it and want an answer, not a change. When "
        "they both ask and instruct, prefer edit."
    )
    edit_request: str = Field(
        default="", description="What they want changed, in their terms. Empty unless editing or rewriting."
    )
    answer: str = Field(
        default="",
        description="Your answer to their question, for 'discuss' only. Answer from the "
        "facts and the interview, cite which of the two a number came from, and say plainly "
        "when something was a default rather than something they told you. Two or three "
        "sentences. End by reminding them the description is still there to submit, change "
        "or rewrite.",
    )


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------

def compose(state: IntakeState, narrative: str, assumptions: List[str]) -> str:
    """Stitch the narrative, the modelling basis and the assumptions into the final text.

    Idempotent on the assumptions heading: the revise path re-composes text that was already
    composed on an earlier turn, and a second heading reads as a second set of assumptions.
    """
    body = (narrative or "").strip()
    spine = state.get("spine") or ""
    family = kb.family(state.get("family_code") or "")

    parts = [body]

    basis = [
        f"Modelling basis: {kb.SPINE_NAMES.get(spine, spine or 'unclassified')}.",
        f"Service category: {state.get('category_code', '')} {state.get('category_name', '')}".strip().rstrip(":"),
    ]
    if family:
        basis.append(f"Cost driver family: {family.code} {family.name}.")
        basis.append(f"Unit of measure: {family.unit}.")
        # Family-specific, where the bands below are only spine-specific. The Cost Driver
        # Library offers it as a sense check on the finished model rather than as an input:
        # a C6 build coming out at 20% labour has lost most of its headcount somewhere.
        if family.structure:
            basis.append(f"Typical cost structure for this family: {family.structure}.")
    if note := SPINE_NOTES.get(spine):
        basis.append(f"Build rule for the estimator: {note}")
    if bench := BENCHMARKS.get(spine):
        basis.append(f"Validate against: {bench}")
    parts.append("\n".join(b for b in basis if b.strip()))

    listed = [a.strip() for a in assumptions if a and a.strip()]
    if listed and ASSUMPTIONS_HEADING not in body:
        parts.append(ASSUMPTIONS_HEADING + "\n" + "\n".join(f"- {a}" for a in listed))

    return "\n\n".join(part for part in parts if part.strip())


def _as_assumption(fact) -> str:
    """One defaulted fact, written as a line of the assumption register."""
    value = f"{fact['value']} {fact.get('unit', '')}".strip()
    return f"{fact['label']}: {value} (applied as a default during intake; not stated)."


def _carry_defaults(facts, assumptions: List[str]) -> List[str]:
    """Make sure every default reaches the register, without saying any of them twice.

    The analyst accepts every default on this list by submitting the description, so a
    default that never made it onto the page is one nobody agreed to. The writer is asked to
    carry them and usually does, in its own words — this adds only the ones it dropped.
    """
    return [*assumptions, *(_as_assumption(f) for f in facts if not covered_by(f, assumptions))]


def _fallback(state: IntakeState) -> str:
    """Never send the analyst away empty-handed.

    A flat rendering of the facts is a worse description than the model would have written,
    but it is still every fact they gave, and it is recoverable by editing. Losing an
    interview to one failed call is not.
    """
    facts = state.get("facts") or {}
    lines = [f"{fact['label']}: {fact['value']}".strip() for fact in facts.values()]
    return compose(state, "\n".join(lines), [_as_assumption(f) for f in defaults_used(facts)])


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def write_node(state: IntakeState) -> IntakeState:
    """Write the description and hand it to the analyst."""
    facts = state.get("facts") or {}
    steer = (state.get("edit_request") or "").strip()
    previous = (state.get("description") or "").strip()

    steer_block = ""
    if steer:
        steer_block = f"""
This is a REWRITE. The analyst read the previous version and said:
  "{steer}"

--- previous version ---
{previous}
--- end ---

Write it again from the facts, applying what they asked for. Do not merely edit the text
above; they asked for it rewritten.
"""

    prompt = f"""You are a senior Design-to-Cost analyst for Supply Chain Management at Vodafone Egypt.

An intake interview has just finished. Write the activity description that the cost
estimator will work from. It is the only input the estimator gets: it never sees the facts
below, never sees the conversation, and cannot ask a follow-up. Anything you leave out is
lost, and anything you invent will be costed as though it were real.

=== HOW THIS SERVICE IS CLASSIFIED ===
Category: {state.get('category_code', '')} {state.get('category_name', '')}
Cost driver family: {state.get('family_code', '')} {state.get('family_name', '')}
Model spine: {kb.SPINE_NAMES.get(state.get('spine') or '', 'unclassified')} — {kb.SPINE_DESCRIPTIONS.get(state.get('spine') or '', '')}
Commercial model: {state.get('commercial_model') or 'not stated'}
Purpose of the exercise: {state.get('purpose') or 'not stated'}

=== FACTS CAPTURED ===
Anything tagged [DEFAULT] or [INFERRED] was not stated by the analyst.
{format_facts(facts)}

=== THE INTERVIEW ===
{format_history(state.get("history") or [], limit=40)}
{steer_block}
Requirements:
- Describe, do not instruct. "Vodafone Egypt will contract a media agency to plan and buy…",
  never "Provide media planning and buying…" or "The estimator must separate…". This is an
  account of the work, and it is read by an estimator who is not taking orders from it.
- Four to seven paragraphs of plain business English: what is being done, at what scale,
  over what period, where and across which locations, by whom or under what fee basis, with
  what equipment, coverage pattern, licence terms or third-party spend as the spine
  requires, and what is delivered at the end.
- PRESERVE EVERY NUMBER verbatim — counts, durations, rates, ratios, percentages, currency
  amounts, with their units. Never round one and never soften it to "several" or "a number
  of". These numbers drive every quantity downstream, and one that goes missing is guessed.
- Use every fact listed. Nothing captured may be dropped. EVERY fact tagged [DEFAULT] or
  [INFERRED] must appear in the assumptions field, and none of them may appear in the body
  as though the analyst had stated it.
- Write for the spine. A rights deal describes territory, term, exclusivity and channels and
  says nothing about FTE. A labour service describes roles, volumes and the coverage pattern.
  A pass-through deal keeps third-party spend and vendor fee visibly separate.
- Where something material was never captured at all, say plainly that it is to be
  determined rather than inventing a figure, and list it as an assumption.
- Keep every amount in the currency it was recorded in.
- Every sentence must carry a fact. Cut anything that would be equally true of any other
  contract — "designed to integrate with existing governance practices", "aligned to
  operator standards", "ensuring service continuity". The estimator cannot cost a sentence
  like that, and its presence makes the ones that matter harder to find.
- Do not name the spine, the category, the cost driver family or the unit of measure in the
  body. Those are appended below it from the classification, and saying them twice in two
  different forms is how they come to disagree.
- The commercial model and the purpose of the exercise are context for the estimator, not
  part of the activity. Mention them only where they were actually stated, and never write a
  sentence whose content is that they were not.
- No preamble, no sign-off, no markdown headings, no bullet lists in the body.
"""
    try:
        result = writer_llm().with_structured_output(_Draft, method=STRUCTURED_METHOD).invoke(prompt)
        narrative = (result.narrative or "").strip()
        assumptions = [a.strip() for a in (result.assumptions or []) if a and a.strip()]
    except Exception:
        narrative = ""
        assumptions = []

    assumptions = _carry_defaults(defaults_used(facts), assumptions)
    description = compose(state, narrative, assumptions) if narrative else _fallback(state)

    return {
        "description": description,
        "edit_request": "",
        "stage": "review",
        "stage_label": "Review",
        "phase": "review",
        "completed_stages": [*(state.get("completed_stages") or []), "write"],
        "bot_message": (
            "Here is the activity description the estimator will work from.\n\n"
            f"{description}\n\n"
            "Submit it, tell me what to change, ask me to rewrite it, or ask me anything "
            "about it first."
        ),
        "suggestions": list(REVIEW_OPTIONS),
        "new_fact_keys": [],
        "done": False,
    }


def review_node(state: IntakeState) -> IntakeState:
    """Work out what the analyst wants done with the draft, and do it if it is an answer."""
    message = (state.get("user_message") or "").strip()
    lowered = message.lower().strip(" .!?\"'")

    # The four chips and the obvious one-word replies, settled without a model call. Every
    # one of these is unambiguous, and a round trip to decide that "submit" means submit is
    # latency the analyst pays for nothing.
    if message == REVIEW_OPTIONS[0] or lowered in {
        "submit", "yes", "yep", "ok", "okay", "confirm", "confirmed", "approved",
        "accept", "looks good", "go ahead", "proceed", "done", "finish", "use it",
    }:
        return {"phase": "done", "stage": "done", "done": True, "bot_message": "", "suggestions": []}

    if message == REVIEW_OPTIONS[1]:
        return {"bot_message": "What would you like to change?", "suggestions": [], "done": False}
    if message == REVIEW_OPTIONS[2]:
        return {
            "bot_message": "How should it be different? Say 'just rewrite it' if you have no preference.",
            "suggestions": ["Shorter", "More detail", "Just rewrite it"],
            "done": False,
        }
    if message == REVIEW_OPTIONS[3]:
        return {"bot_message": "What would you like to know?", "suggestions": [], "done": False}

    prompt = f"""You are the intake analyst for Vodafone Egypt's Design-to-Cost tool. The
analyst is reviewing the description you wrote for them.

=== THE DESCRIPTION ===
{state.get("description") or ""}

=== THE FACTS IT WAS WRITTEN FROM ===
Anything tagged [DEFAULT] or [INFERRED] was not stated by the analyst.
{format_facts(state.get("facts") or {})}

=== THE INTERVIEW ===
{format_history(state.get("history") or [], limit=30)}

=== THEY REPLIED ===
"{message}"

Decide what they want. Prefer 'edit' over 'rewrite' when the change is local, and 'discuss'
only when they asked something and did not instruct a change. UK spellings, no filler.
"""
    try:
        verdict = conversation_llm().with_structured_output(_ReviewIntent, method=STRUCTURED_METHOD).invoke(prompt)
    except Exception:
        # Treating an unclassifiable reply as an edit is the safe failure: the worst case is
        # a description the analyst did not want changed, which they can say so about.
        verdict = _ReviewIntent(intent="edit", edit_request=message)

    if verdict.intent == "submit":
        return {"phase": "done", "stage": "done", "done": True, "bot_message": "", "suggestions": []}

    if verdict.intent == "discuss":
        return {
            "bot_message": verdict.answer or "I am not sure — could you put that another way?",
            "suggestions": list(REVIEW_OPTIONS),
            "done": False,
        }

    return {
        "stage": "write" if verdict.intent == "rewrite" else "revise",
        "edit_request": verdict.edit_request or message,
        "done": False,
    }


def revise_node(state: IntakeState) -> IntakeState:
    """Apply a targeted change, and record anything new the analyst said while asking."""
    instruction = (state.get("edit_request") or "").strip()
    previous = (state.get("description") or "").strip()

    class _Revision(_Draft):
        facts: list = Field(
            default_factory=list,
            description="Any NEW fact the analyst's instruction contains — 'make it 40 "
            "sites' is a fact, not a formatting note. Empty when the change is purely "
            "editorial. Each entry: key (snake_case, reusing the existing key when "
            "correcting something already captured), label, value, unit, source.",
        )

    prompt = f"""You are a senior Design-to-Cost analyst for Supply Chain Management at Vodafone Egypt.

Revise the activity description below. It is the only input the cost estimator gets.

--- current description ---
{previous}
--- end ---

The analyst asked for this change:
  "{instruction}"

=== FACTS CAPTURED IN THE INTERVIEW ===
{format_facts(state.get("facts") or {})}

Apply the change and leave everything they did not object to exactly as it is — same
paragraphs, same numbers, same wording, except where the change requires otherwise. Preserve
every number verbatim.

Return the revised body in `narrative` WITHOUT the "{ASSUMPTIONS_HEADING}" section, the
modelling basis lines, or any heading — those are reattached afterwards. Return the full
assumption list in `assumptions`, updated for the change.

If their instruction states or corrects a fact about the activity, also return it in `facts`
so the facts panel stays in step with the description.
"""
    try:
        result = conversation_llm().with_structured_output(_Revision, method=STRUCTURED_METHOD).invoke(prompt)
        narrative = (result.narrative or "").strip()
        assumptions = [a.strip() for a in (result.assumptions or []) if a and a.strip()]
        extracted = result.facts or []
    except Exception:
        return {
            "bot_message": "I could not apply that change. Say it another way and I will try again.",
            "suggestions": list(REVIEW_OPTIONS),
            "edit_request": "",
            "done": False,
        }

    facts, touched = merge_facts(state.get("facts") or {}, _as_objects(extracted), stage="review")
    assumptions = _carry_defaults(defaults_used(facts), assumptions)
    description = compose({**state, "facts": facts}, narrative, assumptions) if narrative else previous

    return {
        "facts": facts,
        "new_fact_keys": touched,
        "description": description,
        "edit_request": "",
        "bot_message": f"Updated.\n\n{description}\n\nAnything else, or shall I submit it?",
        "suggestions": list(REVIEW_OPTIONS),
        "done": False,
    }


class _Loose:
    """Attribute access over a dict, so revision facts reach ``merge_facts`` unchanged.

    ``merge_facts`` reads attributes because everywhere else it is handed pydantic models.
    The revision schema types its facts loosely — the nested model would otherwise have to
    be declared before the class that contains it, in a function — so they arrive as dicts.
    """

    def __init__(self, data: dict):
        self._data = data if isinstance(data, dict) else {}

    def __getattr__(self, name: str):
        return self._data.get(name, "")


def _as_objects(items) -> list:
    return [item if hasattr(item, "key") else _Loose(item) for item in (items or [])]
