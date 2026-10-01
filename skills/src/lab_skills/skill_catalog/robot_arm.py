"""Skill catalog entries for ``kind=robot_arm``.

Reference device: :mod:`xarm_translocation` (UFactory xArm5), now on
STATUS_SPEC v1.1 with a claim-gated **graph** control surface. Motion is
normally expressed as moves between named nodes in a motion graph. The
xArm-specific freehand skills expose Cartesian/joint targets in OFF or ADVISORY; the device enforces ``X-Claim-Token`` on every
``/control/*`` call (423 without a valid claim).

Live control surface (``/control/graph/*``):

* ``POST /control/graph/move_to``     - move to a named graph node (one hop)
* ``POST /control/graph/travel_to``   - multi-hop travel to any reachable node
                                        (device plans the shortest whitelisted
                                        hop path and executes it, blocking)
* ``POST /control/graph/gripper``     - change to a named catalog gripper state
* ``POST /control/graph/recover_to``  - declare the current node (recovery)
* ``POST /control/graph/record``      - record the last transition as an edge
* ``POST /control/graph/mode``        - set the graph interlock mode

The device's ``equipment.yaml`` entry keeps ``do_not_call_connect: true``,
so the SDK never auto-connects: control requires the arm to be connected
first (the device returns 409/400 "connect first" otherwise), and
``status.allowed_actions`` drives runtime availability once it is. The
``requires_states`` below are the v1.0-style fallback.

Since 2026-08-13 the device advertises these catalog names verbatim in
``allowed_actions`` (gated per endpoint: ``graph.move_to`` /
``graph.gripper`` need at least one whitelisted target in STRICT mode,
``graph.record`` needs a real last transition, ``graph.recover_to`` /
``graph.mode`` need a loaded graph), so ``lab.skills()`` availability
works with no name mapping. Its finer-grained ``move.<node_id>`` /
``gripper.<state>`` enumeration is advertised *alongside* — those are
device-authoritative per-target availability, consumed by the dashboard
assistant's per-hop proposals, and deliberately not SkillDefs here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import SkillDef
from .registry import register


class GraphMoveToArgs(BaseModel):
    node_id: str = Field(description="Graph node id to move to.")
    speed: float | None = Field(
        default=None,
        description="Movement speed; may be capped by the edge speed in STRICT mode.",
    )


class GraphTravelToArgs(BaseModel):
    node_id: str = Field(description="Graph node id to travel to (any reachable node).")
    speed: float | None = Field(
        default=None,
        description="Movement speed for every hop; per-edge caps still apply.",
    )


class GraphGripperArgs(BaseModel):
    state: str = Field(
        description=(
            "Target catalog gripper state, e.g. 'empty' or 'grip_120'. The "
            "transition must be whitelisted for the arm's current node and "
            "current gripper state; the device advertises the legal ones as "
            "'gripper.<state>' in allowed_actions (and as "
            "details.motion_graph.allowed_gripper_targets), and refuses "
            "anything else with HTTP 409."
        ),
    )


class GraphRecoverToArgs(BaseModel):
    node_id: str = Field(description="Graph node id the operator declares as current.")
    force: bool = Field(
        default=False,
        description=(
            "Skip the nearest-node sanity check. Use when position has been "
            "verified by other means."
        ),
    )


class GraphRecordArgs(BaseModel):
    mode: Literal["linear", "joint"] | None = Field(
        default=None,
        description="'linear' or 'joint'; defaults to the mode the last move used.",
    )
    speed: float | None = Field(
        default=None, description="Override edge speed; defaults to the speed used."
    )
    comment: str | None = Field(default=None, description="Free-text comment.")
    preconditions: list[str] | None = Field(
        default=None, description="List of named preconditions for the edge."
    )


class GraphModeArgs(BaseModel):
    mode: Literal["off", "advisory", "strict"] = Field(
        description="Graph interlock mode: off, advisory, or strict.",
    )


    reason: str | None = Field(
        default=None,
        description="Required when lowering to off/advisory; reason for the bounded override.",
    )
    ttl_seconds: float | None = Field(
        default=None, ge=1,
        description="Override duration in seconds; device applies its configured default and cap. Reverts to strict on expiry or claim release/expiry.",
    )


class FreehandPositionArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    x: float = Field(description="Absolute TCP X coordinate in mm.")
    y: float = Field(description="Absolute TCP Y coordinate in mm.")
    z: float = Field(description="Absolute TCP Z coordinate in mm.")
    roll: float | None = Field(default=None, description="Roll in degrees; omitted keeps current orientation.")
    pitch: float | None = Field(default=None, description="Pitch in degrees; omitted keeps current orientation.")
    yaw: float | None = Field(default=None, description="Yaw in degrees; omitted keeps current orientation.")
    speed: float | None = Field(default=None, description="TCP speed in mm/s; device safety limits apply.")
    check_collision: bool = Field(default=True, description="Perform the device collision check.")
    wait: bool = Field(default=True, description="Wait for completion inside the device background task; HTTP response still means accepted.")


class FreehandRelativeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    dx: float = Field(default=0, description="TCP X displacement in mm.")
    dy: float = Field(default=0, description="TCP Y displacement in mm.")
    dz: float = Field(default=0, description="TCP Z displacement in mm.")
    droll: float = Field(default=0, description="Roll change in degrees.")
    dpitch: float = Field(default=0, description="Pitch change in degrees.")
    dyaw: float = Field(default=0, description="Yaw change in degrees.")
    speed: float | None = Field(default=None, description="TCP speed in mm/s; device safety limits apply.")


class FreehandJointsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    angles: list[float] = Field(description="Joint angles in degrees, one per robot joint.")
    speed: float | None = Field(default=None, description="Joint speed in degrees/s.")
    acceleration: float | None = Field(default=None, description="Joint acceleration in degrees/s squared.")
    check_collision: bool = Field(default=True, description="Perform the device collision check.")
    wait: bool = Field(default=True, description="Wait inside the background task; poll status after command acceptance.")


# Device-specific: other robot arms do not implement these endpoints.
FREEHAND_SKILLS = [
    SkillDef(
        name=f"freehand.{name}",
        kind="robot_arm",
        description=(
            f"{description} Requires graph mode OFF or ADVISORY and an active claim. "
            "STRICT refuses with 409. Device workspace, collision, concurrency and "
            "configured interlock checks still apply. Clears the named node pin. "
            "The response means accepted, not completed; poll status for completion. "
            "Availability follows the device's allowed_actions."
        ),
        endpoint=f"/control/freehand/{name}",
        args_schema=args,
        requires_states=["ready", "degraded", "dry_run"],
        estimated_duration_s=10.0,
    )
    for name, description, args in (
        ("position", "Move the xArm to an absolute Cartesian TCP pose.", FreehandPositionArgs),
        ("relative", "Move the xArm by a relative Cartesian TCP offset.", FreehandRelativeArgs),
        ("joints", "Move the xArm to joint angles.", FreehandJointsArgs),
    )
]


register(
    "robot_arm",
    [
        SkillDef(
            name="graph.move_to",
            kind="robot_arm",
            description="Move the arm to a named node in the motion graph.",
            endpoint="/control/graph/move_to",
            args_schema=GraphMoveToArgs,
            requires_states=["ready"],
            estimated_duration_s=10.0,
        ),
        SkillDef(
            name="graph.travel_to",
            kind="robot_arm",
            description=(
                "Multi-hop travel to any reachable node in the motion graph: "
                "the device plans the shortest whitelisted hop path from its "
                "current node (holding the current gripper state throughout) "
                "and executes it hop-by-hop under one reservation, blocking "
                "until the journey completes. Refused (409) if no whitelisted "
                "path exists from the current node."
            ),
            endpoint="/control/graph/travel_to",
            args_schema=GraphTravelToArgs,
            requires_states=["ready"],
            # Whole-journey budget, not per hop: single hops measure ~5-25 s
            # live and the graph is a handful of hops across.
            estimated_duration_s=60.0,
        ),
        SkillDef(
            name="graph.gripper",
            kind="robot_arm",
            description=(
                "Change the gripper to a named catalog state (grip / release / "
                "narrow) while parked at the current graph node. The only "
                "graph-sanctioned way to grip or release: the stroke is "
                "invariant during arm motion, so the device requires a "
                "stationary arm and a pinned current node."
            ),
            endpoint="/control/graph/gripper",
            args_schema=GraphGripperArgs,
            requires_states=["ready"],
            estimated_duration_s=3.0,
        ),
        SkillDef(
            name="graph.recover_to",
            kind="robot_arm",
            description=(
                "Declare the arm's current graph node (recovery), so subsequent "
                "moves are planned from a known position."
            ),
            endpoint="/control/graph/recover_to",
            args_schema=GraphRecoverToArgs,
            requires_states=["ready"],
            estimated_duration_s=2.0,
        ),
        SkillDef(
            name="graph.record",
            kind="robot_arm",
            description="Record the last transition between nodes as a graph edge.",
            endpoint="/control/graph/record",
            args_schema=GraphRecordArgs,
            requires_states=["ready"],
            estimated_duration_s=1.0,
        ),
        SkillDef(
            name="graph.mode",
            kind="robot_arm",
            description="Set graph mode (off / advisory / strict). Lowering requires a reason and opens a bounded window; expiry or claim release restores strict.",
            endpoint="/control/graph/mode",
            args_schema=GraphModeArgs,
            requires_states=["ready"],
            estimated_duration_s=0.5,
        ),
    ],
)


class RealSenseCaptureArgs(BaseModel):
    camera: str | None = Field(
        default=None,
        description=(
            "Which depth camera to capture from: an id from GET /realsense/cameras "
            "on the device (the xArm's first is 'rs435i'). Optional while the arm "
            "carries exactly one camera -- the device resolves the sole camera. "
            "Required (400 camera_required) once a second camera is configured."
        ),
    )
    label: str | None = Field(
        default=None,
        description="Free-form name for this capture, e.g. 'plate-arrival-check'.",
    )
    node_id: str | None = Field(
        default=None,
        description=(
            "Motion-graph node this capture belongs to. Defaults to the arm's "
            "current node, which is what you want unless you are labelling a "
            "capture for a node the arm is not parked at."
        ),
    )
    tags: list[str] | None = Field(
        default=None, description="Free-form tags for later filtering."
    )
    protected: bool = Field(
        default=False,
        description=(
            "Exempt this capture from the store's retention bounds (30 days / "
            "20 GB). Use only for records that must outlive them, such as a "
            "reference frame for a node."
        ),
    )


# Not registered under the generic ``robot_arm`` kind: the depth camera is
# hardware on one arm, not a property of being an arm. The MG400 has no
# RealSense, so advertising a capture verb for it would put an action in the
# catalog that the device would refuse -- the same reasoning that keeps UR
# joint control out of the shared list. ``skills_for`` attaches these to
# ``xarm_translocation`` only.
REALSENSE_SKILLS = [
    SkillDef(
        name="realsense.capture",
        kind="robot_arm",
        description=(
            "Record what the arm's eye-in-hand depth camera sees right now: "
            "one aligned colour + 16-bit depth frameset written to disk with "
            "the arm's pose (node, joints, TCP, rail, gripper) alongside it, "
            "returning a capture id. Use this when the frame is evidence -- an "
            "arrival check, a reference for a node, a record of what was on the "
            "deck. For a transient look use the open reads GET /realsense/"
            "snapshot.jpg and GET /realsense/depth?x=&y= instead, which write "
            "nothing. Starts the camera on demand; withheld while the arm is "
            "moving, since a frameset grabbed mid-move is blurred and its pose "
            "has already changed. Cameras are addressed by id under "
            "/realsense/{camera_id}/ (discover them with GET /realsense/cameras); "
            "this skill posts to the fixed alias /control/realsense/capture with "
            "the id in the body, because the plan executor sends catalog "
            "endpoints verbatim and cannot fill a path parameter."
        ),
        # The device's canonical route is /control/realsense/{camera_id}/capture.
        # The catalog cannot use it: execute_plan and the dashboard passthrough
        # send this string verbatim (no templating), so the device keeps a fixed
        # alias that takes the camera id in the body and resolves the sole camera
        # when it is omitted.
        endpoint="/control/realsense/capture",
        args_schema=RealSenseCaptureArgs,
        # The camera is independent of the arm's health: the device advertises
        # this in ``degraded`` and ``dry_run`` too, because neither stops a
        # frameset being grabbed. ``busy`` is absent on purpose -- the device
        # withholds the action while a motion is in flight, since a frame taken
        # mid-move is blurred and its pose has already changed.
        requires_states=["ready", "degraded", "dry_run"],
        # ~2 s of that is the pipeline starting from cold; a capture against an
        # already-streaming camera returns in well under a second.
        estimated_duration_s=3.0,
    ),
]


__all__ = [
    "REALSENSE_SKILLS",
    "FREEHAND_SKILLS",
    "FreehandPositionArgs",
    "FreehandRelativeArgs",
    "FreehandJointsArgs",
    "GraphGripperArgs",
    "GraphModeArgs",
    "GraphMoveToArgs",
    "GraphRecordArgs",
    "GraphRecoverToArgs",
    "GraphTravelToArgs",
    "RealSenseCaptureArgs",
]
