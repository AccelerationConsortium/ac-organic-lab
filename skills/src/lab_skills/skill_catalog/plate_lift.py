"""Balance lift (flex-balance-lift) capabilities, selected only for ``balance_lift``.

The device is a P8 linear stepper on a Tic T825 that raises a microplate
holder off a balance pan and back. Its service (``balance_lift.motion`` in
the flex-balance-lift repo) is STATUS_SPEC v1.2 with hard claim enforcement
and one precondition function behind ``allowed_actions`` and its 412
refusals, so ``Skill.name`` here equals the device's action names exactly.

Never registered under the generic ``other`` kind: ``registry.skills_for``
returns this list for ``equipment_id == "balance_lift"`` only. The device
keeps its own reference model (a caliper-verified retract stop) and travel
ceiling; this catalog adds no limits and relaxes none. Only a human-approved,
main-merged, registered and validated plan may execute on hardware.
"""

from pydantic import BaseModel, ConfigDict, Field

from .models import SkillDef


class LiftNoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LiftReferenceArgs(BaseModel):
    """Operator evidence that the rod sits at the retract stop; not homing."""

    model_config = ConfigDict(extra="forbid")
    measured_rod_mm: float = Field(
        strict=True, gt=0, lt=50, allow_inf_nan=False,
        description="Caliper reading of the exposed rod, taken as during commissioning.",
    )
    rod_at_retract_stop: bool = Field(
        strict=True, description="Operator asserts the rod is at the retract stop.",
    )


class LiftMovePoseArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pose: str = Field(
        pattern=r"^[a-z][a-z0-9_]{0,31}$",
        description="Commissioned pose name on the device, e.g. 'weighing', 'raised', 'retracted'.",
    )


class LiftJogArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: int = Field(
        strict=True, ge=-600, le=600,
        description="Signed full steps; negative extends the rod and raises the carrier. The device applies its own per-call bound.",
    )


BALANCE_LIFT_SKILLS = [
    SkillDef(
        name="startup",
        kind="other",
        description="Energize the lift driver and clear safe start; no motion.",
        endpoint="/control/startup",
        args_schema=LiftNoArgs,
        requires_states=["requires_init"],
        estimated_duration_s=1.0,
    ),
    SkillDef(
        name="reference",
        kind="other",
        description="Declare the current pose as the retract stop after a caliper reading within tolerance.",
        endpoint="/control/reference",
        args_schema=LiftReferenceArgs,
        requires_states=["requires_init"],
        estimated_duration_s=1.0,
    ),
    SkillDef(
        name="move_pose",
        kind="other",
        description="Move the referenced lift to a commissioned pose (weighing, raised, retracted).",
        endpoint="/control/move/pose",
        args_schema=LiftMovePoseArgs,
        requires_states=["ready"],
        estimated_duration_s=60.0,
    ),
    SkillDef(
        name="jog",
        kind="other",
        description="Bounded relative move for alignment or re-referencing; the device enforces the window.",
        endpoint="/control/jog",
        args_schema=LiftJogArgs,
        requires_states=["requires_init", "ready"],
        estimated_duration_s=5.0,
    ),
    SkillDef(
        name="stop",
        kind="other",
        description="Halt and hold immediately.",
        endpoint="/control/stop",
        args_schema=LiftNoArgs,
        requires_states=["ready", "busy", "degraded", "error"],
        estimated_duration_s=0.5,
    ),
    SkillDef(
        name="park",
        kind="other",
        description="Return to the retract stop and de-energize; the required end of every session.",
        endpoint="/control/park",
        args_schema=LiftNoArgs,
        requires_states=["ready"],
        estimated_duration_s=60.0,
    ),
    SkillDef(
        name="shutdown",
        kind="other",
        description="De-energize the driver where it stands.",
        endpoint="/control/shutdown",
        args_schema=LiftNoArgs,
        requires_states=["requires_init", "ready", "busy", "degraded", "error"],
        estimated_duration_s=0.5,
    ),
]
