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
    assert "hplc" in SKILL_REGISTRY


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
    """OT-2 deck-light toggle + deck-layout declaration (convenience controls)."""

    from lab_skills.skill_catalog import SKILL_REGISTRY

    by_name = {d.name: d for d in SKILL_REGISTRY["liquid_handler"]}
    assert "lights.set" in by_name
    lights = by_name["lights.set"]
    assert lights.endpoint == "/control/lights"
    assert lights.method == "POST"
    # Convenience control: no state precondition.
    assert lights.requires_states == []
    assert not lights.requires_components

    assert "deck.declare" in by_name
    deck = by_name["deck.declare"]
    assert deck.endpoint == "/control/deck/declare"
    assert deck.method == "POST"
    # Metadata-only; declarable in any reachable state (incl. requires_init).
    assert deck.requires_states == []
    assert not deck.requires_components


def test_liquid_handler_protocol_surface_endpoints() -> None:
    """The OT-2 protocol-execution + lifecycle + plate-tracking verbs are
    cataloged with the gateway's ``/control/*`` paths."""

    by_name = {d.name: d for d in SKILL_REGISTRY["liquid_handler"]}
    expected = {
        "startup": "/control/startup",
        "shutdown": "/control/shutdown",
        "setup": "/control/setup",
        "home": "/control/home",
        "pick_up_tip": "/control/pick-up-tip",
        "aspirate": "/control/aspirate",
        "dispense": "/control/dispense",
        "drop_tip": "/control/drop-tip",
        "move_to": "/control/move-to",
        "move_labware": "/control/move-labware",
        "pause": "/control/pause",
        "resume": "/control/resume",
        "plate.load": "/control/plate/load",
        "plate.unload": "/control/plate/unload",
        "well.update": "/control/well/update",
    }
    for name, endpoint in expected.items():
        assert name in by_name, f"missing liquid_handler skill {name!r}"
        assert by_name[name].endpoint == endpoint
        assert by_name[name].method == "POST"

    # aspirate/dispense carry a volume + optional flow_rate; pick_up_tip an
    # explicit-tip location (required by the HTTP run-engine transport).
    move_fields = by_name["aspirate"].args_schema.model_fields
    assert {"pipette", "volume_ul", "location", "flow_rate"} <= set(move_fields)
    assert by_name["dispense"].args_schema is by_name["aspirate"].args_schema
    tip_fields = by_name["pick_up_tip"].args_schema.model_fields
    assert {"pipette", "labware_nickname", "position"} <= set(tip_fields)

    # startup is the only init-state action; motion verbs run from ready.
    assert "requires_init" in by_name["startup"].requires_states
    for name in ("move_to", "pick_up_tip", "aspirate", "dispense", "drop_tip", "move_labware"):
        assert by_name[name].requires_states == ["ready"]

    # move_to targets a well OR absolute deck coordinates (exactly one).
    move_to_fields = by_name["move_to"].args_schema.model_fields
    assert {"pipette", "location", "coordinates", "speed", "force_direct"} <= set(
        move_to_fields
    )


def test_liquid_handler_names_match_gateway_allowed_actions() -> None:
    """Availability is ``def.name in allowed_actions`` (session.py), so every
    OT-2 skill name must be one the gateway actually advertises. This encodes
    the union of ``opentrons-server`` ``service.allowed_actions`` across states
    (ready / requires_init / dry_run / paused) as the contract — a rename on
    either side breaks ``lab.skills()`` matching and must fail here.
    """

    # The exact strings opentrons-server gateway/service.py::allowed_actions
    # can emit, plus the two convenience controls it appends unconditionally.
    gateway_advertised = {
        "startup", "shutdown", "home", "setup", "pause", "resume",
        "move_to", "pick_up_tip", "aspirate", "dispense", "drop_tip", "move_labware",
        "plate.load", "plate.unload", "well.update",
        "tips.reset",
        "lights.set", "deck.declare",
    }
    catalog_names = {d.name for d in SKILL_REGISTRY["liquid_handler"]}
    # Every cataloged skill is something the gateway will honor by name.
    orphans = catalog_names - gateway_advertised
    assert not orphans, f"catalog names the gateway never advertises: {orphans}"
    # And we cover the whole advertised surface (reconcile is intentionally
    # excluded — it is an operator recovery hook, never in allowed_actions).
    assert catalog_names == gateway_advertised


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


def test_hplc_catalog_registered() -> None:
    """Agilent UPLC-MS sidecar (STATUS_SPEC v1.1): claim-gated /control/* verbs."""

    defs = {d.name: d for d in SKILL_REGISTRY["hplc"]}
    assert set(defs) == {
        "run.submit", "run.abort", "queue.cancel", "instrument.standby",
        "workflow.start", "workflow.end",
    }
    for d in defs.values():
        assert d.kind == "hplc"

    assert defs["run.submit"].endpoint == "/control/run"
    assert defs["run.submit"].method == "POST"
    assert defs["run.abort"].endpoint == "/control/abort"
    # standby parks the instrument; a true shutdown is a manual procedure, not a skill.
    assert defs["instrument.standby"].endpoint == "/control/standby"

    # Workflow lock (queue-ownership precedence #2): an HTE campaign takes the
    # equipment-blocking lock. service.start/end are operator-only, NOT skills.
    assert defs["workflow.start"].endpoint == "/control/workflow/start"
    assert defs["workflow.start"].method == "POST"
    assert defs["workflow.end"].endpoint == "/control/workflow/end"
    assert defs["workflow.end"].method == "POST"
    assert "service.start" not in defs and "service.end" not in defs

    # queue.cancel is a DELETE with the queue_id in the path.
    cancel = defs["queue.cancel"]
    assert cancel.method == "DELETE"
    assert cancel.endpoint == "/control/queue/{queue_id}"
    assert "queue_id" in cancel.args_schema.model_fields

    # run.submit body mirrors the device's RunRequest (gradient + structured samples).
    submit_fields = defs["run.submit"].args_schema.model_fields
    assert "gradient" in submit_fields
    assert "samples" in submit_fields
    assert "output_dir" in submit_fields
    assert "plate_format" in submit_fields
    assert "submitter" in submit_fields  # robot-reserved-tray flag
    from lab_skills.skill_catalog.hplc import SampleConfig
    assert {"tray", "well"} <= set(SampleConfig.model_fields)


def test_hplc_run_submit_args_validate_ranges() -> None:
    from lab_skills.skill_catalog.hplc import GradientConfig, RunSubmitArgs, SampleConfig

    grad = GradientConfig(
        name="standard_10min",
        solvent_a="H2O_0.1%FA",
        solvent_b="ACN_0.1%FA",
        run_time=10.0,
        flow_rate=0.6,
        gradient_table=[[0.0, 0.05], [9.9, 0.05]],
    )
    ok = RunSubmitArgs(
        output_dir="C:/CDSProjects/Installation/Results/Batch",
        gradient=grad,
        samples=[SampleConfig(sample_name="cpd_01", tray="front", well="A1", injection_volume=2.0)],
    )
    assert ok.ms_mode == "positive_negative"
    # plate_format defaults to None (trust the device's configured labware); the
    # client-side pre-check assumes 96-well when unset.
    assert ok.plate_format is None
    assert ok.submitter == "manual"

    with pytest.raises(Exception):
        SampleConfig(sample_name="has spaces", tray="front", well="A1", injection_volume=2.0)
    with pytest.raises(Exception):
        SampleConfig(sample_name="cpd", tray="front", well="A1", injection_volume=999.0)  # > 20 uL

    # Well geometry is validated against plate_format (A13 / I1 are off a 96-well plate).
    with pytest.raises(Exception):
        RunSubmitArgs(
            output_dir="x", gradient=grad,
            samples=[SampleConfig(sample_name="c", tray="front", well="A13", injection_volume=2.0)],
        )
    # ...but a 384-well plate accepts P24.
    RunSubmitArgs(
        output_dir="x", gradient=grad, plate_format="384-well",
        samples=[SampleConfig(sample_name="c", tray="rear", well="P24", injection_volume=2.0)],
    )
    # A 6x9 54-vial plate accepts F9 but rejects G1 (off the plate).
    RunSubmitArgs(
        output_dir="x", gradient=grad, plate_format="54-vial",
        samples=[SampleConfig(sample_name="c", tray="rear", well="F9", injection_volume=2.0)],
    )
    with pytest.raises(Exception):
        RunSubmitArgs(
            output_dir="x", gradient=grad, plate_format="54-vial",
            samples=[SampleConfig(sample_name="c", tray="rear", well="G1", injection_volume=2.0)],
        )
    # A custom/unknown plate type is deferred to the device (no client-side raise).
    RunSubmitArgs(
        output_dir="x", gradient=grad, plate_format="custom-24",
        samples=[SampleConfig(sample_name="c", tray="rear", well="Z9", injection_volume=2.0)],
    )

    with pytest.raises(Exception):
        GradientConfig(
            name="x", solvent_a="a", solvent_b="b",
            run_time=999.0, flow_rate=0.6, gradient_table=[[0.0, 0.0]],  # > 120 min
        )


def test_plate_stacker_catalog_registered() -> None:
    """Agilent BioStack 4 (STATUS_SPEC v1.1): six parameterless control
    actions; names/endpoints are the cross-repo contract and must match the
    device exactly.
    """

    defs = {d.name: d for d in SKILL_REGISTRY["plate_stacker"]}
    assert set(defs) == {
        "startup", "shutdown", "home",
        "stage_plate", "present_plate", "handoff",
    }
    for name, d in defs.items():
        assert d.kind == "plate_stacker"
        assert d.endpoint == f"/control/{name}"
        assert d.method == "POST"
        # All six take an empty body.
        assert d.args_schema.model_fields == {}
    # startup is the only init-state action; the rest run from ready.
    assert "requires_init" in defs["startup"].requires_states
    for name in ("home", "stage_plate", "present_plate", "handoff"):
        assert "ready" in defs[name].requires_states


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
