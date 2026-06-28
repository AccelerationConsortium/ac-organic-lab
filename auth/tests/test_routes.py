"""Tests for the email-code auth flow (request-code -> verify-code -> session)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ac_auth.config import Settings
from ac_auth.db import Db
from ac_auth.main import create_app
from ac_auth.roster import Roster, RosterAutomation, RosterUser


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
        code_resend_cooldown_s=60,
        code_max_per_hour=5,
        session_ttl_s=3600,
        cookie_name="ac_auth_session",
        cookie_secure=False,  # TestClient is http
    )
    base.update(kw)
    return Settings(**base)


def _roster(users=(("alice@utoronto.ca", "operator"),), automation=()) -> Roster:
    """Build a Roster fixture. `users` items are (email, role[, status]); the
    allow-list now lives in the roster, not the DB."""
    us = [
        RosterUser(email=spec[0], role=spec[1], status=(spec[2] if len(spec) > 2 else "active"))
        for spec in users
    ]
    au = [RosterAutomation(email=e, approved=approved) for (e, approved) in automation]
    return Roster(users=us, automation=au)


def _ctx(tmp_path, users=(("alice@utoronto.ca", "operator"),), automation=(), **settings_kw):
    s = _settings(tmp_path, **settings_kw)
    db = Db(s.db_path)
    mailer = FakeMailer()
    roster = _roster(users, automation)
    app = create_app(settings=s, db=db, mailer=mailer, roster=roster)
    # roster is reachable via app.state.roster for tests that mutate it mid-run
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


def test_request_code_cooldown_429(tmp_path):
    """A second code request inside the cooldown is rejected with 429, and only
    the first email actually goes out (inbox-flood protection)."""
    app, _, mailer = _ctx(tmp_path)  # default 60s cooldown
    with TestClient(app) as c:
        assert c.post("/auth/request-code", json={"email": "alice@utoronto.ca"}).status_code == 202
        r = c.post("/auth/request-code", json={"email": "alice@utoronto.ca"})
    assert r.status_code == 429 and "retry-after" in {k.lower() for k in r.headers}
    assert len(mailer.sent) == 1


def test_request_code_hourly_cap_429(tmp_path):
    """With the cooldown disabled, the rolling-hour cap still bounds total sends."""
    app, _, mailer = _ctx(tmp_path, code_resend_cooldown_s=0, code_max_per_hour=2)
    with TestClient(app) as c:
        assert c.post("/auth/request-code", json={"email": "alice@utoronto.ca"}).status_code == 202
        assert c.post("/auth/request-code", json={"email": "alice@utoronto.ca"}).status_code == 202
        r = c.post("/auth/request-code", json={"email": "alice@utoronto.ca"})
    assert r.status_code == 429
    assert len(mailer.sent) == 2


def test_request_code_disabled_is_403(tmp_path):
    app, _, mailer = _ctx(tmp_path, users=(("alice@utoronto.ca", "operator", "disabled"),))
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
    app, _, _ = _ctx(
        tmp_path,
        users=(
            ("alice@utoronto.ca", "operator"),
            ("boss@utoronto.ca", "admin"),
            ("gone@utoronto.ca", "operator", "disabled"),
        ),
        automation=(("robot@lab.local", True),),
    )
    with TestClient(app) as c:
        r = c.get("/auth/users")
    assert r.status_code == 200
    by_email = {u["email"]: u["role"] for u in r.json()["users"]}
    # operator maps to the wire value "user"
    assert by_email == {"alice@utoronto.ca": "user", "boss@utoronto.ca": "admin"}


def test_expired_account_cannot_request_or_verify(tmp_path):
    """An account past its expires_at is refused like a disabled one, and an
    existing session for it stops validating."""
    from datetime import date

    app, _, mailer = _ctx(tmp_path)
    # First, sign in while still valid and confirm the session works.
    with TestClient(app) as c:
        c.post("/auth/request-code", json={"email": "alice@utoronto.ca"})
        code = mailer.sent[-1][1]
        assert c.post("/auth/verify-code", json={"email": "alice@utoronto.ca", "code": code}).status_code == 200
        assert c.get("/auth/verify").status_code == 200
        # Now expire the account in the roster; request-code + the live session both fail.
        app.state.roster = Roster(
            users=[RosterUser(email="alice@utoronto.ca", role="operator", expires=date(2000, 1, 1))]
        )
        assert c.post("/auth/request-code", json={"email": "alice@utoronto.ca"}).status_code == 403
        assert c.get("/auth/verify").status_code == 401


def test_last_login_derived_from_sessions(tmp_path):
    app, db, mailer = _ctx(tmp_path)
    assert db.last_login_at("alice@utoronto.ca") is None
    with TestClient(app) as c:
        c.post("/auth/request-code", json={"email": "alice@utoronto.ca"})
        code = mailer.sent[-1][1]
        c.post("/auth/verify-code", json={"email": "alice@utoronto.ca", "code": code})
        # the session row created on login IS the last-login record
        assert db.last_login_at("alice@utoronto.ca") is not None


def test_verify_with_api_key(tmp_path):
    """A machine principal authenticates at the same forward-auth edge via X-Api-Key."""
    app, db, _ = _ctx(tmp_path, automation=(("robot@lab.local", True),))
    token = db.create_api_key("robot@lab.local", label="robot")
    with TestClient(app) as c:
        v = c.get("/auth/verify", headers={"X-Api-Key": token})
        assert v.status_code == 200
        assert v.headers["X-Auth-User"] == "robot@lab.local"
        # A bogus / revoked key is rejected.
        assert c.get("/auth/verify", headers={"X-Api-Key": "ak_nope"}).status_code == 401


def test_unapproved_automation_key_rejected(tmp_path):
    """A key for an automation account that isn't approved in the roster is denied."""
    app, db, _ = _ctx(tmp_path, automation=(("robot@lab.local", False),))
    token = db.create_api_key("robot@lab.local", label="robot")
    with TestClient(app) as c:
        assert c.get("/auth/verify", headers={"X-Api-Key": token}).status_code == 401


def test_authz_check_resolves_grants(tmp_path):
    """/authz/check returns the effective device role, honoring platform grants."""
    from ac_auth.roster import Grant

    app, _, _ = _ctx(
        tmp_path,
        users=(("alice@utoronto.ca", "operator"), ("boss@utoronto.ca", "admin")),
    )
    # give alice a platform-admin grant on hte, and wire ot2 into hte membership
    app.state.roster.users[0].grants.append(Grant(scope="platform", id="hte", role="admin"))
    app.state.membership = {"ot2": {"hte"}}
    with TestClient(app) as c:
        # alice is admin→service on ot2 (via the hte platform grant)...
        r = c.get("/authz/check", params={"user": "alice@utoronto.ca", "equipment": "ot2"})
        assert r.status_code == 200
        body = r.json()
        assert body == {
            "user": "alice@utoronto.ca",
            "equipment": "ot2",
            "allowed": True,
            "role": "service",
            "central_role": "admin",
        }
        # ...but only operator→user on a device outside hte
        out = c.get("/authz/check", params={"user": "alice@utoronto.ca", "equipment": "elsewhere"}).json()
        assert out["role"] == "user" and out["central_role"] == "operator"
        # an unknown user is not allowed
        unk = c.get("/authz/check", params={"user": "stranger@x.com", "equipment": "ot2"}).json()
        assert unk["allowed"] is False and unk["role"] is None


def test_role_none_restricted_to_granted_equipment(tmp_path):
    """A role:none user appears on a device roster only where a grant reaches it,
    and /authz/check denies the rest."""
    from ac_auth.roster import Grant, Roster, RosterUser

    app, _, _ = _ctx(tmp_path)  # alice operator (keeps the lockout admin elsewhere)
    app.state.roster = Roster(
        users=[
            RosterUser(email="boss@utoronto.ca", role="admin"),
            RosterUser(
                email="felix@utoronto.ca",
                role="none",
                grants=[Grant(scope="equipment", id="ot2", role="operator")],
            ),
        ]
    )
    app.state.membership = {}
    with TestClient(app) as c:
        # felix is on the ot2 roster (operator→user)...
        ot2 = {e["owner"]: e["role"] for e in c.get("/equipment/ot2/roster").json()["entries"]}
        assert ot2 == {"boss@utoronto.ca": "service", "felix@utoronto.ca": "user"}
        # ...but NOT on a device he wasn't granted
        other = {e["owner"]: e["role"] for e in c.get("/equipment/cytation_5/roster").json()["entries"]}
        assert other == {"boss@utoronto.ca": "service"}  # felix excluded
        # /authz/check agrees
        assert c.get("/authz/check", params={"user": "felix@utoronto.ca", "equipment": "ot2"}).json()["allowed"] is True
        denied = c.get("/authz/check", params={"user": "felix@utoronto.ca", "equipment": "cytation_5"}).json()
        assert denied["allowed"] is False and denied["role"] is None


def test_roster_projection_maps_roles(tmp_path):
    """The device-plane roster maps every active account through the resolver."""
    app, _, _ = _ctx(
        tmp_path,
        users=(
            ("alice@utoronto.ca", "operator"),
            ("boss@utoronto.ca", "admin"),
            ("gone@utoronto.ca", "operator", "disabled"),
        ),
        automation=(("robot@lab.local", True),),
    )
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
