# Equipment Integration Guide

This guide is the durable operational **guideline** for:

- Adding a new equipment gateway to the dashboard (`GET /api/equipment`).
- Editing `platforms.yaml` to change section layout, display order, or equipment membership.
- Tile sizing and sensor map positions.
- Preventing placeholder hostname regressions.
- Taking equipment offline for maintenance in a way that is visible and safe.
- Onboarding cameras / plugs, and the control-lock (`CONTROL_PASSWORD`) policy.

> **Guide vs. status.** This file is the *how-to that stays true over time*. For
> the *current implementation detail* of each device's tile (how it renders
> today, status derivation, control passthrough, per-device troubleshooting for
> the fume hood, press, plate sealer, robot arm, and OT-2), see the companion
> [`EQUIP_STATUS.md`](EQUIP_STATUS.md). Section numbers are continuous across the
> two: §1–§6b live here, §7–§11 there.

This document complements:

- [`docs/EQUIP_STATUS.md`](EQUIP_STATUS.md) (current per-device tile implementations, §7–§11)
- [`docs/STATUS_SPEC.md`](STATUS_SPEC.md) (device contract — combined v1.0 + v1.1 + v1.2)
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
      - ot2_hte
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
- `default: true` (optional, at most one section) — the platform the Platforms tab shows first; display order is unaffected
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
9. Open the dashboard's Lab Overview page (`/`) in the browser — the HTE platform card shows the live MSE feed inline and a **"Hide stream"** button in its header. The preview is **expanded by default**, so the overview page loads live video for every visitor; click **"Hide stream"** to collapse it (and **"Show stream"** to bring it back for that visit). The full camera tile with PTZ controls, presets, privacy/streaming toggles, snapshot, recording, and rolling-recording is always available on the platform detail page (`/platforms/<platform>`).

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
| PTZ buttons absent on a *fixed* camera (C100/C110/…) | Expected. A fixed camera has no ONVIF PTZ service, so the gateway reports `has_ptz: false` and omits `ptz` / `preset/*` from `allowed_actions` (STATUS_SPEC §6.2 — never advertise an action the hardware would refuse) | Nothing to fix. If a *PTZ* camera lands here, its ONVIF account is likely wrong — see the first row |
| Privacy toggle disabled                              | pytapo creds missing or wrong                                   | Set `<ID>_USER` / `<ID>_PASS` to the **Camera Account**. If RTSP + ONVIF authenticate but the gateway logs `tapo.getPrivacyMode failed: Invalid authentication data`, see the next row — the Camera Account is *correct* and the fix is different |
| Privacy toggle disabled on *every* camera; logs show `tapo.getPrivacyMode failed: Invalid authentication data`, escalating to `Temporary Suspension: Try again in N seconds` | **Newer Tapo firmware rejects the Camera Account on the control API** — it only accepts local user `admin` with the **TP-Link cloud account password**. The repeated poll-loop failures then trigger a device-side lockout that keeps re-arming as long as the wrong credential is presented. Health is unaffected (that needs only ONVIF + go2rtc), so tiles still read `ready`. This is exactly what hit all three cameras 2026-08-04 → 2026-08-11 | Add `<ID>_CLOUD_PASS=<TP-Link cloud password>` to the gateway's `.env` (supported since kasa-tapo-services PR #2; the gateway then logs in as `admin`/cloud while RTSP + ONVIF keep the Camera Account), restart the gateway, and wait out the final lockout window (~10 min — it drains on its own once login attempts stop failing). Resolved this way on all three cameras 2026-08-11 |

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

- **OT-2 deck lights** are bypassed at the **middleware** layer:
  `actionBypassesControlGate("lights")` lets the POST through without the
  `control_auth` cookie, so the lights can be flipped regardless of
  `CONTROL_PASSWORD`. There is no tile-layer decision left to make — since
  the panel embed (2026-08-05) the toggle lives in the gateway's own UI, not
  in `LiquidHandlerTile`; the bypass is kept for API callers and for a future
  dashboard-side lights control.
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

