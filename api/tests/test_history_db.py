"""Unit tests for LabDatabase read helpers backing /api/history/*."""

from __future__ import annotations

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
