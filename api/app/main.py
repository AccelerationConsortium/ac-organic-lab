"""FastAPI entry point for the dashboard.

Imports the registry, polling aggregator, and per-device adapters from the
``ac-organic-lab-skills`` SDK; composes the SDK's snapshots with dashboard
presentation fields (``tile``, ``location``) plus the registry's
``enabled`` / ``maintenance`` mirrors before returning them on
``/api/equipment`` and ``/api/equipment/{id}/status``.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from ac_organic_lab_skills import EquipmentAggregator, load_registry
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .presentation import (
    AggregatorHealth,
    EquipmentList,
    EquipmentSnapshot,
    _snapshot,
    compose_equipment_list,
    load_dashboard_overrides,
)

logger = logging.getLogger("ac_dashboard.api")


def _cors_origins() -> list[str]:
    raw = os.environ.get("DASHBOARD_CORS_ORIGINS", "http://localhost:3000")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    registry = load_registry()
    aggregator = EquipmentAggregator(registry)
    await aggregator.startup()
    app.state.aggregator = aggregator
    app.state.registry = registry
    app.state.overrides = load_dashboard_overrides()
    logger.info(
        "Loaded equipment registry: %d entries", aggregator.equipment_count
    )
    try:
        yield
    finally:
        await aggregator.shutdown()


app = FastAPI(
    title="AC Organic Self-driving Lab Dashboard API",
    description=(
        "Aggregates lab-equipment status into one normalized contract. "
        "See docs/STATUS_SPEC.md for the equipment-side contract."
    ),
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _aggregator() -> EquipmentAggregator:
    aggregator: EquipmentAggregator | None = getattr(app.state, "aggregator", None)
    if aggregator is None:
        raise HTTPException(status_code=503, detail="Aggregator not initialized")
    return aggregator


@app.get("/api/health", response_model=AggregatorHealth, tags=["meta"])
async def health() -> AggregatorHealth:
    aggregator = _aggregator()
    return AggregatorHealth(
        version=__version__,
        equipment_count=aggregator.equipment_count,
    )


@app.get("/api/equipment", response_model=EquipmentList, tags=["equipment"])
async def list_equipment() -> EquipmentList:
    """Return the latest status of every registered equipment in parallel."""

    aggregator = _aggregator()
    skill_list = await aggregator.fetch_all()
    return compose_equipment_list(
        skill_list,
        app.state.overrides,
        app.state.registry,
    )


@app.get(
    "/api/equipment/{equipment_id}/status",
    response_model=EquipmentSnapshot,
    tags=["equipment"],
)
async def get_equipment(equipment_id: str) -> EquipmentSnapshot:
    """Live status fetch for a single equipment."""

    aggregator = _aggregator()
    sdk_snapshot = await aggregator.fetch_one(equipment_id)
    if sdk_snapshot is None:
        raise HTTPException(status_code=404, detail=f"Unknown equipment id: {equipment_id}")
    override = app.state.overrides.get(equipment_id)
    return _snapshot(sdk_snapshot, override, app.state.registry)
