# activity_intake

The conversation that produces the activity description, rebuilt as a LangGraph state
machine. It classifies the service first, then runs only the interview that kind of service
needs, and hands the analyst a description they can question, edit, rewrite or submit.

Replaced `activity_intake_chat.py`, which walked every analyst through the same eight steps
whatever they were buying.

```python
from agents.activity_intake import new_session, run_turn, turn_view

state = new_session()
state = run_turn(state, "we need 24x7 NOC monitoring for 50 network elements")
view  = turn_view(state)          # everything the UI renders this turn
...
state["description"]              # the output, once view["done"] is True
```

`d2c_app/app.py` drives it from `/chat/init` and `/chat/message`, holding one `state` per
browser session and handing `state["description"]` and `activity_facts(state)` to the
estimator on the turn the analyst accepts the draft.

Try it in a terminal, from the project root:

```
python agents/activity_intake/cli.py        # the interview, printing what the UI would show
python agents/activity_intake/selftest.py   # 38 checks, no model calls, free to run
```

Both run as `python -m activity_intake.cli` from inside `agents/` too; run as loose scripts
they find their package through a PEP 366 bootstrap at the top of each. Nothing else in the
package does, and nothing else should: the rest is imported, not executed.

### `APIConnectionError: Connection error.` on every turn

Not the code. This machine has a Windows system proxy configured — `Proxylb:5110` under
*Internet Settings* — and Python honours it through `urllib.request.getproxies()` even
though no `HTTPS_PROXY` variable is set, so there is nothing in the environment to point at.
When the VPN is down that hostname does not resolve and every call dies at DNS before it
reaches Azure. Check it with:

```
python -c "import urllib.request; print(urllib.request.getproxies())"
nslookup Proxylb
```

If the proxy host does not resolve, reconnect the VPN. If Azure OpenAI should bypass the
proxy entirely, set `NO_PROXY=.openai.azure.com,.services.ai.azure.com` — an explicit
variable wins over the registry setting.

---

## The shape of it

```
START ─→ classify ─→ scope ─┬─→ labour ───────┐
                            ├─→ deliverable ──┤
                            ├─→ transaction ──┼─→ commercials ─→ write ─→ END
                            ├─→ rights ───────┤
                            └─→ passthrough ──┘

(resume) ─→ review ─→ revise | write | END
```

`classify` places the service in the Vodafone Egypt category tree, which gives it a Cost
Driver Library family and a **model spine**. The spine decides which of the five middle
nodes runs, and they are not variations on a theme — a rights deal is never asked how many
FTE it needs, and a labour service is never asked about territory or exclusivity. The Cost
Driver Library names getting this wrong as the commonest failure in the domain: *"running a
full FTE build-up on a celebrity endorsement produces a confident, meaningless number."*

A deal that genuinely mixes spines — a sponsorship with its own activation crew — runs a
second block on roughly half the budget, then consolidates.

### Two kinds of skipping

**Between stages**, by spine: a rights deal runs five stages, not nine.

**Inside a stage**, by sufficiency: each stage reports itself finished once its essentials
are answered or explicitly defaulted, so a brief that already fixes scale, period and
geography passes straight through `scope` without a question. Saying *"use the defaults"*
closes everything still open in that stage; saying *"write it now"* ends the interview
wherever it has got to.

In practice that is **three to six exchanges**, against eight mandatory steps before.

### What each stage asks

| Stage | Source | Budget |
|---|---|---|
| `classify` | Category tree + Cost Driver Library Part 3 | 2 |
| `scope` | Copilot Instructions, Step 1 | 3 |
| `labour` / `deliverable` / `transaction` / `rights` / `passthrough` | Skill Specification §4, the five skill prompts | 4 |
| `commercials` | Copilot Instructions Steps 7–8, Cost Driver Library Part 8 | 2 |

Budgets are exchanges, not questions — a turn carries up to three — and the last one is
spent finishing rather than asking, so `budget=4` is three chances to ask.

---

## What the UI gets every turn

`turn_view(state)` returns everything to render:

```python
{
  "reply":       "...",              # the message, with any stage handoff already joined in
  "suggestions": [...],              # quick-reply chips, concrete and specific to this service
  "facts":       [ {"stage", "label", "facts": [...]} ],   # grouped by capturing stage
  "new_fact_keys": ["site_count"],   # what moved THIS turn, for the highlight
  "classification": {"category_code", "category_name", "family_code", "family_name",
                     "spine", "spine_name", "secondary_spines"},
  "progress":    {"stage", "stage_label", "completed", "plan", "position", "total"},
  "description": "...",              # populated from the review phase onward
  "phase":       "interview|review|done",
  "done":        False,
}
```

Every fact carries `key`, `label`, `value`, `unit`, `stage` and **`source`** — one of
`user`, `default` or `inferred`. Badge the last two. The Skill Specification requires
defaults to be labelled and logged, and an analyst scanning the panel needs to see at a
glance which numbers are theirs.

`progress.plan` is the interview this conversation is actually getting, so the indicator can
say "3 of 5" honestly instead of "step 2 of 8" when six of the eight will never run.

---

## The review phase

Once the description is written, four things can happen to it, offered as chips:

| | |
|---|---|
| **Submit** | accept it; `done` goes true and `state["description"]` is final |
| **Edit** | *"make it 18 stores"* — targeted change, everything else untouched, and the facts panel updates too |
| **Rewrite** | *"too long, start again"* — fresh draft from the facts |
| **Discuss** | *"why 4.9 FTE per guard post?"* — answered from the facts and the interview, description untouched |

Free text is classified into one of the four, so the chips are a shortcut rather than the
only way in.

---

## The description

The model writes the narrative and the assumptions. Everything else is composed in code from
the classification — the spine, the unit of measure, the build rule the estimator applies,
and the benchmark bands it validates against — because those are facts about the method, are
already correct in `knowledge.py`, and a model asked to restate them will eventually restate
one of them wrongly.

```
<narrative, four to seven paragraphs>

Modelling basis: Labour-built.
Service category: 2.3 Network operations centre
Cost driver family: C6 NOC / SOC / managed services.
Unit of measure: Cost per monitored element per month; per ticket; per seat.
Build rule for the estimator: Coverage FTE = (weekly coverage hours x 52) / net productive...
Validate against: 24x7 manned post 4.6-5.2 FTE; indirect headcount 8-20% on a complex...

Assumptions to be validated:
- ...
```

Every fact the interview defaulted reaches that register. The writer is asked to carry them
and usually does in its own words; `_carry_defaults` adds only the ones it dropped, matching
on vocabulary rather than on labels so a rephrasing is not listed twice. The analyst is
accepting all of them by submitting, and a default that never made it onto the page is one
nobody agreed to.

---

## Files

| | |
|---|---|
| `__init__.py` | `new_session` · `run_turn` · `turn_view` · `activity_facts` |
| `graph.py` | the state machine, the routers, the stage node |
| `agendas.py` | what each stage elicits; the five skill prompts, arithmetic removed |
| `knowledge.py` | **generated** — 88 subcategories, 32 cost-driver families |
| `state.py` | the session dict, fact merging, the per-turn reducers |
| `writer.py` | the description, and the four things the analyst can do with it |
| `selftest.py` | 38 checks, no model calls |
| `cli.py` | terminal harness |

### Regenerating the knowledge base

`knowledge.py` is built from the two source documents. After either is revised:

```
python agents/build_intake_knowledge.py
python agents/activity_intake/selftest.py   # catches a dangling family ref or a lost spine tag
```

It is code rather than a vector index on purpose. The whole library is about 12,000 tokens,
and the interview needs exactly one family out of thirty-four, chosen deterministically from
a classification the model has already committed to. Retrieval would answer that less
reliably, put an embedding call on the critical path of every turn, and need rebuilding on
every revision.

---

## Models

| | | |
|---|---|---|
| **Conversation** | `gpt-5.2-chat` | classification, extraction, deciding what to ask, review intent, targeted edits |
| **Writer** | `gpt-5`, `reasoning_effort="low"` | the first draft and full rewrites |

Two tiers, split on who is waiting. The conversation tier runs between the analyst pressing
send and the reply appearing; the writer runs once at the end, and everything downstream is
built from what it produces. `gpt-4o-mini` was tried in the predecessor and is documented
there as unable to make the judgement this graph opens with. A targeted **edit** sits on the
conversation tier deliberately — it is a local change to text that already exists, and the
reasoning tier took around twenty-five seconds to make it while the analyst watched a
spinner.

There is no embeddings tier; see *Regenerating the knowledge base* above.

Both clients are built by `agents/env_config.py`, the one place in this project where a
credential or an endpoint enters the process, from the project-root `.env`:

```
AZURE_OPENAI_CONVERSATION_API_KEY   AZURE_OPENAI_CONVERSATION_BASE_URL
AZURE_OPENAI_CONVERSATION_MODEL

AZURE_OPENAI_WRITER_API_KEY   AZURE_OPENAI_WRITER_ENDPOINT
AZURE_OPENAI_WRITER_DEPLOYMENT   AZURE_OPENAI_WRITER_API_VERSION
```

Only the conversation key is required; the whole writer tier defaults to the reasoning tier
the rest of the estimator already runs on, which by default is the same gpt-5 deployment.
Both clients are built on first use rather than at import, so `selftest.py` runs on a
machine with no `.env` at all.

---

## Memory

Three stores, which degrade differently.

**`history`** is the transcript and answers *"what did I just tell you?"*. Replayed each
turn, window-trimmed at 20 messages so a long interview does not grow its own latency.

**`facts`** is the extracted understanding, and it is what survives that trimming. A fact
outlives the message that produced it, is corrected in place when the analyst changes their
mind, and is the only thing the description is written from.

**`asked`** is the ledger of topics already put to the analyst. It exists because the
alternative failed: a model that knows a topic is unanswered reaches for it again as the most
material thing left, and being asked the same question in fresh wording is what makes these
conversations feel like they are not listening. A topic that was asked is closed whether it
was answered or waved through; if it was waved through, the default recorded against it *is*
the answer.

The whole state is plain JSON — dicts, lists, strings — so a session is written to the
session store between turns and read back by whichever worker gets the next request. There is
deliberately no checkpointer and no `interrupt()`: the HTTP layer already holds per-session
state, and the explicit-state form keeps the graph directly testable and insensitive to
langgraph's version-to-version interrupt semantics.
