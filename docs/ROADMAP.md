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

## Current state (last full fleet sweep: 2026-05-30; protocol-version + liveness re-probe: 2026-07-30)

| Milestone | Scope | Status |
|-----------|-------|--------|
| **v0.1** | Monorepo + skills SDK foundation + aggregator move | ✅ shipped on `main` |
| **v0.2** | `EquipmentClient.command`, sync wrapper, control exceptions, skill catalog, `LabSession.skills()`, typed per-kind clients | ✅ shipped on `main` |
| **v0.3** | STATUS_SPEC v1.1 spec doc, `ClaimManager`, `Plan` / `validate_plan` / `PlanReport`, `Violation` + `register_interlock`, two built-in interlocks, graceful degradation for v1.0 devices | ✅ shipped on `main` |
| **v0.4 PR-1** | `execute_plan` (sequential live executor, per-step ClaimManager, layer-3 + layer-4 re-check, bounded `wait_timeout_s` for time-clearing preconditions, `PlanRunReport`), async interlocks (`run_interlocks_async`), sync façades (`SyncLabSession.validate_plan` / `execute_plan`), `command(claim_token=...)` | ✅ shipped on branch `feature-xarm-ot2` (2026-07-12) |
| **v0.4 PR-2** | MCP server companion — `lab_skills.mcp` (catalog → tools, `/status` → resources) + `lab-skills mcp serve` CLI; control gated behind `--allow-control` | ✅ shipped on branch `feature-xarm-ot2` (2026-07-12) |
| **v0.4 PR-3** | Live agent acceptance: run a 5-step `Plan` against PlateLoc via `execute_plan` | ◑ executor validated live 2026-07-15; a *successful* seal is blocked by a PlateLoc compressed-air fault (facilities), retry pending |
| **v0.5** | Standalone `lab-skills serve` CLI exposing the aggregator as a long-lived HTTP service | not started |

**Fleet snapshot** (live sweep 2026-08-09). Zero `legacy_http` left in
`equipment.yaml` — LG2 holds. **31 entries**: 27 `adapter: http`, 4 `mock`
(`laagente_analitica` + three `env_*` zones awaiting hardware). Nothing is
`enabled: false` and no entry carries a `maintenance:` block. All 31 answered
with no `fetch_error`.

Note "answered" is weaker than "reports real hardware" for the four `mock`
entries — a mock adapter always answers. Two entries were added since the
2026-07-30 sweep: `cam_echem_tapo_c100` (a second echem camera) and
`bitacora_eln` moved from mock to a real service.

**Environmental sensors: `sense-every-zone` (first zone live 2026-07-31).**
The repo the earlier notes called `env_sensors` shipped under the name
[`sense-every-zone`](https://github.com/cyrilcaoyang/sense-every-zone) (the
name `LAB_MONITORING.md` had reserved for it). It is a **gateway**: one
FastAPI process per node serving `GET /zones/{zone_id}/status`, with a bare
`/status` that 404s by design — the same shape as `kasa-tapo-services`, so
the registry entry carries a per-zone `status_path`.

Zone `env_hte` is live on real hardware: a Raspberry Pi Zero
(`sdl2-pi0-environ-01`, port 8030) with a Sensirion **SEN55** (T / RH / VOC
index / NOx index / PM1–PM10) and a **PiSugar 3** UPS, natively v1.2 on
`sdl-lab-contract`. It is monitoring-only, so it reaches v1.2 through the
§9 read-only clause: no `/control/*`, `allowed_actions: []`, `activity`
permanently `idle`. `equipment.yaml` flipped from `adapter: mock` to `http`
this commit.

The other three zones (`env_storage`, `env_lab499_west`, `env_lab499_east`)
stay `mock` pending hardware — `sdl2-pi0-environ-02` is enrolled in the
tailnet but offline. Their placeholder envelopes now **mirror the live
zone's shape** (same metric keys and units, `sen55_*` / `ups_*` components),
so every reader exercises one code path; they keep `equipment_status:
dry_run` and `adapter: mock` so synthetic readings can never be mistaken for
lab data.

> **Metric keys lost their unit suffixes** (2026-07-31, both repos). The
> device now publishes `temperature` / `humidity` / `voc` / `nox` / `pm25` /
> `battery` with the unit in `MetricValue.unit`, per best practice #5, rather
> than `temperature_c` / `humidity_rh` / `voc_index`. This was a live bug, not
> a cleanup: `LabMap.tsx` reads `metrics.temperature` by name, so the HTE
> marker would have rendered blank, and `_write_sensor_readings` looked for
> flat `details["temperature_c"]` — a shape no device ever served — so the
> history series would have silently recorded nothing. Suffixed names survive
> only inside `details`, which #5 permits. Pre-2026-07-31 `sensor_readings`
> rows (~170k each of `temperature_c` / `humidity_pct` / `co2_ppm`) were
> entirely mock-generated and are left in place, orphaned and no longer
> queried. `co2` is gone for good: nothing in the lab measures it, and the
> History tab charts the SEN55's VOC index in its place.

> The per-device table and sub-task sections below still cover only the
> original seventeen entries. The devices onboarded since (both Bambu
> printers + `bambu_gateway`, `ot2_complexation`, `cam_echem_tapo_c245`,
> and the newer service tiles) are live and version-checked in the
> protocol mix below, but have no migration history recorded here yet.

**Web-service tile: `analytica_db`.** AnaliticaDB (the lab's record
store, FastAPI on the data server at `100.64.254.6:8010`) is registered
under **Web Services** (`kind: other`, `adapter: http`, `protocol: "1.0"`,
no `open` pill) and serves a STATUS_SPEC `/status` envelope (`ready`, or
`degraded` if its Postgres is unreachable); the tile reads `ready`. That
repo is being generalized into the lab's ELN+LIMS record layer — see
[`DATABASE_DESIGN.md`](DATABASE_DESIGN.md).

**Protocol mix** (live `/status` envelopes, 2026-08-09 sweep). Registry
`protocol:` agrees with the wire for every `adapter: http` entry — no drift.

| Live version | n | Devices |
|---|---|---|
| **1.2** | 15 | `plateloc`, `xarm_translocation`, `torry_pines_shaker`, `ot2_hte`, `ot2_complexation`, `bambu_p1s_01`, `bambu_h2d_01`, `cytation_5`, `agilent_biostack`, `agilent_uplc_ms`, `filter_every_well`, `env_hte`, and the three mock `env_*` zones (whose synthetic envelopes mirror the live zone's v1.2 shape) |
| 1.1 | 3 | `fume_hood_actuator`, `dose_every_well`, `pypoe_web` |
| 1.0 | 13 | three `cam_*`, both `plug_hte_strip_*`, `kasa_tapo_gateway`, `bambu_gateway`, and the six service tiles |

**Seven devices publish the reserved `metrics["cycles_total"]`** (§2.3.1):
`plateloc` (instrument odometer), `torry_pines_shaker`, `filter_every_well`,
`cytation_5`, `agilent_biostack`, and both OT-2 gateways. The devices that
deliberately omit it are those whose primary operation is longer than the
60 s poll — `agilent_uplc_ms` (runs are minutes-to-hours) and
`xarm_translocation` — plus every monitoring-only tile.

The v1.2 devices consume the shared `sdl-lab-contract` package instead of
a vendored `models.py`. Migration dates: `torry_pines_shaker` (live
2026-07-25), `plateloc` (merged 2026-07-26; **deployed and verified live
2026-07-30** — device v1.4.0, `activity`, and `cycles_total: 1862`
mirroring the instrument odometer all present on the wire),
`xarm_translocation` (device repo commit c91dd05, verified live
2026-07-30), plus both OT-2 gateways and both Bambu printers, and
`cytation_5` (device repo commit 65fa1c4, deployed and verified live
2026-08-02).

The shared-package threshold ("3+ repos cleanly on v1.1 for ~1 month") is
comfortably cleared.

**Appendix B v2 gate — criteria 1 and 2 are now met.** Criterion 1 (the
shared `sdl-lab-contract` package) shipped 2026-07-25. Criterion 2 (the
*majority* of the fleet natively reporting `activity`) is **15 of the 18
v1.1+ devices** as of 2026-08-09 — and, unlike the first crossing on
2026-07-31 (which rested on a passive sensor), the margin is now carried by
actuating devices across four hosts: the Cytation PC's five, the two Pis
(`filter_every_well`, `env_hte`), and the UPLC sidecar on its own PC. That
is the end-to-end migration capability criterion 2 actually tests.

Three v1.1 devices remain, none of them blockers: `fume_hood_actuator` and
`dose_every_well` (actuating, worth migrating), and `pypoe_web` (a read-only
web service with no primary operation, where v1.2 would add nothing).

Criterion 2's second half — the §2.3.2 reader-side derivation deleted — has
held since 2026-07-25. The v1.0 group is mostly gateway-fronted or
presentation-only tiles where `activity` has no meaningful referent.

**Only criterion 3 is outstanding — a concrete case v1.2 cannot express.**
Until one appears, v2 stays a design target; see STATUS_SPEC Appendix B.

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

### Verified state (2026-05-30 liveness sweep; versions + states re-probed 2026-07-30)

Probed against `http://127.0.0.1:8001/api/equipment` on the dashboard
host. Spec is what the device's live `/status` envelope reports.

| Device | Adapter | Spec (live) | Live state | Notes |
|---|---|---|---|---|
| `cam_hte_tapo_c245` | `http` | 1.0 | ✅ `ready` | Full PTZ / presets / privacy / streaming / snapshot / recording surface; WebRTC opt-in available. |
| `plug_hte_strip_right` / `_left` | `http` | 1.0 | ✅ `ready` | HS300 power strips; `on`/`off`/`toggle`; per-outlet safety decided client-side (`outletIsSafe()`). |
| `fume_hood_actuator` | `http` | 1.1 | ✅ `ready` | `sash.move`/`sash.stop`; deployed on the Pi at `100.64.254.100:5000`; round-trip verified from `FumeHoodTile`. |
| `xarm_translocation` | `http` | **1.2** | ✅ `requires_init` | v1.2 native (device repo commit c91dd05, verified live 2026-07-30): controller-observed `activity` / `activity_since`, `allowed_actions` gated on activity, concurrent-move refusal (409 `motion_in_progress`, §6.1). Claim protocol + `/control/graph/*` deployed 2026-05-31; claims gated behind `POST /connect`. Open items in the sub-tasks below. |
| `ot2_hte` | `http` | **1.2** | ✅ `ready` | Now v1.2 native (gateway 8.7.0): `activity` + `cycles_total`. Protocol actions + `lights.set` advertised; deck snapshot pulled over SSH. A second OT-2, `ot2_complexation` (same gateway version, also v1.2), is registered and currently `error` — `POST /runs` read-timeout to `sdl2-ot2-complexation`, `last_error.code: startup_failed`. |
| `dose_every_well` | `http` | 1.1 | ✅ `requires_init` | Full v1.1 verified live 2026-05-31: hard `X-Claim-Token` (423), `/control/*` consolidation (breaking), state-driven `allowed_actions`. |
| `torry_pines_shaker` | `http` | **1.2** | ✅ `degraded` (motor healthy) | First native v1.2 device (2026-07-25): motor-observed `activity`, `cycles_total`, per-subsystem `allowed_actions`. Poll timeout restored to 10 s (read-off-lock fix deployed). The heater RTD `cal` fault recurs intermittently — now reported honestly as `degraded` without blocking shakes. 2026-08-02: recovered from a 2026-07-31 USB-serial drop (same COM6 after re-enumeration, no config change) and motor verified with a live 20 s test cycle (`degraded` + `running`, `cycles_total` 0→1); the RTD `cal` fault is active again — temperature control blocked pending recalibration at the instrument. |
| `filter_every_well` | `http` | **1.2** | ✅ `requires_init` | v1.2 **deployed and verified live 2026-08-09** (repo v1.1.0, PR #1): move-lock `activity` (the exact hardware-motion span), `cycles_total` counting platen strokes — a stroke lasts seconds, so the 60 s poll misses it and the counter is the only utilization record. First test suite in that repo (10 tests). `requires_init` after the deploy is normal: `_system_state` boots `stopped`, so a service restart always needs one `POST /control/startup`. PressTile per-direction `hold_time` inputs verified (EQUIP_STATUS.md §8). |
| `plateloc` | `http` | **1.2** | ✅ `ready` | v1.2 **deployed and live 2026-07-30** (device v1.4.0, PR #2): seal-cycle `activity`, `cycles_total` mirroring the instrument odometer (live value 1862, equal to `cycle_count`), three §6 interlocks (stage / health / temperature) all mirrored in `allowed_actions`, `equipment_version` populated. Real claims (TTL `ClaimStore`, commit fa98ca8) verified 2026-05-31; 423 ahead of the 412s. Reader-visible behaviour changes noted below the sub-tasks. |
| `cytation_5` | `http` | **1.2** | ✅ `ready` | v1.2 **deployed and verified live 2026-08-02** (device repo commit 65fa1c4): `activity` observed from the in-flight-operation flag, `activity_since` stamped at span edges, reserved `cycles_total` (measurements + captures; the original `read_count` stays measurement-only). The b86da09 migration had derived `activity` from `equipment_status` (§2.3 forbids it), stamped `activity_since` with the poll instant, and paired `requires_init` with `unknown` — all three fixed. **`/status` no longer shares the reader lock**: it was queueing behind reads, so `busy` / `running` was unobservable from outside (same read-off-lock fix as the shaker); it now serves a short-TTL readback cache and reports `details.readback_age_s`. 13 actions advertised; device-repo Phases 2+3+4 all shipped. |
| `agilent_uplc_ms` | `http` | **1.2** | ✅ `ready` | v1.2 **deployed and verified live 2026-08-09** (sidecar v0.3.0, PR #2): acquisition-observed `activity`, so an error landing mid-run reports `error` + `activity: "running"` instead of erasing the run. No `cycles_total` by design (runs are minutes-to-hours). Hard claims and the `workflow.start`/`end` campaign lock unchanged; the sidecar owns the queue. **Production runs from branch `fix_server_vial`, not `main`** — see the sub-task below. |
| `agilent_biostack` | `http` | **1.2** | ✅ `ready` | v1.2 **deployed and verified live 2026-08-03** (commit e531170): `activity` / `activity_since` from the macro-in-flight flag, reserved `cycles_total` counting plate moves (`stage_plate` / `present_plate` / `handoff`; `home` is `running` but carries no plate, so not a cycle), `allowed_actions` gated on activity. No read-off-lock fix was needed — `get_status()` already avoided `_op_lock`, so a poll answers during a ~21 s macro. Claim trio + `/control/{startup,shutdown,home,stage_plate,present_plate,handoff}` on real hardware (`details.com_port: COM8`, `bench_validated: 2026-05-29`), not dry-run. Still not exercised end-to-end from a workflow or a dashboard tile — see the sub-task. |
| `pypoe_web` | `http` | 1.1 | ✅ `ready` | Internal web service; no control surface. |
| `analytica_db` | `http` | 1.0 | ✅ `ready` | Record-layer tile; STATUS_SPEC `/status` envelope live alongside the data API. |
| `env_hte` | `http` | **1.2** | ✅ `ready` | **Live 2026-07-31.** `sense-every-zone` gateway on `sdl2-pi0-environ-01:8030`, `status_path: /zones/env_hte/status`; SEN55 + PiSugar 3, both components `ready`. Monitoring-only, so v1.2 via the §9 read-only clause (`allowed_actions: []`, `activity` always `idle`). Reached over a DERP relay — `poll_timeout_seconds: 8.0`, observed latency ~120 ms. **Reachable only ~56 % of the time** — campus DHCP lease expiry, not a device fault; the tile reads `ready` on both sides of each gap. See open items below. |
| `env_storage`, `env_lab499_west`, `env_lab499_east` | `mock` | 1.0 | dry_run | Awaiting hardware (`sdl2-pi0-environ-02` enrolled but offline). Synthetic envelopes mirror `env_hte`'s metric shape so readers take one path. |

### Remaining migration work (priority order)

1. ~~**`agilent_biostack` (plate_stacker) `/control/*` surface**~~ —
   **shipped** (device v0.2.0, claim trio plus
   `/control/{startup,shutdown,home,stage_plate,present_plate,handoff}`;
   v1.2 as of 2026-08-03). The xArm is no longer the lab's only
   plate-mover on paper. What remains is *operationalising* it, the same
   gap the xArm has: no dashboard tile controls, and no workflow has
   driven it through `execute_plan` yet. Skill names *do* line up — all
   six `plate_stacker` SkillDefs (`startup`, `shutdown`, `home`,
   `stage_plate`, `present_plate`, `handoff`) match the device's
   endpoint/`allowed_actions` names exactly, so this device does **not**
   have the xArm's mismatch problem (item 2c). Its claim enforcement is
   also still unprobed — see *Control-surface exposure*.
2. **`xarm_translocation` (operationalising the graph surface)** — the
   device now exposes claim + `/control/graph/*` and the catalog has the
   matching SkillDefs, but: (a) control is gated behind `POST /connect`;
   (b) the dashboard tile shows no graph controls yet; and (c) the
   `/web/` side-door must become claim-aware or be fronted (see
   *Control-surface exposure*).
3. ~~**`agilent-cytation-server` Phase 2**~~ — **done**, and the entry was
   stale: per-well tracking ships as `PlateStateStore` +
   `/control/plate/{load,unload}` + `/control/well/update`, and
   `details.loaded_plate` is on the live envelope (`null` with no plate
   loaded). Phase 4's `plate_reader.py` is registered in
   `skill_catalog/` (11 SkillDefs). With the v1.2 work of 2026-08-02 this
   device has no open migration items; what remains is **hardware
   verification of the write surface** — the `/control/*` reads and
   imaging capture have only ever been exercised dry-run
   (`RUNBOOK.md` §3-§4), so no measurement has yet been driven end-to-end
   from a workflow.

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
- [x] **STATUS_SPEC v1.2 migration** (v1.4.0, PR
  [#2](https://github.com/cyrilcaoyang/agilent-plateloc-server/pull/2),
  merged 2026-07-26). Contract types from `sdl-lab-contract` v1.2.0;
  seal-cycle `activity` / `activity_since`; `metrics["cycles_total"]`
  mirroring the instrument's lifetime odometer (`cycle_count` retained);
  `allowed_actions` gated on activity as well as the interlocks;
  `equipment_version` populated (closes the cosmetic above). 85 tests.
  Two behaviour changes worth knowing as a reader — see the notes below.
- [x] **Deploy + live verification** — **done; verified from the dashboard
  side 2026-07-30.** The live envelope now reports `protocol_version:
  "1.2"`, `equipment_version: "1.4.0"`, `activity: "idle"`, and
  `metrics.cycles_total: 1862` equal to the legacy `cycle_count` (the
  odometer mirror of §2.3.1 working as specified). `equipment_status:
  ready`, `message: "Idle, ready to seal"`, heater at 159 °C against a
  160 °C setpoint. The `allowed_actions` list is
  `[startup, shutdown, seal.set_temperature, seal.set_time, stage.in,
  stage.out]` — note `seal.start` is **absent**, consistent with the
  1 °C temperature-band interlock being active at probe time (§6.2
  mirroring, not a fault). The NSSM restart on the device PC still
  required an elevated shell as predicted; whoever performed it did so
  outside this record.
- [x] **Boot auto-connect retry** (v1.5.0, deployed and verified live
  2026-08-03) — the 2026-07-31 PC reboot started the service before the
  USB serial adapter enumerated; the single connect attempt failed
  (`profile_not_found`) and the device sat in `requires_init` for two
  days alongside the shaker, until reconnected via the claim-gated
  `/control/startup`. The lifespan now spawns a background task that
  retries a failed boot auto-connect every 30 s
  (`[service].startup_retry_interval_s`) until the first successful
  connect; a successful retry clears the init failure from
  `last_error` per §6.4. Same fix as shaker v0.2.2; see the
  *Operational regressions* watch item.
- [ ] **PR-3 retry** (see *Operational regressions* / v0.4 PR-3): still
  blocked on the compressed-air supply, unchanged by this release.

**Reader-visible consequences of the plateloc v1.2 release.** Both are
intentional; both change what a poller sees.

1. **`busy` is now rare-to-invisible in a poll.** The ActiveX control runs
   in blocking mode, so a seal cycle is exactly the span of the
   `POST /control/seal/start` request (0.5–12 s). Previously `_busy_state`
   was latched *after* `StartCycle` returned and cleared only by an
   explicit `seal/stop`, so the tile could sit at `busy` indefinitely
   while nothing was running. It now brackets the real cycle — which the
   60 s aggregator poll will usually miss entirely. **Utilization for this
   device must come from `metrics["cycles_total"]` deltas, not from
   sampling `activity`** (STATUS_SPEC §2.3.1). A mid-cycle poll is now
   answered rather than queued behind the cycle, so `busy` + `running` is
   observable when a poll does land inside one.
2. **`error` / `degraded` no longer advertise `["shutdown"]` only.** That
   was a §6.2 violation in the withholding direction (the endpoints
   honoured stage moves the list omitted) and it hid exactly the recovery
   an operator needs: during the 2026-07-15 failure the device offered no
   way to retract the carriage from a hot chamber. Recovery/diagnostic
   actions now stay listed (§2.2 permits it) and the *run* is gated by a
   new third interlock — `seal.start` returns **412** with
   `{detail, last_error_code, last_error_message, retry_after_s}` while
   `last_error` is uncleared. Mid-cycle conflicts are **409**, not 412
   (a concurrency conflict is a state conflict, §6.1). ~~The dashboard's
   `PlateSealerTile` should grow copy for the new 412 shape~~ — done
   2026-07-26 (`parseSealer412` health branch, sharing one
   `recoveryForCode` table with the last_error band; `no_plate` /
   `vacuum_error` copy added). See [`EQUIP_STATUS.md`](EQUIP_STATUS.md) §9.

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

- **STATUS_SPEC v1.2 migration** (device repo commit c91dd05, verified
  live 2026-07-30): contract types from `sdl-lab-contract`;
  `activity` / `activity_since` observed from the controller's motion
  state (not derived from `equipment_status`); `allowed_actions` gated
  on activity — while `activity == "running"` every `move.<node_id>`
  target is withheld and only `"stop"` remains. Only one motion may be
  in flight at a time; a concurrent move is refused with HTTP 409
  `{"detail":{"error":"motion_in_progress","message":"…"}}` — a §6.1
  state conflict, not a 412 satisfiable precondition. `equipment.yaml`
  flipped to `protocol: "1.2"` (this commit). The device still
  advertises `move.<node_id>` in `allowed_actions` while the skill
  catalog registers `graph.{move_to,recover_to,record,mode}` — that
  pre-existing mismatch is unaffected by this migration (see the
  skill-name reconciliation item below).
- **Events exporter** (plan Step 3c): `src/core/events_exporter.py`
  pushes fine-grained `state_transition` / `error` / `startup` /
  `shutdown` rows from the SDK callbacks to `POST /api/ingest/events`
  (best-effort, stdlib-only, disabled unless `XARM_INGEST_URL` is set).
  Conventions documented in LAB_MONITORING.md §4 event_type registry.
  **Deploy step pending:** set `XARM_INGEST_URL` in the `xarm` NSSM
  service env on the device PC, pull + restart.
- **`equipment_version`** populated on `/status` (was null).
- **Registry flipped to `protocol: "1.1"`** (now v1.2, above) — the
  dashboard passthrough now runs the per-request claim dance the
  device's hard enforcement requires (tokenless → 423 verified live
  2026-07-03, arm connected). `do_not_call_connect` stays: claims are
  deliberately gated behind `POST /connect` and the SDK must never
  auto-connect a robot arm.
- Claim-gating turned out broader than recorded: the legacy `/move/*`,
  `/gripper/*`, `/track/*`, `/robot/*`, `/velocity/*` surfaces all carry
  `require_claim` (423 even when *no* claim is held), so the `/web/`
  panel moves through the same gate. Exempt safety floor: `/move/stop`,
  `/clear/errors`, `/connect`, `/disconnect`.
- **Simulation self-identification** (device repo commit e0fc768,
  verified 2026-07-30 against the `uf_software` Docker simulator on the
  central server): when connected via a `docker` profile the device
  reports `equipment_status: "dry_run"` (never `ready`), prefixes
  `message` with `[SIMULATION]`, sets `details.simulated: true`, and the
  `/web/` panel shows an amber SIMULATION banner (headless-Chromium
  screenshot confirmed). `dry_run` coexists with any `activity` per
  STATUS_SPEC §2.3 — a mid-move sim poll reads `dry_run` +
  `activity: "running"` with `allowed_actions: ["stop"]`, and idle
  restores the `move.<node_id>` targets. Startup log prints
  `[events] exporter OFF` when `XARM_INGEST_URL` is unset; if it were
  set, the exporter self-suppresses under a simulation profile.

Open:

- [ ] **Full claim lifecycle verification** with the arm connected:
  claim → move → heartbeat → release, `details.claimed_by` populates.
  (Tokenless-423 half already verified live. The full lifecycle —
  claim → `graph/move_to` → release, `details.claimed_by` populating
  and clearing, plus expiry semantics: heartbeat after TTL → 401,
  tokenless move → 423 — was exercised end-to-end **against the Docker
  simulator** 2026-07-30 during the e0fc768 verification; only the
  real-arm repeat remains.)
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

#### `agilent_uplc_ms` (sidecar: `agilent-hplcms-server`)

> **Branch topology — read before deploying.** The NSSM service
> `hplc-ms-status` on `sdl2-pc-06-uplc` runs from branch
> **`fix_server_vial`**, not `main`. That branch carries work `main` lacks
> (`restrict server vial`; `reconcile standby-park failure`; `degrade on
> LC-module fault (§2.2)` + consumable-empty acknowledgments) — two of those
> commits sat **unpushed on the instrument PC** until 2026-08-09. `main` was
> merged into it for the v1.2 deploy (`f212cb3`, 161 tests). Checking out
> `main` on that PC would silently revert live behaviour. **Open:** merge
> `fix_server_vial` back to `main` and repoint production, so `main`
> describes what actually runs.
>
> Follow-up worth deciding: the §2.2 LC-module degrade fires only when the
> device is otherwise `ready` (`busy` is deliberately left alone, per the
> in-code rationale that a mid-run fault surfaces via OLSS/log-tail `error`).
> That carve-out predates `activity` existing — v1.2 can now express the
> honest shape, `degraded` + `activity: "running"`, exactly like the shaker.

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
- [x] **The dashboard stopped hosting its own control surface** (2026-08-05/06,
  both robots). `/equipment/<id>/control` and the two aliases now **frame the
  gateway's own operator SPA** (`/ot2/{hte,complexation}/ui/` through the edge)
  instead of rendering a second implementation here; ~1.9k lines and 21 tests
  were deleted from `web/`. This is the first device class where the dashboard
  is no longer the operator surface, so it is worth stating why: the two UIs
  were hand-synced ports that had already drifted, and the device panel is the
  better surface on the merits — it reads `/status` directly rather than
  through the aggregator's poll, and holds a real heartbeated claim instead of
  the per-request claim the passthrough takes for a single action. The
  dashboard keeps the read-only tile, `DeckPanel`, and the platform-level
  labware builder + definition store. See [`UI_DESIGN.md`](UI_DESIGN.md) §1.
  - Prerequisite, shipped device-side: the SPA builds with vite `base: "./"`
    and derives its API prefix from the page URL, so one build serves both
    instances through their own edge prefixes (no per-instance rebuild). It
    also detects being framed and suppresses its own auth banner. Both panels
    verified through the edge 2026-08-06.
  - **Audit gap opened and closed in the same week.** A write inside the panel
    bypasses the dashboard passthrough and its `control_action` row. The
    gateway's own events exporter (`ee529ad`, live on both gateways
    2026-08-06, ported from the xArm's) closes it: `control_action` with
    outcome / owner / `duration_s`, plus tip lifecycle and session edges, to
    `/api/ingest/events`. It also covers SDK and workflow calls, which never
    had an audit row at all — so the net trail is *wider* than before the
    embed. Enabled by `OT2_INGEST_URL`; `device_id` from `OT2_EQUIPMENT_ID`.
    Note a dashboard-proxied click now writes two rows (passthrough + device);
    the device row is authoritative for outcome and duration.
- [ ] Follow-ups now unblocked: `execute_plan` can drive a typed OT-2
  `Plan` (v0.4 PR-1); the setup sub-models could tighten the
  `ot_default`-conditional required fields (`loadname` / `config`) with
  validators once a real recipe exercises custom labware.
- [ ] **The `liquid_handler` SkillDefs are now the only in-repo model of the
  OT-2's write surface.** With `Ot2ControlPanel` gone, nothing in `web/`
  exercises those 18 verbs. The remaining guard is
  `test_liquid_handler_names_match_gateway_allowed_actions`, which asserts the
  catalog equals a **hand-transcribed literal** of the gateway's advertised
  strings — so it catches a rename on *our* side, but a rename on the
  gateway's only surfaces when someone updates that literal. Re-check it
  against `gateway/service.py::allowed_actions` whenever the device repo
  touches its control surface.

#### `cytation_5` (device repo: `agilent-cytation-server`)

- [x] **Phase 2** — per-well sample tracking. `PlateStateStore` persists to
  `state.json`; `details.loaded_plate` is live on the wire.
- [x] **STATUS_SPEC v1.2** (commit 65fa1c4, deployed 2026-08-02). The
  earlier b86da09 bump claimed v1.2 while deriving `activity` from
  `equipment_status` — the one thing §2.3 forbids — so the field carried no
  information. Now observed from the in-flight-operation flag, with
  `activity_since` stamped at the operation's real edges, `requires_init`
  paired with `idle` per the invariant table, and the reserved
  `cycles_total`.
- [x] **`/status` read off the lock.** It shared the reader's
  `asyncio.Lock`, so a poll issued during a read returned only *after* the
  read finished, with the busy flag already cleared: `busy` and
  `activity: "running"` were unobservable from outside. Now composed from
  in-memory state plus a 3 s readback cache (≤50 ms lock wait), with
  `details.readback_age_s` published. Same class of bug as the shaker's.

Open:

- [ ] **Hardware verification of the write surface.** Reads, drawer moves
  and `imaging.capture` have only been exercised dry-run; the live envelope
  is verified but no measurement has been driven end-to-end. See
  `RUNBOOK.md` §3-§4 (the FTDI ↔ libusbK driver swap this shares with
  `biotek_driver`) before booking bench time.
- [ ] **Watch: the service's own extras.** Its NSSM launch line was
  `uv run --extra api`, which strips `pylabrobot` from the venv on any
  restart — a plain `uv sync` disarmed the driver on 2026-08-02 and the
  reader came up `requires_init`. `AppParameters` now carries
  `--extra api --extra plr --extra windows`; the same trap applies to any
  device whose driver lives behind an extra.
- [x] **Tailscale `DependOnService` removed** (2026-08-11) — the service
  was offline ~12.5 h on 2026-08-10 because it alone carried
  `DependOnService: Tailscale` and a Tailscale MSI auto-update stopped it
  via SCM's dependency cascade (a clean stop, so NSSM never restarted it).
  Dependency cleared, stale NSSM description refreshed, recommendation
  withdrawn from DEVICE_PC_SETUP §6. Full write-up under *Operational
  regressions* below.
- [ ] **pylabrobot v1 is coming.** PR #1000 ("v1b1 changes", merged to
  `main` 2026-08-01, 759 files) restructures machine interfaces and touches
  `biotek_backend.py`; branch `cytation-10x-fov` carries a
  `CytationMicroscopyBackend`, suggesting reader and imager split apart.
  Unreleased — PyPI is at 0.2.2 (we pin 0.2.1, and nothing Cytation-side
  changed between them). `reader.py` will need rework when v1 ships.

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

**2026-08-02 live check** (recovery from a 2026-07-31 USB drop): the
Prolific PL2303 adapter vanished from the bus long enough for the
service's auto-connect to fail (`serial_init_failed`: "could not open
port 'COM6'"), leaving it in `requires_init` for two days. The adapter
re-enumerated on the **same** COM6 — confirmed by probing COM6/COM7 with
the driver's read-only identity queries (`v`/`V` → `SC25XR v6.1`, serial
50014748) — so no `config.toml` change was needed. Reconnected via the
claim-gated `/control/startup` and verified end-to-end with a 20 s
speed-3 test cycle: `degraded` + `activity: "running"` mid-cycle
(the §2.3 motivating shape, observed live), watchdog-ended on schedule,
`cycles_total` 0→1. Follow-up shipped: **v0.2.2 (deployed 2026-08-03)**
retries a failed boot auto-connect every 30 s until the first
successful connect, so this failure mode now self-heals (see the
*Operational regressions* watch item). Open:

- [ ] **Recalibrate the heater RTD at the instrument** — the `cal` fault
  is active again as of 2026-08-02 (`last_error.code:
  calibration_error`; device token `cal3`: high-point measured cal value
  ≤ low-point). The device firmware refuses temperature reads against
  the broken cal curve, so `shake.set_temperature` /
  `wait_for_temperature` stay withheld; shaking is unaffected. No API
  path can fix this — it is a front-panel two-point recalibration. If it
  recurs shortly after recalibrating, suspect the RTD probe or its
  connection rather than the stored values.

#### `sense-every-zone` (environmental sensors)

Zone `env_hte` is live and registered (see the fleet notes above). Open:

- [x] **Metric-key rename deployed** (device commit `7f22bf9`, unsuffixed
  `metrics` keys) — live since **2026-07-31 03:00 UTC**, i.e. the day the
  zone went up. `sensor_readings` records a clean 57-second changeover: the
  last `temperature_c` / `humidity_pct` / `co2_ppm` rows are stamped
  `02:59:56Z`, the first `temperature` / `humidity` / `voc` / `nox` /
  `pm25` / `pm10` / `battery` rows `03:00:53Z`, unbroken since. This entry
  was stale from the day it was written — the HTE map marker and history
  series were never actually empty. (Corrected 2026-08-11.)
- [ ] **`env_hte` is only ~56 % reachable — campus DHCP lease expiry.**
  Not a device fault: the `compsci` lease is 37800 s (10 h 30 m) and when it
  expires NetworkManager does not re-acquire, so the node sits with no IPv4
  for a further ~10 h 40 m. The Pi never reboots and the service never
  restarts through any of it, so `/status` looks perfectly healthy on either
  side of the gap. Free-running ~21 h 10 m period that drifts ~2 h 50 m
  earlier daily, so the window walks through working hours. Full evidence,
  the hypotheses already ruled out (Wi-Fi power-save, the PiSugar HAT), and
  the provisioning steps are in the device repo's
  `docs/REMOTE_ACCESS.md`. Durable fix is a DHCP reservation for
  `2c:cf:67:e8:9a:4c` from campus IT, or moving the nodes to a lab AP —
  the latter would also replace the DERP relay with a direct tailnet path.
- [ ] **`/health` is not §3-conformant** — returns `{"ok": …,
  "dependencies": […], "timestamp": …}` instead of `{"status": "healthy"}`.
  Deliberate (the repo aliases `HealthResponse = ZoneHealthResponse` and
  leaves the spec type unused as `SpecHealthResponse`), and harmless to the
  aggregator, which polls `status_path` — but it breaks EQUIP_GUIDE §1 Step A
  and any Kuma keyword monitor on `healthy`. Suggested fix: serve the spec
  shape at `/health`, move the richer body to `/health/zones`.
- [ ] **README missing two v1.2-mandated statements** — §9's read-only clause
  requires it to say *which* items are N/A because the device is
  monitoring-only (so a reader can tell "deliberately read-only" from
  "migration half-finished"), and §2.3 requires it to define what "primary
  operation" means for the kind (for a passive sensor: none, hence
  permanently `idle`). Neither is present, nor the "conforms to lab status
  spec v1.2" line. Its sample `equipment.yaml` block is also stale — it
  carries a `platform:` field (removed in schema v2) and a placeholder
  `100.64.254.100` base_url.
- [ ] `equipment_version` is `null` on `/status` (the package is at 0.2.0).
- [ ] Remaining three zones need hardware; `sdl2-pi0-environ-02` is enrolled
  in the tailnet but offline.

## Operational regressions

**All 2026-05-09 regressions are cleared** as of the 2026-05-30 sweep
(camera gateway down; `dose_every_well` placeholder hostname; `plateloc`
COM driver failure — details in git history).

**Cytation offline ~12.5 h, 2026-08-10 → cleared 2026-08-11.** The
`cytation` service was the only NSSM service on the Cytation PC configured
with `DependOnService: Tailscale`. Tailscale's MSI auto-updater (1.102.2,
2026-08-10 11:11) stopped the Tailscale service mid-update, so Windows SCM
stopped `cytation` with it — and SCM never restarts dependents when the
dependency returns. The stop is *clean* (exit code 0, tidy uvicorn
shutdown), so NSSM's `AppExit Default Restart` does not fire: that setting
governs the app crashing, not NSSM receiving a STOP control. The reader sat
`STOPPED` until restarted by hand ~12.5 h later; every sibling service
(no dependency) was unaffected. Fixed by clearing the dependency
(`sc config cytation depend= ""` — note `nssm reset ... DependOnService`
reports success but does **not** clear it, the field is native SCM config),
and verified no other service on the PC carries a Tailscale dependency.
The `DependOnService Tailscale` recommendation this configuration came from
is withdrawn from DEVICE_PC_SETUP §6, with the failure mode documented and
a §8 troubleshooting row (clean-stop + MsiInstaller correlation) so the
next instance is a minutes-long diagnosis. This incident class — a service
left dead by an external event, discovered hours later — is what the
`sdl-lab-hostops` fleet (AGENT_OPS.md) now exists to catch and, where
whitelisted, remediate remotely. Residual watch item below.

Active watch items (not regressions; behavioural notes):

- **Tailscale auto-updates stop the Tailscale service on every release**
  (MSI upgrade path). With the `cytation` dependency removed, no service on
  the Cytation PC stops with it anymore — but a mid-update window still
  drops tailnet reachability for a few seconds fleet-wide, and any *future*
  `DependOnService Tailscale` reintroduces the 2026-08-10 outage class.
  Never add that dependency (DEVICE_PC_SETUP §6); if update timing ever
  matters, pin/stage Tailscale updates on the device PCs instead.

- **`agilent_uplc_ms` poll latency** — ~1.5 s against a 5 s
  `poll_timeout_seconds`. Raise to 8 s if it ever errors.
- ~~**`torry_pines_shaker` poll contention**~~ — cleared 2026-07-25:
  the device-repo read-off-lock + short-TTL readings-cache fix deployed
  with v0.2.0; `poll_timeout_seconds` restored to 10 s.
- **Boot-time USB enumeration race (shaker + plateloc)** — the
  2026-07-31 event was a PC reboot, not an adapter-only blip: the NSSM
  services started before USB serial finished enumerating, and **both**
  devices' single auto-connect attempts failed within one second of
  each other (shaker `serial_init_failed`, plateloc
  `profile_not_found`: "Communication failed - Could not open"). Both
  sat in `requires_init` for two days until manually reconnected
  (shaker 2026-08-02, plateloc 2026-08-03). The retry loop suggested
  here **shipped 2026-08-03**: both services now retry a failed boot
  auto-connect every 30 s (`[service].startup_retry_interval_s`, 0
  disables; retry ends permanently at the first successful connect so
  an operator shutdown is never fought) — shaker v0.2.2, plateloc
  v1.5.0, both deployed and verified live. Port renumbering remains
  the manual case no retry can fix: the shaker's adapter came back as
  the same COM6 this time, but if `serial_init_failed` persists across
  retries, enumerate the Prolific COM ports and probe each with the
  driver's read-only identity queries (`v`/`V`) before editing
  `config.toml` (COM3 is Intel AMT, COM8 is the BioStack).
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

  **Partially addressed 2026-07-26 (plateloc v1.4.0).** Two of the three
  things that went wrong now behave better: after any operational failure
  `seal.start` is refused with 412 until the error clears (so a second
  cycle can't be started into a failed pneumatic supply), and the recovery
  actions — `stage.in` / `stage.out` / `shutdown` — stay in
  `allowed_actions` in `error` / `degraded` instead of collapsing to
  `["shutdown"]`, so the dashboard offers a way to retract the carriage.
  Still **open**: a *proactive* low-air check. The device has no pressure
  introspection (EQUIP_STATUS §9 "What this interlock does NOT cover"), so
  the first failure is still discovered by attempting the operation; a
  facility-level sensor is the only real fix.

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
| `xarm_translocation` | Yes, but `/control/claim` is **login-gated** (401 `login_required`, verified 2026-08-11) | **Yes**; an action with no valid claim → 423 `claim_required` | **The narrowest exposure in the fleet, and the only device where a bare `curl` cannot get a claim at all.** Identity is checked at claim acquisition, and no action is reachable without a claim, so the un-audited direct path is closed for writes; reads (`/status`, `/graph/nearest`) stay open. The `/web/` side-door remains the operator path and is still un-audited. Cost: the dashboard could not drive this arm either until commit `152a87c` — see below. |
| `agilent_uplc_ms` | Yes | **Yes** (X-Claim-Token → 423) | Rejected if a claim is held. A run can still start out-of-band in OpenLab CDS on the instrument PC — surfaced as `busy`, not preventable. |
| `agilent_biostack` | Yes | **Yes** (X-Claim-Token → 423, verified 2026-08-03) | It grew a plate-moving `/control/*` surface (`home`, `stage_plate`, `present_plate`, `handoff`) at device v0.2.0, so it is no longer the harmless read-only row it used to be. Enforcement is now confirmed: a tokenless `POST /control/present_plate` returned **423 ahead of the 412** staged-plate interlock, with the device unchanged and no `last_error` — the probe was chosen precisely because that action is interlock-blocked, so it could not have moved a plate even had the claim check failed open. Rejected if a claim is held; cooperative + un-audited via direct `curl`, like every other row. |
| pypoe | Yes | read-only, no control surface | n/a |

**The xArm has partly closed this on its own (2026-08-11), and it cost us.**
That device now refuses `/control/claim` without a device-accepted credential —
step 2 of the plan below, realised device-side rather than at the edge. The
dashboard, however, presented only the trusted-edge headers
(`X-Auth-User` + `X-Edge-Auth`) whenever `DEVICE_EDGE_SHARED_SECRET` was set,
which is always in production, and the deployed arm does not honour them. With
no fallback, *every* dashboard-mediated action on that arm failed 401 — tiles,
the workflow executor, and the assistant's Authorize button. Fixed in
`152a87c` (fall back to the operator's own credential on 401); the executor
path in `workflow.py` still takes the single-credential route and is untouched.
Full evidence in [`EQUIP_STATUS.md`](EQUIP_STATUS.md) §10.

Two things this table does **not** yet know:

- ~~**Which credential the xArm accepts**~~ — **answered 2026-08-12.** It
  honours the same `X-Auth-User` + `X-Edge-Auth` the passthrough already sends;
  the secret is **per device** (Caddy injects `XARM_EDGE_SHARED_SECRET`) and the
  dashboard was sending one device-agnostic value that did not match. Per-equipment
  resolution shipped the same day (`edge_secret_env` on the registry entry), after
  the interim single-value stopgap was found to have been holding the *OT-2's*
  secret. Measured 2026-08-12: the OT-2 gateways gate `/control/*` on the claim
  alone and never check identity, so the arm is the only device where this is
  load-bearing today; the rest of the fleet is unprobed for the reason below. See
  [`AUTH_DESIGN.md`](AUTH_DESIGN.md). Related: those edge secrets are readable by any
  local user via `systemctl show caddy.service -p Environment` (same doc).
- **Whether any other device is login-gated.** Deliberately not probed: the
  gate sits on claim *acquisition*, so finding out means requesting a claim,
  which has a side effect on live hardware. A heartbeat probe with an invalid
  token — which is side-effect-free — returned ordinary claim-token errors from
  all eleven control-capable devices, but that only proves heartbeat is
  ungated, not claim. Answer it deliberately, per device, not with a sweep.

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
   ✅ **comfortably met** — 24 of 30 entries are on `adapter: http`, all
   responding live (the remaining six are `mock` placeholders).
2. **`agilent-plateloc-server` is at `protocol: "1.1"`.** ✅ **met, and
   exceeded** — plateloc has been live on v1.2 since 2026-07-30; thirteen
   other devices also reach v1.1 or better.
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
