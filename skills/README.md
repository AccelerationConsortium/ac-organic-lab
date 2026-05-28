# lab-skills

The Python SDK and aggregator for the AC Organic Self-driving Lab. This is the package that workflow code, the dashboard server (`api/`), and (future) agents import to drive the lab.

This package is a workspace member of the [`ac-organic-lab`](../README.md) monorepo. The repo-root [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) describes how it fits into the platform; the milestone state lives in [`docs/ROADMAP.md`](../docs/ROADMAP.md).

## Status

**v0.3 shipped.** v0.4 (MCP server companion) is paused on the v0.3 carry-overs (`execute_plan`, async interlocks, sync façades). See [`docs/ROADMAP.md`](../docs/ROADMAP.md) for the full state of the SDK and equipment migration.

What's in the package today:

| Module | What it owns |
|---|---|
| `registry` | `equipment.yaml` parser → `Registry` / `EquipmentEntry` (incl. `enabled`, `maintenance`, `tile`, `location`, camera + plug configs). |
| `aggregator` | One async polling loop with a shared `httpx.AsyncClient`; fans out per-device fetches; returns `EquipmentList` of `EquipmentSnapshot`. |
| `status_adapters` | `http` (STATUS_SPEC v1.0/v1.1), `legacy_http` (per-device translation), `mock`. |
| `models` | `EquipmentStatus` envelope, `ProbeResponse`, `HealthResponse`, `ComponentStatus`, `MetricValue`, `ErrorInfo`. |
| `lab` / `session` / `client` | `Lab.connect(...)` → `LabSession` (async ctx mgr) → `EquipmentClient` (read: `status()`/`probe()`/`health()`; write: `command()`). |
| `typed_clients` | Per-kind subclasses with typed methods (`PlateSealerClient`, `PressClient`, `RobotArmClient`, `SolidDoserClient`, `FumeHoodClient`). |
| `skill_catalog` | `SkillDef` registry per `kind` + runtime `Skill` evaluation against `/status` (`allowed_actions` on v1.1 devices, `requires_states` fallback on v1.0). |
| `claims` | `ClaimManager` for STATUS_SPEC v1.1 `/control/claim,heartbeat,release` with TTL/lease semantics. |
| `plan` | `Plan` / `Step` / `validate_plan(...)` → `PlanReport`; preflight checks before any destructive action. |
| `agent` | `AgentRuntime` + `compose_workflow(...)`; turns agent tasks into validated plans, queues low-confidence tasks for expert review, and supports dry-run execution. |
| `interlocks` | `register_interlock(fn)` → `Violation` list; layer 4 of the four-layer safety model in [`docs/INTERLOCKS.md`](../docs/INTERLOCKS.md). Sync today; async signature lands with `execute_plan`. |
| `sync` | Sync façade for notebooks / sync CLIs; expanding to cover `ClaimManager` and `validate_plan` as part of the v0.3 carry-overs. |
| `waiting` | `wait_until_state(...)` and other state-machine helpers. |
| `exceptions` | Typed error hierarchy: `EquipmentUnreachable`, `EquipmentBusy`, `EquipmentInMaintenance`, `RequiresInit`, `BadRequest`, `ClaimRejected`, `Degraded`, `WaitTimeout`. |

## Usage

```python
from lab_skills import Lab

async with Lab.connect(binding={"sealer": "plateloc"}) as lab:
    sealer = lab.role("sealer")
    status = await sealer.status()
    skills = await lab.skills()      # runtime catalog: what's invokable now
    # v0.3+: wrap mutating calls in a claim
    async with lab.claim("plateloc") as claim:
        await sealer.command("seal.start", body={...})
```

The aggregator reads `equipment.yaml` from the monorepo root by default; override with `LAB_REGISTRY_PATH=/abs/path/to/equipment.yaml`.

Agent runtime smoke test without hardware:

```bash
uv run --with pytest --with pytest-asyncio --with respx python skills/examples/agent_runtime_demo.py
```

The demo composes a low-confidence task, asks for expert approval, and dry-runs the approved plan entirely in memory.

## Local development

From the repo root (uv workspace; `skills/` and `api/` share one `.venv`):

```bash
uv sync
uv run pytest skills/tests -q
```

Single-file hot-reload while editing the SDK:

```bash
uv run pytest skills/tests/test_session.py -q --no-header -x
```
