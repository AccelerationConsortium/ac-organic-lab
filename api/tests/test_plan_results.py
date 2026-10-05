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
    respx.get(f"{BASE}/notes").mock(return_value=httpx.Response(200, json=[]))
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
    respx.get(f"{BASE}/notes").mock(return_value=httpx.Response(200, json=[]))
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
@respx.mock
async def test_a_note_filed_before_a_crash_is_adopted_not_duplicated(configured):
    """The note POST succeeded but the journal update never happened (crash):
    the retry must find that note, not file a second observation."""
    journal = configured
    journal.accept(BUNDLE)
    journal.update("ot2_complexation", BUNDLE["plan_id"], experiment_id="exp-1")
    respx.get(f"{AUTHZ}/authz/scope").mock(return_value=_scope(True))
    respx.get(f"{BASE}/notes").mock(return_value=httpx.Response(200, json=[
        {"note_id": "other", "data": {"source": "ot2-gateway plan", "plan_id": "different",
                                      "device_id": "ot2_complexation"}},
        {"note_id": "note-1", "data": {"source": "ot2-gateway plan",
                                       "plan_id": BUNDLE["plan_id"],
                                       "device_id": "ot2_complexation"}},
    ]))
    post = respx.post(f"{BASE}/notes")

    assert await _file(journal) == [pr.FILED]
    assert not post.called
    assert journal.get("ot2_complexation", BUNDLE["plan_id"])["note_id"] == "note-1"


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
    assert client.get("/api/plan-results").status_code == 404  # the open list is gone


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


# ── filing status for the device; the picker's project list ──────────────


def test_the_device_can_read_what_became_of_its_bundle(client, configured):
    headers = {"Authorization": f"Bearer {TOKEN}"}
    client.post("/api/ingest/plan-results", json=BUNDLE, headers=headers)
    configured.update("ot2_complexation", BUNDLE["plan_id"], state=pr.HELD,
                      last_error="BitacoraDB refused the Experiment: project 'x' does not exist")
    r = client.get(f"/api/ingest/plan-results/ot2_complexation/{BUNDLE['plan_id']}", headers=headers)
    assert r.status_code == 200
    assert r.json()["state"] == pr.HELD and "does not exist" in r.json()["last_error"]
    assert client.get(f"/api/ingest/plan-results/ot2_complexation/{BUNDLE['plan_id']}").status_code == 401
    assert client.get("/api/ingest/plan-results/ot2_complexation/nope", headers=headers).status_code == 404
    assert client.get(f"/api/ingest/plan-results/ot2_hte/{BUNDLE['plan_id']}", headers=headers).status_code == 401


def _projects_mock(scope: dict, titles: list[str]):
    respx.get(f"{AUTHZ}/authz/scope").mock(return_value=httpx.Response(200, json={"user": "ada@lab", **scope}))
    return respx.get(f"{BASE}/projects").mock(
        return_value=httpx.Response(200, json=[{"title": t, "project_id": str(i)} for i, t in enumerate(titles)]))


@respx.mock
def test_the_picker_lists_eln_projects_the_user_has_standing_in(client):
    listing = _projects_mock({"member_projects": ["a"], "pi_projects": ["b"], "is_admin": False},
                             ["a", "b", "c"])
    r = client.get("/api/assistant/eln-projects", headers={"X-Auth-User": "ada@lab"})
    assert r.json() == {"configured": True, "projects": ["a", "b"]}
    assert "X-Auth-Role" not in listing.calls.last.request.headers


@respx.mock
def test_the_picker_never_offers_a_roster_project_missing_from_the_eln(client):
    _projects_mock({"member_projects": [], "pi_projects": ["basf-solubility", "sdl-safety-agent"],
                    "is_admin": False}, ["sdl-safety-agent"])
    r = client.get("/api/assistant/eln-projects", headers={"X-Auth-User": "ada@lab"})
    assert r.json()["projects"] == ["sdl-safety-agent"]


@respx.mock
def test_an_admin_sees_every_eln_project(client):
    listing = _projects_mock({"member_projects": [], "pi_projects": [], "is_admin": True}, ["a", "c"])
    r = client.get("/api/assistant/eln-projects", headers={"X-Auth-User": "ada@lab"})
    assert r.json()["projects"] == ["a", "c"]
    assert listing.calls.last.request.headers["X-Auth-Role"] == "admin"


def test_the_picker_needs_a_signed_in_user_and_a_configured_eln(client, monkeypatch):
    assert client.get("/api/assistant/eln-projects").status_code == 401
    monkeypatch.setattr(rec, "BITACORADB_URL", "")
    r = client.get("/api/assistant/eln-projects", headers={"X-Auth-User": "ada@lab"})
    assert r.json() == {"configured": False, "projects": []}


@respx.mock
def test_the_results_list_needs_a_user_and_shows_only_their_runs(client, configured):
    client.post("/api/ingest/plan-results", json=BUNDLE, headers={"Authorization": f"Bearer {TOKEN}"})
    assert client.get("/api/assistant/plan-results").status_code == 401

    def scope_for(user, **kw):
        return httpx.Response(200, json={"user": user, "member_projects": [], "pi_projects": [],
                                         "is_admin": False, **kw})

    route = respx.get(f"{AUTHZ}/authz/scope")
    route.mock(return_value=scope_for("carol@lab"))
    assert client.get("/api/assistant/plan-results", headers={"X-Auth-User": "carol@lab"}).json() == []
    route.mock(return_value=scope_for("ada@lab"))  # the approver
    rows = client.get("/api/assistant/plan-results", headers={"X-Auth-User": "ada@lab"}).json()
    assert [r["plan_id"] for r in rows] == [BUNDLE["plan_id"]] and rows[0]["eln_project"] == "Complexation"
    route.mock(return_value=scope_for("pi@lab", pi_projects=["Complexation"]))
    assert len(client.get("/api/assistant/plan-results", headers={"X-Auth-User": "pi@lab"}).json()) == 1
