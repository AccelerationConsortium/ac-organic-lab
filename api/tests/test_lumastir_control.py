"""No hardware: exercise the real SDK against a mocked Lumastir transport."""

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from lab_skills.registry import EquipmentEntry
from app.control import build_control_router


@pytest.fixture
async def setup(monkeypatch):
    monkeypatch.setenv("CONTROL_AUTHZ_ENFORCE", "false")
    entry = EquipmentEntry(
        id="lumastir",
        name="Lumastir",
        kind="other",
        adapter="http",
        protocol="1.1",
        base_url="http://device.test",
    )
    calls = []

    def device(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "protocol_version": "1.1",
                    "equipment_id": "lumastir",
                    "equipment_name": "Lumastir",
                    "equipment_kind": "other",
                    "equipment_status": "ready",
                    "message": "",
                    "device_time": "2026-09-10T00:00:00Z",
                    "required_actions": [],
                    "allowed_actions": ["lumastir.motor.set", "lumastir.led.set"],
                },
            )
        if request.url.path.endswith("/claim"):
            return httpx.Response(
                200, json={"claim_token": "private-test-token", "heartbeat_interval_s": 10}
            )
        return httpx.Response(200, json={"status": "ok"})

    app = FastAPI()
    app.state.aggregator = SimpleNamespace(entry=lambda _: entry)
    app.include_router(build_control_router())
    async with httpx.AsyncClient(transport=httpx.MockTransport(device)) as upstream:
        app.state.control_client = upstream
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://dashboard.test",
            headers={"X-Auth-User": "operator@example.test"},
        ) as client:
            yield client, calls, entry


async def test_claim_uses_authenticated_owner_and_fixed_ttl(setup):
    client, calls, _ = setup
    r = await client.post(
        "/api/equipment/lumastir/control/claim", json={"session_id": str(uuid4())}
    )
    assert r.status_code == 200
    import json

    body = json.loads(calls[0].content)
    assert body["owner"] == "operator@example.test" and body["ttl_s"] == 30


async def test_set_uses_sdk_and_explicit_lease_without_releasing(setup):
    client, calls, _ = setup
    r = await client.post(
        "/api/equipment/lumastir/control/motor/set",
        json={"index": 0, "speed": 50},
        headers={"X-Claim-Token": "held-token"},
    )
    assert r.status_code == 200
    assert [r.url.path for r in calls] == ["/status", "/control/motor/set"]
    assert calls[-1].headers["x-claim-token"] == "held-token"


@pytest.mark.parametrize(
    "body",
    [
        {"index": 3, "speed": 50},
        {"index": 0, "speed": 101},
        {"index": 0, "speed": True},
        {"index": 0, "speed": 50, "extra": 1},
    ],
)
async def test_invalid_set_never_reaches_hardware(setup, body):
    client, calls, _ = setup
    r = await client.post(
        "/api/equipment/lumastir/control/motor/set",
        json=body,
        headers={"X-Claim-Token": "held-token"},
    )
    assert r.status_code == 422 and not calls


async def test_missing_claim_is_refused(setup):
    client, calls, _ = setup
    r = await client.post(
        "/api/equipment/lumastir/control/led/set", json={"index": 0, "brightness": 100}
    )
    assert r.status_code == 423 and not calls


async def test_maintenance_blocks_start_but_preserves_stop(setup):
    client, calls, entry = setup
    entry.enabled = False
    r = await client.post(
        "/api/equipment/lumastir/control/claim", json={"session_id": str(uuid4())}
    )
    assert r.status_code == 409 and not calls
    r = await client.post("/api/equipment/lumastir/control/stop", json={})
    assert r.status_code == 200 and calls[0].url.path == "/control/stop"


async def test_unauthenticated_request_never_contacts_device(setup):
    client, calls, _ = setup
    client.headers.pop("X-Auth-User")
    r = await client.post("/api/equipment/lumastir/control/stop", json={})
    assert r.status_code == 401 and not calls


async def test_unregistered_action_is_rejected(setup):
    client, calls, _ = setup
    r = await client.post("/api/equipment/lumastir/control/motor/run", json={})
    assert r.status_code == 404 and not calls


async def test_authz_denial_precedes_device_claim(setup, monkeypatch):
    client, calls, _ = setup
    monkeypatch.setenv("CONTROL_AUTHZ_ENFORCE", "true")

    async def deny(*args):
        return {"allowed": False}

    monkeypatch.setattr("app.control._fetch_authz_verdict", deny)
    r = await client.post(
        "/api/equipment/lumastir/control/claim", json={"session_id": str(uuid4())}
    )
    assert r.status_code == 403 and not calls

async def test_device_precondition_refusal_sends_no_setpoint(setup, monkeypatch):
    client, calls, _ = setup
    async def denied_status(self):
        return SimpleNamespace(allowed_actions=[])
    monkeypatch.setattr('app.lumastir_control.EquipmentClient.status', denied_status)
    response = await client.post('/api/equipment/lumastir/control/motor/set',
        json={'index': 0, 'speed': 50}, headers={'X-Claim-Token': 'held-token'})
    assert response.status_code == 412 and not calls

async def test_device_claim_refusal_is_preserved(setup, monkeypatch):
    from lab_skills.exceptions import BadRequest
    client, _, _ = setup
    async def expired(self, *args, **kwargs):
        raise BadRequest('lumastir', 'Claim expired', http_status=423)
    monkeypatch.setattr('app.lumastir_control.EquipmentClient.command', expired)
    response = await client.post('/api/equipment/lumastir/control/heartbeat',
        json={}, headers={'X-Claim-Token': 'expired-token'})
    assert response.status_code == 423
