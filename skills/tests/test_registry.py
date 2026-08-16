"""Registry loader tests: monorepo equipment.yaml + maintenance fixture."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

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
    assert "env_hte" in ids
    assert "env_storage" in ids
    assert any(i.startswith("env_lab499_") for i in ids)
    assert "ot2_hte" in ids


def test_lookup_by_id() -> None:
    registry = load_registry(REPO_ROOT / "equipment.yaml")
    entry = registry.by_id("dose_every_well")
    assert entry is not None
    assert entry.adapter == "http"
    assert entry.kind == "solid_doser"
    assert registry.by_id("does-not-exist") is None


def test_bambu_gateway_and_printers_are_registered() -> None:
    registry = load_registry(REPO_ROOT / "equipment.yaml")

    gateway = registry.by_id("bambu_gateway")
    assert gateway is not None
    assert gateway.name == "Bambu Printers"
    assert gateway.base_url == "http://127.0.0.1:8012"
    assert gateway.status_path == "/status"
    assert gateway.pills.link_label == "GO"
    assert gateway.pills.link_href == "/utils/devices"
    assert gateway.pills.internal is True

    assert registry.by_id("bambu_p1s_01") is not None
    assert registry.by_id("bambu_h2d_01") is not None


def test_overview_link_labels_match_navigation_behavior() -> None:
    registry = load_registry(REPO_ROOT / "equipment.yaml")

    linked = [entry for entry in registry.equipment if entry.pills.link_label]
    assert linked
    for entry in linked:
        expected = "GO" if entry.pills.internal else "Open"
        assert entry.pills.link_label == expected, entry.id


def test_bambu_gateway_is_in_services_section() -> None:
    config = yaml.safe_load((REPO_ROOT / "platforms.yaml").read_text(encoding="utf-8"))
    services = next(section for section in config["sections"] if section["id"] == "web_services")

    assert "bambu_gateway" in services["equipment"]


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


def test_camera_block_parses_from_committed_yaml() -> None:
    """The committed registry's HTE camera entry round-trips into the new
    ``CameraConfig`` registry block.
    """

    registry = load_registry(REPO_ROOT / "equipment.yaml")
    entry = registry.by_id("cam_hte_tapo_c245")
    assert entry is not None
    assert entry.kind == "camera"
    assert entry.adapter == "http"
    assert entry.camera is not None
    assert entry.camera.host  # camera has a host configured
    assert entry.camera.onvif_port == 2020
    assert {lens.id for lens in entry.camera.lenses} == {"wide", "tele"}
    assert entry.plug is None


def test_camera_kind_extension_accepted() -> None:
    """Pydantic's EquipmentKind literal must accept the 3 new kinds."""

    from lab_skills.registry import EquipmentEntry

    for kind in ("camera", "smart_plug", "power_strip"):
        entry = EquipmentEntry(
            id=f"x_{kind}",
            name=kind,
            kind=kind,  # type: ignore[arg-type]
            adapter="http",
            base_url="http://127.0.0.1:8002",
        )
        assert entry.kind == kind


def test_lab_registry_path_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "LAB_REGISTRY_PATH",
        str(FIXTURE_DIR / "equipment_with_maintenance.yaml"),
    )
    registry = load_registry()
    assert registry.by_id("plateloc") is not None
    assert registry.by_id("dose_every_well") is None
