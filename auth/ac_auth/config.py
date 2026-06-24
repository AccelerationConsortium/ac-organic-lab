"""Settings + credential loading for the ac_auth service.

Secrets (the Gmail App Password) live in a **local credentials file** written by
``python -m ac_auth.setup_gmail`` — never in the repo or env-on-the-command-line.
:func:`load_settings` loads that file into the process environment (without
overriding already-set vars, so real env wins), then builds :class:`Settings`.
:func:`build_mailer` returns the Gmail SMTP mailer (the only email backend).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Where the App Password (and sender) are stored locally. Override with
# AUTH_SECRETS_FILE; otherwise a per-user file outside the repo.
def default_secrets_path() -> Path:
    override = os.environ.get("AUTH_SECRETS_FILE")
    return Path(override) if override else Path.home() / ".ac_auth" / "credentials.env"


def default_db_path() -> Path:
    override = os.environ.get("AUTH_DB_PATH")
    return Path(override) if override else Path.home() / ".ac_auth" / "ac_auth.db"


def load_secrets_file(path: Path | None = None) -> bool:
    """Load ``KEY=VALUE`` lines from the local credentials file into
    ``os.environ`` (only for keys not already set). Missing file → no-op.
    Returns True if a file was read."""
    path = path or default_secrets_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val
    return True


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    db_path: str
    code_ttl_s: int          # one-time code lifetime
    code_max_attempts: int   # verify attempts before a code is burned
    session_ttl_s: int       # session cookie lifetime
    cookie_name: str
    cookie_secure: bool      # set False only for local http testing


def load_settings() -> Settings:
    load_secrets_file()
    return Settings(
        db_path=str(default_db_path()),
        code_ttl_s=_int("AUTH_CODE_TTL_S", 600),          # 10 min
        code_max_attempts=_int("AUTH_CODE_MAX_ATTEMPTS", 5),
        session_ttl_s=_int("AUTH_SESSION_TTL_S", 43200),  # 12 h
        cookie_name=os.environ.get("AUTH_COOKIE_NAME", "ac_auth_session"),
        cookie_secure=_bool("AUTH_COOKIE_SECURE", True),
    )


def build_mailer():
    """The Gmail SMTP mailer — the only email backend. Reads AUTH_SMTP_* from env
    (loaded from the credentials file by :func:`load_settings`)."""
    from .smtp_mailer import SmtpMailer

    return SmtpMailer.from_env()
