"""Admin CLI for the auth sidecar. Run on the server (trust anchor = shell access).

Phase 0: the **allow-list lives in `roster.yaml`**, not the DB. Manage accounts by
editing that file, then validating and reloading:

    cp roster.yaml.example roster.yaml          # first time
    $EDITOR roster.yaml                          # add/change an entry
    python -m ac_auth.cli validate roster.yaml  # schema + invariants (>=1 admin, ...)
    sudo systemctl reload ac-organic-lab-auth   # keep-last-good on failure

Inspect / migrate:

    python -m ac_auth.cli list-users            # read the live roster (+ last login)
    python -m ac_auth.cli export -o roster.yaml # dump the legacy DB table (migration aid)

Machine principals (automation accounts) are declared in `roster.yaml` with
`approved: true`; their API-key SECRETS live (hashed) in SQLite and are issued
here, once approved:

    python -m ac_auth.cli issue-key hte-orchestrator@lab.local --label orch-2026 --ttl-days 365
    python -m ac_auth.cli list-keys hte-orchestrator@lab.local
    python -m ac_auth.cli revoke-key 3

``issue-key`` prints the secret **once** — store it where the platform reads it;
only its hash is kept.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from .config import default_db_path, load_secrets_file
from .db import Db, norm_email


def _db() -> Db:
    load_secrets_file()  # allow AUTH_DB_PATH from the credentials file
    return Db(str(default_db_path()))


def _fmt_date(ts: float | None) -> str:
    return "-" if not ts else datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _cmd_validate(args: argparse.Namespace) -> int:
    """Validate a roster.yaml (schema + invariants). DB-free — usable anywhere,
    and safe in a git pre-commit hook / systemd ExecStartPre. Exit 0 / 2."""
    from .roster import RosterError, default_roster_path, load_roster

    path = args.path or default_roster_path()
    try:
        r = load_roster(path)
    except RosterError as exc:
        print(f"INVALID: {path}", file=sys.stderr)
        for msg in exc.errors:
            print(f"  - {msg}", file=sys.stderr)
        return 2
    n_admin = sum(1 for u in r.users if u.role == "admin" and u.is_active)
    print(f"OK: {path}")
    print(f"  users: {len(r.users)} ({n_admin} active admin) · automation: {len(r.automation)}")
    return 0


def _cmd_list_users(db: Db) -> int:
    """Show the live allow-list (from roster.yaml), with last-login derived from
    the sessions table."""
    from .roster import RosterError, load_roster

    try:
        r = load_roster()
    except RosterError as exc:
        print("INVALID roster: " + "; ".join(exc.errors), file=sys.stderr)
        return 2
    if not r.users and not r.automation:
        print("(empty roster)")
    for u in sorted(r.users, key=lambda x: x.email):
        print(
            f"  {u.email:38s} {u.role:9s} {u.status:9s}"
            f" expires:{_fmt_date(u.expires_at):11s}"
            f" last-login:{_fmt_date(db.last_login_at(u.email))}"
        )
        extra = []
        if u.name:
            extra.append(f"name={u.name!r}")
        if u.lab_account:
            extra.append(f"lab={u.lab_account!r}")
        if u.status == "disabled" and u.disabled_reason:
            extra.append(f"disabled_reason={u.disabled_reason!r}")
        if extra:
            print("        " + "  ".join(extra))
    for a in sorted(r.automation, key=lambda x: x.email):
        state = "approved" if a.approved else "PENDING"
        print(f"  {a.email:38s} {'automation':9s} {state:9s} expires:{_fmt_date(a.expires_at)}")
    return 0


def _cmd_export(args: argparse.Namespace, db: Db) -> int:
    """Dump the legacy SQLite ``users`` table as roster YAML — a one-time
    migration aid for capturing pre-Phase-0 state into a roster file."""
    from pathlib import Path

    from .roster import Roster, RosterAutomation, RosterUser, dump_roster

    users: list[RosterUser] = []
    automation: list[RosterAutomation] = []
    for u in db.list_users():
        expires = (
            datetime.fromtimestamp(u.expires_at, timezone.utc).date() if u.expires_at else None
        )
        if u.is_automation:
            automation.append(
                RosterAutomation(
                    email=u.email,
                    name=u.name,
                    approved=(u.status == "active"),
                    expires=expires,
                    notes=u.notes,
                )
            )
        else:
            users.append(
                RosterUser(
                    email=u.email,
                    role=u.role,  # "user" is normalised to "operator" by the model
                    name=u.name,
                    lab_account=u.lab_account,
                    notes=u.notes,
                    expires=expires,
                    status=u.status,
                    disabled_reason=u.disabled_reason,
                )
            )
    text = dump_roster(Roster(users=users, automation=automation))
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"OK: wrote {args.output} ({len(users)} users, {len(automation)} automation)")
    else:
        sys.stdout.write(text)
    return 0


def _cmd_issue_key(args: argparse.Namespace, db: Db) -> int:
    """Issue an API key for an automation principal. The principal must be an
    **approved** automation entry in roster.yaml — keys are inert otherwise."""
    from .roster import RosterError, load_roster

    email = norm_email(args.email)
    try:
        roster = load_roster()
    except RosterError as exc:
        print("INVALID roster: " + "; ".join(exc.errors), file=sys.stderr)
        return 2
    auto = next((a for a in roster.automation if a.email == email), None)
    if auto is None:
        print(
            f"ERROR: {email} is not an automation account in roster.yaml "
            "(add it under `automation:` first)",
            file=sys.stderr,
        )
        return 2
    if not auto.approved:
        print(
            f"ERROR: {email} is not approved — set `approved: true` in roster.yaml "
            "(global-admin gate) before issuing a key",
            file=sys.stderr,
        )
        return 2
    ttl_s = args.ttl_days * 86400 if args.ttl_days else None
    token = db.create_api_key(email, label=args.label, ttl_s=ttl_s)
    print(
        f"OK: key issued for {email}"
        + (f" (expires in {args.ttl_days}d)" if args.ttl_days else " (no expiry)")
    )
    print("\n  " + token + "\n")
    print("  ^ store this now; it will NOT be shown again (only the hash is kept).")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ac_auth.cli", description="Manage the auth allow-list (roster.yaml).")
    sub = p.add_subparsers(dest="cmd", required=True)

    val = sub.add_parser("validate", help="validate a roster.yaml (schema + invariants)")
    val.add_argument("path", nargs="?", help="roster file (default: AUTH_ROSTER_PATH or auth/roster.yaml)")

    exp = sub.add_parser("export", help="dump the legacy DB allow-list as roster.yaml (migration aid)")
    exp.add_argument("-o", "--output", help="write to this file (default: stdout)")

    sub.add_parser("list-users", help="show the live roster (+ last login from sessions)")

    ik = sub.add_parser("issue-key", help="issue an API key for an approved automation account (prints once)")
    ik.add_argument("email")
    ik.add_argument("--label", default="", help="key label, e.g. orch-2026")
    ik.add_argument("--ttl-days", type=int, default=None, help="expiry in days (default: no expiry)")

    lk = sub.add_parser("list-keys", help="list an automation account's API keys")
    lk.add_argument("email")

    rk = sub.add_parser("revoke-key", help="revoke an API key by id (see list-keys)")
    rk.add_argument("key_id", type=int)

    args = p.parse_args(argv)

    # validate is DB-free (works anywhere; used by the pre-commit / ExecStartPre hooks)
    if args.cmd == "validate":
        return _cmd_validate(args)

    db = _db()
    try:
        if args.cmd == "list-users":
            return _cmd_list_users(db)
        elif args.cmd == "export":
            return _cmd_export(args, db)
        elif args.cmd == "issue-key":
            return _cmd_issue_key(args, db)
        elif args.cmd == "list-keys":
            keys = db.list_api_keys(args.email)
            if not keys:
                print("(no keys — issue one with: issue-key EMAIL)")
            for k in keys:
                state = "revoked" if k.revoked else "active"
                exp = "never" if k.expires_at is None else f"{k.expires_at:.0f}"
                print(f"  id={k.id:<4d} {state:8s} label={k.label!r:24s} expires_at={exp}")
            return 0
        elif args.cmd == "revoke-key":
            db.revoke_api_key(args.key_id)
            print(f"OK: revoked key id={args.key_id}")
            return 0
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
