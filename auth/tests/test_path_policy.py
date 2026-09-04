"""Edge-path policy (Phase 2 of docs/HERMES_ACCESS_DESIGN.md).

Grants are service-level: a grant on ``analytica_db`` opens all 24 of its
routes. That is right for a human operator and wrong for a machine principal
that may read raw measurements but not the experiment design or the analysis
behind them. These tests pin the boundary and, more importantly, the ways it
could be walked around.
"""

from __future__ import annotations

import pytest

from ac_auth.authz import path_permitted
from ac_auth.roster import PathPolicy


# The policy the Hermes principal is intended to carry: raw data yes,
# scientific record no.
HERMES = PathPolicy(
    allow=[
        "/analytica/measurements",
        "/analytica/files",
        "/analytica/samples",
        "/analytica/health",
    ],
    deny=[
        "/analytica/projects",
        "/analytica/experiments",
        "/analytica/plans",
        "/analytica/analyses",
        "/analytica/analysis-files",
        "/analytica/notes",
        "/analytica/agent-run-graphs",
    ],
)


# --------------------------------------------------------------------------
# the boundary itself
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "uri",
    [
        "/analytica/measurements",
        "/analytica/measurements/42",
        "/analytica/measurements?sample_id=7",
        "/analytica/files/abc-123",
        "/analytica/samples",
    ],
)
def test_raw_data_is_allowed(uri):
    assert path_permitted(HERMES, uri) is True


@pytest.mark.parametrize(
    "uri",
    [
        "/analytica/projects",
        "/analytica/projects/proj-1",
        "/analytica/experiments/e-9",
        "/analytica/plans",
        "/analytica/plans/p-1/status",
        "/analytica/analyses/a-2",
        "/analytica/analysis-files",
        "/analytica/notes",
        "/analytica/agent-run-graphs/run-3",
    ],
)
def test_scientific_record_is_denied(uri):
    """Project details, background, design, analysis — the whole point."""
    assert path_permitted(HERMES, uri) is False


def test_bitacora_is_denied_by_default_not_by_rule():
    """bitácora is the design ELN end to end; nothing in it is on the allowed
    side. It needs no deny rule — an unlisted path is already refused."""
    assert path_permitted(HERMES, "/bitacora/projects/p1/rooms/r1/designs") is False
    assert path_permitted(HERMES, "/bitacora/whoami") is False


def test_unlisted_route_defaults_closed():
    """A route added to BitacoraDB tomorrow must not open itself."""
    assert path_permitted(HERMES, "/analytica/some-new-route") is False
    assert path_permitted(HERMES, "/dashboard") is False


def test_absent_policy_is_unrestricted():
    """Every human on the roster today has no paths block; nothing changes."""
    assert path_permitted(None, "/analytica/plans") is True
    assert path_permitted(None, "/anything/at/all") is True


# --------------------------------------------------------------------------
# evasion — the cases that decide whether this is a boundary or decoration
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "uri",
    [
        "/analytica/measurements/../plans",
        "/analytica/measurements/../../analytica/plans",
        "/analytica/measurements/%2e%2e/plans",
        "/analytica/measurements/%252e%252e/plans",
        "/analytica/measurements/./../plans",
    ],
)
def test_traversal_cannot_reach_a_denied_route(uri):
    """A prefix match on the raw string would let every one of these through."""
    assert path_permitted(HERMES, uri) is False


def test_percent_encoded_denied_path_is_still_denied():
    assert path_permitted(HERMES, "/analytica/%70lans") is False   # 'p'lans
    assert path_permitted(HERMES, "/%61nalytica/plans") is False   # 'a'nalytica


def test_query_string_cannot_smuggle_an_allowed_prefix():
    """Matching must ignore the query, or ?/analytica/measurements opens anything."""
    assert path_permitted(HERMES, "/analytica/plans?x=/analytica/measurements") is False


def test_deny_beats_allow_on_overlap():
    """An explicit deny must win even when an allow pattern also matches."""
    policy = PathPolicy(allow=["/analytica/*"], deny=["/analytica/plans"])
    assert path_permitted(policy, "/analytica/measurements") is True
    assert path_permitted(policy, "/analytica/plans") is False
    assert path_permitted(policy, "/analytica/plans/p1/status") is False


def test_prefix_allow_does_not_leak_to_a_sibling():
    """`/analytica/files` must not match `/analytica/files-secret`."""
    policy = PathPolicy(allow=["/analytica/files"])
    assert path_permitted(policy, "/analytica/files") is True
    assert path_permitted(policy, "/analytica/files/1") is True
    assert path_permitted(policy, "/analytica/files-secret") is False


def test_backslash_is_not_a_path_separator_escape():
    assert path_permitted(HERMES, "/analytica/measurements\\..\\plans") is False


# --------------------------------------------------------------------------
# model validation
# --------------------------------------------------------------------------

def test_policy_requires_at_least_one_pattern():
    """An empty block would silently deny everything — likelier a typo."""
    with pytest.raises(ValueError):
        PathPolicy()


def test_patterns_must_be_absolute():
    with pytest.raises(ValueError):
        PathPolicy(allow=["analytica/measurements"])


def test_glob_patterns_are_supported():
    policy = PathPolicy(allow=["/analytica/measurements*", "/lab/*/status"])
    assert path_permitted(policy, "/analytica/measurements") is True
    assert path_permitted(policy, "/analytica/measurements/1") is True
    assert path_permitted(policy, "/lab/ot2_hte/status") is True
    assert path_permitted(policy, "/lab/ot2_hte/control") is False


# --------------------------------------------------------------------------
# enforcement through /auth/verify — the predicate above is only half of it
# --------------------------------------------------------------------------

from fastapi.testclient import TestClient  # noqa: E402

from ac_auth.config import Settings  # noqa: E402
from ac_auth.db import Db  # noqa: E402
from ac_auth.main import create_app  # noqa: E402
from ac_auth.roster import Roster, RosterAutomation, RosterUser  # noqa: E402


class _FakeMailer:
    async def send_login_code(self, to, code, *, ttl_minutes=10):
        pass

    async def aclose(self):
        pass


def _agent_app(tmp_path, policy=HERMES):
    """An app whose only machine principal is path-scoped."""
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
        users=[RosterUser(email="alice@utoronto.ca", role="operator")],
        automation=[
            RosterAutomation(email="hermes@lab.local", approved=True, paths=policy)
        ],
    )
    app = create_app(settings=settings, db=db, mailer=_FakeMailer(), roster=roster)
    return app, db


def test_machine_principal_policy_is_actually_enforced(tmp_path):
    """Regression: machine principals live in `automation`, not `users`.

    Looking the policy up only in `users` returned None for every agent, so the
    boundary silently did not apply to the one population it exists for.
    """
    app, db = _agent_app(tmp_path)
    token = db.create_api_key("hermes@lab.local", label="hermes")
    with TestClient(app) as c:
        ok = c.get(
            "/auth/verify",
            headers={"X-Api-Key": token, "X-Forwarded-Uri": "/analytica/measurements"},
        )
        assert ok.status_code == 200
        assert ok.headers["X-Auth-User"] == "hermes@lab.local"

        denied = c.get(
            "/auth/verify",
            headers={"X-Api-Key": token, "X-Forwarded-Uri": "/analytica/plans"},
        )
        assert denied.status_code == 403


def test_missing_forwarded_uri_fails_closed(tmp_path):
    """If we cannot tell what is being authorized, refuse.

    Failing open would turn the policy into a suggestion: any caller reaching
    /auth/verify without the edge's header would be unrestricted.
    """
    app, db = _agent_app(tmp_path)
    token = db.create_api_key("hermes@lab.local", label="hermes")
    with TestClient(app) as c:
        assert c.get("/auth/verify", headers={"X-Api-Key": token}).status_code == 403


def test_unscoped_principal_is_unaffected_by_the_feature(tmp_path):
    """No paths block => today's behaviour, header or not."""
    app, db = _agent_app(tmp_path)
    token = db.create_api_key("hermes@lab.local", label="hermes")
    app.state.roster.automation[0].paths = None
    with TestClient(app) as c:
        assert c.get("/auth/verify", headers={"X-Api-Key": token}).status_code == 200
        assert c.get(
            "/auth/verify",
            headers={"X-Api-Key": token, "X-Forwarded-Uri": "/analytica/plans"},
        ).status_code == 200
