"""Dashboard-only presentation layer.

Houses the types and helpers that the SDK (``lab_skills``)
deliberately does not carry: location coordinates on the lab floorplan map,
the dashboard-side ``EquipmentSnapshot`` that mirrors ``enabled`` /
``maintenance`` from the registry into the response shape, and the
``_snapshot()`` helper that composes an SDK snapshot with these dashboard
overrides.

Tile sizing (``tile``) is now derived from the equipment entry's ``tiles``
dict keyed by the resolved section id from ``platforms.yaml``.  Platform
assignment is also resolved at compose-time from ``PlatformsConfig``.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from .events import derive_v2_fields, snapshot_activity
from lab_skills import (
    CameraConfig,
    EquipmentList as SkillEquipmentList,
    EquipmentSnapshot as SkillEquipmentSnapshot,
    Maintenance,
    PillConfig,
    PlugConfig,
    PlatformsConfig,
    Registry,
    Tile,
    load_registry,
)


class Location(BaseModel):
    """Position of an equipment on the lab floorplan map."""

    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=100)
    label: str | None = None


class DashboardEquipmentOverride(BaseModel):
    """Dashboard-only fields parsed alongside the SDK's ``EquipmentEntry``.

    Carries presentation fields the SDK ignores.  Currently only ``location``
    (tile sizing moved to ``EquipmentEntry.tiles``).
    """

    id: str
    location: Location | None = None


class EquipmentSnapshot(SkillEquipmentSnapshot):
    """Dashboard-side snapshot: the SDK's snapshot decorated with presentation
    fields (``tile``, ``location``, ``pill``) and a mirror of the registry
    entry's ``enabled`` / ``maintenance`` / ``camera`` / ``plug`` fields.
    """

    location: Location | None = None
    tile: Tile = Field(default_factory=Tile)
    pill: PillConfig = Field(default_factory=PillConfig)
    enabled: bool = True
    maintenance: Maintenance | None = None
    camera: CameraConfig | None = None
    plug: PlugConfig | None = None
    # Display-only Tailscale IP from the registry entry (None → not shown).
    tailscale_ip: str | None = None
    # Server-resolved activity (STATUS_SPEC v1.2 §2.3): device-reported when
    # available, else the §2.3 state invariants, else a per-kind component
    # sniff (§2.3.2, non-normative). Resolved by the SAME function the poll
    # loop records with (events.snapshot_activity), so live tiles and the
    # stored activity_transition series can never disagree. `unknown` when
    # unreachable or genuinely undeterminable — never a false `idle`.
    activity: Literal["idle", "running", "unknown"] = "unknown"
    # How `activity` was determined: device | status | components | none.
    activity_source: Literal["device", "status", "components", "none"] = "none"
    # v2 vocabulary, reader-side (STATUS_SPEC Appendix B.2 projection —
    # non-normative, deterministic from equipment_status + registry). Carries
    # no new information yet; exists so readers can speak the v2 vocabulary
    # before devices report it natively. `health` answers "what's the
    # device's standing"; `mode` answers "what is it operated for";
    # `simulated` means nothing physical happens and all data is synthetic.
    health: Literal[
        "healthy", "degraded", "error", "e_stopped", "requires_init", "unknown"
    ] = "unknown"
    mode: Literal["production", "develop", "maintenance"] = "production"
    simulated: bool = False


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

    Path resolution order matches :func:`lab_skills.load_registry`.
    """

    resolved: Path
    if path is not None:
        resolved = Path(path)
    elif os.environ.get("LAB_REGISTRY_PATH"):
        resolved = Path(os.environ["LAB_REGISTRY_PATH"])
    else:
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
    platforms_config: PlatformsConfig,
) -> EquipmentSnapshot:
    """Compose an SDK snapshot with dashboard overrides."""

    entry = registry.by_id(sdk_snapshot.id)
    eq_to_section = platforms_config.equipment_to_section_id()
    section_id = eq_to_section.get(sdk_snapshot.id)

    enabled = entry.enabled if entry is not None else True
    maintenance = entry.maintenance if entry is not None else None
    camera = entry.camera if entry is not None else None
    plug = entry.plug if entry is not None else None
    location = override.location if override is not None else None

    default_tile = Tile()
    if entry is not None and section_id is not None:
        tile = entry.tiles.get(section_id, default_tile)
    else:
        tile = default_tile

    pill = entry.pills if entry is not None else PillConfig()

    activity, activity_source = snapshot_activity(sdk_snapshot)
    health, mode, simulated = derive_v2_fields(
        sdk_snapshot.status,
        adapter=sdk_snapshot.adapter,
        in_maintenance=maintenance is not None,
    )

    return EquipmentSnapshot(
        # SDK fields
        id=sdk_snapshot.id,
        name=sdk_snapshot.name,
        platform=section_id,
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
        pill=pill,
        enabled=enabled,
        maintenance=maintenance,
        camera=camera,
        plug=plug,
        tailscale_ip=entry.tailscale_ip if entry is not None else None,
        activity=activity,
        activity_source=activity_source,
        health=health,
        mode=mode,
        simulated=simulated,
    )


def compose_equipment_list(
    skill_list: SkillEquipmentList,
    overrides: dict[str, DashboardEquipmentOverride],
    registry: Registry,
    platforms_config: PlatformsConfig,
) -> EquipmentList:
    """Map an SDK ``EquipmentList`` to its dashboard equivalent."""

    return EquipmentList(
        equipment=[
            _snapshot(s, overrides.get(s.id), registry, platforms_config)
            for s in skill_list.equipment
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
