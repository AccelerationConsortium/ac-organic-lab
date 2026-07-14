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
    # verify_api_key returns the principal's EMAIL (identity/approval resolves via
    # the roster); the api_keys table only proves possession of a live key.
    assert db.verify_api_key(token) == "robot@lab.local"
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


def test_profile_fields_and_upsert_preserves_them(tmp_path):
    db = _db(tmp_path)
    db.upsert_user("p@x.com", role="user")
    db.update_user("p@x.com", name="Pat Lee", lab_account="AG group", notes="intern",
                   expires_at=2_000_000_000.0)
    u = db.get_user("p@x.com")
    assert u.name == "Pat Lee" and u.lab_account == "AG group" and u.notes == "intern"
    assert u.expires_at == 2_000_000_000.0 and u.created_at is not None
    # re-adding (upsert) the same user must NOT wipe the profile columns
    db.upsert_user("p@x.com", role="admin")
    u2 = db.get_user("p@x.com")
    assert u2.role == "admin" and u2.name == "Pat Lee" and u2.lab_account == "AG group"
    # clearing expiry
    db.update_user("p@x.com", expires_at=None)
    assert db.get_user("p@x.com").expires_at is None
    db.close()


def test_expiry_and_disable_reason(tmp_path):
    db = _db(tmp_path)
    db.upsert_user("e@x.com")
    db.update_user("e@x.com", expires_at=1.0)              # long past
    assert db.get_user("e@x.com").is_expired() is True
    db.update_user("e@x.com", expires_at=9_999_999_999.0)  # far future
    assert db.get_user("e@x.com").is_expired() is False
    db.set_status("e@x.com", "disabled", reason="left the lab")
    u = db.get_user("e@x.com")
    assert u.status == "disabled" and u.disabled_reason == "left the lab" and u.disabled_at is not None
    db.set_status("e@x.com", "active")                     # re-enable clears reason
    u2 = db.get_user("e@x.com")
    assert u2.disabled_reason == "" and u2.disabled_at is None
    db.close()


def test_touch_login_stamps_and_verifies(tmp_path):
    db = _db(tmp_path)
    db.upsert_user("l@x.com")
    assert db.get_user("l@x.com").last_login_at is None
    assert db.get_user("l@x.com").email_verified is False
    db.touch_login("l@x.com")
    u = db.get_user("l@x.com")
    assert u.last_login_at is not None and u.email_verified is True
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


# ---------------------------------------------------------------------------
# auth_events audit log
# ---------------------------------------------------------------------------


def test_auth_events_record_and_list(tmp_path):
    db = _db(tmp_path)
    db.record_auth_event("code_requested", "A@X.com", ip="100.64.0.1", user_agent="curl")
    db.record_auth_event("login_success", "a@x.com", ip="100.64.0.1")
    db.record_auth_event("login_failed", "b@y.com", detail="invalid or expired code")
    db.record_auth_event("roster_reload_applied", detail="3 users")  # no email

    events = db.list_auth_events()
    assert [e.event for e in events] == [
        "roster_reload_applied", "login_failed", "login_success", "code_requested",
    ]  # newest first
    assert events[-1].email == "a@x.com"  # normalized
    assert events[-1].ip == "100.64.0.1"

    only_a = db.list_auth_events(email="A@X.com")
    assert {e.event for e in only_a} == {"code_requested", "login_success"}
    assert db.list_auth_events(limit=2)[0].event == "roster_reload_applied"
    db.close()


def test_auth_events_rejects_unknown_vocabulary(tmp_path):
    db = _db(tmp_path)
    try:
        db.record_auth_event("something_new", "a@x.com")
        raise AssertionError("expected ValueError for an event outside AUTH_EVENTS")
    except ValueError:
        pass
    db.close()


def test_auth_events_backfilled_from_sessions(tmp_path):
    """A DB that predates auth_events carries its login history as session
    rows; the first open with the new schema projects them into the log."""
    path = str(tmp_path / "t.db")
    db = Db(path)
    db.create_session("a@x.com", ttl_s=3600)
    db.create_session("b@y.com", ttl_s=3600)
    # simulate the pre-audit-log era: drop the events the schema just created
    with db._lock:
        db._conn.execute("DELETE FROM auth_events")
        db._conn.commit()
    db.close()

    db = Db(path)  # reopen -> _migrate backfills
    events = db.list_auth_events()
    assert len(events) == 2
    assert all(e.event == "login_success" for e in events)
    assert db.last_login_at("a@x.com") is not None
    db.close()


def test_api_key_last_used_stamped_on_verify(tmp_path):
    db = _db(tmp_path)
    token = db.create_api_key("robot@lab.local", label="robot")
    assert db.list_api_keys("robot@lab.local")[0].last_used_at is None
    assert db.verify_api_key(token) == "robot@lab.local"
    stamped = db.list_all_api_keys()[0].last_used_at
    assert stamped is not None
    db.close()


def test_purge_expired_keeps_live_state_and_history(tmp_path):
    db = _db(tmp_path)
    db.create_session("live@x.com", ttl_s=3600)
    db.create_session("stale@x.com", ttl_s=-8 * 86400)  # expired past the grace window
    db.create_login_code("live@x.com", "123456", 600)
    with db._lock:  # backdate a code beyond the grace window
        db._conn.execute(
            "UPDATE login_codes SET created_at = created_at - 10*86400 WHERE email='live@x.com'"
        )
        db._conn.commit()
    db.record_auth_event("login_success", "stale@x.com")

    sessions_purged, codes_purged = db.purge_expired(older_than_days=7)
    assert (sessions_purged, codes_purged) == (1, 1)
    assert [s.email for s in db.list_active_sessions()] == ["live@x.com"]
    # the audit log is untouched — it is the durable record
    assert db.last_login_at("stale@x.com") is not None
    db.close()
