"""Authenticated agent-question route; Codex runs in a separate local worker."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import tempfile
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)

TIMEOUT_S = 180
WORKER_SOCKET = "/run/agent-consultant/worker.sock"
DEFAULT_ALLOWLIST = Path(__file__).resolve().parents[1] / "agent-consultant.local.json"


class ConsultantAllowlist(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed_principals: list[str] = Field(min_length=1)


class AgentQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    context: str | None = Field(default=None, max_length=16000)


class AgentAnswer(BaseModel):
    actor: str
    answer: str


class AgentFeedback(BaseModel):
    message: str = Field(min_length=1, max_length=2500)
    context: str | None = Field(default=None, max_length=2500)


class FeedbackReceipt(BaseModel):
    actor: str
    delivered: bool


def _check_consultant_access(actor: str) -> None:
    path = Path(os.environ.get("AGENT_CONSULTANT_ALLOWLIST_PATH", DEFAULT_ALLOWLIST))
    try:
        config = ConsultantAllowlist.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        logger.error("Agent Consultant allowlist unavailable: %s", exc)
        raise HTTPException(503, "Agent Consultant allowlist unavailable") from None
    if actor.casefold() not in {name.casefold() for name in config.allowed_principals}:
        raise HTTPException(403, "machine principal is not allowed to use Agent Consultant")


async def _verify_machine(request: Request) -> str:
    api_key = request.headers.get("x-api-key")
    if not api_key:
        raise HTTPException(401, "missing X-Api-Key")
    auth = os.environ.get("AUTH_SERVICE_BASE", "http://127.0.0.1:8009").rstrip("/")
    client = request.app.state.control_client
    try:
        verified = await client.get(
            f"{auth}/auth/verify",
            headers={"X-Api-Key": api_key, "X-Forwarded-Uri": request.url.path},
            timeout=10,
        )
    except httpx.HTTPError:
        raise HTTPException(503, "authentication service unavailable") from None
    if verified.status_code == 403:
        raise HTTPException(403, "machine principal is not allowed on this path")
    if verified.status_code != 200:
        raise HTTPException(401, "invalid API key")
    actor = verified.headers.get("x-auth-user")
    if not actor:
        raise HTTPException(401, "verified principal has no identity")
    _check_consultant_access(actor)
    return actor


def _final_answer(output: bytes) -> str:
    answer = ""
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if event.get("type") == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                answer = item["text"]
    if not answer.strip():
        raise HTTPException(502, "Codex returned no answer")
    return answer.strip()


def _auth_file() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"


async def _stop(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        await proc.communicate()
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await proc.communicate()


async def _ask_codex(question: str, context: str | None = None) -> str:
    binary = os.environ.get("AGENT_QUESTIONS_CODEX_BIN") or shutil.which("codex")
    if not binary:
        raise HTTPException(503, "Codex CLI is unavailable")
    auth_file = _auth_file()
    if not auth_file.is_file():
        raise HTTPException(503, "Codex CLI is not logged in for the API service")
    prompt = (
        "Answer the following lab agent's question from your general knowledge "
        "and the context supplied by the caller. You have no repository, "
        "device, or lab-record access. Do not claim to have inspected live state. "
        "Do not edit files, run hardware actions, write records, or ask for "
        "elevated permissions. If the answer requires an action, explain what "
        "a human should review.\n\n"
        f"Question:\n{question}\n\nCaller-supplied context:\n{context or '(none)'}"
    )
    # Keep the lab service's credentials out of model-generated commands. The
    # CLI gets only its own login, never an API key from the dashboard service.
    keep = ("PATH", "HOME", "CODEX_HOME", "LANG", "LC_ALL", "TZ", "TMPDIR")
    env = {key: os.environ[key] for key in keep if key in os.environ}
    cmd = (
        binary, "exec", "--json", "--ephemeral", "--ignore-user-config",
        "--strict-config", "--disable", "remote_plugin",
        "--disable", "skill_mcp_dependency_install",
        "--disable", "shell_tool", "--disable", "unified_exec",
        "--sandbox", "read-only", "--config", "approval_policy=never",
        "--config", "agents.enabled=false", "--config", "mcp_servers={}",
        "--skip-git-repo-check",
    )
    # The API unit has ProtectHome=read-only. Codex needs a writable home for
    # its own temporary state even with --ephemeral, so give each turn a
    # private temporary home that points at the service's existing login.
    with tempfile.TemporaryDirectory(prefix="lab-codex-question-") as home:
        (Path(home) / "auth.json").symlink_to(auth_file)
        env["CODEX_HOME"] = home
        workspace = Path(home) / "workspace"
        workspace.mkdir()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, "--cd", str(workspace), "-", cwd=workspace, env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError:
            raise HTTPException(503, "Codex CLI could not start") from None
        try:
            output, _errors = await asyncio.wait_for(
                proc.communicate(prompt.encode()), timeout=TIMEOUT_S
            )
        except asyncio.TimeoutError:
            await _stop(proc)
            raise HTTPException(504, "Codex answer timed out") from None
        except asyncio.CancelledError:
            await _stop(proc)
            raise
        if proc.returncode != 0:
            logger.warning("agent question Codex turn failed: rc=%s", proc.returncode)
            raise HTTPException(502, "Codex could not answer this question")
        return _final_answer(output)


def build_agent_questions_router() -> APIRouter:
    router = APIRouter(prefix="/api/agent", tags=["agent-bridge"])

    @router.post("/questions", response_model=AgentAnswer)
    async def ask(request: Request, response: Response, body: AgentQuestion) -> AgentAnswer:
        actor = await _verify_machine(request)
        logger.info("agent question: actor=%s chars=%d", actor, len(body.question))
        answer = await _ask_worker(body)
        response.headers["Cache-Control"] = "no-store"
        return AgentAnswer(actor=actor, answer=answer)

    @router.post("/feedback", response_model=FeedbackReceipt)
    async def feedback(request: Request, response: Response, body: AgentFeedback) -> FeedbackReceipt:
        actor = await _verify_machine(request)
        delivered = await _call_worker("/feedback", {"actor": actor, **body.model_dump()})
        if delivered.get("delivered") is not True:
            raise HTTPException(502, "Agent Consultant did not confirm feedback delivery")
        response.headers["Cache-Control"] = "no-store"
        return FeedbackReceipt(actor=actor, delivered=True)

    return router


async def _ask_worker(body: AgentQuestion) -> str:
    result = await _call_worker("/questions", body.model_dump())
    return result["answer"]


async def _call_worker(path: str, payload: dict) -> dict:
    socket = os.environ.get("AGENT_CONSULTANT_SOCKET", WORKER_SOCKET)
    try:
        async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=socket)) as worker:
            result = await worker.post(
                f"http://agent-consultant{path}", json=payload,
                timeout=TIMEOUT_S + 10,
            )
    except httpx.HTTPError:
        raise HTTPException(503, "Agent Consultant worker is unavailable") from None
    if result.status_code != 200:
        detail = result.json().get("detail", "Agent Consultant request failed")
        raise HTTPException(result.status_code, detail)
    return result.json()
