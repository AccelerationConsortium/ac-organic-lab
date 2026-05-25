"""Typed control wrapper for ``kind=fume_hood`` devices.

Reference device: ``fume-hood-sash-automation`` v1.1+ (FastAPI,
STATUS_SPEC v1.1). Both endpoints sit under ``/control/sash/*`` and
require ``X-Claim-Token`` (the SDK's ``ClaimManager`` handles that
transparently).
"""

from __future__ import annotations

from typing import Any

from ..skill_catalog.fume_hood import MoveArgs, StopArgs
from ..client import EquipmentClient


class FumeHoodClient(EquipmentClient):
    """Typed control wrapper for fume-hood sash actuators."""

    async def move(self, *, position: int) -> Any:
        return await self.command("/control/sash/move", MoveArgs(position=position))

    async def stop(self) -> Any:
        return await self.command("/control/sash/stop", StopArgs())


__all__ = ["FumeHoodClient"]
