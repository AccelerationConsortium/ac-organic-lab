"""Typed wrapper for ``kind=robot_arm`` devices.

Reference device: ``xarm-translocation``. Its
``do_not_call_connect: true`` registry flag suppresses automatic connection;
explicit control remains available through validated plans when the device's
live ``allowed_actions`` permits it.

This subclass exists so :func:`client_for` returns a uniform per-kind type
even when there are no typed control convenience methods. ``status()`` /
``probe()`` / ``health()`` and the generic command surface are inherited from
:class:`EquipmentClient`; plan execution dispatches the typed SkillDefs in
``lab_skills.skill_catalog.robot_arm`` through that generic surface.
"""

from __future__ import annotations

from ..client import EquipmentClient


class RobotArmClient(EquipmentClient):
    """Typed client shell for the xArm role.

    Dedicated ``move()`` / ``home()`` / ``gripper_*`` convenience methods
    have not landed yet. Workflows should execute cataloged skills through a
    validated :class:`Plan`, not issue ad-hoc commands.
    """


__all__ = ["RobotArmClient"]
