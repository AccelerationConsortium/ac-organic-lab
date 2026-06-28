"""Tests for the email-code auth flow (request-code -> verify-code -> session)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ac_auth.config import Settings
from ac_auth.db import Db
from ac_auth.main import create_app


class FakeMailer:
    """Captures sent codes instead of emailing."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_login_code(self, to: str, code: str, *, ttl_minutes: int = 10) -> None:
        self.sent.append((to, code))

    async def aclose(self) -> None:
        pass


def _settings(tmp_path, **kw) -> Settings:
    base = dict(
        db_path=str(tmp_path / "t.db"),
        code_ttl_s=600,
        code_max_attempts=3,
        session_ttl_s=3600,
        cookie_name="ac_auth_session",
        cookie_secure=False,  # TestClient is http
    )
    base.update(kw)
    return Settings(**base)


def _ctx(tmp_path, users=(("alice@utoronto.ca", "user"),)):
    s = _settings(tmp_path)
    db = Db(s.db_path)
    for email, role in users:
        db.upsert_user(email, role=role)
    mailer = FakeMailer()
    app = create_app(settings=s, db=db, mailer=mailer)
    return app, db, mailer


def test_health(tmp_path):
    app, _, _ = _ctx(tmp_path)
    with TestClient(app) as c:
        assert c.get("/health").json()["status"] == "healthy"


def test_request_code_allowlisted(tmp_path):
    app, _, mailer = _ctx(tmp_path)
    with TestClient(app) as c:
        r = c.post("/auth/request-code", json={"email": "alice@utoronto.ca"})
    assert r.status_code == 202
    assert len(mailer.sent) == 1 and mailer.sent[0][0] == "alice@utoronto.ca"


def test_request_code_unknown_is_403(tmp_path):
    app, _, mailer = _ctx(tmp_path)
    with TestClient(app) as c:
        r = c.post("/auth/request-code", json={"email": "stranger@utoronto.ca"})
    assert r.status_code == 403 and mailer.sent == []


def test_request_code_disabled_is_403(tmp_path):
    app, db, mailer = _ctx(tmp_path)
    db.set_status("alice@utoronto.ca", "disabled")
    with TestClient(app) as c:
        r = c.post("/auth/request-code", json={"email": "alice@utoronto.ca"})
    assert r.status_code == 403 and mailer.sent == []


def test_full_login_flow_sets_session(tmp_path):
    app, _, mailer = _ctx(tmp_path, users=(("boss@utoronto.ca", "admin"),))
    with TestClient(app) as c:
        c.post("/auth/request-code", json={"email": "boss@utoronto.ca"})
        code = mailer.sent[-1][1]
        r = c.post("/auth/verify-code", json={"email": "boss@utoronto.ca", "code": code})
        assert r.status_code == 200 and r.json()["role"] == "admin"
        v = c.get("/auth/verify")  # cookie auto-sent by the client
        assert v.status_code == 200
        assert v.headers["X-Auth-User"] == "boss@utoronto.ca"
        assert v.headers["X-Auth-Role"] == "admin"
        me = c.get("/auth/me").json()
        assert me["authenticated"] is True and me["identity"]["email"] == "boss@utoronto.ca"


def test_verify_without_cookie_is_401(tmp_path):
    app, _, _ = _ctx(tmp_path)
    with TestClient(app) as c:
        assert c.get("/auth/verify").status_code == 401
        assert c.get("/auth/me").json()["authenticated"] is False


def test_wrong_code_401_then_correct_then_single_use(tmp_path):
    app, _, mailer = _ctx(tmp_path)
    with TestClient(app) as c:
        c.post("/auth/request-code", json={"email": "alice@utoronto.ca"})
        code = mailer.sent[-1][1]
        assert c.post("/auth/verify-code", json={"email": "alice@utoronto.ca", "code": "000000"}).status_code == 401
        assert c.post("/auth/verify-code", json={"email": "alice@utoronto.ca", "code": code}).status_code == 200
        # reuse of a burned code
        assert c.post("/auth/verify-code", json={"email": "alice@utoronto.ca", "code": code}).status_code == 401


def test_logout_revokes_session(tmp_path):
    app, _, mailer = _ctx(tmp_path)
    with TestClient(app) as c:
        c.post("/auth/request-code", json={"email": "alice@utoronto.ca"})
        code = mailer.sent[-1][1]
        c.post("/auth/verify-code", json={"email": "alice@utoronto.ca", "code": code})
        assert c.get("/auth/verify").status_code == 200
        c.post("/auth/logout")
        assert c.get("/auth/verify").status_code == 401


def test_users_lists_active_humans_only(tmp_path):
    """The login dropdown sees active humans, not automation accounts or disabled users."""
    app, db, _ = _ctx(tmp_path, users=(("alice@utoronto.ca", "user"), ("boss@utoronto.ca", "admin")))
    db.upsert_user("robot@lab.local", is_automation=True)
    db.upsert_user("gone@utoronto.ca", role="user")
    db.set_status("gone@utoronto.ca", "disabled")
    with TestClient(app) as c:
        r = c.get("/auth/users")
    assert r.status_code == 200
    by_email = {u["email"]: u["role"] for u in r.json()["users"]}
    assert by_email == {"alice@utoronto.ca": "user", "boss@utoronto.ca": "admin"}


def test_verify_with_api_key(tmp_path):
    """A machine principal authenticates at the same forward-auth edge via X-Api-Key."""
    app, db, _ = _ctx(tmp_path)
    db.upsert_user("robot@lab.local", is_automation=True)
    token = db.create_api_key("robot@lab.local", label="robot")
    with TestClient(app) as c:
        v = c.get("/auth/verify", headers={"X-Api-Key": token})
        assert v.status_code == 200
        assert v.headers["X-Auth-User"] == "robot@lab.local"
        # A bogus / revoked key is rejected.
        assert c.get("/auth/verify", headers={"X-Api-Key": "ak_nope"}).status_code == 401


def test_roster_projection_maps_roles(tmp_path):
    """The device-plane roster maps every active account through the resolver."""
    app, db, _ = _ctx(tmp_path, users=(("alice@utoronto.ca", "user"), ("boss@utoronto.ca", "admin")))
    db.upsert_user("robot@lab.local", is_automation=True)
    db.upsert_user("gone@utoronto.ca", role="user")
    db.set_status("gone@utoronto.ca", "disabled")
    with TestClient(app) as c:
        r = c.get("/equipment/agilent_uplc_ms/roster")
    assert r.status_code == 200
    body = r.json()
    assert body["equipment_key"] == "agilent_uplc_ms"
    by_owner = {e["owner"]: e["role"] for e in body["entries"]}
    assert by_owner == {
        "alice@utoronto.ca": "user",
        "boss@utoronto.ca": "service",
        "robot@lab.local": "automation",
    }
    assert "gone@utoronto.ca" not in by_owner  # disabled accounts are excluded
