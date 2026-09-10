"""HPLC skills for the Agilent UPLC-MS STATUS_SPEC v1.2 sidecar.

The SDK manages claims and preconditions. Sample addresses are single
``sample_position`` strings (D4B-A1), forwarded verbatim. Default sidecar
submissions queue during auto-detected technician acquisition; explicit
service mode refuses them. Standby and workflow.start are refused under
both servicing sources. Workflow.start requires the hte role.

Opt-in OpenLab dispatch reports only handoff, never acquisition completion.
Its submit script is selected by the device, so serialization omits
script_name. Operator service toggles and fault acknowledgments are not skills.
The installed device's /docs/agent and /openapi.json describe the full API.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_serializer, model_validator

from .models import SkillDef
from .registry import register


# Client-side well bounds; configured device labware remains authoritative.
_BUILTIN_PLATE_GEOMETRY: dict[str, tuple[int, int]] = {
    "96-well": (8, 12),
    "384-well": (16, 24),
    "54-vial": (6, 9),
}
_WELL_RE = re.compile(r"^([A-Za-z])(\d{1,2})$")


# ---------------------------------------------------------------------------
# run.submit argument schema — mirrors RunRequest on the device
# ---------------------------------------------------------------------------


class GradientConfig(BaseModel):
    """LC gradient program. Ranges mirror the device's hardware limits (Layer 1)."""

    name: str = Field(min_length=1, description="Human-readable gradient label.")
    solvent_a: str = Field(description="Mobile phase A, e.g. 'H2O_0.1%FA'.")
    solvent_b: str = Field(description="Mobile phase B, e.g. 'ACN_0.1%FA'.")
    run_time: float = Field(gt=0, le=120.0, description="Total run time in minutes (max 2 h).")
    flow_rate: float = Field(gt=0, le=2.0, description="Flow rate in mL/min.")
    gradient_table: list[list[float]] = Field(
        description="[[time_min, fraction_b], ...] where fraction_b is 0.0-1.0."
    )
    equilibration_time: float = Field(default=0.0, ge=0, le=30.0, description="Equilibration minutes.")


class SampleConfig(BaseModel):
    sample_name: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_\-]+$",
        description="Alphanumeric identifier (no spaces).",
    )
    sample_position: str = Field(
        pattern=r"^D[1-4][FB]-[A-Za-z]\d{1,2}$",
        description="Agilent drawer and well, e.g. D4B-A1. Sent verbatim to the device.",
    )

    @property
    def well(self) -> str:
        return self.sample_position.split("-", 1)[1]

    injection_volume: float = Field(gt=0, le=20.0, description="Injection volume in uL (max 20).")


class RunSubmitArgs(BaseModel):
    """Body for ``POST /control/run``."""

    output_dir: str = Field(
        min_length=1, description="Absolute path on the instrument PC for result files."
    )
    gradient: GradientConfig
    samples: list[SampleConfig] = Field(min_length=1, description="At least one sample required.")
    plate_format: str | None = Field(
        default=None,
        description=(
            "Declared plate type for all samples, asserted by the device against the "
            "drawer's configured labware. None trusts the device's configured labware; "
            "when unset it assumes '96-well' for this client-side well-range pre-check. "
            "Canonical types: '96-well', '384-well', '54-vial'."
        ),
    )
    submitter: Literal["manual", "robot"] = Field(
        default="manual",
        description=(
            "Runs targeting a drawer reserved for robotic submission are refused (HTTP 412) "
            "unless submitter='robot'."
        ),
    )
    ms_mode: Literal["positive", "negative", "positive_negative"] = "positive_negative"
    standby_after: bool = Field(default=True, description="Return to low-flow standby when done.")
    instrument_config_path: str = Field(
        default="examples/hh_472_config.json",
        description="Path to instrument config JSON (absolute or relative to MOSES_WORK_DIR).",
    )
    script_name: str = Field(
        default="examples/agent_agilent.py",
        description="Moses controller script (must be in the device's MOSES_ALLOWED_SCRIPTS).",
    )

    dispatch: Literal["sidecar", "openlab"] = Field(
        default="sidecar",
        description="sidecar tracks acquisition completion; openlab tracks only handoff (dispatching -> handed_off/failed). Omit script_name for openlab.",
    )

    @model_validator(mode="after")
    def _dispatch_script(self) -> "RunSubmitArgs":
        if self.dispatch == "openlab" and "script_name" in self.model_fields_set:
            raise ValueError("Omit script_name for dispatch='openlab'; the device selects it.")
        return self

    @model_serializer(mode="wrap")
    def _wire_body(self, handler):
        body = handler(self)
        if self.dispatch == "openlab":
            body.pop("script_name", None)
        return body

    @model_validator(mode="after")
    def _validate_wells(self) -> "RunSubmitArgs":
        # A plate_format outside the built-in set is a custom type: geometry is
        # validated on the device against the configured labware, so skip here.
        fmt = self.plate_format or "96-well"
        geometry = _BUILTIN_PLATE_GEOMETRY.get(fmt)
        if geometry is None:
            return self
        rows, cols = geometry
        for s in self.samples:
            m = _WELL_RE.match(s.well)
            if m is None:
                raise ValueError(f"Malformed well {s.well!r} (expected like 'A1', 'H12').")
            row_idx = ord(m.group(1).upper()) - ord("A")
            col = int(m.group(2))
            if not (0 <= row_idx < rows) or not (1 <= col <= cols):
                raise ValueError(
                    f"Well {s.well!r} is out of range for a {fmt} plate."
                )
        return self


class RunSubmitResult(BaseModel):
    """Response body for ``run.submit``."""

    run_id: str
    status: Literal["accepted", "queued", "dispatching"]
    message: str
    queue_position: int | None = None


class AbortArgs(BaseModel):
    """Body for ``POST /control/abort`` (no parameters)."""


class QueueCancelArgs(BaseModel):
    """Path arg for ``DELETE /control/queue/{queue_id}``.

    ``queue_id`` is substituted into the endpoint path (not sent as a body).
    """

    queue_id: str = Field(min_length=1, description="queue_id returned by run.submit / GET /control/queue.")


class StandbyArgs(BaseModel):
    """Body for ``POST /control/standby`` (no parameters)."""


class WorkflowStartArgs(BaseModel):
    """Body for ``POST /control/workflow/start`` (no parameters).

    The lock owner is the claim owner — identity rides on ``X-Claim-Token``,
    not the body.
    """


class WorkflowStartResult(BaseModel):
    """Response body for ``workflow.start`` (mirrors the device's
    ``WorkflowStartResponse``)."""

    status: Literal["workflow_started"] = "workflow_started"
    message: str
    expires_at: datetime
    heartbeat_interval_s: float


class WorkflowEndArgs(BaseModel):
    """Body for ``POST /control/workflow/end`` (no parameters)."""


class WorkflowEndResult(BaseModel):
    """Response body for ``workflow.end`` (mirrors the device's
    ``WorkflowEndResponse``)."""

    status: Literal["workflow_ended"] = "workflow_ended"
    message: str


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


register(
    "hplc",
    [
        SkillDef(
            name="run.submit",
            kind="hplc",
            description=(
                "Submit a batch LC-MS run. Starts immediately if the instrument is "
                "idle, otherwise queues behind the active run (FIFO), including technician "
                "acquisitions. Explicit service mode refuses submissions. OpenLab dispatch "
                "tracks handoff only; handed_off does not mean acquisition completed."
            ),
            endpoint="/control/run",
            args_schema=RunSubmitArgs,
            returns_schema=RunSubmitResult,
            # Enqueue verb: offered while ready or busy (it queues when busy);
            # the device drops it from allowed_actions on queue-full (412) or
            # OpenLab-down (409 requires_init).
            requires_states=["ready", "busy", "dry_run"],
            estimated_duration_s=None,  # bounded by gradient.run_time; not estimable here
        ),
        SkillDef(
            name="run.abort",
            kind="hplc",
            description="Abort the active run and clear all pending queued runs.",
            endpoint="/control/abort",
            args_schema=AbortArgs,
            # No precondition: abort is always honoured (no-op 'not_running' when idle).
            requires_states=["ready", "busy", "degraded", "error", "dry_run"],
            estimated_duration_s=10.0,
        ),
        SkillDef(
            name="queue.cancel",
            kind="hplc",
            description=(
                "Cancel one pending (not-yet-started) job by queue_id. Use run.abort "
                "to stop the run that is already executing."
            ),
            endpoint="/control/queue/{queue_id}",
            method="DELETE",
            args_schema=QueueCancelArgs,
            requires_states=["ready", "busy", "dry_run"],
            estimated_duration_s=0.5,
        ),
        SkillDef(
            name="instrument.standby",
            kind="hplc",
            description=(
                "Park the instrument in low-flow standby (NOT a full shutdown — "
                "powering down is a manual operator procedure). Queues behind any "
                "active run."
            ),
            endpoint="/control/standby",
            args_schema=StandbyArgs,
            # Enqueue verb (same gating as run.submit): refused on queue-full /
            # OpenLab-down.
            requires_states=["ready", "busy", "dry_run"],
            estimated_duration_s=60.0,
        ),
        SkillDef(
            name="workflow.start",
            kind="hplc",
            description=(
                "Take the equipment-blocking workflow lock for a robot/agent "
                "campaign (a series of runs). While held, the device refuses "
                "sample submits from anyone but the lock holder (423 "
                "workflow_active). Requires the claim owner's device role to "
                "be `automation` (403 role_forbidden otherwise)."
            ),
            endpoint="/control/workflow/start",
            args_schema=WorkflowStartArgs,
            returns_schema=WorkflowStartResult,
            # Enqueue-gated like run.submit (refused on queue-full → 412,
            # OpenLab-down → 409 requires_init, servicing → 409); the device
            # also drops it from allowed_actions while a workflow is active.
            requires_states=["ready", "busy", "dry_run"],
            estimated_duration_s=0.5,
        ),
        SkillDef(
            name="workflow.end",
            kind="hplc",
            description=(
                "Release the equipment-blocking workflow lock; the underlying "
                "claim is retained. Idempotent — ending when no workflow is "
                "active still succeeds."
            ),
            endpoint="/control/workflow/end",
            args_schema=WorkflowEndArgs,
            returns_schema=WorkflowEndResult,
            # No precondition (like run.abort): only ever releases the lock.
            # The device offers it exactly while a workflow is active.
            requires_states=["ready", "busy", "degraded", "error", "dry_run"],
            estimated_duration_s=0.5,
        ),
    ],
)


__all__ = [
    "AbortArgs",
    "GradientConfig",
    "QueueCancelArgs",
    "RunSubmitArgs",
    "RunSubmitResult",
    "SampleConfig",
    "StandbyArgs",
    "WorkflowEndArgs",
    "WorkflowEndResult",
    "WorkflowStartArgs",
    "WorkflowStartResult",
]
