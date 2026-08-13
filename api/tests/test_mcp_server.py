"""Tests for the lab-history MCP server's live-snapshot helpers.

The FastMCP wrappers delegate to plain async fns, so these exercise the
logic without the ``mcp`` package or a stdio transport.
"""

from __future__ import annotations

import json

import respx
from httpx import Response

from app.mcp_server import DASHBOARD_API_URL, _get_equipment_status, _list_equipment_now

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
