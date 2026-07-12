"""Plan / Step model + offline plan validation.

A workflow assembles a :class:`Plan` (a sequence of :class:`Step`s),
hands it to :func:`validate_plan`, and inspects the resulting
:class:`PlanReport` before deciding whether to execute. ``validate_plan``
is **fully offline**: it issues no HTTP traffic. Cheap enough to run in a
notebook on every keystroke, conservative enough that a green report
plus successful claim acquisition is a strong "this is safe to run".

Validation layers (in order, all evaluated for every step so the report
is comprehensive rather than first-failure):

1. The role is bound on the session.
2. The bound equipment id exists in the registry.
3. The catalog (:data:`SKILL_REGISTRY`) defines a :class:`SkillDef` for
   ``(entry.kind, step.skill)``.
4. ``step.args`` validate against the SkillDef's ``args_schema``.
5. Every registered interlock (built-in + user-registered) is run against
   the step. See :mod:`lab_skills.interlocks`.

Layer 5 also produces info / warning Violations (for example "this
device is v1.0 and does not support claims; the workflow will be unable
to enforce mutual exclusion"). The plan is still considered ``ok`` as
long as no error / critical Violations are present.

:func:`execute_plan` (v0.4) runs a validated plan against live hardware:
it re-checks each step against the device's live ``/status`` and the
async interlocks (layer 4) *immediately before* executing it — closing
the validate-then-execute race — wraps the step in a
:class:`~lab_skills.ClaimManager`, and POSTs the SkillDef's endpoint with
the claim token attached. It is fail-fast and sequential: the first
failed / blocked step aborts the run and the rest are reported
``skipped``. Pass ``dry_run=True`` for a live preflight that does
everything except the ClaimManager + command POST.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ValidationError

from .claims import ClaimManager
from .exceptions import LabError
from .interlocks import Violation, run_interlocks, run_interlocks_async
from .session import _availability
from .skill_catalog import SkillDef, skills_for

if TYPE_CHECKING:
    from .session import LabSession


class Step(BaseModel):
    """One unit of a :class:`Plan`.

    ``role`` is a session binding name (e.g. ``"sealer"``); ``skill`` is a
    :class:`Skill.name` from the catalog (e.g. ``"seal.start"``);
    ``args`` is a dict that must validate against the SkillDef's
    ``args_schema``. ``id`` is auto-assigned during validation if missing.
    """

    role: str
    skill: str
    args: dict[str, Any] = {}
    id: str | None = None
    requires: list[str] = []  # step.id deps; advisory in v0.3

    # Populated by validate_plan() - the index of this step in its plan.
    # Surfaced on Violations to make plan reports easy to inspect.
    index: int | None = None

    def with_index(self, index: int) -> "Step":
        return self.model_copy(update={"index": index, "id": self.id or f"step_{index}"})


class Plan(BaseModel):
    """An ordered sequence of :class:`Step`s.

    Today plans are linear; a future revision may extend ``Step.requires``
    to a real DAG. Validation already inspects ``requires`` so plans
    written defensively today will continue to validate against tomorrow's
    executor.
    """

    steps: list[Step]


class StepReport(BaseModel):
    """Per-step validation result.

    ``ok`` is ``True`` iff no ``error`` or ``critical`` violations were
    recorded against this step. ``info`` and ``warning`` violations live
    on ``warnings`` (not ``violations``) so workflow code can branch on
    "blockers" vs "advisories" without inspecting severity.
    """

    step_id: str
    step_index: int
    role: str
    skill: str
    ok: bool
    violations: list[Violation]  # error / critical
    warnings: list[Violation]  # info / warning


class PlanReport(BaseModel):
    """Aggregated outcome of :func:`validate_plan`.

    ``ok`` is ``True`` iff every step has ``ok=True``. Inspect ``steps``
    for per-step detail or use the :attr:`violations` /
    :attr:`warnings` shortcuts for whole-plan rollups.
    """

    ok: bool
    steps: list[StepReport]

    @property
    def violations(self) -> list[Violation]:
        out: list[Violation] = []
        for s in self.steps:
            out.extend(s.violations)
        return out

    @property
    def warnings(self) -> list[Violation]:
        out: list[Violation] = []
        for s in self.steps:
            out.extend(s.warnings)
        return out


StepRunStatus = Literal["succeeded", "failed", "blocked", "skipped", "dry_run"]


class StepRunReport(BaseModel):
    """Per-step outcome of :func:`execute_plan`.

    ``status``:

    * ``succeeded`` — the control command returned 2xx.
    * ``dry_run`` — the step passed the live re-check but was not executed
      (``execute_plan(..., dry_run=True)``).
    * ``blocked`` — a layer-3 (device does not currently allow the skill) or
      layer-4 (interlock) check failed; ``violations`` explains why. No
      command was sent.
    * ``failed`` — the command (or claim acquisition) raised; ``error`` holds
      the message.
    * ``skipped`` — an earlier step aborted the run before this one.
    """

    step_id: str
    step_index: int
    role: str
    skill: str
    status: StepRunStatus
    equipment_id: str | None = None
    claimed: bool = False
    response: Any = None
    error: str | None = None
    violations: list[Violation] = []


class PlanRunReport(BaseModel):
    """Aggregated outcome of :func:`execute_plan`.

    ``ok`` is ``True`` iff the offline validation passed **and** every step
    ended ``succeeded`` (or ``dry_run`` in a dry run). ``validation`` carries
    the up-front :class:`PlanReport`; on a validation failure the run stops
    before touching hardware and ``steps`` is empty. ``claims_acquired`` lists
    the equipment ids for which a real (non-degraded) claim was held.
    """

    ok: bool
    dry_run: bool = False
    validation: PlanReport
    steps: list[StepRunReport] = []
    claims_acquired: list[str] = []


# Severities that block plan execution.
_BLOCKING_SEVERITIES = {"error", "critical"}


def validate_plan(plan: Plan, session: LabSession) -> PlanReport:
    """Run every offline check against ``plan`` against the live session
    bindings + registry. Does not make any HTTP calls.

    The session is consulted for:

    * its role -> equipment_id binding
    * the registry (to resolve ``EquipmentEntry`` and ``protocol``)

    The shared ``SKILL_REGISTRY`` is consulted for ``args_schema`` per
    skill name. Interlocks are consulted via
    :func:`lab_skills.interlocks.run_interlocks`.
    """

    binding = session.binding
    registry = session.registry

    step_reports: list[StepReport] = []
    for index, raw_step in enumerate(plan.steps):
        step = raw_step.with_index(index)
        violations: list[Violation] = []
        warnings: list[Violation] = []

        equipment_id = binding.get(step.role)
        entry = registry.by_id(equipment_id) if equipment_id is not None else None

        if equipment_id is None:
            violations.append(
                _violation(
                    step,
                    "unknown_role",
                    f"role {step.role!r} is not bound on this LabSession",
                    actionable=(
                        "pass binding={...} to Lab.connect or session.bind(...)"
                    ),
                )
            )
        elif entry is None:
            violations.append(
                _violation(
                    step,
                    "unknown_equipment",
                    f"role {step.role!r} -> {equipment_id!r} is not in equipment.yaml",
                )
            )
        else:
            sd = _find_skill_def(entry.kind, step.skill)
            if sd is None:
                violations.append(
                    _violation(
                        step,
                        "unknown_skill",
                        (
                            f"skill {step.skill!r} is not registered for kind "
                            f"{entry.kind!r}"
                        ),
                        actionable=(
                            "register a SkillDef in skill_catalog/<kind>.py "
                            "or fix the step's skill name"
                        ),
                    )
                )
            else:
                args_violation = _validate_args(step, sd)
                if args_violation is not None:
                    violations.append(args_violation)

            # v1.0 device -> annotate the warning so the executor (v0.4)
            # knows it cannot enforce mutual exclusion via ClaimManager.
            if entry.protocol == "1.0":
                warnings.append(
                    _violation(
                        step,
                        "no_claim_semantics",
                        (
                            f"target device {entry.id!r} is on STATUS_SPEC v1.0; "
                            f"ClaimManager will degrade to a no-op for this step"
                        ),
                        severity="warning",
                        actionable=(
                            "migrate the device to STATUS_SPEC v1.1 and set "
                            "protocol: \"1.1\" on its equipment.yaml entry"
                        ),
                    )
                )

        # Validate step.requires references actual ids in this plan.
        if step.requires:
            known_ids = {(s.id or f"step_{i}") for i, s in enumerate(plan.steps)}
            for req in step.requires:
                if req not in known_ids:
                    violations.append(
                        _violation(
                            step,
                            "unknown_requires",
                            f"step.requires references unknown step id {req!r}",
                        )
                    )

        # Run user / built-in interlocks. Done last so interlocks see the
        # canonical step (with index + id assigned).
        interlock_violations = run_interlocks(plan, step, session)
        for v in interlock_violations:
            if v.severity in _BLOCKING_SEVERITIES:
                violations.append(v)
            else:
                warnings.append(v)

        step_reports.append(
            StepReport(
                step_id=step.id or f"step_{index}",
                step_index=index,
                role=step.role,
                skill=step.skill,
                ok=not violations,
                violations=violations,
                warnings=warnings,
            )
        )

    return PlanReport(
        ok=all(s.ok for s in step_reports),
        steps=step_reports,
    )


async def execute_plan(
    plan: Plan,
    session: LabSession,
    *,
    owner: str,
    ttl_s: float = 30.0,
    dry_run: bool = False,
) -> PlanRunReport:
    """Execute a validated :class:`Plan` against live hardware, sequentially.

    Flow:

    1. Run the offline :func:`validate_plan` once. If it is not ``ok``, return
       immediately without touching any device (inspect ``report.validation``).
    2. For each step, in order:

       a. Resolve the role to its live :class:`~lab_skills.EquipmentClient`.
       b. Re-run layer-4 interlocks against **live** state
          (:func:`run_interlocks_async`); a blocking violation blocks the step.
       c. Re-read the device's ``/status`` and confirm the skill is currently
          allowed (layer 3, via the same ``_availability`` used by
          ``session.skills()``); if not, block the step.
       d. Unless ``dry_run``: acquire a per-step
          :class:`~lab_skills.ClaimManager`, POST the SkillDef's endpoint with
          ``step.args`` and the claim token, and assert the claim is still
          alive.

    Fail-fast: the first ``failed`` / ``blocked`` step aborts the run; the
    remaining steps are reported ``skipped``. This mirrors
    ``docs/INTERLOCKS.md`` (re-validate layer 3 + layer 4 immediately before
    each step) and design decision #1 in ``docs/ARCHITECTURE.md`` (the device
    is the authority; every write competes for the same cooperative claim).

    ``owner`` is stamped into each claim (surfaced in ``details.claimed_by``).
    ``dry_run=True`` performs every check but skips the claim + command POST,
    marking each passing step ``dry_run`` — a live preflight that never
    actuates hardware.
    """

    validation = validate_plan(plan, session)
    if not validation.ok:
        return PlanRunReport(ok=False, dry_run=dry_run, validation=validation)

    steps_out: list[StepRunReport] = []
    claims_acquired: list[str] = []
    aborted = False

    for index, raw_step in enumerate(plan.steps):
        step = raw_step.with_index(index)
        base = dict(
            step_id=step.id or f"step_{index}",
            step_index=index,
            role=step.role,
            skill=step.skill,
        )

        if aborted:
            steps_out.append(StepRunReport(status="skipped", **base))
            continue

        # (a) resolve the live client. validate_plan already proved the role is
        # bound and not in maintenance, but live state can change under us.
        try:
            client = session.role(step.role)
        except LabError as exc:
            steps_out.append(StepRunReport(status="failed", error=str(exc), **base))
            aborted = True
            continue
        base["equipment_id"] = client.equipment_id
        sd = _find_skill_def(client.entry.kind, step.skill)  # not None post-validation

        # (b) layer-4 interlocks against live state.
        interlock_violations = await run_interlocks_async(plan, step, session)
        blocking = [v for v in interlock_violations if v.severity in _BLOCKING_SEVERITIES]
        if blocking:
            steps_out.append(
                StepRunReport(status="blocked", violations=blocking, **base)
            )
            aborted = True
            continue

        # (c) layer-3 live re-check: does the device allow this skill right now?
        try:
            status = await client.status()
        except LabError as exc:
            steps_out.append(StepRunReport(status="failed", error=str(exc), **base))
            aborted = True
            continue
        if sd is not None:
            available, reason = _availability(sd, status, None, None)
            if not available:
                steps_out.append(
                    StepRunReport(
                        status="blocked",
                        violations=[
                            _violation(
                                step,
                                "not_allowed_live",
                                f"device does not currently allow {step.skill!r}: {reason}",
                                severity="error",
                                interlock_name="execute_plan",
                            )
                        ],
                        **base,
                    )
                )
                aborted = True
                continue

        if dry_run:
            steps_out.append(StepRunReport(status="dry_run", **base))
            continue

        # (d) execute under a per-step claim.
        endpoint = sd.endpoint if sd is not None else f"/control/{step.skill}"
        try:
            async with ClaimManager(client, owner=owner, ttl_s=ttl_s) as claim:
                claimed = not claim.degraded
                if claimed:
                    claims_acquired.append(client.equipment_id)
                response = await client.command(
                    endpoint, step.args, claim_token=claim.token
                )
                claim.assert_alive()
        except LabError as exc:
            steps_out.append(StepRunReport(status="failed", error=str(exc), **base))
            aborted = True
            continue

        steps_out.append(
            StepRunReport(
                status="succeeded", response=response, claimed=claimed, **base
            )
        )

    terminal_ok = {"succeeded", "dry_run"}
    ok = all(s.status in terminal_ok for s in steps_out)
    return PlanRunReport(
        ok=ok,
        dry_run=dry_run,
        validation=validation,
        steps=steps_out,
        claims_acquired=claims_acquired,
    )


# -- helpers ------------------------------------------------------------------


def _violation(
    step: Step,
    code: str,
    message: str,
    *,
    severity: str = "error",
    actionable: str | None = None,
    interlock_name: str = "validate_plan",
) -> Violation:
    return Violation(
        step_id=step.id or f"step_{step.index or 0}",
        step_index=step.index,
        code=code,
        message=message,
        severity=severity,  # type: ignore[arg-type]
        actionable=actionable,
        interlock_name=interlock_name,
    )


def _find_skill_def(kind: str, skill_name: str) -> SkillDef | None:
    by_name = {d.name: d for d in skills_for(kind)}
    return by_name.get(skill_name)


def _validate_args(step: Step, sd: SkillDef) -> Violation | None:
    try:
        sd.args_schema.model_validate(step.args)
    except ValidationError as exc:
        return _violation(
            step,
            "invalid_args",
            f"args do not validate against {sd.name!r} schema: {exc.errors()}",
            actionable=(
                f"see SkillDef.args_schema for {sd.kind}.{sd.name!r} for the "
                f"required shape"
            ),
        )
    return None


__all__ = [
    "Plan",
    "PlanReport",
    "PlanRunReport",
    "Step",
    "StepReport",
    "StepRunReport",
    "execute_plan",
    "validate_plan",
]
