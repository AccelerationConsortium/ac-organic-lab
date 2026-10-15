"""Server-side execution of approved assistant plans (assistant_plan_runs.py).

The device is mocked at the HTTP layer (respx), so every step really goes
through ``control._proxy``: authz, the claim token, the audit row.
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI

from app import assistant, assistant_plan_runs as apr, control
from app.plan_results import PlanResultsJournal
from lab_assistant.plan_contract import plan_step_hash

BASE = "http://reader.test:8040"
ALICE, BOB = "alice@example.edu", "bob@example.edu"
COOKIE = {control._AUTH_COOKIE_NAME: "alice-session"}

STEPS = [
    {"action": "drawer.close", "passthrough_action": "drawer/close", "args": {}},
    {"action": "read.absorbance", "passthrough_action": "read/absorbance",
     "args": {"wavelength_nm": 450, "wells": ["A1"]}},
    {"action": "drawer.open", "passthrough_action": "drawer/open", "args": {}},
]
ALL_ALLOWED = ["drawer.close", "read.absorbance", "drawer.open"]


class FakeDB:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    def record_equipment_event(self, equipment_id, event_type, *, message="", payload=None):
        self.events.append((equipment_id, event_type, payload or {}))

    def of(self, event_type: str) -> list[dict[str, Any]]:
        return [p for _, t, p in self.events if t == event_type]


def _entry(protocol: str = "1.2") -> Any:
    return SimpleNamespace(id="cytation_5", kind="plate_reader", adapter="http", base_url=BASE,
                           status_path="/status", protocol=protocol, edge_secret_env=None)


@pytest.fixture
def env(monkeypatch, tmp_path):
    plans: dict[str, dict[str, Any]] = {}
    app = FastAPI()
    db = FakeDB()
    entry = _entry()
    app.state.db = db
    app.state.aggregator = SimpleNamespace(entry=lambda eid: entry if eid == entry.id else None,
                                           registry=None)
    app.state.control_client = httpx.AsyncClient(trust_env=False)
    store = apr.PlanRunStore(tmp_path / "runs.sqlite3")
    app.state.assistant_plan_runner = apr.PlanRunner(app, plans, store)
    app.include_router(assistant.build_assistant_router(plans=plans))
    app.include_router(apr.build_plan_runs_router())

    allowed = {"actions": list(ALL_ALLOWED)}
    sessions = {"alice-session": ALICE}

    async def verify(client, cookie):
        return sessions.get(cookie.split("=", 1)[1])

    async def status(app_, equipment_id):
        return SimpleNamespace(allowed_actions=list(allowed["actions"]), details={})

    async def authz(client, user, equipment_id):
        return {"allowed": user in (ALICE, BOB)}

    async def audit(db_, equipment_id, event_type, message, payload):
        db.events.append((equipment_id, event_type, payload))

    monkeypatch.setattr(apr, "_verify_human_session", verify)
    monkeypatch.setattr(apr, "_read_live_status", status)
    monkeypatch.setattr(control, "_fetch_authz_verdict", authz)
    monkeypatch.setattr(assistant, "_audit", audit)
    monkeypatch.setenv("CONTROL_AUTHZ_ENFORCE", "true")
    return SimpleNamespace(app=app, plans=plans, db=db, store=store, allowed=allowed,
                           runner=app.state.assistant_plan_runner, entry=entry)


def _propose(env, plan_id: str = "p1", steps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """What engine.record_plan caches when lab-control returns a plan."""
    steps = steps or STEPS
    plan = {"plan_id": plan_id, "equipment_id": "cytation_5", "equipment_name": "Cytation 5",
            "kind": "plate_reader", "steps": steps, "step_hash": plan_step_hash(steps),
            "reason": "test", "actor": ALICE}
    env.plans[plan_id] = {"plan": plan, "actor": ALICE, "expires_at": time.monotonic() + 600,
                          "approved_at": None}
    return plan


def _client(env, user: str | None = ALICE, cookies: dict[str, str] | None = COOKIE) -> httpx.AsyncClient:
    headers = {"X-Auth-User": user} if user else {}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url="http://test",
                             headers=headers, cookies=cookies)


def _mock_device(router: respx.MockRouter, *, claim_status: int = 200) -> dict[str, Any]:
    routes = {
        "claim": router.post(f"{BASE}/control/claim").mock(return_value=httpx.Response(
            claim_status, json={"claim_token": "tok-plan", "heartbeat_interval_s": 60,
                                "expires_at": "2026-10-15T00:00:00Z"} if claim_status == 200
            else {"detail": "claimed", "claimed_by": {"owner": BOB}})),
        "release": router.post(f"{BASE}/control/release").mock(return_value=httpx.Response(204)),
        "heartbeat": router.post(f"{BASE}/control/heartbeat").mock(return_value=httpx.Response(204)),
    }
    for step in STEPS:
        routes[step["action"]] = router.post(f"{BASE}/control/{step['passthrough_action']}").mock(
            return_value=httpx.Response(200, json={"ok": True, "action": step["action"],
                                                   "values": {"A1": 0.42}}))
    return routes


async def _wait_done(env, plan_id: str) -> dict[str, Any]:
    live = env.runner.live.get(plan_id)
    if live is not None and live.task is not None:
        await asyncio.wait_for(live.task, 5)
    run = env.store.get(plan_id)
    assert run is not None
    return run


async def _approve(c: httpx.AsyncClient, plan: dict[str, Any]) -> None:
    r = await c.post(f"/api/assistant/plans/{plan['plan_id']}/approve", json={"step_hash": plan["step_hash"]})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Approval gating
# ---------------------------------------------------------------------------


async def test_execute_requires_approval_hash_and_the_approvers_browser_session(env):
    plan = _propose(env)
    with respx.mock(assert_all_called=False) as router:
        routes = _mock_device(router)
        async with _client(env) as c:
            body = {"step_hash": plan["step_hash"]}
            # Not yet approved.
            assert (await c.post("/api/assistant/plans/p1/execute", json=body)).status_code == 409
            await _approve(c, plan)
            # The hash the operator sends must be the approved plan's.
            r = await c.post("/api/assistant/plans/p1/execute", json={"step_hash": "0" * 64})
            assert r.status_code == 409
        # No identity at all (e.g. a model calling the API directly) is refused.
        async with _client(env, user=None) as c:
            assert (await c.post("/api/assistant/plans/p1/execute", json=body)).status_code == 401
        # Someone else, even with a valid session of their own, is not the approver.
        async with _client(env, user=BOB, cookies={control._AUTH_COOKIE_NAME: "bob-session"}) as c:
            assert (await c.post("/api/assistant/plans/p1/execute", json=body)).status_code == 403
        # The approver without a browser session (an API key, a script) is refused…
        async with _client(env, cookies={}) as c:
            assert (await c.post("/api/assistant/plans/p1/execute", json=body)).status_code == 401
        # …and so is a session cookie the sidecar resolves to someone else.
        async with _client(env, cookies={control._AUTH_COOKIE_NAME: "forged"}) as c:
            assert (await c.post("/api/assistant/plans/p1/execute", json=body)).status_code == 401
        assert not routes["claim"].called
        assert "p1" in env.plans  # nothing spent the approval


async def test_device_without_claims_is_not_run_server_side(env):
    env.entry.protocol = "1.0"
    plan = _propose(env)
    async with _client(env) as c:
        await _approve(c, plan)
        r = await c.post("/api/assistant/plans/p1/execute", json={"step_hash": plan["step_hash"]})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_claim_protocol"


async def test_unauthorized_operator_is_refused_before_claiming(env, monkeypatch):
    async def deny(client, user, equipment_id):
        return {"allowed": False, "reason": "no role"}

    monkeypatch.setattr(control, "_fetch_authz_verdict", deny)
    plan = _propose(env)
    with respx.mock(assert_all_called=False) as router:
        routes = _mock_device(router)
        async with _client(env) as c:
            await _approve(c, plan)
            r = await c.post("/api/assistant/plans/p1/execute", json={"step_hash": plan["step_hash"]})
    assert r.status_code == 403
    assert not routes["claim"].called


# ---------------------------------------------------------------------------
# Claim handoff and a clean run
# ---------------------------------------------------------------------------


async def test_plan_holds_one_claim_for_the_whole_run_and_records_every_step(env):
    plan = _propose(env)
    with respx.mock(assert_all_called=False) as router:
        routes = _mock_device(router)
        async with _client(env) as c:
            await _approve(c, plan)
            r = await c.post("/api/assistant/plans/p1/execute", json={"step_hash": plan["step_hash"]})
            assert r.status_code == 202, r.text
            assert r.json()["owner"] == f"automation (approved by {ALICE})"
            # The approval is spent: it cannot start twice.
            assert "p1" not in env.plans
            run = await _wait_done(env, "p1")
            again = await c.post("/api/assistant/plans/p1/execute", json={"step_hash": plan["step_hash"]})
            assert again.status_code == 409
            status = (await c.get("/api/assistant/plans/p1/run")).json()

    # One fresh claim, as the approver, under the plan's own session …
    assert routes["claim"].call_count == 1
    claim = json.loads(routes["claim"].calls.last.request.content)
    assert claim["owner"] == ALICE and claim["session_id"] == "assistant-plan-p1"
    # … carried by every step, then released once.
    for step in STEPS:
        assert routes[step["action"]].call_count == 1
        assert routes[step["action"]].calls.last.request.headers["x-claim-token"] == "tok-plan"
    assert routes["release"].call_count == 1

    assert run["status"] == "executed"
    assert [r["outcome"] for r in run["results"]] == ["ok", "ok", "ok"]
    # A read's response is the measurement and is kept; others are not.
    assert run["results"][1]["response"]["values"] == {"A1": 0.42}
    assert "response" not in run["results"][0]
    assert status["status"] == "executed" and status["results"][1]["response"] is not None

    rows = env.db.of("control_action")
    assert [r["plan"] for r in rows] == ["p1#1", "p1#2", "p1#3"]
    assert {r["origin"] for r in rows} == {"assistant-plan"}
    assert {r["owner"] for r in rows} == {ALICE}
    finished = env.db.of("assistant_plan_finished")
    assert finished[-1]["status"] == "executed" and finished[-1]["executor"] == "server"


async def test_claim_held_by_someone_else_refuses_and_keeps_the_approval(env):
    plan = _propose(env)
    with respx.mock(assert_all_called=False) as router:
        routes = _mock_device(router, claim_status=409)
        async with _client(env) as c:
            await _approve(c, plan)
            r = await c.post("/api/assistant/plans/p1/execute", json={"step_hash": plan["step_hash"]})
    assert r.status_code == 409
    assert not routes["drawer.close"].called
    assert "p1" in env.plans and not env.plans["p1"].get("starting")
    assert env.store.get("p1") is None


# ---------------------------------------------------------------------------
# Halt on error / not allowed
# ---------------------------------------------------------------------------


async def test_first_refused_step_halts_and_skips_the_rest(env):
    plan = _propose(env)
    with respx.mock(assert_all_called=False) as router:
        routes = _mock_device(router)
        routes["read.absorbance"].mock(return_value=httpx.Response(412, json={"detail": "no plate"}))
        async with _client(env) as c:
            await _approve(c, plan)
            await c.post("/api/assistant/plans/p1/execute", json={"step_hash": plan["step_hash"]})
            run = await _wait_done(env, "p1")
    assert run["status"] == "failed"
    assert [r["outcome"] for r in run["results"]] == ["ok", "failed", "skipped"]
    assert run["results"][1]["status_code"] == 412 and "no plate" in run["results"][1]["message"]
    assert not routes["drawer.open"].called
    assert routes["release"].call_count == 1
    assert "step 2" in run["halt_reason"]


async def test_step_not_in_live_allowed_actions_is_never_sent(env):
    env.allowed["actions"] = ["drawer.close"]  # the reader will not read right now
    plan = _propose(env)
    with respx.mock(assert_all_called=False) as router:
        routes = _mock_device(router)
        async with _client(env) as c:
            await _approve(c, plan)
            await c.post("/api/assistant/plans/p1/execute", json={"step_hash": plan["step_hash"]})
            run = await _wait_done(env, "p1")
    assert [r["outcome"] for r in run["results"]] == ["ok", "skipped", "skipped"]
    assert not routes["read.absorbance"].called
    assert "will not accept 'read.absorbance'" in run["halt_reason"]


# ---------------------------------------------------------------------------
# Abort / pause
# ---------------------------------------------------------------------------


def _blocking_first_step(routes: dict[str, Any]) -> tuple[asyncio.Event, asyncio.Event]:
    started, release = asyncio.Event(), asyncio.Event()

    async def slow(request):
        started.set()
        await release.wait()
        return httpx.Response(200, json={"ok": True})

    routes["drawer.close"].mock(side_effect=slow)
    return started, release


async def test_anyone_signed_in_may_abort_but_the_step_in_flight_completes(env):
    plan = _propose(env)
    with respx.mock(assert_all_called=False) as router:
        routes = _mock_device(router)
        started, release = _blocking_first_step(routes)
        async with _client(env) as c:
            await _approve(c, plan)
            await c.post("/api/assistant/plans/p1/execute", json={"step_hash": plan["step_hash"]})
            await asyncio.wait_for(started.wait(), 5)
            async with _client(env, user=None) as anon:
                assert (await anon.post("/api/assistant/plans/p1/abort")).status_code == 401
            async with _client(env, user=BOB, cookies={}) as bob:
                assert (await bob.post("/api/assistant/plans/p1/abort")).status_code == 200
            release.set()
            run = await _wait_done(env, "p1")
    assert run["status"] == "aborted"
    assert [r["outcome"] for r in run["results"]] == ["ok", "skipped", "skipped"]
    assert run["halt_reason"] == f"aborted by {BOB}"
    assert not routes["read.absorbance"].called
    assert routes["release"].call_count == 1
    assert env.db.of("assistant_plan_aborted")[0]["actor"] == BOB


async def test_pause_holds_at_the_boundary_and_only_the_approver_resumes(env):
    plan = _propose(env)
    with respx.mock(assert_all_called=False) as router:
        routes = _mock_device(router)
        started, release = _blocking_first_step(routes)
        async with _client(env) as c:
            await _approve(c, plan)
            await c.post("/api/assistant/plans/p1/execute", json={"step_hash": plan["step_hash"]})
            await asyncio.wait_for(started.wait(), 5)
            async with _client(env, user=BOB, cookies={}) as bob:
                assert (await bob.post("/api/assistant/plans/p1/pause")).json()["status"] == "paused"
                release.set()
                await asyncio.sleep(0.6)
                assert not routes["read.absorbance"].called  # held after step 1
                assert (await bob.post("/api/assistant/plans/p1/resume")).status_code == 403
            listed = (await c.get("/api/assistant/plan-runs", params={"active": "true"})).json()
            assert [r["plan_id"] for r in listed] == ["p1"] and listed[0]["paused"] is True
            assert (await c.post("/api/assistant/plans/p1/resume")).json()["status"] == "executing"
            run = await _wait_done(env, "p1")
    assert run["status"] == "executed"
    # Paused, the plan kept the device: one claim, released only at the end.
    assert routes["claim"].call_count == 1 and routes["release"].call_count == 1


async def test_measurements_are_withheld_from_other_viewers(env):
    plan = _propose(env)
    with respx.mock(assert_all_called=False) as router:
        _mock_device(router)
        async with _client(env) as c:
            await _approve(c, plan)
            await c.post("/api/assistant/plans/p1/execute", json={"step_hash": plan["step_hash"]})
            await _wait_done(env, "p1")
        async with _client(env, user=BOB, cookies={}) as bob:
            seen = (await bob.get("/api/assistant/plans/p1/run")).json()
    assert seen["results"][1]["response"] is None and seen["results"][1]["response_withheld"]


# ---------------------------------------------------------------------------
# Durability: restart recovery and the notebook
# ---------------------------------------------------------------------------


async def test_a_run_in_progress_at_restart_is_interrupted_never_resumed(env):
    run = {
        "plan_id": "old", "equipment_id": "cytation_5", "approved_by": ALICE, "status": "executing",
        "started_at": "2026-10-15T00:00:00+00:00", "finished_at": None, "step_hash": "h" * 64,
        "eln_project": None, "steps": STEPS,
        "results": [{"index": 1, "action": "drawer.close", "outcome": "ok"},
                    {"index": 2, "action": "read.absorbance", "outcome": "running"},
                    {"index": 3, "action": "drawer.open", "outcome": "pending"}],
    }
    env.store.save(run)
    await env.runner.recover()
    after = env.store.get("old")
    assert after["status"] == "interrupted"
    assert [r["outcome"] for r in after["results"]] == ["ok", "unknown", "skipped"]
    assert "unknown" in after["results"][1]["message"]
    assert env.db.of("assistant_plan_finished")[-1]["status"] == "interrupted"
    assert env.runner.live == {}


async def test_finished_run_is_journaled_for_the_notebook(env, monkeypatch, tmp_path):
    journal = PlanResultsJournal(tmp_path / "plan_results.sqlite3")
    env.app.state.plan_results_journal = journal

    async def member(client, user, project):
        return user == ALICE and project == "Plate reader QC"

    async def no_filing(journal_):
        return 0

    monkeypatch.setattr(apr, "_project_member", member)
    monkeypatch.setattr("app.plan_results.file_pending", no_filing)
    plan = _propose(env)
    with respx.mock(assert_all_called=False) as router:
        _mock_device(router)
        async with _client(env) as c:
            await _approve(c, plan)
            refused = await c.post("/api/assistant/plans/p1/execute",
                                   json={"step_hash": plan["step_hash"], "eln_project": "Someone else's"})
            assert refused.status_code == 403
            await c.post("/api/assistant/plans/p1/execute",
                         json={"step_hash": plan["step_hash"], "eln_project": "Plate reader QC"})
            run = await _wait_done(env, "p1")
    row = journal.get("dashboard:cytation_5", "p1")
    assert row is not None and row["state"] == "pending"
    bundle = row["payload"]
    assert bundle["source"] == "dashboard-assistant plan" and bundle["approved_by"] == ALICE
    assert bundle["plan"]["results"][1]["reading"]["values"] == {"A1": 0.42}
    assert run["notebook"]["state"] == "pending"
