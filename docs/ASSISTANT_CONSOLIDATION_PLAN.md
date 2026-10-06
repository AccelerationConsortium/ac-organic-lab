# Assistant consolidation — one chat engine, scoped surfaces, device-run plans

**Status:** steps 1–2 implemented 2026-10-05; the rules amendment below was
decided the same day; steps 3–4 implemented 2026-10-06
(`lab_assistant/device_chat.py`, the panel switch in opentrons-server).
Step 5 waits on acceptance on Complexation. Reviewed three times by Codex
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
3. **The device chat on the server. — done 2026-10-06.**
   `lab_assistant/device_chat.py` plus three routes under
   `/api/assistant/equipment/{id}/` (`health`, `chat/stream`, `chat/cancel`),
   as designed below. Tests (`api/tests/test_assistant_device.py`) with a
   mocked gateway: the draft reaches the device with the parsed steps as the
   signed-in user with their roster standing; device refusals reach the
   operator; the device secret fails closed; the turn is claim-gated; cancel
   is owned by (user, device, request id).
4. **OT-2 panel switches. — done 2026-10-06** (opentrons-server
   `ui/src/lib/api.ts`): under the edge the bubble calls the server routes
   root-relative; on direct gateway access it keeps using the gateway's own
   assistant. The event vocabulary is the gateway's, so the bubble itself is
   unchanged — live plan cards, plate-report links, the ELN project picker
   and explicit cancel all keep working. The gateway stamps the verified edge
   identity onto `created_by`. Hands-on acceptance on Complexation only
   (never HTE).
5. **Retire** `gateway/assistant.py`, `assistant_claude.py`, the
   `/assistant/*` routes, `OT2_ASSISTANT_*` and the Claude login on the UPLC
   PC, after step 4 has run on Complexation. `tools/ot2_agent_mcp.py` stays
   (Hermes), now documented as a client of the same contract.

## Rules — decided 2026-10-05

The operator (Yang Cao) directed the switch of the OT-2 panel chat to the
server engine on 2026-10-05, which is the human decision steps 3–5 waited
on. The amendment below is therefore applied to AGENTIC_LAB_DESIGN Part I and
UI_DESIGN §5.1 in this same change, with the wording that was drafted here
and reviewed by Codex.

### Amendment (applied)

> **AGENTIC_LAB_DESIGN Part I, rules 1 and 4, add:** *A device executing its
> own approved plan.* Rules 1 and 4 bind clients: nothing outside a device
> composes HTTP to its control surface or acts without an SDK claim. A device
> gateway running a step list through its own executor, under a claim it holds
> itself, is the device, not a client; the SDK boundary and claim rules apply
> unchanged to everything that reaches it — including the dashboard engine,
> whose only device write is the draft below.
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
> **UI_DESIGN §5.1, replace** "no model-driven code path POSTs to a device"
> **with:** "No model-driven code path makes a device *act*. The one exception
> is creating a *draft* on a device that runs its own human-approved plans
> (Part I rule 3, device-run step approvals); the model can never approve,
> run or modify an approved plan."

## Step 3–4 design: the device chat on the server (implemented 2026-10-06)

### Shape

```
OT-2 panel bubble (served under /ot2/<name>/ui/, same edge origin)
   │ POST /api/assistant/equipment/{id}/chat/stream  {messages, model?, request_id?}
   │ GET  /api/assistant/equipment/{id}/health       {configured, model, models}
   ▼  (Next middleware: session required, X-Auth-User injected)
lab_assistant.device_chat  — ONE tool-free loop, any model:
   1. read the device, as the signed-in user, through the dashboard's device
      auth (X-Auth-User + X-Edge-Auth): /status, /docs/agent, /plans/actions,
      /plans (recent, as that user → the gateway's run-access rule applies),
      and the plate summaries the gateway's own Claude path sends today
   2. one structured model call: {reply, steps|for_each_well, prelude,
      epilogue} — claude-cli (--json-schema, --tools ""), openrouter
      (response_format json_schema, strict); see Models for why not Codex
   3. if a draft was returned: POST <gateway>/plans as the user
      (created_by "assistant (<model>) for <user>") — the device validates,
      expands patterns, stores the draft; approve/run stay in the panel
   4. SSE events in the gateway bubble's existing vocabulary
      (thinking / tool_started / tool_finished / complete{reply, plan_id,
      tools_used, model} / error), so the panel UI is unchanged
```

This is exactly how the gateway's own Claude path works today
(`gateway/assistant.py::_chat_claude_events`), moved to the server and made
model-agnostic. It is UI_DESIGN §2.3's "host the translation centrally, keep
resolution/execution in the gateway": the model has no tools; the engine
does the reads and the one write (a draft).

### Models

`GET …/health.models` lists what the host can actually run, probed at
request time, never a static list:

| model id | backend | available when |
|---|---|---|
| `claude-sonnet-5-5` (+ others in `ASSISTANT_DEVICE_CLAUDE_MODELS`) | `claude -p --json-schema` | `claude auth status` logged in |
| `ASSISTANT_OPENAI_MODEL` (+ `ASSISTANT_DEVICE_OPENAI_MODELS`) | OpenRouter (`response_format` json_schema, strict, `require_parameters`) | `ASSISTANT_OPENAI_API_KEY` set |

The first available is the default. A model the request names that is not
in the list is a 422. Hermes is not offered here (it is a tool-using agent
chat, not a structured translator); it stays on the dashboard bubble.

**Codex (`gpt-6-sol`) is not offered yet.** Codex's own review raised it as
P0 and a probe confirmed it: `codex exec -s read-only` still gives the model
a shell with read access to every file the service user can read (it ran
`head -n 1 /etc/hostname` when asked), and `codex features list` has no
switch that removes it. Behind this unit that means `~/.claude`, the
dashboard's env files and the lab DB. Two ways to add it later, either of
which needs a human decision: run `codex exec` under a dedicated
unprivileged account with its own login and nothing else readable, or use an
OpenAI API key through the OpenRouter-style backend (no CLI, no shell). The
`gpt-6-sol` id would then join the table with `codex` as its backend.

Strict structured output (OpenAI, and OpenRouter's pass-through) forbids open
objects and requires every property, so step arguments travel as JSON text
(`args_json`, `wells_json`, `overrides_json`) and are parsed and validated by
the engine before anything reaches the device; the Claude CLI takes the same
schema (nullable `for_each_well` via `"type": ["object","null"]`, verified
against the pinned CLI 2.1.290).

### Identity and access

- The route is under `/api/assistant/`, so the middleware requires a session
  and stamps `X-Auth-User`; the engine forwards that user, their role and
  their roster projects (`X-Auth-Projects` / `X-Auth-Pi-Projects`, resolved
  from `/authz/scope` here — never relayed from the browser, which the
  middleware strips anyway) to the gateway with the device's edge secret, so
  the gateway's `OT2_REQUIRE_LOGIN` and run-data access rule see the real
  user. The secret is the one the registry entry names
  (`edge_secret_env`); a device without one is refused (503), never served
  with a global fallback.
- The turn is claim-gated like the gateway's own bubble — the engine reads
  `/status` and refuses (423) unless the user holds the claim directly or
  through a plan they approved (`automation (approved by <user>)`). The draft
  itself is created without a claim token (the gateway's `POST /plans` needs
  none); approving and running still need the claim in the panel.
- `created_by` is `assistant (<model>) for <user>`, and the gateway appends
  the verified edge identity whenever a label does not already name it, so
  the card and the audit name who the draft was for regardless of the client.
- Cancellation is owned: an in-flight turn is keyed by (user, device,
  request id); a cancel from anyone else is a no-op. It kills the model
  subprocess and is checked again before the draft POST; it cannot undo a
  draft the device has already accepted (the bubble refreshes `/plans` after
  a stop for exactly this reason).
- Reads: `/status` and `/plans` live every turn; `/docs/agent` and
  `/plans/actions` cached five minutes per device; plate summaries for at
  most ten recent readable plans. One deadline (`ASSISTANT_DEVICE_TIMEOUT_S`,
  240 s) on the model call; at most `ASSISTANT_DEVICE_CONCURRENCY` (4) panel
  turns at once.
- No conversation is stored server-side for the panel scope (as today:
  the panel keeps it in the tab); `assistant_panel_draft` audit rows record
  user, device, model and the draft id when one was created.

### Panel switch (opentrons-server)

- Under the edge (`/ot2/<name>/ui/`), the bubble calls the root-relative
  server routes (`/api/assistant/equipment/ot2_<name>/…`, the id mapped
  explicitly from the edge prefix in `ui/src/lib/api.ts`); on direct gateway
  access (`/ui/`) it uses the gateway's own assistant if configured, else
  hides. Behind the edge there is deliberately **no** fallback to the
  gateway's assistant: a refusal (401/423/503) is shown, never worked around
  with a second credential path. Rollback is redeploying the previous UI
  bundle; the gateway assistant stays installed until step 5.
- Cancel: `POST …/chat/cancel {request_id}`, same body and reply as the
  gateway's route.

### Step 5 (after acceptance on Complexation)

Remove `gateway/assistant.py`, `assistant_claude.py`, the `/assistant/*`
routes and `OT2_ASSISTANT_*`; the Claude login on the UPLC PC is no longer
needed. `tools/ot2_agent_mcp.py` stays for Hermes.

### Review outcomes

1. Codex CLI: deferred (see Models) — not a sandboxing question any more but
   a tool-removal one; the unit's `IPAddressDeny`/proxy posture is moot until
   a runner account exists.
2. Reads as the user carry the server-resolved project headers, so project
   members' and PIs' plans arrive unredacted, exactly as in the panel.
3. v1 is tool-free with plate summaries pre-attached; follow-up tools wait
   for a need.
