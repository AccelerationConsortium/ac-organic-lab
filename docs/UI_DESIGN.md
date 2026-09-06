# Dashboard UI — Design & Decisions

**Status:** living document. Created 2026-07-22 by folding in the former
`OT2_INTERFACE.md` (§1, unchanged in substance); §2 (embedded-assistant
tiering) recorded the same day; the former `WORKFLOW_UI_DESIGN.md` design
note folded in as §3 (still [PROPOSED], nothing built); §5 (assistant control
mode) drafted 2026-08-07 as [PROPOSED] and **implemented** 2026-08-11/13
(Steps 1, 1b–1e — see the §5 heading for the shipped scope); §6 (the Utils
Computers/Printers split and the admin-only SSH console) shipped 2026-08-27.
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
   footprint, offsets, spacing, well geometry, and manufacturer metadata)
   that generates a complete Opentrons **schema-2** definition JSON with a
   live to-scale preview. Vendor, OEM part/product numbers, and manufacturer
   product links use the schema's standard `brand.brand`, `brand.brandId[]`,
   and `brand.links[]` fields, so they survive load-to-edit round trips and
   remain valid input to robot-server.
   Validation ports `opentrons-server`'s `LabwareGenerator` limits (footprint
   128 × 86 mm, height 200 mm, wells inside the footprint). Anyone can build
   + **download** the JSON; building never touches a robot.
3. **Central definition store** (`/api/labware`, `api/app/labware.py`) — two
   merged sources:
   - **(a) repo-committed**: `<repo>/labware/*.json`, PR-reviewed (see
     `labware/README.md`); wins on name collisions and is immutable via the
     API.
   - **(b) uploaded**: `<data-dir>/labware/*.json`, written by
     `POST /api/labware` (session verified at the middleware; **any
     signed-in role may write, as of 2026-08-18** — previously admin-only;
     uploads validated with the same rules; every write audited as a
     `control_action` on the `labware_store` pseudo-device). `DELETE`
     removes uploaded definitions only. Uploaded files are a store
     **envelope** wrapping the schema-2 definition so authorship
     (`created_by` / `created_at` / `updated_by` / `updated_at`) can ride
     next to the geometry without polluting it; the saver's ac_auth
     identity (`X-Auth-User`, injected by the edge after session verify)
     is stamped on write and never taken from the request body. Creator
     is sticky across overwrites; updater moves. Repo definitions stay
     unstamped — git is their authorship. Legacy raw (pre-envelope)
     uploads still load with null authorship until the next save.

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
| **2. Dashboard assistant** | Chat bubble, all dashboard pages | Ask mode: eight read-only `lab-history` MCP tools (history DB, live `/api/equipment`, whitelisted journald) + the append-only `record_observation` journal. Control mode (§5, shipped) adds the propose-only `lab-control` server — still no actuating tool, so this row's trust level is unchanged | Per mode/backend (Ask: Qwen-flagship-class; Control: sonnet-class) | Central dashboard host — `api/app/assistant.py` dispatches per mode to a `claude` CLI subprocess or the `assistant_openai.py` tool loop; the MCP servers are local stdio children either way | Anthropic cloud via the host's Claude Code OAuth (claude-cli backend) or OpenRouter via `ASSISTANT_OPENAI_API_KEY` (openai backend) — ARCHITECTURE #10 records the trade |
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

### 2.5 Review-click convention — when one click, when two

**Decision recorded 2026-08-13.** Assistant surfaces across the lab use two
execution shapes, and the choice between them is a rule, not per-surface
taste. What is uniform is the **vocabulary and the meaning of each click**;
the *number* of clicks follows from what the server stores.

- **Propose → confirm card → Authorize (one click).** For a **single
  immediate action** executed while the operator watches, under a live or
  per-request claim. The proposal object is the review artifact: validated
  server-side, one action per card (no batches, no sequences — §5.3),
  expiring, and re-validated at click time; the device's 412/423 is the
  backstop. Authorize *is* the execution moment. Surfaces: the dashboard
  bubble's Control mode (§5.3), the xArm panel assistant.

- **Propose → Approve → Run (two clicks).** For a **stored multi-step
  plan**. Approve pins *what*: it sends the content hash of exactly the
  steps rendered, so a plan revised elsewhere 409s. Run decides *when*:
  approvals expire, an approved plan can be non-executable
  (`blocked_reason`), and the gap between the clicks is where physical
  staging happens (labware on deck, tips loaded). Surfaces: the OT-2
  gateway's plan store; the platform's workflow executor is the same shape
  at campaign scale (bitácora run authorization → `execute_plan`).

Two rules fall out, one in each direction:

1. **Approve is only real when the reviewed artifact lives server-side** —
   with a content hash, an expiry, and an executable re-check at run time.
   Two buttons over client-held state pin nothing and survive nothing: that
   is review theater, and it is not an acceptable middle state. A surface
   without a plan store uses the one-click shape.
2. **Never add a second click to a single, immediate, stoppable action.**
   Friction must stay proportional to risk: when everything takes two
   clicks, operators stop reading and double-click reflexively, which
   erodes the review property exactly where it matters. This is why the
   dashboard bubble stays one-click *by design* — its proposals are capped
   at one action per card, and anything that outgrows consecutive single
   cards is `execute_plan`'s job (§5.3b), which already carries the
   two-step at the right altitude.

**Graduation path.** When a surface's proposals grow into sequences, do not
add clicks in place — give the device a plan store (the OT-2 gateway's
`plans.py` is the reference: draft/approved/executing states, `step_hash`,
approval expiry, executable re-check) or route through
`execute_plan`/workflow authorization. **Watch item: the xArm panel
assistant** — its proposals are becoming multi-hop pick/place sequences
with gripper actions (dropping a plate is not reversible), which is the
two-click profile. The port is mechanical if it comes due: a device-side
plan object holding the resolved hop list with hash + expiry; Approve pins
it; Run executes hop-by-hop under the operator's claim with the existing
per-hop state re-checks; `/move/stop` stays the untouched safety floor.

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
- **Every run links to its record.** Deep-link each run to its BitacoraDB
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
Devices (STATUS_SPEC /control/*)        BitacoraDB (Plan/Note/Analysis rows)
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
- **The run returns record-layer shapes — and, since 2026-08-13, writes them**
  (D-23, `api/app/record.py`): a `Plan` row under the campaign's `Experiment`,
  plus `step_id`-anchored `Note`s for the steps that failed, blocked or were
  skipped. Successful steps produce no note — the `Plan` row already describes
  them, and a note each would bury the two that matter. The write can never
  fail the run (the run is physical and already happened), is a no-op until
  `BITACORADB_URL` + `BITACORADB_EDGE_SECRET_PATH` are configured, and
  reports its outcome in the `done` frame under `record.write`. The Experiment
  start is the run's own launch instant (`RunState.started_at_utc`), not the
  filing time.

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
`abort_requested` outcomes) plus the D-23 record write (shipped 2026-08-13 —
see the bullet above).

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
3. Transitions the BitacoraDB `Plan` `draft → approved`, stamping the principal
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
Record →  Plan 7f3a  ·  3 Notes  ·  0 Analyses            (BitacoraDB)
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
- **Record footer** deep-links into BitacoraDB; live counts update as notes /
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
5. **Record links + live note/analysis counts.** Close the loop to BitacoraDB.

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

## 5. Assistant control mode [IMPLEMENTED — Steps 1, 1b–1m; Plan mode §5.10]

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
field-level guard — see §5.3b. **Step 1i (2026-08-20)** lets one proposal
carry an ordered multi-step *plan* on one device, approved as a whole and run
step by step from the browser — see §5.3b. Step 2 (autonomy) is sketched at
the end and is **not** approved.

**Temporary conversation update (deployed 2026-09-06):** the bubble warns that
chat is temporary and offers Markdown/JSON downloads. Exports include available
messages and historical proposals/control outcomes, with no reusable approvals.
The latest 20 messages are cached for the signed-in owner in this tab;
logout/account changes clear the chat and live cards. Audit records remain.
**Plan mode** — the third toggle, a saved and owner-private planning session —
is implemented as §5.10 below (ASSISTANT_PERSISTENCE.md step 2). The proposed
routine-control exception (that document's §2) is still not enabled.

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
  about or explain a route. As shipped, this widened what the model *sees*,
  never what it may *propose*: the device's multi-hop `travel_to` stayed
  outside the skill catalog and `allowed_actions`, so a route was proposed
  as `move.<node_id>` hops the model had to sequence itself. **Superseded by
  Step 1k (2026-09-01)** — the snapshot carries no edges, so the model
  could not actually route, and every multi-hop plan died on the device's
  409 `edge_not_allowed` at step 2. `travel.<node_id>` is now proposable
  and the device does the routing; see Step 1k below.
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
| **C — held back at Step 1b** | `startup`, `shutdown`, `setup`, `resume`, `tips.reset`, `move_to`, `pick_up_tip`, `aspirate`, `dispense`, `drop_tip`, `move_labware` | Sequence-bound (the six motion/liquid verbs), unevaluable (`setup`), secret-bearing (`startup` carries `password`, which would land on the card and in the `assistant_proposal` row), safety-floor inverse (`resume` — somebody paused, possibly with hands in the deck), or interlock-adjacent (`tips.reset` declares used tips fresh, disarming the contamination guard). **Superseded the same day by Step 1c below**: every action in this row is proposable today, and the row survives as the record of why each was withheld first. |

The table is the Step 1b assignment; `_PROPOSABLE` in `api/app/assistant_control.py`
is what actually ships. Two verbs post-date the table and joined on the same
criterion, not by an exception: `tips.mark` (2026-08-30 — the partial-rack
repair `tips.reset` can only make by over-claiming a full rack, so it is the
*less* interlock-adjacent of the pair) and `tempmod.set` / `tempmod.deactivate`
(2026-08-30 — one range-clamped scalar each). The catalog/allowlist parity
tests named in that module are what keep the code honest as the gateway grows
verbs; a list in prose, this one included, only goes quietly stale.

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

#### Step 1d — fume hood, shaker, press (2026-08-12); HPLC stays out

Three more bench kinds admitted, same criterion, **no new mechanism** — every
admitted action is one card-evaluable act with zero-or-few scalar,
range-clamped args, and no schema in these kinds carries an
interlock-override or credential field, so `_FORBIDDEN_ARG_FIELDS` gains no
entries (the risky-field pinning test covers every kind in `_PROPOSABLE`
automatically). Catalog names were verified byte-for-byte against the live
`allowed_actions` of all three devices before scoping.

| Kind | Proposable | Operator-only |
|---|---|---|
| `fume_hood` | `sash.move` | `sash.stop` |
| `shaker` | `startup`, `shutdown`, `shake.start`, `shake.set_temperature`, `shake.set_speed` | `shake.stop` |
| `press` | `init`, `press.up`, `press.down`, `plate.in`, `plate.out` | `stop` |

Two decisions worth recording:

- **The xArm's safety-floor deviation generalized into a rule: stop verbs are
  never proposable on any kind.** `sash.stop`, `shake.stop`, and the press's
  emergency `stop` (which additionally forces re-init) stay operator buttons,
  reachable without the assistant; the prompt addendum forbids proposing an
  alternative action to "work around" a stop. The press cycle (`plate.in` →
  `press.down` → `press.up` → `plate.out`) is sequence-shaped and runs under
  Step 1c's discipline — the operator is the sequencer, one card per step.
- **The HPLC (`agilent_uplc_ms`, kind `hplc`) is deliberately not scoped** —
  operator decision, 2026-08-12. Beyond the decision itself, its verbs fit
  the criterion poorly: `run.submit` enqueues an acquisition whose
  correctness lives in the method/sequence, not on a card; `workflow.start` /
  `workflow.end` manage the equipment-blocking campaign lock with role
  semantics (`automation`-role claims the assistant's human actor would not
  hold); `instrument.standby` parks the instrument against a FIFO queue the
  card cannot show. It stays operator/workflow-only, and the prompt addendum
  tells the model to route HPLC control requests to those surfaces.

Operator-only is a property of the **action**, not of the asker. The
control-mode prompt addendum says so explicitly, because the first version
reported excluded actions as "needing an operator" — true of every proposal,
and misread as a permissions problem.

#### Step 1e — the xArm's gripper (2026-08-13)

Reported from the bench: the assistant could move the arm but reported "I can't
propose gripper control here" — it could *see*
`details.motion_graph.allowed_gripper_targets: ["grip_120"]` (forwarded since
Step 1b) yet had no action to invoke. Three independent gaps, all closed:

1. **The device never advertised it.** `_build_allowed_actions` emitted only
   `stop` / `connect` / `clear_errors` / `move.<node_id>`, while
   `POST /control/graph/gripper` had always honored the whitelisted
   transitions — `allowed_actions` *understating* capability, the direction
   §6.2 forbids. The device now lists one `gripper.<state>` per reachable
   catalog state (xarm-translocation, `status_builder.py`).
2. **The skill catalog had no entry.** `robot_arm` registered four
   `graph.*` skills, none for the gripper endpoint; `graph.gripper` is now the
   fifth.
3. **The resolver had no bridge.** `_resolve` handled `move.<node_id>` only.
   `gripper.<state>` now bridges the same way, for the same reason — the state
   rides in the action name, so the model cannot name a transition the device
   would refuse.

Enumerating one action per legal state (not a single `gripper.set` with a state
arg) is what keeps the §6.2 mirror possible at all: the whitelist is per
(node, current stroke), which one action name cannot express, so a state-arg
form would let the model propose an illegal transition and collect a 409 after
the operator had already clicked Authorize.

**This is not pick/place.** `DASHBOARD_ASSISTANT_GRAPH_PLAN.md` holds the
composite pick/place verbs back, and still does — a pick remains
move → gripper → move, three cards the operator sequences under Step 1c's
discipline. A single gripper transition is one card-evaluable act: one required
string arg drawn from a list the device publishes, no interlock-override or
credential field (so `_FORBIDDEN_ARG_FIELDS` gains nothing), and the device
refuses it outright unless the arm is parked at a pinned node in STRICT mode.

> **Follow-up (2026-08-13, ROADMAP skill-name reconciliation):** the device
> now *also* advertises the catalog family names
> (`graph.{move_to,gripper,recover_to,record,mode}`) in `allowed_actions`, so
> `lab.skills()` computes `robot_arm` availability without any bridging. The
> per-target `move.<node_id>` / `gripper.<state>` names stay, and the resolver
> bridge above stays with them — the target-in-the-name form is what makes a
> proposal card refusal-proof, which a bare family name cannot be. The family
> names surface in `list_available_actions` as `proposable: false` rows; that
> is correct, not a gap.

#### Step 1f — cameras: PTZ, presets, privacy, streaming (2026-08-18)

Cameras (`kind: camera`, the `kasa-tapo-services` gateway) admitted with the
same criterion as Step 1d and, again, **no new mechanism**: a new
`skill_catalog/camera.py` module registers the gateway's advertised surface,
and `_PROPOSABLE["camera"]` scopes it exactly as any bench kind. What makes
cameras distinctive is not the mechanism but the *risk tier* — `camera` is
one of the two kinds in `EQUIP_GUIDE.md`'s `UNGATED_KINDS` (§6b there): PTZ,
presets, snapshots, and recording carry no `CONTROL_PASSWORD` lock chip on
the tile itself because they cannot damage hardware or a sample. Step 1f does
not weaken the *assistant's* commitment on that account — every camera action
still renders a confirm card and still requires an authorized actor with
`operator`+ on the device (§5.2) — it only explains why the admission bar was
easy to clear.

| Kind | Proposable | Excluded |
|---|---|---|
| `camera` | `ptz`, `preset/save`, `preset/goto`, `privacy`, `streaming` | `preset/{id}` (delete) |

Two things worth recording:

- **Action names are slash-separated, not dotted** (`preset/save`, not
  `preset.save`) — the one respect in which this kind differs cosmetically
  from every other entry in `_PROPOSABLE`. That is simply what
  `kasa_tapo_services/routes/cameras.py` puts on the wire in
  `allowed_actions`, and the resolver's direct name-match branch does not
  care either way — no camera-specific bridging code was needed, unlike the
  xArm's `move.<node_id>` / `gripper.<state>`.
- **`preset/{id}` (the delete verb) is excluded, not merely unscoped.** The
  gateway always advertises it as the literal string `"preset/{id}"` — a
  template announcing "you may `DELETE` any preset", never a concrete,
  resolvable id (see `camera.py`'s module docstring). There is nothing for a
  generic name-match lookup to resolve it to, so it stays operator-only by
  construction, the same category as a stop verb even though nothing here
  is safety-floor.

Also fixed in the same change: `skills/tests/test_skills.py`'s
"kind with no registered defs" fixture previously used `camera` as its
example of an equipment kind with an empty skill catalog — no longer true —
and was repointed at `smart_plug`, which still has none.

#### Step 1g — Cytation plate reader (2026-08-19)

The Cytation (`kind: plate_reader`) is now available in Control mode after its
15-skill catalog was aligned to the live device OpenAPI. The assistant still
has no actuating tool: it validates one proposal, the signed-in operator
authorizes the card, and the dashboard's existing claim → act → release
passthrough performs the request.

| Proposable finite actions | Workflow/operator-only |
|---|---|
| `startup`, `shutdown`, `drawer.open`, `drawer.close`, `plate.load`, `plate.unload`, `well.update`, `read.absorbance`, `read.fluorescence`, `read.luminescence`, `imaging.capture`, `incubator.set_temperature` | `incubator.stop`, `shake.start`, `shake.stop` |

The split follows §5.3b's standalone-act test, not a device-kind exception.
Each admitted action terminates in one request and its complete
wells/wavelength/exposure/`celsius` body fits on the confirm card.
`incubator.set_temperature` (admitted 2026-08-21) is a setpoint change of
the same class as `shake.set_temperature` / `seal.set_temperature`; holding
a temperature is not an operation in progress on this device.
`incubator.stop` remains a safety-floor control. Cytation `shake.start` is
still excluded: unlike the Torrey Pines cycle it has no duration timer and
needs a later `shake.stop`, so that start/use/stop sequence belongs in a
human-authorized workflow.

Because Cytation reads and autofocus imaging can exceed the dashboard's normal
15-second device timeout, plate-reader passthroughs use a 90-second action
window and heartbeat the per-request claim until the action returns. Release
still runs in `finally`; repeated heartbeat failure reports that the action may
have completed but the protected sequence failed, instead of presenting an
unprotected read as successful.

Read and imaging responses are rendered in a bounded JSON block after
Authorize. They stay browser-side: the model turn has already ended, so the
measurement is not silently sent back to the assistant's model provider.
For multi-step or record-bearing work, the `lab-runner` path remains the
authority: a human authorizes a main-merged package in bitácora, `lab-runs`
starts it, and `execute_plan` re-checks the Cytation's live
`allowed_actions`, claims it per step, and records the run.

#### Step 1h — PlateLoc sealer (2026-08-19)

The PlateLoc (`kind: plate_sealer`) is admitted with the same criterion as
Step 1d / 1g and, again, **no new mechanism**. A seal cycle is one bounded
act: the device owns the 0.5–12 s duration timer and withholds `seal.start`
from `allowed_actions` until the heater is in band and the stage is in, so
a confirm card that names temperature and time is evaluable. Stage in/out
and the two setpoint verbs are the same one-card shape already used for
the press cycle.

| Proposable | Operator-only |
|---|---|
| `startup`, `shutdown`, `stage.in`, `stage.out`, `seal.set_temperature`, `seal.set_time`, `seal.start` | `seal.stop` |

`seal.stop` is the safety-floor stop verb on this kind — reachable from
the tile without the assistant, never proposed. Stage in → seal.start →
stage out is sequence-shaped and runs under Step 1c's discipline: one
card per step, re-checking `allowed_actions` between cards. If
`seal.start` is not advertised (heater still ramping, stage out, or an
uncleared recent-failure window), the assistant must say so rather than
propose it anyway.

#### Step 1i — multi-step plans on one device (2026-08-20, operator decision)

Step 1c made the operator the sequencer: a sequence ran as consecutive
confirm cards, one click binding one step. In practice that meant clicking
through a four-card filtration cycle or a three-card pick, and the operator
asked for what the OT-2 gateway already offers its own chat panel
(`opentrons-server/docs/AGENT_PROPOSALS.md`): propose the **whole sequence
once**, review it **once**, run it. Step 1i adopts that model, generalized
to every kind Control mode can already propose for, with **no new
actuating path**:

- **`propose_plan(equipment_id, steps, reason)`** is the multi-step sibling
  of `propose_action` in `lab-control`. Same gates, applied per step — the
  actor binding, the per-kind allowlist, the resolver, the args schema, the
  field guard, per-equipment authz — and the same refusal shape, now naming
  the failing `step`. The one deliberate difference: only **step 1** is held
  to the device's live `allowed_actions`. Later steps are legal only once
  the earlier ones have run (`seal.start` after `stage.in`, `aspirate` after
  `pick_up_tip`, the next hop after this one), so the live list cannot
  vouch for them at proposal time; the device re-checks every step as it is
  actually sent. One device per plan; at most `MAX_PLAN_STEPS` (256) steps —
  a card nobody can read end to end is a rubber stamp.
- **One card, approved by hash.** The card renders the ordered list with
  arguments and device state. **Approve these N steps** sends the
  `step_hash` of exactly what was rendered to
  `POST /api/assistant/plans/{id}/approve`; the API compares it with the
  hash it recomputed from the steps the tool actually produced (cached
  in-process when the `plan` frame passed through, TTL 10 min, gone on
  restart — an approval is a review of one moment) and refuses a mismatch
  with 409, a different operator with 403, an unknown/expired id with 404.
  The approval is a **review record** (`assistant_plan_approved`: who agreed
  to which steps), not a permission grant.
- **Run: the browser executes, step by step.** §5.1's commitment holds
  verbatim: no model-driven code path POSTs to a device. The browser sends
  each step through the same `/api/equipment/{id}/control/{action}`
  passthrough a tile click uses — per-equipment authz, the per-request
  claim dance, the device's own 412/423, and the `control_action` audit row
  all apply per step. Each row carries `origin: assistant-plan` and
  `plan: <plan_id>#<step>` so it joins back to the approval. The first
  refusal **halts** the plan and marks the rest skipped — never
  continue-past-error, because later steps assume earlier ones happened.
  The browser then reports the outcome (`assistant_plan_finished`), best
  effort. Two clicks per plan (Approve, then Run), not one: approving
  records what was reviewed, and it keeps the last action before hardware
  moves from being a single click on a screen nobody read.
- **What it deliberately does not do.** No claim is held across the plan
  (the passthrough is per-request by design, ARCHITECTURE §"dashboard
  writer"); another session can take the device between steps, and the
  device's 423 then halts the plan — same as clicking through cards. No
  layer-4 interlocks run (unchanged from Step 1; single-device is the case
  where that is acceptable). Closing the tab mid-run stops sending steps —
  the human is supposed to be present. Cross-device work is still a
  workflow plan (§5.5).

Binding-rules note: Part I rule 3 ("no ad-hoc command sequences") was
already carved by Step 1c's operator-sequenced cards; Step 1i changes only
the *granularity* of the human approval (one review per sequence instead of
per step), never the existence of the hardware gate — the loosening Part II
§"trust ladder" names as the intended direction. The human still authorizes
every hardware action and the audit names them.

#### Step 1j — the terminal-call contract (2026-08-25)

The recurring operator complaint: the assistant *understands* a control
request, narrates an answer — and no authorize button appears. Two causes,
both structural. First, the button renders **only** from a
`propose_action`/`propose_plan` tool result; the "propose or explain why
not" rule was prompt-only, and once Control mode moved from claude-cli/
sonnet to a flash-tier OpenRouter model (`ASSISTANT_OPENAI_CONTROL_MODEL`),
prose-only endings became routine — the model writes "I've proposed it"
without calling the tool, which renders nothing. Second, a proposal the
lab-control server **refused** (`not_allowed`, `invalid_args`, …) produced
no frame at all: the refusal's why lived only in prose the model may or may
not write, so a refused proposal was indistinguishable from no proposal.

Step 1j makes the contract mechanical instead of rhetorical:

- **`decline_proposal(reason_code, explanation)`** joins `lab-control` as
  the third terminal tool. Every control-mode reply must end with exactly
  one terminal call — propose_action, propose_plan, or decline_proposal
  (reason codes: not_proposable, safety_floor, cross_device,
  too_many_steps, needs_human, device_unavailable, unsafe_state,
  informational, other; an unknown code coerces to `other` — the tool
  terminates turns, it must never be one more thing that can fail). It
  never actuates; it returns `{"declined": {...}}`.
- **Refusals and declines are frames, not prose.** Both backends surface
  `proposal_refused` (a propose result whose `code` is in
  `assistant_control.REFUSAL_CODES`) and `declined` frames; the bubble
  renders them as an amber chip ("Proposal refused (code): …") and a muted
  chip ("No action proposed — …") inside the turn. Informational declines
  render nothing — they exist to end the turn, not to be read. The why of
  a missing button is now always on screen.
- **The openai backend enforces the contract.** A control turn about to end
  with no terminal outcome (no propose/decline call, no proposal / plan /
  refusal / decline payload) is bounced back to the model **exactly once**
  (`CONTROL_TERMINAL_NUDGE`, a harness-authored user-role message that is
  never persisted into the bubble's history). If the model still ends
  without one, the backend emits a harness `declined` frame naming the
  failure instead of ending silently. The claude-cli backend cannot be
  nudged mid-loop (the CLI owns the agentic loop), so there the contract
  rests on the rewritten prompt rule 1 plus the same frame surfacing.
- **The nudge round forces the call (2026-09-04).** A week of journal on
  `deepseek-v4-flash` showed the nudge text is one more instruction a
  flash-tier model can ignore, and that the larger leak was elsewhere: of 91
  control turns, 2 ended prose-only despite the nudge, but **7 hit the 120 s
  wallclock cap** mid-orbit and produced only a bare `error` frame — which
  reads as the assistant ignoring the request. Two mechanical changes: the
  nudge request now offers **only the three terminal tools** with
  `tool_choice: "required"`, so the provider — not the prompt — makes the
  model call one (the harness `declined` frame stays as the fallback for a
  provider that ignores `tool_choice`); and a control turn that hits the
  timeout or the tool-round cap with no terminal outcome now emits a harness
  `declined` frame saying so *before* the `error` frame, so the operator sees
  "ran out of time before proposing" rather than nothing.
- **A longer leash and a Stop button (2026-09-04, operator request).** The
  wallclock cap is 300 s (`ASSISTANT_CLAUDE_TIMEOUT_S`, was 120), with the
  Next proxy's `proxyTimeout` raised to 330 s above it. What makes a long cap
  affordable is that the operator can now end a turn: while a turn is in
  flight **Stop** stands where Send was; it aborts the fetch, which closes the
  SSE response and cancels the API's generator mid-round (the turn log names
  it `client_disconnected`), and the turn is marked "Stopped by you" so a
  half-written answer is never mistaken for a finished one. Nothing actuates
  from a chat turn, so there is nothing to roll back.

The §5.1 commitment is untouched: none of this adds an actuating path —
the worst a misbehaving model can now do is *visibly* fail to propose.

- **The tier-2 trust level (§2.2).** No tool in either mode actuates, so the
  read-only-by-construction guarantee and ARCHITECTURE decision #10 survive
  intact. What the tier gains is a *rendering* capability, not a hardware one.
- **Interlocks.** The passthrough deliberately runs no skill preconditions and
  no project interlocks (ARCHITECTURE decision #1); the device's 412/423 is
  the only backstop. Single-equipment actions are exactly the case where that
  is acceptable — which is *why* Step 1 is capped at one device per proposal,
  and Step 1i at one device per plan. Cross-device sequencing is what layer-4
  interlocks exist for, and it belongs in a workflow plan, not a chat turn.
- **Binding rules (AGENTIC_LAB_DESIGN.md Part I).** A human still authorizes
  every hardware action, and the audit names them.

#### Step 1k — device-planned multi-hop travel on the xArm (2026-09-01, operator decision)

The operator complaint: the assistant cannot move the arm anywhere more than
one hop away. Audit evidence (`equipment_events`, 2026-09-01): every
multi-hop plan ran step 1 fine and died on step 2 with the device's 409
`edge_not_allowed` — e.g. `[move.deck_home, move.deck_slot1_high, …]`,
skipping the mandatory `deck_high` between them. Root cause: §5.3's original
stance asked the model to propose a route as `move.<node_id>` hops while the
`motion_graph` snapshot gives it **no adjacency/edge data** — `travel_targets`
is a flat reachable set, so the intermediate hop was unguessable. The device
refusing the guess is layer-2 doing its job; the failure was asking the model
to plan without a map. Two aggravators: the browser rendered the structured
409 detail as `[object Object]` (`String(detail)` on a dict in
`web/src/lib/api.ts`), and the 15 s passthrough budget sat inside the live
hop-duration range (13.4–23.2 s measured).

What shipped, dashboard-side only (no device change — `POST
/control/graph/travel_to` has existed on the device all along, a shortest-
path planner that executes the whole journey under one reservation and one
blocking call):

- **`graph.travel_to` joins the skill catalog** (`skill_catalog/robot_arm.py`)
  and `_resolve` gains a `travel.<node_id>` bridge, the same shape as
  `move.<node_id>` / `gripper.<state>`.
- **Startability comes from the snapshot, not `allowed_actions`**: the device
  deliberately never enumerates multi-hop targets, so `_action_startable`
  accepts `travel.<node>` iff the node is in the snapshot's
  `reachable_nodes` ∪ `travel_targets` (fail-closed with no snapshot); the
  device re-plans and re-checks the route when the call is sent — it remains
  the authority. `list_available_actions` synthesizes one proposable
  `travel.<node_id>` entry per target (`synthesized_from: "motion_graph"`).
- **The prompt addendum inverts its routing rule**: travel.<destination> for
  anything beyond one hop, never a model-sequenced `move.<node_id>` route; a
  pick/place plan is now (travel, gripper, travel).
- **`control.py` gives `robot_arm` a 180 s action budget** (the per-action
  claim heartbeat already covers the wait), alongside the plate reader's 90 s.
- **`ApiError` stringifies structured details** (prefer `error`/`reason`
  fields, fall back to JSON), so a device refusal reads as its actual reason.

What it deliberately does not change: the safety floor (stop / connect /
clear_errors stay non-proposable), one device per plan, the confirm card as
the only path to actuation, and the plan runner's fail-fast. The §5.1
commitment survives — proposing travel renders a card naming the
destination; the device still whitelists every hop it executes.

#### Step 1l — solid doser (2026-09-02, operator request)

The operator complaint: nothing on the solid doser (`dose_every_well`, "Dose
Every Well", `kind: solid_doser`) could be composed in Control mode — "at
least let it lower and raise the plate lift". Root cause: the kind was simply
absent from `_PROPOSABLE`, so every advertised action refused as
`unmappable_action`. A second gap sat underneath: the four single-axis loader
moves the tile has driven since dose v1.1 (`lid.open`, `lid.close`,
`plate.raise`, `plate.lower`) were advertised by the device but never
cataloged, so even an allowlist entry could not have resolved them.

Admitted with the Step 1d / 1g / 1h criterion and **no new resolver
mechanism**: every admitted action is one card-evaluable act with zero or a
few scalar, range-clamped args; no schema carries an interlock-override or
credential field (`startup.config_name` is a profile name), so
`_FORBIDDEN_ARG_FIELDS` gains no entries. This device has **no stop verb** —
its motion endpoints block until the move completes — so there is no
safety-floor row; the loader's own collision guard (it refuses raising the
plate under a closed lid) and the device's claim/state checks remain the
authority at execution time.

| Proposable | Workflow-only |
|---|---|
| `startup`, `shutdown`, `home`, `tare`, `plate.set`, `plate.load`, `plate.unload`, `lid.open`, `lid.close`, `plate.raise`, `plate.lower`, `dose.well`, `dose.multiple` (≤ 6 wells per step), `calibrate.flow_rate` | `dose.row`, `dose.column`, `dose.all` |

The held-back column is a **request-window** decision, not a safety one.
Dosing is synchronous at roughly 15 s per well (catalog estimate; no dose has
yet run through the dashboard passthrough), a row is 12 wells and a plate 96,
and a passthrough request for this kind now lives at most 120 s
(`control.py` `_SOLID_DOSER_CONTROL_TIMEOUT_SECONDS`, raised from the 15 s
default that had already 504'd six doser `startup`s at 16.6 s) under the
Next.js proxy's 130 s cap (`web/next.config` `proxyTimeout`). With no stop
verb, a timed-out whole-plate dose would also be un-abortable from the
dashboard. So `dose.multiple` is admitted with a per-step cap of 6 wells
(`_ARG_CARDINALITY_LIMITS`; refusal `invalid_args`, message names the split),
the prompt addendum tells the model to chain batches as consecutive steps of
one plan, and whole-line / whole-plate dosing is routed to a validated
workflow plan. The composable shape the operator asked for is one plan —
`plate.lower` → `tare` → `dose.multiple` → `plate.raise` — approved once,
each step re-checked live by the device.

Catalog parity: `skill_catalog/solid_doser.py` gains the four loader moves
(advertised in `ready` and `degraded`; 2.6–2.9 s each in the audit trail),
`SolidDoserClient` gains the matching typed methods, and a skills test pins
the catalog byte-for-byte to the device's advertised names so the next verb
the device grows fails loudly instead of staying silently unproposable.

Deploy note: `assistant_control.py` and the catalog run in the per-request
`lab-control` subprocess from editable installs, so the allowlist is live on
save; the prompt addendum (`assistant.py`) and the control budget
(`control.py`) are in-process and need an API restart.

#### Step 1m — deck-slot vocabulary resolver + deck check (2026-09-04, operator request)

Two operator complaints with one root: the model "mistakes ot2-hte slot 2 for
deck slot 2", and nobody is asked to look at the OT-2 deck before something
is proposed against it.

**The slot confusion is a vocabulary gap, not a model-quality problem.** One
shelf has three names — `ot2_hte/slot_2` in `locations.yaml`, the bare key
`"2"` in every OT-2 argument (`tips.reset`/`tips.mark` `slot`, `move_labware`
`new_location`, `setup` `labware[].location`, the keys of `deck.declare`
`slots`), and `opentrons_2_low` / `opentrons_2_high` in the xArm graph — and
neither the prompt nor `lab-control` ever showed the model the mapping, so
"slot 2" landed in whichever vocabulary it guessed. The fix is deterministic
(`assistant_control.py`):

- **A resolver, before schema validation.** `_canonicalize_locations`
  rewrites every slot-carrying OT-2 argument to the gateway's key, accepting
  `2`, `"slot 2"`, `slot_2`, `ot2_hte/slot_2`, or an xArm node id for the same
  shelf. A token naming a place on a *different* device is refused by name
  (`wrong_device_location`: `ot2_complexation/slot_2` on `ot2_hte`), a
  registry place with several keys on this device is refused as
  `ambiguous_location`, and a token the registry does not know passes through
  unchanged — the resolver never invents a slot; the schema and the device
  stay the authority. Without `locations.yaml` it runs syntax-only.
- **The vocabulary is shown.** `list_available_actions` returns `locations`:
  for an OT-2, each deck slot with its bare key and the names other devices
  use for it; for the arm, each registry place it can reach with the node ids
  that reach it. An arm `travel.ot2_hte/slot_2` refusal now names those nodes
  (`location_nodes`) instead of only listing what is allowed.
- **The card names the place.** Proposals and plans carry
  `resolved_locations` (`field`, canonical `value`, registry `location`,
  `label`, the model's `given` spelling when it differed); the card prints
  `slot=2 (OT-2 HTE · slot 2)`. Plan labels are step-tagged and kept
  *outside* `steps`, so the step hash the operator approves covers exactly
  what the browser sends.

This is vocabulary translation on a human-authorized proposal, not custody
inference — PLATE_TRACKING.md's "aliases never infer a move" rule is
untouched; nothing here decides *whether* something moves.

**Moved into the SDK the same day.** The resolver first shipped inside
`lab-control`, which protected only the assistant; a plan written through
`lab-skills` (a workflow repo, the SDK's MCP `execute_plan`) with
`ot2_hte/slot_2` in a `slot` field still passed the catalog's `str` schema and
died at the gateway. It now lives in `lab_skills.deck_slots` and runs inside
`validate_plan` (a wrong-device place is a `wrong_device_location` Violation,
never a 4xx later) and `execute_plan` (the POST body is the canonical form);
`LabSession(..., locations=...)` / `Lab.connect(locations=...)` select the
registry, defaulting to `locations.yaml` loaded lazily. `lab-control` imports
the same functions and only translates `SlotResolutionError` into a
`ProposalRefused` with the same code. The one path still sent as written is a
direct `EquipmentClient.command()` body, which carries no skill name.

**The deck check makes "look at the deck first" mechanical.** Every proposal
that touches an OT-2 deck — an OT-2 verb in `_DECK_ACTIONS` (`setup`,
`deck.declare`, `move_labware`, the pipette verbs, `tips.*`, `home`), or an
xArm `travel.`/`move.` to a node the registry maps onto an OT-2 slot — carries
`deck_checks`: per device, `details.snapshot.labwares` by slot plus the
per-rack available-tip count, with `touched_slots` marked (from the resolver
for slot arguments, from the snapshot for nickname-addressed verbs). For an
arm move the target OT-2's `/status` is read live, best-effort: a failed read
is reported as `unreachable`, never hidden. The card renders one "Deck now"
row per device (`2*: empty · 4: agilent_96_2ml_deep_square · 11:
opentrons_96_tiprack_1000ul (12 tips)`) and asks the operator to check the
physical deck before authorizing. The snapshot is the gateway's belief; the
operator at the bench is the authority. The prompt gained the matching rule
("CHECK THE DECK FIRST": read status, report the touched slots' contents, ask
for a physical confirmation, and do not propose into an occupied or unknown
slot until the operator has resolved it), so the model asks in words and the
card asks in print even when the model forgets.


#### Camera frames in the chat, and progress pills at the bottom (2026-09-04, operator request)

Two things the operator asked for the same afternoon the assistant moved to a
vision-capable model (`deepseek-v4-flash-vision-exp`).

**"Use the camera and show me."** `lab-history` gained
`capture_camera_snapshot(camera_id, lens)`, a read tool in both modes. It
reads one JPEG frame from **go2rtc's live relay** (`GET /api/frame.jpeg?src=
<camera>_<lens>`) — the stream every dashboard viewer already receives — and
deliberately not from the gateway's `POST /cameras/<id>/control/snapshot`:
`mcp/servers.yaml` forbids this server any `/control/*` path, and a frame off
the relay commands nothing. It refuses when the camera is unreachable, in
privacy mode, or has streaming disabled, so the same toggle that blanks the
tile blanks the assistant. The frame is saved under the assistant runtime
dir (`snapshots/`, pruned after 24 h), served back only behind the sign-in
gate at `GET /api/assistant/snapshots/<name>` (a strict filename shape, no
path walk), and surfaced as an **`image` SSE frame** the bubble renders inline
in the turn with camera · lens · time and a full-size link. On the openai
backend the frame is also **attached to the model's context** as an
`image_url` data-URL part in a harness-authored user message right after the
tool round (`ASSISTANT_OPENAI_IMAGE_INPUT`, default on; set `0` for a
text-only model, whose request an image part would fail), with the
instruction to describe only what is actually visible. Aiming the camera
stays a Control-mode proposal (Step 1f); the prompt says so. The first
deploy emitted the path only as `image_url` while the bubble read `url`, so
frames were captured, served and never rendered (the browser never fetched
them — visible in the journal); the frame now carries both names and the
bubble accepts either. As a second fallback the tool asks the model to put
the path in its reply, and reply text now has its URLs made clickable
(`linkify`), backticks and sentence punctuation excluded.

**"Keep the progress visible on a long reply."** The tool/phase pills moved
from the top of the assistant bubble to its **bottom**, after the text,
images and chips. The chat auto-scrolls to its end, so on a long answer the
pills now stay where the eye is instead of scrolling off with the first
paragraph.


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
   "Autonomy" here means fewer clicks, not no human (AGENTIC_LAB_DESIGN.md
   Part I still requires human-approved plans).
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

### 5.8 Verification record (absorbed from the retired ASSISTANT_CONTROL_VERIFICATION.md)

The full evidence document (566 lines: refusal matrix, mode-gating table,
capture transcripts, the local stub rig) was retired 2026-08-12; it survives
in git history. What follows is everything from it that stays load-bearing.

**Verified.** The software path end to end, 2026-08-11 (device PC + server):
tool surface (two tools, neither actuating), all seven refusal gates with
correct codes, actor binding, server-side mode decision including the
`DASHBOARD_CONTROL_OPEN` refusal, `proposal` frame → confirm card → Authorize
→ passthrough claim dance → both audit rows, and the full production path
through the Next middleware (session cookie → injected identity). On real
hardware, 2026-08-12: with the arm connected by a human and the node pinned,
a Control-mode turn produced a valid `move.robot_home` proposal. **Nothing
was authorized, so nothing moved** — the authorize-on-hardware click and the
subsequent check of both audit rows remain open, deliberately: that is a
human decision, not a verification chore.

**The one real bug, and its lesson.** The CLI (2.1.227) delivers MCP tool
output double-wrapped — `{"result": "<json string>"}` — and the SSE bridge
expected the bare tool JSON, so valid proposals silently produced no card
*and* no `assistant_proposal` row. Fixed in `assistant.py` (tolerates both
envelopes) with a regression test pinning the envelope **captured from a real
stream**. Two rules worth keeping: envelope-shaped tests must encode observed
shapes, not assumed ones; and **trust the frame, not the narration** — the
model announcing "the card is up" is not evidence, it cannot see the browser.

**Deploy check (load-bearing, not diagnostic).** After any deploy that
touches `api/`, confirm the console scripts exist: `ls .venv/bin/lab-*-mcp`.
The MCP spawn prefers the console script beside the running interpreter
(`b542960`); if it is missing, the resolver falls back to `uv run`, whose
self-sync **fails under the API unit's `ProtectHome=read-only`** (uv cannot
write its cache) and on any host that cannot build the dependency tree. The
symptom is maddeningly indirect: the model says its tools are unreachable,
Ask mode is equally toolless, the CLI exits 0, and the failed init event is
not forwarded by the SSE bridge.

**Observables when debugging mode behaviour.** The reliable signal for
whether Control mode was granted is which file `assistant.py` wrote in
`$ASSISTANT_RUNTIME_DIR`: `mcp.control.json` (granted) vs `mcp.json`
(downgraded to ask). The `assistant chat: mode=…` log line is invisible under
a bare `uvicorn` (unconfigured logger); do not rely on it.

**Authorization semantics, as measured.** §5.2's "`operator`+ on that
equipment" is in practice "**holds any grant** on that equipment":
`/authz/check` reports `allowed` for any device grant (a `role: none` user
with a single equipment grant qualifies). Identical to the tile path — just
don't expect a role-name comparison.

**xArm hardware prerequisites** (for whoever performs the remaining
authorize): a human connects the arm (`do_not_call_connect` — connecting
energizes servos and enables the track without a homing sweep); then the
current node must be pinned or `allowed_actions` collapses to `["stop"]` and
nothing is proposable — read `GET /graph/nearest`, pin with
`POST /control/graph/recover_to` **without `force`** (bookkeeping, not
motion), and release the claim afterwards or the leftover claim 423s the
passthrough.

### 5.9 Seeing and remembering (2026-08-13)

Three same-day changes to what the assistant can perceive and retain, none of
which move its trust tier:

- **`get_equipment_status(equipment_id)`** (lab-history, both modes): the one
  device's full envelope — components, `details`, metrics. Until it existed,
  `list_equipment_now` flattened every device to a summary row and
  `list_available_actions` forwarded only actions, so sub-status hardware
  (the OT-2's pipette mounts, tip racks, loaded plate) was invisible in both
  modes.
- **`record_observation(equipment_id, observation)`** (lab-history, both
  modes): the HERMES_ACCESS_DESIGN Phase 4 learning loop as one append-only,
  actor-stamped `agent_observation` row through `/api/ingest/events` — the
  same journal PyPoe's investigator writes and the assistant already reads
  back. `LAB_ACTOR` now rides the lab-history env in Ask mode too (same
  bind-identity-to-the-tool rationale as §5.3); without it the write fails
  closed. Chats themselves stay siloed by design — learning goes through the
  audited journal, never a conversation-fed memory.
- **Subprocess stream limit raised to 10 MiB** (`assistant.py`): stream-json
  puts an entire tool payload on one line, and an OT-2 deck/tip snapshot
  cleared asyncio's 64 KiB default — `readline()` answered with "Separator
  is found, but chunk is longer than limit" and killed the turn.

### 5.10 Plan mode — saved planning sessions (2026-09-06, operator decision)

**Status: implemented on `design/assistant-modes`; not yet deployed.** The
design is [`ASSISTANT_PERSISTENCE.md`](ASSISTANT_PERSISTENCE.md) (§0 UX, D-1
… D-3, D-9; build-order step 2). This section records the shipped shape.

**What it is.** Ask and Control are temporary: the conversation lives in the
tab. Plan is the mode for developing a reusable protocol over days — the
conversation is a **named, owner-private session saved on the dashboard**
(`assistant.db`, `api/app/assistant_sessions.py`), reopened from a rail under
the mode notice. It has **Ask's toolset**: the read-only `lab-history` and
`lab-inventory` servers plus a Plan addendum to the system prompt. It never
registers `lab-control`, so no proposal card can be produced in a saved
session, and nothing in one can actuate hardware (D-9). Saving files nothing —
protocols are edited and registered in bitácora through project review; the
notice bar and the export say so in words.

**Trust story, unchanged.** Identity is the middleware's verified
`X-Auth-User` / `X-Auth-Role` (every `/api/assistant/*` path is already behind
sign-in). The owner has full access; a **global admin may read** — list with
`?scope=all`, open, export — but rename, delete and turns are **403**: an
admin never acts as the owner. Anyone else gets **404**, so the id space leaks
nothing. There is no identity under `DASHBOARD_CONTROL_OPEN`, so there are no
saved sessions in that dev mode either.

**The turn** (`POST /api/assistant/sessions/{id}/turns`, SSE like `/chat`):
the client sends only `text` and a `request_id`. The server stores the user
message, rebuilds the model's context from the **stored** session (last 40
messages), runs the Ask engine with the Plan addendum, and closes the answer in
a `finally` with its true state — `completed`, `failed`, or **`interrupted`**
when the engine stopped short or the browser went away. A repeated
`request_id` **replays** the stored frames instead of running again. One
active turn per session (409 otherwise); a rename with a stale `revision` is
409 and the bubble reloads the other tab's version; on window focus the open
session is re-read when its revision moved. A restart marks every `running`
answer `interrupted` and unlocks its session — an unfinished answer never
becomes a finished one.

**Inert restore.** A stored message is text plus display-only projections:
tool names with a finished flag, camera-frame **links** (a saved URL is not a
saved image; the shared 24 h snapshot dir still governs), imported
control-history entries, refusal/decline chips. No approval state exists to
restore, and `turnFromSaved` in the bubble rebuilds turns, never cards.

**Switching.** Entering Plan from a non-empty temporary chat shows the §0
preview (`CarryOverDialog`): save it into a **new named session** (the
messages travel as the create request's `seed`, marked `imported`), start Plan
without saving, or stay. Nothing is persisted before that click, and the
temporary tab cache is dropped when Plan opens — the temporary conversation
ends either way, and the download buttons are still on screen for it. Leaving
Plan (the toggle, or minimising the panel — mode still resets to Ask on close,
§5.7 Q1) keeps the session on the server and opens a new temporary chat. A
streaming turn or a running plan blocks the switch until it concludes.

**Limits.** `ASSISTANT_SESSION_RETENTION_DAYS` (180) purges untouched sessions
on open and on create; `ASSISTANT_MAX_SESSIONS_PER_OWNER` (200) and
`ASSISTANT_MAX_MESSAGES_PER_SESSION` (2000) refuse with 409. `ASSISTANT_DB_PATH`
moves the file (default: beside the resolved `lab.db`, inside the unit's
writable `data/`). If the store cannot open, `/api/assistant/health` reports
`saved_sessions: false`, the bubble hides the Plan toggle, and Ask/Control are
untouched.

**Accent.** Sky, panel-wide, the way Control is purple — a saved session must
never read as either temporary mode; each turn keeps the accent of the mode it
was sent under, so a carried-over Control exchange stays purple inside a Plan
session.

**Verification.** `api/tests/test_assistant_sessions.py` (17): identity
required; owner/admin/stranger matrix; server-built context and `control=False`
on every Plan turn; idempotent replay; one active turn; interrupted vs failed
vs completed across an engine that stops short, an engine error, a store
reopen, and a stale lock; revision conflicts; retention and both caps; seeded
history imported inertly and an export with `executable: false`. Bubble
(`AssistantBubble.test.tsx`, Plan block, 9): the toggle's availability, picker
→ new session → a turn that posts only `text` + `request_id`, the carry-over
preview (nothing saved until the click, tab cache dropped), start-without-
saving and leaving Plan, inert restore of a carried-over Control card, the
`interrupted` frame with no connection-lost banner, server exports, rename
by revision + delete, and the switch refused mid-turn.

### See also (assistant control mode)

- [`ASSISTANT_PERSISTENCE.md`](ASSISTANT_PERSISTENCE.md) — the three-mode
  design behind §5.10; its §2 (routine-control exception) is still open.
- §2 — assistant tiering; this section changes tier 2's *tool surface*, not
  its trust level.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) decision #10 (read-only assistant),
  #7 (the control-capable SDK MCP server), #1 (why the passthrough has no
  interlocks).
- [`AUTH_DESIGN.md`](AUTH_DESIGN.md) — `operator`+ and claims for control;
  the assistant chat's own gate.
- [`INTERLOCKS.md`](INTERLOCKS.md) — the layers a single passthrough action
  does not get.

---

## 6. Utils → Computers and Servers, and the browser SSH console [IMPLEMENTED]

**Shipped 2026-08-27.** Two changes that arrived together: the Utils
`Devices` pill split into **Computers and Servers** and **3D Printers**, and
each host tile in the first gained a link to a full page with an SSH banner
and a live terminal on that machine.

### 6.1 Why the pill split

`/utils/devices` had been one page stacking two unrelated inventories: the
host machines (`HostsPanel`) and the Bambu printers (`BambuPrinterPanel`).
They share no audience — you open one to answer "what is running where and
is it healthy", the other to watch a print — and the combined page had no
name that described both. They are now separate routes with their own pills:

| Route | Pill | Content |
|---|---|---|
| `/utils/computers` | Computers and Servers | `HostsPanel` — servers + device PCs, host-ops status pill, SSH link |
| `/utils/printers` | 3D Printers | `BambuPrinterPanel` — Bambu MQTT telemetry |

`/utils/devices` redirects to the hosts half and `/utils/bambu_printer`
(the pre-2026 route, already a stub) now redirects to `/utils/printers`
instead of hopping through `/utils/devices`. `/utils` still redirects to the
section default, which is now Computers and Servers.

### 6.2 The SSH console

Each host tile carries an **SSH terminal ↗** link to
`/utils/computers/ssh/<host-id>`, opened in a new tab: an SSH banner (host
identity, login user, shell, and the exact `ssh …` command an operator would
type themselves) above an xterm.js terminal wired to a real `ssh` process on
the dashboard host.

Three decisions worth recording.

**Human admins only — never a machine principal.** This is the narrowest
gate in the dashboard, and deliberately narrower than "admin":
`web/src/middleware.ts` verifies `/api/ssh/*` and the page route with the
**session cookie only**, never forwarding `X-Api-Key`, so an API-key
principal is refused even if its roster role is `admin`. A shell sits *below*
every safety layer the lab has — claims, `allowed_actions`, the four
interlock layers, the propose-only assistant — so handing one to an agent
principal would quietly undo them all.
[`AGENTIC_LAB_DESIGN.md`](AGENTIC_LAB_DESIGN.md) Part II already keeps the
unattended `lab-runner` profile free of any terminal toolset for exactly this
reason (it ingests Slack), and forbids the host-ops fleet from running
arbitrary shell; this is the web-side half of the same rule. Note the
attended `lab-ops` Hermes profile *does* have a local terminal under its own
OS user (`sdl2`, which holds the lab SSH keys) — nothing here widens that,
and nothing here is needed by it.

**A ticket, not the cookie, authenticates the socket.** The identity check
happens over plain HTTP on `POST /api/ssh/session`, which returns a
single-use ticket that expires in 30 s; the WebSocket presents that. Two
independent reasons, both measured rather than assumed:

- Caddy's `forward_auth` answers a WebSocket upgrade with a bare 403 before
  any cookie check — the same failure that produced the `/xarm5/ws` and
  `/hermes/api/ws` exemptions in `deploy/Caddyfile.single-edge`.
- Next resolves routes for an upgrade with the raw socket standing in for the
  response object, and invoking middleware in that state throws inside the
  server (`Error handling upgrade request TypeError: Cannot read properties
  of undefined (reading 'bind')`, next 14.2.35) and kills the handshake
  *before* the rewrite to FastAPI is reached. So `/api/ssh/ws` is excluded in
  the middleware **`matcher`** — `"/api/ssh/((?!ws$).*)"`, a negative
  lookahead — and not merely by an early return inside the function. Getting
  this wrong makes the terminal fail to connect with no useful error.

**Session profiles (added the same week).** Each host offers a server-defined
list of session types the page renders as a segmented picker next to Connect:
gaia offers *Shell* and *tmux* (attach-or-create the shared `console` session,
so a dropped tab or connection is survivable — reconnect and reattach; a
second admin attaching sees the same screen); the Windows PCs offer *cmd*,
*WSL* (Ubuntu WSL2 instead of cmd.exe, cold-booting the distro on demand) and
*WSL tmux* (the same persistence trick — the detached session keeps the WSL VM
alive). The browser only ever names a profile **id**; the remote command each
id maps to is a fixed tuple in `ssh_console.py`'s whitelist, carried inside
the ticket, so the command surface stays exactly as closed as the host list.
The audit rows record which profile a session used.

**Credentials stay in ssh's own config.** The server-side whitelist
(`api/app/ssh_console.py::SSH_HOSTS`) names an alias from the service user's
`~/.ssh/config` (`cytation-pc`, `uplc-pc`, `gibbie-pc`, `localhost`) — the key file, login
user and hostname live there, not in this app, and the browser never supplies
a host. `BatchMode=yes` means no password prompt can appear in a terminal the
server could never answer, and `StrictHostKeyChecking=yes` means a web
request can never teach the dashboard a new host key. `gaia` loops back over
ssh rather than spawning a local shell, so the operator gets a normal login
shell instead of one inheriting the API service's systemd sandbox
(`ProtectHome=read-only` would be baffling).

Every session writes two `ssh_session` rows to `equipment_events` (ticket
issued, session ended with actor / outcome / duration / exit code), keyed by
the host id rather than an `equipment.yaml` id — a host machine is not
equipment, but "who opened a shell where, when" belongs next to "who moved
the sash".

Kill switches: `SSH_CONSOLE_ENABLED=false` removes the surface entirely (404);
`SSH_CONSOLE_MAX_SESSIONS` (default 4) and `SSH_CONSOLE_IDLE_TIMEOUT_S`
(default 1800) bound what a forgotten tab can hold open.

### 6.3 What this is not

Not a replacement for the host-ops fleet. `sdl-lab-hostops` stays the surface
for routine service status / log tails / whitelisted restarts, because it is
audited, agent-reachable, and cannot do anything else. The SSH console is the
maintenance and diagnosis path a human reaches for when the whitelist does
not cover the question — the same division DEVICE_PC_SETUP §2.4 already draws
for SSH key trust.

### See also (SSH console)

- [`AGENTIC_LAB_DESIGN.md`](AGENTIC_LAB_DESIGN.md) Part II — agent trust tiers
  and why no agent surface gets a shell.
- [`DEVICE_PC_SETUP.md`](DEVICE_PC_SETUP.md) §2.4 — SSH key trust on device PCs.
- [`AUTH_DESIGN.md`](AUTH_DESIGN.md) — the session/roster model this gate reads.
- [`LAB_MONITORING.md`](LAB_MONITORING.md) §4 — the `equipment_events` type registry.

---

## 7. Assistant voice — read-aloud + push-to-talk [IMPLEMENTED]

**Shipped 2026-08-27** (read-aloud and the HTTPS prerequisite the same day,
voice input hours later). The assistant bubble gained a spoken output channel
and a spoken input channel. They are deliberately asymmetric: output is a
browser-local convenience, input is a GPU service — and neither changes one
byte of the chat pipeline. Voice is an **I/O shell around the existing SSE
contract**, not a new backend: the same `/api/assistant/chat`, the same
frames, the same modes.

### 7.1 Why this exists, and the prerequisite it forced

The use case is hands-busy operation: gloved at a fume hood, eyes on an
instrument, asking "is the shaker running" without touching a keyboard.

Browsers gate the microphone (`getUserMedia`) on a **secure context**, which
the canonical `http://100.64.254.6` origin is not — so voice forced the edge
to grow an HTTPS origin first: `https://sdl2-server-gaia.tail6a1dd7.ts.net`,
a real Let's Encrypt cert issued by `tailscale cert` (the tailnet has HTTPS
certificates enabled), renewed by `caddy-tailscale-cert.timer`, serving the
same routes via a shared `(edge_routes)` Caddyfile snippet. The plain-HTTP
origin is unchanged; on it, the mic button simply never renders. Two costs,
accepted knowingly: the hostname is now in public Certificate Transparency
logs, and the HTTPS origin carries its own session cookie (a different
origin is a different sign-in).

### 7.2 Read-aloud (output) — `web/src/lib/speech.ts`

A 🔊 toggle in the panel header. **Off by default** (a dashboard that starts
talking unprompted in a shared lab is a bad neighbour), persisted per person
in `localStorage`.

The voice itself is **Kokoro-82M on the lab GPU** (`POST
/api/assistant/voice/speak`, same identity gate as transcription): the
browser's own `speechSynthesis` was the first implementation and proved
unlistenable — on most lab machines its voices are espeak-era robots — so it
survives only as the fallback when the TTS service is down. Kokoro is ~300 MB
of VRAM beside the ASR model and synthesizes a shaped sentence in **~50–90 ms**
warm; voice selectable via `STT_TTS_VOICE` (default `af_heart`, `""` disables).
The toggle renders when either engine is available.

Speech is a **summary channel, not a transcript reader**. `speakableFromMarkdown`
drops fenced code, tables, and URLs (a spoken stack trace conveys nothing),
bounds the utterance to whole sentences within 180 chars (~12 s — under the
~15 s where a long-standing Chrome bug stalls utterances), appends "More on
screen." when anything was cut, and makes `snake_case` ids pronounceable.
This lands softly because the assistant's system prompt already demands 1–3
sentences with the answer first: the lead sentence *is* the summary.

For latency, the **first completed sentence is spoken mid-stream** rather
than on the `done` frame; the rest of the turn is for the eyes. Escape,
toggling off, closing the panel, or sending a new message all cancel speech.

### 7.3 Push-to-talk (input) — `stt/`, `api/app/voice.py`, `use-voice-input.ts`

One click total: 🎤 starts recording; an `AnalyserNode` watches signal level
and ~0.9 s of trailing quiet ends the clip (second click or a 30 s cap as
backstops). The clip POSTs to `/api/assistant/voice/transcribe`, and the
transcript follows a **per-mode policy** that is the section's one real
safety decision:

| Mode | Transcript handling | Why |
|---|---|---|
| **Ask** | **auto-sends** — no Enter | read-only mode; a mishearing costs one wasted query |
| **Control** | fills the input box only | a turn can end in a proposal card; the operator must see what they asked before it goes |

**Voice is never an authorization channel.** *Authorize* and plan approval
remain clicks on rendered cards (§5.1's argument verbatim: identity says
*who*, never *whether the human meant it* — and ASR mishears). Push-to-talk
only; no wake word, no open mic, ever.

The pipeline: browser `MediaRecorder` clip → dashboard proxy (requires the
middleware-verified `X-Auth-User`; anonymous audio is **refused, not
transcribed**) → loopback STT service on `127.0.0.1:8070`. **No audio leaves
the tailnet**, nothing is persisted, and logs carry who spoke and the latency
— never the text. The mic button renders only when browser *and* service are
capable (`/api/assistant/voice/health`, the same configured-gate pattern that
hides the whole bubble).

### 7.4 The voice service (`stt/` — see its README for ops detail)

**Qwen3-ASR-1.7B** resident on the local GPU, chosen over faster-whisper
(CTranslate2 INT8 is broken on Blackwell/sm_120; Whisper hallucinates on
silence — the worst failure mode for a lab assistant) and over Parakeet
(better leaderboard WER, but its word-boosting decoder is experimental and
can't take out-of-vocabulary terms). The decisive feature is **trained-in
context biasing**: the service builds its vocabulary prompt from
`equipment.yaml` at startup — every device name plus spoken forms of ids
("ot2 hte") — so onboarding a device extends the recognizer the same way it
extends the dashboard.

The same service carries `/speak` (Kokoro TTS, §7.2). One engineering note
there: the ASR and TTS loads run **sequentially in one thread**, deliberately —
both stacks import transformers submodules through its lazy-import machinery,
which is not thread-safe, and two parallel load threads raced it in practice
(kokoro's `from transformers import AlbertModel` failed while the ASR thread
was mid-import; the same import succeeds in isolation).

Engineering notes that took debugging to learn: the processor's float32
audio features must be cast to the model's bfloat16 (`BatchFeature.to(device,
dtype=...)` casts only floating tensors); transformers' audio loader needs
`librosa`; and a **warmup transcription of synthetic silence at load** absorbs
CUDA's ~3 s first-request cost so no operator ever pays it.

Measured on the RTX 5080: **~550–700 ms warm for an 11 s clip** including
ffmpeg decode (~400 ms for a typical utterance), 4.4 GB VRAM. End-to-end,
voice-in → first spoken word ≈ 3–5 s, dominated by the assistant turn itself
(~4 s on the Ask-mode OpenRouter backend) — STT is ~10 % of the budget.

`stt/` is in the monorepo but **not a uv workspace member**: its own venv on
Python 3.12 (the workspace's 3.14 has no GPU-stack wheels), and a GPU model
must never load inside the `api/` process that owns the uptime sweep and
every SSE stream. Deployed as `ac-organic-lab-stt.service`
(`deploy/`), loopback-only; the dashboard proxy is its single network caller.

### 7.5 Tuning and open items

- `SPEECH_RMS` (0.04, `use-voice-input.ts`) is set for a headset mic. A room
  mic beside a running shaker may never read as quiet — the manual stop still
  works; raise the threshold if auto-stop misbehaves. Mic quality dominates
  model choice: budget for a headset/lapel mic before tuning the decoder.
- The bake-off harness never ran: ~20 recorded real utterances, device names
  weighted, would confirm Qwen3-ASR over alternatives with data rather than
  benchmarks. `STT_MODEL` is the seam.
- Kuma's pill is the one remaining absolute-HTTP link (own site block on
  :8005); an HTTPS visitor who clicks it leaves the secure origin.
- Cookies stay non-`Secure` until the HTTPS origin becomes the published
  canonical entrypoint.

### See also (assistant voice)

- §5 — the control-mode proposal flow that voice input must never bypass.
- `stt/README.md` — service ops, env vars, privacy posture.
- [`AUTH_DESIGN.md`](AUTH_DESIGN.md) — the verified `X-Auth-User` the proxy requires.
