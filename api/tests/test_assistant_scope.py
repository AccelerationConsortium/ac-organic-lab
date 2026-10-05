"""Equipment scopes — a device panel's assistant sees only that device.

Consolidation plan step 2. The scope must hold at every layer it could leak
through: the tool server itself (invocation-time refusal, registration
filter), the server set each backend spawns (claude-cli config, openai specs,
Hermes config), the CLI's allowed-tool glob, and the route (scope from the
path, verified actor required).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from lab_skills.registry import EquipmentEntry, Registry

from app import assistant, assistant_control as ac, assistant_hermes, assistant_openai
from lab_assistant.scopes import SCOPED_TOOLS, EquipmentScope

ACTOR = "alice@example.edu"


def _registry() -> Registry:
    def entry(eid: str) -> EquipmentEntry:
        return EquipmentEntry(id=eid, name=eid, kind="liquid_handler", adapter="http",
                              base_url=f"http://{eid}.test", status_path="/status",
                              protocol="1.2")
    return Registry(equipment=[entry("ot2_complexation"), entry("ot2_hte")])


# ── the tool server ──────────────────────────────────────────────────────


def test_a_scoped_server_refuses_every_other_device(monkeypatch):
    monkeypatch.setenv("LAB_SCOPE_EQUIPMENT", "ot2_complexation")
    reg = _registry()
    assert ac._out_of_scope(reg, "ot2_complexation") is None
    assert ac._out_of_scope(reg, "ot2-complexation") is None  # spelling variant of itself
    refused = json.loads(ac._out_of_scope(reg, "ot2_hte"))
    assert refused["code"] == "out_of_scope"
    assert json.loads(ac._out_of_scope(reg, "nonexistent"))["code"] == "out_of_scope"
    assert "out_of_scope" in ac.REFUSAL_CODES


def test_an_unscoped_server_is_unchanged(monkeypatch):
    monkeypatch.delenv("LAB_SCOPE_EQUIPMENT", raising=False)
    assert ac._out_of_scope(_registry(), "ot2_hte") is None


async def test_the_scoped_server_registers_only_read_tools_and_refuses_at_call(monkeypatch):
    monkeypatch.setenv("LAB_SCOPE_EQUIPMENT", "ot2_complexation")
    monkeypatch.setenv("LAB_CONTROL_TOOLS", ",".join(SCOPED_TOOLS))
    server = ac._build_server(_registry())
    names = {t.name for t in await server.list_tools()}
    assert names == set(SCOPED_TOOLS)
    # Invocation-time: the refusal comes from the tool itself, whichever
    # backend's client calls it, before anything reaches the network.
    result = await server.call_tool("get_equipment_docs", {"equipment_id": "ot2_hte"})
    assert "out_of_scope" in json.dumps(result, default=str)


async def test_an_unfiltered_server_still_registers_every_tool(monkeypatch):
    monkeypatch.delenv("LAB_CONTROL_TOOLS", raising=False)
    names = {t.name for t in await ac._build_server(_registry()).list_tools()}
    assert {"propose_action", "propose_plan", "decline_proposal", *SCOPED_TOOLS} <= names


# ── what each backend spawns ─────────────────────────────────────────────


def _assert_scoped_servers(servers: dict[str, Any]) -> None:
    assert set(servers) == {"lab-control"}  # never lab-history / lab-inventory
    env = servers["lab-control"]["env"]
    assert env["LAB_SCOPE_EQUIPMENT"] == "ot2_complexation"
    assert env["LAB_CONTROL_TOOLS"] == ",".join(SCOPED_TOOLS)
    assert env["LAB_ACTOR"] == ACTOR


def test_claude_cli_config_for_a_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_RUNTIME_DIR", str(tmp_path))
    path = assistant._write_mcp_config(actor=ACTOR, scope=EquipmentScope("ot2_complexation"))
    _assert_scoped_servers(json.loads(path.read_text())["mcpServers"])
    with pytest.raises(ValueError):
        assistant._write_mcp_config(scope=EquipmentScope("ot2_complexation"))


def test_openai_and_hermes_spawn_the_same_scoped_server():
    scope = EquipmentScope("ot2_complexation")
    _assert_scoped_servers(assistant_openai._server_specs(False, ACTOR, scope))
    config = assistant_hermes.turn_config(ACTOR, control=True, scope=scope)
    _assert_scoped_servers(config["mcp_servers"])
    # The scope overrides a requested control mode: no control addendum.
    assert "`ot2_complexation`" in config["agent"]["system_prompt"]
    assert assistant.CONTROL_PROMPT_ADDENDUM not in config["agent"]["system_prompt"]


def test_unscoped_backends_are_unchanged():
    assert set(assistant_openai._server_specs(False, ACTOR)) == {"lab-history", "lab-inventory"}
    assert "lab-control" in assistant_openai._server_specs(True, ACTOR)


def test_scope_ids_are_validated():
    for bad in ("", "../x", "a b", "x" * 81):
        with pytest.raises(ValueError):
            EquipmentScope(bad)


async def test_claude_cli_turn_runs_with_only_the_scoped_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_RUNTIME_DIR", str(tmp_path))
    argv_file = tmp_path / "argv.json"
    fake = tmp_path / "claude"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"json.dump(sys.argv[1:], open({str(argv_file)!r}, 'w'))\n"
        "print(json.dumps({'type': 'result', 'subtype': 'success', 'result': 'ok'}))\n"
    )
    fake.chmod(0o755)
    monkeypatch.setattr(assistant, "_claude_binary", lambda: str(fake))
    async for _ in assistant._run_claude(
        [assistant.ChatMessage(role="user", content="what is loaded?")],
        control=True, actor=ACTOR, scope=EquipmentScope("ot2_complexation"),
    ):
        pass
    argv = json.loads(argv_file.read_text())
    assert argv[argv.index("--allowedTools") + 1] == assistant.CONTROL_TOOL_GLOB
    prompt = argv[argv.index("--append-system-prompt") + 1]
    assert "`ot2_complexation`" in prompt
    assert assistant.CONTROL_PROMPT_ADDENDUM not in prompt


# ── the route ────────────────────────────────────────────────────────────


class _Aggregator:
    def entry(self, equipment_id: str):
        return object() if equipment_id == "ot2_complexation" else None


def _app() -> FastAPI:
    app = FastAPI()
    app.state.aggregator = _Aggregator()
    app.include_router(assistant.build_assistant_router())
    return app


async def _post(app: FastAPI, path: str, body: dict, headers: dict | None = None) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.post(path, json=body, headers=headers or {})


async def test_route_fixes_the_scope_from_its_path(monkeypatch):
    seen: dict[str, Any] = {}

    async def fake_turn(messages, *, control=False, actor=None, scope=None, **_kw):
        seen.update(control=control, actor=actor, scope=scope)
        yield assistant._sse({"type": "done"})

    monkeypatch.setattr(assistant, "DEFAULT_BACKEND", "claude-cli")
    monkeypatch.setattr(assistant, "_claude_binary", lambda: "/bin/true")
    monkeypatch.setattr(assistant, "_run_claude", fake_turn)
    body = {"messages": [{"role": "user", "content": "hi"}], "mode": "control",
            "scope": "dashboard"}  # extra fields cannot widen it
    r = await _post(_app(), "/api/assistant/equipment/ot2_complexation/chat", body,
                    {"X-Auth-User": ACTOR})
    assert r.status_code == 200, r.text
    assert seen == {"control": False, "actor": ACTOR,
                    "scope": EquipmentScope("ot2_complexation")}


async def test_route_needs_a_verified_actor_and_a_known_device():
    body = {"messages": [{"role": "user", "content": "hi"}]}
    app = _app()
    assert (await _post(app, "/api/assistant/equipment/ot2_complexation/chat", body)).status_code == 401
    auth = {"X-Auth-User": ACTOR}
    assert (await _post(app, "/api/assistant/equipment/ot2_hte/chat", body, auth)).status_code == 404
    assert (await _post(app, "/api/assistant/equipment/bad%20id/chat", body, auth)).status_code == 404
    stale = {**body, "conversation_owner": "bob@example.edu"}
    assert (await _post(app, "/api/assistant/equipment/ot2_complexation/chat", stale, auth)).status_code == 409
