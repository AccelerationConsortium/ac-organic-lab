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
All tools are read-only except ``record_observation``, which appends one
actor-stamped ``agent_observation`` row to the history DB (via the api's
ingest endpoint — nothing here can touch hardware, and the write fails
closed without a verified ``LAB_ACTOR``). The journald tool's ``unit``
argument is whitelisted
to dashboard-related services so this server cannot become a side channel
into the host's full systemd journal. Limits on row counts and lookback
hours match the API-bubble assistant in ``app/assistant.py``.

``LAB_HISTORY_TOOLS`` (env, optional) is a comma-separated include-list of
tool names: when set, only those tools are registered; when unset, all are.
This is the server-side knob every client shares (the claude CLI's
``--allowedTools`` filters only its own calls; the openai backend's tool
loop has no client-side filter at all). The dashboard assistant sets it to
exclude the dosing-run data tools — see ``assistant.HISTORY_TOOLS``. An
unknown name fails the server at startup rather than silently vanishing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import shutil
from datetime import datetime, timezone
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
MAX_OBSERVATION_CHARS = 1000
DASHBOARD_API_URL = os.environ.get("LAB_DASHBOARD_API_URL", "http://127.0.0.1:8001")

# Camera snapshots (2026-09-04). A still is read from go2rtc's live stream —
# ``GET /api/frame.jpeg?src=<camera>_<lens>`` — never from the gateway's
# ``/control/snapshot``: this server must not reach any ``/control/*`` path
# (mcp/servers.yaml), and go2rtc is already relaying that stream to every
# dashboard viewer, so grabbing one frame of it moves nothing and asks the
# camera for nothing. Frames are saved under LAB_SNAPSHOT_DIR (the assistant
# runtime dir's ``snapshots/``) and served back to the browser by
# ``GET /api/assistant/snapshots/<name>``; the ``_file`` key in the result is
# for the assistant backend, which attaches the picture to the model's
# context when the model can see images.
GO2RTC_URL = os.environ.get("LAB_GO2RTC_URL", "http://127.0.0.1:1984").rstrip("/")
SNAPSHOT_DIR = os.environ.get("LAB_SNAPSHOT_DIR") or str(
    pathlib.Path(os.environ.get("ASSISTANT_RUNTIME_DIR", str(pathlib.Path.home() / ".cache" / "lab-assistant")))
    / "snapshots"
)
SNAPSHOT_TTL_S = 24 * 3600
SNAPSHOT_MIN_BYTES = 1_000
SNAPSHOT_FETCH_TIMEOUT_S = 12.0

ALLOWED_UNITS = frozenset(
    {
        "ac-organic-lab-api.service",
        "ac-organic-lab-web.service",
        "kasa-tapo-services.service",
        "ac-go2rtc.service",
    }
)

# Every tool this server can register. _build_server() asserts its decorated
# set equals this, so the include-list validation below can't drift from the
# real registrations.
ALL_TOOLS = frozenset(
    {
        "list_equipment_now",
        "get_equipment_status",
        "record_observation",
        "query_equipment_events",
        "query_service_uptime",
        "query_sensor_readings",
        "query_runs",
        "query_well_results",
        "tail_journald",
        "capture_camera_snapshot",
    }
)


def _included_tools() -> frozenset[str] | None:
    """Parse the ``LAB_HISTORY_TOOLS`` include-list; ``None`` means no filter.

    Fail-fast on unknown or empty lists: a typo'd tool name must kill the
    server at startup, not silently narrow the toolset to something other
    than what the deployment intended.
    """

    raw = os.environ.get("LAB_HISTORY_TOOLS", "").strip()
    if not raw:
        return None
    names = frozenset(n.strip() for n in raw.split(",") if n.strip())
    if not names:
        raise ValueError("LAB_HISTORY_TOOLS is set but names no tools")
    unknown = names - ALL_TOOLS
    if unknown:
        raise ValueError(
            f"LAB_HISTORY_TOOLS names unknown tools {sorted(unknown)}; "
            f"known tools: {sorted(ALL_TOOLS)}"
        )
    return names


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


async def _record_observation(equipment_id: str, observation: str) -> str:
    """Append one operational observation to the shared journal.

    The learning loop the Phase 4 policy permits (HERMES_ACCESS_DESIGN §4):
    device-scoped, actor-stamped, appended through the same ``/api/ingest``
    path devices and PyPoe use — so decision #9's single-writer rule holds and
    ``query_equipment_events(event_type="agent_observation")`` reads it back
    next session. Deliberately NOT a memory store: rows are lab-public,
    reviewable, and anchored to a device, never to a conversation.

    Fails closed without a verified operator (``LAB_ACTOR``): an
    unattributable journal row would be an audit hole, not a note.
    """

    actor = os.environ.get("LAB_ACTOR", "").strip()
    if not actor:
        return json.dumps(
            {
                "error": "observation journaling requires a verified operator "
                "session; tell the user to sign in — do not retry"
            }
        )

    observation = (observation or "").strip()
    if not observation:
        return json.dumps({"error": "observation is empty"})
    if len(observation) > MAX_OBSERVATION_CHARS:
        return json.dumps(
            {
                "error": f"observation is {len(observation)} chars; the journal "
                f"caps notes at {MAX_OBSERVATION_CHARS} — compress it (notes are "
                "for the finding, not the transcript)"
            }
        )

    try:
        data = await _fetch_equipment()
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"could not reach dashboard API: {exc}"})
    known = {e.get("id") for e in data.get("equipment", [])}
    if equipment_id not in known:
        return json.dumps(
            {
                "error": f"no equipment with id {equipment_id!r}",
                "known_ids": sorted(known),
            }
        )

    body = {
        "device_id": equipment_id,
        "records": [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "agent_observation",
                "message": observation,
                "extra": {"actor": actor, "origin": "dashboard-assistant"},
            }
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{DASHBOARD_API_URL}/api/ingest/events", json=body)
            r.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"could not write observation: {exc}"})
    return json.dumps({"recorded": True, "device_id": equipment_id, "actor": actor})


def _prune_snapshots(directory: "pathlib.Path", *, now: float) -> None:
    """Drop frames older than SNAPSHOT_TTL_S. Best-effort housekeeping on
    every capture; a failure to delete is never a failure to capture."""
    try:
        for f in directory.glob("*.jpg"):
            try:
                if now - f.stat().st_mtime > SNAPSHOT_TTL_S:
                    f.unlink()
            except OSError:
                continue
    except OSError:
        pass


async def _capture_camera_snapshot(camera_id: str, lens: str | None = None) -> str:
    """One JPEG frame from a lab camera's live stream, saved for the chat.

    Read-only by construction: the frame comes from go2rtc, which is already
    relaying the stream to the dashboard; the camera is not commanded and the
    gateway's ``/control/*`` surface is never touched. Refuses (instead of
    silently returning a stale or black frame) when the camera reports privacy
    mode on or streaming disabled, because the stream is then not the room.
    """
    import pathlib as _pathlib
    import secrets
    import time
    from datetime import datetime, timezone

    try:
        data = await _fetch_equipment()
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"could not reach dashboard API: {exc}"})

    cameras = [e for e in data.get("equipment", []) if e.get("kind") == "camera"]
    entry = next((e for e in cameras if e.get("id") == camera_id), None)
    if entry is None:
        return json.dumps(
            {
                "error": f"no camera with id {camera_id!r}",
                "cameras": [{"id": e.get("id"), "name": e.get("name")} for e in cameras],
            }
        )
    status = entry.get("status") or {}
    details = status.get("details") or {}
    lenses = [
        item.get("id")
        for item in (details.get("lenses") or [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    if lens is None:
        lens = "wide" if "wide" in lenses else (lenses[0] if lenses else None)
    if lens is None:
        return json.dumps({"error": f"camera {camera_id!r} reports no lenses; cannot pick a stream"})
    if lenses and lens not in lenses:
        return json.dumps({"error": f"camera {camera_id!r} has no lens {lens!r}", "lenses": lenses})
    if entry.get("fetch_error") or status.get("equipment_status") == "unknown":
        return json.dumps(
            {
                "error": f"camera {camera_id!r} is unreachable right now; no live frame to capture",
                "fetch_error": entry.get("fetch_error"),
            }
        )
    if details.get("privacy_mode") is True:
        return json.dumps(
            {
                "error": f"camera {camera_id!r} has privacy mode ON; its stream is off and no frame can be captured",
                "code": "privacy_mode_on",
            }
        )
    if details.get("streaming_enabled") is False:
        return json.dumps(
            {
                "error": f"camera {camera_id!r} has streaming disabled; no frame can be captured",
                "code": "streaming_disabled",
            }
        )

    src = f"{camera_id}_{lens}"
    frame: bytes | None = None
    last_error = "no response"
    async with httpx.AsyncClient(timeout=SNAPSHOT_FETCH_TIMEOUT_S) as client:
        # go2rtc needs a keyframe from the producer; the first request after a
        # quiet spell can come back empty, the second almost never does.
        for _attempt in range(2):
            try:
                r = await client.get(f"{GO2RTC_URL}/api/frame.jpeg", params={"src": src})
            except httpx.HTTPError as exc:
                last_error = f"go2rtc unreachable: {exc}"
                continue
            ctype = r.headers.get("content-type", "")
            if r.status_code == 200 and ctype.startswith("image/jpeg") and len(r.content) >= SNAPSHOT_MIN_BYTES:
                frame = r.content
                break
            last_error = f"go2rtc returned HTTP {r.status_code} {ctype or 'no content-type'} ({len(r.content)} bytes)"
    if frame is None:
        return json.dumps(
            {
                "error": (
                    f"no live frame available for {camera_id}/{lens}: {last_error}. "
                    "The stream may be idle; opening the camera tile on the dashboard "
                    "starts it, then retry."
                )
            }
        )

    directory = _pathlib.Path(SNAPSHOT_DIR)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        taken = datetime.now(timezone.utc)
        name = f"{camera_id}_{lens}_{taken.strftime('%Y%m%dT%H%M%SZ')}_{secrets.token_hex(3)}.jpg"
        path = directory / name
        path.write_bytes(frame)
    except OSError as exc:
        return json.dumps({"error": f"captured the frame but could not save it: {exc}"})
    _prune_snapshots(directory, now=time.time())

    return json.dumps(
        {
            "snapshot": {
                "camera_id": camera_id,
                "camera_name": entry.get("name"),
                "lens": lens,
                "taken_at": taken.isoformat(),
                "bytes": len(frame),
                "image_url": f"/api/assistant/snapshots/{name}",
                "_file": str(path),
            },
            "note": (
                "The picture is shown to the operator in the chat. If you can see "
                "images it is also attached to this conversation as an image — "
                "describe only what is actually visible, and say which camera, lens "
                "and time it is from. This did not move the camera; aiming (PTZ, "
                "presets) is a Control-mode proposal."
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

    include = _included_tools()
    defined: set[str] = set()

    def tool(fn):
        """Register ``fn`` as an MCP tool unless the include-list drops it."""

        defined.add(fn.__name__)
        if include is None or fn.__name__ in include:
            mcp.tool()(fn)
        return fn

    @tool
    async def list_equipment_now() -> str:
        """Return the live status of every registered lab device (id, kind,
        equipment_status, message, fetch_error, latency_ms). Sourced from the
        dashboard's aggregator at /api/equipment. Use this first when you
        need the canonical equipment_id for other tools, or to answer
        "what's running right now"."""

        return await _list_equipment_now()

    @tool
    async def get_equipment_status(equipment_id: str) -> str:
        """Full live status envelope for ONE device: components (e.g. the
        OT-2's pipette mounts), details (deck snapshot, tip racks, loaded
        plate, motion graph), metrics, allowed_actions, activity. Use it when
        asked what hardware a device carries or what state its subsystems are
        in — list_equipment_now only returns one summary row per device."""

        return await _get_equipment_status(equipment_id)

    @tool
    async def capture_camera_snapshot(camera_id: str, lens: str | None = None) -> str:
        """Capture ONE still image from a lab camera's live stream and show it
        to the operator in the chat (it is also attached to your context as an
        image when the model can see images — describe what is actually
        visible, and name the camera, lens and time). camera_id is an
        equipment id of kind "camera" from list_equipment_now; lens is one of
        that camera's details.lenses ids (default: "wide", else the first
        lens). Read-only: the frame is read from the stream the dashboard is
        already relaying, the camera is not commanded, and nothing moves — to
        aim a camera (PTZ, presets) propose it in Control mode instead.
        Refuses when the camera is unreachable, in privacy mode, or has
        streaming disabled."""

        return await _capture_camera_snapshot(camera_id, lens)

    @tool
    async def record_observation(equipment_id: str, observation: str) -> str:
        """Append ONE operational observation about a device to the lab's
        shared journal (readable next session via
        query_equipment_events(event_type="agent_observation")).

        Use ONLY for platform knowledge: device behavior, recurring faults,
        quirks, timing, recovery steps that worked. NEVER for scientific or
        project content (compounds, designs, results, goals) and never for
        routine conversation. Journal when the operator asks you to note
        something, or when you have verified a device-level finding a future
        investigation should not have to rediscover. Notes are permanent,
        lab-public, and stamped with the operator's identity."""

        return await _record_observation(equipment_id, observation)

    @tool
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

    @tool
    async def query_service_uptime(device_id: str, days: int = 7) -> str:
        """Reachability transitions (up/down/recovered) plus the overall
        uptime % over the requested window. Use for "has X been flaky"
        or "when did X last go down"."""

        return await _query_service_uptime(device_id, days)

    @tool
    async def query_sensor_readings(
        sensor_id: str,
        metric: str,
        since_hours: float = 24.0,
        limit: int = 500,
    ) -> str:
        """Environmental sensor history (~1/min). Metric examples:
        temperature_c, humidity_pct, co2_ppm."""

        return await _query_sensor_readings(sensor_id, metric, since_hours, limit)

    @tool
    async def query_runs(device_id: str | None = None, limit: int = 20) -> str:
        """Recent dosing-run records, newest first. Optionally filter by
        device_id (e.g. dose_every_well)."""

        return await _query_runs(device_id, limit)

    @tool
    async def query_well_results(run_id: str) -> str:
        """Per-well dispense results for one dosing run."""

        return await _query_well_results(run_id)

    @tool
    async def tail_journald(unit: str, lines: int = 50) -> str:
        """Last N lines of one of the dashboard's systemd units. Whitelisted
        units: ac-organic-lab-api.service, ac-organic-lab-web.service,
        kasa-tapo-services.service, ac-go2rtc.service. Use for "what is the
        API logging" or "why did the gateway crash"."""

        return await _tail_journald(unit, lines)

    if defined != ALL_TOOLS:
        raise RuntimeError(
            "mcp_server drift: decorated tools "
            f"{sorted(defined)} != ALL_TOOLS {sorted(ALL_TOOLS)} — "
            "update ALL_TOOLS when adding/removing a tool"
        )

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
