"""Deck-slot vocabulary resolver (``lab_skills.deck_slots``), and its seat in
``validate_plan`` / ``execute_plan`` (UI_DESIGN.md §5 Step 1m).

The pure resolution rules are tested here; the dashboard assistant's
lab-control server imports the same functions and tests only its own
wrapping (refusal codes, proposal payload) in ``api/tests``.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from lab_skills import (
    LabSession,
    LocationEntry,
    LocationsConfig,
    Plan,
    Step,
    execute_plan,
    validate_plan,
)
from lab_skills.deck_slots import (
    DECK_TOUCHING_SKILLS,
    SLOT_ARG_SKILLS,
    SlotResolutionError,
    canonical_slot,
    canonicalize_slot_args,
    default_locations,
    location_vocabulary,
    set_default_locations,
    touched_slots,
)
from lab_skills.interlocks import clear_interlocks
from lab_skills.registry import EquipmentEntry, Registry

OT2_BASE = "http://ot2.test:8020"


def _ot2(id: str = "ot2_hte") -> EquipmentEntry:
    return EquipmentEntry(
        id=id,
        name="Opentrons OT-2 HTE",
        kind="liquid_handler",
        adapter="http",
        base_url=OT2_BASE,
        status_path="/status",
        protocol="1.2",
        poll_timeout_seconds=1.0,
    )


def _arm() -> EquipmentEntry:
    return EquipmentEntry(
        id="xarm",
        name="UFactory xArm5",
        kind="robot_arm",
        adapter="http",
        base_url="http://arm.test:8000",
        protocol="1.2",
    )


def _locations() -> LocationsConfig:
    return LocationsConfig(
        locations=[
            LocationEntry(
                name="ot2_hte/slot_1", type="deck", equipment="ot2_hte",
                aliases={"ot2_hte": "1"}, label="OT-2 HTE · slot 1",
            ),
            LocationEntry(
                name="ot2_hte/slot_2", type="deck", equipment="ot2_hte",
                aliases={"ot2_hte": "2", "xarm": ["opentrons_2_low", "opentrons_2_high"]},
                label="OT-2 HTE · slot 2",
            ),
            LocationEntry(
                name="ot2_complexation/slot_2", type="deck", equipment="ot2_complexation",
                aliases={"ot2_complexation": "2"}, label="OT-2 complexation · slot 2",
            ),
        ]
    )


@pytest.fixture()
def locs() -> LocationsConfig:
    return _locations()


@pytest.fixture(autouse=True)
def _reset_state():
    clear_interlocks()
    set_default_locations(None)
    yield
    clear_interlocks()
    set_default_locations(None)


# -- canonical_slot -------------------------------------------------------------


@pytest.mark.parametrize(
    "spelling",
    ["2", " 2 ", 2, 2.0, "slot 2", "slot_2", "slot-2", "Slot 2", "ot2_hte/slot_2", "opentrons_2_low", "opentrons_2_high"],
)
def test_every_spelling_of_slot_2_lands_on_the_key(locs, spelling) -> None:
    key, place = canonical_slot(_ot2(), spelling, locs)
    assert key == "2"
    assert place is not None and place.name == "ot2_hte/slot_2"


def test_another_devices_place_is_refused_by_name_and_by_shape(locs) -> None:
    for spelling in ("ot2_complexation/slot_2", "ot2_complexation/slot_9"):
        with pytest.raises(SlotResolutionError) as exc:
            canonical_slot(_ot2(), spelling, locs)
        assert exc.value.code == "wrong_device_location"


def test_unknown_tokens_pass_through_and_off_deck_survives(locs) -> None:
    """The resolver never invents a slot: an unknown token reaches the schema
    and the device unchanged, and move_labware's OFF_DECK is kept."""
    assert canonical_slot(_ot2(), "13", locs) == ("13", None)
    assert canonical_slot(_ot2(), "north_shelf", locs) == ("north_shelf", None)
    assert canonical_slot(_ot2(), "off_deck", locs) == ("OFF_DECK", None)


def test_non_slot_values_are_invalid_args(locs) -> None:
    for bad in (None, True, "", "   ", [2]):
        with pytest.raises(SlotResolutionError) as exc:
            canonical_slot(_ot2(), bad, locs)
        assert exc.value.code == "invalid_args"


def test_ambiguous_registry_place_is_refused() -> None:
    two_keys = LocationsConfig(
        locations=[
            LocationEntry(
                name="ot2_hte/wide", type="deck", equipment="ot2_hte",
                aliases={"ot2_hte": ["5", "6"]}, label="two-slot module",
            )
        ]
    )
    with pytest.raises(SlotResolutionError) as exc:
        canonical_slot(_ot2(), "ot2_hte/wide", two_keys)
    assert exc.value.code == "ambiguous_location"


def test_syntax_only_without_a_registry() -> None:
    empty = LocationsConfig(locations=[])
    assert canonical_slot(_ot2(), "slot_2", empty) == ("2", None)
    assert canonical_slot(_ot2(), "ot2_hte/slot_2", empty) == ("2", None)
    with pytest.raises(SlotResolutionError):
        canonical_slot(_ot2(), "ot2_complexation/slot_2", empty)


# -- canonicalize_slot_args -----------------------------------------------------


def test_every_slot_carrying_argument_is_rewritten(locs) -> None:
    ot2 = _ot2()

    args, found = canonicalize_slot_args(ot2, "tips.reset", {"slot": "ot2_hte/slot_2"}, locs)
    assert args == {"slot": "2"}
    assert found == [
        {"field": "slot", "value": "2", "location": "ot2_hte/slot_2", "label": "OT-2 HTE · slot 2", "given": "ot2_hte/slot_2"}
    ]

    args, found = canonicalize_slot_args(
        ot2, "move_labware", {"labware_nickname": "plate1", "new_location": "slot 1"}, locs
    )
    assert args["new_location"] == "1" and found[0]["field"] == "new_location"

    args, found = canonicalize_slot_args(
        ot2,
        "setup",
        {"labware": [{"nickname": "plate1", "location": "opentrons_2_low", "loadname": "corning_96_wellplate_360ul_flat"}]},
        locs,
    )
    assert args["labware"][0]["location"] == "2"
    assert found[0]["field"] == "labware[0].location"

    args, found = canonicalize_slot_args(
        ot2, "deck.declare", {"slots": {"slot_2": "corning_96_wellplate_360ul_flat", "1": "tiprack"}}, locs
    )
    assert args["slots"] == {"2": "corning_96_wellplate_360ul_flat", "1": "tiprack"}
    assert sorted(f["field"] for f in found) == ["slots.1", "slots.2"]


def test_the_same_slot_spelled_twice_is_refused(locs) -> None:
    with pytest.raises(SlotResolutionError) as exc:
        canonicalize_slot_args(_ot2(), "deck.declare", {"slots": {"2": "a", "slot_2": "b"}}, locs)
    assert exc.value.code == "invalid_args"


def test_other_kinds_and_nickname_verbs_are_left_alone(locs) -> None:
    assert canonicalize_slot_args(_arm(), "graph.move_to", {"slot": "slot_2"}, locs) == (
        {"slot": "slot_2"},
        [],
    )
    # A nickname-addressed verb carries no place; nothing is touched.
    assert canonicalize_slot_args(_ot2(), "aspirate", {"labware_nickname": "slot_2"}, locs) == (
        {"labware_nickname": "slot_2"},
        [],
    )
    assert "aspirate" in DECK_TOUCHING_SKILLS and "aspirate" not in SLOT_ARG_SKILLS


def test_touched_slots_come_from_the_arguments() -> None:
    assert touched_slots("tips.reset", {"slot": "11"}) == ["11"]
    assert touched_slots("move_labware", {"new_location": "OFF_DECK"}) == []
    assert touched_slots("setup", {"labware": [{"location": "4"}, {"location": "5"}]}) == ["4", "5"]
    assert touched_slots("deck.declare", {"slots": {"2": "x", "3": None}}) == ["2", "3"]
    assert touched_slots("aspirate", {"labware_nickname": "plate1"}) == []


def test_location_vocabulary_for_the_ot2_and_the_arm(locs) -> None:
    by_name = {v["name"]: v for v in location_vocabulary(_ot2(), locs)}
    assert by_name["ot2_hte/slot_2"]["slot"] == "2"
    assert by_name["ot2_hte/slot_2"]["also_known_as"] == {"xarm": ["opentrons_2_low", "opentrons_2_high"]}
    assert "ot2_complexation/slot_2" not in by_name
    [reach] = location_vocabulary(_arm(), locs)
    assert reach == {
        "name": "ot2_hte/slot_2",
        "label": "OT-2 HTE · slot 2",
        "on": "ot2_hte",
        "nodes": ["opentrons_2_low", "opentrons_2_high"],
    }


def test_default_locations_is_installable_and_resettable(locs) -> None:
    set_default_locations(locs)
    assert default_locations() is locs
    # No explicit registry argument -> the installed default is used.
    assert canonical_slot(_ot2(), "opentrons_2_low")[0] == "2"
    set_default_locations(None)
    # Reset: the lazy loader runs; in the monorepo it finds the real file, so
    # only assert it is a config, not its contents.
    assert isinstance(default_locations(), LocationsConfig)


# -- validate_plan / execute_plan ----------------------------------------------


def _session(locs: LocationsConfig) -> LabSession:
    return LabSession(
        registry=Registry(equipment=[_ot2()]),
        binding={"ot2": "ot2_hte"},
        locations=locs,
    )


def test_validate_plan_accepts_a_registry_name_for_a_slot(locs) -> None:
    plan = Plan(steps=[Step(role="ot2", skill="tips.reset", args={"slot": "ot2_hte/slot_2"})])
    report = validate_plan(plan, _session(locs))
    assert report.ok, report.violations


def test_validate_plan_refuses_another_devices_slot_as_a_violation(locs) -> None:
    plan = Plan(steps=[Step(role="ot2", skill="tips.reset", args={"slot": "ot2_complexation/slot_2"})])
    report = validate_plan(plan, _session(locs))
    assert not report.ok
    [v] = report.violations
    assert v.code == "wrong_device_location"
    assert "ot2_complexation" in v.message


def test_validate_plan_still_schema_checks_the_canonical_args(locs) -> None:
    # An empty slot is refused by the resolver as invalid_args, before the
    # schema ever sees it — and the report says so once, not twice.
    plan = Plan(steps=[Step(role="ot2", skill="tips.reset", args={"slot": ""})])
    report = validate_plan(plan, _session(locs))
    assert [v.code for v in report.violations] == ["invalid_args"]


def _ot2_status(allowed: list[str]) -> dict:
    return {
        "protocol_version": "1.2",
        "equipment_id": "ot2_hte",
        "equipment_name": "Opentrons OT-2 HTE",
        "equipment_kind": "liquid_handler",
        "equipment_status": "ready",
        "activity": "idle",
        "device_time": "2026-09-04T00:00:00Z",
        "allowed_actions": allowed,
        "components": {},
        "metrics": {},
    }


@respx.mock
async def test_execute_plan_posts_the_canonical_key(locs) -> None:
    """The gateway receives ``{"slot": "2"}`` even though the plan was written
    with the registry name — the device never sees the other vocabulary."""
    respx.get(f"{OT2_BASE}/status").mock(
        return_value=httpx.Response(200, json=_ot2_status(["tips.reset"]))
    )
    respx.post(f"{OT2_BASE}/control/claim").mock(
        return_value=httpx.Response(
            200,
            json={"claim_token": "tok-1", "heartbeat_interval_s": 10_000.0, "expires_at": "2099-01-01T00:00:00Z"},
        )
    )
    respx.post(f"{OT2_BASE}/control/heartbeat").mock(return_value=httpx.Response(204))
    respx.post(f"{OT2_BASE}/control/release").mock(return_value=httpx.Response(204))
    reset_route = respx.post(f"{OT2_BASE}/control/tips/reset").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    plan = Plan(steps=[Step(role="ot2", skill="tips.reset", args={"slot": "opentrons_2_low"})])
    async with _session(locs) as session:
        report = await execute_plan(plan, session, owner="test")

    assert report.ok, report.model_dump()
    assert json.loads(reset_route.calls.last.request.content) == {"slot": "2"}
