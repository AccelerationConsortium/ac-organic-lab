from pathlib import Path

from lab_skills.plan import _find_skill_def
from lab_skills.registry import load_registry
from lab_skills.skill_catalog import skills_for

LIFT_ACTIONS = {"startup", "reference", "move_pose", "jog", "stop", "park", "shutdown"}


def test_balance_lift_skills_do_not_leak_to_other_equipment():
    assert skills_for("other") == []
    assert skills_for("other", "hermes_web") == []
    assert {d.name for d in skills_for("other", "lumastir")} == {"lumastir.motor.set", "lumastir.led.set"}
    defs = {d.name: d for d in skills_for("other", "balance_lift")}
    assert set(defs) == LIFT_ACTIONS
    assert all(d.endpoint.startswith("/control/") and d.method == "POST" for d in defs.values())
    assert _find_skill_def("other", "move_pose", "balance_lift") is not None
    assert _find_skill_def("other", "move_pose", "lumastir") is None


def test_balance_lift_args_fail_closed():
    import pytest
    from pydantic import ValidationError

    defs = {d.name: d for d in skills_for("other", "balance_lift")}
    defs["reference"].args_schema.model_validate({"measured_rod_mm": 7.05, "rod_at_retract_stop": True})
    for bad in ({"measured_rod_mm": 7.0}, {"measured_rod_mm": "7", "rod_at_retract_stop": True},
                {"measured_rod_mm": 7.0, "rod_at_retract_stop": True, "extra": 1}):
        with pytest.raises(ValidationError):
            defs["reference"].args_schema.model_validate(bad)
    defs["jog"].args_schema.model_validate({"steps": -600})
    for bad in ({"steps": 601}, {"steps": 1.5}, {"steps": True}, {}):
        with pytest.raises(ValidationError):
            defs["jog"].args_schema.model_validate(bad)
    with pytest.raises(ValidationError):
        defs["move_pose"].args_schema.model_validate({"pose": "Raised Pose"})
    with pytest.raises(ValidationError):
        defs["park"].args_schema.model_validate({"steps": 1})


def test_balance_lift_registry_entry():
    r = load_registry(Path(__file__).resolve().parents[2] / "equipment.yaml")
    e = r.by_id("balance_lift")
    assert e.kind == "other" and e.adapter == "http" and e.protocol == "1.2"
    assert e.base_url.endswith(":8078") and e.status_path == "/status"
