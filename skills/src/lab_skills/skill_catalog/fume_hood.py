"""Skill catalog entries for ``kind=fume_hood``.

Reference device: ``fume-hood-sash-automation`` v1.1+ (FastAPI,
STATUS_SPEC v1.1). Endpoints:

* ``POST /control/sash/move`` body ``{position: 1..5}`` - move to preset.
* ``POST /control/sash/stop``                            - stop movement.

Both require ``X-Claim-Token`` obtained via ``POST /control/claim``; the
dashboard's control passthrough and ``ClaimManager`` handle that
transparently.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .models import SkillDef
from .registry import register


class MoveArgs(BaseModel):
    """Body for ``POST /control/sash/move``."""

    position: int = Field(ge=1, le=5, description="Sash preset position, 1 (closed) - 5 (full open).")


class StopArgs(BaseModel):
    """Body for ``POST /control/sash/stop`` (no parameters)."""


register(
    "fume_hood",
    [
        SkillDef(
            name="sash.move",
            kind="fume_hood",
            description="Move the sash to a preset position (1 closed - 5 fully open).",
            endpoint="/control/sash/move",
            args_schema=MoveArgs,
            requires_states=["ready", "requires_init", "dry_run"],
            estimated_duration_s=4.0,
        ),
        SkillDef(
            name="sash.stop",
            kind="fume_hood",
            description="Stop any in-progress sash movement.",
            endpoint="/control/sash/stop",
            args_schema=StopArgs,
            requires_states=["ready", "busy", "degraded", "dry_run"],
            estimated_duration_s=0.5,
        ),
    ],
)


__all__ = ["MoveArgs", "StopArgs"]
