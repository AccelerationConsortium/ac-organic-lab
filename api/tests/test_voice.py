"""Voice transcription proxy (app/voice.py).

The contract under test: identity is REQUIRED (anonymous audio is refused,
not transcribed), the clip is forwarded to the loopback STT service, and the
health route mirrors the service's loaded state so the mic button can hide
itself. The STT service itself is tested in stt/tests.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.voice import build_voice_router

STT = "http://127.0.0.1:8070"


def _make_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.control_client = httpx.AsyncClient()
        yield
        await app.state.control_client.aclose()

    app = FastAPI(lifespan=lifespan)
    app.include_router(build_voice_router())
    return app


def test_anonymous_transcribe_is_401() -> None:
    with TestClient(_make_app()) as client:
        r = client.post(
            "/api/assistant/voice/transcribe",
            files={"audio": ("clip.webm", b"\x1a", "audio/webm")},
        )
    assert r.status_code == 401


@respx.mock
def test_transcribe_forwards_and_returns_text() -> None:
    route = respx.post(f"{STT}/transcribe").mock(
        return_value=httpx.Response(
            200, json={"text": "is the shaker running", "audio_s": 2.5, "elapsed_ms": 412}
        )
    )
    with TestClient(_make_app()) as client:
        r = client.post(
            "/api/assistant/voice/transcribe",
            files={"audio": ("clip.webm", b"\x1a\x45", "audio/webm")},
            headers={"X-Auth-User": "alice@example.edu"},
        )
    assert r.status_code == 200
    assert r.json()["text"] == "is the shaker running"
    assert route.called


def test_empty_clip_is_422() -> None:
    with TestClient(_make_app()) as client:
        r = client.post(
            "/api/assistant/voice/transcribe",
            files={"audio": ("clip.webm", b"", "audio/webm")},
            headers={"X-Auth-User": "alice@example.edu"},
        )
    assert r.status_code == 422


@respx.mock
def test_unreachable_service_is_502() -> None:
    respx.post(f"{STT}/transcribe").mock(side_effect=httpx.ConnectError("boom"))
    with TestClient(_make_app()) as client:
        r = client.post(
            "/api/assistant/voice/transcribe",
            files={"audio": ("clip.webm", b"\x1a", "audio/webm")},
            headers={"X-Auth-User": "alice@example.edu"},
        )
    assert r.status_code == 502


@respx.mock
@pytest.mark.parametrize(
    ("loaded", "expect"), [(True, True), (False, False)]
)
def test_health_mirrors_loaded(loaded: bool, expect: bool) -> None:
    respx.get(f"{STT}/health").mock(
        return_value=httpx.Response(
            200, json={"status": "healthy", "loaded": loaded, "model": "m"}
        )
    )
    with TestClient(_make_app()) as client:
        r = client.get("/api/assistant/voice/health")
    assert r.json()["configured"] is expect


@respx.mock
def test_health_configured_false_when_service_down() -> None:
    respx.get(f"{STT}/health").mock(side_effect=httpx.ConnectError("down"))
    with TestClient(_make_app()) as client:
        r = client.get("/api/assistant/voice/health")
    assert r.json() == {"configured": False}


# --- /speak (server TTS proxy) ------------------------------------------------


def test_anonymous_speak_is_401() -> None:
    with TestClient(_make_app()) as client:
        r = client.post("/api/assistant/voice/speak", json={"text": "hello"})
    assert r.status_code == 401


@respx.mock
def test_speak_forwards_and_returns_wav() -> None:
    route = respx.post(f"{STT}/speak").mock(
        return_value=httpx.Response(200, content=b"RIFFfake", headers={"content-type": "audio/wav"})
    )
    with TestClient(_make_app()) as client:
        r = client.post(
            "/api/assistant/voice/speak",
            json={"text": "The press is ready."},
            headers={"X-Auth-User": "alice@example.edu"},
        )
    assert r.status_code == 200
    assert r.content == b"RIFFfake"
    assert r.headers["content-type"] == "audio/wav"
    assert route.called


@respx.mock
def test_health_passes_tts_flag() -> None:
    respx.get(f"{STT}/health").mock(
        return_value=httpx.Response(
            200, json={"status": "healthy", "loaded": True, "model": "m", "tts": True}
        )
    )
    with TestClient(_make_app()) as client:
        assert client.get("/api/assistant/voice/health").json()["tts"] is True


# --- TTS served by a separate instance (ASSISTANT_TTS_URL) ---------------------

TTS = "http://127.0.0.1:8072"


@respx.mock
def test_speak_goes_to_tts_url_when_split(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASSISTANT_STT_URL", "http://100.64.0.5:8070")
    monkeypatch.setenv("ASSISTANT_TTS_URL", TTS)
    asr_host = respx.post("http://100.64.0.5:8070/speak").mock(return_value=httpx.Response(500))
    tts_host = respx.post(f"{TTS}/speak").mock(
        return_value=httpx.Response(200, content=b"RIFFlocal", headers={"content-type": "audio/wav"})
    )
    with TestClient(_make_app()) as client:
        r = client.post(
            "/api/assistant/voice/speak",
            json={"text": "The press is ready."},
            headers={"X-Auth-User": "alice@example.edu"},
        )
    assert r.status_code == 200
    assert r.content == b"RIFFlocal"
    assert tts_host.called and not asr_host.called


@respx.mock
@pytest.mark.parametrize(
    ("asr_up", "expect"),
    [
        (True, {"configured": True, "model": "m", "tts": True}),
        # The mic hides with the ASR host down, but read-aloud stays on.
        (False, {"configured": False, "model": None, "tts": True}),
    ],
)
def test_health_split_combines_both_services(
    monkeypatch: pytest.MonkeyPatch, asr_up: bool, expect: dict
) -> None:
    monkeypatch.setenv("ASSISTANT_STT_URL", "http://100.64.0.5:8070")
    monkeypatch.setenv("ASSISTANT_TTS_URL", TTS)
    asr = respx.get("http://100.64.0.5:8070/health")
    if asr_up:
        asr.mock(return_value=httpx.Response(200, json={"loaded": True, "model": "m", "tts": False}))
    else:
        asr.mock(side_effect=httpx.ConnectError("down"))
    respx.get(f"{TTS}/health").mock(
        return_value=httpx.Response(200, json={"asr": False, "loaded": False, "model": None, "tts": True})
    )
    with TestClient(_make_app()) as client:
        assert client.get("/api/assistant/voice/health").json() == expect


@respx.mock
def test_health_split_both_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASSISTANT_STT_URL", "http://100.64.0.5:8070")
    monkeypatch.setenv("ASSISTANT_TTS_URL", TTS)
    respx.get("http://100.64.0.5:8070/health").mock(side_effect=httpx.ConnectError("down"))
    respx.get(f"{TTS}/health").mock(side_effect=httpx.ConnectError("down"))
    with TestClient(_make_app()) as client:
        assert client.get("/api/assistant/voice/health").json() == {"configured": False}
