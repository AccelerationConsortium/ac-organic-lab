"""Device-alert notifier — pushes device problems to PyPoe's alert webhook.

Piggybacks on the 60 s uptime poll loop in ``main.py``: the loop calls
``observe()`` once per device per sweep and ``flush()`` once at the end of
the sweep; everything else (debounce, cooldown, storm collapse, delivery,
audit) lives here.

Trigger rules
-------------
- **unreachable** — a device must be down for ``sustained_sweeps``
  consecutive sweeps (default 2) before alerting, so a single missed poll
  never pages anyone. Gateway-fronted kinds reporting ``unknown`` count as
  down per STATUS_SPEC §2.1 (the loop folds that into ``reachable``).
- **error / e_stop** — alert immediately on the transition *into* the
  state (device-reported faults are already debounced by the device).
- **recovered** — sent only for devices that previously alerted.
- Devices under maintenance / disabled / mock are suppressed by the
  caller (``observe`` is simply not called for them).

Storm collapse: if ``storm_threshold`` (default 3) or more devices trip in
the same sweep, one collapsed alert is sent (first device + ``devices``
list) instead of N investigations — simultaneous failures usually share a
cause.

Delivery is best-effort by contract (mirrors the xArm events exporter): a
failed POST is logged and dropped, never raised, and never blocks the
poll loop. Each emitted alert writes an ``alert_emitted`` audit row to
``equipment_events``.

Restart safety: the debounce state used to live only in memory, so every
restart of the API re-fired an alert for each device still in a fault state
("a cold start straight into error still alerts") and forgot which devices
were alerting, so their eventual recovery was never sent (2026-10-10
19:51/19:57 and 2026-10-11 05:01/05:39 each duplicated the whole standing
set in Slack). ``seed_from_history()`` now rebuilds ``_active_alert`` and the
cooldown clock from the ``alert_emitted`` audit rows at startup, and
``observe()`` never queues an alert whose event already stands for that
device — only a *different* event (unreachable → error) or a recovery.

Enabled by setting ``PYPOE_ALERT_URL`` to the full webhook endpoint
(e.g. ``http://100.64.254.6:8006/alerts/device``); unset = disabled.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

import httpx

from .events import ALERT_EMITTED

logger = logging.getLogger("alert_notifier")


def _age_seconds(ts: Any, wall_now: float) -> Optional[float]:
    """Seconds between an ISO-8601 audit timestamp and ``wall_now``; None if unparsable."""
    if not isinstance(ts, str):
        return None
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, wall_now - dt.timestamp())
    except ValueError:
        return None

ALERT_STATES = {"error", "e_stop"}
DEFAULT_SUSTAINED_SWEEPS = 2
DEFAULT_COOLDOWN_S = 30 * 60
DEFAULT_STORM_THRESHOLD = 3
_POST_TIMEOUT_S = 5.0


class AlertNotifier:
    def __init__(
        self,
        url: Optional[str] = None,
        *,
        db: Any = None,
        client: Optional[httpx.AsyncClient] = None,
        sustained_sweeps: int = DEFAULT_SUSTAINED_SWEEPS,
        cooldown_s: float = DEFAULT_COOLDOWN_S,
        storm_threshold: int = DEFAULT_STORM_THRESHOLD,
        now: Any = time.monotonic,
    ) -> None:
        self.url = url if url is not None else os.environ.get("PYPOE_ALERT_URL", "")
        self._db = db
        self._client = client
        self._sustained_sweeps = sustained_sweeps
        self._cooldown_s = cooldown_s
        self._storm_threshold = storm_threshold
        self._now = now

        self._down_streak: dict[str, int] = {}
        self._prev_state: dict[str, str] = {}
        #: device_id → event we alerted on (cleared by the recovery alert)
        self._active_alert: dict[str, str] = {}
        self._last_sent_at: dict[str, float] = {}
        #: candidates gathered during the current sweep, flushed at its end
        self._sweep: list[dict[str, Any]] = []

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    # -- called once at startup ------------------------------------------------

    def seed_from_history(self, *, days: int = 7) -> int:
        """Rebuild the standing-alert set from the ``alert_emitted`` audit rows.

        For each device whose most recent audit row is a non-recovery event,
        mark that event as active (so the same fault is not re-alerted after a
        restart but its recovery *is* sent) and back-date the cooldown clock to
        the row's timestamp. A storm row (``payload.devices``) covers every
        device it collapsed. Returns the number of devices seeded. Never
        raises; a DB without the helper (tests, old schema) seeds nothing.
        """
        if not self.enabled or self._db is None:
            return 0
        getter = getattr(self._db, "get_latest_alerts", None)
        if getter is None:
            return 0
        try:
            rows = getter(days=days)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Alert history seed failed: %s", exc)
            return 0
        wall_now = time.time()
        mono_now = self._now()
        seeded = 0
        for row in rows:
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            event = payload.get("event")
            if not event or event == "recovered":
                continue
            devices = [row.get("device_id"), *(payload.get("devices") or [])]
            age_s = _age_seconds(row.get("ts"), wall_now)
            for device_id in devices:
                if not device_id or device_id in self._active_alert:
                    continue
                self._active_alert[device_id] = event
                if age_s is not None:
                    self._last_sent_at[device_id] = mono_now - age_s
                seeded += 1
        if seeded:
            logger.info(
                "Alert notifier seeded %d standing alert(s) from history: %s",
                seeded, ", ".join(f"{d}={e}" for d, e in sorted(self._active_alert.items())),
            )
        return seeded

    # -- called once per device per sweep -----------------------------------

    def observe(
        self,
        device_id: str,
        *,
        reachable: bool,
        state: str,
        message: Optional[str] = None,
        last_error: Optional[dict] = None,
    ) -> None:
        """Record one device observation; queues alert candidates."""
        if not self.enabled:
            return

        prev_state = self._prev_state.get(device_id)
        self._prev_state[device_id] = state

        standing = self._active_alert.get(device_id)

        if not reachable:
            streak = self._down_streak.get(device_id, 0) + 1
            self._down_streak[device_id] = streak
            # An "unreachable" alert that already stands (seeded from history
            # across a restart, or sent earlier this process) is not re-sent.
            if streak == self._sustained_sweeps and standing != "unreachable":
                self._queue(
                    device_id,
                    "unreachable",
                    state=state,
                    message=message or "device unreachable",
                )
            return

        # Reachable.
        self._down_streak[device_id] = 0

        if state in ALERT_STATES:
            event = "e_stop" if state == "e_stop" else "error"
            # Alert on the transition INTO the fault state — a cold start
            # straight into error counts (prev_state None) — unless this very
            # event already stands for the device. A standing *unreachable*
            # alert followed by a device-reported fault is new information.
            if prev_state not in ALERT_STATES and standing != event:
                self._queue(
                    device_id,
                    event,
                    state=state,
                    message=message,
                    last_error=last_error,
                )
            return

        # Healthy and reachable: close whatever alert stands, whether it was
        # raised in this process or seeded from history after a restart.
        if standing is not None:
            self._queue(
                device_id,
                "recovered",
                state=state,
                message=message or f"back to {state}",
            )

    # -- called once at the end of each sweep --------------------------------

    async def flush(self) -> None:
        """Apply cooldown + storm collapse, deliver, audit. Never raises."""
        candidates, self._sweep = self._sweep, []
        if not candidates or not self.enabled:
            return
        try:
            await self._flush(candidates)
        except Exception as exc:  # pragma: no cover - belt and braces
            logger.warning("Alert flush failed: %s", exc)

    async def _flush(self, candidates: list[dict[str, Any]]) -> None:
        now = self._now()
        recoveries = [c for c in candidates if c["event"] == "recovered"]
        downs = []
        for c in candidates:
            if c["event"] == "recovered":
                continue
            last = self._last_sent_at.get(c["device_id"])
            if last is not None and (now - last) < self._cooldown_s:
                logger.info(
                    "Alert for %s (%s) suppressed by cooldown",
                    c["device_id"], c["event"],
                )
                continue
            downs.append(c)

        if len(downs) >= self._storm_threshold:
            first, rest = downs[0], downs[1:]
            payload = {
                "device_id": first["device_id"],
                "event": first["event"],
                "state": first.get("state"),
                "message": (
                    f"{len(downs)} devices tripped in one sweep — probable "
                    f"shared cause (network/gateway/power). "
                    f"{first.get('message') or ''}".strip()
                ),
                "devices": [c["device_id"] for c in rest],
            }
            await self._send(payload)
            for c in downs:
                self._active_alert[c["device_id"]] = c["event"]
                self._last_sent_at[c["device_id"]] = now
        else:
            for c in downs:
                payload = {k: v for k, v in c.items() if v is not None}
                await self._send(payload)
                self._active_alert[c["device_id"]] = c["event"]
                self._last_sent_at[c["device_id"]] = now

        for c in recoveries:
            payload = {k: v for k, v in c.items() if v is not None}
            await self._send(payload)
            self._active_alert.pop(c["device_id"], None)

    # -- internals ------------------------------------------------------------

    def _queue(
        self,
        device_id: str,
        event: str,
        *,
        state: Optional[str] = None,
        message: Optional[str] = None,
        last_error: Optional[dict] = None,
    ) -> None:
        self._sweep.append(
            {
                "device_id": device_id,
                "event": event,
                "state": state,
                "message": message,
                "last_error": last_error,
            }
        )

    async def _send(self, payload: dict[str, Any]) -> None:
        outcome = "sent"
        try:
            if self._client is not None:
                resp = await self._client.post(
                    self.url, json=payload, timeout=_POST_TIMEOUT_S
                )
            else:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        self.url, json=payload, timeout=_POST_TIMEOUT_S
                    )
            outcome = f"http_{resp.status_code}"
            logger.info(
                "Alert %s for %s → %s",
                payload["event"], payload["device_id"], outcome,
            )
        except Exception as exc:
            outcome = f"failed: {exc}"
            logger.warning(
                "Alert %s for %s NOT delivered: %s",
                payload["event"], payload["device_id"], exc,
            )
        self._audit(payload, outcome)

    def _audit(self, payload: dict[str, Any], outcome: str) -> None:
        if self._db is None:
            return
        try:
            self._db.record_equipment_event(
                payload["device_id"],
                ALERT_EMITTED,
                to_state=payload.get("state"),
                message=payload.get("message"),
                payload={
                    "event": payload["event"],
                    "devices": payload.get("devices"),
                    "outcome": outcome,
                    "target": self.url,
                },
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("Alert audit row failed: %s", exc)
