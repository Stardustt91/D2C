"""
Offline tests for the breadth budget and priceability rules.

These decide how many things get searched, so an error here is expensive in both
review time and API spend — a run that produced 480 cost parameters cost roughly
$58 in search alone.

Run with:  python agents/test_scope.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from estimation_scope import (  # noqa: E402
    CAPS,
    SCOPE_PROFILES,
    classify_parameter,
    drop_immaterial,
    filter_estimable_parameters,
    parsimony_rules,
    prune_to_cap,
    worst_case_parameter_count,
)

_FAILURES: list = []
_PASSES = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global _PASSES
    if condition:
        _PASSES += 1
    else:
        _FAILURES.append(f"{label}{(' — ' + detail) if detail else ''}")


def item(name: str, materiality: str = "medium", unit: str = "EGP per truck per day") -> dict:
    return {"parameter_name": name, "materiality": materiality, "unit": unit, "justification": ""}


# --------------------------------------------------------------------------
# Budget
# --------------------------------------------------------------------------

def test_profiles_are_ordered() -> None:
    lean = SCOPE_PROFILES["lean"]
    detailed = SCOPE_PROFILES["detailed"]
    for level in ("drivers", "components", "inputs", "parameters"):
        check(lean[level] <= detailed[level], f"lean is no wider than detailed at {level}")


def test_worst_case_is_bounded() -> None:
    # The number that matters: worst case is what the search bill scales with.
    total = worst_case_parameter_count()
    check(total <= 200, "worst-case parameter count stays bounded", f"got {total}")
    check(total > 0, "worst case is positive")


def test_prune_keeps_most_material() -> None:
    items = [item("a", "low"), item("b", "high"), item("c", "medium"), item("d", "high")]
    kept = prune_to_cap(items, 2)
    names = [k["parameter_name"] for k in kept]
    check(len(kept) == 2, "cap respected", str(names))
    check(set(names) == {"b", "d"}, "the two high-materiality items survive", str(names))


def test_prune_is_stable_within_a_band() -> None:
    items = [item(n, "medium") for n in "abcd"]
    kept = [k["parameter_name"] for k in prune_to_cap(items, 2)]
    check(kept == ["a", "b"], "original order preserved among equals", str(kept))


def test_prune_below_cap_is_a_noop() -> None:
    items = [item("a"), item("b")]
    check(prune_to_cap(items, 5) == items, "nothing removed when already under the cap")
    check(prune_to_cap([], 3) == [], "empty list is handled")


def test_prune_handles_missing_materiality() -> None:
    items = [{"parameter_name": "a"}, item("b", "high")]
    kept = [k["parameter_name"] for k in prune_to_cap(items, 1)]
    check(kept == ["b"], "an item with no materiality ranks below an explicit high", str(kept))


def test_drop_immaterial_keeps_a_floor() -> None:
    all_low = [item("a", "low"), item("b", "low")]
    check(len(drop_immaterial(all_low)) >= 1, "never returns nothing when everything is low")
    mixed = [item("a", "low"), item("b", "high")]
    check([d["parameter_name"] for d in drop_immaterial(mixed)] == ["b"], "low items dropped when others remain")


# --------------------------------------------------------------------------
# Priceability
# --------------------------------------------------------------------------

def test_real_items_are_priceable() -> None:
    for name in ("Mobile crane daily hire rate", "Prefabricated cabin unit price", "Site engineer net salary"):
        method, reason = classify_parameter(name, "EGP per unit per day")
        check(method == "market_price", f"{name!r} is priceable", f"got {method}: {reason}")


def test_derived_values_are_rejected() -> None:
    # These have no market price. Searching for them burns a fan-out and returns noise.
    for name in ("Salary Taxes", "VAT", "Social Insurance", "Contingency", "Overhead allocation",
                 "Profit margin", "Escalation provision"):
        method, _ = classify_parameter(name, "EGP per month")
        check(method == "derived", f"{name!r} is recognised as derived", f"got {method}")


def test_vague_items_are_rejected() -> None:
    for name in ("Miscellaneous", "Other costs", "Sundries", "General expenses", "Buffer"):
        method, _ = classify_parameter(name, "EGP per month")
        check(method == "not_estimable", f"{name!r} is rejected as unpriceable", f"got {method}")


def test_word_boundary_prevents_false_positives() -> None:
    # The reason the derived check matches whole words: these are real, priceable
    # things whose names merely contain a derived-sounding substring.
    method, _ = classify_parameter("Tax Consultant Fee", "EGP per month")
    check(method == "market_price", "'Tax Consultant Fee' is a real service, not a tax", method)
    method, _ = classify_parameter("Insurance Broker Retainer", "EGP per month")
    check(method == "market_price", "'Insurance Broker Retainer' is a purchasable service", method)


def test_missing_unit_is_not_estimable() -> None:
    method, reason = classify_parameter("Crane hire", "")
    check(method == "not_estimable", "a parameter with no unit cannot be priced", method)
    check("unit" in reason, "the reason names the missing unit")
    method, _ = classify_parameter("Crane hire", "EGP")
    check(method == "not_estimable", "a bare currency is not a pricing unit", method)


def test_percentage_names_are_derived() -> None:
    method, _ = classify_parameter("Insurance 14%", "EGP per month")
    check(method == "derived", "an explicit percentage is derived", method)


def test_filter_splits_keep_and_removed() -> None:
    parameters = [
        item("Mobile crane daily hire rate"),
        item("Miscellaneous"),
        item("VAT", unit="EGP per month"),
        item("Prefabricated cabin unit price"),
    ]
    keep, removed = filter_estimable_parameters(parameters)
    check(len(keep) == 2, "only priceable parameters survive", f"kept {[k['parameter_name'] for k in keep]}")
    check(len(removed) == 2, "the rest are reported, not silently dropped")
    check(all("removal_reason" in r for r in removed), "every removal carries a reason")
    check(all(k["estimation_method"] == "market_price" for k in keep), "kept parameters are tagged")


def test_filter_never_empties_a_cost_input() -> None:
    # A cost input with no parameters cannot be costed at all, which is worse than
    # carrying one the estimator may abstain on.
    parameters = [item("Miscellaneous"), item("Contingency")]
    keep, removed = filter_estimable_parameters(parameters)
    check(len(keep) == 1, "one parameter is always rescued", f"kept {len(keep)}")
    check(len(removed) == 1, "the others are still reported")


def test_filter_handles_empty_input() -> None:
    keep, removed = filter_estimable_parameters([])
    check(keep == [] and removed == [], "empty input produces empty output")


# --------------------------------------------------------------------------
# Prompt language
# --------------------------------------------------------------------------

def test_parsimony_block_states_the_cap() -> None:
    block = parsimony_rules("cost inputs", 3)
    check("AT MOST 3 cost inputs" in block, "the cap appears verbatim in the instruction")
    check("materiality" in block.lower(), "materiality is requested")
    check("smallest set" in block.lower(), "the minimum-set instruction is present")


# --------------------------------------------------------------------------
# Integration with the parameter step
# --------------------------------------------------------------------------

def test_formula_referenced_parameters_are_never_dropped() -> None:
    try:
        from cost_parameter import apply_scope_to_parameters
    except Exception as exc:
        print(f"  (skipped formula-protection test: {exc})")
        return

    parameters = [
        item("Net Salary", unit="EGP per person per month"),
        item("Salary Taxes", unit="EGP per person per month"),
    ]
    # 'Salary Taxes' is derived, but the formula depends on it — dropping it would
    # leave a dangling placeholder and make the cost input uncostable.
    kept, dropped = apply_scope_to_parameters(parameters, "[Net Salary] + [Salary Taxes]")
    names = {k["parameter_name"] for k in kept}
    check("Salary Taxes" in names, "a formula-referenced derived parameter is protected", str(names))
    check(not dropped, "nothing dropped when everything is referenced")


def test_unreferenced_derived_parameters_are_dropped() -> None:
    try:
        from cost_parameter import apply_scope_to_parameters
    except Exception as exc:
        print(f"  (skipped unreferenced-drop test: {exc})")
        return

    parameters = [
        item("Net Salary", unit="EGP per person per month"),
        item("Miscellaneous", unit="EGP per month"),
    ]
    kept, dropped = apply_scope_to_parameters(parameters, "[Net Salary] * 1.14")
    names = {k["parameter_name"] for k in kept}
    check("Net Salary" in names, "the real parameter is kept", str(names))
    check("Miscellaneous" not in names, "the unreferenced catch-all is dropped", str(names))
    check(len(dropped) == 1, "the drop is reported")


def test_parameter_cap_counts_formula_references() -> None:
    try:
        from cost_parameter import apply_scope_to_parameters
    except Exception as exc:
        print(f"  (skipped cap test: {exc})")
        return

    referenced = [item(f"Ref {i}", unit="EGP per unit per day") for i in range(CAPS["parameters"] + 2)]
    formula = " + ".join(f"[Ref {i}]" for i in range(CAPS["parameters"] + 2))
    kept, _ = apply_scope_to_parameters(referenced, formula)
    check(len(kept) == len(referenced), "formula-referenced parameters are all retained")

    extras = [item("Net Salary", unit="EGP per person per month")] + [
        item(f"Extra {i}", unit="EGP per unit per day") for i in range(6)
    ]
    kept, dropped = apply_scope_to_parameters(extras, "[Net Salary]")
    check(len(kept) <= CAPS["parameters"], "unreferenced extras are trimmed to the cap", f"kept {len(kept)}")
    check(len(dropped) > 0, "the trimmed ones are reported")


# --------------------------------------------------------------------------
# Workflow safety rails
#
# The crash these guard against: a level emptied by failed generation calls left
# the next step calling ThreadPoolExecutor(max_workers=0), which raises.
# --------------------------------------------------------------------------

class _Queue:
    def __init__(self):
        self.messages = []

    def put(self, event):
        self.messages.append(event.get("message", ""))


def _sample_structure() -> dict:
    return {"cost_drivers": [
        {"cost_driver_name": "Transportation", "cost_components": [
            {"cost_component_name": "Crane", "cost_inputs": [
                {"cost_input_name": "Crane hire", "cost_parameters": [{"parameter_name": "Daily rate"}]},
            ]},
            {"cost_component_name": "Truck", "cost_inputs": [
                {"cost_input_name": "Truck hire", "cost_parameters": [{"parameter_name": "Daily rate"}]},
            ]},
        ]},
    ]}


def _app():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "agents"))
    import d2c_app.app as app
    return app


def test_count_entities() -> None:
    try:
        app = _app()
    except Exception as exc:
        print(f"  (skipped workflow tests: {exc})")
        return
    check(app._count_entities(_sample_structure()) == (2, 2, 2), "counts components, inputs and parameters")
    check(app._count_entities({"cost_drivers": []}) == (0, 0, 0), "empty structure counts zero")


def test_dedup_wiping_a_level_is_discarded() -> None:
    try:
        app = _app()
    except Exception as exc:
        print(f"  (skipped dedup guard test: {exc})")
        return

    def wipe(structure, activity):
        for driver in structure["cost_drivers"]:
            driver["cost_components"] = []
        return structure

    queue = _Queue()
    original = _sample_structure()
    result = app._run_dedup(wipe, original, "activity", "components", queue)
    check(app._count_entities(result)[0] == 2, "a pass that removes every component is discarded")
    check(any("every" in m for m in queue.messages), "the discard is reported", str(queue.messages))


def test_dedup_partial_removal_is_applied() -> None:
    try:
        app = _app()
    except Exception as exc:
        print(f"  (skipped partial dedup test: {exc})")
        return

    def remove_one(structure, activity):
        structure["cost_drivers"][0]["cost_components"].pop()
        return structure

    queue = _Queue()
    result = app._run_dedup(remove_one, _sample_structure(), "activity", "components", queue)
    check(app._count_entities(result)[0] == 1, "a genuine duplicate removal is applied")
    check(any("Removed" in m for m in queue.messages), "the removal is reported")


def test_dedup_failure_keeps_structure() -> None:
    try:
        app = _app()
    except Exception as exc:
        print(f"  (skipped dedup failure test: {exc})")
        return

    def boom(structure, activity):
        raise RuntimeError("model unavailable")

    queue = _Queue()
    result = app._run_dedup(boom, _sample_structure(), "activity", "components", queue)
    check(app._count_entities(result) == (2, 2, 2), "a failed dedup leaves the structure untouched")
    check(any("failed" in m.lower() for m in queue.messages), "the failure is reported")


def main() -> int:
    tests = [v for n, v in sorted(globals().items()) if n.startswith("test_") and callable(v)]
    for test in tests:
        try:
            test()
        except Exception as exc:
            _FAILURES.append(f"{test.__name__} raised {type(exc).__name__}: {exc}")

    print(f"\n{_PASSES} checks passed across {len(tests)} tests")
    print(f"active profile caps: {CAPS} (worst case {worst_case_parameter_count()} parameters)")
    if _FAILURES:
        print(f"{len(_FAILURES)} FAILED:\n")
        for failure in _FAILURES:
            print(f"  x {failure}")
        return 1
    print("all green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
