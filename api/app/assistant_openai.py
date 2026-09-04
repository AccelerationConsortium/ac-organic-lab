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
(``status`` / ``text`` / ``tool_use`` / ``tool_result`` / ``proposal`` /
``done`` / ``error``), so the browser bubble does not know which backend
answered.

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

import asyncio
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
    _declined_from_tool_result,
    _history_server_env,
    _mcp_server_command,
    _plan_from_tool_result,
    _proposal_from_tool_result,
    _refusal_from_tool_result,
    _sse,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = os.environ.get(
    "ASSISTANT_OPENAI_BASE_URL", "https://openrouter.ai/api/v1"
)
OPENAI_MODEL = os.environ.get("ASSISTANT_OPENAI_MODEL", "qwen/qwen3.8-2.4t-a95b")
OPENAI_CONTROL_MODEL = os.environ.get("ASSISTANT_OPENAI_CONTROL_MODEL", OPENAI_MODEL)
# Optional OpenRouter reasoning_effort, applied to CONTROL turns only. DeepSeek
# reasoning models orbit far longer on "max"/unset (tens of seconds of silent
# think before the first visible token, which reads as "not responding"). Ask
# mode is flash and never set this. Mirror of bitacora's llm.py: a top-level
# `reasoning_effort` in the request body ("" = leave unset, the OpenRouter
# default).
OPENAI_CONTROL_REASONING_EFFORT = os.environ.get(
    "ASSISTANT_OPENAI_CONTROL_REASONING_EFFORT", ""
).strip()

# One round = one chat-completions request. Tool-using answers need several;
# a runaway loop should die well before the wallclock cap does it for us.
MAX_TOOL_ROUNDS = 12

# Step 1j enforcement: bounced back to the model EXACTLY ONCE per control turn
# when its reply ends with prose but no lab-control terminal call
# (propose_action / propose_plan / decline_proposal). This is the mechanical
# backstop for the prompt's rule 1 — small models routinely "understand" the
# request, narrate an answer, and never call the tool, which renders no
# authorize button. The message reaches the model as a user-role turn but is
# written by this backend, never by the operator, and is not persisted into
# the bubble's history (the convo is rebuilt from the bubble each request).
# Since 2026-09-04 the nudge round also FORCES the call at the protocol level
# (see ``_terminal_tool_defs``): the text below is the explanation the model
# reads, ``tool_choice="required"`` is what makes it comply.
CONTROL_TERMINAL_NUDGE = (
    "[automated harness check — the operator did not write this] Your reply "
    "ended without a lab-control terminal call. Control mode requires every "
    "reply to end with exactly one of: propose_action, propose_plan, or "
    "decline_proposal(reason_code, explanation). If the operator's last "
    "message asked for a device action that is in scope, make the propose "
    "call NOW — the authorize button renders ONLY from the tool call, never "
    "from your text. Otherwise call decline_proposal (reason_code "
    "'informational' if there was simply no action to propose). Do not "
    "repeat your previous text."
)

# The short (post ``mcp__<server>__`` prefix) names of the three terminal
# tools. A control turn that never lands one of these — nor a proposal/plan/
# refusal/decline payload from any tool — is incomplete (see the nudge above).
_TERMINAL_TOOL_NAMES = frozenset({"propose_action", "propose_plan", "decline_proposal"})


def _terminal_tool_defs(tool_defs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The subset of ``tool_defs`` that are lab-control terminal tools.

    The nudge round offers ONLY these, with ``tool_choice="required"``, so the
    model cannot end that round in prose a second time (2026-09-04). Asking
    for the call in a user-role message (the nudge text) turned out to be one
    more instruction a flash-tier model can ignore; ``tool_choice`` is a
    protocol-level constraint the provider enforces. Reads it may have wanted
    to make first are deliberately excluded here — by the nudge it has had a
    full turn to read, and the honest answer if it still cannot decide is
    ``decline_proposal``, which is in the set.
    """
    return [
        d
        for d in tool_defs
        if (d.get("function") or {}).get("name", "").split("__")[-1] in _TERMINAL_TOOL_NAMES
    ]


def _no_terminal_declined(explanation: str) -> dict[str, Any]:
    """A harness-authored ``declined`` payload for a control turn that ended
    with no terminal outcome. The bubble renders it as the muted "No action
    proposed" chip — the why of a missing button must always be on screen
    (UI_DESIGN §5 Step 1j)."""
    return {"declined": {"reason_code": "other", "explanation": explanation}}


MCP_CALL_TIMEOUT_S = 30.0
# How often a silent stretch may re-announce the phase: a throttle, so that
# thinking tokens (which arrive far too fast to forward one frame each) cost
# at most one status frame per second.
STATUS_HEARTBEAT_S = 1.0
# The hard ceiling on dead air, enforced whether or not the model is emitting
# anything. This one is not cosmetic — it is the SSE keep-alive that holds the
# connection open. Next.js proxies /api/* to this service with http-proxy's
# `proxyTimeout`: 30 s of socket INACTIVITY, the framework default. A turn
# that puts no bytes on the wire for 30 s therefore has its upstream
# connection aborted mid-answer, and the bubble reports "connection lost"
# while the model is still thinking. Stay well under it.
IDLE_TICK_S = 5.0


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
    reasoning_seen = False
    # Tool names streamed so far, so we can announce each tool_use the instant
    # its name first appears — not after the whole round finishes streaming.
    announced: set[int] = set()
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
            # A reasoning model spends most of a slow turn HERE, emitting
            # thinking tokens under a field this loop used to ignore
            # (OpenRouter normalises it to ``reasoning``; DeepSeek's native
            # API calls it ``reasoning_content``). Dropping it silently is
            # what made a 40 s answer look like a hung connection: no frame
            # reached the browser until the round was already over. We still
            # do not forward the text — the bubble shows a pill, not the
            # model's monologue — but the *fact* of progress is now a signal.
            elif delta.get("reasoning") or delta.get("reasoning_content"):
                reasoning_seen = True
                yield {"reasoning": True}
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                _merge_tool_call_delta(pending, tc)
                # The name almost always arrives in the first delta of a tool
                # call. Announce the tool_use the moment it does, so the bubble
                # shows the tool as "working" while the arguments still stream
                # in — otherwise the call is a surprise that only appears once
                # it has already finished.
                if idx not in announced and pending[idx].get("name"):
                    announced.add(idx)
                    yield {"tool_name": pending[idx]["name"]}
    yield {
        "tool_calls": [pending[i] for i in sorted(pending)],
        "usage": usage,
        "reasoning_seen": reasoning_seen,
    }


async def _paced(
    source: AsyncIterator[dict[str, Any]], interval: float
) -> AsyncIterator[dict[str, Any]]:
    """Re-yield ``source``'s items, inserting ``{"idle": True}`` whenever
    ``interval`` passes with nothing to forward.

    Reasoning deltas already covered part of a slow round, but only when the
    provider streams them: the queue before the first token, and a round that
    goes straight to a tool call, still leave the wire silent. Past 30 s of
    silence the proxy aborts the connection (see ``IDLE_TICK_S``), so the
    pulse has to be unconditional rather than tied to what the model happens
    to emit.
    """

    queue: asyncio.Queue[Any] = asyncio.Queue()
    end = object()

    async def pump() -> None:
        try:
            async for item in source:
                await queue.put(item)
        except Exception as exc:  # noqa: BLE001 - re-raised in the consumer below
            await queue.put(exc)
        else:
            await queue.put(end)

    task = asyncio.ensure_future(pump())
    # The getter deliberately outlives a tick. asyncio.wait() leaves it
    # pending on timeout, where wait_for() would cancel it — and a get()
    # cancelled in the same cycle it was handed an item drops that item on
    # the floor. Losing a text delta to the keep-alive would be a poor trade.
    getter: Any = None
    try:
        while True:
            if getter is None:
                getter = asyncio.ensure_future(queue.get())
            done, _still = await asyncio.wait({getter}, timeout=interval)
            if not done:
                # A pump that ended without a sentinel was cancelled; re-raise
                # rather than tick forever at a corpse.
                if task.done() and queue.empty():
                    task.result()
                    return
                yield {"idle": True}
                continue
            item = getter.result()
            getter = None
            if item is end:
                return
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        if getter is not None:
            getter.cancel()
        task.cancel()
        with contextlib.suppress(BaseException):
            await task


async def _call_tool(
    call: "Callable[[str, dict[str, Any]], Awaitable[str]]", tc: dict[str, Any]
) -> str:
    """Parse one streamed tool call's arguments and invoke it, mapping both
    failure modes to the error payload the model sees as the tool result."""

    try:
        args = json.loads(tc["arguments"] or "{}")
        if not isinstance(args, dict):
            raise ValueError("arguments must be an object")
        return await call(tc["name"], args)
    except KeyError:
        return json.dumps({"error": f"unknown tool {tc['name']}"})
    except (json.JSONDecodeError, ValueError) as exc:
        return json.dumps({"error": f"invalid tool arguments: {exc}"})


# ---------------------------------------------------------------------------
# The turn driver (same contract as assistant._run_claude)
# ---------------------------------------------------------------------------


async def run_openai_turn(
    messages: list[ChatMessage],
    *,
    control: bool = False,
    actor: str | None = None,
    on_proposal: "Callable[[dict[str, Any]], Awaitable[None]] | None" = None,
    on_plan: "Callable[[dict[str, Any]], Awaitable[None]] | None" = None,
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
    tokens_reasoning = 0
    saw_reasoning = False
    cached_in: int | None = None
    terminal = "incomplete"
    # Step 1j: has this turn landed a lab-control terminal outcome — a
    # propose/decline tool call, or any proposal / plan / refusal / decline
    # payload? Gates the one-shot CONTROL_TERMINAL_NUDGE below.
    terminal_call_seen = False
    nudged = False
    # Set by the nudge for exactly the next request: offer only the terminal
    # tools and force a call. Cleared as soon as that payload is built.
    force_terminal = False

    # Before anything slow happens — MCP servers spawning, the model queueing
    # — so the bubble shows a live pill from the moment the request lands
    # rather than an empty box, and the first byte reaches the proxy at once.
    yield _sse({"type": "status", "phase": "thinking", "label": "waiting…"})

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
                        if include_control and not terminal_call_seen:
                            yield _sse(
                                {
                                    "type": "declined",
                                    **_no_terminal_declined(
                                        f"The assistant used all {MAX_TOOL_ROUNDS} tool "
                                        "rounds before proposing or declining, so no "
                                        "authorize button was produced. Ask again for "
                                        "one named action on one device."
                                    ),
                                }
                            )
                        yield _sse(
                            {
                                "type": "error",
                                "message": f"stopped after {MAX_TOOL_ROUNDS} tool rounds",
                            }
                        )
                        return
                    if time.monotonic() > deadline:
                        terminal = "timeout"
                        # The largest source of "understood but no button" in
                        # the 2026-08-28 → 09-04 journal (7 of 91 control
                        # turns): a reasoning model orbiting past the wallclock
                        # cap. A bare error frame reads as the assistant
                        # ignoring the request; say what happened.
                        if include_control and not terminal_call_seen:
                            yield _sse(
                                {
                                    "type": "declined",
                                    **_no_terminal_declined(
                                        f"The assistant ran out of time ({DEFAULT_TIMEOUT_S:.0f} s) "
                                        "before proposing or declining, so no authorize "
                                        "button was produced. Ask again for one named "
                                        "action on one device."
                                    ),
                                }
                            )
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
                    # Control turns are the slow ones; cap their reasoning orbit
                    # when configured. Ask (flash) never sets it.
                    if include_control and OPENAI_CONTROL_REASONING_EFFORT:
                        payload["reasoning_effort"] = OPENAI_CONTROL_REASONING_EFFORT
                    if force_terminal:
                        force_terminal = False
                        terminal_defs = _terminal_tool_defs(tool_defs)
                        if terminal_defs:
                            payload["tools"] = terminal_defs
                            payload["tool_choice"] = "required"
                    rounds += 1
                    round_text = ""
                    tool_calls: list[dict[str, Any]] = []
                    # Tool names already surfaced live via `tool_name` frames,
                    # so the post-round execution loop does not re-emit them.
                    announced_names: set[str] = set()
                    # Announce the phase BEFORE the request goes out. Every
                    # round starts with a stretch that produces no visible
                    # token — model queueing, then reasoning — and that
                    # stretch is the whole of the "assistant feels slow"
                    # complaint. One frame here turns an empty bubble into a
                    # live pill for the entire wait. The label evolves as the
                    # round develops so the operator can tell "still queuing"
                    # from "reasoning" instead of staring at a static pill.
                    yield _sse({"type": "status", "phase": "thinking", "label": "waiting…"})
                    last_beat = time.monotonic()
                    # What the pill says while the round is quiet. It starts
                    # honest about not knowing ("waiting…") and upgrades the
                    # moment thinking tokens prove the request is live.
                    phase_label = "waiting…"
                    async for item in _paced(
                        _stream_round(client, payload), IDLE_TICK_S
                    ):
                        if "text" in item:
                            round_text += item["text"]
                            yield _sse({"type": "text", "delta": item["text"]})
                        elif "reasoning" in item or "idle" in item:
                            # Two kinds of silence, one pill. "reasoning" is
                            # live thinking tokens (throttled: they arrive far
                            # too fast to forward one frame each). "idle" is
                            # _paced reporting that nothing at all arrived for
                            # a tick — the queue-before-first-token stretch,
                            # which streams no deltas of any kind. Either way
                            # a frame goes out, which keeps the connection warm
                            # and the elapsed counter honest.
                            if "reasoning" in item:
                                phase_label = "reasoning…"
                            now = time.monotonic()
                            if now - last_beat >= STATUS_HEARTBEAT_S:
                                last_beat = now
                                yield _sse(
                                    {
                                        "type": "status",
                                        "phase": "thinking",
                                        "label": phase_label,
                                    }
                                )
                        elif "tool_name" in item:
                            # Live tool_use: emitted the moment the model first
                            # names a tool it will call, while its arguments are
                            # still streaming. The bubble shows a "working" pill
                            # immediately instead of only after the outcome.
                            pretty = item["tool_name"].split("__")[-1]
                            announced_names.add(item["tool_name"])
                            yield _sse({"type": "tool_use", "name": pretty})
                        else:
                            tool_calls = item["tool_calls"]
                            usage = item.get("usage") or {}
                            tokens_out += usage.get("completion_tokens") or 0
                            details = usage.get("prompt_tokens_details") or {}
                            if isinstance(details.get("cached_tokens"), int):
                                cached_in = (cached_in or 0) + details["cached_tokens"]
                            out_details = usage.get("completion_tokens_details") or {}
                            if isinstance(out_details.get("reasoning_tokens"), int):
                                tokens_reasoning += out_details["reasoning_tokens"]
                            saw_reasoning = saw_reasoning or bool(
                                item.get("reasoning_seen")
                            )

                    if not tool_calls:
                        if include_control and not terminal_call_seen:
                            if not nudged:
                                # Rule-1 enforcement: bounce the incomplete
                                # reply back exactly once. The prose already
                                # streamed to the bubble stays visible; the
                                # model's next round adds the missing call.
                                nudged = True
                                force_terminal = True
                                convo.append(
                                    {"role": "assistant", "content": round_text or ""}
                                )
                                convo.append(
                                    {"role": "user", "content": CONTROL_TERMINAL_NUDGE}
                                )
                                yield _sse(
                                    {
                                        "type": "status",
                                        "phase": "thinking",
                                        "label": "completing proposal…",
                                    }
                                )
                                continue
                            # Nudged once and still no terminal call: stop and
                            # tell the operator instead of ending silently —
                            # the invisible version of this is exactly the
                            # "understood but no button" complaint.
                            yield _sse(
                                {
                                    "type": "declined",
                                    "declined": {
                                        "reason_code": "other",
                                        "explanation": (
                                            "The assistant ended its turn without "
                                            "proposing or declining, so no authorize "
                                            "button was produced. Rephrase the "
                                            "request, or use the device tile "
                                            "controls."
                                        ),
                                    },
                                }
                            )
                            logger.warning(
                                "control turn ended without a terminal lab-control "
                                "call despite nudge: user=%s model=%s",
                                actor,
                                model,
                            )
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
                        # Already shown live via a tool_name frame; only emit a
                        # fresh tool_use now for any name that did not stream
                        # its name early (provider didn't send name-too-early).
                        if tc["name"] not in announced_names:
                            yield _sse({"type": "tool_use", "name": pretty})
                        # The other silent stretch: a Control tool call reaches
                        # real devices over the tailnet and can sit for the full
                        # MCP_CALL_TIMEOUT_S without a byte on the wire. Pulse
                        # for its whole duration, same reason as _paced.
                        running = asyncio.ensure_future(_call_tool(call, tc))
                        try:
                            while True:
                                done, _still = await asyncio.wait(
                                    {running}, timeout=IDLE_TICK_S
                                )
                                if done:
                                    break
                                yield _sse(
                                    {
                                        "type": "status",
                                        "phase": "thinking",
                                        "label": f"running {pretty}…",
                                    }
                                )
                            result_text = running.result()
                        finally:
                            running.cancel()
                        yield _sse({"type": "tool_result", "name": pretty})
                        if pretty in _TERMINAL_TOOL_NAMES:
                            terminal_call_seen = True
                        proposal = _proposal_from_tool_result({"content": result_text})
                        if proposal is not None:
                            terminal_call_seen = True
                            if on_proposal is not None:
                                try:
                                    await on_proposal(proposal)
                                except Exception:  # noqa: BLE001 - audit must not break the stream
                                    logger.warning(
                                        "assistant_proposal audit failed", exc_info=True
                                    )
                            yield _sse({"type": "proposal", "proposal": proposal})
                        # Step 1i: a propose_plan result is the multi-step
                        # sibling of a proposal — one card, approved by hash.
                        plan = _plan_from_tool_result({"content": result_text})
                        if plan is not None:
                            terminal_call_seen = True
                            if on_plan is not None:
                                try:
                                    await on_plan(plan)
                                except Exception:  # noqa: BLE001 - audit must not break the stream
                                    logger.warning(
                                        "assistant_plan audit failed", exc_info=True
                                    )
                            yield _sse({"type": "plan", "plan": plan})
                        # Step 1j: a refused proposal and an explicit decline
                        # are terminal outcomes the operator must SEE — the
                        # button's absence alone is not an explanation.
                        refusal = _refusal_from_tool_result({"content": result_text})
                        if refusal is not None:
                            terminal_call_seen = True
                            yield _sse({"type": "proposal_refused", "refusal": refusal})
                        declined = _declined_from_tool_result({"content": result_text})
                        if declined is not None:
                            terminal_call_seen = True
                            yield _sse({"type": "declined", "declined": declined})
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
    except (asyncio.CancelledError, GeneratorExit):
        # The browser went away — the operator pressed Stop, closed the panel,
        # or asked something new. Starlette closes the response generator,
        # which lands here. Name it in the turn log and let it propagate.
        terminal = "client_disconnected"
        raise
    finally:
        logger.info(
            "assistant turn done: user=%s mode=%s elapsed=%.1fs num_turns=%s "
            "api_ms=%s tokens_out=%s tokens_reasoning=%s cache_read=%s rc=%s "
            "timed_out=%s rate_limit=%s backend=openai model=%s outcome=%s",
            actor or "unauthenticated(dev-open)",
            "control" if include_control else "ask",
            time.monotonic() - started,
            rounds,
            None,
            tokens_out,
            tokens_reasoning
            if tokens_reasoning
            else ("unreported" if saw_reasoning else 0),
            cached_in,
            None,
            terminal == "timeout",
            None,
            model,
            terminal,
        )
