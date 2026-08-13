"""Unit tests for LabDatabase read helpers backing /api/history/*."""

from __future__ import annotations

import os
import sqlite3

import pytest
from pydantic import ValidationError

from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.db import LabDatabase
from app.events import ACTIVITY_TRANSITION, STATE_TRANSITION


def _db(tmp_path: Path) -> LabDatabase:
    db = LabDatabase(tmp_path / "lab.db")
    db.open()
    return db


def test_get_control_actions_flattens_payload(tmp_path):
    db = _db(tmp_path)
    db.record_equipment_event(
        "plateloc",
        "control_action",
        message="seal/start",
        payload={
            "action": "seal/start",
            "method": "POST",
            "status_code": 200,
            "outcome": "ok",
            "owner": "alice@utoronto.ca",
            "duration_s": 8.412,
        },
    )
    db.record_equipment_event(
        "fume_hood_actuator",
        "control_action",
        message="sash/move",
        payload={
            "action": "sash/move",
            "method": "POST",
            "status_code": 423,
            "outcome": "claim_conflict",
            "owner": "bob@utoronto.ca",
        },
    )
    # a non-control event must not leak into the audit feed
    db.record_equipment_event("plateloc", "state_transition", from_state="ready", to_state="busy")

    rows = db.get_control_actions()
    assert len(rows) == 2  # newest first, control_action only
    assert {r["device_id"] for r in rows} == {"plateloc", "fume_hood_actuator"}
    seal = next(r for r in rows if r["device_id"] == "plateloc")
    assert seal["action"] == "seal/start"
    assert seal["owner"] == "alice@utoronto.ca"
    assert seal["status_code"] == 200
    assert seal["duration_s"] == 8.412

    only_hood = db.get_control_actions(device_id="fume_hood_actuator")
    assert len(only_hood) == 1 and only_hood[0]["outcome"] == "claim_conflict"
    # pre-duration_s rows (and refusals that never reached the device) → null
    assert only_hood[0]["duration_s"] is None

    db.close()


def test_get_control_actions_tolerates_missing_payload(tmp_path):
    db = _db(tmp_path)
    db.record_equipment_event("plateloc", "control_action", message="startup")
    rows = db.get_control_actions()
    assert rows[0]["action"] is None and rows[0]["message"] == "startup"
    db.close()


def test_get_control_actions_surfaces_origin(tmp_path):
    db = _db(tmp_path)
    db.record_equipment_event(
        "xarm",
        "control_action",
        message="graph/move_to",
        payload={"action": "graph/move_to", "owner": "a", "origin": "assistant"},
    )
    db.record_equipment_event(
        "xarm",
        "control_action",
        message="stop",
        payload={"action": "stop", "owner": "a"},
    )
    rows = db.get_control_actions()
    origin_by_action = {r["action"]: r["origin"] for r in rows}
    assert origin_by_action["graph/move_to"] == "assistant"
    assert origin_by_action["stop"] is None
    db.close()


def test_get_equipment_events_filters_by_event_type(tmp_path):
    db = _db(tmp_path)
    db.record_equipment_event("ot2_hte", "state_transition", from_state="ready", to_state="busy")
    db.record_equipment_event(
        "ot2_hte",
        "agent_observation",
        message="Blocking lights read in /status caused the flap",
        payload={"severity": "warning", "source": "claude-agent"},
    )
    db.record_equipment_event("ot2_hte", "agent_observation", message="recurrence #2")
    # unfiltered returns every kind
    assert len(db.get_equipment_events("ot2_hte")) == 3
    # filtered returns only the agent observations, newest first
    obs = db.get_equipment_events("ot2_hte", event_type="agent_observation")
    assert len(obs) == 2
    assert all(o["event_type"] == "agent_observation" for o in obs)
    assert obs[0]["message"] == "recurrence #2"
    db.close()


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_activity_time_pcts_is_a_separate_series(tmp_path):
    """The motivating v1.2 case: a device chronically `degraded` on the
    health series still yields a usable idle/running split on the activity
    series — and neither series contaminates the other."""
    db = _db(tmp_path)
    now = datetime.now(timezone.utc)

    # Health series: degraded the whole window.
    db.record_equipment_event(
        "torry_pines_shaker", STATE_TRANSITION,
        ts=_iso(now - timedelta(hours=10)),
        from_state=None, to_state="degraded",
    )
    # Activity series: idle 10h→4h ago, running 4h→1h ago, idle since.
    db.record_equipment_event(
        "torry_pines_shaker", ACTIVITY_TRANSITION,
        ts=_iso(now - timedelta(hours=10)),
        from_state=None, to_state="idle",
    )
    db.record_equipment_event(
        "torry_pines_shaker", ACTIVITY_TRANSITION,
        ts=_iso(now - timedelta(hours=4)),
        from_state="idle", to_state="running",
    )
    db.record_equipment_event(
        "torry_pines_shaker", ACTIVITY_TRANSITION,
        ts=_iso(now - timedelta(hours=1)),
        from_state="running", to_state="idle",
    )

    state = db.get_state_time_pcts("torry_pines_shaker", days=7)
    activity = db.get_activity_time_pcts("torry_pines_shaker", days=7)

    # Health bar unchanged by the parallel series: 100% degraded, and the
    # activity vocabulary never leaks into it.
    assert state == {"degraded": 100.0}
    # Activity: 3h running / 7h idle of 10h observed.
    assert abs(activity["running"] - 30.0) < 1.0
    assert abs(activity["idle"] - 70.0) < 1.0
    assert "degraded" not in activity
    db.close()


def test_activity_time_pcts_carries_pre_window_state(tmp_path):
    """A running span that began before the window is charged from
    window-start — same carry semantics as the health series."""
    db = _db(tmp_path)
    now = datetime.now(timezone.utc)
    db.record_equipment_event(
        "torry_pines_shaker", ACTIVITY_TRANSITION,
        ts=_iso(now - timedelta(days=9)),
        from_state=None, to_state="running",
    )
    activity = db.get_activity_time_pcts("torry_pines_shaker", days=7)
    assert activity == {"running": 100.0}
    db.close()


def test_get_cycle_count_windowed_delta_with_restart(tmp_path):
    """§2.3.1 counter math: sum of poll-to-poll deltas, pre-window carry as
    baseline, and a decrease treated as device restart (post-restart value
    counts, never negative usage)."""
    db = _db(tmp_path)
    now = datetime.now(timezone.utc)

    def rec(hours_ago: float, value: float) -> None:
        db.record_sensor_reading(
            "torry_pines_shaker", "cycles_total", value, "count",
            ts=_iso(now - timedelta(hours=hours_ago)),
        )

    # never reported → None ("not tracked"), distinct from 0
    assert db.get_cycle_count("torry_pines_shaker", days=7) is None

    rec(200, 400)   # pre-window (7d = 168h) — baseline carry
    rec(10, 410)    # +10 vs carry
    rec(9, 418)     # +8
    rec(8, 3)       # decrease → restart: +3, not -415
    rec(7, 5)       # +2
    assert db.get_cycle_count("torry_pines_shaker", days=7) == 23

    # first-ever reading (no carry, nothing to diff against) → 0, not None:
    # the counter is tracked, no completed delta observed yet
    db.record_sensor_reading("plateloc", "cycles_total", 1820, "count", ts=_iso(now))
    assert db.get_cycle_count("plateloc", days=7) == 0
    db.close()


def test_get_first_event_ts_tracking_since(tmp_path):
    db = _db(tmp_path)
    assert db.get_first_event_ts("plateloc", ACTIVITY_TRANSITION) is None
    now = datetime.now(timezone.utc)
    first = _iso(now - timedelta(hours=2))
    db.record_equipment_event(
        "plateloc", ACTIVITY_TRANSITION, ts=first,
        from_state=None, to_state="idle",
    )
    db.record_equipment_event(
        "plateloc", ACTIVITY_TRANSITION, ts=_iso(now),
        from_state="idle", to_state="running",
    )
    assert db.get_first_event_ts("plateloc", ACTIVITY_TRANSITION) == first
    # the other series does not answer for this one
    assert db.get_first_event_ts("plateloc", STATE_TRANSITION) is None
    db.close()



# ---------------------------------------------------------------------------
# Time-window bounds
#
# Regression guards for a bug live until 2026-07-31: cutoffs were built with
# SQLite's `datetime('now', …)`, which renders space-separated and offset-free
# ("2026-07-31 03:03:20"), while `ts` is stored as ISO-8601
# ("2026-07-31T03:02:54.880535+00:00"). Because 'T' (0x54) sorts above ' '
# (0x20), any row sharing the cutoff's *date* compared greater than the bound
# regardless of its time — so "last 1 hour" returned everything since midnight
# UTC, and the retention DELETE spared a day it should have dropped.
#
# `test_iso_ago_*` are the deterministic guards: they pin the root cause with
# no dependence on wall-clock. The behavioural tests below place rows only
# tens of minutes from their cutoff, so the row and the cutoff share a UTC date
# except in a narrow window just after midnight — keep the offsets small, or
# they stop exercising the bug at all (a 5 h offset is a different UTC date
# for most of the day, and the buggy code excludes it correctly by accident).
# ---------------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_iso_ago_is_lexicographically_comparable_to_stored_timestamps():
    """The bound must sort against `_now()`-format values as an instant."""
    from app.db import _iso_ago, _now

    stored = _now()
    assert "T" in stored and stored.endswith("+00:00")

    # An hour-old bound sorts below a just-written row; a future-dated one above.
    assert _iso_ago(hours=1) < stored
    assert _iso_ago(hours=-1) > stored
    assert _iso_ago(days=1) < _iso_ago(hours=1) < _iso_ago()

    # Same shape as what we compare against — this is the property that broke.
    bound = _iso_ago(hours=1)
    assert "T" in bound, "bound must be 'T'-separated like the stored column"
    assert " " not in bound


def test_iso_ago_beats_the_sqlite_datetime_bound_it_replaced():
    """Pin the exact failure: a space-separated bound mis-sorts every row."""
    from app.db import _iso_ago, _now

    stored = _now()                       # e.g. 2026-07-31T03:02:54.88+00:00
    sqlite_style = stored[:10] + " " + "23:59:59"   # same date, end of day

    # The old bound is *later* in the day than the row, yet compares smaller,
    # so `ts >= bound` wrongly matched. The new bound has no such inversion.
    assert stored > sqlite_style, "the historical bug, reproduced"
    assert not (stored < _iso_ago(hours=-0.001) < sqlite_style)


def test_get_sensor_readings_excludes_rows_just_outside_window(tmp_path):
    db = _db(tmp_path)
    now = datetime.now(timezone.utc)

    db.record_sensor_reading("env_hte", "temperature", 21.0, "°C",
                             ts=_iso(now - timedelta(minutes=30)))
    # 90 min old: outside a 1 h window, but same UTC day as the cutoff.
    db.record_sensor_reading("env_hte", "temperature", 99.0, "°C",
                             ts=_iso(now - timedelta(minutes=90)))

    points = db.get_sensor_readings("env_hte", "temperature", since_hours=1)
    assert [p["value"] for p in points] == [21.0]

    # Widening the window brings the older row back, in chronological order.
    points = db.get_sensor_readings("env_hte", "temperature", since_hours=3)
    assert [p["value"] for p in points] == [99.0, 21.0]
    db.close()


def test_get_sensor_readings_honours_fractional_hours(tmp_path):
    db = _db(tmp_path)
    now = datetime.now(timezone.utc)

    db.record_sensor_reading("env_hte", "humidity", 50.0, "%RH",
                             ts=_iso(now - timedelta(minutes=10)))
    db.record_sensor_reading("env_hte", "humidity", 60.0, "%RH",
                             ts=_iso(now - timedelta(minutes=40)))

    points = db.get_sensor_readings("env_hte", "humidity", since_hours=0.5)
    assert [p["value"] for p in points] == [50.0]
    db.close()


def test_get_cycle_count_excludes_cycles_just_before_the_window(tmp_path):
    db = _db(tmp_path)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=7)

    # Counter advanced 100 -> 104 just *before* the 7-day cutoff (same UTC day
    # as it), then 104 -> 106 inside the window. Only the in-window delta
    # counts, seeded by the pre-window carry-in of 104.
    db.record_sensor_reading("plateloc", "cycles_total", 100.0, "count",
                             ts=_iso(cutoff - timedelta(hours=2)))
    db.record_sensor_reading("plateloc", "cycles_total", 104.0, "count",
                             ts=_iso(cutoff - timedelta(minutes=30)))
    db.record_sensor_reading("plateloc", "cycles_total", 105.0, "count",
                             ts=_iso(now - timedelta(days=2)))
    db.record_sensor_reading("plateloc", "cycles_total", 106.0, "count",
                             ts=_iso(now - timedelta(hours=1)))

    assert db.get_cycle_count("plateloc", days=7) == 2
    # A wider window sees the earlier climb too (100 -> 106, no carry-in).
    assert db.get_cycle_count("plateloc", days=30) == 6
    db.close()


def test_prune_sensor_readings_drops_rows_just_past_retention(tmp_path):
    db = _db(tmp_path)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=30)

    # 30 min past retention, same UTC day as the cutoff — the case the buggy
    # bound spared, letting the table run ~a day over its cap.
    db.record_sensor_reading("env_hte", "temperature", 1.0, "°C",
                             ts=_iso(cutoff - timedelta(minutes=30)))
    # Just inside retention — must survive.
    db.record_sensor_reading("env_hte", "temperature", 2.0, "°C",
                             ts=_iso(cutoff + timedelta(minutes=30)))
    db.record_sensor_reading("env_hte", "temperature", 3.0, "°C",
                             ts=_iso(now - timedelta(hours=1)))

    db.prune_sensor_readings(keep_days=30)

    kept = sorted(p["value"] for p in
                  db.get_sensor_readings("env_hte", "temperature",
                                         since_hours=24 * 40, limit=100))
    assert kept == [2.0, 3.0]
    db.close()


def test_canonicalize_ts_normalises_every_accepted_form():
    """One fixed-width UTC format out, whatever ISO-8601 form comes in."""
    from app.db import canonicalize_ts

    same_instant = {
        "2026-07-31T03:02:54.500000+00:00",   # already canonical
        "2026-07-31T03:02:54.500000Z",        # Zulu suffix (device style)
        "2026-07-31T03:02:54.500000",         # naive ⇒ UTC by convention
        "2026-07-31T08:32:54.500000+05:30",   # other offset
    }
    assert {canonicalize_ts(t) for t in same_instant} == {
        "2026-07-31T03:02:54.500000+00:00"
    }

    # Microseconds are always emitted, so every value is the same width.
    assert canonicalize_ts("2026-07-31T03:02:54Z") == "2026-07-31T03:02:54.000000+00:00"
    assert len({len(canonicalize_ts(t)) for t in same_instant}) == 1

    with pytest.raises(ValueError):
        canonicalize_ts("last tuesday")


def test_canonicalize_ts_prevents_the_zulu_sort_inversion():
    """A `Z` timestamp must not sort above a bound it actually precedes."""
    from app.db import canonicalize_ts

    bound = "2026-07-31T03:02:54.500000+00:00"
    earlier_zulu = "2026-07-31T03:02:54Z"      # .000000 — before the bound

    assert earlier_zulu > bound, "the raw hazard: 'Z' (0x5A) outranks '.' (0x2E)"
    assert canonicalize_ts(earlier_zulu) < bound


def _stale_before(cutoff: datetime, *, days: int) -> tuple[datetime, float]:
    """A ts on the cutoff's UTC date but earlier in the day, plus its weight.

    This is the one shape the pre-2026-07-31 bound mishandled: a space-
    separated cutoff made *every* row sharing its date compare greater, so a
    row from 00:00 that day was pulled into a window opening at, say, 03:40.
    How much that distorts the result equals the cutoff's time-of-day, so the
    caller skips when the clock leaves too little room to tell the difference.
    """
    stale = cutoff.replace(hour=0, minute=0, second=0, microsecond=0)
    misattributed_s = (cutoff - stale).total_seconds()
    return stale, misattributed_s / (days * 86400.0) * 100.0


def test_get_uptime_pct_ignores_events_before_the_window(tmp_path):
    days = 7
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    stale, distortion_pct = _stale_before(cutoff, days=days)
    if distortion_pct < 1.0:
        pytest.skip("run too close to 00:00 UTC to distinguish the bound bug")

    db = _db(tmp_path)
    # "down" before the window is the carry-in state, not in-window downtime.
    db.record_uptime_event("plateloc", "down", ts=_iso(stale))
    # Recovered at window open, up ever since ⇒ observed time is all up.
    db.record_uptime_event("plateloc", "recovered", ts=_iso(cutoff + timedelta(seconds=1)))

    pct = db.get_uptime_pct("plateloc", days=days)
    assert pct == pytest.approx(100.0, abs=0.05), (
        f"stale pre-window 'down' leaked into the window: {pct}% "
        f"(a broken bound would read about {100.0 - distortion_pct:.1f}%)"
    )
    db.close()


def test_get_uptime_pct_charges_a_downtime_span(tmp_path):
    db = _db(tmp_path)
    now = datetime.now(timezone.utc)

    # Up for 8 h, down for 2 h, then up again ⇒ 2 of 10 observed hours down.
    db.record_uptime_event("plateloc", "up", ts=_iso(now - timedelta(hours=10)))
    db.record_uptime_event("plateloc", "down", ts=_iso(now - timedelta(hours=2)))
    db.record_uptime_event("plateloc", "recovered", ts=_iso(now))

    pct = db.get_uptime_pct("plateloc", days=7)
    assert 79.0 < pct < 81.0, f"expected ~80%, got {pct}"
    db.close()


def test_event_time_pcts_excludes_transitions_before_the_window(tmp_path):
    days = 7
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    stale, distortion_pct = _stale_before(cutoff, days=days)
    if distortion_pct < 1.0:
        pytest.skip("run too close to 00:00 UTC to distinguish the bound bug")

    db = _db(tmp_path)
    # Pre-window transition into 'error' is carry-in only; 'ready' holds from
    # window open onward and should account for ~all observed time.
    db.record_equipment_event("plateloc", STATE_TRANSITION, to_state="error",
                              ts=_iso(stale))
    db.record_equipment_event("plateloc", STATE_TRANSITION, to_state="ready",
                              ts=_iso(cutoff + timedelta(seconds=1)))

    pcts = db.get_state_time_pcts("plateloc", days=days)
    assert pcts.get("ready", 0.0) == pytest.approx(100.0, abs=0.05), pcts
    assert pcts.get("error", 0.0) == pytest.approx(0.0, abs=0.05), (
        f"stale 'error' leaked in: {pcts} "
        f"(a broken bound would charge it about {distortion_pct:.1f}%)"
    )
    db.close()


def test_event_time_pcts_handles_zulu_timestamps_from_device_ingest(tmp_path):
    """Device-ingested `Z` rows must land on the right side of the cutoff."""
    db = _db(tmp_path)
    now = datetime.now(timezone.utc)
    from app.db import canonicalize_ts

    # Written the way the ingest path now stores them: canonicalised.
    db.record_equipment_event(
        "xarm_translocation", ACTIVITY_TRANSITION, to_state="running",
        ts=canonicalize_ts(_iso(now - timedelta(hours=4)).replace("+00:00", "Z")),
    )
    db.record_equipment_event(
        "xarm_translocation", ACTIVITY_TRANSITION, to_state="idle",
        ts=canonicalize_ts(_iso(now - timedelta(hours=2)).replace("+00:00", "Z")),
    )

    pcts = db.get_activity_time_pcts("xarm_translocation", days=7)
    # 2 h running then 2 h idle out of 4 h observed.
    assert 45.0 < pcts.get("running", 0.0) < 55.0, pcts
    assert 45.0 < pcts.get("idle", 0.0) < 55.0, pcts
    db.close()


def test_ingest_record_canonicalises_device_timestamps():
    """The ingest boundary is where mixed formats were entering the column."""
    from app.history import IngestEventRecord

    rec = IngestEventRecord(timestamp="2026-07-03T20:43:19.729685Z",
                            event="state_transition")
    assert rec.timestamp == "2026-07-03T20:43:19.729685+00:00"

    # A device on a non-UTC offset is converted, not stored as sent.
    rec = IngestEventRecord(timestamp="2026-07-04T01:43:19.729685+05:00",
                            event="startup")
    assert rec.timestamp == "2026-07-03T20:43:19.729685+00:00"

    # Unparseable timestamps are rejected here rather than stored unreadable.
    with pytest.raises(ValidationError):
        IngestEventRecord(timestamp="2026/07/03 20:43", event="startup")


def test_open_falls_back_to_read_only_when_file_not_writable(tmp_path):
    """The boxed-consumer path (HERMES_ACCESS_DESIGN Phase 0): a LabDatabase
    whose file exists but is not writable opens read-only — reads work,
    writes raise, and the schema script is never attempted."""

    path = tmp_path / "lab.db"
    rw = LabDatabase(path)
    rw.open()
    rw.record_equipment_event("plateloc", "state_transition", message="ready")
    rw.close()

    os.chmod(path, 0o444)
    try:
        ro = LabDatabase(path)
        ro.open()  # must not raise trying to apply _SCHEMA
        rows = ro.get_equipment_events("plateloc", limit=5)
        assert len(rows) == 1
        assert rows[0]["message"] == "ready"
        with pytest.raises(sqlite3.OperationalError):
            ro.record_equipment_event("plateloc", "state_transition", message="x")
        ro.close()
    finally:
        os.chmod(path, 0o644)  # let pytest clean tmp_path up
