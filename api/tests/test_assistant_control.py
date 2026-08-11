"""Tests for the propose-only ``lab-control`` MCP server (UI_DESIGN §5 Step 1).

The two tools never actuate; they return a validated proposal object or a
structured refusal. We exercise the tool-logic functions directly (the FastMCP
wrappers just delegate), stubbing the device ``/status`` read and the ac_auth
``/authz/check`` with respx.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from app import assistant_control as ac
from lab_skills.registry import EquipmentEntry, Registry

ARM_BASE = "http://arm.test:8000"
AUTHZ = "http://127.0.0.1:8009/authz/check"
ACTOR = "alice@example.edu"


def _registry(**overrides: Any) -> Registry:
    base: dict[str, Any] = {
        "id": "xarm",
        "name": "UFactory xArm5",
        "kind": "robot_arm",
        "adapter": "http",
        "base_url": ARM_BASE,
        "status_path": "/status",
        "protocol": "1.2",
    }
    base.update(overrides)
    return Registry(equipment=[EquipmentEntry(**base)])


def _status_json(
    allowed_actions: list[str],
    *,
    equipment_status: str = "ready",
    activity: str = "idle",
) -> dict[str, Any]:
    return {
        "equipment_id": "xarm",
        "equipment_name": "UFactory xArm5",
        "equipment_kind": "robot_arm",
        "equipment_status": equipment_status,
        "message": "ok",
        "allowed_actions": allowed_actions,
        "activity": activity,
        "device_time": "2026-08-11T12:00:00Z",
    }


def _mock_status(allowed_actions: list[str], **kw: Any) -> None:
    respx.get(f"{ARM_BASE}/status").mock(
        return_value=httpx.Response(200, json=_status_json(allowed_actions, **kw))
    )


def _mock_authz(allowed: bool) -> None:
    respx.get(AUTHZ).mock(return_value=httpx.Response(200, json={"allowed": allowed}))


@pytest.fixture(autouse=True)
def _actor_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAB_ACTOR", ACTOR)
    monkeypatch.setenv("CONTROL_AUTHZ_ENFORCE", "true")
    monkeypatch.delenv("AUTH_SERVICE_BASE", raising=False)


# ---------------------------------------------------------------------------
# Pure resolver / passthrough unit checks
# ---------------------------------------------------------------------------


def test_passthrough_strips_control_prefix() -> None:
    sd = ac._find_skill_def("robot_arm", "graph.move_to")
    assert sd is not None
    assert ac._passthrough_action(sd) == "graph/move_to"


def test_resolve_move_target() -> None:
    entry = _registry().equipment[0]
    sd, passthrough, args = ac._resolve(entry, "move.plateloc_out", {})
    assert sd.name == "graph.move_to"
    assert passthrough == "graph/move_to"
    assert args == {"node_id": "plateloc_out"}


def test_resolve_refuses_non_move() -> None:
    entry = _registry().equipment[0]
    with pytest.raises(ac.ProposalRefused) as exc:
        ac._resolve(entry, "stop", {})
    assert exc.value.code == "unmappable_action"


# ---------------------------------------------------------------------------
# propose_action — success
# ---------------------------------------------------------------------------


@respx.mock
async def test_propose_success() -> None:
    _mock_status(["stop", "move.plateloc_out"])
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _registry(), "xarm", "move.plateloc_out", None, "stage the plate"
        )
    )
    prop = out["proposal"]
    assert prop["equipment_id"] == "xarm"
    assert prop["action"] == "move.plateloc_out"
    assert prop["passthrough_action"] == "graph/move_to"
    assert prop["args"] == {"node_id": "plateloc_out"}
    assert prop["actor"] == ACTOR
    assert prop["reason"] == "stage the plate"
    assert prop["device_state"]["equipment_status"] == "ready"
    assert prop["expires_in_s"] == ac.PROPOSAL_TTL_S


# ---------------------------------------------------------------------------
# propose_action — every refusal path
# ---------------------------------------------------------------------------


async def test_propose_no_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAB_ACTOR", raising=False)
    out = json.loads(
        await ac._propose_action(_registry(), "xarm", "move.n1", None, "")
    )
    assert out["code"] == "no_actor"


async def test_propose_unknown_equipment() -> None:
    out = json.loads(
        await ac._propose_action(_registry(), "nope", "move.n1", None, "")
    )
    assert out["code"] == "unknown_equipment"


async def test_propose_multi_equipment_is_unknown() -> None:
    # The tool signature takes exactly one id; a comma-joined "batch" is simply
    # not a known equipment id, so it is refused rather than fanned out.
    out = json.loads(
        await ac._propose_action(_registry(), "xarm,plateloc", "move.n1", None, "")
    )
    assert out["code"] == "unknown_equipment"


async def test_propose_disabled_equipment() -> None:
    out = json.loads(
        await ac._propose_action(
            _registry(enabled=False), "xarm", "move.n1", None, ""
        )
    )
    assert out["code"] == "disabled"


@respx.mock
async def test_propose_unreachable() -> None:
    respx.get(f"{ARM_BASE}/status").mock(side_effect=httpx.ConnectError("boom"))
    out = json.loads(
        await ac._propose_action(_registry(), "xarm", "move.n1", None, "")
    )
    assert out["code"] == "unreachable"


@respx.mock
async def test_propose_action_not_allowed() -> None:
    _mock_status(["stop"])  # move target withheld (e.g. not STRICT, or busy)
    out = json.loads(
        await ac._propose_action(_registry(), "xarm", "move.n1", None, "")
    )
    assert out["code"] == "not_allowed"
    assert out["allowed_actions"] == ["stop"]


@respx.mock
async def test_propose_unmappable_action() -> None:
    _mock_status(["stop", "clear_errors"])
    out = json.loads(
        await ac._propose_action(_registry(), "xarm", "stop", None, "")
    )
    assert out["code"] == "unmappable_action"


@respx.mock
async def test_propose_bad_args() -> None:
    _mock_status(["move.n1"])
    # speed must be a float; a string fails the GraphMoveToArgs schema.
    out = json.loads(
        await ac._propose_action(
            _registry(), "xarm", "move.n1", {"speed": "fast"}, ""
        )
    )
    assert out["code"] == "invalid_args"


@respx.mock
async def test_propose_not_authorized() -> None:
    _mock_status(["move.n1"])
    _mock_authz(False)
    out = json.loads(
        await ac._propose_action(_registry(), "xarm", "move.n1", None, "")
    )
    assert out["code"] == "not_authorized"


@respx.mock
async def test_propose_authz_fails_closed() -> None:
    _mock_status(["move.n1"])
    respx.get(AUTHZ).mock(side_effect=httpx.ConnectError("sidecar down"))
    out = json.loads(
        await ac._propose_action(_registry(), "xarm", "move.n1", None, "")
    )
    assert out["code"] == "not_authorized"


@respx.mock
async def test_propose_authz_disabled_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTROL_AUTHZ_ENFORCE", "false")
    _mock_status(["move.n1"])
    out = json.loads(
        await ac._propose_action(_registry(), "xarm", "move.n1", None, "")
    )
    assert "proposal" in out  # no authz round-trip needed


# ---------------------------------------------------------------------------
# list_available_actions
# ---------------------------------------------------------------------------


@respx.mock
async def test_list_available_actions_marks_proposable() -> None:
    _mock_status(["stop", "move.deck", "move.plateloc_out"])
    out = json.loads(await ac._list_available_actions(_registry(), "xarm"))
    by_action = {a["action"]: a for a in out["actions"]}
    assert by_action["stop"]["proposable"] is False
    assert by_action["move.deck"]["proposable"] is True
    assert by_action["move.deck"]["passthrough_action"] == "graph/move_to"
    assert "args_schema" in by_action["move.deck"]
    assert out["equipment_status"] == "ready"


@respx.mock
async def test_list_available_actions_unknown_equipment() -> None:
    out = json.loads(await ac._list_available_actions(_registry(), "nope"))
    assert out["code"] == "unknown_equipment"
