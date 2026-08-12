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


# ---------------------------------------------------------------------------
# liquid_handler (OT-2) — tier A + B proposals (UI_DESIGN §5 Step 1b)
# ---------------------------------------------------------------------------

OT2_BASE = "http://ot2.test:8020"

# Every OT-2 action the gateway can advertise, so the refusal tests exercise
# the real surface rather than a hand-picked subset.
_OT2_ADVERTISED = [
    "startup", "shutdown", "home", "setup", "pause", "resume",
    "move_to", "pick_up_tip", "aspirate", "dispense", "drop_tip", "move_labware",
    "plate.load", "plate.unload", "well.update", "tips.reset",
    "lights.set", "deck.declare",
]

# The scoping decision itself, pinned. Tier A moves the robot toward a safer or
# more legible state; tier B edits records without motion.
_TIER_AB = {
    "lights.set", "home", "pause",
    "plate.load", "plate.unload", "well.update", "deck.declare",
}


def _ot2_registry() -> Registry:
    return Registry(
        equipment=[
            EquipmentEntry(
                id="ot2_hte",
                name="Opentrons OT-2 HTE",
                kind="liquid_handler",
                adapter="http",
                base_url=OT2_BASE,
                status_path="/status",
                protocol="1.2",
            )
        ]
    )


def _mock_ot2_status(allowed_actions: list[str]) -> None:
    respx.get(f"{OT2_BASE}/status").mock(
        return_value=httpx.Response(
            200,
            json={
                "equipment_id": "ot2_hte",
                "equipment_name": "Opentrons OT-2 HTE",
                "equipment_kind": "liquid_handler",
                "equipment_status": "ready",
                "message": "idle",
                "allowed_actions": allowed_actions,
                "activity": "idle",
                "device_time": "2026-08-12T12:00:00Z",
            },
        )
    )


@pytest.mark.parametrize(
    ("action", "passthrough"),
    [
        ("lights.set", "lights"),
        ("home", "home"),
        ("pause", "pause"),
        ("plate.load", "plate/load"),
        ("plate.unload", "plate/unload"),
        ("well.update", "well/update"),
        ("deck.declare", "deck/declare"),
    ],
)
def test_resolve_ot2_tier_ab(action: str, passthrough: str) -> None:
    """Dotted catalog names resolve by direct lookup (no xArm-style bridging),
    and their endpoints map to the passthrough's URL segment."""

    entry = _ot2_registry().equipment[0]
    sd, resolved_passthrough, args = ac._resolve(entry, action, {})
    assert sd.name == action
    assert resolved_passthrough == passthrough
    assert args == {}


@pytest.mark.parametrize("action", sorted(set(_OT2_ADVERTISED) - _TIER_AB))
def test_resolve_ot2_refuses_operator_only(action: str) -> None:
    """Sequence-bound liquid verbs, ``setup``, ``startup``, ``resume`` and
    ``tips.reset`` stay operator-only. Parametrized over the advertised surface
    so a new gateway verb defaults to refused until scoped deliberately."""

    entry = _ot2_registry().equipment[0]
    with pytest.raises(ac.ProposalRefused) as exc:
        ac._resolve(entry, action, {})
    assert exc.value.code == "unmappable_action"


def test_proposable_names_are_all_cataloged() -> None:
    """Every allowlisted name must exist in the catalog for its kind — the
    resolver looks skills up by name, so a typo would refuse at runtime."""

    for kind, actions in ac._PROPOSABLE.items():
        for action in actions:
            assert ac._find_skill_def(kind, action) is not None, f"{kind}/{action}"


def test_proposable_liquid_handler_is_subset_of_gateway_surface() -> None:
    """Guards against allowlisting a name the OT-2 gateway never advertises,
    which would sit in the table looking supported and always refuse."""

    assert ac._PROPOSABLE["liquid_handler"] <= set(_OT2_ADVERTISED)


def test_no_proposable_action_exposes_a_guard_bypass() -> None:
    """The tier A/B line rests on no allowlisted schema carrying a field that
    weakens an interlock or a secret. If one ever does, the field-level guard
    must land first (see the ``_PROPOSABLE`` docstring)."""

    forbidden = {"force", "force_direct", "password"}
    for kind, actions in ac._PROPOSABLE.items():
        for action in actions:
            sd = ac._find_skill_def(kind, action)
            assert sd is not None
            overlap = forbidden & set(sd.args_schema.model_fields)
            assert not overlap, f"{kind}/{action} exposes {overlap}"


@respx.mock
async def test_propose_ot2_record_edit() -> None:
    _mock_ot2_status(_OT2_ADVERTISED)
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _ot2_registry(),
            "ot2_hte",
            "well.update",
            {"well": "A1", "volume_ul": 50.0, "sample_id": "stock-7"},
            "record the aliquot",
        )
    )
    prop = out["proposal"]
    assert prop["action"] == "well.update"
    assert prop["passthrough_action"] == "well/update"
    assert prop["args"]["well"] == "A1"
    assert prop["kind"] == "liquid_handler"


@respx.mock
async def test_propose_ot2_aspirate_refused() -> None:
    """Advertised by the device and cataloged, but not proposable: correctness
    lives in the whole pick-up-tip -> aspirate -> dispense -> drop-tip sequence,
    which one confirm click cannot bind."""

    _mock_ot2_status(_OT2_ADVERTISED)
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _ot2_registry(),
            "ot2_hte",
            "aspirate",
            {"pipette": "p300", "volume_ul": 50.0,
             "location": {"labware_nickname": "plate", "position": "A1"}},
            "",
        )
    )
    assert out["code"] == "unmappable_action"


@respx.mock
async def test_propose_ot2_bad_args_rejected() -> None:
    _mock_ot2_status(_OT2_ADVERTISED)
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _ot2_registry(), "ot2_hte", "lights.set", {"on": "maybe"}, ""
        )
    )
    assert out["code"] == "invalid_args"


@respx.mock
async def test_list_available_actions_ot2_splits_proposable() -> None:
    _mock_ot2_status(_OT2_ADVERTISED)
    out = json.loads(await ac._list_available_actions(_ot2_registry(), "ot2_hte"))
    by_action = {a["action"]: a for a in out["actions"]}
    assert by_action["home"]["proposable"] is True
    assert by_action["home"]["passthrough_action"] == "home"
    assert "args_schema" in by_action["well.update"]
    for operator_only in ("aspirate", "setup", "startup", "resume", "tips.reset"):
        assert by_action[operator_only]["proposable"] is False
