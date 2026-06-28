"""Interactive helper: set up the Gmail App Password and store it locally.

    python -m ac_auth.setup_gmail

Walks you through enabling 2-Step Verification + creating an App Password, then
writes the sender + App Password to a local, user-only credentials file
(``~/.ac_auth/credentials.env`` by default, or ``$AUTH_SECRETS_FILE``) that the
auth service reads at startup (see :mod:`ac_auth.config`). The password never
goes into the repo, shell history, or command-line args. Optionally sends a test
code so you can confirm delivery.

This is the ONLY email backend — Gmail. (No Microsoft/Outlook/Graph path.)
"""

from __future__ import annotations

import asyncio
import getpass
import os
import sys
import webbrowser

from .config import default_secrets_path

_APP_PW_URL = "https://myaccount.google.com/apppasswords"
_2SV_URL = "https://myaccount.google.com/signinoptions/two-step-verification"
_DEFAULT_FROM_NAME = "AC SDL2 Lab"


def _prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{label}{suffix}: ").strip()
    return val or default


def _write_credentials(path, user: str, password: str, from_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "# ac_auth Gmail credentials — DO NOT COMMIT. Written by ac_auth.setup_gmail.\n"
        f"AUTH_SMTP_USER={user}\n"
        f"AUTH_SMTP_PASSWORD={password}\n"
        f"AUTH_SMTP_FROM_NAME={from_name}\n"
    )
    # Create with owner-only perms where the OS honours it (POSIX); on Windows
    # the file inherits the user profile's ACL, which is already user-scoped.
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, body.encode("utf-8"))
    finally:
        os.close(fd)
    try:
        os.chmod(str(path), 0o600)
    except OSError:
        pass


def main() -> int:
    path = default_secrets_path()
    print("=== Gmail App Password setup for the AC SDL2 auth service ===\n")
    print("This stores a Gmail App Password locally so the service can send")
    print(f"sign-in codes. It will be written to:\n  {path}\n")

    if path.exists():
        if _prompt("Credentials file already exists. Overwrite? (y/N)", "n").lower() not in ("y", "yes"):
            print("Aborted; existing credentials left untouched.")
            return 0

    print("Steps in your browser:")
    print(f"  1. Turn ON 2-Step Verification:  {_2SV_URL}")
    print(f"  2. Create an App Password ('Mail'): {_APP_PW_URL}")
    print("     -> Google shows a 16-character code. You'll paste it below.\n")
    if _prompt("Open those pages in your browser now? (Y/n)", "y").lower() in ("y", "yes"):
        webbrowser.open(_2SV_URL)
        webbrowser.open(_APP_PW_URL)

    user = _prompt("\nGmail address (sender)")
    if "@" not in user:
        print("That doesn't look like an email address; aborting.")
        return 2
    from_name = _prompt("Display name (From)", _DEFAULT_FROM_NAME)
    password = getpass.getpass("Paste the 16-char App Password (hidden; spaces ok): ")
    password = password.replace(" ", "")
    if len(password) < 16:
        print("Warning: App Passwords are 16 characters — double-check what you pasted.")

    _write_credentials(path, user, from_name=from_name, password=password)
    print(f"\nSaved credentials to {path} (user-only).")
    print("The auth service will load these at startup automatically.\n")

    if _prompt("Send a test sign-in code now to confirm delivery? (Y/n)", "y").lower() in ("y", "yes"):
        recipient = _prompt("Send test code to", user)
        # Load what we just wrote, then send.
        os.environ.pop("AUTH_SMTP_USER", None)
        os.environ.pop("AUTH_SMTP_PASSWORD", None)
        os.environ.pop("AUTH_SMTP_FROM_NAME", None)
        from .config import load_secrets_file
        load_secrets_file(path)
        from .smtp_mailer import MailSendError, SmtpMailer, new_code

        async def _send() -> None:
            mailer = SmtpMailer.from_env()
            code = new_code()
            await mailer.send_login_code(recipient, code)
            print(f"SENT. Code {code} emailed to {recipient}. Check inbox AND Junk.")

        try:
            asyncio.run(_send())
        except MailSendError as exc:
            print(f"SEND FAILED: {exc}")
            return 1

    print("\nDone. Manage allow-listed users with: python -m ac_auth.cli add-user EMAIL --role admin")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
