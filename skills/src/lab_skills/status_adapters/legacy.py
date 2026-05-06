"""Per-device translators for pre-migration equipment APIs.

Each adapter understands one device's current quirks and emits the unified
`EquipmentStatus` envelope. These are temporary - they are deleted (or
swapped for `HttpStatusAdapter`) once the corresponding equipment repo
migrates to the spec.

The current four translators handle:

  * `dose_every_well`            - HTTP 400 before POST /startup is mapped to `requires_init`.
  * `filter_every_well`          - `equipment_status: "dry-run"` body field is mapped to `dry_run`.
  * `fume_hood_actuator`         - actuator-only (port 5000); sensor migrated separately.
  * `xarm_translocation`         - read-only; never auto-calls POST /connect; disconnected -> requires_init.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from ..models import (
    ComponentStatus,
    EquipmentState,
    EquipmentStatus,
    MetricValue,
)
from .base import AdapterResult, EquipmentAdapter, get_json, now_utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


# ---------------------------------------------------------------------------
# dose_every_well
# ---------------------------------------------------------------------------


class LegacyDoseEveryWellAdapter(EquipmentAdapter):
    """Adapter for `dose_every_well` before its spec migration.

    Quirk: `GET /status` raises `RuntimeError` when hardware has not been
    initialised yet, which the FastAPI handler translates to HTTP 400. We map
    that to `requires_init` rather than treating it as an error.
    """

    async def fetch(self, client: httpx.AsyncClient) -> AdapterResult:
        if not self.entry.base_url:
            return self.fail("No base_url configured", kind="unconfigured")

        base = self.entry.base_url.rstrip("/")
        status_url = base + (self.entry.status_path or "/status")
        try:
            http_status, body, elapsed_ms = await get_json(
                client, status_url, timeout=self.entry.poll_timeout_seconds
            )
        except httpx.TimeoutException:
            return self.fail(f"Timeout calling {status_url}", kind="timeout")
        except httpx.ConnectError as exc:
            return self.fail(f"Cannot connect to {status_url}: {exc}", kind="connection_refused")
        except httpx.HTTPError as exc:
            return self.fail(f"HTTP error: {exc}", kind="unknown")

        if http_status == 400 and isinstance(body, dict) and "not initialized" in str(
            body.get("error", "")
        ).lower():
            envelope = EquipmentStatus(
                equipment_id=self.entry.id,
                equipment_name=self.entry.name,
                equipment_kind=self.entry.kind,
                equipment_status="requires_init",
                message="Awaiting POST /startup",
                required_actions=["startup"],
                device_time=now_utc(),
                components={
                    "gantry": ComponentStatus(connected=False, state="disconnected"),
                    "solid_doser": ComponentStatus(connected=False, state="disconnected"),
                    "balance": ComponentStatus(connected=False, state="disconnected"),
                },
            )
            return AdapterResult(envelope, now_utc(), elapsed_ms, None)

        if http_status >= 500:
            return self.fail(
                f"{status_url} returned HTTP {http_status}",
                kind="http_5xx",
                http_status=http_status,
                elapsed_ms=elapsed_ms,
            )
        if http_status >= 400:
            return self.fail(
                f"{status_url} returned HTTP {http_status}",
                kind="http_4xx",
                http_status=http_status,
                elapsed_ms=elapsed_ms,
            )
        if not isinstance(body, dict):
            return self.fail(
                f"{status_url} did not return a JSON object",
                kind="parse_error",
                elapsed_ms=elapsed_ms,
            )

        plate_loaded = bool(body.get("plate_loaded", False))
        gantry_connected = bool(body.get("gantry_connected", False))
        doser_connected = bool(body.get("solid_doser_connected", False))
        flow_rate = body.get("flow_rate_mg_per_s")

        components: dict[str, ComponentStatus] = {
            "gantry": ComponentStatus(
                connected=gantry_connected,
                state="ready" if gantry_connected else "disconnected",
            ),
            "solid_doser": ComponentStatus(
                connected=doser_connected,
                state="ready" if doser_connected else "disconnected",
            ),
            "balance": ComponentStatus(
                connected=bool(body.get("plate_weigher", {}).get("balance_connected", False)),
                state="ready"
                if body.get("plate_weigher", {}).get("balance_connected", False)
                else "disconnected",
            ),
            "plate": ComponentStatus(
                connected=plate_loaded,
                state="loaded" if plate_loaded else "absent",
            ),
        }

        metrics: dict[str, MetricValue] = {}
        if flow_rate is not None:
            metrics["flow_rate"] = MetricValue(value=float(flow_rate), unit="mg/s")

        connected_count = sum(1 for c in components.values() if c.connected)
        if connected_count == 0:
            equipment_status: EquipmentState = "requires_init"
            message = "Hardware not connected"
            required_actions = ["startup"]
        elif gantry_connected and doser_connected:
            equipment_status = "ready"
            message = "Idle"
            required_actions = []
        else:
            equipment_status = "degraded"
            message = "One or more sub-components disconnected"
            required_actions = []

        envelope = EquipmentStatus(
            equipment_id=self.entry.id,
            equipment_name=self.entry.name,
            equipment_kind=self.entry.kind,
            equipment_status=equipment_status,
            message=message,
            required_actions=required_actions,
            device_time=now_utc(),
            components=components,
            metrics=metrics,
            details={k: v for k, v in body.items() if k not in {
                "plate_loaded",
                "gantry_connected",
                "solid_doser_connected",
                "flow_rate_mg_per_s",
            }},
        )
        return AdapterResult(envelope, now_utc(), elapsed_ms, None)


# ---------------------------------------------------------------------------
# filter_every_well
# ---------------------------------------------------------------------------


_FILTER_STATE_MAP: dict[str, EquipmentState] = {
    "ok": "ready",
    "ready": "ready",
    "success": "ready",
    "stopped": "requires_init",
    "dry-run": "dry_run",
    "dry_run": "dry_run",
}


class LegacyFilterEveryWellAdapter(EquipmentAdapter):
    """Adapter for `filter-every-well` before its spec migration."""

    async def fetch(self, client: httpx.AsyncClient) -> AdapterResult:
        if not self.entry.base_url:
            return self.fail("No base_url configured", kind="unconfigured")

        url = self.entry.base_url.rstrip("/") + (self.entry.status_path or "/status")
        try:
            http_status, body, elapsed_ms = await get_json(
                client, url, timeout=self.entry.poll_timeout_seconds
            )
        except httpx.TimeoutException:
            return self.fail(f"Timeout calling {url}", kind="timeout")
        except httpx.ConnectError as exc:
            return self.fail(f"Cannot connect to {url}: {exc}", kind="connection_refused")
        except httpx.HTTPError as exc:
            return self.fail(f"HTTP error: {exc}", kind="unknown")

        if http_status >= 500:
            return self.fail(
                f"HTTP {http_status}",
                kind="http_5xx",
                http_status=http_status,
                elapsed_ms=elapsed_ms,
            )
        if http_status >= 400:
            return self.fail(
                f"HTTP {http_status}",
                kind="http_4xx",
                http_status=http_status,
                elapsed_ms=elapsed_ms,
            )
        if not isinstance(body, dict):
            return self.fail("Non-JSON body", kind="parse_error", elapsed_ms=elapsed_ms)

        raw_status = str(body.get("equipment_status", "unknown"))
        equipment_status: EquipmentState = _FILTER_STATE_MAP.get(raw_status, "unknown")
        if body.get("system_state") == "active" and equipment_status == "ready":
            equipment_status = "busy"

        components: dict[str, ComponentStatus] = {}
        if (press := body.get("press_state")) is not None:
            components["press_valve"] = ComponentStatus(
                connected=press != "unknown",
                state=str(press),
            )
        if (plate := body.get("plate_state")) is not None:
            components["plate"] = ComponentStatus(
                connected=plate == "in",
                state=str(plate),
            )

        envelope = EquipmentStatus(
            equipment_id=self.entry.id,
            equipment_name=self.entry.name,
            equipment_kind=self.entry.kind,
            equipment_status=equipment_status,
            message=_safe_str(body.get("message")),
            device_time=now_utc(),
            components=components,
            details={
                k: v
                for k, v in body.items()
                if k
                not in {
                    "equipment_status",
                    "press_state",
                    "plate_state",
                    "message",
                    "equipment_ip",
                    "equipment_tailscale",
                }
            },
        )
        return AdapterResult(envelope, now_utc(), elapsed_ms, None)


# ---------------------------------------------------------------------------
# fume_hood_actuator
# ---------------------------------------------------------------------------


_FUME_HOOD_STATE_MAP: dict[str, EquipmentState] = {
    "ready": "ready",
    "moving": "busy",
    "stopped": "requires_init",
}


class LegacyFumeHoodActuatorAdapter(EquipmentAdapter):
    """Adapter for the fume-hood actuator service (port 5000) before migration."""

    async def fetch(self, client: httpx.AsyncClient) -> AdapterResult:
        if not self.entry.base_url:
            return self.fail("No base_url configured", kind="unconfigured")

        url = self.entry.base_url.rstrip("/") + (self.entry.status_path or "/equipment/status")
        try:
            http_status, body, elapsed_ms = await get_json(
                client, url, timeout=self.entry.poll_timeout_seconds
            )
        except httpx.TimeoutException:
            return self.fail(f"Timeout calling {url}", kind="timeout")
        except httpx.ConnectError as exc:
            return self.fail(f"Cannot connect to {url}: {exc}", kind="connection_refused")
        except httpx.HTTPError as exc:
            return self.fail(f"HTTP error: {exc}", kind="unknown")

        if http_status >= 500:
            return self.fail(
                f"HTTP {http_status}",
                kind="http_5xx",
                http_status=http_status,
                elapsed_ms=elapsed_ms,
            )
        if http_status >= 400:
            return self.fail(
                f"HTTP {http_status}",
                kind="http_4xx",
                http_status=http_status,
                elapsed_ms=elapsed_ms,
            )
        if not isinstance(body, dict):
            return self.fail("Non-JSON body", kind="parse_error", elapsed_ms=elapsed_ms)

        raw_status = str(body.get("equipment_status", "unknown"))
        equipment_status = _FUME_HOOD_STATE_MAP.get(raw_status, "unknown")

        sash_state = str(body.get("sash_state", "unknown"))
        components = {
            "actuator": ComponentStatus(
                connected=sash_state != "unknown",
                state=sash_state,
            ),
        }

        metrics: dict[str, MetricValue] = {}
        if (sash := body.get("sash_position")) is not None:
            metrics["sash_position"] = MetricValue(value=int(sash), unit="preset")
        if (target := body.get("target_position")) is not None:
            metrics["target_position"] = MetricValue(value=int(target), unit="preset")

        envelope = EquipmentStatus(
            equipment_id=self.entry.id,
            equipment_name=self.entry.name,
            equipment_kind=self.entry.kind,
            equipment_status=equipment_status,
            message=_safe_str(body.get("message")),
            device_time=now_utc(),
            components=components,
            metrics=metrics,
            details={
                k: v
                for k, v in body.items()
                if k
                not in {
                    "equipment_status",
                    "sash_state",
                    "sash_position",
                    "target_position",
                    "message",
                    "equipment_ip",
                    "equipment_tailscale",
                    "is_moving",
                }
            },
        )
        return AdapterResult(envelope, now_utc(), elapsed_ms, None)


# ---------------------------------------------------------------------------
# xarm_translocation
# ---------------------------------------------------------------------------


class LegacyXArmAdapter(EquipmentAdapter):
    """**Deprecated.** Adapter for `xarm-translocation` before its migration to STATUS_SPEC v1.0.

    The `xarm-translocation` repo now conforms to STATUS_SPEC v1.0 (see
    `xarm-translocation/src/core/models.py` and the v1.0 conformance note
    in its README). `equipment.yaml` registers it as ``adapter: http`` and
    the factory routes through ``HttpStatusAdapter`` directly. This class
    is retained for one release cycle as a rollback path -- it will be
    deleted in a follow-up PR once the dashboard verifies green for 24h.

    Read-only: must NEVER call POST /connect, even when the controller is
    disconnected. Maps `connection_state: "disconnected"` to `requires_init`
    with `required_actions: ["connect"]`.
    """

    async def fetch(self, client: httpx.AsyncClient) -> AdapterResult:
        if not self.entry.base_url:
            return self.fail("No base_url configured", kind="unconfigured")

        url = self.entry.base_url.rstrip("/") + (self.entry.status_path or "/status")
        try:
            http_status, body, elapsed_ms = await get_json(
                client, url, timeout=self.entry.poll_timeout_seconds
            )
        except httpx.TimeoutException:
            return self.fail(f"Timeout calling {url}", kind="timeout")
        except httpx.ConnectError as exc:
            return self.fail(f"Cannot connect to {url}: {exc}", kind="connection_refused")
        except httpx.HTTPError as exc:
            return self.fail(f"HTTP error: {exc}", kind="unknown")

        if http_status >= 500:
            return self.fail(
                f"HTTP {http_status}",
                kind="http_5xx",
                http_status=http_status,
                elapsed_ms=elapsed_ms,
            )
        if http_status >= 400:
            return self.fail(
                f"HTTP {http_status}",
                kind="http_4xx",
                http_status=http_status,
                elapsed_ms=elapsed_ms,
            )
        if not isinstance(body, dict):
            return self.fail("Non-JSON body", kind="parse_error", elapsed_ms=elapsed_ms)

        connection_state = str(body.get("connection_state", "unknown"))
        is_alive = bool(body.get("is_alive", False))
        last_error = body.get("last_error")

        if connection_state != "connected":
            equipment_status: EquipmentState = "requires_init"
            message = f"Controller {connection_state}"
            required_actions = ["connect"]
        elif last_error:
            equipment_status = "error"
            message = f"Controller reports error: {last_error}"
            required_actions = ["clear_errors"]
        elif is_alive:
            equipment_status = "ready"
            message = "Idle"
            required_actions = []
        else:
            equipment_status = "degraded"
            message = "Controller connected but not alive"
            required_actions = []

        components = {
            "arm": ComponentStatus(
                connected=connection_state == "connected",
                state=str(body.get("arm_state", "unknown")),
            ),
            "gripper": ComponentStatus(
                connected=connection_state == "connected",
                state=str(body.get("gripper_state", "unknown")),
            ),
            "track": ComponentStatus(
                connected=body.get("track_position") is not None,
                state=str(body.get("track_state", "unknown")),
            ),
        }

        details: dict[str, Any] = {
            "current_position": body.get("current_position"),
            "current_joints": body.get("current_joints"),
            "track_position": body.get("track_position"),
            "connection_details": body.get("connection_details"),
        }

        envelope = EquipmentStatus(
            equipment_id=self.entry.id,
            equipment_name=self.entry.name,
            equipment_kind=self.entry.kind,
            equipment_status=equipment_status,
            message=message,
            required_actions=required_actions,
            device_time=now_utc(),
            components=components,
            details=details,
        )
        return AdapterResult(envelope, now_utc(), elapsed_ms, None)
