"""Dashboard presentation layer tests.

The dashboard's job is to compose the SDK's ``EquipmentSnapshot`` /
``EquipmentList`` with presentation-only fields (``tile``, ``location``) plus
mirrored ``enabled`` / ``maintenance`` from the registry. The skills package
ships its own per-adapter, per-aggregator, per-client tests; here we only
verify the presentation-side composition.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from lab_skills import (
    EquipmentList as SkillEquipmentList,
    EquipmentSnapshot as SkillEquipmentSnapshot,
    EquipmentStatus,
    Maintenance,
    Registry,
    load_registry,
)
from lab_skills.registry import EquipmentEntry

from app.presentation import (
    DashboardEquipmentOverride,
    EquipmentList,
    EquipmentSnapshot,
    Tile,
    _snapshot,
    compose_equipment_list,
    load_dashboard_overrides,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _envelope(equipment_id: str, name: str, kind: str, state: str = "ready") -> EquipmentStatus:
    return EquipmentStatus(
        equipment_id=equipment_id,
        equipment_name=name,
        equipment_kind=kind,
        equipment_status=state,
        device_time=datetime(2026, 4, 29, 22, 50, 1, tzinfo=timezone.utc),
    )


def _skill_snapshot(entry: EquipmentEntry, state: str = "ready") -> SkillEquipmentSnapshot:
    return SkillEquipmentSnapshot(
        id=entry.id,
        name=entry.name,
        platform=entry.platform,
        kind=entry.kind,
        adapter=entry.adapter,
        status=_envelope(entry.id, entry.name, entry.kind, state),
        fetched_at=datetime(2026, 4, 29, 22, 50, 1, tzinfo=timezone.utc),
        latency_ms=42,
        base_url=entry.base_url,
    )


def _entry(**overrides) -> EquipmentEntry:
    base = dict(
        id="plateloc",
        name="Agilent PlateLoc",
        platform="hte",
        kind="plate_sealer",
        adapter="mock",
    )
    base.update(overrides)
    return EquipmentEntry(**base)


# ---------------------------------------------------------------------------
# load_dashboard_overrides
# ---------------------------------------------------------------------------


def test_load_dashboard_overrides_from_committed_yaml() -> None:
    overrides = load_dashboard_overrides(REPO_ROOT / "equipment.yaml")
    # Tile defaults survive when not specified per-entry.
    fume = overrides["fume_hood_actuator"]
    assert fume.tile.w == 2 and fume.tile.h == 1
    # xArm has a 2x2 tile in the committed registry.
    xarm = overrides["xarm_translocation"]
    assert (xarm.tile.w, xarm.tile.h) == (2, 2)
    # Environmental sensors carry locations.
    sample_prep = overrides["env_sample_prep"]
    assert sample_prep.location is not None
    assert sample_prep.location.label == "Sample Prep"


# ---------------------------------------------------------------------------
# _snapshot composition
# ---------------------------------------------------------------------------


def test_snapshot_passes_through_sdk_fields_and_defaults_dashboard_fields() -> None:
    entry = _entry()
    registry = Registry(equipment=[entry])
    sdk_snap = _skill_snapshot(entry)
    dashboard_snap = _snapshot(sdk_snap, override=None, registry=registry)

    assert isinstance(dashboard_snap, EquipmentSnapshot)
    # SDK fields preserved
    assert dashboard_snap.id == entry.id
    assert dashboard_snap.kind == entry.kind
    assert dashboard_snap.status.equipment_status == "ready"
    assert dashboard_snap.latency_ms == 42
    # Dashboard defaults
    assert dashboard_snap.tile == Tile()
    assert dashboard_snap.location is None
    assert dashboard_snap.enabled is True
    assert dashboard_snap.maintenance is None


def test_snapshot_attaches_tile_and_location_from_override() -> None:
    entry = _entry()
    registry = Registry(equipment=[entry])
    override = DashboardEquipmentOverride(
        id=entry.id,
        tile=Tile(w=4, h=2),
        location={"x": 25, "y": 75, "label": "Bench A"},
    )
    sdk_snap = _skill_snapshot(entry)
    dashboard_snap = _snapshot(sdk_snap, override=override, registry=registry)
    assert dashboard_snap.tile.w == 4 and dashboard_snap.tile.h == 2
    assert dashboard_snap.location is not None
    assert dashboard_snap.location.label == "Bench A"


def test_snapshot_mirrors_disabled_and_maintenance_from_registry() -> None:
    entry = _entry(
        enabled=False,
        maintenance=Maintenance(reason="Awaiting seals", contact="alice@lab"),
    )
    registry = Registry(equipment=[entry])
    sdk_snap = _skill_snapshot(entry)
    dashboard_snap = _snapshot(sdk_snap, override=None, registry=registry)
    assert dashboard_snap.enabled is False
    assert dashboard_snap.maintenance is not None
    assert dashboard_snap.maintenance.reason == "Awaiting seals"
    assert dashboard_snap.maintenance.contact == "alice@lab"


# ---------------------------------------------------------------------------
# compose_equipment_list
# ---------------------------------------------------------------------------


def test_compose_equipment_list_preserves_order_and_count() -> None:
    a = _entry(id="a", name="A")
    b = _entry(id="b", name="B")
    c = _entry(id="c", name="C")
    registry = Registry(equipment=[a, b, c])
    skill_list = SkillEquipmentList(
        equipment=[_skill_snapshot(e) for e in (a, b, c)],
        fetched_at=datetime(2026, 4, 29, 22, 50, 1, tzinfo=timezone.utc),
    )
    overrides: dict[str, DashboardEquipmentOverride] = {
        "b": DashboardEquipmentOverride(id="b", tile=Tile(w=4, h=1)),
    }
    result = compose_equipment_list(skill_list, overrides, registry)
    assert isinstance(result, EquipmentList)
    assert [s.id for s in result.equipment] == ["a", "b", "c"]
    assert result.equipment[1].tile.w == 4
    # 'a' and 'c' fall back to the default tile.
    assert result.equipment[0].tile == Tile()
    assert result.equipment[2].tile == Tile()


# ---------------------------------------------------------------------------
# Real-yaml end-to-end smoke
# ---------------------------------------------------------------------------


def test_committed_yaml_round_trips_through_presentation() -> None:
    """Loading the real ``equipment.yaml`` and composing a snapshot for every
    entry produces a valid dashboard ``EquipmentList`` shape.
    """

    registry = load_registry(REPO_ROOT / "equipment.yaml")
    overrides = load_dashboard_overrides(REPO_ROOT / "equipment.yaml")
    fake_skill_list = SkillEquipmentList(
        equipment=[_skill_snapshot(e) for e in registry.equipment],
        fetched_at=datetime(2026, 4, 29, 22, 50, 1, tzinfo=timezone.utc),
    )
    listing = compose_equipment_list(fake_skill_list, overrides, registry)
    assert len(listing.equipment) == len(registry.equipment)
    # Every entry has the expected dashboard fields populated.
    for s in listing.equipment:
        assert s.tile is not None
        assert s.enabled in (True, False)
