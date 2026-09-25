"""Agent questions authenticate as machines and invoke a read-only Codex turn."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI

from app import agent_questions as aq
from app import agent_questions_worker as worker


@pytest.fixture
async def client(monkeypatch, tmp_path):
    allowlist = tmp_path / "agent-consultant.local.json"
    allowlist.write_text(json.dumps({"allowed_principals": [
        "agent:jiaru@lab.example", "agent:allan@lab.example", "agent:geyuan@lab.example",
    ]}))
    monkeypatch.setenv("AGENT_CONSULTANT_ALLOWLIST_PATH", str(allowlist))

    def verify(request: httpx.Request) -> httpx.Response:
        identities = {
            "valid-key": "agent:jiaru@lab.example",
            "allan-key": "agent:allan@lab.example",
            "geyuan-key": "agent:geyuan@lab.example",
            "camera-key": "xarm-camera@lab.example",
        }
        if request.headers.get("x-api-key") in identities:
            assert request.headers["x-forwarded-uri"] in ("/api/agent/questions", "/api/agent/feedback")
            return httpx.Response(200, headers={"x-auth-user": identities[request.headers["x-api-key"]]})
        if request.headers.get("x-api-key") == "path-denied-key":
            return httpx.Response(403)
        return httpx.Response(401)

    app = FastAPI()
    app.state.control_client = httpx.AsyncClient(transport=httpx.MockTransport(verify))
    app.include_router(aq.build_agent_questions_router())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as caller:
        yield caller
    await app.state.control_client.aclose()


@pytest.mark.asyncio
async def test_only_verified_machine_can_ask(client, monkeypatch):
    async def answer(body: aq.AgentQuestion) -> str:
        assert body.question == "What does the registry contain?"
        return "It lists equipment."

    monkeypatch.setattr(aq, "_ask_worker", answer)
    body = {"question": "What does the registry contain?"}
    assert (await client.post("/api/agent/questions", json=body)).status_code == 401
    assert (await client.post("/api/agent/questions", json=body,
                              headers={"x-api-key": "bad-key"})).status_code == 401
    assert (await client.post("/api/agent/questions", json=body,
                              headers={"x-api-key": "path-denied-key"})).status_code == 403
    response = await client.post("/api/agent/questions", json=body,
                                 headers={"x-api-key": "valid-key",
                                          "x-auth-user": "forged@lab.example"})
    assert response.status_code == 200
    assert response.json() == {"actor": "agent:jiaru@lab.example", "answer": "It lists equipment."}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_only_three_named_agents_can_use_both_routes(client, monkeypatch):
    async def answer(body: aq.AgentQuestion) -> str:
        return "Answer."

    async def deliver(path: str, payload: dict) -> dict:
        return {"delivered": True}

    monkeypatch.setattr(aq, "_ask_worker", answer)
    monkeypatch.setattr(aq, "_call_worker", deliver)
    for key in ("valid-key", "allan-key", "geyuan-key"):
        assert (await client.post("/api/agent/questions", json={"question": "Hello"},
                                  headers={"x-api-key": key})).status_code == 200
        assert (await client.post("/api/agent/feedback", json={"message": "Hello"},
                                  headers={"x-api-key": key})).status_code == 200
    for path, body in (("questions", {"question": "Hello"}),
                       ("feedback", {"message": "Hello"})):
        response = await client.post(f"/api/agent/{path}", json=body,
                                     headers={"x-api-key": "camera-key"})
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_missing_allowlist_fails_closed(client, monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONSULTANT_ALLOWLIST_PATH", str(tmp_path / "missing.json"))
    response = await client.post("/api/agent/questions", json={"question": "Hello"},
                                 headers={"x-api-key": "valid-key"})
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_worker_runs_one_answer_only(monkeypatch):
    async def answer(question: str, context: str | None) -> str:
        assert question == "Explain the registry"
        assert context == "A test context"
        return "The registry lists equipment."

    monkeypatch.setattr(worker, "_ask_codex", answer)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=worker.app), base_url="http://worker"
    ) as caller:
        response = await caller.post("/questions", json={"question": "Explain the registry", "context": "A test context"})
    assert response.status_code == 200
    assert response.json() == {"answer": "The registry lists equipment."}


@pytest.mark.asyncio
async def test_unavailable_worker_fails_closed(client, monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONSULTANT_SOCKET", str(tmp_path / "absent.sock"))
    response = await client.post("/api/agent/questions", json={"question": "Hello"},
                                 headers={"x-api-key": "valid-key"})
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_public_route_forwards_to_private_worker(client, monkeypatch):
    async def answer(question: str, context: str | None) -> str:
        return f"Answer to: {question}; context: {context}"

    monkeypatch.setattr(worker, "_ask_codex", answer)
    monkeypatch.setattr(
        aq.httpx, "AsyncHTTPTransport",
        lambda **kwargs: httpx.ASGITransport(app=worker.app),
    )
    response = await client.post("/api/agent/questions", json={"question": "Hello", "context": "Details"},
                                 headers={"x-api-key": "valid-key"})
    assert response.status_code == 200
    assert response.json() == {"actor": "agent:jiaru@lab.example", "answer": "Answer to: Hello; context: Details"}


@pytest.mark.asyncio
async def test_codex_turn_enforces_read_only_and_returns_final_answer(monkeypatch, tmp_path):
    captured = {}

    class Process:
        returncode = 0

        async def communicate(self, prompt):
            captured["prompt"] = prompt.decode()
            events = [
                {"type": "item.completed", "item": {"type": "reasoning", "text": "private"}},
                {"type": "item.completed", "item": {"type": "agent_message", "text": "Answer."}},
            ]
            return b"\n".join(json.dumps(event).encode() for event in events), b""

    async def spawn(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return Process()

    monkeypatch.setenv("AGENT_QUESTIONS_CODEX_BIN", "/usr/bin/codex")
    monkeypatch.setenv("ASSISTANT_OPENAI_API_KEY", "must-not-pass-to-codex")
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("test login")
    monkeypatch.setattr(aq, "_auth_file", lambda: auth_file)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    assert await aq._ask_codex("Question?") == "Answer."
    cmd = captured["cmd"]
    assert cmd[:2] == ("/usr/bin/codex", "exec")
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert cmd[cmd.index("--config") + 1] == "approval_policy=never"
    assert "--ephemeral" in cmd and "--ignore-user-config" in cmd
    assert "--strict-config" in cmd
    assert "mcp_servers={}" in cmd and "agents.enabled=false" in cmd
    assert cmd.count("--disable") == 4
    assert "--skip-git-repo-check" in cmd
    assert "shell_tool" in cmd and "unified_exec" in cmd
    assert cmd[-1] == "-"
    assert "Question?" in captured["prompt"]
    assert "Caller-supplied context" in captured["prompt"]
    assert "ASSISTANT_OPENAI_API_KEY" not in captured["env"]
    assert captured["env"]["CODEX_HOME"] != str(auth_file.parent)


def test_missing_final_answer_is_an_error():
    with pytest.raises(Exception, match="Codex returned no answer"):
        aq._final_answer(b'{"type":"turn.completed"}\n')


@pytest.mark.asyncio
async def test_feedback_requires_machine_key_and_reports_delivery(client, monkeypatch):
    async def deliver(path: str, payload: dict) -> dict:
        assert path == "/feedback"
        assert payload == {"actor": "agent:jiaru@lab.example", "message": "Please review this", "context": None}
        return {"delivered": True}

    monkeypatch.setattr(aq, "_call_worker", deliver)
    body = {"message": "Please review this"}
    assert (await client.post("/api/agent/feedback", json=body)).status_code == 401
    assert (await client.post("/api/agent/feedback", json=body,
                              headers={"x-api-key": "bad-key"})).status_code == 401
    response = await client.post("/api/agent/feedback", json=body,
                                 headers={"x-api-key": "valid-key", "x-auth-user": "forged"})
    assert response.status_code == 200
    assert response.json() == {"actor": "agent:jiaru@lab.example", "delivered": True}


@pytest.mark.asyncio
async def test_feedback_worker_posts_plain_text_and_reports_delivery(monkeypatch):
    posts = []

    def slack(request: httpx.Request) -> httpx.Response:
        posts.append(request)
        return httpx.Response(200, text="ok")

    monkeypatch.setenv("AGENT_CONSULTANT_SLACK_WEBHOOK_URL", "https://hooks.slack.test/secret")
    original_client = httpx.AsyncClient
    monkeypatch.setattr(worker.httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(slack), **kwargs
    ))
    response = await worker.deliver_feedback(worker.FeedbackDelivery(
        actor="jiaru@lab.example", message="<@U123> found an issue", context="At a test step"
    ))
    assert response == {"delivered": True}
    payload = json.loads(posts[0].content)
    assert all(block["text"]["type"] == "plain_text" for block in payload["blocks"])
    assert "<@U123> found an issue" == payload["blocks"][1]["text"]["text"]


@pytest.mark.asyncio
async def test_feedback_worker_requires_webhook(monkeypatch):
    monkeypatch.delenv("AGENT_CONSULTANT_SLACK_WEBHOOK_URL", raising=False)
    with pytest.raises(Exception, match="not configured"):
        await worker.deliver_feedback(worker.FeedbackDelivery(
            actor="jiaru@lab.example", message="Please review this"
        ))


@pytest.mark.asyncio
async def test_feedback_worker_rejects_failed_slack_delivery(monkeypatch):
    monkeypatch.setenv("AGENT_CONSULTANT_SLACK_WEBHOOK_URL", "https://hooks.slack.test/secret")
    original_client = httpx.AsyncClient
    monkeypatch.setattr(worker.httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(lambda _: httpx.Response(500)), **kwargs
    ))
    with pytest.raises(Exception, match="Slack feedback delivery failed"):
        await worker.deliver_feedback(worker.FeedbackDelivery(
            actor="jiaru@lab.example", message="Please review this"
        ))
