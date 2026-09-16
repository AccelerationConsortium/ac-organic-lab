"""UR5e joint-step contract for the future commissioned control service.

Selected only for ``ligand_ur5e`` by registry.skills_for; never registered for
the generic robot_arm kind or xArm. This is SDK integration, not a live-control
enable switch: the current UR service has no corresponding control endpoint.

The device must enforce claims, authenticated plan authority, request replay
protection, commissioned joint/travel limits, fresh feedback and a watchdog.
Its ``ur_joint_step`` component may report ``commissioned_idle`` ONLY when all
device preconditions permit a step, and must drop that state on disconnection,
stale feedback, motion, lease loss or a latched failure. The same device helper
must gate ``allowed_actions`` and the endpoint. UI polling is not motion
feedback. This marker is a precondition, not proof of authentication or safety.

The component gate is mandatory even with a nonempty allowed_actions list.
It also keeps today's receive-only service unavailable under the SDK's legacy
empty-allowed_actions -> requires_states fallback. No global SDK fallback or
xArm catalog behavior is changed here.

Only a human-approved, main-merged, registered and validated plan may execute
on hardware. Do not use this module as an ad-hoc hardware test script. A valid
request is not commissioning approval; tighter device-side limits still win.
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import SkillDef


class URJointStepArgs(BaseModel):
    """One explicit signed joint increment; degrees, no speed/force overrides."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Keep a canonical JSON-native string: execute_plan forwards the original
    # plan body rather than the normalized args model.
    request_id: str = Field(
        strict=True,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        json_schema_extra={"format": "uuid"},
        description="Unique nonzero UUID, canonical lowercase form. Never replay an attempted step.",
    )
    joint: int = Field(strict=True, ge=1, le=6, description="Joint number J1 through J6.")
    delta_deg: float = Field(
        strict=True,
        allow_inf_nan=False,
        ge=-0.1,
        le=0.1,
        description=(
            "Explicit signed increment in degrees; magnitude 0.02 through 0.1. "
            "This software cap is not a validated safety limit."
        ),
    )

    @field_validator("request_id")
    @classmethod
    def nonzero_request_id(cls, value: str) -> str:
        if UUID(value).int == 0:
            raise ValueError("A nonzero request UUID is required")
        return value

    @field_validator("delta_deg")
    @classmethod
    def minimum_step(cls, value: float) -> float:
        if abs(value) < 0.02:
            raise ValueError("Joint step magnitude must be 0.02 through 0.1 degrees")
        return value


UR_ARM_SKILLS = [
    SkillDef(
        name="ur.joint_step",
        kind="robot_arm",
        description=(
            "One commissioned UR5e joint step of 0.02–0.1 degrees. Requires "
            "an approved plan, hard claim and device control preconditions; "
            "unavailable on the current receive-only service. Never auto-retry."
        ),
        endpoint="/control/joint/step",
        args_schema=URJointStepArgs,
        requires_states=["ready"],
        requires_components={"ur_joint_step": "commissioned_idle"},
        estimated_duration_s=5.0,
    ),
]


__all__ = ["URJointStepArgs", "UR_ARM_SKILLS"]
