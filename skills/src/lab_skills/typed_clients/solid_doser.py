"""Typed control wrapper for ``kind=solid_doser`` devices.

Reference device: ``dose_every_well`` (PlateDoser). The legacy device
expects ``config_name`` as a query parameter on ``/startup`` and JSON bodies
for the dosing endpoints.
"""

from __future__ import annotations

from typing import Any

from ..skill_catalog.solid_doser import (
    CalibrateFlowRateArgs,
    DoseColumnArgs,
    DoseMultipleArgs,
    DoseRowArgs,
    DoseWellArgs,
    HomeArgs,
    PlateLoadArgs,
    PlateSetArgs,
    PlateUnloadArgs,
    ShutdownArgs,
    StartupArgs,
    TareArgs,
)
from ..client import EquipmentClient


class SolidDoserClient(EquipmentClient):
    """Typed control wrapper for solid-doser devices."""

    async def startup(self, *, config_name: str = "with_cnc_solid_doser") -> Any:
        args = StartupArgs(config_name=config_name)
        return await self.command(
            f"/startup?config_name={args.config_name}",
            None,
        )

    async def shutdown(self) -> Any:
        return await self.command("/shutdown", ShutdownArgs())

    async def plate_set(
        self,
        *,
        definition: str,
        origin_x: float = 0.0,
        origin_y: float = 0.0,
    ) -> Any:
        return await self.command(
            "/plate/set",
            PlateSetArgs(
                definition=definition, origin_x=origin_x, origin_y=origin_y
            ),
        )

    async def plate_load(
        self,
        *,
        plate_definition: str | None = None,
        origin_x: float | None = None,
        origin_y: float | None = None,
    ) -> Any:
        return await self.command(
            "/plate/load",
            PlateLoadArgs(
                plate_definition=plate_definition,
                origin_x=origin_x,
                origin_y=origin_y,
            ),
        )

    async def plate_unload(self) -> Any:
        return await self.command("/plate/unload", PlateUnloadArgs())

    async def dose_well(
        self,
        *,
        well: str,
        target_mg: float,
        verify: bool = True,
        use_pid: bool = False,
    ) -> Any:
        return await self.command(
            "/dose/well",
            DoseWellArgs(
                well=well,
                target_mg=target_mg,
                verify=verify,
                use_pid=use_pid,
            ),
        )

    async def dose_multiple(
        self,
        *,
        well_targets: dict[str, float],
        verify: bool = True,
        use_pid: bool = False,
    ) -> Any:
        return await self.command(
            "/dose/multiple",
            DoseMultipleArgs(
                well_targets=well_targets, verify=verify, use_pid=use_pid
            ),
        )

    async def dose_row(
        self,
        *,
        row: str,
        target_mg: float,
        verify: bool = True,
        use_pid: bool = False,
    ) -> Any:
        return await self.command(
            "/dose/row",
            DoseRowArgs(
                row=row, target_mg=target_mg, verify=verify, use_pid=use_pid
            ),
        )

    async def dose_column(
        self,
        *,
        column: int,
        target_mg: float,
        verify: bool = True,
        use_pid: bool = False,
    ) -> Any:
        return await self.command(
            "/dose/column",
            DoseColumnArgs(
                column=column,
                target_mg=target_mg,
                verify=verify,
                use_pid=use_pid,
            ),
        )

    async def home(self) -> Any:
        return await self.command("/control/home", HomeArgs())

    async def tare(self) -> Any:
        return await self.command("/control/tare", TareArgs())

    async def calibrate_flow_rate(
        self,
        *,
        duration: float = 5.0,
        gate_position: float | None = None,
    ) -> Any:
        return await self.command(
            "/calibrate/flow-rate",
            CalibrateFlowRateArgs(duration=duration, gate_position=gate_position),
        )


__all__ = ["SolidDoserClient"]
