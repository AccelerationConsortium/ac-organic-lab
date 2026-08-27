"""Service contract tests with the engine faked — no GPU, no model download."""

from dataclasses import dataclass

import os

import pytest
from fastapi.testclient import TestClient

os.environ["STT_MODEL"] = ""  # HTTP contract only — never load the real model

from lab_stt import service


@dataclass
class _FakeResult:
    text: str = "is the shaker running"
    audio_s: float = 2.5
    elapsed_ms: int = 412


class _FakeEngine:
    model_id = "fake-model"

    def transcribe(self, blob: bytes, context: str) -> _FakeResult:
        assert context  # vocabulary prompt must always be passed
        return _FakeResult()


class _DoneTask:
    def cancelled(self):
        return False

    def done(self):
        return True

    def exception(self):
        return None

    def cancel(self):
        pass


@pytest.fixture()
def client():
    # TestClient runs the lifespan, which would load the real model — build
    # the app state by hand instead.
    with TestClient(service.app) as c:  # noqa: SIM117 — lifespan wanted for shutdown
        service.app.state.engine = _FakeEngine()
        service.app.state.load_task.cancel()
        service.app.state.load_task = _DoneTask()
        yield c


def test_health_reports_loaded(client):
    body = client.get("/health").json()
    assert body["status"] == "healthy"
    assert body["loaded"] is True


def test_transcribe_returns_text_and_latency(client):
    r = client.post("/transcribe", files={"audio": ("clip.webm", b"\x1a\x45", "audio/webm")})
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "is the shaker running"
    assert body["elapsed_ms"] == 412


def test_empty_upload_is_422(client):
    r = client.post("/transcribe", files={"audio": ("clip", b"", "audio/webm")})
    assert r.status_code == 422


def test_oversized_upload_is_413(client):
    blob = b"x" * (service.MAX_UPLOAD_BYTES + 1)
    r = client.post("/transcribe", files={"audio": ("clip", blob, "audio/webm")})
    assert r.status_code == 413


def test_503_while_model_loading():
    with TestClient(service.app) as c:
        c.app.state.engine = None  # type: ignore[attr-defined]
        r = c.post("/transcribe", files={"audio": ("clip", b"xx", "audio/webm")})
        assert r.status_code == 503
