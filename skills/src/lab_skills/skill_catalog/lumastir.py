"""Lumastir capabilities, registered only for the lumastir equipment ID."""

from pydantic import BaseModel, ConfigDict, Field
from .models import SkillDef


class MotorSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(ge=0, le=2, strict=True)
    speed: float = Field(ge=0, le=100, strict=True, allow_inf_nan=False)


class LedSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(ge=0, le=2, strict=True)
    brightness: float = Field(ge=0, le=100, strict=True, allow_inf_nan=False)


LUMASTIR_SKILLS = [
    SkillDef(
        name="lumastir.motor.set",
        kind="other",
        description="Set one Lumastir motor's PWM percentage; zero stops that motor. Requires a live claim; release stops all outputs.",
        endpoint="/control/motor/set",
        args_schema=MotorSet,
        requires_states=["ready", "busy"],
    ),
    SkillDef(
        name="lumastir.led.set",
        kind="other",
        description="Set one Lumastir LED's PWM percentage; zero turns it off. Requires a live claim; release stops all outputs.",
        endpoint="/control/led/set",
        args_schema=LedSet,
        requires_states=["ready", "busy"],
    ),
]
