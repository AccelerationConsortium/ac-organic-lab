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
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "equipment_id": "xarm",
        "equipment_name": "UFactory xArm5",
        "equipment_kind": "robot_arm",
        "equipment_status": equipment_status,
        "message": "ok",
        "allowed_actions": allowed_actions,
        "activity": activity,
        "device_time": "2026-08-11T12:00:00Z",
    }
    if details is not None:
        payload["details"] = details
    return payload


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
    sd, passthrough, args = ac._resolve(entry, "move.uplc_draw_home", {})
    assert sd.name == "graph.move_to"
    assert passthrough == "graph/move_to"
    assert args == {"node_id": "uplc_draw_home"}


def test_resolve_gripper_state() -> None:
    """``gripper.<state>`` bridges to graph.gripper with the state in the body,
    mirroring the ``move.<node_id>`` bridge."""
    entry = _registry().equipment[0]
    sd, passthrough, args = ac._resolve(entry, "gripper.grip_120", {})
    assert sd.name == "graph.gripper"
    assert passthrough == "graph/gripper"
    assert args == {"state": "grip_120"}


def test_resolve_travel_target() -> None:
    """``travel.<node_id>`` bridges to graph.travel_to with the destination in
    the body — the device plans the hop path itself (Step 1k)."""
    entry = _registry().equipment[0]
    sd, passthrough, args = ac._resolve(entry, "travel.deck_slot1_low", {})
    assert sd.name == "graph.travel_to"
    assert passthrough == "graph/travel_to"
    assert args == {"node_id": "deck_slot1_low"}


def test_resolve_refuses_malformed_travel_action() -> None:
    entry = _registry().equipment[0]
    with pytest.raises(ac.ProposalRefused) as exc:
        ac._resolve(entry, "travel.", {})
    assert exc.value.code == "unmappable_action"


def test_resolve_refuses_travel_on_other_kinds() -> None:
    entry = _registry(kind="plate_sealer").equipment[0]
    with pytest.raises(ac.ProposalRefused) as exc:
        ac._resolve(entry, "travel.deck_home", {})
    assert exc.value.code == "unmappable_action"


def test_resolve_refuses_malformed_gripper_action() -> None:
    entry = _registry().equipment[0]
    with pytest.raises(ac.ProposalRefused) as exc:
        ac._resolve(entry, "gripper.", {})
    assert exc.value.code == "unmappable_action"


def test_resolve_refuses_gripper_on_other_kinds() -> None:
    """The bridge is scoped to robot_arm; a like-named action on another kind
    must not slip through it."""
    entry = _registry(kind="plate_sealer").equipment[0]
    with pytest.raises(ac.ProposalRefused) as exc:
        ac._resolve(entry, "gripper.grip_120", {})
    assert exc.value.code == "unmappable_action"


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
    _mock_status(["stop", "move.uplc_draw_home"])
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _registry(), "xarm", "move.uplc_draw_home", None, "stage the plate"
        )
    )
    prop = out["proposal"]
    assert prop["equipment_id"] == "xarm"
    assert prop["action"] == "move.uplc_draw_home"
    assert prop["passthrough_action"] == "graph/move_to"
    assert prop["args"] == {"node_id": "uplc_draw_home"}
    assert prop["actor"] == ACTOR
    assert prop["reason"] == "stage the plate"
    assert prop["device_state"]["equipment_status"] == "ready"
    assert prop["expires_in_s"] == ac.PROPOSAL_TTL_S


@respx.mock
async def test_propose_gripper_success() -> None:
    _mock_status(["stop", "gripper.grip_120"])
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _registry(), "xarm", "gripper.grip_120", None, "grip the plate"
        )
    )
    prop = out["proposal"]
    assert prop["action"] == "gripper.grip_120"
    assert prop["passthrough_action"] == "graph/gripper"
    assert prop["args"] == {"state": "grip_120"}


@respx.mock
async def test_propose_travel_target_via_motion_graph() -> None:
    """travel.<node_id> is startable when the destination is in the device's
    motion_graph snapshot (reachable_nodes or travel_targets), even though the
    device never lists travel actions in allowed_actions (Step 1k)."""
    _mock_status(
        ["stop", "move.uplc_draw_home"], details={"motion_graph": _MOTION_GRAPH}
    )
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _registry(), "xarm", "travel.deck_home", None, "route to the deck"
        )
    )
    prop = out["proposal"]
    assert prop["action"] == "travel.deck_home"
    assert prop["passthrough_action"] == "graph/travel_to"
    assert prop["args"] == {"node_id": "deck_home"}


@respx.mock
async def test_propose_travel_refused_for_unreachable_node() -> None:
    """A destination outside the snapshot is refused with the valid travel
    targets in the payload — never sent on to earn the device's 409."""
    _mock_status(["stop"], details={"motion_graph": _MOTION_GRAPH})
    out = json.loads(
        await ac._propose_action(_registry(), "xarm", "travel.nowhere", None, "")
    )
    assert out["code"] == "not_allowed"
    assert out["travel_targets"] == sorted(
        {"uplc_draw_home", "uplc_draw_up", "deck_home"}
    )


@respx.mock
async def test_propose_travel_refused_without_motion_graph() -> None:
    """No snapshot -> no travel surface (fail closed; an arm with no graph
    loaded cannot route anywhere)."""
    _mock_status(["stop", "move.deck"])
    out = json.loads(
        await ac._propose_action(_registry(), "xarm", "travel.deck_home", None, "")
    )
    assert out["code"] == "not_allowed"


@respx.mock
async def test_propose_gripper_state_the_device_withholds() -> None:
    """The device enumerates only transitions whitelisted for its current
    (node, gripper state). A state it withholds is refused here rather than
    sent on to earn a 409 — this is the §6.2 mirror doing its job, and it is
    the whole reason the state rides in the action name."""
    _mock_status(["stop", "gripper.empty"])
    out = json.loads(
        await ac._propose_action(
            _registry(), "xarm", "gripper.grip_120", None, "grip the plate"
        )
    )
    assert out["code"] == "not_allowed"
    assert out["allowed_actions"] == ["stop", "gripper.empty"]


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
    _mock_status(["stop", "move.deck", "move.uplc_draw_home"])
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


# The shape the xArm publishes under details.motion_graph
# (status_builder.py::_build_motion_graph_details). travel_targets is the
# multi-hop superset of reachable_nodes — the path context the assistant
# reasons with, deliberately wider than what is proposable.
_MOTION_GRAPH = {
    "current_node": "robot_home",
    "reachable_nodes": ["uplc_draw_home"],
    "travel_targets": ["uplc_draw_home", "uplc_draw_up", "deck_home"],
    "graph_mode": "strict",
    "gripper_stroke": 850.0,
    "gripper_state": "open",
    "allowed_gripper_targets": ["closed"],
    "arm_pose_name": "robot_home",
    "rail_location_name": "home",
}


@respx.mock
async def test_list_available_actions_forwards_motion_graph() -> None:
    """details.motion_graph is forwarded verbatim as read-only path context,
    and (Step 1k) every node in it gains a synthesized travel.<node_id> entry
    bridging to the device's own multi-hop planner (graph.travel_to) — the
    model proposes a destination, never a guessed hop sequence."""

    _mock_status(
        ["stop", "move.uplc_draw_home"], details={"motion_graph": _MOTION_GRAPH}
    )
    out = json.loads(await ac._list_available_actions(_registry(), "xarm"))
    assert out["motion_graph"] == _MOTION_GRAPH
    by_action = {a["action"]: a for a in out["actions"]}
    assert set(by_action) == {
        "stop",
        "move.uplc_draw_home",
        "travel.uplc_draw_home",
        "travel.uplc_draw_up",
        "travel.deck_home",
    }
    travel = by_action["travel.deck_home"]
    assert travel["proposable"] is True
    assert travel["passthrough_action"] == "graph/travel_to"
    assert travel["synthesized_from"] == "motion_graph"
    assert "args_schema" in travel


@respx.mock
async def test_list_available_actions_no_travel_entries_without_graph() -> None:
    """No motion_graph snapshot -> no synthesized travel surface (an arm with
    no graph loaded must not advertise unroutable destinations)."""

    _mock_status(["stop", "move.deck"])
    out = json.loads(await ac._list_available_actions(_registry(), "xarm"))
    assert not any(a["action"].startswith("travel.") for a in out["actions"])


@respx.mock
async def test_list_available_actions_no_motion_graph_key_when_absent() -> None:
    """Devices that publish no graph snapshot (non-arm kinds; an arm with no
    graph loaded) simply lack the key — never an empty placeholder."""

    _mock_status(["stop", "move.deck"])
    out = json.loads(await ac._list_available_actions(_registry(), "xarm"))
    assert "motion_graph" not in out


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
    "tips.mark": "tips/mark",
    "tempmod.set": "tempmod/set",
    "tempmod.deactivate": "tempmod/deactivate",
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
async def test_propose_ot2_tips_mark() -> None:
    """``tips.mark`` is the partial-rack tip-tracker repair. It used to refuse
    as operator-only (it was advertised by the gateway but scoped in neither
    _PROPOSABLE nor the catalog), which left the assistant recommending
    ``tips.reset`` — an over-claim of a full rack — as the only reachable fix.
    """

    _mock_ot2_status(_OT2_ADVERTISED)
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _ot2_registry(),
            "ot2_hte",
            "tips.mark",
            {"slot": "5", "wells": ["A1", "B1", "C1"], "status": "empty"},
            "three tips were taken by hand; sync the tracker",
        )
    )
    assert "code" not in out, out
    prop = out["proposal"]
    assert prop["action"] == "tips.mark"
    assert prop["passthrough_action"] == "tips/mark"
    assert prop["kind"] == "liquid_handler"
    # The card renders generically over args, so the payload must carry them all.
    assert prop["args"] == {"slot": "5", "wells": ["A1", "B1", "C1"], "status": "empty"}


@respx.mock
async def test_propose_ot2_tips_mark_by_column() -> None:
    """The column form round-trips too — the resolver passes args through, so
    both TipsMarkArgs shapes must reach the card."""

    _mock_ot2_status(_OT2_ADVERTISED)
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _ot2_registry(),
            "ot2_hte",
            "tips.mark",
            {"nickname": "tiprack_300", "columns": [1, 2], "status": "new"},
            "restocked the first two columns",
        )
    )
    prop = out["proposal"]
    assert prop["args"] == {
        "nickname": "tiprack_300",
        "columns": [1, 2],
        "status": "new",
    }


@respx.mock
async def test_propose_ot2_tips_mark_bad_args_rejected() -> None:
    """Both-wells-and-columns is a TipsMarkArgs validator error, so it never
    reaches a confirm card."""

    _mock_ot2_status(_OT2_ADVERTISED)
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _ot2_registry(),
            "ot2_hte",
            "tips.mark",
            {"slot": "5", "wells": ["A1"], "columns": [1], "status": "new"},
            "",
        )
    )
    assert out["code"] == "invalid_args"


@respx.mock
async def test_propose_ot2_tempmod_set() -> None:
    """``tempmod.set`` was silently unproposable for the same reason —
    advertised by the gateway, absent from both gates."""

    _mock_ot2_status(_OT2_ADVERTISED)
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _ot2_registry(), "ot2_hte", "tempmod.set", {"celsius": 37.0}, "warm the block"
        )
    )
    prop = out["proposal"]
    assert prop["action"] == "tempmod.set"
    assert prop["passthrough_action"] == "tempmod/set"
    assert prop["args"] == {"celsius": 37.0}


@respx.mock
async def test_propose_ot2_tempmod_out_of_range_rejected() -> None:
    _mock_ot2_status(_OT2_ADVERTISED)
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _ot2_registry(), "ot2_hte", "tempmod.set", {"celsius": 120.0}, ""
        )
    )
    assert out["code"] == "invalid_args"


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


# ---------------------------------------------------------------------------
# fume hood / shaker / press proposals (UI_DESIGN §5 Step 1d) — HPLC excluded
# ---------------------------------------------------------------------------

# Proposable surface per bench kind, mapped to the passthrough URL segment.
# Names are byte-for-byte what the devices advertise (verified live
# 2026-08-12). Note press `init` maps to the /control/startup endpoint.
_BENCH_SURFACES: dict[str, dict[str, str]] = {
    "fume_hood": {"sash.move": "sash/move"},
    "shaker": {
        "startup": "startup",
        "shutdown": "shutdown",
        "shake.start": "shake/start",
        "shake.set_temperature": "shake/set_temperature",
        "shake.set_speed": "shake/set_speed",
    },
    "press": {
        "init": "startup",
        "press.up": "press/up",
        "press.down": "press/down",
        "plate.in": "plate/in",
        "plate.out": "plate/out",
    },
}

# Stop verbs are the safety floor on every kind — never proposable.
_STOP_VERBS = {
    "fume_hood": "sash.stop",
    "shaker": "shake.stop",
    "press": "stop",
    "plate_sealer": "seal.stop",
}

BENCH_BASE = "http://bench.test:9000"


def _bench_registry(kind: str) -> Registry:
    return Registry(
        equipment=[
            EquipmentEntry(
                id=f"{kind}_test",
                name=f"{kind} under test",
                kind=kind,
                adapter="http",
                base_url=BENCH_BASE,
                status_path="/status",
                protocol="1.1",
            )
        ]
    )


def _mock_bench_status(kind: str, allowed_actions: list[str]) -> None:
    respx.get(f"{BENCH_BASE}/status").mock(
        return_value=httpx.Response(
            200,
            json={
                "equipment_id": f"{kind}_test",
                "equipment_name": f"{kind} under test",
                "equipment_kind": kind,
                "equipment_status": "ready",
                "message": "idle",
                "allowed_actions": allowed_actions,
                "device_time": "2026-08-12T12:00:00Z",
            },
        )
    )


@pytest.mark.parametrize(
    ("kind", "action", "passthrough"),
    [
        (kind, action, passthrough)
        for kind, surface in sorted(_BENCH_SURFACES.items())
        for action, passthrough in sorted(surface.items())
    ],
)
def test_resolve_bench_surfaces(kind: str, action: str, passthrough: str) -> None:
    """Every Step 1d action resolves by direct catalog-name lookup with its
    endpoint mapped to the passthrough URL segment."""

    entry = _bench_registry(kind).equipment[0]
    sd, resolved_passthrough, args = ac._resolve(entry, action, {})
    assert sd.name == action
    assert resolved_passthrough == passthrough
    assert args == {}


@pytest.mark.parametrize(
    ("kind", "passthrough", "catalog"),
    [
        (kind, passthrough, catalog)
        for kind, surface in sorted(_BENCH_SURFACES.items())
        for catalog, passthrough in sorted(surface.items())
        if passthrough != catalog
    ],
)
def test_resolve_passthrough_alias(
    kind: str, passthrough: str, catalog: str
) -> None:
    """Models often pass ``passthrough_action`` (``press/up``) instead of the
    advertised name (``press.up``). That used to fail ``allowed_actions``
    membership and never raise a confirm card."""

    entry = _bench_registry(kind).equipment[0]
    sd, resolved_passthrough, args = ac._resolve(entry, passthrough, {})
    assert sd.name == catalog
    assert resolved_passthrough == passthrough
    assert args == {}


@pytest.mark.parametrize(("kind", "action"), sorted(_STOP_VERBS.items()))
def test_resolve_refuses_stop_verbs(kind: str, action: str) -> None:
    """The safety floor generalized: stop verbs stay operator-only on every
    kind, exactly like the xArm's stop / connect / clear_errors."""

    entry = _bench_registry(kind).equipment[0]
    with pytest.raises(ac.ProposalRefused) as exc:
        ac._resolve(entry, action, {})
    assert exc.value.code == "unmappable_action"


@pytest.mark.parametrize(
    "action",
    ["run.submit", "run.abort", "queue.cancel", "instrument.standby",
     "workflow.start", "workflow.end"],
)
def test_resolve_refuses_all_hplc_actions(action: str) -> None:
    """The hplc kind is deliberately absent from _PROPOSABLE (operator
    decision, 2026-08-12): every advertised verb is refused."""

    entry = _bench_registry("hplc").equipment[0]
    with pytest.raises(ac.ProposalRefused) as exc:
        ac._resolve(entry, action, {})
    assert exc.value.code == "unmappable_action"


@respx.mock
@pytest.mark.parametrize("action", ["press.up", "press/up"])
async def test_propose_press_up_accepts_slash_alias(action: str) -> None:
    """A Control-mode 'press up' must produce a proposal whether the model
    used the advertised name or the URL segment list_available_actions also
    returns. Live filter_every_well advertises press.up even when already UP."""

    _mock_bench_status(
        "press", ["stop", "press.up", "press.down", "plate.in", "plate.out"]
    )
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _bench_registry("press"),
            "press_test",
            action,
            {"hold_time": 2.0},
            "raise the platen",
        )
    )
    prop = out["proposal"]
    assert "error" not in out
    assert prop["action"] == "press.up"
    assert prop["passthrough_action"] == "press/up"
    assert prop["args"] == {"hold_time": 2.0}


@respx.mock
async def test_propose_fume_hood_sash_move() -> None:
    _mock_bench_status("fume_hood", ["sash.move", "sash.stop"])
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _bench_registry("fume_hood"),
            "fume_hood_test",
            "sash.move",
            {"position": 3},
            "open the sash halfway for the transfer",
        )
    )
    prop = out["proposal"]
    assert prop["action"] == "sash.move"
    assert prop["passthrough_action"] == "sash/move"
    assert prop["args"] == {"position": 3}


@respx.mock
async def test_propose_shake_start_with_cycle_args() -> None:
    _mock_bench_status("shaker", ["shake.start", "shake.stop"])
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _bench_registry("shaker"),
            "shaker_test",
            "shake.start",
            {"speed_level": 5, "temperature_c": 25.0, "duration_s": 30.0},
            "30 s mix at level 5",
        )
    )
    prop = out["proposal"]
    assert prop["action"] == "shake.start"
    assert prop["passthrough_action"] == "shake/start"
    assert prop["args"]["duration_s"] == 30.0


@respx.mock
async def test_propose_bench_bad_args_rejected() -> None:
    # position is clamped 1..5 by the MoveArgs schema.
    _mock_bench_status("fume_hood", ["sash.move"])
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _bench_registry("fume_hood"), "fume_hood_test", "sash.move",
            {"position": 9}, ""
        )
    )
    assert out["code"] == "invalid_args"


# ---------------------------------------------------------------------------
# camera proposals (UI_DESIGN §5 Step 1f) — PTZ nudge, presets, privacy,
# streaming. Unlike every bench kind above, camera is in EQUIP_GUIDE.md's
# UNGATED_KINDS (convenience; cannot damage hardware), but a confirm card is
# still required — the assistant proposes, it never actuates.
# ---------------------------------------------------------------------------

CAMERA_BASE = "http://camera.test:8002"

# Byte-for-byte what kasa_tapo_services routes/cameras.py advertises, mapped
# to the passthrough URL segment. preset/{id} (delete) is deliberately absent
# — it is a literal template in allowed_actions, not a resolvable id.
_CAMERA_SURFACE = {
    "ptz": "ptz",
    "preset/save": "preset/save",
    "preset/goto": "preset/goto",
    "privacy": "privacy",
    "streaming": "streaming",
}


def _camera_registry() -> Registry:
    return Registry(
        equipment=[
            EquipmentEntry(
                id="cam_lab499_west",
                name="Lab 499 (West) Camera",
                kind="camera",
                adapter="http",
                base_url=CAMERA_BASE,
                status_path="/status",
                protocol="1.0",
            )
        ]
    )


def _mock_camera_status(allowed_actions: list[str]) -> None:
    respx.get(f"{CAMERA_BASE}/status").mock(
        return_value=httpx.Response(
            200,
            json={
                "equipment_id": "cam_lab499_west",
                "equipment_name": "Lab 499 (West) Camera",
                "equipment_kind": "camera",
                "equipment_status": "ready",
                "message": "ok",
                "allowed_actions": allowed_actions,
                "device_time": "2026-08-18T12:00:00Z",
            },
        )
    )


@pytest.mark.parametrize(("action", "passthrough"), sorted(_CAMERA_SURFACE.items()))
def test_resolve_camera_surface(action: str, passthrough: str) -> None:
    """Every proposable camera action resolves by direct catalog-name lookup —
    no xArm-style bridging needed even though names are slash-separated."""

    entry = _camera_registry().equipment[0]
    sd, resolved_passthrough, args = ac._resolve(entry, action, {})
    assert sd.name == action
    assert resolved_passthrough == passthrough
    assert args == {}


def test_resolve_refuses_camera_preset_delete_template() -> None:
    """``preset/{id}`` is the device's literal template string for "you may
    DELETE any preset", not a resolvable action name — never proposable."""

    entry = _camera_registry().equipment[0]
    with pytest.raises(ac.ProposalRefused) as exc:
        ac._resolve(entry, "preset/{id}", {})
    assert exc.value.code == "unmappable_action"


@respx.mock
async def test_propose_camera_ptz_nudge() -> None:
    _mock_camera_status(list(_CAMERA_SURFACE))
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _camera_registry(),
            "cam_lab499_west",
            "ptz",
            {"direction": "left", "speed": 0.5, "duration_ms": 400},
            "pan left to see the deck",
        )
    )
    prop = out["proposal"]
    assert prop["action"] == "ptz"
    assert prop["passthrough_action"] == "ptz"
    assert prop["args"] == {"direction": "left", "speed": 0.5, "duration_ms": 400}
    assert prop["kind"] == "camera"


@respx.mock
async def test_propose_camera_preset_goto() -> None:
    _mock_camera_status(list(_CAMERA_SURFACE))
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _camera_registry(),
            "cam_lab499_west",
            "preset/goto",
            {"preset_id": "1"},
            "return to the bench view",
        )
    )
    prop = out["proposal"]
    assert prop["action"] == "preset/goto"
    assert prop["passthrough_action"] == "preset/goto"
    assert prop["args"] == {"preset_id": "1"}


@respx.mock
async def test_propose_camera_privacy_toggle() -> None:
    _mock_camera_status(list(_CAMERA_SURFACE))
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _camera_registry(), "cam_lab499_west", "privacy", {"enabled": True}, ""
        )
    )
    assert out["proposal"]["passthrough_action"] == "privacy"


@respx.mock
async def test_propose_camera_bad_ptz_args_rejected() -> None:
    # speed must be within 0..1.
    _mock_camera_status(list(_CAMERA_SURFACE))
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _camera_registry(), "cam_lab499_west", "ptz",
            {"direction": "left", "speed": 5.0}, ""
        )
    )
    assert out["code"] == "invalid_args"


@respx.mock
async def test_propose_camera_not_advertised_is_refused() -> None:
    """A fixed-lens camera (no ONVIF PTZ service) never advertises ptz/preset
    actions (STATUS_SPEC §6.2) — proposing one anyway is refused as
    not_allowed, not sent on to earn a 412/404 from the gateway."""

    _mock_camera_status(["privacy", "streaming"])
    out = json.loads(
        await ac._propose_action(
            _camera_registry(), "cam_lab499_west", "ptz",
            {"direction": "left"}, ""
        )
    )
    assert out["code"] == "not_allowed"
    assert out["allowed_actions"] == ["privacy", "streaming"]


def test_proposable_camera_equals_gateway_surface_minus_delete_template() -> None:
    """Step 1f admitted the full advertised surface except the unresolvable
    preset/{id} delete template — pinned so a silent narrowing or widening
    both fail."""

    assert ac._PROPOSABLE["camera"] == set(_CAMERA_SURFACE)


@respx.mock
async def test_list_available_actions_camera_full_surface() -> None:
    _mock_camera_status(list(_CAMERA_SURFACE) + ["preset/{id}"])
    out = json.loads(await ac._list_available_actions(_camera_registry(), "cam_lab499_west"))
    by_action = {a["action"]: a for a in out["actions"]}
    for action, passthrough in _CAMERA_SURFACE.items():
        assert by_action[action]["proposable"] is True, action
        assert by_action[action]["passthrough_action"] == passthrough
    # The delete template is listed (it's genuinely in allowed_actions) but
    # not proposable — nothing here can resolve it to a concrete preset id.
    assert by_action["preset/{id}"]["proposable"] is False


# ---------------------------------------------------------------------------
# plate-reader proposals (UI_DESIGN §5 Step 1g)
# ---------------------------------------------------------------------------

# Finite, card-evaluable Cytation actions. incubator.set_temperature is a
# setpoint change (same class as shake.set_temperature). incubator.stop and
# Cytation shake.start/stop stay operator/workflow-only: stop verbs are the
# safety floor, and Cytation shaking has no duration timer.
_PLATE_READER_SURFACE = {
    "startup": "startup",
    "shutdown": "shutdown",
    "drawer.open": "drawer/open",
    "drawer.close": "drawer/close",
    "plate.load": "plate/load",
    "plate.unload": "plate/unload",
    "well.update": "well/update",
    "read.absorbance": "read/absorbance",
    "read.fluorescence": "read/fluorescence",
    "read.luminescence": "read/luminescence",
    "imaging.capture": "imaging/capture",
    "incubator.set_temperature": "incubator/set_temperature",
}
_PLATE_READER_WORKFLOW_ONLY = {
    "incubator.stop",
    "shake.start",
    "shake.stop",
}


@pytest.mark.parametrize(
    ("action", "passthrough"), sorted(_PLATE_READER_SURFACE.items())
)
def test_resolve_plate_reader_finite_surface(
    action: str, passthrough: str
) -> None:
    entry = _bench_registry("plate_reader").equipment[0]
    sd, resolved_passthrough, args = ac._resolve(entry, action, {})
    assert sd.name == action
    assert resolved_passthrough == passthrough
    assert args == {}


@pytest.mark.parametrize("action", sorted(_PLATE_READER_WORKFLOW_ONLY))
def test_resolve_plate_reader_refuses_persistent_and_stop_actions(
    action: str,
) -> None:
    entry = _bench_registry("plate_reader").equipment[0]
    with pytest.raises(ac.ProposalRefused) as exc:
        ac._resolve(entry, action, {})
    assert exc.value.code == "unmappable_action"


def test_proposable_plate_reader_equals_finite_surface() -> None:
    assert ac._PROPOSABLE["plate_reader"] == set(_PLATE_READER_SURFACE)


@respx.mock
async def test_propose_plate_reader_absorbance_read() -> None:
    _mock_bench_status("plate_reader", list(_PLATE_READER_SURFACE))
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _bench_registry("plate_reader"),
            "plate_reader_test",
            "read.absorbance",
            {"wells": ["A1", "B2"], "wavelength_nm": 600.0},
            "measure the loaded diagnostic plate",
        )
    )
    prop = out["proposal"]
    assert prop["action"] == "read.absorbance"
    assert prop["passthrough_action"] == "read/absorbance"
    assert prop["args"] == {"wells": ["A1", "B2"], "wavelength_nm": 600.0}
    assert prop["kind"] == "plate_reader"


@respx.mock
async def test_propose_plate_reader_incubator_set_temperature() -> None:
    _mock_bench_status("plate_reader", ["incubator.set_temperature"])
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _bench_registry("plate_reader"),
            "plate_reader_test",
            "incubator.set_temperature",
            {"celsius": 37.0},
            "warm the plate to 37 C",
        )
    )
    prop = out["proposal"]
    assert prop["action"] == "incubator.set_temperature"
    assert prop["passthrough_action"] == "incubator/set_temperature"
    assert prop["args"] == {"celsius": 37.0}


@respx.mock
async def test_propose_plate_reader_incubator_rejects_out_of_range() -> None:
    _mock_bench_status("plate_reader", ["incubator.set_temperature"])
    out = json.loads(
        await ac._propose_action(
            _bench_registry("plate_reader"),
            "plate_reader_test",
            "incubator.set_temperature",
            {"celsius": 90.0},
            "",
        )
    )
    assert out["code"] == "invalid_args"


@respx.mock
async def test_propose_plate_reader_rejects_removed_read_gain() -> None:
    _mock_bench_status("plate_reader", ["read.absorbance"])
    out = json.loads(
        await ac._propose_action(
            _bench_registry("plate_reader"),
            "plate_reader_test",
            "read.absorbance",
            {"wells": ["A1"], "wavelength_nm": 600.0, "gain": 10.0},
            "",
        )
    )
    assert out["code"] == "invalid_args"


@respx.mock
async def test_list_available_actions_plate_reader_marks_workflow_only() -> None:
    advertised = list(_PLATE_READER_SURFACE) + list(_PLATE_READER_WORKFLOW_ONLY)
    _mock_bench_status("plate_reader", advertised)
    out = json.loads(
        await ac._list_available_actions(
            _bench_registry("plate_reader"), "plate_reader_test"
        )
    )
    by_action = {a["action"]: a for a in out["actions"]}
    for action, passthrough in _PLATE_READER_SURFACE.items():
        assert by_action[action]["proposable"] is True, action
        assert by_action[action]["passthrough_action"] == passthrough
    for action in _PLATE_READER_WORKFLOW_ONLY:
        assert by_action[action]["proposable"] is False, action


# ---------------------------------------------------------------------------
# plate-sealer proposals (UI_DESIGN §5 Step 1h)
# ---------------------------------------------------------------------------

# Finite PlateLoc actions. seal.stop is the safety floor (never proposable);
# the device withholds seal.start until heater-in-band and stage-in, so a
# proposal for it is only legal when advertised.
_PLATE_SEALER_SURFACE = {
    "startup": "startup",
    "shutdown": "shutdown",
    "seal.start": "seal/start",
    "seal.set_temperature": "seal/temperature",
    "seal.set_time": "seal/time",
    "stage.in": "stage/in",
    "stage.out": "stage/out",
}


@pytest.mark.parametrize(
    ("action", "passthrough"), sorted(_PLATE_SEALER_SURFACE.items())
)
def test_resolve_plate_sealer_finite_surface(
    action: str, passthrough: str
) -> None:
    entry = _bench_registry("plate_sealer").equipment[0]
    sd, resolved_passthrough, args = ac._resolve(entry, action, {})
    assert sd.name == action
    assert resolved_passthrough == passthrough
    assert args == {}


def test_proposable_plate_sealer_equals_finite_surface() -> None:
    assert ac._PROPOSABLE["plate_sealer"] == set(_PLATE_SEALER_SURFACE)


@respx.mock
async def test_propose_plate_sealer_seal_start() -> None:
    _mock_bench_status("plate_sealer", list(_PLATE_SEALER_SURFACE))
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _bench_registry("plate_sealer"),
            "plate_sealer_test",
            "seal.start",
            {"temperature_c": 170, "seconds": 3.0},
            "seal the loaded plate",
        )
    )
    prop = out["proposal"]
    assert prop["action"] == "seal.start"
    assert prop["passthrough_action"] == "seal/start"
    assert prop["args"] == {"temperature_c": 170, "seconds": 3.0}
    assert prop["kind"] == "plate_sealer"


@respx.mock
async def test_propose_plate_sealer_rejects_out_of_range_setpoint() -> None:
    _mock_bench_status("plate_sealer", ["seal.set_temperature"])
    out = json.loads(
        await ac._propose_action(
            _bench_registry("plate_sealer"),
            "plate_sealer_test",
            "seal.set_temperature",
            {"temperature_c": 10},
            "",
        )
    )
    assert out["code"] == "invalid_args"


@respx.mock
async def test_list_available_actions_plate_sealer_marks_stop_operator_only() -> None:
    advertised = list(_PLATE_SEALER_SURFACE) + ["seal.stop"]
    _mock_bench_status("plate_sealer", advertised)
    out = json.loads(
        await ac._list_available_actions(
            _bench_registry("plate_sealer"), "plate_sealer_test"
        )
    )
    by_action = {a["action"]: a for a in out["actions"]}
    for action, passthrough in _PLATE_SEALER_SURFACE.items():
        assert by_action[action]["proposable"] is True, action
        assert by_action[action]["passthrough_action"] == passthrough
    assert by_action["seal.stop"]["proposable"] is False


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


# ---------------------------------------------------------------------------
# Step 1i — multi-step plans on one device (propose_plan)
# ---------------------------------------------------------------------------


def test_plan_step_hash_is_canonical() -> None:
    a = [{"action": "move.a", "args": {"node_id": "a", "speed": 1}}]
    b = [{"action": "move.a", "args": {"speed": 1, "node_id": "a"}}]
    c = [{"action": "move.a", "args": {"node_id": "a", "speed": 2}}]
    assert ac.plan_step_hash(a) == ac.plan_step_hash(b)
    assert ac.plan_step_hash(a) != ac.plan_step_hash(c)
    assert ac.plan_step_hash([{"action": "home"}]) == ac.plan_step_hash(
        [{"action": "home", "args": {}}]
    )


@respx.mock
async def test_propose_plan_holds_only_the_first_step_to_live_allowed_actions() -> None:
    """A route: hop 2 is only legal once hop 1 has run, so the live list cannot
    vouch for it — the device re-checks each hop as it is sent."""

    _mock_status(["stop", "move.uplc_draw_home"])
    _mock_authz(True)
    out = json.loads(
        await ac._propose_plan(
            _registry(),
            "xarm",
            [{"action": "move.uplc_draw_home"}, {"action": "move.reader_in", "args": {}}],
            "route to the reader",
        )
    )
    plan = out["plan"]
    assert plan["plan_id"]
    assert plan["equipment_id"] == "xarm"
    assert [s["action"] for s in plan["steps"]] == ["move.uplc_draw_home", "move.reader_in"]
    assert all(s["passthrough_action"] == "graph/move_to" for s in plan["steps"])
    assert plan["steps"][1]["args"] == {"node_id": "reader_in"}
    assert plan["step_hash"] == ac.plan_step_hash(plan["steps"])
    assert plan["actor"] == ACTOR
    assert plan["reason"] == "route to the reader"
    assert plan["expires_in_s"] == ac.PLAN_TTL_S
    assert plan["device_state"]["equipment_status"] == "ready"


@respx.mock
async def test_propose_plan_travel_pick_and_place() -> None:
    """The Step 1k pick/place shape: travel -> gripper -> travel. Step 1 is
    vouched for by the motion_graph snapshot (travel is never in
    allowed_actions); later steps are the device's to re-check live."""

    _mock_status(
        ["stop", "move.uplc_draw_home"], details={"motion_graph": _MOTION_GRAPH}
    )
    _mock_authz(True)
    out = json.loads(
        await ac._propose_plan(
            _registry(),
            "xarm",
            [
                {"action": "travel.deck_home"},
                {"action": "gripper.grip_120", "args": {}},
                {"action": "travel.uplc_draw_up"},
            ],
            "pick the plate and stage it",
        )
    )
    plan = out["plan"]
    assert [s["action"] for s in plan["steps"]] == [
        "travel.deck_home",
        "gripper.grip_120",
        "travel.uplc_draw_up",
    ]
    assert plan["steps"][0]["passthrough_action"] == "graph/travel_to"
    assert plan["steps"][0]["args"] == {"node_id": "deck_home"}
    assert plan["steps"][2]["passthrough_action"] == "graph/travel_to"


@respx.mock
async def test_propose_plan_refuses_travel_step_one_outside_snapshot() -> None:
    """The step-1 gate applies to travel too: a destination the snapshot does
    not vouch for refuses the plan up front instead of dying mid-route."""

    _mock_status(["stop"], details={"motion_graph": _MOTION_GRAPH})
    _mock_authz(True)
    out = json.loads(
        await ac._propose_plan(
            _registry(), "xarm", [{"action": "travel.nowhere"}], ""
        )
    )
    assert out["code"] == "not_allowed"
    assert out["step"] == 1


@respx.mock
async def test_propose_plan_refuses_when_step_one_cannot_start() -> None:
    _mock_status(["stop", "move.uplc_draw_home"])
    _mock_authz(True)
    out = json.loads(
        await ac._propose_plan(
            _registry(), "xarm", [{"action": "move.reader_in"}, {"action": "move.uplc_draw_home"}], ""
        )
    )
    assert out["code"] == "not_allowed"
    assert out["step"] == 1
    assert "move.uplc_draw_home" in out["allowed_actions"]


@respx.mock
async def test_propose_plan_names_the_failing_later_step() -> None:
    """Every step is resolved and schema-checked at proposal time, and a
    refusal says which one — here a safety-floor verb smuggled in as step 2."""

    _mock_status(["stop", "move.uplc_draw_home"])
    _mock_authz(True)
    out = json.loads(
        await ac._propose_plan(
            _registry(), "xarm", [{"action": "move.uplc_draw_home"}, {"action": "stop"}], ""
        )
    )
    assert out["code"] == "unmappable_action"
    assert out["step"] == 2
    assert out["error"].startswith("step 2 (stop)")


@respx.mock
async def test_propose_plan_refuses_forbidden_field_by_step() -> None:
    _mock_ot2_status(_OT2_ADVERTISED)
    _mock_authz(True)
    out = json.loads(
        await ac._propose_plan(
            _ot2_registry(),
            "ot2_hte",
            [
                {"action": "home"},
                {"action": "pick_up_tip", "args": {"pipette": "p300", "force": 1.5}},
            ],
            "",
        )
    )
    assert out["code"] == "forbidden_field"
    assert out["step"] == 2


@respx.mock
async def test_propose_plan_shape_and_size_limits() -> None:
    _mock_status(["stop", "move.uplc_draw_home"])
    _mock_authz(True)
    assert json.loads(await ac._propose_plan(_registry(), "xarm", [], ""))["code"] == "empty_plan"
    assert json.loads(await ac._propose_plan(_registry(), "xarm", None, ""))["code"] == "empty_plan"
    too_many = [{"action": "move.uplc_draw_home"}] * (ac.MAX_PLAN_STEPS + 1)
    assert (
        json.loads(await ac._propose_plan(_registry(), "xarm", too_many, ""))["code"]
        == "too_many_steps"
    )
    out = json.loads(
        await ac._propose_plan(_registry(), "xarm", [{"action": "move.uplc_draw_home"}, "stop"], "")
    )
    assert out["code"] == "invalid_step"
    assert out["step"] == 2


@respx.mock
async def test_propose_plan_not_authorized_and_no_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_status(["stop", "move.uplc_draw_home"])
    _mock_authz(False)
    out = json.loads(
        await ac._propose_plan(_registry(), "xarm", [{"action": "move.uplc_draw_home"}], "")
    )
    assert out["code"] == "not_authorized"
    monkeypatch.delenv("LAB_ACTOR")
    out = json.loads(
        await ac._propose_plan(_registry(), "xarm", [{"action": "move.uplc_draw_home"}], "")
    )
    assert out["code"] == "no_actor"


# ---------------------------------------------------------------------------
# Step 1j: decline_proposal (the no-proposal terminal call)
# ---------------------------------------------------------------------------


def test_decline_proposal_returns_declined_payload() -> None:
    out = json.loads(ac._decline_proposal("safety_floor", "stop verbs are operator-only"))
    assert out == {
        "declined": {
            "reason_code": "safety_floor",
            "explanation": "stop verbs are operator-only",
        }
    }


def test_decline_proposal_coerces_unknown_reason_to_other() -> None:
    # The tool exists to TERMINATE a turn; a bad enum must not fail it.
    out = json.loads(ac._decline_proposal("bogus_code", "reasoning here"))
    assert out["declined"]["reason_code"] == "other"


def test_decline_proposal_requires_an_explanation() -> None:
    out = json.loads(ac._decline_proposal("informational", "   "))
    assert out["code"] == "empty_explanation"


def test_decline_proposal_flattens_whitespace() -> None:
    out = json.loads(ac._decline_proposal("informational", "no\naction\n  asked"))
    assert out["declined"]["explanation"] == "no action asked"


def test_refusal_codes_cover_every_propose_path_err_code() -> None:
    """Parity guard: every `_err("<code>", ...)` a propose path can emit is in
    REFUSAL_CODES, so assistant.py's proposal_refused frame can never silently
    miss a new refusal. Codes from the non-propose tools (labware lookup,
    decline's own validation) are exempt — they are not proposal refusals."""

    import inspect
    import re

    source = inspect.getsource(ac)
    # Refusals reach _err two ways: a literal `_err("<code>", ...)`, or a
    # raised `ProposalRefused("<code>", ...)` relayed as `_err(exc.code, ...)`.
    all_codes = set(re.findall(r'_err\(\s*\n?\s*"([a-z_]+)"', source))
    all_codes |= set(re.findall(r'ProposalRefused\(\s*\n?\s*"([a-z_]+)"', source))
    # A third way since the deck-slot resolver moved into the SDK (Step 1m):
    # `_canonicalize_locations` re-raises `SlotResolutionError(exc.code, ...)`
    # as `ProposalRefused(exc.code, ...)`, so its codes are authored there.
    from lab_skills import deck_slots

    all_codes |= set(
        re.findall(r'SlotResolutionError\(\s*\n?\s*"([a-z_]+)"', inspect.getsource(deck_slots))
    )
    non_propose = {"unknown_labware", "incomplete_definition", "empty_explanation"}
    assert all_codes - non_propose <= ac.REFUSAL_CODES
    # And the set holds nothing stale that no code path can produce.
    assert ac.REFUSAL_CODES <= all_codes


# ---------------------------------------------------------------------------
# solid doser proposals (UI_DESIGN §5 Step 1l, 2026-09-02, operator request)
# ---------------------------------------------------------------------------

# The bounded surface: names byte-for-byte what dose_every_well advertises
# (verified live 2026-09-02), mapped to the passthrough URL segment. The device
# has no stop verb; the three held-back names are workflow-only because they
# are unbounded synchronous calls (see _PROPOSABLE's Step 1l note).
_SOLID_DOSER_SURFACE = {
    "startup": "startup",
    "shutdown": "shutdown",
    "home": "home",
    "tare": "tare",
    "plate.set": "plate/set",
    "plate.load": "plate/load",
    "plate.unload": "plate/unload",
    "lid.open": "lid/open",
    "lid.close": "lid/close",
    "plate.raise": "plate/raise",
    "plate.lower": "plate/lower",
    "dose.well": "dose/well",
    "dose.multiple": "dose/multiple",
    "calibrate.flow_rate": "calibrate/flow-rate",
}
_SOLID_DOSER_WORKFLOW_ONLY = {"dose.row", "dose.column", "dose.all"}
# What the live device advertises in `ready` (service.py _READY_ACTIONS):
# everything but startup, plus the three workflow-only dose verbs.
_SOLID_DOSER_ADVERTISED_READY = sorted(
    (set(_SOLID_DOSER_SURFACE) - {"startup"}) | _SOLID_DOSER_WORKFLOW_ONLY
)


@pytest.mark.parametrize(("action", "passthrough"), sorted(_SOLID_DOSER_SURFACE.items()))
def test_resolve_solid_doser_surface(action: str, passthrough: str) -> None:
    entry = _bench_registry("solid_doser").equipment[0]
    sd, resolved_passthrough, args = ac._resolve(entry, action, {})
    assert sd.name == action
    assert resolved_passthrough == passthrough
    assert args == {}


@pytest.mark.parametrize(
    "passthrough", ["plate/lower", "plate/raise", "lid/open", "calibrate/flow-rate"]
)
def test_resolve_solid_doser_accepts_passthrough_alias(passthrough: str) -> None:
    """Models pass the slash form they saw as passthrough_action; it must
    canonicalize back to the dotted advertised name (same as the press)."""

    entry = _bench_registry("solid_doser").equipment[0]
    sd, resolved_passthrough, _ = ac._resolve(entry, passthrough, {})
    assert resolved_passthrough == passthrough
    assert sd.name == {v: k for k, v in _SOLID_DOSER_SURFACE.items()}[passthrough]


@pytest.mark.parametrize("action", sorted(_SOLID_DOSER_WORKFLOW_ONLY))
def test_resolve_solid_doser_refuses_unbounded_dosing(action: str) -> None:
    """Whole-line / whole-plate dosing is workflow-only: an unbounded
    synchronous call no passthrough window covers, on a device with no stop."""

    entry = _bench_registry("solid_doser").equipment[0]
    with pytest.raises(ac.ProposalRefused) as exc:
        ac._resolve(entry, action, {})
    assert exc.value.code == "unmappable_action"


def test_proposable_solid_doser_equals_bounded_surface() -> None:
    assert ac._PROPOSABLE["solid_doser"] == set(_SOLID_DOSER_SURFACE)


def test_solid_doser_dose_multiple_batch_cap() -> None:
    """The request-window bound: more wells than the cap refuses with
    invalid_args and a split-the-work message; the cap itself passes."""

    entry = _bench_registry("solid_doser").equipment[0]
    field, cap = ac._ARG_CARDINALITY_LIMITS[("solid_doser", "dose.multiple")]
    assert field == "well_targets"
    at_cap = {f"A{i}": 5.0 for i in range(1, cap + 1)}
    sd, _, args = ac._resolve(entry, "dose.multiple", {"well_targets": at_cap})
    assert sd.name == "dose.multiple"
    assert len(args["well_targets"]) == cap
    over_cap = {f"A{i}": 5.0 for i in range(1, cap + 2)}
    with pytest.raises(ac.ProposalRefused) as exc:
        ac._resolve(entry, "dose.multiple", {"well_targets": over_cap})
    assert exc.value.code == "invalid_args"
    assert f"at most {cap}" in exc.value.message


@respx.mock
async def test_propose_solid_doser_plate_lower() -> None:
    """The Step 1l minimum ask: one card that sets the plate down on the balance."""

    _mock_bench_status("solid_doser", _SOLID_DOSER_ADVERTISED_READY)
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _bench_registry("solid_doser"),
            "solid_doser_test",
            "plate.lower",
            {},
            "set the plate down on the balance",
        )
    )
    prop = out["proposal"]
    assert prop["action"] == "plate.lower"
    assert prop["passthrough_action"] == "plate/lower"
    assert prop["args"] == {}
    assert prop["kind"] == "solid_doser"


@respx.mock
async def test_propose_solid_doser_dose_well() -> None:
    _mock_bench_status("solid_doser", _SOLID_DOSER_ADVERTISED_READY)
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _bench_registry("solid_doser"),
            "solid_doser_test",
            "dose.well",
            {"well": "B3", "target_mg": 12.5},
            "dose B3",
        )
    )
    prop = out["proposal"]
    assert prop["action"] == "dose.well"
    assert prop["passthrough_action"] == "dose/well"
    assert prop["args"] == {"well": "B3", "target_mg": 12.5}


@respx.mock
async def test_propose_solid_doser_rejects_non_positive_mass() -> None:
    _mock_bench_status("solid_doser", _SOLID_DOSER_ADVERTISED_READY)
    out = json.loads(
        await ac._propose_action(
            _bench_registry("solid_doser"),
            "solid_doser_test",
            "dose.well",
            {"well": "A1", "target_mg": 0},
            "",
        )
    )
    assert out["code"] == "invalid_args"


@respx.mock
async def test_propose_solid_doser_dose_all_refused_even_when_advertised() -> None:
    _mock_bench_status("solid_doser", _SOLID_DOSER_ADVERTISED_READY)
    out = json.loads(
        await ac._propose_action(
            _bench_registry("solid_doser"),
            "solid_doser_test",
            "dose.all",
            {"target_mg": 5.0},
            "dose the plate",
        )
    )
    assert out["code"] == "unmappable_action"


@respx.mock
async def test_propose_solid_doser_lift_not_advertised_when_off() -> None:
    """Device off: only startup is advertised, so a lift proposal is refused on
    the live list (not_allowed) — the allowlist is not what stops it."""

    _mock_bench_status("solid_doser", ["startup"])
    out = json.loads(
        await ac._propose_action(
            _bench_registry("solid_doser"), "solid_doser_test", "plate.lower", {}, ""
        )
    )
    assert out["code"] == "not_allowed"


@respx.mock
async def test_propose_plan_solid_doser_place_dose_lift() -> None:
    """The composable shape the operator asked for: lower the plate, tare, dose
    a small batch, raise it — one plan, one approval, each step re-checked live
    by the device. Step 4 arrives in slash form and canonicalizes."""

    _mock_bench_status("solid_doser", _SOLID_DOSER_ADVERTISED_READY)
    _mock_authz(True)
    out = json.loads(
        await ac._propose_plan(
            _bench_registry("solid_doser"),
            "solid_doser_test",
            [
                {"action": "plate.lower"},
                {"action": "tare"},
                {"action": "dose.multiple", "args": {"well_targets": {"A1": 5.0, "A2": 5.0}}},
                {"action": "plate/raise"},
            ],
            "dose A1-A2 at 5 mg",
        )
    )
    plan = out["plan"]
    assert [s["action"] for s in plan["steps"]] == [
        "plate.lower", "tare", "dose.multiple", "plate.raise",
    ]
    assert [s["passthrough_action"] for s in plan["steps"]] == [
        "plate/lower", "tare", "dose/multiple", "plate/raise",
    ]
    assert plan["steps"][2]["args"]["well_targets"] == {"A1": 5.0, "A2": 5.0}
    assert plan["kind"] == "solid_doser"


@respx.mock
async def test_propose_plan_solid_doser_refuses_oversized_batch_by_step() -> None:
    _mock_bench_status("solid_doser", _SOLID_DOSER_ADVERTISED_READY)
    _mock_authz(True)
    out = json.loads(
        await ac._propose_plan(
            _bench_registry("solid_doser"),
            "solid_doser_test",
            [
                {"action": "plate.lower"},
                {
                    "action": "dose.multiple",
                    "args": {"well_targets": {f"A{i}": 5.0 for i in range(1, 8)}},
                },
            ],
            "",
        )
    )
    assert out["code"] == "invalid_args"
    assert out["step"] == 2


@respx.mock
async def test_list_available_actions_solid_doser_marks_unbounded_dosing_workflow_only() -> None:
    _mock_bench_status("solid_doser", _SOLID_DOSER_ADVERTISED_READY)
    out = json.loads(
        await ac._list_available_actions(_bench_registry("solid_doser"), "solid_doser_test")
    )
    by_action = {a["action"]: a for a in out["actions"]}
    for action, passthrough in _SOLID_DOSER_SURFACE.items():
        if action == "startup":
            continue  # not advertised in `ready`
        assert by_action[action]["proposable"] is True, action
        assert by_action[action]["passthrough_action"] == passthrough
    for action in _SOLID_DOSER_WORKFLOW_ONLY:
        assert by_action[action]["proposable"] is False, action


# ---------------------------------------------------------------------------
# Deck-slot vocabulary resolver (UI_DESIGN §5 Step 1m, 2026-09-04)
# ---------------------------------------------------------------------------

from lab_skills import LocationEntry, LocationsConfig  # noqa: E402
from lab_skills.deck_slots import set_default_locations  # noqa: E402


def _locations() -> LocationsConfig:
    return LocationsConfig(
        locations=[
            LocationEntry(
                name="ot2_hte/slot_1", type="deck", equipment="ot2_hte",
                aliases={"ot2_hte": "1"}, label="OT-2 HTE · slot 1",
            ),
            LocationEntry(
                name="ot2_hte/slot_2", type="deck", equipment="ot2_hte",
                aliases={"ot2_hte": "2", "xarm": ["opentrons_2_low", "opentrons_2_high"]},
                label="OT-2 HTE · slot 2",
            ),
            LocationEntry(
                name="ot2_complexation/slot_2", type="deck", equipment="ot2_complexation",
                aliases={"ot2_complexation": "2"}, label="OT-2 complexation · slot 2",
            ),
        ]
    )


@pytest.fixture()
def _locs() -> LocationsConfig:
    """Install a small registry as the SDK's process-wide default (what
    lab-control reads), and reset to the lazy loader afterwards."""
    cfg = _locations()
    set_default_locations(cfg)
    yield cfg
    set_default_locations(None)


@respx.mock
async def test_propose_ot2_action_carries_canonical_slot_and_place_label(_locs) -> None:
    _mock_ot2_status(["tips.reset"])
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _ot2_registry(), "ot2_hte", "tips.reset", {"slot": "ot2_hte/slot_2"}, "refill the rack"
        )
    )
    proposal = out["proposal"]
    assert proposal["args"] == {"slot": "2"}
    assert proposal["resolved_locations"][0]["label"] == "OT-2 HTE · slot 2"


@respx.mock
async def test_propose_ot2_action_refuses_a_place_on_the_other_robot(_locs) -> None:
    _mock_ot2_status(["tips.reset"])
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _ot2_registry(), "ot2_hte", "tips.reset", {"slot": "ot2_complexation/slot_2"}, ""
        )
    )
    assert out["code"] == "wrong_device_location"


@respx.mock
async def test_propose_plan_tags_place_labels_by_step_outside_the_hash(_locs) -> None:
    _mock_ot2_status(["deck.declare"])
    _mock_authz(True)
    steps = [
        {"action": "deck.declare", "args": {"slots": {"slot_2": "corning_96_wellplate_360ul_flat"}}},
        {"action": "tips.reset", "args": {"slot": "opentrons_2_low"}},
    ]
    out = json.loads(await ac._propose_plan(_ot2_registry(), "ot2_hte", steps, ""))
    plan = out["plan"]
    assert plan["steps"][0]["args"] == {"slots": {"2": "corning_96_wellplate_360ul_flat"}}
    assert plan["steps"][1]["args"] == {"slot": "2"}
    assert [r["step"] for r in plan["resolved_locations"]] == [1, 2]
    # The hash covers the steps exactly as sent; labels ride alongside.
    assert plan["step_hash"] == ac.plan_step_hash(plan["steps"])
    assert all("resolved_locations" not in s for s in plan["steps"])


@respx.mock
async def test_list_available_actions_shows_the_slot_vocabulary(_locs) -> None:
    _mock_ot2_status(["tips.reset"])
    out = json.loads(await ac._list_available_actions(_ot2_registry(), "ot2_hte"))
    by_name = {loc["name"]: loc for loc in out["locations"]}
    assert by_name["ot2_hte/slot_2"]["slot"] == "2"
    assert by_name["ot2_hte/slot_2"]["also_known_as"] == {
        "xarm": ["opentrons_2_low", "opentrons_2_high"]
    }
    assert "ot2_complexation/slot_2" not in by_name
    assert "bare key" in out["slot_vocabulary"]


@respx.mock
async def test_arm_travel_to_an_ot2_place_name_gets_the_node_hint(_locs) -> None:
    """``travel.ot2_hte/slot_2`` is not a graph node; the refusal names the
    nodes that reach that shelf instead of only listing what is allowed."""
    _mock_status(["move.deck_home"], details={"motion_graph": {"current_node": "deck_home", "reachable_nodes": [], "travel_targets": []}})
    _mock_authz(True)
    out = json.loads(await ac._propose_action(_registry(), "xarm", "travel.ot2_hte/slot_2", None, ""))
    assert out["code"] == "not_allowed"
    assert out["location_nodes"] == {"ot2_hte/slot_2": ["opentrons_2_low", "opentrons_2_high"]}
    assert "opentrons_2_low" in out["error"]



# ---------------------------------------------------------------------------
# Deck check — the card carries what the gateway believes is on the deck
# (operator request, 2026-09-04)
# ---------------------------------------------------------------------------

_OT2_DETAILS = {
    "snapshot": {
        "labwares": {
            "4": {"id": "slot_4", "loadName": "agilent_96_2ml_deep_square", "location": {"slotName": "4"}},
            "11": {"id": "slot_11", "loadName": "opentrons_96_tiprack_1000ul", "location": {"slotName": "11"}},
        },
        "pipettes": {},
    },
    "tip_racks": {"11": {"total": 96, "available": 12}},
}


def _mock_ot2_status_with_deck(allowed_actions: list[str]) -> None:
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
                "device_time": "2026-09-04T12:00:00Z",
                "details": _OT2_DETAILS,
            },
        )
    )


@respx.mock
async def test_ot2_proposal_carries_the_deck_with_touched_slots_marked(_locs) -> None:
    _mock_ot2_status_with_deck(["tips.reset"])
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(_ot2_registry(), "ot2_hte", "tips.reset", {"slot": "slot 2"}, "")
    )
    [check] = out["proposal"]["deck_checks"]
    assert check["equipment_id"] == "ot2_hte"
    assert check["touched_slots"] == ["2"]
    assert check["slots"]["4"]["labware"] == "agilent_96_2ml_deep_square"
    assert check["slots"]["11"] == {
        "labware": "opentrons_96_tiprack_1000ul",
        "id": "slot_11",
        "tips_available": 12,
    }


@respx.mock
async def test_ot2_nickname_verb_finds_its_slot_from_the_snapshot(_locs) -> None:
    _mock_ot2_status_with_deck(["pick_up_tip"])
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(
            _ot2_registry(), "ot2_hte", "pick_up_tip", {"pipette": "p1000", "labware_nickname": "slot_11"}, ""
        )
    )
    [check] = out["proposal"]["deck_checks"]
    assert check["touched_slots"] == ["11"]


@respx.mock
async def test_ot2_non_deck_verb_carries_no_deck_check(_locs) -> None:
    _mock_ot2_status_with_deck(["lights.set"])
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(_ot2_registry(), "ot2_hte", "lights.set", {"on": True}, "")
    )
    assert "deck_checks" not in out["proposal"]


def _two_device_registry() -> Registry:
    arm = _registry().equipment[0]
    ot2 = _ot2_registry().equipment[0]
    return Registry(equipment=[arm, ot2])


@respx.mock
async def test_arm_travel_into_an_ot2_slot_reads_that_deck(_locs) -> None:
    """Moving anything onto the OT-2 deck is exactly when the operator must
    look at it: the arm's card carries the target OT-2's deck, touched slot
    marked, read live from that device."""
    _mock_status(
        ["move.deck_home"],
        details={"motion_graph": {"current_node": "deck_home", "reachable_nodes": [], "travel_targets": ["opentrons_2_low"]}},
    )
    _mock_ot2_status_with_deck([])
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(_two_device_registry(), "xarm", "travel.opentrons_2_low", None, "")
    )
    [check] = out["proposal"]["deck_checks"]
    assert check["equipment_id"] == "ot2_hte"
    assert check["touched_slots"] == ["2"]
    assert "4" in check["slots"]
    assert "unreachable" not in check


@respx.mock
async def test_arm_travel_into_an_unreachable_ot2_says_so(_locs) -> None:
    _mock_status(
        ["move.deck_home"],
        details={"motion_graph": {"current_node": "deck_home", "reachable_nodes": [], "travel_targets": ["opentrons_2_low"]}},
    )
    respx.get(f"{OT2_BASE}/status").mock(side_effect=httpx.ConnectError("boom"))
    _mock_authz(True)
    out = json.loads(
        await ac._propose_action(_two_device_registry(), "xarm", "travel.opentrons_2_low", None, "")
    )
    [check] = out["proposal"]["deck_checks"]
    assert check["equipment_id"] == "ot2_hte"
    assert check["slots"] == {} and check["unreachable"]


@respx.mock
async def test_ot2_plan_merges_one_deck_check_across_steps(_locs) -> None:
    _mock_ot2_status_with_deck(["deck.declare"])
    _mock_authz(True)
    steps = [
        {"action": "deck.declare", "args": {"slots": {"slot_2": "corning_96_wellplate_360ul_flat"}}},
        {"action": "tips.reset", "args": {"slot": "11"}},
        {"action": "lights.set", "args": {"on": False}},
    ]
    out = json.loads(await ac._propose_plan(_ot2_registry(), "ot2_hte", steps, ""))
    [check] = out["plan"]["deck_checks"]
    assert check["touched_slots"] == ["2", "11"]
