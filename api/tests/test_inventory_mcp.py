"""Tests for the lab-inventory MCP server's helpers.

Like test_mcp_server.py: the FastMCP wrappers delegate to plain async fns,
so these exercise the logic against a respx-mocked bitácora /inventory API
without the ``mcp`` package or a stdio transport.
"""

from __future__ import annotations

import json

import respx
from httpx import Response

from app.inventory_mcp import (
    BITACORA_URL,
    MAX_LIMIT,
    _check_stock,
    _get_chemical,
    _inventory_stats,
    _search_inventory,
)

_THF_BOTTLE = {
    "barcode": "B-0417",
    "product_name": "Tetrahydrofuran, anhydrous",
    "group_name": "SDL2",
    "location": "LM472-AC-Freezer",
    "location_path": "LM472/AC/Freezer",
    "amount_remaining": 250.0,
    "unit": "mL",
    "size_per_unit": 500.0,
    "vendor": "Sigma-Aldrich",
    "lot_number": "L-23",
    "expiry_date": "2027-01-01",
    "storage_req": "flammables cabinet",
    "state": "active",
    "container_type": "bottle",
    "pi": "someone",
    "missed_snapshots": 0,
    "removed_at": None,
    "removed_by": None,
    "removed_reason": None,
}

_SEARCH_PAYLOAD = {
    "query": "thf",
    "count": 1,
    "results": [
        {
            "cas": "109-99-9",
            "name": "tetrahydrofuran",
            "synonyms": ["THF"],
            "formula": "C4H8O",
            "mw": 72.11,
            "smiles": "C1CCOC1",
            "density": 0.889,
            "ghs_pictograms": ["GHS02", "GHS07"],
            "h_codes": ["H225"],
            "p_codes": ["P210"],
            "storage_class": "flammable",
            "sds_url": "https://example.com/sds",
            "enriched": True,
            "bottles": [_THF_BOTTLE],
            "total_bottles": 1,
        }
    ],
}


@respx.mock
async def test_search_compacts_rows_to_summary():
    route = respx.get(f"{BITACORA_URL}/inventory").mock(
        return_value=Response(200, json=_SEARCH_PAYLOAD)
    )
    out = json.loads(await _search_inventory("thf", 20))
    assert out["count"] == 1
    (chem,) = out["results"]
    assert chem["cas"] == "109-99-9"
    assert chem["total_bottles"] == 1
    (bottle,) = chem["bottles"]
    # Search rows are the one-line view: location + amount survive, the
    # vendor/lot/hazard detail is get_chemical's job.
    assert bottle == {
        "barcode": "B-0417",
        "group_name": "SDL2",
        "location": "LM472-AC-Freezer",
        "amount_remaining": 250.0,
        "unit": "mL",
        "state": "active",
    }
    assert "h_codes" not in chem
    assert route.calls.last.request.url.params["q"] == "thf"


@respx.mock
async def test_search_clamps_limit():
    route = respx.get(f"{BITACORA_URL}/inventory").mock(
        return_value=Response(200, json={"query": "", "count": 0, "results": []})
    )
    await _search_inventory("", 10_000)
    assert route.calls.last.request.url.params["limit"] == str(MAX_LIMIT)


@respx.mock
async def test_check_stock_passes_through_verbatim():
    payload = {
        "cas": "109-99-9",
        "name": "tetrahydrofuran",
        "found": True,
        "total_bottles": 1,
        "total_amount": 250.0,
        "unit": "mL",
        "bottles": [_THF_BOTTLE],
        "needed": 50.0,
        "needed_unit": "mL",
        "sufficient": True,
        "message": "250.0 mL available across 1 bottle(s) — need 50.0 mL: sufficient",
    }
    route = respx.get(f"{BITACORA_URL}/inventory/check").mock(
        return_value=Response(200, json=payload)
    )
    out = json.loads(await _check_stock("109-99-9", 50.0, "mL"))
    assert out == payload
    params = route.calls.last.request.url.params
    assert params["cas"] == "109-99-9"
    assert params["needed"] == "50.0"


async def test_check_stock_requires_cas():
    out = json.loads(await _check_stock("", None, "mL"))
    assert "error" in out


@respx.mock
async def test_get_chemical_404_hints_at_search():
    respx.get(f"{BITACORA_URL}/inventory/999-99-9").mock(
        return_value=Response(404, json={"detail": "chemical '999-99-9' not in inventory"})
    )
    out = json.loads(await _get_chemical("999-99-9"))
    assert "not in inventory" in out["error"]
    assert "search_inventory" in out["hint"]


@respx.mock
async def test_inventory_stats_merges_groups():
    respx.get(f"{BITACORA_URL}/inventory/stats").mock(
        return_value=Response(200, json={"chemicals": 615, "bottles": 704, "enriched": 600})
    )
    respx.get(f"{BITACORA_URL}/inventory/groups").mock(
        return_value=Response(200, json={"groups": [{"name": "SDL2", "bottle_count": 704}]})
    )
    out = json.loads(await _inventory_stats())
    assert out["stats"]["bottles"] == 704
    assert out["groups"][0]["name"] == "SDL2"


@respx.mock
async def test_unreachable_api_reports_error_not_raise():
    respx.get(f"{BITACORA_URL}/inventory").mock(side_effect=ConnectionError)
    out = json.loads(await _search_inventory("thf", 5))
    assert "could not reach the inventory API" in out["error"]
