"""Registry loader tests: monorepo equipment.yaml + maintenance fixture."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from lab_skills import load_registry
from lab_skills.registry import Maintenance


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_loads_committed_registry() -> None:
    registry = load_registry(REPO_ROOT / "equipment.yaml")
    ids = [e.id for e in registry.equipment]
    assert "dose_every_well" in ids
    assert "filter_every_well" in ids
    assert "fume_hood_actuator" in ids
    assert "xarm_translocation" in ids
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


def test_committed_registry_has_no_tail_placeholder_hostnames() -> None:
    """Guardrail: committed registry must not ship with unresolved placeholders.

    This catches accidental deploys where `tail-XXXX` was never substituted with
    a real Tailscale MagicDNS name (or an explicit tailnet IP when DNS is not
    available on the host).
    """

    text = (REPO_ROOT / "equipment.yaml").read_text(encoding="utf-8")
    assert "tail-XXXX" not in text


def test_committed_registry_defaults_to_enabled_no_maintenance() -> None:
    """The repo's committed equipment.yaml has no maintenance flags today;
    every entry should default to ``enabled=True`` and ``maintenance=None``.
    """

    registry = load_registry(REPO_ROOT / "equipment.yaml")
    for e in registry.equipment:
        assert e.enabled is True, e.id
        assert e.maintenance is None, e.id


def test_dashboard_presentation_fields_silently_dropped() -> None:
    """``tile`` / ``location`` in equipment.yaml are dashboard-only; the SDK's
    ``EquipmentEntry`` does not carry them and Pydantic must ignore them
    rather than rejecting the registry.
    """

    registry = load_registry(REPO_ROOT / "equipment.yaml")
    sample = registry.by_id("xarm_translocation")
    assert sample is not None
    assert "tile" not in type(sample).model_fields
    assert "location" not in type(sample).model_fields


def test_disabled_entry_round_trip() -> None:
    registry = load_registry(FIXTURE_DIR / "equipment_with_maintenance.yaml")
    entry = registry.by_id("plateloc")
    assert entry is not None
    assert entry.enabled is False
    assert entry.maintenance is None  # `enabled: false` alone is fine


def test_maintenance_fields_parsed() -> None:
    registry = load_registry(FIXTURE_DIR / "equipment_with_maintenance.yaml")
    entry = registry.by_id("filtration_press")
    assert entry is not None
    assert entry.maintenance is not None
    assert isinstance(entry.maintenance, Maintenance)
    assert entry.maintenance.reason == "Awaiting replacement seal foil"
    assert entry.maintenance.until == date(2026, 6, 15)
    assert entry.maintenance.contact == "alice@lab"


def test_lab_registry_path_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "LAB_REGISTRY_PATH",
        str(FIXTURE_DIR / "equipment_with_maintenance.yaml"),
    )
    registry = load_registry()
    assert registry.by_id("plateloc") is not None
    assert registry.by_id("dose_every_well") is None
