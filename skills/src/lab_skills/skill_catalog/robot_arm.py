"""Skill catalog entries for ``kind=robot_arm``.

Reference device: :mod:`xarm_translocation` (UFactory xArm5). The
``equipment.yaml`` entry for this device sets ``do_not_call_connect: true``
because its REST surface is not yet stabilised for SDK-driven control. v0.2
therefore registers an EMPTY :class:`SkillDef` list for this kind: status
helpers (``status``, ``probe``, ``health``) inherited from
:class:`EquipmentClient` work normally, but ``await session.skills()`` will
not surface any control capabilities for the xArm role.

When the xArm repo is migrated to STATUS_SPEC v1.x and the
``do_not_call_connect`` flag is removed from ``equipment.yaml``, populate
this module with the proper :class:`SkillDef` list (move, gripper, home,
etc.) and the catalog will surface them automatically.
"""

from __future__ import annotations

from .registry import register

register("robot_arm", [])


__all__: list[str] = []
