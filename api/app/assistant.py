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
* ``ASSISTANT_CLAUDE_CWD`` -- working directory for the subprocess. Defaults
  to a minimal runtime dir *outside* the repo tree (``_runtime_dir()``) so
  Claude Code does not auto-load the repo's large ``CLAUDE.md`` and its
  ~50k-token doc imports on every turn -- that context was recreating the
  prompt cache each request and burning the account's usage limit. The
  ``lab-history`` MCP server no longer depends on cwd: it is passed
  explicitly via ``--mcp-config`` (see below).
* ``ASSISTANT_RUNTIME_DIR`` -- override the runtime dir that holds the
  generated ``mcp.json`` and serves as the default cwd
  (default ``~/.cache/lab-assistant``).
* ``ASSISTANT_CLAUDE_TIMEOUT_S`` -- hard wallclock cap per turn
  (default 120).

Safety
------
* ``--allowedTools mcp__lab-history__*`` restricts the subprocess to the
  lab MCP server. Bash, file ops, web search, etc. are not in the allowlist
  and will be denied if the model tries to call them.
* ``--mcp-config <mcp.json> --strict-mcp-config`` injects *only* the
  read-only ``lab-history`` server, ignoring any filesystem-discovered MCP
  config. This decouples tool availability from cwd, so the minimal cwd
  above keeps working.
* ``--permission-mode default`` keeps Claude Code's normal permission
  prompts; with the empty allowlist for everything else, the model can't
  silently use forbidden tools.
* ``--no-session-persistence`` so each request is a fresh agent loop --
  the conversation history is passed in the prompt, not via session resume.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Literal

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
# Control mode (UI_DESIGN §5) adds the propose-only lab-control server. Neither
# of its tools actuates; the model's most privileged act is returning a
# validated proposal the operator then authorizes in the browser.
CONTROL_TOOL_GLOB = "mcp__lab-control__*"


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


def _runtime_dir() -> Path:
    """Minimal scratch dir for the subprocess: holds the generated
    ``mcp.json`` and doubles as the default cwd. Deliberately outside the
    repo tree so Claude Code finds no project ``CLAUDE.md`` to load."""

    d = Path(
        os.environ.get(
            "ASSISTANT_RUNTIME_DIR", str(Path.home() / ".cache" / "lab-assistant")
        )
    )
    d.mkdir(parents=True, exist_ok=True)
    return d


def _uv_binary() -> str:
    """Resolve ``uv`` for the MCP server spawn, mirroring _claude_binary()'s
    PATH caveat under systemd."""

    found = shutil.which("uv")
    if found:
        return found
    for c in (Path.home() / ".local" / "bin" / "uv", Path("/usr/local/bin/uv")):
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return "uv"


def _mcp_server_command(script: str) -> tuple[str, list[str]]:
    """Resolve how to launch one of our MCP servers: ``(command, args)``.

    Prefer the console script installed beside the *running* interpreter — i.e.
    the same venv serving this app. Going through ``uv run --project`` instead
    makes every chat turn depend on uv being able to sync the project, and uv
    needs ``~/.cache/uv`` **writable**. The deployed unit sets
    ``ProtectHome=read-only``, so under systemd `uv run` fails and the CLI
    reports the server as ``status: "failed"`` — with no tools, no error frame,
    and nothing in the journal. The model then says its tools are unreachable,
    which reads like a connectivity problem rather than a sandbox one.

    Launching the console script directly needs no writable HOME at all
    (verified against a read-only-home sandbox), so it survives the hardening.
    The ``uv run`` path is kept as a fallback for a dev checkout where the api
    package is not installed into the interpreter's own environment.
    """

    candidates = [Path(sys.executable).parent / script]
    found = shutil.which(script)
    if found:
        candidates.append(Path(found))
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c), []
    return _uv_binary(), ["run", "--project", str(_repo_root() / "api"), script]


def _control_server_env(actor: str) -> dict[str, str]:
    """Environment for the spawned ``lab-control`` MCP server.

    The verified actor is bound here (``LAB_ACTOR``) rather than passed as a
    tool argument, so the model cannot choose whose authority it borrows
    (UI_DESIGN §5.3). Selected config vars are forwarded so the control server
    resolves the same registry + authz sidecar as the dashboard.
    """

    env = {"LAB_ACTOR": actor}
    for key in (
        "AUTH_SERVICE_BASE",
        "CONTROL_AUTHZ_ENFORCE",
        "LAB_REGISTRY_PATH",
        "LAB_DASHBOARD_API_URL",
    ):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def _write_mcp_config(*, include_control: bool = False, actor: str | None = None) -> Path:
    """Materialise the explicit MCP config and return its path.

    Always registers the read-only ``lab-history`` server. When
    ``include_control`` (Control mode with a verified ``actor``), also registers
    the propose-only ``lab-control`` server. Every path written here is
    absolute (see :func:`_mcp_server_command`), so the servers resolve
    regardless of the subprocess cwd.
    """

    history_cmd, history_args = _mcp_server_command("lab-history-mcp")
    servers: dict[str, Any] = {
        "lab-history": {
            "type": "stdio",
            "command": history_cmd,
            "args": history_args,
            "env": {},
        }
    }
    if include_control and actor:
        control_cmd, control_args = _mcp_server_command("lab-control-mcp")
        servers["lab-control"] = {
            "type": "stdio",
            "command": control_cmd,
            "args": control_args,
            "env": _control_server_env(actor),
        }
    # A distinct filename per mode so a control-mode config never lingers into
    # a later ask-mode turn (and vice versa).
    name = "mcp.control.json" if include_control and actor else "mcp.json"
    path = _runtime_dir() / name
    path.write_text(json.dumps({"mcpServers": servers}, indent=2))
    return path


def _claude_cwd() -> str:
    override = os.environ.get("ASSISTANT_CLAUDE_CWD")
    if override:
        return override
    return str(_runtime_dir())


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


# Appended to SYSTEM_PROMPT only when the operator is in Control mode and a
# verified actor is bound. Even then, no tool actuates hardware — see
# assistant_control.py.
CONTROL_PROMPT_ADDENDUM = """

CONTROL MODE IS ACTIVE.
You may now PROPOSE a single equipment action for the operator to authorize,
using the lab-control tools. You still cannot actuate hardware yourself: a
proposal only renders a confirm card that a human must read and click.

When the user asks you to make a device do something:
1. Call list_available_actions(equipment_id) to see what the device currently
   allows and which actions are proposable (each with its argument schema).
   Use list_equipment_now first if you need the canonical equipment_id.
2. Call propose_action(equipment_id, action, args, reason) for exactly ONE
   action on ONE device. `action` must be a string from the device's live
   allowed_actions; `reason` is a short human-facing justification.

list_available_actions marks which advertised actions are proposable.
Safety-floor actions must stay reachable without you and are never
proposable: the xArm's stop / connect / clear_errors, and every device's
stop verb (sash.stop, shake.stop, the press's stop). If the user wants
something stopped, point them at the device tile's stop button — do not
propose an alternative action to "work around" a stop.

Proposable kinds beyond the xArm: the OT-2 (liquid_handler), the fume hood
(sash.move), the shaker (startup, shutdown, shake.start,
shake.set_temperature, shake.set_speed), and the press (init, press.up,
press.down, plate.in, plate.out — a press cycle runs like a liquid sequence,
one card per step in order). The HPLC is NOT proposable at all: its queue,
campaign-lock, and standby verbs stay operator/workflow-only, so answer HPLC
control requests by pointing at the operator surfaces instead.

On the OT-2 the full control surface is proposable, under two disciplines:

- Some argument fields are operator-only and never yours to set: startup's
  password / host_alias (the gateway supplies its own from service env),
  pick_up_tip's force (cross-contamination-guard override), move_to's
  force_direct (collision-safe-path override). They are omitted from the
  schemas you are shown; supplying one refuses the whole proposal. Never ask
  the user to paste a device credential into chat.
- Liquid handling is sequence-bound (pick_up_tip -> aspirate -> dispense ->
  drop_tip). Propose steps ONE at a time, in the correct order, and wait for
  the operator to authorize (or dismiss) each card before proposing the next.
  Re-check device state between steps rather than assuming the last step
  landed. If the work is more than a handful of steps, say so and recommend a
  validated workflow plan instead of a long chain of cards.

On the robot arm (xArm), moves are constrained to a motion graph and only
single hops from the current node are advertised (move.<node_id>).
list_available_actions also returns the device's read-only motion_graph
snapshot: current_node, reachable_nodes (the single-hop targets), and
travel_targets (nodes reachable in 2+ hops). Use it to plan and explain a
route, then propose it one hop per confirm card, re-checking state between
hops. If a target is in travel_targets but not reachable_nodes, name the
intermediate hop to propose first rather than calling the move impossible.

The arm's gripper works the same way: transitions are whitelisted per node and
per current stroke, and each legal one is advertised as gripper.<state> (e.g.
gripper.grip_120) — the same names as motion_graph.allowed_gripper_targets. The
arm must be parked, so a gripper action is never advertised mid-move. Picking a
plate up is therefore a sequence — move to the pick position, then the grip,
then move away — so propose it one card at a time like the liquid verbs, and
never describe the gripper as uncontrollable when a gripper.<state> action is
listed.

Operator-only is a property of the action or field, never of who is asking:
do not imply the user lacks permission, and do not describe a proposable
action as needing an operator — every proposal does, that is the point. If a
proposal is refused, relay the reason plainly — never try to route around it."""


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)
    # UI_DESIGN §5: "ask" (default, read-only) or "control" (propose-only).
    # The server decides the actual toolset from the verified identity — a
    # client that lies about its mode gains nothing.
    mode: Literal["ask", "control"] = "ask"


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
# Rate-limit interpretation
# ---------------------------------------------------------------------------


def _format_reset(epoch: Any) -> str | None:
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).strftime(
            "%H:%M UTC"
        )
    except (TypeError, ValueError, OSError):
        return None


def _rate_limit_block_message(info: dict[str, Any] | None) -> str | None:
    """Return a human-readable cause string when a ``rate_limit_event``
    indicates the request was *blocked*.

    A ``status`` of ``"allowed"`` is normal -- the success path also emits a
    ``rate_limit_event`` (often with ``overageStatus: "rejected"`` when the
    account has no overage budget) -- so only a non-allowed status counts as
    the reason a turn failed.
    """

    if not isinstance(info, dict):
        return None
    status = info.get("status")
    if status in (None, "allowed"):
        return None
    reset = _format_reset(info.get("resetsAt"))
    if info.get("overageDisabledReason") == "out_of_credits":
        base = "Claude is out of credits and overage is disabled"
    else:
        kind = info.get("rateLimitType") or "usage"
        base = f"Claude {kind} limit reached"
    return base + (f"; resets at {reset}." if reset else ".")


# ---------------------------------------------------------------------------
# stream-json event -> SSE frame translation
# ---------------------------------------------------------------------------


def _proposal_from_tool_result(block: dict[str, Any]) -> dict[str, Any] | None:
    """Extract a control proposal from a tool_result block, if present.

    ``lab-control``'s ``propose_action`` returns a JSON string of the shape
    ``{"proposal": {...}}``. Claude relays a tool result's content either as a
    plain string or as a list of ``{"type":"text","text":...}`` blocks; handle
    both. Returns the inner proposal dict, or None for any other tool result
    (including refusals, which carry ``error``/``code`` instead)."""

    content = block.get("content")
    texts: list[str] = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and isinstance(c.get("text"), str):
                texts.append(c["text"])
    for text in texts:
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            continue
        # Claude Code wraps MCP tool output as {"result": "<json string>"};
        # other builds pass the tool's JSON through unchanged. The envelope is
        # a CLI implementation detail, not a contract, so accept both rather
        # than pinning to whichever one this host happens to emit.
        if isinstance(data, dict) and isinstance(data.get("result"), str):
            try:
                data = json.loads(data["result"])
            except (json.JSONDecodeError, TypeError):
                pass
        if isinstance(data, dict) and isinstance(data.get("proposal"), dict):
            return data["proposal"]
    return None


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
                    # Control mode: a propose_action result carries a validated
                    # proposal. Surface it as a dedicated frame so the Bubble
                    # can render a confirm card the operator must authorize.
                    proposal = _proposal_from_tool_result(block)
                    if proposal is not None:
                        out.append({"type": "proposal", "proposal": proposal})

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


async def _run_claude(
    messages: list[ChatMessage],
    *,
    control: bool = False,
    actor: str | None = None,
    on_proposal: "Callable[[dict[str, Any]], Awaitable[None]] | None" = None,
) -> AsyncIterator[bytes]:
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

    include_control = control and bool(actor)
    prompt = _format_prompt(messages)
    mcp_config_path = _write_mcp_config(include_control=include_control, actor=actor)
    system_prompt = SYSTEM_PROMPT + (CONTROL_PROMPT_ADDENDUM if include_control else "")
    allowed_tools = ALLOWED_TOOL_GLOB
    if include_control:
        allowed_tools = f"{ALLOWED_TOOL_GLOB} {CONTROL_TOOL_GLOB}"
    args = [
        binary,
        "--print",
        "--output-format",
        "stream-json",
        "--include-partial-messages",
        "--verbose",  # required alongside stream-json
        "--no-session-persistence",
        "--append-system-prompt",
        system_prompt,
        "--mcp-config",
        str(mcp_config_path),
        "--strict-mcp-config",
        "--allowedTools",
        allowed_tools,
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

    last_rate_limit: dict[str, Any] | None = None
    saw_terminal = False  # did we already yield a done/error frame?

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
            if event.get("type") == "rate_limit_event":
                info = event.get("rate_limit_info")
                if isinstance(info, dict):
                    last_rate_limit = info
            for frame in _translate_event(event):
                if frame.get("type") in ("done", "error"):
                    saw_terminal = True
                if frame.get("type") == "proposal" and on_proposal is not None:
                    proposal = frame.get("proposal")
                    if isinstance(proposal, dict):
                        try:
                            await on_proposal(proposal)
                        except Exception:  # noqa: BLE001 - audit must not break the stream
                            logger.warning("assistant_proposal audit failed", exc_info=True)
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
    # If a terminal frame already went out (normal done, or an error the model
    # reported via the result event), don't double-report on exit code.
    if not saw_terminal and proc.returncode and proc.returncode != 0:
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")[-2000:].strip()
        rate_limit_msg = _rate_limit_block_message(last_rate_limit)
        logger.warning(
            "claude exited %s (rate_limit=%s): %s",
            proc.returncode,
            last_rate_limit,
            stderr_text,
        )
        if rate_limit_msg:
            message = rate_limit_msg
        elif stderr_text:
            message = f"claude exited {proc.returncode}: {stderr_text}"
        else:
            message = (
                f"claude exited {proc.returncode} with no error output — "
                "check `journalctl -u ac-organic-lab-api` on the dashboard host."
            )
        yield _sse({"type": "error", "message": message})


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
        # Attribution (Phase 2): X-Auth-User is set by the Next.js middleware
        # after verifying the session — never client-supplied. The backend
        # Claude account is shared, so who-asked lives in this log line.
        actor = request.headers.get("x-auth-user")

        # Control mode is only honoured for a verified actor, and never under
        # the DASHBOARD_CONTROL_OPEN dev bypass (which has no identity to bind
        # a proposal to). The client's requested mode is advisory; the server
        # decides the toolset. Per-equipment authorization is enforced inside
        # the lab-control server's propose_action, against this same actor.
        control_open = os.environ.get("DASHBOARD_CONTROL_OPEN") == "true"
        control = body.mode == "control" and bool(actor) and not control_open

        logger.info(
            "assistant chat: user=%s mode=%s->%s messages=%d",
            actor or "unauthenticated(dev-open)",
            body.mode,
            "control" if control else "ask",
            len(body.messages),
        )

        db = getattr(request.app.state, "db", None)

        async def record_proposal(proposal: dict[str, Any]) -> None:
            """Audit the proposal itself (not just the eventual click) so the
            trail shows what talked the operator into authorizing. Best-effort;
            never blocks or breaks the stream. Paired with the ``origin`` field
            on the later ``control_action`` row (control.py)."""

            if db is None:
                return
            equipment_id = str(proposal.get("equipment_id") or "unknown")
            payload = {
                "actor": actor,
                "action": proposal.get("action"),
                "passthrough_action": proposal.get("passthrough_action"),
                "args": proposal.get("args"),
                "reason": proposal.get("reason"),
                "device_state": proposal.get("device_state"),
            }
            message = (
                f"assistant proposed {proposal.get('action')} on "
                f"{equipment_id} to {actor}"
            )
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                functools.partial(
                    db.record_equipment_event,
                    equipment_id,
                    "assistant_proposal",
                    message=message,
                    payload=payload,
                ),
            )

        async def gen() -> AsyncIterator[bytes]:
            try:
                async for frame in _run_claude(
                    body.messages,
                    control=control,
                    actor=actor,
                    on_proposal=record_proposal if control else None,
                ):
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
