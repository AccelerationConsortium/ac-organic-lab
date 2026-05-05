# Lab Equipment Status Spec

**Version:** `1.0`
**Status:** stable for v1 dashboard. Superseded for new work by `docs/STATUS_SPEC_v1_1.md`, which is fully back-compatible (every v1.0 device continues to work without changes).

This document is the authoritative contract every lab equipment REST API must implement to be displayed on the AC Organic Self-driving Lab dashboard. Each equipment repository copies the Pydantic models below into its own `models.py`. Once the spec has been stable for ~1 month and 3+ repos have migrated cleanly, these types are promoted to a shared `lab-status-contract` Python package.

> **Migrating to v1.1?** See `docs/STATUS_SPEC_v1_1.md` for the claim/heartbeat/release additions and the `allowed_actions` field. Devices that do not opt in stay on v1.0 unchanged; the SDK degrades gracefully.

## Required HTTP Surface

| Method | Path             | Always 200? | Description                                                            |
|--------|------------------|-------------|------------------------------------------------------------------------|
| GET    | `/`              | Yes         | Minimal probe: `{equipment_id, equipment_name, protocol_version}`.     |
| GET    | `/health`        | Yes         | Service liveness from the dashboard's perspective.                     |
| GET    | `/status`        | Yes*        | Full `EquipmentStatus` envelope. *Non-200 only when the process is broken. |
| GET    | `/openapi.json`  | Yes         | OpenAPI document. FastAPI generates this automatically.                |

Future, optional:

- `POST /control/*` - control endpoints, gated by explicit body schemas.
- `WS /ws` - pushes the same `EquipmentStatus` envelope on changes.

## The Envelope

```python
from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field


PROTOCOL_VERSION = "1.0"


EquipmentKind = Literal[
    "solid_doser",
    "liquid_handler",
    "press",
    "fume_hood",
    "robot_arm",
    "environmental_sensor",
    "hplc",
    "plate_reader",
    "plate_sealer",
    "plate_stacker",
    "other",
]

EquipmentState = Literal[
    "ready",          # initialized, idle, can accept commands
    "busy",           # performing an operation
    "requires_init",  # service up but hardware not initialized (e.g. needs POST /startup or /connect)
    "degraded",       # running but a sub-component is unhealthy
    "dry_run",        # simulation mode, no hardware connected
    "error",          # hardware reported an error
    "e_stop",         # emergency stopped
    "unknown",        # state cannot be determined
]


class ComponentStatus(BaseModel):
    connected: bool
    state: str  # equipment-defined string; pick a small enum per equipment kind
    message: str | None = None
    last_event_at: datetime | None = None


class MetricValue(BaseModel):
    value: float | int | str | bool
    unit: str | None = None
    timestamp: datetime | None = None


class ErrorInfo(BaseModel):
    code: str | None = None
    message: str
    severity: Literal["info", "warning", "error", "critical"]
    timestamp: datetime


class EquipmentStatus(BaseModel):
    protocol_version: str = PROTOCOL_VERSION

    # Identity
    equipment_id: str
    equipment_name: str
    equipment_kind: EquipmentKind
    equipment_version: str | None = None
    host: str | None = None  # local hostname only (output of `hostname`)

    # Operational state
    equipment_status: EquipmentState
    message: str | None = None
    required_actions: list[str] = Field(default_factory=list)

    # Timing
    device_time: datetime
    uptime_seconds: float | None = None

    # Sub-equipment / measurements
    components: dict[str, ComponentStatus] = Field(default_factory=dict)
    metrics: dict[str, MetricValue] = Field(default_factory=dict)
    last_error: ErrorInfo | None = None

    # Free-form per-equipment data; safe to display in a debug/details panel.
    details: dict[str, Any] = Field(default_factory=dict)
```

## Probe Endpoints

`GET /` returns:

```python
class ProbeResponse(BaseModel):
    equipment_id: str
    equipment_name: str
    protocol_version: str
```

`GET /health` returns:

```python
class HealthResponse(BaseModel):
    status: Literal["healthy"] = "healthy"
```

## Best Practices (Normative)

1. **`GET /status` MUST be side-effect-free.** The aggregator polls it every 2-3 seconds. It must never trigger initialization, connection, or movement. If the hardware is not yet initialized, return `equipment_status: "requires_init"` with `required_actions: ["startup"]` (or whatever action is needed) - do not call init from the status handler.

2. **Always return HTTP 200 from `/status`** when the process is alive. Reserve non-2xx for genuine service failures (process crashed, dependency unreachable from the equipment's perspective). Hardware-not-initialized is a *state*, not an error.

3. **Schema versioning via `protocol_version`.** Bump on breaking changes. The dashboard logs a warning when a device reports a different major version.

4. **All timestamps are UTC ISO-8601 with timezone** (e.g. `2026-04-29T22:50:01Z`). Never local time.

5. **Units belong in `metrics`, not field names.** Prefer `metrics["flow_rate"] = {value: 50.0, unit: "mg/s"}` over `flow_rate_mg_per_s`. Legacy fields can stay in `details` for backwards compatibility.

6. **Errors are structured.** Don't pack everything into `message`. Use `last_error: {code, message, severity, timestamp}`.

7. **Snake_case** for all field names everywhere.

8. **No secrets in `/status`.** No API keys, no PII, no auth tokens, no internal credentials.

9. **`/status` is current state only.** Historical events go to a future `/events` route or a centralized log stream - never inflate the status response with logs.

10. **CORS.** Allow `Access-Control-Allow-Origin` for the dashboard server's hostname. This keeps devices debuggable from a browser even when the aggregator is down.

11. **Auth.** None at the equipment-repo level for v1. Tailscale ACLs gate access. Document this in each repo's README. If a device ever leaves the Tailnet, put it behind oauth2-proxy at the edge - never roll auth into individual equipment repos.

12. **Network identity is the registry's job, not the device's.** Do not include `equipment_ip` or `equipment_tailscale` in `/status`. The dashboard's `equipment.yaml` is the single source of truth for "where to reach this device". Repos should remove any `_get_wlan_ip()` / `_get_tailscale_ip()` self-discovery helpers.

13. **Stable enums.** `equipment_status` and `equipment_kind` use the closed enums above. To extend, propose a PR against this spec doc.

14. **Component naming.** Use snake_case component keys (e.g. `gantry`, `solid_doser`, `actuator`, `magnet_sensor`). Pick stable names; renaming a component breaks dashboards.

## Conformance Checklist (per repo)

When migrating a repo to this spec, the PR should include:

- [ ] `models.py` copied from this spec (or imported from `lab-status-contract` once it exists).
- [ ] `GET /` returning `ProbeResponse`.
- [ ] `GET /health` returning `HealthResponse`.
- [ ] `GET /status` returning `EquipmentStatus`, side-effect-free, always 200 unless broken.
- [ ] `GET /openapi.json` available (FastAPI gives this for free; Flask repos either migrate to FastAPI or use `flask-openapi3`).
- [ ] CORS configured to allow the dashboard server origin.
- [ ] No `equipment_ip` / `equipment_tailscale` self-discovery code remaining.
- [ ] Snapshot fixtures saved under `tests/fixtures/status_*.json` covering at least the realistic states (ready, requires_init, error, dry_run if applicable).
- [ ] `README.md` mentions: "This repo conforms to lab status spec v1.0".

For *deploying* the repo to the actual lab PC (uv environment, NSSM service registration, log paths, multi-device hosting), follow `docs/DEVICE_PC_SETUP.md`. Each device repo's README should link to that document rather than re-deriving the recipe.

## Reference Examples

### Solid doser (`dose_every_well`) - `requires_init`

```json
{
  "protocol_version": "1.0",
  "equipment_id": "dose_every_well",
  "equipment_name": "Dose Every Well",
  "equipment_kind": "solid_doser",
  "equipment_version": "0.3.1",
  "host": "doser-pi",
  "equipment_status": "requires_init",
  "message": "Awaiting POST /startup",
  "required_actions": ["startup"],
  "device_time": "2026-04-29T22:50:01Z",
  "uptime_seconds": 312.4,
  "components": {
    "gantry":      {"connected": false, "state": "disconnected"},
    "solid_doser": {"connected": false, "state": "disconnected"},
    "balance":     {"connected": false, "state": "disconnected"}
  },
  "metrics": {
    "flow_rate": {"value": 0.0, "unit": "mg/s"}
  },
  "details": {
    "available_configs": ["with_cnc_solid_doser", "balance_only"]
  }
}
```

### Filtration press (`filter_every_well`) - `ready`

```json
{
  "protocol_version": "1.0",
  "equipment_id": "filter_every_well",
  "equipment_name": "Waters Filtration",
  "equipment_kind": "press",
  "host": "filter-pi",
  "equipment_status": "ready",
  "message": "System idle, plate not loaded",
  "device_time": "2026-04-29T22:50:01Z",
  "components": {
    "press_valve": {"connected": true, "state": "up"},
    "plate":       {"connected": true, "state": "out"}
  }
}
```

### Fume hood actuator (`fume_hood_actuator`) - `busy`

```json
{
  "protocol_version": "1.0",
  "equipment_id": "fume_hood_actuator",
  "equipment_name": "Fume Hood Actuator",
  "equipment_kind": "fume_hood",
  "host": "hood-pi",
  "equipment_status": "busy",
  "message": "Moving sash from preset 2 to preset 4",
  "device_time": "2026-04-29T22:50:01Z",
  "components": {
    "actuator": {"connected": true, "state": "moving"}
  },
  "metrics": {
    "sash_position":   {"value": 2, "unit": "preset"},
    "target_position": {"value": 4, "unit": "preset"}
  }
}
```

### Environmental sensors - `ready`

```json
{
  "protocol_version": "1.0",
  "equipment_id": "env_sensors",
  "equipment_name": "Lab Environmental Sensors",
  "equipment_kind": "environmental_sensor",
  "host": "env-pi",
  "equipment_status": "ready",
  "device_time": "2026-04-29T22:50:01Z",
  "metrics": {
    "temperature": {"value": 22.3, "unit": "C"},
    "humidity":    {"value": 45.1, "unit": "%RH"},
    "voc":         {"value": 120,  "unit": "ppb"}
  }
}
```
