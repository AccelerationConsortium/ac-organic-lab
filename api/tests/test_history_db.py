"""Unit tests for LabDatabase read helpers backing /api/history/*."""

from __future__ import annotations

from pathlib import Path

from app.db import LabDatabase


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

    only_hood = db.get_control_actions(device_id="fume_hood_actuator")
    assert len(only_hood) == 1 and only_hood[0]["outcome"] == "claim_conflict"

    db.close()


def test_get_control_actions_tolerates_missing_payload(tmp_path):
    db = _db(tmp_path)
    db.record_equipment_event("plateloc", "control_action", message="startup")
    rows = db.get_control_actions()
    assert rows[0]["action"] is None and rows[0]["message"] == "startup"
    db.close()
