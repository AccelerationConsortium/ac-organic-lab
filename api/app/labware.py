"""Central custom-labware definition store (Opentrons schema 2).

Two sources, merged and served read-only to every dashboard user:

- **Repo-committed** (``<repo>/labware/*.json``, env ``LABWARE_REPO_DIR``) —
  PR-reviewed definitions; the durable, versioned tier. Read-only via the API.
- **Uploaded** (``<data-dir>/labware/*.json`` next to ``lab.db``, env
  ``LABWARE_UPLOAD_DIR``) — operator-built definitions saved from the
  dashboard's labware builder. Writes are **admin-gated** (a wrong well depth
  crashes a pipette into a plate, so the shared store is deliberately behind
  the admin role; anyone can still download a built JSON from the browser).

The store never talks to a robot. Definitions are consumed by (1) the OT-2
control page's deck picker ("Custom" group — declaring intent only) and
(2) workflows composing ``/control/setup`` plans through lab-skills, which
pass the full definition as the labware ``config`` for
``protocol.load_labware_from_definition``.

Validation is a pragmatic structural check of the Opentrons labware schema 2
plus the OT-2 physical limits from ``opentrons-server``'s ``LabwareGenerator``
(footprint 127 × 85.5 mm, height 200 mm) — not the full official JSON schema.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import re
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .db import resolve_db_path

logger = logging.getLogger("ac_dashboard.api.labware")

_LOCK = threading.Lock()

# OT-2 slot physical limits (mm) — mirrors opentrons-server LabwareGenerator.
_MAX_DIMENSIONS = {"x": 127.0, "y": 85.5, "z": 200.0}

_LOAD_NAME_RE = re.compile(r"^[a-z0-9._]+$")

# Audit rows land in equipment_events under this pseudo-device id, with the
# same control_action payload shape control.py / deck.py use.
_AUDIT_DEVICE_ID = "labware_store"

_DASHBOARD_OWNER = "ac-organic-lab-dashboard"


def _repo_dir() -> Path:
    env = os.environ.get("LABWARE_REPO_DIR")
    if env:
        return Path(env)
    # api/app/labware.py -> parents[2] == repo root
    return Path(__file__).resolve().parents[2] / "labware"


def _upload_dir() -> Path:
    env = os.environ.get("LABWARE_UPLOAD_DIR")
    if env:
        return Path(env)
    return resolve_db_path().parent / "labware"


def _authz_enforced() -> bool:
    """Same escape hatch as control.py / deck.py (local dev without sidecar)."""
    return os.environ.get("CONTROL_AUTHZ_ENFORCE", "true").lower() != "false"


# ---------------------------------------------------------------------------
# Standard Opentrons definitions (opentrons-shared-data package)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _standard_index() -> dict[str, Path]:
    """{load_name: path to the latest schema-2 definition JSON} from the
    installed ``opentrons-shared-data`` package. Empty (with a log line) if
    the package is missing — the store then serves custom sources only."""
    try:
        import opentrons_shared_data  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("opentrons-shared-data not installed; no standard labware served")
        return {}
    root = Path(opentrons_shared_data.__file__).parent / "data" / "labware" / "definitions" / "2"
    index: dict[str, Path] = {}
    if not root.is_dir():
        return index
    for d in root.iterdir():
        if not d.is_dir():
            continue
        versions = sorted(
            (p for p in d.glob("*.json") if p.stem.isdigit()),
            key=lambda p: int(p.stem),
        )
        if versions:
            index[d.name] = versions[-1]
    return index


def _load_standard(load_name: str) -> dict[str, Any] | None:
    path = _standard_index().get(load_name)
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("standard labware definition %s unreadable", path)
        return None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_definition(defn: Any) -> list[str]:
    """Structural + physical-limit validation. Returns a list of problems
    (empty == valid). Deliberately forgiving about extra keys."""

    errors: list[str] = []
    if not isinstance(defn, dict):
        return ["definition must be a JSON object"]

    if defn.get("schemaVersion") != 2:
        errors.append("schemaVersion must be 2 (Opentrons labware schema 2)")

    params = defn.get("parameters")
    load_name = None
    if not isinstance(params, dict):
        errors.append("parameters object is required")
    else:
        load_name = params.get("loadName")
        if not isinstance(load_name, str) or not _LOAD_NAME_RE.fullmatch(load_name):
            errors.append(
                "parameters.loadName must match ^[a-z0-9._]+$ "
                "(lowercase letters, digits, dot, underscore)"
            )
        elif "_" not in load_name:
            # The OT-2 gateway's deck/declare parses bare strings containing
            # "_" as load_names; a name without one would be mistaken for a
            # legacy kind string on declaration.
            errors.append("parameters.loadName must contain at least one underscore")
        if params.get("isTiprack") and not isinstance(
            params.get("tipLength"), (int, float)
        ):
            errors.append("parameters.tipLength is required when isTiprack is true")

    meta = defn.get("metadata")
    if not isinstance(meta, dict) or not meta.get("displayName"):
        errors.append("metadata.displayName is required")

    dims = defn.get("dimensions")
    if not isinstance(dims, dict):
        errors.append("dimensions object is required")
    else:
        for axis, key in (("x", "xDimension"), ("y", "yDimension"), ("z", "zDimension")):
            v = dims.get(key)
            if not isinstance(v, (int, float)) or v <= 0:
                errors.append(f"dimensions.{key} must be a positive number")
            elif v > _MAX_DIMENSIONS[axis]:
                errors.append(
                    f"dimensions.{key} = {v} exceeds the OT-2 slot limit "
                    f"({_MAX_DIMENSIONS[axis]} mm)"
                )

    wells = defn.get("wells")
    ordering = defn.get("ordering")
    if not isinstance(wells, dict) or not wells:
        errors.append("wells object is required and must be non-empty")
    if not isinstance(ordering, list) or not ordering:
        errors.append("ordering (list of columns) is required")
    if isinstance(wells, dict) and isinstance(ordering, list):
        ordered = [w for col in ordering if isinstance(col, list) for w in col]
        if sorted(ordered) != sorted(wells.keys()):
            errors.append("ordering must reference exactly the keys of wells")

    if isinstance(wells, dict) and isinstance(dims, dict):
        x_max = dims.get("xDimension")
        y_max = dims.get("yDimension")
        z_max = dims.get("zDimension")
        for name, w in wells.items():
            if not isinstance(w, dict):
                errors.append(f"well {name} must be an object")
                continue
            for k in ("x", "y", "z", "depth", "totalLiquidVolume"):
                if not isinstance(w.get(k), (int, float)):
                    errors.append(f"well {name}.{k} must be a number")
            if isinstance(w.get("x"), (int, float)) and isinstance(x_max, (int, float)):
                if not (0 <= w["x"] <= x_max):
                    errors.append(f"well {name}.x is outside the footprint")
            if isinstance(w.get("y"), (int, float)) and isinstance(y_max, (int, float)):
                if not (0 <= w["y"] <= y_max):
                    errors.append(f"well {name}.y is outside the footprint")
            if (
                isinstance(w.get("depth"), (int, float))
                and isinstance(z_max, (int, float))
                and w["depth"] > z_max
            ):
                errors.append(f"well {name}.depth exceeds the labware height")
    return errors


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def _read_dir(directory: Path, source: str) -> dict[str, dict[str, Any]]:
    """{load_name: {definition, source}} from one directory (bad files logged
    and skipped — one malformed upload must not take the endpoint down)."""
    out: dict[str, dict[str, Any]] = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            defn = json.loads(path.read_text(encoding="utf-8"))
            load_name = defn["parameters"]["loadName"]
        except Exception:  # noqa: BLE001
            logger.warning("skipping malformed labware definition %s", path)
            continue
        out[str(load_name)] = {"definition": defn, "source": source}
    return out


def _load_all() -> dict[str, dict[str, Any]]:
    """Merged view; a repo-committed definition wins over an uploaded one with
    the same loadName (the reviewed tier is authoritative)."""
    merged = _read_dir(_upload_dir(), "uploaded")
    merged.update(_read_dir(_repo_dir(), "repo"))
    return merged


def _grid(defn: dict[str, Any]) -> tuple[int, int]:
    ordering = defn.get("ordering")
    if isinstance(ordering, list) and ordering and isinstance(ordering[0], list):
        return (len(ordering[0]), len(ordering))
    return (0, 0)


def _summary(load_name: str, item: dict[str, Any]) -> dict[str, Any]:
    defn = item["definition"]
    rows, columns = _grid(defn)
    meta = defn.get("metadata") or {}
    params = defn.get("parameters") or {}
    wells = defn.get("wells") or {}
    first_well = next(iter(wells.values()), {}) if isinstance(wells, dict) else {}
    return {
        "load_name": load_name,
        "display_name": meta.get("displayName") or load_name,
        "display_category": meta.get("displayCategory") or "wellPlate",
        "is_tiprack": bool(params.get("isTiprack")),
        "rows": rows,
        "columns": columns,
        "well_count": len(wells) if isinstance(wells, dict) else 0,
        "well_volume_ul": first_well.get("totalLiquidVolume"),
        "version": defn.get("version"),
        "namespace": defn.get("namespace"),
        "source": item["source"],
    }


# ---------------------------------------------------------------------------
# Auth + audit
# ---------------------------------------------------------------------------


def _require_admin(request: Request) -> str:
    """Writes to the shared store are admin-gated.

    Identity arrives as X-Auth-User / X-Auth-Role, injected by the Next.js
    middleware only after verifying the session (it strips client-supplied
    copies first). Mirroring deck.py: a header-less request means a
    deliberately open deployment (CONTROL_AUTHZ_ENFORCE=false / dev) or a
    direct loopback call that skipped the edge — allowed, attributed to the
    generic dashboard owner. With a verified identity present, the role must
    be admin.
    """
    user = request.headers.get("x-auth-user")
    if not _authz_enforced() or not user:
        return user or _DASHBOARD_OWNER
    role = (request.headers.get("x-auth-role") or "").lower()
    if role != "admin":
        raise HTTPException(
            status_code=403,
            detail=(
                "Saving to the shared labware store is admin-only "
                "(you can still download the built JSON). "
                f"{user} has role {role or 'none'}."
            ),
        )
    return user


async def _audit(
    request: Request, action: str, load_name: str, owner: str, outcome: str
) -> None:
    """Best-effort control_action audit row; never raises."""
    db = getattr(request.app.state, "db", None)
    if db is None:
        return
    payload: dict[str, Any] = {
        "action": action,
        "method": request.method,
        "status_code": 200,
        "outcome": outcome,
        "owner": owner,
        "detail": {"load_name": load_name},
    }
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            functools.partial(
                db.record_equipment_event,
                _AUDIT_DEVICE_ID,
                "control_action",
                message=f"{owner} {action} {load_name} → {outcome}",
                payload=payload,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("labware audit write failed for %s: %s", load_name, exc)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class LabwareUpload(BaseModel):
    definition: dict[str, Any] = Field(..., description="Opentrons schema-2 labware definition")


def build_labware_router() -> APIRouter:
    router = APIRouter(prefix="/api/labware", tags=["labware"])

    @router.get("")
    async def list_labware() -> dict[str, Any]:
        with _LOCK:
            merged = _load_all()
        return {
            "definitions": sorted(
                (_summary(name, item) for name, item in merged.items()),
                key=lambda s: (s["source"], s["load_name"]),
            )
        }

    # NOTE: declared before /{load_name} so "standard" isn't captured by it.
    @router.get("/standard")
    async def list_standard_labware() -> dict[str, Any]:
        """Every official Opentrons definition shipped by opentrons-shared-data
        (latest schema-2 version per load_name), as summaries."""
        out = []
        for load_name in sorted(_standard_index()):
            defn = _load_standard(load_name)
            if defn is not None:
                out.append(_summary(load_name, {"definition": defn, "source": "standard"}))
        return {"definitions": out}

    @router.get("/standard/{load_name}")
    async def get_standard_labware(load_name: str) -> dict[str, Any]:
        defn = _load_standard(load_name)
        if defn is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown standard labware {load_name!r}"
            )
        return {"source": "standard", "definition": defn}

    @router.get("/{load_name}")
    async def get_labware(load_name: str) -> dict[str, Any]:
        with _LOCK:
            merged = _load_all()
        item = merged.get(load_name)
        if item is None:
            raise HTTPException(status_code=404, detail=f"Unknown labware {load_name!r}")
        return {"source": item["source"], "definition": item["definition"]}

    @router.post("")
    async def upload_labware(body: LabwareUpload, request: Request) -> dict[str, Any]:
        owner = _require_admin(request)
        problems = validate_definition(body.definition)
        if problems:
            raise HTTPException(
                status_code=422,
                detail={"message": "Labware definition failed validation", "problems": problems},
            )
        load_name = body.definition["parameters"]["loadName"]
        if load_name in _standard_index():
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{load_name!r} is a standard Opentrons definition (built into the "
                    "robot); a custom copy would shadow it — pick a different load name."
                ),
            )
        with _LOCK:
            if load_name in _read_dir(_repo_dir(), "repo"):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"{load_name!r} is a repo-committed definition; "
                        "change it via a PR, not an upload."
                    ),
                )
            directory = _upload_dir()
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{load_name}.json"
            replaced = path.exists()
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(body.definition, indent=2, sort_keys=True), encoding="utf-8"
            )
            tmp.replace(path)
        await _audit(request, "labware.upload", load_name, owner, "replaced" if replaced else "created")
        return _summary(load_name, {"definition": body.definition, "source": "uploaded"})

    @router.delete("/{load_name}", status_code=204)
    async def delete_labware(load_name: str, request: Request) -> None:
        owner = _require_admin(request)
        with _LOCK:
            if load_name in _read_dir(_repo_dir(), "repo"):
                raise HTTPException(
                    status_code=409,
                    detail=f"{load_name!r} is repo-committed; remove it via a PR.",
                )
            path = _upload_dir() / f"{load_name}.json"
            if not path.is_file():
                raise HTTPException(status_code=404, detail=f"Unknown uploaded labware {load_name!r}")
            path.unlink()
        await _audit(request, "labware.delete", load_name, owner, "deleted")

    return router
