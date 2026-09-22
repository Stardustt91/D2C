"""
D2C Cost Estimation – Multi-step interactive workflow (backend).

ARCHITECTURE
============

State structure (per session / per request):
  - activity_description: str   – Activity text (from chat or manual).
  - structure: dict              – Same as workflow_test.py:
      {
        "cost_drivers": [
          {
            "cost_driver_name": str,
            "justification": str,
            "cost_components": [
              {
                "cost_component_name": str,
                "quantity": int,
                "justification": str,
                "cost_inputs": [
                  {
                    "cost_input_name": str,
                    "quantity": int | None,
                    "justification": str,
                    "formula": str,
                    "monthly_cost_egp": float | None,
                    "cost_parameters": [
                      {
                        "parameter_name": str,
                        "justification": str,
                        "monthly_cost_egp": float | None,
                        "cost_justification": str,
                        "source_urls": list
                      }
                    ]
                  }
                ]
              }
            ]
          }
        ]
      }
  - current_step: str            – "activity" | "drivers" | "components" | "inputs" | "parameters" | "costs"

The structure dict also carries "resource_plan": the resourcing & scaling plan generated
by the "plan" step (volume driver, duration, crews, resource pool quantities with scaling
classes). It is the authoritative source of quantities for all later steps and is editable
in the UI before the cost structure is generated.

Workflow steps (same order as workflow_test.py):
  0. Plan        – Generate resourcing & scaling plan; user reviews/edits quantities; → run drivers.
  1. Activity    – User provides/edits description; "Next" → run drivers.
  2. Drivers     – Generate cost drivers; user edits/adds/deletes; "Verify and Continue" → run components.
  3. Components  – Generate cost components per driver; user edits/adds/deletes; "Verify and Continue" → run inputs.
  4. Inputs      – Generate cost inputs per component; user edits/adds/deletes; "Verify and Continue" → run parameters.
  5. Parameters  – Generate cost parameters + formula per input (costs stay "Not estimated yet"); user edits/adds/deletes.
  6. Costs       – User clicks "Estimate Costs"; estimate each parameter, then compute input total = Quantity × Formula(params).

Hierarchy: Activity → Cost Drivers → Cost Components → Cost Inputs → Cost Parameters → Cost Estimation.

Endpoints:
  - POST /run          – Run one workflow step (SSE): drivers | components | inputs | parameters | costs.
  - POST /entity/operation – CRUD on structure (add/edit/delete at any level); no agent re-runs on add.
  - /chat/*            – The activity-intake interview (init, message). One turn per POST;
                         see agents/activity_intake/README.md for what a turn carries.
"""

import re
import sys
import os
import json
import copy
import queue
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = ROOT / "agents"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))
os.chdir(ROOT)

# Imported for its import-time side effect: loading the project-root .env, so every
# credential and setting is in os.environ before anything below reads one.
import env_config  # noqa: F401,E402

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional

import agents.cost_driver as cost_driver_module
import agents.cost_component as cost_component_module
import agents.cost_inputs as cost_inputs_module
import agents.cost_parameter as cost_parameter_module
import agents.resource_planner as resource_planner_module
from agents.cost_estimation import cost_estimation_from_internet, evaluate_formula_for_input
from agents.deduplication import (
    deduplicate_components,
    deduplicate_inputs,
    deduplicate_parameters,
)
from agents.cost_parameter import validate_formula_units
from agents.activity_intake import (
    activity_facts as chat_activity_facts,
    new_session as new_chat_session,
    run_turn as run_chat_turn,
    turn_view as chat_turn_view,
)
from agents.session_title import generate_title

from d2c_app import sessions_db

chat_sessions = {}

# Export PPTX (tree diagram from current structure)
try:
    from to_pptx import structure_to_rows, build_pptx_from_rows
except ImportError:
    structure_to_rows = build_pptx_from_rows = None

# Per-level breadth budget. Previously a single cap of 20 applied at every level,
# which multiplied out to tens of thousands of possible parameters and in practice
# produced runs of ~480 — each of which costs a live search fan-out. Caps are now
# per level and the list is ranked by materiality before trimming, so what survives
# is the most important items rather than whichever happened to be generated first.
# Imported flat (AGENTS_DIR is on sys.path) to match how the agent modules import it,
# so there is one shared copy of the budget rather than two module instances.
from estimation_scope import CAPS as SCOPE_CAPS, prune_to_cap, scope_summary

print(f"[app] {scope_summary()}")

# Retained for any caller that still expects it; prefer SCOPE_CAPS.
MAX_ENTITIES_PER_LEVEL = max(SCOPE_CAPS.values())


def _count_entities(structure: dict) -> Tuple[int, int, int]:
    """(components, inputs, parameters) currently in the structure."""
    components = inputs = parameters = 0
    for driver in structure.get("cost_drivers", []):
        for component in driver.get("cost_components", []):
            components += 1
            for cost_input in component.get("cost_inputs", []):
                inputs += 1
                parameters += len(cost_input.get("cost_parameters") or [])
    return components, inputs, parameters


def _run_dedup(dedup_fn, structure: dict, activity_description: str, level: str, event_queue) -> dict:
    """
    Run a deduplication pass, reporting what it removed.

    These passes existed in agents/deduplication.py but were never called from
    anywhere, so the same cost concept could appear under several parents and be
    priced repeatedly. Failure is non-fatal: a dedup error returns the original
    structure rather than losing the whole level.
    """
    # Hold an untouched copy to fall back on. The dedup functions currently copy
    # internally, but a pass that mutated its argument would otherwise leave the
    # "keep everything" fallback returning an already-damaged structure.
    original = copy.deepcopy(structure)
    before = _count_entities(original)
    try:
        result = dedup_fn(structure, activity_description) or original
    except Exception as exc:
        event_queue.put({"event": "progress", "message": f"Deduplication of {level} failed ({exc}); keeping all items."})
        return original

    after = _count_entities(result)

    # A deduplication pass removes redundancy; it must never empty a level. If it
    # did, the model mislabelled everything as a duplicate, and applying that would
    # silently wipe the breakdown and leave the next step with nothing to do.
    for index, level_name in enumerate(("components", "inputs", "parameters")):
        if before[index] > 0 and after[index] == 0:
            event_queue.put({
                "event": "progress",
                "message": (
                    f"Deduplication of {level} tried to remove every {level_name} "
                    f"({before[index]} to 0); discarding that result and keeping all items."
                ),
            })
            return original

    removed = tuple(b - a for b, a in zip(before, after))
    if any(removed):
        event_queue.put({
            "event": "progress",
            "message": (
                f"Removed {removed[0]} duplicate component(s), {removed[1]} input(s) and "
                f"{removed[2]} parameter(s) at the {level} step."
            ),
        })
    return result


def _all_drivers(structure: dict) -> List[str]:
    return [d.get("cost_driver_name") for d in structure.get("cost_drivers", []) if d.get("cost_driver_name")]


def _all_components(structure: dict) -> List[Tuple[str, str]]:
    out = []
    for d in structure.get("cost_drivers", []):
        dname = d.get("cost_driver_name")
        for c in d.get("cost_components", []):
            cname = c.get("cost_component_name")
            if cname:
                out.append((dname, cname))
    return out


def _all_inputs(structure: dict) -> List[Tuple[str, str, str]]:
    out = []
    for d in structure.get("cost_drivers", []):
        dname = d.get("cost_driver_name")
        for c in d.get("cost_components", []):
            cname = c.get("cost_component_name")
            for i in c.get("cost_inputs", []):
                iname = i.get("cost_input_name")
                if iname:
                    out.append((dname, cname, iname))
    return out


def _ensure_driver_components(structure: dict) -> dict:
    """Guarantee every cost driver carries at least one cost component.

    A cost driver must never be dropped as the workflow moves down the layers. When
    the component step (or the dedup pass, which can delete a driver's only component
    as a cross-driver duplicate) leaves a driver empty, it is given a single component
    named after the driver instead of being pruned. The self-named component then
    flows into the inputs/parameters/costs steps like any other. Mutates and returns
    `structure`.
    """
    for dr in structure.get("cost_drivers", []):
        if not dr.get("cost_components"):
            dr["cost_components"] = [{
                "cost_component_name": dr.get("cost_driver_name") or "Cost component",
                "justification": "Added automatically so the cost driver is not dropped for having no cost components.",
                "quantity": 1,
                "cost_inputs": [],
            }]
    return structure


def _ensure_component_inputs(structure: dict) -> dict:
    """Guarantee every cost component carries at least one cost input.

    Mirrors `_ensure_driver_components` one level down: a component the inputs step or
    dedup left empty gets a single input named after it, so no component is dropped and
    it can still be costed (a parameterless input is priced directly at the costs step).
    Mutates and returns `structure`.
    """
    for dr in structure.get("cost_drivers", []):
        for comp in dr.get("cost_components", []):
            if not comp.get("cost_inputs"):
                comp["cost_inputs"] = [{
                    "cost_input_name": comp.get("cost_component_name") or "Cost input",
                    "justification": "Added automatically so the cost component is not dropped for having no cost inputs.",
                    "quantity": 1,
                    "cost_parameters": [],
                    "formula": "",
                }]
    return structure


# --- Request/response models ---


class ActivityInput(BaseModel):
    activity_description: str


class RunRequest(BaseModel):
    activity_description: str
    step: str = "drivers"  # plan | drivers | components | inputs | parameters | costs
    structure: Optional[dict] = None
    # Structured answers captured by the chat assistant (volume, crews, shared pools, ...).
    # Only consumed by the "plan" step; numbers here are authoritative for the planner.
    activity_facts: Optional[dict] = None


class ExportPptxRequest(BaseModel):
    structure: dict
    name: Optional[str] = "Cost_Estimation"


class ExportDbRequest(BaseModel):
    structure: dict
    name: Optional[str] = "Cost_Estimation"


class EntityOperation(BaseModel):
    activity_description: str
    structure: dict
    entity_type: str  # driver | component | input | parameter
    operation: str    # add | edit | delete | update_quantity | update_formula | update_cost
    driver_name: Optional[str] = None
    component_name: Optional[str] = None
    input_name: Optional[str] = None
    parameter_name: Optional[str] = None
    new_name: Optional[str] = None
    new_quantity: Optional[str] = None
    new_justification: Optional[str] = None
    new_formula: Optional[str] = None
    new_formula_justification: Optional[str] = None
    new_cost: Optional[float] = None
    new_parameter_cost: Optional[float] = None


class ChatMessage(BaseModel):
    session_id: str
    message: str


class ChatResponse(BaseModel):
    """One turn of the intake interview, as the browser receives it.

    Everything but the last two fields is ``turn_view()`` verbatim — the panel, the chips
    and the progress line are all redrawn wholesale from each response, so the front end
    holds no intake state of its own and cannot drift from the server's.
    """

    bot_message: str
    done: bool = False

    #: Quick replies for the question just asked, concrete and specific to this service;
    #: during review, the four things that can be done with the draft.
    suggestions: List[str] = []
    #: Everything captured so far, grouped by the stage that captured it. Each fact carries
    #: key, label, value, unit, stage and source (user | default | inferred).
    facts: List[dict] = []
    #: The keys that moved this turn, so the panel can highlight the analyst's last
    #: sentence landing rather than making them re-read twenty rows.
    new_fact_keys: List[str] = []
    #: Category, cost driver family and model spine — the routing decision the shape of the
    #: rest of the interview hangs on, and the one worth correcting early.
    classification: dict = {}
    #: The stages THIS conversation will run, given that spine, and where it has reached.
    progress: dict = {}
    #: interview | review | done
    phase: str = "interview"
    #: The draft, from the review phase onward. Shown in the conversation so the analyst can
    #: edit, rewrite or question it before accepting.
    description: str = ""

    #: Set only on the turn the analyst accepts the draft; what the estimator is handed.
    activity_description: Optional[str] = None
    activity_facts: Optional[dict] = None


class SessionCreate(BaseModel):
    activity_description: str
    activity_facts: Optional[dict] = None


class SessionUpdate(BaseModel):
    """A patch. Every field is optional and only the ones actually sent are written:
    a rename carries just `title`, an autosave just `structure` and `step_completed`."""
    title: Optional[str] = None
    activity_description: Optional[str] = None
    activity_facts: Optional[dict] = None
    structure: Optional[dict] = None
    step_completed: Optional[str] = None


#: Longest title accepted from a rename. The generated ones are far shorter; this is
#: only here to stop an accidental paste of a whole description into the field.
MAX_SESSION_TITLE_CHARS = 120


def _provided_fields(body: BaseModel) -> set:
    """Names the caller actually sent, so an omitted field is not read as an explicit null."""
    fields = getattr(body, "model_fields_set", None)  # pydantic v2
    if fields is None:
        fields = getattr(body, "__fields_set__", set())  # pydantic v1
    return set(fields)


# --- Workflow (aligned with workflow_test.py) ---


def run_workflow(
    activity_description: str,
    event_queue: queue.Queue,
    step: str = "drivers",
    initial_structure: Optional[dict] = None,
    activity_facts: Optional[dict] = None,
) -> None:
    """Run a single workflow step. Emits progress + structure via event_queue. step: plan | drivers | components | inputs | parameters | costs."""
    try:
        structure_so_far = copy.deepcopy(initial_structure) if initial_structure else None
        driver_justifications = {}
        resource_plan = (initial_structure or {}).get("resource_plan")

        if step == "plan":
            event_queue.put({"event": "progress", "message": "Deriving the resourcing & scaling plan (volume, crews, resource pools)…"})
            plan = resource_planner_module.resource_plan_identifier(activity_description, activity_facts)
            structure_so_far = {
                "resource_plan": plan,
                "cost_drivers": (initial_structure or {}).get("cost_drivers", []),
            }
            event_queue.put({"event": "structure", "structure": copy.deepcopy(structure_so_far)})
            event_queue.put({"event": "done", "structure": copy.deepcopy(structure_so_far),
                             "message": "Resource plan ready. Review and edit the quantities before continuing."})
            return

        if step == "drivers":
            event_queue.put({"event": "progress", "message": "Identifying cost drivers…"})
            cost_drivers_list, all_drivers = cost_driver_module.cost_drivers_identifier(
                activity_description, resource_plan=resource_plan
            )
            cost_drivers_list = prune_to_cap(cost_drivers_list, SCOPE_CAPS["drivers"])
            kept_driver_names = {d.get("cost_driver_name") for d in cost_drivers_list}
            all_drivers = [d for d in all_drivers if d in kept_driver_names]
            structure_so_far = {
                "cost_drivers": [
                    {
                        "cost_driver_name": d["cost_driver_name"],
                        "justification": d.get("justification", ""),
                        "cost_components": [],
                    }
                    for d in cost_drivers_list
                ]
            }
            if resource_plan:
                structure_so_far["resource_plan"] = resource_plan
            event_queue.put({"event": "structure", "structure": copy.deepcopy(structure_so_far)})
            event_queue.put({"event": "done", "structure": copy.deepcopy(structure_so_far)})
            return

        if step == "components":
            structure_so_far = copy.deepcopy(initial_structure)
            all_drivers = _all_drivers(structure_so_far)
            for d in structure_so_far.get("cost_drivers", []):
                driver_justifications[d.get("cost_driver_name")] = d.get("justification", "")

            base_struct = copy.deepcopy(structure_so_far)
            if not all_drivers:
                event_queue.put({"event": "done", "structure": copy.deepcopy(structure_so_far),
                                 "message": "No cost drivers to work from. Re-run the drivers step first."})
                return
            event_queue.put({"event": "progress", "message": f"Identifying cost components for {len(all_drivers)} drivers in parallel…"})

            def _estimate_components(dname):
                _, _, updated = cost_component_module.cost_components_identifier(
                    activity_description, dname, copy.deepcopy(base_struct), all_drivers,
                    resource_plan=resource_plan,
                )
                for dr in updated.get("cost_drivers", []):
                    if dr.get("cost_driver_name") == dname:
                        return dname, prune_to_cap(dr.get("cost_components", []), SCOPE_CAPS["components"])
                return dname, []

            # Drivers whose generation call errored, as opposed to genuinely returning
            # nothing. The two used to be indistinguishable: both produced an empty
            # list and the driver was then pruned, so one transient API error deleted
            # a driver permanently and a bad minute could empty the whole structure.
            failed_drivers: set = set()

            with ThreadPoolExecutor(max_workers=min(len(all_drivers), 10)) as executor:
                futures = {executor.submit(_estimate_components, dname): dname for dname in all_drivers}
                for future in as_completed(futures):
                    d_single = futures[future]
                    try:
                        dname, comps = future.result()
                    except Exception as e:
                        failed_drivers.add(d_single)
                        event_queue.put({"event": "progress", "message": f"Failed to identify cost components for {d_single}: {e}. The driver is kept so you can retry it."})
                        continue
                    for dr in structure_so_far.get("cost_drivers", []):
                        if dr.get("cost_driver_name") == dname:
                            dr["cost_components"] = comps
                            dr["justification"] = driver_justifications.get(dname, "")
                            break
                    event_queue.put({"event": "progress", "message": f"Identified cost components for {dname}"})
                    event_queue.put({"event": "structure", "structure": copy.deepcopy(structure_so_far)})

            if failed_drivers:
                event_queue.put({
                    "event": "progress",
                    "message": (
                        f"{len(failed_drivers)} driver(s) could not be expanded: "
                        f"{', '.join(sorted(failed_drivers))}. Each was kept with a placeholder component "
                        f"named after the driver; re-run this step to replace them."
                    ),
                })

            # No driver is pruned here. A driver that produced no components — whether its
            # call failed or the model genuinely returned none — is kept; the dedup pass
            # below only merges duplicate components and can leave a driver empty too.
            structure_so_far = _run_dedup(
                deduplicate_components, structure_so_far, activity_description, "components", event_queue
            )
            # Guarantee every driver keeps at least one component (named after the driver
            # when it has none), so a driver is never dropped for lacking children.
            _ensure_driver_components(structure_so_far)
            event_queue.put({"event": "done", "structure": copy.deepcopy(structure_so_far)})
            return

        if step == "inputs":
            structure_so_far = copy.deepcopy(initial_structure)
            all_drivers = _all_drivers(structure_so_far)
            all_components = _all_components(structure_so_far)
            for d in structure_so_far.get("cost_drivers", []):
                driver_justifications[d.get("cost_driver_name")] = d.get("justification", "")

            pairs = [
                (dr.get("cost_driver_name"), comp.get("cost_component_name"))
                for dr in structure_so_far.get("cost_drivers", [])
                for comp in dr.get("cost_components", [])
            ]
            base_struct = copy.deepcopy(structure_so_far)
            if not pairs:
                event_queue.put({"event": "done", "structure": copy.deepcopy(structure_so_far),
                                 "message": "No cost components to work from. Re-run the components step first — "
                                            "if it produced nothing, the drivers step likely returned no usable drivers."})
                return
            event_queue.put({"event": "progress", "message": f"Identifying cost inputs for {len(pairs)} components in parallel…"})

            def _estimate_inputs(dname, cname):
                _, _, updated = cost_inputs_module.cost_inputs_identifier(
                    activity_description, dname, cname, copy.deepcopy(base_struct),
                    all_cost_drivers=all_drivers, all_cost_components=all_components,
                    resource_plan=resource_plan,
                )
                for dr in updated.get("cost_drivers", []):
                    if dr.get("cost_driver_name") == dname:
                        for comp in dr.get("cost_components", []):
                            if comp.get("cost_component_name") == cname:
                                return dname, cname, prune_to_cap(comp.get("cost_inputs", []), SCOPE_CAPS["inputs"])
                return dname, cname, []

            # Components whose call errored, kept rather than pruned — see the same
            # reasoning at the components step.
            failed_components: set = set()

            with ThreadPoolExecutor(max_workers=min(len(pairs), 10)) as executor:
                futures = {executor.submit(_estimate_inputs, d, c): (d, c) for d, c in pairs}
                for future in as_completed(futures):
                    d_pair, c_pair = futures[future]
                    try:
                        dname, cname, inputs = future.result()
                    except Exception as e:
                        failed_components.add((d_pair, c_pair))
                        event_queue.put({"event": "progress", "message": f"Failed to identify cost inputs for {c_pair} ({d_pair}): {e}. The component is kept so you can retry it."})
                        continue
                    for dr in structure_so_far.get("cost_drivers", []):
                        if dr.get("cost_driver_name") == dname:
                            for comp in dr.get("cost_components", []):
                                if comp.get("cost_component_name") == cname:
                                    comp["cost_inputs"] = inputs
                                    break
                            dr["justification"] = driver_justifications.get(dname, "")
                            break
                    event_queue.put({"event": "progress", "message": f"Identified cost inputs for {cname}"})
                    event_queue.put({"event": "structure", "structure": copy.deepcopy(structure_so_far)})

            if failed_components:
                event_queue.put({
                    "event": "progress",
                    "message": (
                        f"{len(failed_components)} component(s) could not be expanded: "
                        f"{', '.join(sorted(c for _, c in failed_components))}. Each was kept with a "
                        f"placeholder input named after the component; re-run this step to replace them."
                    ),
                })

            # Nothing from the previous layers is pruned: drivers and components are kept
            # even when this step produced no inputs for them. The dedup pass only merges
            # duplicate inputs.
            structure_so_far = _run_dedup(
                deduplicate_inputs, structure_so_far, activity_description, "inputs", event_queue
            )
            # Keep every driver and component, and give any empty component an input named
            # after it so it stays costable rather than being dropped.
            _ensure_driver_components(structure_so_far)
            _ensure_component_inputs(structure_so_far)
            event_queue.put({"event": "done", "structure": copy.deepcopy(structure_so_far)})
            return

        if step == "parameters":
            structure_so_far = copy.deepcopy(initial_structure)

            triples = [
                (dr.get("cost_driver_name"), comp.get("cost_component_name"), inp.get("cost_input_name"))
                for dr in structure_so_far.get("cost_drivers", [])
                for comp in dr.get("cost_components", [])
                for inp in comp.get("cost_inputs", [])
            ]
            base_struct = copy.deepcopy(structure_so_far)
            if not triples:
                event_queue.put({"event": "done", "structure": copy.deepcopy(structure_so_far),
                                 "message": "No cost inputs to work from. Re-run the inputs step first."})
                return
            event_queue.put({"event": "progress", "message": f"Identifying cost parameters for {len(triples)} inputs in parallel…"})

            def _estimate_params(dname, cname, iname):
                params_list, formula_str, formula_just = cost_parameter_module.cost_parameters_identifier(
                    activity_description, dname, cname, iname, copy.deepcopy(base_struct),
                    resource_plan=resource_plan,
                )
                return dname, cname, iname, prune_to_cap(params_list, SCOPE_CAPS["parameters"]), formula_str, formula_just

            # Inputs whose call errored, kept rather than pruned — same reasoning as
            # the two steps above.
            failed_inputs: set = set()

            with ThreadPoolExecutor(max_workers=min(len(triples), 10)) as executor:
                futures = {executor.submit(_estimate_params, d, c, i): (d, c, i) for d, c, i in triples}
                for future in as_completed(futures):
                    d_t, c_t, i_t = futures[future]
                    try:
                        dname, cname, iname, params_list, formula_str, formula_just = future.result()
                    except Exception as e:
                        failed_inputs.add((d_t, c_t, i_t))
                        event_queue.put({"event": "progress", "message": f"Failed to identify cost parameters for {i_t} ({c_t}/{d_t}): {e}. The input is kept so you can retry it."})
                        continue
                    structure_so_far = cost_parameter_module.apply_cost_parameters_to_structure(
                        structure_so_far, dname, cname, iname, params_list, formula_str, formula_just
                    )
                    event_queue.put({"event": "progress", "message": f"Identified cost parameters for {iname}"})
                    event_queue.put({"event": "structure", "structure": copy.deepcopy(structure_so_far)})

            if failed_inputs:
                event_queue.put({
                    "event": "progress",
                    "message": (
                        f"{len(failed_inputs)} input(s) could not be expanded: "
                        f"{', '.join(sorted(i for _, _, i in failed_inputs))}. They are kept and will be "
                        f"priced directly at the costs step; re-run this step to retry parameter extraction."
                    ),
                })

            # Nothing is pruned. An input with no parameters is valid — it is priced
            # directly at the costs step — and drivers/components from the earlier layers
            # are always kept, so no part of the previous layers is dropped here.
            # Deduplicate before the unit check so warnings describe the final set.
            structure_so_far = _run_dedup(
                deduplicate_parameters, structure_so_far, activity_description, "parameters", event_queue
            )
            # Defensive: keep every driver and component from the previous layers intact.
            _ensure_driver_components(structure_so_far)
            _ensure_component_inputs(structure_so_far)
            # Deterministic unit/formula consistency check (warnings only, shown in UI)
            for dr in structure_so_far.get("cost_drivers", []):
                for comp in dr.get("cost_components", []):
                    for inp in comp.get("cost_inputs", []):
                        inp["unit_warnings"] = validate_formula_units(inp)
            event_queue.put({"event": "done", "structure": copy.deepcopy(structure_so_far)})
            return

        if step == "costs":
            structure_so_far = copy.deepcopy(initial_structure)
            for d in structure_so_far.get("cost_drivers", []):
                driver_justifications[d.get("cost_driver_name")] = d.get("justification", "")

            triples = [
                (dr.get("cost_driver_name"), comp.get("cost_component_name"), inp.get("cost_input_name"))
                for dr in structure_so_far.get("cost_drivers", [])
                for comp in dr.get("cost_components", [])
                for inp in comp.get("cost_inputs", [])
            ]
            base_struct = copy.deepcopy(structure_so_far)
            event_queue.put({"event": "progress", "message": f"Estimating costs for {len(triples)} inputs in parallel…"})

            def _estimate_cost(dname, cname, iname):
                return dname, cname, iname, cost_estimation_from_internet(
                    activity_description, dname, cname, iname, copy.deepcopy(base_struct)
                )

            # Lock guards merges into the shared structure_so_far.
            merge_lock = threading.Lock()

            def _find_input(struct, dname, cname, iname):
                for dr in struct.get("cost_drivers", []):
                    if dr.get("cost_driver_name") != dname:
                        continue
                    for comp in dr.get("cost_components", []):
                        if comp.get("cost_component_name") != cname:
                            continue
                        for inp in comp.get("cost_inputs", []):
                            if inp.get("cost_input_name") == iname:
                                return inp
                return None

            with ThreadPoolExecutor(max_workers=min(max(len(triples), 1), 10)) as executor:
                futures = {executor.submit(_estimate_cost, d, c, i): (d, c, i) for d, c, i in triples}
                for future in as_completed(futures):
                    d_t, c_t, i_t = futures[future]
                    try:
                        dname, cname, iname, result = future.result()
                    except Exception as e:
                        event_queue.put({"event": "progress", "message": f"Failed to estimate cost for {i_t} ({c_t}/{d_t}): {e}"})
                        continue
                    if result and result.get("structure_so_far") is not None:
                        updated_input = _find_input(result["structure_so_far"], dname, cname, iname)
                        if updated_input is not None:
                            with merge_lock:
                                target = _find_input(structure_so_far, dname, cname, iname)
                                if target is not None:
                                    target.clear()
                                    target.update(copy.deepcopy(updated_input))
                    event_queue.put({"event": "progress", "message": f"Estimated cost for {iname}"})
                    event_queue.put({"event": "structure", "structure": copy.deepcopy(structure_so_far)})

            for _dr in structure_so_far.get("cost_drivers", []):
                _dr["justification"] = driver_justifications.get(_dr.get("cost_driver_name"), _dr.get("justification", ""))

            # Recompute monthly_cost_egp from formula where applicable (quantity is applied in UI)
            for dr in structure_so_far.get("cost_drivers", []):
                for comp in dr.get("cost_components", []):
                    for inp in comp.get("cost_inputs", []):
                        computed = evaluate_formula_for_input(inp)
                        if computed is not None:
                            inp["monthly_cost_egp"] = computed
                        inp["unit_warnings"] = validate_formula_units(inp)

            event_queue.put({"event": "done", "structure": copy.deepcopy(structure_so_far)})
            return

        event_queue.put({"event": "error", "message": f"Unknown step: {step}"})

    except Exception as e:
        import traceback
        traceback.print_exc()
        event_queue.put({"event": "error", "message": str(e)})


def sse_generator(
    activity_description: str,
    step: str = "drivers",
    initial_structure: Optional[dict] = None,
    activity_facts: Optional[dict] = None,
):
    import logging
    log = logging.getLogger("uvicorn.error")
    event_queue = queue.Queue()
    thread = threading.Thread(
        target=run_workflow,
        args=(activity_description, event_queue),
        kwargs={"step": step, "initial_structure": initial_structure, "activity_facts": activity_facts},
    )
    thread.start()
    yield f"data: {json.dumps({'event': 'started', 'step': step})}\n\n"

    # Poll the queue with a short timeout. When idle, emit an SSE comment line as
    # a heartbeat so Azure App Service's idle-response timeout (~230s) and any
    # intermediate proxies do not drop the connection during long agent calls.
    # SSE comments start with ":" and are ignored by EventSource on the client,
    # so the existing frontend handlers are unaffected.
    HEARTBEAT_INTERVAL_S = 15
    # Hard ceiling: only give up when the worker thread is dead and we've waited
    # this long with nothing in the queue. Keeps us from hanging forever on a
    # silently crashed worker without prematurely killing slow estimations.
    STALL_TIMEOUT_S = 300
    idle_seconds = 0
    while True:
        try:
            item = event_queue.get(timeout=HEARTBEAT_INTERVAL_S)
        except queue.Empty:
            idle_seconds += HEARTBEAT_INTERVAL_S
            if not thread.is_alive() and event_queue.empty():
                yield f"data: {json.dumps({'event': 'error', 'message': 'Worker exited without completing.'})}\n\n"
                break
            if idle_seconds >= STALL_TIMEOUT_S:
                yield f"data: {json.dumps({'event': 'timeout'})}\n\n"
                break
            yield ": heartbeat\n\n"
            continue
        idle_seconds = 0
        ev = item.get("event", "")
        yield f"data: {json.dumps(item)}\n\n"
        if ev in ("error", "done"):
            break


# --- Entity CRUD: structure-only updates, no agent calls on add ---


OVERRIDE_MARKER = "[Analyst override]"


def mark_parameter_override(param: dict, value: Optional[float]) -> None:
    """
    Record that a human, not the engine, set this parameter's cost.

    The engine's own justification is kept underneath the note: it is the evidence
    the analyst was looking at when they decided to overrule it, and dropping it
    would make the override unauditable. A previous override note is replaced rather
    than stacked, so repeated edits leave one line, not a pile.
    """
    previous = param.get("cost_justification") or ""
    body = "\n".join(
        line for line in previous.splitlines() if not line.startswith(OVERRIDE_MARKER)
    ).strip()

    if value is None:
        note = f"{OVERRIDE_MARKER} value cleared by an analyst; the estimate below no longer applies."
        param["estimate_status"] = "insufficient_evidence"
        param["estimate_basis"] = "none"
    else:
        note = (
            f"{OVERRIDE_MARKER} value set manually to {value:,.2f} by an analyst, "
            f"replacing the estimate below."
        )
        param["estimate_status"] = "user_set"
        param["estimate_basis"] = "user_override"

    param["cost_justification"] = note + ("\n\n" + body if body else "")
    param["confidence"] = "none"


def recompute_derived_costs(structure: dict) -> None:
    """
    Re-evaluate every cost input whose value is derived from its cost parameters.

    Without this, editing a parameter's cost or an input's formula changes nothing
    visible: the input keeps the figure computed during the costs step, and the
    grand total quietly disagrees with the numbers it is made of.

    Inputs with no parameters are left alone: they were estimated directly and have
    no formula to recompute from. So is an input that has never been costed and whose
    parameters are all still uncosted — before the costs step has run there is
    nothing to recompute, and evaluating anyway would decorate the whole tree with
    "not costed" errors. An input that already carries a figure is always
    re-evaluated, including down to None, because a cleared parameter must take the
    total it fed with it rather than leave a stale number behind.
    """
    for driver in structure.get("cost_drivers", []):
        for component in driver.get("cost_components", []):
            for inp in component.get("cost_inputs", []):
                params = inp.get("cost_parameters") or []
                if not params:
                    continue
                never_costed = (
                    inp.get("monthly_cost_egp") is None
                    and all(p.get("monthly_cost_egp") is None for p in params)
                )
                if never_costed:
                    continue
                inp["monthly_cost_egp"] = evaluate_formula_for_input(inp)
                inp["unit_warnings"] = validate_formula_units(inp)


def handle_entity_operation(op: EntityOperation) -> dict:
    """Apply add/edit/delete to structure. Add only appends new entity; no re-run of agents."""
    structure = copy.deepcopy(op.structure)

    if op.entity_type == "driver":
        if op.operation == "delete":
            structure["cost_drivers"] = [
                d for d in structure.get("cost_drivers", [])
                if d.get("cost_driver_name") != op.driver_name
            ]
        elif op.operation == "add":
            if not op.new_name:
                raise HTTPException(status_code=400, detail="new_name required for add driver")
            structure.setdefault("cost_drivers", []).append({
                "cost_driver_name": op.new_name.strip(),
                "justification": (op.new_justification or "").strip() or f"User-added: {op.new_name}",
                "cost_components": [],
            })
        elif op.operation == "edit":
            for d in structure.get("cost_drivers", []):
                if d.get("cost_driver_name") == op.driver_name:
                    if op.new_name:
                        d["cost_driver_name"] = op.new_name.strip()
                    if op.new_justification is not None:
                        d["justification"] = op.new_justification.strip()
                    break

    elif op.entity_type == "component":
        if op.operation == "delete":
            for d in structure.get("cost_drivers", []):
                if d.get("cost_driver_name") == op.driver_name:
                    d["cost_components"] = [
                        c for c in d.get("cost_components", [])
                        if c.get("cost_component_name") != op.component_name
                    ]
                    break
        elif op.operation == "add":
            if not op.new_name:
                raise HTTPException(status_code=400, detail="new_name required for add component")
            qty = 1
            try:
                if op.new_quantity:
                    qty = int(op.new_quantity)
            except ValueError:
                pass
            new_comp = {
                "cost_component_name": op.new_name.strip(),
                "quantity": qty,
                "justification": (op.new_justification or "").strip() or "User-added component",
                "cost_inputs": [],
            }
            for d in structure.get("cost_drivers", []):
                if d.get("cost_driver_name") == op.driver_name:
                    d.setdefault("cost_components", []).append(new_comp)
                    break
        elif op.operation == "edit":
            for d in structure.get("cost_drivers", []):
                if d.get("cost_driver_name") == op.driver_name:
                    for c in d.get("cost_components", []):
                        if c.get("cost_component_name") == op.component_name:
                            if op.new_name:
                                c["cost_component_name"] = op.new_name.strip()
                            if op.new_quantity is not None:
                                try:
                                    c["quantity"] = int(op.new_quantity)
                                except ValueError:
                                    pass
                            if op.new_justification is not None:
                                c["justification"] = op.new_justification.strip()
                            break
                    break
        elif op.operation == "update_quantity":
            for d in structure.get("cost_drivers", []):
                if d.get("cost_driver_name") == op.driver_name:
                    for c in d.get("cost_components", []):
                        if c.get("cost_component_name") == op.component_name:
                            c["quantity"] = int(op.new_quantity) if op.new_quantity else 1
                            break
                    break

    elif op.entity_type == "input":
        if op.operation == "delete":
            for d in structure.get("cost_drivers", []):
                if d.get("cost_driver_name") == op.driver_name:
                    for c in d.get("cost_components", []):
                        if c.get("cost_component_name") == op.component_name:
                            c["cost_inputs"] = [
                                i for i in c.get("cost_inputs", [])
                                if i.get("cost_input_name") != op.input_name
                            ]
                            break
                    break
        elif op.operation == "add":
            if not op.new_name:
                raise HTTPException(status_code=400, detail="new_name required for add input")
            qty = 1
            try:
                if op.new_quantity:
                    qty = int(op.new_quantity)
            except ValueError:
                pass
            new_inp = {
                "cost_input_name": op.new_name.strip(),
                "quantity": qty,
                "justification": (op.new_justification or "").strip() or "User-added input",
                "formula": "",
                "formula_justification": "",
                "monthly_cost_egp": None,
                "cost_parameters": [],
            }
            for d in structure.get("cost_drivers", []):
                if d.get("cost_driver_name") == op.driver_name:
                    for c in d.get("cost_components", []):
                        if c.get("cost_component_name") == op.component_name:
                            c.setdefault("cost_inputs", []).append(new_inp)
                            break
                    break
        elif op.operation == "edit":
            for d in structure.get("cost_drivers", []):
                if d.get("cost_driver_name") == op.driver_name:
                    for c in d.get("cost_components", []):
                        if c.get("cost_component_name") == op.component_name:
                            for inp in c.get("cost_inputs", []):
                                if inp.get("cost_input_name") == op.input_name:
                                    if op.new_name:
                                        inp["cost_input_name"] = op.new_name.strip()
                                    if op.new_quantity is not None:
                                        try:
                                            inp["quantity"] = int(op.new_quantity)
                                        except ValueError:
                                            pass
                                    if op.new_justification is not None:
                                        inp["justification"] = op.new_justification.strip()
                                    break
                            break
                    break
        elif op.operation == "update_quantity":
            for d in structure.get("cost_drivers", []):
                if d.get("cost_driver_name") == op.driver_name:
                    for c in d.get("cost_components", []):
                        if c.get("cost_component_name") == op.component_name:
                            for inp in c.get("cost_inputs", []):
                                if inp.get("cost_input_name") == op.input_name:
                                    inp["quantity"] = int(op.new_quantity) if op.new_quantity else 1
                                    break
                            break
                    break
        elif op.operation == "update_formula":
            for d in structure.get("cost_drivers", []):
                if d.get("cost_driver_name") == op.driver_name:
                    for c in d.get("cost_components", []):
                        if c.get("cost_component_name") == op.component_name:
                            for inp in c.get("cost_inputs", []):
                                if inp.get("cost_input_name") == op.input_name:
                                    inp["formula"] = (op.new_formula or "").strip()
                                    if op.new_formula_justification is not None:
                                        inp["formula_justification"] = op.new_formula_justification.strip()
                                    break
                            break
                    break
        elif op.operation == "update_cost":
            for d in structure.get("cost_drivers", []):
                if d.get("cost_driver_name") == op.driver_name:
                    for c in d.get("cost_components", []):
                        if c.get("cost_component_name") == op.component_name:
                            for inp in c.get("cost_inputs", []):
                                if inp.get("cost_input_name") == op.input_name:
                                    inp["monthly_cost_egp"] = op.new_cost if op.new_cost is not None else 0
                                    break
                            break
                    break

    elif op.entity_type == "parameter":
        if op.operation == "delete":
            for d in structure.get("cost_drivers", []):
                if d.get("cost_driver_name") == op.driver_name:
                    for c in d.get("cost_components", []):
                        if c.get("cost_component_name") == op.component_name:
                            for inp in c.get("cost_inputs", []):
                                if inp.get("cost_input_name") == op.input_name:
                                    params = inp.get("cost_parameters") or []
                                    inp["cost_parameters"] = [
                                        p for p in params
                                        if (p.get("parameter_name") or "").strip() != (op.parameter_name or "").strip()
                                    ]
                                    break
                            break
                    break
        elif op.operation == "add":
            if not op.new_name:
                raise HTTPException(status_code=400, detail="new_name required for add parameter")
            new_param = {
                "parameter_name": op.new_name.strip(),
                "justification": (op.new_justification or "").strip(),
                "monthly_cost_egp": None,
                "cost_justification": "",
                "source_urls": [],
            }
            for d in structure.get("cost_drivers", []):
                if d.get("cost_driver_name") == op.driver_name:
                    for c in d.get("cost_components", []):
                        if c.get("cost_component_name") == op.component_name:
                            for inp in c.get("cost_inputs", []):
                                if inp.get("cost_input_name") == op.input_name:
                                    inp.setdefault("cost_parameters", []).append(new_param)
                                    break
                            break
                    break
        elif op.operation == "edit":
            for d in structure.get("cost_drivers", []):
                if d.get("cost_driver_name") == op.driver_name:
                    for c in d.get("cost_components", []):
                        if c.get("cost_component_name") == op.component_name:
                            for inp in c.get("cost_inputs", []):
                                if inp.get("cost_input_name") == op.input_name:
                                    for p in inp.get("cost_parameters") or []:
                                        if (p.get("parameter_name") or "").strip() == (op.parameter_name or "").strip():
                                            if op.new_name:
                                                p["parameter_name"] = op.new_name.strip()
                                            if op.new_justification is not None:
                                                p["justification"] = op.new_justification.strip()
                                            if op.new_parameter_cost is not None:
                                                p["monthly_cost_egp"] = op.new_parameter_cost
                                                mark_parameter_override(p, op.new_parameter_cost)
                                            break
                                    break
                            break
                    break
        elif op.operation == "update_cost":
            for d in structure.get("cost_drivers", []):
                if d.get("cost_driver_name") == op.driver_name:
                    for c in d.get("cost_components", []):
                        if c.get("cost_component_name") == op.component_name:
                            for inp in c.get("cost_inputs", []):
                                if inp.get("cost_input_name") == op.input_name:
                                    for p in inp.get("cost_parameters") or []:
                                        if (p.get("parameter_name") or "").strip() == (op.parameter_name or "").strip():
                                            # None means "clear this", not "it costs
                                            # nothing" — writing 0 here would turn an
                                            # unknown into a free line item.
                                            p["monthly_cost_egp"] = op.new_parameter_cost
                                            mark_parameter_override(p, op.new_parameter_cost)
                                            break
                                    break
                            break
                    break

    recompute_derived_costs(structure)
    return {"structure": structure, "message": "OK"}


# --- FastAPI app ---

app = FastAPI(title="D2C Cost Estimation")

STATIC_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def revalidate_ui_assets(request: Request, call_next):
    """Stop the browser running yesterday's JavaScript against today's API.

    StaticFiles sends an ETag but no Cache-Control, which leaves browsers free to
    apply heuristic caching and reuse a stale script.js without asking — the failure
    that the `?v=` query string on one of the script tags was working around. The
    ETag still makes revalidation cheap: unchanged files come back 304 with no body.
    """
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/") or path in ("/", "/chat", "/db-viewer"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.post("/run")
async def run_estimation(body: RunRequest):
    step = (body.step or "drivers").lower()
    if step not in ("plan", "drivers", "components", "inputs", "parameters", "costs"):
        raise HTTPException(status_code=400, detail="step must be one of: plan, drivers, components, inputs, parameters, costs")
    if step not in ("plan", "drivers") and not body.structure:
        raise HTTPException(status_code=400, detail="structure required when step is not 'plan' or 'drivers'")
    return StreamingResponse(
        sse_generator(body.activity_description, step=step, initial_structure=body.structure,
                      activity_facts=body.activity_facts),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.post("/entity/operation")
async def entity_operation(body: EntityOperation):
    try:
        result = handle_entity_operation(body)
        return JSONResponse(content=result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/export-pptx")
async def export_pptx(body: ExportPptxRequest):
    """Generate PowerPoint tree diagram from current structure. Returns file attachment."""
    if structure_to_rows is None or build_pptx_from_rows is None:
        raise HTTPException(status_code=503, detail="PPTX export not available (to_pptx not found)")
    structure = body.structure
    if not structure or not structure.get("cost_drivers"):
        raise HTTPException(status_code=400, detail="No cost structure to export")
    try:
        rows = structure_to_rows(structure)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid structure: {e}")
    if not rows:
        raise HTTPException(status_code=400, detail="No rows to export")
    try:
        pptx_bytes = build_pptx_from_rows(rows)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to build PPTX: {e}")
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    name = (body.name or "Cost_Estimation").strip()[:80]
    safe_name = re.sub(r"[^a-zA-Z0-9_\-\s]", "_", name).strip() or "Cost_Estimation"
    safe_name = re.sub(r"\s+", "_", safe_name)
    filename = f"{safe_name}_{ts}.pptx"
    return Response(
        content=pptx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- SQLite DB export ---

# Same file the sessions table lives in — see d2c_app/sessions_db.py.
DB_PATH = sessions_db.DB_PATH

CSV_COLUMNS = [
    "cost_driver", "cost_driver_justification",
    "cost_component", "cost_component_justification", "cost_component_quantity",
    "cost_input", "cost_input_justification", "cost_input_quantity",
    "cost_parameter", "cost_parameter_justification",
    "formula", "cost", "cost_status", "cost_justification", "cost_source",
]


def _init_db():
    """Create the exports table if it doesn't exist, and bring an older one forward."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS estimations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            activity_name TEXT NOT NULL,
            exported_at TEXT NOT NULL,
            cost_driver TEXT,
            cost_driver_justification TEXT,
            cost_component TEXT,
            cost_component_justification TEXT,
            cost_component_quantity TEXT,
            cost_input TEXT,
            cost_input_justification TEXT,
            cost_input_quantity TEXT,
            cost_parameter TEXT,
            cost_parameter_justification TEXT,
            formula TEXT,
            cost TEXT,
            cost_status TEXT,
            cost_justification TEXT,
            cost_source TEXT
        )
    """)
    # CREATE TABLE IF NOT EXISTS leaves a table made by an earlier version untouched,
    # so a database created before cost_status existed would reject every insert.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(estimations)")}
    if "cost_status" not in columns:
        conn.execute("ALTER TABLE estimations ADD COLUMN cost_status TEXT")
    conn.commit()
    conn.close()


def _fmt_status(status, basis, has_cost):
    """
    How much weight an exported figure can bear.

    The engine publishes a number even when it is not confident — a median of
    disagreeing sources, the price of the closest comparable item, an internal
    benchmark, or its own unsourced judgement. Archived rows outlive the session
    that produced them, so the caveat is stored as its own field rather than left
    buried in the justification prose.
    Mirrors fmtStatus() in d2c_app/export.js.
    """
    if status == "needs_analyst_input":
        return "PROVISIONAL - sources disagree, median shown" if has_cost else "Not estimated - sources disagree"
    if status == "insufficient_evidence":
        if not has_cost:
            return "Not estimated - no usable evidence"
        if basis == "model_judgement":
            return "UNSOURCED - model estimate, no evidence found"
        if basis == "internal_benchmark":
            return "PROVISIONAL - internal historical records only"
        return "PROVISIONAL - closest comparable item"
    if status == "user_set":
        return "Set by analyst"
    return ""


def _fmt_cost(val):
    if val is None:
        return ""
    try:
        n = float(val)
        return f"{n:,.0f} EGP" if n > 0 else ""
    except (ValueError, TypeError):
        return ""


def _fmt_source(obj):
    if obj is None:
        return ""
    if isinstance(obj, list):
        return " | ".join(str(x).strip() for x in obj if x)
    return str(obj).strip()


def _build_db_rows(structure):
    """Build flat rows from the cost structure (same logic as export.js buildRows).

    A level that has not been generated yet still gets its own row, so a driver whose
    components never expanded is archived rather than silently dropped.
    """
    rows = []
    for dr in structure.get("cost_drivers", []):
        dname = dr.get("cost_driver_name", "")
        djust = dr.get("justification", "")
        components = dr.get("cost_components", [])
        if not components:
            rows.append((dname, djust) + ("",) * 13)  # 15 columns in total
            continue
        for comp in components:
            cname = comp.get("cost_component_name", "")
            cjust = comp.get("justification", "")
            cqty = str(comp.get("quantity", "")) if comp.get("quantity") is not None else ""
            inputs = comp.get("cost_inputs", [])
            if not inputs:
                rows.append((dname, djust, cname, cjust, cqty) + ("",) * 10)
                continue
            for inp in inputs:
                iname = inp.get("cost_input_name", "")
                ijust = inp.get("justification", "")
                iqty = str(inp.get("quantity", "")) if inp.get("quantity") is not None else ""
                formula = inp.get("formula", "")
                params = inp.get("cost_parameters") or []
                if params:
                    for p in params:
                        pname = p.get("parameter_name", "")
                        pjust = p.get("justification", "")
                        pcost = _fmt_cost(p.get("monthly_cost_egp"))
                        pcjust = (p.get("cost_justification") or p.get("justification", "")).strip()
                        psource = _fmt_source(p.get("source_urls") or p.get("source_url"))
                        pstatus = _fmt_status(
                            p.get("estimate_status") or "", p.get("estimate_basis") or "", bool(pcost)
                        )
                        rows.append((dname, djust, cname, cjust, cqty, iname, ijust, iqty,
                                     pname, pjust, formula, pcost, pstatus, pcjust, psource))
                else:
                    icost = _fmt_cost(inp.get("monthly_cost_egp"))
                    icjust = (inp.get("cost_justification") or inp.get("justification", "")).strip()
                    isource = _fmt_source(inp.get("source_urls") or inp.get("source_url"))
                    istatus = _fmt_status(
                        inp.get("estimate_status") or "", inp.get("estimate_basis") or "", bool(icost)
                    )
                    rows.append((dname, djust, cname, cjust, cqty, iname, ijust, iqty,
                                 "", "", formula, icost, istatus, icjust, isource))
    return rows


@app.post("/export-db")
async def export_db(body: ExportDbRequest):
    """Save the cost structure into the SQLite database."""
    structure = body.structure
    if not structure or not structure.get("cost_drivers"):
        raise HTTPException(status_code=400, detail="No cost structure to export")
    rows = _build_db_rows(structure)
    if not rows:
        raise HTTPException(status_code=400, detail="No rows to export")
    name = (body.name or "Cost_Estimation").strip()[:80]
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _init_db()
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.executemany(
            """INSERT INTO estimations
               (activity_name, exported_at,
                cost_driver, cost_driver_justification,
                cost_component, cost_component_justification, cost_component_quantity,
                cost_input, cost_input_justification, cost_input_quantity,
                cost_parameter, cost_parameter_justification,
                formula, cost, cost_status, cost_justification, cost_source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(name, ts) + row for row in rows],
        )
        conn.commit()
        count = len(rows)
    finally:
        conn.close()
    return JSONResponse(content={"message": f"Saved {count} rows to database", "rows": count})


@app.get("/db-viewer", response_class=HTMLResponse)
async def db_viewer():
    """Serve the database viewer page."""
    return (STATIC_DIR / "db_viewer.html").read_text(encoding="utf-8")


@app.get("/api/estimations")
async def get_estimations():
    """Return all saved estimations grouped by activity_name + exported_at."""
    _init_db()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM estimations ORDER BY exported_at DESC, id ASC"
        ).fetchall()
    finally:
        conn.close()
    result = []
    for row in rows:
        result.append(dict(row))
    return JSONResponse(content=result)


# --- Estimation sessions ---
#
# Defined with `def` rather than `async def` on purpose: every one of these blocks on
# SQLite, and creating a session also blocks on the titling model. FastAPI runs sync
# handlers in a threadpool, so blocking here cannot stall the event loop that is
# streaming a running estimation to the browser.


@app.get("/api/sessions")
def api_list_sessions():
    """Every saved session, most recently modified first (no structure blobs)."""
    return JSONResponse(content=sessions_db.list_sessions())


@app.post("/api/sessions", status_code=201)
def api_create_session(body: SessionCreate):
    """Start a session for a submitted description, titled by the model."""
    description = (body.activity_description or "").strip()
    if not description:
        raise HTTPException(status_code=400, detail="activity_description is required")
    # generate_title() falls back to a title derived from the description rather than
    # raising, so an unreachable model costs a good title and never the session.
    title = generate_title(description)
    session = sessions_db.create_session(title, description, body.activity_facts)
    return JSONResponse(content=session, status_code=201)


@app.get("/api/sessions/{session_id}")
def api_get_session(session_id: str):
    """One session including its full cost structure — what reopening a session loads."""
    session = sessions_db.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return JSONResponse(content=session)


@app.patch("/api/sessions/{session_id}")
def api_update_session(session_id: str, body: SessionUpdate):
    """Rename a session, or save the progress of one."""
    provided = _provided_fields(body)
    updates = {}

    if "title" in provided:
        title = " ".join((body.title or "").split())
        if not title:
            raise HTTPException(status_code=400, detail="title cannot be empty")
        updates["title"] = title[:MAX_SESSION_TITLE_CHARS]

    for field in ("activity_description", "activity_facts", "structure", "step_completed"):
        if field in provided:
            updates[field] = getattr(body, field)

    session = sessions_db.update_session(session_id, **updates)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return JSONResponse(content=session)


@app.delete("/api/sessions/{session_id}")
def api_delete_session(session_id: str):
    if not sessions_db.delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return JSONResponse(content={"deleted": session_id})


@app.get("/chat", response_class=HTMLResponse)
async def chat_page():
    return (STATIC_DIR / "chat.html").read_text(encoding="utf-8")


def _chat_response(state, **overrides) -> ChatResponse:
    """One turn of the interview as the browser receives it.

    ``turn_view`` is the whole interface between the intake graph and this app; everything
    here beyond renaming ``reply`` is the two fields the estimator needs on the final turn.
    """
    view = chat_turn_view(state)
    payload = {
        "bot_message": view["reply"],
        "done": view["done"],
        "suggestions": view["suggestions"],
        "facts": view["facts"],
        "new_fact_keys": view["new_fact_keys"],
        "classification": view["classification"],
        "progress": view["progress"],
        "phase": view["phase"],
        "description": view["description"],
    }
    return ChatResponse(**{**payload, **overrides})


@app.post("/chat/init")
async def chat_init():
    import uuid
    session_id = str(uuid.uuid4())
    state = new_chat_session()
    chat_sessions[session_id] = state
    return {"session_id": session_id, **_chat_response(state).model_dump()}


# Deliberately `def`, not `async def`: the conversation model's calls are blocking, and the
# turn that writes the description additionally runs the writer tier. Left on the event loop
# a single slow turn would stall every other request in the process; FastAPI runs sync
# routes in a threadpool.
@app.post("/chat/message")
def chat_message(body: ChatMessage):
    if body.session_id not in chat_sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    user_message = body.message.strip()
    # Still the full view, not a bare sentence: the panel is redrawn from whatever comes
    # back, so a short payload here would empty it. Nothing moved, so nothing highlights.
    if not user_message:
        return _chat_response(
            chat_sessions[body.session_id],
            bot_message="Could you tell me a bit more?",
            new_fact_keys=[],
        )

    state = run_chat_turn(chat_sessions[body.session_id], user_message)
    chat_sessions[body.session_id] = state

    # The interview ends silently — the analyst has already read the description and
    # accepted it, so the graph has nothing left to say. This app's own words go here
    # rather than in the graph, which does not know what happens to the text next.
    if state.get("done"):
        return _chat_response(
            state,
            bot_message="Description ready — adding it to the estimator.",
            activity_description=state.get("description") or "",
            activity_facts=chat_activity_facts(state),
        )

    return _chat_response(state)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
