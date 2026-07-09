"""Shared deck-layout store (OT-2 and any future deck device).

Persists a per-equipment deck layout (slot -> labware key) to a JSON file in the
data directory so the browser deck picker is *shared* across users instead of
living in one browser's memory.

This is a deliberate stopgap: there is no hardware coupling here. Once a device
service publishes its own deck state on ``/status`` (e.g. the OT-2 server's
``details.snapshot.deck.slots``), the frontend should read that instead and this
store can be retired.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .db import resolve_db_path

logger = logging.getLogger("ac_dashboard.api.deck")

# Bounded so a stray PUT can't persist arbitrary blobs. Keep in sync with the
# frontend's LABWARE_TYPES / DECK_ROWS in LiquidHandlerTile.tsx.
_ALLOWED_LABWARE = {"96-well", "24-well", "waste"}
_MIN_SLOT = 1
_MAX_SLOT = 12

_LOCK = threading.Lock()

# Fallback actor when no authenticated identity is present (dev-open, or a
# direct loopback call that never traversed the Next.js edge). Mirrors
# control.py's _DASHBOARD_CLAIM_OWNER.
_DASHBOARD_OWNER = "ac-organic-lab-dashboard"


def _authz_base() -> str:
    """Auth sidecar base URL — same env the Next.js middleware / control.py use."""
    return os.environ.get("AUTH_SERVICE_BASE", "http://127.0.0.1:8009")


def _authz_enforced() -> bool:
    """Escape hatch mirroring control.py: set CONTROL_AUTHZ_ENFORCE=false for
    local dev without the auth sidecar running."""
    return os.environ.get("CONTROL_AUTHZ_ENFORCE", "true").lower() != "false"


def _authz_client(request: Request) -> httpx.AsyncClient:
    """The app-wide shared httpx client (configured in main.lifespan); fall back
    to a one-shot client outside the normal app context (tests)."""
    client = getattr(request.app.state, "control_client", None)
    if client is None:
        return httpx.AsyncClient(timeout=httpx.Timeout(10.0), trust_env=False)
    return client


async def _authorize_deck_edit(request: Request, equipment_id: str) -> str:
    """Per-equipment authorization for a deck-layout write.

    Changing a device's deck layout is a control-class write, so it is gated
    exactly like ``/control/*`` in ``control.py``: the caller must hold a role
    on *this* equipment (``operator``+, i.e. an admin or an authorized user of
    the device). We ask the auth sidecar ``GET /authz/check?user&equipment`` and
    refuse with 403 when the principal has no such role.

    The authenticated user arrives as the ``X-Auth-User`` header, injected by
    the Next.js middleware only after it verifies the session (never
    client-supplied). A header-less request means a deliberately open
    deployment (dev) or a direct loopback call that skipped the edge — matching
    ``control.py``, we skip the check there (the browser path is always gated at
    the edge). Fail *closed* if the sidecar is unreachable.

    Returns the resolved actor (for the audit row).
    """
    owner = request.headers.get("x-auth-user") or _DASHBOARD_OWNER
    if not _authz_enforced():
        return owner
    user = request.headers.get("x-auth-user")
    if not user:
        return owner
    client = _authz_client(request)
    try:
        resp = await client.get(
            f"{_authz_base()}/authz/check",
            params={"user": user, "equipment": equipment_id},
        )
        resp.raise_for_status()
        verdict = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "authz check unavailable for %s on %s deck: %s", user, equipment_id, exc
        )
        raise HTTPException(
            status_code=503,
            detail="Authorization service unavailable; deck edit refused (fail-closed).",
        ) from exc
    if not verdict.get("allowed"):
        reason = str(verdict.get("reason") or "no role on this equipment")
        raise HTTPException(
            status_code=403,
            detail=(
                f"{user} is not authorized to change the {equipment_id} "
                f"deck layout: {reason}"
            ),
        )
    return user


async def _audit_deck_change(
    request: Request, equipment_id: str, owner: str, slots: dict[str, str]
) -> None:
    """Best-effort audit row for a deck-layout write (who changed which device's
    layout, to what). Uses the same ``control_action`` event_type + payload
    shape as control.py so it shows up in the same audit view. Never raises."""
    db = getattr(request.app.state, "db", None)
    if db is None:
        return
    payload: dict[str, Any] = {
        "action": "deck.set",
        "method": "PUT",
        "status_code": 200,
        "outcome": "ok",
        "owner": owner,
        "detail": {"slots": slots},
    }
    message = f"{owner} PUT deck.set → ok ({len(slots)} slots)"
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            functools.partial(
                db.record_equipment_event,
                equipment_id,
                "control_action",
                message=message,
                payload=payload,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - auditing must never break the write
        logger.warning("deck audit write failed %s: %s", equipment_id, exc)


def _store_path() -> Path:
    """The JSON store lives next to lab.db in the (writable) data directory."""
    return resolve_db_path().parent / "deck_layouts.json"


def _read_all() -> dict[str, dict[str, str]]:
    path = _store_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_all(data: dict[str, dict[str, str]]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replace so a concurrent reader never sees a half-written file.
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(path)


class DeckLayout(BaseModel):
    """A deck layout: slot number (as string) -> labware key."""

    slots: dict[str, str] = Field(default_factory=dict)


def _validate(slots: dict[str, str]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for raw_slot, labware in slots.items():
        try:
            slot = int(raw_slot)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f"invalid slot {raw_slot!r}")
        if not (_MIN_SLOT <= slot <= _MAX_SLOT):
            raise HTTPException(
                status_code=422, detail=f"slot out of range (1..12): {slot}"
            )
        if labware not in _ALLOWED_LABWARE:
            raise HTTPException(status_code=422, detail=f"unknown labware {labware!r}")
        cleaned[str(slot)] = labware
    return cleaned


def build_deck_router() -> APIRouter:
    router = APIRouter(prefix="/api/equipment", tags=["deck"])

    @router.get("/{equipment_id}/deck", response_model=DeckLayout)
    def get_deck(equipment_id: str) -> DeckLayout:
        with _LOCK:
            all_layouts = _read_all()
        return DeckLayout(slots=all_layouts.get(equipment_id, {}))

    @router.put("/{equipment_id}/deck", response_model=DeckLayout)
    async def put_deck(
        equipment_id: str, layout: DeckLayout, request: Request
    ) -> DeckLayout:
        # Changing the layout is a control-class write: only an admin or an
        # authorized user of THIS device may do it (per-equipment role check,
        # fail-closed). The GET above stays a public read.
        cleaned = _validate(layout.slots)
        owner = await _authorize_deck_edit(request, equipment_id)
        with _LOCK:
            all_layouts = _read_all()
            if cleaned:
                all_layouts[equipment_id] = cleaned
            else:
                all_layouts.pop(equipment_id, None)
            _write_all(all_layouts)
        await _audit_deck_change(request, equipment_id, owner, cleaned)
        return DeckLayout(slots=cleaned)

    return router
