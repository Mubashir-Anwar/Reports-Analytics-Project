"""Copy the data directory into the PostgreSQL store (one time, when moving to REPORTING_STORAGE=postgresql).

    REPORTING_DATA_DIR=./data REPORTING_STORAGE_URL=postgresql://user:pw@host/db python -m backend.app.storage.migrate

Reports, workspace drafts, folders, data adapters, private secrets, users, history, versions, schedules, run outputs
and the audit log are copied. Documents that already exist in the database are kept unless --overwrite is given.
The log files (logs/) stay on disk. Nothing in the data directory is changed."""
from __future__ import annotations

import argparse
import json
import os
import sys

from backend.app.storage.store import FileStore, PostgresStore

PRIVATE = {"users.json", "private_secrets.json"}
SKIP_DIRS = ("logs/",)


def migrate(target: PostgresStore, *, overwrite: bool = False, out=sys.stdout) -> dict[str, int]:
    source = FileStore()
    counts = {"json": 0, "bytes": 0, "lines": 0, "folders": 0, "skipped": 0}
    for folder in ("reports", "workspace"):
        for relative in source.dirs(folder):
            target.make_dir(f"{folder}/{relative}")
            counts["folders"] += 1
    for key in source.keys(""):
        if key.startswith(SKIP_DIRS):
            continue
        already = target.lines(key) if key.endswith(".jsonl") else target.exists(key)
        if already and (not overwrite or key.endswith(".jsonl")):  # log lines are never copied twice
            counts["skipped"] += 1
            continue
        if key.endswith(".jsonl"):
            for line in source.lines(key):
                if line.strip():
                    target.append_line(key, line)
                    counts["lines"] += 1
        elif key.endswith(".json"):
            try:
                target.write_json(key, source.read_json(key), private=key in PRIVATE)
                counts["json"] += 1
            except ValueError:
                print(f"skipped (not valid JSON): {key}", file=out)
                counts["skipped"] += 1
        else:
            target.write_bytes(key, source.read_bytes(key) or b"")
            counts["bytes"] += 1
    print(json.dumps(counts), file=out)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.getenv("REPORTING_STORAGE_URL", ""), help="postgresql://… (default: REPORTING_STORAGE_URL)")
    parser.add_argument("--overwrite", action="store_true", help="replace documents that already exist in the database")
    args = parser.parse_args()
    if not args.url.startswith(("postgresql://", "postgres://")):
        parser.error("give --url postgresql://user:password@host/database (or set REPORTING_STORAGE_URL)")
    print(f"Copying {FileStore().root} into the PostgreSQL store …")
    migrate(PostgresStore(args.url), overwrite=args.overwrite)
    print("Done. Start the service with REPORTING_STORAGE=postgresql and the same REPORTING_STORAGE_URL.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
