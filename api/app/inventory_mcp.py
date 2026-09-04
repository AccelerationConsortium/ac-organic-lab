"""MCP server exposing the lab's chemical inventory — read-only (``lab-inventory``).

Why this exists
---------------
"Is this chemical in the lab, where is it, and how much is left?" is a
question agents ask while planning or sanity-checking work. The stock ledger
itself lives in bitácora (LIMS Phase 1: ``app/src/bitacora/inventory.py``,
a two-table SQLite store fed by LIMS .xlsx imports), reachable over its
``/inventory/*`` HTTP API. This server is a thin read-only front for that
API, mirroring the ``lab-history`` pattern: small question-shaped tools,
clamped result sizes, JSON-string returns.

Contract stability
------------------
The tool names here (``search_inventory``, ``check_stock``, ``get_chemical``,
``inventory_stats``) are the durable agent-facing contract — they are baked
into client allowlists via ``mcp/servers.yaml``. The *backend* is expected to
change: DATABASE_DESIGN.md §5 plans a Substance/Lot/Container ledger inside
BitacoraDB, at which point this server repoints its HTTP calls and the tool
surface must survive unchanged. Do not name tools after the current storage
shape.

Safety
------
Strictly read-only. Inventory writes (import, tombstone, future deduction)
are admin actions gated on edge-verified identity at bitácora and MUST NOT
appear here as tools. This server also never opens ``inventory.sqlite3``
directly — the bitácora service is that file's only reader/writer; HTTP is
the contract (see bitacora/docs/INVENTORY_DESIGN.md, "Storage engine").

Transport
---------
stdio, spawned per session (entry point ``lab-inventory-mcp`` in
pyproject.toml). Registered for the dashboard assistant in
``app/assistant.py`` and for other clients via ``mcp/servers.yaml``.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Same env var the workflow executor uses to reach bitácora (workflow.py),
# so one setting configures every bitácora consumer in this process tree.
BITACORA_URL = os.environ.get("BITACORA_URL", "http://127.0.0.1:8050")

MAX_LIMIT = 50


def _clamp_limit(value: Any, default: int = 20) -> int:
    try:
        n = int(value) if value is not None else default
    except (TypeError, ValueError):
        n = default
    return max(1, min(MAX_LIMIT, n))


async def _fetch(path: str, params: dict[str, Any] | None = None) -> httpx.Response:
    async with httpx.AsyncClient(timeout=10.0) as client:
        return await client.get(f"{BITACORA_URL}{path}", params=params)


def _unreachable(exc: Exception) -> str:
    return json.dumps({"error": f"could not reach the inventory API: {exc}"})


def _bottle_summary(b: dict[str, Any]) -> dict[str, Any]:
    """One-line view of a bottle for search results; get_chemical has the rest."""

    return {
        "barcode": b.get("barcode"),
        "group_name": b.get("group_name"),
        "location": b.get("location"),
        "amount_remaining": b.get("amount_remaining"),
        "unit": b.get("unit"),
        "state": b.get("state"),
    }


async def _search_inventory(query: str, limit: int) -> str:
    """Search chemicals by name / CAS / synonym; compact rows."""

    try:
        r = await _fetch(
            "/inventory", params={"q": query or "", "limit": _clamp_limit(limit)}
        )
        r.raise_for_status()
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        return _unreachable(exc)

    rows = [
        {
            "cas": c.get("cas"),
            "name": c.get("name"),
            "formula": c.get("formula"),
            "mw": c.get("mw"),
            "total_bottles": c.get("total_bottles"),
            "bottles": [_bottle_summary(b) for b in c.get("bottles") or []],
        }
        for c in data.get("results", [])
    ]
    return json.dumps({"query": data.get("query", query), "count": data.get("count"), "results": rows})


async def _check_stock(cas: str, needed: float | None, unit: str) -> str:
    """Sufficiency check for one CAS; bitácora's response is already curated."""

    if not (cas or "").strip():
        return json.dumps({"error": "cas is required"})
    params: dict[str, Any] = {"cas": cas.strip(), "unit": unit}
    if needed is not None:
        params["needed"] = needed
    try:
        r = await _fetch("/inventory/check", params=params)
        r.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return _unreachable(exc)
    return r.text


async def _get_chemical(cas: str) -> str:
    """Full record for one CAS, bottles included, verbatim."""

    if not (cas or "").strip():
        return json.dumps({"error": "cas is required"})
    try:
        r = await _fetch(f"/inventory/{cas.strip()}")
    except Exception as exc:  # noqa: BLE001
        return _unreachable(exc)
    if r.status_code == 404:
        return json.dumps(
            {
                "error": f"chemical {cas!r} not in inventory",
                "hint": "search_inventory finds chemicals by name or synonym "
                "when the CAS is unknown",
            }
        )
    try:
        r.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return _unreachable(exc)
    return r.text


async def _inventory_stats() -> str:
    """Totals plus per-group bottle counts, in one call."""

    try:
        stats = await _fetch("/inventory/stats")
        stats.raise_for_status()
        groups = await _fetch("/inventory/groups")
        groups.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return _unreachable(exc)
    return json.dumps(
        {"stats": stats.json(), "groups": groups.json().get("groups", [])}
    )


# ---------------------------------------------------------------------------
# MCP server (FastMCP convenience API)
# ---------------------------------------------------------------------------


def _build_server():
    """Build the MCP server. Imported lazily so the dashboard test suite
    (which doesn't have the mcp package installed in CI) can still import
    sibling modules from this directory without dragging mcp in."""

    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("lab-inventory")

    @mcp.tool()
    async def search_inventory(query: str = "", limit: int = 20) -> str:
        """Search the lab's chemical inventory by name, CAS, or synonym.
        Each match returns identity (cas, name, formula, mw) plus a one-line
        summary per bottle (group, location, amount remaining, unit). An
        empty query browses the first `limit` chemicals. Use get_chemical
        for hazards, vendor/lot/expiry, and the full bottle detail."""

        return await _search_inventory(query, limit)

    @mcp.tool()
    async def check_stock(cas: str, needed: float | None = None, unit: str = "mL") -> str:
        """Answer "is there enough X on the shelf" for one CAS. With
        `needed`, reports sufficiency against the summed stock in the
        requested unit (a unit mismatch reports what IS in stock instead of
        guessing a conversion); without it, just totals what's there."""

        return await _check_stock(cas, needed, unit)

    @mcp.tool()
    async def get_chemical(cas: str) -> str:
        """Full inventory record for one CAS: hazards (GHS pictograms,
        H/P codes), storage class, SDS link, and every bottle with vendor,
        lot number, expiry, and location. Use search_inventory first when
        you only have a name."""

        return await _get_chemical(cas)

    @mcp.tool()
    async def inventory_stats() -> str:
        """Inventory totals (chemicals, bottles, enriched records) plus
        per-group bottle counts (a group is one lab's shelf, e.g. SDL2)."""

        return await _inventory_stats()

    return mcp


def run() -> None:
    """Entry point used by `lab-inventory-mcp` (see pyproject.toml scripts).

    Boots the FastMCP server over stdio and blocks until the client closes
    the stream. All logging goes to stderr; stdout is reserved for MCP
    JSON-RPC framing.
    """

    logging.basicConfig(level=logging.INFO, stream=__import__("sys").stderr)
    server = _build_server()
    server.run()


if __name__ == "__main__":
    run()


__all__ = ["run", "BITACORA_URL"]
