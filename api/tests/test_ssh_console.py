"""Tests for the admin-only browser SSH console (``app.ssh_console``).

Two things are worth pinning down here, and they are the two that would hurt
if they regressed:

1. **Who may open a shell.** Admins with a verified session, nobody else —
   the module-level half of the rule whose web half lives in
   ``web/src/middleware.ts``.
2. **The ticket really is single-use and short-lived**, because it is the
   only credential the WebSocket ever sees (the upgrade cannot carry the
   middleware's identity injection — see the module docstring).

The PTY pump itself is exercised end-to-end against a real ``ssh`` process
only on the bench; here we cover the parts that are pure logic.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import ssh_console
from app.ssh_console import (
    HOSTS_BY_ID,
    SSH_HOSTS,
    _mint_ticket,
    _redeem_ticket,
    _ssh_argv,
    build_ssh_router,
)


def _default_profile(host_id: str):
    return HOSTS_BY_ID[host_id].profiles[0]


ADMIN = {"X-Auth-User": "admin@lab.ca", "X-Auth-Role": "admin"}
OPERATOR = {"X-Auth-User": "op@lab.ca", "X-Auth-Role": "operator"}


@pytest.fixture(autouse=True)
def _clean_ticket_store() -> Any:
    ssh_console._TICKETS.clear()
    ssh_console._ACTIVE.clear()
    yield
    ssh_console._TICKETS.clear()
    ssh_console._ACTIVE.clear()


def _client(db: Any = None) -> TestClient:
    app = FastAPI()
    app.state.db = db
    app.include_router(build_ssh_router())
    return TestClient(app)


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_hosts_requires_a_signed_in_admin() -> None:
    client = _client()
    assert client.get("/api/ssh/hosts").status_code == 401
    assert client.get("/api/ssh/hosts", headers=OPERATOR).status_code == 403
    assert client.get("/api/ssh/hosts", headers=ADMIN).status_code == 200


def test_session_refuses_a_non_admin() -> None:
    client = _client()
    body = {"host_id": "cytation-pc"}
    assert client.post("/api/ssh/session", json=body).status_code == 401
    assert client.post("/api/ssh/session", json=body, headers=OPERATOR).status_code == 403
    # ...and mints nothing on the way out.
    assert ssh_console._TICKETS == {}


def test_session_refuses_a_host_outside_the_whitelist() -> None:
    client = _client()
    resp = client.post(
        "/api/ssh/session", json={"host_id": "../root@evil"}, headers=ADMIN
    )
    assert resp.status_code == 404
    assert ssh_console._TICKETS == {}


def test_disabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SSH_CONSOLE_ENABLED", "false")
    client = _client()
    assert client.get("/api/ssh/hosts", headers=ADMIN).status_code == 404
    assert (
        client.post("/api/ssh/session", json={"host_id": "gaia"}, headers=ADMIN).status_code
        == 404
    )


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------


def test_session_mints_a_ticket_and_audits() -> None:
    db = MagicMock()
    client = _client(db)
    resp = client.post("/api/ssh/session", json={"host_id": "cytation-pc"}, headers=ADMIN)

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["host"]["id"] == "cytation-pc"
    assert payload["host"]["ssh_command"] == "ssh cytation-pc"
    # The page renders profile buttons from the banner; args stay server-side.
    profile_ids = [p["id"] for p in payload["host"]["profiles"]]
    assert profile_ids == ["cmd", "wsl", "wsl-tmux"]
    assert all("args" not in p for p in payload["host"]["profiles"])
    assert payload["profile"]["id"] == "cmd"  # omitted → the default
    assert len(payload["ticket"]) >= 32

    db.record_equipment_event.assert_called_once()
    args, kwargs = db.record_equipment_event.call_args
    assert args == ("cytation-pc", "ssh_session")
    assert kwargs["payload"]["user"] == "admin@lab.ca"
    assert kwargs["payload"]["outcome"] == "ticket_issued"
    assert kwargs["payload"]["profile"] == "cmd"


def test_session_accepts_a_named_profile_and_refuses_unknown_ones() -> None:
    client = _client()
    ok = client.post(
        "/api/ssh/session", json={"host_id": "gaia", "profile": "tmux"}, headers=ADMIN
    )
    assert ok.status_code == 200
    assert ok.json()["profile"]["id"] == "tmux"
    ticket = ssh_console._TICKETS[ok.json()["ticket"]]
    assert ticket.profile_id == "tmux"

    # A profile is a whitelist entry, not a command: anything not on the
    # host's list — including another host's valid profile — is refused.
    bad = client.post(
        "/api/ssh/session", json={"host_id": "gaia", "profile": "wsl"}, headers=ADMIN
    )
    assert bad.status_code == 404
    assert client.post(
        "/api/ssh/session",
        json={"host_id": "gaia", "profile": "tmux; rm -rf /"},
        headers=ADMIN,
    ).status_code == 404


def test_ticket_is_single_use() -> None:
    token = _mint_ticket("gaia", "admin@lab.ca", "shell")
    first = _redeem_ticket(token)
    assert first is not None and first.host_id == "gaia"
    # A replay of the same socket URL finds nothing.
    assert _redeem_ticket(token) is None


def test_ticket_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    token = _mint_ticket("gaia", "admin@lab.ca", "shell")
    ssh_console._TICKETS[token].issued_at = (
        time.monotonic() - ssh_console._TICKET_TTL_S - 1
    )
    assert _redeem_ticket(token) is None


def test_unknown_ticket_is_refused() -> None:
    assert _redeem_ticket("") is None
    assert _redeem_ticket("not-a-real-ticket") is None


# ---------------------------------------------------------------------------
# The whitelist and the argv it produces
# ---------------------------------------------------------------------------


def test_every_host_is_addressable_by_its_id() -> None:
    assert set(HOSTS_BY_ID) == {h.id for h in SSH_HOSTS}
    # These ids are the /utils/computers/ssh/<id> route the host tiles link to
    # (web/src/app/utils/computers/HostsPanel.tsx); keep both sides in step.
    assert {"gaia", "cytation-pc", "uplc-pc"} <= set(HOSTS_BY_ID)


def test_argv_never_prompts_and_never_learns_a_host_key() -> None:
    host = HOSTS_BY_ID["cytation-pc"]
    argv = _ssh_argv("/usr/bin/ssh", host, _default_profile("cytation-pc"))

    assert argv[0] == "/usr/bin/ssh"
    assert "-tt" in argv                       # a real PTY for xterm.js
    assert "BatchMode=yes" in argv             # no password prompt to nowhere
    assert "StrictHostKeyChecking=yes" in argv  # never trust-on-first-use
    # The target is an ssh_config alias, so the key file and login user stay
    # out of this app entirely; the default profile appends no remote command.
    assert argv[-1] == "cytation-pc"


def test_profile_args_ride_after_the_target() -> None:
    gaia = HOSTS_BY_ID["gaia"]
    tmux = gaia.profile("tmux")
    assert tmux is not None
    argv = _ssh_argv("/usr/bin/ssh", gaia, tmux)
    # Attach-or-create the shared console session, appended after the target.
    assert argv[-6:] == ["localhost", "tmux", "new-session", "-A", "-s", "console"]

    uplc = HOSTS_BY_ID["uplc-pc"]
    wsl = uplc.profile("wsl")
    assert wsl is not None
    assert _ssh_argv("/usr/bin/ssh", uplc, wsl)[-2:] == ["uplc-pc", "wsl.exe"]


def test_every_host_defaults_to_a_plain_shell_and_windows_offers_wsl() -> None:
    for host in SSH_HOSTS:
        assert host.profiles, host.id
        # First profile is the default: a bare login shell, no remote command.
        assert host.profiles[0].args == ()
        # Omitted / empty profile id resolves to that default.
        assert host.profile(None) is host.profiles[0]
        assert host.profile("") is host.profiles[0]
    assert HOSTS_BY_ID["gaia"].profile("tmux") is not None
    for win in ("cytation-pc", "uplc-pc"):
        assert HOSTS_BY_ID[win].profile("wsl") is not None
        assert HOSTS_BY_ID[win].profile("wsl-tmux") is not None


def test_public_banner_carries_no_credentials() -> None:
    import json

    for host in SSH_HOSTS:
        blob = json.dumps(host.public()).lower()
        assert "id_ed25519" not in blob
        assert "password" not in blob
