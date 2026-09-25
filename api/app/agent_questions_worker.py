"""Local Unix-socket worker for Codex answers and Slack feedback delivery.

The dashboard API unit has IPAddressDeny=any and cannot reach Codex's model
service. This separate service has network access, but accepts requests only
on a Unix socket private to the API service account.
"""

from __future__ import annotations

import asyncio
import os

import httpx

from fastapi import FastAPI, HTTPException
from .agent_questions import (
    AgentQuestion, AgentFeedback, CodexUnavailable, _ask_codex,
    _ask_openrouter, _contains_credential,
)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_turn_lock = asyncio.Lock()


@app.post("/questions")
async def answer(body: AgentQuestion) -> dict[str, str]:
    if _turn_lock.locked():
        raise HTTPException(429, "another Codex question is in progress")
    async with _turn_lock:
        try:
            answer = await _ask_codex(body.question, body.context)
        except CodexUnavailable:
            answer = await _ask_openrouter(body.question, body.context)
        return {"answer": answer}


class FeedbackDelivery(AgentFeedback):
    actor: str


@app.post("/feedback")
async def deliver_feedback(body: FeedbackDelivery) -> dict[str, bool]:
    if _contains_credential(body.message) or (body.context and _contains_credential(body.context)):
        raise HTTPException(400, "Remove credentials from the feedback")
    webhook = os.environ.get("AGENT_CONSULTANT_SLACK_WEBHOOK_URL")
    if not webhook:
        raise HTTPException(503, "Agent Consultant feedback delivery is not configured")
    blocks = [_plain_block(f"Agent feedback from {body.actor}"), _plain_block(body.message)]
    if body.context:
        blocks.extend((_plain_block("Context"), _plain_block(body.context)))
    payload = {"blocks": blocks}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            result = await client.post(webhook, json=payload)
    except httpx.HTTPError:
        raise HTTPException(502, "Slack feedback delivery failed") from None
    if result.status_code != 200:
        raise HTTPException(502, "Slack feedback delivery failed")
    return {"delivered": True}


def _plain_block(value: str) -> dict:
    return {"type": "section", "text": {
        "type": "plain_text", "text": value, "emoji": False,
    }}
