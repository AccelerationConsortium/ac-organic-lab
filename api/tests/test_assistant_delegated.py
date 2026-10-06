"""Step 6: the dashboard assistant creates drafts ON devices that run their
own plans (``runs_plans``), instead of a browser-run plan.

What must hold: every lab-control gate still applies before the device is
asked; the draft reaches the device as the signed-in actor with that
device's own edge secret and roster standing; the device's refusal reaches
the model; the returned plan is a receipt (``delegated``) the engine audits
but never stores, so the dashboard approve route cannot act on it; devices
without the flag are untouched; patterns are for ``runs_plans`` devices only.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI
from lab_skills.registry import EquipmentEntry, Registry

from app import assistant_control as ac
from lab_assistant import engine
from lab_assistant.plan_contract import plan_step_hash

ACTOR = "alice@example.edu"
OT2_BASE = "http://ot2.test:8021"
AUTHZ = "http://127.0.0.1:8009"
LIGHTS = {"action": "lights.set", "args": {"on": True}}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAB_ACTOR", ACTOR)
    monkeypatch.setenv("CONTROL_AUTHZ_ENFORCE", "true")
    monkeypatch.delenv("AUTH_SERVICE_BASE", raising=False)
    monkeypatch.setenv("OT2_EDGE_SECRET", "s3cret")


def _registry(runs_plans: bool = True) -> Registry:
    return Registry(equipment=[EquipmentEntry(
        id="ot2_complexation", name="Opentrons OT-2 Complexation", kind="liquid_handler",
        adapter="http", base_url=OT2_BASE, status_path="/status", protocol="1.2",
        edge_secret_env="OT2_EDGE_SECRET", runs_plans=runs_plans)])


def _mock_device(*, allowed=("lights.set", "pick_up_tip"), catalog=("lights.set", "pick_up_tip", "aspirate",
                                                                   "dispense", "drop_tip", "platebalance.read")) -> None:
    respx.get(f"{OT2_BASE}/status").mock(return_value=httpx.Response(200, json={
        "equipment_id": "ot2_complexation", "equipment_name": "Opentrons OT-2 Complexation",
        "equipment_kind": "liquid_handler", "equipment_status": "ready", "message": "idle",
        "allowed_actions": list(allowed), "activity": "idle", "device_time": "2026-10-06T00:00:00Z"}))
    items = []
    paths: dict[str, Any] = {}
    for action in catalog:
        sd = ac._find_skill_def("liquid_handler", action)
        schema = sd.args_schema.model_json_schema() if sd is not None else {"type": "object"}
        items.append({"action": action, "idempotent": False, "args_schema": schema})
        if sd is not None:
            paths[sd.endpoint] = {"post": {}}
    respx.get(f"{OT2_BASE}/docs/agent").mock(return_value=httpx.Response(200, json={
        "documentation_version": "1", "equipment_kind": "liquid_handler", "model": "Opentrons OT-2",
        "actions": items}))
    respx.get(f"{OT2_BASE}/plans/actions").mock(return_value=httpx.Response(200, json={"actions": items}))
    respx.get(f"{OT2_BASE}/openapi.json").mock(return_value=httpx.Response(200, json={
        "openapi": "3.1.0", "info": {"title": "t"}, "paths": paths}))
    respx.get(f"{AUTHZ}/authz/check").mock(return_value=httpx.Response(200, json={"allowed": True}))
    respx.get(f"{AUTHZ}/authz/scope").mock(return_value=httpx.Response(200, json={
        "user": ACTOR, "member_projects": ["alpha"], "pi_projects": ["beta"], "is_admin": False}))


@respx.mock
async def test_the_draft_is_created_on_the_device_as_the_actor() -> None:
    _mock_device()
    post = respx.post(f"{OT2_BASE}/plans").mock(return_value=httpx.Response(201, json={
        "plan_id": "dev-7", "steps": [LIGHTS, LIGHTS], "created_by": f"assistant (dashboard) for {ACTOR}"}))
    out = json.loads(await ac._propose_plan(_registry(), "ot2_complexation", [LIGHTS, LIGHTS], "lights"))
    plan = out["plan"]
    assert plan["delegated"] == {"device_plan_id": "dev-7", "created_by": f"assistant (dashboard) for {ACTOR}",
                                 "step_count": 2, "pattern_summary": None}
    assert plan["plan_id"] == "dev-7" and plan["expires_in_s"] == 0 and plan["actor"] == ACTOR
    assert [s["action"] for s in plan["steps"]] == ["lights.set", "lights.set"]
    assert plan["step_hash"] == plan_step_hash(plan["steps"])
    req = post.calls.last.request
    assert req.headers["X-Auth-User"] == ACTOR and req.headers["X-Edge-Auth"] == "s3cret"
    assert req.headers["X-Auth-Projects"] == "alpha" and req.headers["X-Auth-Pi-Projects"] == "beta"
    assert "X-Auth-Role" not in req.headers
    assert json.loads(req.content) == {"created_by": f"assistant (dashboard) for {ACTOR}",
                                       "steps": [LIGHTS, LIGHTS]}


@respx.mock
async def test_a_pattern_goes_to_the_device_as_given_and_only_there() -> None:
    _mock_device()
    post = respx.post(f"{OT2_BASE}/plans").mock(return_value=httpx.Response(201, json={
        "plan_id": "dev-8", "steps": [{} for _ in range(98)],
        "pattern_summary": "for each of 96 wells of plate 2: dispense → read balance"}))
    pattern = {"prelude": [{"action": "pick_up_tip", "args": {"pipette": "left"}}],
               "for_each_well": {"labware_nickname": "2", "wells": "A1:H12", "order": "column",
                                 "steps": [{"id": "d", "action": "dispense",
                                            "args": {"pipette": "left", "volume_ul": 50,
                                                     "location": {"labware_nickname": "2", "position": "{well}"}}},
                                           {"action": "platebalance.read", "args": {}}]},
               "epilogue": [{"action": "drop_tip", "args": {"pipette": "left"}}]}
    out = json.loads(await ac._propose_plan(_registry(), "ot2_complexation", None, "weigh plate", pattern))
    plan = out["plan"]
    assert plan["delegated"]["step_count"] == 98
    assert plan["delegated"]["pattern_summary"].startswith("for each of 96 wells")
    assert [s["action"] for s in plan["steps"]] == ["pick_up_tip", "dispense", "platebalance.read", "drop_tip"]
    body = json.loads(post.calls.last.request.content)
    assert body["for_each_well"]["wells"] == "A1:H12" and body["prelude"][0]["action"] == "pick_up_tip"
    assert "steps" not in body
    # The same pattern on a device that runs plans from the browser is refused before any read.
    refused = json.loads(await ac._propose_plan(_registry(runs_plans=False), "ot2_complexation", None, "", pattern))
    assert refused["code"] == "not_proposable" and "write the steps out" in refused["error"]
    both = json.loads(await ac._propose_plan(_registry(), "ot2_complexation", [LIGHTS], "", pattern))
    assert both["code"] == "invalid_step"


@respx.mock
async def test_the_gates_still_apply_before_the_device_is_asked() -> None:
    _mock_device(allowed=("pick_up_tip",))
    post = respx.post(f"{OT2_BASE}/plans").mock(return_value=httpx.Response(201, json={"plan_id": "x", "steps": []}))
    # An action the device's catalog does not publish.
    out = json.loads(await ac._propose_plan(_registry(), "ot2_complexation", [{"action": "stop"}], ""))
    assert out["code"] == "capability_unknown" and out["step"] == 1
    # A first step the device says cannot start now.
    out = json.loads(await ac._propose_plan(_registry(), "ot2_complexation", [LIGHTS], ""))
    assert out["code"] == "not_allowed"
    # Not authorized on the equipment.
    respx.get(f"{AUTHZ}/authz/check").mock(return_value=httpx.Response(200, json={"allowed": False}))
    out = json.loads(await ac._propose_plan(_registry(), "ot2_complexation",
                                            [{"action": "pick_up_tip", "args": {"pipette": "left"}}], ""))
    assert out["code"] == "not_authorized"
    assert not post.called


@respx.mock
async def test_a_missing_device_secret_fails_closed(monkeypatch) -> None:
    _mock_device()
    monkeypatch.delenv("OT2_EDGE_SECRET")
    monkeypatch.setenv("DEVICE_EDGE_SHARED_SECRET", "global")
    post = respx.post(f"{OT2_BASE}/plans").mock(return_value=httpx.Response(201, json={"plan_id": "x", "steps": []}))
    out = json.loads(await ac._propose_plan(_registry(), "ot2_complexation", [LIGHTS], ""))
    assert out["code"] == "not_proposable" and "no edge secret" in out["error"]
    assert not post.called


@respx.mock
async def test_the_devices_refusal_reaches_the_model() -> None:
    _mock_device()
    respx.post(f"{OT2_BASE}/plans").mock(return_value=httpx.Response(
        422, json={"detail": "step 2 (dispense, well B1): volume_ul must be positive"}))
    out = json.loads(await ac._propose_plan(_registry(), "ot2_complexation", [LIGHTS], ""))
    assert out["code"] == "device_refused" and "well B1" in out["error"]


@respx.mock
async def test_a_device_without_the_flag_is_unchanged() -> None:
    _mock_device()
    post = respx.post(f"{OT2_BASE}/plans").mock(return_value=httpx.Response(201, json={"plan_id": "x", "steps": []}))
    out = json.loads(await ac._propose_plan(_registry(runs_plans=False), "ot2_complexation", [LIGHTS], ""))
    plan = out["plan"]
    assert "delegated" not in plan and plan["expires_in_s"] > 0 and len(plan["plan_id"]) >= 9
    assert not post.called


def test_the_control_server_env_carries_the_per_device_secrets_only(monkeypatch) -> None:
    monkeypatch.setenv("OT2_EDGE_SECRET", "a")
    monkeypatch.setenv("XARM_EDGE_SHARED_SECRET", "b")
    monkeypatch.setenv("DEVICE_EDGE_SHARED_SECRET", "global")
    monkeypatch.setenv("ASSISTANT_OPENAI_API_KEY", "nope")
    env = engine._control_server_env(ACTOR)
    assert env["LAB_ACTOR"] == ACTOR and env["OT2_EDGE_SECRET"] == "a" and env["XARM_EDGE_SHARED_SECRET"] == "b"
    assert "DEVICE_EDGE_SHARED_SECRET" not in env and "ASSISTANT_OPENAI_API_KEY" not in env


# ── the engine: a delegated plan is audited, never stored ────────────────


class _Db:
    def __init__(self):
        self.events = []

    def record_equipment_event(self, equipment_id, event_type, *, message, payload):
        self.events.append((equipment_id, event_type, payload))


async def test_a_delegated_plan_is_audited_but_not_approvable(monkeypatch) -> None:
    db = _Db()
    seen: dict[str, Any] = {}

    async def fake_turn(messages, *, control=False, actor=None, on_proposal=None, on_plan=None, **_kw):
        steps = [{"action": "lights.set", "passthrough_action": "lights.set", "args": {"on": True}}]
        plan = {"plan_id": "dev-7", "equipment_id": "ot2_complexation", "equipment_name": "OT-2",
                "kind": "liquid_handler", "steps": steps, "step_hash": plan_step_hash(steps), "reason": "",
                "actor": actor, "expires_in_s": 0, "device_state": {}, "delegated": {
                    "device_plan_id": "dev-7", "created_by": "assistant (dashboard) for " + actor,
                    "step_count": 1, "pattern_summary": None}}
        seen["on_plan"] = on_plan
        await on_plan(plan)
        yield engine._sse({"type": "plan", "plan": plan})
        yield engine._sse({"type": "done"})

    monkeypatch.setattr(engine, "DEFAULT_BACKEND", "claude-cli")
    monkeypatch.setattr(engine, "CONTROL_BACKEND", "claude-cli")
    monkeypatch.setattr(engine, "_claude_binary", lambda: "/bin/true")
    monkeypatch.setattr(engine, "_run_claude", fake_turn)
    app = FastAPI()
    app.state.db = db
    app.include_router(engine.build_assistant_router())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "lights"}],
                                                      "mode": "control"}, headers={"X-Auth-User": ACTOR})
        assert r.status_code == 200, r.text
        assert '"delegated"' in r.text
        approve = await c.post("/api/assistant/plans/dev-7/approve",
                               json={"step_hash": plan_step_hash([{"action": "lights.set", "passthrough_action": "lights.set", "args": {"on": True}}])},
                               headers={"X-Auth-User": ACTOR})
    assert approve.status_code == 404
    kinds = [e[1] for e in db.events]
    assert "assistant_plan_delegated" in kinds and "assistant_plan_proposed" not in kinds
    row = [e for e in db.events if e[1] == "assistant_plan_delegated"][0]
    assert row[0] == "ot2_complexation" and row[2]["device_plan_id"] == "dev-7" and row[2]["actor"] == ACTOR
