"""Completion reporting must accommodate the full supported proposal size."""

import httpx
import pytest
from fastapi import FastAPI

from app import assistant, assistant_openai
from app.assistant_control import MAX_PLAN_STEPS, plan_step_hash


async def test_stale_tab_cannot_send_another_owners_history(monkeypatch):
    async def unexpected_turn(*args, **kwargs):
        pytest.fail("A stale conversation must be refused before invoking a backend")
        yield b""

    monkeypatch.setattr(assistant_openai, "run_openai_turn", unexpected_turn)
    monkeypatch.setattr(assistant, "_run_claude", unexpected_turn)
    app = FastAPI()
    app.include_router(assistant.build_assistant_router())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/assistant/chat", headers={"X-Auth-User": "bob@example.edu"}, json={
            "conversation_owner": "alice@example.edu", "messages": [{"role": "user", "content": "private history"}],
        })
        assert response.status_code == 409
        assert "account changed" in response.json()["detail"]


@pytest.mark.parametrize("status", ["executed", "failed"])
async def test_full_size_completion_report_reaches_audit(monkeypatch, status):
    steps = [
        {"action": "move.a", "passthrough_action": "graph/move_to", "args": {"node_id": "a"}}
        for _ in range(MAX_PLAN_STEPS)
    ]
    plan = {
        "plan_id": "report-test", "actor": "alice@example.edu",
        "equipment_id": "fake-arm", "steps": steps, "step_hash": plan_step_hash(steps),
    }
    events = []

    async def capture_audit(db, equipment_id, event_type, message, payload):
        events.append((event_type, payload))

    async def fake_turn(messages, *, control=False, actor=None, on_proposal=None, on_plan=None):
        assert control
        await on_plan(plan)
        yield assistant._sse({"type": "plan", "plan": plan})
        yield assistant._sse({"type": "done"})

    monkeypatch.setattr(assistant, "CONTROL_BACKEND", "openai")
    monkeypatch.setattr(assistant, "_audit", capture_audit)
    monkeypatch.setattr(assistant_openai, "run_openai_turn", fake_turn)
    monkeypatch.setenv("ASSISTANT_OPENAI_API_KEY", "test-only")
    monkeypatch.delenv("DASHBOARD_CONTROL_OPEN", raising=False)
    app = FastAPI()
    app.include_router(assistant.build_assistant_router())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
        headers={"X-Auth-User": plan["actor"]},
    ) as client:
        response = await client.post("/api/assistant/chat", json={
            "mode": "control", "conversation_owner": plan["actor"], "messages": [{"role": "user", "content": "propose"}],
        })
        assert response.status_code == 200
        response = await client.post("/api/assistant/plans/report-test/approve", json={"step_hash": plan["step_hash"]})
        assert response.status_code == 200
        results = [{"index": i + 1, "outcome": "ok"} for i in range(MAX_PLAN_STEPS)]
        if status == "failed":
            results[-2]["outcome"] = "failed"
            results[-1]["outcome"] = "skipped"
        # Oversize bodies and a different identity cannot consume the report.
        response = await client.post("/api/assistant/plans/report-test/finish", json={
            "status": status, "results": results + [{"index": MAX_PLAN_STEPS + 1, "outcome": "skipped"}],
        })
        assert response.status_code == 422
        response = await client.post("/api/assistant/plans/report-test/finish", headers={"X-Auth-User": "bob@example.edu"}, json={
            "status": status, "results": results,
        })
        assert response.status_code == 403
        response = await client.post("/api/assistant/plans/report-test/finish", json={"status": status, "results": results})
        assert response.status_code == 200
        event, payload = events[-1]
        assert event == "assistant_plan_finished"
        assert payload["status"] == status
        assert len(payload["results"]) == MAX_PLAN_STEPS
        assert payload["results"][-1]["outcome"] == results[-1]["outcome"]
