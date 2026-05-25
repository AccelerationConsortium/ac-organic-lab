"""Typed control wrapper for ``kind=plate_sealer`` devices.

Method names are snake_case translations of the catalog
:attr:`SkillDef.name` values (e.g. ``seal.start`` -> ``seal_start``). Args
schemas come from :mod:`lab_skills.skill_catalog.plate_sealer` so the
catalog is the single source of truth for argument ranges; this file is the
ergonomic layer that workflow code calls.

Acceptance target (PR 3):

    await lab.role("sealer").seal_start(temperature_c=170, seconds=3.0)
"""

from __future__ import annotations

from typing import Any

from ..skill_catalog.plate_sealer import (
    SealStartArgs,
    SealStopArgs,
    SetSealingTemperatureArgs,
    SetSealingTimeArgs,
    ShutdownArgs,
    StageInArgs,
    StageOutArgs,
    StartupArgs,
)
from ..client import EquipmentClient


class PlateSealerClient(EquipmentClient):
    """Typed control wrapper for STATUS_SPEC ``kind=plate_sealer`` devices.

    Inherits :meth:`status` / :meth:`probe` / :meth:`health` / :meth:`command`
    from :class:`EquipmentClient`. Each typed method below validates its
    arguments via the catalog's Pydantic schema before posting; out-of-range
    values raise :class:`pydantic.ValidationError` *locally*, never reaching
    the device.
    """

    async def startup(
        self,
        *,
        profile: str | None = None,
    ) -> Any:
        """Connect and (optionally) load a sealing profile."""

        return await self.command(
            "/control/startup",
            StartupArgs(profile=profile),
        )

    async def shutdown(self) -> Any:
        return await self.command("/control/shutdown", ShutdownArgs())

    async def seal_start(
        self,
        *,
        temperature_c: int | None = None,
        seconds: float | None = None,
    ) -> Any:
        """Start a seal cycle, optionally updating the setpoints first."""

        return await self.command(
            "/control/seal/start",
            SealStartArgs(temperature_c=temperature_c, seconds=seconds),
        )

    async def seal_stop(self) -> Any:
        return await self.command("/control/seal/stop", SealStopArgs())

    async def set_sealing_temperature(
        self,
        *,
        temperature_c: int,
    ) -> Any:
        return await self.command(
            "/control/seal/temperature",
            SetSealingTemperatureArgs(temperature_c=temperature_c),
        )

    async def set_sealing_time(
        self,
        *,
        seconds: float,
    ) -> Any:
        return await self.command(
            "/control/seal/time",
            SetSealingTimeArgs(seconds=seconds),
        )

    async def stage_in(self) -> Any:
        return await self.command("/control/stage/in", StageInArgs())

    async def stage_out(self) -> Any:
        return await self.command("/control/stage/out", StageOutArgs())


__all__ = ["PlateSealerClient"]
