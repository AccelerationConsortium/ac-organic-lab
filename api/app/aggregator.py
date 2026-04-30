"""Equipment aggregator.

Owns one shared `httpx.AsyncClient`, fans out fetches across registered
equipment, and exposes both batched and single-equipment status views.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from .adapters import AdapterResult, EquipmentAdapter, build_adapter
from .models import EquipmentList, EquipmentSnapshot
from .registry import EquipmentEntry, Registry


class EquipmentAggregator:
    """Holds adapters and shared HTTP client; serves dashboard requests."""

    def __init__(self, registry: Registry) -> None:
        self._registry = registry
        self._adapters: dict[str, EquipmentAdapter] = {
            entry.id: build_adapter(entry) for entry in registry.equipment
        }
        self._client: httpx.AsyncClient | None = None

    async def startup(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(5.0))

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def registry(self) -> Registry:
        return self._registry

    @property
    def equipment_count(self) -> int:
        return len(self._registry.equipment)

    def entry(self, equipment_id: str) -> EquipmentEntry | None:
        return self._registry.by_id(equipment_id)

    async def fetch_one(self, equipment_id: str) -> EquipmentSnapshot | None:
        entry = self.entry(equipment_id)
        if entry is None:
            return None
        adapter = self._adapters[equipment_id]
        if self._client is None:
            await self.startup()
        assert self._client is not None
        result = await adapter.fetch(self._client)
        return _snapshot(entry, result)

    async def fetch_all(self) -> EquipmentList:
        if self._client is None:
            await self.startup()
        assert self._client is not None

        tasks = [
            self._adapters[entry.id].fetch(self._client)
            for entry in self._registry.equipment
        ]
        results = await asyncio.gather(*tasks)
        snapshots = [
            _snapshot(entry, result)
            for entry, result in zip(self._registry.equipment, results)
        ]
        return EquipmentList(equipment=snapshots, fetched_at=datetime.now(timezone.utc))


def _snapshot(entry: EquipmentEntry, result: AdapterResult) -> EquipmentSnapshot:
    location = None
    if entry.location is not None:
        location = entry.location.model_dump()
    return EquipmentSnapshot(
        id=entry.id,
        name=entry.name,
        platform=entry.platform,
        kind=entry.kind,
        adapter=entry.adapter,
        status=result.status,
        fetched_at=result.fetched_at,
        latency_ms=result.latency_ms,
        fetch_error=result.error,
        base_url=entry.base_url,
        location=location,
        tile=entry.tile,
    )
