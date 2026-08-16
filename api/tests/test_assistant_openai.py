"""Unit tests for the OpenAI-compatible assistant backend.

Covers the pure helpers (tool-call delta accumulation, server specs), the
missing-key refusal, and a full mocked turn: tool round -> tool result ->
text -> done, with the proposal frame + audit hook on the control path. The
chat-completions endpoint is mocked with respx; the MCP layer is replaced by
a fake sessions context so no server processes are spawned.
"""

from __future__ import annotations

import contextlib
import json

import pytest
import respx
from httpx import Response

from app import assistant_openai
from app.assistant import ChatMessage


def _frames(chunks: list[bytes]) -> list[dict]:
    out = []
    for chunk in chunks:
        text = chunk.decode()
        assert text.startswith("data: ")
        out.append(json.loads(text[len("data: ") :]))
    return out


def _sse_body(events: list[dict | str]) -> bytes:
    lines = []
    for e in events:
        payload = e if isinstance(e, str) else json.dumps(e)
        lines.append(f"data: {payload}\n\n")
    return "".join(lines).encode()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_merge_tool_call_delta_accumulates_across_chunks() -> None:
    pending: dict[int, dict] = {}
    assistant_openai._merge_tool_call_delta(
        pending, {"index": 0, "id": "call_1", "function": {"name": "mcp__lab-history__query_runs"}}
    )
    assistant_openai._merge_tool_call_delta(
        pending, {"index": 0, "function": {"arguments": '{"limit"'}}
    )
    assistant_openai._merge_tool_call_delta(
        pending, {"index": 0, "function": {"arguments": ": 5}"}}
    )
    assert pending == {
        0: {
            "id": "call_1",
            "name": "mcp__lab-history__query_runs",
            "arguments": '{"limit": 5}',
        }
    }


def test_server_specs_control_requires_actor(monkeypatch) -> None:
    monkeypatch.delenv("LAB_DASHBOARD_API_URL", raising=False)
    ask = assistant_openai._server_specs(False, "op@lab.local")
    assert set(ask) == {"lab-history", "lab-inventory"}
    assert ask["lab-history"]["env"]["LAB_ACTOR"] == "op@lab.local"

    anonymous_control = assistant_openai._server_specs(True, None)
    assert set(anonymous_control) == {"lab-history", "lab-inventory"}

    control = assistant_openai._server_specs(True, "op@lab.local")
    assert set(control) == {"lab-history", "lab-inventory", "lab-control"}
    assert control["lab-control"]["env"]["LAB_ACTOR"] == "op@lab.local"


# ---------------------------------------------------------------------------
# Turn driver
# ---------------------------------------------------------------------------


async def test_missing_key_yields_error_frame(monkeypatch) -> None:
    monkeypatch.delenv("ASSISTANT_OPENAI_API_KEY", raising=False)
    frames = _frames(
        [
            f
            async for f in assistant_openai.run_openai_turn(
                [ChatMessage(role="user", content="hi")]
            )
        ]
    )
    assert frames == [
        {
            "type": "error",
            "message": "ASSISTANT_OPENAI_API_KEY is not set on the dashboard host.",
        }
    ]


@contextlib.asynccontextmanager
async def _fake_sessions_factory(calls: list, result_text: str):
    async def call(full_name: str, arguments: dict) -> str:
        calls.append((full_name, arguments))
        return result_text

    tool_defs = [
        {
            "type": "function",
            "function": {
                "name": "mcp__lab-history__query_runs",
                "description": "",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    yield tool_defs, call


def _mock_two_rounds(first_tool_args: str) -> None:
    """First request answers with one tool call; second with text + stop."""

    round_one = _sse_body(
        [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_a",
                                    "function": {
                                        "name": "mcp__lab-history__query_runs",
                                        "arguments": first_tool_args,
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
            {"usage": {"completion_tokens": 7}, "choices": []},
            "[DONE]",
        ]
    )
    round_two = _sse_body(
        [
            {"choices": [{"delta": {"content": "All "}}]},
            {"choices": [{"delta": {"content": "good."}}]},
            {"usage": {"completion_tokens": 3}, "choices": []},
            "[DONE]",
        ]
    )
    route = respx.post("https://openrouter.ai/api/v1/chat/completions")
    route.side_effect = [
        Response(200, content=round_one, headers={"content-type": "text/event-stream"}),
        Response(200, content=round_two, headers={"content-type": "text/event-stream"}),
    ]


@respx.mock
async def test_tool_round_then_text(monkeypatch) -> None:
    monkeypatch.setenv("ASSISTANT_OPENAI_API_KEY", "sk-or-test")
    calls: list = []
    monkeypatch.setattr(
        assistant_openai,
        "_mcp_sessions",
        lambda control, actor: _fake_sessions_factory(calls, json.dumps({"runs": []})),
    )
    _mock_two_rounds('{"limit": 5}')

    frames = _frames(
        [
            f
            async for f in assistant_openai.run_openai_turn(
                [ChatMessage(role="user", content="recent runs?")]
            )
        ]
    )
    assert [f["type"] for f in frames] == ["tool_use", "tool_result", "text", "text", "done"]
    assert frames[0]["name"] == "query_runs"
    assert calls == [("mcp__lab-history__query_runs", {"limit": 5})]


@respx.mock
async def test_proposal_frame_and_audit_hook(monkeypatch) -> None:
    monkeypatch.setenv("ASSISTANT_OPENAI_API_KEY", "sk-or-test")
    proposal = {"equipment_id": "xarm_translocation", "action": "graph.move_to"}
    audited: list = []

    async def on_proposal(p: dict) -> None:
        audited.append(p)

    monkeypatch.setattr(
        assistant_openai,
        "_mcp_sessions",
        lambda control, actor: _fake_sessions_factory(
            [], json.dumps({"proposal": proposal})
        ),
    )
    _mock_two_rounds("{}")

    frames = _frames(
        [
            f
            async for f in assistant_openai.run_openai_turn(
                [ChatMessage(role="user", content="move the arm")],
                control=True,
                actor="op@lab.local",
                on_proposal=on_proposal,
            )
        ]
    )
    assert [f["type"] for f in frames] == [
        "tool_use",
        "tool_result",
        "proposal",
        "text",
        "text",
        "done",
    ]
    assert frames[2]["proposal"] == proposal
    assert audited == [proposal]


@respx.mock
async def test_http_error_surfaces_as_error_frame(monkeypatch) -> None:
    monkeypatch.setenv("ASSISTANT_OPENAI_API_KEY", "sk-or-test")
    monkeypatch.setattr(
        assistant_openai,
        "_mcp_sessions",
        lambda control, actor: _fake_sessions_factory([], "{}"),
    )
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=Response(402, content=b'{"error":{"message":"insufficient credits"}}')
    )

    frames = _frames(
        [
            f
            async for f in assistant_openai.run_openai_turn(
                [ChatMessage(role="user", content="hi")]
            )
        ]
    )
    assert frames[-1]["type"] == "error"
    assert "402" in frames[-1]["message"]
