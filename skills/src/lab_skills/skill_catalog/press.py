"""Skill catalog entries for ``kind=press``.

Reference device: :mod:`filter_every_well` (Waters PP96 filtration press).
This device has not yet migrated to STATUS_SPEC v1.x; the catalog records its
*current* legacy endpoints exactly as they ship today
(``filter-every-well/src/filter_every_well/api.py``):

* ``POST /init``                 - bring system to ACTIVE (press up, plate out)
* ``POST /stop``                 - emergency stop, requires re-init
* ``POST /press/up?hold_time=``  - move pneumatic press up
* ``POST /press/down?hold_time=``- move pneumatic press down
* ``POST /plate/in?smooth=``     - retract plate carriage in
* ``POST /plate/out?smooth=``    - extend plate carriage out

When the device migrates to STATUS_SPEC v1.x and gains spec-conformant
``/control/*`` endpoints, only this catalog file changes; the typed client
in v0.3 keeps the same Python signatures.

Note on ``requires_states``: the legacy device emits ``equipment_status``
values like ``"ok"`` / ``"ready"`` / ``"stopped"`` / ``"dry-run"`` rather than
the spec's enum. We use ``["ready", "dry_run"]`` here as the SDK's intent
("device idle and accepting commands") and rely on graceful degradation:
when the live status fails the precondition check, the workflow gets a
typed ``Skill.available=False`` with ``reason="device requires init"`` rather
than a hung command.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .models import SkillDef
from .registry import register


class InitArgs(BaseModel):
    """Body for ``POST /init`` (no parameters)."""


class StopArgs(BaseModel):
    """Body for ``POST /stop`` (no parameters)."""


class PressMoveArgs(BaseModel):
    """Body for ``POST /press/up`` and ``POST /press/down``.

    The legacy device accepts ``hold_time`` as a query parameter; the typed
    wrapper in v0.3 will pass it that way. Range matches the device's default.
    """

    hold_time: float = Field(default=0.5, ge=0.0, le=10.0)


class PlateMoveArgs(BaseModel):
    """Body for ``POST /plate/in`` and ``POST /plate/out``.

    ``smooth=True`` (the device's default) ramps the actuator; ``False`` is
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
                "system ACTIVE. Required after a /stop or before any movement "
                "command on a freshly booted device."
            ),
            endpoint="/init",
            args_schema=InitArgs,
            requires_states=["requires_init", "ready", "dry_run"],
            estimated_duration_s=4.0,
        ),
        SkillDef(
            name="stop",
            kind="press",
            description=(
                "Emergency-stop the press. Disables all movement until the "
                "next /init."
            ),
            endpoint="/stop",
            args_schema=StopArgs,
            requires_states=["ready", "busy", "degraded", "dry_run"],
            estimated_duration_s=0.5,
        ),
        SkillDef(
            name="press.up",
            kind="press",
            description="Move the pneumatic press to the UP position.",
            endpoint="/press/up",
            args_schema=PressMoveArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=2.0,
        ),
        SkillDef(
            name="press.down",
            kind="press",
            description="Move the pneumatic press to the DOWN position.",
            endpoint="/press/down",
            args_schema=PressMoveArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=2.0,
        ),
        SkillDef(
            name="plate.in",
            kind="press",
            description="Retract the plate carriage under the press (IN position).",
            endpoint="/plate/in",
            args_schema=PlateMoveArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=2.0,
        ),
        SkillDef(
            name="plate.out",
            kind="press",
            description="Extend the plate carriage away from the press (OUT position).",
            endpoint="/plate/out",
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
