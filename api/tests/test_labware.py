"""Tests for the central custom-labware store (/api/labware)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.labware import build_labware_router, validate_definition


def _definition(load_name: str = "matterlab_24_vialplate_2ml", **overrides: Any) -> dict:
    """A minimal valid schema-2 definition (2×2 grid to keep it short)."""
    wells = {}
    ordering = []
    for col in range(2):
        col_names = []
        for row in range(2):
            name = f"{chr(ord('A') + row)}{col + 1}"
            wells[name] = {
                "depth": 30.0,
                "totalLiquidVolume": 2000.0,
                "shape": "circular",
                "diameter": 10.0,
                "x": 20.0 + col * 20.0,
                "y": 60.0 - row * 20.0,
                "z": 5.0,
            }
            col_names.append(name)
        ordering.append(col_names)
    base = {
        "schemaVersion": 2,
        "version": 1,
        "namespace": "custom",
        "metadata": {
            "displayName": "MatterLab 24 vial plate 2 mL",
            "displayCategory": "wellPlate",
            "displayVolumeUnits": "µL",
        },
        "brand": {"brand": "MatterLab"},
        "parameters": {
            "format": "irregular",
            "isTiprack": False,
            "isMagneticModuleCompatible": False,
            "loadName": load_name,
            "quirks": [],
        },
        "dimensions": {"xDimension": 127.0, "yDimension": 85.0, "zDimension": 40.0},
        "cornerOffsetFromSlot": {"x": 0, "y": 0, "z": 0},
        "wells": wells,
        "ordering": ordering,
        "groups": [{"wells": sorted(wells), "metadata": {}}],
    }
    base.update(overrides)
    return base


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    repo = tmp_path / "repo_labware"
    uploads = tmp_path / "uploads"
    repo.mkdir()
    monkeypatch.setenv("LABWARE_REPO_DIR", str(repo))
    monkeypatch.setenv("LABWARE_UPLOAD_DIR", str(uploads))
    # Committed definition (source a).
    committed = _definition("lab_repo_96_plate_360ul")
    (repo / "lab_repo_96_plate_360ul.json").write_text(json.dumps(committed))
    app = FastAPI()
    app.include_router(build_labware_router())
    return TestClient(app)


ADMIN = {"X-Auth-User": "admin@lab", "X-Auth-Role": "admin"}
OPERATOR = {"X-Auth-User": "op@lab", "X-Auth-Role": "operator"}


# ---- validation -----------------------------------------------------------


def test_validate_accepts_a_good_definition() -> None:
    assert validate_definition(_definition()) == []


def test_validate_rejects_bad_load_name_and_oversize() -> None:
    bad = _definition()
    bad["parameters"]["loadName"] = "Bad Name!"
    bad["dimensions"]["xDimension"] = 300.0
    problems = validate_definition(bad)
    assert any("loadName" in p for p in problems)
    assert any("exceeds the OT-2 slot limit" in p for p in problems)


def test_validate_requires_underscore_in_load_name() -> None:
    bad = _definition()
    bad["parameters"]["loadName"] = "vialplate"
    assert any("underscore" in p for p in validate_definition(bad))


def test_validate_checks_ordering_and_well_bounds() -> None:
    bad = _definition()
    bad["ordering"] = [["A1"]]  # missing wells
    bad["wells"]["A1"]["x"] = 400.0  # outside footprint
    problems = validate_definition(bad)
    assert any("ordering" in p for p in problems)
    assert any("outside the footprint" in p for p in problems)


def test_validate_requires_tip_length_for_tipracks() -> None:
    bad = _definition()
    bad["parameters"]["isTiprack"] = True
    assert any("tipLength" in p for p in validate_definition(bad))


# ---- store ----------------------------------------------------------------


def test_list_merges_repo_and_uploaded(client: TestClient) -> None:
    res = client.get("/api/labware")
    assert res.status_code == 200
    names = {d["load_name"]: d for d in res.json()["definitions"]}
    assert names["lab_repo_96_plate_360ul"]["source"] == "repo"
    assert names["lab_repo_96_plate_360ul"]["rows"] == 2
    assert names["lab_repo_96_plate_360ul"]["columns"] == 2

    up = client.post("/api/labware", json={"definition": _definition()}, headers=ADMIN)
    assert up.status_code == 200, up.text
    assert up.json()["source"] == "uploaded"

    names = {d["load_name"]: d for d in client.get("/api/labware").json()["definitions"]}
    assert names["matterlab_24_vialplate_2ml"]["source"] == "uploaded"


def test_get_detail_and_404(client: TestClient) -> None:
    res = client.get("/api/labware/lab_repo_96_plate_360ul")
    assert res.status_code == 200
    assert res.json()["definition"]["parameters"]["loadName"] == "lab_repo_96_plate_360ul"
    assert client.get("/api/labware/nope_nothing").status_code == 404


def test_upload_requires_admin_role(client: TestClient) -> None:
    res = client.post("/api/labware", json={"definition": _definition()}, headers=OPERATOR)
    assert res.status_code == 403
    # Header-less = dev-open / direct loopback (mirrors deck.py) — allowed.
    res = client.post("/api/labware", json={"definition": _definition()})
    assert res.status_code == 200


def test_upload_rejects_invalid_definition(client: TestClient) -> None:
    bad = _definition()
    bad["dimensions"]["zDimension"] = -1
    res = client.post("/api/labware", json={"definition": bad}, headers=ADMIN)
    assert res.status_code == 422
    assert res.json()["detail"]["problems"]


def test_upload_cannot_shadow_repo_definition(client: TestClient) -> None:
    res = client.post(
        "/api/labware",
        json={"definition": _definition("lab_repo_96_plate_360ul")},
        headers=ADMIN,
    )
    assert res.status_code == 409


def test_standard_definitions_served(client: TestClient) -> None:
    res = client.get("/api/labware/standard")
    assert res.status_code == 200
    defs = {d["load_name"]: d for d in res.json()["definitions"]}
    assert len(defs) > 100  # opentrons-shared-data ships ~141
    corning = defs["corning_96_wellplate_360ul_flat"]
    assert corning["source"] == "standard"
    assert (corning["rows"], corning["columns"]) == (8, 12)

    detail = client.get("/api/labware/standard/corning_96_wellplate_360ul_flat")
    assert detail.status_code == 200
    assert detail.json()["definition"]["parameters"]["loadName"] == (
        "corning_96_wellplate_360ul_flat"
    )
    assert client.get("/api/labware/standard/nope_nothing").status_code == 404


def test_upload_cannot_shadow_standard_definition(client: TestClient) -> None:
    res = client.post(
        "/api/labware",
        json={"definition": _definition("corning_96_wellplate_360ul_flat")},
        headers=ADMIN,
    )
    assert res.status_code == 409
    assert "standard" in res.json()["detail"]


def test_delete_uploaded_only(client: TestClient) -> None:
    client.post("/api/labware", json={"definition": _definition()}, headers=ADMIN)
    assert (
        client.delete("/api/labware/matterlab_24_vialplate_2ml", headers=OPERATOR).status_code
        == 403
    )
    assert (
        client.delete("/api/labware/matterlab_24_vialplate_2ml", headers=ADMIN).status_code
        == 204
    )
    assert (
        client.delete("/api/labware/matterlab_24_vialplate_2ml", headers=ADMIN).status_code
        == 404
    )
    assert (
        client.delete("/api/labware/lab_repo_96_plate_360ul", headers=ADMIN).status_code == 409
    )
