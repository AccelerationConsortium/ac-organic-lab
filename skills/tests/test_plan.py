"""validate_plan() tests.

Exercises every layer of the offline validator (``lab_skills.plan``):

* role binding lookup
* equipment registry resolution
* SKILL_REGISTRY catalog matching by ``(kind, skill_name)``
* args_schema validation
* requires-id resolution
* protocol-version warning (v1.0 -> ``no_claim_semantics``)
* user-registered interlocks
* the two built-in interlocks (offline-role critical, duration info)
"""

from __future__ import annotations

import pytest

from lab_skills import (
    LabSession,
    Plan,
    Step,
    Violation,
    clear_interlocks,
    register_interlock,
    registered_interlocks,
    validate_plan,
)
from lab_skills.registry import EquipmentEntry, Maintenance, Registry
from lab_skills.skill_catalog import SKILL_REGISTRY, SkillDef
from lab_skills.skill_catalog.plate_sealer import StartupArgs


# Ensure each test starts from a clean interlock registry (built-ins only)
# regardless of test order. Tests that register custom interlocks rely on
# this isolation.
@pytest.fixture(autouse=True)
def _reset_interlocks():
    clear_interlocks()
    yield
    clear_interlocks()


def _registry(*entries: EquipmentEntry) -> Registry:
    return Registry(equipment=list(entries))


def _entry(
    *,
    id: str = "plateloc",
    kind: str = "plate_sealer",
    protocol: str = "1.0",
    enabled: bool = True,
    maintenance: Maintenance | None = None,
    do_not_call_connect: bool = False,
) -> EquipmentEntry:
    return EquipmentEntry(
        id=id,
        name=id.title(),
        kind=kind,  # type: ignore[arg-type]
        adapter="http",
        base_url=f"http://{id}.local:8000",
        protocol=protocol,  # type: ignore[arg-type]
        enabled=enabled,
        maintenance=maintenance,
        do_not_call_connect=do_not_call_connect,
    )


def _session(entry: EquipmentEntry, role: str = "sealer") -> LabSession:
    """Build a LabSession without the async http context.

    validate_plan is offline-only so we never enter ``async with`` and
    never spin up the AsyncClient. The session's binding + registry
    properties are sufficient.
    """

    return LabSession(
        registry=_registry(entry),
        binding={role: entry.id},
    )


# -- happy / offline-validator paths -----------------------------------------


def test_happy_path_v11_device_no_warnings_no_violations() -> None:
    entry = _entry(protocol="1.1")
    session = _session(entry)
    plan = Plan(steps=[Step(role="sealer", skill="seal.start", args={"temperature_c": 170, "seconds": 3.0})])

    report = validate_plan(plan, session)

    assert report.ok is True
    assert report.violations == []
    assert report.warnings == []
    assert len(report.steps) == 1
    sr = report.steps[0]
    assert sr.ok is True
    assert sr.role == "sealer"
    assert sr.skill == "seal.start"
    assert sr.step_id == "step_0"


def test_v10_device_emits_no_claim_semantics_warning() -> None:
    entry = _entry(protocol="1.0")
    session = _session(entry)
    plan = Plan(steps=[Step(role="sealer", skill="seal.start", args={"temperature_c": 170, "seconds": 3.0})])

    report = validate_plan(plan, session)

    assert report.ok is True  # warning, not blocker
    codes = [w.code for w in report.warnings]
    assert "no_claim_semantics" in codes
    nc = next(w for w in report.warnings if w.code == "no_claim_semantics")
    assert nc.severity == "warning"
    assert nc.step_id == "step_0"


def test_unknown_role_blocks_plan() -> None:
    entry = _entry()
    session = _session(entry, role="sealer")
    plan = Plan(steps=[Step(role="not_bound", skill="seal.start")])

    report = validate_plan(plan, session)

    assert report.ok is False
    codes = [v.code for v in report.violations]
    assert "unknown_role" in codes


def test_unknown_skill_blocks_plan() -> None:
    entry = _entry()
    session = _session(entry)
    plan = Plan(steps=[Step(role="sealer", skill="not_a_skill")])

    report = validate_plan(plan, session)

    assert report.ok is False
    assert any(v.code == "unknown_skill" for v in report.violations)


def test_invalid_args_blocks_plan() -> None:
    entry = _entry(protocol="1.1")
    session = _session(entry)
    plan = Plan(
        steps=[
            # 999 C is above the 235 C ceiling on SealStartArgs.temperature_c
            Step(role="sealer", skill="seal.start", args={"temperature_c": 999})
        ]
    )

    report = validate_plan(plan, session)

    assert report.ok is False
    assert any(v.code == "invalid_args" for v in report.violations)


def test_unknown_requires_blocks_plan() -> None:
    entry = _entry(protocol="1.1")
    session = _session(entry)
    plan = Plan(
        steps=[
            Step(role="sealer", skill="seal.start", args={"temperature_c": 170, "seconds": 3.0}, id="a"),
            Step(role="sealer", skill="seal.stop", id="b", requires=["does_not_exist"]),
        ]
    )

    report = validate_plan(plan, session)

    assert report.ok is False
    assert any(v.code == "unknown_requires" for v in report.violations)


def test_step_id_auto_assigned_from_index() -> None:
    entry = _entry(protocol="1.1")
    session = _session(entry)
    plan = Plan(
        steps=[
            Step(role="sealer", skill="startup"),
            Step(role="sealer", skill="seal.start", args={"temperature_c": 170, "seconds": 3.0}),
        ]
    )

    report = validate_plan(plan, session)

    assert [s.step_id for s in report.steps] == ["step_0", "step_1"]


# -- built-in interlocks -----------------------------------------------------


def test_do_not_call_connect_allows_explicit_plan_steps() -> None:
    """The flag suppresses auto-connect; it must not block explicit skills."""

    entry = _entry(
        id="ot2_hte",
        do_not_call_connect=True,
        kind="liquid_handler",
        protocol="1.1",
    )
    session = _session(entry, role="robot")
    plan = Plan(
        steps=[
            Step(
                role="robot",
                skill="tips.reset",
                args={"nickname": "tips_300"},
            )
        ]
    )
    report = validate_plan(plan, session)

    assert report.ok is True
    assert report.violations == []


def test_builtin_disallow_step_to_offline_role_when_disabled() -> None:
    entry = _entry(enabled=False, protocol="1.1")
    session = _session(entry)
    plan = Plan(steps=[Step(role="sealer", skill="seal.start", args={"temperature_c": 170, "seconds": 3.0})])

    report = validate_plan(plan, session)

    assert any(
        v.code == "role_offline" and v.severity == "critical"
        for v in report.violations
    )


def test_builtin_warn_if_skill_duration_unknown() -> None:
    """Inject a SkillDef with no estimated_duration_s and assert the
    info-level interlock fires for it."""

    sd = SkillDef(
        name="_test_no_duration",
        kind="plate_sealer",
        description="test only",
        endpoint="/control/_test",
        args_schema=StartupArgs,
        # estimated_duration_s defaults to None
    )
    SKILL_REGISTRY["plate_sealer"].append(sd)
    try:
        entry = _entry(protocol="1.1")
        session = _session(entry)
        plan = Plan(steps=[Step(role="sealer", skill="_test_no_duration")])

        report = validate_plan(plan, session)

        assert report.ok is True  # info, not blocker
        assert any(
            w.code == "duration_unknown"
            and w.severity == "info"
            and w.interlock_name == "warn_if_skill_duration_unknown"
            for w in report.warnings
        )
    finally:
        SKILL_REGISTRY["plate_sealer"].remove(sd)


# -- user-registered interlocks ---------------------------------------------


def test_register_custom_interlock_runs_and_blocks_plan() -> None:
    @register_interlock(name="custom_block_seal_at_high_temp")
    def _block_high_temp(plan, step, session):
        if step.skill == "seal.start" and step.args.get("temperature_c", 0) > 200:
            return [
                Violation(
                    step_id=step.id,
                    step_index=step.index,
                    code="seal_too_hot",
                    message="temperature_c > 200 not allowed for this campaign",
                    severity="critical",
                    interlock_name="custom_block_seal_at_high_temp",
                )
            ]
        return None

    assert "custom_block_seal_at_high_temp" in registered_interlocks()

    entry = _entry(protocol="1.1")
    session = _session(entry)
    plan = Plan(steps=[Step(role="sealer", skill="seal.start", args={"temperature_c": 220, "seconds": 3.0})])

    report = validate_plan(plan, session)

    assert report.ok is False
    assert any(v.code == "seal_too_hot" for v in report.violations)


def test_clear_interlocks_resets_to_builtins_only() -> None:
    register_interlock(lambda plan, step, session: None, name="ephemeral")
    assert "ephemeral" in registered_interlocks()

    clear_interlocks()

    assert "ephemeral" not in registered_interlocks()
    # built-ins should still be present
    assert "disallow_step_to_offline_role" in registered_interlocks()
    assert "warn_if_skill_duration_unknown" in registered_interlocks()


def test_buggy_interlock_yields_critical_violation_not_crash() -> None:
    @register_interlock(name="buggy_interlock")
    def _boom(plan, step, session):
        raise RuntimeError("oops")

    entry = _entry(protocol="1.1")
    session = _session(entry)
    plan = Plan(steps=[Step(role="sealer", skill="startup")])

    report = validate_plan(plan, session)

    assert report.ok is False
    crashes = [v for v in report.violations if v.code == "interlock_error"]
    assert len(crashes) == 1
    assert crashes[0].severity == "critical"
    assert crashes[0].interlock_name == "buggy_interlock"


def test_register_interlock_decorator_form_without_args() -> None:
    @register_interlock
    def my_rule(plan, step, session):  # noqa: ARG001
        return None

    assert any(name.endswith("my_rule") for name in registered_interlocks())
