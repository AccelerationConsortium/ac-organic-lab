from lab_skills.skill_catalog import skills_for
from lab_skills.plan import _find_skill_def
from lab_skills.registry import load_registry
from pathlib import Path


def test_lumastir_skills_do_not_leak_to_other_equipment():
    assert skills_for("other") == []
    assert skills_for("other", "hermes_web") == []
    names = {d.name for d in skills_for("other", "lumastir")}
    assert names == {"lumastir.motor.set", "lumastir.led.set"}
    assert _find_skill_def("other", "lumastir.motor.set", "lumastir") is not None
    assert _find_skill_def("other", "lumastir.motor.set", "hermes_web") is None


def test_lumastir_registry_enables_control_and_preserves_docs():
    r = load_registry(Path(__file__).resolve().parents[2] / "equipment.yaml")
    e = r.by_id("lumastir")
    # 6 since 2026-09-20: the device gained the house agent-documentation set
    # (/agent-docs, /agent-docs/api-reference, /llms.txt) in release 0.3.0. The
    # bespoke /agent-guide is retained at its original path and relabelled,
    # because other consumers already point at it.
    assert e.enabled and e.maintenance is None and len(e.documentation) == 6
    paths = {d.path for d in e.documentation}
    assert {"/agent-docs", "/agent-docs/api-reference", "/llms.txt"} <= paths
    assert "/agent-guide" in paths
