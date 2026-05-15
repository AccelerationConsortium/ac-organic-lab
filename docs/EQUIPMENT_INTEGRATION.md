# Equipment Integration and Maintenance Runbook

This runbook is the operational checklist for:

- Adding a new equipment gateway to the dashboard (`GET /api/equipment`).
- Preventing placeholder hostname regressions (like `tail-XXXX`).
- Taking equipment offline for maintenance in a way that is visible and safe.

This document complements:

- [`docs/STATUS_SPEC.md`](STATUS_SPEC.md) (device contract — combined v1.0 + v1.1)
- `deploy/README.md` (service operations)
- `equipment.yaml` (committed registry source of truth)

## 1) Add New Equipment

### Prerequisites

- The equipment gateway is reachable from the dashboard host over Tailscale.
- Preferred endpoint is a Tailscale MagicDNS hostname, not a raw `100.x` IP.
- The gateway exposes at least:
  - `GET /health` -> `{"status":"healthy"}`
  - `GET /status` -> STATUS_SPEC envelope (or legacy shape handled by `legacy_http` adapter)

### Step A - Verify reachability from dashboard host

From the dashboard server (the host running `ac-dashboard-api.service`):

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

- stable `id` (used by APIs and UI),
- `kind`, `adapter`, `platform`,
- `base_url` pointing to the real MagicDNS host,
- optional `status_path`, `protocol`, tile/location fields.

Example:

```yaml
- id: xarm_translocation
  name: UFactory xArm5
  platform: hte
  kind: robot_arm
  adapter: http
  protocol: "1.0"
  base_url: http://sdl2-pc-03-cytation.tail6a1dd7.ts.net:8000
  status_path: /status
  poll_timeout_seconds: 2.0
```

### Step C - Restart only the aggregator backend

Only restart `ac-dashboard-api.service` (frontend usually does not need restart):

```bash
sudo systemctl restart ac-dashboard-api.service
sudo systemctl status ac-dashboard-api.service --no-pager
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
sudo systemctl restart ac-dashboard-api.service
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

## 5) Cameras (`kind: camera`) and plugs (`smart_plug`, `power_strip`)

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
6. **Add the matching `equipment.yaml` entry** in this repo (note the `camera:` block mirrors `devices.yaml` for the dashboard-side lens labels):
   ```yaml
   - id: cam_lab499_west
     name: Lab 499 (West) Camera
     platform: lab
     kind: camera
     adapter: http
     protocol: "1.0"
     base_url: http://127.0.0.1:8002
     status_path: /cameras/cam_lab499_west/status
     poll_timeout_seconds: 2.0
     tile: { w: 2, h: 2 }
     camera:
       host: 192.168.1.42
       onvif_port: 2020
       lenses:
         - { id: wide, label: Wide, rtsp_path: stream1 }
         - { id: tele, label: Tele, rtsp_path: stream2 }
   ```
7. **Restart the dashboard API**: `sudo systemctl restart ac-dashboard-api.service`.
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
