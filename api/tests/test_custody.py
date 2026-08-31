"""Custody (PLATE_TRACKING.md D5–D8): the record-layer recorder, the pure
observe/reconcile pair, and the human front door.

What these pin: a mismatch is declared ONLY on contradiction (absence of a
signal is `unobservable`, never a mismatch — `details.loaded_plate` is
bookkeeping, not a sensor); the recorder never raises into a run and only
sends `step_id` together with a `plan_id`; the human front door writes the
same ledger row the executor writes and refuses a place the registry does not
know before anything reaches the ledger.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import custody as cu
from app.custody import CustodyRecorder, Observation, build_custody_router, observe, reconcile
from lab_skills import load_locations
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCS = load_locations(REPO_ROOT / "locations.yaml")
BASE = "http://adb.test"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ── observe / reconcile (pure) ───────────────────────────────────────────

class _Comp:
    def __init__(self, state):
        self.state = state


class _Status:
    def __init__(self, details=None, components=None):
        self.details = details or {}
        self.components = components or {}


class _Snap:
    def __init__(self, status=None, fetch_error=None):
        self.status, self.fetch_error = status, fetch_error


def test_a_device_that_names_its_plate_is_the_strongest_reading():
    carrier = LOCS.by_name("cytation_5/carrier")
    snap = _Snap(_Status(details={"loaded_plate": {"plate_id": "PLT-0042"}}))
    obs = observe(snap, carrier, LOCS)
    assert obs.kind == "plate_id" and obs.value == "PLT-0042"
    assert reconcile("PLT-0042", obs) == "match"
    assert reconcile("PLT-0099", obs) == "mismatch"


def test_an_absent_loaded_plate_is_not_evidence_of_absence():
    """`details.loaded_plate` is what the orchestrator told the device, not a
    sensor — a null there must read as unobservable, never as a mismatch."""
    carrier = LOCS.by_name("cytation_5/carrier")
    assert reconcile("PLT-0042", observe(_Snap(_Status(details={"loaded_plate": None})), carrier, LOCS)) == "unobservable"
    assert reconcile("PLT-0042", observe(_Snap(_Status()), carrier, LOCS)) == "unobservable"


def test_ot2_slot_is_read_through_the_registry_alias():
    slot2 = LOCS.by_name("ot2_hte/slot_2")
    deck = {"snapshot": {"deck": {"slots": {
        "2": {"slot_state": "occupied", "labware": {"load_name": "x", "plate_id": None}},
        "4": {"slot_state": "empty", "labware": None},
    }}}}
    obs = observe(_Snap(_Status(details=deck)), slot2, LOCS)
    assert obs.kind == "presence" and obs.value is True and "slots[2]" in obs.source
    assert reconcile("PLT-1", obs) == "match"
    slot4 = LOCS.by_name("ot2_hte/slot_4")
    assert reconcile("PLT-1", observe(_Snap(_Status(details=deck)), slot4, LOCS)) == "mismatch"
    # a slot-level plate_id beats slot_state
    deck["snapshot"]["deck"]["slots"]["2"]["labware"]["plate_id"] = "PLT-7"
    assert observe(_Snap(_Status(details=deck)), slot2, LOCS).value == "PLT-7"
    # `declared` / `mismatch` are the device's intent/flag, not our observation
    deck["snapshot"]["deck"]["slots"]["2"] = {"slot_state": "declared", "labware": None}
    assert reconcile("PLT-1", observe(_Snap(_Status(details=deck)), slot2, LOCS)) == "unobservable"


def test_presence_components_and_the_gripper():
    stage = LOCS.by_name("plateloc/stage")
    assert reconcile("x", observe(_Snap(_Status(components={"stage": _Comp("in")})), stage, LOCS)) == "match"
    assert reconcile("x", observe(_Snap(_Status(components={"stage": _Comp("out")})), stage, LOCS)) == "mismatch"
    assert reconcile("x", observe(_Snap(_Status(components={"stage": _Comp("moving")})), stage, LOCS)) == "unobservable"
    gripper = LOCS.by_name("xarm_translocation/gripper")
    assert reconcile("x", observe(_Snap(_Status(details={"gripper": {"object_detected": True}})), gripper, LOCS)) == "match"
    assert reconcile("x", observe(_Snap(_Status(details={"gripper": {"object_detected": False}})), gripper, LOCS)) == "mismatch"


def test_unreachable_missing_or_placeless_is_unobservable():
    nest = LOCS.by_name("torry_pines_shaker/nest")
    assert observe(None, nest, LOCS).kind == "none"
    assert observe(_Snap(fetch_error="timeout"), nest, LOCS).kind == "none"
    assert observe(_Snap(_Status()), LOCS.by_name("bench/hte_staging"), LOCS).kind == "none"  # no equipment
    assert reconcile("x", Observation("none")) == "unobservable"


# ── the recorder ────────────────────────────────────────────────────────

@respx.mock
@pytest.mark.anyio
async def test_record_move_resolves_name_and_hid_then_posts_one_row():
    respx.get(f"{BASE}/containers").mock(return_value=httpx.Response(200, json=[{"container_id": "c1", "hid": "PLT-1"}]))
    respx.get(f"{BASE}/locations").mock(return_value=httpx.Response(200, json=[{"location_id": "l1", "name": "ot2_hte/slot_2"}]))
    post = respx.post(f"{BASE}/container-actions").mock(return_value=httpx.Response(200, json={"action_id": "a1"}))
    rec = CustodyRecorder(BASE, "s3cret")
    out = await rec.record_move(hid="PLT-1", to="ot2_hte/slot_2", performed_by="xarm_translocation",
                                recorder="me@lab", project="chanlam", plan_id="p1", step_id="incubate__place",
                                observed=Observation("presence", True, "x"), params={"run_id": "r1"})
    assert out == {"recorded": True, "action_id": "a1", "container_id": "c1", "to_location_id": "l1"}
    body = post.calls.last.request.content
    import json
    sent = json.loads(body)
    assert sent["action_type"] == "move" and sent["target_container_id"] == "c1"
    assert sent["to_location_id"] == "l1" and sent["plan_id"] == "p1" and sent["step_id"] == "incubate__place"
    assert sent["performed_by"] == "xarm_translocation" and sent["creator"] == "me@lab"
    assert sent["params"]["observed"] == {"kind": "presence", "value": True, "source": "x"}
    assert sent["project"] == "chanlam"
    assert post.calls.last.request.headers["X-Auth-Projects"] == "chanlam"
    # second move of the same plate reuses the resolutions (cached)
    await rec.record_move(hid="PLT-1", to="ot2_hte/slot_2", performed_by="h", recorder="me@lab")
    assert respx.calls.call_count == 4   # 2 GETs + 2 POSTs, not 6


@respx.mock
@pytest.mark.anyio
async def test_a_dangling_step_id_is_not_sent_without_its_plan():
    respx.get(f"{BASE}/containers").mock(return_value=httpx.Response(200, json=[{"container_id": "c1"}]))
    respx.get(f"{BASE}/locations").mock(return_value=httpx.Response(200, json=[{"location_id": "l1"}]))
    post = respx.post(f"{BASE}/container-actions").mock(return_value=httpx.Response(200, json={"action_id": "a"}))
    await CustodyRecorder(BASE, "s").record_move(hid="h", to="t", performed_by="x", recorder="u", step_id="s1")
    import json
    sent = json.loads(post.calls.last.request.content)
    assert "step_id" not in sent and sent["params"]["step_id"] == "s1"


@respx.mock
@pytest.mark.anyio
async def test_unknown_container_or_place_and_outages_are_reported_not_raised():
    respx.get(f"{BASE}/containers").mock(return_value=httpx.Response(200, json=[]))
    assert (await CustodyRecorder(BASE, "s").record_move(hid="nope", to="t", performed_by="x", recorder="u"))["reason"] == "unknown_container"
    respx.get(f"{BASE}/containers").mock(return_value=httpx.Response(200, json=[{"container_id": "c"}]))
    respx.get(f"{BASE}/locations").mock(return_value=httpx.Response(200, json=[]))
    assert (await CustodyRecorder(BASE, "s").record_move(hid="h", to="ghost/place", performed_by="x", recorder="u"))["reason"] == "unknown_location"
    respx.get(f"{BASE}/containers").mock(side_effect=httpx.ConnectError("refused"))
    out = await CustodyRecorder(BASE, "s").record_move(hid="h", to="t", performed_by="x", recorder="u")
    assert out["recorded"] is False and out["reason"] == "unreachable"


@respx.mock
@pytest.mark.anyio
async def test_current_location_joins_the_place_name():
    respx.get(f"{BASE}/containers").mock(return_value=httpx.Response(200, json=[{"container_id": "c1", "location_id": "l9", "status": "in_use"}]))
    respx.get(f"{BASE}/locations/l9").mock(return_value=httpx.Response(200, json={"name": "torry_pines_shaker/nest"}))
    cur = await CustodyRecorder(BASE, "s").current_location("PLT-1", user="u")
    assert cur["found"] is True and cur["location_name"] == "torry_pines_shaker/nest"
    respx.get(f"{BASE}/containers").mock(return_value=httpx.Response(200, json=[]))
    assert (await CustodyRecorder(BASE, "s").current_location("PLT-2", user="u"))["found"] is False


@respx.mock
@pytest.mark.anyio
async def test_where_is_this_plate_now_can_ask_past_the_container_cache():
    """The recorder caches container rows for its lifetime, which is right for
    everything `record_move` needs — but a row also carries `location_id`, and
    that is precisely what a move changes. A mid-run "where is it now" against
    the cache answers with where the plate was before this run started moving
    it, which would read as the ledger disagreeing with the run's own move."""
    containers = respx.get(f"{BASE}/containers").mock(
        return_value=httpx.Response(200, json=[{"container_id": "c1", "location_id": "l1"}]))
    respx.get(f"{BASE}/locations/l1").mock(
        return_value=httpx.Response(200, json={"name": "bench/hte_staging"}))
    rec = CustodyRecorder(BASE, "s")
    assert (await rec.current_location("PLT-1", user="u"))["location_name"] == "bench/hte_staging"

    # the plate moves; the cached row still says otherwise
    containers.mock(return_value=httpx.Response(200, json=[{"container_id": "c1", "location_id": "l2"}]))
    respx.get(f"{BASE}/locations/l2").mock(
        return_value=httpx.Response(200, json={"name": "torry_pines_shaker/nest"}))
    assert (await rec.current_location("PLT-1", user="u"))["location_name"] == "bench/hte_staging"
    fresh = await rec.current_location("PLT-1", user="u", refresh=True)
    assert fresh["location_name"] == "torry_pines_shaker/nest"


# ── the human front door ────────────────────────────────────────────────

class _FakeRecorder:
    def __init__(self, result):
        self.result, self.calls = result, []

    async def record_move(self, **kw):
        self.calls.append(kw)
        return self.result


def _app(monkeypatch, recorder, *, db=None):
    app = FastAPI()
    app.state.locations_config = LOCS
    app.state.db = db
    monkeypatch.setattr(cu, "custody_recorder", lambda: recorder)
    app.include_router(build_custody_router())
    return app


def test_a_human_move_writes_the_same_ledger_row_with_the_human_as_performer(monkeypatch):
    rec = _FakeRecorder({"recorded": True, "action_id": "a1", "container_id": "c", "to_location_id": "l"})
    rows = []

    class _Db:
        def record_equipment_event(self, device_id, event_type, **kw):
            rows.append((device_id, event_type, kw["payload"]))

    with TestClient(_app(monkeypatch, rec, db=_Db())) as client:
        r = client.post("/api/custody/move", json={"hid": "PLT-1", "to": "bench/hte_staging", "note": "moved to cool"},
                        headers={"X-Auth-User": "chemist@lab", "X-Auth-Projects": "chanlam"})
    assert r.status_code == 200, r.text
    assert r.json()["recorded"] is True
    call = rec.calls[0]
    assert call["hid"] == "PLT-1" and call["to"] == "bench/hte_staging"
    assert call["performed_by"] == "chemist@lab" and call["recorder"] == "chemist@lab"
    assert call["params"]["reason"] == "bench" and call["params"]["note"] == "moved to cool"
    assert call["project"] == "chanlam"
    kinds = [(d, e) for d, e, _ in rows]
    assert ("custody", "control_action") in kinds and ("custody", "plate_moved") in kinds


def test_a_place_the_registry_does_not_know_never_reaches_the_ledger(monkeypatch):
    rec = _FakeRecorder({"recorded": True})
    with TestClient(_app(monkeypatch, rec)) as client:
        r = client.post("/api/custody/move", json={"hid": "PLT-1", "to": "shaker/nest"}, headers={"X-Auth-User": "u"})
    assert r.status_code == 422 and rec.calls == []


def test_front_door_maps_recorder_outcomes_to_http(monkeypatch):
    with TestClient(_app(monkeypatch, _FakeRecorder({"recorded": False, "reason": "unknown_container"}))) as client:
        assert client.post("/api/custody/move", json={"hid": "nope", "to": "bench/hte_staging"}, headers={"X-Auth-User": "u"}).status_code == 404
    with TestClient(_app(monkeypatch, _FakeRecorder({"recorded": False, "reason": "unknown_location"}))) as client:
        assert client.post("/api/custody/move", json={"hid": "PLT-1", "to": "bench/hte_staging"}, headers={"X-Auth-User": "u"}).status_code == 422
    with TestClient(_app(monkeypatch, None)) as client:
        assert client.post("/api/custody/move", json={"hid": "PLT-1", "to": "bench/hte_staging"}, headers={"X-Auth-User": "u"}).status_code == 503
