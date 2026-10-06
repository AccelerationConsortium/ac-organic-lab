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


# --- /speak (TTS) -----------------------------------------------------------


class _FakeTts:
    voice = "af_heart"

    def synthesize(self, text: str):
        from lab_stt.tts import Synthesis

        assert text
        return Synthesis(wav=b"RIFFfake", audio_s=1.2, elapsed_ms=88)


def test_speak_returns_wav(client):
    service.app.state.tts = _FakeTts()
    r = client.post("/speak", json={"text": "The press is ready."})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert r.content == b"RIFFfake"
    assert r.headers["x-audio-seconds"] == "1.2"


def test_speak_503_when_tts_not_loaded(client):
    service.app.state.tts = None
    r = client.post("/speak", json={"text": "hi"})
    assert r.status_code == 503


def test_speak_rejects_empty_and_oversized(client):
    service.app.state.tts = _FakeTts()
    assert client.post("/speak", json={"text": "  "}).status_code == 422
    assert client.post("/speak", json={"text": "x" * 601}).status_code == 413


def test_health_reports_tts(client):
    service.app.state.tts = _FakeTts()
    assert client.get("/health").json()["tts"] is True
    service.app.state.tts = None
    assert client.get("/health").json()["tts"] is False


# --- TTS-only instance (STT_ASR=0) -------------------------------------------


def _wait_loaded(c: TestClient) -> dict:
    import time

    for _ in range(200):
        body = c.get("/health").json()
        if body["tts"] or body["load_failed"]:
            return body
        time.sleep(0.01)
    raise AssertionError("load never finished")


def test_tts_only_loads_tts_not_asr(monkeypatch):
    def _no_asr(**_kw):
        raise AssertionError("a TTS-only instance must not load the ASR model")

    monkeypatch.setenv("STT_MODEL", "fake-asr")
    monkeypatch.setenv("STT_ASR", "0")
    monkeypatch.setattr(service, "QwenAsrEngine", _no_asr)
    monkeypatch.setattr(service, "KokoroTtsEngine", lambda **_kw: _FakeTts())
    with TestClient(service.app) as c:
        body = _wait_loaded(c)
        assert body["tts"] is True
        assert body["asr"] is False and body["loaded"] is False and body["model"] is None
        r = c.post("/transcribe", files={"audio": ("clip", b"xx", "audio/webm")})
        assert r.status_code == 503
        assert "not served" in r.json()["detail"]


def test_tts_only_surfaces_tts_load_failure(monkeypatch):
    def _broken(**_kw):
        raise RuntimeError("no xpu")

    monkeypatch.setenv("STT_MODEL", "fake-asr")
    monkeypatch.setenv("STT_ASR", "0")
    monkeypatch.setattr(service, "KokoroTtsEngine", _broken)
    with TestClient(service.app) as c:
        body = _wait_loaded(c)
        assert body["load_failed"] is True
        assert body["tts"] is False


def test_tts_only_without_voice_refuses_to_start(monkeypatch):
    monkeypatch.setenv("STT_MODEL", "fake-asr")
    monkeypatch.setenv("STT_ASR", "0")
    monkeypatch.setenv("STT_TTS_VOICE", "")
    with pytest.raises(RuntimeError, match="serve nothing"):
        with TestClient(service.app):
            pass
