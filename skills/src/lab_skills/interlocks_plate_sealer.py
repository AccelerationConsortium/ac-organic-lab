"""Optional interlocks for ``kind=plate_sealer`` devices.

Not registered by default. Workflows that want plate-sealer-aware
plan validation should opt in explicitly::

    from lab_skills import register_interlock
    from lab_skills.interlocks_plate_sealer import (
        plate_sealer_heater_must_be_stable_for_seal_start,
    )

    register_interlock(plate_sealer_heater_must_be_stable_for_seal_start)

This module is **separate from** :mod:`lab_skills.interlocks` (which
holds the always-on built-ins) because (a) project-specific safety
rules are opt-in by convention, and (b) the rule here makes a live
HTTP read to the device, which the built-ins deliberately do not.

The v0.3 interlock protocol is synchronous: an interlock function is
called inside :func:`validate_plan` with ``(plan, step, session)`` and
returns a list of :class:`~lab_skills.interlocks.Violation`. To read
live device state from a sync function we use a blocking
:class:`httpx.Client` with a short timeout; the call is kept tight so
``validate_plan`` stays close to its "offline" promise. v0.4 will
introduce async interlocks (per ``docs/INTERLOCKS.md``) and this
module's helper can collapse into ``await client.status()``.

If the live read fails (timeout, connection error, malformed body),
this interlock emits a ``severity="warning"`` finding rather than a
blocking ``"error"`` -- absence of evidence isn't evidence of an
unsafe state, but the operator should know we couldn't verify.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from .interlocks import Violation

if TYPE_CHECKING:  # avoid import cycle.
    from .plan import Plan, Step
    from .session import LabSession


# Short timeout so a stuck device doesn't stall the whole plan
# validation. The aggregator polls /status every 2-3 s under normal
# conditions, so 2 s is generous; tightening it to 1 s in a notebook
# is reasonable.
_STATUS_FETCH_TIMEOUT_S: float = 2.0


def _fetch_status_sync(base_url: str, status_path: str) -> dict[str, Any] | None:
    """Synchronously GET <base_url><status_path>. Returns None on any failure."""

    url = f"{base_url.rstrip('/')}{status_path}"
    try:
        with httpx.Client(timeout=_STATUS_FETCH_TIMEOUT_S, trust_env=False) as client:
            response = client.get(url)
    except Exception:  # noqa: BLE001 - any transport failure is "unknown"
        return None
    if response.status_code >= 400:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    return body


def plate_sealer_heater_must_be_stable_for_seal_start(
    plan: "Plan", step: "Step", session: "LabSession"
) -> list[Violation] | None:
    """Block ``seal.start`` steps on plate_sealer devices when the heater
    isn't reporting ``components.heater.state == "stable"``.

    Reads ``components.heater`` from the device's live ``/status``.
    Devices that don't publish ``components.heater`` (older v1.x
    deployments predating the heater component) are treated as "no evidence
    available" - the interlock emits no Violation, deferring to other
    interlocks / Layer 3 device-side guards.

    Failure modes:

    * Device unreachable / malformed status -> ``warning`` finding,
      "could not verify heater state". Non-blocking.
    * ``heater.state in {"heating", "cooling"}`` -> ``error`` finding,
      "heater not at setpoint". Blocking.
    * ``heater.state in {"unknown", "disconnected"}`` -> ``warning``
      finding. Non-blocking but visible.
    * ``heater.state == "stable"`` or heater field absent -> no
      finding.
    """

    if step.skill != "seal.start":
        return None

    equipment_id = session.binding.get(step.role)
    if equipment_id is None:
        return None  # validate_plan reports unresolved roles elsewhere.

    entry = session.registry.by_id(equipment_id)
    if entry is None:
        return None
    if entry.kind != "plate_sealer":
        return None
    if not entry.base_url:
        return None

    status_path = entry.status_path or "/status"
    body = _fetch_status_sync(entry.base_url, status_path)

    if body is None:
        return [
            Violation(
                step_id=step.id,
                step_index=step.index,
                code="heater_state_unknown",
                message=(
                    f"could not read /status on {equipment_id!r}; "
                    f"heater stability for seal.start not verified"
                ),
                severity="warning",
                actionable=(
                    f"check {equipment_id!r} is reachable; aggregator "
                    f"poll cadence and device-side errors apply"
                ),
                interlock_name="plate_sealer_heater_must_be_stable_for_seal_start",
            )
        ]

    components = body.get("components") or {}
    heater = components.get("heater") if isinstance(components, dict) else None
    if not isinstance(heater, dict):
        # Device hasn't published heater yet; nothing to assert.
        return None

    state = heater.get("state")
    if state == "stable":
        return None

    if state in {"heating", "cooling"}:
        setpoint = (
            (body.get("metrics") or {}).get("setpoint_temperature") or {}
        ).get("value")
        actual = (
            (body.get("metrics") or {}).get("actual_temperature") or {}
        ).get("value")
        msg = heater.get("message")
        if not isinstance(msg, str) or not msg:
            msg = (
                f"heater is {state} ({actual} -> {setpoint} C); "
                "seal.start would seal at the wrong temperature"
            )
        return [
            Violation(
                step_id=step.id,
                step_index=step.index,
                code="heater_not_stable",
                message=msg,
                severity="error",
                actionable=(
                    "wait for components.heater.state to become 'stable' "
                    "(actual within tolerance of setpoint) before retrying"
                ),
                interlock_name="plate_sealer_heater_must_be_stable_for_seal_start",
            )
        ]

    if state in {"unknown", "disconnected"}:
        msg = heater.get("message") or f"heater state is {state!r}"
        return [
            Violation(
                step_id=step.id,
                step_index=step.index,
                code="heater_state_unverified",
                message=f"plate_sealer heater state is {state!r}: {msg}",
                severity="warning",
                actionable=(
                    "ensure /control/startup has been called and the COM "
                    "bridge is healthy; check device last_error"
                ),
                interlock_name="plate_sealer_heater_must_be_stable_for_seal_start",
            )
        ]

    # Unknown state value (device publishes something we don't recognise).
    return [
        Violation(
            step_id=step.id,
            step_index=step.index,
            code="heater_state_unrecognised",
            message=f"plate_sealer heater state {state!r} is not in the known enum",
            severity="warning",
            interlock_name="plate_sealer_heater_must_be_stable_for_seal_start",
        )
    ]


__all__ = [
    "plate_sealer_heater_must_be_stable_for_seal_start",
]
