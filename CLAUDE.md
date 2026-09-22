# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**D2C (Design-to-Cost)** is an AI-powered cost estimation web application for Vodafone Egypt. Given a telecom activity description, it progressively generates a hierarchical cost breakdown using LLM agents with RAG (FAISS vector DBs) and web search.

## Running the Application

```bash
# From project root:
uvicorn d2c_app.app:app --reload --host 0.0.0.0 --port 8000
# Open: http://localhost:8000
```

## Testing Individual Agents (CLI)

```bash
# Full end-to-end workflow test with CSV output:
python agents/workflow_test.py

# Individual agent modules are also directly runnable:
python agents/cost_driver.py
python agents/cost_component.py

# The activity-intake interview, in a terminal, printing what the UI would render:
python agents/activity_intake/cli.py

# Its 38 checks. No model calls, no credentials needed, free to run:
python agents/activity_intake/selftest.py
```

Apart from `agents/activity_intake/selftest.py` there are no automated test suites, and
there is no `requirements.txt` and no linting configuration. Dependencies (FastAPI, langchain, azure-openai, faiss, etc.) must be installed manually.

## Architecture

### Estimation Hierarchy

The 7-step workflow progressively decomposes an activity into costs:

```
Activity Description
  → Resource Plan      (volume driver, duration, crews, resource pool quantities + scaling classes)
    → Cost Drivers       (e.g., "Shelters", "Transportation")
      → Cost Components  (e.g., "Prefabricated Cabins")
        → Cost Inputs    (e.g., "Prefabricated Cabin Cost")
          → Cost Parameters + Formulas  (e.g., unit price, quantity)
            → Final Cost Estimation (EGP/month)
```

The **resource plan** (`agents/resource_planner.py`, `/run?step=plan`) is the authoritative source of
quantities. Every resource is classified by scaling behavior — `per_unit_of_volume`, `capacity_pool`
(shared pools like trucks/cranes, sized by crews not volume), `time_based`, `fixed_one_time` — with a
written derivation, and reviewed by a deterministic + LLM critic pass. It is stored in
`structure["resource_plan"]`, shown as an editable table in the UI before the cost steps run, and
formula literals at the cost-parameter step must be traceable to it.

**Unit discipline**: each cost parameter declares a pricing `unit` (e.g. "EGP per truck per day");
the estimator prices it in that exact unit and the formula does all conversion to monthly.
`validate_formula_units()` in `cost_parameter.py` emits `unit_warnings` shown in the UI.

**Currency and period conversion is never asked of a model.** Prompts ask for the price *as the
source quotes it* — value, currency, period — and the arithmetic happens in code
(`to_monthly_egp()` in `cost_estimation.py`, `normalize_observation()` in `pricing_normalize.py`,
live rates via `pricing_fx.live_rate_to_egp`). Asking for "EGP per month" directly made estimates
depend on the model knowing today's rate, and honest models declined rather than guess.

**Every estimate carries how much weight it can bear.** The gate publishes a number on each rung of
a fallback ladder, with `estimate_status` and `estimate_basis` saying which rung it came from:

| basis | status | meaning |
|---|---|---|
| `weighted_median` | `estimated` | robust median of qualifying evidence — the only settled figure |
| `median_of_disagreeing_sources` | `needs_analyst_input` | sources contradict; median shown pending review |
| `closest_match` | `insufficient_evidence` | too little evidence; price of the closest comparable item |
| `internal_benchmark` | `insufficient_evidence` | no web evidence; figure from internal historical records |
| `model_judgement` | `insufficient_evidence` | no evidence anywhere; the model's own unsourced benchmark |
| `user_override` | `user_set` | an analyst typed it in (parameter cost is editable in the detail modal) |

Anything below the first rung is badged in the tree, counted in the "includes N provisional figures"
note under the grand total, and exported in the `cost_status` column. Set
`D2C_ALLOW_MODEL_JUDGEMENT=0` to leave the bottom rung blank instead.

### Key Files

| File | Purpose |
|---|---|
| `d2c_app/app.py` | FastAPI server; all routes, SSE streaming, session state, workflow orchestration |
| `d2c_app/index.html` + `script.js` | Main UI: tree view, step progression, entity editing |
| `d2c_app/crud.js` | CRUD modals for adding/editing/deleting cost entities |
| `d2c_app/sessions_db.py` | SQLite store for estimation sessions (schema + CRUD helpers) |
| `d2c_app/sessions.js` | Sidebar session list, rename/delete, and the debounced autosave |
| `agents/session_title.py` | LLM agent: activity description → short session title |
| `d2c_app/chat.html` + `chat.js` | Conversational assistant for building activity descriptions: the facts drawer, the suggestion chips and the progress line |
| `d2c_app/export.js` | Excel (2 sheets: "Cost Estimation" + "Resources"), PowerPoint and database export |
| `agents/env_config.py` | Loads `.env`; the only place credentials/endpoints enter the process. Builds all LLM + embeddings clients |
| `agents/resource_planner.py` | LLM agent: activity → resourcing & scaling plan (quantities, scaling classes, critic pass) |
| `agents/norms.py` | Runtime lookup of normalized historical resource ratios (norms/resource_norms.json) |
| `agents/build_norms.py` | Offline script: extracts normalized ratios from FAISS docstores into the norms file |
| `agents/cost_driver.py` | LLM agent: activity → cost drivers |
| `agents/cost_component.py` | LLM agent: driver → cost components |
| `agents/cost_inputs.py` | LLM agent: component → cost inputs |
| `agents/cost_parameter.py` | LLM agent: input → cost parameters + formulas |
| `agents/cost_estimation.py` | LLM agent: parameters → final EGP cost with sources |
| `agents/activity_intake/` | The AI Assistant: LangGraph intake interview → facts → activity description. Has its own `README.md` |
| `agents/build_intake_knowledge.py` | Offline script: regenerates `activity_intake/knowledge.py` from the category tree and the Cost Driver Library |
| `agents/description_extraction.py` | Superseded questionnaire flow; kept only for its CLI |
| `agents/structure_formatter.py` | Unified display formatter for any stage of the hierarchy |
| `agents/workflow_test.py` | CLI end-to-end test script |
| `new_vector_dbs/` | FAISS vector stores (one per hierarchy level) used for RAG |

### API Endpoints

- `GET /` — Serves `index.html`
- `POST /run?step=<step>` — Runs a workflow step (`plan | drivers | components | inputs | parameters | costs`); streams SSE events (`progress`, `structure`, `done`)
- `POST /entity/operation` — CRUD on cost entities (add/edit/delete/update)
- `POST /export-pptx` — PowerPoint export
- `GET /api/sessions` — List saved sessions, most recently modified first (no structure blobs)
- `POST /api/sessions` — Create a session from a description; the title comes from the model
- `GET /api/sessions/{id}` — One session including its full cost structure
- `PATCH /api/sessions/{id}` — Rename, or save progress. Only the fields sent are written
- `DELETE /api/sessions/{id}` — Delete a session
- `GET /chat` — Chat UI
- `POST /chat/init` — Start an intake session; returns `session_id` plus the opening turn
- `POST /chat/message` — One interview turn: reply, suggestion chips, facts grouped by stage, classification, progress, and the description once it exists

### The AI Assistant (activity intake)

`agents/activity_intake/` is a LangGraph state machine, with its own `README.md` covering it
in full. It replaced `activity_intake_chat.py`, which handed the model a fixed eight-step
cost-modelling workflow as its system prompt and walked every analyst through all eight
steps whatever they were buying — asked to cost a celebrity endorsement, it would work out
how many FTE the campaign needed.

**The shape of the interview is a routing decision, taken once.** `classify` places the
service in the Vodafone Egypt category tree, which gives it a Cost Driver Library family and
a **model spine**; the spine decides which one of five middle stages runs:

```
START ─→ classify ─→ scope ─┬─→ labour ───────┐
                            ├─→ deliverable ──┤
                            ├─→ transaction ──┼─→ commercials ─→ write ─→ END
                            ├─→ rights ───────┤
                            └─→ passthrough ──┘

(resume) ─→ review ─→ revise | write | END
```

A rights deal is never asked how many FTE it needs; a labour service is never asked about
territory or exclusivity. Stages also finish early on sufficiency — a brief that already
fixes scale, period and geography passes through `scope` without a question. In practice
that is **three to six exchanges**, against eight mandatory steps before.

The reference data (`knowledge.py`: 88 subcategories, 32 cost-driver families) is generated
code rather than a vector index, because the interview needs exactly one family out of
thirty-four, chosen deterministically from a classification the model already committed to.
Regenerate it with `python agents/build_intake_knowledge.py` after either source document is
revised, then run `python agents/activity_intake/selftest.py` — 38 checks, no model calls,
which is what catches a dangling family reference or a lost spine tag.

**Two model tiers, split on who is waiting.** Every turn runs on the **conversation tier**
(`gpt-5.2-chat`) — the only model in the system with a person waiting in real time. The
description, and any full rewrite of it, runs on the **writer tier** (`gpt-5`), once, off the
critical path. A targeted *edit* sits on the conversation tier deliberately: it is a local
change to text that already exists, and the reasoning tier took ~25s to make it while the
analyst watched a spinner. Both clients are built on first use, not at import, so `selftest`
runs on a machine with no `.env`.

`turn_view(state)` is the whole interface to the UI, and `/chat/message` returns it verbatim
plus the two fields the estimator needs. It carries the reply, the **suggestion chips**, the
**facts** grouped by capturing stage, `new_fact_keys` for the highlight, the
**classification**, and a `progress.plan` that is the interview *this* conversation will
actually get — so the indicator says "3 of 5" honestly rather than "step 2 of 8" when six of
the eight will never run.

Every fact carries `source`: `user`, `default` or `inferred`. The last two are **badged** in
the facts drawer, because the analyst accepts every default by submitting the description
and a default nobody was shown is one nobody agreed to. The same defaults are carried into
the description's assumption register by `_carry_defaults`, which matches on vocabulary
rather than labels so a rephrasing is not listed twice. Facts are **merged, never replaced**,
with a rename-collapse guarded by a minimum value length — "No" answers half the questions
in this interview and collapsing on it would destroy real facts.

The assistant never asks what something costs. Working that out is the purpose of the system
this feeds, and an analyst who already knew would not be here — it asks about structure and
quantity only. The suggestion chips deliberately carry no "not sure" option from the model;
`chat.js` appends one, because a "not sure" on every question stops the chips being answers.

**The description is reviewed inside the conversation.** Once written, four things can
happen to it — **submit**, **edit** ("make it 18 stores" — targeted, and it updates the facts
panel too), **rewrite** ("too long, start again") and **discuss** ("why 4.9 FTE per guard
post?", answered without touching the text). Free text is classified into one of the four,
so the chips are a shortcut rather than the only way in. On submit, `state["description"]` is
the sole input to the estimation pipeline and `activity_facts(state)` flows on as
`activity_facts` to the resource planner.

The model writes the narrative and the assumptions; the modelling basis — spine, unit of
measure, the build rule the estimator applies, the benchmark bands it validates against — is
composed in code from the classification, because those are facts about the method that are
already correct in `knowledge.py`, and a model asked to restate them will eventually restate
one of them wrongly.

### Sessions

A session is one activity description plus everything derived from it, persisted to the `sessions`
table in `d2c_exports.db` so work survives closing the tab. One is created when the analyst clicks
"Start estimation"; re-running the plan on unchanged text stays in the open session rather than
duplicating it. `agents/session_title.py` names it with the chat-tier model and falls back to a
title derived from the description, so an unreachable model never costs the session.

`d2c_app/sessions.js` owns the sidebar list and persistence; `script.js` owns the live estimation.
They meet at exactly four hooks — `D2CSessions.startEstimation()` and `.noteChange()` in one
direction, `window.loadSessionState()` and `window.getEstimationState()` in the other. Autosave is
debounced and hangs off `buildTree()`, the single function every structural change flows through,
so streamed steps and CRUD edits are all covered by one call site.

Timestamps are stored as ISO-8601 UTC and rendered in local time by the browser.

Exports are named from the open session's title (editable in the export dialog before it runs), so
a workbook lands with the name the analyst gave the work. The Excel export writes two sheets: the
flat cost breakdown, and a "Resources" sheet carrying the whole resource plan — volume driver,
duration, crews, throughput, every resource with its scaling class, confidence, source, derivation
and justification, plus assumptions and the critic's review notes.

### SSE Streaming Pattern

The `/run` endpoint streams three event types:
```
event: progress  → {"message": "..."}
event: structure → {"structure": {...}}  # partial updates
event: done      → {"structure": {...}, "message": "..."}
```

Frontend in `script.js` listens to these and updates the tree in real time.

### Frontend State

Global state managed in `script.js`:
- `currentStructure` — full JSON cost hierarchy
- `currentActivity` — activity description string
- `currentStepCompleted` — tracks which workflow step was last completed
- `currentTreeDepth` — tree collapse depth for display

### Data Model (JSON)

```json
{
  "cost_drivers": [{
    "cost_driver_name": "...",
    "justification": "...",
    "cost_components": [{
      "cost_component_name": "...",
      "quantity": 1,
      "cost_inputs": [{
        "cost_input_name": "...",
        "quantity": 1,
        "formula": "price * quantity",
        "monthly_cost_egp": 180000.0,
        "cost_parameters": [{
          "parameter_name": "...",
          "monthly_cost_egp": 180000.0,
          "cost_justification": "...",
          "source_urls": ["..."]
        }]
      }]
    }]
  }]
}
```

### RAG Setup

Each agent loads a FAISS vector store from `new_vector_dbs/` using Azure OpenAI embeddings (`text-embedding-3-large`). The old `vector_dbs/` directory is deprecated (references are commented out).

### Important Constants

- `MAX_ENTITIES_PER_LEVEL = 20` in `app.py` (set to `4` in `workflow_test.py` for speed)

### Credentials

All credentials come from the environment via `agents/env_config.py`, which loads the project-root
`.env` (python-dotenv) when first imported — so every entry point picks it up: `uvicorn`,
`workflow_test.py`, or running an agent module directly. Copy `.env.example` to `.env` to set up.

Keys are required and fail loudly (`MissingCredential`, naming the variable) rather than
surfacing as a 401 mid-workflow. Endpoints, deployment names and API versions are optional and
default to the values the code previously hardcoded. Five non-interchangeable Azure deployments:
`chat` (gpt-4o-mini, structure steps), `reasoning` (gpt-5, planner/parameters/estimation/
pricing), `conversation` (gpt-5.2-chat, every turn of the AI Assistant interview), `writer`
(gpt-5, the activity description it ends on), and `embeddings` (text-embedding-3-large, the
FAISS retrievers). Each has its own API version — gpt-5 and gpt-5.2-chat need newer ones than
gpt-4o-mini, so they are deliberately not shared.

`conversation` is reached through the Azure AI Services v1 API, so it takes a base URL and a
model name rather than an endpoint, a deployment and an API version. `writer` defaults every
variable, key included, to the `reasoning` tier's — by default it *is* that deployment, and
it exists as its own tier so the intake can be moved without re-pricing every cost-parameter
call.

## Documentation Files

Additional `.md` files in `d2c_app/` and `agents/` document specific subsystems:
- `CRUD_GUIDE.md`, `USER_GUIDE.md`, `CHAT_FLOW_GUIDE.md`, `IMPROVEMENTS.md`, `EXPORT_IMPLEMENTATION.md`, etc.
