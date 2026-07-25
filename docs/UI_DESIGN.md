# Dashboard UI — Design & Decisions

**Status:** living document. Created 2026-07-22 by folding in the former
`OT2_INTERFACE.md` (§1, unchanged in substance); §2 (embedded-assistant
tiering) recorded the same day; the former `WORKFLOW_UI_DESIGN.md` design
note folded in as §3 (still [PROPOSED], nothing built).
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

**Shipped** 2026-07-15 (branch `feature-ot2-interface`).

The dashboard hosts a dedicated, server-side (Linux central server) full-page
interface for each Opentrons OT-2, alongside the existing compact
`LiquidHandlerTile` on the platform pages. The Windows gateways
(`opentrons-server`, ports 8020/8021) are **unchanged** — they remain the
single authority for deck, plate and tip state; this interface is a pure
consumer of the central equipment API.

### 1.1 Routes

| Route | What it is |
|---|---|
| `/equipment/[equipmentId]/control` | Generic full-page equipment control view. Renders the OT-2 interface for `kind: liquid_handler`; other kinds get their status header and a notice. |
| `/ot2_hte` | Fixed-id alias of `/equipment/ot2_hte/control` (HTE bench OT-2). |
| `/ot2_complexation` | Fixed-id alias of `/equipment/ot2_complexation/control` (complexation bench OT-2). |

Adding a third OT-2 needs no new code: register it in `equipment.yaml` and use
`/equipment/<id>/control` (add an alias page only if a short URL is wanted).

### 1.2 Data flow (unchanged invariants)

- **Reads** — the page polls `GET /api/equipment/{id}/status` (2.5 s React
  Query interval), the same central aggregator endpoint the tiles use. It
  renders `details.snapshot.deck` (normalized deck), `details.robot`
  (probe + live module telemetry), `details.tip_racks` / `details.mounted_tips`
  (gateway tip tracking), `details.claimed_by`, `components.*` (pipettes,
  SSH, protocol) and `last_error`. Nothing is read from the gateway directly,
  and no deck state is duplicated on the server.
- **Writes** — only `POST/DELETE /api/equipment/{id}/control/deck/declare`,
  through the existing audited control passthrough (`api/app/control.py`):
  middleware session check (`ac_auth`) → per-request claim → device →
  release → `control_action` audit row. The browser never calls raw gateway
  `/control/*` endpoints. Hardware execution (setup/home/aspirate/…) stays
  behind `lab-skills` validated plans and interlocks — the page does not
  expose those verbs.
- **Authorization** — same `useControlLock(equipmentId)` gate as the tiles:
  signed out ⇒ picker disabled with a "sign in" hint; signed in without a
  role on the device ⇒ "no access". The middleware enforces the same answer
  server-side; the client gate is UX only.

### 1.3 Declaration vs physical setup (important)

The deck editor is labelled **"Declare deck intent"** deliberately:

- **Declaring** records *operator intent* in the gateway's persistent
  declaration store (`POST /control/deck/declare`). It is pure metadata — it
  does **not** load labware into an Opentrons protocol context, move
  hardware, or run `/control/setup`.
- The gateway merges declarations with what it *observes* (run/REPL deck)
  and flags disagreements per slot as `slot_state: "mismatch"` — the page
  renders declared and observed separately and badges mismatches (≠).
- **Physical setup** (actually loading labware/instruments on the robot) is
  `/control/setup` driven by a validated `lab-skills` plan — out of scope for
  this interface by design (constraint: the UI must not pretend declaration
  loads labware).

### 1.4 Custom labware (builder + central store)

Three tiers of custom-labware support, added 2026-07-16:

1. **Free-text declare** — the control page's picker accepts any exact
   Opentrons `load_name` (must match `^[a-z0-9._]+$` and contain `_`,
   otherwise the gateway would parse it as a legacy kind string). Unknown
   names round-trip verbatim.
2. **Labware builder** (`/utils/labware_builder`) — a parametric form (grid, footprint,
   offsets, spacing, well geometry) that generates a complete Opentrons
   **schema-2** definition JSON with a live to-scale preview. Validation
   ports `opentrons-server`'s `LabwareGenerator` limits (footprint
   127 × 85.5 mm, height 200 mm, wells inside the footprint). Anyone can
   build + **download** the JSON; building never touches a robot.
3. **Central definition store** (`/api/labware`, `api/app/labware.py`) —
   two merged sources:
   - **(a) repo-committed**: `<repo>/labware/*.json`, PR-reviewed (see
     `labware/README.md`); wins on name collisions and is immutable via
     the API.
   - **(b) admin-uploaded**: `<data-dir>/labware/*.json`, written by
     `POST /api/labware` (session verified at the middleware, **admin role
     enforced server-side**; uploads validated with the same rules; every
     write audited as a `control_action` on the `labware_store`
     pseudo-device). `DELETE` removes uploaded definitions only.

   Store definitions appear in the deck picker's **"Custom (lab store)"**
   group, and workflows can fetch the full JSON (`GET /api/labware/{name}`)
   to pass as the labware `config` in a lab-skills `setup` plan
   (`protocol.load_labware_from_definition` on the gateway).

Env overrides: `LABWARE_REPO_DIR`, `LABWARE_UPLOAD_DIR` (defaults:
`<repo>/labware`, `<lab.db dir>/labware`).

The API additionally serves the **official Opentrons library** (the
`opentrons-shared-data` package, ~141 definitions, latest schema-2 version
each) read-only at `GET /api/labware/standard` (+ `/{load_name}`); the
builder lists it (searchable) and can load any entry's exact geometry for
modification. Uploads that would shadow a standard load name are refused
(409) — a custom variant needs its own name.

### 1.5 The catalog (`web/src/lib/ot2-catalog.ts`)

Because the gateway is unchanged, the *choices* offered in the pickers are
authored centrally in the dashboard, separate from runtime state. Entries
carry a stable key, display label, category, the exact declare string, grid
dimensions and optional compatibility notes. Three declaration flavours:

- **Exact Opentrons load_names** (preferred) — e.g.
  `corning_96_wellplate_360ul_flat`, `agilent_1_reservoir_290ml`,
  `opentrons_96_tiprack_300ul`. The gateway parses any string containing
  `_` as a load_name and derives kind/grid from it.
- **Module keys** — `temperature_module`, `magnetic_module`,
  `heater_shaker_module`, `thermocycler_module` (the gateway's
  `deck.py _MODULE_KINDS`). Declared modules are sticky fixtures.
- **Legacy generic kinds** — `96-well`, `waste`, … kept so pre-catalog
  declarations keep round-tripping and coarse intent stays expressible.

Custom (MatterLab) labware definitions and `/control/setup` execution are
explicitly out of scope for this phase.

#### Round-trip rule (the bug this fixes)

`POST /control/deck/declare` is a **full-layout replace**, so every edit
re-sends all currently-declared slots. The shared helper
(`declaredMapFromDeck` in `web/src/lib/ot2-deck.ts`) re-sends each declared
slot as its **exact `load_name`** when the gateway reported one (falling back
to `kind` only for legacy declarations, and to the module key for declared
modules). The previous tile round-tripped by `kind` only, which would have
silently degraded an exact load_name declaration on the next unrelated edit.

### 1.6 Component layout

- `web/src/lib/ot2-deck.ts` — pure /status parsing + declaration logic
  (unit-tested, no React).
- `web/src/lib/ot2-catalog.ts` — the authored catalog + search/grouping.
- `web/src/components/DeckPanel.tsx` — the reusable 12-slot deck
  (`variant="tile"` in `LiquidHandlerTile`, `variant="page"` on the full
  page); module telemetry readouts incl. the temperature-module overhang
  cell.
- `web/src/components/Ot2ControlPanel.tsx` — the full page: header strip,
  claim banner, mismatch banner, deck + slot detail + searchable
  "Declare deck intent" picker, robot/pipettes/modules/tip-racks/
  mounted-tips/claim sections, footer with message + staleness.
- The compact tile is now a **read-only summary** (deck mirror, light /
  pipette / SSH / protocol pills) with a prominent "Control interface →"
  link. All controls — session lifecycle (connect/disconnect, pause),
  lights, and deck declaration — live on this page only.

Tests: `web/src/lib/ot2-deck.test.ts`, `web/src/lib/ot2-catalog.test.ts`
(pure logic, node env) and `web/src/components/DeckPanel.test.tsx`,
`web/src/components/DeclarePicker.test.tsx` (jsdom component tests — slot
selection, exact load-name declaration, mismatch rendering, auth-disabled
controls).

### See also (OT-2 interface)

- [`EQUIP_STATUS.md`](EQUIP_STATUS.md) §11 — the compact tile's behaviour.
- `opentrons-server` `docs/DECK_STATE_PLAN.md` — the normalized deck shape;
  its `feature/deck-viewer` spec informed this page's rendering conventions.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) decision #1 — why writes go through
  the audited passthrough and the device stays the single authority.

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
| **2. Dashboard assistant** | Chat bubble, all dashboard pages | Read-only: seven `lab-history` MCP tools (history DB, live `/api/equipment`, whitelisted journald) | Mid (sonnet-class) | Central dashboard host — `api/app/assistant.py` spawns a `claude` CLI subprocess per turn; the MCP server is a local stdio child | Anthropic cloud via the host's Claude Code OAuth |
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
> the how for the *execute + monitor* surface. Nothing here is built yet;
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
