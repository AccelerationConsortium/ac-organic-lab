# Equipment Integration and Maintenance Runbook

This runbook is the operational checklist for:

- Adding a new equipment gateway to the dashboard (`GET /api/equipment`).
- Editing `platforms.yaml` to change section layout, display order, or equipment membership.
- Tile sizing and sensor map positions.
- Preventing placeholder hostname regressions.
- Taking equipment offline for maintenance in a way that is visible and safe.

This document complements:

- [`docs/STATUS_SPEC.md`](STATUS_SPEC.md) (device contract — combined v1.0 + v1.1)
- [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) (equipment.yaml and platforms.yaml schema)
- `deploy/README.md` (service operations)
- `equipment.yaml` — hardware inventory
- `platforms.yaml` — Overview layout config

## 1) Add New Equipment

### Prerequisites

- The equipment gateway is reachable from the dashboard host over Tailscale.
- Preferred endpoint is a Tailscale MagicDNS hostname, not a raw `100.x` IP.
- The gateway exposes at least:
  - `GET /health` -> `{"status":"healthy"}`
  - `GET /status` -> STATUS_SPEC envelope (or legacy shape handled by `legacy_http` adapter)

### Step A - Verify reachability from dashboard host

From the dashboard server (the host running `ac-organic-lab-api.service`):

```bash
curl -fsS --max-time 3 http://<magicdns-host>:<port>/health
```

If this fails, test the tailnet IP once to isolate DNS vs service outage:

```bash
curl -fsS --max-time 3 http://<100.x.y.z>:<port>/health
```

- MagicDNS fails + IP works -> fix Tailscale DNS on the dashboard host.
- Both fail -> ACL, routing, firewall, or gateway service issue (stop here).

### Step B - Register equipment in `equipment.yaml`

Add/update an entry under `equipment:` with:

- stable `id` (used by APIs and UI)
- `kind`, `adapter`
- `base_url` pointing to the real MagicDNS host
- optional `status_path`, `protocol`, `poll_timeout_seconds`
- `tiles:` — per-section tile sizing (keyed by the section id from `platforms.yaml`); omit a key to use the 2×1 default
- `pills:` — Overview pill config; set `open: true` to render an "Open ↗" link to `base_url`

Example:

```yaml
- id: xarm_translocation
  name: UFactory xArm5
  kind: robot_arm
  adapter: http
  protocol: "1.0"
  base_url: http://sdl2-pc-03-cytation.tail6a1dd7.ts.net:8000
  status_path: /status
  poll_timeout_seconds: 2.0
  tiles:
    hte: { w: 2, h: 2 }
  pills: {}
```

### Step B2 - Add the equipment to `platforms.yaml`

Add the new `id` to the `equipment:` list of the appropriate section. The position in the list determines the render order on the Overview card and on the section's detail page.

```yaml
sections:
  - id: hte
    title: HTE Platform
    href: /platforms/hte
    kind: platform
    equipment:
      - cam_hte_tapo_c245
      - xarm_translocation   # ← add here, in desired display order
      - ot2
      - ...
```

If the device should appear in a new section that doesn't exist yet, add a new `sections:` entry. Missing file or invalid schema raises on API startup (no silent fallback).

### Step C - Restart only the aggregator backend

Only restart `ac-organic-lab-api.service` (frontend usually does not need restart):

```bash
sudo systemctl restart ac-organic-lab-api.service
sudo systemctl status ac-organic-lab-api.service --no-pager
```

### Step D - Verify end-to-end from this server

`jq` is optional; this Python one-liner is dependency-free:

```bash
curl -fsS http://sdl2-server-gaia.tail6a1dd7.ts.net:8000/api/equipment | python3 -c 'import sys,json;o=json.load(sys.stdin);e=[x for x in o["equipment"] if x["id"]=="xarm_translocation"][0];print(json.dumps({"id":e["id"],"base_url":e.get("base_url"),"fetch_error":e.get("fetch_error"),"status":(e.get("status") or {}).get("equipment_status"),"message":(e.get("status") or {}).get("message")},indent=2))'
```

Expected for healthy integration:

- `base_url` matches the intended MagicDNS URL
- `fetch_error` is `null`
- `status` is a real state (`ready`, `busy`, `requires_init`, etc.)

## 2) Guardrail: Block Placeholder Hostnames

The test suite now enforces that committed `equipment.yaml` does not contain
`tail-XXXX` placeholders.

Run:

```bash
uv run pytest skills/tests/test_registry.py -q
```

If the guard fails, replace placeholder hostnames with real MagicDNS names
before merging.

## 3) Maintenance / Offline Procedure

Use registry-level maintenance metadata so workflow and planning surfaces stop
treating the equipment as available.

### Recommended registry change

For planned maintenance, keep the entry in `equipment.yaml` and set a
`maintenance` block. Optionally set `enabled: false` as an explicit hard
disable.

```yaml
- id: some_equipment
  # ... normal identity/config fields ...
  enabled: false
  maintenance:
    reason: "Preventive maintenance"
    until: "2026-06-30"
    contact: "owner@lab"
```

Then restart only the aggregator:

```bash
sudo systemctl restart ac-organic-lab-api.service
```

### What should display on the dashboard

Current behavior (today):

- The backend still polls devices even when `enabled: false` or `maintenance` is set.
- If the device service is stopped, tile status typically becomes:
  - `status: "unknown"`
  - non-null `fetch_error` (for example `connection_refused`).
- If the device still answers `/status`, the tile shows that live status.

Operational expectation for maintenance windows:

- Preferred: device continues serving `/status` and reports a clear
  maintenance-like state/message (for example `requires_init` with message
  "Under maintenance"), so operators see intent instead of a hard transport
  failure.
- Acceptable fallback: device is offline and dashboard shows `unknown` with a
  fetch error.

### Workflow-side effect (important)

In `lab_skills`, `enabled: false` or non-null `maintenance` causes
`LabSession.get()` to raise `EquipmentInMaintenance`, preventing normal control
flows from proceeding against that equipment.

## 4) Post-change checklist

- `uv run pytest skills/tests/test_registry.py -q` passes.
- `curl http://sdl2-server-gaia.tail6a1dd7.ts.net:8000/api/equipment` shows expected `base_url`.
- `fetch_error` matches expectation (`null` when online; explicit error if offline).
- Dashboard tile state matches gateway `/status` (or expected offline behavior).

## 5) Customising the dashboard layout

Everything visible on the dashboard — section order, equipment membership, tile sizes, and sensor positions — is driven by `equipment.yaml` and `platforms.yaml`. No frontend code changes are needed.

### Section order and equipment membership (`platforms.yaml`)

`platforms.yaml` is the single source of truth for what appears on the Overview page and in the Nav. Edit the `sections:` list to reorder sections or move equipment between them. The API reloads on the next restart (or `uvicorn --reload` picks it up automatically in dev).

Each section has:
- `id` — stable identifier, used as the tile-sizing key in `equipment.yaml`
- `title` — display name (Overview card header, Nav tab)
- `href` (optional) — if present, a Nav tab is auto-injected for this section
- `kind: platform | environmental_map` — `platform` renders a `PlatformCard`; `environmental_map` renders the `LabMap`
- `equipment:` — ordered list of equipment ids; this order is the render order everywhere

### Tile sizes (platform detail pages)

Platform detail pages (e.g. `/platforms/hte`) lay equipment cards on a 4-column CSS grid. Set the `tiles:` dict on an `equipment.yaml` entry to control the size per section:

```yaml
- id: xarm_translocation
  ...
  tiles:
    hte: { w: 2, h: 2 }   # spans 2 of 4 columns, 2 rows tall
```

A missing section key defaults to `{ w: 2, h: 1 }`.

| `w` | spans (lg+) | typical use |
|-----|-------------|-------------|
| `1` | quarter row | very compact, info-light device |
| `2` | half row (default) | most devices |
| `3` | three-quarters row | rare |
| `4` | full row | banner / hero device |

`h` (1..4) is honoured as a row span. Both fields are validated by Pydantic on startup — a typo fails the API immediately.

Responsive behaviour: on mobile (< sm) every card is full-width; from sm to lg the grid is 2 columns and `w` is capped at 2; from lg+ the full 4-column grid applies.

### "Open ↗" link on the Overview pill row

Set `pills: { open: true }` on any equipment entry to render an "Open ↗" link to its `base_url` in the Overview platform card. Intended for web-service entries (e.g. `pypoe_web`). All other equipment should have `pills: {}`.

### Sensor positions on the lab map

Environmental sensors place markers on the SVG floorplan. The map is rotated 90° clockwise from the building plan (north is up on screen). Set `location.x` and `location.y` as percentages of the map (0-100):

```yaml
- id: env_lab499_west
  kind: environmental_sensor
  ...
  location: { x: 20, y: 75, label: "Lab 499 · West" }
```

The four zones:

- Stairs (top-left quadrant, greyed out)
- Sample Prep (top-right quadrant)
- Storage (middle horizontal band)
- Lab 499 (bottom half — main lab)

## 6) Cameras (`kind: camera`) and plugs (`smart_plug`, `power_strip`)

Tapo cameras and Kasa plugs are not first-class HTTP devices - they speak proprietary lab-LAN protocols. They are bridged into the dashboard by the [`kasa-tapo-services`](https://github.com/cyrilcaoyang/kasa_tapo_services) gateway running on the dashboard host.

### Onboarding a Tapo camera

Prerequisites:
- The camera is on the same lab LAN as the dashboard host.
- `kasa-tapo-services` and `ac-go2rtc` are installed and running on the dashboard host (see `kasa_tapo_services/deploy/README.md`).

Steps:

1. **Create accounts on the camera** (Tapo phone app → Settings → Advanced):
   - **Camera Account** (used by go2rtc for RTSP and by pytapo for privacy/day-night)
   - **ONVIF account** (used for PTZ + presets)
2. **Verify RTSP paths** from the dashboard host (some dual-lens models use `stream1`+`stream3` instead of `stream1`+`stream2`):
   ```bash
   ffprobe -v error rtsp://USER:PASS@<lan_ip>:554/stream1
   ffprobe -v error rtsp://USER:PASS@<lan_ip>:554/stream2
   ```
3. **Add the camera to the gateway's `devices.yaml`** (typically at `/opt/kasa-tapo-services/devices.yaml`):
   ```yaml
   - id: cam_lab499_west
     name: Lab 499 (West) Camera
     kind: camera
     host: 192.168.1.42
     onvif_port: 2020
     lenses:
       - { id: wide, label: Wide, rtsp_path: stream1 }
       - { id: tele, label: Tele, rtsp_path: stream2 }
   ```
4. **Add credentials** to `/etc/kasa-tapo-services/.env`:
   ```
   CAM_LAB499_WEST_USER=<camera-account-user>
   CAM_LAB499_WEST_PASS=<camera-account-pass>
   CAM_LAB499_WEST_ONVIF_USER=<onvif-user>     # omit to reuse Camera Account
   CAM_LAB499_WEST_ONVIF_PASS=<onvif-pass>
   ```
5. **Restart the gateway and go2rtc**:
   ```bash
   sudo systemctl restart kasa-tapo-services.service ac-go2rtc.service
   ```
6. **Add the matching `equipment.yaml` entry** in this repo (note the `camera:` block mirrors `devices.yaml` for the dashboard-side lens labels); then add the id to the appropriate section in `platforms.yaml`:
   ```yaml
   # equipment.yaml
   - id: cam_lab499_west
     name: Lab 499 (West) Camera
     kind: camera
     adapter: http
     protocol: "1.0"
     base_url: http://127.0.0.1:8002
     status_path: /cameras/cam_lab499_west/status
     poll_timeout_seconds: 2.0
     tiles:
       hte: { w: 2, h: 2 }   # adjust section id and size as needed
     pills: {}
     camera:
       host: 192.168.1.42
       onvif_port: 2020
       lenses:
         - { id: wide, label: Wide, rtsp_path: stream1 }
         - { id: tele, label: Tele, rtsp_path: stream2 }
   ```
7. **Restart the dashboard API**: `sudo systemctl restart ac-organic-lab-api.service`.
8. **End-to-end check**:
   ```bash
   curl -fsS http://localhost:8001/api/equipment | python3 -c \
     'import sys,json; cam=[e for e in json.load(sys.stdin)["equipment"] if e["id"]=="cam_lab499_west"][0]; print(json.dumps({"status":cam["status"]["equipment_status"],"presets":cam["status"]["details"].get("presets"),"lenses":[l["stream_connected"] for l in cam["status"]["details"]["lenses"]]}, indent=2))'
   ```
9. Open the dashboard's Lab Overview page (`/`) in the browser — the HTE platform card shows a **"Show stream"** button in the header. Click it to expand the live MSE feed inline; click **"Hide stream"** to collapse it. The stream is hidden by default so the overview page doesn't load live video for every visitor. The full camera tile with PTZ controls, presets, privacy/streaming toggles, snapshot, recording, and rolling-recording is always available on the platform detail page (`/platforms/<platform>`).

### Onboarding a Kasa plug

1. **Discover the plug's IP** (`kasa discover` from the gateway host, or check the Kasa app).
2. **Add an entry to `devices.yaml`** with `kind: smart_plug` (HS103) or `kind: power_strip` + `outlets:` (HS300).
3. **Restart the gateway**.
4. **Add the matching `equipment.yaml` entry** with `status_path: /plugs/<id>/status`.

### What goes wrong (and how to spot it)

| Symptom                                              | Likely cause                                                    | Fix                                                         |
|------------------------------------------------------|------------------------------------------------------------------|-------------------------------------------------------------|
| Tile is `degraded`, `details.onvif_reachable: false` (but Tapo up) | Wrong ONVIF port, wrong account, or ONVIF disabled on camera    | Check Tapo app → Advanced → ONVIF; default port is 2020     |
| Tile is `unknown` / "Unreachable", all of `onvif`/`tapo`/`go2rtc` false | Camera fully offline (LAN/power down) — neither ONVIF nor Tapo answered. NOT a fault (the gateway maps this to `unknown`, and the dashboard renders gateway-fronted `unknown` as "Unreachable"; see STATUS_SPEC §2.1) | Check camera power + LAN; `ffprobe rtsp://…` from the gateway host |
| Tile is `degraded`, lens markers grey                | go2rtc is up but credentials are wrong - source not connecting  | Check `journalctl -u ac-go2rtc -n 50` for RTSP auth errors  |
| MSE viewport shows "No stream"                       | Caddy `/streams/*` block missing                                | Add the snippet in `kasa_tapo_services/deploy/Caddyfile.snippet` |
| PTZ buttons disabled                                 | ONVIF was unreachable on last poll                              | Same as the first row                                       |
| Privacy toggle disabled                              | pytapo creds missing or wrong                                   | Set `<ID>_USER` / `<ID>_PASS` to the **Camera Account**     |

## 6b) Password-gating the control surfaces (`CONTROL_PASSWORD`)

> **Deprecation path:** this shared-password gate is superseded by the
> per-user login in [`AUTH_DESIGN.md`](AUTH_DESIGN.md) (`ac_auth`,
> email-code + roster). It remains documented here because it is what is
> deployed until the auth rollout replaces it; do not build new features
> on it.

Tiles with destructive actions (`PowerStripTile`, `FumeHoodTile`, future
ones via `useControlLock`) share a single password gate. Set
`CONTROL_PASSWORD` in the web service's environment to require it;
leave it unset to keep the dashboard fully open (Tailscale-ACL-only).

Enable on the dashboard host:

```
sudo systemctl edit ac-organic-lab-web.service
# add (inside [Service]):
Environment=CONTROL_PASSWORD=your-shared-password
sudo systemctl restart ac-organic-lab-web.service
```

How it works:

- **Frontend**: clicking the Unlock chip in any control tile opens a
  shared password modal (`ControlAuthProvider` at the root). After a
  correct password, the 10-second auto-relock countdown starts as
  before. The cookie persists 30 minutes, so subsequent unlocks within
  the window don't re-prompt.
- **Server**: Next.js middleware blocks `POST`/`PUT`/`PATCH`/`DELETE`
  on `/api/equipment/*/{control,sash}/*` unless the `control_auth`
  cookie matches. The dashboard's FastAPI passthrough never sees the
  request when auth fails.
- **Scope**: dashboard-side only. The device REST endpoints
  (`100.64.254.100:5000/move`, the camera gateway, etc.) remain
  reachable directly by anyone on the Tailnet — change device-side
  posture in the per-device repos if that's not acceptable.

Caveats:

- Cookie is `HttpOnly` and `SameSite=Strict`, but **not `Secure`**
  because the dashboard is on plain `http://` over Tailscale. If the
  dashboard ever moves to real TLS, flip `secure: true` in
  `/api/control-unlock/route.ts`.
- One password for the whole dashboard. Per-tile or per-user gating
  would require real auth (out of scope).
- Changing the password requires a web service restart.

### What's behind the lock — design policy

The lock chip is intentionally surgical: it guards **destructive
controls only**. Convenience controls stay open even when the password
gate is enabled. The single source of truth for this rule is
`web/src/lib/tile-policy.ts`.

| Tile / control                                  | Lock applies? | Why |
|--------------------------------------------------|---------------|-----|
| Sash move / stop (`FumeHoodTile`)               | Yes           | Mechanical movement |
| Press up/down + plate in/out (`PressTile`)      | Yes           | Pneumatic + plate motion |
| Sealer startup/shutdown, stage in/out, seal start/stop, set temperature/time (`PlateSealerTile`) | Yes | Heated cycle, mechanical motion |
| Shake start/stop, set temperature/speed (`ShakerTile`) | Yes | Heated, mechanical motion |
| xArm motion-graph control ops (`graph.move_to` / `recover_to` / …, `RobotArmTile`) | Yes (chip in header) | Will move hardware once the tile surfaces the `graph.*` controls (device surface + catalog already shipped) |
| Plate-reader, HPLC controls (when they land); **solid-doser (dose) `/control/*`** now claim-gated on the device | Yes | Destructive kinds carry the chip even before tile controls land |
| **OT-2 deck lights** (`LiquidHandlerTile`, `lights.set`) | **No** | Convenience lighting; bypassed per-action at the middleware (see below) |
| OT-2 protocol-execution actions (setup, home, aspirate, dispense, etc.) when they land | Yes | Move pipettes / labware |
| Power-strip outlet labelled *light* / *lamp*    | No (tile chip) / Yes (middleware) | Convenience lighting — see *Two layers* below |
| Power-strip outlet driving equipment (hotplate, stirrer, etc.) | Yes           | Can damage a sample / hardware |
| Camera PTZ, presets, snapshots, recording (`CameraTile`) | No | Convenience; cannot damage hardware. `kind: camera` is in `UNGATED_KINDS` |
| Environmental sensors, UPLC-MS sidecar (read-only) | No            | No controls at all |

#### Two layers, two bypass points

The lock policy has two enforcement layers, and they don't always
agree. Be deliberate about which layer you mean when you say
"bypassed".

| Layer | Source of truth | Bypass mechanism |
|---|---|---|
| **Tile lock chip** (in-tile, 5 s auto-relock) | `useControlLock()` per-tile | `outletIsSafe(label)` per outlet on `PowerStripTile`; tile-level decision not to call `useControlLock()` on the lights row in `LiquidHandlerTile` |
| **`CONTROL_PASSWORD` middleware** (cookie-gated; only when env var is set) | `web/src/middleware.ts` + `tile-policy.ts` | `kindBypassesControlGate(kind)` (camera + env sensors) **OR** `actionBypassesControlGate(action)` (any `/control/lights*` URL — added 2026-05-30 for OT-2) |

The two combinations that matter today:

- **OT-2 deck lights** are bypassed at **both** layers — the
  `LiquidHandlerTile` doesn't gate the lights row on `locked`, and the
  middleware `actionBypassesControlGate("lights")` lets the POST
  through without the `control_auth` cookie. Operator can flip the
  lights regardless of lock state or `CONTROL_PASSWORD`.
- **Power-strip light outlets** are bypassed only at the **tile**
  layer. The middleware still requires the cookie because a single
  `power_strip` mixes safe outlets (light) with destructive outlets
  (hotplate, stirrer) and the URL alone doesn't disambiguate the per-
  outlet decision. The per-outlet "safe" decision happens in
  `PowerStripTile` against `outletIsSafe(label)`.

If you ever need a *new* convenience-class `/control/*` action on a
destructive kind, the pattern is: extend `UNGATED_ACTION_RE` in
`tile-policy.ts` (the regex currently matches `lights*`) and skip
`useControlLock()` on that specific row in the kind-specific tile.

#### How "light" outlets are detected

`outletIsSafe(label)` matches the outlet's gateway-supplied label
against `/\b(light|lamp)\b/i`. Labels come from
`kasa_tapo_services/devices.yaml`'s per-outlet `label:` field. So
naming an outlet `Bench Light A` or `Desk lamp 1` opts it out of the
lock automatically; naming it `Hotplate A` keeps it gated.

False positives are possible (e.g. `Lighthouse fan` would match). When
that becomes a problem, replace the heuristic with an explicit
`safe: true` flag per outlet in `devices.yaml`; this is a one-line
change in `tile-policy.ts`.

#### Adding a new destructive kind

When a new `EquipmentKind` joins the spec, add it to the
`DESTRUCTIVE_KINDS` set in `tile-policy.ts`. The lock chip then
appears automatically on every `EquipmentStatusCard` of that kind.
Kind-specific tiles (`FumeHoodTile` etc.) should also call
`useControlLock()` directly so the chip lives in their header too.

#### Placeholder chips

`EquipmentStatusCard` shows the lock chip whenever the kind is
destructive, even if no clickable controls are rendered yet. This is
deliberate: it makes the design promise visible — when controls land,
they will respect this lock — and keeps the chrome consistent across
the lab. Until controls land, clicking the chip is harmless; the
countdown ticks down and auto-relocks with nothing to actually gate.

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

The Agilent PlateLoc (`plateloc`, STATUS_SPEC v1.1) renders as
`PlateSealerTile`. A 2×2 metric grid (Actual / Setpoint /
Seal time / Cycles), editable Setpoint + Seal time inputs, and
state-aware action buttons (Startup, Stage in/out, Seal start,
Seal stop, Shutdown). The **Stage in** / **Stage out** buttons are
rendered as `PositionPill`s (same pattern as `PressTile`'s plate
IN/OUT pills): whichever pill matches the live
`components.stage.state` is highlighted emerald — no separate Stage
indicator row.

### Seal-start interlocks (defence in depth)

`Seal start` is enforced at **three layers**, all on by default, across
**two independent preconditions**:

1. **Temperature-band interlock** — `|actual_temperature −
   setpoint_temperature| ≤ details.temperature_tolerance_c`
   (plateloc v1.2+).
2. **Stage-position interlock** — `components.stage.state == "in"`
   (plateloc v1.3+). The plate must be loaded under the press.

Both are enforced at all three layers:

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
   The temperature 412 also carries a `Retry-After` header (seconds,
   integer); the stage 412 does not (recovery is operator-driven, not
   time-based). Bypassable via the device-side config flags
   `[service].enforce_temp_interlock = false` and
   `[service].enforce_stage_interlock = false` respectively, both
   reserved for emergency calibration runs.

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
| **409** | device-state conflict (e.g. not initialised) | *"Driver not connected. Click Startup first."* |

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

- **Air-pressure faults.** The 2026-05-23 incident's downstream
  symptom (`Low Air Pressure Error` from the pneumatic press inside
  the sealer) needs a facility-level sensor; the device has no
  pressure introspection. The dashboard surfaces it post-hoc via
  `last_error`.
- **Cross-device chemistry interlocks.** Sealing at 170 °C with a
  flammable solvent below its flash point belongs in layer 4 (project
  plan interlocks); see [`docs/INTERLOCKS.md`](INTERLOCKS.md).

## 10) Robot arm (`kind: robot_arm`)

The UFactory xArm5 (`xarm_translocation`, STATUS_SPEC v1.1 on
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

The Opentrons OT-2 (`ot2`, STATUS_SPEC v1.1 via `opentrons-server` on
`sdl2-pc-03-cytation.tail6a1dd7.ts.net:8020`) renders as the
kind-specific `LiquidHandlerTile`. Protocol-execution actions
(`setup`, `home`, `aspirate`, `dispense`, `pick_up_tip`, `drop_tip`,
`move_labware`, `pause`) are advertised by the device today but the
catalog has no typed protocol-arg shapes for them yet — those land in
a follow-up. **What ships now is the deck-light toggle, the pipette
pills, and a shared deck-layout picker.**

### Tile behaviour

A top row with the light control + pipette pills, a 12-slot deck grid,
a "Select Labware" picker, then the standard `MetricList` /
`ComponentList` for anything left over.

**Top row** — one Light toggle + two pipette pills:

| Element | Source | Behaviour |
|---|---|---|
| **Light** button with a state dot | `components.lights.state` (`on` / `off` / `unknown`) | One button that toggles (POSTs the opposite of the current state). Dot is **amber (glowing)** when on, **black** when off. Convenience-class: no lock chip, but disabled + hinted when signed out. |
| Left / right **pipette pills** | `components.pipette_left.state` / `pipette_right.state` | Model formatted (`p300_multi_gen2` → `P300 Multi`); left mount rendered first (position implies the mount, so no caption). Hover shows mount + raw model; empty mount shows `—`. |

**Deck grid** — 12 slots, 3 columns × 4 rows, numbered to match the
physical deck (**1 bottom-left, 3 bottom-right, 10 top-left, 12
top-right**). Click a slot to select it (highlights sky-blue; click
again to deselect). Rendering by slot contents:

- **Empty** — a large, light-grey *watermark* slot number (centred).
- **96-well / 24-well** — a miniature well grid of round wells (**8×12**
  / **4×6**); the inner grid takes the plate's own aspect ratio so cells
  are square and the wells render as true circles. No number.
- **Waste bin** — the slot is simply greyed out (no wells), labelled
  `waste`.

**Select Labware** — a picker at the bottom, disabled until a slot is
selected; then choose **96-well plate** / **24-well plate** / **Waste
bin** (or **Empty** to clear). Assigns to the highlighted slot.

The `lights`, `pipette_left`, and `pipette_right` components are
rendered by the top row, so they're filtered out of the generic
`ComponentList` (`TILE_OWNED_COMPONENTS` in `LiquidHandlerTile.tsx`) to
avoid duplication. The Light row does NOT respect the in-tile lock chip
(see §6b "Two layers, two bypass points"); the lock chip is in the
header because protocol-execution actions will land later and *will* be
gated.

### Deck-layout store (shared, server-persisted — stopgap)

The deck labware assignment is **shared across users**, not per-browser.
It is stored server-side and served by a small store in
`api/app/deck.py`:

- `GET /api/equipment/{id}/deck` → `{ slots: { "<1..12>": "96-well" | "24-well" | "waste" } }`
- `PUT /api/equipment/{id}/deck` → replaces the layout (validates slot
  range 1..12 and labware against an allowlist), returns the cleaned map.

Persistence is a JSON file (`deck_layouts.json`) next to `lab.db` in the
data directory, written atomically under a lock. The tile loads it via
`react-query` (`queryKey: ["deck", id]`), polls every 15 s so other
operators' edits appear, and writes optimistically on each change.
`equipment.yaml` is deliberately **not** touched (pyyaml would strip its
comments). This whole store is a stopgap: once `opentrons-server`
publishes real deck state on `/status`
(`details.snapshot.deck.slots`, currently all `null`), the tile should
read that instead and this store can be retired.

> **Not auth-gated yet.** Unlike `/control/*`, the `/deck` PUT is not
> covered by the sign-in middleware, so any Tailnet user can edit the
> shared layout. Acceptable for the stopgap; gate it (add to
> `CONTROL_PATH_RE`-style matching) if that becomes a concern.

### Status derivation (device side)

`opentrons-server` polls `GET /robot/lights` on the OT-2's own HTTP
API and surfaces the result as `components.lights = {connected: true,
state: "on"|"off"|"unknown"}`. Whenever the robot is reachable,
`"lights.set"` appears in `allowed_actions` regardless of
`equipment_status` — lights work in `requires_init` just as well as
in `ready`.

### Control passthrough and claim handling

`POST /api/equipment/ot2/control/lights` flows through the generic
passthrough in `api/app/control.py`, which handles claim acquire /
`X-Claim-Token` attach / release per request (the device enforces
`X-Claim-Token` on `/control/*`).

The `CONTROL_PASSWORD` middleware **does not** gate this path even
when the env var is set — `actionBypassesControlGate("lights")`
returns true so the POST goes through without a `control_auth`
cookie. This is intentional convenience-class behaviour, same
operator-facing class as camera PTZ. See §6b for the matrix.

### What goes wrong (and how to spot it)

| Symptom | Likely cause | Fix |
|---|---|---|
| Buttons render but POST returns HTTP 423 | Another workflow holds a longer-lived claim on the OT-2 | `details.claimed_by.owner` on `/api/equipment/ot2/status` shows who. Release via the SDK, then retry. |
| Buttons render but POST returns HTTP 404 | `opentrons-server` predates the `lights` endpoint | Update the gateway on `sdl2-pc-03-cytation:8020`. |
| Lights dot stuck on `—` | Device repo isn't publishing `components.lights` yet | Check `/api/equipment/ot2/status` — if the component is missing, the gateway version is too old. |
| Lights dot reflects the wrong state | Browser tab has a stale `react-query` cache | Status refreshes on the next aggregator poll (~2 s); a hard refresh is also fine. |

### Open work

- **Protocol-execution skills** — add SkillDefs for `setup`, `home`,
  `aspirate`, `dispense`, `pick_up_tip`, `drop_tip`,
  `move_labware`, `pause`, `resume`, `reconcile`. These need labware-
  typed Pydantic args that the catalog has no shapes for yet.
- **Deck from device state** — retire the `api/app/deck.py` stopgap once
  `opentrons-server` publishes real deck contents on `/status`
  (`details.snapshot.deck.slots`); the tile should read that (and push
  assignments through a `plate.load`-style skill) instead of the shared
  JSON store.
- **Gate the `/deck` PUT** behind the sign-in middleware if the shared
  layout needs write protection.
