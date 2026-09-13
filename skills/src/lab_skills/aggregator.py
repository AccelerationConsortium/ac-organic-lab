"""Equipment aggregator.

Owns one shared `httpx.AsyncClient`, fans out fetches across registered
equipment, and exposes both batched and single-equipment status views.
"""

from __future__ import annotations

import asyncio
import logging
import time
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

# Connection-pool limits for the shared client. httpx's defaults
# (``max_keepalive_connections=20``, ``keepalive_expiry=5.0``) are sized for a
# handful of hosts, not a fleet: with more devices than keepalive slots the
# surplus re-opens a TCP connection on every poll. Sizing the pool to the fleet
# costs nothing and removes that ceiling.
#
# Be clear about what this does *not* buy, because it was measured and it
# surprised us (2026-09-12, fleet on a congested 2.4 GHz link): reuse is decided
# by the far end, not here. Device services run uvicorn, which defaults to
# ``--timeout-keep-alive 5``, so a connection idle longer than ~5 s is closed by
# the device regardless of this client's expiry. When a fan-out cycle itself
# takes longer than that — it took ~10 s on that link — every connection is
# already cold by its next use, and raising these limits changes nothing
# measurable (A/B/A/B over 28 devices: median cycle 10.3 s before, 12.1 s after,
# i.e. inside the link's own noise).
#
# So this is a precondition, not a cure. It becomes load-bearing once cycles
# finish inside the device-side keep-alive window — either because the fleet is
# polled less often (per-device intervals) or because the link is fast again.
_MAX_POOL_CONNECTIONS = 100
_KEEPALIVE_EXPIRY_S = 120.0

# Per-device cadence. The background loop is a scheduler, not a fixed-rate
# fan-out: each device is re-read when *its* interval elapses, as its own
# task, and rescheduled from the moment its fetch *completes*. Two things
# follow. A slow device (an 8 s cap on a bad link) no longer holds anyone
# else's next read — measured 2026-09-12 with a shared gather, the 2.5 s tier
# was refreshing every 10.9–14.5 s because every due device waited for the
# slowest before being rescheduled. And a device's idle gap between fetches
# is exactly its interval, so at the 2.5 s default the connection sits
# inside uvicorn's 5 s keep-alive window and is reused; anchored to the
# *start* instead, a device slower than its interval would be polled
# back-to-back, which is more load, not less. On a congested link the cost of a poll
# is the round trip, not the bytes (measured 2026-09-12: 64 B and 1400 B pings
# within 10 % of each other at ~1.8 s), so request *count* is the only lever —
# and a gateway that refreshes its own probe every 10 s returns identical
# bytes to three of every four 2.5 s polls.
#
# Where a device's interval comes from, in order:
#   1. ``EquipmentEntry.poll_interval_seconds`` — the registry always wins.
#   2. ``details.poll_interval_s`` on the device's own envelope — the device's
#      statement of how often its data changes. Honoured only in the slowing
#      direction (``max(default, hint)``), capped, and remembered across a
#      failed fetch so an unreachable gateway is not suddenly hammered.
#   3. The aggregator default (constructor / ``AGGREGATOR_POLL_INTERVAL_S``).
_DEVICE_HINT_KEY = "poll_interval_s"
_MAX_HINT_INTERVAL_S = 300.0   # a buggy device may not freeze its own tile
_MIN_TICK_S = 0.25             # scheduler never spins faster than this


class EquipmentAggregator:
    """Holds adapters and a shared HTTP client; serves dashboard requests.

    The aggregator can run a single background poll loop (``start_polling``)
    that keeps a per-device cache warm. The first pass reads every device so
    the cache is complete within one fan-out; after that each device is re-read
    on its own cadence (see the module comment on where that comes from), each
    as its own task, so no device waits on another. ``get_snapshot`` serves the
    cache without touching the network, so a slow or dead device never stalls
    ``/api/equipment`` and N dashboard viewers cost one fan-out, not N.
    ``fetch_all`` / ``fetch_one`` remain available for a forced live read
    (e.g. the single-device detail endpoint).
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
        # Scheduler state, keyed by equipment id.
        self._snapshots: dict[str, EquipmentSnapshot] = {}
        self._next_due: dict[str, float] = {}          # time.monotonic()
        self._hint_interval: dict[str, float] = {}     # last good device hint
        self._effective_interval: dict[str, float] = {}

    async def startup(self) -> None:
        if self._client is None:
            keepalive = max(_MAX_POOL_CONNECTIONS, self.equipment_count)
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(5.0),
                limits=httpx.Limits(
                    max_connections=keepalive,
                    max_keepalive_connections=keepalive,
                    keepalive_expiry=_KEEPALIVE_EXPIRY_S,
                ),
            )

    async def start_polling(self) -> None:
        """Start the background poll loop that keeps the cache warm.

        Idempotent. The loop polls every device immediately on its first pass
        (no leading sleep), so the cache is typically complete within one
        fan-out of this call; until then ``get_snapshot`` falls back to a live
        fetch.
        """
        await self.startup()
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_loop())

    def poll_interval_for(self, equipment_id: str) -> float:
        """The poll interval currently in force for one device, in seconds.

        Reflects whatever the scheduler has learned so far — a device hint is
        only known after its first successful fetch.
        """
        entry = self.entry(equipment_id)
        if entry is None:
            raise KeyError(equipment_id)
        return self._interval_for(entry)

    def _interval_for(self, entry: EquipmentEntry) -> float:
        if entry.poll_interval_seconds is not None:
            return entry.poll_interval_seconds
        hint = self._hint_interval.get(entry.id)
        if hint is not None:
            return max(self._poll_interval_s, hint)
        return self._poll_interval_s

    def _note_hint(self, entry: EquipmentEntry, result: AdapterResult) -> None:
        """Remember a device's ``details.poll_interval_s`` if it published one.

        A failed fetch carries a synthetic envelope with no details; the last
        good hint is kept so an outage does not reset a slow device to the
        fast default. ``bool`` is excluded explicitly (it subclasses ``int``).
        """
        if result.error is not None:
            return
        raw = (result.status.details or {}).get(_DEVICE_HINT_KEY)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw <= 0:
            return
        self._hint_interval[entry.id] = min(float(raw), _MAX_HINT_INTERVAL_S)

    def _reschedule(
        self, entry: EquipmentEntry, result: AdapterResult, now: float
    ) -> None:
        self._note_hint(entry, result)
        interval = self._interval_for(entry)
        prev = self._effective_interval.get(entry.id)
        # Log a device the first time it deviates from the default, and every
        # time its interval changes afterwards — not the ~50 lines a prime pass
        # at the default would otherwise emit.
        if (prev is None and interval != self._poll_interval_s) or (
            prev is not None and prev != interval
        ):
            if entry.poll_interval_seconds is not None:
                source = "registry"
            elif entry.id in self._hint_interval:
                source = "device hint"
            else:
                source = "default"
            logger.info(
                "Poll interval: %s %s → %.1f s (%s)",
                entry.id,
                f"{prev:.1f} s" if prev is not None else "—",
                interval,
                source,
            )
        self._effective_interval[entry.id] = interval
        self._next_due[entry.id] = now + interval

    async def _refresh(self, entries: list[EquipmentEntry]) -> None:
        """Fetch ``entries`` concurrently as one batch, update their cached
        snapshots, reschedule each, and republish the batch view.

        Used for the priming pass, where completing the whole cache at once
        matters more than cadence. Steady-state reads go through
        :meth:`_refresh_one`, one task per device.
        """
        if self._client is None:
            await self.startup()
        results = await asyncio.gather(
            *[self._bounded_fetch(entry) for entry in entries]
        )
        now = time.monotonic()
        for entry, result in zip(entries, results):
            self._snapshots[entry.id] = _snapshot(entry, result)
            self._reschedule(entry, result, now)
        self._publish()

    async def _refresh_one(self, entry: EquipmentEntry) -> None:
        """Read one device, update its snapshot, reschedule it from *now*,
        and republish. ``_bounded_fetch`` never raises, so neither does this
        (short of cancellation)."""
        result = await self._bounded_fetch(entry)
        self._snapshots[entry.id] = _snapshot(entry, result)
        self._reschedule(entry, result, time.monotonic())
        self._publish()

    def _publish(self) -> None:
        """Rebuild the batch view from the per-device cache, in registry order.

        ``EquipmentList.fetched_at`` is when this view was assembled; each
        snapshot's own ``fetched_at`` is when *that* device was last read.
        """
        equipment = [
            self._snapshots[entry.id]
            for entry in self._registry.equipment
            if entry.id in self._snapshots
        ]
        self._cache = EquipmentList(
            equipment=equipment, fetched_at=datetime.now(timezone.utc)
        )

    async def _poll_loop(self) -> None:
        entries = list(self._registry.equipment)
        # Prime: every device in one pass, so the cache is complete at once.
        try:
            await self._refresh(entries)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # keep the loop alive; retry on the next pass
            logger.warning("Aggregator poll error: %s", exc)
        tick_floor = min(_MIN_TICK_S, self._poll_interval_s)
        in_flight: dict[str, asyncio.Task] = {}
        try:
            while True:
                now = time.monotonic()
                for entry in entries:
                    task = in_flight.get(entry.id)
                    if task is not None:
                        if not task.done():
                            continue  # never two reads of one device at once
                        del in_flight[entry.id]
                        exc = task.exception()
                        if exc is not None:  # should not happen; keep the loop alive
                            logger.warning(
                                "Aggregator poll error (%s): %s", entry.id, exc
                            )
                    if self._next_due.get(entry.id, 0.0) <= now:
                        in_flight[entry.id] = asyncio.create_task(
                            self._refresh_one(entry)
                        )
                # Sleep until the earliest device not currently being read is
                # due — but wake early the moment any in-flight read completes,
                # because that device has just rescheduled itself and may be
                # due long before anything else is. (A plain sleep here starved
                # fast devices: the loop slept until a *slow* device's turn
                # while the fast ones sat completed and unlaunched.)
                now = time.monotonic()
                next_due = min(
                    (
                        self._next_due.get(e.id, 0.0)
                        for e in entries
                        if e.id not in in_flight
                    ),
                    default=now + self._poll_interval_s,
                )
                sleep_for = max(tick_floor, next_due - now)
                if in_flight:
                    await asyncio.wait(
                        list(in_flight.values()),
                        timeout=sleep_for,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                else:
                    await asyncio.sleep(sleep_for)
        except asyncio.CancelledError:
            for task in in_flight.values():
                task.cancel()
            await asyncio.gather(*in_flight.values(), return_exceptions=True)

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
