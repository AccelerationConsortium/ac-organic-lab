"""Central custom-labware definition store (Opentrons schema 2).

Two sources, merged and served read-only to every dashboard user:

- **Repo-committed** (``<repo>/labware/*.json``, env ``LABWARE_REPO_DIR``) —
  PR-reviewed definitions; the durable, versioned tier. Read-only via the API.
  Authorship is git (the PR), not stamped here.
- **Uploaded** (``<data-dir>/labware/*.json`` next to ``lab.db``, env
  ``LABWARE_UPLOAD_DIR``) — operator-built definitions saved from the
  dashboard's labware builder. Writes require a **signed-in session** (any
  role — opened from admin-only 2026-08-18); every save/delete is audited as
  a ``control_action`` on the ``labware_store`` pseudo-device, and the
  uploader's ac_auth identity (``X-Auth-User``) is stamped onto the file as
  store-side authorship — never into the Opentrons definition itself, never
  from the request body. Anyone, signed in or not, can still download a built
  JSON from the browser without saving it.

Uploaded files are a small **store envelope** wrapping the schema-2
definition so authorship survives next to the geometry without polluting it::

    {"definition": {...}, "created_by": "...", "created_at": "...",
     "updated_by": "...", "updated_at": "..."}

Legacy raw definition files (pre-envelope) still load; authorship fields are
null until the next save, which rewrites them as an envelope.

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
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .db import resolve_db_path

logger = logging.getLogger("ac_dashboard.api.labware")

_LOCK = threading.Lock()

# OT-2 slot physical limits (mm) — mirrors opentrons-server LabwareGenerator.
_MAX_DIMENSIONS = {"x": 127.0, "y": 85.5, "z": 200.0}

_LOAD_NAME_RE = re.compile(r"^[a-z0-9._]+$")

# Fields every schema-2 definition carries. Detail routes verify this boundary
# before returning a store/package document so a corrupt summary-like object
# can never masquerade as a loadable definition.
_REQUIRED_DEFINITION_FIELDS = frozenset(
    {
        "dimensions",
        "ordering",
        "wells",
        "parameters",
        "namespace",
        "version",
        "schemaVersion",
        "metadata",
        "brand",
    }
)

# Audit rows land in equipment_events under this pseudo-device id, with the
# same control_action payload shape control.py / deck.py use.
_AUDIT_DEVICE_ID = "labware_store"

_DASHBOARD_OWNER = "ac-organic-lab-dashboard"

# Store-envelope authorship keys. Stamped from X-Auth-User on write; never
# accepted from the request body (identity comes from the trusted edge).
_AUTHORSHIP_KEYS = ("created_by", "created_at", "updated_by", "updated_at")


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


def _require_complete_definition(
    load_name: str, definition: dict[str, Any], source: str
) -> dict[str, Any]:
    """Reject a corrupt summary-like object at the detail-route boundary."""
    problems = sorted(_REQUIRED_DEFINITION_FIELDS - definition.keys())
    parameters = definition.get("parameters")
    if definition.get("schemaVersion") != 2:
        problems.append("schemaVersion=2")
    if not isinstance(parameters, dict) or parameters.get("loadName") != load_name:
        problems.append("parameters.loadName")
    for field in ("dimensions", "wells", "metadata", "brand"):
        if not isinstance(definition.get(field), dict):
            problems.append(f"{field} object")
    if not isinstance(definition.get("ordering"), list):
        problems.append("ordering list")
    if not isinstance(definition.get("namespace"), str) or not definition["namespace"]:
        problems.append("namespace")
    if isinstance(definition.get("version"), bool) or not isinstance(
        definition.get("version"), int
    ):
        problems.append("version")

    dimensions = definition.get("dimensions")
    if isinstance(dimensions, dict) and any(
        not isinstance(dimensions.get(field), (int, float))
        for field in ("xDimension", "yDimension", "zDimension")
    ):
        problems.append("dimensions geometry")
    metadata = definition.get("metadata")
    if isinstance(metadata, dict) and not isinstance(metadata.get("displayName"), str):
        problems.append("metadata.displayName")
    brand = definition.get("brand")
    if isinstance(brand, dict) and not isinstance(brand.get("brand"), str):
        problems.append("brand.brand")

    if isinstance(parameters, dict) and parameters.get("isTiprack"):
        if not isinstance(parameters.get("tipLength"), (int, float)):
            problems.append("parameters.tipLength")

    wells = definition.get("wells")
    ordering = definition.get("ordering")
    if source != "standard" and (
        not isinstance(wells, dict)
        or not wells
        or not isinstance(ordering, list)
        or not ordering
    ):
        problems.append("custom wells and ordering")
    if isinstance(wells, dict):
        geometry_fields = {"x", "y", "z", "depth", "totalLiquidVolume", "shape"}
        if any(
            not isinstance(well, dict) or not geometry_fields.issubset(well)
            for well in wells.values()
        ):
            problems.append("well geometry")

    if problems:
        logger.error(
            "%s labware definition %s is incomplete: %s",
            source,
            load_name,
            ", ".join(sorted(set(problems))),
        )
        raise HTTPException(
            status_code=500,
            detail=f"Stored {source} labware definition {load_name!r} is incomplete",
        )
    return definition


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
        if params.get("isTiprack") and not isinstance(params.get("tipLength"), (int, float)):
            errors.append("parameters.tipLength is required when isTiprack is true")

    meta = defn.get("metadata")
    if not isinstance(meta, dict) or not meta.get("displayName"):
        errors.append("metadata.displayName is required")

    # Opentrons schema-2's standard manufacturer metadata. Keeping these
    # inside `brand` means the exact JSON remains loadable by robot-server;
    # custom top-level metadata would be rejected by the official schema.
    brand = defn.get("brand")
    if (
        not isinstance(brand, dict)
        or not isinstance(brand.get("brand"), str)
        or not brand["brand"].strip()
    ):
        errors.append("brand.brand (vendor / manufacturer) is required")
    else:
        brand_ids = brand.get("brandId", [])
        if not isinstance(brand_ids, list) or not all(
            isinstance(item, str) and item.strip() for item in brand_ids
        ):
            errors.append("brand.brandId must be a list of non-empty product-number strings")
        links = brand.get("links", [])
        if not isinstance(links, list) or not all(
            isinstance(item, str)
            and urlparse(item).scheme in {"http", "https"}
            and bool(urlparse(item).netloc)
            for item in links
        ):
            errors.append("brand.links must be a list of HTTP(S) manufacturer URLs")

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


def _empty_authorship() -> dict[str, Any]:
    return {key: None for key in _AUTHORSHIP_KEYS}


def _parse_store_file(raw: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a store file into (definition, authorship).

    Uploaded files may be either:
    - a **store envelope** ``{definition, created_by, …}`` (current shape), or
    - a legacy raw schema-2 definition (``parameters.loadName`` at the top).

    Repo-committed files are always raw definitions. Authorship is never
    trusted from anywhere except the envelope keys we ourselves wrote.
    """
    if not isinstance(raw, dict):
        raise ValueError("labware store file must be a JSON object")
    params = raw.get("parameters")
    if isinstance(params, dict) and isinstance(params.get("loadName"), str):
        return raw, _empty_authorship()
    defn = raw.get("definition")
    if not isinstance(defn, dict):
        raise ValueError("labware store envelope missing definition")
    authorship = _empty_authorship()
    for key in _AUTHORSHIP_KEYS:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            authorship[key] = value.strip()
    return defn, authorship


def _read_dir(directory: Path, source: str) -> dict[str, dict[str, Any]]:
    """{load_name: {definition, source, authorship…}} from one directory
    (bad files logged and skipped — one malformed upload must not take the
    endpoint down)."""
    out: dict[str, dict[str, Any]] = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            defn, authorship = _parse_store_file(raw)
            load_name = defn["parameters"]["loadName"]
        except Exception:  # noqa: BLE001
            logger.warning("skipping malformed labware definition %s", path)
            continue
        out[str(load_name)] = {"definition": defn, "source": source, **authorship}
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
    brand = defn.get("brand") or {}
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
        "vendor": brand.get("brand"),
        "product_numbers": brand.get("brandId") or [],
        "product_links": brand.get("links") or [],
        "source": item["source"],
        # Store-side authorship (ac_auth X-Auth-User). Null for repo / standard /
        # legacy raw uploads that have not been re-saved as an envelope.
        "created_by": item.get("created_by"),
        "created_at": item.get("created_at"),
        "updated_by": item.get("updated_by"),
        "updated_at": item.get("updated_at"),
    }


def _stamp_authorship(
    *, owner: str, previous: dict[str, Any] | None
) -> dict[str, str]:
    """Build authorship for a write. Creator is sticky; updater always moves.

    ``owner`` is the verified ac_auth principal (or the dashboard fallback when
    authz is deliberately open). Never take these fields from the body.
    """
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
    if previous and previous.get("created_by") and previous.get("created_at"):
        return {
            "created_by": str(previous["created_by"]),
            "created_at": str(previous["created_at"]),
            "updated_by": owner,
            "updated_at": now,
        }
    return {
        "created_by": owner,
        "created_at": now,
        "updated_by": owner,
        "updated_at": now,
    }


def _write_envelope(path: Path, definition: dict[str, Any], authorship: dict[str, str]) -> None:
    # Authorship lives on the envelope only. Drop any smuggled copies from the
    # definition body so a caller cannot plant a fake created_by inside the
    # geometry JSON (schema-2 also forbids unknown top-level keys).
    clean = {k: v for k, v in definition.items() if k not in _AUTHORSHIP_KEYS}
    envelope = {"definition": clean, **authorship}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(envelope, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Auth + audit
# ---------------------------------------------------------------------------


def _require_signed_in(request: Request) -> str:
    """Writes to the shared store require a signed-in identity, not a role.

    Identity arrives as X-Auth-User / X-Auth-Role, injected by the Next.js
    middleware only after verifying the session (it strips client-supplied
    copies first). Mirroring deck.py: a header-less request means a
    deliberately open deployment (CONTROL_AUTHZ_ENFORCE=false / dev) or a
    direct loopback call that skipped the edge — allowed, attributed to the
    generic dashboard owner. Any signed-in role may save or delete; there is
    no privilege check beyond having a verified identity (opened from
    admin-only 2026-08-18 — see labware/README.md). Every write is still
    audited via ``_audit`` below, so a bad definition is traceable to
    whoever saved it.
    """
    user = request.headers.get("x-auth-user")
    if not _authz_enforced() or not user:
        return user or _DASHBOARD_OWNER
    return user


async def _audit(request: Request, action: str, load_name: str, owner: str, outcome: str) -> None:
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
            raise HTTPException(status_code=404, detail=f"Unknown standard labware {load_name!r}")
        return {
            "source": "standard",
            "definition": _require_complete_definition(load_name, defn, "standard"),
        }

    @router.get("/{load_name}")
    async def get_labware(load_name: str) -> dict[str, Any]:
        with _LOCK:
            merged = _load_all()
        item = merged.get(load_name)
        if item is None:
            raise HTTPException(status_code=404, detail=f"Unknown labware {load_name!r}")
        return {
            "source": item["source"],
            "definition": _require_complete_definition(
                load_name, item["definition"], item["source"]
            ),
            "created_by": item.get("created_by"),
            "created_at": item.get("created_at"),
            "updated_by": item.get("updated_by"),
            "updated_at": item.get("updated_at"),
        }

    @router.post("")
    async def upload_labware(body: LabwareUpload, request: Request) -> dict[str, Any]:
        owner = _require_signed_in(request)
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
            previous: dict[str, Any] | None = None
            if path.is_file():
                try:
                    _, previous = _parse_store_file(
                        json.loads(path.read_text(encoding="utf-8"))
                    )
                except Exception:  # noqa: BLE001
                    # Corrupt/legacy file we are about to replace — treat as create.
                    previous = None
            authorship = _stamp_authorship(owner=owner, previous=previous)
            replaced = path.exists()
            # Strip smuggled authorship keys from the definition body before
            # both write and response — store identity is envelope-only.
            clean_definition = {
                k: v for k, v in body.definition.items() if k not in _AUTHORSHIP_KEYS
            }
            _write_envelope(path, clean_definition, authorship)
        await _audit(
            request, "labware.upload", load_name, owner, "replaced" if replaced else "created"
        )
        return _summary(
            load_name,
            {"definition": clean_definition, "source": "uploaded", **authorship},
        )

    @router.delete("/{load_name}", status_code=204)
    async def delete_labware(load_name: str, request: Request) -> None:
        owner = _require_signed_in(request)
        with _LOCK:
            if load_name in _read_dir(_repo_dir(), "repo"):
                raise HTTPException(
                    status_code=409,
                    detail=f"{load_name!r} is repo-committed; remove it via a PR.",
                )
            path = _upload_dir() / f"{load_name}.json"
            if not path.is_file():
                raise HTTPException(
                    status_code=404, detail=f"Unknown uploaded labware {load_name!r}"
                )
            path.unlink()
        await _audit(request, "labware.delete", load_name, owner, "deleted")

    return router
