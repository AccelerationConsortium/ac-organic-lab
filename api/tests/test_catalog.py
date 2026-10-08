"""GET /api/catalog grouping, against the committed equipment.yaml and
platforms.yaml: every listed instrument sits in a named group, and the groups
come in the order the API Reference page shows them."""

from __future__ import annotations

from pathlib import Path

from lab_skills import load_platforms, load_registry

ROOT = Path(__file__).resolve().parents[2]


async def _committed_catalog() -> dict:
    from app.main import app, skill_catalog

    missing = object()
    previous = {
        name: getattr(app.state, name, missing) for name in ("registry", "platforms_config")
    }
    app.state.registry = load_registry(ROOT / "equipment.yaml")
    app.state.platforms_config = load_platforms(ROOT / "platforms.yaml")
    try:
        return (await skill_catalog())["platforms"]
    finally:
        for name, value in previous.items():
            if value is missing:
                delattr(app.state, name)
            else:
                setattr(app.state, name, value)


async def test_catalog_places_every_instrument() -> None:
    platforms = await _committed_catalog()

    unplaced = [i["id"] for i in platforms.get("unknown", {}).get("instruments", [])]
    assert unplaced == [], (
        f"{unplaced} are in equipment.yaml but placed nowhere by platforms.yaml: "
        "add each to its section, or to a catalog_groups entry if it is on no Overview card"
    )
    assert all(group["instruments"] for group in platforms.values())


def test_catalog_groups_ids_name_registered_equipment() -> None:
    registered = {entry.id for entry in load_registry(ROOT / "equipment.yaml").equipment}
    groups = load_platforms(ROOT / "platforms.yaml").catalog_groups

    assert [
        eq_id for group in groups for eq_id in group.equipment if eq_id not in registered
    ] == []


async def test_catalog_lists_platform_pages_first_then_the_rest() -> None:
    platforms = await _committed_catalog()
    config = load_platforms(ROOT / "platforms.yaml")
    with_page = [s.id for s in config.sections if s.href]

    assert list(platforms)[: len(with_page)] == with_page
    assert list(platforms)[len(with_page):] == [
        "web_services", "monitoring", "computers", "printers", "bench_prototypes",
    ]
    assert platforms["computers"]["label"] == "Computers and Servers"


async def test_catalog_group_extends_its_section() -> None:
    platforms = await _committed_catalog()
    gibbie = [i["id"] for i in platforms["sample_prep"]["instruments"]]

    # Off the Gibbie card, but listed under the Gibbie platform in the catalog.
    assert "gibbie_hotplate" in gibbie
    assert platforms["sample_prep"]["label"] == "Gibbie Platform"
    assert {"bitacora_eln", "bitacora_db"} <= {
        i["id"] for i in platforms["web_services"]["instruments"]
    }
