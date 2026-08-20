"""Unit tests for the OpenAI-compatible assistant backend.

Covers the pure helpers (tool-call delta accumulation, server specs), the
missing-key refusal, and a full mocked turn: tool round -> tool result ->
text -> done, with the proposal frame + audit hook on the control path. The
chat-completions endpoint is mocked with respx; the MCP layer is replaced by
a fake sessions context so no server processes are spawned.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest
import itertools
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
    # A status frame opens the turn (before the MCP servers even spawn) and
    # another opens each round: every silent stretch before a round's first
    # visible token is exactly what the pill covers.
    assert [f["type"] for f in frames] == [
        "status",
        "status",
        "tool_use",
        "tool_result",
        "status",
        "text",
        "text",
        "done",
    ]
    assert frames[0]["phase"] == "thinking"
    assert frames[2]["name"] == "query_runs"
    assert calls == [("mcp__lab-history__query_runs", {"limit": 5})]


@respx.mock
async def test_tool_use_announced_live_before_tool_executes(monkeypatch) -> None:
    """The tool_use frame must appear as the name streams in, before the
    argument delta and long before the tool actually runs — that is what makes
    the pill show up 'right away' in Control mode."""

    monkeypatch.setenv("ASSISTANT_OPENAI_API_KEY", "sk-or-test")
    monkeypatch.setattr(
        assistant_openai,
        "_mcp_sessions",
        lambda control, actor: _fake_sessions_factory([], json.dumps({"runs": []})),
    )
    # Send the tool name first, then the arguments, then the round end.
    # The name arrives in its own delta, as DeepSeek/OpenRouter actually do.
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
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {"arguments": "{\"limit\": 5}"},
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
            {"usage": {"completion_tokens": 3}, "choices": []},
            "[DONE]",
        ]
    )
    route = respx.post("https://openrouter.ai/api/v1/chat/completions")
    route.side_effect = [
        Response(200, content=round_one, headers={"content-type": "text/event-stream"}),
        Response(200, content=round_two, headers={"content-type": "text/event-stream"}),
    ]

    async def collect() -> list[str]:
        out: list[str] = []
        async for f in assistant_openai.run_openai_turn(
            [ChatMessage(role="user", content="recent runs?")]
        ):
            data = json.loads(f.decode("utf-8")[len("data: "):].strip())
            out.append(data["type"])
        return out

    types = await collect()
    # tool_use appears before tool_result — and immediately after the opening
    # status frames — so the pill goes up the instant the tool is named.
    assert types.index("tool_use") < types.index("tool_result")
    assert set(types[: types.index("tool_use")]) == {"status"}


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
        "status",
        "status",
        "tool_use",
        "tool_result",
        "proposal",
        "status",
        "text",
        "text",
        "done",
    ]
    assert frames[4]["proposal"] == proposal
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


# ---------------------------------------------------------------------------
# Progress signalling (reasoning models)
# ---------------------------------------------------------------------------


def _mock_reasoning_round(reasoning_field: str, beats: int = 3) -> None:
    """One round that thinks for `beats` deltas, then answers."""

    events: list[dict | str] = [
        {"choices": [{"delta": {reasoning_field: f"step {i} "}}]} for i in range(beats)
    ]
    events += [
        {"choices": [{"delta": {"content": "Done."}}]},
        {
            "usage": {
                "completion_tokens": 9,
                "completion_tokens_details": {"reasoning_tokens": 400},
            },
            "choices": [],
        },
        "[DONE]",
    ]
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=Response(
            200,
            content=_sse_body(events),
            headers={"content-type": "text/event-stream"},
        )
    )


@pytest.mark.parametrize("field", ["reasoning", "reasoning_content"])
@respx.mock
async def test_reasoning_deltas_signal_progress_without_leaking_text(
    monkeypatch, field: str
) -> None:
    """A reasoning model spends most of a slow turn emitting thinking tokens.

    Two things must hold, and neither did before: the monologue must NOT
    reach the browser as assistant text, and the stretch must NOT be
    silence — that silence is what made a 40 s answer look like a hung
    connection. OpenRouter normalises the field to ``reasoning``; DeepSeek's
    native API calls it ``reasoning_content``; both are handled.
    """

    monkeypatch.setenv("ASSISTANT_OPENAI_API_KEY", "sk-or-test")
    monkeypatch.setattr(assistant_openai, "STATUS_HEARTBEAT_S", 0.0)
    monkeypatch.setattr(
        assistant_openai,
        "_mcp_sessions",
        lambda control, actor: _fake_sessions_factory([], "{}"),
    )
    _mock_reasoning_round(field)

    frames = _frames(
        [
            f
            async for f in assistant_openai.run_openai_turn(
                [ChatMessage(role="user", content="think hard")]
            )
        ]
    )

    # The thinking text never becomes assistant text.
    assert [f["delta"] for f in frames if f["type"] == "text"] == ["Done."]
    assert not any("step " in json.dumps(f) for f in frames)
    # ...and the wait is covered by pills, not by dead air: two status frames
    # opening the turn and the round, plus one per reasoning delta (heartbeat
    # unthrottled here), all before the first visible token.
    status_before_text = list(
        itertools.takewhile(lambda f: f["type"] != "text", frames)
    )
    assert [f["type"] for f in status_before_text] == ["status"] * 5
    assert all(f["phase"] == "thinking" for f in status_before_text)


@respx.mock
async def test_reasoning_heartbeat_is_throttled(monkeypatch) -> None:
    """Thinking tokens arrive far too fast to forward one frame each. At the
    production interval a short round emits the two opening status frames
    only — one for the turn, one for the round."""

    monkeypatch.setenv("ASSISTANT_OPENAI_API_KEY", "sk-or-test")
    monkeypatch.setattr(
        assistant_openai,
        "_mcp_sessions",
        lambda control, actor: _fake_sessions_factory([], "{}"),
    )
    _mock_reasoning_round("reasoning", beats=50)

    frames = _frames(
        [
            f
            async for f in assistant_openai.run_openai_turn(
                [ChatMessage(role="user", content="think hard")]
            )
        ]
    )
    assert [f["type"] for f in frames].count("status") == 2


# ---------------------------------------------------------------------------
# Dead air (the "Control mode is non-responsive" regression)
# ---------------------------------------------------------------------------


async def test_paced_ticks_while_the_source_is_silent() -> None:
    """Next.js proxies /api/* with a 30 s socket-inactivity timeout, so a turn
    that puts nothing on the wire gets aborted mid-answer. _paced is what
    guarantees that never happens: it ticks on its own while the model is
    quiet, whether or not the provider streams reasoning deltas."""

    async def silent_then_answer():
        await asyncio.sleep(0.12)
        yield {"text": "hi"}

    items = [i async for i in assistant_openai._paced(silent_then_answer(), 0.02)]

    assert items.count({"idle": True}) >= 3
    assert items[-1] == {"text": "hi"}


async def test_paced_reraises_source_failures() -> None:
    """Fail-fast: a failing round must still surface as an error frame, not be
    swallowed by the pacing layer."""

    async def boom():
        raise RuntimeError("model returned HTTP 502")
        yield  # pragma: no cover - generator marker

    with pytest.raises(RuntimeError, match="502"):
        [i async for i in assistant_openai._paced(boom(), 0.01)]


@contextlib.asynccontextmanager
async def _slow_sessions_factory(delay: float):
    async def call(full_name: str, arguments: dict) -> str:
        await asyncio.sleep(delay)
        return json.dumps({"runs": []})

    yield [
        {
            "type": "function",
            "function": {
                "name": "mcp__lab-history__query_runs",
                "description": "",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ], call


@respx.mock
async def test_slow_tool_call_keeps_the_stream_alive(monkeypatch) -> None:
    """The other half of the same regression: a Control tool call reaches real
    devices over the tailnet and can sit for tens of seconds. Between tool_use
    and tool_result the stream used to go completely silent."""

    monkeypatch.setenv("ASSISTANT_OPENAI_API_KEY", "sk-or-test")
    monkeypatch.setattr(assistant_openai, "IDLE_TICK_S", 0.02)
    monkeypatch.setattr(
        assistant_openai,
        "_mcp_sessions",
        lambda control, actor: _slow_sessions_factory(0.15),
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

    types = [f["type"] for f in frames]
    during_tool = frames[types.index("tool_use") + 1 : types.index("tool_result")]
    assert [f["type"] for f in during_tool] == ["status"] * len(during_tool)
    assert len(during_tool) >= 3
    assert during_tool[0]["label"] == "running query_runs…"
