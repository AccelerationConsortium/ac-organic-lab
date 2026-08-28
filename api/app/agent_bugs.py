"""Agent error-reporting bridge: ``POST /api/agent/bugs``.

Lets a remote lab agent (e.g. Jiaru's autonomous agent that drives the lab via
``lab-skills`` + the equipment API endpoints) report an error it hit and get a
Hermes diagnosis **in the HTTP response** — no separate chat surface, no
copy-paste.

Flow:
1. Authenticate the caller as a machine principal: the request must carry an
   ac_auth ``X-Api-Key`` (an automation account key). We verify it against the
   same ac_auth sidecar the dashboard edge uses (``GET /auth/verify`` accepts
   ``X-Api-Key`` and returns 200 + ``X-Auth-User`` for machine principals, 401
   otherwise). Unauthenticated calls are refused with 401.
2. Shell out to the local Hermes CLI in non-interactive mode
   (``hermes chat -q …``) with a diagnosis prompt built from the error fields.
   This is the same subprocess-relay pattern the assistant bubble uses to reach
   an external agent, but targeted at Hermes.
3. Return Hermes' reply (the diagnosis) in the response.

The relay is intentionally a plain one-shot ``hermes chat -q`` — bounded by a
hard timeout, no session persistence — so each report is a fresh agent loop and
a hung Hermes can never stall the API. The Hermes binary, model, and profile are
configurable via env so the deploy can point at whichever Hermes installation /
profile should answer.

Env:
    AGENT_BUGS_HERMES_BIN    path to the ``hermes`` binary (default: first of
                             $HERMES_BIN, the canonical ~/.local/bin/hermes,
                             the sdl2 venv). -q is appended.
    AGENT_BUGS_HERMES_PROFILE  --profile value (default: unset → Hermes default)
    AGENT_BUGS_HERMES_MODEL   --model value (default: unset → Hermes default)
    AGENT_BUGS_HERMES_TIMEOUT_S  hard wallclock cap on the subprocess
                             (default 240 — a real diagnosis that uses tools
                             routinely takes 1–3 minutes; a too-small cap
                             kills the run mid-investigation and returns 504)
    AUTH_SERVICE_BASE        ac_auth sidecar base (default http://127.0.0.1:8009)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_AUTHZ_BASE_DEFAULT = "http://127.0.0.1:8009"
_HERMES_TIMEOUT_S = 240.0


def _authz_base() -> str:
    return os.environ.get("AUTH_SERVICE_BASE", _AUTHZ_BASE_DEFAULT)


def _hermes_bin() -> str:
    env = os.environ.get("AGENT_BUGS_HERMES_BIN", "").strip()
    if env:
        return env
    for candidate in (
        os.environ.get("HERMES_BIN", "").strip(),
        os.path.expanduser("~/.local/bin/hermes"),
        os.path.expanduser("~/.hermes/hermes-agent/venv/bin/hermes"),
    ):
        if candidate and shutil.which(candidate):
            return candidate
    return "hermes"


def _authz_enforced() -> bool:
    """Escape hatch mirroring CONTROL_AUTHZ_ENFORCE for local dev."""
    return os.environ.get("AGENT_BUGS_AUTHZ_ENFORCE", "true").lower() != "false"


class BugReport(BaseModel):
    """An error a remote agent hit, with enough context to diagnose."""

    endpoint: str = Field(..., description="URL/endpoint that failed, e.g. /api/openapi.json")
    status: Optional[int] = Field(None, description="HTTP status, if applicable")
    reason: Optional[str] = Field(None, description="HTTP reason phrase, if any")
    body: Optional[str] = Field(None, description="Response body / error text")
    context: Optional[str] = Field(None, description="What the agent was doing when it failed")
    traceback: Optional[str] = Field(None, description="Optional stack trace")


async def _verify_api_key(client: httpx.AsyncClient, api_key: str) -> Optional[str]:
    """Verify a machine ``X-Api-Key`` against the ac_auth sidecar.

    Returns the actor email (from ``X-Auth-User``) on success, None on 401.
    Raises on transport errors so callers fail closed when the sidecar is down.
    Mirrors the identity seam control.py / assistant_control.py use.
    """
    resp = await client.get(
        f"{_authz_base()}/auth/verify",
        headers={"X-Api-Key": api_key},
        timeout=10.0,
    )
    if resp.status_code != 200:
        return None
    return resp.headers.get("x-auth-user") or resp.headers.get("X-Auth-User")


def _build_diagnosis_prompt(report: BugReport, actor: str) -> str:
    lines = [
        "A remote lab agent hit an error and is asking you (Hermes) to diagnose it.",
        "Investigate using your tools and the repo context where possible.",
        "Answer with: likely root cause, how to verify/fix, and whether it needs a",
        "human or a code change. Be concrete and concise (a few short paragraphs).",
        "",
        f"Reporter (ac_auth principal): {actor}",
        f"Endpoint: {report.endpoint}",
    ]
    if report.status is not None:
        lines.append(f"HTTP status: {report.status}")
    if report.reason:
        lines.append(f"Reason: {report.reason}")
    if report.body:
        lines.append(f"Response body:\n{report.body}")
    if report.traceback:
        lines.append(f"Traceback:\n{report.traceback}")
    if report.context:
        lines.append(f"Context (what the agent was doing):\n{report.context}")
    return "\n".join(lines)


async def _run_hermes(prompt: str) -> str:
    """Run the Hermes CLI non-interactively with a hard timeout."""
    bin_path = _hermes_bin()
    cmd = [bin_path, "chat", "-q", prompt, "--quiet"]
    profile = os.environ.get("AGENT_BUGS_HERMES_PROFILE", "").strip()
    if profile:
        cmd = [bin_path, "--profile", profile, "chat", "-q", prompt, "--quiet"]
    model = os.environ.get("AGENT_BUGS_HERMES_MODEL", "").strip()
    if model:
        cmd += ["--model", model]
    timeout = float(os.environ.get("AGENT_BUGS_HERMES_TIMEOUT_S", _HERMES_TIMEOUT_S))

    # --quiet suppresses the banner/spinner; the answer is the last text line.
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise HTTPException(504, "Hermes diagnosis timed out")
    if proc.returncode != 0:
        logger.warning("hermes chat failed rc=%s stderr=%s", proc.returncode, err)
        raise HTTPException(502, f"Hermes relay failed (rc={proc.returncode})")
    text = out.decode("utf-8", errors="replace").strip()
    if not text:
        raise HTTPException(502, "Hermes returned an empty diagnosis")
    return text


def build_agent_bugs_router() -> APIRouter:
    router = APIRouter()

    @router.post(
        "/api/agent/bugs",
        tags=["agent-bridge"],
        summary="Ask Hermes to diagnose an error",
        response_model=dict,
    )
    async def agent_bugs(request: Request, report: BugReport) -> Any:
        """Authenticated endpoint: a machine principal reports an error and
        receives Hermes' diagnosis in the response."""
        if not _authz_enforced():
            actor = "local-dev"
        else:
            api_key = request.headers.get("x-api-key")
            if not api_key:
                raise HTTPException(401, "missing X-Api-Key")
            async with httpx.AsyncClient() as client:
                try:
                    actor = await _verify_api_key(client, api_key)
                except httpx.HTTPError as exc:
                    logger.error("auth sidecar unreachable: %s", exc)
                    actor = None
            if not actor:
                raise HTTPException(401, "invalid api key")

        prompt = _build_diagnosis_prompt(report, actor)
        diagnosis = await _run_hermes(prompt)
        return {
            "endpoint": report.endpoint,
            "actor": actor,
            "diagnosis": diagnosis,
        }

    return router
