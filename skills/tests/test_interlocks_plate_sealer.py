"""Tests for the optional plate-sealer heater interlock.

The interlock makes a blocking httpx.Client.get() against the device's
/status; we mock that with respx. All other state (registry, binding,
plan) is fully synthetic and the LabSession is never entered as an
async context (validate_plan is sync/offline-by-design).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from lab_skills import (
    LabSession,
    Plan,
    Step,
    clear_interlocks,
    register_interlock,
    validate_plan,
)
from lab_skills.interlocks_plate_sealer import (
    plate_sealer_heater_must_be_stable_for_seal_start,
)
from lab_skills.registry import EquipmentEntry, Registry


@pytest.fixture(autouse=True)
def _reset_interlocks():
    clear_interlocks()
    register_interlock(plate_sealer_heater_must_be_stable_for_seal_start)
    yield
    clear_interlocks()


def _session() -> LabSession:
    entry = EquipmentEntry(
        id="plateloc",
        name="Plateloc",
        kind="plate_sealer",
        adapter="http",
        base_url="http://plateloc.local:8010",
        protocol="1.1",
        status_path="/status",
    )
    return LabSession(
        registry=Registry(equipment=[entry]), binding={"sealer": "plateloc"}
    )


def _seal_start_plan() -> Plan:
    return Plan(
        steps=[
            Step(
                role="sealer",
                skill="seal.start",
                args={"temperature_c": 170, "seconds": 3.0},
            )
        ]
    )


def _status_body(
    heater_state: str | None,
    *,
    actual: int | None = 170,
    setpoint: int | None = 170,
    message: str | None = None,
) -> dict:
    """Build a minimal STATUS_SPEC-shaped /status body."""

    body: dict = {
        "protocol_version": "1.1",
        "equipment_id": "plateloc",
        "equipment_name": "Plateloc",
        "equipment_kind": "plate_sealer",
        "equipment_status": "ready",
        "device_time": "2026-05-23T16:00:00Z",
        "components": {},
        "metrics": {},
    }
    if heater_state is not None:
        body["components"]["heater"] = {
            "connected": True,
            "state": heater_state,
            "message": message,
            "last_event_at": None,
        }
    if actual is not None:
        body["metrics"]["actual_temperature"] = {"value": actual, "unit": "C"}
    if setpoint is not None:
        body["metrics"]["setpoint_temperature"] = {"value": setpoint, "unit": "C"}
    return body


# -- happy paths -------------------------------------------------------------


@respx.mock
def test_stable_heater_no_violation() -> None:
    respx.get("http://plateloc.local:8010/status").mock(
        return_value=httpx.Response(
            200, json=_status_body("stable", message="At setpoint (170 C)")
        )
    )
    report = validate_plan(_seal_start_plan(), _session())
    assert report.ok is True
    codes = [v.code for v in report.violations + report.warnings]
    assert "heater_not_stable" not in codes
    assert "heater_state_unverified" not in codes


@respx.mock
def test_heater_field_absent_no_opinion() -> None:
    """Old service deployments (no components.heater) -> interlock stays silent."""

    respx.get("http://plateloc.local:8010/status").mock(
        return_value=httpx.Response(200, json=_status_body(heater_state=None))
    )
    report = validate_plan(_seal_start_plan(), _session())
    # No heater-related findings; plan is fine.
    codes = [v.code for v in report.violations + report.warnings]
    assert not any(c.startswith("heater_") for c in codes)


# -- blocking violations -----------------------------------------------------


@respx.mock
def test_heating_blocks_seal_start() -> None:
    respx.get("http://plateloc.local:8010/status").mock(
        return_value=httpx.Response(
            200,
            json=_status_body(
                "heating", actual=142, setpoint=170, message="Heating 142 -> 170 C"
            ),
        )
    )
    report = validate_plan(_seal_start_plan(), _session())
    assert report.ok is False
    v = next(v for v in report.violations if v.code == "heater_not_stable")
    assert v.severity == "error"
    assert "Heating 142" in v.message
    assert v.actionable and "stable" in v.actionable


@respx.mock
def test_cooling_also_blocks() -> None:
    respx.get("http://plateloc.local:8010/status").mock(
        return_value=httpx.Response(
            200, json=_status_body("cooling", actual=195, setpoint=170)
        )
    )
    report = validate_plan(_seal_start_plan(), _session())
    assert report.ok is False
    assert any(v.code == "heater_not_stable" for v in report.violations)


# -- non-blocking warnings ---------------------------------------------------


@respx.mock
def test_unknown_state_warns_does_not_block() -> None:
    respx.get("http://plateloc.local:8010/status").mock(
        return_value=httpx.Response(200, json=_status_body("unknown"))
    )
    report = validate_plan(_seal_start_plan(), _session())
    assert report.ok is True  # warning only
    codes = [w.code for w in report.warnings]
    assert "heater_state_unverified" in codes


@respx.mock
def test_device_unreachable_warns_does_not_block() -> None:
    respx.get("http://plateloc.local:8010/status").mock(
        side_effect=httpx.ConnectError("nope")
    )
    report = validate_plan(_seal_start_plan(), _session())
    assert report.ok is True
    codes = [w.code for w in report.warnings]
    assert "heater_state_unknown" in codes


@respx.mock
def test_500_response_warns_does_not_block() -> None:
    respx.get("http://plateloc.local:8010/status").mock(
        return_value=httpx.Response(503, json={"detail": "broken"})
    )
    report = validate_plan(_seal_start_plan(), _session())
    assert report.ok is True
    codes = [w.code for w in report.warnings]
    assert "heater_state_unknown" in codes


# -- scope guards ------------------------------------------------------------


@respx.mock
def test_does_not_fire_on_non_seal_start_skill() -> None:
    """A plan that doesn't call seal.start must skip the live status check
    entirely; otherwise validate_plan would pay an HTTP round-trip for
    every step on every plate_sealer device."""

    # No respx route registered -> any HTTP call would raise. The
    # interlock must return None before getting there.
    plan = Plan(steps=[Step(role="sealer", skill="stage.in", args={})])
    report = validate_plan(plan, _session())
    assert report.ok is True


def test_does_not_fire_when_role_not_bound_to_plate_sealer() -> None:
    """Skip when the bound device is some other kind (defensive)."""

    other_entry = EquipmentEntry(
        id="filter_every_well",
        name="press",
        kind="press",
        adapter="http",
        base_url="http://press.local:8000",
        protocol="1.1",
        status_path="/status",
    )
    session = LabSession(
        registry=Registry(equipment=[other_entry]),
        binding={"sealer": "filter_every_well"},
    )
    # seal.start isn't in the press skill catalog so validate_plan emits
    # an unknown_skill violation; the heater interlock simply doesn't fire.
    plan = _seal_start_plan()
    report = validate_plan(plan, session)
    codes = [v.code for v in report.violations + report.warnings]
    assert not any(c.startswith("heater_") for c in codes)
