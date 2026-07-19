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

Enabled by setting ``PYPOE_ALERT_URL`` to the full webhook endpoint
(e.g. ``http://100.64.254.6:8006/alerts/device``); unset = disabled.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger("alert_notifier")

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

        if not reachable:
            streak = self._down_streak.get(device_id, 0) + 1
            self._down_streak[device_id] = streak
            if streak == self._sustained_sweeps:
                self._queue(
                    device_id,
                    "unreachable",
                    state=state,
                    message=message or "device unreachable",
                )
            return

        # Reachable again.
        if self._down_streak.get(device_id, 0) > 0:
            self._down_streak[device_id] = 0
            if device_id in self._active_alert:
                self._queue(
                    device_id,
                    "recovered",
                    state=state,
                    message=message or f"back to {state}",
                )
                return

        # Fault-state edge (only on the transition INTO the state; a cold
        # start straight into error still alerts — prev_state None counts).
        if state in ALERT_STATES and prev_state not in ALERT_STATES:
            self._queue(
                device_id,
                "e_stop" if state == "e_stop" else "error",
                state=state,
                message=message,
                last_error=last_error,
            )
        elif state not in ALERT_STATES and prev_state in ALERT_STATES:
            if device_id in self._active_alert:
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
                "alert_emitted",
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
