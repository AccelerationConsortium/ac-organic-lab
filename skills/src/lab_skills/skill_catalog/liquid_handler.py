"""Skill catalog entries for ``kind=liquid_handler``.

Reference device: ``opentrons-server`` (the OT-2 gateway on
``sdl2-pc-03-cytation:8020``). The protocol-execution actions
(setup / home / aspirate / dispense / pick-up-tip / drop-tip /
move-labware / pause / resume / reconcile) are reachable today via
``/control/*`` on the device but aren't catalogued yet — they take
typed protocol arguments the SDK has no schemas for. They'll land
once the catalog grows protocol-aware Pydantic models.

What this module *does* register is the deck-light toggle: a
convenience control with no equipment-state preconditions (the
device advertises ``lights.set`` in ``allowed_actions`` whenever the
robot is reachable). It is intentionally *not* behind
``CONTROL_PASSWORD`` per ``web/src/lib/tile-policy.ts`` — same class
as camera PTZ and "light" outlets on a power strip.
"""

from __future__ import annotations

from typing import Dict, Optional, Union

from pydantic import BaseModel, Field

from .models import SkillDef
from .registry import register


class LightsSetArgs(BaseModel):
    """Body for ``POST /control/lights``.

    Mirrors the upstream Opentrons HTTP API (``POST /robot/lights``).
    """

    on: bool


class DeckDeclareArgs(BaseModel):
    """Body for ``POST /control/deck/declare``.

    Sets the operator/recipe-declared deck layout on the OT-2 gateway (the
    source of truth that retires the dashboard's ``deck_layouts.json`` stopgap).
    Each slot value is a labware ``load_name`` (preferred), a bare ``kind``
    string (e.g. ``"96-well"``, ``"tiprack"``, ``"waste"``), a
    ``{"load_name"|"kind": ...}`` object, or ``null`` to clear that slot. An
    empty ``slots`` map clears the whole declaration.
    """

    slots: Dict[str, Optional[Union[str, Dict[str, str]]]] = Field(default_factory=dict)


register(
    "liquid_handler",
    [
        SkillDef(
            name="lights.set",
            kind="liquid_handler",
            description="Turn the OT-2 deck lights on or off. Convenience control.",
            endpoint="/control/lights",
            args_schema=LightsSetArgs,
            # Empty: the device exposes lights.set in allowed_actions
            # regardless of equipment_status. Don't gate on the SDK side
            # either — the operator might want lights on while the robot
            # is still in requires_init.
            requires_states=[],
            estimated_duration_s=0.2,
        ),
        SkillDef(
            name="deck.declare",
            kind="liquid_handler",
            description=(
                "Declare the deck layout (operator/recipe intent). Metadata only "
                "— no hardware motion. Merged with observed sources on /status."
            ),
            endpoint="/control/deck/declare",
            args_schema=DeckDeclareArgs,
            # Like lights.set: the gateway advertises deck.declare in
            # allowed_actions whenever it is reachable (any state except
            # EXTERNAL_CONTROL), so declaring works even in requires_init.
            requires_states=[],
            estimated_duration_s=0.2,
        ),
    ],
)


__all__ = ["LightsSetArgs", "DeckDeclareArgs"]
