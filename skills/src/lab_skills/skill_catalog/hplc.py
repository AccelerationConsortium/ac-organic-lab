"""Skill catalog entries for ``kind=hplc``.

Reference device: :mod:`agilent_hplcms_server` — the Agilent UPLC-MS status +
control sidecar (instrument ``SDL2_LC1290``). It implements STATUS_SPEC v1.1 and
hard-enforces ``X-Claim-Token`` on ``/control/*`` (the aggregator's per-request
claim dance handles that transparently). Endpoint paths and arg ranges mirror
the device's Pydantic ``Field(gt=, le=)`` constraints in
``agilent_hplcms_server/control/models.py``.

The sidecar OWNS the job queue: its MosesRunner is the sole FIFO queue for our
runs (process-exit authoritative), while OpenLab CDS is reserved for technician
servicing/maintenance. Control surface:

* ``POST   /control/run``               - submit a batch run (starts if idle, else queues)
* ``POST   /control/abort``             - abort the active run and clear the queue
* ``DELETE /control/queue/{queue_id}``  - cancel one *pending* (not-yet-started) job
* ``POST   /control/standby``           - park the instrument in low-flow standby
                                          (NOT a full shutdown — that is a manual
                                          operator procedure at the instrument)
* ``POST   /control/workflow/start``    - take the equipment-blocking workflow lock
                                          for a robot/agent campaign (HTE users only)
* ``POST   /control/workflow/end``      - release the workflow lock (claim retained)

``Skill.name`` matches the device's ``allowed_actions`` (``run.submit`` /
``run.abort`` / ``queue.cancel`` / ``instrument.standby`` / ``workflow.start`` /
``workflow.end``). The device drops the *enqueue* verbs (``run.submit``,
``instrument.standby``, ``workflow.start``) from ``allowed_actions`` whenever it
would refuse them — queue full → 412, OpenLab core down → 409 ``requires_init``,
or a technician is servicing the instrument directly in OpenLab → 409
``instrument_servicing`` — so availability stays truthful. ``workflow.start`` is
additionally offered only while no workflow is active, and ``workflow.end`` exactly
while one is.

**Workflow lock (queue-ownership precedence #2):** an HTE platform user takes the
equipment-blocking lock for a campaign — a series of runs — via ``workflow.start``;
while held, the device refuses sample submits from anyone but the lock holder with
``423 workflow_active``. The lock rides on the caller's claim, so it inherits the
claim's TTL/heartbeat/auto-expiry (a crashed holder loses it). ``workflow.start``
requires the claim owner's role to be ``hte`` (else ``403 role_forbidden``);
``workflow.end`` is idempotent. (Operator/dashboard service-mode toggles —
``/control/service/start|end`` — are deliberately NOT skills: they are technician
controls, not agent actions.)

Not modelled as skills: ``GET /control/queue`` (read-only status, surfaced via
the aggregator) and ``POST /control/startup`` (a read-only readiness probe that
never starts hardware).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .models import SkillDef
from .registry import register


# Autosampler tray + plate geometry. Mirrors agilent_hplcms_server's
# control/models.py: the device composes the Agilent "{drawer}-{well}" position
# string from {tray, well} and validates wells against plate_format.
TrayName = Literal["front", "rear"]
PlateFormat = Literal["96-well", "384-well"]
_PLATE_GEOMETRY: dict[str, tuple[int, int]] = {"96-well": (8, 12), "384-well": (16, 24)}
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
    tray: TrayName = Field(description="Autosampler tray holding this sample (front or rear).")
    well: str = Field(
        min_length=2,
        max_length=4,
        description='Plate well, e.g. "A1" or "H12". Validated against plate_format.',
    )
    injection_volume: float = Field(gt=0, le=20.0, description="Injection volume in uL (max 20).")


class RunSubmitArgs(BaseModel):
    """Body for ``POST /control/run``."""

    output_dir: str = Field(
        min_length=1, description="Absolute path on the instrument PC for result files."
    )
    gradient: GradientConfig
    samples: list[SampleConfig] = Field(min_length=1, description="At least one sample required.")
    plate_format: PlateFormat = Field(
        default="96-well", description="Plate format for all samples in this run."
    )
    submitter: Literal["manual", "robot"] = Field(
        default="manual",
        description=(
            "Runs targeting a tray reserved for robotic submission are refused (HTTP 412) "
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

    @model_validator(mode="after")
    def _validate_wells(self) -> "RunSubmitArgs":
        rows, cols = _PLATE_GEOMETRY[self.plate_format]
        for s in self.samples:
            m = _WELL_RE.match(s.well)
            if m is None:
                raise ValueError(f"Malformed well {s.well!r} (expected like 'A1', 'H12').")
            row_idx = ord(m.group(1).upper()) - ord("A")
            col = int(m.group(2))
            if not (0 <= row_idx < rows) or not (1 <= col <= cols):
                raise ValueError(
                    f"Well {s.well!r} is out of range for a {self.plate_format} plate."
                )
        return self


class RunSubmitResult(BaseModel):
    """Response body for ``run.submit``."""

    run_id: str
    status: Literal["accepted", "queued"]
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
                "idle, otherwise queues behind the active run (FIFO)."
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
                "workflow_active). Requires an HTE platform user (403 "
                "role_forbidden otherwise)."
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
