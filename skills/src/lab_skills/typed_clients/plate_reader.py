"""Typed control wrapper for ``kind=plate_reader`` devices.

Reference device: ``agilent-cytation-server`` (STATUS_SPEC v1.2). Method
names are snake_case translations of the catalog :attr:`SkillDef.name`
values (e.g. ``read.absorbance`` -> ``read_absorbance``). Args schemas
come from :mod:`lab_skills.skill_catalog.plate_reader` so the catalog is
the single source of truth for argument ranges.

Acceptance target::

    await lab.role("plate_reader").read_absorbance(
        wells=["A1"], wavelength_nm=600.0
    )
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal
from urllib.parse import quote

from ..client import EquipmentClient
from ..skill_catalog.plate_reader import (
    AbsorbanceArgs,
    DrawerArgs,
    FluorescenceArgs,
    ImagingCaptureArgs,
    LuminescenceArgs,
    PlateLoadArgs,
    PlateUnloadArgs,
    RunGetArgs,
    RunRecord,
    RunStartArgs,
    RunStopArgs,
    ShakeArgs,
    ShakeStopArgs,
    ShutdownArgs,
    StartupArgs,
    TemperatureArgs,
    TemperatureStopArgs,
    WellSample,
    WellUpdateArgs,
)


class PlateReaderClient(EquipmentClient):
    """Typed control wrapper for STATUS_SPEC ``kind=plate_reader`` devices.

    Inherits :meth:`status` / :meth:`probe` / :meth:`health` / :meth:`command`
    from :class:`EquipmentClient`. Each typed method validates its arguments
    via the catalog's Pydantic schema before posting; out-of-range values
    raise :class:`pydantic.ValidationError` locally, never reaching the
    device.
    """

    async def startup(self) -> Any:
        return await self.command("/control/startup", StartupArgs())

    async def shutdown(self) -> Any:
        return await self.command("/control/shutdown", ShutdownArgs())

    async def drawer_open(self) -> Any:
        return await self.command("/control/drawer/open", DrawerArgs())

    async def drawer_close(self) -> Any:
        return await self.command("/control/drawer/close", DrawerArgs())

    async def plate_load(
        self,
        *,
        plate_id: str,
        model: str | None = None,
        wells: list[WellSample] | None = None,
    ) -> Any:
        return await self.command(
            "/control/plate/load",
            PlateLoadArgs(plate_id=plate_id, model=model, wells=wells),
        )

    async def plate_unload(self) -> Any:
        return await self.command("/control/plate/unload", PlateUnloadArgs())

    async def well_update(
        self,
        *,
        well: str,
        sample_id: str | None = None,
        volume_ul: float | None = None,
        notes: str | None = None,
        clear_sample_id: bool = False,
        clear_notes: bool = False,
    ) -> Any:
        return await self.command(
            "/control/well/update",
            WellUpdateArgs(
                well=well,
                sample_id=sample_id,
                volume_ul=volume_ul,
                notes=notes,
                clear_sample_id=clear_sample_id,
                clear_notes=clear_notes,
            ),
        )

    async def read_absorbance(
        self,
        *,
        wells: list[str],
        wavelength_nm: float,
    ) -> Any:
        return await self.command(
            "/control/read/absorbance",
            AbsorbanceArgs(wells=wells, wavelength_nm=wavelength_nm),
        )

    async def read_fluorescence(
        self,
        *,
        wells: list[str],
        excitation_nm: float,
        emission_nm: float,
        focal_height_mm: float = 7.0,
    ) -> Any:
        return await self.command(
            "/control/read/fluorescence",
            FluorescenceArgs(
                wells=wells,
                excitation_nm=excitation_nm,
                emission_nm=emission_nm,
                focal_height_mm=focal_height_mm,
            ),
        )

    async def read_luminescence(
        self,
        *,
        wells: list[str],
        focal_height_mm: float = 7.0,
        integration_time_s: float = 1.0,
    ) -> Any:
        return await self.command(
            "/control/read/luminescence",
            LuminescenceArgs(
                wells=wells,
                focal_height_mm=focal_height_mm,
                integration_time_s=integration_time_s,
            ),
        )

    async def imaging_capture(
        self,
        *,
        well: str,
        channel: str,
        objective: str | None = None,
        focal_height_mm: float = 5.0,
        exposure_ms: float = 10.0,
        gain: float = 0.0,
        led_intensity: int = 10,
        autofocus: bool = False,
        auto_exposure: bool = False,
    ) -> Any:
        return await self.command(
            "/control/imaging/capture",
            ImagingCaptureArgs(
                well=well,
                channel=channel,
                objective=objective,
                focal_height_mm=focal_height_mm,
                exposure_ms=exposure_ms,
                gain=gain,
                led_intensity=led_intensity,
                autofocus=autofocus,
                auto_exposure=auto_exposure,
            ),
        )

    async def incubator_set_temperature(self, *, celsius: float) -> Any:
        return await self.command(
            "/control/incubator/set_temperature",
            TemperatureArgs(celsius=celsius),
        )

    async def incubator_stop(self) -> Any:
        return await self.command(
            "/control/incubator/stop", TemperatureStopArgs()
        )

    async def shake_start(
        self,
        *,
        pattern: Literal["orbital", "linear"] = "orbital",
        displacement_mm: int = 3,
    ) -> Any:
        return await self.command(
            "/control/shake/start",
            ShakeArgs(pattern=pattern, displacement_mm=displacement_mm),
        )

    async def shake_stop(self) -> Any:
        return await self.command("/control/shake/stop", ShakeStopArgs())

    # ---- timed runs --------------------------------------------------

    async def run_start(
        self,
        *,
        duration_s: float,
        interval_s: float,
        measurements: Sequence[Mapping[str, Any]],
        temperature_c: float | None = None,
        shake: Mapping[str, Any] | None = None,
        **options: Any,
    ) -> Any:
        """Hold ``temperature_c`` for ``duration_s`` and measure every
        ``interval_s``; the device runs the schedule and this returns at once.

        ``measurements`` are read bodies with a ``type`` (``absorbance``,
        ``fluorescence``, ``luminescence`` or ``imaging``). ``options`` are the
        remaining :class:`RunStartArgs` fields (``wait_for_setpoint``,
        ``on_complete``, ``label``, ...), validated locally like the rest.
        """

        return await self.command(
            "/control/run/start",
            RunStartArgs(
                duration_s=duration_s,
                interval_s=interval_s,
                measurements=list(measurements),
                temperature_c=temperature_c,
                shake=shake,
                **options,
            ),
            response_schema=RunRecord,
        )

    async def run_stop(self) -> Any:
        return await self.command("/control/run/stop", RunStopArgs(), response_schema=RunRecord)

    async def run_list(self) -> Any:
        return await self.command("/control/runs", method="GET")

    async def run_get(self, run_id: str) -> Any:
        RunGetArgs(run_id=run_id)
        return await self.command(
            f"/control/runs/{quote(run_id, safe='')}", method="GET", response_schema=RunRecord
        )


__all__ = ["PlateReaderClient"]
