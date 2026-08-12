# Dashboard UI — Design & Decisions

**Status:** living document. Created 2026-07-22 by folding in the former
`OT2_INTERFACE.md` (§1, unchanged in substance); §2 (embedded-assistant
tiering) recorded the same day; the former `WORKFLOW_UI_DESIGN.md` design
note folded in as §3 (still [PROPOSED], nothing built); §5 (assistant control
mode) drafted 2026-08-07, also [PROPOSED].
**Audience:** dashboard operators and developers touching the `web/` UI.

This is the home for dashboard UI design and decisions. Each shipped
interface, page, or cross-cutting UI decision gets its own numbered section
here — **add future UI designs as new sections in this file** rather than
creating a new per-feature doc.

Scope boundaries (what does *not* go here):

- Per-device **tile** behaviour as-built → [`EQUIP_STATUS.md`](EQUIP_STATUS.md).
- How-to for registering equipment, tile sizing, pills, map positions →
  [`EQUIP_GUIDE.md`](EQUIP_GUIDE.md).
- Platform-level design decisions (writers, authority, auth) →
  [`ARCHITECTURE.md`](ARCHITECTURE.md).

Sections marked **[PROPOSED]** are design notes — not built, not part of the
lab contract.

---

## 1. OT-2 full-page interface

**Shipped** 2026-07-15 (branch `feature-ot2-interface`) as a dashboard-hosted
implementation; **replaced** 2026-08-05 by an embed of the device's own panel;
the embed itself **retired** 2026-08-07 in favour of linking the device panel
directly.

Each Opentrons OT-2 has a full-page operator interface — the **gateway's own
SPA** (`opentrons-server`, ports 8020/8021, served at `/ui`), reached from the
"Control interface ↗" link on the compact `LiquidHandlerTile`. The dashboard
keeps the read-only tile; every write happens in the device's panel.

### 1.1 Routes

The dashboard hosts **no** OT-2 control route of its own. The tile links
straight at the panel's edge path, opened in a new tab:

| Link target | What it is |
|---|---|
| `/ot2/hte/ui/` | The HTE bench OT-2's gateway SPA, edge-routed and `forward_auth`-gated. |
| `/ot2/complexation/ui/` | The complexation-platform bench OT-2's gateway SPA, same. |

These paths live in `web/src/lib/device-panels.ts`, mirroring the route blocks
in `deploy/Caddyfile.single-edge`. They are edge paths on *this* origin
(`/ot2/hte/ui/`, `/ot2/complexation/ui/`, `/xarm5/web/`), not device URLs —
so the session cookie and the injected `X-Auth-User` identity carry through
without a second login. They are deliberately **not** in `equipment.yaml`: the
mapping describes the *edge's* routing table, whereas the registry's `base_url`
is the device port the aggregator polls directly, un-proxied.

Adding a third OT-2 needs a `device-panels.ts` entry and an edge route block;
no new components.

Removed with the direct link (2026-08-07): `/equipment/[equipmentId]/control`,
its fixed-id aliases `/ot2_hte` and `/ot2_complexation`, and the
`EmbeddedDevicePanel` component they shared. They were a dashboard page whose
entire content was an iframe of the panel — a second URL for the same
interface, with a dashboard chrome and a `100vh-180px` crop as the only
difference. `/utils/xarm_control` still frames the xArm panel because it is a
listed utility page in its own right, not a per-equipment wrapper.

### 1.2 Why the dashboard stopped hosting its own copy

The dashboard's `Ot2ControlPanel` and the gateway's SPA were ports of each
other, kept in sync by hand, and had already drifted — `ot2-deck.ts` alone
carried ~120 lines of divergence, and adding the plate inspector meant writing
the same component twice. The device panel is also the better surface on the
merits: it reads `/status` directly instead of through the aggregator's poll,
and it holds a **real heartbeated claim** rather than the per-request claim the
passthrough takes for a single action.

The link is a plain `<a target="_blank">` (`AuthGatedLink external`), never
`next/link`: a client-side transition would resolve the edge path against this
app's route manifest and 404. The gateway's framing accommodations (no
`X-Frame-Options`/CSP, self-detection of `window.self !== window.top` to
suppress its own auth banner) are now unused by the dashboard but stay useful
for `/utils/xarm_control`-style embeds and cost nothing.

**Audit — closed device-side.** Sending operators to the panel opened a gap: a
write made inside it goes straight to the device and never produces the `control_action`
row the dashboard passthrough writes (ARCHITECTURE decision #1; ROADMAP's
control-surface closure plan). `opentrons-server` closed it from the other end
with its own events exporter (`ee529ad`, live on both gateways 2026-08-06),
ported from the xArm's: hooked at `_run_action`, the single choke point every
control command passes through, it POSTs `control_action` (action, outcome,
`owner` = the claim holder the edge stamps with the signed-in person,
`duration_s`), tip lifecycle, and session edges to `/api/ingest/events`. It is
off unless `OT2_INGEST_URL` is set, takes its `device_id` from
`OT2_EQUIPMENT_ID` so a two-gateway host attributes each robot, and never emits
in dry run.

Consequence worth knowing when reading history: a **dashboard-proxied** click
now writes **two** `control_action` rows — the passthrough's (its HTTP hop,
with `method` + `status_code`) and the device's (`source: "device"`). The
device row is authoritative for outcome and duration; both follow the same
message convention so they read alike in one series. A panel-originated write
produces only the device row, which is the point.

### 1.3 What the dashboard still owns

- **The compact tile** (`LiquidHandlerTile`) — read-only summary: deck mirror
  via `DeckPanel`, light / pipette / SSH / protocol pills, and the
  "Control interface ↗" link out to the device panel. It reads deck state; it
  never writes it.
- **`web/src/lib/ot2-deck.ts`** — pure `/status` parsing (unit-tested, no
  React), shared by the tile and `DeckPanel`. Its declare-side helpers have no
  caller here any more; see the module docstring for why they are kept.
- **`web/src/components/DeckPanel.tsx`** — the reusable 12-slot deck with
  module telemetry readouts, incl. the temperature-module overhang cell.
- **The labware builder and central definition store** (§1.4) — platform-level
  assets shared by both robots and by workflows, so they stay here.

Deleted with the embed: `Ot2ControlPanel.tsx`, `PlateInspector.tsx`,
`lib/ot2-catalog.ts` (the authored picker catalog — the gateway authors its
own), and their tests.

### 1.4 Declaration vs physical setup (unchanged distinction)

The gateway's deck editor records *operator intent*:

- **Declaring** writes to the gateway's persistent declaration store
  (`POST /control/deck/declare`). It is pure metadata — it does **not** load
  labware into an Opentrons protocol context, move hardware, or run
  `/control/setup`.
- The gateway merges declarations with what it *observes* (run/REPL deck) and
  flags disagreements per slot as `slot_state: "mismatch"`; declared and
  observed render separately, mismatches badged (≠) — including on the
  dashboard tile's read-only mirror.
- **Physical setup** (actually loading labware/instruments on the robot) is
  `/control/setup`, driven by a validated `lab-skills` plan. Neither surface
  pretends declaration loads labware.

`POST /control/deck/declare` is a **full-layout replace**: every edit must
re-send all currently-declared slots, each as its exact Opentrons `load_name`
when the gateway reported one (falling back to `kind` only for legacy
declarations, and to the module key for declared modules). Round-tripping by
`kind` alone silently degrades an exact declaration — the rule is enforced in
the gateway's UI now, and recorded in `ot2-deck.ts`'s docstring for the helpers
kept here.

### 1.5 Custom labware (builder + central store)

Three tiers of custom-labware support, added 2026-07-16 and **retained**:

1. **Free-text declare** — the gateway's picker accepts any exact Opentrons
   `load_name` (must match `^[a-z0-9._]+$` and contain `_`, otherwise the
   gateway would parse it as a legacy kind string). Unknown names round-trip
   verbatim.
2. **Labware builder** (`/utils/labware_builder`) — a parametric form (grid,
   footprint, offsets, spacing, well geometry) that generates a complete
   Opentrons **schema-2** definition JSON with a live to-scale preview.
   Validation ports `opentrons-server`'s `LabwareGenerator` limits (footprint
   127 × 85.5 mm, height 200 mm, wells inside the footprint). Anyone can build
   + **download** the JSON; building never touches a robot.
3. **Central definition store** (`/api/labware`, `api/app/labware.py`) — two
   merged sources:
   - **(a) repo-committed**: `<repo>/labware/*.json`, PR-reviewed (see
     `labware/README.md`); wins on name collisions and is immutable via the
     API.
   - **(b) admin-uploaded**: `<data-dir>/labware/*.json`, written by
     `POST /api/labware` (session verified at the middleware, **admin role
     enforced server-side**; uploads validated with the same rules; every
     write audited as a `control_action` on the `labware_store`
     pseudo-device). `DELETE` removes uploaded definitions only.

   Workflows fetch the full JSON (`GET /api/labware/{name}`) to pass as the
   labware `config` in a lab-skills `setup` plan
   (`protocol.load_labware_from_definition` on the gateway).

Env overrides: `LABWARE_REPO_DIR`, `LABWARE_UPLOAD_DIR` (defaults:
`<repo>/labware`, `<lab.db dir>/labware`).

The API additionally serves the **official Opentrons library** (the
`opentrons-shared-data` package, ~141 definitions, latest schema-2 version
each) read-only at `GET /api/labware/standard` (+ `/{load_name}`); the builder
lists it (searchable) and can load any entry's exact geometry for
modification. Uploads that would shadow a standard load name are refused (409)
— a custom variant needs its own name.

### See also (OT-2 interface)

- [`EQUIP_STATUS.md`](EQUIP_STATUS.md) §11 — the compact tile's behaviour.
- `opentrons-server` `docs/DECK_STATE_PLAN.md` — the normalized deck shape.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) decision #1 — the device as single
  authority, and why the audit trail above matters.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) decision #9 — why device services push
  events to the aggregator rather than writing `lab.db` themselves.

---

## 2. Embedded assistants — scoping, tiering, and runtime placement

**Decision recorded 2026-07-22.** The lab has (and will keep growing) more
than one LLM surface in its UIs. This section fixes how they relate, so
capability boundaries are a design property rather than an accident of who
added which tool last.

### 2.1 The rule

**Every embedded assistant declares four things: its tool surface, its model
tier, its trust level, and where its agent loop runs.** Assistants gain
consistency by sharing *context* (the equipment catalog, the pinned
vocabulary, common prompt fragments) and by **handing off upward** — never
by acquiring each other's tools. A read-only surface stays read-only *by
construction* (the toolset makes actuation impossible — the same guarantee
as [`ARCHITECTURE.md`](ARCHITECTURE.md) decision #10), not by prompt.

There is deliberately **no single agent that knows everything**. A unified
agent's trust level is the maximum of its tools: the moment it can move a
robot, every conversation — including "what's the fumehood temperature" —
inherits control-grade gating, and the cheap review story ("this surface
cannot actuate, period") is gone. It also maximizes the prompt-injection
blast radius: a poisoned device error string read by a read-only assistant
can at worst distort an answer; read by an everything-agent it sits
upstream of actuation.

The aggregator-that-delegates shape *is* the end state, but the user-facing
aggregator is the ELN agent (LaAgenteAnalitica, Track 1 of
[`AGENTIC_ELN_PLAN.md`](AGENTIC_ELN_PLAN.md)), and its delegation targets
are **deterministic services, not other LLMs**: the lab-skills MCP
(`validate_plan` / `execute_plan` behind human approval), the read-only
history MCP, and per-device structured-intent resolvers (e.g. the xArm's
`assistant_actions` action resolver). If the big agent needs to move the
arm, it calls the same structured `execute_plan` path — it never talks to
the xArm's little LLM. LLM-orchestrating-LLM hops add cost, latency, and
audit opacity without benefit at single-lab scale.

### 2.2 The three tiers

| Tier | Surface | Tools | Model class | Agent loop runs on | Inference |
|---|---|---|---|---|---|
| **1. Panel micro-assistant** | xArm control page (pattern for future per-device panels) | One structured-intent tool, device-local; no loop (single translate call) | Cheap/fast (GLM-class via OpenRouter) | *Inside the device gateway process* (`assistant_llm.py` in `xarm_api_server.py`, the device's Windows PC) | OpenRouter cloud API |
| **2. Dashboard assistant** | Chat bubble, all dashboard pages | Read-only: seven `lab-history` MCP tools (history DB, live `/api/equipment`, whitelisted journald). §5 [PROPOSED] adds a propose-only `lab-control` server in control mode — still no actuating tool, so this row's trust level is unchanged | Mid (sonnet-class) | Central dashboard host — `api/app/assistant.py` spawns a `claude` CLI subprocess per turn; the MCP server is a local stdio child | Anthropic cloud via the host's Claude Code OAuth |
| **3. Lab / ELN agent** | ELN chat + planning page (LaAgenteAnalitica) | Lab-skills MCP (read-only first; `execute_plan` behind `--allow-control` + human approval), AnaliticaDB HTTP tools | Best available | The LaAgenteAnalitica backend service — its own host/service (deployment target open, D-8); tools reached **over the tailnet** | Provider cloud API |

Trust level rises down the table; so does the gating (tier 3 actuation
requires `main`-merged plans, claims, and a human approval click).

### 2.3 Runtime-placement rules

- **The browser never runs agent code and never holds model credentials.**
  All three loops are server-side; the UI is a thin stream consumer
  (SSE / AG-UI).
- **Tool execution stays on the host that already owns the resource.** The
  xArm resolver executes in the gateway that owns the arm; history tools
  execute on the dashboard host that owns `lab.db`; the control-capable
  `lab-skills mcp serve` instance runs on the platform host (one
  per-deployment instance, allow-listed users — D-10), not on the agent's
  host. No new process gains network reach over hardware because an
  assistant was added.
- **Only stateless inference leaves the tailnet.** The agent loop and tools
  stay inside; the model API call is the one external dependency in every
  tier.
- **Tier 3 is the only tier where agent host ≠ tool host** — acceptable
  precisely because the control MCP on the platform host is the gate (the
  agent holds a client connection, not the capability).
- **Known trade-off (tier 1):** running the micro-assistant inside the
  gateway puts a cloud API key + egress on a device PC, one per device that
  adopts the pattern. If that scatter becomes a problem, host the NL→intent
  *translation* centrally and keep intent *resolution/execution* in the
  gateway — the model has no tools, so moving the inference call does not
  move any capability.

### 2.4 Hand-off behaviour

Small assistants refuse out-of-scope requests **by pointing upward**, not by
silently declining: the xArm assistant's plain-text refusal should direct
the operator to the dashboard assistant (for "what happened" questions) or
the ELN agent (for multi-device work). The dashboard assistant likewise
points at the ELN agent for anything requiring actuation or planning.

### See also (embedded assistants)

- [`ARCHITECTURE.md`](ARCHITECTURE.md) decisions #7 and #10 — the two MCP
  servers with different trust levels; the assistant's read-only-by-toolset
  guarantee.
- [`AGENTIC_ELN_PLAN.md`](AGENTIC_ELN_PLAN.md) — Track 1 (the ELN agent's
  lab toolset and approval-gated `execute_plan`); decisions D-8 (agent
  runtime) and D-10 (where `--allow-control` runs).
- `xarm-translocation` `src/core/assistant_llm.py` / `assistant_actions.py`
  — the reference tier-1 implementation (LLM out of the safety loop).

---

## 3. Workflow UI [PROPOSED]

> **Status:** design note (2026-07-16; folded in from the former
> `WORKFLOW_UI_DESIGN.md` 2026-07-22). Not part of the lab contract.
> Proposes a browser surface for *carrying out* workflows (author → validate
> → approve → execute → monitor → record) built on the existing dashboard
> and SDK rather than as a new app. Companion to
> [`AGENTIC_ELN_DESIGN.md`](AGENTIC_ELN_DESIGN.md) (the why) — this is
> the how for the *execute + monitor* surface. **The backend half is built**
> (2026-08-08/09): the authorization runner + background runs + SSE + abort in
> `api/app/workflow.py` — see §3.2b, which is the as-built record and wins where
> it differs from the §3.3 proposal (its input is an `authorization_id`, not a
> `plan_id`; there is no separate approve endpoint — approval is bitácora's).
> The *browser* half (a run view in the Notebooks tab) is still unbuilt;
> maturity claims about the SDK trace to [`ROADMAP.md`](ROADMAP.md) /
> [`INTERLOCKS.md`](INTERLOCKS.md) and should be checked against current code.

### 3.1 Thesis: extend the seam, don't build a third app

The lab already runs two browser UIs. A "workflow UI" is the **seam** between
them, not a new surface. Split the workflow lifecycle along the grain that
already exists:

| Surface | Stack | Owns | Workflow role |
|---|---|---|---|
| **Dashboard** (`web/` + `api/`) | Next.js 14 + TanStack Query; `ac_auth` login; audited control passthrough | Real-time monitoring + operator control | **operate / execute / monitor** |
| **GraphChat** (LaAgenteAnalitica) | React 19 + Vite + Yjs; workspace + approval UI | Agent chat, file workspace, analysis | **design / author / analyze** |

This section specifies the dashboard side (execute + monitor + approve). Plan
*authoring* and result *analysis* stay in GraphChat, which should follow its own
"workspace-native" direction; the two link by `plan_id` / `session_id`.

**Non-negotiables** (these are already the architecture — do not regress):

- **No control logic in the browser.** The device is the authority
  ([ARCHITECTURE](ARCHITECTURE.md) decision #2); the UI sends intent and renders
  the device's refusals (412 precondition / 423 claim conflict). Preconditions
  and interlocks live in `lab-skills`, never in React.
- **Render forms from the schema.** Drive plan authoring off
  `schema/protocol.schema.json`, so new step types need no frontend change — the
  "fields are for machines" rule from `bitacora/templates/hte`.
- **Every run links to its record.** Deep-link each run to its AnaliticaDB
  `Plan` / `Note` / `Analysis` rows so "what ran / what was observed" is one hop
  from "what I clicked".
- **Auth + audit are not optional.** Run/approve endpoints sit behind `ac_auth`;
  every approval and every executed step writes an audit row
  ([LAB_MONITORING](LAB_MONITORING.md) `event_type: control_action`).

### 3.2 Architecture

```
Browser (Next.js Workflow tab)
  │  GET  /api/workflow/plans                 list + status
  │  POST /api/workflow/plans/{id}/preflight  validate_plan + dry_run
  │  POST /api/workflow/plans/{id}/approve    ac_auth principal → stored payload
  │  POST /api/workflow/plans/{id}/run        starts a run, returns run_id
  │  GET  /api/workflow/runs/{run_id}/events  SSE stream of step events
  │  POST /api/workflow/runs/{run_id}/abort   cooperative stop
  ▼
api/app/workflow.py   ← NEW: a WORKFLOW CLIENT of lab-skills (SDK path),
  │                      distinct from the thin control passthrough (control.py)
  ▼
lab_skills.execute_plan(plan, session, *, owner, wait_timeout_s, dry_run)
  │   per step: layer-3 re-check (live allowed_actions) → layer-4 async
  │   interlocks → per-step ClaimManager → POST SkillDef endpoint w/ token
  ▼
Devices (STATUS_SPEC /control/*)        AnaliticaDB (Plan/Note/Analysis rows)
```

**Architectural note worth flagging:** this endpoint makes `api/` a
*programmatic workflow writer* — it holds long-lived per-step claims through the
SDK, unlike `control.py`'s thin per-request claim passthrough. That is a
deliberate extension of [ARCHITECTURE](ARCHITECTURE.md) decision #1 ("two
writers, one authority"): the dashboard gains a *third* writer class (a
supervised workflow runner) alongside the operator passthrough. Keep it in its
own module (`workflow.py`), never fold workflow execution into `control.py`, and
make the human-approval gate the thing that authorizes the run. If you prefer to
keep `api/` presentation-thin, the same module can instead live in a small
dedicated runner service that `api/` proxies — the endpoint contract below is
identical either way.

### 3.2b What shipped first: the authorization runner (2026-08-08)

`api/app/workflow.py` — the module this section calls for, built against the
Phase F seam rather than against a DB draft. **Its input is a bitácora run
authorization, not a `plan_id`**: the endpoint contract below was written before
the authorizer existed, and where the two differ, this is what is built.

```
POST /api/workflow/runs   {authorization_id, dry_run?}
  1  GET  bitacora /authorizations/{id}   — a pull, so revocation is real (D-21)
  2  refuse unless `executable`           — revoked or expired, saying which
  3  recompute `package_digest`           — from the published package alone
  4  Plan from package.steps              — step_id → Step.id, the one translation
  5  Lab.connect(binding=auth.binding)    — the machines the authorizer validated
  6  execute_plan(...)                    — per-step claim, live layer-3/4 re-checks
  7  one `plan_run` audit row             — same series as control.py's
                                            `control_action`, distinct type
```

Four things worth keeping when this grows a background runner and an SSE stream:

- **The digest check is done here, in this repo.** A check only the issuer can
  perform is not a check. Bitácora publishes every digest input inside the
  package so a second implementation can verify without reassembling filename
  stems; if an input goes missing the runner refuses rather than hashing a
  subset and calling it verified.
- **Readiness is not taken from the authorization.** Its stored verdict can be a
  day old — evidence it was sane when approved, never clearance to run now.
  `execute_plan` re-checks live `allowed_actions` and interlocks immediately
  before each step, and that is the authority.
- **The binding is pinned, not looked up.** The package names *roles*; re-point
  `liquid_handler` at the other OT-2 and the byte-identical package runs on a
  different machine. The session is built from `auth.binding`, not from however
  this host is configured now.
- **It authenticates as the operator, not as a machine** (D-24). The runner
  presents `control.py`'s own device headers — the edge-injected `X-Auth-User`
  plus the shared secret — so the device records the *human* in
  `claimed_by.owner` and in its own audit rows. Established the hard way: the
  first real run was refused `401 login_required`, and so was a second attempt
  carrying a valid `ac_auth` API key, because the OT-2 gateway deliberately
  contacts no external auth service. Which service checks a credential is a
  per-device fact; there is no lab-wide answer to assume.
- **The run returns record-layer shapes it does not write** (D-23): a `Plan` row
  under the campaign's `Experiment`, plus `step_id`-anchored `Note`s for the
  steps that failed, blocked or were skipped. Successful steps produce no note —
  the `Plan` row already describes them, and a note each would bury the two that
  matter.

**Slice 2 (2026-08-09) made it a background run.** `POST /runs` now answers 202
with a `run_id` as soon as the gates pass — the gates still run inline, so a
refusal is still a 409 with the reason, never a run_id that dies immediately.
Progress streams on `GET /runs/{run_id}/events` (SSE, replay-then-follow so a
late or reconnecting client sees the whole run; ends after `done`), state reads
on `GET /runs/{run_id}`, and `POST /runs/{run_id}/abort` requests a cooperative
stop at the next step boundary — mid-step is the device's territory, and
yanking a claim out from under a seal cycle is how a plate gets stuck in a hot
chamber.

The SDK grew the two hooks this needed rather than the dashboard re-implementing
the step loop: `execute_plan(gate=…)` is awaited before each step and aborts
with a reason (it runs before the per-step claim, so an abort never strands
one, and a gate that *raises* fails closed — a broken revocation check must not
quietly stop revoking), and `on_step=…` observes each step's report (exceptions
swallowed: an observer must not fail the run it watches). The runner's gate
checks the operator abort flag and re-fetches the authorization from bitácora
**between every step** — D-22's requirement that an 18 h incubation stays
revocable, not revocable-at-start-only.

Run state is in-process by design: a run does not survive an API restart, and
execute_plan's per-step claims die with the process anyway — a persisted row
pretending to be a live run would be the record overstating reality. The
durable trail is the `plan_run` audit rows (now also `aborted` / `crashed` /
`abort_requested` outcomes) plus, later, the D-23 record write.

### 3.3 Endpoint contract

#### `POST /api/workflow/plans/{plan_id}/preflight`

Runs `validate_plan` (offline, layer 3 + layer 4 sync rules) and optionally
`execute_plan(dry_run=True)` (live preflight: layer-3 re-check + interlocks, no
claim, no command POST). Returns the `PlanReport` shape from
[INTERLOCKS](INTERLOCKS.md): `{ ok, violations[], warnings[],
estimated_duration_s, devices_required[] }`. The UI uses this to enable/disable
the Approve button and to show violations *before* anyone commits.

#### `POST /api/workflow/plans/{plan_id}/approve`

The human-approval chokepoint. Body echoes the **fully-resolved** plan the
operator saw (rendered steps + parameters). Server:

1. Verifies the `ac_auth` principal (unique user; not the shared owner).
2. Stores the *structured* approved payload (never a re-parse of free text).
3. Transitions the AnaliticaDB `Plan` `draft → approved`, stamping the principal
   and, for protocol-authored plans, the git `source_commit`.
4. Writes an audit row (`who / plan / outcome`).

Protocol-authored plans that were signed off by a `main` merge skip the card and
are stamped from the merge commit — the two sign-off authorities stay distinct
(see [AGENTIC_ELN_DESIGN](AGENTIC_ELN_DESIGN.md) §4).

#### `POST /api/workflow/plans/{plan_id}/run`

Requires an `approved` plan. Starts `execute_plan` in a background task with
`owner = "dashboard:<user>"` and a caller-supplied `wait_timeout_s` (so a plan
can wait out a time-clearing precondition, e.g. a heater ramp). Returns
`{ run_id }` immediately; progress arrives on the SSE stream.

#### `GET /api/workflow/runs/{run_id}/events` (SSE)

One event per step transition plus a terminal event. Event shape:

```jsonc
// event: step
{
  "run_id": "…",
  "plan_id": "…",
  "step_index": 2,
  "step_id": "seal_plate",          // stable id from the protocol
  "role": "sealer",
  "action": "seal.start",
  "state": "waiting",               // see state machine below
  "claim_owner": "dashboard:yang",  // who holds the device now
  "detail": "Heater warming to setpoint (170 C)",
  "refusal": null,                  // verbatim device 412/423 body when blocked
  "ts": "2026-07-16T14:03:11Z"
}
// event: done
{ "run_id": "…", "status": "completed",  // completed | failed | aborted
  "claims_acquired": ["sealer"], "report": { /* PlanRunReport */ } }
// event: error
{ "run_id": "…", "message": "…" }
```

Per-step `state` machine (mirrors `execute_plan`'s fail-fast semantics):

`pending → running → succeeded`
`running → waiting → running` (polling a time-clearing precondition)
`running → blocked` (layer-3/layer-4 or device 412 — precondition not met)
`running → rejected` (device 423 — claim held by another writer)
`running → failed` (execution error; `last_error` surfaces on the device)
any terminal failure ⇒ remaining steps emitted once as `skipped`.

Reuse the assistant bubble's SSE frame conventions (`api/app/assistant.py`
already streams SSE); no new transport dependency. WebSocket is a later upgrade
if bi-directional control (pause/resume mid-step) is wanted — the roadmap lists
WebSocket real-time pages as the next step, so the run view is a natural first
consumer.

### 3.4 Run view (component sketch)

A single Next.js route, `web/src/app/workflow/`, built from the existing
primitives (`use-equipment`-style hooks + TanStack Query for the plan list; a
raw `fetch` + `ReadableStream` reader for the SSE run stream, same as the
assistant bubble).

```
Workflow ▸ Run  (plate BAS-042 · solubility_screen v3)      [Abort]
────────────────────────────────────────────────────────────────
approved by yang · plan_id 7f3a… · source_commit a1b2c3 · owner dashboard:yang

  ✓  1  startup           sealer      succeeded    0.4s
  ⧗  2  seal.start        sealer      waiting      heater 150→170 °C   ⟳
     3  stage.out         sealer      pending
     4  move              plate_mover pending
────────────────────────────────────────────────────────────────
Record →  Plan 7f3a  ·  3 Notes  ·  0 Analyses            (AnaliticaDB)
```

- **State encoded in form, not just text:** a severity stripe / pill per row
  (`succeeded` = accent, `waiting` = amber + spinner, `blocked`/`rejected` =
  clay with the verbatim refusal body expandable, `skipped` = muted). This is
  information design, not decoration — the operator scans for the one row that
  needs attention.
- **Claim holder is always visible** so a 423 reads as "the workflow (or another
  operator) holds the sealer", not a mystery failure.
- **Abort** is cooperative: it cancels the run task, which releases the
  in-flight `ClaimManager` in its `finally`.
- **Record footer** deep-links into AnaliticaDB; live counts update as notes /
  analyses append under this `plan_id`.

### 3.5 Plan authoring (kept minimal here)

Authoring lives primarily in GraphChat, but the dashboard needs at least a
**read + parameterize + preflight** view so an operator can run an existing
protocol without the agent. Render the form from `schema/protocol.schema.json`
(JSON-Schema-to-form), or offer a validated YAML editor that POSTs to
`preflight`. Run-specific values (plate id, operator, the day's materials) are
Plan parameters, never edits to the protocol file — same rule as
`bitacora/templates/hte`.

### 3.6 What to avoid

- **A standalone Streamlit/Gradio workflow app.** It bypasses `ac_auth`, the
  audit trail, and the claim fabric, and becomes a fourth un-governed control
  path — exactly the *control-surface exposure* risk in [ROADMAP](ROADMAP.md).
- **`validate_plan` logic in TypeScript.** It belongs in the SDK, called over
  HTTP. The browser renders results; it does not decide safety.
- **Folding workflow execution into `control.py`.** The thin operator
  passthrough and the supervised workflow runner are different writer classes
  with different claim lifetimes; keep them in separate modules.

### 3.7 Build order

1. **Runner endpoint + SSE (`api/app/workflow.py`).** `run` + `events` over
   `execute_plan`. The highest-leverage first build — it turns the
   already-validated executor into something an operator can watch. Test against
   the respx-mocked v1.1 device fixtures the SDK already ships, then live
   against PlateLoc (per PR-3 on the roadmap).
2. **Minimal run view (`web/src/app/workflow/`).** Plan list + the step stream
   above. Read-only monitoring first.
3. **Approval gate.** The confirm card + `approve` endpoint + `Plan`
   `draft→approved` transition + audit row. Shared with PyPoe's gate — one
   mechanism, two consumers.
4. **Authoring / parameterize view.** Schema-driven form + `preflight`.
5. **Record links + live note/analysis counts.** Close the loop to AnaliticaDB.

### See also (workflow UI)

- [`AGENTIC_ELN_DESIGN.md`](AGENTIC_ELN_DESIGN.md) — why this surface exists and the design→execute→record loop.
- [`INTERLOCKS.md`](INTERLOCKS.md) — `validate_plan` / `execute_plan`, `PlanReport` / `PlanRunReport`, the four layers.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — writer classes (decision #1), device authority (decision #2).
- [`LAB_MONITORING.md`](LAB_MONITORING.md) — the `control_action` audit row shape.
- [`AUTH_DESIGN.md`](AUTH_DESIGN.md) — `ac_auth`, control-route enforcement, the identity a run/approval is stamped with.

---

## 4. Health · Activity split (STATUS_SPEC v1.2 surfaces)

> **Status:** shipped 2026-07-24 with the v1.2 rollout. The recording side
> (parallel `activity_transition` series, server-side derivation) is
> documented in [`LAB_MONITORING.md`](LAB_MONITORING.md) §4; the contract is
> STATUS_SPEC §2.3. This section records the **presentation decisions**.

v1.2 split "is it healthy" (`equipment_status`) from "is it working"
(`activity`). Three UI rules govern every surface that shows them:

1. **Health always wins any single-glyph surface.** The History page's
   `StateDot` and any platform rollup dot stay health-colored; activity
   moves to the tooltip there ("Degraded · Running").
2. **Where there is room, health and activity are two separate visual
   elements** — never one merged pill with health's color and activity's
   text (that would repeat the original conflation in pixels).
3. **A poll-sampled activity series is never presented as usage
   accounting** (§2.3.1): utilization bars are labelled as observed/sampled,
   tooltips carry "sampled every 60 s · tracking since <date>", and
   pre-tracking time renders as *untracked*, never as 0 % usage.

The shipped surfaces:

- **History → Uptime rows**: a thin segmented **utilization bar**
  (`ActivityTimelineBar`) under each health timeline bar. Colors:
  `running` = sky-500 (same hue as `busy`, which is definitionally
  healthy+running), `idle` = slate-300, `unknown` = slate-400 (matches
  health-unknown's "no information"). Devices with no activity rows yet get
  a dashed empty bar ("No activity tracking yet"), not a 100 %-idle lie.
- **Overview equipment rows** (`PlatformCard`): the raw health pill is
  replaced by an **activity pill** (`Running` / `Idle` / `—`) plus a small
  amber ⚠ whose tooltip names the health state, shown only when health
  needs attention (`requires_init`, `degraded`, `error`, `e_stop`,
  `unknown`, `unreachable`). A chronically degraded shaker reads
  "Running ⚠" instead of a permanent orange "Degraded" that hides
  whether it is working.
- **State Reference panel**: two labelled groups (Health / Activity) with a
  footer noting the axes are independent.
- **Vocabulary + shared logic**: `web/src/lib/state-meta.ts` owns both
  vocabularies (`STATE_META`, `ACTIVITY_META`), plus the shared
  `effectiveState()` (transport/gateway "unreachable" attribution) and
  `stateNeedsAttention()`. The *value* of `activity` shown in the UI is
  resolved server-side (`api/app/events.py::derive_activity`, surfaced as
  `EquipmentSnapshot.activity`/`activity_source`) so live tiles and the
  stored series can never disagree — the frontend never sniffs
  `components` itself.

Decided against (see STATUS_SPEC Appendix B for the axis-design
counterparts): a merged health+activity pill; showing activity on
single-glyph surfaces; rendering pre-tracking time as zero usage.

### See also (health · activity)

- [`STATUS_SPEC.md`](STATUS_SPEC.md) §2.3 (contract), §2.3.1 (sampling
  caveat), Appendix B (v2 direction).
- [`LAB_MONITORING.md`](LAB_MONITORING.md) §4 — the `activity_transition`
  registry entry and device-pushed convention.
- [`EQUIP_STATUS.md`](EQUIP_STATUS.md) — per-device tile behaviour.

---

## 5. Assistant control mode [IMPLEMENTED — Steps 1, 1b, 1c]

**Drafted 2026-08-07** on branch `actionable-assistant`; **Step 1 implemented
2026-08-11**. Extends the tier-2 dashboard assistant (§2) from a purely
investigative surface to one that can *propose* a single equipment action the
operator then authorizes. Step 1 ships as specified below, with two documented
deviations: (a) an explicit fail-closed action-name resolver bridges the xArm's
`move.<node_id>` advertised action to the `graph.move_to` skill / `graph/move_to`
passthrough (§5 "action naming"); (b) safety-floor actions (`stop`/`connect`/
`clear_errors`) are excluded from proposals and stay operator-only. It lands as
the propose-only `lab-control` MCP server (`api/app/assistant_control.py`), a
`mode` field + per-mode wiring in `assistant.py`, the Ask/Control UI in
`AssistantBubble.tsx`, and the `X-Control-Origin`/`assistant_proposal` audit
trail. **Step 1b (2026-08-12)** extends the resolver to the OT-2, and
**Step 1c (same day)** to that device's full advertised surface behind a
field-level guard — see §5.3b. Step 2 (autonomy) is sketched at the end and is
**not** approved.

### 5.1 The commitment: the assistant proposes, the browser executes

In Step 1 no model-driven code path POSTs to a device. The model's most
privileged act is producing a **validated proposal object**; actuation happens
when the operator clicks *Authorize*, over the existing
`/api/equipment/{id}/control/{action}` passthrough.

This is the whole safety argument, and it is a property of the toolset rather
than of the prompt — the same guarantee §2.1 already requires of this tier.
Device `message` fields, ingested event rows and journald all flow into the
model, and any of them is a string somebody else wrote. With no actuating
tool, the worst an injected instruction can achieve is *raising a confirm card
a human must read and click*. Authentication cannot supply this: it
establishes **who**, never **whether the human meant it**.

It is also the cheap option. Identity, per-equipment authorization, the
claim dance and the audit row already exist on the operator path
(`middleware.ts` injects the verified `X-Auth-User`; `control.py`'s
`_authorize_control` → `_acquire_claim` → action → release-in-`finally` →
`_record_control_event`). Reusing that path end-to-end means control mode
adds **no new trust surface** — only a new way to reach the same button.

### 5.2 Two modes

| | **Ask** (default) | **Control** |
|---|---|---|
| Accent colour | emerald (today's) | **purple**, panel-wide |
| MCP servers | `lab-history` | `lab-history` + `lab-control` (propose-only) |
| System prompt | today's | + control-mode addendum |
| Requires | signed in | signed in **and** `operator`+ on ≥1 equipment |

- The toggle is a segmented control in the panel header. Purple carries through
  the launcher bubble, header, send button, input focus ring and user turns:
  the mode must be unmistakable at a glance, not a small badge.
- **Resets to Ask** on reload / panel close. Deliberately *not* on an idle
  timer like the tile lock's auto-relock — the confirm card is the gate, so an
  expiry would add friction without adding safety (open question 1).
- Disabled, with a reason tooltip, when `/api/auth/mine` reports no equipment
  roles for this user.
- Mode travels in the POST body, but **the server decides the toolset**:
  `assistant.py` re-resolves the actor from `X-Auth-User` and re-checks
  authorization before it will spawn the control server. A client that lies
  about its mode gains nothing.
- Control mode stays **off** under the `DASHBOARD_CONTROL_OPEN=true` dev
  bypass — that path has no verified identity to bind a proposal to.

### 5.3 Step 1 — propose → authorize → execute

**New MCP server `lab-control`** (`api/app/assistant_control.py`, a second
console script beside `lab-history-mcp`), spawned per request with the
verified actor bound in **environment** (`LAB_ACTOR`) — never as a tool
argument, which the model could otherwise choose. Two tools, neither
actuating:

- **`list_available_actions(equipment_id)`** — the device's live
  `allowed_actions`, its `equipment_status` / `activity`, and the matching
  `SkillDef` argument schema (`api/` already imports `SKILL_REGISTRY`). This
  is how the model learns what is legal instead of guessing at endpoints.
  For a graph-constrained arm it also forwards the device's read-only
  `details.motion_graph` snapshot verbatim (added 2026-08-12) —
  `current_node`, single-hop `reachable_nodes`, multi-hop `travel_targets` —
  because the xArm advertises only the current node's outgoing hops as
  `move.<node_id>` actions, so without it the assistant could not reason
  about or explain a route. This widens what the model *sees*, never what it
  may *propose*: the device's multi-hop `travel_to` stays outside the skill
  catalog and `allowed_actions`, so a route is proposed one `move.<node_id>`
  hop per confirm card, re-checking device state between hops (the prompt
  addendum says so explicitly).
- **`propose_action(equipment_id, action, args, reason)`** — validates and
  returns a normalized proposal. It refuses unless *all* hold: the equipment
  exists and is enabled; `action ∈ status.allowed_actions` (the device is the
  authority — STATUS_SPEC §6.2); `args` validate against the `SkillDef`
  schema; and `LAB_ACTOR` holds `operator`+ **on that equipment**
  (`/authz/check`). Exactly one `equipment_id` per proposal — no batches, no
  sequences.

**Flow.** `assistant.py` recognises `lab-control` results and emits a new SSE
frame `{"type":"proposal", …}` alongside today's `text` / `tool_use` /
`tool_result` / `done` / `error`. The bubble renders a **confirm card**:
equipment name, action, argument table, and the device's current state, with
the model's one-line `reason` shown *subordinate* to those authoritative
fields — the card's load-bearing content comes from the validated proposal,
never from model prose. One proposal outstanding at a time; it expires after
~2 minutes and re-validates `allowed_actions` at click time (the device's
412/423 remains the real backstop).

*Authorize* calls the existing `controlPost` helper. Per execution the
passthrough claims the device **as the human**, runs the one action, and
releases in a `finally` — the "claim, act, release immediately" property is
already implemented; control mode inherits it rather than reimplementing it.

**Audit.** The browser adds `X-Control-Origin: assistant`, recorded on the
existing `control_action` row so assistant-originated actions separate from
tile clicks on the admin page. The proposal itself is written as an
`assistant_proposal` event — otherwise the trail records the click but not
what talked the operator into it.

### 5.3b Steps 1b/1c — which OT-2 actions are proposable

**Implemented 2026-08-12.** The Step 1 resolver hard-coded `robot_arm` move
targets; the per-kind allowlist now lives in `_PROPOSABLE`
(`api/app/assistant_control.py`), which carries the full rationale. Fail-closed
is unchanged: a kind or action absent from the table is refused.

The scoping line is deliberately **not** "is this action dangerous" — nothing
in either mode actuates, and the confirm card is the gate. It is:

> proposable **iff** the card is humanly evaluable at a glance **and** the
> action is correct as a standalone act.

Both halves do work. A card nobody can check is a rubber stamp rather than a
gate — which disqualifies `setup`, whose nested labware/instrument lists carry
free-form `config` JSON. And an action that is only meaningful mid-sequence
cannot be bound to one confirm click, since a proposal is one action on one
device — which disqualifies the liquid verbs. That second half is §5.4's
"belongs in a plan, not a chat turn" applied *within* a single device: a lone
`aspirate` is not wrong, it is incomplete, and the passthrough runs no
interlocks to catch the half-executed remainder.

| Tier | Actions | Why |
|---|---|---|
| **A — propose** | `lights.set`, `home`, `pause` | Zero or one scalar arg; each moves the robot toward a safer or more legible state. `home` is the tier's one real motion — the canonical make-it-safe pose, no args, idempotent, and the documented prerequisite for a hand entering the deck. |
| **B — propose (record edits)** | `plate.load`, `plate.unload`, `well.update`, `deck.declare` | No motion, evaluable cards. They mutate the lab's *belief* about the deck, so a wrong one silently desyncs belief from reality — the reason they still confirm, not a reason to withhold them. |
| **C — operator-only** | `startup`, `shutdown`, `setup`, `resume`, `tips.reset`, `move_to`, `pick_up_tip`, `aspirate`, `dispense`, `drop_tip`, `move_labware` | Sequence-bound (the six motion/liquid verbs), unevaluable (`setup`), secret-bearing (`startup` carries `password`, which would land on the card and in the `assistant_proposal` row), safety-floor inverse (`resume` — somebody paused, possibly with hands in the deck), or interlock-adjacent (`tips.reset` declares used tips fresh, disarming the contamination guard). |

#### Step 1c — the full surface, behind a field guard (2026-08-12)

Tier C was admitted the same day, by operator decision, after the tier table
above was reviewed: **the operator is the sequencer**. A liquid-handling
sequence runs as consecutive confirm cards — one click binds one step — and
the control-mode prompt instructs the model to propose steps strictly in
order, one at a time, re-checking device state between them, and to recommend
a validated workflow plan once the work grows beyond a handful of steps.
`execute_plan` (§5.5) remains the right surface for real multi-step work; what
changed hands is only who may *suggest* the next single step.

The admission price named by Step 1b was paid first, not skipped:

- **The field-level guard** (`_FORBIDDEN_ARG_FIELDS`): `force` (contamination-
  guard override), `force_direct` (collision-safe-path override), `password`
  and `host_alias` (device credentials the gateway supplies from its own env)
  are never model-settable. Supplying one refuses the whole proposal (code
  `forbidden_field`) — by field, not by value, so the invariant never depends
  on reading a boolean — and `list_available_actions` strips them from the
  advertised schemas (reporting them as `operator_only_fields`) so the model
  never sees them as settable. A test pins that every risky field reachable
  through a proposable schema is guarded.
- **Card evaluability for `setup`-sized bodies**: past a compact threshold the
  confirm card renders the full argument set as pretty-printed, scrollable
  JSON instead of a truncated line. The args are exactly the payload Authorize
  POSTs, so nothing may be truncated.

What Step 1c resolves from the tier-C list: the six liquid/motion verbs
(operator-sequenced), `setup` (evaluable via the block render), `startup`
(credential fields guarded; a human authorizing it is the "explicit
invocation" the catalog blesses despite `do_not_call_connect`), `resume` and
`tips.reset` (the confirm card is the gate — the operator reads what they are
un-pausing or re-arming). The xArm's safety-floor actions (`stop` / `connect`
/ `clear_errors`) remain non-proposable; that Step 1 deviation is unchanged.

Two consequences worth keeping:

- **Tier A+B needed no new mechanism** — no schema in those tiers carries an
  interlock-weakening or secret field, so an action table sufficed. Step 1c is
  where the field-level guard became load-bearing (above), and the pinning
  test flipped from "no proposable schema exposes such a field" to "every
  exposed risky field is guarded".
- **The confirm card had to learn to render objects.** `String(v)` flattened
  every nested argument to `[object Object]`, which tier B's shapes
  (`plate.load` wells, `deck.declare` slots) hit immediately — an unreadable
  card fails the first half of the criterion. Arguments now render as JSON, and
  `deck.declare` with an empty `slots` map (which wipes the whole declaration
  while reading as a no-op) is called out in words on the card.

Operator-only is a property of the **action**, not of the asker. The
control-mode prompt addendum says so explicitly, because the first version
reported excluded actions as "needing an operator" — true of every proposal,
and misread as a permissions problem.

### 5.4 What control mode does *not* change

- **The tier-2 trust level (§2.2).** No tool in either mode actuates, so the
  read-only-by-construction guarantee and ARCHITECTURE decision #10 survive
  intact. What the tier gains is a *rendering* capability, not a hardware one.
- **Interlocks.** The passthrough deliberately runs no skill preconditions and
  no project interlocks (ARCHITECTURE decision #1); the device's 412/423 is
  the only backstop. Single-equipment actions are exactly the case where that
  is acceptable — which is *why* Step 1 is capped at one device per proposal.
  Cross-device sequencing is what layer-4 interlocks exist for, and it belongs
  in a plan, not a chat turn.
- **AGENT_RULES.** A human still authorizes every hardware action, and the
  audit names them.

### 5.5 Step 2 — autonomy (not approved)

The target is **not** to give this assistant an actuating tool. It is to
delegate to the SDK's control MCP server (`lab-skills mcp serve
--allow-control`, ARCHITECTURE decision #7), where `execute_plan` re-checks
layer-3 preconditions and layer-4 interlocks per step and holds proper claims
across a multi-step plan. Multi-device work is the only reason to change
surfaces at all.

**Unresolved tension to settle before any of this starts:** §2 assigns
actuation to **tier 3** (the ELN agent), with tier 2 handing off upward. Step 2
either (a) keeps that intact — the dashboard assistant stays a proposer
permanently and autonomy lands in LaAgenteAnalitica — or (b) amends §2's tier
table. Option (a) is the cheaper story and the current default; do not drift
into (b) by accident.

Gates, all required before implementation:

1. Step 1 shipped, with real proposal→authorize traffic in the audit trail.
2. Per-user identity delegates through the SDK path — the claim owner must
   remain the human, not `ac-organic-lab-dashboard`.
3. A plan-approval gate exists: approve the *plan* once, then it runs.
   "Autonomy" here means fewer clicks, not no human (AGENT_RULES still
   requires human-approved plans).
4. The target project has registered layer-4 interlocks.

### 5.6 Build order

| PR | Contents |
|---|---|
| **1 — backend, unwired** | `assistant_control.py` + `lab-control-mcp` entry point; validation against the registry, `allowed_actions`, `SkillDef` schemas and `/authz/check`. pytest incl. refusals: unknown id, action absent from `allowed_actions`, bad args, no role, multi-equipment. Nothing user-visible. |
| **2 — end-to-end** | `assistant.py`: `mode` field, server-side authz re-check, per-mode MCP config / `--allowedTools` / prompt addendum, `proposal` SSE frame. `AssistantBubble.tsx`: toggle, purple theme, confirm card, Authorize → `controlPost`, gating from `/api/auth/mine`. vitest for card rendering + mode gating. |
| **3 — audit + docs** | `X-Control-Origin` on the audit row, `assistant_proposal` event, admin-page column; promote this section from [PROPOSED]; amend ARCHITECTURE #10 ("proposer, still not actuator"); update AUTH_DESIGN's assistant section. |

### 5.7 Open questions

1. **Mode persistence** — reset on panel close (assumed above), or also expire
   after N idle minutes?
2. **Second factor on Authorize** — ac_auth session alone (assumed, consistent
   with tile controls), or reuse the `CONTROL_PASSWORD` lock chip?
3. **Read scope in control mode** — keep `lab-history` connected too (assumed,
   so the model can check history before proposing), or restrict control-mode
   turns to live status only?

### See also (assistant control mode)

- §2 — assistant tiering; this section changes tier 2's *tool surface*, not
  its trust level.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) decision #10 (read-only assistant),
  #7 (the control-capable SDK MCP server), #1 (why the passthrough has no
  interlocks).
- [`AUTH_DESIGN.md`](AUTH_DESIGN.md) — `operator`+ and claims for control;
  the assistant chat's own gate.
- [`INTERLOCKS.md`](INTERLOCKS.md) — the layers a single passthrough action
  does not get.
