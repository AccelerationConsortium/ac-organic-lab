#!/usr/bin/env python3
"""Stage a standalone dashboard build; human-only, dashboard-only cutover.

No instrument, relay, edge, environment, or systemd configuration is changed.
Runtime source stays in the installed checkout. This is not an API-code rollback
tool: the retained bundle can recover the web build, not earlier Python source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.request


REPO = Path(__file__).resolve().parents[1]
API = "ac-organic-lab-api.service"
WEB = "ac-organic-lab-web.service"
SOURCE_PATHS = (
    "web", "api/app", "skills/src", "auth/src", "equipment.yaml",
    "platforms.yaml", "locations.yaml", "pyproject.toml", "uv.lock",
    "api/pyproject.toml", "skills/pyproject.toml", "auth/pyproject.toml",
)


def run(*args: str, cwd: Path = REPO, **kwargs) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True, **kwargs).strip()


def source_hashes(repo: Path = REPO) -> dict[str, str]:
    paths = run("git", "ls-files", "--cached", "--others", "--exclude-standard",
                "-z", "--", *SOURCE_PATHS, cwd=repo).split("\0")
    result = {}
    for name in sorted(set(filter(None, paths))):
        path = repo / name
        if path.is_symlink():
            raise RuntimeError(f"Source symlink needs manual review: {name}")
        if path.is_file():
            result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def check_sources(expected: dict[str, str], repo: Path = REPO) -> None:
    actual = source_hashes(repo)
    changed = sorted(k for k in expected.keys() | actual.keys()
                     if expected.get(k) != actual.get(k))
    if changed:
        raise RuntimeError("Runtime source changed; restage before deploying: "
                           + ", ".join(changed))


def bundle_hashes(bundle: Path) -> dict[str, str]:
    # Exclude disposable build caches; include all server and client artifacts.
    return {
        str(p.relative_to(bundle)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(bundle.rglob("*"))
        if p.is_file() and "cache" not in p.relative_to(bundle).parts
    }


def build_id(bundle: Path) -> str:
    return (bundle / "BUILD_ID").read_text().strip()


def get(url: str) -> bytes:
    # Never inherit a shell's proxy settings for local readiness checks.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
        url, timeout=5
    ) as response:
        return response.read()


def stage(web_url: str) -> None:
    if os.geteuid() == 0:
        raise RuntimeError("Build as the service owner, not root")
    expected = source_hashes()
    digest = hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()
    run_dir = REPO / ".run"
    run_dir.mkdir(exist_ok=True)
    release = Path(tempfile.mkdtemp(prefix="dashboard-release-", dir=run_dir))
    print(f"Staging in {release}", flush=True)
    for name in expected:
        if name.startswith("web/"):
            target = release / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / name, target)
    subprocess.run(["cp", "-a", "--reflink=auto", str(REPO / "web/node_modules"),
                    str(release / "web/node_modules")], check=True)
    env = os.environ.copy()
    env.update({
        "GIT_COMMIT": run("git", "rev-parse", "--short", "HEAD") + "-src-" + digest[:12],
        "NEXT_TELEMETRY_DISABLED": "1",
        # Next 14 uses this value minus one for static build workers.
        "CIRCLE_NODE_TOTAL": "3",
        "NODE_OPTIONS": "--max-old-space-size=2048",
        "DASHBOARD_API_BASE": "http://127.0.0.1:8001",
    })
    subprocess.run(["/usr/bin/node", "node_modules/next/dist/bin/next", "build"],
                   cwd=release / "web", env=env, check=True)
    bundle = release / "web/.next"
    shutil.copytree(bundle / "static", bundle / "standalone/.next/static")
    if (release / "web/public").exists():
        shutil.copytree(release / "web/public", bundle / "standalone/public")
    check_sources(expected)
    manifest = {
        "source_hashes": expected,
        "bundle_hashes": bundle_hashes(bundle),
        "build_id": build_id(bundle),
        "previous_build_id": build_id(REPO / "web/.next"),
        "web_url": web_url.rstrip("/"),
    }
    (release / "manifest.local.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Staged build: {manifest['build_id']}\nRelease: {release}", flush=True)


def preflight(release: Path) -> dict:
    if release.parent != (REPO / ".run").resolve() or not release.name.startswith("dashboard-release-"):
        raise RuntimeError("Release must be a dashboard-release-* directory in this repo's .run")
    manifest = json.loads((release / "manifest.local.json").read_text())
    check_sources(manifest["source_hashes"])
    bundle = release / "web/.next"
    for required in ("standalone/server.js", "standalone/.next/BUILD_ID", "BUILD_ID"):
        if not (bundle / required).is_file():
            raise RuntimeError(f"Incomplete staged bundle: {required}")
    if bundle_hashes(bundle) != manifest["bundle_hashes"]:
        raise RuntimeError("Staged bundle changed since build")
    live = REPO / "web/.next"
    if live.is_symlink() or not live.is_dir():
        raise RuntimeError("Unexpected live bundle layout")
    if build_id(live) != manifest["previous_build_id"]:
        raise RuntimeError("Live build changed since staging; inspect before deploying")
    for service, directory in ((API, REPO / "api"), (WEB, live / "standalone")):
        actual = run("systemctl", "show", service, "-p", "WorkingDirectory", "--value")
        if Path(actual) != directory:
            raise RuntimeError(f"Unexpected WorkingDirectory for {service}")
        run("systemctl", "is-active", service)
    if json.loads(get("http://127.0.0.1:8001/api/health"))["status"] != "healthy":
        raise RuntimeError("Live API is not healthy")
    get(manifest["web_url"] + "/")
    print("Preflight passed. Targets: dashboard API and web only.", flush=True)
    return manifest


def wait_healthy(web_url: str) -> None:
    deadline = time.monotonic() + 45
    last_error = None
    while time.monotonic() < deadline:
        try:
            health = json.loads(get("http://127.0.0.1:8001/api/health"))
            if health.get("status") != "healthy":
                raise RuntimeError("API is not healthy")
            get(web_url + "/")
            print(f"Healthy: API ({health['equipment_count']} devices), web HTTP 200.")
            return
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"Readiness failed: {last_error}")


def apply(release: Path, manifest: dict) -> None:
    backup = Path(tempfile.mkdtemp(prefix="dashboard-rollback-", dir=REPO / ".run"))
    live = REPO / "web/.next"
    staged = release / "web/.next"
    moved_old = False
    moved_new = False
    try:
        # Stop consumers before renaming their running bundle. No build happens
        # in this outage window, and no non-dashboard services are restarted.
        subprocess.run(["systemctl", "stop", WEB, API], check=True, timeout=60)
        live.rename(backup / ".next")
        moved_old = True
        staged.rename(live)
        moved_new = True
        subprocess.run(["systemctl", "start", API, WEB], check=True, timeout=60)
        wait_healthy(manifest["web_url"])
    except Exception:
        if moved_old:
            subprocess.run(["systemctl", "stop", WEB], check=True, timeout=60)
            if moved_new:
                live.rename(staged)
            (backup / ".next").rename(live)
        subprocess.run(["systemctl", "start", API, WEB], check=True, timeout=60)
        print("Deployment failed. Previous web bundle restored if it was moved. "
              "Python source was NOT reverted; inspect API/web logs.", flush=True)
        raise
    (backup / "receipt.local.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Deployed {manifest['build_id']}\nPrevious web bundle retained: {backup / '.next'}")
    print("Camera leases/grants reset on API restart. Viewers may need one refresh.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--stage", action="store_true")
    modes.add_argument("--release", type=Path)
    parser.add_argument("--web-url", default="http://127.0.0.1:8000")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-no-active-workflow", action="store_true")
    args = parser.parse_args()
    if args.stage:
        if args.apply:
            parser.error("--apply requires --release")
        stage(args.web_url)
        return
    if args.apply and (os.geteuid() != 0 or not args.confirm_no_active_workflow):
        parser.error("Human cutover requires sudo and --confirm-no-active-workflow")
    release = args.release.resolve()
    manifest = preflight(release)
    if args.apply:
        apply(release, manifest)
    else:
        print("Dry run only; no services or live files changed.")


if __name__ == "__main__":
    main()
