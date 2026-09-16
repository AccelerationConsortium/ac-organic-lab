"""Read-only TCP connection monitor; sends no printer protocol commands."""

from __future__ import annotations

import asyncio
import time
from ipaddress import IPv4Address

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ..models import ComponentStatus, EquipmentStatus, MetricValue
from .base import AdapterResult, EquipmentAdapter, now_utc
from .ssh_network import network_target_config


class TcpTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: IPv4Address
    port: int = Field(ge=1, le=65535)


class TcpNetworkAdapter(EquipmentAdapter):
    async def fetch(self, client: httpx.AsyncClient) -> AdapterResult:
        try:
            target = TcpTarget.model_validate(network_target_config(self.entry.id))
        except (OSError, ValueError, KeyError) as exc:
            return self.fail(f"Network monitor configuration: {exc}", "unconfigured")
        address = str(target.address)
        started = time.monotonic()
        writer = None
        connected = False
        message = "Printer service reachable. Print status and cloud access are unverified."
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(address, target.port),
                timeout=3,
            )
            connected = True
        except (asyncio.TimeoutError, OSError) as exc:
            message = f"Printer service unreachable: {type(exc).__name__}: {exc}"
        finally:
            if writer is not None:
                writer.close()
                await writer.wait_closed()
        elapsed_ms = int((time.monotonic() - started) * 1000)
        metrics = {"printer_ip": MetricValue(value=address)}
        if connected:
            metrics["tcp_connect"] = MetricValue(value=elapsed_ms, unit="ms")
        stamp = now_utc()
        status = EquipmentStatus(
            protocol_version="1.2",
            equipment_id=self.entry.id,
            equipment_name=self.entry.name,
            equipment_kind=self.entry.kind,
            equipment_status="ready" if connected else "unknown",
            message=message,
            device_time=stamp,
            activity="unknown",
            allowed_actions=[],
            metrics=metrics,
            components={
                "printer_service": ComponentStatus(
                    connected=connected,
                    state="up" if connected else "down",
                    message=f"TCP port {target.port}",
                )
            },
            details={
                "monitoring_only": True,
                "scope": "network_connection",
                "printer_activity": "unknown",
                "cloud_access": "unverified",
                "poll_interval_s": 60,
            },
        )
        return AdapterResult(status=status, fetched_at=stamp, latency_ms=elapsed_ms, error=None)
