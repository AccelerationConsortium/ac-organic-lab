"""Authorized-run trigger MCP server (``lab-runs``).

The "(future) authorized-run trigger" row of ``docs/AGENTIC_LAB_DESIGN.md``'s trust-tier
table, implemented: an agent may **pull a trigger a human has already loaded**
— start, watch, and cooperatively abort a run whose plan a human authorized in
bitácora — and nothing more.

Trust model
-----------
This server verifies nothing itself, on purpose. The dashboard's workflow
executor (``app/workflow.py``) is the authority: it re-fetches the
authorization, re-checks executability, independently recomputes the package
digest, re-validates layer 3 + layer 4 per step, and re-fetches the
authorization *between* steps so revocation works mid-run. A refusal arrives
here as the executor's 409 and is relayed verbatim. Duplicating any of those
gates client-side would create a second, driftable definition of "may this
run" — the same reason ``assistant_control.py`` lets the device's 412/423 be
the backstop.

The ceiling, stated for the prompt-injection case: with only these tools, the
worst a hostile instruction can achieve is starting or aborting a run **a
human already authorized** — both audited (``plan_run`` rows, ``launched_by``
carries this server's bound actor), both safe by construction (an abort is
cooperative and takes effect at a step boundary; a start of a revoked or
tampered package is refused by the executor). This server MUST NOT grow a
tool that composes, edits, or approves a plan — that is the line AGENTIC_LAB_DESIGN.md
draws, and it is enforced by the toolset, not by this comment.

Actor binding
-------------
Like ``lab-control``: the acting identity is read from the **environment**
(``LAB_ACTOR``), never accepted as a tool argument the model could choose. It
is sent as ``X-Auth-User`` on every call and becomes ``launched_by`` /
``abort_requested`` in the run record. The dashboard API trusts that header by
network position (loopback + the edge), so the attribution is only as honest
as the process environment — which is why the deployment target for this
server is the boxed ``hermes`` principal (HERMES_ACCESS_DESIGN Phase 0), with
``LAB_ACTOR`` pinned to its Phase-1 roster identity (``hermes@lab.local``).
Mutating tools fail closed when ``LAB_ACTOR`` is unset; reads work regardless.

Transport
---------
stdio, spawned by the agent client. ``mcp`` is imported lazily inside
:func:`_build_server` so the tool logic imports and unit-tests without it
(matches ``mcp_server.py`` / ``assistant_control.py``).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Bounds on watch_run's blocking window. SSE keepalives arrive every ~15 s, so
# the per-read timeout below (_READ_TIMEOUT_S) only trips on a dead server,
# while the overall deadline is the caller's (clamped) choice. An agent
# following an hours-long run calls watch_run in a loop with after_seq — it
# must not hold one MCP call open for the whole run.
WATCH_TIMEOUT_MIN_S = 5.0
WATCH_TIMEOUT_MAX_S = 300.0
WATCH_TIMEOUT_DEFAULT_S = 60.0

_READ_TIMEOUT_S = 25.0  # > the executor's 15 s SSE keepalive interval
_HTTP_TIMEOUT_S = 15.0  # non-streaming calls


def _api_base() -> str:
    """Dashboard API base URL. Loopback default — this server runs on the
    dashboard host beside the API process that owns the run registry."""

    return os.environ.get("DASHBOARD_API_BASE", "http://127.0.0.1:8001").rstrip("/")


def _actor() -> str | None:
    """The identity bound into this server's environment, or None."""

    actor = os.environ.get("LAB_ACTOR", "").strip()
    return actor or None


def _headers() -> dict[str, str]:
    actor = _actor()
    return {"X-Auth-User": actor} if actor else {}


def _dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)


def _err(code: str, message: str, **extra: Any) -> str:
    return _dumps({"error": message, "code": code, **extra})


def _clamp_timeout(timeout_s: float) -> float:
    return max(WATCH_TIMEOUT_MIN_S, min(WATCH_TIMEOUT_MAX_S, float(timeout_s)))


# ---------------------------------------------------------------------------
# Tool logic (plain functions; the FastMCP wrappers below just delegate)
# ---------------------------------------------------------------------------


async def _start_run(authorization_id: str, dry_run: bool) -> str:
    actor = _actor()
    if actor is None:
        return _err(
            "no_actor",
            "no actor is bound to this server (LAB_ACTOR unset); starting a "
            "run requires an attributable identity",
        )
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            resp = await client.post(
                f"{_api_base()}/api/workflow/runs",
                json={"authorization_id": authorization_id, "dry_run": bool(dry_run)},
                headers=_headers(),
            )
    except httpx.HTTPError as exc:
        return _err("api_unreachable", f"cannot reach the dashboard API: {exc}")

    if resp.status_code == 409:
        # The executor's gate refused (not executable / revoked / digest
        # mismatch / unreachable bitácora). Relay its reason verbatim — the
        # executor is the authority and its message is the diagnosis.
        detail = _response_detail(resp)
        return _err("refused", detail, authorization_id=authorization_id)
    if resp.status_code != 202:
        return _err(
            "api_error",
            f"dashboard API returned {resp.status_code}: {_response_detail(resp)}",
        )
    return _dumps(resp.json())


async def _get_run(run_id: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            resp = await client.get(
                f"{_api_base()}/api/workflow/runs/{run_id}", headers=_headers()
            )
    except httpx.HTTPError as exc:
        return _err("api_unreachable", f"cannot reach the dashboard API: {exc}")
    if resp.status_code == 404:
        return _err("unknown_run", f"no run {run_id!r}")
    if resp.status_code != 200:
        return _err(
            "api_error",
            f"dashboard API returned {resp.status_code}: {_response_detail(resp)}",
        )
    return _dumps(resp.json())


async def _watch_run(run_id: str, after_seq: int, timeout_s: float) -> str:
    """Follow the run's SSE stream, returning events with ``seq >= after_seq``.

    Returns when the run emits ``done``, when the stream ends, or when the
    (clamped) deadline passes — whichever comes first. The response carries
    ``next_seq`` so a loop can resume exactly where it left off without
    re-reading the replayed prefix.
    """

    deadline = time.monotonic() + _clamp_timeout(timeout_s)
    events: list[dict] = []
    done = False
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_HTTP_TIMEOUT_S, read=_READ_TIMEOUT_S)
        ) as client:
            async with client.stream(
                "GET",
                f"{_api_base()}/api/workflow/runs/{run_id}/events",
                headers=_headers(),
            ) as resp:
                if resp.status_code == 404:
                    return _err("unknown_run", f"no run {run_id!r}")
                if resp.status_code != 200:
                    return _err(
                        "api_error",
                        f"dashboard API returned {resp.status_code} for the event stream",
                    )
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        # event: labels and ": keepalive" comments — the JSON
                        # data line repeats the type, so only data lines matter.
                        if time.monotonic() > deadline:
                            break
                        continue
                    try:
                        ev = json.loads(line[len("data:"):].strip())
                    except json.JSONDecodeError:
                        continue
                    if isinstance(ev, dict) and ev.get("seq", -1) >= after_seq:
                        events.append(ev)
                    if isinstance(ev, dict) and ev.get("type") == "done":
                        done = True
                        break
                    if time.monotonic() > deadline:
                        break
    except httpx.HTTPError as exc:
        # A read timeout mid-stream is an ordinary "nothing new inside the
        # window" outcome only if we saw the stream open; connection errors
        # before that are reported.
        if not events and not done:
            return _err("api_unreachable", f"event stream failed: {exc}")

    next_seq = max((ev.get("seq", -1) for ev in events), default=after_seq - 1) + 1
    return _dumps(
        {"run_id": run_id, "events": events, "next_seq": max(next_seq, after_seq),
         "done": done}
    )


async def _abort_run(run_id: str) -> str:
    actor = _actor()
    if actor is None:
        return _err(
            "no_actor",
            "no actor is bound to this server (LAB_ACTOR unset); an abort "
            "requires an attributable identity",
        )
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            resp = await client.post(
                f"{_api_base()}/api/workflow/runs/{run_id}/abort", headers=_headers()
            )
    except httpx.HTTPError as exc:
        return _err("api_unreachable", f"cannot reach the dashboard API: {exc}")
    if resp.status_code == 404:
        return _err("unknown_run", f"no run {run_id!r}")
    if resp.status_code != 200:
        return _err(
            "api_error",
            f"dashboard API returned {resp.status_code}: {_response_detail(resp)}",
        )
    return _dumps(resp.json())


def _response_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        if isinstance(body, dict) and "detail" in body:
            return str(body["detail"])
    except json.JSONDecodeError:
        pass
    return resp.text[:500]


# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------


def _build_server():
    """Build the FastMCP server. ``mcp`` is imported here so the module and
    its tool logic import without the package installed."""

    from mcp.server.fastmcp import FastMCP

    server = FastMCP("lab-runs")

    @server.tool()
    async def start_run(authorization_id: str, dry_run: bool = False) -> str:
        """Start a run whose plan a human already authorized in bitácora. Does
        NOT compose or approve anything: the executor re-verifies the
        authorization (executable, unrevoked, digest-intact) and refuses with
        the reason if any gate fails. ``dry_run=True`` preflights the same
        plan live (role resolution, readiness, interlocks) without claiming
        or actuating — use it first when the authorization is not fresh.
        Returns ``{run_id, status, authorization_id}`` or ``{error, code}``."""

        return await _start_run(authorization_id, dry_run)

    @server.tool()
    async def get_run(run_id: str) -> str:
        """Snapshot of a run: status (running|finished|refused), launched_by,
        abort_requested, event count, and the final result once finished."""

        return await _get_run(run_id)

    @server.tool()
    async def watch_run(
        run_id: str, after_seq: int = 0, timeout_s: float = WATCH_TIMEOUT_DEFAULT_S
    ) -> str:
        """Block up to ``timeout_s`` (clamped to 5–300 s) collecting run events
        with ``seq >= after_seq``; returns early on the terminal ``done``
        event. For a long run, call in a loop passing back ``next_seq`` —
        do not try to cover the whole run with one huge timeout."""

        return await _watch_run(run_id, after_seq, timeout_s)

    @server.tool()
    async def abort_run(run_id: str) -> str:
        """Request a cooperative abort: the current step finishes (mid-step is
        the device's territory), everything after is skipped. Audited under
        this server's bound actor."""

        return await _abort_run(run_id)

    return server


def run() -> None:
    """Entry point for the ``lab-runs-mcp`` console script (pyproject).

    Boots the FastMCP server over stdio and blocks until the client closes
    the stream. Logs go to stderr; stdout is reserved for MCP JSON-RPC."""

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    logger.info(
        "lab-runs MCP server: api=%s actor=%s", _api_base(), _actor() or "UNSET"
    )
    _build_server().run()


if __name__ == "__main__":  # pragma: no cover
    run()
