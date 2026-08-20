"""Tests for the assistant chat backend's Control-mode wiring (UI_DESIGN §5).

These cover the pure, subprocess-free logic added in PR 2: per-mode MCP config
generation, the proposal SSE frame translation, and the request model. The
claude subprocess itself is not exercised here.
"""

from __future__ import annotations

import json

from app import assistant
from app.assistant import ChatRequest


# ---------------------------------------------------------------------------
# _write_mcp_config
# ---------------------------------------------------------------------------


def test_write_mcp_config_ask_is_read_only_servers(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ASSISTANT_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("BITACORA_URL", "http://bitacora.test:8050")
    path = assistant._write_mcp_config()
    cfg = json.loads(path.read_text())
    assert set(cfg["mcpServers"]) == {"lab-history", "lab-inventory"}
    # The inventory server reaches bitácora where the executor does.
    assert (
        cfg["mcpServers"]["lab-inventory"]["env"]["BITACORA_URL"]
        == "http://bitacora.test:8050"
    )
    assert path.name == "mcp.json"


def test_write_mcp_config_control_adds_lab_control(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ASSISTANT_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("AUTH_SERVICE_BASE", "http://authz.test:8009")
    path = assistant._write_mcp_config(include_control=True, actor="alice@example.edu")
    cfg = json.loads(path.read_text())
    assert set(cfg["mcpServers"]) == {"lab-history", "lab-inventory", "lab-control"}
    ctl = cfg["mcpServers"]["lab-control"]
    # Either launch mode is acceptable here; the command must name the server.
    assert "lab-control-mcp" in " ".join([ctl["command"], *ctl["args"]])
    assert ctl["env"]["LAB_ACTOR"] == "alice@example.edu"
    assert ctl["env"]["AUTH_SERVICE_BASE"] == "http://authz.test:8009"
    # A distinct filename so a control config never lingers into an ask turn.
    assert path.name == "mcp.control.json"


def test_write_mcp_config_binds_actor_to_lab_history(tmp_path, monkeypatch) -> None:
    """record_observation stamps journal rows with LAB_ACTOR, in Ask mode too."""

    monkeypatch.setenv("ASSISTANT_RUNTIME_DIR", str(tmp_path))
    path = assistant._write_mcp_config(actor="alice@example.edu")
    cfg = json.loads(path.read_text())
    assert cfg["mcpServers"]["lab-history"]["env"]["LAB_ACTOR"] == "alice@example.edu"
    # Anonymous sessions carry no actor: the journal write fails closed.
    path = assistant._write_mcp_config()
    cfg = json.loads(path.read_text())
    assert "LAB_ACTOR" not in cfg["mcpServers"]["lab-history"]["env"]


def test_write_mcp_config_control_without_actor_is_history_only(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("ASSISTANT_RUNTIME_DIR", str(tmp_path))
    path = assistant._write_mcp_config(include_control=True, actor=None)
    cfg = json.loads(path.read_text())
    assert set(cfg["mcpServers"]) == {"lab-history", "lab-inventory"}


def test_write_mcp_config_narrows_lab_history_toolset(tmp_path, monkeypatch) -> None:
    """The assistant must not see the dosing-run data tools: run/well outcomes
    are experiment data, and tool results transit the model provider."""

    monkeypatch.setenv("ASSISTANT_RUNTIME_DIR", str(tmp_path))
    path = assistant._write_mcp_config()
    cfg = json.loads(path.read_text())
    include = cfg["mcpServers"]["lab-history"]["env"]["LAB_HISTORY_TOOLS"]
    names = {n.strip() for n in include.split(",") if n.strip()}
    assert "query_runs" not in names
    assert "query_well_results" not in names
    # record_observation (the journal write) stays — platform knowledge loop.
    assert "record_observation" in names


def test_history_tools_default_matches_server_registry() -> None:
    """Cross-module parity: every tool the assistant asks for must exist
    server-side (a typo here would kill the spawned server at startup), and
    the exclusion is exactly the two run-data tools."""

    from app.mcp_server import ALL_TOOLS

    names = {n.strip() for n in assistant.HISTORY_TOOLS.split(",") if n.strip()}
    assert names == ALL_TOOLS - {"query_runs", "query_well_results"}


def test_mcp_servers_launch_without_uv_when_console_scripts_exist(
    tmp_path, monkeypatch
) -> None:
    """Regression: the spawn must not route through ``uv run`` when the console
    script is installed.

    ``uv run`` syncs the project first and needs ``~/.cache/uv`` writable. The
    deployed unit sets ``ProtectHome=read-only``, so under systemd every MCP
    server failed to start — the CLI reported ``status: "failed"``, the model
    saw zero tools and said they were unreachable, and nothing surfaced in the
    journal. Launching the installed script directly needs no writable HOME.
    """

    monkeypatch.setenv("ASSISTANT_RUNTIME_DIR", str(tmp_path))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("lab-history-mcp", "lab-inventory-mcp", "lab-control-mcp"):
        script = bindir / name
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(0o755)
    monkeypatch.setattr(assistant.sys, "executable", str(bindir / "python"))

    cfg = json.loads(
        assistant._write_mcp_config(
            include_control=True, actor="alice@example.edu"
        ).read_text()
    )
    for name, script in (
        ("lab-history", "lab-history-mcp"),
        ("lab-inventory", "lab-inventory-mcp"),
        ("lab-control", "lab-control-mcp"),
    ):
        entry = cfg["mcpServers"][name]
        assert entry["command"] == str(bindir / script)
        assert entry["args"] == []
        assert "uv" not in entry["command"]


def test_mcp_server_command_falls_back_to_uv_when_script_missing(
    tmp_path, monkeypatch
) -> None:
    """A dev checkout without the api package installed still works."""

    monkeypatch.setattr(assistant.sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(assistant.shutil, "which", lambda _name: None)
    command, args = assistant._mcp_server_command("lab-history-mcp")
    assert args[:2] == ["run", "--project"]
    assert args[-1] == "lab-history-mcp"


# ---------------------------------------------------------------------------
# _proposal_from_tool_result
# ---------------------------------------------------------------------------


def test_proposal_from_tool_result_list_content() -> None:
    payload = {"proposal": {"equipment_id": "xarm", "action": "move.n1"}}
    block = {
        "type": "tool_result",
        "content": [{"type": "text", "text": json.dumps(payload)}],
    }
    prop = assistant._proposal_from_tool_result(block)
    assert prop is not None
    assert prop["equipment_id"] == "xarm"


def test_proposal_from_tool_result_string_content() -> None:
    block = {"content": json.dumps({"proposal": {"action": "move.n1"}})}
    assert assistant._proposal_from_tool_result(block)["action"] == "move.n1"


def test_proposal_from_tool_result_refusal_is_none() -> None:
    block = {"content": json.dumps({"error": "nope", "code": "not_allowed"})}
    assert assistant._proposal_from_tool_result(block) is None


def test_proposal_from_tool_result_non_json_is_none() -> None:
    assert assistant._proposal_from_tool_result({"content": "just history text"}) is None


# The shapes above are the *unwrapped* tool JSON. Claude Code (verified against
# CLI 2.1.227 on 2026-08-11) actually wraps MCP tool output one level deeper, as
# {"result": "<json string>"} — so the tests above all passed while the real
# stream produced no proposal frame at all, and the confirm card never rendered.
# These two pin the observed envelope. See docs/UI_DESIGN.md §5.8 (the CLI
# double-wraps MCP tool output; the fixture was captured from a real stream).
#
# Captured verbatim from a real `--output-format stream-json` tool_result block.
_WRAPPED_PROPOSAL_CONTENT = (
    '{"result":"{\\"proposal\\": {\\"equipment_id\\": \\"xarm_translocation\\", '
    '\\"equipment_name\\": \\"UFactory xArm5\\", \\"kind\\": \\"robot_arm\\", '
    '\\"action\\": \\"move.uplc_draw_home\\", '
    '\\"passthrough_action\\": \\"graph/move_to\\", '
    '\\"args\\": {\\"node_id\\": \\"uplc_draw_home\\"}, '
    '\\"reason\\": \\"Operator requested moving xArm to uplc_draw_home.\\", '
    '\\"actor\\": \\"alice@example.edu\\", \\"expires_in_s\\": 120, '
    '\\"device_state\\": {\\"equipment_status\\": \\"ready\\", '
    '\\"activity\\": \\"idle\\"}}}"}'
)


def test_proposal_from_tool_result_unwraps_claude_result_envelope() -> None:
    prop = assistant._proposal_from_tool_result({"content": _WRAPPED_PROPOSAL_CONTENT})
    assert prop is not None
    assert prop["action"] == "move.uplc_draw_home"
    assert prop["passthrough_action"] == "graph/move_to"
    assert prop["args"] == {"node_id": "uplc_draw_home"}


def test_proposal_from_tool_result_wrapped_refusal_is_none() -> None:
    """Unwrapping must not turn a refusal into a card."""
    block = {
        "content": json.dumps(
            {"result": json.dumps({"error": "nope", "code": "not_allowed"})}
        )
    }
    assert assistant._proposal_from_tool_result(block) is None


# ---------------------------------------------------------------------------
# _translate_event
# ---------------------------------------------------------------------------


def test_translate_event_emits_proposal_frame() -> None:
    payload = {"proposal": {"equipment_id": "xarm", "action": "move.n1"}}
    event = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "content": [{"type": "text", "text": json.dumps(payload)}],
                }
            ]
        },
    }
    frames = assistant._translate_event(event)
    types = [f["type"] for f in frames]
    assert "tool_result" in types
    assert "proposal" in types
    prop = next(f for f in frames if f["type"] == "proposal")["proposal"]
    assert prop["equipment_id"] == "xarm"


def test_translate_event_history_tool_result_has_no_proposal() -> None:
    event = {
        "type": "user",
        "message": {
            "content": [
                {"type": "tool_result", "content": [{"type": "text", "text": "[]"}]}
            ]
        },
    }
    frames = assistant._translate_event(event)
    # The trailing status frame keeps the bubble's pill alive across the gap
    # where the model thinks about the result it just got back.
    assert [f["type"] for f in frames] == ["tool_result", "status"]
    assert frames[1]["phase"] == "thinking"


# ---------------------------------------------------------------------------
# ChatRequest
# ---------------------------------------------------------------------------


def test_chat_request_mode_defaults_to_ask() -> None:
    req = ChatRequest(messages=[{"role": "user", "content": "hi"}])
    assert req.mode == "ask"


def test_chat_request_accepts_control_mode() -> None:
    req = ChatRequest(messages=[{"role": "user", "content": "hi"}], mode="control")
    assert req.mode == "control"
