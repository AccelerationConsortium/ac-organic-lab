# Lab Equipment Status Spec — v1.1

**Version:** `1.1`
**Status:** draft. Reference implementation lands in `agilent_plateloc` after the SDK side ships in `lab-skills` v0.3.
**Supersedes:** `docs/STATUS_SPEC.md` (v1.0). v1.0 remains valid for any device that has not migrated; the SDK is fully back-compatible with v1.0 devices (see "Back-compatibility" below).

This document extends STATUS_SPEC v1.0 with three things:

1. **Claim/heartbeat/release** — a cooperative locking protocol so two clients (e.g. an autonomous workflow and a human at a Jupyter REPL) cannot clobber each other on a shared device.
2. **`allowed_actions`** — a normative top-level field on `/status` listing the skill names (matching `Skill.name`) the device will currently honor on `/control/*`. The SDK accepts this field today and prefers it over `requires_states` when present.
3. **`details.claimed_by`** — a self-describing block on `/status` so any reader can see which session currently holds the claim without making a separate request.

Everything from STATUS_SPEC v1.0 still applies. v1.1 only adds; nothing is removed or renamed.

## Required HTTP Surface (delta from v1.0)

| Method | Path                  | Always 200? | Description |
|--------|-----------------------|-------------|-------------|
| POST   | `/control/claim`      | No          | Acquire a claim. Returns a token + heartbeat interval. |
| POST   | `/control/heartbeat`  | No          | Refresh the claim's TTL. Header `X-Claim-Token` required. |
| POST   | `/control/release`    | No          | Release the claim. Header `X-Claim-Token` required. |

The three pre-existing read endpoints (`GET /`, `GET /health`, `GET /status`) are unchanged in shape but `GET /status` adds new fields described below.

## The Envelope (delta)

```python
PROTOCOL_VERSION = "1.1"


class ClaimedBy(BaseModel):
    """Identity of the holder of the active claim. Surfaced on /status so every
    reader sees who currently controls the device without a side trip."""

    session_id: str
    owner: str
    expires_at: datetime


class EquipmentStatus(BaseModel):
    # ... all v1.0 fields ...

    # NEW in v1.1
    allowed_actions: list[str] = Field(default_factory=list)
    # `details.claimed_by` is a ClaimedBy | None; nested under details to avoid
    # touching the top-level shape.
```

`allowed_actions`:
- A flat list of skill names (matching `Skill.name` from the catalog, e.g. `"seal.start"`, `"stage.in"`).
- The device is the authority. The list reflects "what would the device honor *right now* if you POSTed it".
- Empty list (or field absent on a v1.0 device) means the SDK falls back to `requires_states` from the catalog. This is the same behavior already shipped in lab-skills v0.2.
- Devices that have not yet migrated to v1.1 simply omit the field; the SDK already accepts that gracefully.

`details.claimed_by`:
- `null` (or missing) when no claim is active.
- A `ClaimedBy` object when a claim is active. `expires_at` is the heartbeat-extended absolute UTC timestamp.

## Claim Protocol

### Why claims at all

A v1.0 device has no notion of who is talking to it. Two clients hitting `/control/seal/start` simultaneously would race. v1.1 adds a cheap optimistic lock: clients ask for a claim, hold it via heartbeats, release it cleanly. Devices reject control commands from anyone but the holder.

Claims are **cooperative**, not authenticated. Any client could ignore the protocol. The SDK enforces it on the client side; devices that want hard enforcement check `X-Claim-Token` on `/control/*` and reject mismatches with HTTP 423. Devices that prefer to leave it advisory can publish `claimed_by` and let workflow code do the right thing.

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

Success: HTTP 204. Idempotent — releasing an unknown / already-released token also returns 204 (releasing should never fail in a way that prevents the client from moving on).

### Hard-enforcement on `/control/*`

A v1.1 device that wants to enforce claims SHOULD check `X-Claim-Token` on every `/control/<endpoint>` request:

- Header missing or stale → HTTP 423 Locked, body explaining the active claim.
- Header valid → proceed.

A v1.1 device that wants to keep claims advisory MAY accept `/control/*` without `X-Claim-Token`. In this mode `details.claimed_by` is the only signal; client-side code is expected to honor it.

## SDK side (`lab-skills`)

Implemented in v0.3:

- `lab_skills.ClaimManager(client, *, owner, session_id, ttl_s=30.0)` async context manager.
  - `__aenter__`: POSTs `/control/claim`, starts the heartbeat background task, returns `self`.
  - On HTTP 409/423: raises `ClaimRejected(retry_after_s=…)`. Honors `retry_after_s` from the JSON body, falling back to the `Retry-After` header.
  - On HTTP 404 / 405 from `/control/claim`: device is v1.0 (does not implement claims). The manager enters **degraded mode** silently — no heartbeat, no release call. This is the graceful-degradation contract: workflows can wrap any device in `ClaimManager` and v1.0 devices behave as no-ops.
  - `__aexit__`: cancels the heartbeat task, POSTs `/control/release` (best-effort).
- Heartbeat: a single asyncio task. After **three consecutive** heartbeat failures it self-cancels, stores an `EquipmentUnreachable` on the manager, and re-raises that on the next call to `claim.assert_alive()` or on `__aexit__`. Three was chosen to mirror read-side resilience.
- `lab_skills.ClaimRejected(LabError)` — new exception. `retry_after_s: float | None` and `claimed_by: ClaimedBy | None` for diagnostics.

## Plan validation, claims, and interlocks

`validate_plan(plan, session)` (also in v0.3) is **offline**: no HTTP. It does, however, annotate per-step `warnings` based on registry-declared device capability:

- A step that targets an `EquipmentEntry` with `protocol = "1.0"` produces a `warnings: ["no_claim_semantics"]` entry. The plan can still be executed; the warning surfaces that no mutual-exclusion guarantee will be in effect.
- A step that targets `protocol = "1.1"` adds no warning. `execute_plan` (v0.4) will wrap it in `ClaimManager`.

The `protocol` field is added to `EquipmentEntry` in v0.3 with a default of `"1.0"`, so existing `equipment.yaml` files stay valid. To opt a migrated device into v1.1 semantics, add `protocol: "1.1"` to its yaml entry.

## Conformance Checklist (v1.1 delta from v1.0)

A repo claiming v1.1 conformance MUST:

- [ ] Bump `protocol_version` reported on `/` and `/status` to `"1.1"`.
- [ ] Implement `POST /control/claim`, `POST /control/heartbeat`, `POST /control/release` with the shapes above.
- [ ] Populate `allowed_actions` on `/status` for the device's current state. Empty list iff no `/control/*` is currently safe to call (e.g. `requires_init`).
- [ ] Populate `details.claimed_by` while a claim is held; clear it on release / expiry.
- [ ] Either enforce `X-Claim-Token` on `/control/*` (recommended; HTTP 423 on miss) or document that claims are advisory in the repo README.
- [ ] Snapshot fixtures for at least: `ready` with no claim, `ready` with a claim held, `requires_init`.
- [ ] `README.md` says "This repo conforms to lab status spec v1.1".

A repo that does **not** want to opt into v1.1 stays on v1.0 unchanged. The SDK treats it as "no claim semantics, fall back to v1.0 catalog `requires_states`".

## Back-compatibility (normative)

The SDK's contract for v1.0 devices, post-v1.1:

| Surface             | v1.0 device (pre-v1.1) | v1.1 device              |
|---------------------|------------------------|--------------------------|
| `/status`           | no `allowed_actions`   | populates `allowed_actions` |
| `Skill.available`   | from `requires_states` | from `allowed_actions` first, falls back to `requires_states` |
| `ClaimManager`      | degraded (no-op)       | claims + heartbeat + release |
| `validate_plan` warning | `["no_claim_semantics"]` | none |
| `execute_plan` (v0.4) | runs without claim wrap | wraps each step in `ClaimManager` |

A workflow that talks to a mix of v1.0 and v1.1 devices in the same `LabSession` is supported and is the expected migration path.

## See also

- `docs/STATUS_SPEC.md` — v1.0 (still authoritative for unmigrated repos)
- `docs/SKILLS_CATALOG.md` — `Skill.name` is what `allowed_actions` lists
- `docs/INTERLOCKS.md` — claims are layer-3-adjacent; cross-device safety still goes through interlocks
