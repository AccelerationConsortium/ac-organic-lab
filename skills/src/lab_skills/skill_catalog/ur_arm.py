"""UR5e (``ligand_ur5e``) skills: the joint-step contract and joint trajectories.

Selected only for ``ligand_ur5e`` by registry.skills_for; never registered for
the generic robot_arm kind or xArm. The joint-step entry is SDK integration,
not a live-control enable switch: the deployed UR service runs its ``motion``
mode, which has no joint-step endpoint. ``UR_TRAJECTORY_SKILLS`` (below) are
live: the device has run the xArm's joint-trajectory contract since
2026-10-11 and advertises their names in ``allowed_actions``.

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
from .robot_arm import TrajectoryLimitsArgs, TrajectorySessionArgs


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


# ── Joint trajectories (UR5e, ur_rtde servoJ) ─────────────────────────
# The xArm's contract (robot_arm.TRAJECTORY_SKILLS) as the UR5e runs it: the
# same routes under /control/freehand/trajectory, six joints, 125 Hz by
# default (50–250). Client guide: xarm-translocation src/docs/TRAJECTORY_API.md
# ("UR5e"). Separate definitions rather than a shared list so each arm's
# schema says its own joint count and the UR's gates (no graph mode; an arm
# session that an operator opens) are stated where an agent reads them.
#
# Route: the device's own port refuses control, so calls go through the
# dashboard's passthrough (/api/equipment/ligand_ur5e/control/...), which
# presents the operator as the device's edge identity and forwards the
# caller's own X-Claim-Token (claim via .../control/claim first: a session
# belongs to the claim that created it), or through the edge's /ur5e route.
# The device lists these names in allowed_actions exactly while each call
# would be honoured, so lab.skills() availability needs no mapping. Every
# entry also requires the device's ``control`` component to read ``enabled``
# (present only with control on, since robot-motion's 2026-10-12 build): a
# receive-only service sends an empty allowed_actions, and without this gate
# the SDK's requires_states fallback would offer these on an arm that has no
# such routes, the same reason ur.joint_step carries its component gate.


class URTrajectoryPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    t: float = Field(ge=0, description="Seconds from the trajectory start; strictly increasing, at least "
                                       "one command period apart (8 ms at the default 125 Hz).")
    joints_deg: list[float] = Field(min_length=6, max_length=6, description="J1..J6 in degrees, base to wrist.")
    velocities_deg_s: list[float] | None = Field(default=None, min_length=6, max_length=6,
                                                 description="Optional; on every point or none.")
    accelerations_deg_s2: list[float] | None = Field(default=None, min_length=6, max_length=6,
                                                     description="Optional; with velocities (both give a quintic).")


class URTrajectoryValidateArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    points: list[URTrajectoryPoint] = Field(min_length=2, max_length=60000)
    rate_hz: float | None = Field(default=None, ge=50, le=250,
                                  description="Command rate; the device default (125 Hz) when omitted.")


class URTrajectoryCreateArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    rate_hz: float | None = Field(default=None, ge=50, le=250,
                                  description="Command rate; the device default (125 Hz) when omitted.")


class URTrajectoryChunkArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    session_id: str = Field(description="From trajectory.create.")
    seq: int = Field(ge=0, description="Chunk number: 0, 1, 2, ...")
    points: list[URTrajectoryPoint] = Field(min_length=1, max_length=5000)
    final: bool = Field(default=False, description="True on the last chunk; start needs it.")


_TRAJ = "/control/freehand/trajectory"
_CONTROL_ON = {"control": "enabled"}
_UR_TRAJ_NOTE = (
    " UR5e: an operator must have Connected the arm (the panel, or INIT on the tile) and the "
    "caller holds the claim; the pendant must be in Remote Control with the speed slider at "
    "100 %. The device's own port refuses control: call through the dashboard passthrough with "
    "your own X-Claim-Token, or the edge's /ur5e route. Validation is not a collision check."
)

UR_TRAJECTORY_SKILLS = [
    SkillDef(name="trajectory.limits", kind="robot_arm", endpoint=f"{_TRAJ}/limits", method="GET",
             description="Read first: the limits a joint trajectory must meet on the UR5e right now "
                         "(joint ranges, speed and acceleration caps from the commissioned motion block, "
                         "the TCP workspace box, force guard, duration, points, rate, start tolerance, "
                         "rules). Moves nothing.",
             args_schema=TrajectoryLimitsArgs, requires_components=_CONTROL_ON, estimated_duration_s=0.5),
    SkillDef(name="trajectory.validate", kind="robot_arm", endpoint=f"{_TRAJ}/validate",
             description="Check a whole joint trajectory against the limits, the measured start pose and "
                         "(with the controller's kinematics) the workspace box; moves nothing. Needs the "
                         "arm session open; no claim." + _UR_TRAJ_NOTE,
             args_schema=URTrajectoryValidateArgs, requires_states=["ready"], requires_components=_CONTROL_ON, estimated_duration_s=2.0),
    SkillDef(name="trajectory.create", kind="robot_arm", endpoint=_TRAJ,
             description="Open a trajectory session bound to your claim (one per arm); returns session_id."
                         + _UR_TRAJ_NOTE,
             args_schema=URTrajectoryCreateArgs, requires_states=["ready"], requires_components=_CONTROL_ON, estimated_duration_s=1.0),
    SkillDef(name="trajectory.upload_chunk", kind="robot_arm", endpoint=f"{_TRAJ}/{{session_id}}/chunks/{{seq}}",
             description="Upload chunk seq of the session's trajectory; the last carries final=true. "
                         "Identical re-sends are harmless." + _UR_TRAJ_NOTE,
             args_schema=URTrajectoryChunkArgs, requires_states=["ready"], requires_components=_CONTROL_ON, estimated_duration_s=2.0),
    SkillDef(name="trajectory.start", kind="robot_arm", endpoint=f"{_TRAJ}/{{session_id}}/start",
             description="Start the complete trajectory: servoJ at the session rate on the device's clock. "
                         "Same gates as a moveJ (412/409) plus the path inside the box (422). Accepted, "
                         "not completed: poll trajectory.status." + _UR_TRAJ_NOTE,
             args_schema=TrajectorySessionArgs, requires_states=["ready"], requires_components=_CONTROL_ON, estimated_duration_s=60.0),
    SkillDef(name="trajectory.status", kind="robot_arm", endpoint=f"{_TRAJ}/{{session_id}}", method="GET",
             description="Session state, progress (executed.t_exec), started_at_utc and the measured joints. "
                         "Poll at 10-20 Hz at most.",
             args_schema=TrajectorySessionArgs, requires_components=_CONTROL_ON, estimated_duration_s=0.5),
    SkillDef(name="trajectory.cancel", kind="robot_arm", endpoint=f"{_TRAJ}/{{session_id}}/cancel",
             description="Slow the running trajectory to a stop along its path (or drop an unstarted "
                         "session). For an immediate halt POST /control/stop, which needs no claim.",
             args_schema=TrajectorySessionArgs, requires_components=_CONTROL_ON, estimated_duration_s=3.0),
]


__all__ = ["URJointStepArgs", "URTrajectoryPoint", "UR_ARM_SKILLS", "UR_TRAJECTORY_SKILLS"]
