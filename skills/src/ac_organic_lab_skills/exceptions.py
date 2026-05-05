"""SDK-level exceptions raised from ``Lab`` / ``LabSession`` / ``EquipmentClient``.

Workflow code matches on these typed exceptions rather than parsing strings or
inspecting HTTP status codes. v0.1 ships the four read-side exceptions below;
v0.2 will add control-side exceptions (``EquipmentBusy``, ``RequiresInit``,
``Degraded``) and v0.3 will add claim-related ones (``ClaimRejected``,
``ClaimRequired``).
"""

from __future__ import annotations

from datetime import date


class LabError(Exception):
    """Base class for all SDK exceptions raised by ``ac_organic_lab_skills``."""


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


__all__ = [
    "EquipmentInMaintenance",
    "EquipmentUnreachable",
    "LabError",
    "RegistryError",
    "WaitTimeout",
]
