"""The vocabulary prompt is what makes device names transcribable — pin it."""

from pathlib import Path

from lab_stt.vocab import build_context


def test_reads_names_and_spaced_ids_from_registry(tmp_path: Path):
    reg = tmp_path / "equipment.yaml"
    reg.write_text(
        "equipment:\n"
        "  - id: ot2_hte\n    name: Opentrons OT-2 (HTE)\n"
        "  - id: plateloc\n    name: Agilent PlateLoc\n"
    )
    ctx = build_context(equipment_yaml=str(reg))
    assert "Opentrons OT-2 (HTE)" in ctx
    assert "ot2 hte" in ctx          # ids spoken with spaces, not underscores
    assert "Agilent PlateLoc" in ctx
    assert ctx.startswith("Lab operations vocabulary:")


def test_missing_registry_still_yields_a_prompt(tmp_path: Path):
    ctx = build_context(equipment_yaml=str(tmp_path / "nope.yaml"))
    assert "vocabulary" in ctx  # standing terms only, never empty


def test_broken_registry_does_not_raise(tmp_path: Path):
    reg = tmp_path / "equipment.yaml"
    reg.write_text(":::: not yaml {{{{")
    assert build_context(equipment_yaml=str(reg))  # logged, not fatal


def test_extra_vocab_file_and_dedup(tmp_path: Path):
    reg = tmp_path / "equipment.yaml"
    reg.write_text("equipment:\n  - id: shaker\n    name: Torrey Pines Shaker\n")
    extra = tmp_path / "extra.txt"
    extra.write_text("acetonitrile\nTorrey Pines Shaker\n")
    ctx = build_context(equipment_yaml=str(reg), extra_file=str(extra))
    assert "acetonitrile" in ctx
    assert ctx.count("Torrey Pines Shaker") == 1
