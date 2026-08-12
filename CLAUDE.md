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
```

There are no automated test suites, no `requirements.txt`, and no linting configuration. Dependencies (FastAPI, langchain, azure-openai, faiss, etc.) must be installed manually.

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
| `d2c_app/chat.html` + `chat.js` | Conversational assistant for building activity descriptions, plus the facts drawer |
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
| `agents/activity_intake_chat.py` | The AI Assistant: 8-step intake interview → facts → activity description |
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
- `POST /chat/init` — Initialize chat session
- `POST /chat/message` — Handle chat turn

### The AI Assistant (activity intake)

`agents/activity_intake_chat.py` runs an eight-step cost-modelling interview — service
definition, roles & staffing, working days, supervision, employment costs, operational
requirements, overheads & profit, finalisation. The workflow *is* the system prompt
(`WORKFLOW_PROMPT`), so the model holds the whole conversation itself: one question at a
time, free text only, no option chips.

**Nothing is appended to that prompt, and nothing should be.** It is byte-identical to the
workflow document; everything the app needs on top of it is carried by the output schema's
field descriptions and by code (`OPENING_MESSAGE`, `REVIEW_HINT` and `_is_submit` are the
app's own words and logic, and never enter the model's context — history starts empty). The
only thing the model is told beyond the workflow is today's date, and that goes in as its
own system message ahead of it (`_today_message`, computed per call so a long-running server
doesn't go stale) — without it, asked in Step 1 for a start date, it answers "next month"
with a date a year out. This
is a rule with a scar behind it: a block instructing the model to record only what the user
had explicitly stated, read next to the workflow's "Ask where the service(s) will be
delivered (country and region or city)", made it ask an analyst who had said "Cairo" which
country Cairo is in. House rules get read in the context of the workflow's own wording, and
what they do there is hard to predict. Test any prompt change against a real conversation.

It runs on its own Azure deployment — the **intake tier**, `gpt-chat-latest` — because it is
the only model in the system with a person waiting in real time on the other end.

Every turn returns a `ChatbotTurn`: the reply, where in the eight steps it is, and the
complete accumulated `facts` as `canonical_key -> value`. Facts are **merged, never
replaced** (`_merge_facts`) — the schema asks for the full set each turn, but a turn that
returns only what changed must not erase steps 1–7. Merging alone leaves a wart: the model
renames its own keys (`service` for what it earlier called `service_description`) and both
survive, showing one fact twice. A held key the turn did not return, whose value duplicates
one it did, is treated as that rename and dropped — but only for values of at least
`_ALIAS_MIN_LENGTH` characters, because "No" answers half the workflow's questions and
collapsing on it would destroy real facts. They are mirrored live into the **facts
drawer**, a rail on the left of the assistant window that opens into a read-only table; the
panel is redrawn wholesale from every response, so it holds no state and cannot drift from
the server's.

The analyst ends the intake by typing **submit** (accepted at any point once facts exist —
eight steps is a long interview and there has to be a way out). Once the model reports the
last step finished, `REVIEW_HINT` is appended to its reply by the app to say so. Bare
confirmations like "yes" or "confirmed" count as submit *only* after step 8 is declared complete,
because the workflow requires explicit confirmation whenever it applies a default and a
mid-flow "confirmed" would otherwise end the interview. On submit the facts — and only the
facts — go to the **reasoning tier** (`generate_activity_description`), which rewrites them
as the activity description; it runs once, off the critical path, and its output is the sole
input to the estimation pipeline. The facts dict also flows on as `activity_facts` to the
resource planner.

Two files the prompt names as preferred sources, `Working Days.xlsx` and
`National Statistics.xlsx`, are not in the repo; the model falls back to its own defaults for
those and must have them confirmed by the analyst.

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

Keys are required and fail loudly at import (`MissingCredential`, naming the variable) rather than
surfacing as a 401 mid-workflow. Endpoints, deployment names and API versions are optional and
default to the values the code previously hardcoded. Four non-interchangeable Azure deployments:
`chat` (gpt-4o-mini, structure steps), `reasoning` (gpt-5, planner/parameters/estimation/pricing/
description write-up), `intake` (gpt-chat-latest, the AI Assistant interview), and `embeddings`
(text-embedding-3-large, the FAISS retrievers). Each has its own API version — gpt-5 and
gpt-chat-latest need newer ones than gpt-4o-mini, so they are deliberately not shared.

## Documentation Files

Additional `.md` files in `d2c_app/` and `agents/` document specific subsystems:
- `CRUD_GUIDE.md`, `USER_GUIDE.md`, `CHAT_FLOW_GUIDE.md`, `IMPROVEMENTS.md`, `EXPORT_IMPLEMENTATION.md`, etc.
