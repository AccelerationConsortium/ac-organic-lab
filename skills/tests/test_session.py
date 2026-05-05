"""``Lab.connect()`` / ``LabSession`` tests, including ``EquipmentInMaintenance``."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from ac_organic_lab_skills import (
    EquipmentClient,
    EquipmentInMaintenance,
    Lab,
    RegistryError,
    load_registry,
    wait_until_state,
)
from ac_organic_lab_skills.exceptions import WaitTimeout

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_registry():
    return load_registry(FIXTURE_DIR / "equipment_with_maintenance.yaml")


@pytest.mark.asyncio
async def test_lab_connect_returns_client_for_healthy_entry(fixture_registry) -> None:
    async with Lab.connect(registry=fixture_registry) as lab:
        client = lab.get("env_north")
        assert isinstance(client, EquipmentClient)
        assert client.equipment_id == "env_north"


@pytest.mark.asyncio
async def test_lab_get_unknown_id_raises_registry_error(fixture_registry) -> None:
    async with Lab.connect(registry=fixture_registry) as lab:
        with pytest.raises(RegistryError):
            lab.get("does_not_exist")


@pytest.mark.asyncio
async def test_lab_get_disabled_entry_raises_maintenance(fixture_registry) -> None:
    async with Lab.connect(registry=fixture_registry) as lab:
        with pytest.raises(EquipmentInMaintenance) as exc_info:
            lab.get("plateloc")
    assert exc_info.value.equipment_id == "plateloc"
    # `enabled: false` without a `maintenance:` block: reason/until/contact are None.
    assert exc_info.value.reason is None
    assert exc_info.value.until is None


@pytest.mark.asyncio
async def test_lab_get_maintenance_block_carries_metadata(fixture_registry) -> None:
    async with Lab.connect(registry=fixture_registry) as lab:
        with pytest.raises(EquipmentInMaintenance) as exc_info:
            lab.get("filtration_press")
    e = exc_info.value
    assert e.reason == "Awaiting replacement seal foil"
    assert e.until == date(2026, 6, 15)
    assert e.contact == "alice@lab"


@pytest.mark.asyncio
async def test_role_binding_resolves_to_client(fixture_registry) -> None:
    binding = {"sensor": "env_north"}
    async with Lab.connect(registry=fixture_registry, binding=binding) as lab:
        c = lab.role("sensor")
        assert c.equipment_id == "env_north"


@pytest.mark.asyncio
async def test_role_unbound_raises_registry_error(fixture_registry) -> None:
    async with Lab.connect(registry=fixture_registry) as lab:
        with pytest.raises(RegistryError):
            lab.role("sealer")


@pytest.mark.asyncio
async def test_session_outside_context_manager_rejects_get(fixture_registry) -> None:
    """Calling ``get()`` before ``__aenter__`` (without ``async with``) is an
    error - the shared HTTP client has not been created yet.
    """

    session = Lab.connect(registry=fixture_registry)
    with pytest.raises(RuntimeError):
        session.get("env_north")


@pytest.mark.asyncio
async def test_wait_until_state_succeeds(fixture_registry) -> None:
    """``wait_until_state`` returns when the device reports the expected
    state. Uses a real (in-process mocked) HTTP path through the SDK's
    ``EquipmentClient`` so we exercise the same code workflow code does.
    """

    # Use a fixture entry that has a base_url
    registry = load_registry(FIXTURE_DIR / "equipment_with_maintenance.yaml")
    # filtration_press is in maintenance; build a fresh entry without it.
    from ac_organic_lab_skills import Registry, EquipmentEntry

    entry = EquipmentEntry(
        id="ready_dev",
        name="Ready Device",
        platform="hte",
        kind="plate_sealer",
        adapter="http",
        base_url="http://ready.test:8000",
        poll_timeout_seconds=1.0,
    )
    test_registry = Registry(equipment=[entry])
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
        async with Lab.connect(registry=test_registry) as lab:
            client = lab.get(entry.id)
            state = await wait_until_state(
                client, "ready", timeout=2.0, poll_interval=0.05
            )
    assert state == "ready"


@pytest.mark.asyncio
async def test_wait_until_state_times_out() -> None:
    from ac_organic_lab_skills import EquipmentEntry, Registry

    entry = EquipmentEntry(
        id="busy_dev",
        name="Busy Device",
        platform="hte",
        kind="plate_sealer",
        adapter="http",
        base_url="http://busy.test:8000",
        poll_timeout_seconds=0.5,
    )
    test_registry = Registry(equipment=[entry])
    body = {
        "protocol_version": "1.0",
        "equipment_id": entry.id,
        "equipment_name": entry.name,
        "equipment_kind": entry.kind,
        "equipment_status": "busy",
        "device_time": "2026-04-29T22:50:01Z",
    }
    with respx.mock(base_url=entry.base_url) as router:
        router.get("/status").mock(return_value=httpx.Response(200, json=body))
        async with Lab.connect(registry=test_registry) as lab:
            client = lab.get(entry.id)
            with pytest.raises(WaitTimeout) as exc_info:
                await wait_until_state(
                    client, "ready", timeout=0.3, poll_interval=0.05
                )
    assert exc_info.value.equipment_id == entry.id
    assert exc_info.value.last_state == "busy"
