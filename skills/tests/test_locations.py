"""Location registry loader tests: monorepo locations.yaml + fixtures.

`locations.yaml` is the third root YAML file — the registry of *places* a
container can be (docs/PLATE_TRACKING.md). These tests guard the committed
file the way `test_registry.py` guards `equipment.yaml`: it loads, it agrees
with the equipment registry, its names are identifiers, and its aliases are
unambiguous. None of this is state; none of it is a state machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lab_skills import load_locations, load_registry
from lab_skills.locations import NAME_RE, LocationsConfig


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def committed() -> LocationsConfig:
    return load_locations(REPO_ROOT / "locations.yaml")


def test_loads_committed_registry(committed: LocationsConfig) -> None:
    names = committed.names()
    # The arm's gripper is a location on purpose: it is where a plate *is*
    # between a pick and a place, and where it stays if a run aborts. No
    # `transport` type exists — this entry is the in-transit state.
    assert "xarm_translocation/gripper" in names
    assert "ot2_hte/slot_2" in names
    assert "cytation_5/carrier" in names
    assert "plateloc/stage" in names
    assert "bench/hte_staging" in names
    assert "waste/hte_solid" in names
    assert all(loc.active for loc in committed.locations)


def test_committed_registry_agrees_with_equipment_yaml(committed: LocationsConfig) -> None:
    """Every `equipment` and every alias key must be a real equipment id, deck
    places must name their device, device-anchored names carry their device's
    prefix, and no name or alias token is claimed twice. Same guard style as
    test_registry.py's placeholder-hostname check: the committed file must be
    clean, every time."""
    registry = load_registry(REPO_ROOT / "equipment.yaml")
    assert committed.validate_against(registry) == []


def test_names_are_identifiers(committed: LocationsConfig) -> None:
    names = committed.names()
    assert len(names) == len(set(names)), "location names must be unique"
    for name in names:
        assert NAME_RE.match(name), name
    # Every OT-2 deck slot is registered for both robots.
    for robot in ("ot2_hte", "ot2_complexation"):
        for n in range(1, 13):
            assert f"{robot}/slot_{n}" in names


def test_aliases_resolve_both_ways(committed: LocationsConfig) -> None:
    """`resolve_alias` is the read-side join from a device's own vocabulary
    (an OT-2 slot key, an xArm graph node) to the canonical name."""
    assert committed.resolve_alias("ot2_hte", "2") == "ot2_hte/slot_2"
    assert committed.resolve_alias("xarm_translocation", "opentrons_2_low") == "ot2_hte/slot_2"
    assert committed.resolve_alias("xarm_translocation", "cytation_low") == "cytation_5/carrier"
    assert committed.resolve_alias("xarm_translocation", "robot_home") is None  # a waypoint, not a place
    slot2 = committed.by_name("ot2_hte/slot_2")
    assert slot2 is not None
    assert "opentrons_2_low" in slot2.alias_tokens("xarm_translocation")
    assert slot2.alias_tokens("ot2_hte") == ["2"]
    assert slot2.alias_tokens("plateloc") == []


def test_for_equipment_keeps_file_order(committed: LocationsConfig) -> None:
    slots = [loc.name for loc in committed.for_equipment("ot2_hte")]
    assert slots == [f"ot2_hte/slot_{n}" for n in range(1, 13)]


def test_types_match_the_record_layer_enum(committed: LocationsConfig) -> None:
    """`type` seeds BitacoraDB `Location.location_type`; the vocabulary is
    DATABASE_DESIGN.md §6's and deliberately has no `transport`."""
    allowed = {"storage", "instrument", "deck", "fridge", "waste"}
    assert {loc.type for loc in committed.locations} <= allowed


# ── validation of bad files ───────────────────────────────────────────────


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "locations.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def test_validate_against_catches_cross_file_problems(tmp_path: Path) -> None:
    registry = load_registry(REPO_ROOT / "equipment.yaml")
    cfg = load_locations(_write(tmp_path, {"locations": [
        {"name": "ghost_device/slot_1", "type": "deck", "equipment": "ghost_device"},
        {"name": "bench/deck_without_device", "type": "deck"},
        {"name": "bench/mislabelled", "type": "instrument", "equipment": "plateloc"},
        {"name": "bench/a", "type": "storage", "aliases": {"ot2_hte": "2"}},
        {"name": "bench/b", "type": "storage", "aliases": {"ot2_hte": ["2"]}},
        {"name": "bench/b", "type": "storage"},
    ]}))
    problems = cfg.validate_against(registry)
    joined = "\n".join(problems)
    assert "ghost_device" in joined and "not in equipment.yaml" in joined
    assert "'deck' location must name its equipment" in joined
    assert "prefixed by their equipment id" in joined
    assert "alias '2' of ot2_hte points at both" in joined
    assert "duplicate location name 'bench/b'" in joined


def test_a_name_that_is_not_a_slash_path_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="slash-path"):
        load_locations(_write(tmp_path, {"locations": [{"name": "slot2", "type": "deck"}]}))
    with pytest.raises(ValueError, match="slash-path"):
        load_locations(_write(tmp_path, {"locations": [{"name": "OT2/Slot 2", "type": "deck"}]}))


def test_an_unknown_type_is_refused(tmp_path: Path) -> None:
    # `transport` is the one someone will reach for; the in-transit place is
    # the gripper, an `instrument`.
    with pytest.raises(ValueError, match="Invalid locations config"):
        load_locations(_write(tmp_path, {"locations": [
            {"name": "xarm_translocation/gripper", "type": "transport"}]}))


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_locations(tmp_path / "nope.yaml")


def test_env_var_path_is_honoured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _write(tmp_path, {"locations": [{"name": "bench/only", "type": "storage"}]})
    monkeypatch.setenv("LAB_LOCATIONS_PATH", str(p))
    assert load_locations().names() == ["bench/only"]
