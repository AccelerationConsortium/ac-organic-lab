"""Optional custody interlocks — "plate X must be at location L before step S".

Not registered by default. A project that tracks plates in the record layer
opts in explicitly, one rule per plate it cares about::

    from lab_skills import register_interlock
    from lab_skills.interlocks_custody import require_plate_at

    async def where_is(hid: str) -> dict:
        # your project's read of the custody ledger
        return await record_layer.current_location(hid)

    register_interlock(require_plate_at(
        "PLT-0042", "torry_pines_shaker/nest", where_is,
        applies_to={"shake_start"}, strict=True,
    ))

This is the layer-4 rule ``docs/INTERLOCKS.md`` and ``docs/PLATE_TRACKING.md``
D1 both name and neither had a written form of: the registry enumerates places
and the devices adjudicate moves, so "is the plate where this step assumes it
is" belongs to nobody but the plan. The dashboard's run executor answers the
same question its own way (``api/app/workflow.py::custody_preflight``, against
the chain of moves that run itself recorded); this is the SDK-native form, for
workflows that run without an authorization and without the dashboard.

**The lookup is the seam, and it is deliberately a callable.** ``skills/`` must
not import ``api/`` — a workflow repo depends on ``lab-skills`` alone
(``ARCHITECTURE.md`` decision #3) — so the ledger arrives as a function the
caller supplies. Its contract is the shape
``api/app/custody.py::CustodyRecorder.current_location`` returns, which is also
what AnaliticaDB's container read reduces to:

``async def lookup(hid: str) -> Mapping`` returning at least

* ``found: True`` and ``location_name: str | None`` — the ledger placed it;
* ``found: False`` — the ledger has no such container;
* ``found: None`` (and optionally ``error``) — the store could not answer.

Passing ``CustodyRecorder.current_location`` bound with its keyword arguments
(``functools.partial(recorder.current_location, user=…, project=…,
refresh=True)``) satisfies it directly. ``refresh=True`` matters for anything
long-running: that recorder caches container rows, and ``location_id`` is
exactly what a move changes.

**Severities follow the custody discipline** the rest of the system uses: a
*contradiction* blocks, an *absence* warns. A ledger that names a different
place is ``critical`` — the plan is about to act on a plate that is not there.
A ledger that cannot answer, or has never heard of the container, is a
``warning``: not knowing where a plate is is not the same as knowing it is in
the wrong place. ``strict=True`` promotes those two to ``error`` for a workflow
that would rather stop than proceed unverified — the same trade the executor's
``CUSTODY_STRICT`` makes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable, Container, Mapping

from .interlocks import AsyncInterlockFn, Severity, Violation

if TYPE_CHECKING:  # avoid import cycle.
    from .plan import Plan, Step
    from .session import LabSession


#: What a `lookup` is asked and what it must answer. See the module docstring.
CustodyLookup = Callable[[str], Awaitable[Mapping[str, Any]]]


def require_plate_at(
    hid: str,
    location: str,
    lookup: CustodyLookup,
    *,
    applies_to: "Container[str] | Callable[[Step], bool] | None" = None,
    strict: bool = False,
    name: str | None = None,
) -> AsyncInterlockFn:
    """Build an async interlock asserting that container ``hid`` is at the
    registry location ``location`` before a step runs.

    ``hid`` is the plate's barcode — ``Container.hid`` in the record layer, and
    the same string the device calls ``plate_id`` (``PLATE_TRACKING.md`` D4).
    ``location`` is a ``locations.yaml`` name (``ot2_hte/slot_2``), matched
    exactly: names are identifiers and are never renamed, so a comparison that
    tried to be clever about spelling would only mask a real disagreement.

    ``lookup`` reads the ledger; its contract is in the module docstring.
    ``applies_to`` narrows the rule to the steps that depend on the plate being
    there — ``None`` checks every step, a set of step ids checks those, and a
    predicate covers the rest. Narrowing is worth doing: the lookup is I/O, and
    :func:`~lab_skills.interlocks.run_interlocks_async` runs before *every*
    step of the plan.

    ``strict`` promotes the two "could not verify" findings from ``warning`` to
    ``error``, which blocks. The contradiction finding is ``critical`` either
    way.

    The returned closure carries a name derived from ``hid`` and ``location``
    (override with ``name``), so registering one rule per plate does not have
    them replace each other in the registry — ``register_interlock`` keys on
    the function's name and re-registration is deliberately a replacement.

    Returns the interlock; it is **not** registered. Hand it to
    :func:`~lab_skills.interlocks.register_interlock` yourself. The global
    registry is process-wide and outlives any one run, so a factory that
    registered on your behalf would quietly accumulate closures pinned to plates
    that left the lab hours ago.
    """

    ilk_name = name or f"require_plate_at[{hid}@{location}]"
    soft: Severity = "error" if strict else "warning"

    def _applies(step: "Step") -> bool:
        if applies_to is None:
            return True
        if callable(applies_to):
            return bool(applies_to(step))
        return (step.id or "") in applies_to

    def _violation(step: "Step", code: str, message: str, severity: Severity,
                   actionable: str | None = None) -> list[Violation]:
        return [
            Violation(
                step_id=step.id or f"step_{step.index or 0}",
                step_index=step.index,
                code=code,
                message=message,
                severity=severity,
                actionable=actionable,
                interlock_name=ilk_name,
            )
        ]

    async def custody_interlock(
        plan: "Plan", step: "Step", session: "LabSession"
    ) -> list[Violation] | None:
        if not _applies(step):
            return None

        try:
            row = await lookup(hid)
        except Exception as exc:  # noqa: BLE001 — a ledger outage is a finding
            # `run_interlocks_async` would turn this into a critical
            # `interlock_error`, which is the backstop for a *buggy rule* — not
            # the right report for a store that is simply down. Catching it here
            # keeps "we could not verify" at the severity the caller chose.
            return _violation(
                step, "custody_lookup_failed",
                f"could not read the custody ledger for plate {hid!r}: "
                f"{type(exc).__name__}: {exc}",
                soft,
                actionable="check the record layer is reachable, then re-validate",
            )

        found = row.get("found")
        if found is True:
            actual = row.get("location_name")
            if actual == location:
                return None
            return _violation(
                step, "plate_not_at_location",
                f"step requires plate {hid!r} at {location!r}, but the custody "
                f"ledger has it at {actual!r}",
                "critical",
                actionable=(
                    f"move the plate to {location!r} and record it (POST "
                    f"/api/custody/move), or fix whichever record is wrong — "
                    f"nothing auto-corrects"
                ),
            )
        if found is False:
            return _violation(
                step, "plate_unknown_to_ledger",
                f"step requires plate {hid!r} at {location!r}, but the custody "
                f"ledger has no container with that hid",
                soft,
                actionable=(
                    "register the container, or check the hid — a plate the "
                    "ledger never heard of has no recorded place to compare"
                ),
            )
        return _violation(
            step, "custody_lookup_failed",
            f"the custody ledger could not say where plate {hid!r} is"
            + (f": {row['error']}" if row.get("error") else ""),
            soft,
            actionable="check the record layer is reachable, then re-validate",
        )

    custody_interlock.__name__ = ilk_name
    custody_interlock.__qualname__ = ilk_name
    return custody_interlock


__all__ = [
    "CustodyLookup",
    "require_plate_at",
]
