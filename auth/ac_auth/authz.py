"""Authorization seam: central account → device role.

This is the **single** place that resolves a user/principal to the role a device
enforces (``hplcms_user`` / ``hte`` / ``hplcms_admin`` — see the device's
``control/roster.py``). Edge wiring (the ``/equipment/{key}/roster`` projection,
``api/app/control.py``) calls only :func:`effective_device_role`, never the role
strings directly, so the flat-today / hierarchy-later split lives here alone.

**Today (interim flat):** every grant is global — a human's ``users.role`` *is*
their effective role everywhere, and a service account is the platform/robot
principal. ``equipment_key`` is already in the signature (and the roster
endpoint is already keyed by it) but is not yet consulted.

**Later (per AUTH_SERVICE_DESIGN.md §3):** when ``platforms`` / ``equipment`` /
``authorizations`` land, only this function changes — it will resolve the
*highest applicable* grant for ``equipment_key`` (global > platform > equipment)
instead of reading the flat ``users.role``. Callers stay identical.

The mapping (doc §"Role model & resolution"):

* service account (robot/platform principal) → ``hte``  (submit + ``workflow.*``)
* human ``admin``                            → ``hplcms_admin`` (submit + ``service.*``)
* human ``user``                             → ``hplcms_user``  (submit only)
"""

from __future__ import annotations

from typing import Literal

from .db import User

# Mirrors agilent_hplcms_server/control/roster.py :: Role.
DeviceRole = Literal["hplcms_user", "hte", "hplcms_admin"]


def effective_device_role(user: User, equipment_key: str) -> DeviceRole:
    """Resolve the device role this account holds on ``equipment_key``.

    ``equipment_key`` is reserved for the future per-equipment grant hierarchy;
    today resolution is flat/global (see module docstring).
    """
    if user.is_service_account:
        return "hte"
    if user.role == "admin":
        return "hplcms_admin"
    return "hplcms_user"


__all__ = ["DeviceRole", "effective_device_role"]
