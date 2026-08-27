"""Loopback STT service: POST an audio clip, get text back.

Runs as its own systemd unit on 127.0.0.1:8070 — never exposed on the
tailnet. The only caller is the dashboard's /api/assistant/voice/transcribe
endpoint (api/app/voice.py), which owns identity; this process owns nothing
but the model. Audio is decoded in a TemporaryDirectory and never persisted
or logged: what an operator says near a mic is not telemetry.

Env:
  STT_MODEL           model id            (default Qwen/Qwen3-ASR-1.7B-hf)
  STT_DEVICE          torch device        (default cuda)
  STT_HOST/STT_PORT   bind                (default 127.0.0.1:8070)
  STT_EQUIPMENT_YAML  registry for the vocabulary prompt
  STT_VOCAB_FILE      optional extra terms, one per line
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile

from .engine import DEFAULT_MODEL, QwenAsrEngine
from .vocab import build_context

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # ~30s of any sane browser codec, with margin


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.context = build_context()
    logger.info("vocabulary prompt: %d chars", len(app.state.context))
    # Load in a thread so a health probe during the multi-second load answers.
    app.state.engine = None
    model_id = os.environ.get("STT_MODEL", DEFAULT_MODEL)
    device = os.environ.get("STT_DEVICE", "cuda")

    # STT_MODEL="" skips the load entirely — the tests' seam for exercising
    # the HTTP contract without a GPU or a 4 GB download.
    if not model_id:
        app.state.load_task = asyncio.create_task(asyncio.sleep(0))
        yield
        return

    def _load() -> QwenAsrEngine:
        return QwenAsrEngine(model_id=model_id, device=device)

    app.state.load_task = asyncio.create_task(asyncio.to_thread(_load))

    def _store(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        if task.exception():
            logger.error("model load failed", exc_info=task.exception())
        else:
            app.state.engine = task.result()

    app.state.load_task.add_done_callback(_store)
    yield
    app.state.load_task.cancel()


app = FastAPI(title="sdl-lab-stt", lifespan=lifespan)

# One clip at a time on the GPU. Push-to-talk from a handful of operators
# never queues in practice; if two clips do race, the second waits ~0.5s.
_gpu_lock = asyncio.Lock()


@app.get("/health")
async def health() -> dict:
    task = app.state.load_task
    return {
        "status": "healthy",
        "model": os.environ.get("STT_MODEL", DEFAULT_MODEL),
        "loaded": app.state.engine is not None,
        "load_failed": bool(task.done() and not task.cancelled() and task.exception()),
    }


@app.post("/transcribe")
async def transcribe(audio: UploadFile = File(...)) -> dict:
    engine = app.state.engine
    if engine is None:
        task = app.state.load_task
        if task.done() and task.exception():
            raise HTTPException(500, "model failed to load — check the service log")
        raise HTTPException(503, "model still loading")

    blob = await audio.read()
    if len(blob) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "clip too large")
    if not blob:
        raise HTTPException(422, "empty upload")

    try:
        async with _gpu_lock:
            result = await asyncio.to_thread(engine.transcribe, blob, app.state.context)
    except subprocess.CalledProcessError:
        raise HTTPException(422, "could not decode audio")

    # elapsed + duration only — never the text — so latency is observable
    # in journald without operator speech landing in a log.
    logger.info("transcribed %.1fs clip in %dms", result.audio_s, result.elapsed_ms)
    return {
        "text": result.text,
        "audio_s": result.audio_s,
        "elapsed_ms": result.elapsed_ms,
        "model": engine.model_id,
    }


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        app,
        host=os.environ.get("STT_HOST", "127.0.0.1"),
        port=int(os.environ.get("STT_PORT", "8070")),
    )
