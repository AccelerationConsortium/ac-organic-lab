# Lab Equipment Status Spec

**Current version:** `1.1` (additive over `1.0`; v1.0 devices remain valid).
**Status:** authoritative contract for every lab equipment REST API displayed on the AC Organic Self-Driving Lab dashboard. v1.0 is the *baseline*; v1.1 adds cooperative claims, `allowed_actions`, and `details.claimed_by`. The two coexist on the wire and in `equipment.yaml`; the SDK degrades gracefully when talking to v1.0 devices.

Each equipment repo copies the Pydantic models below into its own `models.py`. Once the spec has been stable for ~1 month and 3+ repos have migrated cleanly to v1.1, these types will be promoted into a shared `lab-status-contract` Python package and per-device repos will `from lab_status_contract import ...` instead of vendoring a copy.

> **Layered safety:** this spec is one of four interlock layers. Hardware limits (layer 1) and device state machine (layer 2) live in the device repos; skill preconditions (layer 3) and project plan interlocks (layer 4) live in `skills/` and project repos respectively. See [`INTERLOCKS.md`](INTERLOCKS.md). The catalog of skills the SDK can dispatch is described in [`SKILLS_CATALOG.md`](SKILLS_CATALOG.md). For a wider perspective on how this contract relates to other lab automation standards, see the [Comparison with SiLA 2 appendix](#appendix-a--comparison-with-sila-2) at the bottom of this document.

---

## 1. Required HTTP Surface

### v1.0 baseline — read endpoints

| Method | Path             | Always 200? | Description |
|--------|------------------|-------------|-------------|
| GET    | `/`              | Yes         | Minimal probe: `{equipment_id, equipment_name, protocol_version}`. |
| GET    | `/health`        | Yes         | Service liveness from the dashboard's perspective. |
| GET    | `/status`        | Yes*        | Full `EquipmentStatus` envelope. *Non-200 only when the process is broken.* |
| GET    | `/openapi.json`  | Yes         | OpenAPI document. FastAPI generates this automatically. |

### v1.1 delta — claim control endpoints

| Method | Path                  | Always 200? | Description |
|--------|-----------------------|-------------|-------------|
| POST   | `/control/claim`      | No          | Acquire a claim. Returns a token + heartbeat interval. |
| POST   | `/control/heartbeat`  | No          | Refresh the claim's TTL. Header `X-Claim-Token` required. |
| POST   | `/control/release`    | No          | Release the claim. Header `X-Claim-Token` required. |

Future, optional (out of scope for v1.1):

- `POST /control/<verb>` — per-device control endpoints, gated by explicit body schemas. The catalog of verbs is owned by the device repo (see [`SKILLS_CATALOG.md`](SKILLS_CATALOG.md)).
- `WS /ws` — pushes the same `EquipmentStatus` envelope on changes.

---

## 2. The Envelope

```python
from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field


PROTOCOL_VERSION = "1.1"   # v1.0 devices stay on "1.0"


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
    "shaker",        # orbital shaker w/ heater (e.g. Torrey Pines SC20)
    "camera",        # PTZ + lenses, fronted by kasa-tapo-services
    "smart_plug",    # single-outlet (e.g. Kasa HS103)
    "power_strip",   # multi-outlet (e.g. Kasa HS300)
    "other",
]

EquipmentState = Literal[
    "ready",          # initialized, idle, can accept commands
    "busy",           # performing an operation
    "requires_init",  # service up but hardware not initialized (e.g. needs POST /startup or /connect)
    "degraded",       # running but a sub-component is unhealthy
    "dry_run",        # simulation mode, no hardware connected
    "error",          # device is REACHABLE and reported a hardware/subsystem fault — NOT "couldn't reach it" (see §2.1)
    "e_stop",         # emergency stopped
    "unknown",        # state cannot be determined — honest fallback, NOT a failure signal on its own (see §2.1)
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


class ClaimedBy(BaseModel):
    """v1.1: identity of the current claim holder. Surfaced on /status so
    every reader sees who currently controls the device without a side trip."""

    session_id: str
    owner: str
    expires_at: datetime


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

    # NEW in v1.1
    allowed_actions: list[str] = Field(default_factory=list)
    # `details.claimed_by` is a ClaimedBy | None; nested under details to keep
    # the top-level shape stable for v1.0 readers.

    # Free-form per-equipment data; safe to display in a debug/details panel.
    details: dict[str, Any] = Field(default_factory=dict)
```

### 2.1 `unknown` vs `error` vs "unreachable" (normative)

These three are routinely confused. They are not interchangeable:

- **`error`** — the device is **reachable** and a hardware/subsystem fault was **reported**. Reserve it for genuine faults the device can actually observe (driver error, over-temperature, a jam, a sub-component reporting failure). **Never** use `error` to mean "I couldn't reach something." If you didn't get an answer, you don't know there's a fault.
- **`unknown`** — the device's state **cannot be determined**. This is the honest fallback when there is no better answer: a cold start before the first successful poll, an unobserved gap in history, or a state machine that genuinely cannot resolve its state. `unknown` is **not** a failure signal on its own, and for uptime accounting it is treated as *up* (you never established that the device was down — you just have no information).
- **"unreachable"** is **not** an `EquipmentState` — there is deliberately no such enum value. It is a **reader-side (dashboard) presentation** concept: the aggregator's poll of `/status` failed at the transport layer (timeout / connection refused), which it records as a `fetch_error`. The dashboard renders any device carrying a `fetch_error` as **"unreachable"** (offline, counted as *down*), independent of the placeholder `equipment_status` in the synthetic envelope (which is `unknown`).

**Gateway-fronted devices (normative).** A *gateway-fronted* device is one whose hardware is reached over a secondary link behind a shared gateway service (e.g. `kasa-tapo-services` fronting Tapo cameras and Kasa plugs; any future multi-device proxy). When the gateway process is healthy but **cannot reach the backing hardware**, two rules apply:

1. **The gateway MUST report `equipment_status: "unknown"`** (with a `message` explaining why, e.g. `"No route to host"` / `"Camera unreachable: neither ONVIF nor Tapo API responded"`), **not** `error`. Nothing faulted — the device simply can't be reached, so its state cannot be determined. Per best-practice #2 the gateway still returns **HTTP 200** (its own process is alive), so the aggregator records **no** `fetch_error` for it. `error` remains reserved for a *reachable* backing device whose subsystem reports a fault (e.g. a camera answering ONVIF but with go2rtc down → `degraded`; a fault the hardware actually reports → `error`).

2. **The dashboard treats a gateway-fronted kind reporting `unknown` as "unreachable"** for presentation and uptime. Because such a device is genuinely offline yet produces no transport-level `fetch_error`, the offline interpretation is applied at the presentation layer (keyed on `equipment_kind` ∈ {`camera`, `smart_plug`, `power_strip`}). The on-the-wire contract value stays `unknown` — a device must never invent an out-of-enum state — but the operator sees "unreachable", consistent with a directly-polled device that timed out.

**Net rule:** an `unknown` the reader can attribute to a known reachability failure (a transport `fetch_error`, or a gateway-fronted kind reporting `unknown`) renders as **"unreachable"** and counts as *down*; a bare `unknown` with no such attribution (cold start, not-yet-observed) stays **"unknown"** and counts as *up*. Devices should drive toward a precise state whenever they can — `unknown` is the answer of last resort, not a routine one.

### v1.1 field semantics

`allowed_actions`:
- A flat list of skill names (matching `Skill.name` from the SDK catalog, e.g. `"seal.start"`, `"stage.in"`).
- The device is the authority. The list reflects "what would the device honor *right now* if you POSTed it".
- Empty list (or field absent on a v1.0 device) means the SDK falls back to `requires_states` from the catalog. This is the back-compatibility contract shipped in `lab-skills` v0.2+.
- Devices that have not yet migrated to v1.1 simply omit the field.

`details.claimed_by`:
- `null` (or missing) when no claim is active.
- A `ClaimedBy` object when a claim is active. `expires_at` is the heartbeat-extended absolute UTC timestamp.

---

## 3. Probe Endpoints

`GET /` returns:

```python
class ProbeResponse(BaseModel):
    equipment_id: str
    equipment_name: str
    protocol_version: str   # "1.0" or "1.1"
```

`GET /health` returns:

```python
class HealthResponse(BaseModel):
    status: Literal["healthy"] = "healthy"
```

---

## 4. Best Practices (Normative)

1. **`GET /status` MUST be side-effect-free.** The aggregator polls it every 2–3 seconds. It must never trigger initialization, connection, or movement. If the hardware is not yet initialized, return `equipment_status: "requires_init"` with `required_actions: ["startup"]` (or whatever action is needed) — do not call init from the status handler.

2. **Always return HTTP 200 from `/status`** when the process is alive. Reserve non-2xx for genuine service failures (process crashed, dependency unreachable from the equipment's perspective). Hardware-not-initialized is a *state*, not an error.

3. **Schema versioning via `protocol_version`.** Bump on breaking changes. The dashboard logs a warning when a device reports a different major version.

4. **All timestamps are UTC ISO-8601 with timezone** (e.g. `2026-04-29T22:50:01Z`). Never local time.

5. **Units belong in `metrics`, not field names.** Prefer `metrics["flow_rate"] = {value: 50.0, unit: "mg/s"}` over `flow_rate_mg_per_s`. Legacy fields can stay in `details` for backwards compatibility.

6. **Errors are structured.** Don't pack everything into `message`. Use `last_error: {code, message, severity, timestamp}`. `code` SHOULD be a stable enum drawn from a per-repo taxonomy so clients can branch on `code` and surface targeted recovery hints, rather than string-matching on `message`. Define the taxonomy as a `frozenset[str]` or `Literal[...]` in one place; require every mutation site to use a setter that validates against it. Each device repo defines its own `last_error.code` taxonomy and documents it in that repo's README; the spec only requires the set be stable.

7. **Snake_case** for all field names everywhere.

8. **No secrets in `/status`.** No API keys, no PII, no auth tokens, no internal credentials.

9. **`/status` is current state only.** Historical events go to the dashboard's history DB (see [`OBSERVABILITY.md`](OBSERVABILITY.md)) — never inflate the status response with logs.

10. **CORS.** Allow `Access-Control-Allow-Origin` for the dashboard server's hostname. This keeps devices debuggable from a browser even when the aggregator is down.

11. **Auth.** None at the equipment-repo level for v1. Tailscale ACLs gate access. Document this in each repo's README. If a device ever leaves the Tailnet, put it behind oauth2-proxy at the edge — never roll auth into individual equipment repos.

12. **Network identity is the registry's job, not the device's.** Do not include `equipment_ip` or `equipment_tailscale` in `/status`. The dashboard's `equipment.yaml` is the single source of truth for "where to reach this device". Repos should remove any `_get_wlan_ip()` / `_get_tailscale_ip()` self-discovery helpers.

13. **Stable enums.** `equipment_status` and `equipment_kind` use the closed enums above. To extend, propose a PR against this spec doc.

14. **Component naming.** Use snake_case component keys (e.g. `gantry`, `solid_doser`, `actuator`, `magnet_sensor`). Pick stable names; renaming a component breaks dashboards.

15. **Precondition refusals use HTTP 412 with structured bodies.** When `/control/<X>` is invoked while a per-action precondition is violated (heater out of band, plate stage not loaded, …), return 412 with a JSON body distinguishable by *shape*, not by `detail` text. Set `Retry-After` when recovery is time-bounded. See §6 for the full pattern.

16. **`allowed_actions` mirrors precondition refusals.** If `/control/<X>` would currently 412, `/status.allowed_actions` MUST omit `<X>`. Implement via a single helper called from both surfaces; the SDK and dashboard otherwise spend effort compensating for drift. See §6.2.

17. **`last_error` auto-clears on the next successful operational action.** Reserved for execution failures, not precondition refusals. 412 responses never mutate it. See §6.3 / §6.4.

---

## 5. v1.1 — Claim Protocol

### Why claims at all

A v1.0 device has no notion of who is talking to it. Two clients hitting `/control/seal/start` simultaneously would race. v1.1 adds a cheap optimistic lock: clients ask for a claim, hold it via heartbeats, release it cleanly. Devices reject control commands from anyone but the holder.

Claims are **cooperative**, not authenticated. Any client could ignore the protocol. The SDK enforces it on the client side; devices that want hard enforcement check `X-Claim-Token` on `/control/*` and reject mismatches with HTTP 423. Devices that prefer to leave it advisory can publish `details.claimed_by` and let workflow code do the right thing.

### Lifecycle

```
client                                          device
  |                                                |
  |-- POST /control/claim {owner, session_id} --->|
  |<------- 200 {claim_token, heartbeat_interval_s, expires_at} --|
  |                                                |
  |== background heartbeat task ==                 |
  |   |                                            |
  |   |-- POST /control/heartbeat (X-Claim-Token)->|
  |   |<-- 204 (or 200 with new expires_at) ------|
  |   |                                            |
  |   ... every heartbeat_interval_s ...           |
  |                                                |
  |-- POST /control/seal/start (X-Claim-Token) -->|
  |<-- 200 ----------------------------------------|
  |                                                |
  |-- POST /control/release (X-Claim-Token) ----->|
  |<-- 204 ----------------------------------------|
```

### `POST /control/claim`

Request:

```python
class ClaimRequest(BaseModel):
    owner: str            # human or agent identifier; surfaced in details.claimed_by
    session_id: str       # opaque per-session id (UUID is recommended)
    ttl_s: float = 30.0   # device may clamp to its own min/max
```

Success response (HTTP 200):

```python
class ClaimResponse(BaseModel):
    claim_token: str
    heartbeat_interval_s: float   # caller MUST send heartbeats more often than this
    expires_at: datetime          # absolute UTC; claim dies at this time without a heartbeat
```

Rejection (HTTP 409 Conflict — already-claimed by another session):

```python
class ClaimRejection(BaseModel):
    detail: str
    claimed_by: ClaimedBy | None   # who currently holds it (best-effort)
    retry_after_s: float | None    # advisory; clients SHOULD also honor Retry-After header
```

Other rejection codes:

- **HTTP 423 Locked** — like 409, but emphasizes hard enforcement. Treated identically by the SDK.
- **HTTP 422** — request body invalid.
- **HTTP 503** — device not ready to issue claims (e.g. still booting). Includes `Retry-After`.

A second `POST /control/claim` from the *same* `session_id` while a claim is already held by that session is **idempotent**: the device returns 200 with the existing token (or rotates and returns a fresh one — the SDK handles both).

### `POST /control/heartbeat`

Header: `X-Claim-Token: <token>` (required).
Body: empty.

Success: HTTP 204 No Content. The device extends the claim's TTL.

Optional: HTTP 200 with `{"expires_at": "..."}` so the client can observe the new TTL.

Failure modes:

- **HTTP 401 Unauthorized** — token is unknown / expired / belongs to a different session. Client MUST treat the claim as lost.
- **HTTP 404** — device has restarted and forgotten this claim. Client MUST treat as lost.

### `POST /control/release`

Header: `X-Claim-Token: <token>`.
Body: empty.

Success: HTTP 204. **Idempotent** — releasing an unknown / already-released token also returns 204 (releasing should never fail in a way that prevents the client from moving on).

### Hard-enforcement on `/control/*`

A v1.1 device that wants to enforce claims SHOULD check `X-Claim-Token` on every `/control/<endpoint>` request:

- Header missing or stale → HTTP 423 Locked, body explaining the active claim.
- Header valid → proceed.

A v1.1 device that wants to keep claims advisory MAY accept `/control/*` without `X-Claim-Token`. In this mode `details.claimed_by` is the only signal; client-side code is expected to honor it.

---

## 6. Preconditions and Refusals (v1.1+)

Some `/control/*` actions are only meaningful when the device is in a specific runtime state — heater within band, plate stage loaded, etc. — distinct from the coarse `equipment_status` enum. v1.1 codifies how a device should refuse such actions, and how `allowed_actions` should reflect those refusals so clients can avoid the round-trip.

This section is normative for v1.1 devices that implement any precondition richer than `equipment_status in requires_states`. The body shapes in §6.1 are the spec's reference taxonomy; per-device READMEs document their own precondition catalog.

### 6.1 Refusing with HTTP 412 Precondition Failed

When `/control/<action>` is invoked while a precondition is violated, the device SHOULD return **HTTP 412 Precondition Failed** with a structured JSON body. Distinct preconditions get distinct body shapes so clients can branch on the shape, not on `detail` string-matching.

Required body fields:

- `detail: str` — short human-readable summary (used as a fallback when the client doesn't recognise the body shape).

Recommended additional fields (per precondition type):

- `retry_after_s: float | int | null` — best-effort estimate of seconds until the precondition is expected to clear, when the precondition resolves over time (heater ramp, queue drain). `null` when recovery is operator-driven (e.g. load a plate). When non-null, the device SHOULD also set a `Retry-After: <seconds>` HTTP header.
- Precondition-specific fields named to make the body shape self-describing. Examples in the reference implementation:

```json
// Temperature interlock (plateloc v1.2+)
{
  "detail":         "Temperature outside seal band",
  "actual_c":       166.0,
  "setpoint_c":     170.0,
  "tolerance_c":    2.0,
  "retry_after_s":  2
}

// Stage interlock (plateloc v1.3+)
{
  "detail":      "Stage not loaded",
  "stage_state": "out",
  "required":    "in"
}
```

Why 412 (and not 409 or 422):

- **409 Conflict** is appropriate for device-state conflicts ("driver not connected; call /control/startup first"). Reserved for that.
- **422 Unprocessable Entity** is for invalid request bodies (FastAPI's default). The body is fine here; the *device state* isn't.
- **412 Precondition Failed** is the closest semantic fit: "your request would be valid if a precondition were met, and it isn't right now."

A device MAY choose a different code if it has a strong local convention, but 412 is the recommended default and what the dashboard / SDK render targeted hints for.

### 6.2 `allowed_actions` must mirror precondition refusals

If a device returns 412 from `POST /control/<X>` whenever precondition P is violated, then `GET /status` MUST omit `<X>` from `allowed_actions` whenever P is currently violated.

In other words: **`allowed_actions` is advisory and `/control/*`'s 412 is authoritative, but the two must never disagree.** A client that reads `/status`, sees `<X>` in `allowed_actions`, and immediately POSTs `/control/<X>` must not get a 412 — that's a contract violation.

Implementation pattern (proven out by plateloc v1.2.1+):

- Extract the precondition check into a single helper, e.g. `evaluate_temperature_interlock() -> tuple[bool, dict | None]` (returns `(should_block, body_for_412)`).
- The `/control/<X>` endpoint calls the helper and returns 412 with the body on block.
- The `/status` response builder calls the same helper (just inspecting the boolean) when deciding whether to include `<X>` in `allowed_actions`.
- Override flags (see §6.4) short-circuit the helper, so they affect both surfaces identically.

This is enforced as a property test on the reference implementation: for every value of the relevant runtime state, `<X> in allowed_actions` iff a hypothetical POST would NOT return 412. The two surfaces cannot drift.

### 6.3 `last_error` semantics for precondition refusals

Precondition refusals (412 in §6.1) are **not** operational failures. They are the device declining an inapplicable request — the equipment is healthy, the request just doesn't make sense right now. Therefore:

- 412 responses MUST NOT populate `last_error`. `last_error` is reserved for things that *went wrong during execution* (driver errors, mid-cycle hardware faults, communication timeouts).
- The distinction matters for dashboards / history DBs: 412 is render-once-then-discard; `last_error` is "what was the most recent thing that broke."

### 6.4 `last_error` auto-clear policy

A device SHOULD clear `last_error` to `null` on the first 2xx response from any **operational** `/control/<X>` endpoint after the error was recorded. "Operational" means actions that drive the underlying hardware or workflow state, not infrastructure calls:

| Endpoint kind | Clear on 2xx? |
|---|---|
| Hardware actions (`/control/startup`, `/control/seal/start`, `/control/stage/in`, etc.) | Yes |
| Heartbeat / claim release (`/control/heartbeat`, `/control/release`) | No |
| Claim acquisition (`/control/claim`) | No |
| Read endpoints (`/`, `/health`, `/status`) | No |
| 4xx / 5xx from any endpoint | No |

The clearing happens *before* the response body is built, so a successful action that echoes a status snapshot in its body returns the cleared state.

Why: `last_error` should mean "the most recent operational failure since the last successful action," not "the most recent failure since process start." Otherwise stale errors from hours ago surface alongside a currently-healthy device — the operator wonders why the tile is screaming when nothing is wrong.

### 6.5 Override flags for emergency operation

A device MAY expose a config flag to disable a precondition interlock at runtime (e.g. `enforce_temp_interlock`, `enforce_stage_interlock`). When set false:

- The 412 path returns the original 2xx (the action runs as if the precondition passed).
- `allowed_actions` includes `<X>` regardless of the precondition's current value.

Both surfaces must honor the flag identically — which falls out naturally from the single-helper pattern in §6.2.

Override flags exist for emergency calibration / debugging. They are **not** intended for production. The device's README should call out that they exist and that there is no plan to ship with them disabled in production.

### 6.6 SDK consumption

The `lab-skills` SDK consumes §6 patterns as follows:

- **Precondition-aware availability.** `SkillDef.requires_components: dict[str, str]` is an AND-gate layered on top of `allowed_actions` / `requires_states`. The SDK pre-checks per-component state (e.g. `{"heater": "stable", "stage": "in"}` for `seal.start`) before issuing the call, so workflow code sees `available=False` with a useful reason rather than round-tripping a 412. See [`docs/SKILLS_CATALOG.md`](SKILLS_CATALOG.md).
- **Structured 412 handling.** The dashboard's control passthrough (`api/app/control.py`) forwards 412 bodies verbatim so the frontend can branch on the body shape. The lab-skills SDK is expected to grow a typed `PreconditionNotMet(LabError)` exception in v0.4 that distinguishes 412 from `ClaimRejected(409|423)` and exposes the body for diagnostic rendering.

---

## 7. SDK side (`lab-skills`) — v1.1 surface

Implemented in `lab-skills` v0.3:

- `lab_skills.ClaimManager(client, *, owner, session_id, ttl_s=30.0)` async context manager:
  - `__aenter__`: POSTs `/control/claim`, starts the heartbeat background task, returns `self`.
  - On HTTP 409/423: raises `ClaimRejected(retry_after_s=…)`. Honors `retry_after_s` from the JSON body, falling back to the `Retry-After` header.
  - On HTTP 404 / 405 from `/control/claim`: device is v1.0 (does not implement claims). The manager enters **degraded mode** silently — no heartbeat, no release call. This is the graceful-degradation contract: workflows can wrap any device in `ClaimManager` and v1.0 devices behave as no-ops.
  - `__aexit__`: cancels the heartbeat task, POSTs `/control/release` (best-effort).
- Heartbeat: a single asyncio task. After **three consecutive** heartbeat failures it self-cancels, stores an `EquipmentUnreachable` on the manager, and re-raises that on the next call to `claim.assert_alive()` or on `__aexit__`. Three was chosen to mirror read-side resilience.
- `lab_skills.ClaimRejected(LabError)` — exception type. `retry_after_s: float | None` and `claimed_by: ClaimedBy | None` for diagnostics.

### Plan validation, claims, and interlocks

`validate_plan(plan, session)` (also in v0.3) is **offline**: no HTTP. It does, however, annotate per-step `warnings` based on registry-declared device capability:

- A step that targets an `EquipmentEntry` with `protocol = "1.0"` produces a `warnings: ["no_claim_semantics"]` entry. The plan can still be executed; the warning surfaces that no mutual-exclusion guarantee will be in effect.
- A step that targets `protocol = "1.1"` adds no warning. `execute_plan` (v0.4) will wrap it in `ClaimManager`.

The `protocol` field on `EquipmentEntry` in the registry defaults to `"1.0"`, so existing `equipment.yaml` files stay valid. To opt a migrated device into v1.1 semantics, add `protocol: "1.1"` to its yaml entry.

---

## 8. Back-compatibility (normative)

The SDK's contract for v1.0 devices, post-v1.1:

| Surface             | v1.0 device              | v1.1 device              |
|---------------------|--------------------------|--------------------------|
| `/status`           | no `allowed_actions`     | populates `allowed_actions` |
| `Skill.available`   | from `requires_states`   | from `allowed_actions` first, falls back to `requires_states` |
| `ClaimManager`      | degraded (no-op)         | claims + heartbeat + release |
| `validate_plan` warning | `["no_claim_semantics"]` | none |
| `execute_plan` (v0.4) | runs without claim wrap | wraps each step in `ClaimManager` |

A workflow that talks to a mix of v1.0 and v1.1 devices in the same `LabSession` is supported and is the expected migration path.

---

## 9. Conformance Checklists

### v1.0 (read-only baseline)

A repo is considered v1.0 conformant when:

- [ ] `models.py` carries the STATUS_SPEC v1.0 Pydantic types verbatim (or imports them from a shared package once one is published).
- [ ] `GET /` returns `ProbeResponse(equipment_id, equipment_name, protocol_version="1.0")`.
- [ ] `GET /health` returns `HealthResponse(status="healthy")`.
- [ ] `GET /status` returns `EquipmentStatus` with snake_case field names. **Side-effect-free.** Always 200 unless the process is broken.
- [ ] `GET /openapi.json` is served (FastAPI gives this for free; Flask repos either migrate to FastAPI or use `flask-openapi3`).
- [ ] All control endpoints under `/control/*`, gated by Pydantic body schemas with `Field(ge=, le=)` ranges.
- [ ] CORS allows the dashboard origin.
- [ ] No `_get_wlan_ip()` / `_get_tailscale_ip()` self-discovery code.
- [ ] Snapshot fixtures saved under `tests/fixtures/status_*.json` covering at least: `ready`, `requires_init`, `error`, `dry_run` (if applicable).
- [ ] `README.md` says "This repo conforms to lab status spec v1.0".
- [ ] `equipment.yaml` flipped from `adapter: legacy_http` (or `mock`) to `adapter: http`.

### v1.1 (additive over v1.0)

A repo is considered v1.1 conformant when, on top of v1.0:

- [ ] `protocol_version` reported on `/` and `/status` is `"1.1"`.
- [ ] `POST /control/claim`, `POST /control/heartbeat`, `POST /control/release` implemented per §5 above.
- [ ] `EquipmentStatus.allowed_actions` is populated with the skill names (matching `Skill.name` from the catalog) the device will currently honor.
- [ ] `details.claimed_by` populated while a claim is held; cleared on release / expiry.
- [ ] `X-Claim-Token` enforced on `/control/*` (recommended; HTTP 423 on miss) **or** the README documents that claims are advisory.
- [ ] If the device implements any precondition richer than `equipment_status in requires_states`, it follows §6:
  - [ ] Refusals return HTTP 412 with a structured JSON body distinguishable by shape (not by `detail` string).
  - [ ] When recovery is time-bounded, `retry_after_s` is populated and a `Retry-After` header is set.
  - [ ] `allowed_actions` omits the action whenever the device would 412 it — single source of truth (recommended: a `evaluate_<name>_interlock()` helper called from both surfaces).
  - [ ] 412 responses do NOT mutate `last_error`.
  - [ ] `last_error` auto-clears to `null` on the first 2xx response from any operational `/control/*` endpoint. Heartbeat / claim / read endpoints do not clear.
  - [ ] Optional config flag (`enforce_<name>_interlock`, default `true`) short-circuits both surfaces identically.
- [ ] Snapshot fixtures cover `ready` with no claim, `ready` with a claim held, `requires_init`. If §6 preconditions exist, also cover the precondition-blocked shape (`allowed_actions` lacking the gated skill) plus a successfully-failed-then-cleared `last_error` lifecycle.
- [ ] `README.md` says "This repo conforms to lab status spec v1.1".
- [ ] `equipment.yaml` entry has `protocol: "1.1"`.

A repo that does **not** want to opt into v1.1 stays on v1.0 unchanged. The SDK treats it as "no claim semantics, fall back to v1.0 catalog `requires_states`".

For *deploying* the repo to the actual lab PC (uv environment, NSSM service registration, log paths, multi-device hosting), follow [`DEVICE_PC_SETUP.md`](DEVICE_PC_SETUP.md). Each device repo's README should link to that document rather than re-deriving the recipe.

For onboarding the device into the dashboard registry and handling maintenance windows, see [`EQUIPMENT_INTEGRATION.md`](EQUIPMENT_INTEGRATION.md).

---

## 10. Reference Examples

### Solid doser (`dose_every_well`) — v1.0 `requires_init`

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

### Filtration press (`filter_every_well`) — v1.1 `ready`, no claim held

```json
{
  "protocol_version": "1.1",
  "equipment_id": "filter_every_well",
  "equipment_name": "Waters Filtration",
  "equipment_kind": "press",
  "host": "filter-pi",
  "equipment_status": "ready",
  "message": "System idle, plate not loaded",
  "device_time": "2026-04-29T22:50:01Z",
  "allowed_actions": ["press.up", "press.down", "stage.in", "stage.out"],
  "components": {
    "press_valve": {"connected": true, "state": "up"},
    "plate":       {"connected": true, "state": "out"}
  },
  "details": {
    "claimed_by": null
  }
}
```

### PlateLoc (`plateloc`) — v1.1 `requires_init`, claim held

```json
{
  "protocol_version": "1.1",
  "equipment_id": "plateloc",
  "equipment_name": "Agilent PlateLoc",
  "equipment_kind": "plate_sealer",
  "host": "sdl2-pc-03-cytation",
  "equipment_status": "requires_init",
  "message": "Awaiting POST /control/startup",
  "required_actions": ["startup"],
  "device_time": "2026-04-29T22:50:01Z",
  "allowed_actions": ["startup"],
  "details": {
    "claimed_by": {
      "session_id": "f1f1c1a2-…",
      "owner": "agent:solubility-screening",
      "expires_at": "2026-04-29T22:50:31Z"
    }
  }
}
```

### PlateLoc (`plateloc`) — v1.1 `ready`, heater warming up (§6 precondition active)

This is the reference implementation of §6.2: the heater is below band, so the device omits `seal.start` from `allowed_actions`. A client that POSTed `/control/seal/start` right now would receive HTTP 412 with the temperature-interlock body shape (§6.1). `last_error` is `null` because the last operational action (`seal/set_temperature` here) succeeded (§6.4).

```json
{
  "protocol_version": "1.1",
  "equipment_id": "plateloc",
  "equipment_name": "Agilent PlateLoc",
  "equipment_kind": "plate_sealer",
  "host": "sdl2-pc-03-cytation",
  "equipment_status": "ready",
  "message": "Heater warming up",
  "device_time": "2026-05-23T17:30:00Z",
  "allowed_actions": [
    "startup", "shutdown",
    "seal.set_temperature", "seal.set_time",
    "stage.in", "stage.out"
  ],
  "components": {
    "sealer": {"connected": true, "state": "idle"},
    "heater": {"connected": true, "state": "heating", "message": "Warming to setpoint (170 C)"},
    "stage":  {"connected": true, "state": "in"}
  },
  "metrics": {
    "actual_temperature":   {"value": 150, "unit": "C"},
    "setpoint_temperature": {"value": 170, "unit": "C"},
    "sealing_time":         {"value": 1.2, "unit": "s"},
    "cycle_count":          {"value": 1820, "unit": "count"}
  },
  "last_error": null,
  "details": {
    "temperature_tolerance_c": 2.0,
    "claimed_by": null
  }
}
```

### Fume hood actuator (`fume_hood_actuator`) — v1.0 `busy`

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

### Environmental sensors — v1.0 `ready`

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

### Tapo PTZ camera (`camera`) — v1.0 `ready`

Cameras are hosted by the `kasa-tapo-services` gateway (one FastAPI process for many devices). Per-camera, `details` carries the lens list, the preset list, and the current privacy/streaming flags so the frontend tile can render itself without a second round-trip. One `ComponentStatus` is published per physical lens under `lens_<id>`.

```json
{
  "protocol_version": "1.0",
  "equipment_id": "cam_lab499_west",
  "equipment_name": "Lab 499 (West) Camera",
  "equipment_kind": "camera",
  "host": "192.168.1.42",
  "equipment_status": "ready",
  "device_time": "2026-04-29T22:50:01Z",
  "components": {
    "lens_wide": {"connected": true, "state": "connected", "message": "Wide"},
    "lens_tele": {"connected": true, "state": "connected", "message": "Tele"}
  },
  "allowed_actions": ["ptz", "preset/save", "preset/goto", "preset/{id}", "privacy", "streaming"],
  "details": {
    "lenses": [
      {"id": "wide", "label": "Wide", "rtsp_path": "stream1", "mse_url": "/streams/api/ws?src=cam_lab499_west_wide", "stream_connected": true},
      {"id": "tele", "label": "Tele", "rtsp_path": "stream2", "mse_url": "/streams/api/ws?src=cam_lab499_west_tele", "stream_connected": true}
    ],
    "presets": [
      {"id": "1", "name": "home"},
      {"id": "2", "name": "bench"}
    ],
    "privacy_mode": false,
    "streaming_enabled": true,
    "onvif_reachable": true,
    "tapo_reachable": true,
    "go2rtc_reachable": true
  }
}
```

### Kasa HS300 power strip (`power_strip`) — v1.0 `ready`

Power strips report one `ComponentStatus` per outlet; the dashboard renders the strip as a 6-row grid keyed by `outlet_<index>`. Per-outlet labels live in the gateway's `devices.yaml` and flow through to `components[outlet_*].message`.

```json
{
  "protocol_version": "1.0",
  "equipment_id": "plug_hotplate_strip",
  "equipment_name": "HTE Bench Hotplate Strip",
  "equipment_kind": "power_strip",
  "host": "192.168.1.51",
  "equipment_status": "ready",
  "device_time": "2026-04-29T22:50:01Z",
  "components": {
    "outlet_0": {"connected": true, "state": "on",  "message": "Hotplate A"},
    "outlet_1": {"connected": true, "state": "off", "message": "Hotplate B"},
    "outlet_2": {"connected": true, "state": "on",  "message": "Stirrer"}
  },
  "allowed_actions": ["on", "off", "toggle"]
}
```

---

## Appendix A — Comparison with SiLA 2

SiLA 2 (Standardization in Lab Automation, Part A v1.1) is the most widely used open laboratory automation standard. This appendix documents how the STATUS_SPEC defined above relates to it, so readers can see what we adopted, what we deliberately diverged on, and what an interop bridge would need to do.

**TL;DR.** STATUS_SPEC is a **thin, polled, REST/JSON status contract** with an opt-in cooperative claim layer. SiLA 2 is a **fully-typed gRPC RPC framework** with rich command/property semantics, native streaming, mDNS discovery, mTLS, and a formal IDL. STATUS_SPEC is dramatically simpler and easier to integrate from a browser; SiLA gives you stronger typing, eventing, and built-in security at the cost of much heavier tooling.

### Architectural deltas

#### 1. Transport & wire format

| | STATUS_SPEC | SiLA 2 |
|---|---|---|
| Transport | HTTP/1.1 + JSON (REST) | HTTP/2 + gRPC + Protobuf |
| Schema source | Pydantic + auto-generated `openapi.json` | `.sila.xml` Feature Definition Language (FDL) → generated `.proto` |
| Streaming | None (poll `/status` every 2–3 s; optional `WS /ws` for change pushes) | First-class bi-di streaming on observable commands & properties |
| Browser-friendly | Trivial (`curl`, `fetch`) | Needs grpc-web or a REST gateway |

The biggest practical consequence: STATUS_SPEC is *pollable from a web dashboard with no shim* (which is exactly what the Next.js UI does). A SiLA server needs grpc-web/Envoy to be reachable from a browser.

#### 2. Interaction model

STATUS_SPEC separates **read** (`GET /status` — single envelope, always 200) from **write** (`POST /control/<verb>` — verb-per-endpoint, defined by each repo).

SiLA defines three primitives per **Feature**:

- **Properties** (Unobservable: `Get_<Property>`; Observable: `Subscribe_<Property>` stream)
- **Commands** (Unobservable: request/response; Observable: stream of `ExecutionInfo` + `IntermediateResponses` + final response)
- **Metadata** (per-call sideband, e.g. for auth tokens)

In SiLA terms, the `EquipmentStatus` envelope is effectively *one giant aggregated observable property* on an implicit feature. SiLA would split it into many strongly-typed properties (e.g. `CurrentTemperature`, `DrawerState`) and let clients subscribe to deltas instead of polling the whole envelope.

#### 3. `equipment_kind` enum vs SiLA "Features"

STATUS_SPEC enumerates device *kinds* (`plate_reader`, `solid_doser`, `hplc`, …) and *states* in closed `Literal`s; concrete behavior is left to each repo's free-form `details` blob and `/control/*` endpoints.

SiLA inverts this. Devices are described as a *bag of Features*, each Feature uniquely identified by a Fully Qualified Identifier (e.g. `org.silastandard/core/LockController/v1`). A plate reader implements `PlateReader`, `TemperatureController`, `LockController`, etc., and the catalog of features *is* the contract. There is no `equipment_kind` field — clients introspect which features the server exposes.

#### 4. Locking — `claim/heartbeat/release` vs `LockController`

STATUS_SPEC v1.1 introduces cooperative claims via three HTTP endpoints under `/control/*` (§5 above).

SiLA has had **`org.silastandard/core/LockController/v1`** since the start. Same idea (lock identifier passed as `Metadata` on each call, TTL refreshed, `IsLocked` observable property), but:

- It's a **standard Feature** clients can introspect — no separate spec doc, the IDL is the spec.
- Enforcement is **per-call metadata**, not a separate header on a control plane. SiLA's metadata mechanism is generic, so you can stack auth + lock tokens uniformly.
- SiLA returns specific `DefinedExecutionError`s (e.g. `InvalidLockIdentifier`) rather than HTTP 409/423.

Our design is intentionally simpler — HTTP status codes + a header — but the semantics are essentially the same cooperative-lock pattern.

#### 5. Discovery

| STATUS_SPEC | SiLA 2 |
|---|---|
| Central registry (`equipment.yaml` in this monorepo is authoritative for "where to reach this device") | mDNS / DNS-SD; SiLA Servers self-announce on the LAN |
| Repos explicitly *do not* publish IP / Tailscale addr in `/status` | SiLA `SiLAService` core feature exposes `ServerName`, `ServerUUID`, `ImplementedFeatures` |

We moved discovery *out* of the device because Tailnet hostnames are already authoritative. SiLA assumes flat LAN with mDNS; in a Tailscale-based topology that wouldn't work anyway (Tailscale routes single-cast UDP, not multicast).

#### 6. Security / auth

| STATUS_SPEC | SiLA 2 |
|---|---|
| No auth at the repo level; Tailscale ACLs gate access; CORS for the dashboard origin | Mandatory **mTLS** with self-signed or CA-issued certs; optional `AuthenticationService` / `AuthorizationProvider` core features |

SiLA's mTLS is the most operationally heavy part of the standard — every server needs a cert + trust anchor distributed to every client. STATUS_SPEC explicitly punts that to the network layer (Tailnet), which is much lighter for a single-org lab.

#### 7. Errors

STATUS_SPEC: structured `ErrorInfo {code, message, severity, timestamp}` inside `/status`. Control-endpoint errors are conventional HTTP status codes (409/422/423/503).

SiLA: a typed three-tier hierarchy declared in the feature definition — `DefinedExecutionError` (declared per command/property in the FDL), `UndefinedExecutionError` (unanticipated), and `FrameworkError` (e.g. `InvalidCommandExecutionUUID`, `CommandExecutionNotAccepted`). Clients get *generated* exception classes per declared error.

#### 8. Observable commands (long-running work)

STATUS_SPEC models long-running work as: client polls `/status` → sees `equipment_status: busy` → poll until `ready`. There's no canonical request-id, no progress %, no intermediate values.

SiLA observable commands return a `CommandExecutionUUID` immediately, then expose:

- `ExecutionInfo` stream (status: `waiting`/`running`/`finished_successfully`/`finished_with_error`, progress, estimated remaining time)
- `IntermediateResponses` stream (partial data while running)
- A terminal `Result` call

This is genuinely missing from STATUS_SPEC today. A workflow that needs progress on a long imaging capture has to inspect `metrics` or `details` ad-hoc.

#### 9. Versioning

| STATUS_SPEC | SiLA 2 |
|---|---|
| Single `protocol_version` field at the envelope level (`"1.0"` / `"1.1"`) | Per-Feature semver inside the FQI (e.g. `…/PlateReader/v1`, `v2`); a server can expose both versions concurrently |

We bump the whole envelope; SiLA bumps features independently. Our approach is simpler when the entire ecosystem moves together (which is true today — we control all the repos).

#### 10. Metrics & units

STATUS_SPEC's `metrics: {flow_rate: {value, unit, timestamp}}` is a *runtime* convention — no schema enforces that `flow_rate` exists or that its unit is `mg/s`. SiLA encodes units and types in the FDL `Constraint`s on each property, so a client can statically check it.

### What SiLA does that STATUS_SPEC does not

- Bi-di streaming for observable commands & properties (no polling needed)
- Per-call typed metadata (auth tokens, lock tokens, tenant tags)
- Per-feature semver, multi-version coexistence on one server
- Generated client stubs in 6+ languages from one FDL file
- Typed declared errors per command
- mDNS auto-discovery
- mTLS as part of the standard
- Cancellation of in-flight commands (`Cancel(CommandExecutionUUID)`)

### What STATUS_SPEC does that SiLA does not

- Browser-native (no grpc-web shim, no mTLS plumbing in the frontend)
- "Aggregator polls one endpoint and renders a tile" use case is first-class — SiLA's `IsLocked`, `ServerName`, `IsConnected` are scattered across multiple features
- A normative **`required_actions`** field telling the dashboard what to render as a button — SiLA leaves UI hints entirely to the client
- `dry_run` as a first-class equipment state — useful for keeping dashboards alive when hardware is unplugged
- `details.claimed_by` block surfaces lock holder in the same payload as state (in SiLA you'd need to call `LockController.IsLocked` and the locked feature separately)
- Tailscale-aware: pushes discovery to the registry instead of mDNS, which doesn't traverse the Tailnet

### Interop sketch — STATUS_SPEC ⇄ SiLA bridge

If we ever want to interop with vendor instruments shipping native SiLA servers (recent Tecan, Hamilton, and Beckman boxes typically do), a thin Python bridge service would:

1. Speak gRPC/mTLS to the SiLA server.
2. Map `SiLAService.ImplementedFeatures` → `equipment_kind`.
3. Fold observable property subscriptions into a single `/status` envelope (and cache them between dashboard polls so we're not opening a new gRPC stream for each request).
4. Map our `/control/<verb>` POSTs to SiLA Commands.
5. Map our claim protocol onto SiLA's `LockController` metadata.

The semantics line up well enough that this would be a few hundred lines of Python — none of the differences are fundamental incompatibilities. The biggest gap to plan for is **observable commands** (long-running work with progress), because our current polled model loses information that SiLA exposes natively. A future v1.2 could close that gap by adopting an `execution_id` + `events` channel on `/control/*`.

---

## See also

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — system layering and where the contract lives in the monorepo.
- [`SKILLS_CATALOG.md`](SKILLS_CATALOG.md) — `Skill.name` is what `allowed_actions` lists; how the catalog evolves from hard-coded → device-declared.
- [`INTERLOCKS.md`](INTERLOCKS.md) — four-layer safety model; claims are layer-3-adjacent, cross-device safety still goes through interlocks.
- [`EQUIPMENT_INTEGRATION.md`](EQUIPMENT_INTEGRATION.md) — operational runbook for onboarding and maintenance.
- [`DEVICE_PC_SETUP.md`](DEVICE_PC_SETUP.md) — canonical install recipe for a Windows device PC (uv + NSSM).
- [`OBSERVABILITY.md`](OBSERVABILITY.md) — where state-history rows go (devices do not own them).
- [`ROADMAP.md`](ROADMAP.md) — per-device migration status to v1.0 / v1.1.
