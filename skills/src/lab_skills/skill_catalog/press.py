"""Skill catalog entries for ``kind=press``.

Reference device: :mod:`filter_every_well` (Waters PP96 filtration press).
Conforms to STATUS_SPEC v1.1; all control calls go through ``/control/*``
and require an ``X-Claim-Token`` header obtained via ``POST /control/claim``.

Device endpoints
----------------
* ``POST /control/startup``          Bring system to ACTIVE (press up, plate out)
* ``POST /control/stop``             Emergency stop; re-init required
* ``POST /control/press/up``         Move pneumatic press UP
* ``POST /control/press/down``       Move pneumatic press DOWN
* ``POST /control/plate/in``         Retract plate carriage under press
* ``POST /control/plate/out``        Extend plate carriage away from press

``allowed_actions`` mapping
----------------------------
* ``requires_init``  →  ``["init"]``
* ``ready``          →  ``["stop", "press.up", "press.down", "plate.in", "plate.out"]``
* ``busy``           →  ``["stop"]``
* ``dry_run``        →  all actions (simulated)
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .models import SkillDef
from .registry import register


class InitArgs(BaseModel):
    """Body for ``POST /control/startup`` (no parameters)."""


class StopArgs(BaseModel):
    """Body for ``POST /control/stop`` (no parameters)."""


class PressMoveArgs(BaseModel):
    """Body for ``POST /control/press/up`` and ``POST /control/press/down``."""

    hold_time: float = Field(default=0.5, ge=0.0, le=10.0)


class PlateMoveArgs(BaseModel):
    """Body for ``POST /control/plate/in`` and ``POST /control/plate/out``.

    ``smooth=True`` (the device default) ramps the actuator; ``False`` is
    a step move.
    """

    smooth: bool = True


register(
    "press",
    [
        SkillDef(
            name="init",
            kind="press",
            description=(
                "Initialize the press to a known state: press up, plate out, "
                "system ACTIVE. Required after a /control/stop or before any "
                "movement command on a freshly booted device."
            ),
            endpoint="/control/startup",
            args_schema=InitArgs,
            requires_states=["requires_init", "ready", "dry_run"],
            estimated_duration_s=4.0,
        ),
        SkillDef(
            name="stop",
            kind="press",
            description=(
                "Emergency-stop the press. Disables all movement until the "
                "next /control/startup."
            ),
            endpoint="/control/stop",
            args_schema=StopArgs,
            requires_states=["ready", "busy", "degraded", "dry_run"],
            estimated_duration_s=0.5,
        ),
        SkillDef(
            name="press.up",
            kind="press",
            description="Move the pneumatic press to the UP position.",
            endpoint="/control/press/up",
            args_schema=PressMoveArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=2.0,
        ),
        SkillDef(
            name="press.down",
            kind="press",
            description="Move the pneumatic press to the DOWN position.",
            endpoint="/control/press/down",
            args_schema=PressMoveArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=2.0,
        ),
        SkillDef(
            name="plate.in",
            kind="press",
            description="Retract the plate carriage under the press (IN position).",
            endpoint="/control/plate/in",
            args_schema=PlateMoveArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=2.0,
        ),
        SkillDef(
            name="plate.out",
            kind="press",
            description="Extend the plate carriage away from the press (OUT position).",
            endpoint="/control/plate/out",
            args_schema=PlateMoveArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=2.0,
        ),
    ],
)


__all__ = [
    "InitArgs",
    "PlateMoveArgs",
    "PressMoveArgs",
    "StopArgs",
]
