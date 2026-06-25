"""Authorization seam: central account → device role.

This is the **single** place that resolves a user/principal to the role a device
enforces (``user`` / ``automation`` / ``service`` — capability-named; see the
device's ``control/roster.py``). Edge wiring (the ``/equipment/{key}/roster``
projection, ``api/app/control.py``) calls only :func:`effective_device_role`,
never the role strings directly, so the flat-today / hierarchy-later split lives
here alone.

Note the device roles are named by *capability* and are deliberately decoupled
from the central *org* roles (``user`` / ``admin``): the equipment binding (admin
here, user there) belongs to the grant scope, not the role label. ``admin`` is an
org privilege that maps to the ``service`` capability on a device.

**Today (interim flat):** every grant is global — a human's ``users.role`` *is*
their effective role everywhere, and an automation account is the platform/robot
principal. ``equipment_key`` is already in the signature (and the roster
endpoint is already keyed by it) but is not yet consulted.

**Later (per AUTH_SERVICE_DESIGN.md §3):** when ``platforms`` / ``equipment`` /
``authorizations`` land, only this function changes — it will resolve the
*highest applicable* grant for ``equipment_key`` (global > platform > equipment)
instead of reading the flat ``users.role``. Callers stay identical.

The mapping (doc §"Role model & resolution"):

* automation account (robot/platform principal) → ``automation`` (submit + ``workflow.*``)
* human ``admin``                               → ``service``    (submit + ``service.*``)
* human ``user``                                → ``user``       (submit only)
"""

from __future__ import annotations

from typing import Literal

from .db import User

# Mirrors agilent_hplcms_server/control/roster.py :: Role.
DeviceRole = Literal["user", "automation", "service"]


def effective_device_role(user: User, equipment_key: str) -> DeviceRole:
    """Resolve the device role this account holds on ``equipment_key``.

    ``equipment_key`` is reserved for the future per-equipment grant hierarchy;
    today resolution is flat/global (see module docstring).
    """
    if user.is_automation:
        return "automation"
    if user.role == "admin":
        return "service"
    return "user"


__all__ = ["DeviceRole", "effective_device_role"]
