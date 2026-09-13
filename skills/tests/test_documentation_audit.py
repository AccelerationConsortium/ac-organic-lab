"""The offline rollout report is not a live availability assertion."""

from pathlib import Path
import runpy

import pytest

from lab_skills.registry import DocumentationEndpoint, EquipmentEntry, Registry


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "audit_documentation.py"
AUDIT = runpy.run_path(str(SCRIPT))


def entry(equipment_id="device", **kwargs):
    return EquipmentEntry(
        id=equipment_id, name=equipment_id, kind="other", adapter="http",
        base_url="http://device.invalid/prefix", **kwargs,
    )


def complete_docs():
    return [
        DocumentationEndpoint(label=path, path=path, kind=kind)
        for path, kind in AUDIT["REQUIRED"].items()
    ]


def test_four_required_paths_suffice_without_markdown_reference():
    device = entry(documentation=complete_docs())
    assert AUDIT["registration_gaps"](device) == []
    device.documentation.append(DocumentationEndpoint(label="Legacy", path="/docs/agent"))
    assert AUDIT["registration_gaps"](device) == []


def test_wrong_kind_and_duplicate_paths_are_not_complete():
    device = entry(documentation=complete_docs())
    device.documentation[2].kind = "json"
    assert AUDIT["registration_gaps"](device) == ["/agent-docs (expected one markdown entry)"]
    device.documentation = complete_docs() + [complete_docs()[0]]
    assert AUDIT["registration_gaps"](device) == ["/docs (expected one swagger entry)"]


def test_gateway_groups_do_not_hide_missing_per_device_registrations():
    first = entry("first", documentation=complete_docs())
    second = entry("second")
    second.base_url += "/"
    mock = entry("mock")
    mock.adapter = "mock"
    lines, missing = AUDIT["audit"](Registry(equipment=[first, second, mock]))
    assert missing == 1
    assert "Service candidates: first, second" in lines
    assert "SKIP mock: no proxy-supported HTTP base" in lines
    assert lines[-1] == "1/2 HTTP entries declared; 1 service bases; 1 incomplete."


def test_cli_is_report_only_unless_check_requested(monkeypatch, capsys):
    main = AUDIT["main"]
    monkeypatch.setitem(main.__globals__, "load_registry", lambda _: Registry(equipment=[entry()]))
    assert main([]) == 0
    assert main(["--check"]) == 1
    with pytest.raises(SystemExit) as exc:
        main(["--equipment", "typo", "--check"])
    assert exc.value.code == 2
    assert "no live endpoints" in capsys.readouterr().out


def test_cli_can_scope_gate_to_migrated_equipment(monkeypatch, capsys):
    main = AUDIT["main"]
    registry = Registry(equipment=[entry("done", documentation=complete_docs()), entry("pending")])
    monkeypatch.setitem(main.__globals__, "load_registry", lambda _: registry)
    assert main(["--equipment", "done", "--check"]) == 0
    assert "pending" not in capsys.readouterr().out
