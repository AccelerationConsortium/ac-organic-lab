"""Standalone agent runtime demo.

Run from the repo root:

    uv run --with pytest --with pytest-asyncio --with respx python skills/examples/agent_runtime_demo.py

This does not contact hardware. It builds an in-memory lab session, composes a
low-confidence workflow, routes it through expert approval, and dry-runs it.
"""

from __future__ import annotations

from lab_skills import AgentRuntime, AgentTask, LabSession, Skill
from lab_skills.registry import EquipmentEntry, Registry
from lab_skills.skill_catalog.plate_sealer import SealStartArgs


def main() -> None:
    entry = EquipmentEntry(
        id="plateloc",
        name="PlateLoc",
        kind="plate_sealer",
        adapter="http",
        base_url="http://plateloc.local:8000",
        protocol="1.1",
    )
    session = LabSession(
        registry=Registry(equipment=[entry]),
        binding={"sealer": "plateloc"},
    )
    skills = [
        Skill(
            name="seal.start",
            role="sealer",
            equipment_id="plateloc",
            kind="plate_sealer",
            description="Start a heat seal cycle on a plate sealer.",
            args_schema=SealStartArgs,
            estimated_duration_s=3.0,
            available=True,
        )
    ]

    runtime = AgentRuntime()
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
        skills=skills,
        session=session,
    )
    print(f"created: {run.id} state={run.state} reviews={len(run.workflow.review_requests)}")

    run = runtime.approve_review(
        run.id,
        task_id="seal_plate",
        reviewer="chemist@example.com",
        note="Correct protocol step.",
        session=session,
    )
    print(f"approved: state={run.state} steps={len(run.workflow.plan.steps)}")

    run = runtime.execute(run.id)
    print(f"executed: state={run.state} execution={run.execution[0].status}")


if __name__ == "__main__":
    main()
