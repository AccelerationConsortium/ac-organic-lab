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
curl -fsS http://localhost:3000/api/equipment | python3 -c 'import sys,json;o=json.load(sys.stdin);e=[x for x in o["equipment"] if x["id"]=="xarm_translocation"][0];print(json.dumps({"id":e["id"],"base_url":e.get("base_url"),"fetch_error":e.get("fetch_error"),"status":(e.get("status") or {}).get("equipment_status"),"message":(e.get("status") or {}).get("message")},indent=2))'
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
- `curl http://localhost:3000/api/equipment` shows expected `base_url`.
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

Tapo cameras and Kasa plugs are not first-class HTTP devices - they speak proprietary lab-LAN protocols. They are bridged into the dashboard by the [`kasa-tapo-services`](https://github.com/your-org/kasa_tapo_services) gateway running on the dashboard host.

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
| Tile is `error`, `details.onvif_reachable: false`    | Wrong ONVIF port, wrong account, or ONVIF disabled on camera    | Check Tapo app → Advanced → ONVIF; default port is 2020     |
| Tile is `degraded`, lens markers grey                | go2rtc is up but credentials are wrong - source not connecting  | Check `journalctl -u ac-go2rtc -n 50` for RTSP auth errors  |
| MSE viewport shows "No stream"                       | Caddy `/streams/*` block missing                                | Add the snippet in `kasa_tapo_services/deploy/Caddyfile.snippet` |
| PTZ buttons disabled                                 | ONVIF was unreachable on last poll                              | Same as the first row                                       |
| Privacy toggle disabled                              | pytapo creds missing or wrong                                   | Set `<ID>_USER` / `<ID>_PASS` to the **Camera Account**     |

## 7) Fume hood (`kind: fume_hood`)

The fume-hood sash actuator (`fume_hood_actuator`, legacy Flask on
`100.64.254.100:5000`) renders as a kind-specific `FumeHoodTile`.
It shows the 5 sash presets as a row of horizontal pills (1 = closed
/ "LOW" on the left, 5 = fully open / "HIGH" on the right), and
exposes click-to-move and stop controls behind a lock toggle.

### Tile behaviour

- **Pill states**
  - Solid emerald = current preset (sash parked at hall sensor N).
  - Amber pulse = optimistic target while the device reports `busy`.
  - Dimmed = idle / not the current position.
- **No pill lit** = sash is between hall sensors. The legacy device
  has no encoder readback, so position is only known at the 5 hall
  triggers. The aggregator surfaces this as
  `equipment_status: requires_init` rather than masking it.
- **Lock toggle** in the header (matches the power-strip tile,
  5-second auto-relock). Pills and the Stop button are disabled
  while locked.
- **Stop button** appears only while the device is `busy`. It POSTs
  to the device's `/stop` and the tile drops its optimistic target.

### Status derivation (adapter side)

`LegacyFumeHoodActuatorAdapter` derives `equipment_status` from
physical signals, not the device's own status string:

| `is_moving` | `sash_position` | Dashboard `equipment_status` |
|-------------|------------------|------------------------------|
| `true`      | any              | `busy`                       |
| `false`     | `1..5`           | `ready`                      |
| `false`     | `null`           | `requires_init`              |

The device's `equipment_status: "stopped"` (which it returns after
any `/stop` call, even when parked at a preset) is intentionally
ignored — it does not reflect operational state, only "last command
was stop".

### Control passthrough

Two passthrough routes on the dashboard API call the device's
non-spec-conformant endpoints:

- `POST /api/equipment/{id}/sash/move` body `{"position": 1..5}`
- `POST /api/equipment/{id}/sash/stop`  body `{}`

These will collapse into the generic `/control/{action}` path once
the device migrates to STATUS_SPEC.

### What goes wrong (and how to spot it)

| Symptom                                       | Likely cause                                              | Fix                                                          |
|-----------------------------------------------|------------------------------------------------------------|--------------------------------------------------------------|
| Tile is `requires_init` after a successful move | Sash overshot/undershot a hall sensor                    | Click any pill again; the controller's "search by pulsing down" routine relocates within 5 pulses |
| Pill click does nothing visible               | Controls still locked                                     | Click the **Locked** chip to unlock (5 s window before auto-relock) |
| 422 from `/sash/move`                         | Position outside 1..5                                     | Pydantic validation; tile only emits 1..5, so this only happens via direct API calls |
| 504 from `/sash/{move,stop}`                  | Device unreachable (Tailnet, Pi power)                    | `curl http://100.64.254.100:5000/health` from the dashboard host |
