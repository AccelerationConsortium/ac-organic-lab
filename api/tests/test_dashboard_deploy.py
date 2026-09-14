"""Offline guards for the dashboard-only cutover helper."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture
def deploy():
    path = Path(__file__).resolve().parents[2] / "tools/deploy-dashboard.py"
    spec = importlib.util.spec_from_file_location("dashboard_deploy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_changed_or_added_runtime_source_refuses_cutover(deploy, monkeypatch):
    monkeypatch.setattr(deploy, "source_hashes", lambda repo: {"web/a.ts": "new", "api/app/b.py": "added"})
    with pytest.raises(RuntimeError, match="restage before deploying"):
        deploy.check_sources({"web/a.ts": "old"})


def test_bundle_hash_covers_server_and_client_but_not_cache(deploy, tmp_path):
    (tmp_path / "standalone").mkdir()
    (tmp_path / "standalone/server.js").write_text("server")
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache/example").write_text("disposable")
    assert set(deploy.bundle_hashes(tmp_path)) == {"standalone/server.js"}
    before = deploy.bundle_hashes(tmp_path)
    (tmp_path / "standalone/server.js").write_text("changed")
    assert deploy.bundle_hashes(tmp_path) != before


@pytest.mark.parametrize("root,confirmed", [(False, True), (True, False)])
def test_apply_requires_human_sudo_and_idle_confirmation(deploy, monkeypatch, root, confirmed):
    monkeypatch.setattr(deploy.os, "geteuid", lambda: 0 if root else 1000)
    args = ["deploy-dashboard.py", "--release", "/unused", "--apply"]
    if confirmed:
        args.append("--confirm-no-active-workflow")
    monkeypatch.setattr(sys, "argv", args)
    monkeypatch.setattr(deploy, "preflight", lambda *args: pytest.fail("must refuse before preflight"))
    with pytest.raises(SystemExit) as error:
        deploy.main()
    assert error.value.code == 2


def test_preflight_rejects_a_release_outside_repo_run(deploy, tmp_path):
    with pytest.raises(RuntimeError, match="directory in this repo"):
        deploy.preflight(tmp_path)


@pytest.fixture
def rig(deploy, tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "REPO", tmp_path)
    (tmp_path / ".run").mkdir()
    live = tmp_path / "web/.next"
    live.mkdir(parents=True)
    (live / "BUILD_ID").write_text("old")
    release = tmp_path / ".run/dashboard-release-test"
    staged = release / "web/.next"
    staged.mkdir(parents=True)
    (staged / "BUILD_ID").write_text("new")
    calls = []
    monkeypatch.setattr(deploy.subprocess, "run", lambda args, **kwargs: calls.append(args))
    monkeypatch.setattr(deploy, "wait_healthy", lambda url: None)
    return live, release, staged, calls


def test_cutover_only_touches_dashboard_and_retains_old_build(deploy, rig):
    live, release, staged, calls = rig
    manifest = {"web_url": "http://local.test", "build_id": "new"}
    deploy.apply(release, manifest)
    assert deploy.build_id(live) == "new"
    backups = list((deploy.REPO / ".run").glob("dashboard-rollback-*"))
    assert len(backups) == 1
    assert deploy.build_id(backups[0] / ".next") == "old"
    assert json.loads((backups[0] / "receipt.local.json").read_text()) == manifest
    assert calls == [
        ["systemctl", "stop", deploy.WEB, deploy.API],
        ["systemctl", "start", deploy.API, deploy.WEB],
    ]


def test_failed_readiness_restores_previous_web_and_preserves_failed_build(deploy, rig, monkeypatch):
    live, release, staged, calls = rig

    def failed(url):
        raise RuntimeError("readiness failed")

    monkeypatch.setattr(deploy, "wait_healthy", failed)
    with pytest.raises(RuntimeError, match="readiness failed"):
        deploy.apply(release, {"web_url": "http://local.test", "build_id": "new"})
    assert deploy.build_id(live) == "old"
    assert deploy.build_id(staged) == "new"
    assert calls[-2:] == [
        ["systemctl", "stop", deploy.WEB],
        ["systemctl", "start", deploy.API, deploy.WEB],
    ]


def test_stop_failure_does_not_move_bundle(deploy, rig, monkeypatch):
    live, release, staged, calls = rig

    def stop_fails(args, **kwargs):
        if "stop" in args:
            raise subprocess.CalledProcessError(1, args)
        calls.append(args)

    monkeypatch.setattr(deploy.subprocess, "run", stop_fails)
    with pytest.raises(subprocess.CalledProcessError):
        deploy.apply(release, {"web_url": "http://local.test", "build_id": "new"})
    assert deploy.build_id(live) == "old"
    assert deploy.build_id(staged) == "new"
