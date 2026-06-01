"""Tests for the auth sidecar endpoints (audit vs enforce modes)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ac_auth.identity import Identity
from ac_auth.main import app


class _FakeResolver:
    def __init__(self, ident: Identity | None) -> None:
        self._ident = ident
        self.seen: list[str] = []

    async def whois(self, addr: str) -> Identity | None:
        self.seen.append(addr)
        return self._ident

    async def aclose(self) -> None:
        pass


def _client(ident: Identity | None) -> TestClient:
    # Pre-seed the resolver; lifespan won't clobber a pre-set one.
    app.state.resolver = _FakeResolver(ident)
    return TestClient(app)


HUMAN = Identity(login="alice@github", display="Alice", node="alice-laptop", tags=(), addr="100.64.0.9")
TAGGED = Identity(
    login="sdl2-server-gaia.tail6a1dd7.ts.net", display="sdl2-server-gaia",
    node="gaia", tags=("tag:sdl2-devices",), addr="100.64.254.6",
)


# ---------------------------------------------------------------- audit mode

def test_health_reports_enforce_off(monkeypatch):
    monkeypatch.delenv("AUTH_ENFORCE", raising=False)
    with _client(None) as c:
        r = c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "healthy", "enforce": False}


def test_audit_allows_human_and_sets_identity_headers(monkeypatch):
    monkeypatch.delenv("AUTH_ENFORCE", raising=False)
    with _client(HUMAN) as c:
        r = c.get("/auth/verify", headers={"X-Forwarded-For": "100.64.0.9"})
    assert r.status_code == 200
    assert r.headers["X-Auth-User"] == "alice@github"
    assert r.headers["X-Auth-Tagged"] == "0"
    assert r.json()["identity"]["login"] == "alice@github"


def test_audit_allows_when_no_identity(monkeypatch):
    monkeypatch.delenv("AUTH_ENFORCE", raising=False)
    with _client(None) as c:
        r = c.get("/auth/verify", headers={"X-Forwarded-For": "100.64.0.9"})
    assert r.status_code == 200
    assert r.json()["identity"] is None
    assert "X-Auth-User" not in r.headers


def test_audit_allows_tagged_node(monkeypatch):
    monkeypatch.delenv("AUTH_ENFORCE", raising=False)
    with _client(TAGGED) as c:
        r = c.get("/auth/verify", headers={"X-Forwarded-For": "100.64.254.6"})
    assert r.status_code == 200
    assert r.headers["X-Auth-Tagged"] == "1"


def test_xff_first_hop_is_used(monkeypatch):
    monkeypatch.delenv("AUTH_ENFORCE", raising=False)
    resolver = _FakeResolver(HUMAN)
    app.state.resolver = resolver
    with TestClient(app) as c:
        c.get("/auth/verify", headers={"X-Forwarded-For": "100.64.0.9, 10.0.0.1"})
    assert resolver.seen == ["100.64.0.9"]


# ------------------------------------------------------------- enforce mode

def test_enforce_blocks_no_identity(monkeypatch):
    monkeypatch.setenv("AUTH_ENFORCE", "true")
    with _client(None) as c:
        r = c.get("/auth/verify", headers={"X-Forwarded-For": "100.64.0.9"})
    assert r.status_code == 401


def test_enforce_blocks_tagged_node(monkeypatch):
    monkeypatch.setenv("AUTH_ENFORCE", "true")
    with _client(TAGGED) as c:
        r = c.get("/auth/verify", headers={"X-Forwarded-For": "100.64.254.6"})
    assert r.status_code == 403


def test_enforce_allows_human(monkeypatch):
    monkeypatch.setenv("AUTH_ENFORCE", "true")
    with _client(HUMAN) as c:
        r = c.get("/auth/verify", headers={"X-Forwarded-For": "100.64.0.9"})
    assert r.status_code == 200
    assert r.headers["X-Auth-User"] == "alice@github"


# ------------------------------------------------------------------- /auth/me

def test_me_authenticated_and_anonymous(monkeypatch):
    monkeypatch.delenv("AUTH_ENFORCE", raising=False)
    with _client(HUMAN) as c:
        assert c.get("/auth/me", headers={"X-Forwarded-For": "100.64.0.9"}).json()["authenticated"] is True
    with _client(None) as c:
        assert c.get("/auth/me", headers={"X-Forwarded-For": "100.64.0.9"}).json()["authenticated"] is False
