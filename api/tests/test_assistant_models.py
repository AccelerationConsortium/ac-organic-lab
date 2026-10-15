"""The main bubble's model picker: ASSISTANT_MODELS / ASSISTANT_CONTROL_MODELS.

A chosen model must be one the server offers for that mode; anything else is
refused, never quietly answered by the default.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from app import assistant
from lab_assistant import device_chat, hermes_backend, openai_backend

ALICE = "alice@example.edu"


@pytest.fixture
def calls(monkeypatch):
    seen: list[tuple[str, dict]] = []

    def runner(name):
        async def turn(messages, **kwargs):
            seen.append((name, kwargs))
            yield assistant._sse({"type": "done"})
        return turn

    monkeypatch.setattr(assistant, "DEFAULT_BACKEND", "hermes")
    monkeypatch.setattr(assistant, "CONTROL_BACKEND", "hermes")
    monkeypatch.setattr(hermes_backend, "MODEL", "deepseek/deepseek-v4.1-flash")
    monkeypatch.setattr(hermes_backend, "configured", lambda: True)
    monkeypatch.setattr(hermes_backend, "run_hermes_turn", runner("hermes"))
    monkeypatch.setattr(openai_backend, "run_openai_turn", runner("openai"))
    monkeypatch.setattr(assistant, "_run_claude", runner("claude-cli"))
    monkeypatch.setattr(assistant, "_claude_binary", lambda: "/usr/bin/claude")
    monkeypatch.setenv("ASSISTANT_OPENAI_API_KEY", "test-only")
    monkeypatch.delenv("ASSISTANT_MODELS", raising=False)
    monkeypatch.delenv("ASSISTANT_CONTROL_MODELS", raising=False)
    monkeypatch.delenv("DASHBOARD_CONTROL_OPEN", raising=False)

    async def claude_ready(binary):
        return None

    monkeypatch.setattr(device_chat, "claude_unavailable_reason", claude_ready)
    return seen


def _client() -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(assistant.build_assistant_router())
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"X-Auth-User": ALICE})


async def _chat(c: httpx.AsyncClient, **body) -> httpx.Response:
    return await c.post("/api/assistant/chat", json={
        "conversation_owner": ALICE, "messages": [{"role": "user", "content": "hi"}], **body})


async def test_unset_offers_only_todays_default_and_no_model_behaves_as_before(calls):
    async with _client() as c:
        health = (await c.get("/api/assistant/health")).json()
        assert health["models"] == ["deepseek/deepseek-v4.1-flash"]
        assert health["control_models"] == ["deepseek/deepseek-v4.1-flash"]
        assert (await _chat(c)).status_code == 200
        # Naming the default is the same turn: the runner chooses it itself.
        assert (await _chat(c, model="deepseek/deepseek-v4.1-flash")).status_code == 200
    assert calls == [("hermes", {"control": False, "actor": ALICE, "on_proposal": None, "on_plan": None})] * 2


async def test_a_listed_model_reaches_its_own_backend(calls, monkeypatch):
    monkeypatch.setenv("ASSISTANT_MODELS", "qwen/qwen3-max, openai:openai/gpt-5.5, claude-cli:sonnet")
    async with _client() as c:
        health = (await c.get("/api/assistant/health")).json()
        assert health["models"] == ["deepseek/deepseek-v4.1-flash", "qwen/qwen3-max",
                                    "openai:openai/gpt-5.5", "claude-cli:sonnet"]
        for model in ("qwen/qwen3-max", "openai:openai/gpt-5.5", "claude-cli:sonnet"):
            assert (await _chat(c, model=model)).status_code == 200
    assert [(name, kw["model"]) for name, kw in calls] == [
        ("hermes", "qwen/qwen3-max"), ("openai", "openai/gpt-5.5"), ("claude-cli", "sonnet")]


async def test_an_unknown_model_is_refused_not_replaced_by_the_default(calls, monkeypatch):
    monkeypatch.setenv("ASSISTANT_MODELS", "qwen/qwen3-max")
    async with _client() as c:
        r = await _chat(c, model="anthropic/claude-opus-5-5")
    assert r.status_code == 422
    assert "is not offered" in r.json()["detail"] and "qwen/qwen3-max" in r.json()["detail"]
    assert calls == []


async def test_control_has_its_own_list(calls, monkeypatch):
    monkeypatch.setenv("ASSISTANT_MODELS", "qwen/qwen3-max")
    monkeypatch.setenv("ASSISTANT_CONTROL_MODELS", "claude-cli:opus")
    async with _client() as c:
        assert (await _chat(c, mode="control", model="qwen/qwen3-max")).status_code == 422
        assert (await _chat(c, mode="control", model="claude-cli:opus")).status_code == 200
    assert calls[-1][0] == "claude-cli"
    assert calls[-1][1]["model"] == "opus" and calls[-1][1]["control"] is True


async def test_claude_is_offered_only_while_it_can_answer_from_this_host(calls, monkeypatch):
    async def unreachable(binary):
        return "cannot reach api.anthropic.com:443 via http://127.0.0.1:3128"

    monkeypatch.setattr(device_chat, "claude_unavailable_reason", unreachable)
    monkeypatch.setenv("ASSISTANT_MODELS", "claude-cli:sonnet,qwen/qwen3-max")
    async with _client() as c:
        health = (await c.get("/api/assistant/health")).json()
        assert health["models"] == ["deepseek/deepseek-v4.1-flash", "qwen/qwen3-max"]
        assert any("claude-cli:sonnet: cannot reach" in m for m in health["models_unavailable"])
        r = await _chat(c, model="claude-cli:sonnet")
    assert r.status_code == 422 and "cannot reach" in r.json()["detail"]
    assert calls == []
