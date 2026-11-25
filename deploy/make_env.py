"""Make deploy/.env for docker compose, with new random secrets. Needs only Python 3 (no packages).

    python deploy/make_env.py                                    make deploy/.env (an existing file keeps its values)
    python deploy/make_env.py --url https://reports.example.com  also set the address people use

A secret that is missing is added; a value that is there is never changed and nothing secret is printed.
Keep a copy of REPORTING_SECRETS_KEY somewhere safe: without it every data source password must be entered again.
"""
from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path

NEEDED = {
    "STORAGE_DB_PASSWORD": "Password of the PostgreSQL system database",
    "REPORTING_ADMIN_TOKEN": "For automation only (X-Admin-Token)",
    "REPORTING_SESSION_SECRET": "Signs the sign-ins (the same on every instance)",
    "REPORTING_SECRETS_KEY": "Encrypts data source passwords: keep a copy",
    "REPORTING_ACCESS_SECRET": "Signs the report links of a website",
}
OPTIONAL = {
    "REPORTING_PUBLIC_URL": "The address people use, e.g. https://reports.example.com (links in e-mails)",
    "REPORTING_TIMEZONE": "e.g. Asia/Karachi (default UTC)",
    "SMTP_HOST": "Mail server for scheduled reports (empty = no e-mail); SMTP_SECURITY is starttls, ssl or none",
    "SMTP_PORT": "",
    "SMTP_SECURITY": "",
    "SMTP_USER": "",
    "SMTP_PASSWORD": "",
    "SMTP_FROM": "",
}
HEAD = "# Settings of docker compose (made by deploy/make_env.py). Never commit or share this file."


def merge(text: str, url: str = "") -> tuple[str, list[str]]:
    """(the new text, names added). Lines that are there stay as they are."""
    names = {}
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, _, value = line.partition("=")
            names[name.strip().removeprefix("export ").strip()] = value.strip()
    out = [text.rstrip("\n") if text.strip() else HEAD]
    added = []
    for name, what in NEEDED.items():
        if name not in names:
            out += [f"# {what}", f"{name}={secrets.token_hex(32)}"]      # hex: safe inside a database URL
            added.append(name)
        elif not names[name]:
            raise ValueError(f"{name} is there but empty: give it a value, or delete the line and run this again")
    for name, what in OPTIONAL.items():
        if name == "REPORTING_PUBLIC_URL" and url and not names.get(name):
            out += [f"# {what}", f"{name}={url.rstrip('/')}"]
            added.append(name)
        elif name not in names and f"#{name}=" not in text:
            out += ([f"# {what}"] if what else []) + [f"#{name}="]
    return "\n".join(out) + "\n", added


def main() -> int:
    parser = argparse.ArgumentParser(description="Make deploy/.env for docker compose")
    parser.add_argument("--file", default=str(Path(__file__).with_name(".env")))
    parser.add_argument("--url", default="", help="the address people use")
    args = parser.parse_args()
    path = Path(args.file)
    try:
        text, added = merge(path.read_text(encoding="utf-8") if path.exists() else "", args.url)
    except ValueError as exc:
        print(exc)
        return 1
    fresh = path.with_name(path.name + ".new")
    fresh.unlink(missing_ok=True)
    with os.fdopen(os.open(fresh, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(fresh, path)
    print(f"{path}: " + (f"added {', '.join(added)}" if added else "nothing was missing"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
