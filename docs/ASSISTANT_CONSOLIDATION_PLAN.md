# Assistant consolidation — one chat engine, scoped surfaces, device-run plans

**Status:** step 1 implemented 2026-10-05 (extraction, no behaviour change).
Steps 2–5 wait on the rules amendment below. Reviewed twice by Codex
(GPT-6-Astra); its findings are folded in.
Supersedes the first draft of this file, which proposed a dashboard-side
step runner; that was dropped because it conflicts with AGENTIC_LAB_DESIGN
Part I rules 1 and 3 and UI_DESIGN §5.1, and is unsafe with per-request
claims (another session can act between steps without any refusal).

## Decision in one paragraph

Keep **one chat engine**, on the central host, as a new workspace package
`assistant/` (`lab-assistant`) in this monorepo. Every chat surface — the
dashboard bubble and each device panel — is the same engine with a **scope**
that fixes its toolset server-side. A device panel's scope sees only that
device's tools. **Plans are still drafted, approved and executed on the
device** for devices that implement the plan contract (today the OT-2
gateway); the engine only creates the draft there. The OT-2 gateway's
built-in LLM (`gateway/assistant.py`, `assistant_claude.py`) is retired.

## What does not change

- **UI_DESIGN §2.1:** no single agent that knows everything. A scope's tool
  surface *is* its trust level; scopes never union. A panel scope cannot
  reach another device because the tools are not there, not because a prompt
  says so.
- **UI_DESIGN §2.3:** the agent loop and tools run server-side; only
  stateless inference leaves the tailnet. This plan is the §2.3 "Known
  trade-off (tier 1)" remedy: inference moves central, resolution/execution
  stays in the gateway.
- **UI_DESIGN §5 for non-delegating devices:** single actions and browser-run
  plans keep their current behaviour, including "the human is supposed to be
  present". No dashboard code path sequences hardware steps.
- **The OT-2 approval gate:** approve and execute stay on the gateway, from a
  claim-holding session that matches the approver (`plans.py`
  `check_executable`). The engine never approves or runs.

## Target shape

```
browser (dashboard bubble | OT-2 panel bubble)        thin SSE clients
        │  POST /api/assistant/chat  {scope, messages}
        ▼
assistant/ (lab-assistant)   ── engine: backends, loop, streaming, sessions, prompts
        │  scope → toolset (MCP servers + per-scope tool filter)
        ├── lab-history / lab-inventory (read-only, as today)
        ├── lab-control (propose_action / propose_plan, as today)
        └── device plan tools (new): for a delegating device,
            propose_plan → POST <device>/plans (draft only)
                                   │
                                   ▼
                        OT-2 gateway: PlanStore / PlanExecutor
                        (approve + run in the panel; plan holds the claim;
                         results bundle → plan_results → BitacoraDB)
```

### Scopes

A scope is a **ceiling bound server-side**, never chosen per request. The
panel's chat route fixes the scope from the route itself
(`/api/assistant/equipment/<id>/chat`), and a conversation records its actor
and scope at creation; any later request naming a different scope or actor is
refused. Checks run at **tool invocation time** in every backend (claude-cli,
openai, hermes), not only when the tool list is built: the OpenAI loop
forwards arguments directly today (`openai_backend.py`), so filtering the
list alone would not be enough.

| scope | who uses it | toolset | modes |
|---|---|---|---|
| `dashboard` | dashboard bubble | today's: history, inventory, control (Control mode) | Ask, Control, Plan |
| `equipment:<id>` | that device's panel | that device's docs/status/deck/consumables/actions (read), `propose_plan` **for `<id>` only**, its plate reports. **No** lab-wide `lab-history` or `lab-inventory` tools (they read the whole fleet and write observations); device-filtered read tools only | Ask, Control |

- The engine validates `<id>` against the registry and refuses unknown or
  disabled equipment.
- For `equipment:<id>`, tool arguments that name an equipment id, plan id or
  report id are checked against `<id>` at invocation; any other value is
  refused, not rewritten silently.
- The scope is recorded on every session row and audit row; saved sessions
  and the panel's browser history are partitioned by actor and device.
- Identity is the edge-stamped `X-Auth-User`, as today. The panel is served
  from the same edge origin (`/ot2/<name>/ui/`), so its requests reach
  `/api/assistant/...` with the same session cookie and edge injection.

### The device plan contract

A device *delegates* if its running gateway serves `/docs/agent`,
`/plans/actions` and `POST /plans` (detected the way `_propose_plan` already
reads `/plans/actions` and `/openapi.json`). For such a device:

- Delegation requires **positive evidence**: the running gateway's
  `/plans/actions` and `/openapi.json` both advertise the contract. Missing or
  unreadable discovery means the existing (non-delegated) path, never a
  silent switch of execution model.
- `propose_plan` keeps every existing check (allowlists, forbidden fields,
  live identity/catalog validation, deck checks), then creates the draft
  through a fixed-target **SDK** draft operation (added to `lab-skills`), not
  hand-built HTTP. The gateway's `created_by` is caller-supplied, so the
  dashboard records the actor itself in a distinct `assistant_plan_delegated`
  audit row (actor, scope, device, device plan id, step hash).
- An uncertain create (timeout, lost response) is reported as uncertain and
  reconciled by listing the device's drafts; it is never retried blindly.
- No dashboard-side plan record and no dashboard approval or run: the
  dashboard bubble renders a delegated plan as "approve on the device panel"
  and must not offer Approve/Run for it.
- The chat card says where to approve: the device panel's plan list (which
  already shows plans proposed elsewhere). In the panel scope it is the same
  page, so approve/run work exactly as today.
- No gateway change is needed for drafting: `POST /plans` is already
  claim-free and propose-only callers may use it.

Non-delegating devices keep today's dashboard proposal path unchanged.

## Package layout and migration

`assistant/` workspace member (`[tool.uv.workspace] members` gains
`"assistant"`):

```
assistant/
  pyproject.toml           name = "lab-assistant"; depends on lab-skills, httpx, fastapi
  lab_assistant/
    engine.py              backend dispatch (claude-cli | openai | hermes), loop, SSE
    backends/claude_cli.py openai.py hermes.py
    sessions.py            Plan-mode store (moved, schema unchanged)
    prompts/               fragments; device rules come from /docs/agent at runtime
    scopes.py              scope → MCP server set + tool filter + mode set
    routes.py              build_assistant_router(deps) — mounted by api/app/main.py
  tests/
```

`api/app/` keeps what is dashboard-owned (registry wiring, control
passthrough, history DB, auth helpers) and passes them to
`build_assistant_router(deps)`. The MCP servers stay console scripts
(`lab-history-mcp`, `lab-inventory-mcp`, `lab-control-mcp`); `lab-control`
moves with the engine or stays in `api/` — decided in step 1 by import
weight.

### Steps (each a separate PR, each shippable)

1. **Extract, no behaviour change. — done.** `lab-control` stays in `api/`
   (it imports dashboard labware internals); the plan constants and hash moved
   to `lab_assistant.plan_contract` so the engine never imports `api/`; the
   sessions store gets the `lab.db` location registered by the dashboard.
   Old `app.*` names are `sys.modules` aliases (so monkeypatches still hit the
   running code); logger names are pinned to the old ones. Move `assistant.py`, `assistant_openai.py`,
   `assistant_hermes.py`, `assistant_sessions.py` (and, if clean,
   `assistant_control.py`) into `assistant/`. Keep `app.assistant` import
   shims for one release so `main.py`, tests and console scripts resolve.
   Existing assistant tests move and must pass unchanged. Gate: the whole
   `api/tests` suite green, plus the moved tests.
2. **Scopes.** Add `scope` to the chat request (default `dashboard`, so the
   bubble is unchanged). Implement `equipment:<id>`: tool filter, argument
   pinning, mode set, prompt fragment from the device's `/docs/agent`.
   Tests: a panel scope cannot list or call another device's tools; an
   argument naming another device is refused; unknown scope refused.
3. **Delegated drafts.** `propose_plan` for a delegating device creates the
   draft on the device. Tests with a mocked gateway: draft created with the
   validated steps and hash; non-delegating devices unchanged; gateway errors
   surface as tool errors; no dashboard plan record created.
4. **OT-2 panel switches** (opentrons-server): the bubble streams from
   `/api/assistant/chat` with `scope=equipment:<equipment_id>` and keeps
   rendering plans from the gateway's own `/plans`. Its events (`complete`,
   `tool_started`/`tool_finished`) are mapped onto the engine's frames; live
   plan cards, plate-report links, the ELN project picker (gateway `/me`) and
   explicit cancel are kept. The built-in assistant stays behind
   `OT2_ASSISTANT_LOCAL=1` until a central-host outage and a rollback have
   both been exercised. All hands-on acceptance on Complexation only (never
   HTE), with the UI bundle rebuilt.
5. **Retire** `gateway/assistant.py`, `assistant_claude.py`, the
   `/assistant/*` routes, `OT2_ASSISTANT_*` and the Claude login on the UPLC
   PC, after step 4 has run on Complexation. `tools/ot2_agent_mcp.py` stays
   (Hermes), now documented as a client of the same contract.

## Rules check — needs a human decision before steps 2–5

Codex's review is right that the first draft of this section overclaimed:

1. **Pre-existing discrepancy, not created here.** OT-2 gateway plans (panel
   approve + run, `gateway/plans.py`) do not meet AGENTIC_LAB_DESIGN Part I
   rule 3 (main-merged protocol, registered BitacoraDB Plan, passing
   `validate_plan()`); they execute through the gateway's own store, not the
   SDK (rule 1). This plan neither fixes nor worsens that, but it should be
   written down rather than assumed accepted.
2. **UI_DESIGN §5.1** says no model-driven code path POSTs to a device.
   Step 3's draft creation is such a POST (it moves nothing). §2.3's
   "centralise the inference" note describes tool-free translation, not a
   central tool loop, so it does not cover this either.

### Proposed amendment (draft — not in force until approved)

> **AGENTIC_LAB_DESIGN Part I, rules 1 and 4, add:** *A device executing its
> own approved plan.* Rules 1 and 4 bind clients: nothing outside a device
> composes HTTP to its control surface or acts without an SDK claim. A device
> gateway running a step list through its own executor, under a claim it holds
> itself, is the device, not a client; the SDK boundary and claim rules apply
> unchanged to everything that reaches it — including the dashboard engine,
> whose only device write is the draft below, made through the SDK.
>
> **AGENTIC_LAB_DESIGN Part I, rule 3, add:** *Device-run step approvals.* A
> device gateway that implements the plan contract (`POST /plans`,
> `/plans/actions`, `/docs/agent`) may execute an ad-hoc single-device step
> list approved in its own panel by a human who holds the device claim, with
> the hash of exactly the steps shown. Such a run is recorded as what it is —
> an UNFORMATTED Experiment, never a Run Authorization or a registered Plan —
> and stays single-device. Cross-device or campaign work still requires a
> validated plan.
>
> **UI_DESIGN §5.1, replace the sentence** "no model-driven code path POSTs to
> a device" **with:** "No model-driven code path makes a device *act*. The one
> exception is creating a *draft* on a device that runs its own
> human-approved plans (Part I rule 3, device-run step approvals); the model
> can never approve, run or modify an approved plan."

## Open questions for review

1. ~~`lab-control` placement~~ — stays in `api/` (decided in step 1).
2. ~~Same origin~~ — confirmed: both panel prefixes are edge-authenticated
   (`deploy/Caddyfile.single-edge`), root `/api/assistant/*` falls through to
   Next, whose middleware verifies the cookie, strips forged identity and
   injects `X-Auth-User`. Root-relative fetches need no CORS change; direct
   gateway origins would need an explicit policy.
3. Availability (panel chat down when the central host is down): a human
   decision. Proposed: keep the local fallback until an outage and a rollback
   have been exercised.
4. HTE: same codebase, so steps 4–5 reach it; all live testing stays on
   Complexation.
