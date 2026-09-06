"""Tests for the lab-history MCP server's live-snapshot helpers.

The FastMCP wrappers delegate to plain async fns, so these exercise the
logic without the ``mcp`` package or a stdio transport.
"""

from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from app.mcp_server import (
    ALL_TOOLS,
    DASHBOARD_API_URL,
    MAX_OBSERVATION_CHARS,
    _get_equipment_status,
    _included_tools,
    _list_equipment_now,
    _record_observation,
)

_EQUIPMENT = {
    "equipment": [
        {
            "id": "ot2_hte",
            "name": "OT-2 (HTE)",
            "kind": "liquid_handler",
            "fetch_error": None,
            "latency_ms": 12,
            "fetched_at": "2026-08-13T18:00:00Z",
            "status": {
                "equipment_status": "ready",
                "message": "Idle",
                "components": {
                    "pipette_left": {"connected": True, "state": "p300_single_gen2"},
                    "pipette_right": {"connected": False, "state": "empty"},
                },
                "details": {"tip_racks": {"3": "opentrons_96_tiprack_300ul"}},
            },
        },
        {"id": "plateloc", "name": "PlateLoc", "kind": "plate_sealer", "status": {}},
    ]
}


@respx.mock
async def test_get_equipment_status_returns_full_envelope():
    respx.get(f"{DASHBOARD_API_URL}/api/equipment").mock(
        return_value=Response(200, json=_EQUIPMENT)
    )
    out = json.loads(await _get_equipment_status("ot2_hte"))
    assert out["id"] == "ot2_hte"
    # The whole point: sub-status depth that list_equipment_now flattens away.
    assert out["status"]["components"]["pipette_left"]["state"] == "p300_single_gen2"
    assert out["status"]["details"]["tip_racks"]["3"] == "opentrons_96_tiprack_300ul"


@respx.mock
async def test_get_equipment_status_unknown_id_lists_known_ids():
    respx.get(f"{DASHBOARD_API_URL}/api/equipment").mock(
        return_value=Response(200, json=_EQUIPMENT)
    )
    out = json.loads(await _get_equipment_status("ot2_htee"))
    assert "no equipment with id" in out["error"]
    assert out["known_ids"] == ["ot2_hte", "plateloc"]


@respx.mock
async def test_get_equipment_status_unreachable_api_is_an_error_payload():
    respx.get(f"{DASHBOARD_API_URL}/api/equipment").mock(
        return_value=Response(503)
    )
    out = json.loads(await _get_equipment_status("ot2_hte"))
    assert "could not reach dashboard API" in out["error"]


@respx.mock
async def test_list_equipment_now_still_flattens():
    respx.get(f"{DASHBOARD_API_URL}/api/equipment").mock(
        return_value=Response(200, json=_EQUIPMENT)
    )
    rows = json.loads(await _list_equipment_now())["equipment"]
    assert [r["id"] for r in rows] == ["ot2_hte", "plateloc"]
    assert "components" not in json.dumps(rows)


# ---------------------------------------------------------------------------
# record_observation
# ---------------------------------------------------------------------------


@respx.mock
async def test_record_observation_writes_actor_stamped_row(monkeypatch):
    monkeypatch.setenv("LAB_ACTOR", "alice@example.edu")
    respx.get(f"{DASHBOARD_API_URL}/api/equipment").mock(
        return_value=Response(200, json=_EQUIPMENT)
    )
    ingest = respx.post(f"{DASHBOARD_API_URL}/api/ingest/events").mock(
        return_value=Response(204)
    )
    out = json.loads(
        await _record_observation("ot2_hte", "  left mount readback lags ~2s after home  ")
    )
    assert out == {
        "recorded": True,
        "device_id": "ot2_hte",
        "actor": "alice@example.edu",
    }
    sent = json.loads(ingest.calls.last.request.content)
    assert sent["device_id"] == "ot2_hte"
    (rec,) = sent["records"]
    assert rec["event"] == "agent_observation"
    assert rec["message"] == "left mount readback lags ~2s after home"
    assert rec["extra"] == {"actor": "alice@example.edu", "origin": "dashboard-assistant"}


async def test_record_observation_fails_closed_without_actor(monkeypatch):
    monkeypatch.delenv("LAB_ACTOR", raising=False)
    out = json.loads(await _record_observation("ot2_hte", "a note"))
    assert "verified operator" in out["error"]


@respx.mock
async def test_record_observation_unknown_id_never_reaches_ingest(monkeypatch):
    monkeypatch.setenv("LAB_ACTOR", "alice@example.edu")
    respx.get(f"{DASHBOARD_API_URL}/api/equipment").mock(
        return_value=Response(200, json=_EQUIPMENT)
    )
    ingest = respx.post(f"{DASHBOARD_API_URL}/api/ingest/events").mock(
        return_value=Response(204)
    )
    out = json.loads(await _record_observation("ot2_htee", "a note"))
    assert out["known_ids"] == ["ot2_hte", "plateloc"]
    assert not ingest.called


async def test_record_observation_refuses_oversized_note(monkeypatch):
    monkeypatch.setenv("LAB_ACTOR", "alice@example.edu")
    out = json.loads(await _record_observation("ot2_hte", "x" * (MAX_OBSERVATION_CHARS + 1)))
    assert "compress" in out["error"]


# ---------------------------------------------------------------------------
# LAB_HISTORY_TOOLS include-list
# ---------------------------------------------------------------------------


def test_included_tools_unset_means_no_filter(monkeypatch):
    monkeypatch.delenv("LAB_HISTORY_TOOLS", raising=False)
    assert _included_tools() is None


def test_included_tools_parses_and_strips(monkeypatch):
    monkeypatch.setenv(
        "LAB_HISTORY_TOOLS", " list_equipment_now, query_runs ,tail_journald "
    )
    assert _included_tools() == {"list_equipment_now", "query_runs", "tail_journald"}


def test_included_tools_unknown_name_fails_fast(monkeypatch):
    monkeypatch.setenv("LAB_HISTORY_TOOLS", "list_equipment_now,query_run")
    with pytest.raises(ValueError, match="query_run"):
        _included_tools()


def test_included_tools_set_but_empty_fails_fast(monkeypatch):
    monkeypatch.setenv("LAB_HISTORY_TOOLS", " , ,")
    with pytest.raises(ValueError, match="names no tools"):
        _included_tools()


async def test_build_server_registers_only_included_tools(monkeypatch):
    pytest.importorskip("mcp")
    from app.mcp_server import _build_server

    monkeypatch.setenv(
        "LAB_HISTORY_TOOLS",
        "list_equipment_now,get_equipment_status,record_observation,"
        "query_equipment_events,query_service_uptime,query_sensor_readings,"
        "tail_journald,capture_camera_snapshot",
    )
    names = {t.name for t in await _build_server().list_tools()}
    assert names == ALL_TOOLS - {"query_runs", "query_well_results"}


async def test_build_server_unfiltered_registers_all_tools(monkeypatch):
    pytest.importorskip("mcp")
    from app.mcp_server import _build_server

    monkeypatch.delenv("LAB_HISTORY_TOOLS", raising=False)
    names = {t.name for t in await _build_server().list_tools()}
    assert names == ALL_TOOLS



# ---------------------------------------------------------------------------
# capture_camera_snapshot (2026-09-04): a frame off go2rtc's relay, saved for
# the chat; never the gateway's /control/snapshot.
# ---------------------------------------------------------------------------

import json as _json  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

import httpx as _httpx  # noqa: E402

from app import mcp_server as _ms  # noqa: E402

_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 5000


def _camera_equipment(*, privacy: bool = False, streaming: bool = True, lenses=("wide", "tele"), fetch_error=None):
    return {
        "equipment": [
            {
                "id": "cam_hte_tapo_c245",
                "name": "HTE bench camera",
                "kind": "camera",
                "base_url": "http://127.0.0.1:8002",
                "fetch_error": fetch_error,
                "status": {
                    "equipment_status": "ready",
                    "details": {
                        "lenses": [{"id": lens} for lens in lenses],
                        "privacy_mode": privacy,
                        "streaming_enabled": streaming,
                    },
                },
            },
            {"id": "plateloc", "name": "PlateLoc", "kind": "plate_sealer", "status": {}},
        ]
    }


@respx.mock
async def test_capture_camera_snapshot_saves_a_frame_and_returns_its_chat_url(monkeypatch, tmp_path):
    monkeypatch.setattr(_ms, "SNAPSHOT_DIR", str(tmp_path))
    respx.get(f"{DASHBOARD_API_URL}/api/equipment").mock(
        return_value=_httpx.Response(200, json=_camera_equipment())
    )
    frame = respx.get(f"{_ms.GO2RTC_URL}/api/frame.jpeg").mock(
        return_value=_httpx.Response(200, content=_JPEG, headers={"content-type": "image/jpeg"})
    )
    out = _json.loads(await _ms._capture_camera_snapshot("cam_hte_tapo_c245", None))
    snap = out["snapshot"]
    # Default lens is wide; the go2rtc source is <camera>_<lens>.
    assert frame.calls.last.request.url.params["src"] == "cam_hte_tapo_c245_wide"
    assert snap["camera_name"] == "HTE bench camera"
    assert snap["lens"] == "wide" and snap["bytes"] == len(_JPEG)
    assert snap["image_url"].startswith("/api/assistant/snapshots/cam_hte_tapo_c245_wide_")
    saved = _Path(snap["_file"])
    assert saved.parent == tmp_path and saved.read_bytes() == _JPEG
    assert snap["image_url"].endswith(saved.name)
    assert "did not move the camera" in out["note"]


@respx.mock
async def test_capture_camera_snapshot_honours_lens_and_retries_once(monkeypatch, tmp_path):
    monkeypatch.setattr(_ms, "SNAPSHOT_DIR", str(tmp_path))
    respx.get(f"{DASHBOARD_API_URL}/api/equipment").mock(
        return_value=_httpx.Response(200, json=_camera_equipment())
    )
    frame = respx.get(f"{_ms.GO2RTC_URL}/api/frame.jpeg")
    frame.side_effect = [
        _httpx.Response(500, content=b""),
        _httpx.Response(200, content=_JPEG, headers={"content-type": "image/jpeg"}),
    ]
    out = _json.loads(await _ms._capture_camera_snapshot("cam_hte_tapo_c245", "tele"))
    assert out["snapshot"]["lens"] == "tele"
    assert frame.calls.last.request.url.params["src"] == "cam_hte_tapo_c245_tele"
    assert len(frame.calls) == 2


@respx.mock
async def test_capture_camera_snapshot_reports_no_frame_after_two_failures(monkeypatch, tmp_path):
    monkeypatch.setattr(_ms, "SNAPSHOT_DIR", str(tmp_path))
    respx.get(f"{DASHBOARD_API_URL}/api/equipment").mock(
        return_value=_httpx.Response(200, json=_camera_equipment())
    )
    respx.get(f"{_ms.GO2RTC_URL}/api/frame.jpeg").mock(return_value=_httpx.Response(200, content=b"", headers={"content-type": "image/jpeg"}))
    out = _json.loads(await _ms._capture_camera_snapshot("cam_hte_tapo_c245", None))
    assert "no live frame available" in out["error"]
    assert list(tmp_path.glob("*.jpg")) == []


@respx.mock
async def test_capture_camera_snapshot_refuses_privacy_mode_without_touching_go2rtc(monkeypatch, tmp_path):
    monkeypatch.setattr(_ms, "SNAPSHOT_DIR", str(tmp_path))
    respx.get(f"{DASHBOARD_API_URL}/api/equipment").mock(
        return_value=_httpx.Response(200, json=_camera_equipment(privacy=True))
    )
    frame = respx.get(f"{_ms.GO2RTC_URL}/api/frame.jpeg").mock(return_value=_httpx.Response(200, content=_JPEG))
    out = _json.loads(await _ms._capture_camera_snapshot("cam_hte_tapo_c245", None))
    assert out["code"] == "privacy_mode_on"
    assert len(frame.calls) == 0


@respx.mock
async def test_capture_camera_snapshot_unknown_camera_or_lens_lists_the_options(monkeypatch, tmp_path):
    monkeypatch.setattr(_ms, "SNAPSHOT_DIR", str(tmp_path))
    respx.get(f"{DASHBOARD_API_URL}/api/equipment").mock(
        return_value=_httpx.Response(200, json=_camera_equipment())
    )
    out = _json.loads(await _ms._capture_camera_snapshot("plateloc", None))
    assert "no camera with id" in out["error"]
    assert [c["id"] for c in out["cameras"]] == ["cam_hte_tapo_c245"]
    out = _json.loads(await _ms._capture_camera_snapshot("cam_hte_tapo_c245", "macro"))
    assert out["lenses"] == ["wide", "tele"]


def test_capture_camera_snapshot_is_a_registered_tool_name():
    assert "capture_camera_snapshot" in _ms.ALL_TOOLS
