"""Turn on the Admin checklist items in the service's .env. Needs only Python 3 (no packages).

    python tools/harden_env.py                     add what is missing: secrets key, session secret, docs off
    python tools/harden_env.py --https             also secure cookies + HSTS (only when people use https://)
    python tools/harden_env.py --test-mailbox      also send schedule e-mails to tools/test_mailbox.py on this PC
    python tools/harden_env.py --check             only say what is missing, change nothing

Only missing settings are added: a value that is there is never changed, and nothing secret is printed.
A copy of the old file is kept next to it (.env.before-harden-<time>). Restart the service afterwards.

Storage is not switched here, because the data must be copied first: use Admin -> System database
(it copies everything to MongoDB / PostgreSQL / SQL Server and then writes REPORTING_STORAGE for you).

Keep a copy of REPORTING_SECRETS_KEY somewhere safe. On the next start every saved data source password is
encrypted with it; without the key those passwords must be entered again.
"""
from __future__ import annotations

import argparse
import os
import secrets
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAFE_AREAS = "runner,admin,designer,server,analytics"      # every area except docs (/docs, /openapi.json)


def wanted(https: bool, mailbox: bool) -> list[tuple[str, str, str]]:
    """(name, value, comment) of every setting the checklist wants."""
    items = [
        ("REPORTING_SECRETS_KEY", secrets.token_urlsafe(48), "Encrypts data source passwords: KEEP A COPY of this value"),
        ("REPORTING_SESSION_SECRET", secrets.token_hex(32), "Signs the sign-ins (use the same value on every instance)"),
        ("REPORTING_ENABLED_AREAS", SAFE_AREAS, "API docs (/docs) are off; add ,docs only on a test server"),
    ]
    if https:
        items += [("REPORTING_SECURE_COOKIES", "true", "Cookies only over https://"),
                  ("REPORTING_HSTS", "true", "Browsers always use https:// for this site")]
    if mailbox:
        items += [("SMTP_HOST", "127.0.0.1", "Test mail box on this PC: python tools/test_mailbox.py"),
                  ("SMTP_PORT", "2525", ""), ("SMTP_SECURITY", "none", ""), ("SMTP_FROM", "reports@localhost", "")]
    return items


def read_names(text: str) -> dict[str, str]:
    names = {}
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, _, value = line.partition("=")
            names[name.strip().removeprefix("export ").strip()] = value.strip().strip("'\"")
    return names


def merge(text: str, items: list[tuple[str, str, str]], environ: dict[str, str]) -> tuple[str, list[str], list[str]]:
    """(new text, names added, names skipped because the real environment already sets them)."""
    names = read_names(text)
    todo = [item for item in items if not names.get(item[0])]
    skipped = [name for name, _, _ in todo if environ.get(name, "").strip()]
    todo = [item for item in todo if item[0] not in skipped]
    empty = {name for name, _, _ in todo if name in names}      # "NAME=" with no value: that line is replaced

    def kept(line: str) -> bool:
        return line.lstrip().startswith("#") or line.split("=", 1)[0].strip().removeprefix("export ").strip() not in empty

    out = [line for line in text.rstrip("\n").splitlines() if kept(line)] if text.strip() else [
        "# Settings of the reporting service. Never commit or share this file."]
    for name, value, comment in todo:
        out += ([f"# {comment}"] if comment else []) + [f"{name}={value}"]
    return "\n".join(out) + "\n", [name for name, _, _ in todo], skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Turn on the Admin checklist items in .env")
    parser.add_argument("--file", default=str(ROOT / ".env"), help="the .env file (default: the one next to backend/)")
    parser.add_argument("--https", action="store_true", help="people open the service with https://")
    parser.add_argument("--test-mailbox", action="store_true", help="schedule e-mails go to tools/test_mailbox.py")
    parser.add_argument("--check", action="store_true", help="only list what is missing")
    args = parser.parse_args(argv)
    path = Path(args.file)
    old = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    text, added, skipped = merge(old, wanted(args.https, args.test_mailbox), dict(os.environ))
    for name in skipped:
        print(f"  {name}: set by the server's environment, not by .env (left alone)")
    if not added:
        print(f"{path}: nothing is missing")
        return 0
    if args.check:
        print(f"{path}: missing {', '.join(added)}")
        return 1
    if path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, path.with_name(f".env.before-harden-{stamp}"))
    fresh = path.with_name(path.name + ".new")
    fresh.unlink(missing_ok=True)
    with os.fdopen(os.open(fresh, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(fresh, path)
    print(f"{path}: added {', '.join(added)}")
    if "REPORTING_SECRETS_KEY" in added:
        print("  Keep a copy of REPORTING_SECRETS_KEY (open the file to see it): it unlocks the data source passwords.")
    if "REPORTING_ENABLED_AREAS" in added:
        print("  /docs and /openapi.json are now off.")
    print("  Storage: switch it in Admin -> System database (it copies the data first). Then restart the service.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
