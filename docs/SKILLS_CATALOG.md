# Skill Catalog Design

**Status:** design. Implementation lands in `skills/` v0.2 (static catalog) and v0.3 (runtime availability via STATUS_SPEC v1.1 `allowed_actions`).

This document specifies how the SDK describes "what the lab can do right now" — the `Skill` data model, where the catalog lives, how availability is computed, and how it evolves from a hard-coded table to a self-describing protocol.

## Why a skill catalog at all

Workflow code, agents, and the dashboard all need a structured answer to:

- *What capabilities exist on a given role?*
- *Which of them are invokable right now?*
- *What arguments does each one take?*
- *If one is not invokable, why?*

This information already exists *implicitly* in the device's REST surface: every `POST /control/*` endpoint is a skill, every `EquipmentState` value gates which endpoints are valid, and every Pydantic request model is a schema. The catalog makes that knowledge **explicit, typed, and uniform across devices** so consumers don't reverse-engineer it case-by-case.

## Two data shapes: `SkillDef` (static) and `Skill` (runtime)

```python
# Static description of a capability - one entry per (kind, action) pair.
# Lives in the SDK's catalog registry; does not depend on any running device.
class SkillDef(BaseModel):
    name: str                                # e.g. "seal.start"
    kind: EquipmentKind                      # which equipment kind exposes it
    description: str                         # human-readable
    endpoint: str                            # e.g. "/control/seal/start"
    method: Literal["GET", "POST", "DELETE"] = "POST"  # DELETE for id-in-path removal skills (queue.cancel)
    args_schema: type[BaseModel]             # Pydantic class for the request body
    returns_schema: type[BaseModel] | None   # for skills that return values
    requires_states: list[EquipmentState]    # states that permit this skill
                                             # (used in v0.2 before allowed_actions exists)
    requires_components: dict[str, str]      # AND-gate: per-component state
                                             # e.g. {"heater": "stable"} for seal.start
    estimated_duration_s: float | None       # rough cost hint for planners


# Runtime instance bound to a specific role + computed availability.
# This is what `await lab.skills()` returns.
class Skill(BaseModel):
    name: str                                # e.g. "seal.start"
    role: str                                # project's role binding, e.g. "sealer"
    equipment_id: str                        # the bound device id
    kind: EquipmentKind
    description: str
    args_schema: type[BaseModel]
    estimated_duration_s: float | None

    available: bool
    reason: str | None                       # populated when available=False
                                             # e.g. "device is busy", "claim required",
                                             # "device under maintenance"
```

`SkillDef` is a *catalog entry*: properties of a capability independent of any specific instrument. `Skill` is a *runtime view* binding a `SkillDef` to a project role and a live availability assessment.

## Where the catalog lives

```
skills/src/lab_skills/
├── skill_catalog/
│   ├── __init__.py
│   ├── registry.py        # SKILL_REGISTRY: dict[EquipmentKind, list[SkillDef]]
│   ├── plate_sealer.py    # skill defs for kind = plate_sealer
│   ├── press.py           # skill defs for kind = press
│   ├── robot_arm.py       # ...
│   ├── solid_doser.py
│   ├── liquid_handler.py
│   ├── plate_reader.py
│   ├── plate_stacker.py
│   ├── hplc.py            # skill defs for kind = hplc (Agilent UPLC-MS sidecar)
│   ├── fume_hood.py
│   └── env_sensor.py
└── ...
```

One module per `EquipmentKind`, each populating `SKILL_REGISTRY`. Adding a new device kind = adding one file. The registry is a pure-Python dict at import time; no I/O, no parsing.

Example for `plate_sealer`:

```python
# skills/.../skill_catalog/plate_sealer.py
from pydantic import BaseModel, Field
from .registry import register

class SealStartArgs(BaseModel):
    temperature_c: int = Field(ge=20, le=235)
    seconds: float = Field(ge=0.5, le=12.0)

class StageInArgs(BaseModel):
    pass

register("plate_sealer", [
    SkillDef(
        name="seal.start",
        kind="plate_sealer",
        description="Run a seal cycle at the given temperature and duration.",
        endpoint="/control/seal/start",
        args_schema=SealStartArgs,
        returns_schema=None,
        requires_states=["ready"],
        estimated_duration_s=8.0,
    ),
    SkillDef(
        name="stage.in",
        kind="plate_sealer",
        description="Move plate stage into the sealing chamber.",
        endpoint="/control/stage/in",
        args_schema=StageInArgs,
        requires_states=["ready"],
        estimated_duration_s=3.0,
    ),
    # ... seal.stop, stage.out, etc.
])
```

## How `await lab.skills()` is computed

```mermaid
sequenceDiagram
    participant W as Workflow
    participant S as LabSession
    participant Agg as Aggregator
    participant Reg as SKILL_REGISTRY
    participant D as Device /status

    W->>S: await lab.skills()
    S->>Reg: lookup SkillDefs for each bound role's kind
    par
      S->>Agg: fetch_one("plateloc")
      Agg->>D: GET /status
      D-->>Agg: EquipmentStatus
    and
      S->>Agg: fetch_one("filter_every_well")
      Agg->>D: GET /status
      D-->>Agg: EquipmentStatus
    end
    S->>S: for each (role, SkillDef) compute availability
    S-->>W: list[Skill]
```

For each (role, equipment_id) in the binding:

1. Look up `kind = registry.entry(equipment_id).kind`
2. Look up `defs = SKILL_REGISTRY[kind]`
3. Fetch the latest `/status` snapshot (via the aggregator's cache; refresh if older than `poll_timeout_seconds`)
4. For each `SkillDef`, build a `Skill`:
   - **Available** if and only if all of:
     - `status.equipment_status in def.requires_states` (or `status.allowed_actions` contains `def.name` once v1.1 is in)
     - every entry in `def.requires_components` matches a corresponding `components[name].state` in the live status (AND-gate on top of the state check; empty dict is "no constraint")
     - the device is reachable (no `fetch_error`)
     - if claims are required (v0.3), this session holds a valid lease
     - the device is not under maintenance
   - **Reason** populated with the failing condition's human-readable message

The result is a snapshot — calling `lab.skills()` again returns fresh availability without re-importing anything.

## Three sources of truth for "what's invokable", evolving over time

| Source | Authority | When it's used |
|---|---|---|
| **Local `requires_states` per `SkillDef`** | SDK's hard-coded knowledge of equipment kinds | v0.2: only source until devices ship v1.1 |
| **Device's `/openapi.json`** | What the device actually accepts | optional cross-check / auto-discovery (v0.4+) |
| **Device's `/status.allowed_actions`** | Authoritative runtime list from the device | v0.3+: source of truth wherever available |

The SDK uses these in priority order:

1. If `status.allowed_actions` is present (STATUS_SPEC v1.1 device), use it directly. The local table is a *hint* for missing fields like `description` and `args_schema`.
2. Else fall back to `def.requires_states` (v1.0 devices).
3. Independently of which source resolves the state check, also enforce `def.requires_components` as an AND-gate. Examples in the registered catalog:
   - **`plate_sealer.seal.start`** — `requires_components={"heater": "stable", "stage": "in"}`. Mirrors plateloc v1.3+'s two-precondition HTTP 412 (heater band + stage interlock). The dashboard tile pre-checks both client-side; `lab.skills()` would also report `available=False` if either condition fails.
   - **`shaker.shake.start`** — `requires_components={"motor": "idle"}`. Motor-only AND-gate so a heater-side `degraded` (e.g. SC25XR `cal3` RTD fault) doesn't block shaking. The corresponding heater-side action `shaker.shake.set_temperature` carries the complementary `requires_components={"heater": "stable"}` so it stays unavailable when the heater is the failing subsystem.
   - **`liquid_handler.lights.set`** (OT-2 deck-light) — `requires_states=[]`, no `requires_components`. Convenience-class control; advertised in `allowed_actions` whenever the robot is reachable regardless of `equipment_status`.
   - **`hplc.run.submit`** / **`hplc.instrument.standby`** / **`hplc.workflow.start`** (Agilent UPLC-MS) — enqueue verbs gated by the device's FIFO queue (the sidecar owns the queue; OpenLab is reserved for technician servicing). The sidecar drops them from `allowed_actions` when the queue is full (HTTP 412 `queue_full` with a `Retry-After`), when OpenLab is down (409 `requires_init`), or when a technician is servicing the instrument directly (409 `instrument_servicing`). `instrument.standby` parks the instrument in low-flow standby — a true power-down is a deliberate manual procedure, deliberately not exposed as a skill. `workflow.start` takes the equipment-blocking workflow lock for a robot/agent campaign (requires the claim owner's role to be `automation` → 403 `role_forbidden` otherwise; while held, non-holder submits get 423 `workflow_active`); it is offered only while no workflow is active. The non-enqueue verbs **`hplc.run.abort`**, **`hplc.queue.cancel`** (the latter a `DELETE /control/queue/{queue_id}`), and **`hplc.workflow.end`** (idempotent lock release, offered exactly while a workflow is active) carry no such precondition and stay listed whenever the instrument is operational. The operator/dashboard service-mode toggles (`POST /control/service/start|end`) are technician controls, deliberately **not** modelled as agent skills.

This means devices migrating to v1.1 progressively make the catalog more accurate without any SDK rebuild. The SDK never needs to "know" the precondition rules of a specific device — the device declares them. `requires_components` is a curated SDK-side hint for the cases where the device's coarse state (or `allowed_actions`) is too permissive for a specific action.

## Auto-derivation from OpenAPI (deferred)

Every device already publishes `/openapi.json` (FastAPI is free). A future SDK release can:

1. Discover skills by walking `paths` in the OpenAPI document, filtering to `/control/*`.
2. Resolve `requestBody` schemas to dynamic Pydantic models via `pydantic.create_model` from the JSON Schema.
3. Use `operationId` (or path-derived names) for `Skill.name`.
4. Use `summary` / `description` for documentation.

This eliminates the per-kind catalog file. The cost is more SDK complexity (JSON Schema → Pydantic at startup) and less editorial control over `Skill.name` formatting.

We ship the hard-coded catalog first because:

- The exact set of devices is small and known.
- Hand-curated names (`seal.start` vs `controlSealStart`) read better in agent prompts and dashboards.
- Errors fail at import time, not when an agent first calls a skill.

We revisit OpenAPI derivation when the device count grows past ~10 or when a third-party device is added that we don't want to ship a hand-written catalog for.

## What this enables

- **`await lab.skills()`** — a list of typed, currently-available capabilities, used by workflows and the dashboard.
- **`lab.role("sealer").seal_start(...)`** — typed methods on `EquipmentClient` are *generated* from the catalog, not hand-written per device repo (avoids drift).
- **Plan validation** (`docs/INTERLOCKS.md`) — `validate_plan` consults the catalog to verify each step's args match the schema before any side-effects.
- **MCP server** (v0.4) — tools listed to agents are `Skill` objects converted to MCP tool descriptors. Adding a new equipment kind to the lab automatically extends the agent's toolbelt.
- **Dashboard skill panels** — UI can render "what can be done now" by reading the catalog instead of hard-coding buttons per device.

## Versioning the catalog

Skill names are part of the SDK's API surface. Renaming a skill is a breaking change. Rules:

- New skills can be added in minor releases.
- Skill schemas can be extended (new optional fields) in minor releases.
- Removing a skill, renaming a skill, or changing a required field requires a major version bump and a deprecation period.
- The set of `EquipmentKind` enum values is governed by `STATUS_SPEC.md`, not the catalog.

## See also

- `docs/STATUS_SPEC.md` — the device contract that the catalog mirrors
- `docs/INTERLOCKS.md` — how the catalog feeds plan validation
- `docs/ARCHITECTURE.md` — overall system layering
