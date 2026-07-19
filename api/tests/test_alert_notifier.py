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
