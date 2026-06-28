"""Send transactional email (one-time sign-in codes) via Gmail SMTP.

This is the sole email backend (no Microsoft/Outlook/Graph path). Credentials
come from env (typically loaded from the local credentials file written by
``python -m ac_auth.setup_gmail`` — see that module and :mod:`ac_auth.config`).

Gmail setup (one time)
----------------------
1. Turn on **2-Step Verification** for the sending Google account.
2. Generate an **App Password** (Google Account → Security → 2-Step Verification
   → App passwords → "Mail"). It's a 16-char token — use it as the password
   here, NOT the normal account password.

Then set the env vars in :meth:`SmtpMailer.from_env` and you can send.

Notes / caveats
---------------
- Defaults target Gmail (``smtp.gmail.com:587``, STARTTLS); host/port are
  configurable for any SMTP provider.
- Deliverability: a ``@gmail.com`` sender is well-authenticated (Google
  reputation) so codes usually reach `utoronto.ca` inboxes, but as cross-provider
  mail from a personal-looking address it can occasionally hit Junk / first-send
  quarantine. Set a ``from_name`` (e.g. "AC SDL2 Lab") so it reads as official;
  if new recipients miss codes, ask UofT IT to allow-list the sender.
- ``smtplib`` is blocking, so :meth:`send` runs it in a worker thread
  (``asyncio.to_thread``) to keep the FastAPI event loop responsive. No new deps.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from typing import Optional

logger = logging.getLogger("ac_auth.smtp_mailer")


class MailSendError(RuntimeError):
    """Raised when a code email could not be sent (auth, transport, or SMTP
    error). The login flow surfaces a retryable "couldn't send your code"
    rather than crashing — but a failed send is NOT silently swallowed; the
    user is waiting on that code."""


def new_code(digits: int = 6) -> str:
    """A cryptographically-random numeric one-time code (zero-padded).

    Generation only — storing it hashed/single-use/TTL-bounded and verifying it
    is the auth flow's job (see ``ac_auth.db``), not the mailer's.
    """
    return f"{secrets.randbelow(10 ** digits):0{digits}d}"


@dataclass(frozen=True)
class SmtpMailerConfig:
    """SMTP config (Gmail-shaped defaults)."""

    username: str                      # full email used to authenticate, e.g. ac.sdl.lab@gmail.com
    password: str                      # Gmail App Password (NOT the account password)
    sender: str = ""                   # From address; defaults to username
    from_name: Optional[str] = None    # display name, e.g. "AC SDL2 Lab"
    host: str = "smtp.gmail.com"
    port: int = 587                    # 587 = STARTTLS
    timeout_s: float = 30.0

    @property
    def from_addr(self) -> str:
        return self.sender or self.username


class SmtpMailer:
    """Sends mail over SMTP (Gmail by default). Same interface as ``GraphMailer``."""

    def __init__(self, config: SmtpMailerConfig) -> None:
        self._cfg = config

    @classmethod
    def from_env(cls) -> "SmtpMailer":
        """Build from env vars, raising a clear error if required ones are missing.

        Required: AUTH_SMTP_USER, AUTH_SMTP_PASSWORD (the Gmail App Password).
        Optional: AUTH_MAIL_SENDER (From; defaults to user), AUTH_SMTP_FROM_NAME,
        AUTH_SMTP_HOST (default smtp.gmail.com), AUTH_SMTP_PORT (default 587).
        """
        missing = [k for k in ("AUTH_SMTP_USER", "AUTH_SMTP_PASSWORD") if not os.environ.get(k)]
        if missing:
            raise MailSendError(f"Missing required env vars: {', '.join(missing)}")
        return cls(
            SmtpMailerConfig(
                username=os.environ["AUTH_SMTP_USER"],
                password=os.environ["AUTH_SMTP_PASSWORD"],
                sender=os.environ.get("AUTH_MAIL_SENDER", ""),
                from_name=os.environ.get("AUTH_SMTP_FROM_NAME") or None,
                host=os.environ.get("AUTH_SMTP_HOST", "smtp.gmail.com"),
                port=int(os.environ.get("AUTH_SMTP_PORT", "587")),
            )
        )

    def _build(self, to: str, subject: str, body: str, *, html: bool) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = formataddr((self._cfg.from_name, self._cfg.from_addr))
        msg["To"] = to
        msg["Subject"] = subject
        if html:
            msg.set_content("This message requires an HTML-capable client.")
            msg.add_alternative(body, subtype="html")
        else:
            msg.set_content(body)
        return msg

    def _send_sync(self, msg: EmailMessage) -> None:
        """Blocking SMTP send (STARTTLS). Runs in a worker thread via :meth:`send`."""
        try:
            with smtplib.SMTP(self._cfg.host, self._cfg.port, timeout=self._cfg.timeout_s) as s:
                s.ehlo()
                s.starttls(context=ssl.create_default_context())
                s.ehlo()
                s.login(self._cfg.username, self._cfg.password)
                s.send_message(msg)
        except smtplib.SMTPAuthenticationError as exc:
            raise MailSendError(
                f"SMTP auth failed ({exc.smtp_code}). For Gmail, the account needs "
                "2-Step Verification ON and you must use a 16-char App Password "
                "(not the normal password)."
            ) from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise MailSendError(f"SMTP send failed: {type(exc).__name__}: {exc}") from exc

    async def send(self, to: str, subject: str, body: str, *, html: bool = False) -> None:
        """Send one email. Raises :class:`MailSendError` on any failure."""
        msg = self._build(to, subject, body, html=html)
        await asyncio.to_thread(self._send_sync, msg)
        logger.info("sent mail to %s as %s (subject=%r)", to, self._cfg.from_addr, subject)

    async def send_login_code(self, to: str, code: str, *, ttl_minutes: int = 10) -> None:
        """Convenience: email a pre-generated one-time sign-in ``code`` (the auth
        flow generates / stores / verifies it; this only delivers)."""
        await self.send(
            to,
            subject="AC SDL dashboard - your sign-in code",
            body=(
                f"Your one-time sign-in code is: {code}\n\n"
                f"It expires in {ttl_minutes} minutes. "
                "If you didn't request it, you can ignore this email.\n"
            ),
        )

    async def aclose(self) -> None:
        """No persistent connection to close; present for interface parity."""
        return None


if __name__ == "__main__":
    # Standalone send test:
    #   AUTH_SMTP_USER=ac.sdl.lab@gmail.com AUTH_SMTP_FROM_NAME="AC SDL2 Lab" \
    #     python -m ac_auth.smtp_mailer recipient@utoronto.ca
    # The App Password is read from AUTH_SMTP_PASSWORD if set, else prompted
    # (so it never lands in shell history).
    import getpass
    import sys

    async def _main() -> int:
        recipient = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("AUTH_MAIL_TEST_TO")
        if not recipient:
            print("usage: python -m ac_auth.smtp_mailer <recipient-email>")
            return 2
        if not os.environ.get("AUTH_SMTP_USER"):
            print("set AUTH_SMTP_USER (the Gmail address) first")
            return 2
        if not os.environ.get("AUTH_SMTP_PASSWORD"):
            os.environ["AUTH_SMTP_PASSWORD"] = getpass.getpass(
                f"Gmail App Password for {os.environ['AUTH_SMTP_USER']}: "
            )
        mailer = SmtpMailer.from_env()
        code = new_code()
        try:
            await mailer.send_login_code(recipient, code)
        except MailSendError as exc:
            print(f"SEND FAILED: {exc}")
            return 1
        print(f"SENT. Code {code} emailed to {recipient}. Check inbox AND Junk.")
        return 0

    raise SystemExit(asyncio.run(_main()))
