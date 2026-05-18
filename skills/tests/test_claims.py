"""ClaimManager tests against respx-mocked v1.1 / v1.0 devices.

Validates the client side of the claim/heartbeat/release protocol described
in the v1.1 section of ``docs/STATUS_SPEC.md``:

* happy path acquires a token, runs heartbeats, releases on exit
* registry-declared v1.0 device skips the network entirely (no-op manager)
* runtime 404 from /control/claim degrades silently (mixed-protocol fleets)
* HTTP 409 / 423 raise :class:`ClaimRejected` with retry_after_s
* an exception inside the ``async with`` body still triggers a release
* three consecutive heartbeat failures self-cancel and surface
  :class:`EquipmentUnreachable` from ``assert_alive`` and on exit
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from lab_skills import (
    ClaimManager,
    ClaimRejected,
    EquipmentClient,
    EquipmentUnreachable,
)
from lab_skills.registry import EquipmentEntry


def _entry(**overrides) -> EquipmentEntry:
    base = dict(
        id="test_dev",
        name="Test Device",
        kind="plate_sealer",
        adapter="http",
        base_url="http://device.local:8000",
        status_path="/status",
        poll_timeout_seconds=1.0,
        protocol="1.1",
    )
    base.update(overrides)
    return EquipmentEntry(**base)


@pytest.fixture
async def http():
    async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as c:
        yield c


@pytest.mark.asyncio
async def test_claim_happy_path_acquires_heartbeats_and_releases(http) -> None:
    entry = _entry()
    with respx.mock(base_url=entry.base_url) as router:
        claim_route = router.post("/control/claim").mock(
            return_value=httpx.Response(
                200,
                json={
                    "claim_token": "tok-abc",
                    "heartbeat_interval_s": 0.02,
                    "expires_at": "2026-05-05T18:00:00Z",
                },
            )
        )
        heartbeat_route = router.post("/control/heartbeat").mock(
            return_value=httpx.Response(204)
        )
        release_route = router.post("/control/release").mock(
            return_value=httpx.Response(204)
        )
        client = EquipmentClient(entry, http)

        async with ClaimManager(client, owner="alice") as claim:
            assert not claim.degraded
            assert claim.token == "tok-abc"
            assert claim.expires_at is not None
            # Let the heartbeat task fire at least once.
            await asyncio.sleep(0.05)
            claim.assert_alive()

    assert claim_route.called
    assert release_route.called
    assert heartbeat_route.call_count >= 1
    # Heartbeat carries the X-Claim-Token header.
    last_hb = heartbeat_route.calls.last.request
    assert last_hb.headers["X-Claim-Token"] == "tok-abc"
    # Release also carries it.
    release_req = release_route.calls.last.request
    assert release_req.headers["X-Claim-Token"] == "tok-abc"


@pytest.mark.asyncio
async def test_claim_skips_network_for_registry_v10_device(http) -> None:
    """Registry-declared v1.0 devices never see ``/control/claim`` traffic."""

    entry = _entry(protocol="1.0")
    with respx.mock(base_url=entry.base_url, assert_all_called=False) as router:
        claim_route = router.post("/control/claim")
        client = EquipmentClient(entry, http)
        async with ClaimManager(client, owner="alice") as claim:
            assert claim.degraded
            assert claim.token is None
            claim.assert_alive()  # no-op; never raises for degraded managers

        assert not claim_route.called


@pytest.mark.asyncio
async def test_claim_runtime_404_degrades_silently(http) -> None:
    """Registry says v1.1 but device replies 404 -> degrade quietly."""

    entry = _entry(protocol="1.1")
    with respx.mock(base_url=entry.base_url, assert_all_called=False) as router:
        router.post("/control/claim").mock(return_value=httpx.Response(404))
        heartbeat_route = router.post("/control/heartbeat")
        release_route = router.post("/control/release")
        client = EquipmentClient(entry, http)

        async with ClaimManager(client, owner="alice") as claim:
            assert claim.degraded
            assert claim.token is None

        assert not heartbeat_route.called
        assert not release_route.called


@pytest.mark.asyncio
async def test_claim_409_raises_rejected_with_body_retry_after(http) -> None:
    entry = _entry()
    with respx.mock(base_url=entry.base_url, assert_all_called=False) as router:
        router.post("/control/claim").mock(
            return_value=httpx.Response(
                409,
                json={
                    "detail": "already held by another session",
                    "retry_after_s": 12.5,
                    "claimed_by": {
                        "session_id": "s-xyz",
                        "owner": "bob",
                        "expires_at": "2026-05-05T18:01:00Z",
                    },
                },
            )
        )
        client = EquipmentClient(entry, http)
        with pytest.raises(ClaimRejected) as exc_info:
            async with ClaimManager(client, owner="alice"):
                pass  # pragma: no cover

    err = exc_info.value
    assert err.equipment_id == "test_dev"
    assert err.http_status == 409
    assert err.retry_after_s == 12.5
    assert err.claimed_by == {
        "session_id": "s-xyz",
        "owner": "bob",
        "expires_at": "2026-05-05T18:01:00Z",
    }
    assert "already held" in err.message


@pytest.mark.asyncio
async def test_claim_423_falls_back_to_retry_after_header(http) -> None:
    entry = _entry()
    with respx.mock(base_url=entry.base_url, assert_all_called=False) as router:
        router.post("/control/claim").mock(
            return_value=httpx.Response(
                423,
                headers={"Retry-After": "7"},
                json={"detail": "device locked"},
            )
        )
        client = EquipmentClient(entry, http)
        with pytest.raises(ClaimRejected) as exc_info:
            async with ClaimManager(client, owner="alice"):
                pass  # pragma: no cover

    assert exc_info.value.http_status == 423
    assert exc_info.value.retry_after_s == 7.0


@pytest.mark.asyncio
async def test_release_runs_when_body_raises(http) -> None:
    """An exception raised inside the ``async with`` body still triggers
    /control/release. The original exception propagates to the caller."""

    entry = _entry()
    # heartbeat route may or may not fire before the body raises; what matters
    # for this test is that release_route fires.
    with respx.mock(base_url=entry.base_url, assert_all_called=False) as router:
        router.post("/control/claim").mock(
            return_value=httpx.Response(
                200,
                json={
                    "claim_token": "tok",
                    "heartbeat_interval_s": 5.0,
                    "expires_at": "2026-05-05T18:00:00Z",
                },
            )
        )
        router.post("/control/heartbeat").mock(return_value=httpx.Response(204))
        release_route = router.post("/control/release").mock(
            return_value=httpx.Response(204)
        )
        client = EquipmentClient(entry, http)

        class _Boom(Exception):
            pass

        with pytest.raises(_Boom):
            async with ClaimManager(client, owner="alice"):
                raise _Boom("workflow failed mid-cycle")

    assert release_route.called


@pytest.mark.asyncio
async def test_heartbeat_self_cancels_after_three_failures(http) -> None:
    entry = _entry()
    with respx.mock(base_url=entry.base_url) as router:
        router.post("/control/claim").mock(
            return_value=httpx.Response(
                200,
                json={
                    "claim_token": "tok",
                    "heartbeat_interval_s": 0.01,
                    "expires_at": "2026-05-05T18:00:00Z",
                },
            )
        )
        router.post("/control/heartbeat").mock(
            return_value=httpx.Response(500)
        )
        router.post("/control/release").mock(return_value=httpx.Response(204))
        client = EquipmentClient(entry, http)

        with pytest.raises(EquipmentUnreachable):
            async with ClaimManager(client, owner="alice") as claim:
                # Wait long enough for >=3 failed heartbeat attempts.
                # Interval is 10ms; 200ms is comfortably enough headroom on
                # a cold CI runner without ballooning test time.
                for _ in range(40):
                    await asyncio.sleep(0.005)
                    if claim._heartbeat_failure is not None:
                        break
                # Surface the failure synchronously - mirrors how workflow
                # code is expected to bail mid-cycle.
                claim.assert_alive()


@pytest.mark.asyncio
async def test_assert_alive_no_op_for_degraded_manager(http) -> None:
    entry = _entry(protocol="1.0")
    with respx.mock(base_url=entry.base_url, assert_all_called=False):
        client = EquipmentClient(entry, http)
        async with ClaimManager(client, owner="alice") as claim:
            assert claim.degraded
            # assert_alive must never raise for a degraded manager.
            claim.assert_alive()
            claim.assert_alive()
