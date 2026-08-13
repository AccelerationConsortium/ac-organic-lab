"""Tests for the ``lab-runs`` authorized-run trigger MCP server.

The server is a thin HTTP client over the workflow executor's routes; the
executor's own gates are tested in test_workflow.py. Here we pin the trigger's
contract: actor binding fails closed on mutating tools, executor refusals are
relayed verbatim as ``{error, code:"refused"}``, and watch_run's SSE follow
filters by seq and terminates on ``done``.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app import run_trigger as rt

API = "http://127.0.0.1:8001"
ACTOR = "hermes@lab.local"
AUTH_ID = "auth_0123456789ab"


@pytest.fixture(autouse=True)
def _actor_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAB_ACTOR", ACTOR)
    monkeypatch.delenv("DASHBOARD_API_BASE", raising=False)


# ---------------------------------------------------------------------------
# start_run
# ---------------------------------------------------------------------------


@respx.mock
async def test_start_run_success() -> None:
    route = respx.post(f"{API}/api/workflow/runs").mock(
        return_value=httpx.Response(
            202,
            json={"run_id": "run_abc", "status": "running",
                  "authorization_id": AUTH_ID},
        )
    )
    out = json.loads(await rt._start_run(AUTH_ID, dry_run=False))
    assert out["run_id"] == "run_abc"
    # The bound actor rides X-Auth-User; the body carries the id + dry_run.
    req = route.calls.last.request
    assert req.headers["x-auth-user"] == ACTOR
    assert json.loads(req.content) == {"authorization_id": AUTH_ID, "dry_run": False}


@respx.mock
async def test_start_run_dry_run_flag_passes_through() -> None:
    route = respx.post(f"{API}/api/workflow/runs").mock(
        return_value=httpx.Response(
            202, json={"run_id": "run_dry", "status": "running",
                       "authorization_id": AUTH_ID}
        )
    )
    await rt._start_run(AUTH_ID, dry_run=True)
    assert json.loads(route.calls.last.request.content)["dry_run"] is True


@respx.mock
async def test_start_run_refusal_relayed_verbatim() -> None:
    """A 409 is the executor's gate speaking (revoked / digest mismatch /
    not executable); the trigger relays the reason instead of interpreting."""

    respx.post(f"{API}/api/workflow/runs").mock(
        return_value=httpx.Response(
            409, json={"detail": "authorization revoked by ycao 2026-08-12"}
        )
    )
    out = json.loads(await rt._start_run(AUTH_ID, dry_run=False))
    assert out["code"] == "refused"
    assert out["error"] == "authorization revoked by ycao 2026-08-12"
    assert out["authorization_id"] == AUTH_ID


async def test_start_run_without_actor_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No LAB_ACTOR -> refused before any HTTP happens (respx not active, so
    a request attempt would raise, proving none was made)."""

    monkeypatch.delenv("LAB_ACTOR", raising=False)
    out = json.loads(await rt._start_run(AUTH_ID, dry_run=False))
    assert out["code"] == "no_actor"


@respx.mock
async def test_start_run_api_unreachable() -> None:
    respx.post(f"{API}/api/workflow/runs").mock(
        side_effect=httpx.ConnectError("refused")
    )
    out = json.loads(await rt._start_run(AUTH_ID, dry_run=False))
    assert out["code"] == "api_unreachable"


# ---------------------------------------------------------------------------
# get_run / abort_run
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_run_snapshot() -> None:
    respx.get(f"{API}/api/workflow/runs/run_abc").mock(
        return_value=httpx.Response(
            200, json={"run_id": "run_abc", "status": "finished",
                       "launched_by": ACTOR, "events": 7,
                       "result": {"ok": True}},
        )
    )
    out = json.loads(await rt._get_run("run_abc"))
    assert out["status"] == "finished"
    assert out["result"] == {"ok": True}


@respx.mock
async def test_get_run_unknown() -> None:
    respx.get(f"{API}/api/workflow/runs/run_nope").mock(
        return_value=httpx.Response(404, json={"detail": "no run 'run_nope'"})
    )
    out = json.loads(await rt._get_run("run_nope"))
    assert out["code"] == "unknown_run"


@respx.mock
async def test_abort_run_carries_actor() -> None:
    route = respx.post(f"{API}/api/workflow/runs/run_abc/abort").mock(
        return_value=httpx.Response(
            200, json={"run_id": "run_abc", "status": "running",
                       "abort_requested": ACTOR},
        )
    )
    out = json.loads(await rt._abort_run("run_abc"))
    assert out["abort_requested"] == ACTOR
    assert route.calls.last.request.headers["x-auth-user"] == ACTOR


async def test_abort_run_without_actor_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LAB_ACTOR", raising=False)
    out = json.loads(await rt._abort_run("run_abc"))
    assert out["code"] == "no_actor"


# ---------------------------------------------------------------------------
# watch_run
# ---------------------------------------------------------------------------


def _sse(*events: dict) -> bytes:
    """Serialize events the way workflow.py's stream does (event: label +
    data: JSON), with a keepalive comment mixed in like a live stream."""

    parts = [": keepalive\n\n"]
    for ev in events:
        parts.append(f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n")
    return "".join(parts).encode()


@respx.mock
async def test_watch_run_collects_until_done() -> None:
    events = [
        {"type": "started", "seq": 0, "data": {"authorization_id": AUTH_ID}},
        {"type": "step", "seq": 1, "data": {"i": 0, "status": "ok"}},
        {"type": "done", "seq": 2, "data": {"status": "finished"}},
    ]
    respx.get(f"{API}/api/workflow/runs/run_abc/events").mock(
        return_value=httpx.Response(200, content=_sse(*events))
    )
    out = json.loads(await rt._watch_run("run_abc", after_seq=0, timeout_s=30))
    assert out["done"] is True
    assert [e["seq"] for e in out["events"]] == [0, 1, 2]
    assert out["next_seq"] == 3


@respx.mock
async def test_watch_run_after_seq_skips_replayed_prefix() -> None:
    events = [
        {"type": "started", "seq": 0, "data": {}},
        {"type": "step", "seq": 1, "data": {}},
        {"type": "step", "seq": 2, "data": {}},
    ]
    respx.get(f"{API}/api/workflow/runs/run_abc/events").mock(
        return_value=httpx.Response(200, content=_sse(*events))
    )
    out = json.loads(await rt._watch_run("run_abc", after_seq=2, timeout_s=30))
    # The stream replays from seq 0; the caller's cursor filters it out.
    assert [e["seq"] for e in out["events"]] == [2]
    assert out["done"] is False  # stream ended without done
    assert out["next_seq"] == 3


@respx.mock
async def test_watch_run_no_new_events_keeps_cursor() -> None:
    respx.get(f"{API}/api/workflow/runs/run_abc/events").mock(
        return_value=httpx.Response(200, content=b": keepalive\n\n")
    )
    out = json.loads(await rt._watch_run("run_abc", after_seq=5, timeout_s=30))
    assert out["events"] == []
    assert out["next_seq"] == 5  # never goes backwards, never invents progress


@respx.mock
async def test_watch_run_unknown_run() -> None:
    respx.get(f"{API}/api/workflow/runs/run_nope/events").mock(
        return_value=httpx.Response(404, json={"detail": "no run"})
    )
    out = json.loads(await rt._watch_run("run_nope", after_seq=0, timeout_s=30))
    assert out["code"] == "unknown_run"


def test_watch_timeout_clamped() -> None:
    assert rt._clamp_timeout(0) == rt.WATCH_TIMEOUT_MIN_S
    assert rt._clamp_timeout(10_000) == rt.WATCH_TIMEOUT_MAX_S
    assert rt._clamp_timeout(60) == 60.0


# ---------------------------------------------------------------------------
# config seams
# ---------------------------------------------------------------------------


@respx.mock
async def test_api_base_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_API_BASE", "http://api.test:9999/")
    respx.get("http://api.test:9999/api/workflow/runs/run_abc").mock(
        return_value=httpx.Response(200, json={"run_id": "run_abc",
                                               "status": "running"})
    )
    out = json.loads(await rt._get_run("run_abc"))
    assert out["status"] == "running"
