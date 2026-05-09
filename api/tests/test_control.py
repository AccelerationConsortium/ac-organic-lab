"""Tests for the /api/equipment/{id}/control/* passthrough.

We mount the control router on a tiny FastAPI app, stub the aggregator
that the router pulls off ``request.app.state.aggregator``, and assert
that the right gateway URL is hit with the right body.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.control import _control_url, build_control_router


def _entry(**overrides: Any) -> Any:
    base = {
        "id": "cam_lab499_west",
        "adapter": "http",
        "base_url": "http://127.0.0.1:8002",
        "status_path": "/cameras/cam_lab499_west/status",
    }
    base.update(overrides)
    obj = MagicMock(spec_set=list(base.keys()))
    for key, value in base.items():
        setattr(obj, key, value)
    return obj


def _make_app(entry: Any) -> FastAPI:
    aggregator = MagicMock()
    aggregator.entry.return_value = entry
    app = FastAPI()
    app.state.aggregator = aggregator
    app.include_router(build_control_router())
    return app


def test_control_url_strips_status_suffix() -> None:
    assert _control_url(
        "http://127.0.0.1:8002/", "/cameras/cam_x/status", "ptz"
    ) == "http://127.0.0.1:8002/cameras/cam_x/control/ptz"


def test_control_url_supports_nested_actions() -> None:
    assert _control_url(
        "http://127.0.0.1:8002", "/cameras/c1/status", "preset/save"
    ) == "http://127.0.0.1:8002/cameras/c1/control/preset/save"


def test_control_url_rejects_non_status_path() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        _control_url("http://127.0.0.1:8002", "/cameras/c1/probe", "ptz")


@respx.mock
def test_post_ptz_proxies_to_gateway() -> None:
    entry = _entry()
    app = _make_app(entry)
    route = respx.post(
        "http://127.0.0.1:8002/cameras/cam_lab499_west/control/ptz",
    ).mock(return_value=httpx.Response(200, json={"ok": True, "message": "moved"}))

    with TestClient(app) as client:
        r = client.post(
            "/api/equipment/cam_lab499_west/control/ptz",
            json={"direction": "left", "speed": 0.5, "duration_ms": 200},
        )

    assert r.status_code == 200
    assert r.json()["message"] == "moved"
    assert route.called
    sent = route.calls.last.request
    assert sent.read() != b""


@respx.mock
def test_post_preset_save_supports_nested_action() -> None:
    entry = _entry()
    app = _make_app(entry)
    route = respx.post(
        "http://127.0.0.1:8002/cameras/cam_lab499_west/control/preset/save",
    ).mock(return_value=httpx.Response(200, json={"ok": True, "state": {"preset_id": "9"}}))

    with TestClient(app) as client:
        r = client.post(
            "/api/equipment/cam_lab499_west/control/preset/save",
            json={"name": "home"},
        )

    assert r.status_code == 200
    assert r.json()["state"]["preset_id"] == "9"
    assert route.called


@respx.mock
def test_delete_preset_proxies_method() -> None:
    entry = _entry()
    app = _make_app(entry)
    route = respx.delete(
        "http://127.0.0.1:8002/cameras/cam_lab499_west/control/preset/9",
    ).mock(return_value=httpx.Response(200, json={"ok": True}))

    with TestClient(app) as client:
        r = client.delete("/api/equipment/cam_lab499_west/control/preset/9")

    assert r.status_code == 200
    assert route.called


def test_unknown_equipment_returns_404() -> None:
    aggregator = MagicMock()
    aggregator.entry.return_value = None
    app = FastAPI()
    app.state.aggregator = aggregator
    app.include_router(build_control_router())
    with TestClient(app) as client:
        r = client.post("/api/equipment/no_such/control/ptz", json={})
    assert r.status_code == 404


def test_legacy_adapter_rejected() -> None:
    entry = _entry(adapter="legacy_http")
    app = _make_app(entry)
    with TestClient(app) as client:
        r = client.post("/api/equipment/cam_lab499_west/control/ptz", json={})
    assert r.status_code == 400
    assert "control surface" in r.json()["detail"]


@respx.mock
def test_gateway_5xx_propagates_with_detail() -> None:
    entry = _entry()
    app = _make_app(entry)
    respx.post(
        "http://127.0.0.1:8002/cameras/cam_lab499_west/control/privacy",
    ).mock(return_value=httpx.Response(503, json={"detail": "pytapo not configured"}))

    with TestClient(app) as client:
        r = client.post(
            "/api/equipment/cam_lab499_west/control/privacy",
            json={"enabled": True},
        )

    assert r.status_code == 503
    assert "pytapo" in r.json()["detail"]
