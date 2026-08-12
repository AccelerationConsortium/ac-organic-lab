"""Tests for the /api/equipment/{id}/control/* passthrough.

We mount the control router on a tiny FastAPI app, stub the aggregator
that the router pulls off ``request.app.state.aggregator``, and assert
that the right gateway URL is hit with the right body.
"""

from __future__ import annotations

import json
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


@respx.mock
def test_get_control_action_proxies_without_claim() -> None:
    """GET /control/{action} (e.g. dose_every_well's read-balance) must not
    attempt the claim dance — only POST does (`_proxy`'s `needs_claim`) —
    and must forward the device's JSON body back verbatim."""
    entry = _fume_hood_entry(
        id="dose_every_well", base_url="http://127.0.0.1:8000", status_path="/status"
    )
    app = _make_app(entry)
    claim_route = respx.post("http://127.0.0.1:8000/control/claim")
    action_route = respx.get("http://127.0.0.1:8000/control/read-balance").mock(
        return_value=httpx.Response(200, json={"mass_g": 1.2345, "mass_mg": 1234.5})
    )

    with TestClient(app) as client:
        r = client.get("/api/equipment/dose_every_well/control/read-balance")

    assert r.status_code == 200
    assert r.json() == {"mass_g": 1.2345, "mass_mg": 1234.5}
    assert action_route.called
    assert not claim_route.called


@respx.mock
def test_plate_passthrough_proxies_json_get() -> None:
    """GET /{id}/plate/{sub} is a sibling namespace to /control/* — used by
    dose_every_well's read-only plate/status and plate/definitions, which
    aren't claim-gated on the device (no _proxy claim dance needed)."""
    entry = _entry(
        id="dose_every_well", base_url="http://127.0.0.1:8000", status_path="/status"
    )
    app = _make_app(entry)
    route = respx.get("http://127.0.0.1:8000/plate/definitions").mock(
        return_value=httpx.Response(200, json=[{"key": "96-well-standard", "rows": 8}])
    )

    with TestClient(app) as client:
        r = client.get("/api/equipment/dose_every_well/plate/definitions")

    assert r.status_code == 200
    assert r.json() == [{"key": "96-well-standard", "rows": 8}]
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


# ---------------------------------------------------------------------------
# Sash (fume hood) passthrough via the generic /control/{action} route
# ---------------------------------------------------------------------------


def _fume_hood_entry(**overrides: Any) -> Any:
    """STATUS_SPEC v1.1 fume hood actuator. Claim semantics on /control/*."""

    base = {
        "id": "fume_hood_actuator",
        "kind": "fume_hood",
        "adapter": "http",
        "protocol": "1.1",
        "base_url": "http://100.64.254.100:5000",
        "status_path": "/status",
    }
    base.update(overrides)
    obj = MagicMock(spec_set=list(base.keys()))
    for key, value in base.items():
        setattr(obj, key, value)
    return obj


@respx.mock
def test_sash_move_proxies_through_control_route() -> None:
    entry = _fume_hood_entry()
    app = _make_app(entry)
    respx.post("http://100.64.254.100:5000/control/claim").mock(
        return_value=httpx.Response(
            200,
            json={
                "claim_token": "tok-sash",
                "heartbeat_interval_s": 10.0,
                "expires_at": "2026-05-25T20:00:00Z",
            },
        )
    )
    action_route = respx.post("http://100.64.254.100:5000/control/sash/move").mock(
        return_value=httpx.Response(202, json={"equipment_status": "busy"})
    )
    release_route = respx.post("http://100.64.254.100:5000/control/release").mock(
        return_value=httpx.Response(204)
    )

    with TestClient(app) as client:
        r = client.post(
            "/api/equipment/fume_hood_actuator/control/sash/move",
            json={"position": 3},
        )

    assert r.status_code == 200
    assert action_route.called
    assert release_route.called
    assert action_route.calls.last.request.headers["x-claim-token"] == "tok-sash"


@respx.mock
def test_sash_stop_proxies_through_control_route() -> None:
    entry = _fume_hood_entry()
    app = _make_app(entry)
    respx.post("http://100.64.254.100:5000/control/claim").mock(
        return_value=httpx.Response(
            200,
            json={
                "claim_token": "tok-sash",
                "heartbeat_interval_s": 10.0,
                "expires_at": "2026-05-25T20:00:00Z",
            },
        )
    )
    action_route = respx.post("http://100.64.254.100:5000/control/sash/stop").mock(
        return_value=httpx.Response(200, json={"equipment_status": "ready"})
    )
    respx.post("http://100.64.254.100:5000/control/release").mock(
        return_value=httpx.Response(204)
    )

    with TestClient(app) as client:
        r = client.post("/api/equipment/fume_hood_actuator/control/sash/stop", json={})

    assert r.status_code == 200
    assert action_route.called


# ---------------------------------------------------------------------------
# v1.1 claim semantics (PlateLoc / filter_every_well)
# ---------------------------------------------------------------------------


def _v11_entry(**overrides: Any) -> Any:
    """Mock a STATUS_SPEC v1.1 device entry (status_path == /status, no prefix)."""

    base = {
        "id": "plateloc",
        "kind": "plate_sealer",
        "adapter": "http",
        "protocol": "1.1",
        "base_url": "http://127.0.0.1:9999",
        "status_path": "/status",
    }
    base.update(overrides)
    obj = MagicMock(spec_set=list(base.keys()))
    for key, value in base.items():
        setattr(obj, key, value)
    return obj


@respx.mock
def test_v11_control_acquires_claim_attaches_token_releases() -> None:
    entry = _v11_entry()
    app = _make_app(entry)

    claim_route = respx.post("http://127.0.0.1:9999/control/claim").mock(
        return_value=httpx.Response(
            200,
            json={
                "claim_token": "tok-abc",
                "heartbeat_interval_s": 10.0,
                "expires_at": "2026-05-23T16:00:00Z",
            },
        )
    )
    action_route = respx.post(
        "http://127.0.0.1:9999/control/seal/temperature"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))
    release_route = respx.post("http://127.0.0.1:9999/control/release").mock(
        return_value=httpx.Response(204)
    )

    with TestClient(app) as client:
        r = client.post(
            "/api/equipment/plateloc/control/seal/temperature",
            json={"temperature_c": 50},
        )

    assert r.status_code == 200
    assert claim_route.called
    assert action_route.called
    assert release_route.called

    # The action call must carry the X-Claim-Token header from the claim
    # response; the release call must carry the same token.
    assert action_route.calls.last.request.headers["x-claim-token"] == "tok-abc"
    assert release_route.calls.last.request.headers["x-claim-token"] == "tok-abc"


@respx.mock
def test_v12_control_still_runs_the_claim_dance() -> None:
    """protocol "1.2" is additive over v1.1 — the device still hard-enforces
    X-Claim-Token, so the passthrough must claim exactly as for v1.1.
    Regression: `needs_claim` once tested `== "1.1"`, which would have sent
    tokenless requests to the first v1.2 device (423 on every click)."""
    entry = _v11_entry(id="torry_pines_shaker", kind="shaker", protocol="1.2")
    app = _make_app(entry)

    claim_route = respx.post("http://127.0.0.1:9999/control/claim").mock(
        return_value=httpx.Response(
            200,
            json={
                "claim_token": "tok-v12",
                "heartbeat_interval_s": 10.0,
                "expires_at": "2026-07-25T16:00:00Z",
            },
        )
    )
    action_route = respx.post("http://127.0.0.1:9999/control/shake/stop").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    respx.post("http://127.0.0.1:9999/control/release").mock(
        return_value=httpx.Response(204)
    )

    with TestClient(app) as client:
        r = client.post(
            "/api/equipment/torry_pines_shaker/control/shake/stop", json={}
        )

    assert r.status_code == 200
    assert claim_route.called
    assert action_route.calls.last.request.headers["x-claim-token"] == "tok-v12"


@respx.mock
def test_authenticated_user_is_stamped_as_claim_owner() -> None:
    """When the edge injects X-Auth-User, that real owner (not the dashboard
    fallback) is sent in the device claim and recorded in the audit row."""
    entry = _v11_entry()
    app, db = _make_app_with_db(entry)

    respx.get("http://127.0.0.1:8009/authz/check").mock(
        return_value=httpx.Response(200, json={"allowed": True, "role": "user"})
    )
    claim_route = respx.post("http://127.0.0.1:9999/control/claim").mock(
        return_value=httpx.Response(
            200,
            json={
                "claim_token": "tok-abc",
                "heartbeat_interval_s": 10.0,
                "expires_at": "2026-05-23T16:00:00Z",
            },
        )
    )
    respx.post("http://127.0.0.1:9999/control/seal/temperature").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    respx.post("http://127.0.0.1:9999/control/release").mock(
        return_value=httpx.Response(204)
    )

    with TestClient(app) as client:
        r = client.post(
            "/api/equipment/plateloc/control/seal/temperature",
            json={"temperature_c": 50},
            headers={"X-Auth-User": "alice@utoronto.ca"},
        )

    assert r.status_code == 200
    # The claim body carried the authenticated owner...
    assert json.loads(claim_route.calls.last.request.content)["owner"] == "alice@utoronto.ca"
    # ...and the audit row recorded it too.
    assert db.events[0]["payload"]["owner"] == "alice@utoronto.ca"


@respx.mock
def test_v11_claim_conflict_surfaces_claimed_by() -> None:
    entry = _v11_entry()
    app = _make_app(entry)

    respx.post("http://127.0.0.1:9999/control/claim").mock(
        return_value=httpx.Response(
            409,
            json={
                "detail": "already claimed",
                "claimed_by": {
                    "session_id": "f1f1",
                    "owner": "workflow:solubility",
                    "expires_at": "2026-05-23T16:01:00Z",
                },
                "retry_after_s": 12.0,
            },
        )
    )
    action_route = respx.post(
        "http://127.0.0.1:9999/control/seal/temperature"
    )

    with TestClient(app) as client:
        r = client.post(
            "/api/equipment/plateloc/control/seal/temperature",
            json={"temperature_c": 50},
        )

    assert r.status_code == 409
    detail = r.json()["detail"]
    # FastAPI wraps the dict under "detail", and our handler forwards the
    # device's full JSON body as the detail field so the frontend can
    # render claimed_by.owner + retry_after_s.
    assert detail["claimed_by"]["owner"] == "workflow:solubility"
    assert detail["retry_after_s"] == 12.0
    assert not action_route.called  # we never made the action call


@respx.mock
def test_v11_releases_even_when_action_fails() -> None:
    entry = _v11_entry()
    app = _make_app(entry)

    respx.post("http://127.0.0.1:9999/control/claim").mock(
        return_value=httpx.Response(200, json={
            "claim_token": "tok-zzz",
            "heartbeat_interval_s": 10.0,
            "expires_at": "2026-05-23T16:00:00Z",
        })
    )
    respx.post("http://127.0.0.1:9999/control/seal/temperature").mock(
        return_value=httpx.Response(503, json={"detail": "device busy"})
    )
    release_route = respx.post("http://127.0.0.1:9999/control/release").mock(
        return_value=httpx.Response(204)
    )

    with TestClient(app) as client:
        r = client.post(
            "/api/equipment/plateloc/control/seal/temperature",
            json={"temperature_c": 50},
        )

    assert r.status_code == 503
    # The failed-action response shouldn't leak a held claim. Release
    # must still fire.
    assert release_route.called


@respx.mock
def test_v11_claim_action_passes_through_without_wrapping() -> None:
    """Calls to /control/claim itself must NOT trigger another claim dance."""

    entry = _v11_entry()
    app = _make_app(entry)

    # Only one claim call should be made, and it should come from the
    # passthrough itself - no recursive claim, no release.
    claim_route = respx.post("http://127.0.0.1:9999/control/claim").mock(
        return_value=httpx.Response(200, json={
            "claim_token": "tok-passthrough",
            "heartbeat_interval_s": 10.0,
            "expires_at": "2026-05-23T16:00:00Z",
        })
    )
    release_route = respx.post("http://127.0.0.1:9999/control/release")

    with TestClient(app) as client:
        r = client.post(
            "/api/equipment/plateloc/control/claim",
            json={"owner": "manual", "session_id": "abc"},
        )

    assert r.status_code == 200
    assert claim_route.call_count == 1
    assert not release_route.called


# ---------------------------------------------------------------------------
# Audit trail — every control action writes one equipment_events row
# ---------------------------------------------------------------------------


class _FakeDB:
    """Captures equipment_events writes so tests can assert the audit row."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def record_equipment_event(self, device_id, event_type, **kwargs):
        self.events.append(
            {"device_id": device_id, "event_type": event_type, **kwargs}
        )


def _make_app_with_db(entry: Any) -> tuple[FastAPI, _FakeDB]:
    app = _make_app(entry)
    db = _FakeDB()
    app.state.db = db
    return app, db


@respx.mock
def test_ok_action_writes_audit_row() -> None:
    entry = _entry()
    app, db = _make_app_with_db(entry)
    respx.post(
        "http://127.0.0.1:8002/cameras/cam_lab499_west/control/ptz",
    ).mock(return_value=httpx.Response(200, json={"ok": True}))

    with TestClient(app) as client:
        r = client.post(
            "/api/equipment/cam_lab499_west/control/ptz",
            json={"direction": "left"},
        )

    assert r.status_code == 200
    assert len(db.events) == 1
    ev = db.events[0]
    assert ev["device_id"] == "cam_lab499_west"
    assert ev["event_type"] == "control_action"
    assert ev["payload"]["action"] == "ptz"
    assert ev["payload"]["outcome"] == "ok"
    assert ev["payload"]["status_code"] == 200
    assert ev["payload"]["owner"] == "ac-organic-lab-dashboard"
    # Wall-clock of the device interaction — rounded to ms, non-negative.
    assert isinstance(ev["payload"]["duration_s"], float)
    assert ev["payload"]["duration_s"] >= 0.0


@respx.mock
def test_device_refusal_writes_audit_row() -> None:
    entry = _entry()
    app, db = _make_app_with_db(entry)
    respx.post(
        "http://127.0.0.1:8002/cameras/cam_lab499_west/control/privacy",
    ).mock(return_value=httpx.Response(503, json={"detail": "pytapo not configured"}))

    with TestClient(app) as client:
        r = client.post(
            "/api/equipment/cam_lab499_west/control/privacy",
            json={"enabled": True},
        )

    assert r.status_code == 503
    assert len(db.events) == 1
    assert db.events[0]["payload"]["outcome"] == "refused"
    assert db.events[0]["payload"]["status_code"] == 503


@respx.mock
def test_claim_denied_writes_audit_row() -> None:
    entry = _v11_entry()
    app, db = _make_app_with_db(entry)
    respx.post("http://127.0.0.1:9999/control/claim").mock(
        return_value=httpx.Response(
            409,
            json={
                "detail": "already claimed",
                "claimed_by": {
                    "session_id": "f1f1",
                    "owner": "workflow:solubility",
                    "expires_at": "2026-05-23T16:01:00Z",
                },
            },
        )
    )

    with TestClient(app) as client:
        r = client.post(
            "/api/equipment/plateloc/control/seal/temperature",
            json={"temperature_c": 50},
        )

    assert r.status_code == 409
    assert len(db.events) == 1
    assert db.events[0]["payload"]["outcome"] == "claim_denied"
    assert db.events[0]["payload"]["action"] == "seal/temperature"
    # The claim hop DID reach the device, so time-to-refusal is recorded.
    assert db.events[0]["payload"]["duration_s"] >= 0.0


def test_audit_is_noop_without_db() -> None:
    """Control still works when the history DB is unavailable."""

    entry = _entry()
    app = _make_app(entry)  # no app.state.db
    with respx.mock:
        respx.post(
            "http://127.0.0.1:8002/cameras/cam_lab499_west/control/ptz",
        ).mock(return_value=httpx.Response(200, json={"ok": True}))
        with TestClient(app) as client:
            r = client.post(
                "/api/equipment/cam_lab499_west/control/ptz", json={}
            )
    assert r.status_code == 200


@respx.mock
def test_v10_device_skips_claim_dance() -> None:
    """v1.0 / no-protocol devices POST directly with no claim overhead."""

    entry = _v11_entry(protocol="1.0")
    app = _make_app(entry)

    claim_route = respx.post("http://127.0.0.1:9999/control/claim")
    action_route = respx.post(
        "http://127.0.0.1:9999/control/seal/temperature"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))

    with TestClient(app) as client:
        r = client.post(
            "/api/equipment/plateloc/control/seal/temperature",
            json={"temperature_c": 50},
        )

    assert r.status_code == 200
    assert not claim_route.called
    assert action_route.called
    assert "x-claim-token" not in action_route.calls.last.request.headers


# ---------------------------------------------------------------------------
# Per-equipment authorization at the gateway (Phase 2 pre-enforcement)
# ---------------------------------------------------------------------------

_AUTHZ_URL = "http://127.0.0.1:8009/authz/check"


@respx.mock
def test_authz_forbidden_user_gets_403_and_no_gateway_call() -> None:
    """An authenticated user with no role on the equipment is refused before
    any claim/action reaches the device."""
    respx.get(_AUTHZ_URL).mock(
        return_value=httpx.Response(
            200,
            json={"allowed": False, "role": None, "reason": "no grant for this equipment"},
        )
    )
    action = respx.post(
        "http://127.0.0.1:8002/cameras/cam_lab499_west/control/ptz"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))
    with TestClient(_make_app(_entry())) as client:
        resp = client.post(
            "/api/equipment/cam_lab499_west/control/ptz",
            json={"direction": "up"},
            headers={"X-Auth-User": "larry.aung@mail.utoronto.ca"},
        )
    assert resp.status_code == 403
    assert "not authorized" in resp.json()["detail"]
    assert action.called is False


@respx.mock
def test_authz_allowed_user_proxies_normally() -> None:
    respx.get(_AUTHZ_URL).mock(
        return_value=httpx.Response(200, json={"allowed": True, "role": "user"})
    )
    action = respx.post(
        "http://127.0.0.1:8002/cameras/cam_lab499_west/control/ptz"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))
    with TestClient(_make_app(_entry())) as client:
        resp = client.post(
            "/api/equipment/cam_lab499_west/control/ptz",
            json={"direction": "up"},
            headers={"X-Auth-User": "yangcyril.cao@utoronto.ca"},
        )
    assert resp.status_code == 200
    assert action.called is True


@respx.mock
def test_authz_skipped_without_identity_header() -> None:
    """No X-Auth-User (dev-open / pre-edge) → no sidecar call, current
    behavior preserved. The middleware rejects unauthenticated control in
    production before it ever reaches here."""
    authz = respx.get(_AUTHZ_URL).mock(
        return_value=httpx.Response(200, json={"allowed": False})
    )
    respx.post(
        "http://127.0.0.1:8002/cameras/cam_lab499_west/control/ptz"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))
    with TestClient(_make_app(_entry())) as client:
        resp = client.post(
            "/api/equipment/cam_lab499_west/control/ptz", json={"direction": "up"}
        )
    assert resp.status_code == 200
    assert authz.called is False


@respx.mock
def test_authz_sidecar_down_fails_closed() -> None:
    respx.get(_AUTHZ_URL).mock(side_effect=httpx.ConnectError("refused"))
    action = respx.post(
        "http://127.0.0.1:8002/cameras/cam_lab499_west/control/ptz"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))
    with TestClient(_make_app(_entry())) as client:
        resp = client.post(
            "/api/equipment/cam_lab499_west/control/ptz",
            json={"direction": "up"},
            headers={"X-Auth-User": "yangcyril.cao@utoronto.ca"},
        )
    assert resp.status_code == 503
    assert action.called is False


# ---------------------------------------------------------------------------
# Device credential fallback (401 on the trusted-edge fast path)
#
# The fast path is an optimisation, not a commitment: a device that does not
# implement it answers 401. Before the fallback existed it was tried
# *exclusively* whenever DEVICE_EDGE_SHARED_SECRET was set, so the operator's
# own credential was unreachable in production and every dashboard-mediated
# control action against such a device failed 401 — observed live against
# xarm_translocation, 2026-08-11.
# ---------------------------------------------------------------------------


def _auth_request_headers() -> dict[str, str]:
    """What the Next.js middleware injects for a signed-in operator."""

    return {"X-Auth-User": "alice@example.edu", "Cookie": "ac_auth_session=sess-1"}


def test_device_auth_candidates_orders_edge_first_then_operator(monkeypatch) -> None:
    from starlette.datastructures import Headers

    from app import control as control_mod

    monkeypatch.setattr(control_mod, "_EDGE_SHARED_SECRET", "shhh")
    request = MagicMock()
    request.headers = Headers({"x-auth-user": "alice@example.edu"})
    request.cookies = {"ac_auth_session": "sess-1"}

    candidates = control_mod._device_auth_candidates(request)
    assert [sorted(c) for c in candidates] == [
        ["X-Auth-User", "X-Edge-Auth"],
        ["Cookie"],
    ]
    # The single-hop helper still yields the preferred one.
    assert control_mod._device_auth_headers(request) == candidates[0]


def test_device_auth_candidates_never_empty(monkeypatch) -> None:
    from starlette.datastructures import Headers

    from app import control as control_mod

    monkeypatch.setattr(control_mod, "_EDGE_SHARED_SECRET", "")
    request = MagicMock()
    request.headers = Headers({})
    request.cookies = {}
    # One unauthenticated attempt: open dev deployment / login-disabled device.
    assert control_mod._device_auth_candidates(request) == [{}]


@respx.mock
def test_claim_falls_back_to_operator_cookie_when_edge_headers_401(monkeypatch) -> None:
    """The device rejects the shared-secret headers; the cookie must be tried."""

    from app import control as control_mod

    monkeypatch.setattr(control_mod, "_EDGE_SHARED_SECRET", "shhh")
    monkeypatch.setenv("CONTROL_AUTHZ_ENFORCE", "false")
    app = _make_app(_fume_hood_entry())

    def claim_responder(request: httpx.Request) -> httpx.Response:
        if "x-edge-auth" in request.headers:
            return httpx.Response(401, json={"detail": {"error": "login_required"}})
        return httpx.Response(200, json={
            "claim_token": "tok-fallback",
            "heartbeat_interval_s": 10.0,
            "expires_at": "2026-08-11T20:00:00Z",
        })

    claim_route = respx.post("http://100.64.254.100:5000/control/claim").mock(
        side_effect=claim_responder
    )
    action_route = respx.post("http://100.64.254.100:5000/control/sash/move").mock(
        return_value=httpx.Response(200, json={"equipment_status": "busy"})
    )
    release_route = respx.post("http://100.64.254.100:5000/control/release").mock(
        return_value=httpx.Response(204)
    )

    with TestClient(app) as client:
        r = client.post(
            "/api/equipment/fume_hood_actuator/control/sash/move",
            json={"position": 3},
            headers=_auth_request_headers(),
        )

    assert r.status_code == 200
    assert len(claim_route.calls) == 2, "edge attempt, then operator credential"
    # The whole sequence sticks to the credential that worked — no mixing.
    for route in (action_route, release_route):
        sent = route.calls.last.request.headers
        assert "x-edge-auth" not in sent
        assert sent["cookie"] == "ac_auth_session=sess-1"
    assert action_route.calls.last.request.headers["x-claim-token"] == "tok-fallback"


@respx.mock
def test_claim_401_surfaces_when_no_other_credential(monkeypatch) -> None:
    """Nothing left to try: the device's 401 reaches the caller unchanged."""

    from app import control as control_mod

    monkeypatch.setattr(control_mod, "_EDGE_SHARED_SECRET", "shhh")
    monkeypatch.setenv("CONTROL_AUTHZ_ENFORCE", "false")
    app = _make_app(_fume_hood_entry())
    claim_route = respx.post("http://100.64.254.100:5000/control/claim").mock(
        return_value=httpx.Response(401, json={"detail": {"error": "login_required"}})
    )

    with TestClient(app) as client:
        r = client.post(
            "/api/equipment/fume_hood_actuator/control/sash/move",
            json={"position": 3},
            headers={"X-Auth-User": "alice@example.edu"},  # no cookie, no api key
        )

    assert r.status_code == 401
    assert len(claim_route.calls) == 1


@respx.mock
def test_claim_403_is_not_retried_with_another_credential(monkeypatch) -> None:
    """403 means the device identified the caller and refused. Trying a second
    credential would be working around an authorization decision."""

    from app import control as control_mod

    monkeypatch.setattr(control_mod, "_EDGE_SHARED_SECRET", "shhh")
    monkeypatch.setenv("CONTROL_AUTHZ_ENFORCE", "false")
    app = _make_app(_fume_hood_entry())
    claim_route = respx.post("http://100.64.254.100:5000/control/claim").mock(
        return_value=httpx.Response(403, json={"detail": "role_forbidden"})
    )

    with TestClient(app) as client:
        r = client.post(
            "/api/equipment/fume_hood_actuator/control/sash/move",
            json={"position": 3},
            headers=_auth_request_headers(),
        )

    assert r.status_code == 403
    assert len(claim_route.calls) == 1


@respx.mock
def test_claimless_action_also_falls_back(monkeypatch) -> None:
    """A v1.0 device has no claim hop, so the action itself carries the retry."""

    from app import control as control_mod

    monkeypatch.setattr(control_mod, "_EDGE_SHARED_SECRET", "shhh")
    monkeypatch.setenv("CONTROL_AUTHZ_ENFORCE", "false")
    app = _make_app(_entry(protocol="1.0"))

    def responder(request: httpx.Request) -> httpx.Response:
        if "x-edge-auth" in request.headers:
            return httpx.Response(401, json={"detail": {"error": "login_required"}})
        return httpx.Response(200, json={"ok": True})

    route = respx.post(
        "http://127.0.0.1:8002/cameras/cam_lab499_west/control/ptz"
    ).mock(side_effect=responder)

    with TestClient(app) as client:
        r = client.post(
            "/api/equipment/cam_lab499_west/control/ptz",
            json={"pan": 1},
            headers=_auth_request_headers(),
        )

    assert r.status_code == 200
    assert len(route.calls) == 2


# ---------------------------------------------------------------------------
# Per-device edge secrets
#
# Each device behind the single edge trusts a *different* X-Edge-Auth secret, so
# one dashboard-wide value satisfies at most one of them. Sending the wrong one
# is indistinguishable from sending none: the device answers 401 login_required.
# That is what broke xArm control for two weeks while the OT-2s worked — the
# global happened to hold the OT-2 secret.
# ---------------------------------------------------------------------------


def _entry_with_secret(env_name: str | None, **overrides: Any) -> Any:
    base = {
        "id": "xarm_translocation",
        "kind": "robot_arm",
        "adapter": "http",
        "protocol": "1.1",
        "base_url": "http://100.64.254.16:8000",
        "status_path": "/status",
        "edge_secret_env": env_name,
    }
    base.update(overrides)
    obj = MagicMock(spec_set=list(base.keys()))
    for key, value in base.items():
        setattr(obj, key, value)
    return obj


def test_edge_secret_resolves_per_equipment(monkeypatch) -> None:
    from app import control as control_mod

    monkeypatch.setattr(control_mod, "_EDGE_SHARED_SECRET", "global-secret")
    monkeypatch.setenv("XARM_EDGE_SHARED_SECRET", "xarm-secret")
    monkeypatch.setenv("OT2_EDGE_SECRET", "ot2-secret")

    xarm = _entry_with_secret("XARM_EDGE_SHARED_SECRET")
    ot2 = _entry_with_secret("OT2_EDGE_SECRET", id="ot2_hte", kind="liquid_handler")

    assert control_mod._edge_secret_for(xarm) == "xarm-secret"
    assert control_mod._edge_secret_for(ot2) == "ot2-secret"
    # The regression this exists for: two devices, two secrets, one process.
    assert control_mod._edge_secret_for(xarm) != control_mod._edge_secret_for(ot2)


def test_edge_secret_falls_back_to_global(monkeypatch) -> None:
    """Entries that name no variable keep the pre-2026-08-12 behaviour."""

    from app import control as control_mod

    monkeypatch.setattr(control_mod, "_EDGE_SHARED_SECRET", "global-secret")
    assert control_mod._edge_secret_for(_entry_with_secret(None)) == "global-secret"
    assert control_mod._edge_secret_for(None) == "global-secret"


def test_edge_secret_named_but_unset_falls_back_loudly(monkeypatch, caplog) -> None:
    """A typo'd variable name is otherwise indistinguishable from a device that
    does not gate at all — so it falls back, and says so."""

    from app import control as control_mod

    monkeypatch.setattr(control_mod, "_EDGE_SHARED_SECRET", "global-secret")
    monkeypatch.delenv("TYPOED_SECRET_NAME", raising=False)
    with caplog.at_level("WARNING"):
        resolved = control_mod._edge_secret_for(_entry_with_secret("TYPOED_SECRET_NAME"))
    assert resolved == "global-secret"
    assert "TYPOED_SECRET_NAME" in caplog.text


def test_candidates_use_the_entry_secret(monkeypatch) -> None:
    from starlette.datastructures import Headers

    from app import control as control_mod

    monkeypatch.setattr(control_mod, "_EDGE_SHARED_SECRET", "global-secret")
    monkeypatch.setenv("XARM_EDGE_SHARED_SECRET", "xarm-secret")
    request = MagicMock()
    request.headers = Headers({"x-auth-user": "alice@example.edu"})
    request.cookies = {}

    candidates = control_mod._device_auth_candidates(
        request, _entry_with_secret("XARM_EDGE_SHARED_SECRET")
    )
    assert candidates[0]["X-Edge-Auth"] == "xarm-secret"
