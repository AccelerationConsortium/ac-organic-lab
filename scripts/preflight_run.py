#!/usr/bin/env python3
"""Read-only pre-flight check for an authorized run.

Pulls a run authorization from bitácora, resolves its pinned binding, and
verifies everything checkable *without* touching hardware: authorization
executability and expiry margin, bound-equipment health and claim state,
advertised skills vs the package's steps, OT-2 instrument/mount match, and
the executor + record-path services. Finishes by printing the bench
checklist it cannot verify remotely (labware slots, liquid volumes, tips),
derived from the package itself.

No claims are taken and no /control/* endpoint is called — safe to run at
any time, any number of times.

Usage:   python3 scripts/preflight_run.py ra_67f32cb0920b4a41
Env:     BITACORA_URL   (default http://127.0.0.1:8050)
         DASHBOARD_URL  (default http://127.0.0.1:8001)
Exit:    0 = no failures (warnings allowed), 1 = at least one FAIL.
Stdlib only; run from the dashboard host (both services are loopback).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

BITACORA_URL = os.environ.get("BITACORA_URL", "http://127.0.0.1:8050")
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://127.0.0.1:8001")

RESULTS: list[tuple[str, str]] = []


def report(level: str, msg: str) -> None:
    RESULTS.append((level, msg))
    print(f"[{level}] {msg}")


def get_json(url: str, timeout: float = 10.0):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.load(resp)


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].startswith("ra_"):
        print(__doc__)
        return 2
    auth_id = sys.argv[1]

    # --- 1. Authorization ---------------------------------------------------
    try:
        auth = get_json(f"{BITACORA_URL}/authorizations/{auth_id}")
    except urllib.error.HTTPError as e:
        report("FAIL", f"authorization {auth_id}: bitácora returned {e.code}")
        return finish()
    except OSError as e:
        report("FAIL", f"bitácora unreachable at {BITACORA_URL}: {e}")
        return finish()

    if auth.get("revoked_at"):
        report("FAIL", f"authorization revoked at {auth['revoked_at']} by {auth.get('revoked_by')}")
    if not auth.get("executable"):
        report("FAIL", "authorization is not executable")
    else:
        report("PASS", f"authorization executable (authorized_by {auth.get('authorized_by')}, "
                       f"protocol {auth.get('protocol_path')})")

    expires = auth.get("expires_at")
    if expires:
        exp = datetime.fromisoformat(expires)
        left = exp - datetime.now(timezone.utc)
        mins = left.total_seconds() / 60
        if mins <= 0:
            report("FAIL", f"authorization expired at {expires}")
        elif mins < 60:
            report("WARN", f"authorization expires in {mins:.0f} min — the between-step re-check "
                           "will abort a run that crosses expiry; re-authorize for a calm window")
        else:
            report("PASS", f"expiry margin {mins / 60:.1f} h (expires {expires})")

    package = auth.get("package") or {}
    steps = package.get("steps") or []
    binding = auth.get("binding") or {}
    if not steps:
        report("FAIL", "authorization package has no steps")
        return finish()
    report("PASS", f"package: {len(steps)} steps, digest {auth.get('package_digest', '?')[:19]}…")

    roles = sorted({s["role"] for s in steps})
    skills_by_role: dict[str, set[str]] = defaultdict(set)
    for s in steps:
        skills_by_role[s["role"]].add(s["skill"])

    # --- 2. Bound equipment via the dashboard aggregator --------------------
    try:
        equipment = {e["id"]: e for e in get_json(f"{DASHBOARD_URL}/api/equipment")["equipment"]}
    except OSError as e:
        report("FAIL", f"dashboard API unreachable at {DASHBOARD_URL}: {e}")
        return finish()
    report("PASS", "dashboard API (executor host) reachable")

    for role in roles:
        eq_id = binding.get(role, role)  # unlisted roles resolve as equipment ids
        tag = f"{role} → {eq_id}"
        entry = equipment.get(eq_id)
        if entry is None:
            report("FAIL", f"{tag}: not in the equipment registry")
            continue
        if entry.get("fetch_error"):
            report("FAIL", f"{tag}: unreachable ({entry['fetch_error']})")
            continue
        status = entry.get("status") or {}
        state = status.get("equipment_status")
        if state == "ready":
            report("PASS", f"{tag}: ready (activity: {status.get('activity', 'unknown')})")
        else:
            report("FAIL", f"{tag}: equipment_status is {state!r}, need ready — "
                           f"message: {status.get('message')}")
        details = status.get("details") or {}
        claimed = details.get("claimed_by")
        if claimed:
            report("FAIL", f"{tag}: claim held by {claimed.get('owner')} "
                           f"(expires {claimed.get('expires_at')}) — per-step claims will 423")
        last_error = status.get("last_error")
        if last_error:
            report("WARN", f"{tag}: last_error present ({last_error.get('code')}: "
                           f"{last_error.get('message')})")

        allowed = set(status.get("allowed_actions") or [])
        missing = skills_by_role[role] - allowed
        if missing:
            report("FAIL", f"{tag}: skills not advertised right now: {sorted(missing)}")
        else:
            report("PASS", f"{tag}: all {len(skills_by_role[role])} package skills advertised")

        # OT-2-specific checks, when the gateway publishes robot details.
        robot = details.get("robot")
        if robot:
            if not robot.get("reachable"):
                report("FAIL", f"{tag}: gateway reports robot unreachable")
            if robot.get("run_active"):
                report("FAIL", f"{tag}: a robot run is already active")
            want = {(i["mount"], i["instrument_name"])
                    for s in steps if s["skill"] == "setup"
                    for i in s["args"].get("instruments", [])}
            have = {(i["mount"], i["name"]) for i in robot.get("instruments", [])}
            if want and not want <= have:
                report("FAIL", f"{tag}: instruments missing: {sorted(want - have)} "
                               f"(attached: {sorted(have)})")
            elif want:
                report("PASS", f"{tag}: instruments match ({', '.join(f'{m}: {n}' for m, n in sorted(want))})")
            mounted = details.get("mounted_tips")
            if mounted and any(mounted.values() if isinstance(mounted, dict) else [mounted]):
                report("WARN", f"{tag}: gateway thinks tips are mounted ({mounted}) — "
                               "drop them via the panel and tips.reset before the run")

    # --- 3. Record path + Slack trigger (best-effort, warn-only) ------------
    adb = equipment.get("bitacora_db")
    adb_state = ((adb or {}).get("status") or {}).get("equipment_status")
    if adb_state == "ready":
        report("PASS", "BitacoraDB ready (run record will file)")
    else:
        report("WARN", f"BitacoraDB is {adb_state!r} — record write is best-effort and "
                       "won't fail the run, but the milestone wants the record filed")
    try:
        active = subprocess.run(["systemctl", "is-active", "--quiet", "hermes-slack.service"],
                                check=False).returncode == 0
        report("PASS" if active else "WARN",
               "hermes-slack.service active (Slack trigger path)" if active
               else "hermes-slack.service not active — trigger from the dashboard instead")
    except FileNotFoundError:
        report("SKIP", "systemctl not found — cannot check the Slack connector")

    # --- 4. Bench checklist (derived from the package, not checkable here) --
    print("\n── Bench checklist (verify by hand at the robot) ──")
    multi = {i["nickname"] for s in steps if s["skill"] == "setup"
             for i in s["args"].get("instruments", []) if "multi" in i["instrument_name"]}
    for s in steps:
        if s["skill"] == "setup":
            for lw in s["args"].get("labware", []):
                print(f"  slot {lw['location']:>2}: {lw['loadname']}  ({lw['nickname']})")
    vols: dict[tuple[str, str], float] = defaultdict(float)
    channels: dict[tuple[str, str], bool] = {}
    for s in steps:
        if s["skill"] == "aspirate":
            loc = s["args"]["location"]
            key = (loc["labware_nickname"], loc["position"])
            vols[key] += s["args"]["volume_ul"]
            channels[key] = s["args"].get("pipette") in multi
    for (lw, pos), v in sorted(vols.items()):
        col = f" (multi-channel: the FULL column of {pos})" if channels[(lw, pos)] else ""
        print(f"  liquid: {lw} {pos}{col} — load ≥ {v + 50:.0f} µL (aspirates {v:.0f} µL + dead volume)")
    for s in steps:
        if s["skill"] == "pick_up_tip":
            col = " (multi-channel: full column)" if s["args"].get("pipette") in multi else ""
            print(f"  tips:   {s['args']['labware_nickname']} at {s['args']['position']}{col}")
    print("  also:  no module/labware squatting the slots above; door closed; camera on if recording")

    return finish()


def finish() -> int:
    fails = sum(1 for lvl, _ in RESULTS if lvl == "FAIL")
    warns = sum(1 for lvl, _ in RESULTS if lvl == "WARN")
    print(f"\n{'NOT READY' if fails else 'READY'}: {fails} fail, {warns} warn, "
          f"{sum(1 for lvl, _ in RESULTS if lvl == 'PASS')} pass")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
