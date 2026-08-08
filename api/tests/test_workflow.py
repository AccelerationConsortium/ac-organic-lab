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
    from app.workflow import _DIGEST_FIELDS

    payload = {k: v for k, v in pkg.items() if k in _DIGEST_FIELDS}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


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

    from app.workflow import build_workflow_router, lab_session

    assert not inspect.iscoroutinefunction(lab_session), (
        "lab_session must be sync — it returns a context manager, not a session"
    )
    src = inspect.getsource(build_workflow_router)
    assert "async with connection as session:" in src, (
        "the endpoint must enter the session before execute_plan; passing an "
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
