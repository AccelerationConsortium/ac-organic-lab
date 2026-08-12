"""Propose-only control MCP server for the dashboard lab assistant.

This is the ``lab-control`` MCP server from ``docs/UI_DESIGN.md`` §5 (Assistant
control mode, Step 1). It is a *second* console script beside
``lab-history-mcp`` (``app/mcp_server.py``) and is spawned per request by
``app/assistant.py`` only when the operator is in **Control** mode.

Safety
------
**Neither tool actuates hardware.** The most privileged thing this server can
do is return a *validated proposal object*; the actual control POST happens
later, in the browser, when the operator clicks *Authorize* over the existing
``/api/equipment/{id}/control/{action}`` passthrough. With no actuating tool
registered, the worst a prompt-injected instruction can achieve is raising a
confirm card a human must read and click — the read-only-by-construction
guarantee of ARCHITECTURE decision #10 survives (UI_DESIGN §5.1 / §5.4).

Actor binding
-------------
The verified operator is passed in the **environment** (``LAB_ACTOR``), never
as a tool argument the model could choose. ``propose_action`` refuses when it
is unset. Per-equipment authorization (``operator``+ on *that* equipment) is
re-checked against the ac_auth sidecar (``AUTH_SERVICE_BASE`` ``/authz/check``)
before a proposal is returned; the check fails closed.

Scope
-----
Step 1 proposes exactly one action on one device. The action-name resolver
covers ``robot_arm`` move targets only (``move.<node_id>`` -> the
``graph.move_to`` skill -> ``POST /control/graph/move_to``). Safety-floor
actions (``stop`` / ``connect`` / ``clear_errors``) are deliberately **not**
proposable — they are operator buttons and must stay reachable without the
assistant. Anything the resolver cannot map is refused.

Transport
---------
stdio (what Claude Code expects). Registered by ``assistant.py`` via a
generated ``--mcp-config`` entry; the ``mcp`` package is imported lazily inside
:func:`_build_server` so this module and its tool logic import and unit-test
without it.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

import httpx
from pydantic import ValidationError

from lab_skills import Lab, load_registry
from lab_skills.exceptions import (
    EquipmentInMaintenance,
    EquipmentUnreachable,
    LabError,
)
from lab_skills.registry import EquipmentEntry, Registry
from lab_skills.skill_catalog import SkillDef, skills_for

logger = logging.getLogger(__name__)

# How long the browser should treat a returned proposal as fresh before it
# must be re-proposed. The device's 412/423 at click time remains the real
# backstop; this only bounds how stale the confirm card may be (UI_DESIGN §5.3).
PROPOSAL_TTL_S = 120


# ---------------------------------------------------------------------------
# Config seams (mirror api/app/control.py so behaviour matches the passthrough)
# ---------------------------------------------------------------------------


def _authz_base() -> str:
    """Auth sidecar base URL (same env the passthrough + middleware use)."""

    return os.environ.get("AUTH_SERVICE_BASE", "http://127.0.0.1:8009")


def _authz_enforced() -> bool:
    """Escape hatch mirroring ``control.py``: set ``CONTROL_AUTHZ_ENFORCE=false``
    for local dev without the auth sidecar. Enforced by default."""

    return os.environ.get("CONTROL_AUTHZ_ENFORCE", "true").lower() != "false"


def _actor() -> str | None:
    """The verified operator bound into this server's environment, or None.

    Set by ``assistant.py`` from the request's ``X-Auth-User`` (injected by the
    Next.js middleware after verifying the session — never client-supplied).
    """

    actor = os.environ.get("LAB_ACTOR", "").strip()
    return actor or None


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


class ProposalRefused(Exception):
    """A proposal cannot be formed. Carries a machine code + human message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)


def _err(code: str, message: str, **extra: Any) -> str:
    return _dumps({"error": message, "code": code, **extra})


# ---------------------------------------------------------------------------
# Action-name resolver (the one non-obvious mapping — see module docstring)
# ---------------------------------------------------------------------------


def _find_skill_def(kind: str, name: str) -> SkillDef | None:
    for d in skills_for(kind):
        if d.name == name:
            return d
    return None


def _passthrough_action(sd: SkillDef) -> str:
    """The ``{action}`` segment the browser POSTs to
    ``/api/equipment/{id}/control/{action}`` for this skill's endpoint.

    ``control.py`` composes the device URL as ``<status_path base>/control/`` +
    ``action.lstrip("/")``, so we hand back the endpoint with its ``/control/``
    prefix removed (``/control/graph/move_to`` -> ``graph/move_to``).
    """

    ep = sd.endpoint
    prefix = "/control/"
    if ep.startswith(prefix):
        return ep[len(prefix):]
    return ep.lstrip("/")


def _resolve(entry: EquipmentEntry, action: str, args: dict[str, Any]) -> tuple[SkillDef, str, dict[str, Any]]:
    """Map a device ``allowed_actions`` string to a proposable action.

    Returns ``(skill_def, passthrough_action, resolved_args)`` or raises
    :class:`ProposalRefused`. Step 1 scope: ``robot_arm`` move targets only.
    """

    if entry.kind == "robot_arm" and action.startswith("move."):
        node_id = action[len("move."):]
        if not node_id:
            raise ProposalRefused("unmappable_action", f"malformed move action {action!r}")
        sd = _find_skill_def("robot_arm", "graph.move_to")
        if sd is None:  # pragma: no cover - catalog always registers this
            raise ProposalRefused(
                "unmappable_action", "graph.move_to is not registered in the skill catalog"
            )
        # The node id lives in the action name; merge it into the body the
        # graph.move_to endpoint expects. Model-supplied args (e.g. speed) are
        # kept but node_id from the action name wins.
        resolved = {**(args or {}), "node_id": node_id}
        return sd, _passthrough_action(sd), resolved

    raise ProposalRefused(
        "unmappable_action",
        f"action {action!r} on kind {entry.kind!r} is not proposable by the assistant "
        "(Step 1 supports robot_arm move targets only; safety-floor actions stay "
        "operator-only)",
    )


def _validate_args(sd: SkillDef, resolved_args: dict[str, Any]) -> None:
    try:
        sd.args_schema.model_validate(resolved_args)
    except ValidationError as exc:
        raise ProposalRefused(
            "invalid_args",
            f"args do not validate against the {sd.name!r} schema: {exc.errors()}",
        )


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


async def _check_authz(actor: str, equipment_id: str) -> tuple[bool, str | None]:
    """Does ``actor`` hold ``operator``+ on ``equipment_id``? Fails closed."""

    if not _authz_enforced():
        return True, None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_authz_base()}/authz/check",
                params={"user": actor, "equipment": equipment_id},
            )
            resp.raise_for_status()
            verdict = resp.json()
    except Exception as exc:  # noqa: BLE001 - fail closed on any authz error
        return False, f"authorization check failed: {exc}"
    if not verdict.get("allowed"):
        return False, f"{actor} is not authorized to control {equipment_id}"
    return True, None


# ---------------------------------------------------------------------------
# Tool logic (plain async fns; FastMCP wrappers delegate so this is unit
# testable without the mcp package installed).
# ---------------------------------------------------------------------------


async def _read_status(registry: Registry, equipment_id: str):
    """Live ``/status`` for one device via the SDK (read-only, side-effect-free)."""

    async with Lab.connect(registry=registry) as lab:
        client = lab.get(equipment_id)
        return await client.status()


async def _list_available_actions(registry: Registry, equipment_id: str) -> str:
    """Live ``allowed_actions`` for a device, each annotated with whether the
    assistant can propose it and (when it can) the JSON-Schema for its args."""

    entry = registry.by_id(equipment_id)
    if entry is None:
        return _err("unknown_equipment", f"no equipment with id {equipment_id!r}")
    try:
        status = await _read_status(registry, equipment_id)
    except EquipmentInMaintenance:
        return _err("disabled", f"{equipment_id!r} is disabled or under maintenance")
    except (EquipmentUnreachable, LabError) as exc:
        return _err("unreachable", f"could not read /status for {equipment_id!r}: {exc}")

    actions: list[dict[str, Any]] = []
    for action in status.allowed_actions or []:
        info: dict[str, Any] = {"action": action, "proposable": False}
        try:
            sd, passthrough, _ = _resolve(entry, action, {})
        except ProposalRefused:
            actions.append(info)
            continue
        info.update(
            proposable=True,
            passthrough_action=passthrough,
            description=sd.description,
            args_schema=sd.args_schema.model_json_schema(),
        )
        actions.append(info)

    return _dumps(
        {
            "equipment_id": entry.id,
            "equipment_name": entry.name,
            "kind": entry.kind,
            "equipment_status": status.equipment_status,
            "activity": status.activity,
            "message": status.message,
            "actions": actions,
        }
    )


async def _propose_action(
    registry: Registry,
    equipment_id: str,
    action: str,
    args: dict[str, Any] | None,
    reason: str,
) -> str:
    """Validate a single-equipment action and return a normalized proposal.

    Refuses unless ALL hold: a verified actor is bound; the equipment exists
    and is enabled; ``action`` is in the device's live ``allowed_actions``; the
    resolver maps it to a control endpoint; ``args`` validate against the
    skill schema; and the actor holds ``operator``+ on that equipment.
    """

    actor = _actor()
    if actor is None:
        return _err(
            "no_actor",
            "no verified actor is bound to this session (LAB_ACTOR unset); "
            "control proposals require a signed-in operator",
        )

    entry = registry.by_id(equipment_id)
    if entry is None:
        return _err("unknown_equipment", f"no equipment with id {equipment_id!r}")
    if not entry.enabled or entry.maintenance is not None:
        return _err("disabled", f"{equipment_id!r} is disabled or under maintenance")

    try:
        status = await _read_status(registry, equipment_id)
    except EquipmentInMaintenance:
        return _err("disabled", f"{equipment_id!r} is disabled or under maintenance")
    except (EquipmentUnreachable, LabError) as exc:
        return _err("unreachable", f"could not read /status for {equipment_id!r}: {exc}")

    if action not in (status.allowed_actions or []):
        return _err(
            "not_allowed",
            f"{action!r} is not in {equipment_id!r}'s current allowed_actions",
            allowed_actions=list(status.allowed_actions or []),
        )

    try:
        sd, passthrough, resolved_args = _resolve(entry, action, args or {})
        _validate_args(sd, resolved_args)
    except ProposalRefused as exc:
        return _err(exc.code, exc.message)

    ok, why = await _check_authz(actor, equipment_id)
    if not ok:
        return _err("not_authorized", why or "not authorized")

    proposal = {
        "equipment_id": entry.id,
        "equipment_name": entry.name,
        "kind": entry.kind,
        "action": action,
        "passthrough_action": passthrough,
        "args": resolved_args,
        "reason": reason,
        "actor": actor,
        "expires_in_s": PROPOSAL_TTL_S,
        "device_state": {
            "equipment_status": status.equipment_status,
            "activity": status.activity,
            "message": status.message,
        },
    }
    return _dumps({"proposal": proposal})


# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------


def _build_server(registry: Registry):
    """Build the FastMCP server. ``mcp`` is imported here so the module and its
    tool logic import without the package installed (matches mcp_server.py)."""

    from mcp.server.fastmcp import FastMCP

    server = FastMCP("lab-control")

    @server.tool()
    async def list_available_actions(equipment_id: str) -> str:
        """The device's live ``allowed_actions`` plus, for each action the
        assistant can propose, its argument JSON-Schema. Call this before
        propose_action to learn what is legal instead of guessing endpoints.
        Only ``robot_arm`` move targets are proposable in this mode."""

        return await _list_available_actions(registry, equipment_id)

    @server.tool()
    async def propose_action(
        equipment_id: str,
        action: str,
        args: dict | None = None,
        reason: str = "",
    ) -> str:
        """Propose ONE action on ONE device for the operator to authorize. Does
        NOT actuate hardware — it returns a validated proposal that the operator
        confirms with a click. ``action`` must be one from
        list_available_actions; ``reason`` is a one-line justification shown
        subordinate to the device's authoritative state. Returns an ``error`` +
        ``code`` object when the proposal is refused."""

        return await _propose_action(registry, equipment_id, action, args, reason)

    return server


def run() -> None:
    """Entry point for the ``lab-control-mcp`` console script (pyproject).

    Boots the FastMCP server over stdio and blocks until the client closes the
    stream. Logs go to stderr; stdout is reserved for MCP JSON-RPC framing.
    """

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    registry = load_registry()
    logger.info(
        "lab-control MCP server: %d devices, actor=%s, authz_enforced=%s",
        len(registry.equipment),
        _actor(),
        _authz_enforced(),
    )
    _build_server(registry).run()


if __name__ == "__main__":  # pragma: no cover
    run()


__all__ = ["run", "PROPOSAL_TTL_S"]
