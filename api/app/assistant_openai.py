"""OpenAI-compatible chat backend for the lab assistant (OpenRouter et al.).

``assistant.py``'s claude-CLI backend shells out to Claude Code; this module
is the second backend: it speaks the OpenAI chat-completions protocol to any
compatible endpoint (default: OpenRouter) and drives the SAME stdio MCP
servers — ``lab-history`` and ``lab-inventory``, plus ``lab-control`` in
Control mode — through its own tool loop. The safety story is unchanged and
worth restating: the toolset is the boundary. This loop can only call tools
the propose/read-only servers expose, the acting identity is bound into the servers' environment
(never model-chosen), and a ``propose_action`` result still only renders a
confirm card the operator must click (ARCHITECTURE decision #10).

It emits byte-for-byte the same SSE frames as ``assistant._run_claude``
(``text`` / ``tool_use`` / ``tool_result`` / ``proposal`` / ``done`` /
``error``), so the browser bubble does not know which backend answered.

Configuration (all env):

* ``ASSISTANT_OPENAI_BASE_URL`` -- OpenAI-compatible endpoint
  (default ``https://openrouter.ai/api/v1``).
* ``ASSISTANT_OPENAI_API_KEY`` -- bearer token. The backend refuses to run
  without it. Note this deliberately walks back part of decision #10's
  "no API key in the dashboard env" rationale — recorded trade, 2026-08-13.
* ``ASSISTANT_OPENAI_MODEL`` -- Ask-mode model id
  (default ``qwen/qwen3.8-2.4t-a95b``).
* ``ASSISTANT_OPENAI_CONTROL_MODEL`` -- Control-mode model id
  (default: same as ``ASSISTANT_OPENAI_MODEL``).

Backend *selection* (which mode uses this module at all) lives in
``assistant.py`` (``ASSISTANT_BACKEND`` / ``ASSISTANT_CONTROL_BACKEND``).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from typing import Any, AsyncIterator, Awaitable, Callable

import httpx

from .assistant import (
    CONTROL_PROMPT_ADDENDUM,
    DEFAULT_TIMEOUT_S,
    SYSTEM_PROMPT,
    ChatMessage,
    _control_server_env,
    _history_server_env,
    _mcp_server_command,
    _proposal_from_tool_result,
    _sse,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = os.environ.get(
    "ASSISTANT_OPENAI_BASE_URL", "https://openrouter.ai/api/v1"
)
OPENAI_MODEL = os.environ.get("ASSISTANT_OPENAI_MODEL", "qwen/qwen3.8-2.4t-a95b")
OPENAI_CONTROL_MODEL = os.environ.get("ASSISTANT_OPENAI_CONTROL_MODEL", OPENAI_MODEL)

# One round = one chat-completions request. Tool-using answers need several;
# a runaway loop should die well before the wallclock cap does it for us.
MAX_TOOL_ROUNDS = 12
MCP_CALL_TIMEOUT_S = 30.0


def api_key() -> str | None:
    """Read at call time (not import) so a key pasted into the EnvironmentFile
    takes effect on the next service restart without code reload ordering."""

    key = os.environ.get("ASSISTANT_OPENAI_API_KEY", "").strip()
    return key or None


# ---------------------------------------------------------------------------
# MCP plumbing
# ---------------------------------------------------------------------------


def _server_specs(control: bool, actor: str | None) -> dict[str, dict[str, Any]]:
    """Mirror ``assistant._write_mcp_config``'s server set + env binding,
    minus the JSON file (we spawn the servers ourselves)."""

    history_cmd, history_args = _mcp_server_command("lab-history-mcp")
    # Includes LAB_HISTORY_TOOLS: this loop registers whatever list_tools
    # returns, so the include-list on the server is its only tool filter.
    history_env = _history_server_env(actor)
    inventory_cmd, inventory_args = _mcp_server_command("lab-inventory-mcp")
    inventory_env: dict[str, str] = {}
    bitacora_url = os.environ.get("BITACORA_URL")
    if bitacora_url:
        inventory_env["BITACORA_URL"] = bitacora_url
    specs: dict[str, dict[str, Any]] = {
        "lab-history": {"command": history_cmd, "args": history_args, "env": history_env},
        "lab-inventory": {
            "command": inventory_cmd,
            "args": inventory_args,
            "env": inventory_env,
        },
    }
    if control and actor:
        control_cmd, control_args = _mcp_server_command("lab-control-mcp")
        specs["lab-control"] = {
            "command": control_cmd,
            "args": control_args,
            "env": _control_server_env(actor),
        }
    return specs


@contextlib.asynccontextmanager
async def _mcp_sessions(control: bool, actor: str | None):
    """Spawn the mode's MCP servers; yield ``(tool_defs, call)`` where
    ``tool_defs`` is the OpenAI ``tools`` array and ``call(name, args)``
    invokes the right server. Namespacing matches the claude CLI
    (``mcp__<server>__<tool>``) so journal greps line up across backends."""

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async with contextlib.AsyncExitStack() as stack:
        tool_defs: list[dict[str, Any]] = []
        routes: dict[str, tuple[Any, str]] = {}
        for server_name, spec in _server_specs(control, actor).items():
            params = StdioServerParameters(
                command=spec["command"],
                args=spec["args"],
                # Full service env + the binding extras — same inheritance the
                # CLI gives its servers. lab-history needs LAB_DB_PATH etc.
                env={**os.environ, **spec["env"]},
            )
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            listed = await session.list_tools()
            for tool in listed.tools:
                full = f"mcp__{server_name}__{tool.name}"
                routes[full] = (session, tool.name)
                tool_defs.append(
                    {
                        "type": "function",
                        "function": {
                            "name": full,
                            "description": tool.description or "",
                            "parameters": tool.inputSchema
                            or {"type": "object", "properties": {}},
                        },
                    }
                )

        async def call(full_name: str, arguments: dict[str, Any]) -> str:
            import datetime

            session, bare = routes[full_name]
            result = await session.call_tool(
                bare,
                arguments,
                read_timeout_seconds=datetime.timedelta(seconds=MCP_CALL_TIMEOUT_S),
            )
            texts = [
                c.text
                for c in result.content
                if getattr(c, "type", None) == "text" and isinstance(c.text, str)
            ]
            return "\n".join(texts) if texts else json.dumps({"error": "empty tool result"})

        yield tool_defs, call


# ---------------------------------------------------------------------------
# Chat-completions streaming
# ---------------------------------------------------------------------------


def _merge_tool_call_delta(
    pending: dict[int, dict[str, Any]], delta: dict[str, Any]
) -> None:
    """Fold one streamed ``tool_calls`` delta entry into the accumulator."""

    idx = delta.get("index", 0)
    slot = pending.setdefault(idx, {"id": None, "name": "", "arguments": ""})
    if delta.get("id"):
        slot["id"] = delta["id"]
    fn = delta.get("function") or {}
    if fn.get("name"):
        slot["name"] += fn["name"]
    if fn.get("arguments"):
        slot["arguments"] += fn["arguments"]


async def _stream_round(
    client: httpx.AsyncClient,
    payload: dict[str, Any],
) -> AsyncIterator[dict[str, Any]]:
    """One chat-completions request. Yields ``{"text": ...}`` per content
    delta, then one final ``{"tool_calls": [...], "usage": ...}`` summary."""

    pending: dict[int, dict[str, Any]] = {}
    usage: dict[str, Any] | None = None
    async with client.stream("POST", "/chat/completions", json=payload) as resp:
        if resp.status_code != 200:
            body = (await resp.aread()).decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"{payload['model']} returned HTTP {resp.status_code}: {body}")
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            data = line[6:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(chunk.get("usage"), dict):
                usage = chunk["usage"]
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            text = delta.get("content")
            if text:
                yield {"text": text}
            for tc in delta.get("tool_calls") or []:
                _merge_tool_call_delta(pending, tc)
    yield {
        "tool_calls": [pending[i] for i in sorted(pending)],
        "usage": usage,
    }


# ---------------------------------------------------------------------------
# The turn driver (same contract as assistant._run_claude)
# ---------------------------------------------------------------------------


async def run_openai_turn(
    messages: list[ChatMessage],
    *,
    control: bool = False,
    actor: str | None = None,
    on_proposal: "Callable[[dict[str, Any]], Awaitable[None]] | None" = None,
) -> AsyncIterator[bytes]:
    key = api_key()
    include_control = control and bool(actor)
    model = OPENAI_CONTROL_MODEL if include_control else OPENAI_MODEL
    if key is None:
        yield _sse(
            {
                "type": "error",
                "message": "ASSISTANT_OPENAI_API_KEY is not set on the dashboard host.",
            }
        )
        return

    system_prompt = SYSTEM_PROMPT + (CONTROL_PROMPT_ADDENDUM if include_control else "")
    # Identity is templated from the same config var that selects the model
    # (ASSISTANT_OPENAI_MODEL / ASSISTANT_OPENAI_CONTROL_MODEL), so it stays
    # truthful without any hardcoded model name to edit when the model changes:
    # flip the env var, restart, and the self-report follows automatically.
    identity = (
        "You are running as %s via OpenRouter. If the user asks what model you "
        "are, reply with exactly that identifier." % model
    )
    system_prompt = identity + "\n\n" + system_prompt
    convo: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    convo += [{"role": m.role, "content": m.content} for m in messages]

    started = time.monotonic()
    deadline = started + DEFAULT_TIMEOUT_S
    rounds = 0
    tokens_out = 0
    cached_in: int | None = None
    terminal = "incomplete"

    try:
        async with _mcp_sessions(include_control, actor) as (tool_defs, call):
            async with httpx.AsyncClient(
                base_url=DEFAULT_BASE_URL,
                headers={
                    "Authorization": f"Bearer {key}",
                    "X-Title": "ac-organic-lab assistant",
                },
                timeout=httpx.Timeout(60.0, connect=10.0),
            ) as client:
                while True:
                    if rounds >= MAX_TOOL_ROUNDS:
                        terminal = "tool_round_cap"
                        yield _sse(
                            {
                                "type": "error",
                                "message": f"stopped after {MAX_TOOL_ROUNDS} tool rounds",
                            }
                        )
                        return
                    if time.monotonic() > deadline:
                        terminal = "timeout"
                        yield _sse(
                            {
                                "type": "error",
                                "message": f"exceeded {DEFAULT_TIMEOUT_S:.0f}s timeout",
                            }
                        )
                        return
                    payload = {
                        "model": model,
                        "messages": convo,
                        "tools": tool_defs,
                        "stream": True,
                        "stream_options": {"include_usage": True},
                    }
                    rounds += 1
                    round_text = ""
                    tool_calls: list[dict[str, Any]] = []
                    async for item in _stream_round(client, payload):
                        if "text" in item:
                            round_text += item["text"]
                            yield _sse({"type": "text", "delta": item["text"]})
                        else:
                            tool_calls = item["tool_calls"]
                            usage = item.get("usage") or {}
                            tokens_out += usage.get("completion_tokens") or 0
                            details = usage.get("prompt_tokens_details") or {}
                            if isinstance(details.get("cached_tokens"), int):
                                cached_in = (cached_in or 0) + details["cached_tokens"]

                    if not tool_calls:
                        terminal = "done"
                        yield _sse({"type": "done"})
                        return

                    convo.append(
                        {
                            "role": "assistant",
                            "content": round_text or None,
                            "tool_calls": [
                                {
                                    "id": tc["id"] or f"call_{rounds}_{i}",
                                    "type": "function",
                                    "function": {
                                        "name": tc["name"],
                                        "arguments": tc["arguments"] or "{}",
                                    },
                                }
                                for i, tc in enumerate(tool_calls)
                            ],
                        }
                    )
                    for i, tc in enumerate(tool_calls):
                        pretty = tc["name"].split("__")[-1]
                        yield _sse({"type": "tool_use", "name": pretty})
                        try:
                            args = json.loads(tc["arguments"] or "{}")
                            if not isinstance(args, dict):
                                raise ValueError("arguments must be an object")
                            result_text = await call(tc["name"], args)
                        except KeyError:
                            result_text = json.dumps(
                                {"error": f"unknown tool {tc['name']}"}
                            )
                        except (json.JSONDecodeError, ValueError) as exc:
                            result_text = json.dumps(
                                {"error": f"invalid tool arguments: {exc}"}
                            )
                        yield _sse({"type": "tool_result", "name": pretty})
                        proposal = _proposal_from_tool_result({"content": result_text})
                        if proposal is not None:
                            if on_proposal is not None:
                                try:
                                    await on_proposal(proposal)
                                except Exception:  # noqa: BLE001 - audit must not break the stream
                                    logger.warning(
                                        "assistant_proposal audit failed", exc_info=True
                                    )
                            yield _sse({"type": "proposal", "proposal": proposal})
                        convo.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc["id"] or f"call_{rounds}_{i}",
                                "content": result_text,
                            }
                        )
    except RuntimeError as exc:
        terminal = "error"
        yield _sse({"type": "error", "message": str(exc)})
    finally:
        logger.info(
            "assistant turn done: user=%s mode=%s elapsed=%.1fs num_turns=%s "
            "api_ms=%s tokens_out=%s cache_read=%s rc=%s timed_out=%s "
            "rate_limit=%s backend=openai model=%s outcome=%s",
            actor or "unauthenticated(dev-open)",
            "control" if include_control else "ask",
            time.monotonic() - started,
            rounds,
            None,
            tokens_out,
            cached_in,
            None,
            terminal == "timeout",
            None,
            model,
            terminal,
        )
