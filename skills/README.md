# lab-skills

The Python SDK and aggregator for the AC Organic Self-driving Lab. This is the package that workflow code, the dashboard server (`api/`), and (future) agents import to drive the lab.

This package is a workspace member of the [`ac-organic-lab`](../README.md) monorepo. The repo-root [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) describes how it fits into the platform; the milestone state lives in [`docs/ROADMAP.md`](../docs/ROADMAP.md).

## Status

**v0.4 shipped** (2026-07-12): `execute_plan` (sequential live executor with
per-step claims and layer-3 + layer-4 re-checks), async interlocks, the sync
façades, and the MCP server companion (`lab_skills.mcp`, `lab-skills mcp
serve` — catalog as tools, `execute_plan` gated behind `--allow-control`).
Contract types come from the shared `sdl-lab-contract` package (versioned to
the spec, currently v1.2). v0.5 (standalone `lab-skills serve` aggregator
CLI) is not started. See [`docs/ROADMAP.md`](../docs/ROADMAP.md) for the full
state of the SDK and equipment migration.

What's in the package today:

| Module | What it owns |
|---|---|
| `registry` | `equipment.yaml` parser → `Registry` / `EquipmentEntry` (incl. `enabled`, `maintenance`, `tile`, `location`, camera + plug configs). |
| `aggregator` | One async polling loop with a shared `httpx.AsyncClient`; fans out per-device fetches; returns `EquipmentList` of `EquipmentSnapshot`. |
| `status_adapters` | `http` (STATUS_SPEC v1.0–v1.2), `legacy_http` (per-device translation; unused since LG2 closed), `mock`. |
| `models` | `EquipmentStatus` envelope, `ProbeResponse`, `HealthResponse`, `ComponentStatus`, `MetricValue`, `ErrorInfo`. |
| `lab` / `session` / `client` | `Lab.connect(...)` → `LabSession` (async ctx mgr) → `EquipmentClient` (read: `status()`/`probe()`/`health()`; write: `command()`). |
| `typed_clients` | Per-kind subclasses with typed methods (`PlateSealerClient`, `PlateReaderClient`, `PressClient`, `RobotArmClient`, `SolidDoserClient`, `FumeHoodClient`). |
| `skill_catalog` | `SkillDef` registry per `kind` + runtime `Skill` evaluation against `/status` (`allowed_actions` on v1.1 devices, `requires_states` fallback on v1.0). |
| `claims` | `ClaimManager` for STATUS_SPEC v1.1 `/control/claim,heartbeat,release` with TTL/lease semantics. |
| `plan` | `Plan` / `Step` / `validate_plan(...)` → `PlanReport` (offline preflight), and `execute_plan(...)` → `PlanRunReport` (live: per-step `ClaimManager`, layer-3 + layer-4 re-checks before each step, `wait_timeout_s` for time-clearing preconditions, `dry_run` preflight). |
| `interlocks` | `register_interlock(fn)` → `Violation` list; layer 4 of the four-layer safety model in [`docs/INTERLOCKS.md`](../docs/INTERLOCKS.md). Sync rules run in `validate_plan` + `execute_plan`; `async def` rules run in `execute_plan` via `run_interlocks_async`. |
| `sync` | Sync façade for notebooks / sync CLIs, including `SyncLabSession.validate_plan` / `execute_plan`. (A standalone sync `ClaimManager` remains deferred — see ROADMAP.) |
| `mcp` | The MCP server companion (`lab-skills mcp serve`): catalog → tools (`list_equipment`, `list_skills`, `get_status`, `validate_plan`, `preflight_plan`), `/status` → resources; the actuating `execute_plan` tool only behind `--allow-control`. Optional extra (`pip install lab-skills[mcp]`). |
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
