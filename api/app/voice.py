"""Voice transcription proxy for the assistant bubble.

``POST /api/assistant/voice/transcribe`` forwards a push-to-talk clip to the
loopback STT service (``stt/``, port 8070) and returns its text. The split of
responsibilities mirrors the rest of the assistant surface:

* **This endpoint owns identity** — same rule as ``/api/assistant/chat``:
  the verified ``X-Auth-User`` header (set by the Next.js middleware, never
  client-supplied) is required. Anonymous audio is refused rather than
  transcribed; the actor lands in the journald line, the audio does not.
* **The STT service owns nothing but the model.** It binds loopback-only, so
  this proxy is its single caller on the network.

Voice is an INPUT channel only. The transcript is returned to the browser and
becomes an ordinary chat message the operator can see (and, in Control mode,
must review) — transcription never triggers actuation, never touches a
device, and never bypasses the proposal/authorize flow.

Env:
  ASSISTANT_STT_URL   base URL of the STT service (default http://127.0.0.1:8070);
                      unset the default by setting it to "" to disable voice.
"""

from __future__ import annotations

import logging
import os

import httpx
from fastapi import APIRouter, File, HTTPException, Request, UploadFile

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # mirror the STT service's own cap


def _stt_url() -> str:
    return os.environ.get("ASSISTANT_STT_URL", "http://127.0.0.1:8070").rstrip("/")


def build_voice_router() -> APIRouter:
    router = APIRouter(prefix="/api/assistant/voice", tags=["assistant"])

    @router.get("/health")
    async def health(request: Request) -> dict:
        """Whether the mic button should render at all — the same pattern as
        ``/api/assistant/health`` gating the whole bubble."""
        url = _stt_url()
        if not url:
            return {"configured": False}
        client: httpx.AsyncClient = request.app.state.control_client
        try:
            r = await client.get(f"{url}/health", timeout=3.0)
            body = r.json()
        except Exception:
            return {"configured": False}
        return {
            "configured": bool(body.get("loaded")),
            "model": body.get("model"),
            # Server-side neural TTS (Kokoro). The bubble prefers it over the
            # browser's own speechSynthesis voices when true.
            "tts": bool(body.get("tts")),
        }

    @router.post("/transcribe")
    async def transcribe(request: Request, audio: UploadFile = File(...)) -> dict:
        actor = request.headers.get("x-auth-user")
        if not actor:
            raise HTTPException(401, "sign in to use voice input")
        url = _stt_url()
        if not url:
            raise HTTPException(503, "voice input is not configured on this host")

        blob = await audio.read()
        if len(blob) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "clip too large")
        if not blob:
            raise HTTPException(422, "empty clip")

        client: httpx.AsyncClient = request.app.state.control_client
        try:
            r = await client.post(
                f"{url}/transcribe",
                files={"audio": (audio.filename or "clip", blob, audio.content_type or "application/octet-stream")},
                timeout=30.0,
            )
        except httpx.HTTPError:
            raise HTTPException(502, "speech service unreachable")
        if r.status_code != 200:
            detail = "transcription failed"
            try:
                detail = r.json().get("detail", detail)
            except Exception:
                pass
            raise HTTPException(r.status_code if r.status_code >= 400 else 502, detail)

        body = r.json()
        # Latency + who, never what was said.
        logger.info(
            "voice transcribe: user=%s audio_s=%s elapsed_ms=%s",
            actor, body.get("audio_s"), body.get("elapsed_ms"),
        )
        return {"text": body.get("text", ""), "elapsed_ms": body.get("elapsed_ms")}

    @router.post("/speak")
    async def speak(request: Request) -> "Response":
        """Synthesize a short utterance (the shaped read-aloud summary).
        Same identity rule as /transcribe: it spends lab GPU time, so it is
        not an anonymous surface."""
        from fastapi.responses import Response

        actor = request.headers.get("x-auth-user")
        if not actor:
            raise HTTPException(401, "sign in to use voice output")
        url = _stt_url()
        if not url:
            raise HTTPException(503, "voice is not configured on this host")
        body = await request.json()
        text = str(body.get("text", ""))[:600]

        client: httpx.AsyncClient = request.app.state.control_client
        try:
            r = await client.post(f"{url}/speak", json={"text": text}, timeout=15.0)
        except httpx.HTTPError:
            raise HTTPException(502, "speech service unreachable")
        if r.status_code != 200:
            raise HTTPException(r.status_code if r.status_code >= 400 else 502, "synthesis failed")
        return Response(content=r.content, media_type="audio/wav")

    return router
