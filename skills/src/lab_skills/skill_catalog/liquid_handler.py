"""Skill catalog entries for ``kind=liquid_handler``.

Reference device: ``opentrons-server`` (the OT-2 gateway on
``sdl2-pc-03-cytation:8020``). The protocol-execution actions
(setup / home / aspirate / dispense / pick-up-tip / drop-tip /
move-labware / pause / resume / reconcile) are reachable today via
``/control/*`` on the device but aren't catalogued yet — they take
typed protocol arguments the SDK has no schemas for. They'll land
once the catalog grows protocol-aware Pydantic models.

What this module *does* register is the deck-light toggle: a
convenience control with no equipment-state preconditions (the
device advertises ``lights.set`` in ``allowed_actions`` whenever the
robot is reachable). It is intentionally *not* behind
``CONTROL_PASSWORD`` per ``web/src/lib/tile-policy.ts`` — same class
as camera PTZ and "light" outlets on a power strip.
"""

from __future__ import annotations

from pydantic import BaseModel

from .models import SkillDef
from .registry import register


class LightsSetArgs(BaseModel):
    """Body for ``POST /control/lights``.

    Mirrors the upstream Opentrons HTTP API (``POST /robot/lights``).
    """

    on: bool


register(
    "liquid_handler",
    [
        SkillDef(
            name="lights.set",
            kind="liquid_handler",
            description="Turn the OT-2 deck lights on or off. Convenience control.",
            endpoint="/control/lights",
            args_schema=LightsSetArgs,
            # Empty: the device exposes lights.set in allowed_actions
            # regardless of equipment_status. Don't gate on the SDK side
            # either — the operator might want lights on while the robot
            # is still in requires_init.
            requires_states=[],
            estimated_duration_s=0.2,
        ),
    ],
)


__all__ = ["LightsSetArgs"]
