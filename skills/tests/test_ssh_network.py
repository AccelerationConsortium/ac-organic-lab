"""Connection monitoring must not mistake ARP/sharing for print readiness."""

import asyncio
import base64
import json
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from lab_skills.registry import EquipmentEntry
from lab_skills.status_adapters.factory import build_adapter
from lab_skills.status_adapters.ssh_network import (
    NetworkObservation,
    probe_command,
)


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    path = tmp_path / "network.local.json"
    path.write_text(
        json.dumps(
            {
                "printer": {
                    "ssh_alias": "printer-host",
                    "interface": "Ethernet 2",
                    "uplink": "Wi-Fi",
                    "mac": "00-11-22-33-44-55",
                    "subnet": "192.0.2.0/24",
                }
            }
        )
    )
    monkeypatch.setenv("LAB_NETWORK_MONITORS_CONFIG", str(path))
    return build_adapter(
        EquipmentEntry(
            id="printer",
            name="Printer connection",
            kind="other",
            adapter="ssh_network",
            gateway_fronted=True,
        )
    )


def observed(**changes):
    return NetworkObservation(
        **(
            {
                "ethernet": True,
                "uplink": True,
                "sharing": True,
                "dhcp": True,
                "reachable": True,
                "ip": "192.0.2.119",
                "latency_ms": 1,
            }
            | changes
        )
    )


def test_success_is_network_only(adapter):
    result = adapter.result(observed(), 12)
    assert result.status.equipment_status == "ready"
    assert result.status.activity == "unknown"
    assert result.status.allowed_actions == []
    assert result.status.details["cloud_access"] == "unverified"
    assert result.status.details["monitoring_only"] is True


@pytest.mark.parametrize("field", ["dhcp", "sharing", "uplink"])
def test_sharing_failure_is_degraded_even_when_ping_works(adapter, field):
    assert adapter.result(observed(**{field: False}), 0).status.equipment_status == "degraded"


def test_no_live_printer_is_unknown_for_gateway_offline_accounting(adapter):
    result = adapter.result(observed(reachable=False, ip=None, latency_ms=None), 0)
    assert result.status.equipment_status == "unknown"
    assert result.status.metrics == {}
    assert not result.status.components["printer_ethernet"].connected


def process_returning(payload):
    return Mock(
        returncode=0, communicate=AsyncMock(return_value=(json.dumps(payload).encode(), b""))
    )


async def test_dhcp_address_change_is_observed(adapter, monkeypatch):
    process = process_returning(observed(ip="192.0.2.120").model_dump())
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    async with httpx.AsyncClient() as client:
        result = await adapter.fetch(client)
    assert result.error is None
    assert result.status.metrics["printer_ip"].value == "192.0.2.120"


@pytest.mark.parametrize(
    "payload", [{}, {"reachable": "true"}, observed(ip="198.51.100.1").model_dump()]
)
async def test_invalid_observation_never_reports_healthy(adapter, monkeypatch, payload):
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", AsyncMock(return_value=process_returning(payload))
    )
    async with httpx.AsyncClient() as client:
        result = await adapter.fetch(client)
    assert result.error is not None
    assert result.status.equipment_status == "unknown"


async def test_missing_config_is_visible(adapter, monkeypatch):
    monkeypatch.setenv("LAB_NETWORK_MONITORS_CONFIG", "/nonexistent/network.local.json")
    async with httpx.AsyncClient() as client:
        result = await adapter.fetch(client)
    assert result.error.kind == "unconfigured"


async def test_cancelled_probe_kills_child(adapter, monkeypatch):
    entered = asyncio.Event()

    async def block():
        entered.set()
        await asyncio.Event().wait()

    process = Mock(returncode=None)
    # An async side effect must await rather than return a coroutine.
    calls = 0

    async def communicate():
        nonlocal calls
        calls += 1
        if calls == 1:
            await block()
        return b"", b""

    process.communicate = communicate
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    async with httpx.AsyncClient() as client:
        task = asyncio.create_task(adapter.fetch(client))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    process.kill.assert_called_once()


def test_local_config_is_data_not_shell(adapter):
    target = adapter.target().model_copy(update={"interface": "Ethernet'; Write-Output nope; '"})
    command = probe_command(target)
    script = base64.b64decode(command[-1].split()[-1]).decode("utf-16le")
    assert target.interface not in script
    assert "StrictHostKeyChecking=yes" in command
    assert "EnableSharing" not in script and "Restart-Service" not in script
