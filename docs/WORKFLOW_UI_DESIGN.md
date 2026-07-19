# Workflow UI — design note

> **Status:** design note (2026-07-16). Not part of the lab contract. Proposes a
> browser surface for *carrying out* workflows (author → validate → approve →
> execute → monitor → record) built on the existing dashboard and SDK rather
> than as a new app. Companion to
> [`AGENTIC_ELN_ASSESSMENT.md`](AGENTIC_ELN_ASSESSMENT.md) (the why) — this is
> the how for the *execute + monitor* surface. Nothing here is built yet;
> maturity claims about the SDK trace to [`ROADMAP.md`](ROADMAP.md) /
> [`INTERLOCKS.md`](INTERLOCKS.md) and should be checked against current code.

## 1. Thesis: extend the seam, don't build a third app

The lab already runs two browser UIs. A "workflow UI" is the **seam** between
them, not a new surface. Split the workflow lifecycle along the grain that
already exists:

| Surface | Stack | Owns | Workflow role |
|---|---|---|---|
| **Dashboard** (`web/` + `api/`) | Next.js 14 + TanStack Query; `ac_auth` login; audited control passthrough | Real-time monitoring + operator control | **operate / execute / monitor** |
| **GraphChat** (LaAgenteAnalitica) | React 19 + Vite + Yjs; workspace + approval UI | Agent chat, file workspace, analysis | **design / author / analyze** |

This note specifies the dashboard side (execute + monitor + approve). Plan
*authoring* and result *analysis* stay in GraphChat, which should follow its own
"workspace-native" direction; the two link by `plan_id` / `session_id`.

**Non-negotiables** (these are already the architecture — do not regress):

- **No control logic in the browser.** The device is the authority
  ([ARCHITECTURE](ARCHITECTURE.md) decision #2); the UI sends intent and renders
  the device's refusals (412 precondition / 423 claim conflict). Preconditions
  and interlocks live in `lab-skills`, never in React.
- **Render forms from the schema.** Drive plan authoring off
  `schema/protocol.schema.json`, so new step types need no frontend change — the
  "fields are for machines" rule from `organic-hte-template`.
- **Every run links to its record.** Deep-link each run to its AnaliticaDB
  `Plan` / `Note` / `Analysis` rows so "what ran / what was observed" is one hop
  from "what I clicked".
- **Auth + audit are not optional.** Run/approve endpoints sit behind `ac_auth`;
  every approval and every executed step writes an audit row
  ([OBSERVABILITY](OBSERVABILITY.md) `event_type: control_action`).

## 2. Architecture

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

## 3. Endpoint contract

### `POST /api/workflow/plans/{plan_id}/preflight`

Runs `validate_plan` (offline, layer 3 + layer 4 sync rules) and optionally
`execute_plan(dry_run=True)` (live preflight: layer-3 re-check + interlocks, no
claim, no command POST). Returns the `PlanReport` shape from
[INTERLOCKS](INTERLOCKS.md): `{ ok, violations[], warnings[],
estimated_duration_s, devices_required[] }`. The UI uses this to enable/disable
the Approve button and to show violations *before* anyone commits.

### `POST /api/workflow/plans/{plan_id}/approve`

The human-approval chokepoint. Body echoes the **fully-resolved** plan the
operator saw (rendered steps + parameters). Server:

1. Verifies the `ac_auth` principal (unique user; not the shared owner).
2. Stores the *structured* approved payload (never a re-parse of free text).
3. Transitions the AnaliticaDB `Plan` `draft → approved`, stamping the principal
   and, for protocol-authored plans, the git `source_commit`.
4. Writes an audit row (`who / plan / outcome`).

Protocol-authored plans that were signed off by a `main` merge skip the card and
are stamped from the merge commit — the two sign-off authorities stay distinct
(see [AGENTIC_ELN_ASSESSMENT](AGENTIC_ELN_ASSESSMENT.md) §5).

### `POST /api/workflow/plans/{plan_id}/run`

Requires an `approved` plan. Starts `execute_plan` in a background task with
`owner = "dashboard:<user>"` and a caller-supplied `wait_timeout_s` (so a plan
can wait out a time-clearing precondition, e.g. a heater ramp). Returns
`{ run_id }` immediately; progress arrives on the SSE stream.

### `GET /api/workflow/runs/{run_id}/events` (SSE)

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

## 4. Run view (component sketch)

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

## 5. Plan authoring (kept minimal here)

Authoring lives primarily in GraphChat, but the dashboard needs at least a
**read + parameterize + preflight** view so an operator can run an existing
protocol without the agent. Render the form from `schema/protocol.schema.json`
(JSON-Schema-to-form), or offer a validated YAML editor that POSTs to
`preflight`. Run-specific values (plate id, operator, the day's materials) are
Plan parameters, never edits to the protocol file — same rule as
`organic-hte-template`.

## 6. What to avoid

- **A standalone Streamlit/Gradio workflow app.** It bypasses `ac_auth`, the
  audit trail, and the claim fabric, and becomes a fourth un-governed control
  path — exactly the *control-surface exposure* risk in [ROADMAP](ROADMAP.md).
- **`validate_plan` logic in TypeScript.** It belongs in the SDK, called over
  HTTP. The browser renders results; it does not decide safety.
- **Folding workflow execution into `control.py`.** The thin operator
  passthrough and the supervised workflow runner are different writer classes
  with different claim lifetimes; keep them in separate modules.

## 7. Build order

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

## See also

- [`AGENTIC_ELN_ASSESSMENT.md`](AGENTIC_ELN_ASSESSMENT.md) — why this surface exists and the design→execute→record loop.
- [`INTERLOCKS.md`](INTERLOCKS.md) — `validate_plan` / `execute_plan`, `PlanReport` / `PlanRunReport`, the four layers.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — writer classes (decision #1), device authority (decision #2).
- [`OBSERVABILITY.md`](OBSERVABILITY.md) — the `control_action` audit row shape.
- [`AUTH_DESIGN.md`](AUTH_DESIGN.md) — `ac_auth`, control-route enforcement, the identity a run/approval is stamped with.
