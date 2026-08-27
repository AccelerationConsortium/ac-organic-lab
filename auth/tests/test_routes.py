"""Tests for the email-code auth flow (login -> verify-code -> session)."""

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
    us = []
    for spec in users:
        kw = dict(email=spec[0], role=spec[1], status=(spec[2] if len(spec) > 2 else "active"))
        if len(spec) > 3:  # optional display name (4th element)
            kw["name"] = spec[3]
        us.append(RosterUser(**kw))
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


def test_banner_js_served(tmp_path):
    """The shared banner asset is served ungated as JS and carries the
    window.labAuth host-page contract the panels consume."""
    app, _, _ = _ctx(tmp_path)
    with TestClient(app) as c:
        r = c.get("/auth/banner.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    assert r.headers.get("cache-control") == "no-store"
    body = r.text
    assert "window.labAuth" in body
    assert "labauth:change" in body
    assert "/auth/verify-code" in body  # login flow wired to the id-based endpoints


def test_request_code_allowlisted(tmp_path):
    app, _, mailer = _ctx(tmp_path)
    with TestClient(app) as c:
        r = c.post("/auth/login", json={"email": "alice@utoronto.ca"})
    assert r.status_code == 202
    assert len(mailer.sent) == 1 and mailer.sent[0][0] == "alice@utoronto.ca"


def test_request_code_unknown_is_403(tmp_path):
    app, _, mailer = _ctx(tmp_path)
    with TestClient(app) as c:
        r = c.post("/auth/login", json={"email": "stranger@utoronto.ca"})
    assert r.status_code == 403 and mailer.sent == []


def test_request_code_cooldown_429(tmp_path):
    """A second code request inside the cooldown is rejected with 429, and only
    the first email actually goes out (inbox-flood protection)."""
    app, _, mailer = _ctx(tmp_path)  # default 60s cooldown
    with TestClient(app) as c:
        assert c.post("/auth/login", json={"email": "alice@utoronto.ca"}).status_code == 202
        r = c.post("/auth/login", json={"email": "alice@utoronto.ca"})
    assert r.status_code == 429 and "retry-after" in {k.lower() for k in r.headers}
    assert len(mailer.sent) == 1


def test_request_code_hourly_cap_429(tmp_path):
    """With the cooldown disabled, the rolling-hour cap still bounds total sends."""
    app, _, mailer = _ctx(tmp_path, code_resend_cooldown_s=0, code_max_per_hour=2)
    with TestClient(app) as c:
        assert c.post("/auth/login", json={"email": "alice@utoronto.ca"}).status_code == 202
        assert c.post("/auth/login", json={"email": "alice@utoronto.ca"}).status_code == 202
        r = c.post("/auth/login", json={"email": "alice@utoronto.ca"})
    assert r.status_code == 429
    assert len(mailer.sent) == 2


def test_request_code_disabled_is_403(tmp_path):
    app, _, mailer = _ctx(tmp_path, users=(("alice@utoronto.ca", "operator", "disabled"),))
    with TestClient(app) as c:
        r = c.post("/auth/login", json={"email": "alice@utoronto.ca"})
    assert r.status_code == 403 and mailer.sent == []


def test_full_login_flow_sets_session(tmp_path):
    app, _, mailer = _ctx(tmp_path, users=(("boss@utoronto.ca", "admin"),))
    with TestClient(app) as c:
        c.post("/auth/login", json={"email": "boss@utoronto.ca"})
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


def test_verify_browser_navigation_redirects_to_login(tmp_path):
    """A logged-out *browser page* (Accept: text/html) behind the edge's
    forward_auth gets a 302 to the login page (Caddy copies the 3xx back on the
    deny path); API/XHR callers (*/* or JSON) still get 401 so the Next
    middleware's programmatic check is unchanged."""
    app, _, _ = _ctx(tmp_path)
    with TestClient(app) as c:
        r = c.get(
            "/auth/verify",
            headers={"accept": "text/html,application/xhtml+xml"},
            follow_redirects=False,
        )
        assert r.status_code == 302 and r.headers["location"] == "/"
        assert c.get("/auth/verify", headers={"accept": "*/*"}).status_code == 401
        assert c.get("/auth/verify", headers={"accept": "application/json"}).status_code == 401


def test_wrong_code_401_then_correct_then_single_use(tmp_path):
    app, _, mailer = _ctx(tmp_path)
    with TestClient(app) as c:
        c.post("/auth/login", json={"email": "alice@utoronto.ca"})
        code = mailer.sent[-1][1]
        assert c.post("/auth/verify-code", json={"email": "alice@utoronto.ca", "code": "000000"}).status_code == 401
        assert c.post("/auth/verify-code", json={"email": "alice@utoronto.ca", "code": code}).status_code == 200
        # reuse of a burned code
        assert c.post("/auth/verify-code", json={"email": "alice@utoronto.ca", "code": code}).status_code == 401


def test_logout_revokes_session(tmp_path):
    app, _, mailer = _ctx(tmp_path)
    with TestClient(app) as c:
        c.post("/auth/login", json={"email": "alice@utoronto.ca"})
        code = mailer.sent[-1][1]
        c.post("/auth/verify-code", json={"email": "alice@utoronto.ca", "code": code})
        assert c.get("/auth/verify").status_code == 200
        c.post("/auth/logout")
        assert c.get("/auth/verify").status_code == 401


def test_users_lists_active_humans_only(tmp_path):
    """The login dropdown sees active humans as {id, name, role} — never an
    email — and excludes automation accounts and disabled users."""
    from ac_auth.main import _login_id

    app, _, _ = _ctx(
        tmp_path,
        users=(
            ("alice@utoronto.ca", "operator", "active", "Alice A"),  # has a name
            ("boss@utoronto.ca", "admin"),                           # no name
            ("gone@utoronto.ca", "operator", "disabled"),
        ),
        automation=(("robot@lab.local", True),),
    )
    with TestClient(app) as c:
        r = c.get("/auth/users")
    assert r.status_code == 200
    entries = r.json()["users"]
    # Privacy: the payload never carries a raw email.
    assert all("email" not in u for u in entries)
    assert all({"id", "name", "role"} <= set(u) for u in entries)
    # Only active humans, keyed by opaque login id → wire role (operator == user).
    by_id = {u["id"]: u["role"] for u in entries}
    assert by_id == {
        _login_id("alice@utoronto.ca"): "user",
        _login_id("boss@utoronto.ca"): "admin",
    }
    by_id_name = {u["id"]: u["name"] for u in entries}
    # Named user shows its name; unnamed user falls back to a masked address.
    assert by_id_name[_login_id("alice@utoronto.ca")] == "Alice A"
    masked = by_id_name[_login_id("boss@utoronto.ca")]
    assert masked == "b…@utoronto.ca" and "boss@utoronto.ca" not in masked
    # Sorted by display name (case-insensitive): "Alice A" before "b…@…".
    assert [u["name"] for u in entries] == sorted((u["name"] for u in entries), key=str.lower)


def test_login_and_verify_by_opaque_id(tmp_path):
    """The dropdown flow: the client sends the opaque id (never an email); the
    sidecar resolves id -> email server-side for both /auth/login and
    /auth/verify-code. A bogus id is refused like an unknown account."""
    from ac_auth.main import _login_id

    app, _, mailer = _ctx(tmp_path, users=(("alice@utoronto.ca", "operator"),))
    uid = _login_id("alice@utoronto.ca")
    with TestClient(app) as c:
        assert c.post("/auth/login", json={"id": uid}).status_code == 202
        code = mailer.sent[-1][1]
        r = c.post("/auth/verify-code", json={"id": uid, "code": code})
        assert r.status_code == 200 and r.json()["role"] == "user"
        assert c.get("/auth/verify").status_code == 200
    # Unknown id resolves to no email → 422 (invalid account), nothing sent.
    with TestClient(app) as c:
        assert c.post("/auth/login", json={"id": "deadbeefdeadbeef"}).status_code == 422


def test_expired_account_cannot_request_or_verify(tmp_path):
    """An account past its expires_at is refused like a disabled one, and an
    existing session for it stops validating."""
    from datetime import date

    app, _, mailer = _ctx(tmp_path)
    # First, sign in while still valid and confirm the session works.
    with TestClient(app) as c:
        c.post("/auth/login", json={"email": "alice@utoronto.ca"})
        code = mailer.sent[-1][1]
        assert c.post("/auth/verify-code", json={"email": "alice@utoronto.ca", "code": code}).status_code == 200
        assert c.get("/auth/verify").status_code == 200
        # Now expire the account in the roster; login + the live session both fail.
        app.state.roster = Roster(
            users=[RosterUser(email="alice@utoronto.ca", role="operator", expires=date(2000, 1, 1))]
        )
        assert c.post("/auth/login", json={"email": "alice@utoronto.ca"}).status_code == 403
        assert c.get("/auth/verify").status_code == 401


def test_last_login_derived_from_sessions(tmp_path):
    app, db, mailer = _ctx(tmp_path)
    assert db.last_login_at("alice@utoronto.ca") is None
    with TestClient(app) as c:
        c.post("/auth/login", json={"email": "alice@utoronto.ca"})
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


def _login(c, mailer, email):
    """Drive the email-code flow; TestClient keeps the session cookie."""
    assert c.post("/auth/login", json={"email": email}).status_code == 202
    code = mailer.sent[-1][1]
    assert c.post("/auth/verify-code", json={"email": email, "code": code}).status_code == 200


def test_authz_matrix_requires_global_admin(tmp_path):
    """/authz/matrix: 401 anonymous; 403 for a non-admin — including a
    platform-scoped admin (platform-admin is a hardware role, not governance)."""
    from ac_auth.roster import Grant

    app, _, mailer = _ctx(
        tmp_path,
        users=(("alice@utoronto.ca", "operator"), ("boss@utoronto.ca", "admin")),
    )
    app.state.roster.users[0].grants.append(Grant(scope="platform", id="hte", role="admin"))
    app.state.membership = {"ot2": {"hte"}}
    with TestClient(app) as c:
        assert c.get("/authz/matrix").status_code == 401
        _login(c, mailer, "alice@utoronto.ca")
        assert c.get("/authz/matrix").status_code == 403


def test_authz_matrix_resolves_roles_for_admin(tmp_path):
    """An admin gets users × equipment with the same resolution as /authz/check."""
    from ac_auth.roster import Grant

    app, _, mailer = _ctx(
        tmp_path,
        users=(("alice@utoronto.ca", "operator"), ("boss@utoronto.ca", "admin")),
        automation=(("robot@lab.local", True),),
    )
    app.state.roster.users[0].grants.append(Grant(scope="platform", id="hte", role="admin"))
    app.state.membership = {"ot2": {"hte"}, "cytation_5": set()}
    with TestClient(app) as c:
        _login(c, mailer, "boss@utoronto.ca")
        r = c.get("/authz/matrix")
        assert r.status_code == 200
        body = r.json()
        assert body["equipment"] == ["cytation_5", "ot2"]
        rows = {u["email"]: u for u in body["users"]}
        # alice: platform-admin on hte -> service on ot2, plain user elsewhere
        assert rows["alice@utoronto.ca"]["kind"] == "human"
        assert rows["alice@utoronto.ca"]["roles"] == {"cytation_5": "user", "ot2": "service"}
        assert rows["boss@utoronto.ca"]["roles"] == {"cytation_5": "service", "ot2": "service"}
        assert rows["robot@lab.local"]["kind"] == "automation"
        assert rows["robot@lab.local"]["roles"] == {"cytation_5": "automation", "ot2": "automation"}


def test_authz_mine_lists_own_equipment_roles(tmp_path):
    """A restricted user sees their granted equipment (even off-membership)
    with role, and membership equipment they cannot reach as null."""
    from ac_auth.roster import Grant, Roster, RosterUser

    app, _, mailer = _ctx(tmp_path)
    app.state.roster = Roster(
        users=[
            RosterUser(email="boss@utoronto.ca", role="admin"),
            RosterUser(
                email="larry@utoronto.ca",
                role="none",
                grants=[Grant(scope="equipment", id="xarm_translocation", role="operator")],
            ),
        ]
    )
    app.state.membership = {"ot2": {"hte"}}  # xarm deliberately NOT in membership
    with TestClient(app) as c:
        assert c.get("/authz/mine").status_code == 401
        _login(c, mailer, "larry@utoronto.ca")
        body = c.get("/authz/mine").json()
        assert body["user"] == "larry@utoronto.ca" and body["role"] == "none"
        assert body["equipment"] == {"ot2": None, "xarm_translocation": "user"}


# ---------------------------------------------------------------------------
# auth_events stamping + /admin/* endpoints
# ---------------------------------------------------------------------------


def _signin(c: TestClient, mailer: FakeMailer, email: str) -> None:
    assert c.post("/auth/login", json={"email": email}).status_code == 202
    code = mailer.sent[-1][1]
    assert c.post("/auth/verify-code", json={"email": email, "code": code}).status_code == 200


def test_login_flow_records_auth_events(tmp_path):
    app, db, mailer = _ctx(tmp_path)
    with TestClient(app) as c:
        # unknown address -> login_rejected
        assert c.post("/auth/login", json={"email": "mallory@x.com"}).status_code == 403
        # real flow: code_requested -> login_failed (bad code) -> login_success -> logout
        assert c.post("/auth/login", json={"email": "alice@utoronto.ca"}).status_code == 202
        assert (
            c.post(
                "/auth/verify-code", json={"email": "alice@utoronto.ca", "code": "000000"}
            ).status_code
            == 401
        )
        code = mailer.sent[-1][1]
        assert (
            c.post(
                "/auth/verify-code", json={"email": "alice@utoronto.ca", "code": code}
            ).status_code
            == 200
        )
        assert c.post("/auth/logout").status_code == 200

        # read inside the client context — lifespan exit closes the DB
        events = {(e.event, e.email) for e in db.list_auth_events()}
        assert ("login_rejected", "mallory@x.com") in events
        assert ("code_requested", "alice@utoronto.ca") in events
        assert ("login_failed", "alice@utoronto.ca") in events
        assert ("login_success", "alice@utoronto.ca") in events
        assert ("logout", "alice@utoronto.ca") in events


def test_admin_endpoints_require_admin(tmp_path):
    app, _, mailer = _ctx(
        tmp_path,
        users=(("alice@utoronto.ca", "operator"), ("root@utoronto.ca", "admin")),
    )
    paths = [
        "/admin/accounts",
        "/admin/auth-events",
        "/admin/sessions",
        "/admin/api-keys",
        "/admin/state",
    ]
    with TestClient(app) as c:
        for p in paths:
            assert c.get(p).status_code == 401  # anonymous
        _signin(c, mailer, "alice@utoronto.ca")
        for p in paths:
            assert c.get(p).status_code == 403  # signed in, not admin
        # switch principals inside one client (lifespan exit closes the DB)
        assert c.post("/auth/logout").status_code == 200
        _signin(c, mailer, "root@utoronto.ca")
        for p in paths:
            assert c.get(p).status_code == 200  # admin


def test_overview_endpoints_any_signed_in_user_aggregates_only(tmp_path):
    app, _, mailer = _ctx(
        tmp_path,
        users=(("alice@utoronto.ca", "operator"), ("root@utoronto.ca", "admin")),
        automation=(("robot@lab.local", False),),  # pending approval
    )
    with TestClient(app) as c:
        # anonymous -> 401
        assert c.get("/overview/state").status_code == 401
        assert c.get("/overview/sessions").status_code == 401

        # a plain operator (NOT admin) may read both
        _signin(c, mailer, "alice@utoronto.ca")
        assert c.get("/overview/state").status_code == 200
        assert c.get("/overview/sessions").status_code == 200

        state = c.get("/overview/state").json()
        # aggregate roster counts only — no account listing, no pending/expiring emails
        assert state["roster"] == {
            "users": 2,
            "automation": 1,
            "projects": 0,
            "active_accounts": 2,  # robot is pending approval -> not active
        }
        assert "pending_automation" not in state
        assert "expiring_soon" not in state

        sessions = c.get("/overview/sessions").json()
        # summaries only — no email list
        assert set(sessions["live"]) == {"count", "accounts", "seconds"}
        assert sessions["live"]["count"] >= 1  # alice holds a live session
        assert sessions["live"]["accounts"] >= 1
        assert "total_time_s" in sessions
        # no list-of-dicts payloads anywhere in the response
        assert not any(isinstance(v, list) and v and isinstance(v[0], dict) for v in sessions.values())


def test_admin_accounts_and_state_shape(tmp_path):
    app, db, mailer = _ctx(
        tmp_path,
        users=(("root@utoronto.ca", "admin"), ("alice@utoronto.ca", "operator")),
        automation=(("robot@lab.local", False),),  # pending approval
    )
    with TestClient(app) as c:
        _signin(c, mailer, "root@utoronto.ca")

        accounts = c.get("/admin/accounts").json()
        by_email = {u["email"]: u for u in accounts["users"]}
        root = by_email["root@utoronto.ca"]
        assert root["last_login_at"] is not None  # the sign-in above
        assert root["active_sessions"] >= 1
        assert accounts["automation"][0]["approved"] is False

        state = c.get("/admin/state").json()
        assert state["roster"]["users"] == 2
        assert state["pending_automation"] == ["robot@lab.local"]
        assert state["last_reload"] is None  # no SIGHUP yet

        sessions = c.get("/admin/sessions").json()["sessions"]
        assert any(s["email"] == "root@utoronto.ca" for s in sessions)

        events = c.get("/admin/auth-events", params={"email": "root@utoronto.ca"}).json()
        assert any(e["event"] == "login_success" for e in events["events"])
