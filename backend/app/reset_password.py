"""Forgotten password: set a new one from the server's command line (no database editing needed).

Whoever can open a shell on the server (and read its .env) owns the installation anyway, so this is the safe
recovery path for a super admin. It works with every system database (files, SQL Server, PostgreSQL, MySQL, Oracle,
SQLite, MongoDB): it uses the same settings as the service.

    python -m backend.app.reset_password                      list the accounts (super admins first)
    python -m backend.app.reset_password admin                ask for a new password for "admin" (typed twice, hidden)
    python -m backend.app.reset_password admin --generate     make a strong random password and print it once
    python -m backend.app.reset_password admin --make-super-admin   also make the account a super admin

The account is also enabled again, every session it had is signed out, and an audit event "user.password_recovered"
is written. In Docker:  docker compose exec reporting python -m backend.app.reset_password admin
"""
from __future__ import annotations

import argparse
import getpass
import secrets
import string
import sys
from datetime import datetime, timezone

MIN_LENGTH = 12


def _strong_password(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits + "-_.!@#%"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.islower() for c in value) and any(c.isupper() for c in value) and any(c.isdigit() for c in value):
            return value


def _problem(password: str, username: str) -> str:
    if len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters"
    if password.casefold() == username.casefold():
        return "The password cannot be the user name"
    if len(set(password)) < 5:
        return "Use more different characters"
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.app.reset_password", description=__doc__.split("\n\n")[0])
    parser.add_argument("username", nargs="?", help="the account (leave out to list the accounts)")
    parser.add_argument("--generate", action="store_true", help="make a random password and print it once")
    parser.add_argument("--make-super-admin", action="store_true", help="also give the account every right (super admin)")
    args = parser.parse_args(argv)

    from backend.app.security.secrets import load_local_env_file  # noqa: PLC0415
    load_local_env_file()                       # the same .env (system database, secrets key) as the service
    from backend.app.db.upgrade import upgrade_storage  # noqa: PLC0415
    from backend.app.db.urls import redact  # noqa: PLC0415
    from backend.app.security.users import find_user, is_super_admin, load_users, save_user, set_user_password  # noqa: PLC0415
    from backend.app.storage.files import initialize_data_directory  # noqa: PLC0415
    from backend.app.storage.store import storage_settings  # noqa: PLC0415

    initialize_data_directory()
    upgrade_storage()
    backend, url = storage_settings()
    print(f"System database: {backend}{' · ' + redact(url) if url else ''}")
    users = load_users()
    if not args.username:
        if not users:
            print("There are no accounts yet. Make the first one with: python -m backend.app.setup")
            return 0
        print("Accounts (super admins first):")
        for user in sorted(users, key=lambda item: (not is_super_admin(item), item["username"].casefold())):
            flags = [label for label, on in (("super admin", is_super_admin(user)), ("disabled", user.get("disabled"))) if on]
            print(f"  {user['username']}{'  (' + ', '.join(flags) + ')' if flags else ''}")
        print("\nReset one:  python -m backend.app.reset_password <username>")
        return 0

    user = find_user(args.username)
    if user is None:
        print(f"No account '{args.username}'. Run without a name to list the accounts.", file=sys.stderr)
        return 2
    if args.generate:
        password = _strong_password()
    else:
        if not sys.stdin.isatty():
            print("Type the password in a terminal, or use --generate.", file=sys.stderr)
            return 2
        password = getpass.getpass(f"New password for {user['username']}: ")
        reason = _problem(password, user["username"])
        if reason:
            print(reason, file=sys.stderr)
            return 2
        if getpass.getpass("Type it again: ") != password:
            print("The two passwords are different; nothing was changed.", file=sys.stderr)
            return 2

    set_user_password(user, password)
    user["disabled"] = False
    user["sessionVersion"] = int(user.get("sessionVersion", 0)) + 1      # signs out every session of this account
    user["updatedAt"] = datetime.now(timezone.utc).isoformat()
    if args.make_super_admin:
        user["superAdmin"] = True
    save_user(user)                                                       # only this account: never a stale copy of the others
    try:
        from backend.app.db import get_records  # noqa: PLC0415
        get_records().audit("user.password_recovered", actor="server console", actor_type="system", target=user["username"],
                            details={"superAdmin": is_super_admin(user), "generated": args.generate})
    except Exception as exc:  # noqa: BLE001 - the password is changed either way
        print(f"(The audit event could not be written: {exc})", file=sys.stderr)
    print(f"Done: '{user['username']}' has a new password and is enabled"
          f"{' (super admin)' if is_super_admin(user) else ''}; its old sessions are signed out.")
    if args.generate:
        print(f"\n  New password: {password}\n\nIt is shown only now. Sign in and change it under your account.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
