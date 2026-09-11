"""Process-wide :class:`SkillDef` registry.

A flat ``dict[EquipmentKind, list[SkillDef]]`` populated at import time by
the per-kind modules. The registry is intentionally a global: there is one
catalog per Python process, and per-kind modules contribute their entries
through :func:`register` rather than mutating it directly.

Workflow code does not normally read this registry; it goes through
``await session.skills()`` which composes catalog entries with live device
state. Tests and the (future) MCP server companion read it directly.
"""

from __future__ import annotations

from ..models import EquipmentKind
from .models import SkillDef

SKILL_REGISTRY: dict[EquipmentKind, list[SkillDef]] = {}


def register(kind: EquipmentKind, defs: list[SkillDef]) -> None:
    """Register a list of :class:`SkillDef`s for an equipment kind.

    Idempotent: re-registering the same ``kind`` (e.g. when the catalog
    package is re-imported after a hot reload in a test) replaces the prior
    entry rather than accumulating duplicates. All entries must declare the
    same ``kind`` they are registered under.
    """

    for d in defs:
        if d.kind != kind:
            raise ValueError(
                f"SkillDef {d.name!r} declares kind={d.kind!r} but was "
                f"registered under kind={kind!r}"
            )
    SKILL_REGISTRY[kind] = list(defs)


def skills_for(kind: EquipmentKind, equipment_id: str | None = None) -> list[SkillDef]:
    """Return the registered :class:`SkillDef`s for an equipment kind.

    Pass equipment_id for equipment-specific capabilities such as Lumastir;
    omitting it never adds those capabilities to a generic kind.

    Returns an empty list (not ``None``) when the kind has no registered
    capabilities. Reads return a copy so callers cannot mutate the registry
    by accident.
    """

    if kind == "other" and equipment_id == "lumastir":
        from .lumastir import LUMASTIR_SKILLS

        return list(LUMASTIR_SKILLS)
    return list(SKILL_REGISTRY.get(kind, []))


__all__ = ["SKILL_REGISTRY", "register", "skills_for"]
