"""Plan mode — saved planning sessions (ASSISTANT_PERSISTENCE.md step 2).

The properties the design asks step 2 to prove: ownership isolation (owner
full, admin read-only, everyone else 404), idempotent turn retries that never
rerun tools, one active turn per session, truthful interrupted/failed states
across disconnects and restarts, bounded retention, and inert restore — a
saved conversation grants no execution authority and never claims a protocol
was filed.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app import assistant, assistant_openai, assistant_sessions
from app.assistant_sessions import AssistantSessionStore

ALICE = {"X-Auth-User": "alice@example.edu", "X-Auth-Role": "operator"}
BOB = {"X-Auth-User": "bob@example.edu", "X-Auth-Role": "operator"}
ADMIN = {"X-Auth-User": "root@example.edu", "X-Auth-Role": "admin"}


def _app(tmp_path, *, with_store: bool = True) -> tuple[FastAPI, AssistantSessionStore | None]:
    app = FastAPI()
    app.include_router(assistant.build_assistant_router())
    app.include_router(assistant_sessions.build_assistant_sessions_router())
    store: AssistantSessionStore | None = None
    if with_store:
        store = AssistantSessionStore(tmp_path / "assistant.db")
        store.open()
    app.state.assistant_sessions = store
    return app, store


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _frames(body: bytes) -> list[dict[str, Any]]:
    out = []
    for chunk in body.split(b"\n\n"):
        for line in chunk.split(b"\n"):
            if line.startswith(b"data:"):
                out.append(json.loads(line[5:].strip()))
    return out


def _use_fake_runner(monkeypatch, fake) -> None:
    monkeypatch.setattr(assistant, "DEFAULT_BACKEND", "openai")
    monkeypatch.setenv("ASSISTANT_OPENAI_API_KEY", "test-only")
    monkeypatch.setattr(assistant_openai, "run_openai_turn", fake)


def _answering_runner(calls: list[dict[str, Any]], *, reply: str = "Step 1: home the arm."):
    """A fake engine that records what it was asked and answers like Ask does."""

    async def fake(messages, *, control=False, actor=None, on_proposal=None, on_plan=None, extra_system_prompt=None):
        calls.append(
            {
                "messages": [(m.role, m.content) for m in messages],
                "control": control,
                "actor": actor,
                "extra": extra_system_prompt,
                "on_proposal": on_proposal,
                "on_plan": on_plan,
            }
        )
        yield assistant._sse({"type": "status", "phase": "thinking"})
        yield assistant._sse({"type": "tool_use", "name": "get_equipment_status"})
        yield assistant._sse({"type": "tool_result", "name": "get_equipment_status"})
        yield assistant._sse({"type": "text", "delta": reply[:6]})
        yield assistant._sse({"type": "text", "delta": reply[6:]})
        yield assistant._sse({"type": "done"})

    return fake


async def _create(client, headers, title="Solubility prep", seed=None) -> dict[str, Any]:
    r = await client.post(
        "/api/assistant/sessions", headers=headers, json={"title": title, "seed": seed or []}
    )
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Identity + ownership
# ---------------------------------------------------------------------------


async def test_saved_sessions_require_a_verified_identity(tmp_path):
    app, _ = _app(tmp_path)
    async with _client(app) as client:
        assert (await client.get("/api/assistant/sessions")).status_code == 401
        r = await client.post("/api/assistant/sessions", json={"title": "x"})
        assert r.status_code == 401
        # Under the dev bypass there is still no identity to own a session.
        r = await client.post("/api/assistant/sessions", json={"title": "x"}, headers={"X-Auth-Role": "admin"})
        assert r.status_code == 401


async def test_owner_isolation_and_admin_read_only(tmp_path, monkeypatch):
    calls: list[dict[str, Any]] = []
    _use_fake_runner(monkeypatch, _answering_runner(calls))
    app, _ = _app(tmp_path)
    async with _client(app) as client:
        session = await _create(client, ALICE)
        sid = session["id"]
        assert session["owner"] == ALICE["X-Auth-User"]

        # Bob sees nothing, and cannot tell the id exists.
        assert (await client.get("/api/assistant/sessions", headers=BOB)).json()["sessions"] == []
        assert (await client.get(f"/api/assistant/sessions/{sid}", headers=BOB)).status_code == 404
        r = await client.patch(f"/api/assistant/sessions/{sid}", headers=BOB, json={"title": "mine", "revision": 1})
        assert r.status_code == 404
        assert (await client.delete(f"/api/assistant/sessions/{sid}", headers=BOB)).status_code == 404
        r = await client.post(f"/api/assistant/sessions/{sid}/turns", headers=BOB, json={"request_id": "req-bob-0001", "text": "hi"})
        assert r.status_code == 404
        assert (await client.get("/api/assistant/sessions", headers=BOB, params={"scope": "all"})).status_code == 403

        # A global admin may read — list everyone's, open, export — but never act as the owner.
        listing = (await client.get("/api/assistant/sessions", headers=ADMIN, params={"scope": "all"})).json()
        assert [s["id"] for s in listing["sessions"]] == [sid]
        opened = await client.get(f"/api/assistant/sessions/{sid}", headers=ADMIN)
        assert opened.status_code == 200 and opened.json()["read_only"] is True
        assert (await client.get(f"/api/assistant/sessions/{sid}/export", headers=ADMIN)).status_code == 200
        r = await client.patch(f"/api/assistant/sessions/{sid}", headers=ADMIN, json={"title": "x", "revision": 1})
        assert r.status_code == 403
        assert (await client.delete(f"/api/assistant/sessions/{sid}", headers=ADMIN)).status_code == 403
        r = await client.post(f"/api/assistant/sessions/{sid}/turns", headers=ADMIN, json={"request_id": "req-admin-001", "text": "hi"})
        assert r.status_code == 403
        assert calls == []  # no engine run on anyone's behalf

        # The owner's own view is writable.
        mine = (await client.get(f"/api/assistant/sessions/{sid}", headers=ALICE)).json()
        assert mine["read_only"] is False and mine["messages"] == []


# ---------------------------------------------------------------------------
# Turns: context is server-built, tools are Ask's, retries replay
# ---------------------------------------------------------------------------


async def test_turn_runs_on_the_ask_toolset_and_rebuilds_context_from_storage(tmp_path, monkeypatch):
    calls: list[dict[str, Any]] = []
    _use_fake_runner(monkeypatch, _answering_runner(calls))
    app, _ = _app(tmp_path)
    async with _client(app) as client:
        sid = (await _create(client, ALICE))["id"]
        r1 = await client.post(f"/api/assistant/sessions/{sid}/turns", headers=ALICE, json={"request_id": "req-0000001", "text": "Draft a plate-sealing protocol"})
        assert r1.status_code == 200
        f1 = _frames(r1.content)
        assert f1[0]["type"] == "session" and f1[0]["session_id"] == sid and "turn_id" in f1[0]
        assert f1[-1]["type"] == "done"
        assert "".join(f["delta"] for f in f1 if f["type"] == "text") == "Step 1: home the arm."

        r2 = await client.post(f"/api/assistant/sessions/{sid}/turns", headers=ALICE, json={"request_id": "req-0000002", "text": "Add a cooling step"})
        assert r2.status_code == 200

        # Plan never registers lab-control, and the Plan addendum rides along.
        for call in calls:
            assert call["control"] is False
            assert call["actor"] == ALICE["X-Auth-User"]
            assert "PLAN MODE IS ACTIVE" in call["extra"]
            assert call["on_proposal"] is None and call["on_plan"] is None
        # The second turn's context came from storage: the stored first turn plus the new message.
        assert calls[1]["messages"] == [
            ("user", "Draft a plate-sealing protocol"),
            ("assistant", "Step 1: home the arm."),
            ("user", "Add a cooling step"),
        ]

        opened = (await client.get(f"/api/assistant/sessions/{sid}", headers=ALICE)).json()
        msgs = opened["messages"]
        assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
        assert all(m["state"] == "completed" for m in msgs)
        assert msgs[1]["events"] == [{"type": "tool", "name": "get_equipment_status", "ok": True}]
        assert msgs[1]["mode"] == "plan" and msgs[1]["imported"] is False
        assert opened["session"]["message_count"] == 4 and opened["session"]["active_turn"] is False


async def test_retrying_a_request_id_replays_the_stored_turn_without_rerunning(tmp_path, monkeypatch):
    calls: list[dict[str, Any]] = []
    _use_fake_runner(monkeypatch, _answering_runner(calls))
    app, _ = _app(tmp_path)
    async with _client(app) as client:
        sid = (await _create(client, ALICE))["id"]
        body = {"request_id": "req-retry-0001", "text": "Draft it"}
        first = _frames((await client.post(f"/api/assistant/sessions/{sid}/turns", headers=ALICE, json=body)).content)
        second = _frames((await client.post(f"/api/assistant/sessions/{sid}/turns", headers=ALICE, json=body)).content)
        assert len(calls) == 1
        assert second[0] == {"type": "session", "session_id": sid, "turn_id": first[0]["turn_id"], "replayed": True}
        assert [f["type"] for f in second[1:]] == ["tool_use", "tool_result", "text", "done"]
        assert second[-2]["delta"] == "Step 1: home the arm."
        opened = (await client.get(f"/api/assistant/sessions/{sid}", headers=ALICE)).json()
        assert opened["session"]["message_count"] == 2


async def test_one_active_turn_per_session(tmp_path, monkeypatch):
    release = asyncio.Event()

    async def slow(messages, **kwargs):
        yield assistant._sse({"type": "status", "phase": "thinking"})
        await release.wait()
        yield assistant._sse({"type": "text", "delta": "late answer"})
        yield assistant._sse({"type": "done"})

    _use_fake_runner(monkeypatch, slow)
    app, _ = _app(tmp_path)
    async with _client(app) as client:
        sid = (await _create(client, ALICE))["id"]
        first = asyncio.create_task(
            client.post(f"/api/assistant/sessions/{sid}/turns", headers=ALICE, json={"request_id": "req-slow-0001", "text": "one"})
        )
        # Let the first request reach the engine and block there.
        for _ in range(50):
            await asyncio.sleep(0.01)
            if (await client.get(f"/api/assistant/sessions/{sid}", headers=ALICE)).json()["session"]["active_turn"]:
                break
        else:
            pytest.fail("first turn never became active")
        second = await client.post(f"/api/assistant/sessions/{sid}/turns", headers=ALICE, json={"request_id": "req-slow-0002", "text": "two"})
        assert second.status_code == 409
        assert "already running" in second.json()["detail"]
        release.set()
        r = await first
        assert _frames(r.content)[-1]["type"] == "done"
        opened = (await client.get(f"/api/assistant/sessions/{sid}", headers=ALICE)).json()
        assert opened["session"]["active_turn"] is False
        assert [m["text"] for m in opened["messages"]] == ["one", "late answer"]


# ---------------------------------------------------------------------------
# Truthful terminal states
# ---------------------------------------------------------------------------


async def test_engine_ending_without_a_terminal_frame_is_interrupted_not_completed(tmp_path, monkeypatch):
    async def cut_off(messages, **kwargs):
        yield assistant._sse({"type": "text", "delta": "half an ans"})

    _use_fake_runner(monkeypatch, cut_off)
    app, _ = _app(tmp_path)
    async with _client(app) as client:
        sid = (await _create(client, ALICE))["id"]
        r = await client.post(f"/api/assistant/sessions/{sid}/turns", headers=ALICE, json={"request_id": "req-cut-00001", "text": "go"})
        assert _frames(r.content)[-1] == {"type": "interrupted"}
        msgs = (await client.get(f"/api/assistant/sessions/{sid}", headers=ALICE)).json()["messages"]
        assert msgs[1]["state"] == "interrupted" and msgs[1]["text"] == "half an ans"
        # The replay of that request id says interrupted too — never done.
        again = _frames((await client.post(f"/api/assistant/sessions/{sid}/turns", headers=ALICE, json={"request_id": "req-cut-00001", "text": "go"})).content)
        assert again[-1] == {"type": "interrupted"}


async def test_engine_error_records_failed_state(tmp_path, monkeypatch):
    async def broken(messages, **kwargs):
        yield assistant._sse({"type": "error", "message": "provider unavailable"})

    _use_fake_runner(monkeypatch, broken)
    app, _ = _app(tmp_path)
    async with _client(app) as client:
        sid = (await _create(client, ALICE))["id"]
        await client.post(f"/api/assistant/sessions/{sid}/turns", headers=ALICE, json={"request_id": "req-err-00001", "text": "go"})
        msgs = (await client.get(f"/api/assistant/sessions/{sid}", headers=ALICE)).json()["messages"]
        assert msgs[1]["state"] == "failed" and msgs[1]["error"] == "provider unavailable"


def test_restart_marks_running_turns_interrupted_and_unlocks(tmp_path):
    store = AssistantSessionStore(tmp_path / "a.db")
    store.open()
    sid = store.create_session("alice@example.edu", "t")["id"]
    turn, created = store.begin_turn(sid, "req-x-0000001", "hello")
    assert created and store.get_session(sid)["active_turn"] is True
    store.close()  # the service restarts mid-answer

    reopened = AssistantSessionStore(tmp_path / "a.db")
    reopened.open()
    assert reopened.get_session(sid)["active_turn"] is False
    answer = reopened.get_turn(sid, turn["turn_id"])["assistant"]
    assert answer["state"] == "interrupted" and answer["text"] == ""
    # And a new turn can start.
    _, created = reopened.begin_turn(sid, "req-x-0000002", "again")
    assert created


def test_stale_running_turn_is_reclaimed(tmp_path, monkeypatch):
    store = AssistantSessionStore(tmp_path / "a.db")
    store.open()
    sid = store.create_session("alice@example.edu", "t")["id"]
    turn, _ = store.begin_turn(sid, "req-s-0000001", "one")
    with pytest.raises(assistant_sessions.TurnInProgress):
        store.begin_turn(sid, "req-s-0000002", "two")
    # Age the active turn past the stale window (a client that vanished).
    store.conn.execute(
        "UPDATE sessions SET active_turn_started_at='2000-01-01T00:00:00.000000+00:00' WHERE id=?", (sid,)
    )
    _, created = store.begin_turn(sid, "req-s-0000002", "two")
    assert created
    assert store.get_turn(sid, turn["turn_id"])["assistant"]["state"] == "interrupted"


# ---------------------------------------------------------------------------
# Rename / delete / concurrency / retention
# ---------------------------------------------------------------------------


async def test_rename_uses_revisions_and_delete_removes(tmp_path):
    app, _ = _app(tmp_path)
    async with _client(app) as client:
        session = await _create(client, ALICE, title="Draft")
        sid, rev = session["id"], session["revision"]
        ok = await client.patch(f"/api/assistant/sessions/{sid}", headers=ALICE, json={"title": "Sealing v2", "revision": rev})
        assert ok.status_code == 200 and ok.json()["title"] == "Sealing v2" and ok.json()["revision"] == rev + 1
        stale = await client.patch(f"/api/assistant/sessions/{sid}", headers=ALICE, json={"title": "Sealing v3", "revision": rev})
        assert stale.status_code == 409 and "another tab" in stale.json()["detail"]
        assert (await client.delete(f"/api/assistant/sessions/{sid}", headers=ALICE)).status_code == 204
        assert (await client.get(f"/api/assistant/sessions/{sid}", headers=ALICE)).status_code == 404
        assert (await client.get("/api/assistant/sessions", headers=ALICE)).json()["sessions"] == []


async def test_retention_and_caps_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(assistant_sessions, "MAX_SESSIONS_PER_OWNER", 1)
    monkeypatch.setattr(assistant_sessions, "MAX_MESSAGES_PER_SESSION", 2)
    monkeypatch.setattr(assistant_sessions, "RETENTION_DAYS", 30)
    calls: list[dict[str, Any]] = []
    _use_fake_runner(monkeypatch, _answering_runner(calls))
    app, store = _app(tmp_path)
    assert store is not None
    async with _client(app) as client:
        sid = (await _create(client, ALICE))["id"]
        full = await client.post("/api/assistant/sessions", headers=ALICE, json={"title": "second"})
        assert full.status_code == 409 and "delete one" in full.json()["detail"]
        # Bob's quota is his own.
        assert (await client.post("/api/assistant/sessions", headers=BOB, json={"title": "bob's"})).status_code == 201

        r = await client.post(f"/api/assistant/sessions/{sid}/turns", headers=ALICE, json={"request_id": "req-cap-00001", "text": "one"})
        assert r.status_code == 200
        r = await client.post(f"/api/assistant/sessions/{sid}/turns", headers=ALICE, json={"request_id": "req-cap-00002", "text": "two"})
        assert r.status_code == 409 and "maximum" in r.json()["detail"]

        # Age Alice's session past retention; the next sweep purges it and its messages.
        store.conn.execute("UPDATE sessions SET updated_at='2000-01-01T00:00:00.000000+00:00' WHERE id=?", (sid,))
        assert store.sweep() == 1
        assert store.get_session(sid) is None
        assert store.conn.execute("SELECT COUNT(*) FROM messages WHERE session_id=?", (sid,)).fetchone()[0] == 0
        assert (await client.post("/api/assistant/sessions", headers=ALICE, json={"title": "room again"})).status_code == 201


# ---------------------------------------------------------------------------
# Inert restore + export grants nothing
# ---------------------------------------------------------------------------


async def test_seeded_history_restores_inertly_and_export_is_not_executable(tmp_path):
    app, _ = _app(tmp_path)
    seed = [
        {"role": "user", "text": "stage the plate", "mode": "control"},
        {
            "role": "assistant",
            "text": "Proposed move.uplc_draw_home.",
            "mode": "control",
            "events": [
                {"type": "tool", "name": "propose_action", "ok": True},
                {"type": "control", "event": "action_proposed", "detail": {"action": "move.uplc_draw_home"}},
                {"type": "control", "event": "action_response", "detail": {"outcome": "ok"}},
            ],
        },
    ]
    async with _client(app) as client:
        session = await _create(client, ALICE, title="Carried over", seed=seed)
        assert session["message_count"] == 2
        opened = (await client.get(f"/api/assistant/sessions/{session['id']}", headers=ALICE)).json()
        msgs = opened["messages"]
        assert [m["imported"] for m in msgs] == [True, True]
        assert [m["mode"] for m in msgs] == ["control", "control"]
        assert msgs[1]["events"][1]["event"] == "action_proposed"  # display-only history
        assert msgs[1]["state"] == "completed"

        export = await client.get(f"/api/assistant/sessions/{session['id']}/export", headers=ALICE, params={"format": "json"})
        assert export.status_code == 200
        assert export.headers["content-disposition"].startswith('attachment; filename="plan-Carried-over-')
        data = export.json()
        assert data["executable"] is False
        assert data["record_type"] == "planning_session_export"
        assert "not a registered Plan" in data["notice"] and "files nothing" in data["notice"]
        assert data["session"]["owner"] == ALICE["X-Auth-User"]
        assert [m["imported"] for m in data["messages"]] == [True, True]

        md = await client.get(f"/api/assistant/sessions/{session['id']}/export", headers=ALICE, params={"format": "md"})
        assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
        assert md.text.startswith("# Planning session: Carried over")
        assert "not a registered Plan" in md.text
        assert "control; imported; completed" in md.text


async def test_missing_store_disables_plan_but_not_the_rest(tmp_path):
    app, _ = _app(tmp_path, with_store=False)
    async with _client(app) as client:
        assert (await client.get("/api/assistant/sessions", headers=ALICE)).status_code == 503
        health = (await client.get("/api/assistant/health")).json()
        assert health["saved_sessions"] is False


async def test_health_reports_saved_sessions_available(tmp_path):
    app, _ = _app(tmp_path)
    async with _client(app) as client:
        assert (await client.get("/api/assistant/health")).json()["saved_sessions"] is True


def test_store_is_separate_from_lab_db_and_migrates_once(tmp_path):
    store = AssistantSessionStore(tmp_path / "assistant.db")
    store.open()
    assert store.conn.execute("PRAGMA user_version").fetchone()[0] == len(assistant_sessions._MIGRATIONS)
    assert store.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    tables = {r[0] for r in store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"sessions", "messages", "turn_requests"} <= tables
    assert "equipment_events" not in tables  # never lab.db's schema
    store.close()
    again = AssistantSessionStore(tmp_path / "assistant.db")
    again.open()  # idempotent — no re-migration error
    again.close()


def test_default_path_is_a_sibling_of_lab_db(monkeypatch, tmp_path):
    monkeypatch.delenv("ASSISTANT_DB_PATH", raising=False)
    monkeypatch.setenv("LAB_DB_PATH", str(tmp_path / "somewhere" / "lab.db"))
    assert assistant_sessions.resolve_sessions_db_path() == tmp_path / "somewhere" / "assistant.db"
    monkeypatch.setenv("ASSISTANT_DB_PATH", str(tmp_path / "elsewhere.db"))
    assert assistant_sessions.resolve_sessions_db_path() == tmp_path / "elsewhere.db"


def test_sqlite_row_factory_survives_direct_sql(tmp_path):
    # Guard for the tests above that poke at the connection directly.
    store = AssistantSessionStore(tmp_path / "a.db")
    store.open()
    assert isinstance(store.conn.execute("SELECT 1 AS one").fetchone(), sqlite3.Row)
