# AC Organic Self-Driving Lab — Roadmap

This document is the durable, git-tracked record of where the SDK + dashboard
stack is, what is deferred, and what must be true before the next SDK
milestone (v0.4) starts. The original layered plan lives in
`.cursor/plans/build_ac-organic-lab-skills_5bb34ed0.plan.md`; this doc is
its committed companion so the project state is recoverable without the
Cursor plan UI.

## Current state (last fleet sweep: 2026-05-09)

| Milestone | Scope | Status |
|-----------|-------|--------|
| **v0.1** | Monorepo + skills SDK foundation + aggregator move | ✅ shipped on `main` |
| **v0.2** | `EquipmentClient.command`, sync wrapper, control exceptions, skill catalog, `LabSession.skills()`, typed per-kind clients | ✅ shipped on `main` |
| **v0.3** | STATUS_SPEC v1.1 spec doc, `ClaimManager`, `Plan` / `validate_plan` / `PlanReport`, `Violation` + `register_interlock`, two built-in interlocks, graceful degradation for v1.0 devices | ✅ shipped on `main` |
| **v0.4** | MCP server companion (catalog → tools, `/status` → resources, CLI `lab-skills mcp serve`) | ⏸ paused, but the *fleet-readiness* reason has been cleared — see *Why v0.4 is paused* below |
| **v0.5** | Standalone `lab-skills serve` CLI exposing the aggregator as a long-lived HTTP service | not started |

`uv run pytest -q` from the repo root: **137 passed** (98 v0.1/v0.2 + 8 claims
+ 14 plan/interlocks + 17 platforms-refactor). Dashboard `/api/equipment` is verified
end-to-end on `http://100.64.254.6:3000/api/equipment`. Of the 17 registered
devices (3 added since the sweep: `plug_hte_strip_right`, `plug_hte_strip_left`,
`pypoe_web`): 7+ return live data from real hardware; 4 environmental sensors
emit synthetic readings; 1 BioStack is an explicit `mock` placeholder;
`cam_hte_tapo_c245` and the two power strips are offline because
`kasa-tapo-services` is not running; `dose_every_well`'s placeholder hostname
was resolved in the equipment.yaml rewrite — service reachability unverified.
See *Operational regressions* below.

## Why v0.4 is paused

v0.3 shipped the *SDK side* of the v1.1 contract. The agent-facing payoff —
"an MCP-aware agent calls a tool, hardware moves with claim semantics
intact" — needs at least one device that actually implements
`/control/{claim,heartbeat,release}` and populates `allowed_actions`.

That bar is now cleared on the device side. As of the 2026-05-09 liveness
sweep against `http://100.64.254.6:3000/api/equipment`, five devices
respond on `adapter: http`:

- `plateloc` at `protocol: "1.1"`, reporting `allowed_actions: ["startup"]`
- `ot2` at `protocol: "1.1"`, reporting `allowed_actions: ["startup"]`
- `xarm_translocation`, `agilent_uplc_ms`, `cytation_5` at `protocol: "1.0"`

The fleet is no longer the bottleneck for v0.4. **What is blocking now:**

1. **v0.3 carry-overs** (`execute_plan`, async interlocks, sync façades —
   listed below) need to land before MCP can wrap them as tools.
2. **One remaining operational regression** must be cleared:
   - `kasa-tapo-services` is not running on the dashboard host; the
     camera tile and both power-strip tiles report `connection_refused`
     on `127.0.0.1:8002`.
   - ~~`dose_every_well` placeholder hostname~~ — **resolved**: the
     equipment.yaml rewrite replaced `doser.tail-XXXX.ts.net` with the
     real Tailscale name `sdl2-pi5-minicnc.tail6a1dd7.ts.net`. Service
     reachability still needs a live poll to confirm.

   See *Operational regressions* below.

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

### Verified state (2026-05-09 liveness sweep)

Probed against `http://100.64.254.6:3000/api/equipment` and, for
legacy devices, directly against the device port to confirm the
response shape.

| Device | Adapter | Spec | Live | Notes |
|---|---|---|---|---|
| `xarm_translocation` | `http` | 1.0 | ✅ `requires_init` | Idle, healthy. `/control/*` deferred to gateway-PC shim discussion. |
| `agilent_uplc_ms` | `http` | 1.0 | ✅ `ready` | `agilent-hplcms-server` shipped. Read-only sidecar over `moses` + OpenLab. Polling latency ~1.5 s — borderline against the 5 s timeout if OpenLab WMI gets slower. |
| `ot2` | `http` | 1.1 | ✅ `requires_init`, `allowed_actions: ["startup"]` | `opentrons_workflows` shipped: SSH transport, FastAPI gateway, claim/lease scaffolding. v1.1 server side; physical OT-2 idle until `/control/startup`. |
| `cytation_5` | `http` | 1.0 | ✅ `dry_run` | `agilent-cytation-server` shipped (PyLabRobot-backed). That repo's own roadmap: Phase 0+1 done; Phase 2 (per-well sample tracking), Phase 3 (v1.1 + `/control/*`), Phase 4 (skill catalog) still ahead. |
| `plateloc` | `http` | 1.1 | ✅ `requires_init`, `allowed_actions: ["startup"]`, `last_error: COM driver` | Server v1.1 deployed and answering. Physical PlateLoc not responding to the COM control — distinct issue from spec conformance, tracked under *Operational regressions*. |
| `cam_hte_tapo_c245` | `http` | 1.0 | ❌ `connection_refused` | Aggregator can't reach `127.0.0.1:8002`. `kasa-tapo-services` is not running on the dashboard host. *Operational regression.* |
| `filter_every_well` | `http` | 1.1 | ✅ `ready` / `requires_init` | Migrated to STATUS_SPEC v1.1. `/status` returns `EquipmentStatus` with `allowed_actions`. Control endpoints under `/control/*`. Claim/heartbeat/release enforced (`ENFORCE_CLAIMS=True`). |
| `fume_hood_actuator` | `legacy_http` | — | ✅ `ready` | Verified still legacy: Flask, `/equipment/status` (not `/status`), no `GET /` probe (404). Hand-rolled JSON shape with `equipment_name` / `equipment_status` / `system_state` / `sash_state`. |
| `dose_every_well` | `http` | 1.1 | 🟡 unverified | Placeholder hostname resolved: `sdl2-pi5-minicnc.tail6a1dd7.ts.net:8000`. Adapter flipped to `http`, `protocol: "1.1"`. Live reachability not confirmed since the equipment.yaml rewrite. |
| `agilent_biostack` | `mock` | — | — | No driver. `required_actions: ["integrate_repo"]`. |
| `env_*` (4 sensors) | `mock` | — | — | Synthesised readings. Awaiting `env_sensors` repo. |

### Remaining migration work (priority order)

1. **`dose_every_well` (solid_doser)** — hostname is resolved; same
   migration shape as `filter_every_well` once service is reachable.
   Confirm live `/status`, then proceed with the v1.0 checklist below.
3. **`fume_hood_actuator`** — Flask → FastAPI port plus spec envelope.
   The live device today serves a hand-rolled shape on
   `/equipment/status`; the v1.0 work renames to `/status` and bumps
   the envelope.
4. **`agilent_plateloc` (claim semantics depth)** — server is at v1.1
   but `_lock` is still the placeholder. Real claim/heartbeat/release
   with TTL expiry is the highest-value carry-over from this section
   and is what gives v0.4 something to validate against.
5. **`xarm_translocation` (graduating control ops)** — gateway-PC shim
   discussion still open; defer per existing note.
6. **`agilent_biostack`** — no driver, no migration. Without a stacker
   driver the xArm is the only plate mover, which makes it both a
   throughput bottleneck and a single point of failure for the
   solubility workflow.

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

#### `agilent_plateloc`

- v1.0 conformance: ✅ shipped.
- v1.1 deployment: ✅ live as of 2026-05-09. `equipment.yaml` flipped to
  `adapter: http`, `protocol: "1.1"`, `base_url:
  http://sdl2-pc-03-cytation.tail6a1dd7.ts.net:8010`.
  `/status` returns `protocol_version: "1.1"`, `equipment_status:
  "requires_init"`, `allowed_actions: ["startup"]`.
- Outstanding hardware issue (not a spec issue): `last_error.code =
  "startup"`, "Initialize('sdl2') failed (code -2147221503). Could
  not initialize - No response from PlateLoc". The COM driver cannot
  reach the physical sealer; the README hints to use the Diagnostics
  dialog to fix the profile.
- Open work for v1.1 *depth* (the v0.4 reference-device work):
  - [✅] Bump `PROTOCOL_VERSION` to `"1.1"` and serve it on `/`.
  - [✅] Populate `allowed_actions` based on current state
    (currently `["startup"]` while `requires_init`).
  - [ ] Replace `_lock` (currently in `service.py`) with a real
    claim/heartbeat/release implementation.
  - [ ] Add `claim_token` storage + TTL expiry.
  - [ ] Populate `allowed_actions` for `ready` (`["seal.start",
    "seal.set_temperature", ...]`) and the other states.
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

- Current (2026-05-09 verified): Flask on `100.64.254.100:5000`. Direct
  probes:
  - `GET /equipment/status` → `{equipment_name: "fume_hood_sash_actuator",
    equipment_ip: "172.31.32.236", equipment_status: "ready",
    system_state: "active", sash_state: "stationary", is_moving: false,
    ...}` (legacy hand-rolled shape; aggregator translates via
    `legacy_http`).
  - `GET /status` → `{current_position: null, is_moving: false}`
    (different ad-hoc shape; not the legacy `StatusResponse` and not
    `EquipmentStatus`).
  - `GET /` → 404. `GET /health` → `{status: "healthy", actuator: ...}`
    (not the spec `HealthResponse` shape).
- Repo confirmed: `fume-hood-sash-automation`.
- v1.0 work:
  - [ ] Port from Flask to FastAPI. Re-use the actuator/sensor classes
    untouched.
  - [ ] Add `models.py` with STATUS_SPEC types.
  - [ ] Standardise `/status` (drop the `/equipment/` prefix).
  - [ ] Add `/`, `/health`, `/control/*`.
  - [ ] Update `equipment.yaml`: `status_path: /equipment/status` →
    `/status`; `adapter: legacy_http` → `http`.

#### `xarm_translocation`

- v1.0 read-only conformance: ✅ landed. The FastAPI service in
  `xarm-translocation` exposes `GET /`, `GET /health`, `GET /status`
  via the standard `EquipmentStatus` envelope (see
  `xarm-translocation/src/core/models.py` and `status_builder.py`).
  The web UI moved from `/` to `/web/`. Browser status fetches and
  WebSocket pushes both carry the spec envelope.
- `equipment.yaml` flipped from `adapter: legacy_http` to
  `adapter: http`, with `protocol: "1.0"`. `do_not_call_connect: true`
  is retained because the SDK still has no skill catalog or
  `/control/*` surface for the xArm.
- `LegacyXArmAdapter` is marked deprecated in
  `skills/src/lab_skills/status_adapters/legacy.py` and is no longer
  wired in the factory; it stays importable for one release cycle as
  a rollback path, then deletes in a follow-up PR.
- Open work, not in this round:
  - [ ] Resolve the gateway-PC shim architecture (xArm on a private
    subnet behind a PC running a thin REST proxy) before exposing any
    `/control/*`.
  - [ ] Promote one unit operation at a time to `/control/<op>` and
    add a matching `SkillDef` in
    `skills/src/lab_skills/skill_catalog/robot_arm.py`.
  - [ ] STATUS_SPEC v1.1 (claim/heartbeat/release,
    `allowed_actions`) once at least one `/control/*` action exists.

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

#### `ot2` (newly shipped)

- Repo: `opentrons_workflows` — split into `transport/` (SSH),
  `control/` (OT-2 wrapper + state readers), `gateway/` (FastAPI
  service + claims), `labware/`.
- Conformance: STATUS_SPEC v1.1. Live `/status` returns
  `protocol_version: "1.1"`, `equipment_status: "requires_init"`,
  `allowed_actions: ["startup"]`, with a snapshot of deck / pipettes /
  labwares / modules.
- This is the second v1.1 device alongside `plateloc`, which means
  v0.4 has more than one reference target — good for catalog
  ergonomics work.

#### `cytation_5` (newly shipped)

- Repo: `agilent-cytation-server` — PyLabRobot-backed driver wrapped
  in a STATUS_SPEC service.
- Conformance: STATUS_SPEC v1.0 (read-only). Live `/status` reports
  `equipment_status: "dry_run"` because the physical Cytation 5 is
  not yet wired to the lab PC's USB; the service is up and answering
  with the spec envelope (components: optics / incubator /
  plate_stage / imaging; metrics: actual_temperature, read_count).
- That repo carries its own multi-phase roadmap:
  - Phase 0+1 — STATUS_SPEC v1.0 read-only, `equipment.yaml` flips
    from `mock` to `http`. **✅ done.**
  - Phase 2 — per-well sample tracking (PyLabRobot
    `Container`/`Plate`/`Well` + orchestrator-assigned `sample_id`,
    surfaced under `details.loaded_plate`).
  - Phase 3 — STATUS_SPEC v1.1 (`/control/claim,heartbeat,release`,
    `allowed_actions`, full `/control/*`).
  - Phase 4 — `lab_skills/skill_catalog/plate_reader.py` registered
    in this monorepo.

#### `kasa-tapo-services` (camera + smart-plug gateway)

- Repo: `kasa_tapo_services` — STATUS_SPEC v1.0 gateway that fronts
  Tapo cameras (PTZ, presets, snapshot, recording) and Kasa plugs.
  Bound to `127.0.0.1:8002` on the dashboard host (intentional;
  cameras and plugs are lab-LAN only).
- Status as of 2026-05-09: gateway process is **not running** on the
  dashboard host. The dashboard's camera tile reports
  `connection_refused`. The full media + control passthrough surface
  is therefore offline. See *Operational regressions*.

#### Mock-only entries

The remaining yaml entries (`agilent_biostack`, env sensors) stay
`adapter: mock` until their respective repos exist. They are
intentionally not on this round's critical path. Note that
`agilent_biostack` is the only plate-stacker in the inventory: while
it is mock, the xArm is the sole plate-mover for the entire
solubility workflow, which makes it both a throughput bottleneck and
a single point of failure. Promoting BioStack should be queued behind
the legacy_http migrations but ahead of v0.5.

## Operational regressions (2026-05-09)

These are not v0.4 carry-overs — they are deployed-state issues to
clear before resuming the SDK roadmap, since they make the
dashboard's "what's running" picture wrong.

1. **Camera gateway is down.** `cam_hte_tapo_c245` registers
   `adapter: http`, `base_url: http://127.0.0.1:8002`, but the
   aggregator gets `connection_refused`. `kasa-tapo-services` is not
   running on the dashboard host. Action: start
   `kasa-tapo-services.service` (or whatever the systemd unit is
   called per `deploy/README.md` § "Optional: cameras + smart plugs"),
   then re-poll. While down, the camera tile, media listing, and
   PTZ/snapshot/record surface are all offline.

2. ~~**`dose_every_well` placeholder hostname.**~~ **Resolved.**
   `equipment.yaml` was rewritten: `base_url` is now
   `http://sdl2-pi5-minicnc.tail6a1dd7.ts.net:8000`, adapter is
   `http`, protocol is `"1.1"`. The DNS-failure poll loop is gone.
   Confirm live reachability with
   `curl -fsS --max-time 3 http://sdl2-pi5-minicnc.tail6a1dd7.ts.net:8000/health`
   from the dashboard host; if the service is not yet deployed, add
   `enabled: false` until it is.

3. **`plateloc` COM driver cannot reach hardware** (informational,
   not a software regression). Server is v1.1 and answering, but the
   physical PlateLoc isn't responding to the Agilent COM control:
   `last_error.code = "startup"`, "Initialize('sdl2') failed (code
   -2147221503). Could not initialize - No response from PlateLoc".
   Hint from the device side: open the Diagnostics dialog to
   create / fix the `sdl2` profile. Track this on the device repo,
   not here.

## Resumption criteria for v0.4

The MCP milestone resumes when **all** of the following are true:

1. **At least three devices in `equipment.yaml` have `adapter: http`.**
   ✅ **met** — five non-camera devices (`xarm_translocation`,
   `agilent_uplc_ms`, `ot2`, `cytation_5`, `plateloc`) plus the camera
   are registered as `http`. Five are responding live.
2. **`agilent_plateloc` is at `protocol: "1.1"`.** ✅ **met** —
   confirmed via live `/status` returning `protocol_version: "1.1"`
   and `allowed_actions: ["startup"]`.
3. **`lab.skills()` returns a non-empty catalog with `available=True`
   entries against at least one v1.1 device.** 🟡 **likely met,
   needs explicit verification** — both `plateloc` and `ot2` are
   reporting `allowed_actions: ["startup"]`, which the SDK's skill
   evaluation should surface as available. Run
   `lab.skills()` against the deployed binding and confirm.
4. **A workflow can run a five-step `Plan` against `agilent_plateloc`
   (dry-run is fine) using `validate_plan` + an executor.**
   🔴 **blocked** on the v0.3 carry-overs (`execute_plan`, async
   interlocks, sync façades). Until those land there is no executor
   to run the plan with. Also blocked on the `_lock` →
   claim/heartbeat/release deepening listed under
   `agilent_plateloc` above; until that ships, "claim semantics
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
