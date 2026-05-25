"""Skill catalog entries for ``kind=shaker``.

Reference device: :mod:`torry_pines_shaker_server` — wraps
``matterlab_shakers.TorreyPinesShaker`` (Torrey Pines Scientific SC20
series) over STATUS_SPEC v1.1. Endpoints and arg ranges mirror
``torry-pines-shaker-server/src/torry_pines_shaker_server/api.py``;
argument ranges match the Pydantic ``Field(ge=, le=)`` constraints
declared on the device.

The recipe v2 ``shake`` step (§3.5 in
``organic-solubility/docs/RECIPE_V2.md``) maps to one
``POST /control/shake/start`` call; the device server — not the
workflow — owns the duration timer.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .models import SkillDef
from .registry import register


class StartupArgs(BaseModel):
    """Body for ``POST /control/startup`` (no parameters)."""


class ShutdownArgs(BaseModel):
    """Body for ``POST /control/shutdown`` (no parameters)."""


class ShakeStartArgs(BaseModel):
    """Body for ``POST /control/shake/start``.

    Per recipe v2 §3.5:

    * ``speed_level`` is the driver's 1-9 orbital-speed enum, not RPM.
      Level 0 is "motor off"; use ``shake.stop`` instead.
    * ``temperature_c`` is the heater setpoint (-20..+110 C).
    * ``duration_s`` is timed by the device; the watchdog stops the
      motor at the end (and on process exit).
    * ``wait_for_temperature`` (default ``False``) makes the device
      wait for the setpoint to be reached before starting the
      ``duration_s`` countdown. Layer-1 refusal: HTTP 412 with
      ``{actual_c, setpoint_c, tolerance_c, retry_after_s}`` when the
      configured timeout is exceeded.
    """

    speed_level: int = Field(ge=1, le=9)
    temperature_c: float = Field(ge=-20.0, le=110.0)
    duration_s: float = Field(gt=0.0)
    wait_for_temperature: bool = False


class ShakeStopArgs(BaseModel):
    """Body for ``POST /control/shake/stop`` (no parameters)."""


class SetTemperatureArgs(BaseModel):
    """Body for ``POST /control/shake/set_temperature``."""

    temperature_c: float = Field(ge=-20.0, le=110.0)


class SetSpeedArgs(BaseModel):
    """Body for ``POST /control/shake/set_speed``."""

    speed_level: int = Field(ge=1, le=9)


register(
    "shaker",
    [
        SkillDef(
            name="startup",
            kind="shaker",
            description="Open the serial port and verify the shaker is reachable.",
            endpoint="/control/startup",
            args_schema=StartupArgs,
            requires_states=["requires_init", "ready", "dry_run"],
            estimated_duration_s=2.0,
        ),
        SkillDef(
            name="shutdown",
            kind="shaker",
            description=(
                "Stop the motor and close the serial port. The lifespan "
                "watchdog also fires this on process exit."
            ),
            endpoint="/control/shutdown",
            args_schema=ShutdownArgs,
            requires_states=["ready", "busy", "degraded", "error", "dry_run"],
            estimated_duration_s=1.0,
        ),
        SkillDef(
            name="shake.start",
            kind="shaker",
            description=(
                "Run one shake cycle. The device owns the duration "
                "timer; on process exit the watchdog stops the motor."
            ),
            endpoint="/control/shake/start",
            args_schema=ShakeStartArgs,
            requires_states=["ready", "dry_run"],
            # The device returns HTTP 412 when wait_for_temperature=True
            # and the setpoint is not reached within the configured
            # timeout; the SDK surfaces that via PreconditionNotMet (v0.4).
            estimated_duration_s=None,  # caller-supplied via duration_s
        ),
        SkillDef(
            name="shake.stop",
            kind="shaker",
            description=(
                "Halt the current shake cycle. Idempotent; safe to "
                "call when no cycle is running."
            ),
            endpoint="/control/shake/stop",
            args_schema=ShakeStopArgs,
            requires_states=["busy", "ready", "dry_run"],
            estimated_duration_s=1.0,
        ),
        SkillDef(
            name="shake.set_temperature",
            kind="shaker",
            description="Set the heater setpoint without engaging the motor.",
            endpoint="/control/shake/set_temperature",
            args_schema=SetTemperatureArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=0.5,
        ),
        SkillDef(
            name="shake.set_speed",
            kind="shaker",
            description=(
                "Set the orbital speed level (1-9). Engages the motor "
                "if currently idle; use shake.stop to halt."
            ),
            endpoint="/control/shake/set_speed",
            args_schema=SetSpeedArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=0.5,
        ),
    ],
)


__all__ = [
    "SetSpeedArgs",
    "SetTemperatureArgs",
    "ShakeStartArgs",
    "ShakeStopArgs",
    "ShutdownArgs",
    "StartupArgs",
]
