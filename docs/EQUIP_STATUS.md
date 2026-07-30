# Equipment Status — current per-device tile implementations

**Companion to [`EQUIP_GUIDE.md`](EQUIP_GUIDE.md).** That doc is the durable
*guideline* — how to onboard, maintain, and lay out equipment, and the control-
lock policy. **This** doc is the *current implementation detail*: exactly how
each device's dashboard tile is built today, its status derivation, control
passthrough, and per-device troubleshooting. It changes as tiles evolve; the
guide does not. Section numbers continue from the guide (§1–§6b there, §7–§11
here) so existing cross-references keep resolving.

## 7) Fume hood (`kind: fume_hood`)

The fume-hood sash actuator (`fume_hood_actuator`, FastAPI on
`100.64.254.100:5000`) conforms to STATUS_SPEC v1.1. It renders as a
kind-specific `FumeHoodTile` with the 5 sash presets as a row of
horizontal pills (1 = closed / "LOW" on the left, 5 = fully open /
"HIGH" on the right), and exposes click-to-move and stop controls
behind a lock toggle.

### Tile behaviour

- **Pill states**
  - Solid emerald = current preset (sash parked at hall sensor N).
  - Amber pulse = optimistic target while the device reports `busy`.
  - Dimmed = idle / not the current position.
- **No pill lit** = sash is between hall sensors. The device has no
  encoder readback, so position is only known at the 5 hall triggers.
  The device surfaces this as `equipment_status: requires_init`
  rather than masking it.
- **Lock toggle** in the header (matches the power-strip tile,
  10-second auto-relock). Pills and the Stop button are disabled
  while locked.
- **Stop button** appears only while the device is `busy`. It POSTs
  to `/control/sash/stop` and the tile drops its optimistic target.

### Status derivation (device side)

`status_builder.py` on the device derives `equipment_status` from
physical signals — it is the single source of truth for both
`/status` and `/control/sash/*`'s precondition gates, so STATUS_SPEC
§6.2's mirror invariant holds by construction.

| `is_moving` | `sash_position` | `equipment_status` | `allowed_actions`              |
|-------------|------------------|--------------------|--------------------------------|
| `true`      | any              | `busy`             | `["sash.stop"]`                |
| `false`     | `1..5`           | `ready`            | `["sash.move", "sash.stop"]`   |
| `false`     | `null`           | `requires_init`    | `["sash.move"]`                |

The device's pre-spec `equipment_status: "stopped"` string has been
removed; the new device reports the values above directly.

### Control passthrough

Both controls flow through the generic
`/api/equipment/{id}/control/{action}` route (`api/app/control.py`),
which handles the v1.1 claim/heartbeat/release dance per request:

- `POST /api/equipment/fume_hood_actuator/control/sash/move` body `{"position": 1..5}`
- `POST /api/equipment/fume_hood_actuator/control/sash/stop`  body `{}`

The dashboard acquires a short-lived claim as
`owner: ac-organic-lab-dashboard`, attaches the `X-Claim-Token` to
the action, then releases in a `finally` block. Workflows that need
exclusive control should keep using `lab_skills.ClaimManager`; a
workflow's longer-lived claim will cause the dashboard's per-request
claim to 409, surfacing `claimed_by` to the browser.

### What goes wrong (and how to spot it)

| Symptom                                       | Likely cause                                              | Fix                                                          |
|-----------------------------------------------|------------------------------------------------------------|--------------------------------------------------------------|
| Tile is `requires_init` after a successful move | Sash overshot/undershot a hall sensor                    | Click any pill again; the controller's "search by pulsing down" routine relocates within 5 pulses |
| Pill click does nothing visible               | Controls still locked                                     | Click the **Locked** chip to unlock (5 s window before auto-relock) |
| 422 from `/control/sash/move`                 | Position outside 1..5                                     | Pydantic validation on the device; tile only emits 1..5, so this only happens via direct API calls |
| 423 from `/control/sash/{move,stop}`          | Another workflow holds a longer-lived claim               | Refresh `/api/equipment` — `details.claimed_by.owner` shows who has the claim. Wait for them or release it via the SDK. |
| 504 from `/control/sash/{move,stop}`          | Device unreachable (Tailnet, Pi power)                    | `curl http://100.64.254.100:5000/health` from the dashboard host |

## 8) Filtration press (`kind: press`)

The Waters PP96 filtration press (`filter_every_well`, STATUS_SPEC
v1.1 on `100.64.254.104:8000`) renders as a kind-specific
`PressTile`. Two rows of click-to-move pills: **Press** (UP/DOWN) and
**Plate** (IN/OUT), plus state-aware **Init** and **Stop** buttons.
Each press pill has a paired numeric input for the `hold_time` the
device should energise the pneumatic valve.

### Tile behaviour

- **Press row pills** — UP and DOWN. Solid emerald = current valve
  position (read from `components.press_valve.state`). Amber pulse =
  optimistic target while `equipment_status: busy`. Dimmed = idle /
  not the current position.
- **`hold_time` inputs** — one numeric box next to UP, one next to
  DOWN. Range 0.0–10.0 s, step 0.5 s. Defaults:
  - **UP: 2.0 s** (brief retract after seating)
  - **DOWN: 5.0 s** (typical seating press for a filtration cycle)
  These mirror the `PressUpArgs` / `PressDownArgs` defaults in
  `skills/.../skill_catalog/press.py`, so SDK workflows that omit
  `hold_time` see the same numbers the dashboard sends.
- **Plate row pills** — IN and OUT, identical rendering rules but no
  `hold_time` parameter (the device takes `smooth: bool` only).
- **Lock toggle** in the header (10-second auto-relock). Pills, inputs,
  Init, and Stop are all disabled while locked. Inputs are *also*
  disabled while `equipment_status: busy`, so the operator can't
  change the planned hold time mid-cycle.
- **Init button** appears only when `equipment_status:
  requires_init`. Calls `POST /control/startup`; the device moves
  press → UP, plate → OUT, system → ACTIVE.
- **Stop button** appears in `ready` or `busy`. Calls
  `POST /control/stop`; re-init is required afterwards.

### Status derivation (adapter side)

Press uses the standard `http` adapter (no kind-specific adapter). The
device reports the spec envelope verbatim. `components.press_valve` is
the authoritative source for the current position; the tile falls back
to `details.press_state` for legacy responses.

### Control passthrough and claim handling

All buttons hit the generic passthrough at
`POST /api/equipment/filter_every_well/control/{action}` in
`api/app/control.py`. Because the device enforces
`ENFORCE_CLAIMS=True` (returns HTTP 423 without
`X-Claim-Token`), the passthrough automatically:

1. POSTs `/control/claim` as `owner: ac-organic-lab-dashboard`
2. Attaches `X-Claim-Token` to the actual action call
3. POSTs `/control/release` in a `finally` block

The full dance is per-request — there is no long-lived dashboard
claim. Workflows that need exclusive control should keep using
`lab_skills.ClaimManager`; a workflow's longer-lived claim will cause
the dashboard's per-request claim to 409, surfacing `claimed_by` to
the browser so the operator knows the device is busy.

The dashboard's per-request httpx timeout is set to 15 s
(`_CONTROL_TIMEOUT_SECONDS` in `control.py`) so that a 10 s
`hold_time` plus the claim/release round-trips never 504 while the
device is still working.

### What goes wrong (and how to spot it)

| Symptom                                              | Likely cause                                              | Fix                                                                  |
|------------------------------------------------------|-----------------------------------------------------------|----------------------------------------------------------------------|
| All pills greyed even after Unlock                  | `equipment_status: requires_init`                         | Click **Init** first; pills enable after the device reaches `ready`. |
| Pill click does nothing visible                      | Controls still locked                                     | Click the **Locked** chip to unlock (5 s window before auto-relock). |
| Click returns HTTP 423                               | The dashboard's claim acquisition lost the race           | Refresh `/api/equipment` — `details.claimed_by.owner` shows who has the claim. Wait for them or release it via the SDK. |
| 504 on a long `hold_time`                            | Bumped device hold beyond the 15 s budget                 | `hold_time` is hard-capped at 10 s by the device; values above that are clamped client-side by the input. |
| 422 from `/control/press/{up,down}`                  | `hold_time` outside 0..10 s                               | Pydantic validation; the tile clamps to that range client-side, so this only happens via direct API calls. |
| Tile pills stuck on amber pulse for tens of seconds | Device crashed mid-move and didn't transition back to `ready` | `curl http://100.64.254.104:8000/status` from the dashboard host; if it says `error`, restart the device service. |

## 9) Plate sealer (`kind: plate_sealer`)

The Agilent PlateLoc (`plateloc`, STATUS_SPEC **v1.2** since device
v1.4.0 / 2026-07-26) renders as
`PlateSealerTile`. A 2×2 metric grid (Actual / Setpoint /
Seal time / Cycles), editable Setpoint + Seal time inputs, and
state-aware action buttons (Startup, Stage in/out, Seal start,
Seal stop, Shutdown). The **Stage in** / **Stage out** buttons are
rendered as `PositionPill`s (same pattern as `PressTile`'s plate
IN/OUT pills): whichever pill matches the live
`components.stage.state` is highlighted emerald — no separate Stage
indicator row.

### v1.2 activity (device v1.4.0)

`activity` is the **seal cycle** — the device's primary operation — and it
is what the tile's second status dot reflects (`StatusDots`). Three things
follow for anyone reading this tile:

- **A `busy` tile is a lucky catch, not the norm.** The ActiveX control runs
  blocking, so the cycle is exactly the span of the
  `POST /control/seal/start` request (0.5–12 s) — usually over before the
  next 60 s poll. Device v1.4.0 fixed the older behaviour where
  `_busy_state` latched *after* the call and only an explicit `seal/stop`
  cleared it, which could park the tile at `busy` with nothing running.
- **Utilization must come from `metrics.cycles_total`**, not from sampling
  `activity` (STATUS_SPEC §2.3.1). It mirrors the instrument's lifetime
  odometer, so the poll-to-poll delta recovers cycles the aggregator slept
  through. `cycle_count` is the same number, kept for existing readers —
  the **Cycles** cell can read either; prefer `cycles_total` for new code.
- **A poll during a cycle is answered, not queued.** The device reads the
  instrument off its state lock now; while a cycle owns the COM channel the
  envelope serves the last observation and says so via
  `details.readings_as_of` (and matching metric timestamps). Treat those
  values as up to one cycle stale, not live.

While `activity == "running"` the device advertises only
`["shutdown", "seal.stop"]` and answers **409** to everything else —
`seal/start`, `stage/{in,out}`, `seal/temperature`, `seal/time` — so the
tile's buttons and the device agree without extra client logic.

### Seal-start interlocks (defence in depth)

`Seal start` is enforced at **three layers**, all on by default, across
**three independent preconditions**:

1. **Temperature-band interlock** — `|actual_temperature −
   setpoint_temperature| ≤ details.temperature_tolerance_c`
   (plateloc v1.2+).
2. **Stage-position interlock** — `components.stage.state == "in"`
   (plateloc v1.3+). The plate must be loaded under the press.
3. **Health interlock** — no `last_error` inside the device's 60 s
   recent-failure window (plateloc v1.4+). §2.2: don't start a normal run
   while the device knows of an active fault. Clears the §6.4 way — the
   first 2xx from any operational endpoint — so in practice the operator's
   re-home click clears it.

All are enforced at all three layers:

1. **Device (layer 1, authoritative).** Plateloc v1.2+ / v1.3+ refuses
   `POST /control/seal/start` with **HTTP 412 Precondition Failed**.
   Two distinct body shapes depending on which interlock fires first
   (the device checks stage before temperature):

   *Stage interlock body* (v1.3+):
   ```json
   {
     "detail":      "Stage not loaded",
     "stage_state": "out",
     "required":    "in"
   }
   ```

   *Temperature interlock body* (v1.2+):
   ```json
   {
     "detail":         "Temperature outside seal band",
     "actual_c":       166.0,
     "setpoint_c":     170.0,
     "tolerance_c":    2.0,
     "retry_after_s":  2
   }
   ```
   *Health interlock body* (v1.4+):
   ```json
   {
     "detail":             "Recent operational failure not cleared",
     "last_error_code":    "low_air_pressure",
     "last_error_message": "StartCycle returned error code -2147221503 (driver: Low Air Pressure Error)",
     "retry_after_s":      47
   }
   ```

   The temperature and health 412s also carry a `Retry-After` header
   (seconds, integer); the stage 412 does not (recovery is operator-driven,
   not time-based). The temperature and stage gates are bypassable via the
   device-side config flags `[service].enforce_temp_interlock = false` and
   `[service].enforce_stage_interlock = false` respectively, both reserved
   for emergency calibration runs. The health gate has **no** flag — §2.2
   requires it.

   Order of precedence at the endpoint (the tile should match it): **stage
   → health → temperature**. The first two are in-memory, the third needs
   COM reads.

   > **Behaviour change in device v1.4.0.** `error` / `degraded` used to
   > collapse `allowed_actions` to `["shutdown"]` while the endpoints still
   > honoured stage moves — both a §6.2 violation and, in the 2026-07-15
   > air failure, the reason the tile offered no way to retract a plate
   > from a hot chamber. Recovery and diagnostic actions now stay listed in
   > those states; the run is withheld by the health interlock instead. So
   > a tile in `error` legitimately shows **Stage in / Stage out /
   > Shutdown** enabled and **Seal start** disabled.

2. **SDK (layer 3, `lab-skills`).** The `seal.start` SkillDef carries
   `requires_components={"heater": "stable", "stage": "in"}`.
   `lab.skills()` reports `available=False, reason="component
   '<name>'.state='<actual>'; requires '<wanted>'"` whenever either
   gate fails, so workflow code sees the same precondition before it
   tries the call. The catalog gate is an AND condition layered on
   top of `allowed_actions` / `requires_states` — see
   [`docs/SKILLS_CATALOG.md`](SKILLS_CATALOG.md) for the field shape.
   `_availability` iterates in insertion order (heater first, then
   stage), so the surfaced reason is whichever check fires first.

3. **Dashboard tile (UX safety net).** `PlateSealerTile` disables the
   **Seal start** button when it can compute either precondition
   itself:
   - **Temperature** — needs `details.temperature_tolerance_c`,
     `actual_temperature`, `setpoint_temperature` all present.
     Falls through to the device's 412 if any are missing.
   - **Stage** — needs `components.stage.state`. Falls through if
     the device doesn't publish the component at all (older
     firmware). The client-side `DEFAULT_TOLERANCE_C` fallback was
     retired in 2026-05-23 once the device started publishing
     tolerance unconditionally.

The dashboard tile also independently checks the device's
`components.heater.state == "stable"` as a secondary heater signal —
catches disconnected/error cases that fell inside the band by luck.

When blocked, the tile shows the reason in three places, all from the
same `sealStartTitle` string. The string is computed in priority
order: **stage → temperature → heater** — matching the device's 412
precedence so the tile and the device agree on which interlock is the
"current" reason.

- **Seal start** button tooltip on hover.
- **Actual** pill turns amber whenever the temperature interlock
  is the active block, overriding the device-side heater tone.
- **Stage in** / **Stage out** pills: whichever matches the live
  `components.stage.state` glows emerald; the other sits neutral.
  When `stage.state == "unknown"` (fresh restart, or after a
  mid-cycle failure) **neither** pill is highlighted — the operator
  needs to click one to home before sealing.
- **Footer-left text** replaces `status.message` (which otherwise reads
  the device's verbatim *"Idle, ready to seal"* — true from the device's
  perspective but misleading when the dashboard's interlock blocks the
  click). The footer falls through to the device message whenever the
  dashboard's gate isn't the bottleneck (e.g. `requires_init` or
  `busy`).

### Inline error band (412 / 423 / 409)

The sealer tile renders an amber inline message below the action
buttons when an action returns one of these structured errors:

| Status | Source | Example rendered text |
|---|---|---|
| **412 / stage** | layer-1 stage interlock (race past the tile's block) | *"Plate stage is out, needs to be loaded. Click \"Stage in\" first."* |
| **412 / temperature** | layer-1 temperature interlock (race past the tile's block) | *"Heater at 166 °C, need 170 ±2 °C. Try again in ~2 s."* |
| **423** | claim conflict — another caller holds the device | *"Device claim is held by workflow:solubility. Try again later."* |
| **409** | device-state conflict (not initialised, **or** a seal cycle in flight) | *"Driver not connected. Click Startup first."* |

**Open work (device v1.4.0, 2026-07-26):** two shapes the tile does not yet
have copy for.

| Status | Source | Suggested text |
|---|---|---|
| **412 / health** | the new health interlock; distinguish by `last_error_code` being present in the body | *"Clear the last fault first — <recovery hint for that code>. Retrying in ~47 s also clears it."* Reuse `interpretLastError`'s copy table, keyed off `last_error_code`. |
| **409 / cycle in flight** | `detail` contains *"seal cycle is in progress"* | *"A seal cycle is running. Wait for it to finish, or click Seal stop."* Today this falls through to the generic 409 branch, which says "Driver not connected" — actively misleading. |

Until that lands, `interpretActionError` renders the generic 409 copy for a
mid-cycle refusal. Fixing it is a small change in
`PlateSealerTile.interpretActionError`.

The 412 / 409 / 423 paths are differentiated in
`PlateSealerTile.interpretActionError` and the structured body is
parsed there. Auto-clear policy: the band clears on (a) the next click,
or (b) the next `/status` poll that observes `equipment_status: ready`
and the band/heater interlock satisfied.

### `last_error` band (v1.3.1+)

A separate **rose-toned** band renders above the action buttons
whenever `status.last_error` is non-null. Distinct from the amber
refusal band so the operator can tell at a glance whether the message
is "hardware reported a fault" (rose) or "your action was refused"
(amber). Both can be visible simultaneously.

Branching is done in `PlateSealerTile.interpretLastError` on the
`last_error.code` taxonomy plateloc shipped in v1.3.1:

| `code` | Rendered recovery text |
|---|---|
| `low_air_pressure` | *"Air supply low. Check the regulator at ~80 psi."* |
| `com_init_failed` / `com_timeout` | *"Driver unresponsive — restart the device service."* |
| `profile_not_found` | *"Open the Diagnostics dialog on the device PC and create the profile."* |
| `stage_jam` | *"Stage move failed. Check the carriage path, then re-home with Stage in / Stage out."* |
| `heater_overtemp` / `heater_undertemp` | *"Heater fault — service required."* |
| `process_internal` | *"Lab-software bug — please file an issue."* |
| `com_other` | *"Driver fault — see message."* |
| missing / null / unknown code | Raw `last_error.message` rendered verbatim (back-compat for pre-v1.3.1 / forward-compat for new codes) |

**v1.4.0: `last_error` can now be a warning, not only a failure.** A failing
instrument *readback* (which drives `equipment_status: "degraded"`) is
surfaced as a `last_error` with `severity: "warning"` and a classified code —
previously it reached the dashboard only as free text in `message`. An
operational failure always takes precedence over the synthesized one. The
tile currently renders both identically in the rose band; toning `warning`
differently from `error` is optional polish, but the band should **not** be
read as "the last action failed" when `severity == "warning"`.

The device's verbatim driver message is always shown after the
recovery sentence (dimmed) and as the hover `title` attribute on the
whole band — operators can still inspect the underlying error code
when filing a ticket.

**Auto-clear is device-driven.** Plateloc v1.2.1+ clears
`last_error` to `null` on the first 2xx response from any operational
`/control/*` endpoint (per [`docs/STATUS_SPEC.md`](STATUS_SPEC.md)
§6.4), so the rose band naturally goes away the next time the
operator does something that works. The dashboard does no
client-side clearing.

When `last_error.code` is a value not in the table above, that is the
**operational watch signal**: it usually means plateloc started
emitting a new failure mode the dashboard hasn't grown copy for yet.
Add a branch in `interpretLastError` (paired with copy in this
table) when that happens.

### What this interlock does NOT cover

- **Air-pressure faults, proactively.** The 2026-05-23 / 2026-07-15
  symptom (`Low Air Pressure Error` from the pneumatic press inside
  the sealer) needs a facility-level sensor; the device has no
  pressure introspection, so the *first* failure is always discovered by
  attempting the operation. The dashboard surfaces it post-hoc via
  `last_error`. Since device v1.4.0 the *second* attempt is prevented (the
  health interlock refuses `seal.start` until the fault clears) and the
  pneumatic retract stays advertised so the operator can try to recover the
  plate — but if air is genuinely gone, `stage.out` fails too, and that is
  a facilities problem, not a software one.
- **Cross-device chemistry interlocks.** Sealing at 170 °C with a
  flammable solvent below its flash point belongs in layer 4 (project
  plan interlocks); see [`docs/INTERLOCKS.md`](INTERLOCKS.md).

## 10) Robot arm (`kind: robot_arm`)

The UFactory xArm5 (`xarm_translocation`, STATUS_SPEC **v1.2** since
device repo commit c91dd05 / 2026-07-30 on
`sdl2-pc-03-cytation.tail6a1dd7.ts.net:8000`) renders as the
kind-specific `RobotArmTile`. As of **2026-05-31** the device exposes a
**claim-gated motion-graph control surface** (see below); the *tile*,
however, is still read-only — three single-line component summaries plus
the lock chip and an "Open control panel ↗" deep-link to the device's
own `/web/` UI. Surfacing the graph controls in the tile is open work.

### Device control surface (2026-05-31)

The xArm gateway now implements the v1.1 claim protocol and a motion-graph
control surface (confirmed via the device's `/openapi.json`):

| Endpoint | Skill (`robot_arm` catalog) | Body |
|---|---|---|
| `POST /control/graph/move_to` | `graph.move_to` | `node_id`, `speed?` |
| `POST /control/graph/recover_to` | `graph.recover_to` | `node_id`, `force=false` |
| `POST /control/graph/record` | `graph.record` | `mode?`, `speed?`, `comment?`, `preconditions?` |
| `POST /control/graph/mode` | `graph.mode` | `mode` (`off`/`advisory`/`strict`) |
| `POST /control/{claim,heartbeat,release}` | — | claim protocol |
| `POST /control/claim/enforce` | — | runtime enforce toggle |

Notes:
- The old `stop` endpoint was **retired**; motion is now expressed as moves
  between named nodes in a motion graph.
- Control is **connect-gated**: while the arm is disconnected (`requires_init`)
  the device refuses `/control/claim` with `400 "connect first"`, so the full
  claim/enforcement lifecycle can only be exercised after `POST /connect`.
- The matching SkillDefs are registered in
  `skills/src/lab_skills/skill_catalog/robot_arm.py`; `equipment.yaml` keeps
  `do_not_call_connect: true`, so the SDK never auto-connects — availability
  flows from the device's `allowed_actions` once connected.

### v1.2 activity and concurrent-move refusal (device commit c91dd05)

`activity` / `activity_since` are **observed from the controller's motion
state** — not derived from `equipment_status` — so the tile's `StatusPill`
(health) and the activity signal are now independent axes (STATUS_SPEC
§2.3). `allowed_actions` mirrors the controller: while
`activity == "running"` every `move.<node_id>` target is withheld and only
`"stop"` remains. The tile derives `isBusy` from `equipment_status` today;
once it surfaces graph controls it should prefer `activity` for the
button-disable signal (§2.3.2's deletability promise — the device-side
source is authoritative).

Only one motion may be in flight at a time. A second concurrent move is
refused with **HTTP 409**:

```json
{
  "detail": {
    "error": "motion_in_progress",
    "message": "A motion is already in progress; stop it before issuing another move"
  }
}
```

This is **409, not 412**: it is a state conflict (§6.1), not a satisfiable
precondition. The distinction matters for the dashboard's
`interpretActionError` (below): 412 carries a `Retry-After` and a
device-specific recovery hint; 409 is "wait or stop, then retry." The
plateloc sealer established the same §6.1-vs-§6.2 split for its mid-cycle
conflicts (§9), so the xArm 409 is the **second device** in the fleet to
exercise the 409-vs-412 boundary — the pattern is now confirmed across two
device kinds (plate sealer + robot arm).

### Tile layout

Three rows, each leading with a `w-14` caption pill:

| Row | Cells |
|-----|-------|
| **Arm** | component state pill (`enabled` → emerald, `disabled` / `disconnected` → muted, `error` / `fault` → warn) · `TCP <mm/s>` · `Ang <°/s>` |
| **Gripper** | component state pill (tooltip shows the model, e.g. `bio_gen2`) · `Stroke <mm>` if `metrics.gripper_position` is published; otherwise `Range 71–150 mm` from the device's static `gripper_config.stroke_range` · `Force <N>` from `metrics.force_magnitude` when the wrist FT sensor is enabled; otherwise the configured grip force from `gripper_config.force` with a `cfg` suffix |
| **Track** | component state pill · `Pos <mm>` from `metrics.track_position` · `At <name>` in emerald when `details.motion_graph.rail_location_name` is non-null (track parked at a named rail location); otherwise `At —` muted |

The lock chip lives in the header and is the visible promise that
controls — once the tile surfaces the `graph.*` actions — will be gated.

### Why not a generic `EquipmentStatusCard`?

The previous tile rendered `MetricList` + `ComponentList` verbatim,
which produced six pills on top of four metrics — readable but noisy
and redundant against the device's `/web/` panel. The three-row layout
trades the generic introspection for a glanceable per-component
summary that maps to the operator's mental model (arm motion, gripper
state, track position).

### Open work

- **Surface graph controls in the tile** — the device + catalog now
  support `graph.move_to` / `recover_to` / `record` / `mode`, but
  `RobotArmTile` still renders read-only. Add control affordances that
  POST through the audited `/api/equipment/xarm_translocation/control/graph/*`
  passthrough; the lock chip then becomes load-bearing.
- **409 `motion_in_progress` copy** — when graph controls land, the
  generic `interpretActionError` 409 branch needs a device-specific
  specializer (or a `parse412`-style hook for 409s). The xArm 409 body is
  `{"detail": {"error": "motion_in_progress", "message": "…"}}` —
  `detail` is an **object**, not a string, so the current
  `typeof body.detail === "string"` check (action-error.ts line 51)
  yields `undefined` and the tile renders the generic "Action rejected."
  fallback. The fix: extract `body.detail.message` when
  `body.detail.error === "motion_in_progress"` and render
  *"A motion is already in progress. Click Stop, then retry."* Today
  this is academic — the tile only calls `connect` / `stop` / `clear`,
  none of which can trigger a 409. It becomes live the day the graph
  move controls ship (previous item). The plateloc sealer's 409
  mid-cycle copy (§9, "Open work" table) is the precedent for the
  same shape.
- **Prefer `activity` over `equipment_status` for button-disable** —
  the tile derives `isBusy` from `st === "busy"` today. Once the
  device's `activity` field is wired into `EquipmentSnapshot`,
  disable move buttons on `activity === "running"` instead — the
  device-side source is authoritative (§2.3.2).
- **Verify claim enforcement live** — once the arm is connected
  (`POST /connect`), confirm tokenless `/control/graph/*` → 423 and
  `details.claimed_by` population.
- **The `/web/` deep-link is the un-audited side-door** — driving the arm
  from the device's own panel bypasses the dashboard's claim + audit path.
  Make the native panel claim-aware or front it at the edge (see the
  *Control-surface exposure* section of [`docs/ROADMAP.md`](ROADMAP.md)).
- **Live `metrics.gripper_position`** — the device repo doesn't yet
  publish current stroke. The tile already prefers a live value over
  the static range; the slot lights up automatically once the device
  emits the metric.

## 11) Liquid handler (`kind: liquid_handler`) — OT-2

The Opentrons OT-2 renders as the kind-specific `LiquidHandlerTile`.
Each OT-2 also has a dedicated **full-page interface** at
`/equipment/<id>/control` (aliases `/ot2_hte`, `/ot2_complexation`) with the
full deck, declared-vs-observed state, module telemetry, tip tracking and
claim visibility — see [`UI_DESIGN.md`](UI_DESIGN.md) §1.
There are **two** OT-2s, each fronted by its own `opentrons-server`
gateway process (the gateway is multi-instance by design — one process
per robot, own port, own robot host, own state files):

- `ot2_hte` — HTE bench robot `sdl2-ot2-hte` (100.64.254.90, robot name
  `ot2cytation`), gateway on `sdl2-pc-03-cytation.tail6a1dd7.ts.net:8020`.
- `ot2_complexation` — Echem bench robot `sdl2-ot2-complexation`
  (100.64.254.91, robot name `ot2training`), gateway on `…:8021`
  (service `ot2-gateway-complexation`, deployed 2026-07-10; see
  `opentrons-server` README "Running multiple OT-2 gateways" — note the
  robot is reached over its lab-WiFi DHCP address, so if it goes
  `requires_init` with SSH errors, check the lease via
  `GET :31950/networking/status` and update `OT2_HOST_ALIAS`).

Both are STATUS_SPEC v1.1. The kind-level skill catalog and tile apply to
both automatically. The catalog ships typed Pydantic ``args_schema``s for
every advertised action: session lifecycle (``startup`` / ``shutdown``),
protocol execution (``setup`` / ``home`` / ``pick_up_tip`` / ``aspirate``
/ ``dispense`` / ``drop_tip`` / ``move_labware`` / ``pause`` / ``resume``),
plate/well tracking (``plate.load`` / ``plate.unload`` / ``well.update``),
tip tracking (``tips.reset`` — physical rack swap), and convenience
(``lights.set`` / ``deck.declare``). ``pick_up_tip`` carries a
cross-contamination guard (``sample_id`` / ``force`` on the args); refusals
return HTTP 412 and never mutate ``last_error``.

### Tile behaviour (read-only since 2026-07-15)

The tile is a **read-only summary**: a top row with a "Control interface →"
link + read-only light/pipette pills, a non-interactive 12-slot deck grid,
then the SSH / Protocol status pills (and any leftover `MetricList` /
`ComponentList`). **All control** — session lifecycle, lights toggle,
declaring deck intent — lives on the dedicated full-page interface at
`/equipment/<id>/control` (see [`UI_DESIGN.md`](UI_DESIGN.md) §1);
the tile carries no lock chip because it has nothing to gate.

**Top row** — the control-page link + three read-only pills:

| Element | Source | Behaviour |
|---|---|---|
| **Control interface →** link | — | Navigates to `/equipment/<id>/control`, where all OT-2 controls live. |
| **Light pill** with a state dot | `components.lights.state` (`on` / `off` / `unknown`) | Read-only indicator: dot is **amber (glowing)** when on, **black** when off, grey when unknown. The toggle is on the control page. |
| Left / right **pipette pills** | `components.pipette_left.state` / `pipette_right.state` | Model formatted (`p300_multi_gen2` → `P300 Multi`); left mount rendered first (position implies the mount, so no caption). Hover shows mount + raw model; empty mount shows `—`. |

**Deck grid** — 12 slots, 3 columns × 4 rows, numbered to match the
physical deck (**1 bottom-left, 3 bottom-right, 10 top-left, 12
top-right**). Blocks are a **fixed 160×120 px**; the grid keeps three
fixed columns and distributes extra width between them
(`justify-content: space-between`), so resizing the window only spaces
the blocks out horizontally rather than stretching them (scrolls if the
tile is narrower than three blocks). On the tile the grid is
non-interactive (hover tooltips only); slot selection happens on the
control page, where the same `DeckPanel` renders with a click handler.

**Deck source (2026-07 — device-driven, with fallback).** The tile
prefers the gateway's *own* normalized deck published at
`status.details.snapshot.deck` (see `opentrons-server`
`docs/DECK_STATE_PLAN.md`): each slot carries `{ labware: { kind,
load_name, rows, columns, … } | null, module, slot_state, source,
declared }`. `deviceDeckFromStatus()` accepts only this **new** shape
(deck-level `source` present *and* per-slot `slot_state`); a gateway
still on the old loose `deck` (or none) falls back to the shared
dashboard store (below). This is a graceful, per-device migration — no
flag day. Rendering by slot:

- **Empty** — a large, light-grey *watermark* slot number (centred).
- **Any well-plate / tip-rack / reservoir / tube-rack** — a miniature
  well grid drawn from the payload's `rows`/`columns` (falls back to a
  per-`kind` grid table when the device omits them), so the tile renders
  any grid generically rather than switching on a fixed enum.
- **Waste / trash** — the slot is greyed out (no wells), labelled `waste`.
- **Module / unknown-grid labware** — a small centred label.
- **State badges (migrated only, top-right):** `busy` when
  `slot_state == "in_use"`; `≠` (amber, with an amber slot border) when
  `slot_state == "mismatch"` — the operator declared one labware but the
  device observed another (hover shows both).

**Declare deck intent** — lives on the **control page** (searchable,
grouped picker over the central authored catalog: exact Opentrons
load_names, the four module keys, and the legacy generic kinds —
`web/src/lib/ot2-catalog.ts`). It sets **operator-declared intent**, not
observed state (it does not load labware or run setup — see
[`UI_DESIGN.md`](UI_DESIGN.md) §1), and writes via
`POST /control/deck/declare` (the `deck.declare` skill) through the
control passthrough — auth-gated, claim-danced and audited like any
control write. Only operator-declared slots are sent; observed labware is
left to the device. Declaring a slot that the device observes differently
surfaces as a `mismatch`.

**SSH + Protocol pills** — `components.ssh` and `components.protocol`
render as two side-by-side pills (dot green when `connected` / `ready`,
else grey; state text alongside). These plus `lights` /
`pipette_left` / `pipette_right` are all in `TILE_OWNED_COMPONENTS`
(`LiquidHandlerTile.tsx`), so they're filtered out of the generic
`ComponentList` to avoid duplication.

The lifecycle (`startup` / `shutdown` / `pause`) and lights controls that
used to live on the tile moved to the control page with the same gating:
lifecycle writes are lock-gated (sign-in + per-device role), lights stays
convenience-class (sign-in only; see [`EQUIP_GUIDE.md`](EQUIP_GUIDE.md)
§6b "Two layers, two bypass points").

### Deck-layout store (shared, server-persisted — legacy fallback)

For gateways that do **not** yet publish `details.snapshot.deck` (see the
device-source note above), the deck picker falls back to a shared,
server-persisted store in `api/app/deck.py`:

- `GET /api/equipment/{id}/deck` → `{ slots: { "<1..12>": "96-well" | "24-well" | "waste" } }`
- `PUT /api/equipment/{id}/deck` → replaces the layout (validates slot
  range 1..12 and labware against an allowlist), returns the cleaned map.

Persistence is a JSON file (`deck_layouts.json`) next to `lab.db` in the
data directory, written atomically under a lock. The tile loads it via
`react-query` (`queryKey: ["deck", id]`, `enabled` **only when the device
isn't migrated**), polls every 15 s, and writes optimistically.

**Retirement plan — cutover done 2026-07-10.** Both OT-2 gateways run
the deck-publishing build and were verified live, and the two
`deck_layouts.json` entries were migrated into each gateway's declared
store via `POST /control/deck/declare`. Both tiles therefore render from
device state; this store is dormant for the OT-2s. Remaining cleanup
(deliberately deferred): delete `api/app/deck.py` + `deck_layouts.json`
and remove the legacy fallback branch from `LiquidHandlerTile.tsx` once
the device-driven path has soaked. Until deleted, the store still serves
any future not-yet-migrated liquid handler.

> **Auth-gated (2026-07-09).** The `/deck` PUT is a control-class write, so
> it is gated exactly like `/control/*`: `deck` is in the middleware's
> `CONTROL_PATH_RE`, so an unauthenticated PUT is rejected at the edge (401),
> and the `deck.py` backend then runs a per-equipment authorization check
> (`GET /authz/check?user&equipment`, fail-closed) so only an **admin** or a
> user holding `operator`+ on *that device* (an "authorized OT-2 user") may
> change its layout — a non-authorized signed-in user gets 403. Each change is
> audited to `equipment_events` (`control_action`, `action: deck.set`) with the
> real actor. The GET stays a public read. The `LiquidHandlerTile` picker is
> disabled (with a "No access" / "Sign in to edit" hint) for users who lack the
> role, via the same `useControlLock(id)` → `/authz/mine` gate the control
> affordances use.

### Status derivation (device side)

`opentrons-server` polls `GET /robot/lights` on the OT-2's own HTTP
API and surfaces the result as `components.lights = {connected: true,
state: "on"|"off"|"unknown"}`. Whenever the robot is reachable,
`"lights.set"` appears in `allowed_actions` regardless of
`equipment_status` — lights work in `requires_init` just as well as
in `ready`.

### Control passthrough and claim handling

`POST /api/equipment/ot2_hte/control/lights` flows through the generic
passthrough in `api/app/control.py`, which handles claim acquire /
`X-Claim-Token` attach / release per request (the device enforces
`X-Claim-Token` on `/control/*`).

The `CONTROL_PASSWORD` middleware **does not** gate this path even
when the env var is set — `actionBypassesControlGate("lights")`
returns true so the POST goes through without a `control_auth`
cookie. This is intentional convenience-class behaviour, same
operator-facing class as camera PTZ. See [`EQUIP_GUIDE.md`](EQUIP_GUIDE.md) §6b for the matrix.

### What goes wrong (and how to spot it)

| Symptom | Likely cause | Fix |
|---|---|---|
| Buttons render but POST returns HTTP 423 | Another workflow holds a longer-lived claim on the OT-2 | `details.claimed_by.owner` on `/api/equipment/ot2_hte/status` shows who. Release via the SDK, then retry. |
| Buttons render but POST returns HTTP 404 | `opentrons-server` predates the `lights` endpoint | Update the gateway on `sdl2-pc-03-cytation:8020`. |
| Lights dot stuck on `—` | Device repo isn't publishing `components.lights` yet | Check `/api/equipment/ot2_hte/status` — if the component is missing, the gateway version is too old. |
| Lights dot reflects the wrong state | Browser tab has a stale `react-query` cache | Status refreshes on the next aggregator poll (~2 s); a hard refresh is also fine. |

### Open work

- **Protocol-execution skills** — add SkillDefs for `setup`, `home`,
  `aspirate`, `dispense`, `pick_up_tip`, `drop_tip`,
  `move_labware`, `pause`, `resume`, `reconcile`. These need labware-
  typed Pydantic args that the catalog has no shapes for yet.
- **Deck from device state** — ✅ *shipped and cut over 2026-07-10*: the
  tile reads `details.snapshot.deck` and declares via the `deck.declare`
  skill (`POST /control/deck/declare`); both gateways are live on the
  deck-publishing build and the stopgap layouts were migrated into their
  declared stores. **Remaining:** delete `api/app/deck.py` +
  `deck_layouts.json` and the legacy fallback branch in
  `LiquidHandlerTile.tsx` after a soak period.
- **Gate the `/deck` PUT** behind the sign-in middleware if the shared
  layout needs write protection.
