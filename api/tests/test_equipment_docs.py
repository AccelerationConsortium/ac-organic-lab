"""Read-only equipment-documentation proxy tests.

All upstream HTTP is mocked. These tests verify the registry allowlist and the
older-server 404 path without contacting equipment.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urljoin

import httpx
import pytest
from fastapi import FastAPI
from lab_skills import load_platforms, load_registry

from app.equipment_docs import build_equipment_docs_router
from app import equipment_docs
from lab_skills.registry import EquipmentEntry, Registry


DEVICE_BASE = "http://equipment.test:8020"


def _app() -> FastAPI:
    app = FastAPI()
    app.state.registry = Registry(
        equipment=[
            EquipmentEntry(
                id="ot2_hte",
                name="Opentrons OT-2 (HTE)",
                kind="liquid_handler",
                adapter="http",
                base_url=DEVICE_BASE,
                documentation=[
                    {"label": "Swagger UI", "path": "/docs", "kind": "swagger"},
                    {
                        "label": "OpenAPI JSON",
                        "path": "/openapi.json",
                        "kind": "openapi",
                    },
                    {"label": "Agent guide", "path": "/docs/agent", "kind": "json"},
                    {"label": "Sensor guide", "path": "/agent-docs", "kind": "markdown"},
                    {"label": "API reference", "path": "/agent-docs/api-reference", "kind": "markdown"},
                    {"label": "Discovery", "path": "/llms.txt", "kind": "text"},
                ],
            )
        ]
    )
    app.include_router(build_equipment_docs_router())
    return app


async def _request(
    path: str,
    handler: httpx.MockTransport | None = None,
    *,
    headers: dict[str, str] | None = None,
    test_app: FastAPI | None = None,
    client_options: dict | None = None,
) -> httpx.Response:
    app = test_app or _app()
    if handler is None:
        def unexpected_upstream(request: httpx.Request) -> httpx.Response:
            raise AssertionError(f"Unexpected upstream request: {request.url}")
        handler = httpx.MockTransport(unexpected_upstream)
    upstream = httpx.AsyncClient(transport=handler, **(client_options or {}))
    app.state.documentation_client = upstream
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://dashboard.test",
        ) as client:
            return await client.get(path, headers=headers)
    finally:
        await upstream.aclose()


async def test_proxies_registered_openapi_document() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={"openapi": "3.1.0", "info": {"title": "OT-2"}, "paths": {}},
        )

    response = await _request(
        "/api/equipment/ot2_hte/documentation/openapi.json",
        httpx.MockTransport(handler),
    )

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "OT-2"
    assert [str(call.url) for call in calls] == [f"{DEVICE_BASE}/openapi.json"]


async def test_swagger_uses_same_origin_proxied_openapi() -> None:
    response = await _request("/api/equipment/ot2_hte/documentation/docs")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "/api/equipment/ot2_hte/documentation/openapi.json" in response.text
    assert '"supportedSubmitMethods": []' in response.text


async def test_unregistered_path_is_not_proxied() -> None:
    response = await _request("/api/equipment/ot2_hte/documentation/control/home")

    assert response.status_code == 404


async def test_older_gateway_404_is_reported_unchanged() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{DEVICE_BASE}/docs/agent"
        return httpx.Response(404, json={"detail": "Not Found"})

    response = await _request(
        "/api/equipment/ot2_hte/documentation/docs/agent",
        httpx.MockTransport(handler),
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_dashboard_openapi_lists_documentation_proxy() -> None:
    from app.main import app

    assert (
        "/api/equipment/{equipment_id}/documentation/{document_path}"
        in app.openapi()["paths"]
    )


async def test_catalog_lists_opentrons_and_bambu_documentation() -> None:
    from pathlib import Path

    from app.main import app, skill_catalog

    root = Path(__file__).resolve().parents[2]
    previous_registry = getattr(app.state, "registry", None)
    previous_platforms = getattr(app.state, "platforms_config", None)
    app.state.registry = load_registry(root / "equipment.yaml")
    app.state.platforms_config = load_platforms(root / "platforms.yaml")
    try:
        payload = await skill_catalog()
    finally:
        if previous_registry is None:
            del app.state.registry
        else:
            app.state.registry = previous_registry
        if previous_platforms is None:
            del app.state.platforms_config
        else:
            app.state.platforms_config = previous_platforms

    instruments = {
        instrument["id"]: instrument
        for platform in payload["platforms"].values()
        for instrument in platform["instruments"]
    }
    assert [doc["source_path"] for doc in instruments["bambu_gateway"]["documentation"]] == [
        "/docs",
        "/openapi.json",
    ]
    assert {
        doc["source_path"] for doc in instruments["ot2_hte"]["documentation"]
    } == {
        "/docs", "/openapi.json", "/docs/agent", "/plans/actions",
        # The gateway gained the house Markdown set alongside its JSON
        # guide; the JSON one stays because callers already fetch it.
        "/agent-docs", "/agent-docs/api-reference", "/llms.txt",
    }
    sensor = instruments["env_hte"]
    assert sensor["actions"] == []
    assert {doc["source_path"] for doc in sensor["documentation"]} == {
        "/docs", "/openapi.json", "/agent-docs", "/agent-docs/api-reference", "/llms.txt",
    }
    assert all(doc["url"].startswith("/api/equipment/env_hte/documentation/") for doc in sensor["documentation"])
    assert "env_storage" not in instruments
    assert instruments["ot2_hte"]["documentation"][0]["url"].startswith(
        "/api/equipment/ot2_hte/documentation/"
    )


@pytest.mark.parametrize("path,media_type", [
    ("/agent-docs", "text/markdown"),
    ("/agent-docs/api-reference", "text/markdown"),
    ("/llms.txt", "text/plain"),
])
async def test_proxies_text_documentation(path: str, media_type: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{DEVICE_BASE}{path}"
        assert request.headers["accept"] == media_type
        return httpx.Response(200, text="# Sensor documentation\n", headers={"content-type": f"{media_type}; charset=utf-8"})

    response = await _request(
        f"/api/equipment/ot2_hte/documentation{path}", httpx.MockTransport(handler),
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.text == "# Sensor documentation\n"


@pytest.mark.parametrize("status", [200, 401, 403, 404, 500])
async def test_docs_never_forward_identity_or_retry_auth(status: int) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.method == "GET"
        assert request.headers["accept-encoding"] == "identity"
        for name in ("cookie", "authorization", "x-api-key", "x-edge-auth", "x-auth-user", "x-auth-role"):
            assert name not in request.headers
        return httpx.Response(status, json={"detail": "public document"}, headers={
            "set-cookie": "upstream_secret=example", "x-private": "example",
        })

    response = await _request(
        "/api/equipment/ot2_hte/documentation/docs/agent", httpx.MockTransport(handler),
        headers={"Cookie": "ac_auth_session=viewer-cookie", "Authorization": "Bearer viewer-token",
                 "X-Api-Key": "viewer-key", "X-Auth-User": "forged-user", "X-Auth-Role": "admin",
                 "X-Edge-Auth": "forged-secret"},
        client_options={"cookies": {"ac_auth_session": "pooled-cookie"},
                        "headers": {"X-Api-Key": "default-key", "Authorization": "Bearer default"},
                        "auth": ("default-user", "default-password"), "follow_redirects": True},
    )
    assert len(calls) == 1
    assert response.status_code == status
    assert response.json() == {"detail": "public document"}
    assert "set-cookie" not in response.headers
    assert "x-private" not in response.headers
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path,media_type,body", [
    ("/agent-docs", "text/html", "<script>alert(1)</script>"),
    ("/openapi.json", "text/html", "<html>Login</html>"),
    ("/openapi.json", "text/plain", "not an OpenAPI document"),
    ("/llms.txt", "image/svg+xml", "<svg onload='alert(1)' />"),
    ("/agent-docs", "", "missing media type"),
    ("/agent-docs", "text/plain", b"\xff"),
    ("/openapi.json", "application/json", "<script>not JSON</script>"),
])
async def test_rejects_unexpected_or_invalid_content(path, media_type, body) -> None:
    response = await _request(
        f"/api/equipment/ot2_hte/documentation{path}",
        httpx.MockTransport(lambda _: httpx.Response(
            200, content=body, headers={"content-type": media_type},
        )),
    )
    assert response.status_code == 502
    assert "<script>" not in response.text


async def test_legacy_plain_text_markdown_stays_inert() -> None:
    response = await _request(
        "/api/equipment/ot2_hte/documentation/agent-docs",
        httpx.MockTransport(lambda _: httpx.Response(200, text="<script>alert(1)</script>")),
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_redirect_is_not_followed_even_with_redirecting_client() -> None:
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "http://elsewhere.test/private"})

    response = await _request(
        "/api/equipment/ot2_hte/documentation/agent-docs", httpx.MockTransport(handler),
        client_options={"follow_redirects": True},
    )
    assert response.status_code == 502
    assert len(calls) == 1
    assert "location" not in response.headers


class _DocumentStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], delay: float = 0):
        self.chunks = chunks
        self.delay = delay
        self.closed = False
        self.reads = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.reads += 1
            yield chunk

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("declared_length", [None, "10000000", "-1", "bad"])
async def test_size_limit_closes_stream_before_buffering_full_document(monkeypatch, declared_length):
    monkeypatch.setattr(equipment_docs, "_MAX_DOCUMENT_BYTES", 64 * 1024)
    stream = _DocumentStream([b"x" * (64 * 1024)] * 4)
    headers = {"content-type": "text/plain"}
    if declared_length is not None:
        headers["content-length"] = declared_length
    response = await _request(
        "/api/equipment/ot2_hte/documentation/agent-docs",
        httpx.MockTransport(lambda _: httpx.Response(200, stream=stream, headers=headers)),
    )
    assert response.status_code == 502
    assert stream.closed
    assert stream.reads == (2 if declared_length is None else 0)


async def test_compressed_response_is_rejected_before_reading():
    stream = _DocumentStream([b"not read"])
    response = await _request(
        "/api/equipment/ot2_hte/documentation/agent-docs",
        httpx.MockTransport(lambda _: httpx.Response(200, stream=stream, headers={
            "content-type": "text/plain", "content-encoding": "gzip",
        })),
    )
    assert response.status_code == 502
    assert stream.closed
    assert stream.reads == 0


async def test_slow_stream_hits_total_deadline_and_closes(monkeypatch):
    monkeypatch.setattr(equipment_docs, "_DOCUMENT_TIMEOUT_SECONDS", 0.02)
    stream = _DocumentStream([b"one", b"two"], delay=1)
    response = await _request(
        "/api/equipment/ot2_hte/documentation/agent-docs",
        httpx.MockTransport(lambda _: httpx.Response(200, stream=stream, headers={
            "content-type": "text/plain",
        })),
    )
    assert response.status_code == 504
    assert stream.closed


@pytest.mark.parametrize("service_prefix", ["", "/device"])
@pytest.mark.parametrize("link_style", ["relative", "root_relative", "absolute"])
async def test_index_links_resolve_directly_and_through_dashboard(service_prefix, link_style):
    app = _app()
    entry = app.state.registry.equipment[0]
    entry.base_url = f"{DEVICE_BASE}{service_prefix}"
    direct_index = f"{entry.base_url}/llms.txt"
    target = "agent-docs/api-reference"
    destination = {
        "relative": target,
        "root_relative": f"{service_prefix}/{target}",
        "absolute": f"{entry.base_url}/{target}",
    }[link_style]
    assert urljoin(direct_index, destination) == f"{entry.base_url}/{target}"
    index = (f"# Guide\n- [API reference]({destination}#errors)\n"
             "- [Unregistered](/control/home)\n"
             "- [External](https://external.test/agent-docs)\n"
             f"- [Status]({entry.base_url}/status)\n")
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if str(request.url) == direct_index:
            return httpx.Response(200, text=index)
        assert str(request.url) == f"{entry.base_url}/{target}"
        return httpx.Response(200, text="# API reference")

    response = await _request(
        "/api/equipment/ot2_hte/documentation/llms.txt", httpx.MockTransport(handler), test_app=app,
    )
    assert response.status_code == 200
    assert f"[API reference]({target}#errors)" in response.text
    assert "[Unregistered](/control/home)" in response.text
    assert "[External](https://external.test/agent-docs)" in response.text
    assert f"[Status]({entry.base_url}/status)" in response.text
    proxied_target = urljoin(str(response.url), f"{target}#errors")
    reference = await _request(proxied_target, httpx.MockTransport(handler), test_app=app)
    assert reference.status_code == 200
    assert reference.text == "# API reference"
    assert calls == [direct_index, f"{entry.base_url}/{target}"]


async def test_text_not_index_is_not_rewritten():
    text = f"[Example]({DEVICE_BASE}/agent-docs)"
    response = await _request(
        "/api/equipment/ot2_hte/documentation/agent-docs",
        httpx.MockTransport(lambda _: httpx.Response(200, text=text)),
    )
    assert response.text == text


@pytest.mark.parametrize("exception,status", [(httpx.ConnectError, 502), (httpx.ReadTimeout, 504)])
async def test_upstream_failure_does_not_expose_exception_details(exception, status):
    def handler(request):
        raise exception("private transport configuration", request=request)

    response = await _request(
        "/api/equipment/ot2_hte/documentation/agent-docs", httpx.MockTransport(handler),
    )
    assert response.status_code == status
    assert "private transport configuration" not in response.text


async def test_response_at_size_limit_succeeds(monkeypatch):
    monkeypatch.setattr(equipment_docs, "_MAX_DOCUMENT_BYTES", 16)
    response = await _request(
        "/api/equipment/ot2_hte/documentation/agent-docs",
        httpx.MockTransport(lambda _: httpx.Response(200, text="x" * 16)),
    )
    assert response.status_code == 200
    assert response.text == "x" * 16


async def test_invalid_index_url_is_reported_as_upstream_error():
    response = await _request(
        "/api/equipment/ot2_hte/documentation/llms.txt",
        httpx.MockTransport(lambda _: httpx.Response(
            200, text="[Malformed](https://example.com\uff1a443/agent-docs)",
        )),
    )
    assert response.status_code == 502


@pytest.mark.parametrize("text", ["[" * 100_000, "[a](" * 25_000])
async def test_unterminated_markdown_is_preserved(text):
    response = await _request(
        "/api/equipment/ot2_hte/documentation/llms.txt",
        httpx.MockTransport(lambda _: httpx.Response(200, text=text)),
    )
    assert response.status_code == 200
    assert response.text == text
