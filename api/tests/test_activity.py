"""Unit tests for the server-side activity derivation (STATUS_SPEC v1.2 §2.3).

`derive_activity` is the ONE shared definition consumed by both the recorder
(`main._uptime_poll_loop`) and the presentation layer; these tests pin its
resolution order: device-reported → §2.3 state invariants → per-kind
component sniff → unknown.
"""

from __future__ import annotations

from datetime import datetime, timezone

from lab_skills import ComponentStatus, EquipmentStatus, FetchError

from app.events import (
    ACTIVITY_TRANSITION,
    APP_EVENT_TYPES,
    derive_activity,
    snapshot_activity,
)


def _status(**overrides) -> EquipmentStatus:
    base = dict(
        equipment_id="shaker_sc25xr",
        equipment_name="Torrey Pines SC25XR",
        equipment_kind="shaker",
        equipment_status="ready",
        device_time=datetime.now(timezone.utc),
    )
    base.update(overrides)
    return EquipmentStatus(**base)


class _Snap:
    """Minimal stand-in for the SDK's EquipmentSnapshot (duck-typed)."""

    def __init__(self, status: EquipmentStatus, fetch_error: FetchError | None = None):
        self.status = status
        self.fetch_error = fetch_error


# ------------------------------------------------------------------ device-reported (v1.2)

def test_device_reported_activity_wins():
    # The motivating case: degraded (heater RTD fault) AND running (motor mid-cycle).
    status = _status(
        protocol_version="1.2",
        equipment_status="degraded",
        activity="running",
        # a contradictory motor sniff must NOT override the device's own answer
        components={"motor": ComponentStatus(connected=True, state="idle")},
    )
    assert derive_activity(status) == ("running", "device")


def test_device_reported_idle_wins_over_state():
    status = _status(protocol_version="1.2", equipment_status="error", activity="idle")
    assert derive_activity(status) == ("idle", "device")


# ------------------------------------------------------------------ §2.3 state invariants

def test_busy_implies_running():
    assert derive_activity(_status(equipment_status="busy")) == ("running", "status")


def test_ready_requires_init_e_stop_imply_idle():
    for state in ("ready", "requires_init", "e_stop"):
        assert derive_activity(_status(equipment_status=state)) == ("idle", "status")


def test_unpinned_states_fall_through():
    # degraded/error/dry_run/unknown allow either answer — no sniff for a
    # press, so the honest answer is unknown.
    for state in ("degraded", "error", "dry_run", "unknown"):
        status = _status(equipment_kind="press", equipment_status=state)
        assert derive_activity(status) == ("unknown", "none")


# ------------------------------------------------------------------ per-kind component sniff (§2.3.2)

def test_degraded_shaker_motor_shaking_is_running():
    status = _status(
        equipment_status="degraded",
        components={
            "motor": ComponentStatus(connected=True, state="shaking"),
            "heater": ComponentStatus(connected=True, state="unknown", message="cal3"),
        },
    )
    assert derive_activity(status) == ("running", "components")


def test_degraded_shaker_motor_stopped_is_idle():
    status = _status(
        equipment_status="degraded",
        components={"motor": ComponentStatus(connected=True, state="stopped")},
    )
    assert derive_activity(status) == ("idle", "components")


def test_shaker_sniff_is_honest_when_motor_unhelpful():
    # missing, disconnected, or state-unknown motor → unknown, not false idle
    cases = [
        {},
        {"motor": ComponentStatus(connected=False, state="running")},
        {"motor": ComponentStatus(connected=True, state="unknown")},
        {"motor": ComponentStatus(connected=True, state="")},
    ]
    for components in cases:
        status = _status(equipment_status="degraded", components=components)
        assert derive_activity(status) == ("unknown", "none"), components


# ------------------------------------------------------------------ recorder-facing wrapper

def test_snapshot_activity_unreachable_is_unknown():
    # fetch_error overrides everything — a stale "running" must not extend
    # through an outage.
    snap = _Snap(
        _status(equipment_status="busy"),
        fetch_error=FetchError(kind="timeout", message="timed out"),
    )
    assert snapshot_activity(snap) == ("unknown", "none")


def test_snapshot_activity_reachable_delegates():
    snap = _Snap(_status(equipment_status="busy"))
    assert snapshot_activity(snap) == ("running", "status")


def test_activity_transition_is_in_pinned_vocabulary():
    assert ACTIVITY_TRANSITION in APP_EVENT_TYPES
    assert ACTIVITY_TRANSITION == "activity_transition"
