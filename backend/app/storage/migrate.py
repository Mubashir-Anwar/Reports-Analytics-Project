"""Copy the data directory into a database (one time, when moving off files).

    REPORTING_DATA_DIR=./data python -m backend.app.storage.migrate --url sqlserver://user:pw@host:1433/reporting
    REPORTING_DATA_DIR=./data python -m backend.app.storage.migrate --url oracle://user:pw@host:1521/?service_name=XEPDB1
    REPORTING_DATA_DIR=./data python -m backend.app.storage.migrate --url mongodb://user:pw@host:27017/reporting
    REPORTING_DATA_DIR=./data python -m backend.app.storage.migrate --url postgresql://user:pw@host/reporting

Reports, workspace drafts, folders, data adapters, private secrets, users, API keys, history, versions, schedules
and run outputs are copied. The run log and audit trail (data/records/) are copied as they are (the audit hash
chain stays valid), and the older run history (data/audit/report-executions.jsonl) becomes run records.
Documents that already exist in the database are kept unless --overwrite is given. The log files (logs/) stay
on disk. Nothing in the data directory is changed. Then start the service with the printed settings."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any

from backend.app.db import model
from backend.app.db.base import DuplicateSeq, RecordsBackend
from backend.app.db.files import FileRecords
from backend.app.db.urls import kind_of, redact
from backend.app.storage.store import FileStore, PostgresStore

PRIVATE = {"users.json", "private_secrets.json", "api-keys.json"}
SKIP_DIRS = ("logs/", "records/")
LEGACY_AUDIT = "audit/report-executions.jsonl"


def target_for(url: str) -> tuple[str, Any, RecordsBackend]:
    """(REPORTING_STORAGE value, document store, records backend) for a database URL."""
    kind = kind_of(url)
    if kind == "mongodb":
        from backend.app.db.mongo import MongoRecords
        from backend.app.storage.mongo_store import MongoStore
        return "mongodb", MongoStore(url), MongoRecords(url)
    if kind == "sql":
        from backend.app.db.sql import SqlRecords
        from backend.app.storage.sql_store import SqlStore
        return "sql", SqlStore(url), SqlRecords(url)
    raise ValueError("Give a postgresql://, mysql://, sqlserver://, oracle://, sqlite:/// or mongodb:// URL")


def entities_for(records: RecordsBackend) -> Any:
    """The typed-entity backend on the same database as these records."""
    from backend.app.db import entities as ent  # noqa: PLC0415
    found = ent.MongoEntities(records.url) if records.name == "mongodb" else ent.SqlEntities(records.url)
    found.ensure()
    return found


def legacy_run(item: dict[str, Any]) -> dict[str, Any] | None:
    """One line of the older run history as a run record (None when it cannot be read)."""
    access = str(item.get("access", ""))
    kind, _, who = access.partition(":")
    channel, actor_type = {"report-token": ("api", "token"), "admin-token": ("api", "token"), "signed-link": ("signed-link", "token"),
                           "api-key": ("api", "api-key"), "user": ("runner", "user"), "admin-run": ("designer-run", "token"),
                           "designer-draft": ("designer-run", "token"), "server-session": ("server", "user"),
                           "schedule": ("schedule", "user"), "background": ("server", "user")}.get(kind, ("api", "token"))
    try:
        started = model.parse_time(str(item.get("requestedAt")).replace(" ", "T"))
    except (TypeError, ValueError):
        return None
    actor = who or (item.get("GeneratedBy") if actor_type == "user" else "") or access or "?"
    return model.clean({
        "id": model.new_id(), "orgId": model.org_id(), "startedAt": started, "durationMs": item.get("durationMs"),
        "channel": channel, "actor": actor, "actorType": actor_type, "reportId": item.get("reportId"), "format": item.get("format"),
        "dataSource": item.get("connectionId"), "parameters": item.get("parameters"),
        "status": "success" if item.get("status") == "success" else "failed", "errorCode": item.get("errorCode"),
        "errorMessage": item.get("errorMessage"), "rowCount": item.get("rowCount"), "requestPath": item.get("path"),
        "onBehalfOf": item.get("GeneratedBy") if actor_type != "user" else None,
    }, model.RUN_FIELDS)


def copy_records(target: RecordsBackend, source: FileStore, *, out=sys.stdout) -> dict[str, int]:
    """Run log, audit trail and the older run history into the target's records (each item once)."""
    counts = {"runs": 0, "events": 0, "legacyRuns": 0, "recordsSkipped": 0}
    target.ensure_schema()
    files = FileRecords(source.root)
    if not target.runs(limit=1):                  # never copy the run log twice
        for item in files._read("runs"):
            target._insert_run(model.clean(item, model.RUN_FIELDS))
            counts["runs"] += 1
        for line in source.lines(LEGACY_AUDIT):
            try:
                record = legacy_run(json.loads(line))
            except ValueError:
                record = None
            if record is None:
                counts["recordsSkipped"] += 1
                continue
            target._insert_run(record)
            counts["legacyRuns"] += 1
    else:
        print("The target already has run records; the run log was not copied again.", file=out)
    if target._last_event() is None:
        for event in sorted(files._read("events"), key=lambda item: item["seq"]):
            try:
                target._insert_event(model.clean(event, model.AUDIT_FIELDS))
                counts["events"] += 1
            except DuplicateSeq:
                counts["recordsSkipped"] += 1
    else:
        print("The target already has audit events; the audit trail was not copied again.", file=out)
    return counts


def migrate(target: Any, *, overwrite: bool = False, out=sys.stdout, records: RecordsBackend | None = None) -> dict[str, int]:
    source = FileStore()
    source.upgrade_v2()                               # report files of an older data folder become rows first
    from backend.app.db.people import upgrade_people  # noqa: PLC0415
    upgrade_people(source)                            # and its accounts, keys and folder rules
    from backend.app.security.secrets import upgrade_secrets  # noqa: PLC0415
    upgrade_secrets()                                 # and private_secrets.json -> the secrets table
    from backend.app.db.schedules import upgrade_schedules  # noqa: PLC0415
    upgrade_schedules(source)                         # and rs_job_schedules -> schedules + schedule_targets
    from backend.app.jobs.store import upgrade_runs  # noqa: PLC0415
    upgrade_runs(source)                              # and rs_job_runs -> runs, run_files, run_deliveries
    counts = {"json": 0, "bytes": 0, "lines": 0, "folders": 0, "skipped": 0}
    if records is not None:
        from backend.app.db import entities as ent  # noqa: PLC0415
        target_entities = entities_for(records)
        copied = 0
        for entity in ent.ENTITIES:       # the typed tables of a files installation (records/): folders and reports keep their ids
            for row in ent.FileEntities(source.root).find(entity):
                if overwrite or target_entities.get(entity, *ent.key_of(entity, ent.encode(entity, row))) is None:
                    target_entities.put(entity, row)
                    copied += 1
        counts["typedRows"] = copied
    for folder in ("reports", "workspace"):
        if source.has_dir(folder):
            target.make_dir(folder)
        for relative in source.dirs(folder):
            target.make_dir(f"{folder}/{relative}")
            counts["folders"] += 1
    for key in source.keys(""):
        if key.startswith(SKIP_DIRS) or (records is not None and key == LEGACY_AUDIT):
            continue
        if records is not None and source.report_id_of(key):
            continue                                  # copied above as a row
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
                target.write_json(key, source.read_json(key), private=key.rsplit("/", 1)[-1] in PRIVATE)
                counts["json"] += 1
            except ValueError:
                print(f"skipped (not valid JSON): {key}", file=out)
                counts["skipped"] += 1
        else:
            target.write_bytes(key, source.read_bytes(key) or b"")
            counts["bytes"] += 1
    if records is not None:
        counts.update(copy_records(records, source, out=out))
        from backend.app.db.upgrade import move_documents  # noqa: PLC0415
        counts.update({f"typed_{name}": value for name, value in move_documents(target, target_entities).items()})
    print(json.dumps(counts), file=out)
    return counts


def main() -> int:
    import os
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.getenv("REPORTING_STORAGE_URL", ""), help="database URL (default: REPORTING_STORAGE_URL)")
    parser.add_argument("--overwrite", action="store_true", help="replace documents that already exist in the database")
    parser.add_argument("--legacy-postgresql", action="store_true",
                        help="use the older PostgreSQL tables (reporting_documents) instead of the rs_ tables")
    args = parser.parse_args()
    if not args.url:
        parser.error("give --url (or set REPORTING_STORAGE_URL)")
    try:
        if args.legacy_postgresql:
            from backend.app.db.sql import SqlRecords
            storage, store, records = "postgresql", PostgresStore(args.url), SqlRecords(args.url)
        else:
            storage, store, records = target_for(args.url)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Copying {FileStore().root} into {redact(args.url)} …")
    migrate(store, overwrite=args.overwrite, records=records)
    print(f"Done ({datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC). Start the service with:\n"
          f"  REPORTING_STORAGE={storage}\n  REPORTING_STORAGE_URL=<the same URL>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
