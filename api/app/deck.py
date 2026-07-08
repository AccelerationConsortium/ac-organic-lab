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

import json
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .db import resolve_db_path

# Bounded so a stray PUT can't persist arbitrary blobs. Keep in sync with the
# frontend's LABWARE_TYPES / DECK_ROWS in LiquidHandlerTile.tsx.
_ALLOWED_LABWARE = {"96-well", "24-well", "waste"}
_MIN_SLOT = 1
_MAX_SLOT = 12

_LOCK = threading.Lock()


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
    def put_deck(equipment_id: str, layout: DeckLayout) -> DeckLayout:
        cleaned = _validate(layout.slots)
        with _LOCK:
            all_layouts = _read_all()
            if cleaned:
                all_layouts[equipment_id] = cleaned
            else:
                all_layouts.pop(equipment_id, None)
            _write_all(all_layouts)
        return DeckLayout(slots=cleaned)

    return router
