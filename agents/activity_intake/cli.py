"""
Run the interview in a terminal.

    python agents/activity_intake/cli.py   # from the project root; see the note below
    python -m activity_intake.cli          # from inside agents/

Prints what the UI would render — the reply, the suggestion chips, the facts panel with its
provenance tags, and the classification and progress line — so a change to a prompt can be
judged against the thing the analyst actually sees rather than against a state dump.

Type ``facts`` to reprint the panel, ``state`` for the raw classification and stage, and
``quit`` to leave.
"""

from __future__ import annotations

import sys

# ``python cli.py`` runs this file as a loose script: __package__ is empty, so the relative
# import below has no package to be relative to and raises ImportError before anything
# useful happens. -m is the correct invocation and the one the rest of the docs give, but
# this is the file people reach for when they want to poke at the interview, and cd-ing into
# the directory it lives in and running it is the obvious thing to try. PEP 366: name the
# package and put its parent on the path, and the relative import resolves as it would have.
if not __package__:
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = Path(__file__).resolve().parent.name

from . import OPENING_MESSAGE, new_session, run_turn, turn_view

BOLD, DIM, GREEN, YELLOW, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[0m"

#: Defaults and inferences are flagged rather than blended in. On a terminal that is a
#: colour; in the UI it is a badge; either way it is the difference between a number the
#: analyst gave and one the interview made up on their behalf.
TAGS = {"user": "", "default": f" {YELLOW}[DEFAULT]{RESET}", "inferred": f" {DIM}[inferred]{RESET}"}


def _print_facts(view: dict) -> None:
    groups = view["facts"]
    if not groups:
        print(f"{DIM}  (nothing captured yet){RESET}")
        return
    fresh = set(view["new_fact_keys"])
    for group in groups:
        print(f"\n  {BOLD}{group['label']}{RESET}")
        for fact in group["facts"]:
            mark = f"{GREEN}+{RESET}" if fact["key"] in fresh else " "
            value = f"{fact['value']} {fact.get('unit', '')}".strip()
            print(f"  {mark} {fact['label']}: {value}{TAGS.get(fact.get('source', 'user'), '')}")


def _print_header(view: dict) -> None:
    cls, progress = view["classification"], view["progress"]
    if cls["spine"]:
        family = f" · {cls['family_code']} {cls['family_name']}" if cls["family_code"] else ""
        print(
            f"{DIM}[{cls['category_code']} {cls['category_name']}{family} · "
            f"{cls['spine_name']}]{RESET}"
        )
    print(
        f"{DIM}[{progress['stage_label']} — step {progress['position']} of "
        f"{progress['total']}: {' > '.join(progress['plan'])}]{RESET}"
    )


def main() -> None:
    state = new_session()
    print(f"\n{OPENING_MESSAGE}\n")

    while True:
        try:
            message = input(f"{BOLD}> {RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not message:
            continue
        if message.lower() in {"quit", "exit"}:
            return
        if message.lower() == "facts":
            _print_facts(turn_view(state))
            continue
        if message.lower() == "state":
            view = turn_view(state)
            print(f"\n{view['classification']}\n{view['progress']}\n")
            continue

        state = run_turn(state, message)
        view = turn_view(state)

        print("\n--- facts ---")
        _print_facts(view)

        if view["done"]:
            print(f"\n{BOLD}=== ACTIVITY DESCRIPTION ==={RESET}\n")
            print(view["description"])
            return

        print()
        _print_header(view)
        print(f"\n{view['reply']}")
        for index, option in enumerate(view["suggestions"], 1):
            print(f"{DIM}   [{index}] {option}{RESET}")
        print()


if __name__ == "__main__":
    sys.exit(main())
