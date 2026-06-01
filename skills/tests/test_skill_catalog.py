"""Skill catalog registry tests.

Validates that importing :mod:`lab_skills.skill_catalog` populates
the process-wide :data:`SKILL_REGISTRY` with the expected per-kind entries,
that each :class:`SkillDef` carries a Pydantic args schema, and that names are
unique within a kind.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from lab_skills import SKILL_REGISTRY, Skill, SkillDef
from lab_skills.skill_catalog import register


def test_registry_populated_for_active_kinds() -> None:
    assert "plate_sealer" in SKILL_REGISTRY
    assert "press" in SKILL_REGISTRY
    assert "solid_doser" in SKILL_REGISTRY
    assert "fume_hood" in SKILL_REGISTRY


def test_plate_reader_catalog_registered() -> None:
    from lab_skills.skill_catalog import SKILL_REGISTRY

    names = {d.name for d in SKILL_REGISTRY["plate_reader"]}
    assert {
        "startup", "shutdown",
        "drawer.open", "drawer.close",
        "plate.load", "plate.unload", "well.update",
        "read.absorbance", "read.fluorescence", "read.luminescence",
        "imaging.capture",
    } <= names


def test_liquid_handler_catalog_registered() -> None:
    """OT-2 deck-light toggle (convenience control)."""

    from lab_skills.skill_catalog import SKILL_REGISTRY

    by_name = {d.name: d for d in SKILL_REGISTRY["liquid_handler"]}
    assert "lights.set" in by_name
    lights = by_name["lights.set"]
    assert lights.endpoint == "/control/lights"
    assert lights.method == "POST"
    # Convenience control: no state precondition.
    assert lights.requires_states == []
    assert not lights.requires_components


def test_robot_arm_graph_control_surface() -> None:
    """xArm migrated to STATUS_SPEC v1.1 with a claim-gated motion-graph
    control surface; the catalog mirrors the device's ``/control/graph/*``
    endpoints. (``do_not_call_connect: true`` stays in equipment.yaml — the
    SDK never auto-connects — but availability now comes from the device's
    ``allowed_actions`` once connected.)
    """

    defs = {d.name: d for d in SKILL_REGISTRY["robot_arm"]}
    assert set(defs) == {"graph.move_to", "graph.recover_to", "graph.record", "graph.mode"}
    for d in defs.values():
        assert d.kind == "robot_arm"
        assert d.endpoint == f"/control/{d.name.replace('.', '/')}"
        assert d.method == "POST"
    # move_to targets a named node; mode is constrained to the 3 interlock modes.
    assert "node_id" in defs["graph.move_to"].args_schema.model_fields
    assert defs["graph.mode"].args_schema.model_fields["mode"].is_required()


def test_plate_sealer_skill_endpoints_match_spec() -> None:
    """Catalog endpoints mirror the STATUS_SPEC ``kind=plate_sealer`` ``/control/*``
    contract.

    If those endpoints ever rename, the catalog file is the one place to
    update (the typed clients in v0.3 will not duplicate them).
    """

    by_name = {s.name: s for s in SKILL_REGISTRY["plate_sealer"]}
    assert by_name["startup"].endpoint == "/control/startup"
    assert by_name["shutdown"].endpoint == "/control/shutdown"
    assert by_name["seal.start"].endpoint == "/control/seal/start"
    assert by_name["seal.stop"].endpoint == "/control/seal/stop"
    assert by_name["seal.set_temperature"].endpoint == "/control/seal/temperature"
    assert by_name["seal.set_time"].endpoint == "/control/seal/time"
    assert by_name["stage.in"].endpoint == "/control/stage/in"
    assert by_name["stage.out"].endpoint == "/control/stage/out"


def test_each_skill_def_has_pydantic_args_schema() -> None:
    for kind, defs in SKILL_REGISTRY.items():
        for d in defs:
            assert isinstance(d, SkillDef)
            assert issubclass(d.args_schema, BaseModel), (
                f"{kind}/{d.name} args_schema must be a Pydantic BaseModel"
            )


def test_skill_names_unique_per_kind() -> None:
    for kind, defs in SKILL_REGISTRY.items():
        names = [d.name for d in defs]
        assert len(names) == len(set(names)), f"duplicate skill names in {kind}: {names}"


def test_register_rejects_mismatched_kind() -> None:
    """``register("press", [SkillDef(kind="plate_sealer", ...)])`` should fail
    rather than silently misclassify a SkillDef.
    """

    class _Args(BaseModel):
        pass

    bad = SkillDef(
        name="oops",
        kind="plate_sealer",
        description="x",
        endpoint="/x",
        args_schema=_Args,
    )
    with pytest.raises(ValueError):
        register("press", [bad])


def test_seal_start_args_schema_validates_ranges() -> None:
    from lab_skills.skill_catalog.plate_sealer import SealStartArgs

    SealStartArgs(temperature_c=170, seconds=3.0)  # ok
    with pytest.raises(Exception):
        SealStartArgs(temperature_c=10, seconds=3.0)  # below 20 C
    with pytest.raises(Exception):
        SealStartArgs(temperature_c=170, seconds=20.0)  # above 12 s


def test_skill_runtime_model_accepts_args_schema_class() -> None:
    """``Skill.args_schema`` is a *class* (matches SkillDef); the runtime
    model accepts it without coercing to an instance.
    """

    from lab_skills.skill_catalog.plate_sealer import SealStartArgs

    sk = Skill(
        name="seal.start",
        role="sealer",
        equipment_id="plateloc",
        kind="plate_sealer",
        description="...",
        args_schema=SealStartArgs,
        available=True,
    )
    assert sk.args_schema is SealStartArgs
    assert sk.available is True
    assert sk.reason is None
