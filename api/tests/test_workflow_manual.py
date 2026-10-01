"""Offline route + real executor tests: no lifespan, network or equipment."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from lab_skills import Lab
from lab_skills.registry import Registry
from lab_skills.locations import LocationsConfig, LocationEntry
from app import workflow as wf
from app.manual_steps import ManualJournal
from test_workflow import _auth, _package

HEADERS = {"X-Auth-User": "chemist@example.test"}


@pytest.fixture
async def rig(tmp_path, monkeypatch):
    app = FastAPI()
    app.include_router(wf.build_workflow_router())
    app.state.manual_journal = ManualJournal(tmp_path / "manual.sqlite3")
    app.state.locations_config = LocationsConfig(locations=[
        LocationEntry(name="bench/source", type="storage"),
        LocationEntry(name="bench/destination", type="storage")])
    wf._RUNS.clear()
    state = {"revoked": False, "writes": [], "unresolved": False, "location": "bench/source", "calls": [], "member": True}
    steps = [{"step_id": "carry", "kind": "manual", "manual": {"instructions": "Carry plate PLT-1 to the destination bench"},
              "custody": {"plate": "reaction", "hid": "PLT-1", "from": "bench/source", "to": "bench/destination"}},
             {"step_id": "inspect", "kind": "manual", "manual": {"instructions": "Inspect the plate and report its condition"}}]
    auth = _auth(package=_package(steps=steps), plate_bindings={"reaction": "PLT-1"})
    async def fetch(*args, **kwargs): return replace(auth, revoked_at="now", executable=False) if state["revoked"] else auth
    async def scope(*args): return {"member_projects": ["p1"] if state["member"] else []}
    async def opened(**kw): return {"opened": True, "plan_id": "plan-1"}
    async def record(**kw): state["calls"].append(kw); return {"recorded": True}
    async def audit(*args, **kw): pass
    class Ledger:
        async def current_location(self, *args, **kw): return {"found": True, "hid": "PLT-1", "location_name": state["location"]}
        async def record_move(self, **kw):
            state["writes"].append(kw)
            if state["unresolved"]: return {"recorded": False, "uncertain": True}
            state["location"] = kw["to"]
            return {"recorded": True, "action_id": "action-1"}
    monkeypatch.setattr(wf, "fetch_authorization", fetch)
    monkeypatch.setattr(wf, "member_scope", scope)
    monkeypatch.setattr(wf, "open_run_record", opened)
    monkeypatch.setattr(wf, "write_run_record", record, raising=False)
    # The final recording seam is imported under its actual name below.
    monkeypatch.setattr(wf, "_record_run_event", audit)
    monkeypatch.setattr(wf, "custody_recorder", Ledger)
    monkeypatch.setattr(wf, "lab_session", lambda *args: Lab.connect(registry=Registry(equipment=[]), binding={}))
    monkeypatch.setenv("DASHBOARD_CONTROL_OPEN", "false")
    for name in ("record_run", "close_run_record"):
        if hasattr(wf, name): monkeypatch.setattr(wf, name, record)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, app, state
    for run in list(wf._RUNS.values()):
        run.abort_requested = "test-cleanup"; run.manual_changed.set()
    await asyncio.sleep(.05)
    wf._RUNS.clear()


async def until(predicate):
    for _ in range(150):
        result = predicate()
        if result: return result
        await asyncio.sleep(.01)
    raise AssertionError("runner did not reach expected state")


async def start(client):
    response = await client.post("/api/workflow/runs", headers=HEADERS, json={"authorization_id": "ra_test"})
    assert response.status_code == 202, response.text
    run = wf._RUNS[response.json()["run_id"]]
    await until(lambda: run.waiting_on or run.result)
    assert run.waiting_on, run.result
    return run


def payload(run, outcome="done"):
    return {"request_id": run.waiting_on["request_id"], "outcome": outcome, "note": "Checked the plate"}


@pytest.mark.asyncio
async def test_route_wait_confirm_record_then_next_step_and_duplicate(rig):
    client, app, state = rig
    run = await start(client)
    assert not state["writes"]
    body = payload(run); path = f"/api/workflow/runs/{run.run_id}/manual/carry"
    response = await client.post(path, headers=HEADERS, json=body)
    assert response.status_code == 200, response.text
    await until(lambda: run.waiting_on and run.waiting_on["step_id"] == "inspect")
    assert len(state["writes"]) == 1
    assert state["writes"][0]["performed_by"] == HEADERS["X-Auth-User"]
    assert state["writes"][0]["expected_from"] == "bench/source"
    assert (await client.post(path, headers=HEADERS, json=body)).status_code == 200
    assert (await client.post(path, headers=HEADERS, json={**body, "outcome": "failed"})).status_code == 409
    assert len(state["writes"]) == 1
    response = await client.post(f"/api/workflow/runs/{run.run_id}/manual/inspect", headers=HEADERS, json=payload(run))
    assert response.status_code == 200
    await until(lambda: run.result)
    assert run.result["ok"] is True


@pytest.mark.asyncio
async def test_uncertain_record_retries_identical_fact_without_advancing(rig):
    client, app, state = rig; state["unresolved"] = True
    run = await start(client); body = payload(run); path = f"/api/workflow/runs/{run.run_id}/manual/carry"
    await client.post(path, headers=HEADERS, json=body)
    await until(lambda: run.waiting_on and run.waiting_on["state"] == "uncertain")
    assert run.waiting_on["step_id"] == "carry" and len(state["writes"]) == 1
    state["unresolved"] = False
    await client.post(path, headers=HEADERS, json=body)
    await until(lambda: run.waiting_on and run.waiting_on["step_id"] == "inspect")
    assert state["writes"][0] == state["writes"][1]


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["abort", "revoke", "failed"])
async def test_wait_remains_stoppable_and_no_later_step(rig, stop):
    client, app, state = rig; run = await start(client)
    if stop == "abort":
        assert (await client.post(f"/api/workflow/runs/{run.run_id}/abort", headers=HEADERS)).status_code == 200
    elif stop == "failed":
        await client.post(f"/api/workflow/runs/{run.run_id}/manual/carry", headers=HEADERS, json=payload(run, "failed"))
    else:
        state["revoked"] = True; run.manual_changed.set()
    await until(lambda: run.result)
    assert run.result["ok"] is False
    assert not state["writes"]
    assert len(app.state.manual_journal.for_run(run.run_id)) == 1


@pytest.mark.asyncio
async def test_missing_identity_outsider_stale_request_and_restart(rig):
    client, app, state = rig; run = await start(client)
    body = payload(run); path = f"/api/workflow/runs/{run.run_id}/manual/carry"
    assert (await client.post(path, json=body)).status_code == 401
    state["member"] = False
    assert (await client.post(path, headers=HEADERS, json=body)).status_code == 403
    state["member"] = True
    assert (await client.post(path.replace("/carry", "/wrong"), headers=HEADERS, json=body)).status_code == 409
    run.abort_requested = "restart-test"; run.manual_changed.set()
    await until(lambda: run.result)
    app.state.manual_journal.recover()
    wf._RUNS.clear()
    history = await client.get(f"/api/workflow/runs/{run.run_id}/manual", headers=HEADERS)
    assert history.status_code == 200 and history.json()["live"] is False
    assert history.json()["requests"][0]["state"] == "interrupted"
    assert (await client.post(path, headers=HEADERS, json=body)).status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["manual", "reader"])
async def test_launcher_can_watch_own_run_without_project_membership(rig, kind):
    client, _, scope = rig
    run = wf.RunState(
        run_id="run-owned", authorization_id="ra_test",
        launched_by="hermes@lab.local", dry_run=False,
        status="finished", project_id="p1",
        has_manual=kind == "manual", has_reader=kind == "reader",
    )
    run.emit("done", {"ok": True})
    wf._RUNS[run.run_id] = run
    scope["member"] = False
    path = f"/api/workflow/runs/{run.run_id}"
    for suffix in ("", "/events"):
        assert (await client.get(path + suffix)).status_code == 401
        assert (await client.get(path + suffix, headers={"X-Auth-User": "other@lab.local"})).status_code == 403
        response = await client.get(path + suffix, headers={"X-Auth-User": run.launched_by})
        assert response.status_code == 200
        if suffix:
            assert '"type": "done"' in response.text
        else:
            assert response.json()["launched_by"] == run.launched_by

    scope["member"] = True
    assert (await client.get(path, headers={"X-Auth-User": "member@lab.local"})).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["bench/wrong", None])
async def test_wrong_or_unknown_source_refuses_before_inviting_human(rig, location):
    client, app, state = rig
    state["location"] = location
    response = await client.post("/api/workflow/runs", headers=HEADERS,
                                 json={"authorization_id": "ra_test"})
    assert response.status_code == 202, response.text
    run = wf._RUNS[response.json()["run_id"]]
    await until(lambda: run.result)
    assert run.result["ok"] is False
    assert not state["writes"] and not run.waiting_on
    assert app.state.manual_journal.for_run(run.run_id) == []
