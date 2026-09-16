"""UR SDK tests: synthetic status and mocked HTTP only, never robot I/O."""

import json
import socket
from uuid import uuid4

import httpx
import pytest
import respx
from pydantic import ValidationError

from lab_skills import LabSession, Plan, Step, execute_plan, validate_plan
from lab_skills.models import EquipmentStatus
from lab_skills.registry import EquipmentEntry, Registry
from lab_skills.session import _availability
from lab_skills.skill_catalog import SKILL_REGISTRY, skills_for
from lab_skills.skill_catalog.ur_arm import URJointStepArgs

BASE = "http://ur-sdk-offline.invalid"
ACTION = "ur.joint_step"
ENDPOINT = "/control/joint/step"


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def refused(*args, **kwargs):
        raise AssertionError("UR SDK tests must not open real connections")

    monkeypatch.setattr(socket.socket, "connect", refused)
    monkeypatch.setattr(socket.socket, "connect_ex", refused)


def args(**changes):
    return {"request_id": str(uuid4()), "joint": 2, "delta_deg": 0.1, **changes}


def session(equipment_id="ligand_ur5e", **entry_changes):
    entry = EquipmentEntry(
        **{
            "id": equipment_id,
            "name": "Offline robot fixture",
            "kind": "robot_arm",
            "adapter": "http",
            "base_url": BASE,
            "protocol": "1.2",
            "do_not_call_connect": True,
            **entry_changes,
        }
    )
    return LabSession(registry=Registry(equipment=[entry]), binding={"arm": entry.id})


def plan(*, skill=ACTION, body=None, count=1):
    return Plan(
        steps=[
            Step(role="arm", skill=skill, args=args() if body is None else body)
            for _ in range(count)
        ]
    )


def status_body(*, allowed=None, component=None, **changes):
    return {
        "protocol_version": "1.2",
        "equipment_id": "ligand_ur5e",
        "equipment_name": "Offline robot fixture",
        "equipment_kind": "robot_arm",
        "equipment_status": "ready",
        "activity": "idle",
        "device_time": "2026-01-01T00:00:00Z",
        "allowed_actions": [] if allowed is None else allowed,
        "components": (
            {} if component is None else {"ur_joint_step": {"connected": True, "state": component}}
        ),
        **changes,
    }


def availability(body, **changes):
    return _availability(
        skills_for("robot_arm", "ligand_ur5e")[0],
        **{
            "status": EquipmentStatus.model_validate(body),
            "maintenance_reason": None,
            "unreachable_reason": None,
            **changes,
        },
    )


def mock_status(body):
    return respx.get(f"{BASE}/status").respond(200, json=body)


def mock_claim():
    claim = respx.post(f"{BASE}/control/claim").respond(
        200,
        json={
            "claim_token": "offline-test-token",
            "heartbeat_interval_s": 10000,
            "expires_at": "2099-01-01T00:00:00Z",
        },
    )
    release = respx.post(f"{BASE}/control/release").respond(204)
    return claim, release


def test_ur_catalog_is_equipment_specific_and_does_not_mutate_xarm():
    before = list(SKILL_REGISTRY["robot_arm"])
    ur = skills_for("robot_arm", "ligand_ur5e")
    assert [definition.name for definition in ur] == [ACTION]
    assert ur[0].endpoint == ENDPOINT
    assert ur[0].method == "POST"
    assert ur[0].requires_components == {"ur_joint_step": "commissioned_idle"}
    for equipment_id in (None, "xarm5", "xarm", "another_arm"):
        assert skills_for("robot_arm", equipment_id) == before
        assert ACTION not in {d.name for d in skills_for("robot_arm", equipment_id)}
    assert skills_for("other", "ligand_ur5e") == []
    assert SKILL_REGISTRY["robot_arm"] == before
    ur.clear()
    assert len(skills_for("robot_arm", "ligand_ur5e")) == 1


@pytest.mark.parametrize("joint", range(1, 7))
@pytest.mark.parametrize("delta", [-0.1, -0.02, 0.02, 0.1])
def test_explicit_small_steps_are_json_native(joint, delta):
    body = args(joint=joint, delta_deg=delta)
    parsed = URJointStepArgs.model_validate(body)
    assert json.loads(parsed.model_dump_json()) == body
    assert parsed.model_dump() == body
    assert validate_plan(plan(body=body), session()).ok


@pytest.mark.parametrize(
    "change",
    [
        {"joint": 0},
        {"joint": 7},
        {"joint": True},
        {"joint": 1.0},
        {"joint": "1"},
        {"delta_deg": True},
        {"delta_deg": "0.1"},
        {"delta_deg": 0},
        {"delta_deg": 0.019},
        {"delta_deg": -0.019},
        {"delta_deg": 0.10001},
        {"delta_deg": -0.10001},
        {"delta_deg": 0.5},
        {"delta_deg": float("nan")},
        {"delta_deg": float("inf")},
        {"request_id": ""},
        {"request_id": "not-a-uuid"},
        {"request_id": "00000000-0000-0000-0000-000000000000"},
        {"request_id": "A1234567-ABCD-4321-ABCD-123456789ABC"},
        {"request_id": uuid4()},
        {"request_id": str(uuid4()) + "\n"},
        {"speed": 10},
        {"force": True},
        {"target": [0] * 6},
        {"linear": True},
        {"commissioning_override": True},
    ],
)
def test_invalid_or_override_arguments_fail_offline(change):
    body = args(**change)
    with pytest.raises(ValidationError):
        URJointStepArgs.model_validate(body)
    report = validate_plan(plan(body=body), session())
    assert not report.ok
    assert "invalid_args" in {v.code for v in report.violations}


@pytest.mark.parametrize("missing", ["request_id", "joint", "delta_deg"])
def test_no_implicit_motion_parameters(missing):
    body = args()
    del body[missing]
    with pytest.raises(ValidationError):
        URJointStepArgs.model_validate(body)
    assert not validate_plan(plan(body=body), session()).ok


def test_no_xarm_graph_or_other_motion_actions_for_ur():
    for action, body in [
        ("graph.move_to", {"node_id": "fixture"}),
        ("graph.mode", {"mode": "off"}),
        ("connect", {}),
        ("home", {}),
        ("ur.linear_step", {}),
        ("graph.gripper", {"state": "empty"}),
    ]:
        report = validate_plan(plan(skill=action, body=body), session())
        assert not report.ok
        assert "unknown_skill" in {v.code for v in report.violations}
    assert not validate_plan(plan(), session("xarm5")).ok
    assert validate_plan(
        plan(skill="graph.move_to", body={"node_id": "fixture"}), session("xarm5")
    ).ok


@pytest.mark.parametrize("allowed", [[], [ACTION]])
@pytest.mark.parametrize("component", [None, "disabled", "ready", "running", "fault"])
def test_observer_or_uncommissioned_component_never_allows_motion(allowed, component):
    available, reason = availability(status_body(allowed=allowed, component=component))
    assert not available
    assert "ur_joint_step" in reason


def test_future_device_availability_respects_existing_sdk_gates():
    # Synthetic future status only: no current service publishes this marker.
    body = status_body(allowed=[ACTION], component="commissioned_idle")
    assert availability(body) == (True, None)
    assert not availability({**body, "allowed_actions": ["ur.stop"]})[0]
    assert not availability(body, maintenance_reason="offline fixture")[0]
    assert not availability(body, unreachable_reason="offline fixture")[0]
    assert not availability(body, status=None)[0]


@respx.mock
async def test_receive_only_service_blocks_before_any_claim_or_command():
    mock_status(status_body(details={"monitoring_only": True, "control_enabled": False}))
    async with session() as lab:
        skills = await lab.skills()
        assert len(skills) == 1 and not skills[0].available
        report = await execute_plan(plan(), lab, owner="offline-fixture")
    assert not report.ok
    assert report.steps[0].status == "blocked"
    assert all(call.request.method == "GET" for call in respx.calls)


@respx.mock
async def test_mock_future_device_receives_exact_step_and_claim_token():
    mock_status(status_body(allowed=[ACTION], component="commissioned_idle"))
    claim, release = mock_claim()
    body = args(joint=6, delta_deg=-0.02)
    response = {"request_id": body["request_id"], "completed": True}
    motion = respx.post(f"{BASE}{ENDPOINT}").respond(200, json=response)
    async with session() as lab:
        report = await execute_plan(plan(body=body), lab, owner="offline-fixture")
    assert report.ok
    assert report.steps[0].claimed
    assert report.steps[0].response == response
    assert motion.call_count == claim.call_count == release.call_count == 1
    assert json.loads(motion.calls.last.request.content) == body
    assert motion.calls.last.request.headers["X-Claim-Token"] == "offline-test-token"
    assert report.claims_acquired == ["ligand_ur5e"]


@respx.mock
async def test_mock_dry_run_never_claims_or_sends_motion():
    mock_status(status_body(allowed=[ACTION], component="commissioned_idle"))
    async with session() as lab:
        report = await execute_plan(plan(), lab, owner="offline-fixture", dry_run=True)
    assert report.ok and report.steps[0].status == "dry_run"
    assert all(call.request.method == "GET" for call in respx.calls)


@respx.mock
@pytest.mark.parametrize("status_code", [401, 403, 409, 423])
async def test_mock_claim_refusal_never_dispatches(status_code):
    mock_status(status_body(allowed=[ACTION], component="commissioned_idle"))
    claim = respx.post(f"{BASE}/control/claim").respond(
        status_code, json={"detail": "Offline refusal fixture"}
    )
    async with session() as lab:
        report = await execute_plan(plan(count=2), lab, owner="offline-fixture")
    assert not report.ok
    assert [s.status for s in report.steps] == ["failed", "skipped"]
    assert claim.call_count == 1
    assert all(call.request.url.path != ENDPOINT for call in respx.calls)


@respx.mock
@pytest.mark.parametrize("outcome", [412, 423, "timeout"])
async def test_mock_dispatch_failure_is_not_retried_and_skips_next_step(outcome):
    mock_status(status_body(allowed=[ACTION], component="commissioned_idle"))
    _, release = mock_claim()
    motion = respx.post(f"{BASE}{ENDPOINT}")
    if outcome == "timeout":
        motion.mock(side_effect=httpx.ReadTimeout("Offline ambiguous-reply fixture"))
    else:
        motion.respond(outcome, json={"detail": "Offline precondition/claim refusal"})
    async with session() as lab:
        report = await execute_plan(plan(count=2), lab, owner="offline-fixture")
    assert not report.ok
    expected = "unknown" if outcome == "timeout" else "failed"
    assert [s.status for s in report.steps] == [expected, "skipped"]
    assert motion.call_count == release.call_count == 1


@respx.mock
async def test_authorization_gate_refusal_makes_no_http_requests():
    async def deny(_step):
        return "Offline revoked-authorization fixture"

    async with session() as lab:
        report = await execute_plan(plan(), lab, owner="offline-fixture", gate=deny)
    assert not report.ok
    assert report.aborted_reason == "Offline revoked-authorization fixture"
    assert not respx.calls
