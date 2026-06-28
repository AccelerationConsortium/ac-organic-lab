"""Tests for the roster.yaml loader + validation + reload guards (Phase 0)."""

from __future__ import annotations

import pytest

from ac_auth.roster import (
    RosterError,
    dump_roster,
    load_roster,
    reload_roster,
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
