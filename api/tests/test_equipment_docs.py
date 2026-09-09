"""Read-only equipment-documentation proxy tests.

All upstream HTTP is mocked. These tests verify the registry allowlist and the
older-server 404 path without contacting equipment.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from lab_skills import load_platforms, load_registry

from app.equipment_docs import build_equipment_docs_router
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
) -> httpx.Response:
    app = _app()
    upstream = httpx.AsyncClient(transport=handler) if handler is not None else None
    if upstream is not None:
        app.state.control_client = upstream
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://dashboard.test",
        ) as client:
            return await client.get(path)
    finally:
        if upstream is not None:
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
    } == {"/docs", "/openapi.json", "/docs/agent", "/plans/actions"}
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
    assert response.headers["content-type"] == f"{media_type}; charset=utf-8"
    assert response.text == "# Sensor documentation\n"
