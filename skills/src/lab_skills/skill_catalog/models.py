"""Static :class:`SkillDef` and runtime :class:`Skill` shapes.

Mirrors ``docs/SKILLS_CATALOG.md`` exactly. The ``args_schema`` / ``returns_schema``
fields hold Pydantic *classes*, not instances, so each SkillDef carries the
type information that workflow code, MCP, and plan-validation use to typecheck
arguments before any HTTP round-trip.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..models import EquipmentKind, EquipmentState


class SkillDef(BaseModel):
    """Static description of a capability - one entry per ``(kind, action)`` pair.

    Lives in :data:`SKILL_REGISTRY`; does not depend on any running device.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    """Hand-curated dotted action name, e.g. ``"seal.start"``. Stable across
    SDK releases; renaming is a breaking change."""

    kind: EquipmentKind
    """Which equipment kind exposes this capability."""

    description: str
    """One-sentence human-readable summary, suitable for display in agent
    prompts and dashboard tooltips."""

    endpoint: str
    """Path on the device that implements the capability, e.g.
    ``"/control/seal/start"``. May be a non-spec path for legacy devices that
    have not yet migrated to STATUS_SPEC v1.x (e.g. ``"/init"`` on
    ``filter_every_well``); the catalog records the path as the device
    currently ships it."""

    method: Literal["GET", "POST"] = "POST"

    args_schema: type[BaseModel]
    """Pydantic class for the request body. Use a no-field model for
    parameter-less actions."""

    returns_schema: type[BaseModel] | None = None
    """Optional Pydantic class for the response body."""

    requires_states: list[EquipmentState] = Field(default_factory=list)
    """States that permit this skill on a v1.0 (pre-allowed_actions) device.
    Used as a fallback when ``status.allowed_actions`` is empty / missing."""

    requires_components: dict[str, str] = Field(default_factory=dict)
    """Fine-grained gate: ``{component_name: required_state}``. **All**
    listed components must report that exact ``state`` for the skill to
    be considered available. Layered on top of ``allowed_actions`` /
    ``requires_states`` as an AND condition.

    Use this when the device's coarse ``equipment_status`` (or its
    declared ``allowed_actions``) is too permissive for a specific action.
    Example: ``seal.start`` on a plate sealer requires
    ``equipment_status == "ready"`` *and* ``components["heater"].state ==
    "stable"`` — the heater check is the bit ``requires_components``
    expresses.

    Empty dict (the default) means "no per-component constraint", so
    existing SkillDefs are unaffected."""

    estimated_duration_s: float | None = None
    """Rough cost hint for planners. ``None`` means "unknown / not estimable"."""


class Skill(BaseModel):
    """Runtime view of a :class:`SkillDef` bound to a project role.

    Returned by ``await session.skills()``; do not construct directly. The
    ``available`` flag and ``reason`` are computed from the live device
    snapshot at the time ``skills()`` is called.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    role: str
    equipment_id: str
    kind: EquipmentKind
    description: str
    args_schema: type[BaseModel]
    estimated_duration_s: float | None = None

    available: bool
    reason: str | None = None
    """Populated when ``available=False``. Examples: ``"device is busy"``,
    ``"device requires init"``, ``"device under maintenance"``,
    ``"device unreachable"``."""


__all__ = ["Skill", "SkillDef"]
