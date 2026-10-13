"""`/auth/verify?equipment=<key>` — the edge's per-device gate.

Device panels behind the edge (robot-motion's /ur5e and /ur3e, the xArm, the
OT-2 gateways) keep no operator list of their own; their forward_auth names the
equipment and this endpoint decides, with the same resolver as /authz/check.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from ac_auth.config import Settings
from ac_auth.db import Db
from ac_auth.main import create_app
from ac_auth.roster import Grant, Roster, RosterAutomation, RosterUser


class _FakeMailer:
    async def send_login_code(self, to, code, *, ttl_minutes=10):
        pass

    async def aclose(self):
        pass


def _app(tmp_path):
    settings = Settings(
        db_path=str(tmp_path / "t.db"),
        code_ttl_s=600,
        code_max_attempts=3,
        code_resend_cooldown_s=60,
        code_max_per_hour=5,
        session_ttl_s=3600,
        cookie_name="ac_auth_session",
        cookie_secure=False,
    )
    db = Db(settings.db_path)
    roster = Roster(
        users=[
            RosterUser(email="boss@utoronto.ca", role="admin"),
            RosterUser(
                email="arm.user@utoronto.ca",
                role="none",
                grants=[Grant(scope="platform", id="ligand", role="operator")],
            ),
            RosterUser(email="nobody@utoronto.ca", role="none"),
        ],
        automation=[
            RosterAutomation(
                email="agent:arm.user@utoronto.ca",
                approved=True,
                grants=[Grant(scope="platform", id="ligand", role="operator")],
            ),
            RosterAutomation(
                email="camera-bot@lab.local",
                approved=True,
                grants=[Grant(scope="equipment", id="cam_1", role="operator")],
            ),
        ],
    )
    app = create_app(settings=settings, db=db, mailer=_FakeMailer(), roster=roster)
    return app, db


def _verify(c, key, equipment=None):
    params = [] if equipment is None else [("equipment", e) for e in equipment]
    return c.get("/auth/verify", params=params, headers={"X-Api-Key": key})


def test_role_on_the_equipment_is_required(tmp_path):
    app, db = _app(tmp_path)
    agent = db.create_api_key("agent:arm.user@utoronto.ca", label="t")
    bot = db.create_api_key("camera-bot@lab.local", label="t")
    with TestClient(app) as c:
        app.state.membership = {"ur5e": {"ligand"}, "cam_1": {"other"}}
        ok = _verify(c, agent, ["ur5e"])
        assert ok.status_code == 200
        assert ok.headers["X-Auth-User"] == "agent:arm.user@utoronto.ca"
        assert _verify(c, bot, ["ur5e"]).status_code == 403
        assert _verify(c, bot, ["cam_1"]).status_code == 200


def test_without_equipment_verify_is_unchanged(tmp_path):
    """Panels that do not name an equipment keep plain logged-in semantics."""
    app, db = _app(tmp_path)
    bot = db.create_api_key("camera-bot@lab.local", label="t")
    with TestClient(app) as c:
        assert _verify(c, bot).status_code == 200


def test_unknown_equipment_fails_closed(tmp_path):
    app, db = _app(tmp_path)
    agent = db.create_api_key("agent:arm.user@utoronto.ca", label="t")
    with TestClient(app) as c:
        app.state.membership = {"ur5e": {"ligand"}}
        assert _verify(c, agent, ["ur5e_typo"]).status_code == 403


def test_a_second_equipment_value_is_refused(tmp_path):
    """A caller cannot append its own `equipment=` to pick the one it holds."""
    app, db = _app(tmp_path)
    bot = db.create_api_key("camera-bot@lab.local", label="t")
    with TestClient(app) as c:
        app.state.membership = {"ur5e": {"ligand"}}
        assert _verify(c, bot, ["ur5e", "cam_1"]).status_code == 403
        assert _verify(c, bot, ["cam_1", "cam_1"]).status_code == 403


def test_human_session_resolves_platform_grants(tmp_path):
    app, db = _app(tmp_path)
    with TestClient(app) as c:
        app.state.membership = {"ur5e": {"ligand"}}
        for email, expected in [
            ("arm.user@utoronto.ca", 200),
            ("boss@utoronto.ca", 200),
            ("nobody@utoronto.ca", 403),
        ]:
            c.cookies.clear()
            token = db.create_session(email, ttl_s=3600)
            c.cookies.set("ac_auth_session", token)
            r = c.get("/auth/verify", params={"equipment": "ur5e"})
            assert r.status_code == expected, email
