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
    assert respx.calls.call_count == 5   # fresh plate location, cached destination


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


@respx.mock
@pytest.mark.anyio
async def test_move_retries_lost_response_with_identical_request_and_expected_location():
    import json
    respx.get(f"{BASE}/containers").mock(return_value=httpx.Response(200, json=[{"container_id": "c1", "location_id": "changed"}]))
    def locations(request):
        name = request.url.params["name"]
        return httpx.Response(200, json=[{"location_id": {"from": "l0", "to": "l1"}[name]}])
    respx.get(f"{BASE}/locations").mock(side_effect=locations)
    post = respx.post(f"{BASE}/container-actions").mock(side_effect=[
        httpx.ReadTimeout("response lost"), httpx.Response(200, json={"action_id": "original"})])
    result = await CustodyRecorder(BASE, "s").record_move(
        hid="plate", to="to", performed_by="device", recorder="u", expected_from="from",
        client_action_id="auth:run:step:plate")
    assert result["recorded"] is True and result["action_id"] == "original"
    first, second = [json.loads(call.request.content) for call in post.calls]
    assert first == second
    assert first["expected_location_id"] == "l0", "use the run expectation, not the later cache"
    assert first["client_action_id"] == "auth:run:step:plate"


@respx.mock
@pytest.mark.anyio
@pytest.mark.parametrize("status,attempts,uncertain", [(409, 1, False), (502, 2, True)])
async def test_move_distinguishes_conflict_from_uncertain_failure(status, attempts, uncertain):
    respx.get(f"{BASE}/containers").mock(return_value=httpx.Response(200, json=[{"container_id": "c1"}]))
    respx.get(f"{BASE}/locations").mock(return_value=httpx.Response(200, json=[{"location_id": "l1"}]))
    post = respx.post(f"{BASE}/container-actions").mock(return_value=httpx.Response(status, text="refused"))
    result = await CustodyRecorder(BASE, "s").record_move(hid="p", to="t", performed_by="d", recorder="u")
    assert result["recorded"] is False and result["uncertain"] is uncertain
    assert post.call_count == attempts


@pytest.mark.parametrize("drawer", ["in", "out", "unknown"])
def test_cytation_drawer_position_does_not_observe_plate_presence(drawer):
    carrier = LOCS.by_name("cytation_5/carrier")
    status = _Status(details={"drawer": drawer, "loaded_plate": None,
                             "plate_in_reader": False},
                     components={"plate_stage": _Comp(drawer)})
    observation = observe(_Snap(status), carrier, LOCS)
    assert observation.kind == "none"
    assert reconcile("PLT-1", observation) == "unobservable"
    status.details["loaded_plate"] = {"plate_id": "OTHER-PLATE"}
    assert reconcile("PLT-1", observe(_Snap(status), carrier, LOCS)) == "mismatch"


# ── seating (BitacoraDB contract 0.16.0; PLATE_TRACKING.md §11, G2 reads) ─────────
#
# The record layer may now seat a container on another container's site; its
# place is the *resolved* one. Every reader here must see through seating and
# fall back to the raw cache only against an older record layer.

from app.custody import (  # noqa: E402
    build_lab_map,
    container_view,
    expected_placement_guard,
    resolved_location_id,
    seat_of,
)
from lab_skills import load_platforms  # noqa: E402

PLATFORMS = load_platforms(REPO_ROOT / "platforms.yaml")

SEATED_VIAL = {
    "hid": "V-0107", "container_id": "v1", "container_type": "vial", "status": "in_use",
    "location_id": None, "seated_on_container_id": "r1", "seated_at_site": "B3",
    "resolved_location_id": "l3", "seat_conflict": False,
    "seating_chain": [{"container_id": "r1", "hid": "RK-003", "site": "B3", "readable": True}],
}


def test_resolved_place_wins_and_the_raw_cache_is_only_a_fallback():
    assert resolved_location_id(SEATED_VIAL) == "l3"                      # 0.16.0: derived place
    assert resolved_location_id({"location_id": "l1"}) == "l1"            # 0.15.1 row: raw cache
    assert resolved_location_id({"location_id": "l1", "resolved_location_id": None}) is None
    assert seat_of(SEATED_VIAL) == {"container_id": "r1", "hid": "RK-003", "site": "B3", "readable": True}
    assert seat_of({"location_id": "l1"}) is None
    # a carrier the caller may not read arrives masked — still a seat, unreadable
    masked = {**SEATED_VIAL, "seated_on_container_id": None,
              "seating_chain": [{"container_id": None, "hid": None, "site": "B3", "readable": False}]}
    assert seat_of(masked) == {"container_id": None, "hid": None, "site": "B3", "readable": False}


def test_the_move_guard_names_the_seat_when_seated_and_the_place_otherwise():
    assert expected_placement_guard(SEATED_VIAL) == {
        "expected_seated_on_container_id": "r1", "expected_seated_at_site": "B3"}
    assert expected_placement_guard({"location_id": "l1"}) == {"expected_location_id": "l1"}
    assert expected_placement_guard({"location_id": None}) == {"expected_location_id": None}


@respx.mock
@pytest.mark.anyio
async def test_current_location_of_a_seated_vial_is_the_rack_s_slot():
    respx.get(f"{BASE}/containers").mock(return_value=httpx.Response(200, json=[SEATED_VIAL]))
    respx.get(f"{BASE}/locations/l3").mock(return_value=httpx.Response(200, json={"name": "ot2_complexation/slot_3"}))
    cur = await CustodyRecorder(BASE, "s").current_location("V-0107", user="u")
    assert cur["found"] is True
    assert cur["location_id"] == "l3" and cur["location_name"] == "ot2_complexation/slot_3"
    assert cur["raw_location_id"] is None and cur["seat"]["hid"] == "RK-003"


@respx.mock
@pytest.mark.anyio
async def test_moving_a_seated_vial_guards_on_its_seat_not_a_null_place():
    import json
    respx.get(f"{BASE}/containers").mock(return_value=httpx.Response(200, json=[SEATED_VIAL]))
    respx.get(f"{BASE}/locations").mock(return_value=httpx.Response(200, json=[{"location_id": "l9", "name": "bench/hte_staging"}]))
    post = respx.post(f"{BASE}/container-actions").mock(return_value=httpx.Response(200, json={"action_id": "a1"}))
    out = await CustodyRecorder(BASE, "s").record_move(hid="V-0107", to="bench/hte_staging", performed_by="me", recorder="me")
    assert out["recorded"] is True
    sent = json.loads(post.calls.last.request.content)
    assert sent["expected_seated_on_container_id"] == "r1" and sent["expected_seated_at_site"] == "B3"
    assert "expected_location_id" not in sent


@respx.mock
@pytest.mark.anyio
async def test_history_asks_for_carried_moves_only_from_a_record_layer_that_answers_them():
    cu._CONTRACT_CACHE.clear()
    actions = respx.get(f"{BASE}/container-actions").mock(return_value=httpx.Response(200, json=[]))
    status = respx.get(f"{BASE}/status").mock(return_value=httpx.Response(200, json={"details": {"schema_version": "0.15.1"}}))
    await CustodyRecorder(BASE, "s").history("c1", user="u")
    assert "carried" not in actions.calls.last.request.url.params
    cu._CONTRACT_CACHE.clear()
    status.mock(return_value=httpx.Response(200, json={"details": {"schema_version": "0.16.0"}}))
    await CustodyRecorder(BASE, "s").history("c1", user="u")
    assert actions.calls.last.request.url.params["carried"] == "true"
    # the probe is cached: a second history read does not ask /status again
    n = status.call_count
    await CustodyRecorder(BASE, "s").history("c1", user="u")
    assert status.call_count == n
    cu._CONTRACT_CACHE.clear()


def _ledger_places():
    return {
        "l3": {"location_id": "l3", "name": "ot2_complexation/slot_3", "equipment_id": "ot2_complexation"},
        "lb": {"location_id": "lb", "name": "bench/hte_staging", "equipment_id": None},
        "lg": {"location_id": "lg", "name": "ghost/shelf", "equipment_id": None, "location_type": "storage"},
    }


def test_lab_map_files_containers_at_their_resolved_place_and_nests_occupants():
    rack = {"hid": "RK-003", "container_id": "r1", "container_type": "rack", "status": "in_use",
            "location_id": "l3", "resolved_location_id": "l3", "meta": {"sites": ["A1", "B3"]},
            "seating_chain": [], "seat_conflict": False}
    vial2 = {**SEATED_VIAL, "hid": "V-0108", "container_id": "v2", "seat_conflict": True}
    conflicting = {**SEATED_VIAL, "seat_conflict": True}
    loose = {"hid": "PLT-1", "container_id": "p1", "container_type": "plate", "status": "empty",
             "location_id": "lb", "resolved_location_id": "lb", "seating_chain": []}
    well = {"hid": "PLT-1:A1", "container_id": "w1", "parent_container_id": "p1", "location_id": None}
    lost = {"hid": "PLT-9", "container_id": "p9", "container_type": "plate", "location_id": None,
            "resolved_location_id": None, "seating_chain": []}
    ghost = {"hid": "PLT-G", "container_id": "pg", "container_type": "plate", "location_id": "lg",
             "resolved_location_id": "lg", "seating_chain": []}
    masked = {"hid": "V-0200", "container_id": "v3", "container_type": "vial", "location_id": None,
              "seated_on_container_id": None, "seated_at_site": "C1", "resolved_location_id": "l3",
              "seating_chain": [{"container_id": None, "hid": None, "site": "C1", "readable": False}]}

    out = build_lab_map([rack, conflicting, vial2, loose, well, lost, ghost, masked], _ledger_places(),
                        registry=LOCS, platforms=PLATFORMS)

    sections = {s["id"]: s for s in out["sections"]}
    assert "complexation" in sections and sections["complexation"]["title"] == "Complexation Platform"
    slot3 = next(p for p in sections["complexation"]["places"] if p["name"] == "ot2_complexation/slot_3")
    assert slot3["registered"] is True
    # the rack is at the slot, with both vials nested under it at B3 — flagged
    hids = {c["hid"]: c for c in slot3["containers"]}
    assert set(hids) == {"RK-003", "V-0200"}
    rack_node = hids["RK-003"]
    assert rack_node["sites"] == ["A1", "B3"]
    assert [(o["hid"], o["seat"]["site"], o["seat_conflict"]) for o in rack_node["occupants"]] == [
        ("V-0107", "B3", True), ("V-0108", "B3", True)]
    # a vial on a carrier the caller cannot see is at the slot, inside something unseen
    assert hids["V-0200"]["chain_masked"] is True and hids["V-0200"]["occupants"] == []
    # a loose plate at a bench place; wells are never nodes
    other = sections["other"]
    bench = next(p for p in other["places"] if p["name"] == "bench/hte_staging")
    assert [c["hid"] for c in bench["containers"]] == ["PLT-1"]
    assert all(c["hid"] != "PLT-1:A1" for p in other["places"] for c in p["containers"])
    # every active registry place appears, with or without containers
    assert any(p["name"] == "ot2_hte/slot_1" and p["containers"] == [] for s in out["sections"] for p in s["places"])
    assert all(p["name"] != "retired/place" for s in out["sections"] for p in s["places"])
    # a ledger place the registry no longer lists is still shown, marked
    ghost_place = next(p for p in other["places"] if p["name"] == "ghost/shelf")
    assert ghost_place["registered"] is False and [c["hid"] for c in ghost_place["containers"]] == ["PLT-G"]
    # never placed
    assert [c["hid"] for c in out["unplaced"]] == ["PLT-9"]
    assert out["counts"] == {"containers": 7, "placed": 6}
    # the platform order is platforms.yaml's; the catch-all is last
    assert out["sections"][-1]["id"] == "other"


def test_lab_map_against_a_pre_seating_record_layer_reads_the_raw_cache():
    plate = {"hid": "PLT-1", "container_id": "p1", "container_type": "plate", "location_id": "l3"}
    out = build_lab_map([plate], _ledger_places(), registry=LOCS, platforms=PLATFORMS)
    slot3 = next(p for s in out["sections"] for p in s["places"] if p["name"] == "ot2_complexation/slot_3")
    assert [c["hid"] for c in slot3["containers"]] == ["PLT-1"]
    view = container_view(plate, _ledger_places())
    assert view["location"] == "ot2_complexation/slot_3" and view["seat"] is None and view["sites"] is None


def test_map_route_reads_through_and_reports_an_unreachable_ledger(monkeypatch):
    class _MapRecorder:
        async def lab_map(self, **kw):
            assert kw["registry"] is LOCS and kw["platforms"] is PLATFORMS
            return {"sections": [], "unplaced": [], "counts": {"containers": 0, "placed": 0}}
    app = _app(monkeypatch, _MapRecorder())
    app.state.platforms_config = PLATFORMS
    with TestClient(app) as client:
        assert client.get("/api/custody/map", headers={"X-Auth-User": "u"}).json()["counts"]["containers"] == 0

    class _Down:
        async def lab_map(self, **kw):
            raise httpx.ConnectError("refused")
    with TestClient(_app(monkeypatch, _Down())) as client:
        assert client.get("/api/custody/map", headers={"X-Auth-User": "u"}).status_code == 502
    with TestClient(_app(monkeypatch, None)) as client:
        assert client.get("/api/custody/map", headers={"X-Auth-User": "u"}).status_code == 503
