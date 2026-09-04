"""Skill catalog entries for ``kind=robot_arm``.

Reference device: :mod:`xarm_translocation` (UFactory xArm5), now on
STATUS_SPEC v1.1 with a claim-gated **graph** control surface. Motion is
expressed as moves between named nodes in a motion graph rather than raw
cartesian/joint targets; the device enforces ``X-Claim-Token`` on every
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

from pydantic import BaseModel, Field

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
            description="Set the graph interlock mode (off / advisory / strict).",
            endpoint="/control/graph/mode",
            args_schema=GraphModeArgs,
            requires_states=["ready"],
            estimated_duration_s=0.5,
        ),
    ],
)


__all__ = [
    "GraphGripperArgs",
    "GraphModeArgs",
    "GraphMoveToArgs",
    "GraphRecordArgs",
    "GraphRecoverToArgs",
    "GraphTravelToArgs",
]
