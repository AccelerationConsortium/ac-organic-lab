"""Tests for the roster.yaml loader + validation + reload guards (Phase 0)."""

from __future__ import annotations

import pytest

from ac_auth.roster import (
    Roster,
    RosterError,
    RosterProject,
    RosterUser,
    dump_roster,
    load_roster,
    reload_roster,
)


# --- projects, PIs & membership (Phase 2) ----------------------------------


def test_project_helpers_and_pi_vs_member():
    r = Roster(
        users=[
            RosterUser(email="alice@lab.edu"),
            RosterUser(email="pi@lab.edu", role="none"),
        ],
        projects=[RosterProject(id="proj1", pis=["pi@lab.edu"], members=["alice@lab.edu"])],
    )
    assert r.pi_projects("pi@lab.edu") == {"proj1"}
    assert r.pi_projects("alice@lab.edu") == set()       # member, not owner
    assert r.member_projects("alice@lab.edu") == {"proj1"}
    assert r.member_projects("pi@lab.edu") == set()       # owner, not listed member
    assert r.project("proj1").pis == ["pi@lab.edu"]


def test_user_can_be_in_multiple_projects():
    r = Roster(
        users=[RosterUser(email="alice@lab.edu"), RosterUser(email="pi@lab.edu", role="admin")],
        projects=[
            RosterProject(id="p1", pis=["pi@lab.edu"], members=["alice@lab.edu"]),
            RosterProject(id="p2", pis=["pi@lab.edu"], members=["alice@lab.edu"]),
        ],
    )
    assert r.member_projects("alice@lab.edu") == {"p1", "p2"}  # no "leaving" needed


def test_closed_project_drops_members_but_keeps_owners():
    r = Roster(
        users=[RosterUser(email="alice@lab.edu"), RosterUser(email="pi@lab.edu", role="admin")],
        projects=[
            RosterProject(id="p1", status="closed", pis=["pi@lab.edu"], members=["alice@lab.edu"]),
        ],
    )
    assert r.member_projects("alice@lab.edu") == set()  # project closed → no member read
    assert r.pi_projects("pi@lab.edu") == {"p1"}         # owner still owns it


def test_multiple_pis_per_project():
    r = Roster(
        users=[RosterUser(email="p1@lab.edu", role="admin"), RosterUser(email="p2@lab.edu", role="none")],
        projects=[RosterProject(id="proj", pis=["p1@lab.edu", "p2@lab.edu"])],
    )
    assert r.pi_projects("p1@lab.edu") == {"proj"}
    assert r.pi_projects("p2@lab.edu") == {"proj"}


def test_project_requires_at_least_one_pi():
    with pytest.raises(ValueError):
        RosterProject(id="proj", members=["a@lab.edu"])


def test_project_references_must_be_known_users():
    with pytest.raises(ValueError):
        Roster(
            users=[RosterUser(email="a@lab.edu", role="admin")],
            projects=[RosterProject(id="proj", pis=["a@lab.edu"], members=["ghost@lab.edu"])],
        )


def test_duplicate_project_id_rejected():
    with pytest.raises(ValueError):
        Roster(
            users=[RosterUser(email="a@lab.edu", role="admin")],
            projects=[RosterProject(id="p", pis=["a@lab.edu"]), RosterProject(id="p", pis=["a@lab.edu"])],
        )


def _write(tmp_path, text: str):
    p = tmp_path / "roster.yaml"
    p.write_text(text, encoding="utf-8")
    return p


_GOOD = """
users:
  - email: Boss@Utoronto.CA
    role: admin
  - email: alice@utoronto.ca
    role: operator
    name: Alice Ng
    lab_account: AG group
  - email: legacy@utoronto.ca
    role: user            # legacy synonym for operator
automation:
  - email: hte-orchestrator@lab.local
    name: HTE principal
    approved: true
"""


def test_load_good_roster(tmp_path):
    r = load_roster(_write(tmp_path, _GOOD))
    by_email = {u.email: u for u in r.users}
    assert "boss@utoronto.ca" in by_email          # email normalised to lowercase
    assert by_email["boss@utoronto.ca"].role == "admin"
    assert by_email["legacy@utoronto.ca"].role == "operator"  # user -> operator
    assert by_email["alice@utoronto.ca"].name == "Alice Ng"
    assert len(r.automation) == 1 and r.automation[0].approved is True


def test_missing_file_is_error(tmp_path):
    with pytest.raises(RosterError):
        load_roster(tmp_path / "nope.yaml")


def test_no_active_admin_rejected(tmp_path):
    text = "users:\n  - {email: a@x.com, role: operator}\n"
    with pytest.raises(RosterError, match="no active admin"):
        load_roster(_write(tmp_path, text))


def test_role_none_loads_and_restricts(tmp_path):
    text = (
        "users:\n"
        "  - {email: admin@x.com, role: admin}\n"
        "  - email: felix@x.com\n"
        "    role: none\n"
        "    grants:\n"
        "      - {scope: equipment, id: ot2, role: operator}\n"
    )
    r = load_roster(_write(tmp_path, text))
    felix = next(u for u in r.users if u.email == "felix@x.com")
    assert felix.role == "none" and felix.grants[0].id == "ot2"


def test_global_admin_grant_satisfies_lockout(tmp_path):
    # the only admin is a role:none user holding a global admin grant — still valid
    text = (
        "users:\n"
        "  - email: boss@x.com\n"
        "    role: none\n"
        "    grants:\n"
        "      - {scope: global, role: admin}\n"
    )
    r = load_roster(_write(tmp_path, text))
    assert r.has_active_admin() is True


def test_platform_admin_grant_does_not_satisfy_lockout(tmp_path):
    # a platform admin is NOT a global admin → still a lockout
    text = (
        "users:\n"
        "  - email: p@x.com\n"
        "    role: none\n"
        "    grants:\n"
        "      - {scope: platform, id: hte, role: admin}\n"
    )
    with pytest.raises(RosterError, match="no active admin"):
        load_roster(_write(tmp_path, text))


def test_disabled_admin_does_not_count(tmp_path):
    text = (
        "users:\n"
        "  - {email: a@x.com, role: admin, status: disabled}\n"
        "  - {email: b@x.com, role: operator}\n"
    )
    with pytest.raises(RosterError, match="no active admin"):
        load_roster(_write(tmp_path, text))


def test_expired_admin_does_not_count(tmp_path):
    text = (
        "users:\n"
        "  - {email: a@x.com, role: admin, expires: 2000-01-01}\n"
        "  - {email: b@x.com, role: operator}\n"
    )
    with pytest.raises(RosterError, match="no active admin"):
        load_roster(_write(tmp_path, text))


def test_duplicate_email_rejected(tmp_path):
    text = (
        "users:\n"
        "  - {email: dup@x.com, role: admin}\n"
        "  - {email: DUP@x.com, role: operator}\n"
    )
    with pytest.raises(RosterError, match="duplicate email"):
        load_roster(_write(tmp_path, text))


def test_bad_email_rejected(tmp_path):
    text = "users:\n  - {email: notanemail, role: admin}\n"
    with pytest.raises(RosterError, match="invalid email"):
        load_roster(_write(tmp_path, text))


def test_bad_role_rejected(tmp_path):
    text = "users:\n  - {email: a@x.com, role: superuser}\n"
    with pytest.raises(RosterError):
        load_roster(_write(tmp_path, text))


def test_unknown_field_rejected(tmp_path):
    # extra="forbid" turns a typo'd key into a hard error, not a silent drop
    text = "users:\n  - {email: a@x.com, role: admin, rol: operator}\n"
    with pytest.raises(RosterError):
        load_roster(_write(tmp_path, text))


def test_grant_validation(tmp_path):
    base = "users:\n  - email: a@x.com\n    role: admin\n    grants:\n"
    # equipment + admin is illegal
    with pytest.raises(RosterError, match="admin is not allowed at equipment"):
        load_roster(_write(tmp_path, base + "      - {scope: equipment, id: ot2, role: admin}\n"))
    # platform grant needs an id
    with pytest.raises(RosterError, match="requires an id"):
        load_roster(_write(tmp_path, base + "      - {scope: platform, role: operator}\n"))
    # a valid equipment operator grant loads
    r = load_roster(_write(tmp_path, base + "      - {scope: equipment, id: ot2, role: operator}\n"))
    assert r.users[0].grants[0].id == "ot2"


def test_expiry_parsing_and_active(tmp_path):
    text = (
        "users:\n"
        "  - {email: boss@x.com, role: admin}\n"
        "  - {email: gone@x.com, role: operator, expires: 2000-01-01}\n"
        "  - {email: future@x.com, role: operator, expires: 2999-01-01}\n"
    )
    r = load_roster(_write(tmp_path, text))
    by = {u.email: u for u in r.users}
    assert by["gone@x.com"].is_expired is True and by["gone@x.com"].is_active is False
    assert by["future@x.com"].is_expired is False and by["future@x.com"].is_active is True


def test_roundtrip_dump_then_load(tmp_path):
    r = load_roster(_write(tmp_path, _GOOD))
    rt = tmp_path / "roundtrip.yaml"
    rt.write_text(dump_roster(r), encoding="utf-8")
    again = load_roster(rt)
    assert {u.email for u in again.users} == {u.email for u in r.users}
    assert {a.email for a in again.automation} == {a.email for a in r.automation}
    assert {u.email: u.role for u in again.users} == {u.email: u.role for u in r.users}


# ---- reload (keep-last-good + mass-change guard) --------------------------


def test_reload_keeps_last_good_on_invalid(tmp_path):
    good = load_roster(_write(tmp_path, _GOOD))
    bad = tmp_path / "bad.yaml"
    bad.write_text("users:\n  - {email: only-operator@x.com, role: operator}\n", encoding="utf-8")
    res = reload_roster(bad, good)
    assert res.applied is False
    assert res.roster is good                      # unchanged
    assert any("no active admin" in e for e in res.errors)


def test_reload_applies_valid_change(tmp_path):
    good = load_roster(_write(tmp_path, _GOOD))
    newf = tmp_path / "new.yaml"
    newf.write_text(
        "users:\n  - {email: boss@utoronto.ca, role: admin}\n"
        "  - {email: newbie@x.com, role: operator}\n",
        encoding="utf-8",
    )
    res = reload_roster(newf, good)
    assert res.applied is True
    assert "newbie@x.com" in {u.email for u in res.roster.users}


def test_reload_mass_change_guard(tmp_path):
    good = load_roster(_write(tmp_path, _GOOD))  # boss(admin)+alice+legacy+automation active
    # a new roster that drops everyone except the admin
    newf = tmp_path / "shrunk.yaml"
    newf.write_text("users:\n  - {email: boss@utoronto.ca, role: admin}\n", encoding="utf-8")
    blocked = reload_roster(newf, good, max_revocations=1)
    assert blocked.applied is False and blocked.roster is good
    assert any("would be" in e for e in blocked.errors)
    # force overrides the guard
    forced = reload_roster(newf, good, max_revocations=1, force=True)
    assert forced.applied is True


# --- cross_check_ids (referential integrity vs equipment/platform registries) --


def _xcheck_roster(**kw):
    from ac_auth.roster import Grant, Roster, RosterAutomation, RosterUser

    defaults = dict(
        users=[
            RosterUser(email="admin@lab.edu", role="admin"),
            RosterUser(
                email="u@lab.edu",
                role="none",
                grants=[
                    Grant(scope="equipment", id="plateloc", role="operator"),
                    Grant(scope="platform", id="hte", role="operator"),
                    Grant(scope="global", role="operator"),
                ],
            ),
        ],
        automation=[
            RosterAutomation(
                email="robot@lab.local",
                approved=True,
                platform="hte",
                grants=[Grant(scope="equipment", id="cam_x", role="operator")],
            )
        ],
    )
    defaults.update(kw)
    return Roster(**defaults)


def test_cross_check_all_resolving_is_clean():
    from ac_auth.roster import cross_check_ids

    r = _xcheck_roster()
    assert cross_check_ids(r, equipment_ids={"plateloc", "cam_x"}, platform_ids={"hte"}) == []


def test_cross_check_catches_dead_equipment_grant():
    """The la_agente_analitica class: a typo'd id is exact-match-dead."""
    from ac_auth.roster import cross_check_ids

    r = _xcheck_roster()
    errors = cross_check_ids(r, equipment_ids={"plate_loc", "cam_x"}, platform_ids={"hte"})
    assert len(errors) == 1
    assert "u@lab.edu" in errors[0] and "'plateloc'" in errors[0] and "dead grant" in errors[0]


def test_cross_check_catches_dead_platform_grant_and_scope():
    from ac_auth.roster import cross_check_ids

    r = _xcheck_roster()
    errors = cross_check_ids(r, equipment_ids={"plateloc", "cam_x"}, platform_ids={"complexation"})
    # u's platform grant AND the automation account's platform: field both die.
    assert len(errors) == 2
    assert any("u@lab.edu" in e and "'hte'" in e for e in errors)
    assert any("robot@lab.local" in e and "dead scope" in e for e in errors)


def test_cross_check_checks_automation_grants_too():
    from ac_auth.roster import cross_check_ids

    r = _xcheck_roster()
    errors = cross_check_ids(r, equipment_ids={"plateloc"}, platform_ids={"hte"})
    assert len(errors) == 1
    assert "robot@lab.local" in errors[0] and "'cam_x'" in errors[0]


def test_cross_check_none_skips_that_registry():
    """None = registry file unavailable; must not fail (distinct from empty set,
    which means the registry exists and the grant is genuinely dead)."""
    from ac_auth.roster import cross_check_ids

    r = _xcheck_roster()
    assert cross_check_ids(r, equipment_ids=None, platform_ids=None) == []
    assert cross_check_ids(r, equipment_ids=None, platform_ids={"hte"}) == []
    errors = cross_check_ids(r, equipment_ids=set(), platform_ids=None)
    assert len(errors) == 2  # both equipment grants dead against an empty registry


def test_cross_check_global_grants_are_exempt():
    """Global grants name no id — nothing to resolve."""
    from ac_auth.roster import Grant, Roster, RosterUser, cross_check_ids

    r = Roster(
        users=[
            RosterUser(email="admin@lab.edu", role="admin"),
            RosterUser(
                email="g@lab.edu", role="none", grants=[Grant(scope="global", role="admin")]
            ),
        ]
    )
    assert cross_check_ids(r, equipment_ids=set(), platform_ids=set()) == []
