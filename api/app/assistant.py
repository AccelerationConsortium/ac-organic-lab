"""Lab assistant chat endpoint (Claude Code subprocess backend).

The browser bubble (``web/src/components/AssistantBubble.tsx``) POSTs to
``/api/assistant/chat`` and consumes Server-Sent Events. Instead of calling
the Anthropic API directly (which would need ``ANTHROPIC_API_KEY``), this
endpoint shells out to the locally-installed ``claude`` CLI in
non-interactive mode. That subprocess uses the dashboard user's Claude
Code OAuth login and automatically inherits the ``lab-history`` MCP server
that was registered with ``claude mcp add``, so the same seven read-only
tools are available without any API plumbing.

Configuration
-------------
* ``ASSISTANT_CLAUDE_BIN`` -- override the binary path (default: first
  ``claude`` on PATH).
* ``ASSISTANT_CLAUDE_MODEL`` -- model alias passed to ``claude --model``;
  default ``sonnet`` to keep cost off the Opus tier.
* ``ASSISTANT_CLAUDE_CWD`` -- working directory for the subprocess. Must be
  a directory where the ``lab-history`` MCP server is reachable (Local-scope
  registrations only resolve from the project root). Defaults to the repo
  root inferred from this file.
* ``ASSISTANT_CLAUDE_TIMEOUT_S`` -- hard wallclock cap per turn
  (default 120).

Safety
------
* ``--allowedTools mcp__lab-history__*`` restricts the subprocess to the
  lab MCP server. Bash, file ops, web search, etc. are not in the allowlist
  and will be denied if the model tries to call them.
* ``--permission-mode default`` keeps Claude Code's normal permission
  prompts; with the empty allowlist for everything else, the model can't
  silently use forbidden tools.
* ``--no-session-persistence`` so each request is a fresh agent loop --
  the conversation history is passed in the prompt, not via session resume.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_MODEL = os.environ.get("ASSISTANT_CLAUDE_MODEL", "sonnet")
DEFAULT_TIMEOUT_S = float(os.environ.get("ASSISTANT_CLAUDE_TIMEOUT_S", "120"))
ALLOWED_TOOL_GLOB = "mcp__lab-history__*"


def _repo_root() -> Path:
    # api/app/assistant.py -> api/app -> api -> repo root
    return Path(__file__).resolve().parents[2]


def _claude_binary() -> str | None:
    """Resolve the path to the Claude Code CLI.

    The dashboard runs under systemd with a minimal PATH that excludes
    ``~/.local/bin``, so ``shutil.which`` alone usually returns None on the
    lab host even when the user has it installed. We honour an explicit
    override, then fall back to a small list of well-known install paths
    so a default install just works without editing the unit.
    """

    override = os.environ.get("ASSISTANT_CLAUDE_BIN")
    if override:
        return override
    found = shutil.which("claude")
    if found:
        return found
    candidates = [
        Path.home() / ".local" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
        Path("/opt/claude/bin/claude"),
        # Common second home when the service user differs from the
        # interactive user that ran `claude login`.
        Path("/home/sdl2/.local/bin/claude"),
    ]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


def _claude_cwd() -> str:
    override = os.environ.get("ASSISTANT_CLAUDE_CWD")
    if override:
        return override
    return str(_repo_root())


SYSTEM_PROMPT = """You are the AC Organic Self-driving Lab assistant. You help
lab operators understand what is happening to equipment in real time and
across history.

You have one MCP server connected: lab-history. Its tools are all read-only:

* list_equipment_now -- live snapshot of every device (id, kind, equipment_status,
  message, fetch_error, latency_ms). Use this first when you need the canonical
  equipment_id for other tools, or to answer "what's running right now".
* query_equipment_events -- past state transitions, errors, startup/shutdown
  for one device.
* query_service_uptime -- reachability transitions + overall uptime % over a
  window for one device.
* query_sensor_readings -- environmental sensor history (~1/min).
* query_runs -- recent dosing-run records.
* query_well_results -- per-well dispense results for one run.
* tail_journald -- last N lines of one of the dashboard's systemd units.

You cannot actuate hardware. If the user asks you to, say so and offer to
investigate the relevant logs/history instead.

Be terse. Operators are glancing at a small chat panel, not reading prose.

* Default to 1-3 sentences. Stretch only when the user explicitly asks for
  detail.
* Show the answer first; skip preamble like "I checked X and Y" or "Let me
  look into that".
* Use a short bulleted list only when the answer is genuinely a list of 3+
  items. Otherwise, prose.
* When you cite history, include the device_id and a relative time
  ("3 hours ago"). If the data does not answer the question, say so plainly
  rather than speculate."""


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------


def _sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, default=str)}\n\n".encode("utf-8")


def _format_prompt(messages: list[ChatMessage]) -> str:
    """Render the conversation as a single prompt string.

    Claude Code's --print mode takes one prompt; we don't manage session-id
    state, so the prior turns are inlined verbatim and the latest user
    message ends the prompt. The role markers are conventional enough that
    Claude reliably treats them as a conversation transcript.
    """

    if len(messages) == 1:
        return messages[0].content
    lines: list[str] = ["Conversation so far:"]
    for m in messages[:-1]:
        marker = "User" if m.role == "user" else "Assistant"
        lines.append(f"\n{marker}: {m.content}")
    lines.append("\n\nNew user message:\n" + messages[-1].content)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# stream-json event -> SSE frame translation
# ---------------------------------------------------------------------------


def _translate_event(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert one ``claude -p --output-format stream-json`` line into zero
    or more frames suitable for the AssistantBubble SSE consumer.

    The Bubble understands ``text`` (token delta), ``tool_use`` (model is
    about to call a tool), ``tool_result`` (tool returned), ``done``, and
    ``error``. Everything else from claude-code's richer event taxonomy is
    dropped on the floor.
    """

    out: list[dict[str, Any]] = []
    etype = event.get("type")

    if etype == "stream_event":
        inner = event.get("event") or {}
        itype = inner.get("type")
        if itype == "content_block_delta":
            delta = inner.get("delta") or {}
            if delta.get("type") == "text_delta":
                text = delta.get("text") or ""
                if text:
                    out.append({"type": "text", "delta": text})
        elif itype == "content_block_start":
            block = inner.get("content_block") or {}
            if block.get("type") == "tool_use":
                name = block.get("name") or "tool"
                # Strip the "mcp__<server>__" prefix so the bubble shows a
                # short tool name; full name is still in the title attr.
                pretty = name.split("__")[-1] if "__" in name else name
                out.append({"type": "tool_use", "name": pretty})

    elif etype == "user":
        # Sent back to ourselves when a tool result is appended to the
        # transcript. Surface a "tool_result" frame so the Bubble can flip
        # the spinner to a checkmark.
        message = event.get("message") or {}
        content = message.get("content") or []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    # We don't have the tool name on the result block, but
                    # the Bubble's match-most-recent logic accepts any name
                    # so we pass through "tool" if absent.
                    out.append({"type": "tool_result", "name": "tool"})

    elif etype == "result":
        # Final wrap-up. is_error=true means a hard failure that didn't
        # produce a usable assistant reply.
        if event.get("is_error"):
            out.append(
                {
                    "type": "error",
                    "message": event.get("result")
                    or event.get("subtype")
                    or "Claude returned an error",
                }
            )
        else:
            out.append({"type": "done"})

    elif etype == "system" and event.get("subtype") == "status":
        status = event.get("status")
        if status == "error":
            out.append({"type": "error", "message": event.get("message") or "claude error"})

    return out


# ---------------------------------------------------------------------------
# Subprocess driver
# ---------------------------------------------------------------------------


async def _run_claude(messages: list[ChatMessage]) -> AsyncIterator[bytes]:
    binary = _claude_binary()
    if binary is None:
        yield _sse(
            {
                "type": "error",
                "message": (
                    "claude CLI not found on PATH. Install Claude Code or set "
                    "ASSISTANT_CLAUDE_BIN to its full path."
                ),
            }
        )
        return

    prompt = _format_prompt(messages)
    args = [
        binary,
        "--print",
        "--output-format",
        "stream-json",
        "--include-partial-messages",
        "--verbose",  # required alongside stream-json
        "--no-session-persistence",
        "--append-system-prompt",
        SYSTEM_PROMPT,
        "--allowedTools",
        ALLOWED_TOOL_GLOB,
        "--model",
        DEFAULT_MODEL,
        "--permission-mode",
        "default",
        prompt,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=_claude_cwd(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        yield _sse({"type": "error", "message": f"could not spawn {binary}"})
        return

    assert proc.stdout is not None

    timeout_handle: asyncio.TimerHandle | None = None
    timed_out = False

    def _on_timeout() -> None:
        nonlocal timed_out
        timed_out = True
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    loop = asyncio.get_running_loop()
    timeout_handle = loop.call_later(DEFAULT_TIMEOUT_S, _on_timeout)

    try:
        while True:
            try:
                line = await proc.stdout.readline()
            except asyncio.CancelledError:
                # Client disconnected. Kill the subprocess so it doesn't
                # keep burning quota on a response no one will see.
                if proc.returncode is None:
                    proc.kill()
                raise
            if not line:
                break
            try:
                event = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                logger.debug("non-JSON line from claude: %s", line[:200])
                continue
            for frame in _translate_event(event):
                yield _sse(frame)
    finally:
        if timeout_handle is not None:
            timeout_handle.cancel()
        if proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        stderr_bytes = b""
        if proc.stderr is not None:
            try:
                stderr_bytes = await proc.stderr.read()
            except Exception:  # noqa: BLE001
                pass

    if timed_out:
        yield _sse(
            {
                "type": "error",
                "message": f"claude exceeded {DEFAULT_TIMEOUT_S:.0f}s timeout",
            }
        )
        return
    if proc.returncode and proc.returncode != 0:
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")[-2000:]
        logger.warning("claude exited %s: %s", proc.returncode, stderr_text)
        yield _sse(
            {
                "type": "error",
                "message": f"claude exited {proc.returncode}: {stderr_text or 'no stderr'}",
            }
        )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def build_assistant_router() -> APIRouter:
    router = APIRouter(prefix="/api/assistant", tags=["assistant"])

    @router.get("/health")
    async def health() -> dict[str, Any]:
        binary = _claude_binary()
        return {
            "configured": binary is not None,
            "backend": "claude-code-cli",
            "binary": binary,
            "model": DEFAULT_MODEL,
            "allowed_tools": ALLOWED_TOOL_GLOB,
            "cwd": _claude_cwd(),
        }

    @router.post("/chat")
    async def chat(request: Request, body: ChatRequest) -> StreamingResponse:
        if _claude_binary() is None:
            raise HTTPException(
                status_code=503,
                detail="claude CLI is not installed on the dashboard host",
            )

        async def gen() -> AsyncIterator[bytes]:
            try:
                async for frame in _run_claude(body.messages):
                    yield frame
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("assistant stream errored")
                yield _sse({"type": "error", "message": str(exc)})

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return router


__all__ = ["build_assistant_router", "SYSTEM_PROMPT", "DEFAULT_MODEL"]
