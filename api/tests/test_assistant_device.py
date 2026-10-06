"""The device panel's assistant on the server (consolidation plan, steps 3–4).

device_chat.py: a tool-free structured turn acting on the device as the
signed-in user. What must hold: the model's reply is parsed and validated
before anything reaches the device; the device secret fails closed; the
actor's standing is resolved here and forwarded, never relayed; the chat is
claim-gated like the gateway's own; cancellation is owned by (user, device,
request id); the stream speaks the gateway bubble's event vocabulary.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from lab_assistant import device_chat, engine

ACTOR = "alice@example.edu"
RID = "0123456789abcdef0123456789abcdef"


# ── the structured reply ─────────────────────────────────────────────────


def _reply(**kw: Any) -> dict[str, Any]:
    return {"reply": "ok", "steps": [], "prelude": [], "for_each_well": None, "epilogue": [], **kw}


def test_a_reply_without_a_draft_proposes_nothing():
    assert device_chat.proposal_from_reply(_reply(reply=" hi ")) == ("hi", None)


def test_steps_arrive_as_json_text_and_leave_as_objects():
    reply, body = device_chat.proposal_from_reply(_reply(steps=[
        {"action": "lights.set", "args_json": '{"on": true}'},
        {"action": "home", "args_json": ""},
    ]))
    assert body == {"steps": [{"action": "lights.set", "args": {"on": True}},
                              {"action": "home", "args": {}}]}


def test_a_pattern_is_rebuilt_as_the_gateway_takes_it():
    _reply_text, body = device_chat.proposal_from_reply(_reply(
        prelude=[{"action": "pick_up_tip", "args_json": '{"pipette":"left"}'}],
        for_each_well={"labware_nickname": "2", "wells_json": '"A1:H12"', "order": "column",
                       "steps": [{"id": "d", "action": "dispense",
                                  "args_json": '{"location": {"labware_nickname": "2", "position": "{well}"}}'}],
                       "overrides_json": '{"H12": {"d": {"volume_ul": 50}}}'},
        epilogue=[{"action": "drop_tip", "args_json": '{"pipette":"left"}'}],
    ))
    assert body["for_each_well"]["wells"] == "A1:H12"
    assert body["for_each_well"]["steps"][0]["id"] == "d"
    assert body["for_each_well"]["overrides"] == {"H12": {"d": {"volume_ul": 50}}}
    assert body["prelude"][0]["action"] == "pick_up_tip" and body["epilogue"][0]["action"] == "drop_tip"


@pytest.mark.parametrize("bad", [
    _reply(steps=[{"action": "x", "args_json": "{not json"}]),
    _reply(steps=[{"action": "x", "args_json": "[1]"}]),
    _reply(steps=[{"action": "x", "args_json": "{}"}],
           for_each_well={"labware_nickname": "2", "wells_json": '"A1:B1"', "order": "row",
                          "steps": [], "overrides_json": "{}"}),
    {"steps": []},
])
def test_an_unusable_reply_is_refused_before_the_device_sees_it(bad):
    with pytest.raises(device_chat.ReplyError):
        device_chat.proposal_from_reply(bad)


# ── the device ───────────────────────────────────────────────────────────


def _entry(**kw: Any):
    base = {"id": "ot2_complexation", "name": "OT-2 Complexation", "kind": "liquid_handler",
            "base_url": "http://gw.test/", "edge_secret_env": "OT2_EDGE_SECRET"}
    return type("Entry", (), {**base, **kw})()


def test_the_device_secret_fails_closed(monkeypatch):
    monkeypatch.setenv("DEVICE_EDGE_SHARED_SECRET", "global")
    monkeypatch.delenv("OT2_EDGE_SECRET", raising=False)
    with pytest.raises(device_chat.DeviceConfigError):
        device_chat.device_from_entry(_entry())
    with pytest.raises(device_chat.DeviceConfigError):
        device_chat.device_from_entry(_entry(edge_secret_env=None))
    monkeypatch.setenv("OT2_EDGE_SECRET", "s3cret")
    device = device_chat.device_from_entry(_entry())
    assert (device.base_url, device.edge_secret) == ("http://gw.test", "s3cret")


def test_the_gateway_sees_the_real_person_and_their_standing():
    device = device_chat.Device("ot2_complexation", "OT-2", "http://gw.test", "s3cret")
    headers = device_chat.device_headers(device, user=ACTOR, role=None,
                                         projects=["zeta", "alpha"], pi_projects=[])
    assert headers == {"X-Auth-User": ACTOR, "X-Edge-Auth": "s3cret",
                       "X-Auth-Projects": "alpha,zeta", "X-Auth-Pi-Projects": ""}
    assert "X-Auth-Role" not in headers


def test_the_claim_may_be_held_directly_or_through_an_approved_plan():
    def status(owner: str | None) -> dict[str, Any]:
        return {"details": {"claimed_by": {"owner": owner}} if owner else {}}
    assert device_chat.holds_claim(status(ACTOR), ACTOR)
    assert device_chat.holds_claim(status(f"automation (approved by {ACTOR})"), ACTOR)
    assert not device_chat.holds_claim(status("bob@example.edu"), ACTOR)
    assert not device_chat.holds_claim(status(None), ACTOR)


# ── the routes ───────────────────────────────────────────────────────────


class _Aggregator:
    def entry(self, equipment_id: str):
        return _entry() if equipment_id == "ot2_complexation" else None


class _Gateway:
    """A fake OT-2 gateway: records what it was asked, as whom."""

    def __init__(self, owner: str | None = ACTOR):
        self.owner = owner
        self.requests: list[httpx.Request] = []
        self.drafts: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.url.host == "authz":
            return httpx.Response(200, json={"user": ACTOR, "projects": ["alpha"], "pi_projects": [],
                                             "role": "member"})
        if request.headers.get("X-Edge-Auth") != "s3cret":
            return httpx.Response(401, json={"detail": "login_required"})
        if path == "/status":
            claimed = {"claimed_by": {"owner": self.owner}} if self.owner else {}
            return httpx.Response(200, json={"equipment_status": "ready",
                                             "details": {"deck": {"1": "rack"}, "robot": {}, **claimed}})
        if path == "/docs/agent":
            return httpx.Response(200, json={"model": "Opentrons OT-2"})
        if path == "/plans/actions":
            return httpx.Response(200, json={"actions": {"lights.set": {}}})
        if path == "/plans" and request.method == "GET":
            return httpx.Response(200, json=[{"plan_id": "p1", "status": "executed", "steps": [],
                                              "results": [], "redacted": True}])
        if path == "/plans" and request.method == "POST":
            body = json.loads(request.content)
            self.drafts.append(body)
            return httpx.Response(201, json={"plan_id": "p2", "steps": body.get("steps", []),
                                             "created_by": body["created_by"]})
        return httpx.Response(404, json={"detail": path})


def _app(gateway: _Gateway, monkeypatch, *, models=None, structured=None) -> FastAPI:
    monkeypatch.setenv("OT2_EDGE_SECRET", "s3cret")
    monkeypatch.setenv("AUTHZ_BASE", "http://authz")
    monkeypatch.setattr(device_chat, "make_client",
                        lambda: httpx.AsyncClient(transport=httpx.MockTransport(gateway.handler)))
    offered = models if models is not None else [device_chat.ModelChoice("claude-sonnet-5-5", "claude-cli")]

    async def available() -> list[device_chat.ModelChoice]:
        return offered

    monkeypatch.setattr(device_chat, "available_models", available)
    seen: dict[str, Any] = {}

    async def fake_claude(turn, model, system, payload):
        seen.update(model=model, system=system, payload=payload)
        return structured if structured is not None else _reply(reply="all quiet")

    monkeypatch.setattr(device_chat, "run_claude", fake_claude)
    import app.control as control
    monkeypatch.setattr(control, "_authz_base", lambda: "http://authz")
    app = FastAPI()
    app.state.aggregator = _Aggregator()
    app.state.seen = seen
    app.include_router(engine.build_assistant_router())
    return app


async def _call(app: FastAPI, method: str, path: str, body: dict | None = None,
                headers: dict | None = None) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.request(method, path, json=body, headers=headers or {})


def _events(response: httpx.Response) -> list[dict[str, Any]]:
    out = []
    for record in response.text.split("\n\n"):
        data = [l[5:].strip() for l in record.splitlines() if l.startswith("data:")]
        if data:
            out.append(json.loads("\n".join(data)))
    return out


async def test_health_needs_a_user_and_a_known_device_and_lists_models(monkeypatch):
    app = _app(_Gateway(), monkeypatch)
    assert (await _call(app, "GET", "/api/assistant/equipment/ot2_complexation/health")).status_code == 401
    auth = {"X-Auth-User": ACTOR}
    assert (await _call(app, "GET", "/api/assistant/equipment/nope/health", headers=auth)).status_code == 404
    r = await _call(app, "GET", "/api/assistant/equipment/ot2_complexation/health", headers=auth)
    assert r.json() == {"configured": True, "reason": None, "model": "claude-sonnet-5-5",
                        "models": ["claude-sonnet-5-5"]}
    app = _app(_Gateway(), monkeypatch, models=[])
    r = await _call(app, "GET", "/api/assistant/equipment/ot2_complexation/health", headers=auth)
    assert r.json()["configured"] is False and r.json()["models"] == []


async def test_a_turn_reads_as_the_user_and_creates_the_draft_on_the_device(monkeypatch):
    gw = _Gateway()
    app = _app(gw, monkeypatch, structured=_reply(
        reply="Lights on.", steps=[{"action": "lights.set", "args_json": '{"on": true}'}]))
    r = await _call(app, "POST", "/api/assistant/equipment/ot2_complexation/chat/stream",
                    {"messages": [{"role": "user", "content": "lights on"}], "request_id": RID},
                    {"X-Auth-User": ACTOR, "X-Auth-Role": "member"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _events(r)
    assert [e["type"] for e in events][:1] == ["thinking"]
    names = [e["name"] for e in events if e["type"] == "tool_started"]
    assert names[:3] == ["get_status", "get_equipment_docs", "list_actions"]
    assert names[-1] == "propose_plan"
    done = events[-1]
    assert done["type"] == "complete"
    assert done["result"] == {"reply": "Lights on.", "plan_id": "p2", "model": "claude-sonnet-5-5",
                              "tools_used": ["get_status", "get_equipment_docs", "list_actions",
                                             "list_plans", "propose_plan"]}
    # Every device request carried the person and their roster standing.
    device_requests = [q for q in gw.requests if q.url.host != "authz"]
    assert device_requests and all(q.headers["X-Auth-User"] == ACTOR for q in device_requests)
    assert all(q.headers["X-Auth-Projects"] == "alpha" for q in device_requests)
    assert gw.drafts == [{"steps": [{"action": "lights.set", "args": {"on": True}}],
                          "created_by": f"assistant (claude-sonnet-5-5) for {ACTOR}"}]
    # The model saw the conversation and the reads, and no tool surface.
    payload = app.state.seen["payload"]
    assert payload["messages"] == [{"role": "user", "content": "lights on"}]
    assert payload["reads"]["get_status"]["equipment_status"] == "ready"
    assert payload["current_plans"][0]["redacted"] is True and payload["plate_reports"] == []


async def test_a_turn_is_claim_gated_like_the_gateway_bubble(monkeypatch):
    body = {"messages": [{"role": "user", "content": "hi"}]}
    auth = {"X-Auth-User": ACTOR}
    r = await _call(_app(_Gateway(owner="bob@example.edu"), monkeypatch), "POST",
                    "/api/assistant/equipment/ot2_complexation/chat/stream", body, auth)
    assert r.status_code == 423
    r = await _call(_app(_Gateway(owner=f"automation (approved by {ACTOR})"), monkeypatch), "POST",
                    "/api/assistant/equipment/ot2_complexation/chat/stream", body, auth)
    assert r.status_code == 200


async def test_refusals_keep_their_status_before_the_stream_opens(monkeypatch):
    app = _app(_Gateway(), monkeypatch)
    body = {"messages": [{"role": "user", "content": "hi"}]}
    path = "/api/assistant/equipment/ot2_complexation/chat/stream"
    assert (await _call(app, "POST", path, body)).status_code == 401
    auth = {"X-Auth-User": ACTOR}
    assert (await _call(app, "POST", path, {**body, "model": "gpt-9"}, auth)).status_code == 422
    assert (await _call(app, "POST", path, {**body, "conversation_owner": "bob"}, auth)).status_code == 409
    assert (await _call(app, "POST", "/api/assistant/equipment/nope/chat/stream", body, auth)).status_code == 404


async def test_a_device_refusal_of_the_draft_reaches_the_operator(monkeypatch):
    class Refusing(_Gateway):
        def handler(self, request):
            if request.url.path == "/plans" and request.method == "POST":
                return httpx.Response(422, json={"detail": "step 1 (lights.set): on must be a boolean"})
            return super().handler(request)

    app = _app(Refusing(), monkeypatch, structured=_reply(
        steps=[{"action": "lights.set", "args_json": '{"on": "yes"}'}]))
    r = await _call(app, "POST", "/api/assistant/equipment/ot2_complexation/chat/stream",
                    {"messages": [{"role": "user", "content": "lights"}]}, {"X-Auth-User": ACTOR})
    events = _events(r)
    refused = [e for e in events if e["type"] == "tool_finished" and e["name"] == "propose_plan"][0]
    assert refused["success"] is False and "on must be a boolean" in refused["error"]
    assert events[-1]["result"]["plan_id"] is None
    assert "could not create that draft" in events[-1]["result"]["reply"]


async def test_cancel_is_owned_by_user_device_and_request(monkeypatch):
    app = _app(_Gateway(), monkeypatch)
    path = "/api/assistant/equipment/ot2_complexation/chat/cancel"
    r = await _call(app, "POST", path, {"request_id": RID}, {"X-Auth-User": ACTOR})
    assert r.json() == {"canceled": False}
    assert (await _call(app, "POST", path, {"request_id": RID})).status_code == 401
    assert (await _call(app, "POST", path, {"request_id": "short"}, {"X-Auth-User": ACTOR})).status_code == 422


# ── review fixes (Codex, implementation review) ──────────────────────────


async def test_a_cancelled_turn_reaps_the_model_process(tmp_path, monkeypatch):
    """Stop (or a closed tab) while the CLI runs must kill it, not orphan it."""
    import asyncio
    import os
    import signal

    fake = tmp_path / "claude"
    pidfile = tmp_path / "pid"
    fake.write_text(f"#!/bin/sh\necho $$ > {pidfile}\nsleep 60\n")
    fake.chmod(0o755)
    monkeypatch.setattr(device_chat, "_claude_binary", lambda: str(fake))
    turn = device_chat.Turn(user=ACTOR, equipment_id="ot2_complexation", request_id=RID)
    task = asyncio.create_task(device_chat.run_claude(turn, "claude-sonnet-5-5", "sys", {"messages": []}))
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        await asyncio.sleep(0.02)
    pid = int(pidfile.read_text())
    started = asyncio.get_event_loop().time()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Promptly: the `sleep` child held our pipes, so only a tree kill returns
    # before it would have exited on its own.
    assert asyncio.get_event_loop().time() - started < 5
    # Reaped: the pid is gone (or a zombie no longer, since we waited on it).
    with pytest.raises(ProcessLookupError):
        os.kill(pid, signal.SIGCONT)
    assert turn.process is None


def test_plate_summaries_keep_per_well_attribution():
    report = {"labware": "2", "plans": ["p1"], "stats": {"n": 2}, "density_g_per_ml": None,
              "wells": {"A1": {"mass_g": 0.1, "deviation_pct": 1.5, "status": "ok", "noise": 1},
                        "B1": {"mass_g": None, "status": "unweighed"}},
              "unattributed_readings": [1, 2], "excluded_deliveries": []}
    summary = device_chat.summarize_report(report)
    assert summary["wells"] == {"A1": {"mass_g": 0.1, "deviation_pct": 1.5, "status": "ok"},
                                "B1": {"status": "unweighed"}}
    assert summary["unattributed_readings"] == 2 and summary["excluded_deliveries"] == 0


def test_the_prompt_keeps_the_gateway_safety_lines():
    device = device_chat.Device("ot2_complexation", "OT-2 Complexation", "http://gw.test", "s")
    text = device_chat.system_prompt({"model": "Opentrons OT-2"}, device)
    for fragment in ("explicit observation", "never infer that a tip or rack is fresh",
                     "force_direct=true", "Operator-only actions include", "You do not decide chemistry",
                     "force_drop: true", "{well}", "OT-2 Complexation", "You have NO tools"):
        assert fragment in text, fragment
