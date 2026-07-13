"""Control-capable MCP server for the lab-skills SDK.

This is the SDK's **skill-catalog** MCP server (``docs/ARCHITECTURE.md``
decision #7 / #10) — distinct from the dashboard's read-only *history* MCP
server in ``api/app/mcp_server.py``. It exposes the runtime skill catalog as
MCP tools and each device's ``/status`` as MCP resources, so an MCP-aware
agent (Claude Code, Claude Desktop, a custom client) can inspect and — when
explicitly permitted — drive the lab through the same claim / plan-validation
/ interlock path as any other SDK client.

Safety
------
The read + validate + **dry-run** tools are always exposed. The actuating
``execute_plan`` tool (``dry_run=False``, real claims + control POSTs) is
registered **only** when the server is started with ``allow_control=True``
(CLI ``--allow-control``). With control disabled an agent can list skills,
read status, validate a plan, and preflight it live, but cannot move
hardware. Control still runs through ``execute_plan`` — per-step claims,
layer-3 + layer-4 re-checks, the device as the authority — so the cooperative
lock and interlocks apply to agent ops exactly as to workflow ops (LG6).

Transport
---------
stdio (what Claude Code expects). Register with, e.g.::

    claude mcp add lab-skills -- \\
        uv run --project /path/to/ac-organic-lab/skills \\
            lab-skills mcp serve --binding sealer=plateloc

The ``mcp`` package is imported lazily inside :func:`_build_server`, so this
module (and the tool logic below) imports and unit-tests without it.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from typing import Any

from .exceptions import LabError
from .lab import Lab
from .plan import Plan, Step
from .plan import execute_plan as _execute_plan
from .plan import validate_plan as _validate_plan
from .registry import Registry

logger = logging.getLogger(__name__)


@dataclass
class MCPConfig:
    """Everything the server needs, captured once at start-up.

    A fresh :class:`~lab_skills.LabSession` is opened per tool call (low call
    rate; keeps each call's HTTP client + any claim heartbeat scoped to that
    call), all against this fixed registry + binding.
    """

    registry: Registry
    binding: dict[str, str] = field(default_factory=dict)
    owner: str = "mcp:lab-skills"
    allow_control: bool = False
    http_timeout: float = 5.0


def _dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)


def _build_plan(steps: list[dict[str, Any]]) -> Plan:
    return Plan(
        steps=[
            Step(role=s["role"], skill=s["skill"], args=s.get("args") or {})
            for s in steps
        ]
    )


# ---------------------------------------------------------------------------
# Tool logic (plain async functions; the FastMCP wrappers just delegate here
# so the behaviour is unit-testable without the mcp package).
# ---------------------------------------------------------------------------


async def _list_equipment(config: MCPConfig) -> str:
    """Registry view — offline, no device I/O."""

    rows = [
        {
            "id": e.id,
            "name": e.name,
            "kind": e.kind,
            "protocol": e.protocol,
            "adapter": e.adapter,
            "enabled": e.enabled,
            "maintenance": e.maintenance.reason if e.maintenance else None,
            "do_not_call_connect": e.do_not_call_connect,
        }
        for e in config.registry.equipment
    ]
    bound = {role: eid for role, eid in config.binding.items()}
    return _dumps({"equipment": rows, "bindings": bound})


async def _list_skills(config: MCPConfig) -> str:
    """Runtime skill catalog (catalog × live availability) for every bound
    role. ``args_schema`` is emitted as JSON Schema."""

    async with Lab.connect(
        registry=config.registry, binding=config.binding, http_timeout=config.http_timeout
    ) as lab:
        skills = await lab.skills()
    rows = [
        {
            "name": s.name,
            "role": s.role,
            "equipment_id": s.equipment_id,
            "kind": s.kind,
            "description": s.description,
            "available": s.available,
            "reason": s.reason,
            "estimated_duration_s": s.estimated_duration_s,
            "args_schema": s.args_schema.model_json_schema(),
        }
        for s in skills
    ]
    return _dumps({"skills": rows})


async def _get_status(config: MCPConfig, target: str) -> str:
    """Live ``/status`` for a role name or equipment id."""

    try:
        async with Lab.connect(
            registry=config.registry,
            binding=config.binding,
            http_timeout=config.http_timeout,
        ) as lab:
            client = lab.role(target) if target in config.binding else lab.get(target)
            status = await client.status()
        return _dumps(status.model_dump(mode="json"))
    except LabError as exc:
        return _dumps({"error": f"{type(exc).__name__}: {exc}", "target": target})


async def _validate(config: MCPConfig, steps: list[dict[str, Any]]) -> str:
    """Offline plan validation — no HTTP."""

    try:
        plan = _build_plan(steps)
    except (KeyError, TypeError) as exc:
        return _dumps({"error": f"invalid steps: {exc}"})
    async with Lab.connect(
        registry=config.registry, binding=config.binding, http_timeout=config.http_timeout
    ) as lab:
        report = _validate_plan(plan, lab)
    return _dumps(report.model_dump(mode="json"))


async def _run(
    config: MCPConfig,
    steps: list[dict[str, Any]],
    *,
    dry_run: bool,
    wait_timeout_s: float = 0.0,
) -> str:
    """Execute (or, ``dry_run``, preflight) a plan and return the run report."""

    try:
        plan = _build_plan(steps)
    except (KeyError, TypeError) as exc:
        return _dumps({"error": f"invalid steps: {exc}"})
    async with Lab.connect(
        registry=config.registry, binding=config.binding, http_timeout=config.http_timeout
    ) as lab:
        report = await _execute_plan(
            plan,
            lab,
            owner=config.owner,
            dry_run=dry_run,
            wait_timeout_s=wait_timeout_s,
        )
    return _dumps(report.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------


def _build_server(config: MCPConfig):
    """Build the FastMCP server. ``mcp`` is imported here so the module and
    its tool logic import without the package installed."""

    from mcp.server.fastmcp import FastMCP

    server = FastMCP("lab-skills")

    @server.tool()
    async def list_equipment() -> str:
        """List every registered device (id, name, kind, protocol, enabled,
        maintenance, do_not_call_connect) plus the active role→id bindings.
        Offline — start here to learn the roles and ids the other tools take."""

        return await _list_equipment(config)

    @server.tool()
    async def list_skills() -> str:
        """The runtime skill catalog for every bound role: each skill's name,
        role, equipment_id, kind, description, JSON-Schema args, and whether it
        is available right now (with a reason when not). Reads live /status."""

        return await _list_skills(config)

    @server.tool()
    async def get_status(target: str) -> str:
        """Live STATUS_SPEC /status envelope for a role name or equipment id
        (equipment_status, components, metrics, allowed_actions, ...)."""

        return await _get_status(config, target)

    @server.tool()
    async def validate_plan(steps: list[dict]) -> str:
        """Offline-validate a plan (no hardware). ``steps`` is a list of
        {role, skill, args} objects. Returns a PlanReport with per-step
        violations / warnings. Run this before preflight/execute."""

        return await _validate(config, steps)

    @server.tool()
    async def preflight_plan(steps: list[dict], wait_timeout_s: float = 0.0) -> str:
        """Live preflight: validate, then per step re-check the device's live
        allowed_actions + interlocks — but never claim or POST a command.
        Marks passing steps 'dry_run'. Safe to run without --allow-control."""

        return await _run(config, steps, dry_run=True, wait_timeout_s=wait_timeout_s)

    if config.allow_control:

        @server.tool()
        async def execute_plan(steps: list[dict], wait_timeout_s: float = 0.0) -> str:
            """ACTUATES HARDWARE. Execute a plan: per step, re-check live
            allowed_actions + interlocks, acquire a claim, POST the control
            command. Fail-fast (first failure aborts; rest 'skipped').
            ``wait_timeout_s`` waits out a time-clearing precondition (e.g. a
            heater ramp). Only registered when the server was started with
            --allow-control."""

            return await _run(
                config, steps, dry_run=False, wait_timeout_s=wait_timeout_s
            )

    @server.resource("lab://equipment")
    async def equipment_resource() -> str:
        """The registry + bindings, as a resource."""

        return await _list_equipment(config)

    @server.resource("lab://status/{target}")
    async def status_resource(target: str) -> str:
        """Live /status for one role or equipment id, as a resource."""

        return await _get_status(config, target)

    return server


def serve(config: MCPConfig) -> None:
    """Boot the FastMCP server over stdio and block until the client closes
    the stream. Logs go to stderr; stdout is reserved for MCP JSON-RPC."""

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    logger.info(
        "lab-skills MCP server: %d devices, %d bound roles, control=%s",
        len(config.registry.equipment),
        len(config.binding),
        config.allow_control,
    )
    _build_server(config).run()


__all__ = ["MCPConfig", "serve"]
