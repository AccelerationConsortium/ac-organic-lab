"""Typed wrapper for ``kind=robot_arm`` devices.

Reference device: ``xarm-translocation``. Read-only in v0.2 because
``equipment.yaml`` sets ``do_not_call_connect: true`` on the xArm entry; the
SDK is allowed to query status but not to issue control commands.

This subclass exists so :func:`client_for` returns a uniform per-kind type
even when there are no typed control methods. ``status()`` / ``probe()`` /
``health()`` are inherited from :class:`EquipmentClient`. When the xArm
repo migrates to STATUS_SPEC v1.x and ``do_not_call_connect`` is removed,
populate this module with the proper typed methods (move, gripper, home,
etc.) - the catalog file in ``lab_skills.skill_catalog.robot_arm``
will declare the SkillDefs first; method signatures here mirror them.
"""

from __future__ import annotations

from ..client import EquipmentClient


class RobotArmClient(EquipmentClient):
    """Read-only client for the xArm role.

    Deliberately exposes no ``move()`` / ``home()`` / ``gripper_*`` methods
    in v0.2. Use :meth:`EquipmentClient.command` directly if you need to
    issue an ad-hoc request and have read ``equipment.yaml``'s
    ``do_not_call_connect`` flag.
    """


__all__ = ["RobotArmClient"]
