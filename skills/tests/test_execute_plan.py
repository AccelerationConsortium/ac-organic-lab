"""Tests for :func:`lab_skills.execute_plan` (v0.4 sequential executor).

Exercises the live control path against a respx-mocked v1.1 / v1.0 device:
happy path, dry-run preflight, layer-3 (live allowed_actions) blocking,
layer-4 async-interlock blocking, offline-validation short-circuit, a failed
command, claim-token plumbing, v1.0 degraded claims, and the sync façade.

``stage.in`` / ``stage.out`` are used because they carry no
``requires_components`` gate, so a minimal ``/status`` body suffices.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from lab_skills import (
    Lab,
    Plan,
    Step,
    clear_interlocks,
    execute_plan,
    register_interlock,
    validate_plan,
)
from lab_skills.registry import EquipmentEntry, Registry
from lab_skills.sync import Lab as SyncLab

BASE = "http://plateloc.local:8010"


@pytest.fixture(autouse=True)
def _reset_interlocks():
    clear_interlocks()
    yield
    clear_interlocks()


def _registry(*, protocol: str = "1.1") -> Registry:
    return Registry(
        equipment=[
            EquipmentEntry(
                id="plateloc",
                name="Plateloc",
                kind="plate_sealer",
                adapter="http",
                base_url=BASE,
                protocol=protocol,
                status_path="/status",
                poll_timeout_seconds=1.0,
            )
        ]
    )


def _status_body(allowed: list[str], *, state: str = "ready", protocol: str = "1.1") -> dict:
    body = {
        "protocol_version": protocol,
        "equipment_id": "plateloc",
        "equipment_name": "Plateloc",
        "equipment_kind": "plate_sealer",
        "equipment_status": state,
        "device_time": "2026-07-12T00:00:00Z",
        "components": {},
        "metrics": {},
    }
    if protocol == "1.1":
        body["allowed_actions"] = allowed
    return body


def _mock_status(allowed: list[str], **kw) -> None:
    respx.get(f"{BASE}/status").mock(
        return_value=httpx.Response(200, json=_status_body(allowed, **kw))
    )


def _mock_claim_lifecycle() -> None:
    respx.post(f"{BASE}/control/claim").mock(
        return_value=httpx.Response(
            200,
            json={
                "claim_token": "tok-1",
                # Huge interval: the heartbeat task never fires during a test.
                "heartbeat_interval_s": 10_000.0,
                "expires_at": "2099-01-01T00:00:00Z",
            },
        )
    )
    respx.post(f"{BASE}/control/heartbeat").mock(return_value=httpx.Response(204))
    respx.post(f"{BASE}/control/release").mock(return_value=httpx.Response(204))


def _two_stage_plan() -> Plan:
    return Plan(steps=[Step(role="sealer", skill="stage.in"),
                       Step(role="sealer", skill="stage.out")])


# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_happy_path_executes_all_steps_under_claims() -> None:
    _mock_status(["stage.in", "stage.out"])
    _mock_claim_lifecycle()
    in_route = respx.post(f"{BASE}/control/stage/in").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    out_route = respx.post(f"{BASE}/control/stage/out").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    async with Lab.connect(registry=_registry(), binding={"sealer": "plateloc"}) as lab:
        report = await execute_plan(_two_stage_plan(), lab, owner="tester")

    assert report.ok
    assert [s.status for s in report.steps] == ["succeeded", "succeeded"]
    assert all(s.claimed for s in report.steps)
    assert report.claims_acquired == ["plateloc", "plateloc"]
    # The claim token is attached to each control POST (hard-enforcement pass).
    assert in_route.calls.last.request.headers.get("X-Claim-Token") == "tok-1"
    assert out_route.calls.last.request.headers.get("X-Claim-Token") == "tok-1"


@respx.mock
@pytest.mark.asyncio
async def test_dry_run_preflights_without_commands_or_claims() -> None:
    _mock_status(["stage.in", "stage.out"])
    claim_route = respx.post(f"{BASE}/control/claim").mock(
        return_value=httpx.Response(200, json={"claim_token": "x", "heartbeat_interval_s": 1e4, "expires_at": "2099-01-01T00:00:00Z"})
    )
    in_route = respx.post(f"{BASE}/control/stage/in").mock(return_value=httpx.Response(200))

    async with Lab.connect(registry=_registry(), binding={"sealer": "plateloc"}) as lab:
        report = await execute_plan(_two_stage_plan(), lab, owner="tester", dry_run=True)

    assert report.ok and report.dry_run
    assert [s.status for s in report.steps] == ["dry_run", "dry_run"]
    assert report.claims_acquired == []
    # Preflight touched /status but never claimed or commanded.
    assert not claim_route.called
    assert not in_route.called


@respx.mock
@pytest.mark.asyncio
async def test_step_blocked_by_live_allowed_actions_aborts_rest() -> None:
    # Device only allows stage.in right now; the plan leads with stage.out.
    _mock_status(["stage.in"])
    _mock_claim_lifecycle()
    respx.post(f"{BASE}/control/stage/in").mock(return_value=httpx.Response(200))

    plan = Plan(steps=[
        Step(role="sealer", skill="stage.out"),
        Step(role="sealer", skill="stage.in"),
    ])
    async with Lab.connect(registry=_registry(), binding={"sealer": "plateloc"}) as lab:
        report = await execute_plan(plan, lab, owner="tester")

    assert not report.ok
    assert [s.status for s in report.steps] == ["blocked", "skipped"]
    assert report.steps[0].violations[0].code == "not_allowed_live"


@respx.mock
@pytest.mark.asyncio
async def test_offline_validation_failure_short_circuits_before_hardware() -> None:
    status_route = respx.get(f"{BASE}/status").mock(return_value=httpx.Response(200))
    # role "mixer" is not bound -> validate_plan fails before any I/O.
    plan = Plan(steps=[Step(role="mixer", skill="stage.in")])
    async with Lab.connect(registry=_registry(), binding={"sealer": "plateloc"}) as lab:
        report = await execute_plan(plan, lab, owner="tester")

    assert not report.ok
    assert report.steps == []
    assert not report.validation.ok
    assert not status_route.called


@respx.mock
@pytest.mark.asyncio
async def test_async_interlock_blocks_step() -> None:
    _mock_status(["stage.in"])
    _mock_claim_lifecycle()
    in_route = respx.post(f"{BASE}/control/stage/in").mock(return_value=httpx.Response(200))

    calls: list[str] = []

    @register_interlock(name="async_refuse")
    async def _refuse(plan, step, session):
        # Reads live state (async) and refuses.
        status = await session.role(step.role).status()
        calls.append(status.equipment_status)
        from lab_skills import Violation

        return [Violation(
            step_id=step.id, step_index=step.index, code="refused",
            message="nope", severity="critical", interlock_name="async_refuse",
        )]

    async with Lab.connect(registry=_registry(), binding={"sealer": "plateloc"}) as lab:
        report = await execute_plan(
            Plan(steps=[Step(role="sealer", skill="stage.in")]), lab, owner="tester"
        )

    assert not report.ok
    assert report.steps[0].status == "blocked"
    assert report.steps[0].violations[0].code == "refused"
    assert calls == ["ready"]  # the async interlock actually ran + read /status
    assert not in_route.called  # blocked before the command


@respx.mock
@pytest.mark.asyncio
async def test_failed_command_marks_step_failed_and_aborts() -> None:
    _mock_status(["stage.in", "stage.out"])
    _mock_claim_lifecycle()
    respx.post(f"{BASE}/control/stage/in").mock(
        return_value=httpx.Response(409, json={"detail": "device busy"})
    )
    out_route = respx.post(f"{BASE}/control/stage/out").mock(return_value=httpx.Response(200))

    async with Lab.connect(registry=_registry(), binding={"sealer": "plateloc"}) as lab:
        report = await execute_plan(_two_stage_plan(), lab, owner="tester")

    assert not report.ok
    assert [s.status for s in report.steps] == ["failed", "skipped"]
    assert report.steps[0].error is not None
    assert not out_route.called


@respx.mock
@pytest.mark.asyncio
async def test_v10_device_degrades_claims_but_still_commands() -> None:
    # v1.0 device: no allowed_actions -> requires_states fallback (ready ok);
    # ClaimManager degrades to a no-op (no claim POST, no token).
    _mock_status([], state="ready", protocol="1.0")
    claim_route = respx.post(f"{BASE}/control/claim").mock(return_value=httpx.Response(404))
    in_route = respx.post(f"{BASE}/control/stage/in").mock(return_value=httpx.Response(200))

    async with Lab.connect(registry=_registry(protocol="1.0"), binding={"sealer": "plateloc"}) as lab:
        report = await execute_plan(
            Plan(steps=[Step(role="sealer", skill="stage.in")]), lab, owner="tester"
        )

    assert report.ok
    assert report.steps[0].status == "succeeded"
    assert report.steps[0].claimed is False
    assert report.claims_acquired == []
    # Registry-declared v1.0 skips the claim endpoint entirely.
    assert not claim_route.called
    assert in_route.called
    assert in_route.calls.last.request.headers.get("X-Claim-Token") is None


@respx.mock
def test_sync_facade_execute_and_validate_plan() -> None:
    _mock_status(["stage.in"])
    _mock_claim_lifecycle()
    respx.post(f"{BASE}/control/stage/in").mock(return_value=httpx.Response(200))

    plan = Plan(steps=[Step(role="sealer", skill="stage.in")])
    with SyncLab.connect(registry=_registry(), binding={"sealer": "plateloc"}) as lab:
        vreport = lab.validate_plan(plan)
        assert vreport.ok
        rreport = lab.execute_plan(plan, owner="tester")

    assert rreport.ok
    assert rreport.steps[0].status == "succeeded"
    assert rreport.claims_acquired == ["plateloc"]
