"""Unit tests for the SQLite store (users, login codes, sessions)."""

from __future__ import annotations

from ac_auth.db import Db


def _db(tmp_path) -> Db:
    return Db(str(tmp_path / "t.db"))


def test_user_crud_and_email_normalization(tmp_path):
    db = _db(tmp_path)
    assert db.get_user("a@x.com") is None
    db.upsert_user("A@X.com", role="admin")
    u = db.get_user("a@x.com")  # case-insensitive lookup
    assert u is not None and u.role == "admin" and u.status == "active"
    db.set_status("a@x.com", "disabled")
    assert db.get_user("a@x.com").status == "disabled"
    assert [x.email for x in db.list_users()] == ["a@x.com"]
    db.delete_user("a@x.com")
    assert db.get_user("a@x.com") is None
    db.close()


def test_login_code_single_use(tmp_path):
    db = _db(tmp_path)
    db.create_login_code("a@x.com", "123456", 600)
    assert db.verify_login_code("a@x.com", "000000", 3) is False  # wrong (attempt 1)
    assert db.verify_login_code("a@x.com", "123456", 3) is True   # correct -> burned
    assert db.verify_login_code("a@x.com", "123456", 3) is False  # reuse -> burned
    db.close()


def test_login_code_attempts_exhausted(tmp_path):
    db = _db(tmp_path)
    db.create_login_code("a@x.com", "123456", 600)
    for _ in range(3):
        assert db.verify_login_code("a@x.com", "999999", 3) is False
    # attempts now == max -> even the correct code is refused
    assert db.verify_login_code("a@x.com", "123456", 3) is False
    db.close()


def test_login_code_expired(tmp_path):
    db = _db(tmp_path)
    db.create_login_code("a@x.com", "123456", -1)  # already expired
    assert db.verify_login_code("a@x.com", "123456", 3) is False
    db.close()


def test_new_code_invalidates_prior(tmp_path):
    db = _db(tmp_path)
    db.create_login_code("a@x.com", "111111", 600)
    db.create_login_code("a@x.com", "222222", 600)
    assert db.verify_login_code("a@x.com", "111111", 3) is False  # old one invalidated
    assert db.verify_login_code("a@x.com", "222222", 3) is True
    db.close()


def test_sessions(tmp_path):
    db = _db(tmp_path)
    tok = db.create_session("a@x.com", 3600)
    assert db.session_email(tok) == "a@x.com"
    assert db.session_email("bogus") is None
    db.revoke_session(tok)
    assert db.session_email(tok) is None
    assert db.session_email(db.create_session("b@x.com", -1)) is None  # expired
    db.close()
