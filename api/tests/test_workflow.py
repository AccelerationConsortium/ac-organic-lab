"""Phase F: executing an authorized package (D-20…D-23).

The refusals carry the weight. This is the first code in the dashboard that
runs a whole plan against hardware, so every gate is tested for *blocking* —
a revoked authorization, an expired one, a package that does not match its
digest, and one whose digest cannot be checked at all must each stop the run
before a device is touched.

`execute_plan` itself is the SDK's and is not re-tested here; what is tested is
everything this module does around it.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from app.workflow import (
    Authorization,
    RunRefused,
    assert_executable,
    digest_payload_of,
    notes_from,
    plan_from,
    plan_row_from,
    verify_package_digest,
)

STEPS = [
    {"step_id": "home_gantry", "role": "liquid_handler", "skill": "home", "args": {}},
    {"step_id": "tip_on", "role": "liquid_handler", "skill": "pick_up_tip",
     "args": {"pipette": "p300"}},
]


def _package(**over) -> dict:
    pkg = {
        "compiler_version": "0.3.0",
        "protocol": "ot2-transfer-smoke",
        "design_ref": None,
        "steps": STEPS,
        "design": None,
        "plate_map": None,
        "parameters": {"single_volume_ul": 100},
        "warnings": [],
    }
    pkg.update(over)
    return pkg


def _digest(pkg: dict) -> str:
    """What bitácora computes: the digest payload, canonical JSON, sha256.
    Uses the verifier's own `digest_payload_of` so the test and the gate cannot
    disagree about *which* fields are hashed — the one thing this file exists
    to pin is that the gate hashes what bitácora hashed."""
    payload = digest_payload_of(pkg)
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


#: A two-plate layout as bitácora's `plates:` block publishes it (template
#: 1.10.0, PLATES_AS_OBJECTS). Only the shape matters here.
TWO_PLATES = {
    "acid_stock": {"labware": "agilent_96_2ml_deep_square", "rows": 8, "columns": 12,
                   "role": "feedstock", "wells": {"A1": {"contents": "acid_a"}}},
    "reaction": {"labware": "corning_96_wellplate_360ul_flat", "rows": 8, "columns": 12,
                 "role": "conditions", "wells": {"A1": {"conditions": {"acid": "acid_a"}}}},
}

#: A substance registry as bitácora's `substances:` block publishes it
#: (COMPILER_VERSION 0.5.0). Only the shape matters here.
SUBSTANCES = {
    "acid_a": {"name": "benzoic acid", "cas": "65-85-0", "role": "reagent"},
    "solvent": {"name": "acetonitrile", "cas": "75-05-8", "role": "solvent"},
}

#: The compiled steps a per-well `dose` action expands into, and the `lineage`
#: entries bitácora publishes beside them (compiler 0.6.0, PLATE_TRACKING D11):
#: one entry per (source plate, destination well) pair.
LINEAGE_STEPS = [
    {"step_id": f"dose__{w}__dispense", "role": "liquid_handler",
     "skill": "dispense", "args": {"volume_ul": 50}}
    for w in ("A1", "A2")
]
LINEAGE = [
    {"step_id": "dose", "mapping": "identity",
     "source": {"plate": "acid_stock", "hid": "PLT-A"}, "source_well": w,
     "dest": {"plate": "reaction", "hid": "PLT-R"}, "dest_well": w}
    for w in ("A1", "A2")
]


def _auth(**over) -> Authorization:
    pkg = over.pop("package", _package())
    base = dict(
        authorization_id="ra_test", project_id="p1",
        protocol_path="protocols/ot2-transfer-smoke.yaml", commit_sha="a" * 40,
        package_digest=_digest(pkg), package=pkg,
        binding={"liquid_handler": "ot2_complexation"},
        authorized_by="yangcyril.cao@utoronto.ca", executable=True,
        revoked_at=None, expires_at="2099-01-01T00:00:00+00:00",
    )
    base.update(over)
    return Authorization(**base)


# ── the gates ──────────────────────────────────────────────────────────


def test_a_valid_authorization_passes_every_gate() -> None:
    auth = _auth()
    assert_executable(auth)
    verify_package_digest(auth)
    assert len(plan_from(auth).steps) == 2


@pytest.mark.asyncio
async def test_authorized_plate_reader_package_reaches_the_sdk_plan_gate() -> None:
    """lab-runs is device-agnostic: a pinned Cytation binding and the live
    plate-reader catalog are enough; the agent needs no direct control tool."""
    from lab_skills import Lab, validate_plan
    from lab_skills.registry import EquipmentEntry, Registry

    pkg = _package(
        protocol="cytation-absorbance-smoke",
        steps=[
            {
                "step_id": "read_diagnostic_wells",
                "role": "plate_reader",
                "skill": "read.absorbance",
                "args": {"wells": ["A1", "B2"], "wavelength_nm": 600.0},
            }
        ],
        parameters={},
    )
    auth = _auth(
        package=pkg,
        protocol_path="protocols/cytation-absorbance-smoke.yaml",
        binding={"plate_reader": "cytation_5"},
    )
    assert_executable(auth)
    verify_package_digest(auth)
    plan = plan_from(auth)

    registry = Registry(
        equipment=[
            EquipmentEntry(
                id="cytation_5",
                name="BioTek Cytation 5",
                kind="plate_reader",
                adapter="http",
                base_url="http://cytation.test:8040",
                status_path="/status",
                protocol="1.2",
            )
        ]
    )
    async with Lab.connect(registry=registry, binding=auth.binding) as lab:
        report = validate_plan(plan, lab)

    assert report.ok
    assert report.steps[0].role == "plate_reader"
    assert report.steps[0].skill == "read.absorbance"


def test_a_revoked_authorization_is_refused() -> None:
    """Revocation is only real if the runner asks at run time — the reason the
    handover is a pull rather than a push (D-21)."""
    auth = _auth(executable=False, revoked_at="2026-08-08T00:00:00+00:00")
    with pytest.raises(RunRefused, match="revoked"):
        assert_executable(auth)


def test_an_expired_authorization_is_refused_and_says_what_to_do() -> None:
    auth = _auth(executable=False, expires_at="2020-01-01T00:00:00+00:00")
    with pytest.raises(RunRefused, match="expired") as exc:
        assert_executable(auth)
    assert "re-authorize" in str(exc.value)


def test_a_package_that_does_not_match_its_digest_is_refused() -> None:
    """The whole point of carrying a digest: an edit between authorizing and
    running must stop the run, not be discovered afterwards."""
    tampered = _package(parameters={"single_volume_ul": 300})  # 100 → 300 µL
    auth = _auth(package=tampered, package_digest=_digest(_package()))
    with pytest.raises(RunRefused, match="digest mismatch"):
        verify_package_digest(auth)


def test_a_package_missing_a_digest_input_is_refused_not_skipped() -> None:
    """Absent inputs must not silently narrow what the check covers. Before
    bitácora published them, a verifier reassembled `protocol` and `design_ref`
    from filename stems; if that ever regresses, this fails loudly rather than
    hashing a subset and calling it verified."""
    short = _package()
    del short["design"]
    auth = _auth(package=short, package_digest="sha256:whatever")
    with pytest.raises(RunRefused, match="missing digest input"):
        verify_package_digest(auth)


def test_the_digest_covers_the_science_not_only_the_steps() -> None:
    """A relayout with identical steps changes the package, so it must change
    the digest — mirrors the compiler-side test in bitácora."""
    a = _package()
    b = _package(plate_map={"labware": "lw", "wells": {"A1": {}}})
    assert a["steps"] == b["steps"]
    assert _digest(a) != _digest(b)


def test_a_two_plate_package_verifies() -> None:
    """Regression: bitácora adds `plates` to the digest payload when a protocol
    declares a multi-plate layout (template 1.10.0). A verifier that hashed only
    the one-plate field set recomputed a different digest and refused the run
    as tampered — a false tamper report, the exact failure `digest_payload`
    exists to prevent. The payload must include `plates` when it is present."""
    pkg = _package(plates=TWO_PLATES)
    assert "plates" in digest_payload_of(pkg)
    auth = _auth(package=pkg)            # digest computed over the same payload
    verify_package_digest(auth)          # must not raise
    # …and swapping feedstock contents must change the digest (the reason the
    # block is digested at all: "otherwise swapping feedstock contents would
    # authorize the same package").
    swapped = _package(plates={**TWO_PLATES, "acid_stock": {
        **TWO_PLATES["acid_stock"], "wells": {"A1": {"contents": "acid_b"}}}})
    assert _digest(swapped) != _digest(pkg)
    with pytest.raises(RunRefused, match="digest mismatch"):
        verify_package_digest(_auth(package=swapped, package_digest=_digest(pkg)))


def test_a_one_plate_package_digest_is_unchanged() -> None:
    """`plates` is optional-when-truthy, not required: a one-plate package has
    no `plates` key and its digest must be exactly what it was before the
    field existed — otherwise every already-issued authorization would start
    failing verification. Also pins that `plates` is NOT a required input."""
    pkg = _package()
    assert "plates" not in pkg
    assert set(digest_payload_of(pkg)) == {
        "compiler_version", "protocol", "design_ref", "steps", "design",
        "plate_map", "parameters",
    }
    verify_package_digest(_auth(package=pkg))   # no "missing digest input"


def test_an_empty_plates_block_is_not_digested() -> None:
    """Mirrors bitácora's `if self.plates:` — an absent, null or empty block is
    omitted, so `plates: {}` and no `plates` key hash identically. The verifier
    must apply the same truthiness rule or the two sides disagree on exactly
    the packages that carry the key but no plates."""
    assert digest_payload_of(_package(plates={})) == digest_payload_of(_package())
    assert digest_payload_of(_package(plates=None)) == digest_payload_of(_package())
    assert _digest(_package(plates={})) == _digest(_package())


def test_a_package_with_a_substance_registry_verifies() -> None:
    """The same regression `plates` had, one field later: bitácora's compiler
    (0.5.0) added `substances` to the digest payload, and a verifier that did not
    know the field recomputed a different digest and refused the run as tampered.
    A false tamper report is the worst possible way to learn about drift — it
    accuses a human of editing a package nobody touched."""
    pkg = _package(substances=SUBSTANCES)
    assert "substances" in digest_payload_of(pkg)
    verify_package_digest(_auth(package=pkg))    # must not raise
    # …and swapping a substance must change the digest: that is why the block is
    # digested at all — the same steps run against different chemistry.
    swapped = _package(substances={**SUBSTANCES, "acid_a": {
        **SUBSTANCES["acid_a"], "cas": "99-96-7"}})
    assert _digest(swapped) != _digest(pkg)
    with pytest.raises(RunRefused, match="digest mismatch"):
        verify_package_digest(_auth(package=swapped, package_digest=_digest(pkg)))


def test_a_package_without_substances_digests_exactly_as_before() -> None:
    """`substances` is optional-when-truthy, not required. A protocol that names
    no substances has no `substances` key, and its digest must be byte-identical
    to what it was before the field existed — otherwise adding the field would
    invalidate every already-issued authorization."""
    pkg = _package()
    assert "substances" not in pkg
    assert "substances" not in digest_payload_of(pkg)
    verify_package_digest(_auth(package=pkg))    # not a "missing digest input"


def test_an_empty_substances_block_is_not_digested() -> None:
    """Mirrors bitácora's `if self.substances:` — absent, null and empty all hash
    identically, so the two sides cannot disagree about exactly the packages that
    carry the key but no substances."""
    assert digest_payload_of(_package(substances={})) == digest_payload_of(_package())
    assert digest_payload_of(_package(substances=None)) == digest_payload_of(_package())
    assert _digest(_package(substances={})) == _digest(_package())


def test_a_package_with_compiled_lineage_verifies() -> None:
    """`lineage` is the third optional digest input (compiler 0.6.0) and was
    added here *with* the field rather than after the first false tamper report
    — the mistake `plates` and `substances` each made once. It has to be
    digested for the same reason they are: the expanded well pairs are what the
    ledger will claim happened, so re-pointing a transfer must not authorize the
    same package."""
    pkg = _package(lineage=LINEAGE)
    assert "lineage" in digest_payload_of(pkg)
    verify_package_digest(_auth(package=pkg))    # must not raise
    repointed = _package(lineage=[{**LINEAGE[0], "source_well": "H12"}, LINEAGE[1]])
    assert _digest(repointed) != _digest(pkg)
    with pytest.raises(RunRefused, match="digest mismatch"):
        verify_package_digest(_auth(package=repointed, package_digest=_digest(pkg)))


def test_a_package_without_lineage_digests_exactly_as_before() -> None:
    """Optional-when-truthy, like its two predecessors: a protocol whose steps
    declare no mapping has no `lineage` key, and its digest must be unchanged."""
    assert "lineage" not in digest_payload_of(_package())
    assert digest_payload_of(_package(lineage=[])) == digest_payload_of(_package())
    assert digest_payload_of(_package(lineage=None)) == digest_payload_of(_package())
    verify_package_digest(_auth(package=_package()))   # not a "missing digest input"


# ── the one translation ────────────────────────────────────────────────


def test_step_ids_survive_the_translation_into_sdk_steps() -> None:
    """A package step names its id `step_id`; an SDK Step names it `id`. Getting
    this wrong would not fail — it would run the right actions under the wrong
    labels, and executed step_ids are permanent, so the Notes anchored to them
    would be wrong for good."""
    plan = plan_from(_auth())
    assert [s.id for s in plan.steps] == ["home_gantry", "tip_on"]
    assert [s.index for s in plan.steps] == [0, 1]
    assert plan.steps[1].args == {"pipette": "p300"}


def test_a_step_that_is_not_a_compiled_step_is_refused() -> None:
    auth = _auth(package=_package(steps=[{"role": "r", "skill": "s"}]))
    with pytest.raises(RunRefused, match="missing 'step_id'"):
        plan_from(auth)


def test_an_empty_package_is_refused() -> None:
    with pytest.raises(RunRefused, match="no steps"):
        plan_from(_auth(package=_package(steps=[])))


# ── the record shape (produced, not written — D-23) ────────────────────


class _StepReport:
    def __init__(self, step_id, status, error=None, role="liquid_handler",
                 skill="home", equipment_id="ot2_complexation"):
        self.step_id, self.status, self.error = step_id, status, error
        self.role, self.skill, self.equipment_id = role, skill, equipment_id
        self.violations = []


class _Report:
    def __init__(self, steps, ok=False, dry_run=False):
        self.steps, self.ok, self.dry_run = steps, ok, dry_run


def test_only_the_steps_that_went_wrong_become_notes() -> None:
    """A run where everything worked is fully described by its Plan row. A note
    per successful step would bury the two that matter."""
    report = _Report([
        _StepReport("home_gantry", "succeeded"),
        _StepReport("tip_on", "failed", error="Low Air Pressure Error"),
        _StepReport("aspirate", "skipped"),
    ])
    notes = notes_from(report, authorization_id="ra_test")
    assert [n["step_id"] for n in notes] == ["tip_on", "aspirate"]
    assert notes[0]["kind"] == "device_fault"
    assert notes[0]["body"] == "Low Air Pressure Error"
    assert notes[1]["kind"] == "deviation"


def test_a_note_carries_the_anchor_the_record_layer_joins_on() -> None:
    notes = notes_from(_Report([_StepReport("tip_on", "failed", error="boom")]),
                       authorization_id="ra_test")
    assert notes[0]["step_id"] == "tip_on"
    assert notes[0]["data"]["authorization_id"] == "ra_test"
    assert notes[0]["data"]["equipment_id"] == "ot2_complexation"


def test_the_plan_row_threads_back_to_who_approved_it() -> None:
    """`authorization_id` has no column of its own, so it rides in meta — that
    thread from 'this ran' to 'this human approved it, against this commit, with
    this digest' is the whole point of the gate."""
    auth = _auth()
    row = plan_row_from(auth, _Report([_StepReport("home_gantry", "succeeded")], ok=True))
    assert row["source_commit"] == auth.commit_sha
    assert row["protocol_path"] == auth.protocol_path
    assert row["meta"]["authorization_id"] == "ra_test"
    assert row["meta"]["package_digest"] == auth.package_digest
    assert row["meta"]["authorized_by"] == "yangcyril.cao@utoronto.ca"
    assert row["meta"]["binding"] == {"liquid_handler": "ot2_complexation"}
    assert [s["step_id"] for s in row["steps"]] == ["home_gantry"]


def test_dry_run_steps_are_not_deviations() -> None:
    """A preflight that touched nothing has nothing to report as having gone
    wrong."""
    report = _Report([_StepReport("home_gantry", "dry_run")], ok=True, dry_run=True)
    assert notes_from(report, authorization_id="ra_test") == []


# ── the session must be entered ────────────────────────────────────────


def test_lab_session_returns_an_unentered_context_manager() -> None:
    """Found live on 2026-08-08: the runner passed `Lab.connect(...)` straight to
    `execute_plan`, and a LabSession is inert until entered — `session.role(...)`
    raises `LabSession is not active`. It failed at the *first step*, after every
    gate had passed, which read like a device problem rather than a lifetime bug.

    None of the unit tests above could see it: they exercise the gates and the
    translation, never the execution. So this pins the contract instead — the
    helper hands back something you must `async with`, and the endpoint does."""
    import inspect

    from app.workflow import lab_session

    assert not inspect.iscoroutinefunction(lab_session), (
        "lab_session must be sync — it returns a context manager, not a session"
    )
    from app.workflow import _drive_run

    src = inspect.getsource(_drive_run)
    assert "async with connection as session:" in src, (
        "the run driver must enter the session before execute_plan; passing an "
        "un-entered one fails at the first step, after the gates have passed"
    )


# ── the device credential ──────────────────────────────────────────────


def test_device_headers_reuses_the_passthrough_definition() -> None:
    """One definition of "how this app authenticates to a device". The runner
    presents the same credential `control.py` already sends for an operator's
    single click — edge-injected identity plus the shared secret — rather than
    a second scheme.

    The first real run tried an ac_auth API key instead and was refused: the key
    was valid (the sidecar verified it) but the OT-2 gateway deliberately
    contacts no external auth service, so an issued key means nothing to it."""
    import inspect

    from app.workflow import device_headers

    src = inspect.getsource(device_headers)
    assert "_device_auth_headers" in src, (
        "device auth must come from control.py, not be reimplemented here"
    )


def test_the_record_keeps_both_humans() -> None:
    """The device may see only the automation principal. If the record does not
    carry who approved AND who launched, the human vanishes from the trail —
    which AUTH_DESIGN forbids: never "the robot did it" with nobody attached."""
    row = plan_row_from(
        _auth(), _Report([_StepReport("home_gantry", "succeeded")], ok=True),
        launched_by="someone.else@utoronto.ca",
    )
    assert row["meta"]["authorized_by"] == "yangcyril.cao@utoronto.ca"
    assert row["meta"]["launched_by"] == "someone.else@utoronto.ca"


def test_an_unknown_outcome_is_not_recorded_as_a_deviation() -> None:
    """A timed-out command may have run. Filing it as a deviation invites
    someone to re-run a step that could already have moved liquid — so it gets
    its own note kind, and the body keeps the SDK's "do not retry" wording."""
    report = _Report([
        _StepReport("a1_aspirate_1", "unknown",
                    error="no response within the timeout — do not retry blindly"),
        _StepReport("a1_dispense_1", "skipped"),
    ])
    notes = notes_from(report, authorization_id="ra_test")
    kinds = {n["step_id"]: n["kind"] for n in notes}
    assert kinds["a1_aspirate_1"] == "outcome_unknown"
    assert kinds["a1_dispense_1"] == "deviation"
    assert "do not retry" in notes[0]["body"]


# ── the background run lifecycle (slice 2: run_id + SSE + abort + D-22) ─
#
# Driven through the real endpoints with the run driver monkeypatched at
# `execute_plan` only — the gates, registry, SSE machinery and abort path are
# all real. A fake bitácora answers `fetch_authorization`.


import asyncio as _asyncio

import pytest as _pytest
from fastapi.testclient import TestClient as _TestClient


def _fake_auth(**over):
    a = _auth(**over)
    return a


@_pytest.fixture
def run_rig(monkeypatch):
    from app.main import app
    import app.workflow as wf

    wf._RUNS.clear()
    auth = _fake_auth()

    async def fake_fetch(client, authorization_id, identity=None):
        if authorization_id != auth.authorization_id:
            raise wf.RunRefused(f"no authorization {authorization_id!r}")
        return auth

    class _Conn:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(wf, "fetch_authorization", fake_fetch)
    monkeypatch.setattr(wf, "lab_session", lambda request, a: _Conn())

    finished = _asyncio.Event()

    def install_execute(fn):
        """Install a fake execute_plan; the rig signals `finished` after done."""
        import lab_skills

        async def wrapper(plan, session, *, owner, dry_run=False, gate=None,
                          on_step=None, **kw):
            try:
                return await fn(plan, session, owner=owner, dry_run=dry_run,
                                gate=gate, on_step=on_step)
            finally:
                finished.set()

        monkeypatch.setattr(lab_skills, "execute_plan", wrapper)

    with _TestClient(app) as client:
        yield client, wf, auth, install_execute, finished


def _step_report(step_id, status="succeeded"):
    class R:  # duck-typed StepRunReport
        pass

    r = R()
    r.step_id, r.status, r.error = step_id, status, None
    r.role, r.skill, r.equipment_id = "liquid_handler", "home", "ot2_complexation"
    r.violations = []
    return r


class _FakeRunReport:
    def __init__(self, steps, ok=True, aborted_reason=None, dry_run=False):
        self.steps, self.ok = steps, ok
        self.aborted_reason, self.dry_run = aborted_reason, dry_run


def test_start_returns_a_run_id_immediately(run_rig):
    client, wf, auth, install, finished = run_rig

    async def fake_exec(plan, session, *, owner, dry_run, gate, on_step):
        for s in ("home_gantry", "tip_on"):
            await on_step(_step_report(s))
        return _FakeRunReport([_step_report("home_gantry"), _step_report("tip_on")])

    install(fake_exec)
    r = client.post("/api/workflow/runs",
                    json={"authorization_id": auth.authorization_id},
                    headers={"X-Auth-User": "op@utoronto.ca"})
    assert r.status_code == 202
    body = r.json()
    assert body["run_id"].startswith("run_")

    # TestClient runs the app's event loop between requests; poll until done.
    for _ in range(50):
        got = client.get(f"/api/workflow/runs/{body['run_id']}").json()
        if got["status"] == "finished":
            break
    assert got["result"]["ok"] is True
    assert [s["step_id"] for s in got["result"]["steps"]] == ["home_gantry", "tip_on"]
    assert got["result"]["record"]["plan"]["meta"]["launched_by"] == "op@utoronto.ca"


def test_a_refusal_is_still_a_409_not_a_doomed_run_id(run_rig):
    client, wf, auth, install, finished = run_rig
    r = client.post("/api/workflow/runs", json={"authorization_id": "ra_nope"},
                    headers={"X-Auth-User": "op@utoronto.ca"})
    assert r.status_code == 409
    assert "ra_nope" in r.json()["detail"]
    assert wf._RUNS == {}  # nothing was accepted for execution


def test_the_event_stream_replays_from_the_start_and_ends_on_done(run_rig):
    client, wf, auth, install, finished = run_rig

    async def fake_exec(plan, session, *, owner, dry_run, gate, on_step):
        await on_step(_step_report("home_gantry"))
        return _FakeRunReport([_step_report("home_gantry")])

    install(fake_exec)
    run_id = client.post("/api/workflow/runs",
                         json={"authorization_id": auth.authorization_id},
                         headers={"X-Auth-User": "op@utoronto.ca"}).json()["run_id"]
    for _ in range(50):
        if client.get(f"/api/workflow/runs/{run_id}").json()["status"] == "finished":
            break

    # Connect AFTER the run finished: replay must still deliver everything.
    with client.stream("GET", f"/api/workflow/runs/{run_id}/events") as resp:
        text = "".join(chunk for chunk in resp.iter_text())
    assert "event: started" in text
    assert "event: step" in text and "home_gantry" in text
    assert text.rstrip().split("event: ")[-1].startswith("done")


def test_abort_reaches_the_gate_and_the_report_names_who(run_rig):
    client, wf, auth, install, finished = run_rig
    release = _asyncio.Event()

    async def fake_exec(plan, session, *, owner, dry_run, gate, on_step):
        await on_step(_step_report("home_gantry"))
        await release.wait()                      # run is mid-flight
        reason = await gate(object())             # next step boundary
        assert reason and "aborted by" in reason
        return _FakeRunReport(
            [_step_report("home_gantry"), _step_report("tip_on", "skipped")],
            ok=False, aborted_reason=reason,
        )

    install(fake_exec)
    run_id = client.post("/api/workflow/runs",
                         json={"authorization_id": auth.authorization_id},
                         headers={"X-Auth-User": "op@utoronto.ca"}).json()["run_id"]

    r = client.post(f"/api/workflow/runs/{run_id}/abort",
                    headers={"X-Auth-User": "someone.else@utoronto.ca"})
    assert r.json()["abort_requested"] == "someone.else@utoronto.ca"
    release.set()

    for _ in range(50):
        got = client.get(f"/api/workflow/runs/{run_id}").json()
        if got["status"] == "finished":
            break
    assert "aborted by someone.else@utoronto.ca" in got["result"]["aborted_reason"]


def test_the_gate_rechecks_the_authorization_between_steps(run_rig):
    """D-22 end to end: revoke in bitácora mid-run, and the next step boundary
    stops the run with the revocation named — not at start-only."""
    client, wf, auth, install, finished = run_rig

    async def fake_exec(plan, session, *, owner, dry_run, gate, on_step):
        assert await gate(object()) is None       # still executable
        object.__setattr__(auth, "revoked_at", "2026-08-09T02:00:00+00:00")
        object.__setattr__(auth, "executable", False)
        object.__setattr__(auth, "revoked_by", "yangcyril.cao@utoronto.ca")
        reason = await gate(object())
        assert reason and "revoked" in reason and "yangcyril.cao" in reason
        return _FakeRunReport([_step_report("home_gantry", "skipped")],
                              ok=False, aborted_reason=reason)

    install(fake_exec)
    run_id = client.post("/api/workflow/runs",
                         json={"authorization_id": auth.authorization_id},
                         headers={"X-Auth-User": "op@utoronto.ca"}).json()["run_id"]
    for _ in range(50):
        got = client.get(f"/api/workflow/runs/{run_id}").json()
        if got["status"] == "finished":
            break
    assert "revoked" in got["result"]["aborted_reason"]


# ── custody (PLATE_TRACKING.md D6–D9): the robot half ───────────────────────
#
# A compiled step carrying `custody: {plate, hid, to}` writes ONE `move` row
# when it succeeds — and nothing when it did not, or when its outcome is
# unknown. Driven through the real run driver with a fake recorder, so the
# hook's placement (after the step report, never raising) is what's tested.


def test_authorization_carries_plate_bindings_and_finds_custody_steps() -> None:
    steps = [
        {"step_id": "pick", "role": "plate_mover", "skill": "graph.gripper", "args": {},
         "custody": {"plate": "reaction", "hid": "PLT-1", "to": "xarm_translocation/gripper"}},
        {"step_id": "shake", "role": "shaker", "skill": "shake.start", "args": {}},
        {"step_id": "bad", "role": "r", "skill": "s", "args": {}, "custody": {"plate": "x"}},  # unresolved → ignored
    ]
    auth = _auth(package=_package(steps=steps), plate_bindings={"reaction": "PLT-1"})
    assert auth.plate_bindings == {"reaction": "PLT-1"}
    assert set(auth.custody_by_step) == {"pick"}
    assert _auth().plate_bindings == {}
    from app.workflow import planned_row_from
    row = planned_row_from(auth, launched_by="me", dry_run=False)
    assert row["meta"]["plate_bindings"] == {"reaction": "PLT-1"} and row["meta"]["ok"] is None
    assert row["steps"][0]["params"]["custody"]["to"] == "xarm_translocation/gripper"
    assert row["steps"][1]["params"]["status"] == "planned"


class _CustodyRecorderFake:
    def __init__(self):
        self.moves = []

    async def record_move(self, **kw):
        self.moves.append(kw)
        return {"recorded": True, "action_id": f"a{len(self.moves)}", "container_id": "c", "to_location_id": "l"}

    async def current_location(self, hid, **kw):
        return {"found": True, "hid": hid, "location_name": "bench/hte_staging"}


CUSTODY_STEPS = [
    {"step_id": "pick", "role": "plate_mover", "skill": "graph.gripper", "args": {"state": "grip_120"},
     "custody": {"plate": "reaction", "hid": "PLT-1", "to": "xarm_translocation/gripper"}},
    {"step_id": "place", "role": "plate_mover", "skill": "graph.gripper", "args": {"state": "empty"},
     "custody": {"plate": "reaction", "hid": "PLT-1", "to": "torry_pines_shaker/nest"}},
    {"step_id": "shake", "role": "shaker", "skill": "shake.start", "args": {}},
]


class _FakeAggregator:
    """Answers `fetch_one` with a canned snapshot per equipment id (or None)."""

    def __init__(self, snapshots=None):
        self.snapshots = snapshots or {}
        self.asked = []

    async def fetch_one(self, equipment_id):
        self.asked.append(equipment_id)
        return self.snapshots.get(equipment_id)


def _custody_rig(run_rig, monkeypatch, statuses, *, dry_run=False, aggregator=None):
    client, wf, auth, install, finished = run_rig
    import app.workflow as wfmod
    from app.main import app as _app
    fake = _CustodyRecorderFake()
    monkeypatch.setattr(wfmod, "custody_recorder", lambda: fake)
    # Never read the live lab from a test: the real aggregator would poll the
    # real xArm and turn its empty gripper into a "mismatch".
    _app.state.aggregator = aggregator if aggregator is not None else _FakeAggregator()
    # the rig's auth has no custody steps; swap in one that does
    cauth = _auth(package=_package(steps=CUSTODY_STEPS), plate_bindings={"reaction": "PLT-1"})

    async def fake_fetch(client_, authorization_id, identity=None):
        return cauth

    monkeypatch.setattr(wfmod, "fetch_authorization", fake_fetch)

    async def fake_exec(plan, session, *, owner, dry_run, gate, on_step):
        reports = []
        for step, status in zip(plan.steps, statuses):
            r = _step_report(step.id, status)
            r.role, r.skill = step.role, step.skill
            r.equipment_id = "xarm_translocation" if step.role == "plate_mover" else "torry_pines_shaker"
            reports.append(r)
            await on_step(r)
        return _FakeRunReport(reports, ok=all(s == "succeeded" for s in statuses))

    install(fake_exec)
    r = client.post("/api/workflow/runs", json={"authorization_id": cauth.authorization_id, "dry_run": dry_run})
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]
    # drain the event stream (ends on `done`)
    events = []
    with client.stream("GET", f"/api/workflow/runs/{run_id}/events") as resp:
        for line in resp.iter_lines():
            if line.startswith("data:"):
                import json as _json
                events.append(_json.loads(line[5:]))
    return fake, events, cauth


class _Snap:
    def __init__(self, details=None, components=None):
        class _St:
            pass
        self.status = _St()
        self.status.details = details or {}
        self.status.components = components or {}
        self.fetch_error = None


def test_the_destination_device_is_read_fresh_and_a_contradiction_is_filed(run_rig, monkeypatch):
    """After the pick the arm reports an empty gripper — a contradiction: a
    deviation note and a mismatch verdict, and the move row stays (the ledger
    records what was commanded; nothing auto-corrects)."""
    agg = _FakeAggregator({"xarm_translocation": _Snap(details={"gripper": {"object_detected": False}}),
                           "torry_pines_shaker": _Snap(components={})})
    fake, events, _ = _custody_rig(run_rig, monkeypatch, ["succeeded", "succeeded", "succeeded"], aggregator=agg)
    assert agg.asked == ["xarm_translocation", "torry_pines_shaker"]   # the destinations, not the actor
    custody = {e["data"]["step_id"]: e["data"] for e in events if e["type"] == "custody"}
    assert custody["pick"]["verdict"] == "mismatch" and custody["pick"]["recorded"] is True
    assert custody["place"]["verdict"] == "unobservable"
    done = next(e for e in events if e["type"] == "done")["data"]
    dev = [n for n in done["record"]["notes"] if n["kind"] == "deviation" and n["step_id"] == "pick"]
    assert dev and "custody mismatch" in dev[0]["body"]
    assert len(fake.moves) == 2


def test_a_succeeded_custody_step_records_exactly_one_move_with_its_anchors(run_rig, monkeypatch):
    fake, events, cauth = _custody_rig(run_rig, monkeypatch, ["succeeded", "succeeded", "succeeded"])
    assert [m["hid"] for m in fake.moves] == ["PLT-1", "PLT-1"]          # pick, place — not shake
    assert [m["to"] for m in fake.moves] == ["xarm_translocation/gripper", "torry_pines_shaker/nest"]
    assert [m["step_id"] for m in fake.moves] == ["pick", "place"]
    assert all(m["performed_by"] == "xarm_translocation" for m in fake.moves)
    assert all(m["project"] == cauth.project_id for m in fake.moves)
    assert all(m["params"]["authorization_id"] == cauth.authorization_id for m in fake.moves)
    custody = [e for e in events if e["type"] == "custody"]
    assert [c["data"]["recorded"] for c in custody] == [True, True]
    # the shaker nest has no sensor and this rig has no aggregator → unobservable, never a mismatch
    assert {c["data"]["verdict"] for c in custody} == {"unobservable"}
    started = next(e for e in events if e["type"] == "started")["data"]
    assert started["custody_steps"] == ["pick", "place"]
    assert started["plate_bindings"] == {"reaction": "PLT-1"}


def test_a_failed_or_unknown_step_writes_no_move(run_rig, monkeypatch):
    fake, events, _ = _custody_rig(run_rig, monkeypatch, ["succeeded", "unknown", "skipped"])
    assert [m["step_id"] for m in fake.moves] == ["pick"]               # place was unknown → nothing
    custody = {e["data"]["step_id"]: e["data"] for e in events if e["type"] == "custody"}
    assert custody["place"]["recorded"] is False and custody["place"]["reason"] == "unknown"
    done = next(e for e in events if e["type"] == "done")["data"]
    # …and the unknown outcome is filed as a note, never as a deviation
    kinds = [n["kind"] for n in done["record"]["notes"] if n.get("step_id") == "place"]
    assert "outcome_unknown" in kinds and "deviation" not in kinds


def test_a_dry_run_records_nothing(run_rig, monkeypatch):
    fake, events, _ = _custody_rig(run_rig, monkeypatch, ["dry_run", "dry_run", "dry_run"], dry_run=True)
    assert fake.moves == []
    assert all(e["data"]["reason"] == "dry_run" for e in events if e["type"] == "custody")
    record = next(e for e in events if e["type"] == "record")["data"]
    assert record == {"opened": False, "reason": "dry_run"}


# ── custody preflight (PLATE_TRACKING.md D1/D7): the layer-4 rule ────────────
#
# "Plate X must be at location L before step S", with L taken from this run's
# own chain of accepted moves rather than from the protocol. The run-start
# cross-check asks the ledger once; these tests are about the steps after it,
# where a human can lift a plate off a nest mid-incubation and nothing else
# would notice until the arm reached for empty air.


class _LedgerFake:
    """A recorder with a scripted ledger. ``current_location`` answers come from
    ``answers`` in order — the run-start cross-check consumes the first — and
    ``records=False`` makes every move fail, which is what breaks the chain."""

    def __init__(self, answers, *, records=True):
        self.answers, self.records = list(answers), records
        self.moves: list[dict] = []
        self.lookups: list[dict] = []

    async def record_move(self, **kw):
        self.moves.append(kw)
        if not self.records:
            return {"recorded": False, "reason": "unreachable"}
        return {"recorded": True, "action_id": f"a{len(self.moves)}",
                "container_id": "c", "to_location_id": "l"}

    async def current_location(self, hid, **kw):
        self.lookups.append({"hid": hid, **kw})
        if self.answers:
            return self.answers.pop(0)
        return {"found": True, "hid": hid, "location_name": "bench/hte_staging"}


class _EventDb:
    """Captures lab.db rows, so the ops-audit half is assertable without a file."""

    def __init__(self):
        self.rows: list[tuple[str, str, dict]] = []

    def record_equipment_event(self, device_id, event_type, *, message="", payload=None):
        self.rows.append((device_id, event_type, payload or {}))


def _at(name: str, hid: str = "PLT-1") -> dict:
    return {"found": True, "hid": hid, "container_id": "c", "location_name": name}


def _preflight_rig(run_rig, monkeypatch, ledger, statuses, *,
                   dry_run=False, strict=False):
    """Drive a run whose fake executor honours the gate the way `execute_plan`
    does — gate before every step, and once it refuses, this and every remaining
    step is `skipped` with the reason. The existing `_custody_rig` never calls
    the gate at all, which is exactly the half the preflight lives in."""
    client, wf, _unused, install, finished = run_rig
    import app.workflow as wfmod
    from app.main import app as _app

    monkeypatch.setattr(wfmod, "custody_recorder", lambda: ledger)
    monkeypatch.setattr(wfmod, "CUSTODY_STRICT", strict)
    monkeypatch.setattr(_app.state, "aggregator", _FakeAggregator())
    db = _EventDb()
    monkeypatch.setattr(_app.state, "db", db)

    cauth = _auth(package=_package(steps=CUSTODY_STEPS), plate_bindings={"reaction": "PLT-1"})

    async def fake_fetch(client_, authorization_id, identity=None):
        return cauth

    monkeypatch.setattr(wfmod, "fetch_authorization", fake_fetch)

    async def fake_exec(plan, session, *, owner, dry_run, gate, on_step):
        reports, reason = [], None
        for step, status in zip(plan.steps, statuses):
            if reason is None:
                reason = await gate(step)
            r = _step_report(step.id, "skipped" if reason else status)
            r.role, r.skill = step.role, step.skill
            r.equipment_id = ("xarm_translocation" if step.role == "plate_mover"
                              else "torry_pines_shaker")
            r.error = reason if reason else None
            reports.append(r)
            await on_step(r)
        return _FakeRunReport(
            reports, ok=reason is None and all(s == "succeeded" for s in statuses),
            aborted_reason=reason, dry_run=dry_run,
        )

    install(fake_exec)
    r = client.post("/api/workflow/runs",
                    json={"authorization_id": cauth.authorization_id, "dry_run": dry_run})
    assert r.status_code == 202, r.text
    events = []
    with client.stream("GET", f"/api/workflow/runs/{r.json()['run_id']}/events") as resp:
        for line in resp.iter_lines():
            if line.startswith("data:"):
                import json as _json
                events.append(_json.loads(line[5:]))
    return events, db


def _preflights(events) -> list[dict]:
    return [e["data"] for e in events if e["type"] == "custody_preflight"]


def test_the_opening_chain_declines_to_place_a_plate_the_ledger_could_not() -> None:
    """`expected_locations` is where "we cannot say" enters the chain, and it has
    to: a plate the ledger cannot place is one the preflight must decline to
    judge, not one it may assume is where the protocol wishes it were."""
    from app.workflow import expected_locations

    assert expected_locations([
        {"plate": "reaction", "hid": "PLT-1", "found": True, "location_name": "bench/hte_staging"},
        {"plate": "stock", "hid": "PLT-2", "found": False},
        {"plate": "spare", "hid": "PLT-3", "found": None, "error": "refused"},
        {"plate": "unbound"},                       # no hid at all — not a link
    ]) == {"PLT-1": "bench/hte_staging", "PLT-2": None, "PLT-3": None}


def test_the_preflight_runs_only_on_custody_steps_and_asks_the_ledger_fresh(run_rig, monkeypatch):
    """Two handoff steps, one shake. The shake is not a handoff, so nothing is
    checked before it — and every check is a *fresh* read: the recorder caches
    container rows, and `location_id` is the one field a move invalidates."""
    ledger = _LedgerFake([_at("bench/hte_staging"),          # run start
                          _at("bench/hte_staging"),          # before pick
                          _at("xarm_translocation/gripper")])  # before place
    events, _ = _preflight_rig(run_rig, monkeypatch, ledger,
                               ["succeeded", "succeeded", "succeeded"])
    checks = _preflights(events)
    assert [c["step_id"] for c in checks] == ["pick", "place"]
    assert [c["verdict"] for c in checks] == ["ok", "ok"]
    assert checks[1]["expected"] == "xarm_translocation/gripper"   # the pick's own move
    assert len(ledger.lookups) == 3                                # start + two steps
    assert all(look["refresh"] for look in ledger.lookups[1:])
    done = next(e for e in events if e["type"] == "done")["data"]
    assert done["ok"] is True and done["aborted_reason"] is None


def test_a_ledger_that_disagrees_is_filed_and_the_run_goes_on(run_rig, monkeypatch):
    """Advisory by default, exactly like the run-start cross-check: the
    disagreement is recorded — a deviation Note naming both places and a
    `plate_custody_mismatch` row stamped `phase: preflight` — and the operator
    decides. Nothing auto-corrects, and nothing stops on its own."""
    ledger = _LedgerFake([_at("bench/hte_staging"),   # run start
                          _at("bench/hte_staging"),   # before pick — agrees
                          _at("bench/hte_staging")])  # before place — the plate never left
    events, db = _preflight_rig(run_rig, monkeypatch, ledger,
                                ["succeeded", "succeeded", "succeeded"])
    check = _preflights(events)[1]
    assert check["verdict"] == "mismatch"
    assert check["expected"] == "xarm_translocation/gripper"
    assert check["actual"] == "bench/hte_staging"
    done = next(e for e in events if e["type"] == "done")["data"]
    assert done["ok"] is True and done["aborted_reason"] is None     # advisory
    note = next(n for n in done["record"]["notes"]
                if n.get("data", {}).get("phase") == "preflight")
    assert note["kind"] == "deviation" and note["step_id"] == "place"
    assert "xarm_translocation/gripper" in note["body"] and "bench/hte_staging" in note["body"]
    rows = [(d, p) for d, e, p in db.rows if e == "plate_custody_mismatch"]
    assert len(rows) == 1
    assert rows[0][0] == "xarm_translocation"      # the device anchoring `expected`
    assert rows[0][1]["phase"] == "preflight"      # what tells it from the after-step row


def test_strict_custody_stops_the_run_before_the_step_that_would_be_wrong(run_rig, monkeypatch):
    """A plate that is not where the chain requires makes every *later* step
    wrong too, not just this one — so the gate aborts and the SDK skips the
    rest, rather than blocking one step and carrying on."""
    ledger = _LedgerFake([_at("bench/hte_staging"), _at("bench/hte_staging"),
                          _at("bench/hte_staging")])
    events, _ = _preflight_rig(run_rig, monkeypatch, ledger,
                               ["succeeded", "succeeded", "succeeded"], strict=True)
    done = next(e for e in events if e["type"] == "done")["data"]
    assert done["ok"] is False
    assert "custody preflight for place" in done["aborted_reason"]
    assert [s["status"] for s in done["steps"]] == ["succeeded", "skipped", "skipped"]
    assert [m["step_id"] for m in ledger.moves] == ["pick"]   # `place` never moved


def test_a_move_the_ledger_refused_makes_the_next_step_unverifiable_not_wrong(run_rig, monkeypatch):
    """The chain advances only as far as the ledger went. When a `move` write
    fails the plate is somewhere the ledger does not know, so the next preflight
    declines to judge — under strict too. Accusing the ledger of disagreeing
    with a move it was never told about would be a fabricated deviation."""
    ledger = _LedgerFake([_at("bench/hte_staging"), _at("bench/hte_staging")],
                         records=False)
    events, db = _preflight_rig(run_rig, monkeypatch, ledger,
                                ["succeeded", "succeeded", "succeeded"], strict=True)
    checks = _preflights(events)
    assert [c["verdict"] for c in checks[:1]] == ["ok"]
    assert checks[1] == {"step_id": "place", "plate": "reaction", "hid": "PLT-1",
                         "to": "torry_pines_shaker/nest",
                         "checked": False, "reason": "unverifiable"}
    assert len(ledger.lookups) == 2          # the unverifiable step asks nothing
    done = next(e for e in events if e["type"] == "done")["data"]
    assert done["aborted_reason"] is None
    assert not [r for r in db.rows if r[1] == "plate_custody_mismatch"]


def test_an_unknown_outcome_makes_the_next_step_unverifiable(run_rig, monkeypatch):
    """A step that was sent and never answered may or may not have moved the
    plate. The chain forgets where it is rather than guessing, which is the same
    rule the after-step hook applies when it declines to write a `move` row."""
    ledger = _LedgerFake([_at("bench/hte_staging"), _at("bench/hte_staging")])
    events, _ = _preflight_rig(run_rig, monkeypatch, ledger,
                               ["unknown", "succeeded", "succeeded"], strict=True)
    checks = _preflights(events)
    assert checks[0]["verdict"] == "ok"
    assert checks[1]["reason"] == "unverifiable"
    assert [m["step_id"] for m in ledger.moves] == ["place"]   # `pick` wrote nothing
    done = next(e for e in events if e["type"] == "done")["data"]
    assert done["aborted_reason"] is None


def test_with_no_record_layer_the_preflight_is_silent(run_rig, monkeypatch):
    """`record.py` property 3: a deployment that has not opted into the record
    layer must behave exactly as before. No ledger, nothing to check, no frames
    — and certainly no refusal."""
    events, db = _preflight_rig(run_rig, monkeypatch, None,
                                ["succeeded", "succeeded", "succeeded"], strict=True)
    assert _preflights(events) == []
    done = next(e for e in events if e["type"] == "done")["data"]
    assert done["ok"] is True and done["aborted_reason"] is None


def test_a_dry_run_reports_the_disagreement_and_still_runs(run_rig, monkeypatch):
    """A dry run *is* the preflight. Refusing to preflight because the preflight
    found something is the one outcome that helps nobody — so it checks, says
    so, and completes even under strict."""
    ledger = _LedgerFake([_at("bench/hte_staging"), _at("waste/bin")])
    events, db = _preflight_rig(run_rig, monkeypatch, ledger,
                                ["dry_run", "dry_run", "dry_run"],
                                dry_run=True, strict=True)
    checks = _preflights(events)
    assert checks[0]["verdict"] == "mismatch" and checks[0]["actual"] == "waste/bin"
    done = next(e for e in events if e["type"] == "done")["data"]
    assert done["aborted_reason"] is None
    assert [s["status"] for s in done["steps"]] == ["dry_run", "dry_run", "dry_run"]
    # `bench/hte_staging` is a bench spot with no equipment behind it, so the
    # audit row falls back to the custody pseudo-device.
    assert [d for d, e, _ in db.rows if e == "plate_custody_mismatch"] == ["custody"]


# ── lineage (PLATE_TRACKING.md D11): the transfer rows, at run close ─────────
#
# Custody says where the plate went; lineage says what fed what. The derivation
# is `app.lineage`'s and tested there — what these pin is the wiring: the rows
# are filed after the record closes and before `done`, a dry run files none, and
# an unconfigured record layer is a clean skip rather than a missing key or an
# error.


class _TransferRecorderFake:
    def __init__(self, result=None):
        self.transfers: list[dict] = []
        self.result = result

    async def record_transfer(self, **kw):
        self.transfers.append(kw)
        return self.result or {"recorded": True, "action_id": f"t{len(self.transfers)}"}

    async def record_move(self, **kw):  # pragma: no cover — no custody steps here
        raise AssertionError("this package annotates no custody")


def _lineage_rig(run_rig, monkeypatch, statuses, *, dry_run=False,
                 configured=True, recorder=None):
    client, wf, _unused, install, finished = run_rig
    import app.workflow as wfmod
    from app.main import app as _app

    fake = recorder if recorder is not None else _TransferRecorderFake()
    monkeypatch.setattr(wfmod, "custody_recorder", lambda: fake if configured else None)
    monkeypatch.setattr(_app.state, "aggregator", _FakeAggregator())

    lauth = _auth(package=_package(steps=LINEAGE_STEPS, lineage=LINEAGE))

    async def fake_fetch(client_, authorization_id, identity=None):
        return lauth

    monkeypatch.setattr(wfmod, "fetch_authorization", fake_fetch)

    async def fake_exec(plan, session, *, owner, dry_run, gate, on_step):
        reports = []
        for step, status in zip(plan.steps, statuses):
            r = _step_report(step.id, status)
            r.role, r.skill, r.equipment_id = step.role, step.skill, "ot2_hte"
            reports.append(r)
            await on_step(r)
        return _FakeRunReport(reports, ok=all(s == "succeeded" for s in statuses),
                              dry_run=dry_run)

    install(fake_exec)
    r = client.post("/api/workflow/runs",
                    json={"authorization_id": lauth.authorization_id, "dry_run": dry_run})
    assert r.status_code == 202, r.text
    events = []
    with client.stream("GET", f"/api/workflow/runs/{r.json()['run_id']}/events") as resp:
        for line in resp.iter_lines():
            if line.startswith("data:"):
                import json as _json
                events.append(_json.loads(line[5:]))
    return fake, next(e for e in events if e["type"] == "done")["data"]


def test_a_run_that_carries_lineage_files_its_transfers_before_it_reports_done(run_rig, monkeypatch):
    """Deliberately inside the `done` payload, like the record write above it: a
    consumer that sees the run finish also sees whether its provenance was
    written, instead of having to ask afterwards."""
    fake, done = _lineage_rig(run_rig, monkeypatch, ["succeeded", "succeeded"])
    assert done["record"]["transfers"] == {"derived": 2, "emitted": 2,
                                           "failed": [], "skipped": []}
    assert [(t["source_hid"], t["source_well"], t["dest_hid"], t["dest_well"])
            for t in fake.transfers] == [("PLT-A", "A1", "PLT-R", "A1"),
                                         ("PLT-A", "A2", "PLT-R", "A2")]
    # The row anchors to the PROTOCOL step and is attributed to the machine that
    # poured, with the launcher as creator — the same split `record_move` makes.
    assert {t["step_id"] for t in fake.transfers} == {"dose"}
    assert all(t["performed_by"] == "ot2_hte" for t in fake.transfers)
    assert all(t["params"]["via"] == "executor" for t in fake.transfers)


def test_only_the_wells_that_actually_ran_are_filed(run_rig, monkeypatch):
    fake, done = _lineage_rig(run_rig, monkeypatch, ["succeeded", "failed"])
    assert done["record"]["transfers"]["emitted"] == 1
    assert [t["dest_well"] for t in fake.transfers] == ["A1"]


def test_a_dry_run_files_no_lineage(run_rig, monkeypatch):
    """A preflight moved no liquid, so nothing fed anything."""
    fake, done = _lineage_rig(run_rig, monkeypatch, ["dry_run", "dry_run"], dry_run=True)
    assert fake.transfers == []
    assert "transfers" not in done["record"]


def test_with_no_record_layer_lineage_is_a_clean_skip(run_rig, monkeypatch):
    """`record.py` property 3: unconfigured is a normal state, and it must say
    so rather than vanish — a missing key reads like a bug in the executor."""
    _, done = _lineage_rig(run_rig, monkeypatch, ["succeeded", "succeeded"],
                           configured=False)
    assert done["record"]["transfers"] == {"emitted": 0, "reason": "not_configured"}
    assert done["ok"] is True


def test_a_ledger_that_refuses_a_transfer_does_not_fail_the_run(run_rig, monkeypatch):
    """Property 1, one layer out: the liquid has already moved. "We could not
    file the paperwork" must never be reported as a failed run."""
    rec = _TransferRecorderFake({"recorded": False, "reason": "http_422",
                                 "detail": "unit is required"})
    _, done = _lineage_rig(run_rig, monkeypatch, ["succeeded", "succeeded"],
                           recorder=rec)
    assert done["ok"] is True
    transfers = done["record"]["transfers"]
    assert transfers["emitted"] == 0 and len(transfers["failed"]) == 2
