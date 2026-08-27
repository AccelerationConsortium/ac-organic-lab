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
    assert len(payload["ticket"]) >= 32

    db.record_equipment_event.assert_called_once()
    args, kwargs = db.record_equipment_event.call_args
    assert args == ("cytation-pc", "ssh_session")
    assert kwargs["payload"]["user"] == "admin@lab.ca"
    assert kwargs["payload"]["outcome"] == "ticket_issued"


def test_ticket_is_single_use() -> None:
    token = _mint_ticket("gaia", "admin@lab.ca")
    first = _redeem_ticket(token)
    assert first is not None and first.host_id == "gaia"
    # A replay of the same socket URL finds nothing.
    assert _redeem_ticket(token) is None


def test_ticket_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    token = _mint_ticket("gaia", "admin@lab.ca")
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
    argv = _ssh_argv("/usr/bin/ssh", HOSTS_BY_ID["cytation-pc"])

    assert argv[0] == "/usr/bin/ssh"
    assert "-tt" in argv                       # a real PTY for xterm.js
    assert "BatchMode=yes" in argv             # no password prompt to nowhere
    assert "StrictHostKeyChecking=yes" in argv  # never trust-on-first-use
    # The target is an ssh_config alias, so the key file and login user stay
    # out of this app entirely.
    assert argv[-1] == "cytation-pc"


def test_public_banner_carries_no_credentials() -> None:
    for host in SSH_HOSTS:
        blob = " ".join(host.public().values()).lower()
        assert "id_ed25519" not in blob
        assert "password" not in blob
