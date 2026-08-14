"""Unit tests for ``api/app/record.py`` — the D-23 run-record write.

The three properties the module exists to hold get a test each, because each
one is a way the record layer could quietly lie: a failed write must not fail
the run, an unresolvable Experiment must not silently drop the notes that
matter, and an unconfigured deployment must be a no-op rather than an error.

The mocked responses mirror AnaliticaDB's own OpenAPI schemas — `ExperimentRead`
and `PlanRead` expose `experiment_id` / `plan_id`, **not** a generic `id`. An
earlier draft of these tests mocked `{"id": ...}`, which matched the client's
first draft and hid a KeyError that the never-raise wrapper would have turned
into a permanently silent "record write failed".
"""

from __future__ import annotations

import functools
import json
import os

import httpx
import pytest
import respx

from app import record as rec
from app.record import RunRecorder, experiment_hid

BASE = "http://adb.test"
PLAN = {"project": "chanlam", "protocol_path": "protocols/plate-1.yaml",
        "source_commit": "8227820abc", "steps": [], "meta": {}}
NOTES = [{"kind": "device_fault", "step_id": "s9", "body": "gripper stalled", "data": {}}]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ── experiment identity ────────────────────────────────────────────────


class TestExperimentHid:

    def test_a_shared_design_is_the_campaign(self):
        """Two plates of one design must land under ONE Experiment, so the
        design's slug wins over the per-plate protocol path."""
        assert experiment_hid(design_ref="designs/chanlam-v1.yaml",
                              protocol_path="protocols/plate-2.yaml") == "chanlam-v1"

    def test_a_protocol_without_a_design_still_gets_one(self):
        """Property 2: notes require an experiment_id, so a pre-shared-design
        protocol must still resolve to something rather than lose its notes."""
        assert experiment_hid(design_ref=None,
                              protocol_path="protocols/plate-1.yaml") == "plate-1"

    def test_neither_is_still_not_empty(self):
        assert experiment_hid(design_ref=None, protocol_path="") == "unfiled"


# ── the happy path ─────────────────────────────────────────────────────


@respx.mock
@pytest.mark.anyio
async def test_a_run_is_filed_as_a_plan_with_its_notes():
    respx.get(f"{BASE}/experiments").mock(return_value=httpx.Response(200, json=[]))
    exp = respx.post(f"{BASE}/experiments").mock(
        return_value=httpx.Response(200, json={"experiment_id": "exp-1"}))
    plan = respx.post(f"{BASE}/plans").mock(
        return_value=httpx.Response(200, json={"plan_id": "plan-1"}))
    note = respx.post(f"{BASE}/notes").mock(return_value=httpx.Response(200, json={"note_id": "n1"}))

    out = await RunRecorder(BASE, "s3cret").write(
        plan=PLAN, notes=NOTES, design_ref="designs/chanlam-v1.yaml",
        operator="me@lab", started_at="2026-08-13T00:00:00Z")

    assert out["written"] is True
    assert out["plan_id"] == "plan-1" and out["experiment_id"] == "exp-1"
    assert out["notes_written"] == 1
    assert exp.called and plan.called and note.called
    # The Plan carries the identity thread and a human-readable title.
    body = json.loads(plan.calls[0].request.content)
    assert body["experiment_id"] == "exp-1"
    assert body["title"] == "plate-1 @ 8227820"
    assert body["creator"] == "me@lab"
    # The note is anchored to both the experiment and the plan.
    nbody = json.loads(note.calls[0].request.content)
    assert nbody["experiment_id"] == "exp-1"
    assert nbody["plan_id"] == "plan-1"
    assert nbody["step_id"] == "s9"


@respx.mock
@pytest.mark.anyio
async def test_an_existing_experiment_is_reused_not_recreated():
    """A campaign's second plate must not open a second Experiment."""
    respx.get(f"{BASE}/experiments").mock(
        return_value=httpx.Response(200, json=[{"experiment_id": "exp-existing", "hid": "chanlam-v1"}]))
    created = respx.post(f"{BASE}/experiments").mock(return_value=httpx.Response(200, json={"experiment_id": "x"}))
    respx.post(f"{BASE}/plans").mock(return_value=httpx.Response(200, json={"plan_id": "plan-2"}))

    out = await RunRecorder(BASE, "s").write(
        plan=PLAN, notes=[], design_ref="designs/chanlam-v1.yaml",
        operator="me@lab", started_at="2026-08-13T00:00:00Z")

    assert out["experiment_id"] == "exp-existing"
    assert not created.called, "reused the campaign's Experiment, so none is created"


# ── property 1: a failed write is never a failed run ───────────────────


@respx.mock
@pytest.mark.anyio
async def test_a_record_layer_outage_is_reported_not_raised():
    """The run already happened. Raising here would turn 'sealed the plate but
    could not file the paperwork' into a crashed run."""
    respx.get(f"{BASE}/experiments").mock(side_effect=httpx.ConnectError("refused"))

    out = await RunRecorder(BASE, "s").write(
        plan=PLAN, notes=NOTES, design_ref=None,
        operator="me@lab", started_at="2026-08-13T00:00:00Z")

    assert out["written"] is False
    assert "refused" in out["error"]
    # The notes that could not be filed are counted, not silently dropped.
    assert out["notes_pending"] == 1
    assert out["experiment_hid"] == "plate-1"


@respx.mock
@pytest.mark.anyio
async def test_one_rejected_note_does_not_cost_the_others():
    respx.get(f"{BASE}/experiments").mock(return_value=httpx.Response(200, json=[{"experiment_id": "e"}]))
    respx.post(f"{BASE}/plans").mock(return_value=httpx.Response(200, json={"plan_id": "p"}))
    respx.post(f"{BASE}/notes").mock(side_effect=[
        httpx.Response(422, json={"detail": "bad kind"}),
        httpx.Response(200, json={"note_id": "n2"}),
    ])

    out = await RunRecorder(BASE, "s").write(
        plan=PLAN, notes=[{"kind": "?", "body": "a", "step_id": "s1"},
                          {"kind": "deviation", "body": "b", "step_id": "s2"}],
        design_ref=None, operator="me@lab", started_at="2026-08-13T00:00:00Z")

    assert out["written"] is True
    assert out["notes_written"] == 1
    assert out["notes_failed"][0]["step_id"] == "s1"


# ── property 3: off unless configured ──────────────────────────────────


@pytest.mark.anyio
async def test_an_unconfigured_deployment_is_a_no_op_not_an_error(monkeypatch):
    monkeypatch.setattr(rec, "ANALITICADB_URL", "")
    out = await rec.write_run_record(plan=PLAN, notes=NOTES, design_ref=None,
                                     operator="me@lab", started_at="2026-08-13T00:00:00Z")
    assert out == {"written": False, "reason": "not_configured"}


@pytest.mark.anyio
async def test_a_url_without_a_secret_stays_off(monkeypatch):
    """Half-configured is off, not a request with an empty edge secret — which
    AnaliticaDB would reject anyway, once per run, forever."""
    monkeypatch.setattr(rec, "ANALITICADB_URL", BASE)
    monkeypatch.setattr(rec, "ANALITICADB_EDGE_SECRET_PATH", "")
    out = await rec.write_run_record(plan=PLAN, notes=NOTES, design_ref=None,
                                     operator="me@lab", started_at="2026-08-13T00:00:00Z")
    assert out["reason"] == "not_configured"


# ── note kinds: the executor's vocabulary vs the record layer's enum ────
#
# AnaliticaDB accepts only {observation, event, deviation, comment}. The
# executor emits `device_fault` and `outcome_unknown` too, so without the
# translation below every note for a FAILED or UNKNOWN step — the two that
# matter — would be rejected 422 and lost.


#: The enum as of 2026-08-13, used when AnaliticaDB is unreachable (CI, a dev
#: laptop) so these tests never depend on the network. `adb_note_kinds` prefers
#: the live spec, because a literal typed here is a snapshot that drifts — and
#: the failure it would hide is a note being rejected and a step's failure
#: record silently lost.
FALLBACK_NOTE_KINDS = frozenset({"observation", "event", "deviation", "comment"})


@functools.lru_cache(maxsize=2)
def _live_enum(path: str, prop: str) -> frozenset[str] | None:
    """An enum from AnaliticaDB's own OpenAPI — the request body of `path`'s
    POST, property `prop`.

    `None` when the record layer is not configured or not reachable, so the
    caller falls back to its literal. Never raises: an offline test run must
    still be a test run, just a slightly weaker one.
    """
    base = (os.environ.get("ANALITICADB_URL") or "").rstrip("/")
    if not base:
        return None
    try:
        spec = httpx.get(f"{base}/openapi.json", timeout=3.0).raise_for_status().json()
        comp = spec["components"]["schemas"]
        deref = lambda x: comp[x["$ref"].split("/")[-1]] if "$ref" in x else x  # noqa: E731
        body = deref(spec["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"])
        return frozenset(deref(body["properties"][prop])["enum"]) or None
    except Exception:
        return None


def _live_note_kinds() -> frozenset[str] | None:
    return _live_enum("/notes", "kind")


def _live_plan_statuses() -> frozenset[str] | None:
    return _live_enum("/plans/{plan_id}/status", "status")


@pytest.fixture(scope="session")
def adb_note_kinds() -> frozenset[str]:
    """What the record layer actually accepts — live when we can reach it."""
    return _live_note_kinds() or FALLBACK_NOTE_KINDS


class TestNoteKindTranslation:

    def test_every_executor_kind_maps_into_the_record_layer_enum(self, adb_note_kinds):
        from app.record import NOTE_KIND
        for reported, mapped in NOTE_KIND.items():
            assert mapped in adb_note_kinds, f"{reported} -> {mapped} would 422"

    def test_an_unmapped_kind_still_lands_somewhere_valid(self, adb_note_kinds):
        """A new executor kind must degrade to a valid enum value, never be
        posted verbatim and rejected."""
        out = rec.note_for_record({"kind": "something_new", "body": "b"})
        assert out["kind"] in adb_note_kinds

    def test_an_unanswered_step_is_never_filed_as_a_deviation(self, adb_note_kinds):
        """`notes_from` is explicit: a note reading "deviation" invites someone
        to re-run a step that may already have moved liquid."""
        out = rec.note_for_record({"kind": "outcome_unknown", "body": "no answer"})
        assert out["kind"] != "deviation"
        assert out["kind"] in adb_note_kinds

    def test_the_executors_own_word_survives_the_translation(self):
        out = rec.note_for_record({"kind": "device_fault", "body": "stalled",
                                   "data": {"status": "failed"}})
        assert out["kind"] == "deviation"
        assert out["data"]["kind_reported"] == "device_fault"
        assert out["data"]["status"] == "failed", "existing data is preserved"


def test_the_offline_fallback_still_matches_the_live_enum(adb_note_kinds):
    """Keep the snapshot honest.

    Only meaningful where AnaliticaDB is reachable (the lab host); elsewhere the
    fixture *is* the fallback and this is a tautology. It fails loudly rather
    than warns because a stale literal makes every other test in this class
    weaker without saying so.
    """
    live = _live_note_kinds()
    if live is None:
        pytest.skip("AnaliticaDB unreachable — nothing to compare the snapshot against")
    assert live == FALLBACK_NOTE_KINDS, (
        f"AnaliticaDB's NoteKind enum changed: live={sorted(live)}, "
        f"FALLBACK_NOTE_KINDS={sorted(FALLBACK_NOTE_KINDS)}. Update the literal, "
        "and check app.record.NOTE_KIND still maps into it."
    )



# ── plan status: a filed run must not sit in the catalog as a draft ─────
#
# AnaliticaDB creates a Plan as `draft`. Left there, every run we file reads as
# a plan nobody carried out — the row says a run happened, the status says it
# never did.


#: As of 2026-08-13; `adb_plan_statuses` prefers the live spec for the same
#: reason `adb_note_kinds` does — a literal here is a snapshot that drifts.
FALLBACK_PLAN_STATUSES = frozenset({"draft", "approved", "executing", "completed", "abandoned"})


@pytest.fixture(scope="session")
def adb_plan_statuses() -> frozenset[str]:
    return _live_plan_statuses() or FALLBACK_PLAN_STATUSES


def _plan_meta(**kw):
    return {**PLAN, "meta": {"authorization_id": "ra_x", **kw}}


class TestPlanStatus:

    def test_every_status_we_post_is_in_the_enum(self, adb_plan_statuses):
        from app.record import PLAN_STATUS
        for k, v in PLAN_STATUS.items():
            assert v in adb_plan_statuses, f"{k} -> {v} would 422"

    def test_a_successful_run_completes(self):
        assert rec._terminal_status({"ok": True}) == "completed"

    def test_a_failed_or_aborted_run_is_abandoned(self):
        """It did not complete. The *why* lives in the notes and meta.ok."""
        assert rec._terminal_status({"ok": False}) == "abandoned"

    def test_a_dry_run_stays_draft(self):
        """A preflight never touched hardware; `completed` would claim it did."""
        assert rec._terminal_status({"ok": True, "dry_run": True}) is None


@respx.mock
@pytest.mark.anyio
async def test_a_real_run_posts_its_terminal_status():
    respx.get(f"{BASE}/experiments").mock(return_value=httpx.Response(200, json=[{"experiment_id": "e"}]))
    respx.post(f"{BASE}/plans").mock(return_value=httpx.Response(200, json={"plan_id": "p"}))
    st = respx.post(f"{BASE}/plans/p/status").mock(return_value=httpx.Response(200, json={"plan_id": "p"}))

    out = await RunRecorder(BASE, "s").write(
        plan=_plan_meta(ok=True), notes=[], design_ref=None,
        operator="me@lab", started_at="2026-08-13T00:00:00Z")

    assert out["status"] == "completed" and out["status_error"] is None
    assert json.loads(st.calls[0].request.content)["status"] == "completed"


@respx.mock
@pytest.mark.anyio
async def test_a_dry_run_does_not_post_a_status_at_all():
    respx.get(f"{BASE}/experiments").mock(return_value=httpx.Response(200, json=[{"experiment_id": "e"}]))
    respx.post(f"{BASE}/plans").mock(return_value=httpx.Response(200, json={"plan_id": "p"}))
    st = respx.post(f"{BASE}/plans/p/status").mock(return_value=httpx.Response(200, json={}))

    out = await RunRecorder(BASE, "s").write(
        plan=_plan_meta(ok=True, dry_run=True), notes=[], design_ref=None,
        operator="me@lab", started_at="2026-08-13T00:00:00Z")

    assert not st.called, "a preflight must not be filed as an executed plan"
    assert out["status"] == "draft"


@respx.mock
@pytest.mark.anyio
async def test_a_failed_status_post_does_not_lose_the_filed_run():
    """The Plan and its notes are already stored; a status call that fails is
    reported, never raised (property 1)."""
    respx.get(f"{BASE}/experiments").mock(return_value=httpx.Response(200, json=[{"experiment_id": "e"}]))
    respx.post(f"{BASE}/plans").mock(return_value=httpx.Response(200, json={"plan_id": "p"}))
    respx.post(f"{BASE}/plans/p/status").mock(side_effect=httpx.ConnectError("gone"))

    out = await RunRecorder(BASE, "s").write(
        plan=_plan_meta(ok=True), notes=[], design_ref=None,
        operator="me@lab", started_at="2026-08-13T00:00:00Z")

    assert out["written"] is True and out["plan_id"] == "p"
    assert "gone" in out["status_error"]


def test_the_plan_status_fallback_still_matches_the_live_enum():
    """Same guard as the note-kind snapshot, for the same reason."""
    live = _live_plan_statuses()
    if live is None:
        pytest.skip("AnaliticaDB unreachable — nothing to compare the snapshot against")
    assert live == FALLBACK_PLAN_STATUSES, (
        f"AnaliticaDB's PlanStatus enum changed: live={sorted(live)}, "
        f"FALLBACK_PLAN_STATUSES={sorted(FALLBACK_PLAN_STATUSES)}. Update the literal, "
        "and check app.record.PLAN_STATUS still maps into it."
    )

