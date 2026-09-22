"""
The conversation's memory: what has been said, and what is known because of it.

Two things accumulate, and they are not the same thing. ``history`` is the transcript, and
it is what lets the model answer "what did I just tell you?". ``facts`` is the extracted
understanding, and it is what the panel renders, what survives history being trimmed, and
what the description is written from. A fact outlives the message that produced it.

FACTS ARE MERGED, NEVER REPLACED
--------------------------------
Every turn asks the model for the facts it found in that turn. A turn that returns nothing
must not erase what stage 1 established. Reusing a key overwrites, which is how a correction
lands: "actually it is 40 sites, not 30" comes back under ``site_count`` and replaces the
value. Adding a key adds. Nothing else removes anything.

The one thing that does get removed is a rename. Models occasionally file the same fact
under a fresh key - ``service`` on turn four for what they called ``service_description`` on
turn one - and a plain merge then shows the analyst the same sentence twice under two
headings. A key held from an earlier turn, not returned this turn, whose value is
byte-identical to one that was returned, is that rename and is dropped. The length floor
underneath makes it safe: "No" is the answer to half the questions in this interview, and
collapsing ``remote_delivery`` into ``overnight_stay`` because both say "No" would destroy
two real answers to save one duplicate.

SOURCE IS PART OF THE FACT
--------------------------
Every fact records whether the analyst stated it, whether it is a default the analyst
accepted, or whether it was inferred from what they said. The Skill Specification requires
defaults to be labelled and logged, and requires the agent to refuse a final model while a
default sits on a top-five driver without acknowledgement. None of that is possible if the
facts panel shows twenty strings with no provenance. The UI can badge them; the description
writer files defaults under assumptions rather than stating them as fact; and an analyst
scanning the panel can see at a glance which numbers are theirs.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Iterable, List, Literal, Tuple

from typing_extensions import Annotated, TypedDict

Source = Literal["user", "default", "inferred"]

#: Below this, two facts sharing a value is a coincidence rather than a rename. See the
#: module docstring: the interview asks a great many yes/no questions.
_ALIAS_MIN_LENGTH = 12

#: How much of the transcript the model is shown. Facts carry everything material forward,
#: so older messages are recoverable understanding rather than lost information, and a long
#: interview should not grow its own latency turn by turn.
HISTORY_WINDOW = 20


def join_reply(current: str, update: str) -> str:
    """Reducer for ``bot_message``: several nodes may speak within one turn.

    A stage that finishes hands straight on to the next, and both have something to say —
    "Got that." followed by the next stage's first question. Overwriting would drop the
    acknowledgement and leave the analyst reading a question that ignores what they just
    said; two separate bot messages would be worse still. The caller resets the channel to
    "" at the start of each turn, so nothing survives into the next one.
    """
    left, right = (current or "").strip(), (update or "").strip()
    if not left:
        return right
    if not right:
        return left
    return f"{left}\n\n{right}"


def union_keys(current: List[str], update: List[str]) -> List[str]:
    """Reducer for ``new_fact_keys``: the union of what every node touched this turn."""
    seen = list(current or [])
    for key in update or []:
        if key not in seen:
            seen.append(key)
    return seen


class Fact(TypedDict, total=False):
    """One thing known about the activity."""

    key: str
    label: str
    value: str
    unit: str
    stage: str
    source: Source


class IntakeState(TypedDict, total=False):
    """One conversation. Everything the caller persists between turns.

    Plain dicts and lists throughout, with no object that only exists in this process: a
    session is written to a database between turns and read back by a different worker.
    """

    # --- this turn's input ---
    user_message: str

    # --- memory ---
    history: List[Dict[str, str]]        # {"role": "user"|"assistant", "content": str}
    facts: Dict[str, Fact]
    asked: List[str]                     # topics already put to the analyst, slugged
    turn: int

    # --- classification, set once at stage 0 ---
    category_code: str
    category_name: str
    family_code: str
    family_name: str
    spine: str
    secondary_spines: List[str]
    spine_queue: List[str]               # spines still to interview, primary first
    commercial_model: str
    purpose: str

    # --- progress ---
    stage: str
    stage_label: str
    completed_stages: List[str]
    stage_questions: int                 # questions spent in the current stage
    phase: str                           # interview | review | done

    # --- the description and its review ---
    description: str
    edit_request: str

    # --- this turn's output ---
    # Reduced rather than overwritten, because a turn can pass through several nodes and
    # each of them may have something to contribute. Everything above is plain overwrite:
    # a node computes those from the state it was handed, and nodes run in sequence.
    bot_message: Annotated[str, join_reply]
    new_fact_keys: Annotated[List[str], union_keys]
    suggestions: List[str]
    done: bool


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------

def as_text(value: Any) -> str:
    """Flatten a value to the string the panel and the writer will read.

    Models answer "roles" with a list and "coverage" with a nested object, and both are
    within a schema that types the value as free text. Rendering those as ``str(dict)`` puts
    Python repr in front of an analyst, so they are flattened readably here instead.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple, set)):
        return ", ".join(part for part in (as_text(v) for v in value) if part)
    if isinstance(value, dict):
        return "; ".join(
            f"{str(k).replace('_', ' ')}: {part}"
            for k, v in value.items()
            if (part := as_text(v))
        )
    return str(value).strip()


def _humanise(key: str) -> str:
    return key.replace("_", " ").strip().capitalize()


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------

def merge_facts(
    existing: Dict[str, Fact],
    incoming: Iterable[Any],
    *,
    stage: str,
) -> Tuple[Dict[str, Fact], List[str]]:
    """Fold this turn's extracted facts into what is already held.

    Returns the merged dictionary and the keys this turn touched — the second is what the
    UI highlights, so the analyst can see the panel react to the sentence they just typed
    rather than having to re-read twenty rows to find what changed.
    """
    merged: Dict[str, Fact] = dict(existing)
    touched: List[str] = []

    for item in incoming or []:
        key = as_text(getattr(item, "key", "")).lower().replace(" ", "_")
        value = as_text(getattr(item, "value", ""))
        if not key or not value:
            continue

        source = getattr(item, "source", "user")
        held = merged.get(key) or {}
        merged[key] = {
            "key": key,
            "label": as_text(getattr(item, "label", "")) or _humanise(key),
            "value": value,
            "unit": as_text(getattr(item, "unit", "")),
            # Pinned to where the fact FIRST appeared, not to where it was last mentioned.
            # A later stage re-confirming something — the commercial frame restating the
            # supervision ratio it just applied a default to — would otherwise move the row
            # out of "Staffing" and into "Commercial frame" in front of the analyst, and a
            # panel whose rows migrate between headings is one nobody can scan.
            "stage": held.get("stage") or stage,
            "source": source if source in ("user", "default", "inferred") else "user",
        }
        touched.append(key)

    return _drop_renames(merged, touched), touched


def _drop_renames(facts: Dict[str, Fact], touched: List[str]) -> Dict[str, Fact]:
    """Remove keys superseded by a rename this turn. See the module docstring."""
    fresh = {
        facts[k]["value"].strip().casefold()
        for k in touched
        if len(facts[k]["value"].strip()) >= _ALIAS_MIN_LENGTH
    }
    if not fresh:
        return facts
    return {
        key: fact
        for key, fact in facts.items()
        if key in touched or fact["value"].strip().casefold() not in fresh
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def format_facts(facts: Dict[str, Fact]) -> str:
    """The facts as the model sees them in a prompt, provenance included."""
    if not facts:
        return "  (nothing captured yet)"
    lines = []
    for fact in facts.values():
        value = fact["value"]
        if unit := fact.get("unit"):
            value = f"{value} {unit}"
        tag = "" if fact.get("source") == "user" else f"  [{fact.get('source', 'user').upper()}]"
        lines.append(f"  - {fact['key']}: {value}{tag}")
    return "\n".join(lines)


def format_history(history: List[Dict[str, str]], limit: int = HISTORY_WINDOW) -> str:
    if not history:
        return "  (no messages yet)"
    return "\n".join(
        f"  {entry.get('role', 'user')}: {entry.get('content', '')}"
        for entry in history[-limit:]
        if entry.get("content")
    )


def facts_panel(facts: Dict[str, Fact]) -> List[Dict[str, Any]]:
    """The facts grouped by the stage that captured them, for the UI panel.

    Grouped rather than flat because the panel is the analyst's view of the interview's
    progress as well as of its content: seeing "Staffing and employment" fill up is how they
    know where they are, now that there is no fixed step number to show them.
    """
    from .agendas import AGENDAS

    groups: Dict[str, List[Fact]] = {}
    for fact in facts.values():
        groups.setdefault(fact.get("stage") or "classify", []).append(fact)

    return [
        {
            "stage": stage,
            "label": AGENDAS[stage].label if stage in AGENDAS else _humanise(stage),
            "facts": items,
        }
        for stage, items in groups.items()
    ]


def defaults_used(facts: Dict[str, Fact]) -> List[Fact]:
    """Facts the analyst never stated. These are what the assumption register owes them."""
    return [fact for fact in facts.values() if fact.get("source") != "user"]


#: Words this short carry no signal — "a", "of", "is", "in" appear in every assumption ever
#: written. The cut is at two rather than three so that EGP, FTE, SLA and 24x7 count, which
#: in this domain are the most distinctive tokens a value has.
_STOPWORD_LENGTH = 2

#: How much of a default's vocabulary must show up in an assumption before that assumption
#: counts as having said it. Loose enough to survive rephrasing ("Supervision is one shift
#: lead per shift" for "1 shift lead per shift"), tight enough not to match on the handful of
#: words every sentence in a cost model shares.
_COVERAGE = 0.6


def _content_words(text: str) -> set:
    cleaned = "".join(c if c.isalnum() or c.isspace() else " " for c in (text or "").casefold())
    return {word for word in cleaned.split() if len(word) > _STOPWORD_LENGTH}


def covered_by(fact: Fact, assumptions: List[str]) -> bool:
    """Whether some assumption already says what this fact says.

    The writer is told to carry every default into its assumption list and usually does, in
    its own words. Appending them again on top of that produced a register with fourteen
    entries said twice — once as prose and once as "Label: value". Matching on the label
    alone missed every rephrasing; matching on how much of the value's vocabulary survives
    into the assumption does not.

    Values too short to have any vocabulary of their own — "No", "Included", "None" — fall
    back to the label, because on its own "No" matches everything or nothing depending on
    which way the test errs, and both are wrong. Erring towards a duplicate is deliberate:
    saying a default twice is untidy, and losing one means the analyst submits a description
    that silently assumed something nobody showed them.
    """
    value = f"{fact.get('value', '')} {fact.get('unit', '')}".strip()
    words = _content_words(value) or _content_words(f"{fact.get('label', '')} {value}")
    if not words:
        return True
    return any(len(words & _content_words(a)) / len(words) >= _COVERAGE for a in assumptions)


def today_line() -> str:
    """Today's date, for resolving what the analyst says relative to it.

    A model with no clock invents one: asked in the scope stage for "the period the model
    should cover", the predecessor resolved "next month" to a date a year out. Computed per
    call rather than at import, because a server left running crosses midnight and a stale
    date is the failure this exists to prevent.
    """
    return (
        f"Today is {date.today():%A, %d %B %Y}. Resolve any relative date the analyst gives "
        '("next month", "starting in Q2", "for the next year") against it, and record the '
        "actual month and year."
    )
