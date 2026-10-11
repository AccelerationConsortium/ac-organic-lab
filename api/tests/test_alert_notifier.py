"""Unit tests for ``api/app/alert_notifier.py``.

Exercises the debounce/cooldown/storm rules against a mocked webhook —
no network, no sleep (a fake clock drives cooldown).
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from app.alert_notifier import AlertNotifier

URL = "http://pypoe.test/alerts/device"


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class FakeDb:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def record_equipment_event(self, device_id, event_type, **kw):
        self.events.append((device_id, event_type, kw))


def _mk(clock=None, **kw):
    return AlertNotifier(
        URL,
        db=FakeDb(),
        now=clock or FakeClock(),
        **kw,
    )


def _run(coro):
    return asyncio.run(coro)


def test_disabled_without_url(monkeypatch):
    monkeypatch.delenv("PYPOE_ALERT_URL", raising=False)
    n = AlertNotifier()
    assert not n.enabled
    n.observe("plateloc", reachable=False, state="unreachable")
    assert n._sweep == []


@respx.mock
def test_unreachable_requires_sustained_sweeps():
    route = respx.post(URL).mock(return_value=httpx.Response(202))
    n = _mk()

    async def main():
        # Sweep 1: down — no alert yet.
        n.observe("plateloc", reachable=False, state="unreachable", message="timeout")
        await n.flush()
        assert not route.called
        # Sweep 2: still down — alert fires.
        n.observe("plateloc", reachable=False, state="unreachable", message="timeout")
        await n.flush()
        assert route.call_count == 1
        import json
        body = json.loads(route.calls[0].request.content)
        assert body["device_id"] == "plateloc"
        assert body["event"] == "unreachable"

    _run(main())


@respx.mock
def test_single_sweep_blip_never_alerts():
    route = respx.post(URL).mock(return_value=httpx.Response(202))
    n = _mk()

    async def main():
        n.observe("plateloc", reachable=False, state="unreachable")
        await n.flush()
        n.observe("plateloc", reachable=True, state="ready")
        await n.flush()
        assert not route.called  # no alert → no recovery either

    _run(main())


@respx.mock
def test_recovery_sent_only_after_alert():
    route = respx.post(URL).mock(return_value=httpx.Response(202))
    n = _mk()

    async def main():
        for _ in range(2):
            n.observe("plateloc", reachable=False, state="unreachable")
            await n.flush()
        n.observe("plateloc", reachable=True, state="ready")
        await n.flush()
        import json
        events = [json.loads(c.request.content)["event"] for c in route.calls]
        assert events == ["unreachable", "recovered"]

    _run(main())


@respx.mock
def test_error_state_alerts_immediately_and_recovers():
    route = respx.post(URL).mock(return_value=httpx.Response(202))
    n = _mk()

    async def main():
        n.observe("plateloc", reachable=True, state="ready")
        await n.flush()
        n.observe(
            "plateloc",
            reachable=True,
            state="error",
            message="vacuum fault",
            last_error={"code": "vacuum_error"},
        )
        await n.flush()
        n.observe("plateloc", reachable=True, state="ready")
        await n.flush()
        import json
        bodies = [json.loads(c.request.content) for c in route.calls]
        assert [b["event"] for b in bodies] == ["error", "recovered"]
        assert bodies[0]["last_error"] == {"code": "vacuum_error"}

    _run(main())


@respx.mock
def test_error_edge_not_repeated_while_state_persists():
    route = respx.post(URL).mock(return_value=httpx.Response(202))
    n = _mk()

    async def main():
        n.observe("plateloc", reachable=True, state="error")
        await n.flush()
        for _ in range(5):
            n.observe("plateloc", reachable=True, state="error")
            await n.flush()
        assert route.call_count == 1

    _run(main())


@respx.mock
def test_cooldown_suppresses_realert_until_window_passes():
    route = respx.post(URL).mock(return_value=httpx.Response(202))
    clock = FakeClock()
    n = _mk(clock=clock, cooldown_s=1800)

    async def main():
        # Alert, recover, then fail again inside the cooldown window.
        for _ in range(2):
            n.observe("d", reachable=False, state="unreachable")
            await n.flush()
        n.observe("d", reachable=True, state="ready")
        await n.flush()
        clock.t = 600  # 10 min later — inside cooldown
        for _ in range(2):
            n.observe("d", reachable=False, state="unreachable")
            await n.flush()
        import json
        events = [json.loads(c.request.content)["event"] for c in route.calls]
        assert events == ["unreachable", "recovered"]  # re-alert suppressed
        # After the window, the same failure alerts again. The device is
        # still down, so a fresh streak has to rebuild from a recovery.
        n.observe("d", reachable=True, state="ready")
        await n.flush()
        clock.t = 3600  # past cooldown
        for _ in range(2):
            n.observe("d", reachable=False, state="unreachable")
            await n.flush()
        events = [json.loads(c.request.content)["event"] for c in route.calls]
        assert events[-1] == "unreachable"

    _run(main())


@respx.mock
def test_storm_collapse_sends_one_alert():
    route = respx.post(URL).mock(return_value=httpx.Response(202))
    n = _mk(storm_threshold=3)

    async def main():
        devices = ["a", "b", "c", "d"]
        for _ in range(2):
            for d in devices:
                n.observe(d, reachable=False, state="unreachable")
            await n.flush()
        assert route.call_count == 1
        import json
        body = json.loads(route.calls[0].request.content)
        assert body["device_id"] == "a"
        assert set(body["devices"]) == {"b", "c", "d"}
        assert "4 devices" in body["message"]

    _run(main())


@respx.mock
def test_delivery_failure_never_raises_and_audits():
    respx.post(URL).mock(side_effect=httpx.ConnectError("refused"))
    n = _mk()

    async def main():
        for _ in range(2):
            n.observe("plateloc", reachable=False, state="unreachable")
            await n.flush()

    _run(main())  # must not raise
    audit = n._db.events
    assert len(audit) == 1
    device_id, event_type, kw = audit[0]
    assert device_id == "plateloc"
    assert event_type == "alert_emitted"
    assert "failed" in kw["payload"]["outcome"]


@respx.mock
def test_successful_delivery_writes_audit_row():
    respx.post(URL).mock(return_value=httpx.Response(202))
    n = _mk()

    async def main():
        for _ in range(2):
            n.observe("plateloc", reachable=False, state="unreachable")
            await n.flush()

    _run(main())
    device_id, event_type, kw = n._db.events[0]
    assert event_type == "alert_emitted"
    assert kw["payload"]["outcome"] == "http_202"
    assert kw["payload"]["event"] == "unreachable"


# ---------------------------------------------------------------------------
# Restart safety: seed the standing-alert set from the audit rows
# ---------------------------------------------------------------------------
# Every restart of the API used to re-fire an alert for each device still in
# a fault state and forget the rest, so recoveries were lost. The journal
# restarts at 2026-10-10 19:51/19:57 and 2026-10-11 05:01/05:39 each produced
# a verbatim duplicate alert set in Slack.

import json as _json
from datetime import datetime, timedelta, timezone


class HistoryDb(FakeDb):
    def __init__(self, rows):
        super().__init__()
        self.rows = rows

    def get_latest_alerts(self, *, days=7):
        return self.rows


def _row(device, event, *, age_s=600, devices=None):
    ts = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).isoformat()
    return {"ts": ts, "device_id": device, "to_state": None, "message": "m",
            "payload": {"event": event, "devices": devices, "outcome": "http_202"}}


def _events(route):
    return [(_json.loads(c.request.content)["device_id"], _json.loads(c.request.content)["event"])
            for c in route.calls]


@respx.mock
def test_cold_start_into_standing_error_does_not_refire_but_recovers():
    route = respx.post(URL).mock(return_value=httpx.Response(202))
    clock = FakeClock()
    n = AlertNotifier(URL, db=HistoryDb([_row("lle_ur5_arm", "error")]), now=clock)
    assert n.seed_from_history() == 1

    async def main():
        # Still in error after the restart: nothing is sent.
        for _ in range(3):
            n.observe("lle_ur5_arm", reachable=True, state="error", message="Safety stop")
            await n.flush()
        assert route.call_count == 0
        # ...but the eventual recovery IS sent (it used to be lost).
        n.observe("lle_ur5_arm", reachable=True, state="ready", message="program stopped")
        await n.flush()
        assert _events(route) == [("lle_ur5_arm", "recovered")]
        # And a fresh fault afterwards alerts normally again — once the
        # per-device cooldown (back-dated from the audit row) has passed.
        clock.t += 1800
        n.observe("lle_ur5_arm", reachable=True, state="error")
        await n.flush()
        assert _events(route)[-1] == ("lle_ur5_arm", "error")

    _run(main())


@respx.mock
def test_cold_start_into_standing_unreachable_does_not_refire():
    route = respx.post(URL).mock(return_value=httpx.Response(202))
    n = AlertNotifier(URL, db=HistoryDb([_row("flex_plate_weigher", "unreachable")]), now=FakeClock())
    n.seed_from_history()

    async def main():
        for _ in range(4):
            n.observe("flex_plate_weigher", reachable=False, state="unknown")
            await n.flush()
        assert route.call_count == 0
        n.observe("flex_plate_weigher", reachable=True, state="ready")
        await n.flush()
        assert _events(route) == [("flex_plate_weigher", "recovered")]

    _run(main())


@respx.mock
def test_standing_unreachable_then_device_reported_error_is_new_information():
    route = respx.post(URL).mock(return_value=httpx.Response(202))
    n = AlertNotifier(URL, db=HistoryDb([_row("plateloc", "unreachable", age_s=7200)]), now=FakeClock())
    n.seed_from_history()

    async def main():
        n.observe("plateloc", reachable=True, state="error", message="vacuum")
        await n.flush()
        assert _events(route) == [("plateloc", "error")]

    _run(main())


@respx.mock
def test_storm_row_seeds_every_collapsed_device_and_recovery_row_seeds_nothing():
    route = respx.post(URL).mock(return_value=httpx.Response(202))
    rows = [
        _row("balance_lift", "unreachable", devices=["eufymake_connection", "gibbie_hotplate"]),
        _row("cytation_5", "recovered"),
    ]
    n = AlertNotifier(URL, db=HistoryDb(rows), now=FakeClock())
    assert n.seed_from_history() == 3
    assert set(n._active_alert) == {"balance_lift", "eufymake_connection", "gibbie_hotplate"}

    async def main():
        for _ in range(2):
            for d in ("balance_lift", "eufymake_connection", "gibbie_hotplate"):
                n.observe(d, reachable=False, state="unknown")
            await n.flush()
        assert route.call_count == 0
        # cytation_5 was recovered: a fresh fault alerts normally.
        n.observe("cytation_5", reachable=True, state="error")
        await n.flush()
        assert _events(route) == [("cytation_5", "error")]

    _run(main())


def test_seed_backdates_cooldown_clock():
    clock = FakeClock()
    clock.t = 10_000.0
    n = AlertNotifier(URL, db=HistoryDb([_row("plateloc", "error", age_s=600)]), now=clock, cooldown_s=1800)
    n.seed_from_history()
    assert n._last_sent_at["plateloc"] == pytest.approx(10_000.0 - 600, abs=5)


def test_seed_is_a_noop_without_url_or_helper(monkeypatch):
    monkeypatch.delenv("PYPOE_ALERT_URL", raising=False)
    assert AlertNotifier(db=HistoryDb([_row("x", "error")])).seed_from_history() == 0
    assert AlertNotifier(URL, db=FakeDb()).seed_from_history() == 0  # FakeDb has no helper
