"""Plan-level interlocks (safety layer 4 from ``docs/INTERLOCKS.md``).

An *interlock* is a registered function that a workflow asks to be checked
before any step of a :class:`Plan` is executed. Interlocks are the place to
encode rules that no individual device can know about - cross-device
spatial constraints, project chemistry, and operator-defined invariants.

Surface:

- :func:`register_interlock` registers a callable
  ``fn(plan, step, session) -> list[Violation] | None``. As of v0.4 the
  callable may be **async** (``async def``) — such interlocks may read live
  device state (``await session.role(...).status()``).
- :class:`Violation` is a typed, serialisable result row.
- :func:`registered_interlocks` introspects the current registry (mostly
  for tests / agent introspection).
- :func:`clear_interlocks` resets the registry to the built-ins (tests).

Two runners, one registry (v0.4):

- :func:`run_interlocks` is **sync and offline**. It runs only the sync
  interlocks and *skips* async ones, so :func:`lab_skills.plan.validate_plan`
  stays a pure-CPU check safe to run in tight notebook loops. Async
  interlocks are simply not evaluated there (they need I/O).
- :func:`run_interlocks_async` runs **both** sync and async interlocks and
  is what :func:`lab_skills.plan.execute_plan` calls immediately before each
  step, so live-state (layer-4) rules are enforced at execution time.

Built-in interlocks
-------------------

* :func:`disallow_step_to_offline_role` (severity: ``critical``) - rejects
  steps whose target equipment is disabled or in maintenance.
  ``do_not_call_connect`` is intentionally not considered here: it suppresses
  automatic connection/startup, while explicit plan steps remain governed by
  live ``allowed_actions``, claims, and the other interlock layers.
* :func:`warn_if_skill_duration_unknown` (severity: ``info``) - advisory
  warning when a step's :class:`SkillDef` has no
  ``estimated_duration_s``. Workflows that surface ETA to operators benefit
  from knowing which steps will not contribute to the total.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Awaitable, Callable, Literal, Union

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


_Result = "list[Violation] | None"
SyncInterlockFn = Callable[["Plan", "Step", "LabSession"], "list[Violation] | None"]
AsyncInterlockFn = Callable[
    ["Plan", "Step", "LabSession"], Awaitable["list[Violation] | None"]
]
InterlockFn = Union[SyncInterlockFn, AsyncInterlockFn]


_INTERLOCKS: dict[str, InterlockFn] = {}


def _is_async(fn: InterlockFn) -> bool:
    return inspect.iscoroutinefunction(fn)


def _error_violation(ilk_name: str, step: "Step", exc: BaseException) -> Violation:
    """Turn a raising interlock into a critical Violation so one buggy rule
    cannot take the whole check down."""

    return Violation(
        step_id=step.id or f"step_{step.index or 0}",
        step_index=step.index,
        code="interlock_error",
        message=f"interlock {ilk_name!r} raised {type(exc).__name__}: {exc}",
        severity="critical",
        interlock_name=ilk_name,
    )


def register_interlock(
    fn: InterlockFn | None = None, *, name: str | None = None
) -> InterlockFn | Callable[[InterlockFn], InterlockFn]:
    """Register ``fn`` as a plan-validation interlock.

    ``fn`` may be sync or ``async def``. Sync rules run in both
    :func:`run_interlocks` (offline validate_plan) and
    :func:`run_interlocks_async` (execute_plan); async rules run only in
    the latter, where awaiting live device state is allowed.

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
    """Run every registered **sync** interlock against ``step`` and aggregate
    violations. Async interlocks are skipped (this path is offline, used by
    :func:`lab_skills.plan.validate_plan`); they run in
    :func:`run_interlocks_async`.

    Each interlock is wrapped in its own try/except so a buggy rule cannot
    take the whole validator down. A raising interlock surfaces as a
    critical Violation against the step it was checking.
    """

    out: list[Violation] = []
    for ilk_name, fn in list(_INTERLOCKS.items()):
        if _is_async(fn):
            continue
        try:
            result = fn(plan, step, session)
        except Exception as exc:  # noqa: BLE001 - any rule bug surfaces here
            out.append(_error_violation(ilk_name, step, exc))
            continue
        if result:
            out.extend(result)
    return out


async def run_interlocks_async(
    plan: "Plan", step: "Step", session: "LabSession"
) -> list[Violation]:
    """Run **every** registered interlock (sync + async) against ``step``.

    This is the execution-time runner used by
    :func:`lab_skills.plan.execute_plan` immediately before each step, so
    async interlocks that read live device state (layer 4) are enforced at
    the moment of execution — closing the validate-then-execute race
    (``docs/INTERLOCKS.md``). Each interlock is isolated in its own
    try/except; a raising rule becomes a critical Violation.
    """

    out: list[Violation] = []
    for ilk_name, fn in list(_INTERLOCKS.items()):
        try:
            if _is_async(fn):
                result = await fn(plan, step, session)  # type: ignore[misc]
            else:
                result = fn(plan, step, session)
                if inspect.isawaitable(result):  # sync fn returning a coroutine
                    result = await result
        except Exception as exc:  # noqa: BLE001 - any rule bug surfaces here
            out.append(_error_violation(ilk_name, step, exc))
            continue
        if result:
            out.extend(result)
    return out


# -- Built-in interlocks ------------------------------------------------------


def disallow_step_to_offline_role(
    plan: "Plan", step: "Step", session: "LabSession"
) -> list[Violation] | None:
    """Reject steps whose target equipment is disabled or in maintenance.

    ``do_not_call_connect`` only prevents automatic connection/startup; it
    does not forbid an explicit, validated plan step. Explicit commands are
    still gated at execution time by live ``allowed_actions``, claims, and
    every registered layer-4 interlock.
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
    "AsyncInterlockFn",
    "InterlockFn",
    "Severity",
    "SyncInterlockFn",
    "Violation",
    "clear_interlocks",
    "disallow_step_to_offline_role",
    "register_interlock",
    "registered_interlocks",
    "run_interlocks",
    "run_interlocks_async",
    "warn_if_skill_duration_unknown",
]
