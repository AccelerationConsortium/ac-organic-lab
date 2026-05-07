# Equipment Integration and Maintenance Runbook

This runbook is the operational checklist for:

- Adding a new equipment gateway to the dashboard (`GET /api/equipment`).
- Preventing placeholder hostname regressions (like `tail-XXXX`).
- Taking equipment offline for maintenance in a way that is visible and safe.

This document complements:

- `docs/STATUS_SPEC.md` and `docs/STATUS_SPEC_v1_1.md` (device contract)
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
