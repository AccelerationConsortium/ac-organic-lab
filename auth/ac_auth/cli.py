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

Machine principals (the robot/platform service account → device role ``hte``)
authenticate by API key, not email code:

    python -m ac_auth.cli add-service-account hte-robot@lab.local --label "HTE platform"
    python -m ac_auth.cli issue-key hte-robot@lab.local --label "robot-2026" --ttl-days 365
    python -m ac_auth.cli list-keys hte-robot@lab.local
    python -m ac_auth.cli revoke-key 3

``issue-key`` prints the secret **once** — store it where the platform reads it;
only its hash is kept.
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

    sa = sub.add_parser("add-service-account", help="add a machine principal (device role hte)")
    sa.add_argument("email", help="identifier for the service account, e.g. hte-robot@lab.local")
    sa.add_argument("--label", default="", help="human-readable note")

    ik = sub.add_parser("issue-key", help="issue an API key for a service account (prints once)")
    ik.add_argument("email")
    ik.add_argument("--label", default="", help="key label, e.g. robot-2026")
    ik.add_argument("--ttl-days", type=int, default=None, help="expiry in days (default: no expiry)")

    lk = sub.add_parser("list-keys", help="list a service account's API keys")
    lk.add_argument("email")

    rk = sub.add_parser("revoke-key", help="revoke an API key by id (see list-keys)")
    rk.add_argument("key_id", type=int)

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
                kind = "service" if u.is_service_account else u.role
                print(f"  {u.email:40s} {kind:8s} {u.status}")
        elif args.cmd == "disable-user":
            db.set_status(args.email, "disabled")
            print(f"OK: disabled {norm_email(args.email)}")
        elif args.cmd == "enable-user":
            db.set_status(args.email, "active")
            print(f"OK: enabled {norm_email(args.email)}")
        elif args.cmd == "delete-user":
            db.delete_user(args.email)
            print(f"OK: deleted {norm_email(args.email)}")
        elif args.cmd == "add-service-account":
            u = db.upsert_user(args.email, role="user", is_service_account=True)
            print(f"OK: service account {u.email} (device role: hte)")
            if args.label:
                print(f"     note: {args.label}")
            print("     issue a key with: issue-key " + u.email)
        elif args.cmd == "issue-key":
            user = db.get_user(args.email)
            if user is None or not user.is_service_account:
                print(f"ERROR: {norm_email(args.email)} is not a service account "
                      "(create it first: add-service-account EMAIL)", file=sys.stderr)
                return 2
            ttl_s = args.ttl_days * 86400 if args.ttl_days else None
            token = db.create_api_key(args.email, label=args.label, ttl_s=ttl_s)
            print(f"OK: key issued for {norm_email(args.email)}"
                  + (f" (expires in {args.ttl_days}d)" if args.ttl_days else " (no expiry)"))
            print("\n  " + token + "\n")
            print("  ^ store this now; it will NOT be shown again (only the hash is kept).")
        elif args.cmd == "list-keys":
            keys = db.list_api_keys(args.email)
            if not keys:
                print("(no keys — issue one with: issue-key EMAIL)")
            for k in keys:
                state = "revoked" if k.revoked else "active"
                exp = "never" if k.expires_at is None else f"{k.expires_at:.0f}"
                print(f"  id={k.id:<4d} {state:8s} label={k.label!r:24s} expires_at={exp}")
        elif args.cmd == "revoke-key":
            db.revoke_api_key(args.key_id)
            print(f"OK: revoked key id={args.key_id}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
