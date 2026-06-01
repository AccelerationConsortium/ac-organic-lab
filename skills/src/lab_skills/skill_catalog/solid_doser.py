"""Skill catalog entries for ``kind=solid_doser``.

Reference device: :mod:`dose_every_well` (PlateDoser). On STATUS_SPEC v1.1;
all mutating control is under ``/control/*`` (claim-gated), per
``dose_every_well/src/dose_every_well/api/server.py``:

* ``POST /control/startup?config_name=``   - initialise from named config
* ``POST /control/shutdown``               - safe shutdown + return to home
* ``POST /control/plate/set``              - configure plate definition + origin
* ``POST /control/plate/load``             - load plate onto balance
* ``POST /control/plate/unload``           - unload plate from balance
* ``POST /control/dose/well``              - dose one well to a target mass
* ``POST /control/dose/multiple``          - dose multiple wells
* ``POST /control/dose/row``               - dose an entire row
* ``POST /control/dose/column``            - dose an entire column
* ``POST /control/home``                   - return all components to home
* ``POST /control/tare``                   - tare the balance
* ``POST /control/calibrate/flow-rate``    - calibrate solid-doser flow rate

Catalog records each as a separate :class:`SkillDef`. ``Skill.name`` matches
the device's ``allowed_actions``; the dashboard/SDK acquire a claim before
POSTing (the device hard-enforces ``X-Claim-Token`` on ``/control/*``).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .models import SkillDef
from .registry import register


class StartupArgs(BaseModel):
    config_name: str = Field(
        default="with_cnc_solid_doser",
        description="Configuration name to load (e.g. 'standalone', 'with_cnc_solid_doser').",
    )


class ShutdownArgs(BaseModel):
    """Body for ``POST /shutdown`` (no parameters)."""


class PlateSetArgs(BaseModel):
    definition: str = Field(description="Plate definition name (e.g. '96-well-standard').")
    origin_x: float = Field(default=0.0, description="X coordinate of well A1 in mm.")
    origin_y: float = Field(default=0.0, description="Y coordinate of well A1 in mm.")


class PlateLoadArgs(BaseModel):
    plate_definition: str | None = None
    origin_x: float | None = None
    origin_y: float | None = None


class PlateUnloadArgs(BaseModel):
    """Body for ``POST /plate/unload`` (no parameters)."""


class DoseWellArgs(BaseModel):
    well: str = Field(description="Well identifier, e.g. 'A1'.")
    target_mg: float = Field(gt=0, description="Target mass in milligrams.")
    verify: bool = Field(default=True, description="Verify the dose with the balance.")
    use_pid: bool = Field(default=False, description="Use PID feedback control.")


class DoseMultipleArgs(BaseModel):
    well_targets: dict[str, float] = Field(
        description="Map of well names to target masses (mg)."
    )
    verify: bool = True
    use_pid: bool = False


class DoseRowArgs(BaseModel):
    row: str = Field(description="Row letter, A-H for a 96-well plate.")
    target_mg: float = Field(gt=0, description="Target mass per well in milligrams.")
    verify: bool = True
    use_pid: bool = False


class DoseColumnArgs(BaseModel):
    column: int = Field(ge=1, le=24, description="Column number, 1-12 for a 96-well plate.")
    target_mg: float = Field(gt=0, description="Target mass per well in milligrams.")
    verify: bool = True
    use_pid: bool = False


class HomeArgs(BaseModel):
    """Body for ``POST /control/home`` (no parameters)."""


class TareArgs(BaseModel):
    """Body for ``POST /control/tare`` (no parameters)."""


class CalibrateFlowRateArgs(BaseModel):
    duration: float = Field(default=5.0, gt=0, description="Dispense duration in seconds.")
    gate_position: float | None = Field(
        default=None, ge=0, le=35, description="Gate servo position."
    )


register(
    "solid_doser",
    [
        SkillDef(
            name="startup",
            kind="solid_doser",
            description="Initialize PlateDoser system from a named configuration.",
            endpoint="/control/startup",
            args_schema=StartupArgs,
            requires_states=["requires_init", "ready", "dry_run"],
            estimated_duration_s=10.0,
        ),
        SkillDef(
            name="shutdown",
            kind="solid_doser",
            description="Safely shut down PlateDoser; unload any plate and home all components.",
            endpoint="/control/shutdown",
            args_schema=ShutdownArgs,
            requires_states=["ready", "busy", "degraded", "dry_run"],
            estimated_duration_s=5.0,
        ),
        SkillDef(
            name="plate.set",
            kind="solid_doser",
            description="Set the current plate definition and origin coordinates.",
            endpoint="/control/plate/set",
            args_schema=PlateSetArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=0.1,
        ),
        SkillDef(
            name="plate.load",
            kind="solid_doser",
            description="Load a plate onto the balance.",
            endpoint="/control/plate/load",
            args_schema=PlateLoadArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=8.0,
        ),
        SkillDef(
            name="plate.unload",
            kind="solid_doser",
            description="Unload the current plate from the balance.",
            endpoint="/control/plate/unload",
            args_schema=PlateUnloadArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=8.0,
        ),
        SkillDef(
            name="dose.well",
            kind="solid_doser",
            description="Dose one well to a target mass.",
            endpoint="/control/dose/well",
            args_schema=DoseWellArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=15.0,
        ),
        SkillDef(
            name="dose.multiple",
            kind="solid_doser",
            description="Dose multiple wells with explicit per-well target masses.",
            endpoint="/control/dose/multiple",
            args_schema=DoseMultipleArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=None,
        ),
        SkillDef(
            name="dose.row",
            kind="solid_doser",
            description="Dose an entire row to the same target mass per well.",
            endpoint="/control/dose/row",
            args_schema=DoseRowArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=None,
        ),
        SkillDef(
            name="dose.column",
            kind="solid_doser",
            description="Dose an entire column to the same target mass per well.",
            endpoint="/control/dose/column",
            args_schema=DoseColumnArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=None,
        ),
        SkillDef(
            name="home",
            kind="solid_doser",
            description="Return gantry, solid doser, and plate weigher to home positions.",
            endpoint="/control/home",
            args_schema=HomeArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=5.0,
        ),
        SkillDef(
            name="tare",
            kind="solid_doser",
            description="Tare the balance (zero its current reading).",
            endpoint="/control/tare",
            args_schema=TareArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=2.0,
        ),
        SkillDef(
            name="calibrate.flow_rate",
            kind="solid_doser",
            description="Calibrate the solid doser's mass-per-second flow rate.",
            endpoint="/control/calibrate/flow-rate",
            args_schema=CalibrateFlowRateArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=20.0,
        ),
    ],
)


__all__ = [
    "CalibrateFlowRateArgs",
    "DoseColumnArgs",
    "DoseMultipleArgs",
    "DoseRowArgs",
    "DoseWellArgs",
    "HomeArgs",
    "PlateLoadArgs",
    "PlateSetArgs",
    "PlateUnloadArgs",
    "ShutdownArgs",
    "StartupArgs",
    "TareArgs",
]
