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
        self.id = status.equipment_id
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


# ------------------------------------------------------------------ sniff deleted (§2.3.2 deletability, 2026-07-25)

def test_unmigrated_degraded_shaker_reads_unknown_not_sniffed():
    """The shaker motor sniff was deleted the day the device migrated to
    native v1.2 activity (§2.3.2's deletability promise, kept). A device in
    an invariant-unpinned state that doesn't report activity now honestly
    reads unknown — components no longer influence the derivation."""
    status = _status(
        equipment_status="degraded",
        components={
            "motor": ComponentStatus(connected=True, state="shaking"),
            "heater": ComponentStatus(connected=True, state="unknown", message="cal3"),
        },
    )
    assert derive_activity(status) == ("unknown", "none")


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


# ------------------------------------------------------------------ v2 projection (Appendix B.2)


def test_derive_v2_health_mapping_is_the_b2_table():
    from app.events import derive_v2_fields

    expected = {
        "ready": "healthy",
        "busy": "healthy",
        "requires_init": "requires_init",
        "degraded": "degraded",
        "error": "error",
        "e_stop": "e_stopped",
        "dry_run": "unknown",   # the word was spent on the mode axis
        "unknown": "unknown",
    }
    for state, health in expected.items():
        got, _, _ = derive_v2_fields(_status(equipment_status=state))
        assert got == health, state


def test_derive_v2_mode_and_simulated():
    from app.events import derive_v2_fields

    # plain production device
    assert derive_v2_fields(_status()) == ("healthy", "production", False)
    # dry_run device → simulated, develop
    _, mode, simulated = derive_v2_fields(_status(equipment_status="dry_run"))
    assert (mode, simulated) == ("develop", True)
    # registry mock adapter → simulated even when the envelope says ready
    _, mode, simulated = derive_v2_fields(_status(), adapter="mock")
    assert (mode, simulated) == ("develop", True)
    # registry maintenance block wins the mode
    _, mode, _ = derive_v2_fields(_status(), in_maintenance=True)
    assert mode == "maintenance"
    # maintenance + simulated: mode reads maintenance, simulated stays true
    _, mode, simulated = derive_v2_fields(
        _status(equipment_status="dry_run"), in_maintenance=True
    )
    assert (mode, simulated) == ("maintenance", True)


# ------------------------------------------------------------------ cycles_total recording


class _FakeDB:
    def __init__(self):
        self.readings: list[tuple] = []

    def record_sensor_reading(self, sensor_id, metric, value, unit, **kw):
        self.readings.append((sensor_id, metric, value, unit))


def test_record_cycles_total_stores_raw_counter():
    from lab_skills import MetricValue

    from app.main import _record_cycles_total

    db = _FakeDB()
    snap = _Snap(_status(metrics={"cycles_total": MetricValue(value=418, unit="count")}))
    _record_cycles_total(db, snap)
    assert db.readings == [("shaker_sc25xr", "cycles_total", 418.0, "count")]


def test_record_cycles_total_ignores_absent_or_non_numeric():
    from lab_skills import MetricValue

    from app.main import _record_cycles_total

    db = _FakeDB()
    # absent key
    _record_cycles_total(db, _Snap(_status()))
    # non-numeric value must not be coerced into the series
    _record_cycles_total(
        db, _Snap(_status(metrics={"cycles_total": MetricValue(value="lots")}))
    )
    assert db.readings == []


# ------------------------------------------------------ §2.1 reachability (gateway-fronted)


class _Entry:
    """Minimal stand-in for a registry EquipmentEntry."""

    def __init__(self, kind: str, gateway_fronted: bool = False):
        self.kind = kind
        self.gateway_fronted = gateway_fronted


def test_transport_failure_is_unreachable_for_any_kind():
    from app.events import snapshot_reachable

    snap = _Snap(_status(equipment_status="ready"),
                 fetch_error=FetchError(kind="timeout", message="exceeded 5s cap"))
    assert snapshot_reachable(snap, _Entry("plate_sealer")) is False
    assert snapshot_reachable(snap, _Entry("power_strip")) is False


def test_gateway_fronted_unknown_is_unreachable_without_a_fetch_error():
    """The bug this function exists for: gateway answers 200, hardware is gone.

    A plug whose strip is off the LAN reports `unknown` with NO transport
    error, so a poller keying on `fetch_error` alone logged it as `up`.
    """
    snap = _Snap(_status(equipment_kind="power_strip", equipment_status="unknown",
                         message="No route to host"))
    assert snap.fetch_error is None
    from app.events import snapshot_reachable
    for kind in ("power_strip", "smart_plug", "camera"):
        assert snapshot_reachable(snap, _Entry(kind)) is False, kind


def test_gateway_fronted_healthy_states_stay_reachable():
    from app.events import snapshot_reachable

    for state in ("ready", "busy", "degraded", "error"):
        snap = _Snap(_status(equipment_kind="power_strip", equipment_status=state))
        assert snapshot_reachable(snap, _Entry("power_strip")) is True, state


def test_registry_flagged_gateway_unknown_is_unreachable_for_any_kind():
    """§2.1's rule extended per entry: an OT-2 *gateway* answers 200 while the
    robot behind it is gone and reports `unknown`. Flagged `gateway_fronted`,
    that reads as unreachable (uptime + PyPoe); unflagged, a liquid_handler's
    `unknown` stays the unattributable cold-start case and counts as up.
    """
    from app.events import snapshot_reachable

    snap = _Snap(_status(equipment_kind="liquid_handler", equipment_status="unknown",
                         message="Robot unreachable at http://100.64.254.19:31951"))
    assert snap.fetch_error is None
    assert snapshot_reachable(snap, _Entry("liquid_handler", gateway_fronted=True)) is False
    assert snapshot_reachable(snap, _Entry("liquid_handler")) is True
    # A flagged gateway that *can* reach its hardware is reachable like anyone.
    ok = _Snap(_status(equipment_kind="liquid_handler", equipment_status="ready"))
    assert snapshot_reachable(ok, _Entry("liquid_handler", gateway_fronted=True)) is True


def test_bare_unknown_on_a_directly_polled_device_counts_as_up():
    """§2.1's other half: an unattributable `unknown` is NOT a down signal.

    A cold start before the first successful poll must not be charged as
    downtime — "asked and didn't learn" is not "couldn't ask".
    """
    from app.events import snapshot_reachable

    snap = _Snap(_status(equipment_kind="plate_sealer", equipment_status="unknown"))
    assert snapshot_reachable(snap, _Entry("plate_sealer")) is True
    # Unregistered device: no kind to key the rule on, so it stays up.
    assert snapshot_reachable(snap, None) is True


def test_gateway_fronted_kinds_match_the_spec_list():
    """§2.1 keys the rule on equipment_kind, so the set is contract."""
    from app.events import GATEWAY_FRONTED_KINDS

    assert GATEWAY_FRONTED_KINDS == {"camera", "smart_plug", "power_strip"}
