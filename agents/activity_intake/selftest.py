"""
Checks that run without calling a model.

    python agents/activity_intake/selftest.py   # from the project root
    python -m activity_intake.selftest          # from inside agents/

Everything here is either reference data that has to hold together or logic that decides
where a conversation goes. None of it needs a model, so it costs nothing and can run on
every change — which matters most after ``build_intake_knowledge.py`` is re-run against a
revised Cost Driver Library, where a renamed family or a dropped spine tag would otherwise
surface as a mis-routed interview weeks later.

Plain asserts and a runner rather than pytest, because pytest is not installed in this
environment and one more thing to install is one more reason not to run the tests.
"""

from __future__ import annotations

import sys
import traceback

# Run as a loose script there is no package for the relative imports below to be relative
# to. Same bootstrap as ``cli.py``, and for the same reason: these are the two files people
# run directly, so both accept being run the obvious way. See the note there.
if not __package__:
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = Path(__file__).resolve().parent.name

from . import _wants_to_finish, new_session, turn_view
from . import knowledge as kb
from .agendas import AGENDAS, BENCHMARKS, SPINE_AGENDAS, SPINE_NOTES, planned_stages
from .graph import GRAPH, _advance, _entry, _make_router, _onward
from .state import (
    covered_by,
    facts_panel,
    join_reply,
    merge_facts,
    union_keys,
)
from .writer import ASSUMPTIONS_HEADING, REVIEW_OPTIONS, _carry_defaults, compose

CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


class _F:
    """Stand-in for the pydantic model the graph nodes hand to ``merge_facts``."""

    def __init__(self, key, value, label="", unit="", source="user"):
        self.key, self.value, self.label, self.unit, self.source = key, value, label, unit, source


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

@check
def every_family_the_tree_points_at_exists():
    """A dangling ref means an interview with no driver list."""
    referenced = {code for c in kb.CATEGORIES for code in c.families}
    missing = sorted(referenced - set(kb.FAMILIES))
    assert not missing, f"tree references unknown families: {missing}"


@check
def every_subcategory_routes_somewhere():
    """A subcategory with no spine cannot choose an interview."""
    orphans = [c.code for c in kb.CATEGORIES if c.spine not in SPINE_AGENDAS]
    assert not orphans, f"subcategories with no usable spine: {orphans}"


@check
def every_family_is_complete():
    gaps = [f.code for f in kb.FAMILIES.values() if not (f.name and f.unit and f.drivers)]
    assert not gaps, f"families missing name, unit or drivers: {gaps}"


@check
def the_tree_wins_where_it_disagrees_with_the_family():
    """D15 is recorded as "Mixed" because corporate services span three spines. Only the
    tree knows that travel management is pass-through and translation is transaction."""
    assert kb.FAMILIES["D15"].spine == "", "D15 should carry no spine of its own"
    assert kb.resolve_spine("17.1", "D15") == "passthrough"
    assert kb.resolve_spine("17.2", "D15") == "transaction"


@check
def an_unknown_category_falls_back_to_the_family():
    """The classifier returns a composite code on a genuinely split service ("15.4 / 15.6").
    That is not a tree key, and the family must still decide the spine."""
    assert kb.resolve_spine("15.4 / 15.6", "C11") == "labour"
    assert kb.resolve_spine("", "D11") == "rights"
    assert kb.resolve_spine("nonsense", "nonsense") == ""


@check
def every_spine_carries_its_agenda_notes_and_bands():
    for spine in SPINE_AGENDAS:
        assert spine in kb.SPINE_NAMES, spine
        assert spine in kb.SPINE_DESCRIPTIONS, spine
        assert spine in SPINE_NOTES, spine
        assert spine in BENCHMARKS, spine


@check
def the_taxonomy_index_covers_every_subcategory():
    assert kb.TAXONOMY_INDEX.count("\n") + 1 == len(kb.CATEGORIES)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

@check
def a_rights_deal_never_sees_the_staffing_stage():
    """The whole reason this is a graph."""
    plan = planned_stages("rights")
    assert plan == ["classify", "scope", "rights", "commercials", "write"], plan
    assert "labour" not in plan


@check
def a_mixed_deal_runs_both_blocks_in_order():
    plan = planned_stages("rights", ("labour",))
    assert plan == ["classify", "scope", "rights", "labour", "commercials", "write"], plan


@check
def a_bogus_secondary_spine_is_dropped_from_the_plan():
    assert planned_stages("labour", ("not_a_spine",)) == [
        "classify", "scope", "labour", "commercials", "write"
    ]


@check
def advance_walks_the_spine_queue_then_the_commercial_frame():
    state = {"spine": "rights", "spine_queue": ["rights", "labour"], "completed_stages": []}

    after_scope = _advance(state, "scope", reply="", facts={}, touched=[])
    assert after_scope["stage"] == "rights", after_scope["stage"]
    assert after_scope["spine_queue"] == ["rights", "labour"]

    after_rights = _advance({**state, **after_scope}, "rights", reply="", facts={}, touched=[])
    assert after_rights["stage"] == "labour"
    assert after_rights["spine_queue"] == ["labour"]
    # The second block opens with part of its budget already spent.
    assert after_rights["stage_questions"] > 0, "secondary spine should start short of budget"

    after_labour = _advance({**state, **after_rights}, "labour", reply="", facts={}, touched=[])
    assert after_labour["stage"] == "commercials"
    assert after_labour["spine_queue"] == []
    assert after_labour["stage_questions"] == 0, "the commercial frame gets its full budget"

    assert _advance(state, "commercials", reply="", facts={}, touched=[])["stage"] == "write"


@check
def the_primary_spine_gets_its_whole_budget():
    state = {"spine": "labour", "spine_queue": ["labour"], "completed_stages": []}
    assert _advance(state, "scope", reply="", facts={}, touched=[])["stage_questions"] == 0


@check
def a_router_ends_the_turn_when_its_stage_asked_something():
    """A node that asked left ``stage`` pointing at itself."""
    route = _make_router("labour")
    assert route({"stage": "labour"}) == "end"
    assert route({"stage": "commercials"}) == "commercials"
    assert route({}) == "end"


@check
def every_router_target_has_an_edge():
    for stage in ("scope", *SPINE_AGENDAS, "commercials"):
        route, targets = _make_router(stage), _onward(stage)
        # Everything _advance can produce from this stage must be in the edge map.
        for queue, spine in (([], "labour"), (["rights"], "rights"), (["labour"], "rights")):
            nxt = _advance(
                {"spine": spine, "spine_queue": [stage, *queue] if stage in SPINE_AGENDAS else queue},
                stage, reply="", facts={}, touched=[],
            )["stage"]
            assert route({"stage": nxt}) in {**targets, "end": "end"}, f"{stage} -> {nxt}"


@check
def the_entry_router_resumes_a_persisted_session():
    assert _entry({}) == "classify"
    assert _entry({"stage": "rights"}) == "rights"
    assert _entry({"stage": "rights", "phase": "review"}) == "review"
    # A stage name the graph no longer has must not crash a session resumed from storage.
    assert _entry({"stage": "step_7_overheads"}) == "classify"


@check
def the_compiled_graph_has_a_node_per_stage():
    nodes = set(GRAPH.get_graph().nodes)
    for name in ("classify", "scope", *SPINE_AGENDAS, "commercials", "write", "review", "revise"):
        assert name in nodes, f"missing node: {name}"


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

@check
def facts_accumulate_across_stages():
    facts, touched = merge_facts({}, [_F("site_count", "30 sites")], stage="scope")
    facts, touched = merge_facts(facts, [_F("roles", "2 field engineers")], stage="labour")
    assert set(facts) == {"site_count", "roles"}
    assert touched == ["roles"], "only this turn's keys are highlighted"


@check
def a_correction_overwrites_rather_than_duplicating():
    facts, _ = merge_facts({}, [_F("site_count", "30 sites")], stage="scope")
    facts, _ = merge_facts(facts, [_F("site_count", "40 sites")], stage="review")
    assert facts["site_count"]["value"] == "40 sites"
    assert len(facts) == 1


@check
def a_fact_stays_in_the_panel_group_that_first_captured_it():
    facts, _ = merge_facts({}, [_F("supervision", "1 lead per shift")], stage="labour")
    facts, _ = merge_facts(facts, [_F("supervision", "1 lead per shift")], stage="commercials")
    assert facts["supervision"]["stage"] == "labour"


@check
def a_renamed_key_does_not_show_the_same_fact_twice():
    long = "Preventive and corrective maintenance for 300 network sites"
    facts, _ = merge_facts({}, [_F("service_description", long)], stage="classify")
    facts, _ = merge_facts(facts, [_F("service", long)], stage="scope")
    assert list(facts) == ["service"], facts


@check
def two_short_answers_that_happen_to_match_are_both_kept():
    """"No" is the answer to half the questions in this interview."""
    facts, _ = merge_facts({}, [_F("remote_delivery", "No")], stage="scope")
    facts, _ = merge_facts(facts, [_F("overnight_stay", "No")], stage="labour")
    assert set(facts) == {"remote_delivery", "overnight_stay"}


@check
def empty_and_malformed_facts_are_ignored():
    facts, touched = merge_facts({}, [_F("", "x"), _F("k", ""), _F("ok", "value")], stage="scope")
    assert set(facts) == {"ok"} and touched == ["ok"]


@check
def a_key_is_normalised_so_two_spellings_are_one_row():
    facts, _ = merge_facts({}, [_F("Site Count", "30")], stage="scope")
    assert list(facts) == ["site_count"]


@check
def list_and_dict_values_are_flattened_for_the_panel():
    facts, _ = merge_facts({}, [_F("roles", ["L1 monitor", "L2 engineer"])], stage="labour")
    assert facts["roles"]["value"] == "L1 monitor, L2 engineer"


@check
def provenance_survives_and_defaults_to_user():
    facts, _ = merge_facts({}, [_F("a", "1", source="default"), _F("b", "2", source="wat")], stage="scope")
    assert facts["a"]["source"] == "default"
    assert facts["b"]["source"] == "user"


@check
def the_panel_groups_facts_under_their_stage_label():
    facts, _ = merge_facts({}, [_F("site_count", "30")], stage="scope")
    facts, _ = merge_facts(facts, [_F("roles", "2 engineers")], stage="labour")
    groups = {g["stage"]: g["label"] for g in facts_panel(facts)}
    assert groups == {"scope": AGENDAS["scope"].label, "labour": AGENDAS["labour"].label}


# ---------------------------------------------------------------------------
# Reducers — several nodes may speak within one turn
# ---------------------------------------------------------------------------

@check
def replies_from_consecutive_nodes_join():
    assert join_reply("", "Got it.") == "Got it."
    assert join_reply("Got it.", "Where?") == "Got it.\n\nWhere?"
    assert join_reply("Got it.", "") == "Got it."
    assert join_reply("", "") == ""


@check
def highlighted_keys_are_the_union_across_nodes():
    assert union_keys(["a"], ["b", "a"]) == ["a", "b"]
    assert union_keys([], []) == []


# ---------------------------------------------------------------------------
# The description
# ---------------------------------------------------------------------------

@check
def compose_appends_the_basis_the_model_is_not_asked_to_write():
    text = compose(
        {"spine": "labour", "family_code": "C6", "category_code": "2.3", "category_name": "NOC"},
        "The narrative.",
        ["One assumption."],
    )
    assert "The narrative." in text
    assert "Labour-built" in text
    assert "C6 NOC / SOC / managed services" in text
    assert "Cost per monitored element per month" in text
    assert SPINE_NOTES["labour"][:40] in text
    assert f"{ASSUMPTIONS_HEADING}\n- One assumption." in text


@check
def compose_does_not_stack_a_second_assumptions_heading():
    once = compose({"spine": "rights"}, "Body.", ["A."])
    twice = compose({"spine": "rights"}, once, ["A.", "B."])
    assert twice.count(ASSUMPTIONS_HEADING) == 1


@check
def compose_survives_an_unclassified_state():
    assert "unclassified" in compose({}, "Body.", [])


@check
def a_default_the_writer_already_stated_is_not_stated_again():
    facts, _ = merge_facts(
        {},
        [_F("supervision_structure", "1 shift lead per shift, supervising the L1/L2 team", source="default")],
        stage="labour",
    )
    written = ["Supervision is one shift lead per shift, supervising the L1/L2 team."]
    assert _carry_defaults(list(facts.values()), written) == written


@check
def a_default_the_writer_dropped_is_added():
    facts, _ = merge_facts({}, [_F("currency", "EGP", label="Currency", source="default")], stage="commercials")
    out = _carry_defaults(list(facts.values()), ["Something else entirely."])
    assert len(out) == 2 and "Currency: EGP" in out[1]


@check
def coverage_matching_is_not_fooled_by_shared_filler():
    def fact(label, value):
        return {"label": label, "value": value, "unit": ""}

    assert covered_by(fact("SG&A", "20 percent overhead"), ["SG&A overhead: 20 percent"])
    assert not covered_by(fact("Attrition", "25% per year"), ["The operating margin is 5%."])
    assert covered_by(fact("", ""), ["anything"]), "a value with no words cannot be uncovered"


@check
def a_default_too_short_to_have_vocabulary_falls_back_to_its_label():
    """"EGP" and "No" have no content words of their own. Before this they matched
    everything, and every short default silently vanished from the register."""
    def fact(label, value):
        return {"label": label, "value": value, "unit": ""}

    assert covered_by(fact("Currency", "EGP"), ["All amounts are stated in EGP."])
    assert not covered_by(fact("Currency", "EGP"), ["The operating margin is 5%."])
    assert not covered_by(fact("Remote delivery", "No"), ["The operating margin is 5%."])
    assert covered_by(fact("Remote delivery", "No"), ["No part of the delivery is remote."])


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

@check
def a_new_session_opens_on_classification_with_nothing_known():
    view = turn_view(new_session())
    assert view["progress"]["stage"] == "classify"
    assert view["facts"] == [] and view["done"] is False
    assert view["classification"]["spine"] == ""


@check
def the_escape_hatch_waits_for_a_classification():
    """Jumping to the writer before the service is placed produces a description with no
    spine, no unit of measure and no driver list — worse than the questions it saved."""
    assert not _wants_to_finish("write it now", {})
    assert _wants_to_finish("write it now", {"spine": "labour"})
    assert _wants_to_finish("  That's enough. ", {"spine": "rights"})
    assert not _wants_to_finish("write it now", {"spine": "labour", "phase": "review"})
    assert not _wants_to_finish("we need 30 sites written up", {"spine": "labour"})


@check
def the_review_chips_match_the_four_things_the_analyst_can_do():
    assert len(REVIEW_OPTIONS) == 4


def main() -> int:
    failed = []
    for fn in CHECKS:
        try:
            fn()
            print(f"  ok    {fn.__name__.replace('_', ' ')}")
        except Exception:
            failed.append(fn.__name__)
            print(f"  FAIL  {fn.__name__.replace('_', ' ')}")
            print("".join(f"          {line}" for line in traceback.format_exc().splitlines(True)))

    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
