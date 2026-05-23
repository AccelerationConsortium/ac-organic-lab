"""Typed control wrapper for ``kind=press`` devices.

Reference device: ``filter_every_well`` (Waters PP96). The legacy device
expects ``hold_time`` and ``smooth`` as query parameters rather than a JSON
body, so the typed methods serialise those args into the URL query string
rather than the body. When the device migrates to STATUS_SPEC v1.x and
adopts proper ``/control/*`` POSTs with JSON bodies, the catalog file is
the only place that needs to change; the method signatures below stay
stable.
"""

from __future__ import annotations

from typing import Any

from ..skill_catalog.press import (
    InitArgs,
    PlateMoveArgs,
    PressDownArgs,
    PressUpArgs,
    StopArgs,
)
from ..client import EquipmentClient


class PressClient(EquipmentClient):
    """Typed control wrapper for filtration-press devices."""

    async def init(self) -> Any:
        return await self.command("/init", InitArgs())

    async def stop(self) -> Any:
        return await self.command("/stop", StopArgs())

    async def press_up(self, *, hold_time: float | None = None) -> Any:
        # Validate via the catalog schema, then forward as query string -
        # the legacy device reads `hold_time` from the URL, not the body.
        # Default (2.0 s) comes from PressUpArgs when hold_time is omitted.
        args = (
            PressUpArgs() if hold_time is None else PressUpArgs(hold_time=hold_time)
        )
        return await self.command(
            f"/press/up?hold_time={args.hold_time}",
            None,
        )

    async def press_down(self, *, hold_time: float | None = None) -> Any:
        # Default (5.0 s) comes from PressDownArgs when hold_time is omitted.
        args = (
            PressDownArgs() if hold_time is None else PressDownArgs(hold_time=hold_time)
        )
        return await self.command(
            f"/press/down?hold_time={args.hold_time}",
            None,
        )

    async def plate_in(self, *, smooth: bool = True) -> Any:
        args = PlateMoveArgs(smooth=smooth)
        return await self.command(
            f"/plate/in?smooth={'true' if args.smooth else 'false'}",
            None,
        )

    async def plate_out(self, *, smooth: bool = True) -> Any:
        args = PlateMoveArgs(smooth=smooth)
        return await self.command(
            f"/plate/out?smooth={'true' if args.smooth else 'false'}",
            None,
        )


__all__ = ["PressClient"]
