"""``EquipmentClient`` tests with respx-mocked HTTP."""

from __future__ import annotations

import httpx
import pytest
import respx

from lab_skills import DocumentIdentityMismatch, EquipmentClient, EquipmentUnreachable
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


def _cytation_guide(**overrides) -> dict:
    """A trimmed copy of the Cytation 5's /docs/agent shape (2026-10-14)."""

    guide = {
        "documentation_version": "1.1.0",
        "equipment_id": "test_dev",
        "protocol_version": "1.2",
        "discovery": {"status": "/status", "openapi": "/openapi.json"},
        "agent_boundaries": ["Use the lab-skills SDK for hardware operations."],
        "measurement_recipe": {"summary": "How to take a wavelength sweep."},
        "plate_requirements": ["Confirm the physical plate with the operator."],
        "action_schemas": {"paths": {"/control/read/absorbance": {"post": {}}}},
    }
    guide.update(overrides)
    return guide


def _mock_device_docs(router, guide, *, actions_status: int = 404) -> None:
    router.get("/docs/agent").mock(return_value=httpx.Response(200, json=guide))
    router.get("/plans/actions").mock(return_value=httpx.Response(actions_status))
    router.get("/openapi.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "openapi": "3.1.0",
                "info": {"title": "reader"},
                "paths": {"/control/read/absorbance": {"post": {}}},
            },
        )
    )


@pytest.mark.asyncio
async def test_device_guide_is_returned_exactly_as_served(http) -> None:
    entry = _entry(kind="plate_reader")
    guide = _cytation_guide()
    with respx.mock(base_url=entry.base_url) as router:
        _mock_device_docs(router, guide)
        discovered = await EquipmentClient(entry, http).discover_device_guide()

    assert discovered.guide is not None
    assert discovered.guide.equipment_id == "test_dev"
    assert discovered.guide.document == guide  # not normalized
    assert discovered.action_catalog is None
    assert set(discovered.unavailable) == {"/plans/actions"}
    assert discovered.openapi is not None
    assert "/control/read/absorbance" in discovered.openapi.paths


@pytest.mark.asyncio
async def test_device_guide_shape_is_rejected_by_the_typed_gateway_path(http) -> None:
    # The strict path stays strict: no equipment_kind, and discovery is a dict.
    entry = _entry(kind="plate_reader")
    with respx.mock(base_url=entry.base_url) as router:
        _mock_device_docs(router, _cytation_guide())
        with pytest.raises(EquipmentUnreachable, match="agent documentation body is not valid"):
            await EquipmentClient(entry, http).discover()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"equipment_id": "cytation_6"}, "identifies 'cytation_6', expected 'test_dev'"),
        ({"equipment_id": None}, "names no equipment_id"),
        ({"equipment_kind": "plate_sealer"}, "kind is 'plate_sealer', expected 'plate_reader'"),
    ],
)
async def test_device_guide_for_another_device_is_refused(http, overrides, fragment) -> None:
    entry = _entry(kind="plate_reader")
    guide = {k: v for k, v in _cytation_guide(**overrides).items() if v is not None}
    with respx.mock(base_url=entry.base_url) as router:
        _mock_device_docs(router, guide)
        with pytest.raises(DocumentIdentityMismatch) as exc_info:
            await EquipmentClient(entry, http).discover_device_guide()
    assert fragment in exc_info.value.message
    assert exc_info.value.path == "/docs/agent"


@pytest.mark.asyncio
async def test_device_guide_must_be_a_json_object(http) -> None:
    entry = _entry(kind="plate_reader")
    with respx.mock(base_url=entry.base_url) as router:
        router.get("/docs/agent").mock(return_value=httpx.Response(200, json=["not", "a", "guide"]))
        with pytest.raises(EquipmentUnreachable, match="device guide body is not valid"):
            await EquipmentClient(entry, http).device_guide()


@pytest.mark.asyncio
async def test_device_guide_transport_error_is_not_an_older_deployment(http) -> None:
    entry = _entry(kind="plate_reader")
    with respx.mock(base_url=entry.base_url) as router:
        router.get("/docs/agent").mock(side_effect=httpx.ConnectError("refused"))
        router.get("/plans/actions").mock(return_value=httpx.Response(404))
        router.get("/openapi.json").mock(return_value=httpx.Response(404))
        with pytest.raises(EquipmentUnreachable, match="cannot connect"):
            await EquipmentClient(entry, http).discover_device_guide()


@pytest.mark.asyncio
async def test_device_guide_absent_is_reported_unavailable(http) -> None:
    entry = _entry(kind="plate_reader")
    with respx.mock(base_url=entry.base_url) as router:
        for path in ("/docs/agent", "/plans/actions", "/openapi.json"):
            router.get(path).mock(return_value=httpx.Response(404))
        discovered = await EquipmentClient(entry, http).discover_device_guide()
    assert discovered.guide is None
    assert set(discovered.unavailable) == {"/docs/agent", "/plans/actions", "/openapi.json"}


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
