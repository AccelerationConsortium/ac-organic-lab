"""Dashboard-only presentation layer.

Houses the types and helpers that the SDK (``ac_organic_lab_skills``)
deliberately does not carry: tile sizing for the equipment grid, location
coordinates on the lab floorplan map, the dashboard-side ``EquipmentSnapshot``
that mirrors ``enabled`` / ``maintenance`` from the registry into the response
shape, and the ``_snapshot()`` helper that composes an SDK snapshot with these
dashboard overrides.

The dashboard re-parses ``equipment.yaml`` for its presentation-only fields
(``tile``, ``location``) so the SDK's ``EquipmentEntry`` stays free of UI
concerns. Re-parsing is cheap (one YAML read at startup) and avoids passing
dashboard concerns through the SDK's public API.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from ac_organic_lab_skills import (
    EquipmentList as SkillEquipmentList,
    EquipmentSnapshot as SkillEquipmentSnapshot,
    Maintenance,
    Registry,
    load_registry,
)


class Location(BaseModel):
    """Position of an equipment on the lab floorplan map.

    Coordinates are percentages (0-100) of the map's width and height so the
    layout is independent of the SVG's pixel size. ``label`` is the human-readable
    spot name (e.g. "North Bench").
    """

    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=100)
    label: str | None = None


class Tile(BaseModel):
    """Tile size for the dashboard equipment grid.

    The platform card lays equipment out on a 4-column CSS grid with fixed-height
    rows. ``w`` is the number of columns the tile spans (1..4) and ``h`` is the
    number of rows it spans (1..4). Default 2x1 = current half-row layout.
    """

    w: int = Field(default=2, ge=1, le=4)
    h: int = Field(default=1, ge=1, le=4)


class DashboardEquipmentOverride(BaseModel):
    """Dashboard-only fields parsed alongside the SDK's ``EquipmentEntry``.

    One per registry entry, keyed by ``id``. Carries presentation fields that
    the SDK ignores. Built by :func:`load_dashboard_overrides`.
    """

    id: str
    location: Location | None = None
    tile: Tile = Field(default_factory=Tile)


class EquipmentSnapshot(SkillEquipmentSnapshot):
    """Dashboard-side snapshot: the SDK's snapshot decorated with presentation
    fields (``tile``, ``location``) and a mirror of the registry entry's
    ``enabled`` / ``maintenance`` fields so the dashboard response shape stays
    informative. Tile UI rendering of the maintenance state is a tracked
    follow-up.
    """

    location: Location | None = None
    tile: Tile = Field(default_factory=Tile)
    enabled: bool = True
    maintenance: Maintenance | None = None


class EquipmentList(BaseModel):
    """Batch dashboard response for ``GET /api/equipment``."""

    equipment: list[EquipmentSnapshot]
    fetched_at: datetime


class AggregatorHealth(BaseModel):
    """Body of ``GET /api/health`` on the dashboard server."""

    status: Literal["healthy"] = "healthy"
    version: str
    equipment_count: int


# -- Composition helpers -----------------------------------------------------


def load_dashboard_overrides(
    path: str | os.PathLike | None = None,
) -> dict[str, DashboardEquipmentOverride]:
    """Re-parse the equipment registry to extract dashboard-only fields.

    Path resolution order matches :func:`ac_organic_lab_skills.load_registry`:
    explicit ``path``, ``LAB_REGISTRY_PATH`` env var, or the first
    ``equipment.yaml`` found by walking up from this module.
    """

    resolved: Path
    if path is not None:
        resolved = Path(path)
    elif os.environ.get("LAB_REGISTRY_PATH"):
        resolved = Path(os.environ["LAB_REGISTRY_PATH"])
    else:
        # Walk up from this file (api/app/presentation.py) looking for the
        # repo-root equipment.yaml. Keeps the dashboard's resolution order
        # symmetric with the SDK's.
        here = Path(__file__).resolve()
        candidate = None
        for ancestor in here.parents:
            c = ancestor / "equipment.yaml"
            if c.exists():
                candidate = c
                break
        if candidate is None:
            raise FileNotFoundError(
                f"Could not locate equipment.yaml in any ancestor of {here}; "
                "pass path= or set LAB_REGISTRY_PATH."
            )
        resolved = candidate

    with resolved.open("r") as f:
        data = yaml.safe_load(f) or {}

    overrides: dict[str, DashboardEquipmentOverride] = {}
    for raw in data.get("equipment", []):
        if not isinstance(raw, dict) or "id" not in raw:
            continue
        overrides[raw["id"]] = DashboardEquipmentOverride.model_validate(raw)
    return overrides


def _snapshot(
    sdk_snapshot: SkillEquipmentSnapshot,
    override: DashboardEquipmentOverride | None,
    registry: Registry,
) -> EquipmentSnapshot:
    """Compose an SDK snapshot with dashboard overrides into the dashboard
    response model.
    """

    entry = registry.by_id(sdk_snapshot.id)
    enabled = entry.enabled if entry is not None else True
    maintenance = entry.maintenance if entry is not None else None
    location = override.location if override is not None else None
    tile = override.tile if override is not None else Tile()

    return EquipmentSnapshot(
        # SDK fields (forwarded verbatim)
        id=sdk_snapshot.id,
        name=sdk_snapshot.name,
        platform=sdk_snapshot.platform,
        kind=sdk_snapshot.kind,
        adapter=sdk_snapshot.adapter,
        status=sdk_snapshot.status,
        fetched_at=sdk_snapshot.fetched_at,
        latency_ms=sdk_snapshot.latency_ms,
        fetch_error=sdk_snapshot.fetch_error,
        base_url=sdk_snapshot.base_url,
        # Dashboard fields
        location=location,
        tile=tile,
        enabled=enabled,
        maintenance=maintenance,
    )


def compose_equipment_list(
    skill_list: SkillEquipmentList,
    overrides: dict[str, DashboardEquipmentOverride],
    registry: Registry,
) -> EquipmentList:
    """Map an SDK ``EquipmentList`` to its dashboard equivalent."""

    return EquipmentList(
        equipment=[
            _snapshot(s, overrides.get(s.id), registry) for s in skill_list.equipment
        ],
        fetched_at=skill_list.fetched_at,
    )


__all__ = [
    "AggregatorHealth",
    "DashboardEquipmentOverride",
    "EquipmentList",
    "EquipmentSnapshot",
    "Location",
    "Maintenance",
    "Tile",
    "_snapshot",
    "compose_equipment_list",
    "load_dashboard_overrides",
    "load_registry",
]
