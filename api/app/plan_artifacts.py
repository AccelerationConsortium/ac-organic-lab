"""What a finished OT-2 plan can truthfully tell the notebook, in Bitácora's
own formats — generated deterministically from the plan record, no model.

Three artefacts (``docs/UNFORMATTED_RUNS_PLAN.md`` step 1):

* **as-run protocol** — a Bitácora ``protocols/<name>.yaml`` plus the
  ``compile/actions.yaml`` entries it needs. A pattern plan (``prelude`` /
  ``for_each_well`` / ``epilogue``, the gateway's provenance) becomes one
  per-well action with ``before_wells`` / ``steps`` / ``after_wells``; a flat
  plan becomes one single-skill action per step. Numbers from the run are
  literal ``args`` — this records *what ran*; it is not yet a parameterised
  protocol, and it says so in its description.
* **design skeleton** — a ``designs/<name>.yaml`` with what the run supports
  (realized per-well levels, replicates, readout, the plate) and explicit
  ``TODO`` placeholders for every field that is scientific intent
  (objective, hypothesis, factor meaning, controls). Those are never guessed:
  the agent does not decide chemistry.
* **well samples** — one BitacoraDB ``Sample`` per weighed well, keyed
  ``<plate>:<well>`` the way Bitácora's lineage joins them, carrying the
  measured mass in ``meta``. (BitacoraDB has no gravimetric ``technique``
  yet, so masses ride on the sample rather than as a ``Measurement``.)

Placeholders: the gateway's ``{well}`` is Bitácora's ``{well}``; ``{row}`` /
``{column}`` map to ``{well_row}`` / ``{well_column}``; ``{index}`` has no
Bitácora equivalent and is reported as a warning rather than invented.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

import yaml

PROTOCOL_PREFIX = "asrun"
_PLACEHOLDERS = {"{row}": "{well_row}", "{column}": "{well_column}"}
_IDENT = re.compile(r"[^a-z0-9_]+")


def _ident(text: str) -> str:
    out = _IDENT.sub("_", str(text).lower()).strip("_")
    return out if out and out[0].isalpha() else f"p_{out}"


def artifact_name(bundle: dict[str, Any]) -> str:
    """``asrun-<plan id>``: valid for a protocol name, a design name and an
    action name (lower-case; the plan id keeps its case in ``meta``)."""
    return f"{PROTOCOL_PREFIX}-{str(bundle['plan_id']).lower()}"


def _translate(value: Any) -> Any:
    if isinstance(value, str):
        for ours, theirs in _PLACEHOLDERS.items():
            value = value.replace(ours, theirs)
        return value
    if isinstance(value, dict):
        return {k: _translate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_translate(v) for v in value]
    return value


def _has_index_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return "{index}" in value
    if isinstance(value, dict):
        return any(_has_index_placeholder(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_index_placeholder(v) for v in value)
    return False


def _subs(steps: Iterable[dict[str, Any]], prefix: str) -> list[dict[str, Any]]:
    out = []
    for i, step in enumerate(steps, start=1):
        sub = {"id": step.get("id") or f"{prefix}{i}", "skill": step["action"],
               "args": _translate(step.get("args") or {})}
        out.append(sub)
    return out


def as_run_protocol(bundle: dict[str, Any]) -> dict[str, Any]:
    """``{"protocol": {...}, "actions": {...}, "warnings": [...]}`` — the two
    YAML documents (as dicts) and anything that could not be expressed."""
    plan = bundle.get("plan") or {}
    pattern = plan.get("pattern")
    name = artifact_name(bundle)
    warnings: list[str] = []
    labware = (bundle.get("plate_report") or {}).get("labware")
    description = (
        f"As-run record of OT-2 plan {bundle['plan_id']} on {bundle['equipment_id']} "
        f"({bundle.get('status')}, {bundle.get('steps_ok')}/{bundle.get('steps_total')} steps ok), "
        f"generated from the plan record. Numbers are what ran, not parameters; this is "
        f"not an authored protocol until a human has reviewed and merged it."
    )
    actions: dict[str, Any] = {}
    steps: list[dict[str, Any]] = []
    plate_map: dict[str, Any] | None = None

    if pattern:
        few = pattern.get("for_each_well") or {}
        wells = list(pattern.get("wells") or [])
        labware = few.get("labware_nickname") or labware
        action: dict[str, Any] = {"role": "liquid_handler", "for_each_well": True}
        before = _subs(pattern.get("prelude") or [], "pre")
        template = _subs(few.get("steps") or [], "w")
        after = _subs(pattern.get("epilogue") or [], "post")
        if before:
            action["before_wells"] = before
        action["steps"] = template
        if after:
            action["after_wells"] = after
        if _has_index_placeholder(few.get("steps")):
            warnings.append("the template used {index}, which Bitácora has no placeholder for; "
                            "the per-well expansion in the plan record is authoritative")
        overrides = few.get("overrides") or {}
        if overrides:
            warnings.append("per-well overrides are recorded as well conditions in the plate map; "
                            "the template shows the unoverridden arguments")
        actions[name] = action
        steps.append({"step_id": "s001", "action": name})
        well_entries: dict[str, Any] = {}
        for well in wells:
            entry: dict[str, Any] = {}
            over = overrides.get(well)
            if isinstance(over, dict):
                conditions: dict[str, Any] = {}
                for step_id, args in over.items():
                    if isinstance(args, dict):
                        for arg, value in args.items():
                            if isinstance(value, (str, int, float, bool)):
                                conditions[_ident(f"{step_id}_{arg}")] = value
                if conditions:
                    entry["conditions"] = conditions
            well_entries[well] = entry
        plate_map = {"labware": str(labware or "plate"), "role": "conditions", "wells": well_entries}
    else:
        for i, step in enumerate(plan.get("steps") or [], start=1):
            action_name = f"{name}-s{i:03d}"
            actions[action_name] = {"role": "liquid_handler", "skill": step["action"],
                                    "args": _translate(step.get("args") or {})}
            steps.append({"step_id": f"s{i:03d}", "action": action_name})
        if labware:
            report_wells = ((bundle.get("plate_report") or {}).get("wells") or {})
            if report_wells:
                plate_map = {"labware": str(labware), "role": "conditions",
                             "wells": {w: {} for w in report_wells}}

    protocol: dict[str, Any] = {"protocol": name, "description": description, "steps": steps}
    if plate_map:
        protocol["plate_map"] = plate_map
    return {"protocol": protocol, "actions": {"actions": actions}, "warnings": warnings}


def design_skeleton(bundle: dict[str, Any]) -> str:
    """YAML *text* with comments — a human edits this; the TODOs are the
    fields only they can fill. Not a valid design until they do."""
    plan = bundle.get("plan") or {}
    report = bundle.get("plate_report") or {}
    name = artifact_name(bundle)
    pattern = plan.get("pattern") or {}
    wells = list(pattern.get("wells") or (report.get("wells") or {}).keys())
    few = pattern.get("for_each_well") or {}
    labware = few.get("labware_nickname") or report.get("labware") or "plate"
    overrides = few.get("overrides") or {}
    # Realized levels: any argument that an override varied across wells.
    varied: dict[str, set] = {}
    for well, over in overrides.items():
        if isinstance(over, dict):
            for step_id, args in over.items():
                if isinstance(args, dict):
                    for arg, value in args.items():
                        if isinstance(value, (str, int, float, bool)):
                            varied.setdefault(_ident(f"{step_id}_{arg}"), set()).add(value)
    stats = report.get("stats") or {}
    readout = "mass (g) per well, plate balance" if stats.get("n") else "TODO"
    lines = [
        f"# Design skeleton generated from OT-2 plan {bundle['plan_id']} ({bundle.get('started_at', '')[:10]}).",
        "# Every TODO below is scientific intent the run cannot supply — fill it in or",
        "# delete the line; the agent never guesses these (it does not decide chemistry).",
        f"design: {name}",
        "objective: TODO  # what question this plate answers",
        "hypothesis: TODO  # delete if none",
        "factors:",
    ]
    if varied:
        for fname, levels in sorted(varied.items()):
            lvls = ", ".join(yaml.safe_dump(v, default_flow_style=True).strip().rstrip("\n.") for v in sorted(levels, key=str))
            lines += [f"  - name: {fname}  # realized levels from per-well overrides; rename to what it MEANS",
                      f"    levels: [{lvls}]"]
    else:
        lines += ["  - name: todo_factor  # the run varied nothing per well that the record shows",
                  "    levels: [TODO]"]
    lines += [
        "fixed:",
        f"  labware: {labware}",
        f"  wells_used: {len(wells)}",
        "controls: []  # TODO: name the control wells, if any",
        "replicates: 1  # TODO: identical wells were not distinguishable from the run",
        f"readout: {readout}",
    ]
    substances = _substances(plan)
    if substances:
        # The design schema wants a structural identity (cas / smiles /
        # inchikey) per substance; the deck records carry only labels. Offer
        # them as a comment the human completes, never as an invalid entry.
        lines.append("# substances the deck records named (add cas/smiles/inchikey to declare them):")
        for label, text in sorted(substances.items()):
            lines.append(f"#   {label}: {text}")
    return "\n".join(lines) + "\n"


def _substances(plan: dict[str, Any]) -> dict[str, str]:
    """Contents the deck records carried (``plate.load`` / ``well.update``
    steps name them); nothing is inferred."""
    out: dict[str, str] = {}
    for step in plan.get("steps") or []:
        if step.get("action") in {"plate.load", "well.update"}:
            args = step.get("args") or {}
            for key in ("contents", "sample_id", "substance"):
                value = args.get(key)
                if isinstance(value, str) and value.strip():
                    out[_ident(value)] = value
    return out


def protocol_yaml(bundle: dict[str, Any]) -> dict[str, str]:
    """The as-run protocol as the two files Bitácora expects, as text."""
    built = as_run_protocol(bundle)
    header = "# Generated as-run record — review before merging; see description.\n"
    return {
        "protocol": header + yaml.safe_dump(built["protocol"], sort_keys=False, allow_unicode=True),
        "actions": header + yaml.safe_dump(built["actions"], sort_keys=False, allow_unicode=True),
        "warnings": "\n".join(built["warnings"]),
    }


def well_samples(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """One Sample body per weighed well (without ``experiment_id``); keyed
    ``<plate>:<well>`` so Bitácora's lineage join finds it."""
    report = bundle.get("plate_report") or {}
    labware = str(report.get("labware") or "plate")
    taken = bundle.get("finished_at") or bundle.get("started_at")
    out = []
    for well, cell in (report.get("wells") or {}).items():
        if not isinstance(cell, dict) or cell.get("mass_g") is None:
            continue
        meta = {k: cell.get(k) for k in ("mass_g", "deviation_pct", "volume_ul", "implied_volume_ul",
                                          "stable", "status", "repeat_weighings") if cell.get(k) is not None}
        meta.update({"plate": labware, "well": well, "plan_id": bundle["plan_id"],
                     "device_id": bundle["device_id"], "source": "ot2-gateway plan", "unit": "g"})
        out.append({
            "hid": f"{labware}:{well}",
            "title": f"{labware} {well} — {cell['mass_g']} g",
            "matrix": "liquid",
            "taken_at": taken,
            "creator": bundle.get("approved_by") or "",
            "meta": meta,
        })
    return out
