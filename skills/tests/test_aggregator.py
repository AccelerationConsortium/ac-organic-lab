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

from lab_skills import EquipmentAggregator, Registry
from lab_skills.registry import EquipmentEntry


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
            kind="plate_sealer",
            adapter="http",
            base_url="http://sealer-a.test:8000",
            poll_timeout_seconds=1.0,
        ),
        EquipmentEntry(
            id="sensor_b",
            name="Sensor B",
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
        # Exact keys/units are pinned in test_status_adapters.py; here we only
        # care that the aggregator carries a populated zone envelope through.
        env = listing.equipment[1]
        assert env.status.equipment_status == "dry_run"
        assert {"temperature", "humidity", "voc"} <= set(env.status.metrics)
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


# -- Per-device poll cadence (scheduler) ------------------------------------
#
# These use a very small default interval so the scheduler runs many passes
# inside a fraction of a second. Margins are deliberately loose: the assertions
# are about *ordering* (a slow device is read far fewer times than a fast one),
# never about an exact count.

import asyncio

from pydantic import ValidationError


def _hinted_envelope(entry: EquipmentEntry, state: str, hint) -> dict:
    env = _spec_envelope(entry, state)
    env["details"] = {"poll_interval_s": hint}
    return env


def _http_entry(eid: str, host: str, **kw) -> EquipmentEntry:
    return EquipmentEntry(
        id=eid, name=eid, kind="other", adapter="http",
        base_url=f"http://{host}.test:8000", poll_timeout_seconds=1.0, **kw,
    )


def test_registry_poll_interval_defaults_to_none_and_rejects_zero() -> None:
    assert _http_entry("a", "a").poll_interval_seconds is None
    assert _http_entry("b", "b", poll_interval_seconds=30.0).poll_interval_seconds == 30.0
    with pytest.raises(ValidationError):
        _http_entry("c", "c", poll_interval_seconds=0)


async def _run_scheduler(registry: Registry, routes: dict, *, default: float, for_s: float):
    """Start the poll loop against respx routes, let it run, stop it."""
    aggregator = EquipmentAggregator(registry, poll_interval_s=default)
    with respx.mock(assert_all_called=False) as router:
        mocked = {eid: router.get(url) for eid, url in routes.items()}
        for eid, route in mocked.items():
            route.mock(**routes_impl[eid])
        await aggregator.start_polling()
        await asyncio.sleep(for_s)
        await aggregator.stop_polling()
        listing = await aggregator.get_snapshot()
    await aggregator.shutdown()
    return aggregator, mocked, listing


routes_impl: dict = {}


@pytest.mark.asyncio
async def test_device_hint_slows_polling_but_never_speeds_it() -> None:
    fast = _http_entry("fast", "fast")        # hints 0.001 s — must be ignored
    slow = _http_entry("slow", "slow")        # hints 0.6 s — must be honoured
    plain = _http_entry("plain", "plain")     # no hint — default cadence
    registry = Registry(equipment=[fast, slow, plain])
    routes_impl.clear()
    routes_impl.update({
        "fast": dict(return_value=httpx.Response(200, json=_hinted_envelope(fast, "ready", 0.001))),
        "slow": dict(return_value=httpx.Response(200, json=_hinted_envelope(slow, "ready", 0.6))),
        "plain": dict(return_value=httpx.Response(200, json=_spec_envelope(plain, "ready"))),
    })
    default = 0.05
    aggregator, mocked, listing = await _run_scheduler(
        registry,
        {e.id: f"{e.base_url}/status" for e in registry.equipment},
        default=default, for_s=0.5,
    )
    # Effective intervals: registry default for fast/plain, the hint for slow.
    assert aggregator.poll_interval_for("fast") == default
    assert aggregator.poll_interval_for("plain") == default
    assert aggregator.poll_interval_for("slow") == 0.6
    # The slow device was read once (the prime pass) and not again inside 0.5 s;
    # the fast ones many times.
    assert mocked["slow"].call_count == 1
    assert mocked["fast"].call_count >= 4
    assert mocked["plain"].call_count >= 4
    # The published list still carries every device, in registry order, and the
    # slow device's own fetched_at is no newer than the list's assembled_at.
    assert [s.id for s in listing.equipment] == ["fast", "slow", "plain"]
    slow_snap = listing.equipment[1]
    assert slow_snap.fetch_error is None
    assert slow_snap.fetched_at <= listing.fetched_at


@pytest.mark.asyncio
async def test_registry_interval_overrides_device_hint_and_hint_is_capped() -> None:
    forced = _http_entry("forced", "forced", poll_interval_seconds=0.7)  # hints 0.001
    capped = _http_entry("capped", "capped")                              # hints 1e9
    junk = _http_entry("junk", "junk")                                    # hints True
    registry = Registry(equipment=[forced, capped, junk])
    routes_impl.clear()
    routes_impl.update({
        "forced": dict(return_value=httpx.Response(200, json=_hinted_envelope(forced, "ready", 0.001))),
        "capped": dict(return_value=httpx.Response(200, json=_hinted_envelope(capped, "ready", 1e9))),
        "junk":   dict(return_value=httpx.Response(200, json=_hinted_envelope(junk, "ready", True))),
    })
    aggregator, mocked, _ = await _run_scheduler(
        registry, {e.id: f"{e.base_url}/status" for e in registry.equipment},
        default=0.05, for_s=0.3,
    )
    assert aggregator.poll_interval_for("forced") == 0.7      # registry wins
    assert mocked["forced"].call_count == 1
    assert aggregator.poll_interval_for("capped") == 300.0    # _MAX_HINT_INTERVAL_S
    assert mocked["capped"].call_count == 1
    assert aggregator.poll_interval_for("junk") == 0.05       # bool hint ignored
    assert mocked["junk"].call_count >= 3


@pytest.mark.asyncio
async def test_device_hint_survives_a_failed_fetch() -> None:
    dev = _http_entry("dev", "dev")
    registry = Registry(equipment=[dev])
    calls = {"n": 0}

    def first_ok_then_timeout(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=_hinted_envelope(dev, "ready", 0.3))
        raise httpx.TimeoutException("gateway went away")

    routes_impl.clear()
    routes_impl.update({"dev": dict(side_effect=first_ok_then_timeout)})
    aggregator, mocked, listing = await _run_scheduler(
        registry, {"dev": f"{dev.base_url}/status"}, default=0.05, for_s=0.75,
    )
    # Prime at t=0 (ok, hint 0.3), then failures at ~0.3 and ~0.6 — never a
    # burst back to the 0.05 s default while the device is unreachable.
    assert 2 <= mocked["dev"].call_count <= 4, mocked["dev"].call_count
    assert aggregator.poll_interval_for("dev") == 0.3
    snap = listing.equipment[0]
    assert snap.fetch_error is not None and snap.fetch_error.kind == "timeout"


@pytest.mark.asyncio
async def test_a_slow_device_does_not_hold_back_a_fast_one() -> None:
    """The regression the per-device scheduler exists to prevent.

    With a shared gather, every due device waited for the slowest before being
    rescheduled — measured live 2026-09-12 as the 2.5 s tier refreshing every
    10.9–14.5 s behind an 8 s-cap device. Here ``slow`` takes 0.4 s to answer
    and ``fast`` answers instantly; both are on a 0.05 s interval. ``fast`` must
    be read many times while ``slow`` is still mid-flight.
    """
    fast = _http_entry("fast", "fast")
    slow = _http_entry("slow", "slow")          # poll_timeout 1.0 → cap 1.0 > 0.4
    registry = Registry(equipment=[fast, slow])

    async def answer_slowly(request):
        await asyncio.sleep(0.4)
        return httpx.Response(200, json=_spec_envelope(slow, "ready"))

    routes_impl.clear()
    routes_impl.update({
        "fast": dict(return_value=httpx.Response(200, json=_spec_envelope(fast, "ready"))),
        "slow": dict(side_effect=answer_slowly),
    })
    aggregator, mocked, listing = await _run_scheduler(
        registry, {e.id: f"{e.base_url}/status" for e in registry.equipment},
        default=0.05, for_s=0.6,
    )
    # Prime (~0.4 s, both in one batch) then ~0.2 s of steady state: fast gets
    # several reads in that window; slow gets the prime plus at most one more.
    assert mocked["fast"].call_count >= 3, mocked["fast"].call_count
    assert 1 <= mocked["slow"].call_count <= 2, mocked["slow"].call_count
    # Never two concurrent reads of one device: slow's second read (if any)
    # started only after its first completed, so its snapshot is a real answer.
    assert listing.equipment[1].fetch_error is None


# -- Dead reused keep-alive connection ---------------------------------------
#
# uvicorn closes an idle keep-alive socket after 5 s. A poll sent on a pooled
# socket in that same instant fails with "Server disconnected without sending
# a response" (httpx.RemoteProtocolError) — a property of connection reuse, not
# of the device. The adapter retries exactly once on a fresh connection.

@pytest.mark.asyncio
async def test_fetch_retries_once_when_the_reused_connection_was_closed() -> None:
    dev = _http_entry("dev", "dev")
    registry = Registry(equipment=[dev])
    aggregator = EquipmentAggregator(registry)
    await aggregator.startup()
    try:
        with respx.mock(base_url=dev.base_url) as router:
            router.get("/status").mock(side_effect=[
                httpx.RemoteProtocolError("Server disconnected without sending a response."),
                httpx.Response(200, json=_spec_envelope(dev, "ready")),
            ])
            snap = await aggregator.fetch_one("dev")
        assert snap is not None and snap.fetch_error is None
        assert snap.status.equipment_status == "ready"
    finally:
        await aggregator.shutdown()


@pytest.mark.asyncio
async def test_fetch_reports_a_second_disconnect_instead_of_looping() -> None:
    dev = _http_entry("dev", "dev")
    registry = Registry(equipment=[dev])
    aggregator = EquipmentAggregator(registry)
    await aggregator.startup()
    try:
        with respx.mock(base_url=dev.base_url) as router:
            route = router.get("/status").mock(
                side_effect=httpx.RemoteProtocolError("Server disconnected without sending a response.")
            )
            snap = await aggregator.fetch_one("dev")
        assert route.call_count == 2            # one retry, no more
        assert snap is not None and snap.fetch_error is not None
        assert snap.fetch_error.kind == "unknown"
        assert "disconnected" in snap.fetch_error.message
    finally:
        await aggregator.shutdown()


def test_client_keepalive_expiry_is_below_uvicorns_idle_timeout() -> None:
    from lab_skills import aggregator as agg
    assert agg._KEEPALIVE_EXPIRY_S < 5.0
