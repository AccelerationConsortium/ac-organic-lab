"""Sample lineage (PLATE_TRACKING.md D11): the pure derivation and the poster.

Two halves, and the split is the point. The compiler already expanded the well
pairs, so the derivation here does no mapping arithmetic at all — it only
decides *which declared pairs actually ran*, from the compiled steps' final
statuses. Everything these tests pin is about that decision being conservative:
a pair nothing realized is dropped, a pair whose step did not succeed is
dropped, one failed well suppresses only its own transfers, and `dose` never
gates `dose_acids`.

The poster's half pins the never-raises posture the whole record layer runs on
(`record.py` property 1): an unregistered plate, a missing well, an HTTP
refusal and a dead socket all come back as data, because the run they describe
has already physically happened.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.custody import CustodyRecorder
from app.lineage import gating_steps, post_transfers, transfers_from

BASE = "http://adb.test"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _entry(step_id="transfer_acid", *, mapping="identity",
           source="acid_stock", source_hid="PLT-A", source_well="A1",
           dest="reaction", dest_hid="PLT-R", dest_well="A1") -> dict:
    """One `lineage` entry in the shape bitácora's compiler publishes: one per
    (source plate, destination well) pair."""
    return {"step_id": step_id, "mapping": mapping,
            "source": {"plate": source, "hid": source_hid}, "source_well": source_well,
            "dest": {"plate": dest, "hid": dest_hid}, "dest_well": dest_well}


def _pkg(step_ids, lineage) -> dict:
    return {"steps": [{"step_id": s, "role": "liquid_handler", "skill": "dispense"}
                      for s in step_ids],
            "lineage": lineage}


def _all(step_ids, status="succeeded") -> dict[str, str]:
    return {s: status for s in step_ids}


# ── the pure derivation ──────────────────────────────────────────────────


def test_a_declared_pair_whose_step_succeeded_becomes_one_spec() -> None:
    steps = ["transfer_acid__A1__aspirate", "transfer_acid__A1__dispense"]
    specs = transfers_from(_pkg(steps, [_entry()]), _all(steps))
    assert len(specs) == 1
    assert specs[0]["source_hid"] == "PLT-A" and specs[0]["source_well"] == "A1"
    assert specs[0]["dest_hid"] == "PLT-R" and specs[0]["dest_well"] == "A1"
    assert specs[0]["mapping"] == "identity"
    # The anchor is the PROTOCOL step, not one of the 96 compiled ids — the row
    # belongs to the step a human authored.
    assert specs[0]["step_id"] == "transfer_acid"
    assert specs[0]["gated_by"] == steps


def test_pairwise_two_sources_into_one_well_stay_two_rows() -> None:
    """Two source plates feeding the same destination well is two entries and
    therefore two rows. The derivation must not collapse them: which plate the
    liquid came from is the entire content of a lineage row."""
    steps = ["combine__B3__aspirate_acid", "combine__B3__aspirate_amine",
             "combine__B3__dispense"]
    pkg = _pkg(steps, [
        _entry("combine", mapping="pairwise", source="acid_stock",
               source_hid="PLT-A", source_well="B3", dest_well="B3"),
        _entry("combine", mapping="pairwise", source="amine_stock",
               source_hid="PLT-N", source_well="B3", dest_well="B3"),
    ])
    specs = transfers_from(pkg, _all(steps))
    assert [s["source_hid"] for s in specs] == ["PLT-A", "PLT-N"]
    assert {s["dest_well"] for s in specs} == {"B3"}


def test_column_broadcast_is_passed_through_not_re_expanded() -> None:
    """The compiler decided that row C of the destination is fed by C1 of the
    source. Re-deriving that here would be a second copy of bitácora's mapping
    semantics — the exact duplication PLATE_TRACKING rejects — so the entry's
    own wells are carried verbatim."""
    steps = ["spread__C7__dispense"]
    pkg = _pkg(steps, [_entry("spread", mapping="column_broadcast",
                              source_well="C1", dest_well="C7")])
    specs = transfers_from(pkg, _all(steps))
    assert (specs[0]["source_well"], specs[0]["dest_well"]) == ("C1", "C7")
    assert specs[0]["mapping"] == "column_broadcast"


def test_a_well_whose_step_did_not_succeed_files_nothing() -> None:
    """`succeeded` is the only status that counts. `unknown` in particular does
    not: a step that was sent and never answered may have moved the liquid, and
    a transfer row would assert a pour that may not have happened."""
    steps = ["dose__A1__dispense", "dose__A2__dispense", "dose__A3__dispense"]
    pkg = _pkg(steps, [_entry("dose", dest_well=w) for w in ("A1", "A2", "A3")])
    specs = transfers_from(pkg, {"dose__A1__dispense": "succeeded",
                                 "dose__A2__dispense": "failed",
                                 "dose__A3__dispense": "unknown"})
    assert [s["dest_well"] for s in specs] == ["A1"]


def test_a_pair_no_compiled_step_realizes_is_dropped() -> None:
    """An entry with nothing behind it describes liquid that was never moved,
    and a ledger row is a claim about the physical world."""
    assert transfers_from(_pkg(["other__A1__dispense"], [_entry("dose")]),
                          {"other__A1__dispense": "succeeded"}) == []


def test_no_lineage_at_all_is_an_empty_list_not_an_error() -> None:
    """Every package compiled before 0.6.0, and every protocol with no mapping,
    lands here — it must be silent, not a crash."""
    steps = ["home"]
    assert transfers_from({"steps": [{"step_id": "home"}]}, _all(steps)) == []
    assert transfers_from(_pkg(steps, []), _all(steps)) == []
    assert transfers_from(_pkg(steps, None), _all(steps)) == []
    assert transfers_from({}, {}) == []


def test_dose_does_not_gate_dose_acids() -> None:
    """A bare `startswith` would let one step's success vouch for another's
    wells, and these are different steps that merely share a stem. The
    separator is what makes the prefix a boundary."""
    steps = ["dose__A1__dispense", "dose_acids__A1__dispense"]
    pkg = _pkg(steps, [_entry("dose"), _entry("dose_acids", dest_hid="PLT-R")])
    assert gating_steps(steps, {"step_id": "dose", "dest_well": "A1"}) == ["dose__A1__dispense"]
    # dose_acids failed; only its own entry is suppressed
    specs = transfers_from(pkg, {"dose__A1__dispense": "succeeded",
                                 "dose_acids__A1__dispense": "failed"})
    assert [s["step_id"] for s in specs] == ["dose"]


def test_one_failed_well_suppresses_only_its_own_transfers() -> None:
    """The narrowing rule earning its keep. Without it, one bad well in a 96-well
    dispense would make the ledger silent about the 95 that demonstrably moved —
    which is exactly backwards."""
    steps = ["dose__tip_on", "dose__A1__dispense", "dose__A2__dispense", "dose__tip_off"]
    pkg = _pkg(steps, [_entry("dose", dest_well="A1"), _entry("dose", dest_well="A2")])
    specs = transfers_from(pkg, {"dose__tip_on": "succeeded",
                                 "dose__A1__dispense": "succeeded",
                                 "dose__A2__dispense": "failed",
                                 "dose__tip_off": "succeeded"})
    assert [s["dest_well"] for s in specs] == ["A1"]
    assert specs[0]["gated_by"] == ["dose__A1__dispense"]   # the bracket steps drop out


def test_a_step_that_did_not_expand_per_well_is_gated_whole() -> None:
    """A single-skill action (a stacker present, a bulk reagent add) compiles to
    exactly its own step_id, and a flat sequence to `{step}__{sub}` — neither
    has a per-well id to narrow to, so every sub-step gates every pair."""
    assert gating_steps(["seal"], {"step_id": "seal", "dest_well": "A1"}) == ["seal"]
    flat = ["fill__aspirate", "fill__dispense"]
    assert gating_steps(flat, {"step_id": "fill", "dest_well": "A1"}) == flat
    pkg = _pkg(flat, [_entry("fill", dest_well="A1"), _entry("fill", dest_well="A2")])
    assert transfers_from(pkg, {"fill__aspirate": "succeeded",
                                "fill__dispense": "failed"}) == []


# ── the poster ───────────────────────────────────────────────────────────


def _children(rows: list[tuple[str, str]]) -> httpx.Response:
    return httpx.Response(200, json=[{"container_id": cid, "position": pos}
                                     for pos, cid in rows])


def _mock_plates() -> tuple:
    src = respx.get(f"{BASE}/containers", params={"hid": "PLT-A"}).mock(
        return_value=httpx.Response(200, json=[{"container_id": "cA", "hid": "PLT-A"}]))
    dst = respx.get(f"{BASE}/containers", params={"hid": "PLT-R"}).mock(
        return_value=httpx.Response(200, json=[{"container_id": "cR", "hid": "PLT-R"}]))
    kids_a = respx.get(f"{BASE}/containers", params={"parent_container_id": "cA"}).mock(
        return_value=_children([("A1", "wA1"), ("A2", "wA2")]))
    kids_r = respx.get(f"{BASE}/containers", params={"parent_container_id": "cR"}).mock(
        return_value=_children([("A1", "rA1"), ("A2", "rA2")]))
    return src, dst, kids_a, kids_r


async def _post(recorder, specs, **over):
    kw = dict(plan_id="p1", operator="op@lab", project="chanlam", run_id="run_x",
              authorization_id="ra_1", performed_by_lookup=lambda sid: "ot2_hte")
    kw.update(over)
    return await post_transfers(recorder, specs, **kw)


@respx.mock
@pytest.mark.anyio
async def test_a_transfer_row_points_at_the_wells_and_anchors_to_the_plan() -> None:
    """What makes this row lineage rather than another custody move: source and
    target are the *child* containers. A `move` says a plate changed place; a
    `transfer` says a well's contents have a parent."""
    _mock_plates()
    post = respx.post(f"{BASE}/container-actions").mock(
        return_value=httpx.Response(200, json={"action_id": "a1"}))
    rec = CustodyRecorder(BASE, "s3cret")
    steps = ["transfer_acid__A1__dispense"]
    specs = transfers_from(_pkg(steps, [_entry()]), _all(steps))
    out = await _post(rec, specs)

    assert out == {"emitted": 1, "failed": [], "skipped": []}
    sent = json.loads(post.calls.last.request.content)
    assert sent["action_type"] == "transfer"
    assert sent["source_container_id"] == "wA1" and sent["target_container_id"] == "rA1"
    assert sent["plan_id"] == "p1" and sent["step_id"] == "transfer_acid"
    assert sent["performed_by"] == "ot2_hte" and sent["creator"] == "op@lab"
    assert sent["project"] == "chanlam"
    assert sent["params"] == {
        "authorization_id": "ra_1", "run_id": "run_x", "mapping": "identity",
        "protocol_step_id": "transfer_acid", "via": "executor",
        "source": {"hid": "PLT-A", "well": "A1"},
        "dest": {"hid": "PLT-R", "well": "A1"},
    }
    # Amounts stay out entirely in this slice — absent, never a guessed zero.
    assert "amount_commanded" not in sent and "amount_observed" not in sent


@respx.mock
@pytest.mark.anyio
async def test_a_plates_wells_are_resolved_once_however_many_rows_it_feeds() -> None:
    """96 rows must not ask the same question 96 times — a plate's wells do not
    appear or vanish mid-run."""
    _mock_plates()
    respx.post(f"{BASE}/container-actions").mock(
        return_value=httpx.Response(200, json={"action_id": "a"}))
    rec = CustodyRecorder(BASE, "s")
    steps = ["dose__A1__dispense", "dose__A2__dispense"]
    specs = transfers_from(_pkg(steps, [_entry("dose", source_well=w, dest_well=w)
                                        for w in ("A1", "A2")]), _all(steps))
    assert (await _post(rec, specs))["emitted"] == 2
    gets = [c for c in respx.calls if c.request.method == "GET"]
    assert len(gets) == 4        # two plates × (row + children), not eight


@respx.mock
@pytest.mark.anyio
async def test_a_well_the_ledger_does_not_have_is_skipped_not_failed() -> None:
    """The plate is registered but not with that position — a modelling gap to
    fix by registering the plate properly, not by retrying."""
    _mock_plates()
    post = respx.post(f"{BASE}/container-actions").mock(
        return_value=httpx.Response(200, json={"action_id": "a"}))
    rec = CustodyRecorder(BASE, "s")
    steps = ["dose__H12__dispense"]
    specs = transfers_from(_pkg(steps, [_entry("dose", source_well="H12", dest_well="H12")]),
                           _all(steps))
    out = await _post(rec, specs)
    assert out["emitted"] == 0 and out["failed"] == []
    assert out["skipped"][0]["reason"] == "unknown_well"
    assert out["skipped"][0]["well"] == "H12" and out["skipped"][0]["side"] == "source"
    assert not post.calls              # nothing was ever offered to the ledger


@respx.mock
@pytest.mark.anyio
async def test_an_unregistered_plate_and_a_childless_one_are_both_skips() -> None:
    respx.get(f"{BASE}/containers", params={"hid": "PLT-A"}).mock(
        return_value=httpx.Response(200, json=[]))
    rec = CustodyRecorder(BASE, "s")
    out = await _post(rec, transfers_from(_pkg(["d__A1__x"], [_entry("d")]),
                                          {"d__A1__x": "succeeded"}))
    assert out["skipped"][0]["reason"] == "unknown_container"

    respx.get(f"{BASE}/containers", params={"hid": "PLT-A"}).mock(
        return_value=httpx.Response(200, json=[{"container_id": "cA"}]))
    respx.get(f"{BASE}/containers", params={"parent_container_id": "cA"}).mock(
        return_value=httpx.Response(200, json=[]))
    out = await _post(CustodyRecorder(BASE, "s"),
                      transfers_from(_pkg(["d__A1__x"], [_entry("d")]),
                                     {"d__A1__x": "succeeded"}))
    assert out["skipped"][0]["reason"] == "no_child_containers"


@respx.mock
@pytest.mark.anyio
async def test_a_refused_or_unreachable_write_lands_in_failed_and_never_raises() -> None:
    """The run has already physically happened. A provenance write that the
    ledger refuses must come back as data — raising here would turn "we could
    not file the paperwork" into a crashed run."""
    _mock_plates()
    respx.post(f"{BASE}/container-actions").mock(
        return_value=httpx.Response(422, text="unit is required"))
    rec = CustodyRecorder(BASE, "s")
    steps = ["d__A1__x"]
    specs = transfers_from(_pkg(steps, [_entry("d")]), _all(steps))
    out = await _post(rec, specs)
    assert out["emitted"] == 0 and out["skipped"] == []
    assert out["failed"][0]["reason"] == "http_422"
    assert "unit is required" in out["failed"][0]["detail"]

    respx.post(f"{BASE}/container-actions").mock(side_effect=httpx.ConnectError("refused"))
    out = await _post(CustodyRecorder(BASE, "s"), specs)
    assert out["failed"][0]["reason"] == "unreachable"

    # …and a recorder that raises outright is still only a failed row.
    class _Boom:
        async def record_transfer(self, **kw):
            raise RuntimeError("boom")

    out = await _post(_Boom(), specs)
    assert out["emitted"] == 0 and out["failed"][0]["reason"] == "raised"


@respx.mock
@pytest.mark.anyio
async def test_an_unbound_plate_is_carried_and_skipped_never_guessed() -> None:
    """The compiler OMITS `hid` when a plate was unbound at compile time; only
    an *authorized* package is guaranteed to carry them. So the key is absent,
    not null, and reading it must not be a KeyError anywhere on the path. The
    entry is still derived — the derivation is about what ran — and the poster
    declines rather than inventing an hid."""
    steps = ["d__A1__x"]
    pkg = _pkg(steps, [{"step_id": "d", "mapping": "identity",
                        "source": {"plate": "acid_stock"}, "source_well": "A1",
                        "dest": {"plate": "reaction"}, "dest_well": "A1"},
                       # …and an entry missing the blocks entirely
                       {"step_id": "d", "mapping": "identity", "dest_well": "A1"}])
    specs = transfers_from(pkg, _all(steps))
    assert len(specs) == 2 and specs[0]["source_hid"] is None
    out = await _post(CustodyRecorder(BASE, "s"), specs)
    assert [s["reason"] for s in out["skipped"]] == ["unbound_plate", "unbound_plate"]
    assert not respx.calls


@respx.mock
@pytest.mark.anyio
async def test_the_row_is_attributed_to_the_machine_that_poured() -> None:
    """`performed_by` is the equipment, `creator` the human who launched — the
    same split `record_move` makes. A step whose equipment the report could not
    resolve falls back to the launcher rather than to nobody."""
    _mock_plates()
    post = respx.post(f"{BASE}/container-actions").mock(
        return_value=httpx.Response(200, json={"action_id": "a"}))
    specs = transfers_from(_pkg(["d__A1__x"], [_entry("d")]), {"d__A1__x": "succeeded"})
    await _post(CustodyRecorder(BASE, "s"), specs, performed_by_lookup=lambda sid: None)
    assert json.loads(post.calls.last.request.content)["performed_by"] == "op@lab"
