"""Skill catalog entries for ``kind=fume_hood``.

Reference device: :mod:`fume-hood-sash-automation`. Flask-based, not yet on
STATUS_SPEC v1.x; current shape from
``fume-hood-sash-automation/src/hood_sash_automation/api/api_service.py``:

* ``POST /move`` body ``{position: 1..5}`` - move sash to a preset position
* ``POST /stop``                            - stop a running movement

When the device migrates to STATUS_SPEC v1.x with spec-conformant
``/control/*`` endpoints, only this catalog file changes; the typed wrapper
in v0.3 keeps the same Python signatures.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .models import SkillDef
from .registry import register


class MoveArgs(BaseModel):
    """Body for ``POST /move``."""

    position: int = Field(ge=1, le=5, description="Sash preset position, 1 (closed) - 5 (full open).")


class StopArgs(BaseModel):
    """Body for ``POST /stop`` (no parameters)."""


register(
    "fume_hood",
    [
        SkillDef(
            name="move",
            kind="fume_hood",
            description="Move the sash to a preset position (1 closed - 5 fully open).",
            endpoint="/move",
            args_schema=MoveArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=4.0,
        ),
        SkillDef(
            name="stop",
            kind="fume_hood",
            description="Stop any in-progress sash movement.",
            endpoint="/stop",
            args_schema=StopArgs,
            requires_states=["ready", "busy", "degraded", "dry_run"],
            estimated_duration_s=0.5,
        ),
    ],
)


__all__ = ["MoveArgs", "StopArgs"]
