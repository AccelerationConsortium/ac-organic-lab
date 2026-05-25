"""Skill catalog entries for ``kind=plate_reader``.

Reference device: :mod:`agilent_cytation_server` — implements STATUS_SPEC
v1.1 and exposes the full ``/control/*`` write surface (claim protocol,
drawer, plate management, three read types, imaging). Endpoint paths and
arg ranges mirror the device's Pydantic ``Field(ge=, le=)`` constraints
in ``agilent_cytation_server/control_args.py``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .models import SkillDef
from .registry import register


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class StartupArgs(BaseModel):
    """Body for ``POST /control/startup`` (no parameters)."""


class ShutdownArgs(BaseModel):
    """Body for ``POST /control/shutdown`` (no parameters)."""


# ---------------------------------------------------------------------------
# Drawer
# ---------------------------------------------------------------------------


class DrawerArgs(BaseModel):
    """Body for ``POST /control/drawer/{open,close}`` (no parameters)."""


# ---------------------------------------------------------------------------
# Plate / well sample tracking (Phase 2)
# ---------------------------------------------------------------------------


class WellSample(BaseModel):
    """One well of the currently-loaded plate. Mirrors the device-side
    ``agilent_cytation_server.models.WellSample``.
    """

    well: str
    sample_id: str | None = None
    volume_ul: float | None = Field(default=None, ge=0.0)
    notes: str | None = None


class PlateLoadArgs(BaseModel):
    """Body for ``POST /control/plate/load``."""

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
# ---------------------------------------------------------------------------


class AbsorbanceArgs(BaseModel):
    """Body for ``POST /control/read/absorbance``."""

    wells: list[str] = Field(..., min_length=1, max_length=96)
    wavelength_nm: float = Field(..., ge=200.0, le=999.0)


class FluorescenceArgs(BaseModel):
    """Body for ``POST /control/read/fluorescence``."""

    wells: list[str] = Field(..., min_length=1, max_length=96)
    excitation_nm: float = Field(..., ge=200.0, le=999.0)
    emission_nm: float = Field(..., ge=200.0, le=999.0)
    gain: float = Field(default=50.0, ge=0.0, le=255.0)
    focal_height_mm: float = Field(default=7.0, ge=0.0, le=30.0)


class LuminescenceArgs(BaseModel):
    """Body for ``POST /control/read/luminescence``."""

    wells: list[str] = Field(..., min_length=1, max_length=96)
    integration_time_s: float = Field(default=1.0, ge=0.1, le=60.0)
    gain: float = Field(default=50.0, ge=0.0, le=255.0)


class ReadResult(BaseModel):
    """Response body for read.* skills (``dict[well, value]``)."""

    wells: dict[str, float]


# ---------------------------------------------------------------------------
# Imaging
# ---------------------------------------------------------------------------


class ImagingCaptureArgs(BaseModel):
    """Body for ``POST /control/imaging/capture``."""

    well: str = Field(..., min_length=2, max_length=3)
    channel: str
    focal_height_mm: float = Field(default=5.0, ge=0.0, le=30.0)
    exposure_ms: float = Field(default=10.0, ge=0.01, le=10_000.0)
    gain: float = Field(default=1.0, ge=0.0, le=255.0)


class ImagingCaptureResult(BaseModel):
    """Response body for ``imaging.capture``."""

    well: str
    channel: str
    focal_height_mm: float
    exposure_ms: float
    gain: float
    details: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


register(
    "plate_reader",
    [
        SkillDef(
            name="startup",
            kind="plate_reader",
            description="Connect to the Cytation 5 and initialise the optics + incubator.",
            endpoint="/control/startup",
            args_schema=StartupArgs,
            requires_states=["requires_init", "ready", "dry_run"],
            estimated_duration_s=15.0,
        ),
        SkillDef(
            name="shutdown",
            kind="plate_reader",
            description="Disconnect from the Cytation 5.",
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
                "Register that a plate is physically on the stage. The orchestrator "
                "owns plate_id; the device persists per-well sample/volume state."
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
            description="Read absorbance at one wavelength for the named wells.",
            endpoint="/control/read/absorbance",
            args_schema=AbsorbanceArgs,
            returns_schema=ReadResult,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=15.0,
        ),
        SkillDef(
            name="read.fluorescence",
            kind="plate_reader",
            description="Read fluorescence (ex/em pair) for the named wells.",
            endpoint="/control/read/fluorescence",
            args_schema=FluorescenceArgs,
            returns_schema=ReadResult,
            requires_states=["ready", "dry_run"],
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
            estimated_duration_s=30.0,
        ),
        SkillDef(
            name="imaging.capture",
            kind="plate_reader",
            description=(
                "Capture one image on the imaging path. Channels: brightfield, "
                "phase_contrast, dapi, gfp, rfp, cy5 (device-configured)."
            ),
            endpoint="/control/imaging/capture",
            args_schema=ImagingCaptureArgs,
            returns_schema=ImagingCaptureResult,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=3.0,
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
    "ShutdownArgs",
    "StartupArgs",
    "WellSample",
    "WellUpdateArgs",
]
