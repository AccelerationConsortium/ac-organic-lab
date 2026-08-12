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
# liquid_handler (OT-2) proposals — full surface + field guard
# (UI_DESIGN §5 Steps 1b/1c)
# ---------------------------------------------------------------------------

OT2_BASE = "http://ot2.test:8020"

# Every OT-2 action the gateway can advertise, mapped to the passthrough URL
# segment its proposal must carry. Step 1c scoped the full surface, so this is
# also the scoping decision, pinned: an entry leaving this map must be a
# deliberate re-scoping, and a new gateway verb is refused until added here
# AND to _PROPOSABLE.
_OT2_SURFACE = {
    "startup": "startup",
    "shutdown": "shutdown",
    "home": "home",
    "setup": "setup",
    "pause": "pause",
    "resume": "resume",
    "move_to": "move-to",
    "pick_up_tip": "pick-up-tip",
    "aspirate": "aspirate",
    "dispense": "dispense",
    "drop_tip": "drop-tip",
    "move_labware": "move-labware",
    "plate.load": "plate/load",
    "plate.unload": "plate/unload",
    "well.update": "well/update",
    "tips.reset": "tips/reset",
    "lights.set": "lights",
    "deck.declare": "deck/declare",
}
_OT2_ADVERTISED = list(_OT2_SURFACE)


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


@pytest.mark.parametrize(("action", "passthrough"), sorted(_OT2_SURFACE.items()))
def test_resolve_ot2_full_surface(action: str, passthrough: str) -> None:
    """Every advertised OT-2 action resolves by direct catalog-name lookup (no
    xArm-style bridging), with its endpoint mapped to the passthrough's URL
    segment (dotted names and hyphenated endpoints both covered)."""

    entry = _ot2_registry().equipment[0]
    sd, resolved_passthrough, args = ac._resolve(entry, action, {})
    assert sd.name == action
    assert resolved_passthrough == passthrough
    assert args == {}


def test_resolve_ot2_refuses_unscoped_verb() -> None:
    """``reconcile`` is the gateway's operator recovery hook — never in
    allowed_actions, never cataloged, and deliberately not in _PROPOSABLE. It
    stands in for any future gateway verb: refused until scoped."""

    entry = _ot2_registry().equipment[0]
    with pytest.raises(ac.ProposalRefused) as exc:
        ac._resolve(entry, "reconcile", {})
    assert exc.value.code == "unmappable_action"


@pytest.mark.parametrize(
    ("action", "args"),
    [
        ("startup", {"password": "hunter2"}),
        ("startup", {"simulation": True, "host_alias": "ot2-evil"}),
        ("pick_up_tip", {"pipette": "p300", "force": True}),
        # force=False is still refused: "never model-settable" means the field,
        # not the value — the invariant must not depend on reading a boolean.
        ("pick_up_tip", {"pipette": "p300", "force": False}),
        ("drop_tip", {"pipette": "p300", "force": True}),
        ("move_to", {"pipette": "p300", "force_direct": True}),
    ],
)
def test_resolve_refuses_forbidden_fields(action: str, args: dict) -> None:
    """The Step 1c field guard: interlock overrides and device credentials are
    never model-settable, whatever the value."""

    entry = _ot2_registry().equipment[0]
    with pytest.raises(ac.ProposalRefused) as exc:
        ac._resolve(entry, action, args)
    assert exc.value.code == "forbidden_field"


def test_resolve_startup_without_credentials_ok() -> None:
    """``startup`` is proposable exactly because the guard keeps credentials
    out: the gateway supplies its own from service env."""

    entry = _ot2_registry().equipment[0]
    sd, passthrough, args = ac._resolve(entry, "startup", {"simulation": True})
    assert sd.name == "startup"
    assert passthrough == "startup"
    assert args == {"simulation": True}


def test_proposable_names_are_all_cataloged() -> None:
    """Every allowlisted name must exist in the catalog for its kind — the
    resolver looks skills up by name, so a typo would refuse at runtime."""

    for kind, actions in ac._PROPOSABLE.items():
        for action in actions:
            assert ac._find_skill_def(kind, action) is not None, f"{kind}/{action}"


def test_proposable_liquid_handler_equals_gateway_surface() -> None:
    """Step 1c admitted the full advertised surface — pinned as equality so a
    silent narrowing or an allowlisted-but-never-advertised name both fail."""

    assert ac._PROPOSABLE["liquid_handler"] == set(_OT2_ADVERTISED)


def test_risky_schema_fields_are_all_guarded() -> None:
    """Step 1c's admission price: every interlock-override / credential field
    reachable through a proposable schema must be covered by the field guard.
    A new proposable action carrying such a field fails here until the guard
    knows it (see the ``_FORBIDDEN_ARG_FIELDS`` docstring)."""

    risky = {"force", "force_direct", "password", "host_alias"}
    for kind, actions in ac._PROPOSABLE.items():
        guarded = ac._FORBIDDEN_ARG_FIELDS.get(kind, frozenset())
        for action in actions:
            sd = ac._find_skill_def(kind, action)
            assert sd is not None
            exposed = risky & set(sd.args_schema.model_fields)
            assert exposed <= guarded, f"{kind}/{action} exposes unguarded {exposed - guarded}"
    # And the guard is doing real work, not vacuously satisfied.
    assert ac._FORBIDDEN_ARG_FIELDS["liquid_handler"] == risky


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
async def test_propose_ot2_aspirate() -> None:
    """Step 1c: liquid verbs are proposable one step at a time — the operator
    is the sequencer, authorizing consecutive cards."""

    _mock_ot2_status(_OT2_ADVERTISED)
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _ot2_registry(),
            "ot2_hte",
            "aspirate",
            {"pipette": "p300", "volume_ul": 50.0,
             "location": {"labware_nickname": "plate", "position": "A1"}},
            "step 2 of 4: draw 50 uL from the stock plate",
        )
    )
    prop = out["proposal"]
    assert prop["action"] == "aspirate"
    assert prop["passthrough_action"] == "aspirate"
    assert prop["args"]["volume_ul"] == 50.0


@respx.mock
async def test_propose_ot2_forbidden_field_refused_end_to_end() -> None:
    """The guard fires through _propose_action too, before authz — a
    credential never reaches the card or the audit row."""

    _mock_ot2_status(_OT2_ADVERTISED)
    out = json.loads(
        await ac._propose_action(
            _ot2_registry(), "ot2_hte", "startup", {"password": "hunter2"}, ""
        )
    )
    assert out["code"] == "forbidden_field"
    assert "hunter2" not in json.dumps(out)


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
async def test_list_available_actions_ot2_full_surface_and_stripped_schemas() -> None:
    _mock_ot2_status(_OT2_ADVERTISED)
    out = json.loads(await ac._list_available_actions(_ot2_registry(), "ot2_hte"))
    by_action = {a["action"]: a for a in out["actions"]}
    # Everything advertised is proposable (Step 1c), with the right passthrough.
    for action, passthrough in _OT2_SURFACE.items():
        assert by_action[action]["proposable"] is True, action
        assert by_action[action]["passthrough_action"] == passthrough
    # Operator-only fields are stripped from what the model is shown, and
    # reported by name so their absence is explainable.
    startup = by_action["startup"]
    assert startup["operator_only_fields"] == ["host_alias", "password"]
    assert "password" not in startup["args_schema"]["properties"]
    assert "host_alias" not in startup["args_schema"]["properties"]
    assert "simulation" in startup["args_schema"]["properties"]
    assert by_action["pick_up_tip"]["operator_only_fields"] == ["force"]
    assert "force" not in by_action["pick_up_tip"]["args_schema"]["properties"]
    assert by_action["move_to"]["operator_only_fields"] == ["force_direct"]
    # Actions with no risky fields carry no operator_only_fields key at all.
    assert "operator_only_fields" not in by_action["home"]
