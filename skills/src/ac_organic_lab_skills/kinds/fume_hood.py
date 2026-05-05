"""Typed control wrapper for ``kind=fume_hood`` devices.

Reference device: ``fume-hood-sash-automation`` (Flask). Both endpoints
take JSON bodies; ``/move`` requires ``position: 1..5``.
"""

from __future__ import annotations

from typing import Any

from ..catalog.fume_hood import MoveArgs, StopArgs
from ..client import EquipmentClient


class FumeHoodClient(EquipmentClient):
    """Typed control wrapper for fume-hood sash actuators."""

    async def move(self, *, position: int) -> Any:
        return await self.command("/move", MoveArgs(position=position))

    async def stop(self) -> Any:
        return await self.command("/stop", StopArgs())


__all__ = ["FumeHoodClient"]
