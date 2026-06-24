# AC Organic Self-Driving Lab — Roadmap

This document is the durable, git-tracked record of where the SDK + dashboard
stack is, what is deferred, and what must be true before the next SDK
milestone (v0.4) starts. The original layered plan lives in
`.cursor/plans/build_ac-organic-lab-skills_5bb34ed0.plan.md`; this doc is
its committed companion so the project state is recoverable without the
Cursor plan UI.

## Current state (last fleet sweep: 2026-05-30)

| Milestone | Scope | Status |
|-----------|-------|--------|
| **v0.1** | Monorepo + skills SDK foundation + aggregator move | ✅ shipped on `main` |
| **v0.2** | `EquipmentClient.command`, sync wrapper, control exceptions, skill catalog, `LabSession.skills()`, typed per-kind clients | ✅ shipped on `main` |
| **v0.3** | STATUS_SPEC v1.1 spec doc, `ClaimManager`, `Plan` / `validate_plan` / `PlanReport`, `Violation` + `register_interlock`, two built-in interlocks, graceful degradation for v1.0 devices | ✅ shipped on `main` |
| **v0.4** | MCP server companion (catalog → tools, `/status` → resources, CLI `lab-skills mcp serve`) | ⏸ paused on the v0.3 SDK carry-overs; fleet readiness comfortably cleared |
| **v0.5** | Standalone `lab-skills serve` CLI exposing the aggregator as a long-lived HTTP service | not started |

**Fleet snapshot (live, all on `adapter: http`).** Zero `legacy_http`,
zero `mock` left in `equipment.yaml`. Thirteen devices respond live
from real hardware (`cam_hte_tapo_c245`, both `plug_hte_strip_*`,
`fume_hood_actuator`, `xarm_translocation`, `ot2`, `dose_every_well`,
`torry_pines_shaker`, `filter_every_well`, `plateloc`, `cytation_5`,
`agilent_uplc_ms`, `agilent_biostack`, `pypoe_web`); the four `env_*`
environmental sensors are still synthesised pending the `env_sensors`
repo.

**Protocol mix.** Eight devices reach `protocol_version: "1.1"` on
their live `/status` envelope (`fume_hood_actuator`,
`xarm_translocation`, `ot2`, `torry_pines_shaker`, `filter_every_well`,
`plateloc`, `cytation_5`, `pypoe_web`); the rest are v1.0. The
`lab-status-contract` shared-package threshold ("3+ repos cleanly on
v1.1 for ~1 month") is comfortably cleared.

**Skill catalog inventory** (`SKILL_REGISTRY` keys, count of `SkillDef`s
each):

| Kind | n | Notes |
|------|---|-------|
| `fume_hood` | 2 | `sash.move`, `sash.stop` |
| `liquid_handler` | 1 | `lights.set` (OT-2 deck-light convenience control) |
| `plate_reader` | 11 | Mirrors `agilent-cytation-server` `/control/*` surface |
| `plate_sealer` | 8 | Includes 412-precondition skills with `requires_components` (heater + stage) |
| `press` | 6 | `init`, `stop`, `press.{up,down}`, `plate.{in,out}` |
| `robot_arm` | 4 | `graph.{move_to,recover_to,record,mode}` — xArm motion-graph control surface (v1.1, claim-gated); added 2026-05-31 |
| `shaker` | 6 | `startup`, `shutdown`, `shake.{start,stop,set_temperature,set_speed}` with motor/heater AND-gates so a heater-side `degraded` doesn't block shaking |
| `solid_doser` | 12 | `dose.{well,multiple,row,column}` etc.; all endpoints moved under `/control/*` for dose v1.1 (2026-05-31) |

**Cross-repo changes since the last sweep.**

- **`kasa_tapo_services`** ships WebRTC opt-in via `bootstrap_go2rtc.py`
  — the rendered `go2rtc.yaml` now carries a `webrtc:` block
  (`0.0.0.0:8555/tcp` by default + a MagicDNS `candidates` entry from
  `GO2RTC_WEBRTC_HOST`). Drops PTZ video latency from ~0.5–1.5 s (MSE)
  to ~100–300 ms (WebRTC).
- **Dashboard `MsePlayer → WebRtcPlayer` swap shipped.** The web UI now
  has a `CameraPlayer` chooser (`web/src/components/CameraPlayer.tsx`)
  that renders `WebRtcPlayer` when the browser lacks the unmanaged
  `MediaSource` API (iPhone Safari) and `MsePlayer` otherwise. Both
  players share `web/src/lib/go2rtc.ts` and connect to the same
  `/streams/api/ws?src=<stream>` endpoint (go2rtc multiplexes MSE and
  WebRTC over one socket). This fixes "can't view the camera stream on
  iPhone" — iPhone never exposes `MediaSource`, so the MSE-only path
  always failed there. WebRTC media flows iPhone → `<magicdns>:8555/tcp`
  directly over the Tailnet; signaling still routes through Caddy
  `/streams/*`. Requires a one-time `ac-go2rtc.service` restart so
  `ExecStartPre` re-renders the `webrtc:` block (done 2026-05-31).
- **Dashboard control path** now shares one `httpx.AsyncClient` across
  every `/api/equipment/{id}/control/{action}` request (lifespan-owned
  at `app.state.control_client`). For v1.1 devices the
  claim → action → release dance reuses one keep-alive socket instead
  of paying TCP handshake × 3 per click; on warm sockets the
  per-action overhead drops from ~3 RTTs to ~0.
- **OT-2 deck-light toggle**: `opentrons-server` now exposes
  `POST /control/lights` (claim-gated), publishes `components.lights`
  and `lights.set` in `allowed_actions`. The matching `SkillDef` is in
  `skill_catalog/liquid_handler.py`; the dashboard renders a dedicated
  `LiquidHandlerTile` whose Lights row bypasses the `CONTROL_PASSWORD`
  gate (via `actionBypassesControlGate("lights")` in `tile-policy.ts`).
- **xArm5 tile** redesigned as three single-line component rows
  (Arm / Gripper / Track) reading state + selected metrics directly
  from `components.{arm,gripper,track}` and `metrics.{tcp_speed,
  angle_speed,track_position,force_magnitude}` plus
  `details.motion_graph.rail_location_name` for the at-preset pill.
  Redundant generic metric/component lists removed.

`uv run pytest -q` passes end-to-end; `api/tests` 24/24,
`skills/tests/test_skill_catalog.py` 10/10 (including the new
`test_liquid_handler_catalog_registered`),
`kasa_tapo_services/tests` 70/70 (including 4 new WebRTC-render
assertions).

## Why v0.4 is paused

v0.3 shipped the *SDK side* of the v1.1 contract. The agent-facing payoff —
"an MCP-aware agent calls a tool, hardware moves with claim semantics
intact" — needs at least one device that actually implements
`/control/{claim,heartbeat,release}` and populates `allowed_actions`.

That bar is comfortably cleared on the device side. As of the
2026-05-30 liveness sweep, **eight** devices reach
`protocol_version: "1.1"` on their live `/status` envelope with
non-empty `allowed_actions`:

| Device | `allowed_actions` (live) |
|---|---|
| `plateloc` | `["startup", "shutdown", "seal.set_temperature", "seal.set_time", "stage.in", "stage.out"]` |
| `ot2` | `["shutdown", "home", "setup", "pause", "pick_up_tip", "aspirate", "dispense", "drop_tip", "move_labware", "lights.set"]` |
| `cytation_5` | `["claim", "heartbeat", "release", "shutdown", "drawer.open", "drawer.close", "plate.load", "plate.unload", "well.update", "read.absorbance", "read.fluorescence", "read.luminescence", "imaging.capture"]` |
| `filter_every_well` | `["stop", "press.up", "press.down", "plate.in", "plate.out"]` |
| `fume_hood_actuator` | `["sash.move", "sash.stop"]` |
| `torry_pines_shaker` | `["startup", "shutdown", "shake.start", "shake.set_temperature", "shake.set_speed"]` |
| `xarm_translocation` | `["graph.move_to", "graph.recover_to", "graph.record", "graph.mode"]` (claim-gated; live list empty until the arm is connected — `requires_init` otherwise) |
| `pypoe_web` | `[]` (read-only web service) |

The fleet is no longer the bottleneck for v0.4. **What is blocking
now is purely SDK-internal:** the v0.3 carry-overs (`execute_plan`,
async interlocks, sync façades — listed below) need to land before
MCP can wrap them as tools.

The deferred SDK items below are the v0.4 / v0.4.5 carry-over list.

### Deferred from v0.3 → folded into v0.4

- `execute_plan(plan)` — sequential executor that wraps each step in
  `ClaimManager`, re-runs interlocks before each step, emits
  `PlanRunReport`. Cannot be acceptance-tested without claim semantics on
  at least one real device.
- **Async interlocks** — current `fn(plan, step, session) -> list[Violation]
  | None` is sync. Live-status interlocks (`docs/INTERLOCKS.md`'s
  long-form async signature) come with `execute_plan`.
- **Sync façades** for `ClaimManager` and `validate_plan` in
  `lab_skills.sync` so notebooks / sync CLIs / the MCP stdio loop can
  use them without async plumbing.

## Equipment migration plan (the in-flight effort)

Each per-device repo is migrated independently. The dashboard's
`equipment.yaml` flips per-entry as repos ship: `adapter: legacy_http`
→ `http` once `/status` is `EquipmentStatus`-shaped, and `protocol:
"1.0"` → `"1.1"` once the device implements claims.

### Verified state (2026-05-30 liveness sweep)

Probed against `http://127.0.0.1:8001/api/equipment` on the dashboard
host. Spec is what the device's live `/status` envelope reports — the
yaml `protocol:` mirror is the registry's expectation and is
occasionally ahead of the device.

| Device | Adapter | Spec (live) | Live state | Notes |
|---|---|---|---|---|
| `cam_hte_tapo_c245` | `http` | 1.0 | ✅ `ready` | `kasa-tapo-services` gateway back up. Full PTZ / presets / privacy / streaming / snapshot / recording / rolling-recorder surface in `allowed_actions`. WebRTC opt-in now available in the gateway (see *Cross-repo changes*). |
| `plug_hte_strip_right` / `plug_hte_strip_left` | `http` | 1.0 | ✅ `ready` | HS300 power strips. `on` / `off` / `toggle`. Per-outlet "safe vs destructive" decided client-side by `outletIsSafe()` against the gateway label. |
| `fume_hood_actuator` | `http` | 1.1 | ✅ `ready`, `allowed_actions: ["sash.move", "sash.stop"]` | v1.1 FastAPI service deployed on the Pi at `100.64.254.100:5000`. Round-trip verified from the `FumeHoodTile`. |
| `xarm_translocation` | `http` | 1.1 | ✅ `requires_init` (controller not connected) | **Claim protocol + motion-graph control surface deployed (2026-05-31).** Exposes `/control/{claim,heartbeat,release}`, `/control/graph/{move_to,recover_to,record,mode}`, and a `/control/claim/enforce` toggle; `stop` retired. Claims are gated behind a connected controller (`POST /connect` first — `claim` returns 400 "connect first" while disconnected). `robot_arm` catalog populated with the 4 `graph.*` SkillDefs. Still pending: dashboard graph controls + `/web/` deep-link fate, and live claim-enforcement verification once connected. |
| `ot2` | `http` | 1.1 | ✅ `ready`, `allowed_actions` includes protocol actions **+ `lights.set`** | `opentrons-server` exposes `/control/lights`; dashboard's `LiquidHandlerTile` consumes it. Gateway snapshot path (`details.snapshot`) populates real deck/pipette/labware JSON pulled from the live `protocol` over SSH — no `opentrons_server` install on the robot itself. Protocol-execution actions (setup/home/aspirate/…) advertised but no matching SkillDefs yet (typed protocol args still pending). |
| `dose_every_well` | `http` | 1.1 | ✅ `requires_init`, `allowed_actions: ["startup"]` | **v1.1 migration shipped + deployed (2026-05-31), verified live.** Claim protocol with **hard `X-Claim-Token` enforcement** on every mutating `/control/*` (423 without a claim); `details.claimed_by` surfaced; all control consolidated under `/control/*` (breaking — legacy top-level paths removed). `allowed_actions` state-driven (`requires_init` → `["startup"]`). 23 device tests pass on the Pi; tokenless→423 and the claim lifecycle confirmed end-to-end. |
| `torry_pines_shaker` | `http` | 1.1 | ✅ `ready`, `allowed_actions: ["startup", "shutdown", "shake.start", "shake.set_temperature", "shake.set_speed"]` | The 2026-05-09 `cal3` degrade has been cleared. Service-lock contention partly mitigated by capping `poll_timeout_seconds` at 2.5 s so the shaker fails fast under contention rather than dragging the fleet — restore once the device-repo `_build_status` read-off-lock fix ships. |
| `filter_every_well` | `http` | 1.1 | ✅ `ready`, `allowed_actions: ["stop", "press.up", "press.down", "plate.in", "plate.out"]` | v1.1 migration shipped + deployed on the Pi at `100.64.254.104`. PressTile, per-direction `hold_time` inputs, claim/release per request all verified. |
| `plateloc` | `http` | 1.1 | ✅ `ready`, full sealer `/control/*` surface advertised | **Real claim/heartbeat/release shipped (commit fa98ca8) and verified live 2026-05-31** — `_lock` placeholder replaced by a TTL `ClaimStore`; hard `X-Claim-Token` enforcement (423 *ahead of* the 412 interlocks, confirmed via tokenless `seal/time`), `details.claimed_by` populated. `seal.start` still gated by stage-in + heater-stable 412 interlocks. Only remaining gap is cosmetic (`equipment_version` null; CHANGELOG/`pyproject` still 1.3.1). |
| `cytation_5` | `http` | 1.1 | ✅ `ready`, 13 actions allowed (`read.{absorbance,fluorescence,luminescence}`, `imaging.capture`, drawer / plate / well) | Phase 3 (v1.1 + `/control/*`) and Phase 4 (skill catalog in this monorepo) **shipped** since the last sweep. Phase 2 (per-well sample tracking surfaced under `details.loaded_plate`) is the remaining device-repo work. |
| `agilent_uplc_ms` | `http` | 1.1 | ✅ `ready`, `allowed_actions: ["run.submit", "run.abort", "queue.cancel", "instrument.standby"]` | **v1.1 control migration shipped (`agilent-hplcms-server`, branch `feature-agent-control`).** Claim protocol with **hard `X-Claim-Token` enforcement** on mutating `/control/*` (423 without a claim); `details.claimed_by` surfaced; OLSS "Paused" mapped to `busy` (+`required_actions: ["resume_paused_sequence"]`). The FIFO-queue enqueue verbs (`run.submit` / `instrument.standby`) drop from `allowed_actions` on queue-full (412 + `Retry-After`) or OpenLab-down (409); `run.abort` / `queue.cancel` stay listed. `instrument.standby` is a low-flow park, not a full shutdown (power-down stays a manual procedure). `hplc` skill catalog populated. `do_not_call_connect` removed (control now allowed). Polling latency ~1.5 s — borderline against the 5 s timeout if OpenLab WMI degrades; raise to 8 s if it ever errors. |
| `agilent_biostack` | `http` | 1.0 | ✅ `ready` | Driver landed since the last sweep; entry flipped from `mock` to `http`. No `/control/*` surface yet (`allowed_actions: []`); read-only for now. |
| `pypoe_web` | `http` | 1.1 | ✅ `ready` | Internal web service. No control surface. |
| `env_*` (4 sensors) | `http` (mock backend) | 1.0 | dry_run (synthesised) | Awaiting the `env_sensors` repo. Not on the v0.4 critical path. |

### Remaining migration work (priority order)

> **Done since 2026-05-30 (verified live 2026-05-31):**
> `agilent-plateloc-server` real claims (commit fa98ca8) and
> `dose_every_well` full v1.1 (hard claims + `/control/*` consolidation).
> The `robot_arm` skill catalog was populated with the xArm `graph.*`
> control surface the device now exposes.

1. **`agilent_biostack` (plate_stacker) `/control/*` surface** —
   driver landed and is on `adapter: http` v1.0 read-only. Until it
   grows control endpoints the xArm is the lab's only plate-mover,
   which makes it both a throughput bottleneck and a single point of
   failure for the solubility workflow.
2. **`xarm_translocation` (operationalising the graph surface)** — the
   device now exposes claim + `/control/graph/*` and the catalog has the
   matching SkillDefs, but: (a) control is gated behind `POST /connect`;
   (b) the dashboard tile shows no graph controls yet (still the
   read-only three-row tile + `/web/` deep-link); and (c) the `/web/`
   side-door must become claim-aware or be fronted (see *Control-surface
   exposure*). Live claim-enforcement is still to be verified once the
   arm is connected.
3. **`agilent-cytation-server` Phase 2** — per-well sample tracking
   under `details.loaded_plate`. Phases 3 and 4 (v1.1 +
   `/control/*`, skill catalog) already shipped.

### Per-device migration checklist (v1.0)

A repo is considered v1.0 conformant when:

- [ ] `models.py` carries the STATUS_SPEC v1.0 Pydantic types verbatim
  (or imports them from a shared package once one is published).
- [ ] `GET /` returns `ProbeResponse(equipment_id, equipment_name,
  protocol_version="1.0")`.
- [ ] `GET /health` returns `HealthResponse(status="healthy")`.
- [ ] `GET /status` returns `EquipmentStatus` with snake_case field
  names. **Side-effect-free.** Always 200 unless the process is broken.
- [ ] `GET /openapi.json` is served (FastAPI gives this for free).
- [ ] All control endpoints under `/control/*`, gated by Pydantic body
  schemas with `Field(ge=, le=)` ranges.
- [ ] CORS allows the dashboard origin.
- [ ] No `_get_wlan_ip()` / `_get_tailscale_ip()` self-discovery code.
- [ ] Snapshot fixtures saved under `tests/fixtures/status_*.json`
  covering at least: `ready`, `requires_init`, `error`, `dry_run` (if
  applicable).
- [ ] `README.md` says "This repo conforms to lab status spec v1.0".
- [ ] `equipment.yaml` flipped from `adapter: legacy_http` (or
  `mock`) to `adapter: http`.

(Reference: `docs/STATUS_SPEC.md`. The full conformance checklist lives
in that doc and is the source of truth.)

### Per-device migration checklist (v1.1, additive over v1.0)

A repo is considered v1.1 conformant when, on top of v1.0:

- [ ] `protocol_version` reported on `/` and `/status` is `"1.1"`.
- [ ] `POST /control/claim`, `POST /control/heartbeat`,
  `POST /control/release` implemented per the v1.1 section of
  [`docs/STATUS_SPEC.md`](STATUS_SPEC.md).
- [ ] `EquipmentStatus.allowed_actions` is populated with the skill
  names (matching `Skill.name` from the catalog) the device will
  currently honor.
- [ ] `details.claimed_by` populated while a claim is held; cleared on
  release / expiry.
- [ ] `X-Claim-Token` enforced on `/control/*` (recommended; HTTP 423
  on miss) **or** the README documents that claims are advisory.
- [ ] Snapshot fixtures cover `ready` with no claim, `ready` with a
  claim held, `requires_init`.
- [ ] `README.md` says "This repo conforms to lab status spec v1.1".
- [ ] `equipment.yaml` entry has `protocol: "1.1"`.

### Per-device sub-tasks (current state)

#### `agilent-plateloc-server`

- v1.0 conformance: ✅ shipped.
- v1.1 deployment: ✅ live and reaching `ready`. `equipment.yaml` at
  `adapter: http`, `protocol: "1.1"`, `base_url:
  http://sdl2-pc-03-cytation.tail6a1dd7.ts.net:8010`. `/status` now
  reports `equipment_status: "ready"`, full sealer surface
  (`startup`, `shutdown`, `seal.set_temperature`, `seal.set_time`,
  `stage.in`, `stage.out`) in `allowed_actions`. The 2026-05-09 COM
  driver `Initialize('sdl2') failed` symptom has been cleared since
  the Diagnostics-dialog profile fix on the device PC.
- Open work for v1.1 *depth* (the v0.4 reference-device work):
  - [✅] Bump `PROTOCOL_VERSION` to `"1.1"` and serve it on `/`.
  - [✅] Populate `allowed_actions` based on current state.
  - [✅] `seal.start` interlocks (heater-stable + stage-in,
    412-mirroring `allowed_actions`) wired across device + SDK +
    dashboard tile (`requires_components` AND-gate in the SkillDef).
  - [✅] Replace `_lock` with a real claim/heartbeat/release
    implementation (`ClaimStore`, commit fa98ca8).
  - [✅] `claim_token` storage + TTL expiry (`_MIN`/`_MAX_TTL_S` clamp,
    `secrets.compare_digest`).
  - [✅] Populate `details.claimed_by` while a claim is held
    (`service.get_status()` reads it outside the COM lock).
  - [✅] Hard `X-Claim-Token` enforcement on all 8 control endpoints,
    423 ahead of the 412 interlocks; snapshot fixtures for the three
    claim states. Verified live 2026-05-31.
  - [ ] Cosmetic only: bump `equipment_version` / `pyproject` /
    CHANGELOG off 1.3.1 to match the shipped state.

#### `filter_every_well`

- v1.1 migration: ✅ complete.
  - `models.py` + `claims.py` added; `api.py` rewritten.
  - `GET /` (`ProbeResponse`), `GET /health`, `GET /status`
    (`EquipmentStatus`, `protocol_version: "1.1"`) all live.
  - Claim/heartbeat/release fully implemented (`ENFORCE_CLAIMS=True`).
  - `allowed_actions` is state-driven: `requires_init` → `["init"]`;
    `ready` → movement skills; `busy` → `["stop"]`.
  - Control endpoints under `/control/*`; `_move_lock` prevents
    concurrent movement commands and surfaces `busy` on `/status`
    while a motion is in progress.
  - `equipment.yaml` flipped to `adapter: http`, `protocol: "1.1"`.
  - Skill catalog `press.py` updated: all endpoints are `/control/*`.
    `press.up` / `press.down` use distinct `PressUpArgs` (default
    `hold_time=2.0`) / `PressDownArgs` (default `hold_time=5.0`)
    schemas so the API reference renders the per-direction defaults.
  - Dashboard tile (`PressTile`) exposes per-direction `hold_time`
    inputs (UP=2 s, DOWN=5 s defaults) next to the UP/DOWN pills.
    Inputs are disabled while locked or `busy`. See
    [`EQUIPMENT_INTEGRATION.md` §8](EQUIPMENT_INTEGRATION.md#8-filtration-press-kind-press).
- Remaining: deploy to Pi at `100.64.254.104`, redeploy the service,
  confirm `/status` returns `requires_init` on boot then `ready` after
  `/control/startup`.

#### `dose_every_well`

- **Full v1.1 migration: ✅ shipped + deployed + verified live
  (2026-05-31)** on `sdl2-pi5-minicnc.tail6a1dd7.ts.net:8000`. (The repo
  had already done the v1.0 envelope — `models.py`, `EquipmentStatus` on
  `/status`, `/`, `/health` — earlier than this doc tracked.)
- What landed:
  - [✅] `claims.py` — single-slot TTL `ClaimRegistry`
    (acquire/heartbeat/release/validate/current), pure module.
  - [✅] `POST /control/{claim,heartbeat,release}`; `PROTOCOL_VERSION`
    → `"1.1"`; claim wire models.
  - [✅] **Hard `X-Claim-Token` enforcement** on every mutating
    `/control/*` via a `require_claim` dependency (423, flat body with
    `claimed_by`).
  - [✅] `details.claimed_by` on `/status`; `allowed_actions` derived
    per state (matches the `solid_doser` catalog names).
  - [✅] **All control consolidated under `/control/*`** — breaking:
    legacy top-level `/startup`, `/dose/*`, `/plate/*`, `/calibrate/*`
    were *removed* (no unprotected aliases — an alias would be the very
    un-gated side-door being closed). Catalog `solid_doser.py` endpoints
    updated in lockstep.
  - [✅] Tests (`test_claims.py` portable + `test_api_claims.py` on the
    Pi, 23 pass) + v1.1 status fixtures; README v1.1 note.
- Branch `develop-modular`, commit `252d04e`. Cosmetic: `pyproject`
  `version` still `0.8.0` (`__version__` is `0.9.0`); `fastapi`/`uvicorn`
  not yet declared deps (present on the Pi).

#### `fume_hood_actuator`

- v1.1 migration: ✅ landed in fume-hood-sash-automation branch
  `v1.1-migration`.
  - Port from Flask to FastAPI (`api/api_service.py` rewritten as a
    `build_app()` factory; hardware deps lazy-imported so tests can
    stand up the app on dev machines).
  - `api/models.py` vendoring STATUS_SPEC v1.1 types.
  - `api/claims.py` in-process single-slot claim registry with TTL.
  - `api/status_builder.py` shared helper: both `/status` and the
    `/control/sash/*` gates derive `equipment_status` +
    `allowed_actions` from `(is_moving, sash_position)`, so STATUS_SPEC
    §6.2's mirror invariant ("allowed_actions and 412/423 refusals
    never disagree") holds by construction.
  - Endpoints: `GET /`, `GET /health`, `GET /status`,
    `POST /control/claim`, `POST /control/heartbeat`,
    `POST /control/release`, `POST /control/sash/move`,
    `POST /control/sash/stop`. `X-Claim-Token` is enforced on
    `/control/sash/*` (HTTP 423 on miss).
  - Removed: `/equipment/status`, `/position`, `/move`, `/stop` legacy
    routes; `_get_interface_ip()` self-discovery; `equipment_ip` /
    `equipment_tailscale` (spec §4.12).
  - `tests/fixtures/status_*.json`: snapshots for ready (no claim),
    ready (claim held), busy, requires_init, error.
  - `tests/docker-test/tests/test_actuator_api.py`: 20 cases against
    `fastapi.testclient.TestClient` with a hand-rolled FakeActuator.
    All pass.
  - The sensor service in `src/hood_sash_automation/sensor/` is
    untouched (still Flask, still on its pre-spec shape); it will
    migrate separately.
- Dashboard side: `equipment.yaml` flipped to `adapter: http`,
  `protocol: "1.1"`, `status_path: /status`. The dedicated
  `/api/equipment/{id}/sash/{move,stop}` passthrough was collapsed —
  the generic `/api/equipment/{id}/control/{action}` route now handles
  both calls (including dashboard-side claim acquire/release per
  request, matching `filter_every_well`). The skill catalog entries
  rename from `move`/`/move` to `sash.move`/`/control/sash/move`
  (same for stop); the typed client follows. `FumeHoodTile` reads
  `metrics.sash_position` / `metrics.target_position` /
  `equipment_status` unchanged — works against the new envelope.
  `LegacyFumeHoodActuatorAdapter` is unwired from the factory but
  remains importable from `.legacy` for one release cycle as a
  rollback path.
- Outstanding (not blocking the migration): redeploy
  `hood_sash_automation_actuator` on the Pi (uv-install the new
  fastapi/uvicorn deps; the entry-point name is unchanged); verify
  live `/status` returns `protocol_version: "1.1"`; verify
  `/control/sash/move` round-trip from the dashboard tile.

#### `xarm_translocation`

- v1.0 read-only conformance: ✅ landed (`xarm-translocation` FastAPI
  service exposes `GET /`, `GET /health`, `GET /status` via
  `EquipmentStatus`).
- v1.1 envelope + control surface: ✅ deployed (2026-05-31, verified
  via OpenAPI on the device). Device now exposes the **claim protocol**
  (`/control/{claim,heartbeat,release}`), a `/control/claim/enforce`
  toggle, and a **motion-graph control surface**
  (`/control/graph/{move_to,recover_to,record,mode}`); the old `stop`
  endpoint was retired. Claims are gated behind a connected controller
  (`/control/claim` returns 400 "connect first" while `requires_init`).
- Catalog: ✅ `skill_catalog/robot_arm.py` now registers the 4
  `graph.*` SkillDefs (`graph.move_to`, `graph.recover_to`,
  `graph.record`, `graph.mode`) matching the device endpoints.
- Open work:
  - [ ] **Verify claim enforcement live once the arm is connected**
    (`POST /connect`): tokenless `/control/graph/*` → 423,
    `details.claimed_by` populates, lifecycle. (Blocked today by the
    arm being disconnected.)
  - [ ] **`/web/` side-door**: make the native panel claim-aware or
    front it; decide the dashboard "Open control panel ↗" deep-link's
    fate. See *Control-surface exposure*.
  - [ ] **Dashboard graph controls**: `RobotArmTile` still shows only
    the read-only three-row summary + deep-link; surface the
    `graph.*` actions through the audited passthrough.
  - [ ] Publish current gripper stroke as `metrics.gripper_position`
    so the tile shows live position instead of the static range pill.

#### `agilent_uplc_ms` (v1.1 control migration)

- Repo: `agilent-hplcms-server` (branch `feature-agent-control`) — status +
  control sidecar for the Agilent UPLC-MS instrument (`SDL2_LC1290`). It drives
  `moses` as a subprocess and observes Agilent OpenLab CDS; it never imports or
  shares an env with `moses`.
- Conformance: STATUS_SPEC **v1.1**. `/status` carries `allowed_actions` and
  `details.claimed_by`; the claim protocol (`/control/{claim,heartbeat,release}`)
  is implemented with **hard `X-Claim-Token` enforcement** on mutating
  `/control/*` (missing/stale token → 423 Locked). `GET /control/queue` and the
  read-only `POST /control/startup` stay open.
- Control surface (`hplc` skill catalog): `run.submit` (`POST /control/run`),
  `run.abort` (`POST /control/abort`), `queue.cancel`
  (`DELETE /control/queue/{queue_id}`), `instrument.standby`
  (`POST /control/standby` — a low-flow park; a true power-down is a manual
  operator procedure, deliberately not an API action). The two FIFO-queue
  enqueue verbs drop from
  `allowed_actions` when the queue is full (412 `queue_full` + `Retry-After`) or
  OpenLab is down (409 `requires_init`); the single shared helper guarantees
  `allowed_actions` never disagrees with the endpoints (§6.2).
- State mapping: an OLSS "Paused" sequence is reported as `equipment_status:
  "busy"` with `required_actions: ["resume_paused_sequence"]` (paused is not a
  legal `EquipmentState`); the precise OLSS status survives in
  `details.olss_software_status` and the `hplc`/`ms` component state.
- Registry: `protocol: "1.1"` (turns on the aggregator's per-request claim
  dance) and `do_not_call_connect` removed — `/control/startup` is a read-only
  readiness probe, so there is no auto-connect hazard.
- Open watch item: dashboard polling latency is ~1.5 s, against a
  5 s `poll_timeout_seconds`. The OpenLab WMI introspection is the
  cost. If this regresses, raise `poll_timeout_seconds` to 8 s
  before it starts erroring.

#### `ot2`

- Repo: `opentrons-server` — split into `transport/` (SSH),
  `control/` (OT-2 wrapper + state readers), `gateway/` (FastAPI
  service + claims), `labware/`.
- Conformance: STATUS_SPEC v1.1. Live `/status` now reaches `ready`
  with `allowed_actions: ["shutdown", "home", "setup", "pause",
  "pick_up_tip", "aspirate", "dispense", "drop_tip", "move_labware",
  "lights.set"]`.
- **Deck-light toggle shipped** (this round): `POST /control/lights`
  is claim-gated; `components.lights` (`on`/`off`/`unknown`) +
  `"lights.set"` in `allowed_actions`. The matching SkillDef lives at
  `skill_catalog/liquid_handler.py` (`requires_states=[]` — always
  available); the dashboard renders a dedicated `LiquidHandlerTile`
  whose Lights row bypasses the `CONTROL_PASSWORD` gate via
  `actionBypassesControlGate("lights")` in `tile-policy.ts`.
- Open work:
  - [ ] SkillDefs + typed args for the protocol-execution actions
    (`setup`, `home`, `aspirate`, `dispense`, `pick_up_tip`,
    `drop_tip`, `move_labware`). These need labware-typed parameters
    the catalog has no shapes for yet.

#### `cytation_5`

- Repo: `agilent-cytation-server` — PyLabRobot-backed driver wrapped
  in a STATUS_SPEC service.
- Conformance: STATUS_SPEC **v1.1** live. `/status` reaches `ready`
  with `allowed_actions` advertising all 13 actions
  (`claim`/`heartbeat`/`release`, `shutdown`, `drawer.open`/`close`,
  `plate.load`/`unload`, `well.update`, the three `read.*` modes,
  `imaging.capture`). `equipment.yaml` reflects `protocol: "1.1"` and
  the v1.1 envelope.
- That repo's multi-phase roadmap:
  - Phase 0+1 — STATUS_SPEC v1.0 read-only. **✅ done.**
  - Phase 2 — per-well sample tracking (`details.loaded_plate` from
    `Container`/`Plate`/`Well` + orchestrator-assigned `sample_id`).
    Still ahead.
  - Phase 3 — STATUS_SPEC v1.1 (`/control/claim,heartbeat,release`,
    `allowed_actions`, full `/control/*`). **✅ done.**
  - Phase 4 — `lab_skills/skill_catalog/plate_reader.py` registered
    in this monorepo. **✅ done** (11 SkillDefs).

#### `kasa-tapo-services` (camera + smart-plug gateway)

- Repo: `kasa_tapo_services` — STATUS_SPEC v1.0 gateway that fronts
  Tapo cameras (PTZ, presets, snapshot, recording, rolling-recorder)
  and Kasa plugs (HS300 strips, HS103 single plugs). Bound to
  `127.0.0.1:8002` on the dashboard host (intentional; cameras and
  plugs are lab-LAN only).
- Status as of 2026-05-30: gateway is **live and answering**. The
  2026-05-09 `connection_refused` regression has been cleared.
  `cam_hte_tapo_c245` and both HS300 power-strip tiles render real
  state.
- **WebRTC opt-in shipped** (this round): `bootstrap_go2rtc.py` now
  emits a `webrtc:` block in the rendered `go2rtc.yaml`
  (`0.0.0.0:8555/tcp` by default; MagicDNS `candidates` from
  `GO2RTC_WEBRTC_HOST`). Reduces PTZ video latency from ~0.5–1.5 s
  (MSE) to ~100–300 ms (WebRTC). The dashboard-side
  `MsePlayer → WebRtcPlayer` swap (a `CameraPlayer` chooser in
  `ac-organic-lab/web/`) has since shipped — see *Cross-repo changes*
  above. MSE keeps working alongside for browsers that support it.

#### Remaining mock-only entries

The four `env_*` environmental sensors stay synthesised pending the
`env_sensors` repo. They are intentionally not on this round's
critical path. `agilent_biostack` has graduated from `mock` to `http`
v1.0 since the last sweep — see the migration priority list above for
the next step (growing a `/control/*` surface).

## Operational regressions

**As of the 2026-05-30 sweep, all 2026-05-09 regressions are
cleared.** Recording the previous list here for historical context:

1. ~~**Camera gateway down.**~~ **Resolved.**
   `kasa-tapo-services` is back up; `cam_hte_tapo_c245` reaches
   `ready` and serves the full PTZ / presets / privacy / streaming /
   snapshot / recording / rolling-recorder surface. The two HS300
   power strips also recovered with it.

2. ~~**`dose_every_well` placeholder hostname.**~~ **Resolved.**
   `equipment.yaml` points at
   `http://sdl2-pi5-minicnc.tail6a1dd7.ts.net:8000`; service is live
   and answering with `equipment_status: "requires_init"` (legacy
   envelope still — v1.1 migration is the next device-repo step).

3. ~~**`plateloc` COM driver cannot reach hardware.**~~ **Resolved.**
   Server reaches `ready` since the Diagnostics-dialog profile fix on
   the device PC. Full sealer `/control/*` surface advertised in
   `allowed_actions`.

Active watch items (not regressions; behavioural notes):

- **`agilent_uplc_ms` poll latency** — ~1.5 s against a 5 s
  `poll_timeout_seconds`. Raise to 8 s if it ever errors.
- **`torry_pines_shaker` poll contention** — `service.get_status()`
  on the device still holds the service lock across 4 serial round-
  trips; the dashboard caps `poll_timeout_seconds` at 2.5 s so the
  shaker fails fast under contention rather than dragging the fleet.
  Restore to ~10 s once the device-repo `_build_status` read-off-
  lock fix ships.

## Control-surface exposure (known security / safety risk)

**Recorded 2026-05-31.** Surfaced while wiring the dashboard control
audit trail (see below). This is a *known, partly-by-design* posture,
not a regression — captured here so it's tracked rather than tribal.

**The exposure.** Every spec device's `/control/*` surface is reachable
**directly on the Tailnet**; the dashboard is only one client of it.
Anyone on the Tailnet can `curl -X POST http://<device>/control/...` and
move hardware, bypassing both the dashboard's `CONTROL_PASSWORD` gate and
its audit trail. This is the documented v1 stance — STATUS_SPEC §11
(*"Auth. None at the equipment-repo level for v1. Tailscale ACLs gate
access"*) and the `CONTROL_PASSWORD` note in EQUIPMENT_INTEGRATION §6b
(*"the device REST endpoints … remain reachable directly by anyone on
the Tailnet"*). Auth was deliberately pushed to the network layer.

**What the 2026-05-31 audit change does and doesn't cover.**
`api/app/control.py` now writes one `equipment_events` row
(`event_type: "control_action"`, with actor / action / outcome) per
dashboard passthrough call. It captures **dashboard-mediated** control
only; it structurally *cannot* see direct-to-device traffic because the
dashboard isn't in that path.

**Per-device exposure** (during a workflow: "can someone move it
out-of-band, un-audited?"):

| Device(s) | Tailnet-direct? | Claims enforced? | Out-of-band move risk |
|---|---|---|---|
| cameras, plugs | **No** — gateway is loopback (`127.0.0.1:8002`) | n/a | **Low** — dashboard is the only network path |
| press, fume hood, ot2, cytation | Yes | **Yes** (X-Claim-Token → 423) | Rejected *if* a workflow holds the claim; cooperative + un-audited |
| `plateloc` | Yes | **Yes** (real `ClaimStore`, X-Claim-Token → 423) — *was a stub; fixed 2026-05-31* | Rejected if a claim is held; cooperative + un-audited via direct `curl` |
| `dose_every_well` | Yes | **Yes** (X-Claim-Token → 423) — *was v1.0 no-claim; fixed 2026-05-31* | Rejected if a claim is held; cooperative + un-audited via direct `curl` |
| `xarm_translocation` | Yes | **Yes** on `/control/*` (claim protocol deployed 2026-05-31); native `/web/` claim-awareness **unverified** | `/control/*` now claim-gated; the `/web/` side-door is still *advertised* by the tile's "Open control panel ↗" deep-link and its claim-awareness is unconfirmed |
| `agilent_uplc_ms` | Yes | **Yes** (X-Claim-Token → 423) — *v1.1 control migration* | Rejected if a claim is held; cooperative + un-audited via direct `curl`. Note: a run can still be started out-of-band directly in OpenLab CDS on the instrument PC — the sidecar surfaces that as `busy` but cannot prevent it. |
| biostack, pypoe | Yes | read-only, no control surface | n/a (nothing to move) |

**Key framing: claims are a concurrency guard, not a security guard.**
STATUS_SPEC §5 is explicit that claims are *cooperative, not
authenticated*. Where enforced, they stop a second *cooperating* client
(another dashboard, a workflow) from racing — surfacing as 423 with
`claimed_by`. They do not stop a determined human, a direct `curl`, or
the xArm `/web/` panel. As of 2026-05-31 `plateloc` and `dose_every_well`
now hard-enforce claims (so the *cooperating* race is closed there); the
residual no-claim gaps are the xArm `/web/` native panel and any device
whose control surface predates claims.

**What closes it, in order:**

1. **Finish claims where stubbed/absent** — ✅ done for `plateloc`
   (real `ClaimStore`) and `dose_every_well` (full v1.1) as of
   2026-05-31; remaining only on any future control surface that
   predates claims. Gives enforceable mutual exclusion for the
   cooperating case. Does *not* stop out-of-band humans or the xArm
   `/web/` panel.
2. **Front device control surfaces behind the auth/audit edge** — the
   [`AUTH_SERVICE_DESIGN.md`](AUTH_SERVICE_DESIGN.md) auth module, but note its
   Caddy `forward_auth` covers only the *dashboard's*
   `/api/equipment/*/control/*` routes. Closing the direct-device hole needs the
   devices themselves behind that edge, or bound to loopback +
   reverse-proxied like the camera/plug gateway already is.
3. **xArm `/web/` panel specifically** — fold into the gateway-PC shim
   work (see the `xarm_translocation` sub-task): replace the
   "Open control panel ↗" deep-link with audited, claim-gated dashboard
   controls, and/or front the native panel at the edge. Until then it is
   the single most exposed control path in the lab.
4. **Operational / physical** — for the genuinely un-closeable cases
   (Tailnet + shell access), discipline and the hardware e-stop remain
   the backstop, per INTERLOCKS ("not a real-time safety system").

**Design decision: the claim *is* the mode — do not build per-device
AUTOMATED/MANUAL switches.** A separate mode state machine is only needed
where a device has a *second* control surface to coordinate (today: the
xArm `/web/`), and even there the right fix is to make that surface honor
the claim, not to add a parallel flag. For single-`/control/*` devices the
claim already provides mutual exclusion. Treat "manual mode" as *a human
holds the claim* (`owner: human@…`) and "automated mode" as *a workflow
holds the claim* — one mechanism, already exclusive and audited. If an
explicit AUTOMATED/MANUAL UX is ever wanted, implement it as a convention
on top of claims (a long-lived operator-held claim), not a new field.

The enforcement target is **one invariant, not a per-device feature**:
*every path to the hardware goes through a hard-enforced claim.* That
decomposes into (a) turn on STATUS_SPEC §5 hard enforcement
(`X-Claim-Token` required → 423) on the stragglers — `plateloc` (stub) and
`dose_every_well` (v1.0) — reusing shared claim code (the future
`lab-status-contract`, LG5) rather than hand-rolling per repo; and (b)
ensure each device has exactly one control path (the xArm `/web/`
side-door fix). Auth can centralize at the edge; claim *exclusivity state*
cannot (the device is the authority on its own state, ARCHITECTURE
decision #2) — only the chokepoint can be consolidated, via the
gateway-PC shim, where one proxy fronts several devices.

## Resumption criteria for v0.4

The MCP milestone resumes when **all** of the following are true:

1. **At least three devices in `equipment.yaml` have `adapter: http`.**
   ✅ **comfortably met** — all 17 entries are on `adapter: http`;
   thirteen respond live.
2. **`agilent-plateloc-server` is at `protocol: "1.1"`.** ✅ **met** —
   plus seven other devices also reach v1.1
   (`fume_hood_actuator`, `xarm_translocation`, `ot2`,
   `torry_pines_shaker`, `filter_every_well`, `cytation_5`,
   `pypoe_web`).
3. **`lab.skills()` returns a non-empty catalog with `available=True`
   entries against at least one v1.1 device.** ✅ **met** — every
   v1.1 device above reports a non-empty `allowed_actions` on live
   `/status`, and the SkillDef registry now spans 8 kinds, **all
   non-empty** (50 SkillDefs total — including the 4 `graph.*` skills
   added for the xArm on 2026-05-31).
4. **A workflow can run a five-step `Plan` against `agilent-plateloc-server`
   (dry-run is fine) using `validate_plan` + an executor.**
   🔴 **blocked** on the v0.3 carry-overs (`execute_plan`, async
   interlocks, sync façades) — there is still no executor to run the
   plan with. The device-side blocker is **cleared**: `plateloc` now
   ships real claim/heartbeat/release (verified live 2026-05-31), so
   "claim semantics intact" is finally verifiable once the executor
   lands.

Once those are green, v0.4 PR-1 (`execute_plan` + async interlocks +
sync façades) is the unblocking change, then PR-2 (`lab_skills.mcp` +
CLI) is the headline feature, then PR-3 (live agent acceptance against
PlateLoc) is the gate.

## Out of scope for this whole roadmap

- **Workflow runner** (Prefect / Temporal) — lives in project repos or
  a future `ac-organic-lab-runner`.
- **LLM / agent code** — future `ac-organic-lab-agents` repo.
- **Run records / manifests** — project repos own these.
- **`lab-status-contract` shared package** — wait until 3+ device repos
  ship on v1.1 cleanly (per `docs/STATUS_SPEC.md`).
- **Maintenance-tile UI rendering** — tracked separately; not blocking.

## See also

- [`docs/STATUS_SPEC.md`](STATUS_SPEC.md) — combined v1.0 + v1.1 device contract (authoritative); includes claims, `allowed_actions`, and the SiLA-comparison appendix.
- [`docs/SKILLS_CATALOG.md`](SKILLS_CATALOG.md) — what `Skill.name` / `allowed_actions` refer to.
- [`docs/INTERLOCKS.md`](INTERLOCKS.md) — four-layer safety model; layer 4 ships in v0.3.
- [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) — system layering + repo boundaries.
- [`docs/DEVICE_PC_SETUP.md`](DEVICE_PC_SETUP.md) — canonical install recipe for a Windows
  device PC (uv + NSSM); each device repo links here from its README.
- [`docs/OBSERVABILITY.md`](OBSERVABILITY.md) — logging tiers, central history DB schema.
- [`docs/EQUIPMENT_INTEGRATION.md`](EQUIPMENT_INTEGRATION.md) — onboarding / maintenance runbook.
- `.cursor/plans/build_ac-organic-lab-skills_5bb34ed0.plan.md` — original
  layered plan; this file is its committed counterpart.
