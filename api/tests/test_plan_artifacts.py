"""plan_artifacts: a finished plan rendered in Bitácora's formats, truthfully."""

from __future__ import annotations

import yaml

from app import plan_artifacts as pa

PATTERN_BUNDLE = {
    "plan_id": "fF3vFstTxLZc8FCc", "device_id": "ot2_complexation", "equipment_id": "ot2_complexation",
    "status": "executed", "steps_total": 4, "steps_ok": 4, "approved_by": "ada@lab",
    "started_at": "2026-10-06T04:45:33Z", "finished_at": "2026-10-06T05:43:54Z",
    "plate_report": {"labware": "2", "stats": {"n": 2, "mean_g": 0.1},
                     "wells": {"A1": {"mass_g": 0.1, "deviation_pct": 0.0, "stable": True, "status": "weighed",
                                      "volume_ul": 100},
                               "B1": {"mass_g": 0.1, "status": "weighed"},
                               "C1": {"mass_g": None, "status": "unweighed"}}},
    "plan": {
        "pattern": {
            "wells": ["A1", "B1"],
            "prelude": [{"action": "pick_up_tip", "args": {"pipette": "left"}}],
            "epilogue": [{"action": "drop_tip", "args": {"pipette": "left"}}],
            "for_each_well": {
                "labware_nickname": "2", "wells": "A1:B1", "order": "column",
                "steps": [{"id": "d", "action": "dispense",
                           "args": {"pipette": "left", "volume_ul": 100,
                                    "location": {"labware_nickname": "2", "position": "{well}"},
                                    "note": "row {row} col {column}"}},
                          {"action": "platebalance.read", "args": {}}],
                "overrides": {"B1": {"d": {"volume_ul": 50}}},
            },
        },
        "steps": [{"action": "pick_up_tip", "args": {"pipette": "left"}}] * 4,
        "results": [],
    },
}


def test_a_pattern_plan_becomes_one_per_well_action_with_brackets():
    built = pa.as_run_protocol(PATTERN_BUNDLE)
    name, action_id = "asrun-ff3vfsttxlzc8fcc", "asrun_ff3vfsttxlzc8fcc"
    proto, actions = built["protocol"], built["actions"]["actions"]
    # Protocol/design names take hyphens, action names underscores (the schemas differ).
    assert proto["protocol"] == name and proto["steps"] == [{"step_id": "s001", "action": action_id}]
    assert "not an authored protocol" in proto["description"]
    action = actions[action_id]
    assert action["for_each_well"] is True and action["role"] == "liquid_handler"
    assert action["before_wells"] == [{"id": "pre1", "skill": "pick_up_tip", "args": {"pipette": "left"}}]
    assert action["after_wells"] == [{"id": "post1", "skill": "drop_tip", "args": {"pipette": "left"}}]
    assert [s["id"] for s in action["steps"]] == ["d", "w2"]
    assert action["steps"][0]["skill"] == "dispense"
    # Placeholders: {well} kept, {row}/{column} translated to Bitácora's names.
    assert action["steps"][0]["args"]["location"]["position"] == "{well}"
    assert action["steps"][0]["args"]["note"] == "row {well_row} col {well_column}"
    # The plate map is the wells walked, in order; an override is a realized condition.
    assert list(proto["plate_map"]["wells"]) == ["A1", "B1"]
    assert proto["plate_map"]["wells"]["B1"] == {"conditions": {"d_volume_ul": 50}}
    assert proto["plate_map"]["labware"] == "2" and proto["plate_map"]["role"] == "conditions"
    assert any("overrides" in w for w in built["warnings"])


def test_a_flat_plan_becomes_one_action_per_step():
    bundle = {**PATTERN_BUNDLE, "plan": {"steps": [{"action": "lights.set", "args": {"on": True}},
                                                     {"action": "home", "args": {}}], "results": []}}
    built = pa.as_run_protocol(bundle)
    assert [s["step_id"] for s in built["protocol"]["steps"]] == ["s001", "s002"]
    acts = built["actions"]["actions"]
    assert acts["asrun_ff3vfsttxlzc8fcc_s001"] == {"role": "liquid_handler", "skill": "lights.set",
                                                   "args": {"on": True}}
    assert built["warnings"] == []


def test_the_yaml_round_trips_and_an_index_placeholder_is_a_warning_not_a_guess():
    files = pa.protocol_yaml(PATTERN_BUNDLE)
    assert yaml.safe_load(files["protocol"])["protocol"] == "asrun-ff3vfsttxlzc8fcc"
    assert "actions" in yaml.safe_load(files["actions"])
    bundle = {**PATTERN_BUNDLE, "plan": {**PATTERN_BUNDLE["plan"], "pattern": {
        **PATTERN_BUNDLE["plan"]["pattern"],
        "for_each_well": {**PATTERN_BUNDLE["plan"]["pattern"]["for_each_well"],
                          "steps": [{"action": "comment", "args": {"text": "well {index}"}}]}}}}
    built = pa.as_run_protocol(bundle)
    assert built["actions"]["actions"]["asrun_ff3vfsttxlzc8fcc"]["steps"][0]["args"]["text"] == "well {index}"
    assert any("{index}" in w for w in built["warnings"])


def test_the_design_skeleton_leaves_intent_to_the_human():
    text = pa.design_skeleton(PATTERN_BUNDLE)
    assert "objective: TODO" in text and "hypothesis: TODO" in text
    assert "controls: []  # TODO" in text
    # The one thing the run did vary per well is offered as a realized level,
    # named after the argument — never after a guessed chemical meaning.
    assert "- name: d_volume_ul" in text and "levels: [50]" in text
    assert "readout: mass (g) per well, plate balance" in text
    assert "labware: 2" in text and "wells_used: 2" in text
    assert "design: asrun-ff3vfsttxlzc8fcc" in text
    # Without overrides there is nothing to offer, and it says so.
    plain = {**PATTERN_BUNDLE, "plan": {**PATTERN_BUNDLE["plan"], "pattern": {
        **PATTERN_BUNDLE["plan"]["pattern"],
        "for_each_well": {**PATTERN_BUNDLE["plan"]["pattern"]["for_each_well"], "overrides": {}}}}}
    assert "- name: todo_factor" in pa.design_skeleton(plain)
    # Deck-recorded labels are offered as a comment, not as (invalid) substances.
    named = {**PATTERN_BUNDLE, "plan": {**PATTERN_BUNDLE["plan"], "steps": [
        {"action": "plate.load", "args": {"contents": "acetonitrile"}}]}}
    text = pa.design_skeleton(named)
    assert "#   acetonitrile: acetonitrile" in text and "\nsubstances:" not in text


def test_well_samples_carry_the_measured_mass_and_the_lineage_key():
    samples = pa.well_samples(PATTERN_BUNDLE)
    assert [s["hid"] for s in samples] == ["2:A1", "2:B1"]  # C1 was not weighed
    a1 = samples[0]
    assert a1["matrix"] == "liquid" and a1["taken_at"] == "2026-10-06T05:43:54Z" and a1["creator"] == "ada@lab"
    assert a1["meta"]["mass_g"] == 0.1 and a1["meta"]["plate"] == "2" and a1["meta"]["well"] == "A1"
    assert a1["meta"]["plan_id"] == "fF3vFstTxLZc8FCc" and a1["meta"]["unit"] == "g"
    assert "deviation_pct" not in samples[1]["meta"]  # absent, not invented


def test_the_skeleton_parses_as_a_valid_design_skeleton_once_parsed():
    """What the attach flow will hand Bitácora's DesignEditor: valid YAML whose
    only schema violations are the TODO values a human replaces."""
    import yaml

    doc = yaml.safe_load(pa.design_skeleton(PATTERN_BUNDLE))
    assert doc["design"] == "asrun-ff3vfsttxlzc8fcc"
    assert doc["objective"] == "TODO" and doc["factors"][0]["name"] == "d_volume_ul"
    assert doc["controls"] == [] and doc["replicates"] == 1
    assert all(k in {"design", "objective", "hypothesis", "factors", "fixed", "controls", "replicates",
                     "readout"} for k in doc)
