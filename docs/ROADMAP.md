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
| `robot_arm` | 0 | xArm has no SkillDefs yet (gateway-PC shim deferred) |
| `shaker` | 6 | `startup`, `shutdown`, `shake.{start,stop,set_temperature,set_speed}` with motor/heater AND-gates so a heater-side `degraded` doesn't block shaking |
| `solid_doser` | 12 | `dose.{well,multiple,row,column}` etc. |

**Cross-repo changes since the last sweep.**

- **`kasa_tapo_services`** ships WebRTC opt-in via `bootstrap_go2rtc.py`
  — the rendered `go2rtc.yaml` now carries a `webrtc:` block
  (`0.0.0.0:8555/tcp` by default + a MagicDNS `candidates` entry from
  `GO2RTC_WEBRTC_HOST`). MSE keeps working alongside until the
  dashboard's `MsePlayer → WebRtcPlayer` swap lands. Drops PTZ video
  latency from ~0.5–1.5 s (MSE) to ~100–300 ms (WebRTC).
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
| `xarm_translocation` | `["stop"]` (gateway-PC shim still deferred) |
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
| `xarm_translocation` | `http` | 1.1 | ✅ `ready`, `allowed_actions: ["stop"]` | Device now serves v1.1; `equipment.yaml` reflects that. Dashboard renders the redesigned three-row `RobotArmTile`. `/control/*` surface beyond `stop` still deferred to the gateway-PC shim discussion. |
| `ot2` | `http` | 1.1 | ✅ `ready`, `allowed_actions` includes protocol actions **+ `lights.set`** | `opentrons-server` exposes `/control/lights`; dashboard's `LiquidHandlerTile` consumes it. Gateway snapshot path (`details.snapshot`) populates real deck/pipette/labware JSON pulled from the live `protocol` over SSH — no `opentrons_server` install on the robot itself. Protocol-execution actions (setup/home/aspirate/…) advertised but no matching SkillDefs yet (typed protocol args still pending). |
| `dose_every_well` | `http` | 1.0 (device) / 1.1 (registry mirror) | ✅ `requires_init`, `allowed_actions: []` | Live and answering on `sdl2-pi5-minicnc.tail6a1dd7.ts.net:8000`. Device still on the pre-v1.1 envelope (legacy `SystemStatus` lifted enough to validate as v1.0); v1.1 work tracked in its sub-task below. |
| `torry_pines_shaker` | `http` | 1.1 | ✅ `ready`, `allowed_actions: ["startup", "shutdown", "shake.start", "shake.set_temperature", "shake.set_speed"]` | The 2026-05-09 `cal3` degrade has been cleared. Service-lock contention partly mitigated by capping `poll_timeout_seconds` at 2.5 s so the shaker fails fast under contention rather than dragging the fleet — restore once the device-repo `_build_status` read-off-lock fix ships. |
| `filter_every_well` | `http` | 1.1 | ✅ `ready`, `allowed_actions: ["stop", "press.up", "press.down", "plate.in", "plate.out"]` | v1.1 migration shipped + deployed on the Pi at `100.64.254.104`. PressTile, per-direction `hold_time` inputs, claim/release per request all verified. |
| `plateloc` | `http` | 1.1 | ✅ `ready`, full sealer `/control/*` surface advertised | Hardware reached and operational since the COM-profile fix on the device PC. `seal.start` gated by stage-in + heater-stable interlocks (412 mirror with `requires_components` AND-gate). `_lock` → real claim/heartbeat/release deepening is still the only v1.1 carry-over in its sub-task below. |
| `cytation_5` | `http` | 1.1 | ✅ `ready`, 13 actions allowed (`read.{absorbance,fluorescence,luminescence}`, `imaging.capture`, drawer / plate / well) | Phase 3 (v1.1 + `/control/*`) and Phase 4 (skill catalog in this monorepo) **shipped** since the last sweep. Phase 2 (per-well sample tracking surfaced under `details.loaded_plate`) is the remaining device-repo work. |
| `agilent_uplc_ms` | `http` | 1.0 | ✅ `ready` | `agilent-hplcms-server` read-only sidecar. Polling latency ~1.5 s — borderline against the 5 s timeout if OpenLab WMI degrades; raise to 8 s if it ever errors. |
| `agilent_biostack` | `http` | 1.0 | ✅ `ready` | Driver landed since the last sweep; entry flipped from `mock` to `http`. No `/control/*` surface yet (`allowed_actions: []`); read-only for now. |
| `pypoe_web` | `http` | 1.1 | ✅ `ready` | Internal web service. No control surface. |
| `env_*` (4 sensors) | `http` (mock backend) | 1.0 | dry_run (synthesised) | Awaiting the `env_sensors` repo. Not on the v0.4 critical path. |

### Remaining migration work (priority order)

1. **`agilent-plateloc-server` (claim semantics depth)** — server is at
   v1.1 but `_lock` is still the placeholder. Real claim/heartbeat/
   release with TTL expiry is the highest-value carry-over from this
   section and is what gives v0.4 something to validate against.
2. **`dose_every_well` (solid_doser) v1.0 → v1.1** — service is live
   on `sdl2-pi5-minicnc.tail6a1dd7.ts.net:8000` but the device still
   serves the legacy envelope. v1.0 conformance checklist below; v1.1
   layer afterwards.
3. **`agilent_biostack` (plate_stacker) `/control/*` surface** —
   driver landed and is on `adapter: http` v1.0 read-only. Until it
   grows control endpoints the xArm is the lab's only plate-mover,
   which makes it both a throughput bottleneck and a single point of
   failure for the solubility workflow.
4. **`xarm_translocation` (graduating control ops)** — gateway-PC shim
   discussion still open; defer per existing note. Device reaches
   v1.1 on the envelope but only advertises `stop`.
5. **`agilent-cytation-server` Phase 2** — per-well sample tracking
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
  - [ ] Replace `_lock` (currently in `service.py`) with a real
    claim/heartbeat/release implementation.
  - [ ] Add `claim_token` storage + TTL expiry.
  - [ ] Populate `details.claimed_by` while a claim is held.
  - [ ] Snapshot fixtures for the three claim states.

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

- **Hostname resolved** (as of equipment.yaml rewrite): `base_url`
  updated to `http://sdl2-pi5-minicnc.tail6a1dd7.ts.net:8000`;
  `adapter` flipped from `legacy_http` to `http`, `protocol: "1.1"`.
  DNS-failure regression is cleared. Live `/status` reachability should
  be confirmed with a direct `curl` from the dashboard host.
- Current driver state (per `dose_every_well/` repo, not directly
  re-verified in this sweep): FastAPI, `/status` returns `SystemStatus`
  (legacy), control endpoints are `/startup`, `/shutdown`, `/dose/well`,
  `/dose/multiple`, `/dose/row`, `/dose/column`, `/calibrate/flow-rate`,
  `/control/home`, `/control/tare`, `/control/read-balance`, plus plate
  management.
- v1.0 work:
  - [ ] Add `models.py` with STATUS_SPEC types.
  - [ ] Replace `SystemStatus` with `EquipmentStatus` on `/status`.
  - [ ] Add `/` and `/health`.
  - [ ] Move `/startup`, `/shutdown`, `/dose/*`, `/plate/*` under
    `/control/*` (some are already there). Keep legacy paths for one
    transition window.
  - [ ] Update `equipment.yaml`.

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
- v1.1 envelope: ✅ live. Device now reports `protocol_version: "1.1"`
  with `allowed_actions: ["stop"]` and a populated
  `components.{arm,gripper,track,force_torque}` block. `equipment.yaml`
  flipped to `protocol: "1.1"`.
- Dashboard: the dedicated `RobotArmTile` now renders three single-row
  component summaries (Arm: status + TCP / Ang speed; Gripper: status
  + stroke range + force; Track: status + position + rail preset
  pill). The previous `EquipmentStatusCard` fallback was retired for
  this kind.
- Open work, not in this round:
  - [ ] Resolve the gateway-PC shim architecture (xArm on a private
    subnet behind a PC running a thin REST proxy) before exposing any
    `/control/*` beyond `stop`.
  - [ ] Promote one unit operation at a time to `/control/<op>` and
    add a matching `SkillDef` in
    `skills/src/lab_skills/skill_catalog/robot_arm.py` (currently
    intentionally empty).
  - [ ] Publish current gripper stroke as
    `metrics.gripper_position` so the tile shows live position
    instead of the static range pill (slot already wired client-side).

#### `agilent_uplc_ms` (newly shipped)

- Repo: `agilent-hplcms-server` — read-only sidecar for the Agilent
  UPLC-MS instrument (`SDL2_LC1290`).
- Conformance: STATUS_SPEC v1.0 (read-only). Has no `/control/*`
  surface and is not planning one — it observes `moses` + Agilent
  OpenLab CDS and reports state; never opens its own session.
- Live `/status`: `equipment_status: "ready"`, message
  `"OpenLab supervisor up; no active acquisition"`. Fully populated
  `components` dict (openlab_acquisition / instrument_service /
  reverse_proxy / moses_controller / hplc / ms).
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
  (MSE) to ~100–300 ms (WebRTC). MSE keeps working alongside; the
  dashboard's `MsePlayer → WebRtcPlayer` swap is the follow-up that
  flips the user-visible behaviour and lives in `ac-organic-lab/web/`.

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
   `/status`, and the SkillDef registry now spans 8 kinds (52
   SkillDefs total including `lights.set` on liquid_handler and the
   shaker AND-gates).
4. **A workflow can run a five-step `Plan` against `agilent-plateloc-server`
   (dry-run is fine) using `validate_plan` + an executor.**
   🔴 **blocked** on the v0.3 carry-overs (`execute_plan`, async
   interlocks, sync façades). Until those land there is no executor
   to run the plan with. Also blocked on the `_lock` →
   claim/heartbeat/release deepening listed under
   `agilent-plateloc-server` above; until that ships, "claim semantics
   intact" is not actually verifiable.

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
