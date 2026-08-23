"""`GET /api/locations` serves the loaded location registry.

Mounted on a bare FastAPI with `app.state` set by hand (the pattern
test_control.py uses) so the test proves the one thing that matters about a
state-backed route: it reads the attribute the lifespan sets, and says so
honestly when it is missing.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from lab_skills import load_locations

from app.locations import build_locations_router

REPO_ROOT = Path(__file__).resolve().parents[2]


def _app(with_config: bool = True) -> FastAPI:
    app = FastAPI()
    if with_config:
        app.state.locations_config = load_locations(REPO_ROOT / "locations.yaml")
    app.include_router(build_locations_router())
    return app


def test_route_serves_the_committed_registry() -> None:
    with TestClient(_app()) as client:
        r = client.get("/api/locations")
    assert r.status_code == 200
    body = r.json()
    names = [loc["name"] for loc in body["locations"]]
    assert "xarm_translocation/gripper" in names
    slot2 = next(loc for loc in body["locations"] if loc["name"] == "ot2_hte/slot_2")
    assert slot2["type"] == "deck"
    assert slot2["equipment"] == "ot2_hte"
    assert slot2["aliases"]["ot2_hte"] == "2"
    assert "opentrons_2_low" in slot2["aliases"]["xarm_translocation"]


def test_route_reads_the_state_attribute_the_lifespan_sets() -> None:
    """If main.py's lifespan stopped setting `locations_config`, this route
    must 503, not 500 — and must not fall back to reloading the file itself,
    which would hide the wiring break."""
    with TestClient(_app(with_config=False)) as client:
        r = client.get("/api/locations")
    assert r.status_code == 503
    assert "not loaded" in r.json()["detail"]


def test_main_wires_the_router_and_the_lifespan_attribute() -> None:
    """Source-level check that the two halves are both present in main.py, so
    a refactor cannot drop one without failing here."""
    src = (REPO_ROOT / "api" / "app" / "main.py").read_text()
    assert "app.state.locations_config = load_locations()" in src
    assert "build_locations_router()" in src
