"""Skill catalog entries for ``kind=plate_reader``.

Reference device: :mod:`agilent_cytation_server` — STATUS_SPEC v1.2, full
``/control/*`` write surface (claim protocol, drawer, plate management, three
read types, imaging, incubator, shaker, timed runs). Endpoint paths and arg ranges mirror
the device's Pydantic constraints in
``agilent_cytation_server/control_args.py``; keep them in step.
"""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, model_validator

from .models import SkillDef
from .registry import register


# Ranges are the driver's own limits, duplicated here so the SDK refuses a
# doomed request locally instead of round-tripping to a 422.
_ABS_NM = (230.0, 999.0)
_EX_NM = (250.0, 700.0)
_EM_NM = (250.0, 700.0)
_FOCAL_MM = (4.5, 13.88)


class _StrictArgs(BaseModel):
    """Reject unknown fields rather than ignoring them.

    Matches the device: a dropped ``gain`` on a read would yield a plausible
    number measured at some other gain. The device 422s extras; so does this
    schema, so ``validate_plan`` / typed clients fail locally.
    """

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Lifecycle / drawer
# ---------------------------------------------------------------------------


class StartupArgs(BaseModel):
    """Body for ``POST /control/startup`` (no parameters)."""


class ShutdownArgs(BaseModel):
    """Body for ``POST /control/shutdown`` (no parameters)."""


class DrawerArgs(BaseModel):
    """Body for ``POST /control/drawer/{open,close}`` (no parameters)."""


# ---------------------------------------------------------------------------
# Plate / well sample tracking
# ---------------------------------------------------------------------------


class WellSample(BaseModel):
    """One well of the currently-loaded plate. Mirrors the device-side
    ``agilent_cytation_server.models.WellSample``."""

    well: str
    sample_id: str | None = None
    volume_ul: float | None = Field(default=None, ge=0.0)
    notes: str | None = None


class PlateLoadArgs(BaseModel):
    """Body for ``POST /control/plate/load``.

    More than bookkeeping: PyLabRobot addresses wells through the ``Plate``
    resource assigned to the reader, so loading is what makes any read
    possible at all.
    """

    plate_id: str = Field(..., min_length=1, max_length=128)
    model: str | None = None  # defaults to device-configured default_model
    wells: list[WellSample] | None = None  # defaults to 96 empty wells


class PlateUnloadArgs(BaseModel):
    """Body for ``POST /control/plate/unload`` (no parameters)."""


class WellUpdateArgs(BaseModel):
    """Body for ``POST /control/well/update``."""

    well: str = Field(..., min_length=2, max_length=3)
    sample_id: str | None = None
    volume_ul: float | None = Field(default=None, ge=0.0)
    notes: str | None = None
    clear_sample_id: bool = False
    clear_notes: bool = False


# ---------------------------------------------------------------------------
# Reads
#
# NOTE: no `gain` field on any of these. The device exposes no read-gain
# control and returns 422 for the field rather than ignoring it.
# ---------------------------------------------------------------------------


class AbsorbanceArgs(_StrictArgs):
    """Body for ``POST /control/read/absorbance``."""

    wells: list[str] = Field(..., min_length=1, max_length=96)
    wavelength_nm: float = Field(..., ge=_ABS_NM[0], le=_ABS_NM[1])


class FluorescenceArgs(_StrictArgs):
    """Body for ``POST /control/read/fluorescence``."""

    wells: list[str] = Field(..., min_length=1, max_length=96)
    excitation_nm: float = Field(..., ge=_EX_NM[0], le=_EX_NM[1])
    emission_nm: float = Field(..., ge=_EM_NM[0], le=_EM_NM[1])
    focal_height_mm: float = Field(default=7.0, ge=_FOCAL_MM[0], le=_FOCAL_MM[1])


class LuminescenceArgs(_StrictArgs):
    """Body for ``POST /control/read/luminescence``."""

    wells: list[str] = Field(..., min_length=1, max_length=96)
    focal_height_mm: float = Field(default=7.0, ge=_FOCAL_MM[0], le=_FOCAL_MM[1])
    integration_time_s: float = Field(default=1.0, ge=0.1, le=60.0)


class ReadResult(BaseModel):
    """Response body for read.* skills (``dict[well, value]``)."""

    wells: dict[str, float]


# ---------------------------------------------------------------------------
# Incubator / shaker
# ---------------------------------------------------------------------------


class TemperatureArgs(_StrictArgs):
    """Body for ``POST /control/incubator/set_temperature``.

    Range mirrors the live device OpenAPI (probed 2026-08-23): 18-65 °C.
    The Cytation 5's incubation ceiling is 65 °C, and the 18 °C floor
    reflects that this unit heats only — there is no cooling module, so
    sub-ambient setpoints are refused by the device.
    """

    celsius: float = Field(..., ge=18.0, le=65.0)


class TemperatureStopArgs(BaseModel):
    """Body for ``POST /control/incubator/stop`` (no parameters)."""


class ShakeArgs(_StrictArgs):
    """Body for ``POST /control/shake/start``.

    ``displacement_mm`` is PyLabRobot's ``frequency`` argument renamed: it is
    orbit displacement in mm and runs *inversely* to speed — 6 mm is ~360 CPM,
    1 mm is ~1096 CPM.
    """

    pattern: Literal["orbital", "linear"] = "orbital"
    displacement_mm: int = Field(default=3, ge=1, le=6)


class ShakeStopArgs(BaseModel):
    """Body for ``POST /control/shake/stop`` (no parameters)."""


# ---------------------------------------------------------------------------
# Imaging
# ---------------------------------------------------------------------------


class _ImagingSettings(_StrictArgs):
    """Every capture setting except the well — shared by ``imaging.capture``
    and a timed run's imaging step (one capture per listed well)."""

    channel: str = Field(
        ...,
        description=(
            "Channel id: brightfield, phase_contrast, dapi, gfp, rfp, cy5, "
            "texas_red, cfp, yfp. Fluorescence channels require the matching "
            "filter cube; see details.imaging.installed_filters on /status."
        ),
    )
    objective: str | None = None
    focal_height_mm: float = Field(default=5.0, ge=_FOCAL_MM[0], le=_FOCAL_MM[1])
    exposure_ms: float = Field(default=10.0, ge=0.01, le=10_000.0)
    gain: float = Field(default=0.0, ge=0.0, le=47.0)
    led_intensity: int = Field(default=10, ge=1, le=10)
    autofocus: bool = False
    auto_exposure: bool = False


class ImagingCaptureArgs(_ImagingSettings):
    """Body for ``POST /control/imaging/capture``.

    ``gain`` here is the Spinnaker camera's analog gain in dB — unrelated to
    the PMT gain the reads deliberately do not expose. Fluorescence channels
    require the matching filter cube to be physically fitted; read
    ``details.imaging.installed_filters`` from ``/status`` before offering one.
    """

    well: str = Field(..., min_length=2, max_length=3)


class ImagingCaptureResult(BaseModel):
    """Response body for ``imaging.capture``.

    ``focal_height_mm`` / ``exposure_ms`` are the **resolved** values, which
    differ from the request when autofocus / auto-exposure ran.
    """

    well: str
    channel: str
    objective: str | None = None
    focal_height_mm: float
    exposure_ms: float
    gain: float
    image_path: str | None = None
    details: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Timed runs — hold a setpoint, measure on a schedule, executed by the device
#
# Mirrors ``RunStartArgs`` in the device's control_args.py. The device runs
# the schedule as a background task, so ``run.start`` returns 202 at once and
# needs no claim or heartbeat afterwards; every other hardware verb is
# withheld until the run ends.
# ---------------------------------------------------------------------------

_RUN_MAX_DURATION_S = 7 * 24 * 3600.0
_RUN_MAX_TIMEPOINTS = 2000


class RunAbsorbanceStep(AbsorbanceArgs):
    type: Literal["absorbance"]


class RunFluorescenceStep(FluorescenceArgs):
    type: Literal["fluorescence"]


class RunLuminescenceStep(LuminescenceArgs):
    type: Literal["luminescence"]


class RunImagingStep(_ImagingSettings):
    """One capture per listed well, all with the same settings."""

    type: Literal["imaging"]
    wells: list[str] = Field(..., min_length=1, max_length=96)


RunMeasurement = Annotated[
    Union[RunAbsorbanceStep, RunFluorescenceStep, RunLuminescenceStep, RunImagingStep],
    Field(discriminator="type"),
]


class RunStartArgs(_StrictArgs):
    """Body for ``POST /control/run/start``.

    Timepoints fall at ``0, interval_s, 2*interval_s, ...`` from the start of
    the hold, plus one at ``duration_s`` when the interval does not divide it:
    "60 C for 6 h, every 30 min" is ``temperature_c=60, duration_s=21600,
    interval_s=1800`` — 13 timepoints.
    """

    label: str | None = Field(default=None, max_length=200)
    temperature_c: float | None = Field(
        default=None, ge=18.0, le=65.0,
        description="Setpoint held for the run; omit to measure without touching the incubator.",
    )
    duration_s: float = Field(
        ..., gt=0, le=_RUN_MAX_DURATION_S,
        description="Length of the hold, counted from when the setpoint is reached.",
    )
    interval_s: float = Field(..., gt=0, description="Time between timepoints.")
    measurements: list[RunMeasurement] = Field(
        ..., min_length=1, max_length=16, description="Taken in order at every timepoint.",
    )
    wait_for_setpoint: bool = True
    setpoint_tolerance_c: float = Field(default=0.5, gt=0, le=5.0)
    setpoint_timeout_s: float = Field(default=3600.0, gt=0, le=6 * 3600.0)
    shake: ShakeArgs | None = Field(
        default=None,
        description="Shake between timepoints; stopped for each timepoint's measurements.",
    )
    temperature_log_interval_s: float = Field(default=60.0, gt=0, le=3600.0)
    max_consecutive_failures: int = Field(default=3, ge=1, le=100)
    on_complete: Literal["stop_temperature", "hold_temperature"] = Field(
        default="stop_temperature",
        description="Incubator after the run ends, however it ends.",
    )

    @model_validator(mode="after")
    def _bounded_schedule(self) -> "RunStartArgs":
        if math.floor(self.duration_s / self.interval_s + 1e-9) + 2 > _RUN_MAX_TIMEPOINTS:
            raise ValueError(
                f"Schedule has more than {_RUN_MAX_TIMEPOINTS} timepoints; "
                "lengthen interval_s or shorten duration_s"
            )
        return self


class RunStopArgs(BaseModel):
    """Body for ``POST /control/run/stop`` (no parameters)."""


class RunListArgs(BaseModel):
    """``GET /control/runs`` (no parameters; the device's ``limit`` query
    defaults to 20)."""


class RunGetArgs(BaseModel):
    """Path arg for ``GET /control/runs/{run_id}``."""

    run_id: str = Field(..., pattern=r"^run-[0-9A-Za-z-]{1,64}$")


class RunRecord(BaseModel):
    """Response of ``run.start`` / ``run.stop`` / ``run.get``. ``timepoints``
    and ``temperature_log`` are present on ``run.get``; extra fields from a
    newer device are kept."""

    model_config = {"extra": "allow"}

    run_id: str
    state: Literal[
        "equilibrating", "holding", "stopping", "completed", "stopped", "failed", "interrupted"
    ]
    label: str | None = None
    message: str | None = None
    timepoints_total: int
    next_timepoint_at: str | None = None
    timepoints: list[dict[str, Any]] = Field(default_factory=list)
    temperature_log: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


register(
    "plate_reader",
    [
        SkillDef(
            name="startup",
            kind="plate_reader",
            description="Connect to the Cytation and initialise optics + camera.",
            endpoint="/control/startup",
            args_schema=StartupArgs,
            requires_states=["requires_init", "ready", "dry_run"],
            estimated_duration_s=15.0,
        ),
        SkillDef(
            name="shutdown",
            kind="plate_reader",
            description="Disconnect from the Cytation.",
            endpoint="/control/shutdown",
            args_schema=ShutdownArgs,
            requires_states=["ready", "busy", "degraded", "error", "dry_run"],
            estimated_duration_s=1.0,
        ),
        SkillDef(
            name="drawer.open",
            kind="plate_reader",
            description="Eject the plate stage so a robot can place / retrieve a plate.",
            endpoint="/control/drawer/open",
            args_schema=DrawerArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=4.0,
        ),
        SkillDef(
            name="drawer.close",
            kind="plate_reader",
            description="Retract the plate stage into the reader.",
            endpoint="/control/drawer/close",
            args_schema=DrawerArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=4.0,
        ),
        SkillDef(
            name="plate.load",
            kind="plate_reader",
            description=(
                "Register that a plate is on the stage. Required before any read: "
                "the driver addresses wells through the plate resource."
            ),
            endpoint="/control/plate/load",
            args_schema=PlateLoadArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=0.5,
        ),
        SkillDef(
            name="plate.unload",
            kind="plate_reader",
            description="Clear the currently-loaded plate record.",
            endpoint="/control/plate/unload",
            args_schema=PlateUnloadArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=0.5,
        ),
        SkillDef(
            name="well.update",
            kind="plate_reader",
            description="Mutate one well's sample_id / volume_ul / notes.",
            endpoint="/control/well/update",
            args_schema=WellUpdateArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=0.2,
        ),
        SkillDef(
            name="read.absorbance",
            kind="plate_reader",
            description="Read absorbance at one wavelength (230-999 nm) for the named wells.",
            endpoint="/control/read/absorbance",
            args_schema=AbsorbanceArgs,
            returns_schema=ReadResult,
            requires_states=["ready", "dry_run"],
            # Motor idle is not enough — a plate must be loaded — but that is
            # not a component state, so the device's allowed_actions carries
            # it. The shaker gate IS expressible and matters: reads and the
            # shake task cannot share the serial link.
            requires_components={"shaker": "idle"},
            estimated_duration_s=15.0,
        ),
        SkillDef(
            name="read.fluorescence",
            kind="plate_reader",
            description="Read fluorescence (ex/em 250-700 nm) for the named wells.",
            endpoint="/control/read/fluorescence",
            args_schema=FluorescenceArgs,
            returns_schema=ReadResult,
            requires_states=["ready", "dry_run"],
            requires_components={"shaker": "idle"},
            estimated_duration_s=20.0,
        ),
        SkillDef(
            name="read.luminescence",
            kind="plate_reader",
            description="Read luminescence (no external excitation) for the named wells.",
            endpoint="/control/read/luminescence",
            args_schema=LuminescenceArgs,
            returns_schema=ReadResult,
            requires_states=["ready", "dry_run"],
            requires_components={"shaker": "idle"},
            estimated_duration_s=30.0,
        ),
        SkillDef(
            name="imaging.capture",
            kind="plate_reader",
            description=(
                "Capture one image bottom-up. Channels: brightfield, phase_contrast, "
                "and any fluorescence channel whose filter cube is fitted. Optional "
                "autofocus / auto-exposure search."
            ),
            endpoint="/control/imaging/capture",
            args_schema=ImagingCaptureArgs,
            returns_schema=ImagingCaptureResult,
            requires_states=["ready", "dry_run"],
            # `imaging` reports state "disconnected" when the camera failed to
            # initialise, so this gate is a real camera check, not a config flag.
            requires_components={"imaging": "idle", "shaker": "idle"},
            estimated_duration_s=5.0,
        ),
        SkillDef(
            name="incubator.set_temperature",
            kind="plate_reader",
            description="Set the incubator setpoint (18-65 C) and begin ramping.",
            endpoint="/control/incubator/set_temperature",
            args_schema=TemperatureArgs,
            requires_states=["ready", "busy", "dry_run"],
            estimated_duration_s=1.0,
        ),
        SkillDef(
            name="incubator.stop",
            kind="plate_reader",
            description="End temperature control; the incubator drifts to ambient.",
            endpoint="/control/incubator/stop",
            args_schema=TemperatureStopArgs,
            requires_states=["ready", "busy", "dry_run"],
            estimated_duration_s=1.0,
        ),
        SkillDef(
            name="shake.start",
            kind="plate_reader",
            description=(
                "Start shaking (orbital or linear). Motion outlives this call: the "
                "driver re-issues the command every 16 minutes until stopped."
            ),
            endpoint="/control/shake/start",
            args_schema=ShakeArgs,
            requires_states=["ready", "dry_run"],
            requires_components={"shaker": "idle"},
            estimated_duration_s=2.0,
        ),
        SkillDef(
            name="shake.stop",
            kind="plate_reader",
            description="Stop shaking. Remains available while the plate is moving.",
            endpoint="/control/shake/stop",
            args_schema=ShakeStopArgs,
            requires_states=["ready", "busy", "dry_run"],
            estimated_duration_s=1.0,
        ),
        SkillDef(
            name="run.start",
            kind="plate_reader",
            description=(
                "Start a timed run: hold the incubator at a setpoint for a duration "
                "and take reads/images at a fixed interval (e.g. 60 C for 6 h, "
                "every 30 min). Runs on the device; returns at once."
            ),
            endpoint="/control/run/start",
            args_schema=RunStartArgs,
            returns_schema=RunRecord,
            requires_states=["ready", "dry_run"],
            # The run owns the shaker and refuses to adopt a running one.
            requires_components={"shaker": "idle"},
            estimated_duration_s=1.0,
        ),
        SkillDef(
            name="run.stop",
            kind="plate_reader",
            description=(
                "Stop the active timed run after the measurement in flight; the "
                "shaker stops and on_complete is applied to the incubator."
            ),
            endpoint="/control/run/stop",
            args_schema=RunStopArgs,
            returns_schema=RunRecord,
            requires_states=["ready", "busy", "dry_run"],
            estimated_duration_s=1.0,
        ),
        SkillDef(
            name="run.list",
            kind="plate_reader",
            description="List recent timed runs (progress summaries, newest first).",
            endpoint="/control/runs",
            method="GET",
            args_schema=RunListArgs,
            estimated_duration_s=0.5,
        ),
        SkillDef(
            name="run.get",
            kind="plate_reader",
            description="One timed run with every timepoint's results and the temperature log.",
            endpoint="/control/runs/{run_id}",
            method="GET",
            args_schema=RunGetArgs,
            returns_schema=RunRecord,
            estimated_duration_s=0.5,
        ),
    ],
)


__all__ = [
    "AbsorbanceArgs",
    "DrawerArgs",
    "FluorescenceArgs",
    "ImagingCaptureArgs",
    "ImagingCaptureResult",
    "LuminescenceArgs",
    "PlateLoadArgs",
    "PlateUnloadArgs",
    "ReadResult",
    "RunAbsorbanceStep",
    "RunFluorescenceStep",
    "RunGetArgs",
    "RunImagingStep",
    "RunListArgs",
    "RunLuminescenceStep",
    "RunRecord",
    "RunStartArgs",
    "RunStopArgs",
    "ShakeArgs",
    "ShakeStopArgs",
    "ShutdownArgs",
    "StartupArgs",
    "TemperatureArgs",
    "TemperatureStopArgs",
    "WellSample",
    "WellUpdateArgs",
]
