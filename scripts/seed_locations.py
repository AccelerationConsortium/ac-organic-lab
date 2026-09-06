#!/usr/bin/env python3
"""Seed / check the record layer's `Location` table from `locations.yaml`.

`locations.yaml` is authoritative for WHICH places exist; BitacoraDB is
authoritative for WHAT IS WHERE. This script is the one-directional bridge
(docs/PLATE_TRACKING.md D2): an idempotent upsert-by-name — POST each entry
(409 = already there), PATCH only `label` / `capacity` / `active` when they
differ, set `active: false` for a name the yaml no longer lists — and a
`--check` mode that reports drift without writing anything. It runs on demand,
never at dashboard boot (the record layer is optional; boot must not depend on
it). Names are never renamed or deleted: a renamed place is a new entry plus
`active: false` on the old one.

Usage:
    uv run python scripts/seed_locations.py --check     # report drift only
    uv run python scripts/seed_locations.py             # upsert
Env:
    BITACORADB_URL               (e.g. http://127.0.0.1:8013)
    BITACORADB_EDGE_SECRET_PATH  (file holding the trusted-front secret)
    BITACORADB_USER              (X-Auth-User to record as creator; default: seed_locations)
    LAB_LOCATIONS_PATH            (optional; default: the repo's locations.yaml)
Exit: 0 = in sync (or seeded), 1 = drift found in --check, 2 = config/transport error.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "src"))
from lab_skills import load_locations  # noqa: E402


def _secret(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="report drift, write nothing")
    ap.add_argument("--url", default=os.environ.get("BITACORADB_URL", ""))
    ap.add_argument("--secret-path", default=os.environ.get("BITACORADB_EDGE_SECRET_PATH", ""))
    ap.add_argument("--user", default=os.environ.get("BITACORADB_USER", "seed_locations"))
    ap.add_argument("--locations", default=os.environ.get("LAB_LOCATIONS_PATH") or None)
    args = ap.parse_args(argv)

    if not args.url:
        print("BITACORADB_URL is not set", file=sys.stderr)
        return 2
    secret = _secret(args.secret_path) if args.secret_path else ""
    if not secret:
        print("BITACORADB_EDGE_SECRET_PATH is not set or unreadable", file=sys.stderr)
        return 2
    cfg = load_locations(args.locations)
    headers = {"X-Edge-Secret": secret, "X-Auth-User": args.user, "X-Auth-Role": "admin"}
    base = args.url.rstrip("/")

    try:
        with httpx.Client(timeout=15.0, headers=headers) as client:
            r = client.get(f"{base}/locations")
            if r.status_code == 404:
                print("record layer has no /locations — needs BitacoraDB ≥ 0.13.0 (migration d1e2f3a4b5c6)", file=sys.stderr)
                return 2
            r.raise_for_status()
            live = {row["name"]: row for row in r.json()}
            wanted = {loc.name: loc for loc in cfg.locations}

            to_create = [n for n in wanted if n not in live]
            to_update: list[tuple[str, dict]] = []
            for name, loc in wanted.items():
                row = live.get(name)
                if row is None:
                    continue
                patch = {}
                if (row.get("label") or None) != (loc.label or None):
                    patch["label"] = loc.label
                if (row.get("capacity") or None) != (loc.capacity or None):
                    patch["capacity"] = loc.capacity
                if bool(row.get("active", True)) != bool(loc.active):
                    patch["active"] = loc.active
                if row.get("location_type") != loc.type:
                    print(f"TYPE DISAGREES (immutable — add a new entry, deactivate this one): "
                          f"{name}: yaml={loc.type} db={row.get('location_type')}")
                if (row.get("equipment_id") or None) != (loc.equipment or None):
                    print(f"EQUIPMENT DISAGREES (immutable): {name}: yaml={loc.equipment} db={row.get('equipment_id')}")
                if patch:
                    to_update.append((name, patch))
            to_deactivate = [n for n, row in live.items() if n not in wanted and row.get("active", True)]

            print(f"yaml: {len(wanted)} places; db: {len(live)} rows")
            print(f"  create: {len(to_create)}  update: {len(to_update)}  deactivate (db-only): {len(to_deactivate)}")
            for n in to_create:
                print(f"  + {n}")
            for n, patch in to_update:
                print(f"  ~ {n}: {patch}")
            for n in to_deactivate:
                print(f"  - {n}  (not in yaml → active:false)")
            if args.check:
                return 1 if (to_create or to_update or to_deactivate) else 0

            for n in to_create:
                loc = wanted[n]
                body = {"name": loc.name, "location_type": loc.type, "equipment_id": loc.equipment,
                        "capacity": loc.capacity, "label": loc.label, "active": loc.active,
                        "creator": args.user,
                        "meta": {"aliases": loc.aliases, "notes": loc.notes, "source": "locations.yaml"}}
                rc = client.post(f"{base}/locations", json=body)
                if rc.status_code == 409:
                    print(f"  = {n} (exists)")
                    continue
                rc.raise_for_status()
                print(f"  + {n} created")
            for n, patch in to_update:
                rp = client.patch(f"{base}/locations/{live[n]['location_id']}", json=patch)
                rp.raise_for_status()
                print(f"  ~ {n} updated")
            for n in to_deactivate:
                rp = client.patch(f"{base}/locations/{live[n]['location_id']}", json={"active": False})
                rp.raise_for_status()
                print(f"  - {n} deactivated")
    except httpx.HTTPError as exc:
        print(f"record layer error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
