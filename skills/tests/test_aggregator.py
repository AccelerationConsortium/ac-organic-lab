"""``EquipmentAggregator`` parity tests with respx-mocked HTTP.

These complement the per-adapter tests in ``test_adapters.py``: they exercise
``fetch_one`` / ``fetch_all`` against a multi-entry registry to make sure
batch fetching, error classification, and the ``EquipmentSnapshot`` shape all
hold together.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from ac_organic_lab_skills import EquipmentAggregator, Registry
from ac_organic_lab_skills.registry import EquipmentEntry


def _spec_envelope(entry: EquipmentEntry, state: str) -> dict:
    return {
        "protocol_version": "1.0",
        "equipment_id": entry.id,
        "equipment_name": entry.name,
        "equipment_kind": entry.kind,
        "equipment_status": state,
        "device_time": "2026-04-29T22:50:01Z",
    }


@pytest.fixture
def two_device_registry() -> Registry:
    return Registry(equipment=[
        EquipmentEntry(
            id="sealer_a",
            name="Sealer A",
            platform="hte",
            kind="plate_sealer",
            adapter="http",
            base_url="http://sealer-a.test:8000",
            poll_timeout_seconds=1.0,
        ),
        EquipmentEntry(
            id="sensor_b",
            name="Sensor B",
            platform="lab",
            kind="environmental_sensor",
            adapter="mock",
        ),
    ])


@pytest.mark.asyncio
async def test_fetch_one_returns_snapshot(two_device_registry: Registry) -> None:
    sealer = two_device_registry.by_id("sealer_a")
    assert sealer is not None
    aggregator = EquipmentAggregator(two_device_registry)
    await aggregator.startup()
    try:
        with respx.mock(base_url=sealer.base_url) as router:
            router.get("/status").mock(
                return_value=httpx.Response(200, json=_spec_envelope(sealer, "ready"))
            )
            snapshot = await aggregator.fetch_one("sealer_a")
        assert snapshot is not None
        assert snapshot.id == "sealer_a"
        assert snapshot.status.equipment_status == "ready"
        assert snapshot.fetch_error is None
        # SDK snapshot must NOT carry tile / location.
        d = snapshot.model_dump()
        assert "tile" not in d
        assert "location" not in d
    finally:
        await aggregator.shutdown()


@pytest.mark.asyncio
async def test_fetch_all_emits_one_snapshot_per_entry(
    two_device_registry: Registry,
) -> None:
    sealer = two_device_registry.by_id("sealer_a")
    assert sealer is not None
    aggregator = EquipmentAggregator(two_device_registry)
    await aggregator.startup()
    try:
        with respx.mock(base_url=sealer.base_url) as router:
            router.get("/status").mock(
                return_value=httpx.Response(200, json=_spec_envelope(sealer, "ready"))
            )
            listing = await aggregator.fetch_all()
        assert [s.id for s in listing.equipment] == ["sealer_a", "sensor_b"]
        # Mock adapter on the env sensor produces dry_run with metrics.
        env = listing.equipment[1]
        assert env.status.equipment_status == "dry_run"
        assert set(env.status.metrics.keys()) == {"temperature", "humidity", "o2", "voc"}
    finally:
        await aggregator.shutdown()


@pytest.mark.asyncio
async def test_fetch_one_unknown_id_returns_none(two_device_registry: Registry) -> None:
    aggregator = EquipmentAggregator(two_device_registry)
    await aggregator.startup()
    try:
        result = await aggregator.fetch_one("does_not_exist")
        assert result is None
    finally:
        await aggregator.shutdown()


@pytest.mark.asyncio
async def test_fetch_one_classifies_timeout(two_device_registry: Registry) -> None:
    sealer = two_device_registry.by_id("sealer_a")
    assert sealer is not None
    aggregator = EquipmentAggregator(two_device_registry)
    await aggregator.startup()
    try:
        with respx.mock(base_url=sealer.base_url) as router:
            router.get("/status").mock(side_effect=httpx.TimeoutException("timed out"))
            snapshot = await aggregator.fetch_one("sealer_a")
        assert snapshot is not None
        assert snapshot.fetch_error is not None
        assert snapshot.fetch_error.kind == "timeout"
        assert snapshot.status.equipment_status == "unknown"
    finally:
        await aggregator.shutdown()
