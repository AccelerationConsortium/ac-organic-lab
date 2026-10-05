"""Unit tests for ``api/app/plan_results.py`` — OT-2 plan results into the ELN.

Mocked responses follow BitacoraDB's schemas (``experiment_id`` / ``note_id``)
and ac_auth's ``/authz/scope`` (``{user, member_projects, pi_projects,
is_admin}``). No network, no production DB: the journal lives in tmp_path.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import plan_results as pr
from app import record as rec

BASE = "http://adb.test"
AUTHZ = "http://authz.test"
TOKEN = "device-token"

BUNDLE = {
    "schema": "ot2.plan_results.v1",
    "plan_id": "nBZn1DsXVfSCWwV0",
    "device_id": "ot2_complexation",
    "equipment_id": "ot2_complexation",
    "gateway_version": "0.4.0",
    "simulation": False,
    "approved_by": "ada@lab",
    "proposed_by": "assistant",
    "eln_project": "Complexation",
    "status": "executed",
    "halt_reason": None,
    "started_at": "2026-10-05T02:46:38+00:00",
    "finished_at": "2026-10-05T03:20:49+00:00",
    "steps_total": 2,
    "steps_ok": 2,
    "steps_failed": 0,
    "steps_skipped": 0,
    "plate_report": {"stats": {"n": 96, "mean_g": 0.0995, "min_g": 0.086,
                               "max_g": 0.1131, "cv_pct": 4.9}},
    "plan": {"plan_id": "nBZn1DsXVfSCWwV0",
             "steps": [{"action": "lights.set", "args": {"on": True}},
                       {"action": "platebalance.read", "args": {}}],
             "results": [{"action": "lights.set", "outcome": "ok"},
                         {"action": "platebalance.read", "outcome": "ok",
                          "reading": {"value": 0.1004, "unit": "g", "stable": True}}]},
}


@pytest.fixture
def configured(tmp_path, monkeypatch):
    secret = tmp_path / "edge.secret"
    secret.write_text("edge-secret")
    monkeypatch.setattr(rec, "BITACORADB_URL", BASE)
    monkeypatch.setattr(rec, "BITACORADB_EDGE_SECRET_PATH", str(secret))
    monkeypatch.setenv("AUTH_SERVICE_BASE", AUTHZ)
    monkeypatch.setenv("PLAN_RESULTS_DEVICE_TOKENS", json.dumps({"ot2_complexation": TOKEN}))
    return pr.PlanResultsJournal(tmp_path / "plan_results.sqlite3")


def _scope(member: bool):
    return httpx.Response(200, json={"user": "ada@lab", "is_admin": False, "pi_projects": [],
                                     "member_projects": ["Complexation"] if member else ["Other"]})


async def _file(journal) -> list[str]:
    async with httpx.AsyncClient() as client:
        return [await pr.file_one(journal, row, client) for row in journal.pending()]


# ── filing ───────────────────────────────────────────────────────────────


@pytest.mark.anyio
@respx.mock
async def test_files_an_unformatted_experiment_and_a_note_as_the_approver(configured):
    journal = configured
    journal.accept(BUNDLE)
    respx.get(f"{AUTHZ}/authz/scope").mock(return_value=_scope(True))
    respx.get(f"{BASE}/experiments").mock(return_value=httpx.Response(200, json=[]))
    create = respx.post(f"{BASE}/experiments").mock(
        return_value=httpx.Response(201, json={"experiment_id": "exp-1"}))
    note = respx.post(f"{BASE}/notes").mock(
        return_value=httpx.Response(201, json={"note_id": "note-1"}))

    assert await _file(journal) == [pr.FILED]

    sent = json.loads(create.calls.last.request.content)
    assert sent["hid"] == "ot2_complexation-plan-nBZn1DsXVfSCWwV0"
    assert sent["title"].startswith("UNFORMATTED")
    assert sent["operator"] == "ada@lab" and sent["project"] == "Complexation"
    assert sent["meta"]["unformatted"] is True
    assert "not a Run Authorization" in sent["meta"]["approval"]
    headers = create.calls.last.request.headers
    assert headers["X-Auth-User"] == "ada@lab"
    assert headers["X-Auth-Projects"] == "Complexation"
    assert headers["X-Edge-Secret"] == "edge-secret"

    body = json.loads(note.calls.last.request.content)
    assert body["kind"] == "observation" and body["experiment_id"] == "exp-1"
    assert "UNFORMATTED" in body["body"] and "96 wells weighed" in body["body"]
    assert body["data"]["plate_report"]["stats"]["n"] == 96
    assert body["data"]["step_results"][1]["reading"]["value"] == 0.1004

    row = journal.get("ot2_complexation", "nBZn1DsXVfSCWwV0")
    assert row["state"] == pr.FILED and row["note_id"] == "note-1"
    assert journal.pending() == []


@pytest.mark.anyio
@respx.mock
async def test_an_approver_outside_the_project_is_held_not_filed(configured):
    journal = configured
    journal.accept(BUNDLE)
    respx.get(f"{AUTHZ}/authz/scope").mock(return_value=_scope(False))
    create = respx.post(f"{BASE}/experiments")

    assert await _file(journal) == [pr.HELD]
    assert not create.called
    row = journal.get("ot2_complexation", "nBZn1DsXVfSCWwV0")
    assert "not a member" in row["last_error"]
    assert journal.pending() == []


@pytest.mark.anyio
@respx.mock
async def test_a_failed_note_is_retried_without_a_second_experiment(configured):
    journal = configured
    journal.accept(BUNDLE)
    respx.get(f"{AUTHZ}/authz/scope").mock(return_value=_scope(True))
    respx.get(f"{BASE}/experiments").mock(return_value=httpx.Response(200, json=[]))
    create = respx.post(f"{BASE}/experiments").mock(
        return_value=httpx.Response(201, json={"experiment_id": "exp-1"}))
    note = respx.post(f"{BASE}/notes").mock(side_effect=[
        httpx.Response(503, text="down"),
        httpx.Response(201, json={"note_id": "note-1"}),
    ])

    assert await _file(journal) == [pr.PENDING]
    row = journal.get("ot2_complexation", "nBZn1DsXVfSCWwV0")
    assert row["experiment_id"] == "exp-1" and row["attempts"] == 1 and row["last_error"]

    assert await _file(journal) == [pr.FILED]
    assert create.call_count == 1 and note.call_count == 2


@pytest.mark.anyio
async def test_an_unconfigured_record_layer_keeps_the_bundle_pending(configured, monkeypatch):
    monkeypatch.setattr(rec, "BITACORADB_URL", "")
    configured.accept(BUNDLE)
    assert await _file(configured) == [pr.PENDING]
    assert "not configured" in configured.get("ot2_complexation", "nBZn1DsXVfSCWwV0")["last_error"]


def test_the_same_bundle_is_idempotent_and_a_different_one_conflicts(configured):
    configured.accept(BUNDLE)
    configured.accept(BUNDLE)
    assert len(configured.summaries()) == 1
    with pytest.raises(Exception) as excinfo:
        configured.accept({**BUNDLE, "status": "failed"})
    assert getattr(excinfo.value, "status_code", None) == 409


# ── the route ────────────────────────────────────────────────────────────


@pytest.fixture
def client(configured, monkeypatch):
    async def no_filing(_journal):
        return 0

    monkeypatch.setattr(pr, "file_pending", no_filing)
    app = FastAPI()
    app.state.plan_results_journal = configured
    app.include_router(pr.build_plan_results_router())
    return TestClient(app)


def test_route_accepts_a_device_with_its_token(client, configured):
    r = client.post("/api/ingest/plan-results", json=BUNDLE,
                    headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 202, r.text
    assert r.json() == {"status": "accepted", "plan_id": BUNDLE["plan_id"], "state": pr.PENDING}
    assert configured.get("ot2_complexation", BUNDLE["plan_id"])["payload"]["eln_project"] == "Complexation"
    assert client.get("/api/plan-results").json()[0]["state"] == pr.PENDING


def test_route_refuses_unknown_devices_bad_tokens_and_simulations(client, monkeypatch):
    url = "/api/ingest/plan-results"
    good = {"Authorization": f"Bearer {TOKEN}"}
    assert client.post(url, json=BUNDLE).status_code == 401
    assert client.post(url, json=BUNDLE, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.post(url, json={**BUNDLE, "device_id": "ot2_hte"}, headers=good).status_code == 401
    assert client.post(url, json={**BUNDLE, "simulation": True}, headers=good).status_code == 422
    assert client.post(url, json={**BUNDLE, "eln_project": ""}, headers=good).status_code == 422
    monkeypatch.delenv("PLAN_RESULTS_DEVICE_TOKENS")
    assert client.post(url, json=BUNDLE, headers=good).status_code == 401
