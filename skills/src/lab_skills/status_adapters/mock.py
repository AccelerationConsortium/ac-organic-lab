"""Placeholder adapter for equipment that hasn't been integrated yet.

Always emits an `unknown`-state envelope with a friendly message so the device
shows up on the dashboard as "not yet integrated" rather than disappearing.

Environmental sensors get fake but plausible readings so the lab map has
content to render until real sensors are deployed. The fake readings drift
slightly between polls so it's visually obvious they're live, not frozen.
"""

from __future__ import annotations

import hashlib
import math
import time

import httpx

from ..models import EquipmentStatus, MetricValue
from .base import AdapterResult, EquipmentAdapter, now_utc


class MockAdapter(EquipmentAdapter):
    async def fetch(self, client: httpx.AsyncClient) -> AdapterResult:
        if self.entry.kind == "environmental_sensor":
            metrics = _fake_env_metrics(self.entry.id)
            envelope = EquipmentStatus(
                equipment_id=self.entry.id,
                equipment_name=self.entry.name,
                equipment_kind=self.entry.kind,
                equipment_status="dry_run",
                message="Placeholder readings (sensor service not yet deployed)",
                device_time=now_utc(),
                metrics=metrics,
            )
        elif self.entry.base_url:
            # A non-sensor mock entry that points at a real base_url is a
            # deployed external web UI we only *link* to (it doesn't speak
            # STATUS_SPEC, so there's nothing to poll) — show it as a reachable
            # link tile rather than "not integrated". Not a health check: the
            # tile is a launcher, not a monitored device.
            envelope = EquipmentStatus(
                equipment_id=self.entry.id,
                equipment_name=self.entry.name,
                equipment_kind=self.entry.kind,
                equipment_status="ready",
                message="External web UI (link only — not health-polled)",
                device_time=now_utc(),
            )
        else:
            envelope = EquipmentStatus(
                equipment_id=self.entry.id,
                equipment_name=self.entry.name,
                equipment_kind=self.entry.kind,
                equipment_status="unknown",
                message="Integration not yet implemented",
                required_actions=["integrate_repo"],
                device_time=now_utc(),
            )
        return AdapterResult(
            status=envelope,
            fetched_at=now_utc(),
            latency_ms=None,
            error=None,
        )


def _seed(equipment_id: str) -> int:
    h = hashlib.sha1(equipment_id.encode()).digest()
    return int.from_bytes(h[:4], "big")


def _drift(seed: int, period_s: float, amplitude: float) -> float:
    """Smooth, deterministic-per-id drift: sinusoid in time, phase per id."""

    phase = (seed % 1000) / 1000.0 * 2 * math.pi
    t = time.time() / period_s * 2 * math.pi
    return math.sin(t + phase) * amplitude


def _fake_env_metrics(equipment_id: str) -> dict[str, MetricValue]:
    seed = _seed(equipment_id)
    base_temp = 21.5 + (seed % 30) / 10.0  # 21.5 - 24.5 °C
    base_hum = 40.0 + (seed % 200) / 10.0  # 40 - 60 %
    base_o2 = 20.9
    base_voc = 80.0 + (seed % 600) / 10.0  # 80 - 140 ppb
    return {
        "temperature": MetricValue(
            value=round(base_temp + _drift(seed, 90, 0.3), 2), unit="C"
        ),
        "humidity": MetricValue(
            value=round(base_hum + _drift(seed * 3, 120, 1.5), 1), unit="%RH"
        ),
        "o2": MetricValue(
            value=round(base_o2 + _drift(seed * 5, 60, 0.05), 2), unit="%"
        ),
        "voc": MetricValue(
            value=round(base_voc + _drift(seed * 7, 45, 8.0), 0), unit="ppb"
        ),
    }
