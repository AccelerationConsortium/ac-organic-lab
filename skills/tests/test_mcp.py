"""Tests for the control-capable MCP server (``lab_skills.mcp``) and CLI.

The tool *logic* functions are tested directly (no mcp package needed) against
a respx-mocked device. A light ``_build_server`` smoke test confirms the
control-gated tool registration; it is skipped if ``mcp`` is not installed.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from lab_skills.mcp import (
    MCPConfig,
    _get_status,
    _list_equipment,
    _list_skills,
    _run,
    _validate,
)
from lab_skills.cli import _parse_binding
from lab_skills.registry import EquipmentEntry, Registry

BASE = "http://plateloc.local:8010"


def _config(*, allow_control: bool = False) -> MCPConfig:
    registry = Registry(
        equipment=[
            EquipmentEntry(
                id="plateloc",
                name="Plateloc",
                kind="plate_sealer",
                adapter="http",
                base_url=BASE,
                protocol="1.1",
                status_path="/status",
                poll_timeout_seconds=1.0,
            )
        ]
    )
    return MCPConfig(
        registry=registry,
        binding={"sealer": "plateloc"},
        owner="mcp-test",
        allow_control=allow_control,
    )


def _status_body(allowed: list[str], *, state: str = "ready") -> dict:
    return {
        "protocol_version": "1.1",
        "equipment_id": "plateloc",
        "equipment_name": "Plateloc",
        "equipment_kind": "plate_sealer",
        "equipment_status": state,
        "device_time": "2026-07-12T00:00:00Z",
        "components": {},
        "metrics": {},
        "allowed_actions": allowed,
    }


def _mock_status(allowed: list[str]) -> None:
    respx.get(f"{BASE}/status").mock(
        return_value=httpx.Response(200, json=_status_body(allowed))
    )


# ---------------------------------------------------------------------------
# CLI binding parser
# ---------------------------------------------------------------------------


def test_parse_binding_ok() -> None:
    assert _parse_binding(["sealer=plateloc", "reader=cytation_5"]) == {
        "sealer": "plateloc",
        "reader": "cytation_5",
    }


@pytest.mark.parametrize("bad", ["sealer", "=plateloc", "sealer="])
def test_parse_binding_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        _parse_binding([bad])


# ---------------------------------------------------------------------------
# Tool logic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_equipment_is_offline() -> None:
    out = json.loads(await _list_equipment(_config()))
    assert out["bindings"] == {"sealer": "plateloc"}
    assert out["equipment"][0]["id"] == "plateloc"
    assert out["equipment"][0]["protocol"] == "1.1"


@respx.mock
@pytest.mark.asyncio
async def test_list_skills_reflects_live_availability() -> None:
    _mock_status(["stage.in"])  # only stage.in allowed right now
    out = json.loads(await _list_skills(_config()))
    by_name = {s["name"]: s for s in out["skills"]}
    assert by_name["stage.in"]["available"] is True
    assert by_name["stage.out"]["available"] is False
    # args_schema is emitted as JSON Schema (an object with a "type").
    assert by_name["seal.start"]["args_schema"]["type"] == "object"
    assert all(s["role"] == "sealer" for s in out["skills"])


@respx.mock
@pytest.mark.asyncio
async def test_get_status_by_role_and_by_id() -> None:
    _mock_status(["stage.in"])
    by_role = json.loads(await _get_status(_config(), "sealer"))
    by_id = json.loads(await _get_status(_config(), "plateloc"))
    assert by_role["equipment_status"] == "ready"
    assert by_id["equipment_id"] == "plateloc"


@pytest.mark.asyncio
async def test_validate_reports_unknown_role_offline() -> None:
    # role "mixer" is not bound -> a blocking violation, no HTTP.
    out = json.loads(
        await _validate(_config(), [{"role": "mixer", "skill": "stage.in"}])
    )
    assert out["ok"] is False
    codes = [v["code"] for s in out["steps"] for v in s["violations"]]
    assert "unknown_role" in codes


@pytest.mark.asyncio
async def test_validate_rejects_malformed_steps() -> None:
    out = json.loads(await _validate(_config(), [{"skill": "stage.in"}]))  # no role
    assert "error" in out


@respx.mock
@pytest.mark.asyncio
async def test_run_dry_run_preflights_without_commands() -> None:
    _mock_status(["stage.in"])
    claim = respx.post(f"{BASE}/control/claim").mock(return_value=httpx.Response(200, json={}))
    cmd = respx.post(f"{BASE}/control/stage/in").mock(return_value=httpx.Response(200))

    out = json.loads(
        await _run(_config(), [{"role": "sealer", "skill": "stage.in"}], dry_run=True)
    )
    assert out["ok"] is True and out["dry_run"] is True
    assert out["steps"][0]["status"] == "dry_run"
    assert not claim.called and not cmd.called


# ---------------------------------------------------------------------------
# Server build (control gating)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_server_gates_execute_plan_on_allow_control() -> None:
    mcp = pytest.importorskip("mcp")  # noqa: F841 - just gate on availability
    from lab_skills.mcp import _build_server

    read_only = _build_server(_config(allow_control=False))
    controlling = _build_server(_config(allow_control=True))

    ro_names = {t.name for t in await read_only.list_tools()}
    ctl_names = {t.name for t in await controlling.list_tools()}

    # Always-on tools.
    assert {"list_equipment", "list_skills", "get_status", "validate_plan",
            "preflight_plan"} <= ro_names
    # The actuating tool is registered only with --allow-control.
    assert "execute_plan" not in ro_names
    assert "execute_plan" in ctl_names
