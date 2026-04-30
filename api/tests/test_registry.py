"""Registry loader tests."""

from __future__ import annotations

from pathlib import Path

from app.registry import load_registry

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_loads_committed_registry() -> None:
    registry = load_registry(REPO_ROOT / "equipment.yaml")
    ids = [e.id for e in registry.equipment]
    assert "dose_every_well" in ids
    assert "filter_every_well" in ids
    assert "fume_hood_actuator" in ids
    assert "xarm_translocation" in ids
    # Environmental sensors are zone-based after the 90° CW map rotation.
    assert "env_sample_prep" in ids
    assert "env_storage" in ids
    assert any(i.startswith("env_lab499_") for i in ids)
    assert "ot2" in ids


def test_lookup_by_id() -> None:
    registry = load_registry(REPO_ROOT / "equipment.yaml")
    entry = registry.by_id("dose_every_well")
    assert entry is not None
    assert entry.adapter == "legacy_http"
    assert entry.kind == "solid_doser"
    assert registry.by_id("does-not-exist") is None
