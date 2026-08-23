"""``GET /api/locations`` — the static location registry (``locations.yaml``).

Read-only. Serves the ``LocationsConfig`` the lifespan loaded into
``app.state.locations_config``, the same way ``/api/platforms`` serves
``platforms.yaml``. This is the registry of *places* — not where anything is
(that is the record layer; see ``docs/PLATE_TRACKING.md``).

A router rather than a bare route on ``main.py`` so a test can mount it on a
fresh ``FastAPI()`` with ``app.state`` set by hand — no aggregator, no lifespan
— and still prove the route reads the attribute the lifespan sets.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from lab_skills import LocationsConfig


def build_locations_router() -> APIRouter:
    router = APIRouter(tags=["meta"])

    @router.get("/api/locations", response_model=LocationsConfig)
    async def list_locations(request: Request) -> LocationsConfig:
        """Return the static location registry (every place a container can be)."""
        config = getattr(request.app.state, "locations_config", None)
        if config is None:
            raise HTTPException(status_code=503, detail="Locations config not loaded")
        return config

    return router
