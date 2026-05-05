"""Plan-level interlocks (safety layer 4 from ``docs/INTERLOCKS.md``).

An *interlock* is a registered function that a workflow asks to be checked
before any step of a :class:`Plan` is executed. Interlocks are the place to
encode rules that no individual device can know about - cross-device
spatial constraints, project chemistry, and operator-defined invariants.

v0.3 surface:

- :func:`register_interlock` registers a sync callable
  ``fn(plan, step, session) -> list[Violation] | None``.
- :class:`Violation` is a typed, serialisable result row.
- :func:`registered_interlocks` introspects the current registry (mostly
  for tests / agent introspection).
- :func:`clear_interlocks` resets the registry to the built-ins (tests).

Async interlocks that read live device state are deferred to v0.4, where
``execute_plan`` will call them between every step. v0.3 keeps the surface
**offline only** so :func:`lab_skills.plan.validate_plan` is a pure CPU
check that workflow code can run in tight loops.

Built-in interlocks
-------------------

* :func:`disallow_step_to_offline_role` (severity: ``critical``) - rejects
  steps whose target equipment is disabled, in maintenance, or has the
  registry flag ``do_not_call_connect: true`` set (xArm today). The flag's
  whole purpose is "do not POST control to this device"; this interlock
  enforces it at plan time so the violation surfaces before any HTTP call.
* :func:`warn_if_skill_duration_unknown` (severity: ``info``) - advisory
  warning when a step's :class:`SkillDef` has no
  ``estimated_duration_s``. Workflows that surface ETA to operators benefit
  from knowing which steps will not contribute to the total.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Literal

from pydantic import BaseModel

from .skill_catalog import skills_for

if TYPE_CHECKING:  # avoid import cycle: plan imports interlocks.
    from .plan import Plan, Step
    from .session import LabSession


Severity = Literal["info", "warning", "error", "critical"]


class Violation(BaseModel):
    """One interlock finding tied to a single :class:`Step` of a plan.

    ``severity`` decides whether the finding blocks the plan
    (``"error"`` / ``"critical"``) or only annotates it
    (``"info"`` / ``"warning"``).
    """

    step_id: str
    step_index: int | None
    code: str
    message: str
    severity: Severity = "error"
    actionable: str | None = None
    interlock_name: str


InterlockFn = Callable[["Plan", "Step", "LabSession"], "list[Violation] | None"]


_INTERLOCKS: dict[str, InterlockFn] = {}


def register_interlock(
    fn: InterlockFn | None = None, *, name: str | None = None
) -> InterlockFn | Callable[[InterlockFn], InterlockFn]:
    """Register ``fn`` as a plan-validation interlock.

    Usable as ``@register_interlock`` or
    ``@register_interlock(name="...")``. The default name is
    ``fn.__qualname__``; passing a name explicitly is recommended so
    :class:`Violation.interlock_name` stays readable in dashboards.

    Re-registration with the same name **replaces** the existing
    function; this is intentional so workflows can iterate on a rule in a
    notebook without restarting the kernel.
    """

    def _bind(target: InterlockFn) -> InterlockFn:
        n = name or getattr(target, "__qualname__", target.__name__)
        _INTERLOCKS[n] = target
        return target

    if fn is None:
        return _bind
    return _bind(fn)


def registered_interlocks() -> list[str]:
    """Return the names of currently registered interlocks (for introspection)."""

    return list(_INTERLOCKS)


def run_interlocks(
    plan: "Plan", step: "Step", session: "LabSession"
) -> list[Violation]:
    """Run every registered interlock against ``step`` and aggregate violations.

    Each interlock is wrapped in its own try/except so a buggy rule cannot
    take the whole validator down. A raising interlock surfaces as a
    critical Violation against the step it was checking.
    """

    out: list[Violation] = []
    for ilk_name, fn in list(_INTERLOCKS.items()):
        try:
            result = fn(plan, step, session)
        except Exception as exc:  # noqa: BLE001 - any rule bug surfaces here
            out.append(
                Violation(
                    step_id=step.id,
                    step_index=step.index,
                    code="interlock_error",
                    message=f"interlock {ilk_name!r} raised {type(exc).__name__}: {exc}",
                    severity="critical",
                    interlock_name=ilk_name,
                )
            )
            continue
        if result:
            out.extend(result)
    return out


# -- Built-in interlocks ------------------------------------------------------


def disallow_step_to_offline_role(
    plan: "Plan", step: "Step", session: "LabSession"
) -> list[Violation] | None:
    """Reject steps whose target equipment is offline / in maintenance / no-control.

    ``do_not_call_connect: true`` in ``equipment.yaml`` is read as the
    operator's standing instruction "do not POST control to this device".
    The xArm is the canonical example today (it lives on a private subnet
    behind a gateway PC). This interlock surfaces such a violation at
    plan time rather than at execution time so agents/operators see
    *why* a plan was rejected without having to chase an HTTP failure.
    """

    binding = session.binding
    equipment_id = binding.get(step.role)
    if equipment_id is None:
        # role-binding violations are also produced by validate_plan; we
        # silently skip here so the user does not see the same complaint
        # twice from two interlocks at different layers.
        return None
    entry = session.registry.by_id(equipment_id)
    if entry is None:
        return None
    if not entry.enabled or entry.maintenance is not None:
        reason = "disabled (enabled: false)"
        if entry.maintenance is not None:
            reason = f"in maintenance: {entry.maintenance.reason}"
        return [
            Violation(
                step_id=step.id,
                step_index=step.index,
                code="role_offline",
                message=(
                    f"step targets role {step.role!r} -> {equipment_id!r} which is {reason}"
                ),
                severity="critical",
                actionable="re-enable the device or rebind the role",
                interlock_name="disallow_step_to_offline_role",
            )
        ]
    if entry.do_not_call_connect:
        return [
            Violation(
                step_id=step.id,
                step_index=step.index,
                code="do_not_call_connect",
                message=(
                    f"role {step.role!r} -> {equipment_id!r} is marked "
                    f"do_not_call_connect: true; control commands are forbidden"
                ),
                severity="critical",
                actionable=(
                    "use a different role for this skill, or remove "
                    "do_not_call_connect from equipment.yaml"
                ),
                interlock_name="disallow_step_to_offline_role",
            )
        ]
    return None


def warn_if_skill_duration_unknown(
    plan: "Plan", step: "Step", session: "LabSession"
) -> list[Violation] | None:
    """Info-level warning when a step's SkillDef has no ``estimated_duration_s``.

    Workflows that surface ETA to operators or agents lose precision for
    every skill that does not declare a duration. This interlock makes
    the gap visible at plan time so the catalog gets filled in.
    """

    equipment_id = session.binding.get(step.role)
    if equipment_id is None:
        return None
    entry = session.registry.by_id(equipment_id)
    if entry is None:
        return None
    by_name = {d.name: d for d in skills_for(entry.kind)}
    sd = by_name.get(step.skill)
    if sd is None:
        # validate_plan reports the unknown_skill case at error severity;
        # don't double-up.
        return None
    if sd.estimated_duration_s is None:
        return [
            Violation(
                step_id=step.id,
                step_index=step.index,
                code="duration_unknown",
                message=(
                    f"skill {step.skill!r} has no estimated_duration_s; "
                    f"plan ETA will be incomplete"
                ),
                severity="info",
                actionable=(
                    f"populate estimated_duration_s on the SkillDef for "
                    f"{entry.kind}.{step.skill!r} in skill_catalog/"
                ),
                interlock_name="warn_if_skill_duration_unknown",
            )
        ]
    return None


def _register_builtins() -> None:
    register_interlock(
        disallow_step_to_offline_role, name="disallow_step_to_offline_role"
    )
    register_interlock(
        warn_if_skill_duration_unknown, name="warn_if_skill_duration_unknown"
    )


def clear_interlocks() -> None:
    """Reset the interlock registry to just the built-ins.

    Test helper; re-imports the built-ins so the SDK starts every test in
    a known state.
    """

    _INTERLOCKS.clear()
    _register_builtins()


# Populate built-ins on first import.
_register_builtins()


__all__ = [
    "InterlockFn",
    "Severity",
    "Violation",
    "clear_interlocks",
    "disallow_step_to_offline_role",
    "register_interlock",
    "registered_interlocks",
    "run_interlocks",
    "warn_if_skill_duration_unknown",
]
