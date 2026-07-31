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

from ..models import ComponentStatus, EquipmentStatus, MetricValue
from .base import AdapterResult, EquipmentAdapter, now_utc


class MockAdapter(EquipmentAdapter):
    async def fetch(self, client: httpx.AsyncClient) -> AdapterResult:
        if self.entry.kind == "environmental_sensor":
            # Mirror a real `sense-every-zone` zone: same protocol version,
            # same metric keys/units, same component names. `equipment_status`
            # stays `dry_run` on purpose — the *shape* is the real device's,
            # but the readings are synthetic and must never be mistaken for
            # lab data (STATUS_SPEC Appendix B.1: report reality, let readers
            # filter what counts). `derive_v2_fields` maps `dry_run` — and the
            # registry's `adapter: mock` — to `simulated: true`.
            zone = self.entry.id.removeprefix("env_")
            envelope = EquipmentStatus(
                protocol_version="1.2",
                equipment_id=self.entry.id,
                equipment_name=self.entry.name,
                equipment_kind=self.entry.kind,
                equipment_status="dry_run",
                message="Placeholder readings (sensor service not yet deployed)",
                device_time=now_utc(),
                activity="idle",
                activity_since=now_utc(),
                components={
                    f"sen55_{zone}": ComponentStatus(connected=True, state="ready"),
                    f"ups_{zone}": ComponentStatus(connected=True, state="ready"),
                },
                metrics=_fake_env_metrics(self.entry.id),
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
    """Synthesise the metric set a real `sense-every-zone` basic zone reports.

    The live `env_hte` node (SEN55 + PiSugar 3) is the reference shape, so the
    keys and units here mirror `sense_every_zone`'s `_METRIC_MAP` exactly — a
    mock zone must be indistinguishable from a real one in *shape*, so the
    dashboard's readers (`LabMap`, the `sensor_readings` recorder) exercise the
    same path for both. Only the values are fake.

    Deliberately absent: `co` / `o2` / `h2`. Those come from the Alphasense
    cells on the fumehood node only — a basic zone node cannot measure them,
    and inventing them would make the mocks a worse model than the hardware.
    """

    seed = _seed(equipment_id)
    base_temp = 21.5 + (seed % 30) / 10.0        # 21.5 - 24.5 °C
    base_hum = 40.0 + (seed % 200) / 10.0        # 40 - 60 %RH
    base_voc = 80.0 + (seed % 60)                # 80 - 140, Sensirion index
    base_pm25 = 4.0 + (seed % 40) / 10.0         # 4.0 - 8.0 µg/m³

    pm25 = round(base_pm25 + _drift(seed * 7, 45, 0.8), 1)
    return {
        "temperature": MetricValue(
            value=round(base_temp + _drift(seed, 90, 0.3), 2), unit="°C"
        ),
        "humidity": MetricValue(
            value=round(base_hum + _drift(seed * 3, 120, 1.5), 1), unit="%RH"
        ),
        # Sensirion VOC/NOx are unitless 1-500 indices, not a ppb concentration.
        "voc": MetricValue(
            value=round(base_voc + _drift(seed * 5, 45, 8.0)), unit="index"
        ),
        "nox": MetricValue(
            value=max(1, round(1 + _drift(seed * 11, 300, 1.0))), unit="index"
        ),
        # SEN55 reports four cumulative size bins, so pm1 <= pm25 <= pm4 <= pm10.
        "pm1": MetricValue(value=round(pm25 * 0.95, 1), unit="µg/m³"),
        "pm25": MetricValue(value=pm25, unit="µg/m³"),
        "pm4": MetricValue(value=pm25, unit="µg/m³"),
        "pm10": MetricValue(value=pm25, unit="µg/m³"),
        # PiSugar 3 UPS, mains-powered and topped up.
        "battery": MetricValue(
            value=max(0, min(100, round(96 + _drift(seed * 13, 900, 4.0)))), unit="%"
        ),
        "battery_voltage": MetricValue(
            value=round(4.10 + _drift(seed * 17, 600, 0.03), 3), unit="V"
        ),
    }
