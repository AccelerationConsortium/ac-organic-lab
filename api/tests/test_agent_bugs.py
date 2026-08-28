"""Tests for the /api/agent/bugs agent→Hermes error bridge.

Mocks the ac_auth sidecar with respx (like test_control.py) and stubs the
Hermes relay by monkeypatching ``_run_hermes`` so no real subprocess runs.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_bugs import build_agent_bugs_router

AUTHZ = "http://127.0.0.1:8009/auth/verify"


@pytest.fixture
def env(monkeypatch):
    # Keep auth enforced; the sidecar URL is the default.
    monkeypatch.delenv("AGENT_BUGS_AUTHZ_ENFORCE", raising=False)
    monkeypatch.setenv("AGENT_BUGS_HERMES_TIMEOUT_S", "1")
    return monkeypatch


def _app() -> FastAPI:
    a = FastAPI()
    a.include_router(build_agent_bugs_router())
    return a


def test_unauthenticated_is_401(env):
    with respx.mock:
        respx.get(AUTHZ).mock(return_value=httpx.Response(401))
        with TestClient(_app()) as c:
            r = c.post(
                "/api/agent/bugs",
                json={"endpoint": "/api/openapi.json", "status": 500},
            )
    assert r.status_code == 401


def test_missing_api_key_is_401(env):
    with respx.mock:
        with TestClient(_app()) as c:
            r = c.post(
                "/api/agent/bugs",
                json={"endpoint": "/x", "status": 500},
            )
    assert r.status_code == 401


def test_valid_key_relays_and_returns_diagnosis(env, monkeypatch):
    async def fake_run(prompt: str) -> str:
        return f"DIAGNOSED: root cause is in {prompt.split(chr(10))[3] if len(prompt.split(chr(10)))>3 else '?'}"

    monkeypatch.setattr("app.agent_bugs._run_hermes", fake_run)

    with respx.mock:
        respx.get(AUTHZ).mock(
            return_value=httpx.Response(200, headers={"X-Auth-User": "jiaru@lab.local"})
        )
        with TestClient(_app()) as c:
            r = c.post(
                "/api/agent/bugs",
                json={
                    "endpoint": "/api/openapi.json",
                    "status": 500,
                    "reason": "Internal Server Error",
                    "body": "Internal Server Error",
                    "context": "Jiaru hit the API Reference page",
                },
                headers={"X-Api-Key": "live-key"},
            )
    assert r.status_code == 200
    body = r.json()
    assert body["endpoint"] == "/api/openapi.json"
    assert body["actor"] == "jiaru@lab.local"
    assert "DIAGNOSED" in body["diagnosis"]


def test_valid_key_but_hermes_times_out(env, monkeypatch):
    import asyncio
    from fastapi import HTTPException

    async def slow_run(prompt: str) -> str:
        raise HTTPException(504, "Hermes diagnosis timed out")

    monkeypatch.setattr("app.agent_bugs._run_hermes", slow_run)
    with respx.mock:
        respx.get(AUTHZ).mock(
            return_value=httpx.Response(200, headers={"X-Auth-User": "jiaru@lab.local"})
        )
        with TestClient(_app()) as c:
            r = c.post(
                "/api/agent/bugs",
                json={"endpoint": "/x", "status": 500},
                headers={"X-Api-Key": "key"},
            )
    assert r.status_code == 504


def test_authz_disabled_bypasses_sidecar(env):
    env.setenv("AGENT_BUGS_AUTHZ_ENFORCE", "false")

    async def fake_run(prompt: str) -> str:
        return "diagnosis from local-dev"

    env.setattr("app.agent_bugs._run_hermes", fake_run)

    with TestClient(_app()) as c:
        r = c.post(
            "/api/agent/bugs",
            json={"endpoint": "/x", "status": 500},
        )
    assert r.status_code == 200
    assert r.json()["actor"] == "local-dev"
