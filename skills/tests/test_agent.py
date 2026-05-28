from __future__ import annotations

import pytest

from lab_skills import (
    AgentRuntime,
    AgentTask,
    LabSession,
    Skill,
    clear_interlocks,
    compose_workflow,
)
from lab_skills.registry import EquipmentEntry, Registry
from lab_skills.skill_catalog.plate_sealer import SealStartArgs, StartupArgs


@pytest.fixture(autouse=True)
def _reset_interlocks():
    clear_interlocks()
    yield
    clear_interlocks()


def _entry(*, do_not_call_connect: bool = False) -> EquipmentEntry:
    return EquipmentEntry(
        id="plateloc",
        name="PlateLoc",
        kind="plate_sealer",
        adapter="http",
        base_url="http://plateloc.local:8000",
        protocol="1.1",
        do_not_call_connect=do_not_call_connect,
    )


def _session(entry: EquipmentEntry) -> LabSession:
    return LabSession(
        registry=Registry(equipment=[entry]),
        binding={"sealer": entry.id},
    )


def _skill(
    name: str = "seal.start",
    *,
    description: str = "Start a heat seal cycle on a plate sealer.",
    available: bool = True,
    reason: str | None = None,
) -> Skill:
    return Skill(
        name=name,
        role="sealer",
        equipment_id="plateloc",
        kind="plate_sealer",
        description=description,
        args_schema=SealStartArgs if name == "seal.start" else StartupArgs,
        estimated_duration_s=3.0,
        available=available,
        reason=reason,
    )


def test_compose_workflow_accepts_high_confidence_skill_match() -> None:
    result = compose_workflow(
        [
            AgentTask(
                id="seal_plate",
                goal="seal start plate",
                args={"temperature_c": 170, "seconds": 3.0},
            )
        ],
        [_skill()],
        _session(_entry()),
    )

    assert result.plan_report.ok is True
    assert result.accepted_tasks == ["seal_plate"]
    assert result.skipped_tasks == []
    assert result.review_requests == []
    assert result.plan.steps[0].role == "sealer"
    assert result.plan.steps[0].skill == "seal.start"


def test_compose_workflow_requests_expert_review_for_low_confidence_task() -> None:
    result = compose_workflow(
        [AgentTask(id="inspect_crystals", goal="judge crystal morphology")],
        [_skill()],
        _session(_entry()),
    )

    assert result.plan.steps == []
    assert result.accepted_tasks == []
    assert result.skipped_tasks == ["inspect_crystals"]
    assert len(result.review_requests) == 1
    assert "below threshold" in result.review_requests[0].reason


def test_compose_workflow_requests_expert_review_for_rule_blocker() -> None:
    result = compose_workflow(
        [
            AgentTask(
                id="seal_plate",
                goal="seal start plate",
                args={"temperature_c": 170, "seconds": 3.0},
            )
        ],
        [_skill()],
        _session(_entry(do_not_call_connect=True)),
    )

    assert result.plan_report.ok is False
    assert result.accepted_tasks == ["seal_plate"]
    assert any(v.code == "do_not_call_connect" for v in result.plan_report.violations)
    assert len(result.review_requests) == 1
    assert result.review_requests[0].task_id == "seal_plate"
    assert result.review_requests[0].violations[0].code == "do_not_call_connect"


def test_agent_runtime_approves_low_confidence_task_then_dry_runs() -> None:
    runtime = AgentRuntime()
    session = _session(_entry())
    run = runtime.create_run(
        objective="Seal a plate after expert review",
        tasks=[
            AgentTask(
                id="seal_plate",
                goal="seal",
                args={"temperature_c": 170, "seconds": 3.0},
                confidence_hint=0.5,
            )
        ],
        skills=[_skill()],
        session=session,
    )

    assert run.state == "waiting_for_expert"
    assert run.workflow.plan.steps == []
    assert run.workflow.review_requests[0].proposed_step is not None

    approved = runtime.approve_review(
        run.id,
        task_id="seal_plate",
        reviewer="chemist@example.com",
        note="Correct step for this protocol.",
        session=session,
    )

    assert approved.state == "ready"
    assert approved.workflow.review_requests == []
    assert approved.workflow.plan.steps[0].id == "seal_plate"
    assert approved.decisions[0].decision == "approved"

    executed = runtime.execute(run.id)

    assert executed.state == "completed"
    assert executed.execution[0].status == "completed"
    assert "no hardware" in executed.execution[0].message


def test_agent_runtime_rejects_review_and_completes_empty_plan() -> None:
    runtime = AgentRuntime()
    run = runtime.create_run(
        objective="Inspect crystals",
        tasks=[AgentTask(id="inspect_crystals", goal="judge crystal morphology")],
        skills=[_skill()],
        session=_session(_entry()),
    )

    rejected = runtime.reject_review(
        run.id,
        task_id="inspect_crystals",
        reviewer="chemist@example.com",
        note="Needs a microscope workflow.",
    )

    assert rejected.state == "ready"
    assert rejected.workflow.plan.steps == []
    assert rejected.decisions[0].decision == "rejected"


def test_agent_runtime_refuses_execution_while_review_is_pending() -> None:
    runtime = AgentRuntime()
    run = runtime.create_run(
        objective="Inspect crystals",
        tasks=[AgentTask(id="inspect_crystals", goal="judge crystal morphology")],
        skills=[_skill()],
        session=_session(_entry()),
    )

    with pytest.raises(RuntimeError, match="pending expert review"):
        runtime.execute(run.id)
