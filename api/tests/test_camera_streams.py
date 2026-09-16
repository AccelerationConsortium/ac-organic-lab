"""No hardware I/O: admission, identity, revocation and proxy lifecycle."""

import asyncio
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import camera_streams as cs
from app import workflow


@pytest.fixture
def clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(cs.time, "monotonic", lambda: now[0])
    return now


def mint(b, user="alice", stream="cam_main", **kwargs):
    return b.mint(user, "cam", cs.SessionIn(stream=stream, **kwargs), False)


def test_same_feed_two_tabs_share_source_but_reserve_two_viewers():
    b = cs.ViewingBroker()
    a, other = mint(b), mint(b)
    assert a.id != other.id and len(b.sessions) == 2
    with pytest.raises(HTTPException) as e:
        mint(b, stream="cam_tele")
    assert e.value.status_code == 429
    b.end(a.id, "tab closed")
    assert a.stopped.is_set() and other.id in b.sessions


def test_two_feed_override_keeps_original_bandwidth_budget(monkeypatch):
    monkeypatch.setenv("CAMERA_MAX_SOURCES", "2")
    monkeypatch.setenv("CAMERA_NETWORK_BUDGET_MBPS", "6")
    monkeypatch.setenv("CAMERA_FEED_BUDGET_MBPS", "1.5")
    b = cs.ViewingBroker()
    mint(b)
    second = mint(b, user="bob", stream="cam_tele")
    with pytest.raises(HTTPException) as exc:
        mint(b, user="charlie")
    assert exc.value.status_code == 429
    b.end(second.id, "tab closed")
    mint(b, user="bob")
    mint(b, user="charlie")
    assert len(b.sessions) == 3
    with pytest.raises(HTTPException) as exc:
        mint(b, user="dana", stream="cam_tele")
    assert exc.value.status_code == 429


def test_two_feed_override_still_refuses_third_source(monkeypatch):
    monkeypatch.setenv("CAMERA_MAX_SOURCES", "2")
    monkeypatch.setenv("CAMERA_NETWORK_BUDGET_MBPS", "30")
    b = cs.ViewingBroker()
    mint(b)
    mint(b, stream="cam_tele")
    with pytest.raises(HTTPException) as exc:
        mint(b, stream="cam_other")
    assert exc.value.status_code == 429


def test_ticket_single_use_and_expiry(clock):
    b = cs.ViewingBroker()
    s = mint(b)
    ticket = s.ticket
    assert b.redeem(ticket) is s
    with pytest.raises(HTTPException):
        b.redeem(ticket)
    unused = mint(b)
    clock[0] += 31
    with pytest.raises(HTTPException):
        b.redeem(unused.ticket)
    assert s.id in b.sessions and unused.id not in b.sessions
    clock[0] += 30
    b.reap()
    assert not b.sessions and s.stopped.is_set()


def test_global_account_and_budget_limits(monkeypatch):
    monkeypatch.setenv("CAMERA_MAX_VIEWERS_PER_USER", "1")
    b = cs.ViewingBroker()
    mint(b)
    with pytest.raises(HTTPException):
        mint(b)
    mint(b, user="bob")
    monkeypatch.setenv("CAMERA_NETWORK_BUDGET_MBPS", "2")
    with pytest.raises(HTTPException):
        mint(cs.ViewingBroker())
    monkeypatch.setenv("CAMERA_NETWORK_BUDGET_MBPS", "20")
    monkeypatch.setenv("CAMERA_MAX_VIEWERS", "1")
    b = cs.ViewingBroker()
    mint(b)
    with pytest.raises(HTTPException):
        mint(b, user="bob")


def test_agent_requires_explicit_camera_and_run_approval(monkeypatch):
    b = cs.ViewingBroker()
    with pytest.raises(HTTPException):
        b.mint("agent", "cam", cs.SessionIn(stream="cam_main"), True)
    run = SimpleNamespace(status="running", abort_requested=None)
    monkeypatch.setitem(workflow._RUNS, "approved-run", run)
    g = cs.Grant(
        "g",
        "agent",
        ["cam_main"],
        "watch transfer",
        time.monotonic() + 3600,
        "approved-run",
        "admin",
    )
    b.grants[g.id] = g
    s = b.mint("agent", "cam", cs.SessionIn(stream="cam_main", grant_id="g"), True)
    assert s.mode == "monitoring"
    with pytest.raises(HTTPException):
        mint(b, user="intruder", grant_id="g")
    with pytest.raises(HTTPException):
        mint(b, user="agent", stream="cam_tele", grant_id="g")
    run.status = "finished"
    b.reap()
    assert not b.sessions and not b.grants


@pytest.fixture
def client():
    def auth(req):
        if req.url.host == "gibbie":
            return httpx.Response(
                200,
                headers={"content-type": "multipart/x-mixed-replace; boundary=frame"},
                content=b"--frame\r\nContent-Type: image/jpeg\r\n\r\nJPEG\r\n",
            )
        if req.url.path == "/auth/verify":
            cookie = req.headers.get("cookie", "")
            who = (
                "admin"
                if "admin" in cookie
                else "alice"
                if "alice" in cookie
                else "bob"
                if "bob" in cookie
                else ""
            )
            if not who and req.headers.get("x-api-key"):
                who = "agent"
            if not who:
                return httpx.Response(401)
            return httpx.Response(
                200,
                headers={
                    "x-auth-user": who,
                    "x-auth-role": "admin" if who == "admin" else "operator",
                },
            )
        return httpx.Response(
            200,
            json={
                "allowed": req.url.params.get("user") != "bob",
                "central_role": "automation"
                if req.url.params.get("user") == "agent"
                else "operator",
            },
        )

    app = FastAPI()
    app.state.camera_viewing = cs.ViewingBroker()
    app.state.control_client = httpx.AsyncClient(transport=httpx.MockTransport(auth))
    app.state.registry = SimpleNamespace(
        equipment=[
            SimpleNamespace(
                id="cam",
                kind="camera",
                enabled=True,
                base_url="http://gibbie:8070",
                camera=SimpleNamespace(
                    transport="go2rtc",
                    lenses=[SimpleNamespace(id="main", stream_path=None)],
                ),
            )
        ]
    )
    app.include_router(cs.build_camera_streams_router())
    with TestClient(app) as c:
        yield c


def test_identity_camera_scope_and_no_raw_url(client):
    url = "/api/camera-streams/sessions"
    assert (
        client.post(
            url,
            json={"stream": "cam_main"},
            headers={"x-auth-user": "admin", "x-auth-role": "admin"},
        ).status_code
        == 401
    )
    assert (
        client.post(url, json={"stream": "cam_main"}, headers={"cookie": "bob"}).status_code == 403
    )
    assert (
        client.post(url, json={"stream": "http://evil/"}, headers={"cookie": "alice"}).status_code
        == 422
    )
    assert (
        client.post(url, json={"stream": "unknown"}, headers={"cookie": "alice"}).status_code == 404
    )
    assert (
        client.post(
            url,
            json={"stream": "cam_main"},
            headers={"cookie": "alice", "origin": "https://evil.test"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            url, json={"stream": "cam_main"}, headers={"x-api-key": "agent-key"}
        ).status_code
        == 403
    )
    r = client.post(url, json={"stream": "cam_main"}, headers={"cookie": "alice"})
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store"
    s = r.json()
    assert "ticket" not in s["ws_path"]
    assert client.post(f"{url}/{s['id']}/heartbeat", headers={"cookie": "alice"}).status_code == 200
    assert client.post(f"{url}/{s['id']}/heartbeat", headers={"cookie": "admin"}).status_code == 403
    assert client.get(url, headers={"cookie": "alice"}).status_code == 403
    assert client.get(url, headers={"x-api-key": "agent-key"}).status_code == 401
    admin = client.get(url, headers={"cookie": "admin"}).json()
    assert "ticket" not in admin["sessions"][0]
    assert (
        client.delete(
            f"/api/camera-streams/admin/sessions/{s['id']}", headers={"cookie": "admin"}
        ).status_code
        == 204
    )
    assert client.post(f"{url}/{s['id']}/heartbeat", headers={"cookie": "alice"}).status_code == 410


def test_camera_component_on_non_camera_equipment_is_admitted(client):
    """A registered instrument camera is viewable without changing its kind."""
    client.app.state.registry.equipment[0].kind = "liquid_handler"
    response = client.post(
        "/api/camera-streams/sessions",
        json={"stream": "cam_main"},
        headers={"cookie": "alice"},
    )
    assert response.status_code == 201


def test_registered_mjpeg_camera_is_proxied_without_exposing_upstream(client):
    entry = client.app.state.registry.equipment[0]
    entry.kind = "liquid_handler"
    entry.camera.transport = "mjpeg"
    entry.camera.lenses[0].stream_path = "/devices/gibbie_flex/camera/stream"
    created = client.post(
        "/api/camera-streams/sessions",
        json={"stream": "cam_main"},
        headers={"cookie": "alice"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["transport"] == "mjpeg"
    assert "gibbie" not in str(body)
    streamed = client.get(
        f"/api/camera-streams/sessions/{body['id']}/mjpeg",
        headers={"cookie": "alice"},
    )
    assert streamed.status_code == 200
    assert streamed.headers["content-type"].startswith("multipart/x-mixed-replace")
    assert b"JPEG" in streamed.content
    assert body["id"] not in client.app.state.camera_viewing.sessions


def test_mjpeg_session_cannot_be_opened_by_another_account(client):
    entry = client.app.state.registry.equipment[0]
    entry.camera.transport = "mjpeg"
    entry.camera.lenses[0].stream_path = "/camera"
    body = client.post(
        "/api/camera-streams/sessions",
        json={"stream": "cam_main"},
        headers={"cookie": "alice"},
    ).json()
    response = client.get(
        f"/api/camera-streams/sessions/{body['id']}/mjpeg",
        headers={"cookie": "admin"},
    )
    assert response.status_code == 403


def test_grants_are_human_admin_only_and_revocable(client):
    body = {"principal": "alice", "streams": ["cam_main"], "purpose": "kiosk"}
    url = "/api/camera-streams/grants"
    assert client.post(url, json=body, headers={"cookie": "alice"}).status_code == 403
    assert client.post(url, json=body, headers={"x-api-key": "admin-key"}).status_code == 401
    assert (
        client.post(
            url, json={**body, "run_id": "invented"}, headers={"cookie": "admin"}
        ).status_code
        == 409
    )
    g = client.post(url, json=body, headers={"cookie": "admin"}).json()
    r = client.post(
        "/api/camera-streams/sessions",
        json={"stream": "cam_main", "grant_id": g["id"]},
        headers={"cookie": "alice"},
    )
    assert r.json()["mode"] == "monitoring"
    client.delete(url + "/" + g["id"], headers={"cookie": "admin"})
    assert not client.app.state.camera_viewing.sessions


def test_socket_ticket_replay_and_upstream_close(client, monkeypatch):
    closed = []

    class Upstream:
        async def send(self, raw):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(0.01)
            return b"video"

    @asynccontextmanager
    async def fake_connect(url, **kwargs):
        assert url == "ws://127.0.0.1:1984/api/ws?src=cam_main"
        try:
            yield Upstream()
        finally:
            closed.append(True)

    monkeypatch.setattr(cs, "connect", fake_connect)
    s = client.post(
        "/api/camera-streams/sessions", json={"stream": "cam_main"}, headers={"cookie": "alice"}
    ).json()
    with client.websocket_connect("/api/camera-streams/ws") as ws:
        ws.send_json({"type": "session", "value": s["ticket"]})
        ws.send_json({"type": "mse", "value": "avc1.640029"})
        assert ws.receive_bytes() == b"video"
    assert closed and not client.app.state.camera_viewing.sessions
    with client.websocket_connect("/api/camera-streams/ws") as ws:
        ws.send_json({"type": "session", "value": s["ticket"]})
        assert ws.receive_json()["type"] == "session/error"


def test_socket_rejects_management_messages(client, monkeypatch):
    @asynccontextmanager
    async def fake_connect(*args, **kwargs):
        class U:
            async def send(self, raw):
                raise AssertionError("must not forward")

            def __aiter__(self):
                return self

            async def __anext__(self):
                await asyncio.sleep(100)

        yield U()

    monkeypatch.setattr(cs, "connect", fake_connect)
    s = client.post(
        "/api/camera-streams/sessions", json={"stream": "cam_main"}, headers={"cookie": "alice"}
    ).json()
    with client.websocket_connect("/api/camera-streams/ws") as ws:
        ws.send_json({"type": "session", "value": s["ticket"]})
        ws.send_json({"type": "exec", "value": "bad"})
        assert ws.receive_json()["type"] == "session/error"
