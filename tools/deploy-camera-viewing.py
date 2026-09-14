#!/usr/bin/env python3
"""Checked local cutover. --check is read-only; --apply requires root and idle confirmation.

Preserves live Caddy routes rather than loading the repository's template.
Backups are retained. Never starts/stops/restarts any instrument service.
"""

import argparse
import copy
import datetime
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


def request(url, method="GET", data=None, headers=None):
    r = urllib.request.Request(url, method=method, data=data, headers=headers or {})
    with urllib.request.urlopen(r, timeout=20) as response:
        return response.read(), response.headers


def configure(value):
    """Replace only legacy camera routes, adding the authenticated broker beside them."""
    count = 0
    if isinstance(value, dict):
        for child in value.values():
            count += configure(child)
    elif isinstance(value, list):
        for item in list(value):
            if isinstance(item, dict) and item.get("match") == [
                {"path": ["/streams/*"]}
            ]:
                index = value.index(item)
                item["handle"] = [
                    {
                        "handler": "static_response",
                        "status_code": 410,
                        "body": "Legacy camera stream closed. Refresh and sign in.\n",
                    }
                ]
                value.insert(
                    index,
                    {
                        "match": [{"path": ["/api/camera-streams/*"]}],
                        "handle": [
                            {
                                "handler": "reverse_proxy",
                                "upstreams": [{"dial": "127.0.0.1:8001"}],
                            }
                        ],
                        "terminal": True,
                    },
                )
                count += 1
            else:
                count += configure(item)
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--confirm-no-active-workflow", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    stage = repo / ".run/camera-rollout"
    live_web = repo / "web/.next"
    staged_web = stage / "web/.next"
    uid = repo.stat().st_uid
    import pwd

    owner_home = Path(pwd.getpwuid(uid).pw_dir)
    relay = owner_home / ".local/bin/go2rtc"
    helper = owner_home / ".local/bin/codex"
    caddyfile = Path("/etc/caddy/Caddyfile")
    assert (staged_web / "standalone/server.js").is_file(), (
        "No staged production web build"
    )
    assert (live_web / "standalone/server.js").is_file(), "Unexpected live web layout"
    version = subprocess.check_output([str(stage / "go2rtc"), "-version"], text=True)
    assert "-lab-lease1" in version, "Staged relay lacks the lease-safe version marker"
    assert relay.is_file() and helper.is_file(), "Missing relay or apply-patch helper"
    original_file = caddyfile.read_text()
    pattern = re.compile(
        r"(?m)^(?P<indent>[ \t]*)handle_path /streams/\* \{\s*\n[ \t]*reverse_proxy 127\.0\.0\.1:1984\s*\n[ \t]*\}"
    )
    matches = list(pattern.finditer(original_file))
    assert matches, (
        "Installed Caddyfile has changed: review camera routing before deployment"
    )

    def replacement(m):
        p = m.group("indent")
        return (
            f'{p}handle /streams/* {{\n{p}\trespond "Legacy camera stream closed. Refresh and sign in." 410\n{p}}}\n'
            f"{p}handle /api/camera-streams/* {{\n{p}\treverse_proxy 127.0.0.1:8001\n{p}}}"
        )

    config_raw, config_headers = request("http://127.0.0.1:2019/config/")
    live_config = json.loads(config_raw)
    proposed = copy.deepcopy(live_config)
    count = configure(proposed)
    assert count >= 1, "No live legacy camera route found; stop for review"
    assert config_headers.get("ETag"), "Caddy concurrency guard unavailable"
    for service in ("ac-organic-lab-api", "ac-organic-lab-web"):
        working = subprocess.check_output(
            ["systemctl", "show", service, "-p", "WorkingDirectory", "--value"],
            text=True,
        ).strip()
        assert str(repo) in working, f"Unexpected service path for {service}"
    print(
        f"Preflight passed: {count} live camera routes; {len(matches)} persistent blocks; staged API/web/relay available."
    )
    print(
        "Targets: camera relay, dashboard API/web services, and camera-only routes in /etc/caddy/Caddyfile."
    )
    print(
        "Instrument services are not restarted. Existing browser video may need one refresh."
    )
    if not args.apply:
        print(
            "Read-only check complete. Use --apply --confirm-no-active-workflow under sudo to cut over."
        )
        return
    assert args.confirm_no_active_workflow, (
        "Confirm no dashboard-executed workflow is active before restarting its API"
    )
    assert os.geteuid() == 0, (
        "Administrator privileges required; run this script with sudo"
    )
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_file = caddyfile.with_name(caddyfile.name + ".pre-camera-viewing-" + stamp)
    backup_relay = relay.with_name(relay.name + ".pre-camera-viewing-" + stamp)
    backup_web = live_web.with_name(".next.old-camera-viewing-" + stamp)
    for path in (backup_file, backup_relay, backup_web):
        assert not path.exists(), f"Backup target already exists: {path}"
    shutil.copy2(caddyfile, backup_file)
    shutil.copy2(relay, backup_relay)
    # Install via a new inode; never overwrite a running executable in place.
    candidate = relay.with_name("go2rtc.camera-viewing-" + stamp)
    shutil.copy2(stage / "go2rtc", candidate)
    meta = relay.stat()
    os.chown(candidate, meta.st_uid, meta.st_gid)
    os.replace(candidate, relay)
    try:
        request("http://127.0.0.1:1984/api/restart", "POST", b"")
    except (OSError, urllib.error.URLError):
        pass  # exec can close the HTTP response; readiness below is authoritative.
    for _ in range(20):
        time.sleep(0.5)
        try:
            raw, _ = request("http://127.0.0.1:1984/api")
            if "-lab-lease1" in json.loads(raw).get("version", ""):
                break
        except (OSError, urllib.error.URLError):
            pass
    else:
        raise RuntimeError(
            f"Relay did not return; original binary retained at {backup_relay}"
        )
    subprocess.run(["systemctl", "restart", "ac-organic-lab-api"], check=True)
    for _ in range(20):
        time.sleep(0.5)
        try:
            request("http://127.0.0.1:8001/api/camera-streams/sessions")
        except urllib.error.HTTPError as e:
            if e.code == 401:
                break
        except (OSError, urllib.error.URLError):
            pass
    else:
        raise RuntimeError("New API not ready; web and live edge have not been changed")
    live_web.rename(backup_web)
    staged_web.rename(live_web)
    subprocess.run(["systemctl", "restart", "ac-organic-lab-web"], check=True)
    # Strict optimistic guard: never overwrite a Caddy change made during build/restart.
    assert caddyfile.read_text() == original_file, (
        "Caddyfile changed concurrently; finish edge deployment manually"
    )
    chunks = []
    for m in matches:
        chunks.append(
            "@@\n"
            + "\n".join("-" + line for line in m.group().splitlines())
            + "\n"
            + "\n".join("+" + line for line in replacement(m).splitlines())
        )
    patch = (
        "*** Begin Patch\n*** Update File: /etc/caddy/Caddyfile\n"
        + "\n".join(chunks)
        + "\n*** End Patch"
    )
    subprocess.run([str(helper), "--codex-run-as-apply-patch", patch], check=True)
    request(
        "http://127.0.0.1:2019/config/",
        "POST",
        json.dumps(proposed).encode(),
        {"Content-Type": "application/json", "If-Match": config_headers["ETag"]},
    )
    print(
        "Cutover complete. Camera routes only were replaced; unrelated live edge configuration was preserved."
    )
    print(f"Rollback copies: {backup_relay}, {backup_file}, {backup_web}")
    print(
        "Verify signed-in viewing, two tabs, lease expiry and instrument latency. Keep raw /streams/* blocked during rollback."
    )


if __name__ == "__main__":
    main()
