# Lab Equipment Status Spec

**Current version:** `1.2` (additive over `1.1`, which was additive over `1.0`; v1.0 and v1.1 devices remain valid).
**Status:** authoritative contract for every lab equipment REST API displayed on the AC Organic Self-Driving Lab dashboard. v1.0 is the *baseline*; v1.1 adds cooperative claims, `allowed_actions`, and `details.claimed_by`; v1.2 adds `activity` / `activity_since`, separating "is it healthy" from "is it working". All three coexist on the wire and in `equipment.yaml`; the SDK degrades gracefully when talking to older devices.

Each equipment repo copies the Pydantic models below into its own `models.py`. Once the spec has been stable for ~1 month and 3+ repos have migrated cleanly to v1.1, these types will be promoted into a shared `sdl-lab-contract` Python package and per-device repos will `from sdl_lab_contract import ...` instead of vendoring a copy.

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


PROTOCOL_VERSION = "1.2"   # v1.0 / v1.1 devices stay on their own version


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

# NEW in v1.2. Orthogonal to EquipmentState: health and activity are
# independent questions, and `equipment_status` answers only the first
# (§2.2 requires a fault to claim the top-level state). See §2.3.
Activity = Literal[
    "idle",           # not performing its primary operation
    "running",        # primary operation in progress
    "unknown",        # cannot be determined — answer of last resort, same discipline as §2.1
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

    # NEW in v1.2 — see §2.3. Defaults are deliberate: an older device that
    # omits these reads as "undetermined", never as a false "idle".
    activity: Activity = "unknown"
    activity_since: datetime | None = None  # start of the current activity span

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

2. **The dashboard treats a gateway-fronted kind reporting `unknown` as "unreachable"** for presentation and uptime. Because such a device is genuinely offline yet produces no transport-level `fetch_error`, the offline interpretation is applied at the presentation layer (keyed on `equipment_kind` ∈ {`camera`, `smart_plug`, `power_strip`}, **or** on the registry entry carrying `gateway_fronted: true` — for a single-device gateway of any other kind, such as the OT-2 gateways, whose robot sits behind a wired or USB link and which report `unknown` when it cannot be reached). The on-the-wire contract value stays `unknown` — a device must never invent an out-of-enum state — but the operator sees "unreachable", consistent with a directly-polled device that timed out.

**Net rule:** an `unknown` the reader can attribute to a known reachability failure (a transport `fetch_error`, or a gateway-fronted kind reporting `unknown`) renders as **"unreachable"** and counts as *down*; a bare `unknown` with no such attribution (cold start, not-yet-observed) stays **"unknown"** and counts as *up*. Devices should drive toward a precise state whenever they can — `unknown` is the answer of last resort, not a routine one.

### 2.2 Top-level state must reflect run suitability (normative)

`equipment_status` is the safety-relevant summary of the equipment, not merely
the liveness of its service or supervisor process. A device **MUST NOT** report
`ready` while it knows of an active fault that makes the equipment unsuitable
for its normal primary operation:

- Report **`error`** when a reachable device or required subsystem reports an
  active fault that prevents a normal run.
- Report **`degraded`** when a subsystem is unhealthy but the equipment retains
  a safe, useful subset of its normal capability.
- Report **`ready`** only when the equipment is initialized, idle, and has no
  known run-blocking fault. A healthy service process or the absence of an
  active run is not sufficient by itself.

`components`, `metrics`, and `last_error` provide diagnosis; they must not be
used to hide a run-blocking fault beneath a top-level `ready`. A historical
`last_error` whose underlying fault is no longer active does not by itself make
the equipment unsuitable; follow the clearing policy in §6.4.

`allowed_actions` must agree with the top-level state. While a run-blocking
fault is active, omit actions that start or enqueue a normal run. Recovery,
abort, standby, and diagnostic actions may remain available when safe.

### 2.3 `activity` is orthogonal to `equipment_status` (normative, v1.2+)

`equipment_status` answers *"is this equipment healthy and suitable for a
run?"*. `activity` answers *"is it performing its primary operation right
now?"*. These are independent questions, and §2.2 deliberately gives the
top-level state to the **first** one — so before v1.2 there was nowhere for the
second answer to live. `activity` is that place.

Concretely: a shaker with a faulty heater RTD but a healthy motor, mid-cycle,
reports `equipment_status: "degraded"` **and** `activity: "running"`. Neither
fact suppresses the other. `equipment_status` alone cannot express this, which
is why a reader that only stores the top-level state loses all record of
utilization for a chronically-degraded device.

Rules:

- Devices **MUST** derive `activity` from observed hardware state, **never**
  from `equipment_status`. A device that computes one from the other adds no
  information.
- Each device repo **MUST** document what "primary operation" means for its
  kind, in its README (shaker: orbital motor turning; HPLC: injection or
  gradient in progress; press: platen cycle; plate sealer: a seal cycle).
  Per-subsystem detail stays in `components`.
- `activity` **MUST NOT** be used to soften, delay, or hide a run-blocking
  fault. The §2.2 prohibition applies unchanged.
- `activity_since` is the start of the **current** span (the instant `activity`
  last changed value), not the start of the enclosing request or process. It
  lets a reader recover the true duration of an in-progress operation instead
  of inferring it from its own poll timestamps.
- Report `unknown` only when the answer genuinely cannot be determined — same
  discipline §2.1 imposes on `equipment_status: unknown`. It is the answer of
  last resort, not a routine one.

**Consistency invariants.** `activity` and `equipment_status` are independent
but not unconstrained. A reader **MAY** treat a violation as a device bug:

| `equipment_status` | required `activity` |
|---|---|
| `busy` | `running` |
| `ready` | `idle` |
| `requires_init` | `idle` |
| `e_stop` | `idle` |
| `degraded` | `running` **or** `idle` — whichever is true |
| `error`, `dry_run`, `unknown` | any |

`busy` is retained unchanged for compatibility, and is definitionally
equivalent to healthy + `running`. It follows that `busy` and `degraded` can
never co-occur in `equipment_status` — that combination is expressed as
`degraded` + `activity: "running"`.

> A future v2 could make the state enum health-only and let `activity` carry
> every run signal, retiring `busy`. That is cleaner but breaks every device,
> the catalog's `requires_states`, and stored history. Not now — the agreed
> target shape and the criteria that gate starting it are recorded in
> [Appendix B](#appendix-b--v2-direction-non-normative).

`allowed_actions` **MUST** agree with `activity` as well as with the top-level
state: while `activity == "running"`, omit actions that would start or enqueue
a *second* concurrent run. Abort and stop actions remain available.

#### 2.3.1 Readers: `activity` alone is not usage accounting (normative)

A poll-sampled `activity` series **MUST NOT** be presented as usage or
utilization accounting. Operations shorter than the reader's poll interval can
begin and end entirely between two polls, so they are not undercounted — they
are missed outright. (The dashboard aggregator polls at 60 s; a default shaker
cycle is 30 s.)

For accounting that survives sampling, devices **SHOULD** provide at least one
of:

- a monotonically increasing `metrics["cycles_total"]` (reserved key,
  `unit: "count"`), whose poll-to-poll delta reveals operations the reader
  slept through — it never decreases except on device restart; or
- device-originated start/stop records posted to the aggregator's event-ingest
  path, which carry exact timestamps independent of any reader's poll cadence.

Readers that cannot offer either **MUST** label such a series as sampled, and
**MUST** show when tracking began rather than rendering pre-tracking time as
zero usage.

#### 2.3.2 Reader-side derivation is non-normative

Until every device reports `activity`, a reader **MAY** infer it for known
kinds from `components` (e.g. a shaker's `components["motor"].state` in
`{running, shaking}`). Such inference is **reader-local and non-normative**:
devices **MUST NOT** rely on it, it is not part of the contract, and it is
expected to be deleted once the fleet has migrated. A device that omits
`activity` is reported as `unknown`, which is the honest answer.

### v1.1 field semantics

`allowed_actions`:
- A flat list of skill names (matching `Skill.name` from the SDK catalog, e.g. `"seal.start"`, `"stage.in"`).
- The device is the authority. The list reflects "what would the device honor *right now* if you POSTed it".
- Empty list (or field absent on a v1.0 device) means the SDK falls back to `requires_states` from the catalog. This is the back-compatibility contract shipped in `lab-skills` v0.2+.
- Devices that have not yet migrated to v1.1 simply omit the field.

`details.claimed_by`:
- `null` (or missing) when no claim is active.
- A `ClaimedBy` object when a claim is active. `expires_at` is the heartbeat-extended absolute UTC timestamp.

### v1.2 field semantics

`activity`:
- Independent of `equipment_status`; see §2.3 for the full rules and the consistency invariants.
- Absent on a v1.0/v1.1 device → readers treat it as `"unknown"`, never as `"idle"`.
- Devices that have not yet migrated to v1.2 simply omit the field, exactly as with the v1.1 additions.

`activity_since`:
- `null` when unknown, or when the device cannot timestamp the transition.
- Otherwise the absolute UTC instant `activity` last changed value.

`metrics["cycles_total"]` (reserved, optional):
- Monotonic count of completed primary operations since device start, `unit: "count"`.
- Resets only on device restart; a reader detecting a decrease treats it as a restart, not as negative usage.
- A device that has a **hardware odometer** for its primary operation SHOULD publish that instead of an in-process counter (plateloc mirrors the instrument's lifetime seal-cycle count). It satisfies the monotonic semantics by construction and additionally survives a service restart, so no usage is lost across a redeploy. Publishing it under the reserved key alongside any pre-existing device-specific metric key is fine — plateloc reports the same number as both `cycles_total` and its legacy `cycle_count`.

---

## 3. Probe Endpoints

`GET /` returns:

```python
class ProbeResponse(BaseModel):
    equipment_id: str
    equipment_name: str
    protocol_version: str   # "1.0", "1.1" or "1.2"
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

9. **`/status` is current state only.** Historical events go to the dashboard's history DB (see [`LAB_MONITORING.md`](LAB_MONITORING.md)) — never inflate the status response with logs.

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
// Temperature interlock (plateloc device v1.2+ — device version, not spec version)
{
  "detail":         "Temperature outside seal band",
  "actual_c":       166.0,
  "setpoint_c":     170.0,
  "tolerance_c":    2.0,
  "retry_after_s":  2
}

// Stage interlock (plateloc device v1.3+)
{
  "detail":      "Stage not loaded",
  "stage_state": "out",
  "required":    "in"
}

// Health interlock (plateloc device v1.4+) — §2.2's "don't start a normal
// run while a fault is active", as a precondition. Recovery is both
// time-bounded (the device's recent-error window) and immediate via the
// §6.4 auto-clear, so it carries retry_after_s.
{
  "detail":             "Recent operational failure not cleared",
  "last_error_code":    "low_air_pressure",
  "last_error_message": "StartCycle returned error code -2147221503 (driver: Low Air Pressure Error)",
  "retry_after_s":      47
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

The `protocol` field on `EquipmentEntry` in the registry defaults to `"1.0"`, so existing `equipment.yaml` files stay valid. To opt a migrated device into v1.1 semantics, add `protocol: "1.1"` to its yaml entry (`protocol: "1.2"` for v1.2).

---

## 8. Back-compatibility (normative)

The SDK's contract across versions:

| Surface             | v1.0 device              | v1.1 device              | v1.2 device              |
|---------------------|--------------------------|--------------------------|--------------------------|
| `/status`           | no `allowed_actions`     | populates `allowed_actions` | also populates `activity` / `activity_since` |
| `Skill.available`   | from `requires_states`   | from `allowed_actions` first, falls back to `requires_states` | unchanged from v1.1 |
| `ClaimManager`      | degraded (no-op)         | claims + heartbeat + release | unchanged from v1.1 |
| `validate_plan` warning | `["no_claim_semantics"]` | none | none |
| `execute_plan` (v0.4) | runs without claim wrap | wraps each step in `ClaimManager` | unchanged from v1.1 |
| `activity`          | absent → reader sees `"unknown"` | absent → reader sees `"unknown"` | observed from hardware |

A workflow that talks to a mix of v1.0, v1.1 and v1.2 devices in the same `LabSession` is supported and is the expected migration path.

v1.2 adds no endpoints and no required fields — a v1.1 device is v1.2-compatible on the wire the moment the reader defaults `activity` to `"unknown"`. Readers **MUST NOT** condition any *safety* decision on `activity` alone, precisely because an unmigrated device always reports `unknown`; the §2.2 health gate remains the authority.

---

## 9. Conformance Checklists

### Read-only devices and the ladder (normative)

The three checklists below are cumulative — v1.1 is "on top of v1.0", v1.2 "on
top of v1.1". Read literally that ladder is unclimbable for a device with no
control surface, because **v1.1's additions are all about writing** (claims,
`allowed_actions`, §6 precondition refusals) while **v1.2's are all read-side**
(`activity`, `activity_since`, `cycles_total`). Such a device can implement
every v1.2 item and no v1.1 item at all.

This is not a corner case. As of 2026-07-30, **15 of the 30 entries in
`equipment.yaml` expose no control affordances**: all four `env_*` sensors,
every web service, both gateways, and the monitoring-only Bambu printers. And
`activity` is the field that matters *most* for a device nobody can command —
"is it working" is the only question such a device answers.

Therefore: **a device that exposes no `/control/*` endpoints satisfies the v1.1
control requirements vacuously** and MAY report `"1.1"` or `"1.2"` on the
strength of the read-side items alone. Concretely, for such a device:

- The §5 claim endpoints, `details.claimed_by`, and `X-Claim-Token` items are
  **N/A** — there is no access to serialize.
- `allowed_actions: []` is conformant. It is not an unfinished list; there is
  nothing to allow.
- §6 does not apply (no action can be refused).
- Every **read-side** item still applies in full — in particular §2.2's
  health honesty, and for v1.2 the §2.3 requirement that `activity` come from
  observed hardware state with the consistency invariants holding.
- The README **MUST** state which items are N/A because the device is
  monitoring-only, so a reader can tell "deliberately read-only" from
  "migration half-finished".

What this does **not** license: a device that *does* expose `/control/*` may
not skip claims to reach v1.2. Partial control without claims stays v1.0 — the
exemption is for having nothing to claim, not for finding claims inconvenient.

Why this is safe for readers: `protocol_version` was never the
claim-capability signal. §5 already permits a v1.1 device to leave claims
advisory, and the SDK's `ClaimManager` treats 404/405 from `/control/claim` as
"device does not implement claims" and degrades silently (§7). A client decides
claimability from that response and from `allowed_actions`, never from the
version number.

This clause is a **clarification of conformance, not a wire change** — no field
gains or loses meaning, so it is not a spec revision and does not bump
`sdl-lab-contract`. The deeper conflation it papers over (one version number
spanning two independent axes) is recorded as an open question in
[Appendix B.6](#b6-open-question--conformance-profiles).

### v1.0 (read-only baseline)

A repo is considered v1.0 conformant when:

- [ ] `models.py` carries the STATUS_SPEC v1.0 Pydantic types verbatim (or imports them from a shared package once one is published).
- [ ] `GET /` returns `ProbeResponse(equipment_id, equipment_name, protocol_version="1.0")`.
- [ ] `GET /health` returns `HealthResponse(status="healthy")`.
- [ ] `GET /status` returns `EquipmentStatus` with snake_case field names. **Side-effect-free.** Always 200 unless the process is broken.
- [ ] Top-level `equipment_status` follows §2.2: an active fault in a required subsystem that makes the equipment unsuitable for a normal run is never reported as `ready`.
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

A repo that does **not** want to opt into v1.1 stays on v1.0 unchanged. The SDK treats it as "no claim semantics, fall back to v1.0 catalog `requires_states`". A repo that *cannot* opt in because it has no control surface at all is covered by the read-only clause at the top of this section.

### v1.2 (additive over v1.1)

A repo is considered v1.2 conformant when, on top of v1.1:

- [ ] `protocol_version` reported on `/` and `/status` is `"1.2"`.
- [ ] `EquipmentStatus.activity` is populated from **observed hardware state**, not derived from `equipment_status` (§2.3).
- [ ] `activity_since` is set to the instant `activity` last changed, or `null` if the device cannot timestamp it.
- [ ] The §2.3 consistency invariants hold — in particular `busy` ⇒ `running`, and `ready` / `requires_init` / `e_stop` ⇒ `idle`.
- [ ] `allowed_actions` omits start-a-new-run actions while `activity == "running"`; abort/stop remain listed when safe.
- [ ] `README.md` defines what "primary operation" means for this device, and says "This repo conforms to lab status spec v1.2".
- [ ] Recommended for any device whose primary operation can be shorter than 60 s: a monotonic `metrics["cycles_total"]`, or device-originated start/stop event records (§2.3.1).
- [ ] Snapshot fixtures cover at least: healthy + `running` (i.e. `busy`), healthy + `idle` (`ready`), and — if the device has an independently-faultable subsystem — `degraded` + `running`.
- [ ] `equipment.yaml` entry has `protocol: "1.2"`.

A repo that does not opt into v1.2 stays on its current version unchanged; readers report its activity as `unknown`.

For *deploying* the repo to the actual lab PC (uv environment, NSSM service registration, log paths, multi-device hosting), follow [`DEVICE_PC_SETUP.md`](DEVICE_PC_SETUP.md). Each device repo's README should link to that document rather than re-deriving the recipe.

For onboarding the device into the dashboard registry and handling maintenance windows, see [`EQUIP_GUIDE.md`](EQUIP_GUIDE.md).

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

### Orbital shaker (`shaker`) — v1.2 `degraded` **and** `running`

The motivating case for §2.3: the heater's RTD has failed calibration, so the
device is not suitable for a temperature-controlled run and §2.2 requires
`degraded`. The motor is healthy and mid-cycle, so `activity` is `running`.
Neither fact hides the other, and a reader that stores both can still report
utilization for a device that has been `degraded` for weeks.

```json
{
  "protocol_version": "1.2",
  "equipment_id": "shaker_sc25xr",
  "equipment_name": "Torrey Pines SC25XR",
  "equipment_kind": "shaker",
  "host": "shaker-pi",
  "equipment_status": "degraded",
  "activity": "running",
  "activity_since": "2026-04-29T22:49:44Z",
  "message": "Heater RTD calibration fault (cal3) — shaking without temperature control",
  "required_actions": ["Recalibrate heater RTD"],
  "device_time": "2026-04-29T22:50:01Z",
  "components": {
    "motor":  {"connected": true, "state": "shaking"},
    "heater": {"connected": true, "state": "unknown", "message": "cal3: RTD out of calibration"}
  },
  "metrics": {
    "speed_level":  {"value": 5, "unit": "level"},
    "cycles_total": {"value": 418, "unit": "count"}
  },
  "last_error": {
    "code": "cal3",
    "message": "Heater RTD out of calibration",
    "severity": "warning",
    "timestamp": "2026-04-14T08:12:33Z"
  },
  "allowed_actions": ["shake.stop"],
  "details": {"claimed_by": null}
}
```

Note `allowed_actions`: `shake.start` is omitted because `activity` is
`running` (no second concurrent cycle), and `heater.set_temperature` is omitted
because of the fault — the two gates of §2.3 acting together. `shake.stop`
stays available so an abort is always reachable.

The same device, fault unchanged, between cycles:

```json
{
  "equipment_status": "degraded",
  "activity": "idle",
  "activity_since": "2026-04-29T22:50:14Z",
  "allowed_actions": ["shake.start"]
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

v1.2 narrows the gap slightly but does not close it: `activity` survives a concurrent health fault (a `degraded` device is still visibly `running`, which a `busy`→`ready` poll loop could not express), and `activity_since` gives elapsed time without the client timing its own polls. There is still no request-id, no progress fraction, and no intermediate values — and §2.3.1 is explicit that polling cannot see an operation shorter than the poll interval, which is why `metrics["cycles_total"]` and device-originated event records exist.

SiLA observable commands return a `CommandExecutionUUID` immediately, then expose:

- `ExecutionInfo` stream (status: `waiting`/`running`/`finished_successfully`/`finished_with_error`, progress, estimated remaining time)
- `IntermediateResponses` stream (partial data while running)
- A terminal `Result` call

This is genuinely missing from STATUS_SPEC today. A workflow that needs progress on a long imaging capture has to inspect `metrics` or `details` ad-hoc.

#### 9. Versioning

| STATUS_SPEC | SiLA 2 |
|---|---|
| Single `protocol_version` field at the envelope level (`"1.0"` / `"1.1"` / `"1.2"`) | Per-Feature semver inside the FQI (e.g. `…/PlateReader/v1`, `v2`); a server can expose both versions concurrently |

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

## Appendix B — v2 direction (non-normative)

**Status:** design target recorded 2026-07-25, immediately after the v1.2
rollout. Nothing in this appendix changes v1.x behavior; it exists so the
agreed shape — and, just as importantly, the alternatives already considered
and rejected — survive outside the conversation that produced them. The
[migration gate](#b5-migration-gate) below governs when (not whether) this
work starts.

The problem v2 finishes solving: `equipment_status` is one enum answering
several independent questions (health, activity, mode), with §2.2 precedence
deciding which answer wins the single word. v1.2 broke *activity* out
additively; v2 factors the rest.

### B.1 The factored model

| Question | Field | Values |
|---|---|---|
| Can I reach it? | — (reader-side, §2.1) | reachable / **unreachable** — never on the wire |
| Is it healthy / what blocks a run? | `health` | `healthy` \| `degraded` \| `error` \| `e_stopped` \| `requires_init` \| `unknown` |
| Is it performing its primary operation? | `activity` | `idle` \| `running` \| `unknown` — unchanged from v1.2 §2.3 |
| What is it operated *for*? | `mode` | `production` \| `develop` \| `maintenance` |
| Is everything I'm reading synthetic? | `simulated` | `bool` |

Semantics that make the small field-count sound:

- **`health` absorbs the readiness/safety conditions** (`requires_init`,
  `e_stopped`) rather than carrying a separate K8s-style conditions list —
  precedence was chosen over composition for simplicity. A device reports
  exactly **one** value: the most run-blocking applicable, safety first —
  `e_stopped > error > degraded > requires_init > healthy`. `unknown` stays
  the §2.1 honest fallback, outside the ranking. `required_actions` remains
  the actionability carrier; co-occurrence detail (e-stopped *and* faulted)
  lives in `components` / `last_error`, exactly as today.
- **`activity` is pure observation** — "primary operation in progress",
  no judgment about ability. "Ready to start" is a *derived conjunction*
  (`healthy` ∧ `idle` ∧ nothing blocking), whose precise, per-action,
  device-authoritative form already exists: `allowed_actions`.
- **`mode: develop`** = operated for non-production purposes (workflow
  bring-up, engineering tests on real hardware, agent training — SEMI E10's
  *Engineering Time*). Results produced under `mode != production` are never
  production records. `maintenance` aligns with the registry's
  `maintenance:` block.
- **`simulated: true`** = no hardware attached, nothing actuates, all data
  synthetic. Implies `mode: develop`. A simulated device behaves
  **identically on every axis** — it is never required to stay idle, or
  simulation could not exercise the wait-for-completion paths it exists to
  test (LG7). Instead, readers **MUST** exclude `simulated` devices from
  production utilization, uptime rollups, and record stores. Consumers must
  branch on the right field: "can an agent safely hammer this device?" →
  `simulated`, never `mode` (a develop-mode xArm still moves); "may this
  result enter the record store as real data?" → `mode`.

The design rule underneath all of this, worth applying to any future
extension: **devices report reality — even simulated reality — and never
self-censor to make accounting easier; readers apply judgment** (unreachable
attribution, utilization exclusion, production filtering are all
reader-side). *Record what is; filter what counts.*

### B.2 v1.x → v2 mapping

| v1.x `equipment_status` | v2 |
|---|---|
| `ready` | `healthy` + `idle` |
| `busy` | `healthy` + `running` (retired — already the §2.3 invariant) |
| `requires_init` | `requires_init` + `idle` |
| `degraded` | `degraded` + observed activity |
| `error` | `error` + any |
| `e_stop` | `e_stopped` + `idle` |
| `dry_run` | `simulated: true` (⇒ `mode: develop`) + the simulation's own health/activity |
| `unknown` | `unknown` + `unknown` |

The mapping is deterministic, so stored v1.x history remains re-derivable
into v2 terms, and `ready` → `healthy` is a rename (with `idle` moved to its
own axis, "ready + running" would read as a contradiction).

Because it is deterministic, the dashboard already serves the projection
reader-side (since 2026-07-25): `EquipmentSnapshot.health` / `mode` /
`simulated`, computed by `api/app/events.py::derive_v2_fields` from
`equipment_status` + the registry (`adapter: mock` ⇒ simulated;
`maintenance:` block ⇒ mode maintenance). Non-normative, same status as the
§2.3.2 activity sniff — it adds no information, exists so readers can speak
the v2 vocabulary early, and is the single definition any native v2 fields
must agree with when they arrive.

### B.3 Decided against — do not relitigate

- **Compound states** (`busy_degraded`, …): combinatorial explosion; the
  original v1.2 motivation.
- **A separate conditions list** (K8s-style) for `requires_init` /
  `e_stopped`: purer (they can co-occur with `degraded`/`error`) but costs a
  field, list semantics, and iteration in every reader. Folded into `health`
  with the precedence order; revisit only if a v3 has evidence the
  co-occurrence detail in `components` is not enough.
- **Renaming activity `idle` → `ready`**: readiness is a conjunction, not an
  observation — a `degraded` shaker between cycles is *not* ready for its
  normal run, and would have no honest value. Also collides with v1.x
  `ready` during coexistence. (PackML's `Idle` does mean ready-to-start, but
  only because 17 sibling states hold the not-ready cases.)
- **`unknown` ≡ `unreachable`**: "asked and didn't learn" vs "couldn't ask";
  opposite uptime accounting (§2.1); and on the activity axis every
  reachable unmigrated device would read as offline.
- **`simulated` as a mode or activity value**: it qualifies the *whole
  envelope*, not one axis; as an activity value, simulated devices would
  have no observable activity at all.
- **Requiring simulated devices to report no activity/runs**: kills
  simulation's purpose (untestable wait-for-completion, recorder, agent
  training); the guard is the reader-side exclusion in B.1 instead.

### B.4 Standards mapping

- `health` ↔ NAMUR NE 107's device-health signals (Failure / Out of
  Specification / Maintenance Required / Function Check) and monitoring's
  OK/WARNING/CRITICAL/**UNKNOWN** lineage (unknown as honest fallback).
- `activity` + `mode` ↔ PackML (ISA-TR88.00.02): operational state machine
  × UnitMode (Production/Maintenance/Manual) as **separate axes**; SEMI E10's
  time buckets (Productive / Standby / Engineering / Downtime) for the
  utilization accounting the activity series feeds.
- The factoring itself ↔ Kubernetes' `phase` → `conditions` history: a
  single enum cannot answer independent questions ("Running but not Ready"),
  **and** the deprecated field survived for years because breaking a
  deployed API is harder than shipping a better one. Both lessons apply: v2
  fields arrive additively alongside `equipment_status`, readers migrate,
  and the old enum retires last. Never a cutover.

### B.5 Migration gate

No v2 implementation work starts until **all** of:

1. **The shared `sdl-lab-contract` package exists** (ARCHITECTURE.md LG5)
   — a breaking contract change must cost one package bump, not an edit to
   every vendored `models.py`.
2. **The majority of the fleet natively reports `activity`** and the §2.3.2
   reader-side derivation has been deleted — proof the fleet can absorb a
   contract migration end-to-end. Counted over the *whole* registry, read-only
   devices included: half of it is read-only, and the §9 read-only clause is
   what lets those devices declare the version that means "activity is
   observed", so this gate is auditable from `equipment.yaml` rather than by
   inspecting every envelope by hand.
3. **A concrete case exists that v1.2 cannot express** — cleanliness alone
   does not justify breaking 17 devices, `requires_states`, and stored
   history.

### B.6 Open question — conformance profiles

**Status: not decided.** Unlike B.1–B.3, this section records a question v2
must answer, not an agreed shape. It exists so the question is not rediscovered
from scratch the third time a monitoring-only device is onboarded.

**The conflation.** One `protocol_version` spans two independent axes:

| axis | what it covers | today's versions |
|---|---|---|
| read-envelope richness | `/status` fields — `activity`, `activity_since`, `cycles_total` | v1.0 → v1.2 |
| control contract | claims (§5), `allowed_actions`, §6 precondition refusals | v1.1 |

Half the fleet has only the first axis and no vocabulary for saying so. The §9
read-only clause deliberately patches the *symptom* inside v1.x, at the price
of leaving the number ambiguous: `"1.2"` now means "rich reads, and either
claims or nothing to claim". That is an acceptable trade for a clarification
that touches no wire field, and a poor foundation to build v2 on.

**Why v2 is the moment.** B.1–B.2 factor the *state fields* and say nothing
about conformance or versioning, so v2 as drafted inherits the conflation with
a *longer* ladder (`health`, `activity`, `mode`, `simulated` all arrive as
read-side additions on top of a control-shaped v1.1 rung). Deciding this is
also cheap exactly once: the moment there is already one breaking bump to spend.

**Shapes considered, none chosen:**

- **Status quo + the §9 clause.** Zero cost, already in force. Ambiguous number.
- **A `profile` string** on `/status` (e.g. `monitoring` | `controllable`).
  One field, readable at a glance; but a two-value enum will want a third value
  the week after it ships (what is a device with control but no claims?).
- **A `capabilities: list[str]`** on `/status` (`claims`, `control`,
  `preconditions`, `events`, …) with `protocol_version` reduced to describing
  the read envelope alone. Most honest to how devices actually differ, and it
  subsumes the `profile` idea. Costs a field, list semantics in every reader,
  and a rule for what an unrecognised capability means.
- **SiLA-style per-feature versioning** (Appendix A §3, §9): no envelope-level
  version at all, a device is a set of independently-versioned features and a
  monitoring-only device simply implements fewer. Most expressive, and the
  standard this document already benchmarks against — but it replaces one
  string with a registry of feature identifiers, which is the heaviness
  Appendix A's TL;DR deliberately rejected.

**Already half-built on the reader side**, which should inform the choice: the
registry carries a per-entry `protocol:` (a second-order copy of the device's
claim, §7), and the dashboard already distinguishes a device-reported activity
from a derived one (`EquipmentSnapshot.activity_source` ∈ {`device`, `status`,
`none`}). Whatever shape wins should make the registry field derivable from the
envelope rather than separately maintained.

**A smaller instance of the same question**, worth resolving with it: §9's v1.2
checklist requires a README definition of the device's "primary operation", but
a *fronting service* has none — the `bambu_gateway` aggregate envelope fronts
two printers and does not itself operate. Today such a service stays v1.0 by
default, which understates that its per-printer surfaces are v1.2. Either
services are out of scope for the activity axis (and the spec should say so), or
"primary operation" needs a defined answer for a device whose job is to front
other devices.

## See also

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — system layering and where the contract lives in the monorepo.
- [`SKILLS_CATALOG.md`](SKILLS_CATALOG.md) — `Skill.name` is what `allowed_actions` lists; how the catalog evolves from hard-coded → device-declared.
- [`INTERLOCKS.md`](INTERLOCKS.md) — four-layer safety model; claims are layer-3-adjacent, cross-device safety still goes through interlocks.
- [`EQUIP_GUIDE.md`](EQUIP_GUIDE.md) — operational runbook for onboarding and maintenance.
- [`DEVICE_PC_SETUP.md`](DEVICE_PC_SETUP.md) — canonical install recipe for a Windows device PC (uv + NSSM).
- [`LAB_MONITORING.md`](LAB_MONITORING.md) — where state-history rows go (devices do not own them).
- [`ROADMAP.md`](ROADMAP.md) — per-device migration status to v1.0 / v1.1 / v1.2.
