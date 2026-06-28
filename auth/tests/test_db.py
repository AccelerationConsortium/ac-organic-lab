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


def test_automation_flag(tmp_path):
    db = _db(tmp_path)
    db.upsert_user("alice@x.com")                       # default human
    db.upsert_user("robot@lab.local", is_automation=True)
    assert db.get_user("alice@x.com").is_automation is False
    assert db.get_user("robot@lab.local").is_automation is True
    db.close()


def test_api_key_lifecycle(tmp_path):
    db = _db(tmp_path)
    db.upsert_user("robot@lab.local", is_automation=True)
    token = db.create_api_key("robot@lab.local", label="robot-2026")
    assert token.startswith("ak_")
    principal = db.verify_api_key(token)
    assert principal is not None and principal.email == "robot@lab.local"
    assert principal.is_automation is True
    assert db.verify_api_key("ak_bogus") is None

    keys = db.list_api_keys("robot@lab.local")
    assert len(keys) == 1 and keys[0].label == "robot-2026" and keys[0].revoked is False
    db.revoke_api_key(keys[0].id)
    assert db.verify_api_key(token) is None              # revoked → dead
    assert db.list_api_keys("robot@lab.local")[0].revoked is True
    db.close()


def test_api_key_expiry(tmp_path):
    db = _db(tmp_path)
    db.upsert_user("robot@lab.local", is_automation=True)
    live = db.create_api_key("robot@lab.local", ttl_s=3600)
    dead = db.create_api_key("robot@lab.local", ttl_s=-1)  # already expired
    assert db.verify_api_key(live) is not None
    assert db.verify_api_key(dead) is None
    db.close()


def test_list_users_active_only(tmp_path):
    db = _db(tmp_path)
    db.upsert_user("a@x.com")
    db.upsert_user("b@x.com")
    db.set_status("b@x.com", "disabled")
    assert {u.email for u in db.list_users(active_only=True)} == {"a@x.com"}
    assert {u.email for u in db.list_users()} == {"a@x.com", "b@x.com"}
    db.close()


def test_migration_adds_automation_column_when_absent(tmp_path):
    """A DB created before any automation column existed gains is_automation on open."""
    import sqlite3

    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """CREATE TABLE users (email TEXT PRIMARY KEY, role TEXT NOT NULL DEFAULT 'user',
                               status TEXT NOT NULL DEFAULT 'active', created_at REAL NOT NULL);
           INSERT INTO users (email, role, status, created_at) VALUES ('old@x.com','admin','active',0);"""
    )
    conn.commit()
    conn.close()

    db = Db(path)  # opening runs _migrate
    u = db.get_user("old@x.com")
    assert u is not None and u.role == "admin" and u.is_automation is False
    db.close()


def test_migration_renames_legacy_service_account_column(tmp_path):
    """A DB with the historical is_service_account column is renamed to
    is_automation in place, preserving the stored value (the deployed-server case)."""
    import sqlite3

    path = str(tmp_path / "had_sa.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """CREATE TABLE users (email TEXT PRIMARY KEY, role TEXT NOT NULL DEFAULT 'user',
                               status TEXT NOT NULL DEFAULT 'active',
                               is_service_account INTEGER NOT NULL DEFAULT 0,
                               created_at REAL NOT NULL);
           INSERT INTO users (email, role, status, is_service_account, created_at)
               VALUES ('robot@x.com','user','active',1,0);"""
    )
    conn.commit()
    conn.close()

    db = Db(path)  # opening runs _migrate → RENAME COLUMN
    cols = {r[1] for r in db._conn.execute("PRAGMA table_info(users)").fetchall()}
    assert "is_automation" in cols and "is_service_account" not in cols
    u = db.get_user("robot@x.com")
    assert u is not None and u.is_automation is True  # value preserved through rename
    db.close()
