"""Per-equipment-kind :class:`EquipmentClient` subclasses + dispatch.

Each module here defines one subclass of
:class:`lab_skills.EquipmentClient` with hand-written typed methods
for the catalog entries declared in
``lab_skills.skill_catalog.<kind>``. Args schemas are imported from the
catalog (single source of truth); the method bodies are thin wrappers over
:meth:`EquipmentClient.command`.

The :func:`client_for` factory dispatches on ``entry.kind`` to pick the right
subclass; kinds without a registered wrapper (``liquid_handler``,
``plate_reader``, ``plate_stacker``, ``hplc``, ``environmental_sensor``,
``other``) fall back to the plain :class:`EquipmentClient`. Workflow code
that only needs ``status()`` / ``probe()`` / ``health()`` works the same on
both.
"""

from __future__ import annotations

import httpx

from ..client import EquipmentClient
from ..models import EquipmentKind
from ..registry import EquipmentEntry
from .fume_hood import FumeHoodClient
from .plate_sealer import PlateSealerClient
from .press import PressClient
from .robot_arm import RobotArmClient
from .solid_doser import SolidDoserClient

_CLIENT_BY_KIND: dict[EquipmentKind, type[EquipmentClient]] = {
    "plate_sealer": PlateSealerClient,
    "press": PressClient,
    "solid_doser": SolidDoserClient,
    "fume_hood": FumeHoodClient,
    "robot_arm": RobotArmClient,
}


def client_for(entry: EquipmentEntry, http: httpx.AsyncClient) -> EquipmentClient:
    """Return the right :class:`EquipmentClient` subclass for ``entry.kind``.

    Falls back to the plain base class for kinds without a typed wrapper so
    every registered device still gets a usable client (with status helpers).
    """

    cls = _CLIENT_BY_KIND.get(entry.kind, EquipmentClient)
    return cls(entry, http)


__all__ = [
    "FumeHoodClient",
    "PlateSealerClient",
    "PressClient",
    "RobotArmClient",
    "SolidDoserClient",
    "client_for",
]
