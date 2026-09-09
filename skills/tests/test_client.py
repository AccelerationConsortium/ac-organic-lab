"""``EquipmentClient`` tests with respx-mocked HTTP."""

from __future__ import annotations

import httpx
import pytest
import respx

from lab_skills import EquipmentClient, EquipmentUnreachable
from lab_skills.registry import EquipmentEntry


def _entry(**overrides) -> EquipmentEntry:
    base = dict(
        id="test_dev",
        name="Test Device",
        kind="plate_sealer",
        adapter="http",
        base_url="http://device.local:8000",
        status_path="/status",
        poll_timeout_seconds=1.0,
    )
    base.update(overrides)
    return EquipmentEntry(**base)


@pytest.fixture
async def http():
    async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as c:
        yield c


@pytest.mark.asyncio
async def test_status_round_trip(http) -> None:
    entry = _entry()
    body = {
        "protocol_version": "1.0",
        "equipment_id": entry.id,
        "equipment_name": entry.name,
        "equipment_kind": entry.kind,
        "equipment_status": "ready",
        "device_time": "2026-04-29T22:50:01Z",
    }
    with respx.mock(base_url=entry.base_url) as router:
        router.get("/status").mock(return_value=httpx.Response(200, json=body))
        client = EquipmentClient(entry, http)
        envelope = await client.status()
    assert envelope.equipment_status == "ready"
    assert envelope.equipment_id == entry.id


@pytest.mark.asyncio
async def test_probe_and_health(http) -> None:
    entry = _entry()
    probe_body = {
        "equipment_id": entry.id,
        "equipment_name": entry.name,
        "protocol_version": "1.0",
    }
    with respx.mock(base_url=entry.base_url) as router:
        router.get("/").mock(return_value=httpx.Response(200, json=probe_body))
        router.get("/health").mock(return_value=httpx.Response(200, json={"status": "healthy"}))
        client = EquipmentClient(entry, http)
        probe = await client.probe()
        health = await client.health()
    assert probe.equipment_id == entry.id
    assert health.status == "healthy"


@pytest.mark.asyncio
async def test_status_timeout_raises_unreachable(http) -> None:
    entry = _entry()
    with respx.mock(base_url=entry.base_url) as router:
        router.get("/status").mock(side_effect=httpx.TimeoutException("timed out"))
        client = EquipmentClient(entry, http)
        with pytest.raises(EquipmentUnreachable) as exc_info:
            await client.status()
    assert exc_info.value.equipment_id == entry.id
    assert "timeout" in exc_info.value.message.lower()


@pytest.mark.asyncio
async def test_status_5xx_raises_unreachable(http) -> None:
    entry = _entry()
    with respx.mock(base_url=entry.base_url) as router:
        router.get("/status").mock(return_value=httpx.Response(500))
        client = EquipmentClient(entry, http)
        with pytest.raises(EquipmentUnreachable):
            await client.status()


@pytest.mark.asyncio
async def test_documentation_discovery_reads_the_running_gateway(http) -> None:
    entry = _entry(kind="liquid_handler")
    with respx.mock(base_url=entry.base_url) as router:
        router.get("/docs/agent").mock(
            return_value=httpx.Response(
                200,
                json={
                    "documentation_version": "1",
                    "equipment_kind": "liquid_handler",
                    "model": "Opentrons Flex",
                    "links": {"actions": "/plans/actions"},
                },
            )
        )
        router.get("/plans/actions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "actions": [
                        {
                            "action": "gripper_move_to_relative",
                            "idempotent": False,
                            "args_schema": {"type": "object"},
                        }
                    ]
                },
            )
        )
        router.get("/openapi.json").mock(
            return_value=httpx.Response(
                200,
                json={
                    "openapi": "3.1.0",
                    "info": {"title": "gateway"},
                    "paths": {"/control/gripper-move-to-relative": {"post": {}}},
                },
            )
        )
        discovered = await EquipmentClient(entry, http).discover()

    assert discovered.agent_docs is not None
    assert discovered.agent_docs.model == "Opentrons Flex"
    assert discovered.action_catalog is not None
    assert discovered.action_catalog.actions[0].action == "gripper_move_to_relative"
    assert discovered.openapi is not None
    assert "/control/gripper-move-to-relative" in discovered.openapi.paths
    assert discovered.unavailable == {}


@pytest.mark.asyncio
async def test_documentation_discovery_reports_older_gateway_endpoints(http) -> None:
    entry = _entry(kind="liquid_handler")
    with respx.mock(base_url=entry.base_url) as router:
        for path in ("/docs/agent", "/plans/actions"):
            router.get(path).mock(return_value=httpx.Response(404))
        router.get("/openapi.json").mock(
            return_value=httpx.Response(
                200,
                json={"openapi": "3.1.0", "info": {}, "paths": {"/status": {"get": {}}}},
            )
        )
        discovered = await EquipmentClient(entry, http).discover()

    assert discovered.agent_docs is None
    assert discovered.action_catalog is None
    assert set(discovered.unavailable) == {"/docs/agent", "/plans/actions"}
    assert "predate" in discovered.unavailable["/plans/actions"]
    assert discovered.openapi is not None


@pytest.mark.asyncio
async def test_status_invalid_envelope_raises_unreachable(http) -> None:
    entry = _entry()
    with respx.mock(base_url=entry.base_url) as router:
        router.get("/status").mock(return_value=httpx.Response(200, json={"hello": "world"}))
        client = EquipmentClient(entry, http)
        with pytest.raises(EquipmentUnreachable):
            await client.status()


@pytest.mark.asyncio
async def test_no_base_url_raises_unreachable(http) -> None:
    entry = _entry(base_url=None)
    client = EquipmentClient(entry, http)
    with pytest.raises(EquipmentUnreachable):
        await client.status()
