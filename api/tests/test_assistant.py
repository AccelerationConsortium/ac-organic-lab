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


def test_translate_event_emits_plan_frame() -> None:
    """Step 1i: a propose_plan result surfaces as its own frame, beside (not
    instead of) the tool_result, so the bubble renders the plan card."""

    payload = {"plan": {"plan_id": "p1", "equipment_id": "xarm", "steps": [{"action": "move.n1"}]}}
    event = {
        "type": "user",
        "message": {
            "content": [
                {"type": "tool_result", "content": [{"type": "text", "text": json.dumps(payload)}]}
            ]
        },
    }
    frames = assistant._translate_event(event)
    types = [f["type"] for f in frames]
    assert "tool_result" in types
    assert "plan" in types
    assert "proposal" not in types
    assert next(f for f in frames if f["type"] == "plan")["plan"]["plan_id"] == "p1"


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
# Step 1j: refusal + decline extraction and frames
# ---------------------------------------------------------------------------


def test_refusal_from_tool_result_matches_refusal_codes() -> None:
    block = {"content": json.dumps({"error": "not in allowed_actions", "code": "not_allowed"})}
    refusal = assistant._refusal_from_tool_result(block)
    assert refusal == {"code": "not_allowed", "message": "not in allowed_actions"}


def test_refusal_ignores_errors_without_a_refusal_code() -> None:
    # History-tool errors (or any error whose code is not in REFUSAL_CODES)
    # must never render the amber refusal chip.
    assert assistant._refusal_from_tool_result({"content": json.dumps({"error": "boom"})}) is None
    assert (
        assistant._refusal_from_tool_result(
            {"content": json.dumps({"error": "boom", "code": "something_else"})}
        )
        is None
    )


def test_refusal_from_tool_result_unwraps_claude_result_envelope() -> None:
    block = {
        "content": json.dumps(
            {"result": json.dumps({"error": "nope", "code": "invalid_args"})}
        )
    }
    assert assistant._refusal_from_tool_result(block) == {
        "code": "invalid_args",
        "message": "nope",
    }


def test_declined_from_tool_result() -> None:
    payload = {"declined": {"reason_code": "informational", "explanation": "no action asked"}}
    block = {"content": json.dumps(payload)}
    assert assistant._declined_from_tool_result(block) == payload["declined"]
    assert assistant._declined_from_tool_result({"content": "[]"}) is None


def test_translate_event_emits_proposal_refused_frame() -> None:
    payload = {"error": "'seal.start' is not in allowed_actions", "code": "not_allowed"}
    event = {
        "type": "user",
        "message": {
            "content": [
                {"type": "tool_result", "content": [{"type": "text", "text": json.dumps(payload)}]}
            ]
        },
    }
    frames = assistant._translate_event(event)
    types = [f["type"] for f in frames]
    assert "proposal_refused" in types
    assert "proposal" not in types
    refusal = next(f for f in frames if f["type"] == "proposal_refused")["refusal"]
    assert refusal["code"] == "not_allowed"


def test_translate_event_emits_declined_frame() -> None:
    payload = {"declined": {"reason_code": "safety_floor", "explanation": "stop verbs stay operator-only"}}
    event = {
        "type": "user",
        "message": {
            "content": [
                {"type": "tool_result", "content": [{"type": "text", "text": json.dumps(payload)}]}
            ]
        },
    }
    frames = assistant._translate_event(event)
    types = [f["type"] for f in frames]
    assert "declined" in types
    assert next(f for f in frames if f["type"] == "declined")["declined"]["reason_code"] == "safety_floor"


# ---------------------------------------------------------------------------
# ChatRequest
# ---------------------------------------------------------------------------


def test_chat_request_mode_defaults_to_ask() -> None:
    req = ChatRequest(messages=[{"role": "user", "content": "hi"}])
    assert req.mode == "ask"


def test_chat_request_accepts_control_mode() -> None:
    req = ChatRequest(messages=[{"role": "user", "content": "hi"}], mode="control")
    assert req.mode == "control"


# ---------------------------------------------------------------------------
# The chat route's wire contract (what the proxies in front of it see)
# ---------------------------------------------------------------------------


def test_chat_route_is_proxy_safe(monkeypatch) -> None:
    """The stream crosses Next.js's rewrite proxy and the Caddy edge before it
    reaches the bubble, and each of those can hold SSE frames in a buffer:
    Next's default gzip `compression` keeps text/event-stream in zlib until
    the stream ends unless the response says `no-transform`, and Caddy's
    encode holds the first 512 bytes. Both were live failures (2026-08-20 —
    'no thinking progress shown'); the header and the preamble are the fix."""

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app import assistant_openai

    async def fake_turn(
        messages, *, control=False, actor=None, on_proposal=None, on_plan=None
    ):
        yield assistant._sse({"type": "text", "delta": "hi"})
        yield assistant._sse({"type": "done"})

    monkeypatch.setattr(assistant, "DEFAULT_BACKEND", "openai")
    monkeypatch.setenv("ASSISTANT_OPENAI_API_KEY", "sk-or-test")
    monkeypatch.setattr(assistant_openai, "run_openai_turn", fake_turn)

    app = FastAPI()
    app.include_router(assistant.build_assistant_router())
    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/api/assistant/chat",
            json={"mode": "ask", "messages": [{"role": "user", "content": "hi"}]},
        ) as r:
            body = b"".join(r.iter_bytes())

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert "no-transform" in r.headers["cache-control"]
    assert body.startswith(assistant.SSE_PREAMBLE)
    assert len(assistant.SSE_PREAMBLE) > 512
    # The preamble is an SSE comment: a frame with no `data:` line, which the
    # bubble's parser skips — the real frames follow untouched.
    frames = body.split(b"\n\n")
    assert frames[0].startswith(b": ")
    assert frames[1] == b'data: {"type": "text", "delta": "hi"}'
    assert frames[2] == b'data: {"type": "done"}'


# ---------------------------------------------------------------------------
# Step 1i — plans: frame extraction and the approve / finish routes
# ---------------------------------------------------------------------------

PLAN_STEPS = [
    {"action": "move.a", "passthrough_action": "graph/move_to", "args": {"node_id": "a"}},
    {"action": "move.b", "passthrough_action": "graph/move_to", "args": {"node_id": "b"}},
]
PLAN = {
    "plan_id": "p-test-1",
    "equipment_id": "xarm",
    "equipment_name": "UFactory xArm5",
    "kind": "robot_arm",
    "steps": PLAN_STEPS,
    "step_hash": assistant.plan_step_hash(PLAN_STEPS),
    "reason": "route to the reader",
    "actor": "alice@example.edu",
    "expires_in_s": 600,
    "device_state": {"equipment_status": "ready", "activity": "idle", "message": None},
}


def test_plan_from_tool_result_string_and_envelope() -> None:
    raw = json.dumps({"plan": PLAN})
    assert assistant._plan_from_tool_result({"content": raw}) == PLAN
    # Claude Code's {"result": "<json>"} envelope, list-of-text-blocks form.
    wrapped = json.dumps({"result": raw})
    assert (
        assistant._plan_from_tool_result({"content": [{"type": "text", "text": wrapped}]})
        == PLAN
    )
    # A proposal is not a plan, and a refusal is neither.
    assert assistant._plan_from_tool_result({"content": json.dumps({"proposal": {}})}) is None
    assert assistant._plan_from_tool_result({"content": json.dumps({"error": "x"})}) is None
    assert assistant._proposal_from_tool_result({"content": raw}) is None


class _FakeDb:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def record_equipment_event(self, device_id, event_type, *, message=None, payload=None, **_):
        self.events.append((device_id, event_type, payload or {}))


def _plan_client(monkeypatch, plan: dict, *, actor: str = "alice@example.edu"):
    """An app whose Control turn proposes ``plan`` (through the runner's on_plan
    hook, as the real backends do) — returns (client, db)."""

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app import assistant_openai

    async def fake_turn(
        messages, *, control=False, actor=None, on_proposal=None, on_plan=None
    ):
        if control and on_plan is not None:
            await on_plan(plan)
        yield assistant._sse({"type": "plan", "plan": plan})
        yield assistant._sse({"type": "done"})

    monkeypatch.setattr(assistant, "DEFAULT_BACKEND", "openai")
    monkeypatch.setattr(assistant, "CONTROL_BACKEND", "openai")
    monkeypatch.delenv("DASHBOARD_CONTROL_OPEN", raising=False)
    monkeypatch.setenv("ASSISTANT_OPENAI_API_KEY", "sk-or-test")
    monkeypatch.setattr(assistant_openai, "run_openai_turn", fake_turn)

    app = FastAPI()
    app.include_router(assistant.build_assistant_router())
    db = _FakeDb()
    app.state.db = db
    client = TestClient(app)
    with client.stream(
        "POST",
        "/api/assistant/chat",
        json={"mode": "control", "messages": [{"role": "user", "content": "go"}]},
        headers={"X-Auth-User": actor},
    ) as r:
        body = b"".join(r.iter_bytes())
    assert b'"type": "plan"' in body
    return client, db


def test_plan_approve_then_finish_audits_end_to_end(monkeypatch) -> None:
    client, db = _plan_client(monkeypatch, PLAN)
    hdr = {"X-Auth-User": "alice@example.edu"}

    r = client.post(
        "/api/assistant/plans/p-test-1/approve", json={"step_hash": PLAN["step_hash"]}, headers=hdr
    )
    assert r.status_code == 200, r.text
    assert r.json()["approved"] is True
    # Approval is a one-shot review record, not a toggle.
    r = client.post(
        "/api/assistant/plans/p-test-1/approve", json={"step_hash": PLAN["step_hash"]}, headers=hdr
    )
    assert r.status_code == 409

    r = client.post(
        "/api/assistant/plans/p-test-1/finish",
        json={
            "status": "failed",
            "results": [
                {"index": 1, "outcome": "ok"},
                {"index": 2, "outcome": "failed", "status_code": 412, "message": "not reachable"},
            ],
            "halt_reason": "step 2 (move.b) failed: 412",
        },
        headers=hdr,
    )
    assert r.status_code == 200
    # Retired: the record is gone, so a second report is an unknown plan.
    r = client.post(
        "/api/assistant/plans/p-test-1/finish", json={"status": "aborted"}, headers=hdr
    )
    assert r.status_code == 404

    assert [e[1] for e in db.events] == [
        "assistant_plan_proposed",
        "assistant_plan_approved",
        "assistant_plan_finished",
    ]
    assert all(e[0] == "xarm" for e in db.events)
    assert db.events[1][2]["step_hash"] == PLAN["step_hash"]
    assert db.events[2][2]["status"] == "failed"


def test_plan_approve_refuses_wrong_hash_other_actor_and_unknown(monkeypatch) -> None:
    client, _db = _plan_client(monkeypatch, PLAN)
    hdr = {"X-Auth-User": "alice@example.edu"}

    # The hash the operator sends must be the hash of what the tool produced.
    r = client.post(
        "/api/assistant/plans/p-test-1/approve", json={"step_hash": "0" * 64}, headers=hdr
    )
    assert r.status_code == 409
    # Proposed to alice; bob cannot approve it, and nobody can unauthenticated.
    r = client.post(
        "/api/assistant/plans/p-test-1/approve",
        json={"step_hash": PLAN["step_hash"]},
        headers={"X-Auth-User": "bob@example.edu"},
    )
    assert r.status_code == 403
    r = client.post(
        "/api/assistant/plans/p-test-1/approve", json={"step_hash": PLAN["step_hash"]}
    )
    assert r.status_code == 401
    r = client.post(
        "/api/assistant/plans/nope/approve", json={"step_hash": PLAN["step_hash"]}, headers=hdr
    )
    assert r.status_code == 404
    # An un-approved plan cannot be reported as run — only as dismissed.
    r = client.post(
        "/api/assistant/plans/p-test-1/finish", json={"status": "executed"}, headers=hdr
    )
    assert r.status_code == 409
    r = client.post(
        "/api/assistant/plans/p-test-1/finish", json={"status": "aborted"}, headers=hdr
    )
    assert r.status_code == 200


def test_plan_with_wrong_hash_from_tool_is_not_approvable(monkeypatch) -> None:
    """The dashboard recomputes the hash from the steps it was handed; a tool
    result whose hash does not match is never cached, so Approve 404s."""

    bad = {**PLAN, "plan_id": "p-bad", "step_hash": "f" * 64}
    client, db = _plan_client(monkeypatch, bad)
    r = client.post(
        "/api/assistant/plans/p-bad/approve",
        json={"step_hash": "f" * 64},
        headers={"X-Auth-User": "alice@example.edu"},
    )
    assert r.status_code == 404
    assert db.events == []
