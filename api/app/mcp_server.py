"""MCP server exposing the lab's read-only history + log tools.

Why this exists
---------------
The dashboard's chat-bubble path (assistant.py) needs an Anthropic API key,
which not every lab account has. Claude Code (the CLI) instead uses your
Claude Team / Pro subscription and can connect to MCP servers via stdio --
so by re-exposing the same tools through MCP, you can ask Claude Code "what
happened to the plateloc this morning" without provisioning any API access.

Transport
---------
stdio only. Claude Code spawns this process per session, talks JSON-RPC over
stdin/stdout, and tears it down when the session ends. To register it::

    claude mcp add lab-history -- \\
        uv run --project /home/sdl2/caoyang/ac-organic-lab/api \\
            lab-history-mcp

(The ``lab-history-mcp`` entry point is declared in pyproject.toml; it
points at ``run()`` below.)

Safety
------
All tools are read-only. The journald tool's ``unit`` argument is whitelisted
to dashboard-related services so this server cannot become a side channel
into the host's full systemd journal. Limits on row counts and lookback
hours match the API-bubble assistant in ``app/assistant.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from typing import Any

import httpx

from .db import LabDatabase, resolve_db_path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Caps -- mirror app/assistant.py
# ---------------------------------------------------------------------------

MAX_LIMIT = 200
MAX_SINCE_HOURS = 24 * 7
MAX_JOURNAL_LINES = 200
DASHBOARD_API_URL = os.environ.get("LAB_DASHBOARD_API_URL", "http://127.0.0.1:8001")

ALLOWED_UNITS = frozenset(
    {
        "ac-organic-lab-api.service",
        "ac-organic-lab-web.service",
        "kasa-tapo-services.service",
        "ac-go2rtc.service",
    }
)


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


_DB_HOLDER: dict[str, LabDatabase] = {}


def _db() -> LabDatabase:
    """Lazy-open a single LabDatabase per MCP server process."""

    if "db" not in _DB_HOLDER:
        db = LabDatabase(resolve_db_path())
        db.open()
        _DB_HOLDER["db"] = db
    return _DB_HOLDER["db"]


def _clamp_limit(value: Any, default: int = 50) -> int:
    try:
        n = int(value) if value is not None else default
    except (TypeError, ValueError):
        n = default
    return max(1, min(MAX_LIMIT, n))


def _clamp_since_hours(value: Any, default: float = 24.0) -> float:
    try:
        h = float(value) if value is not None else default
    except (TypeError, ValueError):
        h = default
    return max(0.1, min(float(MAX_SINCE_HOURS), h))


async def _fetch_equipment() -> dict[str, Any]:
    """One aggregator read shared by the live-snapshot tools. Raises on failure."""

    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{DASHBOARD_API_URL}/api/equipment")
        r.raise_for_status()
        return r.json()


async def _list_equipment_now() -> str:
    """Live snapshot from the dashboard's aggregator."""

    try:
        data = await _fetch_equipment()
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"could not reach dashboard API: {exc}"})

    rows = [
        {
            "id": e["id"],
            "name": e["name"],
            "kind": e.get("kind"),
            "equipment_status": (e.get("status") or {}).get("equipment_status"),
            "message": (e.get("status") or {}).get("message"),
            "fetch_error": e.get("fetch_error"),
            "latency_ms": e.get("latency_ms"),
            "fetched_at": e.get("fetched_at"),
        }
        for e in data.get("equipment", [])
    ]
    return json.dumps({"equipment": rows})


async def _get_equipment_status(equipment_id: str) -> str:
    """Full status envelope for one device.

    `list_equipment_now` deliberately flattens every device to one summary row;
    everything below `equipment_status` — components (an OT-2's pipette mounts),
    `details` (deck snapshot, tip racks, loaded plate), metrics, allowed_actions
    — was invisible to the assistant until this tool existed."""

    try:
        data = await _fetch_equipment()
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"could not reach dashboard API: {exc}"})

    for e in data.get("equipment", []):
        if e.get("id") == equipment_id:
            return json.dumps(
                {
                    "id": e["id"],
                    "name": e.get("name"),
                    "kind": e.get("kind"),
                    "fetch_error": e.get("fetch_error"),
                    "fetched_at": e.get("fetched_at"),
                    "status": e.get("status"),
                }
            )
    return json.dumps(
        {
            "error": f"no equipment with id {equipment_id!r}",
            "known_ids": sorted(
                x.get("id", "") for x in data.get("equipment", [])
            ),
        }
    )


async def _query_equipment_events(
    device_id: str | None, limit: int, event_type: str | None = None
) -> str:
    if not device_id:
        return json.dumps({"error": "device_id is required"})
    events = _db().get_equipment_events(
        device_id, limit=_clamp_limit(limit), event_type=event_type
    )
    return json.dumps({"device_id": device_id, "events": events}, default=str)


async def _query_service_uptime(device_id: str, days: int) -> str:
    if not device_id:
        return json.dumps({"error": "device_id is required"})
    days = max(1, min(30, int(days)))
    db = _db()
    return json.dumps(
        {
            "device_id": device_id,
            "days": days,
            "uptime_pct": db.get_uptime_pct(device_id, days=days),
            "events": db.get_uptime_events(device_id, limit=50),
        },
        default=str,
    )


async def _query_sensor_readings(
    sensor_id: str, metric: str, since_hours: float, limit: int
) -> str:
    if not sensor_id or not metric:
        return json.dumps({"error": "sensor_id and metric are both required"})
    readings = _db().get_sensor_readings(
        sensor_id,
        metric,
        since_hours=_clamp_since_hours(since_hours),
        limit=_clamp_limit(limit, default=500),
    )
    return json.dumps(
        {
            "sensor_id": sensor_id,
            "metric": metric,
            "since_hours": since_hours,
            "readings": readings,
        },
        default=str,
    )


async def _query_runs(device_id: str | None, limit: int) -> str:
    runs = _db().get_runs(limit=_clamp_limit(limit), device_id=device_id)
    return json.dumps({"runs": runs}, default=str)


async def _query_well_results(run_id: str) -> str:
    if not run_id:
        return json.dumps({"error": "run_id is required"})
    return json.dumps(
        {"run_id": run_id, "wells": _db().get_well_results(run_id)}, default=str
    )


async def _tail_journald(unit: str, lines: int) -> str:
    if unit not in ALLOWED_UNITS:
        return json.dumps(
            {
                "error": f"unit {unit!r} is not in the whitelist",
                "allowed_units": sorted(ALLOWED_UNITS),
            }
        )
    lines = max(10, min(MAX_JOURNAL_LINES, int(lines)))
    binary = os.environ.get("ASSISTANT_JOURNALCTL") or shutil.which("journalctl")
    if binary is None:
        return json.dumps({"error": "journalctl is not available on this host"})
    proc = await asyncio.create_subprocess_exec(
        binary,
        "-u",
        unit,
        "-n",
        str(lines),
        "--no-pager",
        "--output=short-iso",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=8.0)
    except asyncio.TimeoutError:
        proc.kill()
        return json.dumps({"error": "journalctl timed out after 8s"})
    if proc.returncode != 0:
        return json.dumps(
            {
                "error": f"journalctl exited {proc.returncode}",
                "stderr": stderr.decode("utf-8", errors="replace")[-2000:],
            }
        )
    return json.dumps(
        {"unit": unit, "lines": lines, "log": stdout.decode("utf-8", errors="replace")[-20_000:]}
    )


# ---------------------------------------------------------------------------
# MCP server (FastMCP convenience API)
# ---------------------------------------------------------------------------


def _build_server():
    """Build the MCP server. Imported lazily so the dashboard test suite
    (which doesn't have the mcp package installed in CI) can still import
    sibling modules from this directory without dragging mcp in."""

    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("lab-history")

    @mcp.tool()
    async def list_equipment_now() -> str:
        """Return the live status of every registered lab device (id, kind,
        equipment_status, message, fetch_error, latency_ms). Sourced from the
        dashboard's aggregator at /api/equipment. Use this first when you
        need the canonical equipment_id for other tools, or to answer
        "what's running right now"."""

        return await _list_equipment_now()

    @mcp.tool()
    async def get_equipment_status(equipment_id: str) -> str:
        """Full live status envelope for ONE device: components (e.g. the
        OT-2's pipette mounts), details (deck snapshot, tip racks, loaded
        plate, motion graph), metrics, allowed_actions, activity. Use it when
        asked what hardware a device carries or what state its subsystems are
        in — list_equipment_now only returns one summary row per device."""

        return await _get_equipment_status(equipment_id)

    @mcp.tool()
    async def query_equipment_events(
        device_id: str, limit: int = 50, event_type: str | None = None
    ) -> str:
        """State-change events from the history DB (startup, shutdown,
        error, state_transition) for one device, newest first. Use for
        questions like "has plateloc had errors today".

        Pass event_type="agent_observation" to read back prior findings the
        PyPoe alert investigator journaled about this device — useful for
        "has anything been noted about ot2_hte before"."""

        return await _query_equipment_events(device_id, limit, event_type)

    @mcp.tool()
    async def query_service_uptime(device_id: str, days: int = 7) -> str:
        """Reachability transitions (up/down/recovered) plus the overall
        uptime % over the requested window. Use for "has X been flaky"
        or "when did X last go down"."""

        return await _query_service_uptime(device_id, days)

    @mcp.tool()
    async def query_sensor_readings(
        sensor_id: str,
        metric: str,
        since_hours: float = 24.0,
        limit: int = 500,
    ) -> str:
        """Environmental sensor history (~1/min). Metric examples:
        temperature_c, humidity_pct, co2_ppm."""

        return await _query_sensor_readings(sensor_id, metric, since_hours, limit)

    @mcp.tool()
    async def query_runs(device_id: str | None = None, limit: int = 20) -> str:
        """Recent dosing-run records, newest first. Optionally filter by
        device_id (e.g. dose_every_well)."""

        return await _query_runs(device_id, limit)

    @mcp.tool()
    async def query_well_results(run_id: str) -> str:
        """Per-well dispense results for one dosing run."""

        return await _query_well_results(run_id)

    @mcp.tool()
    async def tail_journald(unit: str, lines: int = 50) -> str:
        """Last N lines of one of the dashboard's systemd units. Whitelisted
        units: ac-organic-lab-api.service, ac-organic-lab-web.service,
        kasa-tapo-services.service, ac-go2rtc.service. Use for "what is the
        API logging" or "why did the gateway crash"."""

        return await _tail_journald(unit, lines)

    return mcp


def run() -> None:
    """Entry point used by `lab-history-mcp` (see pyproject.toml scripts).

    Boots the FastMCP server over stdio and blocks until Claude Code closes
    the stream. All logging goes to stderr; stdout is reserved for MCP
    JSON-RPC framing.
    """

    logging.basicConfig(level=logging.INFO, stream=__import__("sys").stderr)
    server = _build_server()
    # FastMCP.run() picks transport from the argument; default is stdio,
    # which is what Claude Code expects.
    server.run()


if __name__ == "__main__":
    run()


__all__ = ["run", "ALLOWED_UNITS"]
