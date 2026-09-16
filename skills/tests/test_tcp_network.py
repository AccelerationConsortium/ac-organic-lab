"""TCP checks must not issue commands or infer print state from reachability."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from lab_skills.registry import EquipmentEntry
from lab_skills.status_adapters.factory import build_adapter


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    config = tmp_path / "network.local.json"
    config.write_text(json.dumps({"printer": {"address": "192.0.2.18", "port": 3030}}))
    monkeypatch.setenv("LAB_NETWORK_MONITORS_CONFIG", str(config))
    return build_adapter(
        EquipmentEntry(
            id="printer",
            name="Resin printer",
            kind="other",
            adapter="tcp_network",
            gateway_fronted=True,
        )
    )


async def test_success_closes_without_sending_commands(adapter, monkeypatch):
    writer = Mock(wait_closed=AsyncMock())
    connect = AsyncMock(return_value=(Mock(), writer))
    monkeypatch.setattr(asyncio, "open_connection", connect)
    async with httpx.AsyncClient() as client:
        result = await adapter.fetch(client)
    connect.assert_awaited_once_with("192.0.2.18", 3030)
    writer.write.assert_not_called()
    writer.close.assert_called_once()
    writer.wait_closed.assert_awaited_once()
    assert result.status.equipment_status == "ready"
    assert result.status.activity == "unknown"
    assert result.status.allowed_actions == []
    assert result.status.details["cloud_access"] == "unverified"


@pytest.mark.parametrize(
    "error", [ConnectionRefusedError(), asyncio.TimeoutError(), OSError("no route")]
)
async def test_no_connection_is_unreachable(adapter, monkeypatch, error):
    monkeypatch.setattr(asyncio, "open_connection", AsyncMock(side_effect=error))
    async with httpx.AsyncClient() as client:
        result = await adapter.fetch(client)
    assert result.status.equipment_status == "unknown"
    assert not result.status.components["printer_service"].connected
    assert "tcp_connect" not in result.status.metrics


async def test_address_must_be_literal_ipv4(adapter, monkeypatch, tmp_path):
    config = tmp_path / "invalid.local.json"
    config.write_text(json.dumps({"printer": {"address": "printer.invalid", "port": 3030}}))
    monkeypatch.setenv("LAB_NETWORK_MONITORS_CONFIG", str(config))
    connect = AsyncMock()
    monkeypatch.setattr(asyncio, "open_connection", connect)
    async with httpx.AsyncClient() as client:
        result = await adapter.fetch(client)
    assert result.error.kind == "unconfigured"
    connect.assert_not_called()


async def test_cancellation_propagates_to_pending_connection(adapter, monkeypatch):
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def connect(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(asyncio, "open_connection", connect)
    async with httpx.AsyncClient() as client:
        task = asyncio.create_task(adapter.fetch(client))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert cancelled.is_set()
