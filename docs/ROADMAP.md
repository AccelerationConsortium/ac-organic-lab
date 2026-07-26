# AC Organic Self-Driving Lab — Roadmap

This document is the durable, git-tracked record of where the SDK + dashboard
stack is, what is deferred, and what must be true before the next SDK
milestone (v0.4) starts. The original layered plan lives in
`.cursor/plans/build_ac-organic-lab-skills_5bb34ed0.plan.md`; this doc is
its committed companion so the project state is recoverable without the
Cursor plan UI.

> Trimmed 2026-07-03: the v1.0/v1.1 conformance checklists now live only in
> `STATUS_SPEC.md` §9 (their source of truth), and shipped per-device
> migration histories are compressed to their outcomes — full detail is in
> git history and the device repos. Open work is preserved verbatim.

## Current state (last fleet sweep: 2026-05-30)

| Milestone | Scope | Status |
|-----------|-------|--------|
| **v0.1** | Monorepo + skills SDK foundation + aggregator move | ✅ shipped on `main` |
| **v0.2** | `EquipmentClient.command`, sync wrapper, control exceptions, skill catalog, `LabSession.skills()`, typed per-kind clients | ✅ shipped on `main` |
| **v0.3** | STATUS_SPEC v1.1 spec doc, `ClaimManager`, `Plan` / `validate_plan` / `PlanReport`, `Violation` + `register_interlock`, two built-in interlocks, graceful degradation for v1.0 devices | ✅ shipped on `main` |
| **v0.4 PR-1** | `execute_plan` (sequential live executor, per-step ClaimManager, layer-3 + layer-4 re-check, bounded `wait_timeout_s` for time-clearing preconditions, `PlanRunReport`), async interlocks (`run_interlocks_async`), sync façades (`SyncLabSession.validate_plan` / `execute_plan`), `command(claim_token=...)` | ✅ shipped on branch `feature-xarm-ot2` (2026-07-12) |
| **v0.4 PR-2** | MCP server companion — `lab_skills.mcp` (catalog → tools, `/status` → resources) + `lab-skills mcp serve` CLI; control gated behind `--allow-control` | ✅ shipped on branch `feature-xarm-ot2` (2026-07-12) |
| **v0.4 PR-3** | Live agent acceptance: run a 5-step `Plan` against PlateLoc via `execute_plan` | ◑ executor validated live 2026-07-15; a *successful* seal is blocked by a PlateLoc compressed-air fault (facilities), retry pending |
| **v0.5** | Standalone `lab-skills serve` CLI exposing the aggregator as a long-lived HTTP service | not started |

**Fleet snapshot (live, all on `adapter: http`).** Zero `legacy_http`,
zero `mock` left in `equipment.yaml`. Thirteen devices respond live
from real hardware (`cam_hte_tapo_c245`, both `plug_hte_strip_*`,
`fume_hood_actuator`, `xarm_translocation`, `ot2_hte`, `dose_every_well`,
`torry_pines_shaker`, `filter_every_well`, `plateloc`, `cytation_5`,
`agilent_uplc_ms`, `agilent_biostack`, `pypoe_web`); the four `env_*`
environmental sensors are still synthesised pending the `env_sensors`
repo.

**Web-service tile: `analytica_db`.** AnaliticaDB (the lab's record
store, FastAPI on the data server at `100.64.254.6:8010`) is registered
under **Web Services** (`kind: other`, `adapter: http`, `protocol: "1.0"`,
no `open` pill) and serves a STATUS_SPEC `/status` envelope (`ready`, or
`degraded` if its Postgres is unreachable); the tile reads `ready`. That
repo is being generalized into the lab's ELN+LIMS record layer — see
[`DATABASE_DESIGN.md`](DATABASE_DESIGN.md).

**Protocol mix.** Eight devices reach `protocol_version: "1.1"` on
their live `/status` envelope (`fume_hood_actuator`,
`xarm_translocation`, `ot2_hte`, `torry_pines_shaker`, `filter_every_well`,
`plateloc`, `cytation_5`, `pypoe_web`); the rest are v1.0. The
`sdl-lab-contract` shared-package threshold ("3+ repos cleanly on
v1.1 for ~1 month") is comfortably cleared.

**Skill catalog inventory** (`SKILL_REGISTRY` keys, count of `SkillDef`s
each):

| Kind | n | Notes |
|------|---|-------|
| `fume_hood` | 2 | `sash.move`, `sash.stop` |
| `hplc` | 6 | `run.{submit,abort}`, `queue.cancel`, `instrument.standby`, `workflow.{start,end}` (Agilent UPLC-MS sidecar) |
| `liquid_handler` | 18 | OT-2 full `/control/*` surface: lifecycle (`startup`/`shutdown`), protocol exec (`setup`/`home`/`move_to`/`pick_up_tip`/`aspirate`/`dispense`/`drop_tip`/`move_labware`/`pause`/`resume`), plate + tip tracking (`plate.{load,unload}`/`well.update`/`tips.reset`), convenience (`lights.set`/`deck.declare`). Typed args added 2026-07-12; `move_to` (well or absolute-XYZ pipette motion) added 2026-07-18 |
| `plate_reader` | 11 | Mirrors `agilent-cytation-server` `/control/*` surface |
| `plate_sealer` | 8 | Includes 412-precondition skills with `requires_components` (heater + stage) |
| `plate_stacker` | 6 | Agilent BioStack `/control/*` surface |
| `press` | 6 | `init`, `stop`, `press.{up,down}`, `plate.{in,out}` |
| `robot_arm` | 4 | `graph.{move_to,recover_to,record,mode}` — xArm motion-graph control surface (v1.1, claim-gated); added 2026-05-31 |
| `shaker` | 6 | `startup`, `shutdown`, `shake.{start,stop,set_temperature,set_speed}` with motor/heater AND-gates so a heater-side `degraded` doesn't block shaking |
| `solid_doser` | 13 | `dose.{well,multiple,row,column}` etc.; all endpoints moved under `/control/*` for dose v1.1 (2026-05-31) |

**Cross-repo changes since the last sweep** (outcomes only; detail in the
respective repos):

- `kasa_tapo_services` ships WebRTC opt-in (`bootstrap_go2rtc.py` renders a
  `webrtc:` block) — PTZ video latency drops from ~0.5–1.5 s (MSE) to
  ~100–300 ms.
- Dashboard `CameraPlayer` chooser shipped (`WebRtcPlayer` when the browser
  lacks `MediaSource`, e.g. iPhone Safari; `MsePlayer` otherwise) — fixes
  camera streaming on iPhone.
- Dashboard control path shares one lifespan-owned keep-alive
  `httpx.AsyncClient`; the v1.1 claim → action → release dance reuses one
  socket (per-action overhead ~3 RTTs → ~0).
- OT-2 deck-light toggle shipped end-to-end (`POST /control/lights`,
  `lights.set` SkillDef, dedicated `LiquidHandlerTile`).
- xArm5 tile redesigned as three single-line component rows (Arm / Gripper /
  Track) reading `components.*` + selected metrics.
- `uv run pytest -q` passes end-to-end: `api/tests` 24/24, skill-catalog
  tests 10/10, `kasa_tapo_services/tests` 70/70.

## v0.4 status

v0.3 shipped the *SDK side* of the v1.1 contract. The device bar for the
agent-facing payoff — devices implementing `/control/{claim,heartbeat,release}`
and populating `allowed_actions` — is **comfortably cleared** (eight devices
live on v1.1). The v0.3 SDK carry-overs that blocked the executor are now
**done** (PR-1, branch `feature-xarm-ot2`, 2026-07-12):

- ✅ `execute_plan(plan, session, *, owner, dry_run=False)` — sequential live
  executor: offline `validate_plan` up front, then per step re-checks layer 3
  (live `allowed_actions` via the same `_availability` as `skills()`) + layer 4
  (async interlocks), wraps the step in a per-step `ClaimManager`, POSTs the
  SkillDef endpoint with the claim token, fail-fast with the rest reported
  `skipped`. Returns `PlanRunReport` (with `claims_acquired`). `dry_run=True`
  is a live preflight that skips the claim + command POST. `wait_timeout_s`
  polls the layer-3 re-check so a plan can wait out a **time-clearing
  precondition** — e.g. PlateLoc omitting `seal.start` from `allowed_actions`
  during the heater ramp — instead of blocking (this is what PR-3's PlateLoc
  acceptance needs).
- ✅ **Async interlocks** — `register_interlock` now accepts `async def`;
  `run_interlocks_async` runs sync + async rules before each step;
  `run_interlocks` stays sync/offline for `validate_plan`. Unified on the
  per-step `list[Violation]` form (see `docs/INTERLOCKS.md`).
- ✅ **Sync façades** — `SyncLabSession.validate_plan` / `execute_plan`, plus
  `EquipmentClient.command(claim_token=...)` (the plumbing that lets a claimed
  command pass a hard-enforcing device's `X-Claim-Token` check).

**Deferred from PR-1 (documented, low priority):** a *standalone* sync
`ClaimManager` façade for holding a claim across manual sync calls needs a
background-thread event loop (its heartbeat can't fire between
`run_until_complete` calls); `execute_plan`'s sync façade doesn't need it (the
whole run is one `run_until_complete`). The richer `InterlockResult` /
whole-plan `(plan, lab)` interlock shape was **not** adopted — the shipped
model is per-step returning `list[Violation]`, matching `validate_plan`.

**Remaining for v0.4:** PR-2 (`lab_skills.mcp` + CLI) shipped 2026-07-12 —
tools `list_equipment` / `list_skills` / `get_status` / `validate_plan` /
`preflight_plan` always on; the actuating `execute_plan` tool + resources
(`lab://equipment`, `lab://status/{target}`) gated behind `--allow-control`;
`mcp` is an optional extra so the SDK/aggregator need no MCP deps. Only **PR-3**
(live agent acceptance against real PlateLoc, on the bench) is left.

## Equipment migration plan

Each per-device repo is migrated independently. The dashboard's
`equipment.yaml` flips per-entry as repos ship: `adapter: legacy_http`
→ `http` once `/status` is `EquipmentStatus`-shaped, and `protocol:
"1.0"` → `"1.1"` once the device implements claims.

### Verified state (2026-05-30 liveness sweep)

Probed against `http://127.0.0.1:8001/api/equipment` on the dashboard
host. Spec is what the device's live `/status` envelope reports.

| Device | Adapter | Spec (live) | Live state | Notes |
|---|---|---|---|---|
| `cam_hte_tapo_c245` | `http` | 1.0 | ✅ `ready` | Full PTZ / presets / privacy / streaming / snapshot / recording surface; WebRTC opt-in available. |
| `plug_hte_strip_right` / `_left` | `http` | 1.0 | ✅ `ready` | HS300 power strips; `on`/`off`/`toggle`; per-outlet safety decided client-side (`outletIsSafe()`). |
| `fume_hood_actuator` | `http` | 1.1 | ✅ `ready` | `sash.move`/`sash.stop`; deployed on the Pi at `100.64.254.100:5000`; round-trip verified from `FumeHoodTile`. |
| `xarm_translocation` | `http` | 1.1 | ✅ `requires_init` | Claim protocol + `/control/graph/*` deployed 2026-05-31; claims gated behind `POST /connect`. Open items in the sub-tasks below. |
| `ot2_hte` | `http` | 1.1 | ✅ `ready` | Protocol actions + `lights.set` advertised; deck snapshot pulled over SSH. Protocol-action SkillDefs pending (typed labware args). |
| `dose_every_well` | `http` | 1.1 | ✅ `requires_init` | Full v1.1 verified live 2026-05-31: hard `X-Claim-Token` (423), `/control/*` consolidation (breaking), state-driven `allowed_actions`. |
| `torry_pines_shaker` | `http` | **1.2** | ✅ `ready`/`degraded` | First native v1.2 device (2026-07-25): motor-observed `activity`, `cycles_total`, per-subsystem `allowed_actions`. Poll timeout restored to 10 s (read-off-lock fix deployed). The heater RTD `cal` fault recurs intermittently — now reported honestly as `degraded` without blocking shakes. |
| `filter_every_well` | `http` | 1.1 | ✅ `ready` | v1.1 deployed on the Pi at `100.64.254.104`; PressTile per-direction `hold_time` inputs verified (EQUIP_STATUS.md §8). |
| `plateloc` | `http` | 1.1 | ✅ `ready` | Real claims (TTL `ClaimStore`, commit fa98ca8) verified 2026-05-31; 423 ahead of the 412 seal interlocks. Only a cosmetic version bump remains. |
| `cytation_5` | `http` | 1.1 | ✅ `ready` | 13 actions advertised; device-repo Phases 3+4 shipped; Phase 2 (per-well tracking) remains. |
| `agilent_uplc_ms` | `http` | 1.1 | ✅ `ready` | v1.1 sidecar with hard claims and the `workflow.start`/`end` campaign lock; the sidecar owns the queue. Detail in the sub-task below. |
| `agilent_biostack` | `http` | 1.0 | ✅ `ready` | Driver landed; read-only (`allowed_actions: []`), no `/control/*` yet. |
| `pypoe_web` | `http` | 1.1 | ✅ `ready` | Internal web service; no control surface. |
| `analytica_db` | `http` | 1.0 | ✅ `ready` | Record-layer tile; STATUS_SPEC `/status` envelope live alongside the data API. |
| `env_*` (4 sensors) | `http` (mock) | 1.0 | dry_run | Awaiting the `env_sensors` repo. Not on the v0.4 critical path. |

### Remaining migration work (priority order)

1. **`agilent_biostack` (plate_stacker) `/control/*` surface** —
   driver landed and is on `adapter: http` v1.0 read-only. Until it
   grows control endpoints the xArm is the lab's only plate-mover,
   which makes it both a throughput bottleneck and a single point of
   failure for the solubility workflow.
2. **`xarm_translocation` (operationalising the graph surface)** — the
   device now exposes claim + `/control/graph/*` and the catalog has the
   matching SkillDefs, but: (a) control is gated behind `POST /connect`;
   (b) the dashboard tile shows no graph controls yet; and (c) the
   `/web/` side-door must become claim-aware or be fronted (see
   *Control-surface exposure*).
3. **`agilent-cytation-server` Phase 2** — per-well sample tracking
   under `details.loaded_plate`. Phases 3 and 4 (v1.1 +
   `/control/*`, skill catalog) already shipped.

### Conformance checklists

The v1.0 and v1.1 per-device conformance checklists live in
[`docs/STATUS_SPEC.md` §9](STATUS_SPEC.md) — that section is their source
of truth. A repo flips `equipment.yaml` to `adapter: http` when v1.0
conformant and to `protocol: "1.1"` when the claim protocol +
`allowed_actions` are implemented.

### Per-device sub-tasks (open work only)

Shipped migrations are summarized in the fleet table above; what follows
is the remaining work per device.

#### `agilent-plateloc-server`

- [x] `last_error.code` taxonomy extended (v1.3.2, PR
  [#1](https://github.com/cyrilcaoyang/agilent-plateloc-server/pull/1)):
  `no_plate` + `vacuum_error` added and classified in `_classify_error`.
  Both surfaced live on the bench 2026-07-15 (previously fell through to
  `com_other`); deployed to the device and verified live. `pyproject` /
  CHANGELOG bumped 1.3.1 → 1.3.2.
- [ ] Cosmetic: `equipment_version` on `/status` is still `null` (config
  unset); populate it to match the shipped state.

#### `dose_every_well`

- [ ] Cosmetic: `pyproject` version still `0.8.0` (`__version__` is
  `0.9.0`); `fastapi`/`uvicorn` not yet declared as deps (present on the
  Pi). Shipped state: branch `develop-modular`, commit `252d04e`.

#### `fume_hood_actuator`

- [ ] The sensor service in `src/hood_sash_automation/sensor/` is still
  Flask on its pre-spec shape; it migrates separately. (The actuator
  service is fully migrated and verified; `LegacyFumeHoodActuatorAdapter`
  stays importable from `.legacy` for one release cycle as rollback.)

#### `xarm_translocation`

Shipped 2026-07-03 (device repo + this monorepo):

- **Events exporter** (plan Step 3c): `src/core/events_exporter.py`
  pushes fine-grained `state_transition` / `error` / `startup` /
  `shutdown` rows from the SDK callbacks to `POST /api/ingest/events`
  (best-effort, stdlib-only, disabled unless `XARM_INGEST_URL` is set).
  Conventions documented in LAB_MONITORING.md §4 event_type registry.
  **Deploy step pending:** set `XARM_INGEST_URL` in the `xarm` NSSM
  service env on the device PC, pull + restart.
- **`equipment_version`** populated on `/status` (was null).
- **Registry flipped to `protocol: "1.1"`** — the dashboard passthrough
  now runs the per-request claim dance the device's hard enforcement
  requires (tokenless → 423 verified live 2026-07-03, arm connected).
  `do_not_call_connect` stays: claims are deliberately gated behind
  `POST /connect` and the SDK must never auto-connect a robot arm.
- Claim-gating turned out broader than recorded: the legacy `/move/*`,
  `/gripper/*`, `/track/*`, `/robot/*`, `/velocity/*` surfaces all carry
  `require_claim` (423 even when *no* claim is held), so the `/web/`
  panel moves through the same gate. Exempt safety floor: `/move/stop`,
  `/clear/errors`, `/connect`, `/disconnect`.

Open:

- [ ] **Full claim lifecycle verification** with the arm connected:
  claim → move → heartbeat → release, `details.claimed_by` populates.
  (Tokenless-423 half already verified live.)
- [ ] **Skill-name reconciliation**: the device advertises
  `allowed_actions` as `connect`/`stop`/`clear_errors`/`move.<node_id>`
  (graph-derived, STRICT mode only) while the catalog registers
  `graph.{move_to,recover_to,record,mode}` — so `lab.skills()` reports
  every `graph.*` skill unavailable. Rename one side or map in the SDK.
- [ ] **Dashboard graph controls**: `RobotArmTile` still shows only the
  read-only three-row summary + deep-link; surface the `graph.*` actions
  through the audited passthrough.
- [ ] Gripper stroke: the device now publishes
  `details.gripper.position_mm` (cached read-back) rather than
  `metrics.gripper_position`; reconcile with the tile's expectation.
- [ ] Run the device repo's Phase 6 hardware verification checklist
  (`src/docs/PHASE6_HARDWARE_VERIFICATION.md`) — needs a human at the
  machine.

#### `agilent_uplc_ms` (sidecar: `agilent-hplcms-server`, branch `feature-agent-control`)

Shipped: STATUS_SPEC v1.1 with hard `X-Claim-Token` enforcement; `hplc`
catalog (`run.submit`, `run.abort`, `queue.cancel`, `instrument.standby`,
`workflow.start`/`workflow.end`). Semantics worth keeping in mind:

- The sidecar **owns the queue**; OpenLab is reserved for technician
  servicing. Enqueue verbs drop from `allowed_actions` on queue-full
  (412 + `Retry-After`), OpenLab-down (409 `requires_init`), or
  servicing (409 `instrument_servicing`); one shared helper keeps
  `allowed_actions` and refusals in agreement (§6.2).
- `workflow.start`/`end` is the equipment-blocking lock for an
  automation-role campaign (non-holder submits → 423 `workflow_active`).
  Operator service-mode toggles are technician controls, not agent skills.
- `instrument.standby` is a low-flow park; a true power-down stays a
  manual operator procedure, deliberately not an API action.
- OLSS "Paused" maps to `busy` + `required_actions:
  ["resume_paused_sequence"]`; the raw OLSS status survives in `details`.
- [ ] Watch item: dashboard polling latency ~1.5 s against a 5 s
  `poll_timeout_seconds` (OpenLab WMI introspection is the cost). Raise
  to 8 s if it ever errors.

#### `ot2_hte`

- [x] SkillDefs + typed args for the protocol-execution actions
  (`setup`, `home`, `aspirate`, `dispense`, `pick_up_tip`, `drop_tip`,
  `move_labware`) — shipped 2026-07-12 in `skill_catalog/liquid_handler.py`
  with typed `args_schema`s (labware/instrument/module specs, well
  locations, per-call `flow_rate`). Also cataloged the lifecycle
  (`startup`/`shutdown`/`pause`/`resume`) and plate-tracking
  (`plate.{load,unload}`/`well.update`) verbs, so `lab.skills()` now
  mirrors the gateway's full `allowed_actions` surface (16 SkillDefs). A
  parity test pins the names to the gateway's advertised strings.
- [ ] Follow-ups now unblocked: `execute_plan` can drive a typed OT-2
  `Plan` (v0.4 PR-1); the setup sub-models could tighten the
  `ot_default`-conditional required fields (`loadname` / `config`) with
  validators once a real recipe exercises custom labware.

#### `cytation_5`

- [ ] Device-repo Phase 2: per-well sample tracking
  (`details.loaded_plate` from `Container`/`Plate`/`Well` +
  orchestrator-assigned `sample_id`).

#### `torry_pines_shaker` (device repo: `torry-pines-shaker-server`)

Both findings from the 2026-07-25 live exercise were **fixed and deployed
2026-07-25** (device repo PR #1, v0.2.0 — the fleet's first native v1.2
device, and the reference implementation):

- [x] **§6.2 violation** — one pure availability function now feeds both
  `/status.allowed_actions` and new 412 gates, split per subsystem: a
  heater fault withholds `shake.set_temperature` (and
  `wait_for_temperature` starts) but not shaking; a motor readback
  failure withholds `shake.start`/`shake.set_speed`.
- [x] **v1.2 migration** — health and activity computed independently
  (`degraded` + `activity: "running"` mid-fault-cycle; `busy` ≡ healthy +
  running), `activity_since`, `metrics["cycles_total"]`, contract types
  from `sdl-lab-contract`. The dashboard's reader-side motor sniff was
  deleted the same day (§2.3.2's deletability promise, kept), and the
  poll timeout restored to 10 s (the read-off-lock fix deployed with
  v0.2.0 — the poll-contention watch item below is cleared).

#### Remaining mock-only entries

The four `env_*` environmental sensors stay synthesised pending the
`env_sensors` repo; intentionally not on this round's critical path.

## Operational regressions

**All 2026-05-09 regressions are cleared** as of the 2026-05-30 sweep
(camera gateway down; `dose_every_well` placeholder hostname; `plateloc`
COM driver failure — details in git history).

Active watch items (not regressions; behavioural notes):

- **`agilent_uplc_ms` poll latency** — ~1.5 s against a 5 s
  `poll_timeout_seconds`. Raise to 8 s if it ever errors.
- ~~**`torry_pines_shaker` poll contention**~~ — cleared 2026-07-25:
  the device-repo read-off-lock + short-TTL readings-cache fix deployed
  with v0.2.0; `poll_timeout_seconds` restored to 10 s.
- **`plateloc` compressed-air supply** — during the 2026-07-15 PR-3
  bench run the seal cycle failed on Low Air Pressure / vacuum faults
  (`stage.out` was also refused with "Low Air Pressure Error"),
  leaving a plate trapped in a hot chamber with no software recovery
  path (both actuation and retract are pneumatic). Root cause is
  facilities (shop air off / below regulator setpoint), not the SDK or
  driver. Restore air and confirm `stage.out` succeeds before
  re-attempting PR-3. Follow-up worth considering: a device-side
  low-air interlock that refuses `seal.start` / `stage.in` up front,
  rather than discovering it mid-cycle.

## Control-surface exposure (known security / safety risk)

**Recorded 2026-05-31.** The design analysis and the closure plan are
owned by [`AUTH_DESIGN.md`](AUTH_DESIGN.md); this section keeps only the
operational snapshot and the decisions made.

**The exposure.** Every spec device's `/control/*` surface is reachable
directly on the Tailnet; the dashboard is only one client of it. Direct
`curl` bypasses the dashboard's gate and its audit trail (the
`equipment_events` audit added 2026-05-31 covers dashboard-mediated
control only). This is the documented v1 stance (STATUS_SPEC §11); auth
was deliberately pushed to the network layer, and **claims are a
concurrency guard, not a security guard** (STATUS_SPEC §5 — cooperative,
not authenticated).

**Per-device exposure** (during a workflow: "can someone move it
out-of-band, un-audited?"):

| Device(s) | Tailnet-direct? | Claims enforced? | Out-of-band move risk |
|---|---|---|---|
| cameras, plugs | **No** — gateway is loopback (`127.0.0.1:8002`) | n/a | **Low** — dashboard is the only network path |
| press, fume hood, ot2_hte, cytation | Yes | **Yes** (X-Claim-Token → 423) | Rejected *if* a workflow holds the claim; cooperative + un-audited |
| `plateloc`, `dose_every_well` | Yes | **Yes** (hard-enforced since 2026-05-31) | Rejected if a claim is held; cooperative + un-audited via direct `curl` |
| `xarm_translocation` | Yes | **Yes** on `/control/*`; native `/web/` claim-awareness **unverified** | The `/web/` side-door is still advertised by the tile deep-link — the single most exposed control path in the lab |
| `agilent_uplc_ms` | Yes | **Yes** (X-Claim-Token → 423) | Rejected if a claim is held. A run can still start out-of-band in OpenLab CDS on the instrument PC — surfaced as `busy`, not preventable. |
| biostack, pypoe | Yes | read-only, no control surface | n/a |

**What closes it, in order:**

1. ~~Finish claims where stubbed/absent~~ — ✅ done (2026-05-31);
   remaining only on any future control surface that predates claims.
2. **Front device control surfaces behind the auth/audit edge**
   (AUTH_DESIGN) — note Caddy `forward_auth` covers only the dashboard's
   passthrough routes; closing the direct-device hole needs devices
   behind that edge or loopback-bound + reverse-proxied like the
   camera/plug gateway. The single edge is also the *only* way to get
   one sign-in across UIs: a session cookie can't be shared across
   per-host device UIs (raw `100.x` IPs can't carry a `Domain` cookie;
   `*.ts.net` is on the Public Suffix List so browsers drop tailnet-wide
   cookies — confirmed live 2026-07-06 on the xArm). One origin behind
   the edge ⇒ one shared cookie ⇒ SSO. See AUTH_DESIGN → *Why sessions
   can't be shared per-host*.
3. **xArm `/web/` panel** — replace the deep-link with audited,
   claim-gated dashboard controls and/or front the native panel at the
   edge.
4. **Operational / physical** — for the genuinely un-closeable cases
   (Tailnet + shell access), discipline and the hardware e-stop remain
   the backstop, per INTERLOCKS ("not a real-time safety system").

**Design decision: the claim *is* the mode — do not build per-device
AUTOMATED/MANUAL switches.** "Manual mode" = a human holds the claim;
"automated mode" = a workflow holds it — one mechanism, already exclusive
and audited. A separate mode state machine is only warranted where a
device has a *second* control surface to coordinate (today: the xArm
`/web/`), and the right fix there is making that surface honor the claim.
The enforcement target is one invariant, not a per-device feature:
*every path to the hardware goes through a hard-enforced claim.*

## Resumption criteria for v0.4

The MCP milestone resumes when **all** of the following are true:

1. **At least three devices in `equipment.yaml` have `adapter: http`.**
   ✅ **comfortably met** — all 17 entries are on `adapter: http`;
   thirteen respond live.
2. **`agilent-plateloc-server` is at `protocol: "1.1"`.** ✅ **met** —
   plus seven other devices also reach v1.1.
3. **`lab.skills()` returns a non-empty catalog with `available=True`
   entries against at least one v1.1 device.** ✅ **met** — every
   v1.1 device reports non-empty `allowed_actions` live, and the
   SkillDef registry spans 10 kinds, all non-empty (80 SkillDefs total).
4. **A workflow can run a five-step `Plan` against `agilent-plateloc-server`
   (dry-run is fine) using `validate_plan` + an executor.**
   ✅ **met (code)** — `execute_plan` shipped in PR-1 with offline + `dry_run`
   coverage (respx-mocked v1.1 device, sequential claim/command/blocking/skip
   paths). The five-step-against-real-PlateLoc *acceptance* (PR-3) was
   **exercised live 2026-07-15**: `execute_plan` drove real PlateLoc
   end-to-end — per-step `ClaimManager`, `wait_timeout_s` waiting out the
   heater ramp, `seal.start` through hard-enforced claims, and faithful
   `last_error` surfacing of device faults (500 → `equipment_status: error`,
   fail-fast with the rest `skipped`). A *successful* seal did **not**
   complete: the cycle failed on a PlateLoc compressed-air / vacuum fault
   (facilities, not the SDK — see watch items). A green run awaits air-supply
   restoration and a bench retry with the corrected workflow (heat/cool happen
   **outside** the per-plate cycle; the plate is in only for the seal itself).

All four criteria are green, and PR-1 + PR-2 have landed. PR-3 is the remaining
gate: its `execute_plan` path is now validated live (above); only a
successful seal is outstanding, blocked on the PlateLoc air supply.

## Out of scope for this whole roadmap

- **Workflow runner** (Prefect / Temporal) — lives in project repos or
  a future `ac-organic-lab-runner`.
- **LLM / agent code** — future `ac-organic-lab-agents` repo.
- **Run records / manifests** — project repos own these (see the
  project-repo blueprint in `DATABASE_DESIGN.md`).
- ~~**`sdl-lab-contract` shared package** — wait until 3+ device repos
  ship on v1.1 cleanly (per `docs/STATUS_SPEC.md`).~~ Threshold cleared
  (12 devices on v1.1); extraction started 2026-07-25 with the v1.2
  types, named `sdl-lab-contract`. First consumer is `lab-skills`
  itself; device repos swap their vendored `models.py` for the import
  as part of their v1.2 migration (see the STATUS_SPEC Appendix B gate).
- **Maintenance-tile UI rendering** — tracked separately; not blocking.

## See also

- [`docs/STATUS_SPEC.md`](STATUS_SPEC.md) — the authoritative device
  contract, incl. claims, `allowed_actions`, and the conformance
  checklists (§9).
- [`docs/SKILLS_CATALOG.md`](SKILLS_CATALOG.md) — what `Skill.name` /
  `allowed_actions` refer to.
- [`docs/INTERLOCKS.md`](INTERLOCKS.md) — four-layer safety model;
  layer 4 shipped in v0.3.
- [`docs/AUTH_DESIGN.md`](AUTH_DESIGN.md) — identity, authorization,
  and the control-surface closure plan.
- [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) — system layering + repo
  boundaries.
- [`docs/DEVICE_PC_SETUP.md`](DEVICE_PC_SETUP.md) — canonical install
  recipe for a Windows device PC (uv + NSSM).
- [`docs/LAB_MONITORING.md`](LAB_MONITORING.md) — logging tiers, central
  history DB schema, alerting.
- [`docs/EQUIP_GUIDE.md`](EQUIP_GUIDE.md) — onboarding / maintenance
  guideline; [`docs/EQUIP_STATUS.md`](EQUIP_STATUS.md) — current per-device
  tile implementations.
- `.cursor/plans/build_ac-organic-lab-skills_5bb34ed0.plan.md` — original
  layered plan; this file is its committed counterpart.
