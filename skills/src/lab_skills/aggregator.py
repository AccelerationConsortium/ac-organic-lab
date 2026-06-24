"""Equipment aggregator.

Owns one shared `httpx.AsyncClient`, fans out fetches across registered
equipment, and exposes both batched and single-equipment status views.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from .status_adapters import AdapterResult, EquipmentAdapter, build_adapter
from .models import EquipmentList, EquipmentSnapshot
from .registry import EquipmentEntry, Registry


logger = logging.getLogger("lab_skills.aggregator")


# No single device fetch may exceed this many seconds in the batched
# ``/api/equipment`` path. This caps a device whose own ``poll_timeout_seconds``
# is larger (e.g. the OT-2, whose ``/status`` builds a snapshot over SSH) so that
# one slow or unreachable device cannot stall the whole dashboard. Per-device
# timeouts smaller than this still apply unchanged.
_MAX_FETCH_SECONDS = 8.0

# Default cadence of the background poll loop that refreshes the cache. Matches
# the frontend's React Query refetch interval so dashboard reads are always served
# a snapshot at most this stale.
_DEFAULT_POLL_INTERVAL_S = 2.5


class EquipmentAggregator:
    """Holds adapters and a shared HTTP client; serves dashboard requests.

    The aggregator can run a single background poll loop (``start_polling``)
    that fans out to every device on a fixed cadence and caches the result.
    ``get_snapshot`` then serves that cache without touching the network, so a
    slow or dead device never stalls ``/api/equipment`` and N dashboard viewers
    cost one fan-out, not N. ``fetch_all`` / ``fetch_one`` remain available for
    a forced live read (e.g. the single-device detail endpoint).
    """

    def __init__(
        self,
        registry: Registry,
        *,
        poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
    ) -> None:
        self._registry = registry
        self._adapters: dict[str, EquipmentAdapter] = {
            entry.id: build_adapter(entry) for entry in registry.equipment
        }
        self._client: httpx.AsyncClient | None = None
        self._poll_interval_s = poll_interval_s
        self._cache: EquipmentList | None = None
        self._poll_task: asyncio.Task | None = None

    async def startup(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(5.0))

    async def start_polling(self) -> None:
        """Start the background poll loop that keeps the cache warm.

        Idempotent. The loop polls immediately on its first iteration (no
        leading sleep), so the cache is typically populated within one fan-out
        of this call; until then ``get_snapshot`` falls back to a live fetch.
        """
        await self.startup()
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_loop())

    async def _poll_loop(self) -> None:
        while True:
            try:
                self._cache = await self.fetch_all()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # keep the loop (and the last good cache) alive
                logger.warning("Aggregator poll error: %s", exc)
            try:
                await asyncio.sleep(self._poll_interval_s)
            except asyncio.CancelledError:
                break

    async def stop_polling(self) -> None:
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

    def cached_snapshot(self) -> EquipmentList | None:
        """The most recent cached snapshot, or ``None`` if not yet primed."""
        return self._cache

    async def get_snapshot(self) -> EquipmentList:
        """Serve the cached snapshot, falling back to a live fan-out only when
        the background loop has not yet produced its first result.

        This is the read path for ``/api/equipment``: in steady state it returns
        in-memory data (no network), so the dashboard renders independently of
        any device's reachability or latency.
        """
        if self._cache is not None:
            return self._cache
        return await self.fetch_all()

    async def shutdown(self) -> None:
        await self.stop_polling()
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
            self._bounded_fetch(entry) for entry in self._registry.equipment
        ]
        results = await asyncio.gather(*tasks)
        snapshots = [
            _snapshot(entry, result)
            for entry, result in zip(self._registry.equipment, results)
        ]
        return EquipmentList(equipment=snapshots, fetched_at=datetime.now(timezone.utc))

    async def _bounded_fetch(self, entry: EquipmentEntry) -> AdapterResult:
        """Fetch one device's status, capped so a single slow or unreachable
        device cannot stall the whole batched dashboard fetch.

        Each device still honours its own ``poll_timeout_seconds`` inside the
        adapter, but no fetch is allowed to exceed ``_MAX_FETCH_SECONDS``, so
        ``/api/equipment`` returns within roughly that bound even during a
        full-fleet outage. A capped fetch renders as an ``unknown`` / ``timeout``
        tile, exactly like any other unreachable device.
        """

        adapter = self._adapters[entry.id]
        assert self._client is not None
        cap = min(entry.poll_timeout_seconds, _MAX_FETCH_SECONDS)
        try:
            return await asyncio.wait_for(adapter.fetch(self._client), timeout=cap)
        except (asyncio.TimeoutError, TimeoutError):
            return adapter.fail(
                f"Status fetch exceeded {cap:.0f}s cap (device unreachable?)",
                kind="timeout",
            )
        except Exception as exc:  # adapters shouldn't raise; never let one kill the batch
            return adapter.fail(f"Unexpected fetch error: {exc}", kind="unknown")


def _snapshot(entry: EquipmentEntry, result: AdapterResult) -> EquipmentSnapshot:
    return EquipmentSnapshot(
        id=entry.id,
        name=entry.name,
        kind=entry.kind,
        adapter=entry.adapter,
        status=result.status,
        fetched_at=result.fetched_at,
        latency_ms=result.latency_ms,
        fetch_error=result.error,
        base_url=entry.base_url,
    )
