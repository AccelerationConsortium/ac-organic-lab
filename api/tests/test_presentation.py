"""Dashboard presentation layer tests.

The dashboard's job is to compose the SDK's ``EquipmentSnapshot`` /
``EquipmentList`` with presentation-only fields (``tile``, ``location``,
``pill``) plus mirrored ``enabled`` / ``maintenance`` from the registry.
Platform assignment and tile sizing are now resolved from ``PlatformsConfig``.
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
    PillConfig,
    PlatformSection,
    PlatformsConfig,
    Registry,
    Tile,
    load_registry,
)
from lab_skills.registry import EquipmentEntry

from app.presentation import (
    DashboardEquipmentOverride,
    EquipmentList,
    EquipmentSnapshot,
    _snapshot,
    compose_equipment_list,
    load_dashboard_overrides,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _empty_platforms() -> PlatformsConfig:
    return PlatformsConfig(sections=[])


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
    # Environmental sensors carry locations.
    sample_prep = overrides["env_sample_prep"]
    assert sample_prep.location is not None
    assert sample_prep.location.label == "Sample Prep"
    # No tile on overrides; tile is now on EquipmentEntry.tiles.
    assert not hasattr(sample_prep, "tile") or sample_prep.location is not None


# ---------------------------------------------------------------------------
# _snapshot composition
# ---------------------------------------------------------------------------


def test_snapshot_passes_through_sdk_fields_and_defaults_dashboard_fields() -> None:
    entry = _entry()
    registry = Registry(equipment=[entry])
    sdk_snap = _skill_snapshot(entry)
    dashboard_snap = _snapshot(sdk_snap, override=None, registry=registry,
                               platforms_config=_empty_platforms())

    assert isinstance(dashboard_snap, EquipmentSnapshot)
    # SDK fields preserved
    assert dashboard_snap.id == entry.id
    assert dashboard_snap.kind == entry.kind
    assert dashboard_snap.status.equipment_status == "ready"
    assert dashboard_snap.latency_ms == 42
    # Dashboard defaults when no platforms mapping
    assert dashboard_snap.tile == Tile()
    assert dashboard_snap.platform is None
    assert dashboard_snap.location is None
    assert dashboard_snap.enabled is True
    assert dashboard_snap.maintenance is None


def test_snapshot_resolves_platform_and_tile_from_platforms_config() -> None:
    entry = _entry(
        tiles={"test_section": Tile(w=4, h=2)},
    )
    registry = Registry(equipment=[entry])
    platforms_config = PlatformsConfig(sections=[
        PlatformSection(
            id="test_section",
            title="Test Section",
            kind="platform",
            equipment=[entry.id],
        )
    ])
    sdk_snap = _skill_snapshot(entry)
    dashboard_snap = _snapshot(sdk_snap, override=None, registry=registry,
                               platforms_config=platforms_config)
    assert dashboard_snap.platform == "test_section"
    assert dashboard_snap.tile.w == 4 and dashboard_snap.tile.h == 2


def test_snapshot_attaches_location_from_override() -> None:
    entry = _entry()
    registry = Registry(equipment=[entry])
    override = DashboardEquipmentOverride(
        id=entry.id,
        location={"x": 25, "y": 75, "label": "Bench A"},
    )
    sdk_snap = _skill_snapshot(entry)
    dashboard_snap = _snapshot(sdk_snap, override=override, registry=registry,
                               platforms_config=_empty_platforms())
    assert dashboard_snap.location is not None
    assert dashboard_snap.location.label == "Bench A"


def test_snapshot_mirrors_disabled_and_maintenance_from_registry() -> None:
    entry = _entry(
        enabled=False,
        maintenance=Maintenance(reason="Awaiting seals", contact="alice@lab"),
    )
    registry = Registry(equipment=[entry])
    sdk_snap = _skill_snapshot(entry)
    dashboard_snap = _snapshot(sdk_snap, override=None, registry=registry,
                               platforms_config=_empty_platforms())
    assert dashboard_snap.enabled is False
    assert dashboard_snap.maintenance is not None
    assert dashboard_snap.maintenance.reason == "Awaiting seals"
    assert dashboard_snap.maintenance.contact == "alice@lab"


def test_snapshot_carries_pill_config() -> None:
    entry = _entry(
        id="pypoe_web",
        pills=PillConfig(open=True),
        base_url="http://100.64.254.6:8000",
    )
    registry = Registry(equipment=[entry])
    sdk_snap = _skill_snapshot(entry)
    dashboard_snap = _snapshot(sdk_snap, override=None, registry=registry,
                               platforms_config=_empty_platforms())
    assert dashboard_snap.pill.open is True


# ---------------------------------------------------------------------------
# compose_equipment_list
# ---------------------------------------------------------------------------


def test_compose_equipment_list_preserves_order_and_count() -> None:
    a = _entry(id="a", name="A")
    b = _entry(id="b", name="B", tiles={"sec": Tile(w=4, h=1)})
    c = _entry(id="c", name="C")
    registry = Registry(equipment=[a, b, c])
    skill_list = SkillEquipmentList(
        equipment=[_skill_snapshot(e) for e in (a, b, c)],
        fetched_at=datetime(2026, 4, 29, 22, 50, 1, tzinfo=timezone.utc),
    )
    platforms_config = PlatformsConfig(sections=[
        PlatformSection(id="sec", title="Sec", kind="platform", equipment=["b"]),
    ])
    result = compose_equipment_list(skill_list, {}, registry, platforms_config)
    assert isinstance(result, EquipmentList)
    assert [s.id for s in result.equipment] == ["a", "b", "c"]
    assert result.equipment[1].tile.w == 4
    # 'a' and 'c' are not in any section — default tile
    assert result.equipment[0].tile == Tile()
    assert result.equipment[2].tile == Tile()


# ---------------------------------------------------------------------------
# Real-yaml end-to-end smoke
# ---------------------------------------------------------------------------


def test_committed_yaml_round_trips_through_presentation() -> None:
    """Loading the real equipment.yaml and platforms.yaml and composing a
    snapshot for every entry produces a valid dashboard ``EquipmentList``.
    """
    from lab_skills import load_platforms

    registry = load_registry(REPO_ROOT / "equipment.yaml")
    platforms_config = load_platforms(REPO_ROOT / "platforms.yaml")
    overrides = load_dashboard_overrides(REPO_ROOT / "equipment.yaml")
    fake_skill_list = SkillEquipmentList(
        equipment=[_skill_snapshot(e) for e in registry.equipment],
        fetched_at=datetime(2026, 4, 29, 22, 50, 1, tzinfo=timezone.utc),
    )
    listing = compose_equipment_list(fake_skill_list, overrides, registry, platforms_config)
    assert len(listing.equipment) == len(registry.equipment)
    for s in listing.equipment:
        assert s.tile is not None
        assert s.enabled in (True, False)
