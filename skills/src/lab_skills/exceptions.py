"""SDK-level exceptions raised from ``Lab`` / ``LabSession`` / ``EquipmentClient``.

Workflow code matches on these typed exceptions rather than parsing strings or
inspecting HTTP status codes.

* v0.1 shipped the read-side exceptions: :class:`LabError`,
  :class:`RegistryError`, :class:`EquipmentUnreachable`, :class:`WaitTimeout`,
  :class:`EquipmentInMaintenance`.
* v0.2 adds the control-side exceptions raised from
  :meth:`EquipmentClient.command`: :class:`EquipmentBusy` (HTTP 409),
  :class:`RequiresInit` (HTTP 400 with a "not initialized" / "not connected"
  message), :class:`BadRequest` (other 4xx validation failures), and
  :class:`Degraded` (raised by skill availability and waiting helpers when a
  device reports ``equipment_status: degraded``).
* v0.3 adds the claim-protocol exception :class:`ClaimRejected` (raised by
  :class:`lab_skills.ClaimManager` on HTTP 409 / 423 from
  ``/control/claim``).
"""

from __future__ import annotations

from datetime import date
from typing import Any


class LabError(Exception):
    """Base class for all SDK exceptions raised by ``lab_skills``."""


class RegistryError(LabError):
    """Raised when ``equipment.yaml`` is missing, unparsable, or refers to an
    equipment_id that does not exist.
    """


class EquipmentUnreachable(LabError):
    """Raised when the SDK cannot reach a configured device's REST endpoint
    after retries (transport failures, 5xx, parse errors).
    """

    def __init__(self, equipment_id: str, message: str) -> None:
        super().__init__(f"{equipment_id}: {message}")
        self.equipment_id = equipment_id
        self.message = message


class WaitTimeout(LabError):
    """Raised by ``wait_until_state`` when the requested state is not reached
    within the allotted timeout.
    """

    def __init__(
        self,
        equipment_id: str,
        expected_state: str,
        last_state: str | None,
        timeout_seconds: float,
    ) -> None:
        super().__init__(
            f"{equipment_id} did not reach state {expected_state!r} within "
            f"{timeout_seconds:.1f}s (last observed: {last_state!r})"
        )
        self.equipment_id = equipment_id
        self.expected_state = expected_state
        self.last_state = last_state
        self.timeout_seconds = timeout_seconds


class EquipmentInMaintenance(LabError):
    """Raised by ``Lab.get(equipment_id)`` when the entry is disabled or has a
    non-null ``maintenance`` block. Workflow code should catch this and either
    skip the device or surface a clear message; the dashboard reads the same
    metadata to render its tile in maintenance state.
    """

    def __init__(
        self,
        equipment_id: str,
        reason: str | None,
        until: date | None = None,
        contact: str | None = None,
    ) -> None:
        suffix = f" until {until.isoformat()}" if until is not None else ""
        msg = (
            f"{equipment_id} is in maintenance"
            + (f" ({reason})" if reason else "")
            + suffix
        )
        super().__init__(msg)
        self.equipment_id = equipment_id
        self.reason = reason
        self.until = until
        self.contact = contact


class _CommandError(LabError):
    """Common base for control-side exceptions raised by ``EquipmentClient.command``.

    Carries the equipment id, the HTTP status code returned by the device, and
    the device's detail message. Workflow code typically catches the more
    specific subclasses (``EquipmentBusy``, ``RequiresInit``, ``BadRequest``)
    rather than this base.
    """

    def __init__(
        self,
        equipment_id: str,
        message: str,
        *,
        http_status: int | None = None,
    ) -> None:
        super().__init__(f"{equipment_id}: {message}")
        self.equipment_id = equipment_id
        self.message = message
        self.http_status = http_status


class EquipmentBusy(_CommandError):
    """Raised when a device rejects a command because it is in the wrong state.

    Mapped from HTTP 409 on ``/control/*``. Common causes: the device is
    already running the cycle, a previous cycle has not finished, or another
    operator/workflow currently holds it.
    """


class RequiresInit(_CommandError):
    """Raised when a device rejects a command because hardware is not initialized.

    Mapped from HTTP 400 / 422 with a body indicating the device is not
    connected / not initialized / awaiting startup. The remedy is to call the
    device's ``/control/startup`` (or equivalent) before retrying.
    """


class BadRequest(_CommandError):
    """Raised when a device rejects a command body as invalid (other 4xx).

    Mapped from HTTP 400 / 422 / other 4xx that does not match the
    "not initialized" pattern. The device's detail message is preserved on
    ``message`` for the operator to read.
    """


class ClaimRejected(LabError):
    """Raised when ``POST /control/claim`` is refused by a v1.1 device.

    Mapped from HTTP 409 / 423 on ``/control/claim``. The device was reachable
    but is currently held by another session, or otherwise will not issue a
    claim right now. ``retry_after_s`` (parsed from the JSON body or the
    ``Retry-After`` HTTP header) is the device's hint for how long to wait
    before retrying. ``claimed_by`` identifies the current holder when the
    device chose to publish it.

    Note: HTTP 404 / 405 from ``/control/claim`` is *not* a rejection. The
    SDK treats it as "this device is v1.0 and has no claim semantics" and
    silently degrades :class:`ClaimManager` to a no-op. See the v1.1
    section of ``docs/STATUS_SPEC.md``.
    """

    def __init__(
        self,
        equipment_id: str,
        message: str,
        *,
        http_status: int | None = None,
        retry_after_s: float | None = None,
        claimed_by: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{equipment_id}: {message}")
        self.equipment_id = equipment_id
        self.message = message
        self.http_status = http_status
        self.retry_after_s = retry_after_s
        self.claimed_by = claimed_by


class Degraded(LabError):
    """Raised by waiting / skill helpers when a device reports a degraded state.

    A degraded device is reachable and not in maintenance, but a sub-component
    is unhealthy (per :data:`EquipmentState`). Callers can choose to retry, to
    fall back to an alternate role, or to fail fast.
    """

    def __init__(
        self,
        equipment_id: str,
        message: str,
    ) -> None:
        super().__init__(f"{equipment_id} is degraded: {message}")
        self.equipment_id = equipment_id
        self.message = message


__all__ = [
    "BadRequest",
    "ClaimRejected",
    "Degraded",
    "EquipmentBusy",
    "EquipmentInMaintenance",
    "EquipmentUnreachable",
    "LabError",
    "RegistryError",
    "RequiresInit",
    "WaitTimeout",
]
