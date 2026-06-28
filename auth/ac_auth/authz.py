"""Authorization seam: central account → device role.

This is the **single** place that resolves a user/principal to the role a device
enforces (``user`` / ``automation`` / ``service`` — capability-named; see the
device's ``control/roster.py``). Edge wiring (the ``/equipment/{key}/roster``
projection, ``/authz/check``, ``api/app/control.py``) calls only
:func:`effective_device_role` / :func:`effective_central_role`, never the role
strings directly, so the resolution rule lives here alone.

Note the device roles are named by *capability* and are deliberately decoupled
from the central *org* roles (``operator`` / ``admin``): ``admin`` is an org
privilege that maps to the ``service`` capability on a device.

**Phase 1 (per-scope grants).** A principal's effective central role on an
equipment is the **highest applicable** of:

* the flat global role on the row (``user``/``operator`` or ``admin``), treated
  as a ``global`` grant, **plus**
* any ``grants`` entry that applies to this equipment — ``global`` (always),
  ``platform`` (when the equipment belongs to that platform, per
  `platforms.yaml` membership), or ``equipment`` (exact key match).

``operator < admin``. With no grants and no membership this reduces exactly to
the old flat behavior, so the change is backward-compatible. The mapping:

* automation account (robot/platform principal) → ``automation``
* effective central ``admin``                    → ``service``
* effective central ``operator``                 → ``user``
"""

from __future__ import annotations

from typing import Iterable, Literal, Mapping, Optional

from .db import User

# Mirrors agilent_hplcms_server/control/roster.py :: Role.
DeviceRole = Literal["user", "automation", "service"]
CentralRole = Literal["operator", "admin"]

# Higher wins.
_RANK: dict[str, int] = {"operator": 1, "admin": 2}


def _norm_central(role: str) -> CentralRole:
    # the flat row stores the legacy "user"; treat it as "operator"
    return "admin" if role == "admin" else "operator"


def _grant_applies(grant, equipment_key: str, membership: Mapping[str, Iterable[str]]) -> bool:
    """Does a single grant (duck-typed: .scope/.id/.role) apply to this equipment?"""
    scope = getattr(grant, "scope", None)
    if scope == "global":
        return True
    if scope == "platform":
        return grant.id in set(membership.get(equipment_key, ()))
    if scope == "equipment":
        return grant.id == equipment_key
    return False


def effective_central_role(
    user: User,
    equipment_key: str,
    membership: Optional[Mapping[str, Iterable[str]]] = None,
) -> CentralRole:
    """Highest central role (``operator``/``admin``) this human holds on
    ``equipment_key``, across the flat global role + applicable grants."""
    membership = membership or {}
    best = _norm_central(user.role)  # flat role = an implicit global grant
    for grant in getattr(user, "grants", ()) or ():
        if _RANK.get(getattr(grant, "role", ""), 0) > _RANK[best] and _grant_applies(
            grant, equipment_key, membership
        ):
            best = grant.role  # type: ignore[assignment]
    return best


def effective_device_role(
    user: User,
    equipment_key: str,
    membership: Optional[Mapping[str, Iterable[str]]] = None,
) -> DeviceRole:
    """Resolve the device role this account holds on ``equipment_key``.

    ``membership`` is the ``equipment_key -> {platform_id}`` map (from
    `platforms.yaml`, via :func:`ac_auth.platforms.load_membership`); omit it and
    platform-scoped grants simply don't resolve (global/equipment still do).
    """
    if user.is_automation:
        return "automation"
    return "service" if effective_central_role(user, equipment_key, membership) == "admin" else "user"


__all__ = ["DeviceRole", "CentralRole", "effective_central_role", "effective_device_role"]
