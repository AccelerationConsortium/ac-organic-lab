"""Admin CLI for the auth allow-list. Run on the server (trust anchor = shell access).

    python -m ac_auth.cli add-user alice@utoronto.ca            # role user (default)
    python -m ac_auth.cli add-user boss@utoronto.ca --role admin  # first admin = bootstrap
    python -m ac_auth.cli list-users
    python -m ac_auth.cli disable-user alice@utoronto.ca
    python -m ac_auth.cli enable-user alice@utoronto.ca
    python -m ac_auth.cli delete-user alice@utoronto.ca

There is no separate "bootstrap-admin": the first ``add-user --role admin`` IS
the bootstrap. Authentication is passwordless email codes, so an admin just needs
to be on the allow-list — they then sign in by requesting a code like anyone else.
"""

from __future__ import annotations

import argparse
import sys

from .config import default_db_path, load_secrets_file
from .db import Db, norm_email


def _db() -> Db:
    load_secrets_file()  # allow AUTH_DB_PATH from the credentials file
    return Db(str(default_db_path()))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ac_auth.cli", description="Manage the auth allow-list.")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add-user", help="add or update an allow-listed user")
    a.add_argument("email")
    a.add_argument("--role", choices=("user", "admin"), default="user")

    sub.add_parser("list-users", help="list allow-listed users")
    for name in ("disable-user", "enable-user", "delete-user"):
        sp = sub.add_parser(name)
        sp.add_argument("email")

    args = p.parse_args(argv)
    db = _db()
    try:
        if args.cmd == "add-user":
            u = db.upsert_user(args.email, role=args.role)
            print(f"OK: {u.email} role={u.role} status={u.status}")
        elif args.cmd == "list-users":
            users = db.list_users()
            if not users:
                print("(no users yet — add one with: add-user EMAIL --role admin)")
            for u in users:
                print(f"  {u.email:40s} {u.role:6s} {u.status}")
        elif args.cmd == "disable-user":
            db.set_status(args.email, "disabled")
            print(f"OK: disabled {norm_email(args.email)}")
        elif args.cmd == "enable-user":
            db.set_status(args.email, "active")
            print(f"OK: enabled {norm_email(args.email)}")
        elif args.cmd == "delete-user":
            db.delete_user(args.email)
            print(f"OK: deleted {norm_email(args.email)}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
