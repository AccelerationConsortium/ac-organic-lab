"""Offline documentation registration audit; never contacts equipment.

Run with ``uv run python scripts/audit_documentation.py``. All HTTP entries
are candidates, since the registry does not identify lab-owned device services.
Use repeated --equipment IDs to scope a device-only rollout gate with --check.
"""

from __future__ import annotations

import argparse
from collections import defaultdict

from lab_skills.registry import EquipmentEntry, Registry, load_registry


REQUIRED = {
    "/docs": "swagger",
    "/openapi.json": "openapi",
    "/agent-docs": "markdown",
    "/llms.txt": "text",
}


def registration_gaps(entry: EquipmentEntry) -> list[str]:
    """Check exact declarations, including ambiguous duplicate paths."""
    kinds: dict[str, list[str]] = defaultdict(list)
    for document in entry.documentation:
        kinds[document.path].append(document.kind)
    return [
        f"{path} (expected one {kind} entry)"
        for path, kind in REQUIRED.items()
        if kinds[path] != [kind]
    ]


def audit(registry: Registry) -> tuple[list[str], int]:
    groups: dict[str, list[EquipmentEntry]] = defaultdict(list)
    lines = ["Offline declarations only; no live endpoints or wheel resources verified."]
    incomplete = 0
    checked = 0
    for entry in registry.equipment:
        if entry.adapter != "http" or not entry.base_url:
            lines.append(f"SKIP {entry.id}: no proxy-supported HTTP base")
        else:
            groups[entry.base_url.rstrip("/")].append(entry)
    for entries in groups.values():
        lines.append("Service candidates: " + ", ".join(entry.id for entry in entries))
        for entry in entries:
            checked += 1
            gaps = registration_gaps(entry)
            if gaps:
                incomplete += 1
                lines.append(f"  INCOMPLETE {entry.id}: " + "; ".join(gaps))
            else:
                lines.append(f"  DECLARED {entry.id}: four required paths and kinds")
    lines.append(
        f"{checked - incomplete}/{checked} HTTP entries declared; "
        f"{len(groups)} service bases; {incomplete} incomplete."
    )
    return lines, incomplete


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", help="Registry YAML; defaults to SDK resolution")
    parser.add_argument("--equipment", action="append", help="Limit to this ID; repeatable")
    parser.add_argument("--check", action="store_true", help="Exit 1 if declarations incomplete")
    args = parser.parse_args(argv)
    registry = load_registry(args.registry)
    if args.equipment:
        selected = set(args.equipment)
        unknown = selected - {entry.id for entry in registry.equipment}
        if unknown:
            parser.error("Unknown equipment IDs: " + ", ".join(sorted(unknown)))
        registry = Registry(equipment=[entry for entry in registry.equipment if entry.id in selected])
    else:
        print("All HTTP entries are candidates, including non-device applications.")
        print("Use --equipment IDs to scope a lab-owned device rollout gate.")
    lines, incomplete = audit(registry)
    print("\n".join(lines))
    return int(args.check and incomplete > 0)


if __name__ == "__main__":
    raise SystemExit(main())
