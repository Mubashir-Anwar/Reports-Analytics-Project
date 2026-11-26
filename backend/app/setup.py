"""Environment check and first sign-in, in one command (local PC, server or Docker):

    python -m backend.app.setup                          check the environment, make .env, make the setup admin
    python -m backend.app.setup --check                  only check (Python, packages, web pages, database)
    python -m backend.app.setup --username ali           another name for the setup admin (default: setup_admin)
    python -m backend.app.setup --ask                    type the password yourself (else a strong one is printed once)
    python -m backend.app.setup --hours 48               how long the setup admin works (default 24, at most 168)
    python -m backend.app.setup --force                  make one even when a permanent super admin exists

.env: when there is none it is made with new random REPORTING_SECRETS_KEY and REPORTING_SESSION_SECRET (an existing
.env is never changed). Everything else has a default or is set in the product (docs/SETTINGS.md lists all settings).

The setup admin is a super admin that switches itself off after 24 hours. Sign in with it at /login, create your own
super admin in Admin -> Users & rights, and use that one from then on. If the time ran out before that, run the
command again: it gives the setup admin a new password and a new 24 hours.

In Docker:  docker compose exec app-1 python -m backend.app.setup
"""
from __future__ import annotations

import argparse
import getpass
import importlib
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_USERNAME = "setup_admin"
MAX_HOURS = 168
# not pyodbc: only SQL Server with a Windows login uses it, and on Linux it needs the system library unixODBC
PACKAGES = ("fastapi", "uvicorn", "pymongo", "sqlalchemy", "psycopg", "pymysql", "pymssql", "oracledb", "openpyxl", "reportlab",
            "uharfbuzz", "PIL", "defusedxml", "cryptography", "duckdb", "numpy", "pdfplumber", "paramiko", "rapidocr", "onnxruntime",
            "tzdata")
ENV_TEXT = """# Settings of this installation (made by python -m backend.app.setup). Never commit or share this file.
# Every other setting has a default; docs/SETTINGS.md explains all of them. Data sources are added in the product.

# Encrypts data source passwords at rest. Keep a copy somewhere safe: without it they must be entered again.
REPORTING_SECRETS_KEY={key}
# Signs sign-in sessions (the same on every instance).
REPORTING_SESSION_SECRET={session}
"""


def check_environment(root: Path) -> list[tuple[str, bool, str]]:
    """(what, ok, detail) lines; nothing is changed."""
    out = [("Python", sys.version_info >= (3, 11), f"{sys.version.split()[0]} (3.11 or newer is needed)")]
    missing = []
    for name in PACKAGES:
        try:
            importlib.import_module(name)
        except Exception:  # noqa: BLE001 - a broken package counts as missing
            missing.append(name)
    out.append(("Packages", not missing, "all installed" if not missing else
                f"missing: {', '.join(missing)}. Run: python -m pip install -r requirements.txt"))
    pages = (root / "ui" / "app" / "server.html").exists()
    out.append(("Web pages", pages, "built (ui/app)" if pages else "not built. Run in frontend/: npm install, then npm run build"))
    return out


def env_in_git(root: Path) -> str:
    """'' when .env is safe from git; otherwise what is wrong. Skipped (safe) without git or a .git folder."""
    import shutil  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    git = shutil.which("git")
    if not git or not (root / ".git").exists():
        return ""
    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([git, *args], cwd=root, capture_output=True, text=True, timeout=20, check=False)  # noqa: S603
    tracked = [name for name in run("ls-files").stdout.splitlines() if Path(name).name == ".env" or Path(name).name.startswith(".env.")]
    if tracked:
        return f"{', '.join(tracked)} is in git: run  git rm --cached {tracked[0]}  and make new secrets (they were shared)"
    if run("check-ignore", "-q", ".env").returncode != 0:
        return ".gitignore does not keep .env out of git: add the line  .env"
    return ""


def make_env_file(path: Path) -> bool:
    """A new .env with random secrets, unless there is one or the environment already gives both."""
    if path.exists() or os.getenv("REPORTING_SKIP_ENV_FILE", "").strip() == "1":
        return False
    if os.getenv("REPORTING_SECRETS_KEY", "").strip() and os.getenv("REPORTING_SESSION_SECRET", "").strip():
        return False
    if os.getenv("REPORTING_SECRETS_KEY", "").strip() or os.getenv("REPORTING_STORAGE_URL", "").strip():
        return False                              # configured by the platform (Docker, a service): leave it alone
    text = ENV_TEXT.format(key=secrets.token_urlsafe(48), session=secrets.token_urlsafe(48))
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        return False                              # a read-only folder (a container): the environment decides
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return True


def make_setup_admin(username: str, password: str, hours: float, *, force: bool = False) -> dict:
    """Create (or renew) the temporary super admin. ValueError when it must not be made."""
    from backend.app.constants import USER_RIGHTS, USERNAME_PATTERN  # noqa: PLC0415
    from backend.app.security.users import find_user, load_users, needs_first_admin, save_users, set_user_password  # noqa: PLC0415
    if not USERNAME_PATTERN.fullmatch(username):
        raise ValueError("Username: 2-64 letters, numbers, dot, @, _ or -")
    if not force and not needs_first_admin():
        raise ValueError("There is a super admin already: sign in with it. Forgotten password: "
                         "python -m backend.app.reset_password. To make a setup admin anyway, add --force")
    now = datetime.now(timezone.utc)
    user = find_user(username)
    if user is not None and not user.get("temporary"):
        raise ValueError(f"'{username}' is a normal account: choose another name with --username")
    record = dict(user or {"username": username, "createdAt": now.isoformat(), "createdBy": "server console", "sessionVersion": 0})
    set_user_password(record, password)
    record.update({"fullName": "Setup admin (temporary)", "temporary": True, "superAdmin": True, "rights": list(USER_RIGHTS),
                   "disabled": False, "mustChangePassword": False, "sessionVersion": int(record.get("sessionVersion") or 0) + 1,
                   "expiresAt": (now + timedelta(hours=hours)).isoformat(timespec="seconds")})
    others = [item for item in load_users() if item["username"] != record["username"]]
    save_users([*others, record], by="server console")
    try:
        from backend.app.db import get_records  # noqa: PLC0415
        get_records().audit("user.setup_admin", actor="server console", actor_type="system", target=username,
                            details={"expiresAt": record["expiresAt"], "renewed": user is not None})
    except Exception as exc:  # noqa: BLE001 - the account is made either way
        print(f"(The audit event could not be written: {exc})", file=sys.stderr)
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.app.setup", description="Check the environment and make the setup admin.")
    parser.add_argument("--check", action="store_true", help="only check, change nothing")
    parser.add_argument("--username", default=DEFAULT_USERNAME, help=f"name of the setup admin (default {DEFAULT_USERNAME})")
    parser.add_argument("--hours", type=float, default=24, help="how long it works (default 24)")
    parser.add_argument("--ask", action="store_true", help="type the password (else a strong one is printed once)")
    parser.add_argument("--force", action="store_true", help="also when a permanent super admin exists")
    args = parser.parse_args(argv)
    if not 1 <= args.hours <= MAX_HOURS:
        parser.error(f"--hours must be 1 to {MAX_HOURS}")

    root = Path(__file__).resolve().parents[2]
    print("1. Environment")
    checks = check_environment(root)
    for label, ok, detail in checks:
        print(f"   {'OK ' if ok else 'NO '} {label}: {detail}")
    if not checks[0][1] or not checks[1][1]:
        print("\nFix the lines marked NO, then run this command again.", file=sys.stderr)
        return 1

    if not args.check:
        made = make_env_file(root / ".env")
        print(f"   OK  Settings: {'.env made with new random secrets' if made else 'kept as they are'}")
    leak = env_in_git(root)
    print(f"   {'NO ' if leak else 'OK '} Secrets out of git: {leak or '.env is ignored by git and not committed'}")
    from backend.app.security.secrets import load_local_env_file  # noqa: PLC0415
    load_local_env_file()
    from backend.app.db.urls import redact  # noqa: PLC0415
    from backend.app.storage.store import get_store, storage_settings  # noqa: PLC0415
    backend, url = storage_settings()
    where = f"{backend}{' - ' + redact(url) if url else ''}"
    try:
        from backend.app.storage.files import initialize_data_directory  # noqa: PLC0415
        if args.check:
            get_store().read_json("connections.json")
        else:
            initialize_data_directory()
            from backend.app.db.upgrade import upgrade_storage  # noqa: PLC0415
            upgrade_storage()
    except Exception as exc:  # noqa: BLE001 - told to the person who runs the command
        print(f"   NO  System database: {where}: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(f"   OK  System database: {where}")
    if args.check:
        return 0 if all(ok for _, ok, _ in checks) and not leak else 1

    print("\n2. Setup admin")
    from backend.app.reset_password import _problem, _strong_password  # noqa: PLC0415
    if args.ask:
        if not sys.stdin.isatty():
            print("   --ask needs a terminal; leave it out to have a password made.", file=sys.stderr)
            return 2
        password = getpass.getpass(f"   Password for {args.username}: ")
        reason = _problem(password, args.username)
        if reason or getpass.getpass("   Type it again: ") != password:
            print(f"   {reason or 'The two passwords are different'}; nothing was changed.", file=sys.stderr)
            return 2
    else:
        password = _strong_password()
    try:
        record = make_setup_admin(args.username, password, args.hours, force=args.force)
    except ValueError as exc:
        print(f"   {exc}", file=sys.stderr)
        return 2
    print(f"   Username : {record['username']}")
    print(f"   Password : {'(the one you typed)' if args.ask else password + '   (shown only now)'}")
    print(f"   Works for: {args.hours:g} hours, until {datetime.fromisoformat(record['expiresAt']).astimezone():%Y-%m-%d %H:%M}")
    print("\n3. Next")
    print("   Start    : python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000   (skip if it is running)")
    print("   Sign in  : http://127.0.0.1:8000/login")
    print("   Then     : press 'Create new admin' and use your own account from then on.")
    if not checks[2][1]:
        print("   Note     : build the web pages first (see NO above).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
