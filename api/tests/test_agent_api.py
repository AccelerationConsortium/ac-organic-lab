from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent import build_agent_router
from lab_skills import AgentRuntime, Skill
from lab_skills.registry import EquipmentEntry, Registry
from lab_skills.skill_catalog.plate_sealer import SealStartArgs


def _entry() -> EquipmentEntry:
    return EquipmentEntry(
        id="plateloc",
        name="PlateLoc",
        kind="plate_sealer",
        adapter="http",
        base_url="http://plateloc.local:8000",
        protocol="1.1",
    )


def _skill() -> Skill:
    return Skill(
        name="seal.start",
        role="sealer",
        equipment_id="plateloc",
        kind="plate_sealer",
        description="Start a heat seal cycle on a plate sealer.",
        args_schema=SealStartArgs,
        estimated_duration_s=3.0,
        available=True,
    )


def _app() -> FastAPI:
    app = FastAPI()
    app.state.registry = Registry(equipment=[_entry()])
    app.state.agent_runtime = AgentRuntime()
    app.state.agent_skills = [_skill()]
    app.include_router(build_agent_router())
    return app


def test_create_agent_run_that_waits_for_expert_review() -> None:
    with TestClient(_app()) as client:
        response = client.post(
            "/api/agent/runs",
            json={
                "objective": "Seal plate after review",
                "binding": {"sealer": "plateloc"},
                "tasks": [
                    {
                        "id": "seal_plate",
                        "goal": "seal",
                        "args": {"temperature_c": 170, "seconds": 3.0},
                        "confidence_hint": 0.5,
                    }
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "waiting_for_expert"
    assert body["workflow"]["review_requests"][0]["task_id"] == "seal_plate"


def test_approve_review_and_execute_dry_run() -> None:
    with TestClient(_app()) as client:
        created = client.post(
            "/api/agent/runs",
            json={
                "objective": "Seal plate after review",
                "binding": {"sealer": "plateloc"},
                "tasks": [
                    {
                        "id": "seal_plate",
                        "goal": "seal",
                        "args": {"temperature_c": 170, "seconds": 3.0},
                        "confidence_hint": 0.5,
                    }
                ],
            },
        ).json()

        approved = client.post(
            f"/api/agent/runs/{created['id']}/reviews/seal_plate/approve",
            json={"reviewer": "chemist@example.com", "note": "Looks correct."},
        )
        executed = client.post(
            f"/api/agent/runs/{created['id']}/execute",
            json={"dry_run": True},
        )

    assert approved.status_code == 200
    assert approved.json()["state"] == "ready"
    assert executed.status_code == 200
    body = executed.json()
    assert body["state"] == "completed"
    assert body["execution"][0]["status"] == "completed"


def test_execute_rejects_pending_review() -> None:
    with TestClient(_app()) as client:
        created = client.post(
            "/api/agent/runs",
            json={
                "objective": "Seal plate after review",
                "binding": {"sealer": "plateloc"},
                "tasks": [
                    {
                        "id": "seal_plate",
                        "goal": "seal",
                        "args": {"temperature_c": 170, "seconds": 3.0},
                        "confidence_hint": 0.5,
                    }
                ],
            },
        ).json()

        response = client.post(
            f"/api/agent/runs/{created['id']}/execute",
            json={"dry_run": True},
        )

    assert response.status_code == 409
    assert "pending expert review" in response.json()["detail"]
