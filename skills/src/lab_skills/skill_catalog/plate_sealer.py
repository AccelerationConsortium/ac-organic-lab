"""Skill catalog entries for ``kind=plate_sealer``.

Skills map 1:1 to the ``/control/*`` endpoints defined by STATUS_SPEC v1.x for
plate sealers (see ``docs/STATUS_SPEC.md``). Argument ranges mirror the
Pydantic ``Field(ge=, le=)`` constraints declared on the device side; tightening
either side is a coordinated change.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .models import SkillDef
from .registry import register


class StartupArgs(BaseModel):
    """Body for ``POST /control/startup``.

    ``profile`` is the PlateLoc profile name configured in the device's
    Diagnostics dialog. ``None`` lets the device pick its default.
    """

    profile: str | None = None


class ShutdownArgs(BaseModel):
    """Body for ``POST /control/shutdown`` (no parameters)."""


class SealStartArgs(BaseModel):
    """Body for ``POST /control/seal/start``.

    Both fields are optional: omitting one keeps the device's current
    setpoint. Specifying both is the typical workflow path
    (``await sealer.seal_start(temperature_c=170, seconds=3.0)``).
    """

    temperature_c: int | None = Field(default=None, ge=20, le=235)
    seconds: float | None = Field(default=None, ge=0.5, le=12.0)


class SealStopArgs(BaseModel):
    """Body for ``POST /control/seal/stop`` (no parameters)."""


class SetSealingTemperatureArgs(BaseModel):
    """Body for ``POST /control/seal/temperature``."""

    temperature_c: int = Field(ge=20, le=235)


class SetSealingTimeArgs(BaseModel):
    """Body for ``POST /control/seal/time``."""

    seconds: float = Field(ge=0.5, le=12.0)


class StageInArgs(BaseModel):
    """Body for ``POST /control/stage/in`` (no parameters)."""


class StageOutArgs(BaseModel):
    """Body for ``POST /control/stage/out`` (no parameters)."""


register(
    "plate_sealer",
    [
        SkillDef(
            name="startup",
            kind="plate_sealer",
            description="Connect to the PlateLoc and load a sealing profile.",
            endpoint="/control/startup",
            args_schema=StartupArgs,
            requires_states=["requires_init", "ready", "dry_run"],
            estimated_duration_s=5.0,
        ),
        SkillDef(
            name="shutdown",
            kind="plate_sealer",
            description="Disconnect from the PlateLoc.",
            endpoint="/control/shutdown",
            args_schema=ShutdownArgs,
            requires_states=["ready", "busy", "degraded", "error", "dry_run"],
            estimated_duration_s=1.0,
        ),
        SkillDef(
            name="seal.start",
            kind="plate_sealer",
            description=(
                "Run a seal cycle at the given temperature and duration. "
                "Optionally updates the temperature/time setpoints first."
            ),
            endpoint="/control/seal/start",
            args_schema=SealStartArgs,
            requires_states=["ready", "dry_run"],
            # Two preconditions, both enforced by plateloc v1.3+ at layer 1
            # via HTTP 412 with distinct body shapes:
            #
            #   - heater stable: |actual − setpoint| ≤ tolerance and
            #     PID settled (v1.2+). Refusal body has
            #     actual_c / setpoint_c / tolerance_c / retry_after_s.
            #   - stage in: plate carriage retracted under the press
            #     (v1.3+). Refusal body has stage_state + required.
            #
            # The dashboard tile and lab.skills() honor both hints so
            # workflow callers see available=False with a useful reason
            # before round-tripping the call.
            requires_components={"heater": "stable", "stage": "in"},
            estimated_duration_s=8.0,
        ),
        SkillDef(
            name="seal.stop",
            kind="plate_sealer",
            description="Abort the current seal cycle.",
            endpoint="/control/seal/stop",
            args_schema=SealStopArgs,
            requires_states=["busy", "dry_run"],
            estimated_duration_s=1.0,
        ),
        SkillDef(
            name="seal.set_temperature",
            kind="plate_sealer",
            description="Set the sealing temperature setpoint without starting a cycle.",
            endpoint="/control/seal/temperature",
            args_schema=SetSealingTemperatureArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=0.5,
        ),
        SkillDef(
            name="seal.set_time",
            kind="plate_sealer",
            description="Set the seal-cycle duration setpoint without starting a cycle.",
            endpoint="/control/seal/time",
            args_schema=SetSealingTimeArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=0.5,
        ),
        SkillDef(
            name="stage.in",
            kind="plate_sealer",
            description="Move the plate stage into the sealing chamber.",
            endpoint="/control/stage/in",
            args_schema=StageInArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=3.0,
        ),
        SkillDef(
            name="stage.out",
            kind="plate_sealer",
            description="Move the plate stage out of the sealing chamber.",
            endpoint="/control/stage/out",
            args_schema=StageOutArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=3.0,
        ),
    ],
)


__all__ = [
    "SealStartArgs",
    "SealStopArgs",
    "SetSealingTemperatureArgs",
    "SetSealingTimeArgs",
    "ShutdownArgs",
    "StageInArgs",
    "StageOutArgs",
    "StartupArgs",
]
