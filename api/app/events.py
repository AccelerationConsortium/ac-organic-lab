"""Event-type vocabulary for ``equipment_events`` + activity derivation.

``equipment_events.event_type`` has no CHECK constraint (deliberately — the
``/api/ingest/events`` path accepts device-defined types), so a typo at a
write site silently creates an invisible parallel series that no query ever
reads. Every event type *this app* writes is therefore pinned here, and write
sites import the constant instead of retyping the string.

This module also owns :func:`derive_activity` — the ONE server-side
definition of "is this device performing its primary operation right now",
shared by the recorder (``main._uptime_poll_loop``) and the presentation
layer, so the two can never disagree about what a stored
``activity_transition`` row meant.
"""

from __future__ import annotations

from lab_skills import EquipmentStatus

# --------------------------------------------------------------------------
# Event-type vocabulary (rows written by this app)
# --------------------------------------------------------------------------

#: Health series — ``equipment_status`` changes observed by the poll loop
#: (plus the synthetic ``unreachable`` presentation state on fetch failure).
STATE_TRANSITION = "state_transition"

#: Activity series (STATUS_SPEC v1.2 §2.3) — ``idle`` / ``running`` /
#: ``unknown`` transitions, recorded by the poll loop in parallel with
#: STATE_TRANSITION, reusing the same from_state/to_state columns.
ACTIVITY_TRANSITION = "activity_transition"

#: Operator control-write audit rows (written by ``control.py``).
CONTROL_ACTION = "control_action"

#: Device-alert delivery audit rows (written by ``alert_notifier.py``).
ALERT_EMITTED = "alert_emitted"

#: All event types written by this app. Device-originated types arriving via
#: /api/ingest/events (startup, shutdown, error, agent_observation, …) are
#: intentionally NOT constrained by this set.
APP_EVENT_TYPES = frozenset({
    STATE_TRANSITION,
    ACTIVITY_TRANSITION,
    CONTROL_ACTION,
    ALERT_EMITTED,
})

#: Reserved metrics key (STATUS_SPEC §2.3.1): monotonic count of completed
#: primary operations since device start, `unit: "count"`. Its poll-to-poll
#: delta reveals cycles shorter than the 60 s poll interval, which the
#: sampled activity series misses outright. A decrease means device restart,
#: never negative usage. Pinned here for the same reason as the event types:
#: recorder and query must spell it identically.
CYCLES_TOTAL_METRIC = "cycles_total"


# --------------------------------------------------------------------------
# Activity derivation (reader-side, per STATUS_SPEC §2.3 / §2.3.2)
# --------------------------------------------------------------------------

#: `equipment_status` values whose required `activity` is pinned by the §2.3
#: consistency-invariant table. `degraded` / `error` / `dry_run` / `unknown`
#: are deliberately absent — the table allows either answer there, so the
#: state alone tells us nothing.
_STATE_IMPLIED_ACTIVITY: dict[str, str] = {
    "busy": "running",          # busy ≡ healthy + running (§2.3)
    "ready": "idle",
    "requires_init": "idle",
    "e_stop": "idle",
}

#: Per-kind component sniffs (§2.3.2: reader-local, NON-NORMATIVE, expected
#: to be deleted once the fleet reports `activity` natively). Component key →
#: states meaning "primary operation in progress". Only consulted when the
#: device didn't report `activity` and the state enum doesn't pin it — i.e.
#: today's chronically-degraded shaker, whose motor keeps shaking through the
#: heater RTD fault.
_KIND_RUNNING_COMPONENTS: dict[str, tuple[str, frozenset[str]]] = {
    "shaker": ("motor", frozenset({"running", "shaking"})),
}


def derive_activity(status: EquipmentStatus) -> tuple[str, str]:
    """Resolve a device's activity, best answer first.

    Returns ``(activity, source)`` where activity ∈ {idle, running, unknown}
    and source records how it was determined:

    - ``"device"``     — the device reported ``activity`` itself (v1.2).
    - ``"status"``     — implied by the §2.3 consistency-invariant table
      (busy ⇒ running; ready / requires_init / e_stop ⇒ idle).
    - ``"components"`` — per-kind component sniff (§2.3.2, non-normative).
    - ``"none"``       — genuinely undeterminable → ``"unknown"``.

    Callers that only need the activity can discard the source; the recorder
    stores it in the event payload so non-normative derivations remain
    identifiable (and re-derivable/deletable) after the fleet migrates.
    """
    if status.activity != "unknown":
        return status.activity, "device"

    implied = _STATE_IMPLIED_ACTIVITY.get(status.equipment_status)
    if implied is not None:
        return implied, "status"

    sniff = _KIND_RUNNING_COMPONENTS.get(status.equipment_kind)
    if sniff is not None:
        component_key, running_states = sniff
        component = status.components.get(component_key)
        if component is not None and component.connected:
            state = (component.state or "").lower()
            if state in running_states:
                return "running", "components"
            if state and state != "unknown":
                return "idle", "components"

    return "unknown", "none"


def snapshot_activity(snap) -> tuple[str, str]:
    """Resolve activity for an aggregator ``EquipmentSnapshot`` (best-effort).

    ``snap.fetch_error`` overrides everything — an unreachable device's
    activity cannot be determined, so it reads ``unknown`` (never a stale
    ``running`` extending a pre-outage span). Used by both the recorder
    (``main._uptime_poll_loop``) and the presentation layer, so the stored
    ``activity_transition`` series and the live tiles can never disagree.
    """
    try:
        if snap.fetch_error is not None:
            return "unknown", "none"
        return derive_activity(snap.status)
    except Exception:
        return "unknown", "none"
