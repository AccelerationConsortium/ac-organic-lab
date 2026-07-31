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

**Fleet snapshot.** Zero `legacy_http` left in `equipment.yaml` — LG2 holds.
`adapter: mock` is **no longer zero**: 25 of 30 entries are `http`, and five
are `mock` (three of the four `env_*` zones, plus `laagente_analitica` and
`bitacora_eln`, which were onboarded as placeholder tiles). Nothing is
`enabled: false` and no entry carries a `maintenance:` block.

All thirty entries answered the 2026-07-30 sweep with no `fetch_error`,
broken down as: seventeen real-hardware devices (`cam_hte_tapo_c245`,
`cam_echem_tapo_c245`, both `plug_hte_strip_*`, `fume_hood_actuator`,
`xarm_translocation`, `ot2_hte`, `ot2_complexation`, `dose_every_well`,
`torry_pines_shaker`, `filter_every_well`, `plateloc`, `cytation_5`,
`agilent_uplc_ms`, `agilent_biostack`, `bambu_p1s_01`, `bambu_h2d_01`),
nine service / gateway tiles (`pypoe_web`, `kasa_tapo_gateway`,
`bambu_gateway`, `uptime_kuma`, `laagente_analitica`, `bitacora_eln`,
`analytica_db`, `ac_organic_lab_api`, `ac_organic_lab_auth`), and the
four `env_*` environmental zones — of which **`env_hte` is now real
hardware** (see below); the other three remain synthesised. Note that
"answered" is weaker than "reports real hardware" for the five remaining
`mock` entries — a mock adapter always answers.

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

**Protocol mix** (live `/status` envelopes, 2026-07-30 sweep, `env_hte` added
2026-07-31). Registry `protocol:` agrees with the wire for every `adapter:
http` entry — no drift. The three mock `env_*` zones are the one deliberate
exception: their registry entries carry no `protocol:` (defaulting to `1.0`,
since they are placeholders for undeployed hardware) while their synthetic
envelopes mirror the live zone's v1.2 shape. Nothing reads a mock's version,
and the `adapter: mock` marks them simulated either way.

| Live version | n | Devices |
|---|---|---|
| **1.2** | 8 | `plateloc`, `xarm_translocation`, `torry_pines_shaker`, `ot2_hte`, `ot2_complexation`, `bambu_p1s_01`, `bambu_h2d_01`, `env_hte` |
| 1.1 | 7 | `fume_hood_actuator`, `dose_every_well`, `filter_every_well`, `cytation_5`, `agilent_uplc_ms`, `agilent_biostack`, `pypoe_web` |
| 1.0 | 15 | both `cam_*`, both `plug_hte_strip_*`, `kasa_tapo_gateway`, `bambu_gateway`, the six service tiles, the three remaining mock `env_*` zones |

The v1.2 devices consume the shared `sdl-lab-contract` package instead of
a vendored `models.py`. Migration dates: `torry_pines_shaker` (live
2026-07-25), `plateloc` (merged 2026-07-26; **deployed and verified live
2026-07-30** — device v1.4.0, `activity`, and `cycles_total: 1862`
mirroring the instrument odometer all present on the wire),
`xarm_translocation` (device repo commit c91dd05, verified live
2026-07-30), plus both OT-2 gateways and both Bambu printers.

The shared-package threshold ("3+ repos cleanly on v1.1 for ~1 month") is
comfortably cleared. The Appendix B v2 gate additionally needs the *majority*
of the fleet reporting `activity`: **8 of the 15 v1.1+ devices** — a bare
majority, crossed on 2026-07-31 when `env_hte` went live.

Treat that as met-on-a-technicality, not as the gate opening. `env_hte` is a
passive sensor whose `activity` is permanently `idle`; it demonstrates nothing
about the fleet's ability to absorb a contract migration, which is what gate
criterion 2 is actually testing. The six *actuating* v1.1 stragglers are the
real measure, and all six are still outstanding: `fume_hood_actuator`,
`dose_every_well`, `filter_every_well`, `cytation_5`, `agilent_uplc_ms`,
`agilent_biostack`. (`pypoe_web` is the seventh v1.1 entry, but it is a
read-only web service with no primary operation, so v1.2 would add nothing.)
Gate criterion 2 also requires the §2.3.2 reader-side derivation to be
deleted — already true since 2026-07-25. The v1.0 group is mostly
gateway-fronted or presentation-only tiles where `activity` has no meaningful
referent.

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
| `torry_pines_shaker` | `http` | **1.2** | ✅ `ready`/`degraded` | First native v1.2 device (2026-07-25): motor-observed `activity`, `cycles_total`, per-subsystem `allowed_actions`. Poll timeout restored to 10 s (read-off-lock fix deployed). The heater RTD `cal` fault recurs intermittently — now reported honestly as `degraded` without blocking shakes. |
| `filter_every_well` | `http` | 1.1 | ✅ `ready` | v1.1 deployed on the Pi at `100.64.254.104`; PressTile per-direction `hold_time` inputs verified (EQUIP_STATUS.md §8). |
| `plateloc` | `http` | **1.2** | ✅ `ready` | v1.2 **deployed and live 2026-07-30** (device v1.4.0, PR #2): seal-cycle `activity`, `cycles_total` mirroring the instrument odometer (live value 1862, equal to `cycle_count`), three §6 interlocks (stage / health / temperature) all mirrored in `allowed_actions`, `equipment_version` populated. Real claims (TTL `ClaimStore`, commit fa98ca8) verified 2026-05-31; 423 ahead of the 412s. Reader-visible behaviour changes noted below the sub-tasks. |
| `cytation_5` | `http` | 1.1 | ✅ `ready` | 13 actions advertised; device-repo Phases 3+4 shipped; Phase 2 (per-well tracking) remains. |
| `agilent_uplc_ms` | `http` | 1.1 | ✅ `ready` | v1.1 sidecar with hard claims and the `workflow.start`/`end` campaign lock; the sidecar owns the queue. Detail in the sub-task below. |
| `agilent_biostack` | `http` | **1.1** | ✅ `ready` | No longer read-only: device v0.2.0 now serves the claim trio + `/control/{startup,shutdown,home,stage_plate,present_plate,handoff}`, advertising `["shutdown","home","stage_plate","handoff"]` live. Real hardware (`details.com_port: COM8`, `bench_validated: 2026-05-29`), not dry-run. Not yet exercised end-to-end from a workflow or a dashboard tile — see the sub-task. |
| `pypoe_web` | `http` | 1.1 | ✅ `ready` | Internal web service; no control surface. |
| `analytica_db` | `http` | 1.0 | ✅ `ready` | Record-layer tile; STATUS_SPEC `/status` envelope live alongside the data API. |
| `env_hte` | `http` | **1.2** | ✅ `ready` | **Live 2026-07-31.** `sense-every-zone` gateway on `sdl2-pi0-environ-01:8030`, `status_path: /zones/env_hte/status`; SEN55 + PiSugar 3, both components `ready`. Monitoring-only, so v1.2 via the §9 read-only clause (`allowed_actions: []`, `activity` always `idle`). Reached over a DERP relay — `poll_timeout_seconds: 8.0`, observed latency ~120 ms. |
| `env_storage`, `env_lab499_west`, `env_lab499_east` | `mock` | 1.0 | dry_run | Awaiting hardware (`sdl2-pi0-environ-02` enrolled but offline). Synthetic envelopes mirror `env_hte`'s metric shape so readers take one path. |

### Remaining migration work (priority order)

1. ~~**`agilent_biostack` (plate_stacker) `/control/*` surface**~~ —
   **shipped** (device v0.2.0, live v1.1 as of the 2026-07-30 sweep): the
   claim trio plus `/control/{startup,shutdown,home,stage_plate,
   present_plate,handoff}`. The xArm is no longer the lab's only
   plate-mover on paper. What remains is *operationalising* it, the same
   gap the xArm has: no dashboard tile controls, and no workflow has
   driven it through `execute_plan` yet. Skill names *do* line up — all
   six `plate_stacker` SkillDefs (`startup`, `shutdown`, `home`,
   `stage_plate`, `present_plate`, `handoff`) match the device's
   endpoint/`allowed_actions` names exactly, so this device does **not**
   have the xArm's mismatch problem (item 2c).
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

#### `sense-every-zone` (environmental sensors)

Zone `env_hte` is live and registered (see the fleet notes above). Open:

- [ ] **Deploy the metric-key rename to the Pi.** Device commit `7f22bf9`
  (unsuffixed `metrics` keys) is pushed to `main` but **not yet running** —
  the node still serves `temperature_c` / `humidity_rh` / `voc_index`, which
  no reader now looks for, so the HTE map marker and its history series stay
  empty until this lands. On `sdl2-pi0-environ-01`: `git -C
  /opt/sense-every-zone pull && sudo systemctl restart sense-every-zone`,
  then confirm `curl -s
  http://127.0.0.1:8030/zones/env_hte/status | grep -o '"temperature"'`.
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
| `xarm_translocation` | Yes | **Yes** on `/control/*`; native `/web/` claim-awareness **unverified** | The `/web/` side-door is still advertised by the tile deep-link — the single most exposed control path in the lab |
| `agilent_uplc_ms` | Yes | **Yes** (X-Claim-Token → 423) | Rejected if a claim is held. A run can still start out-of-band in OpenLab CDS on the instrument PC — surfaced as `busy`, not preventable. |
| `agilent_biostack` | Yes | Claim trio served; **hard enforcement unverified** | **New exposure as of device v0.2.0** — it grew a plate-moving `/control/*` surface (`home`, `stage_plate`, `present_plate`, `handoff`) after this table was written, so it is no longer the harmless read-only row it used to be. Whether a tokenless POST is refused with 423 has not been probed. Verify before a workflow relies on it. |
| pypoe | Yes | read-only, no control surface | n/a |

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
