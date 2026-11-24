"""Where schedules, runs and their output files are kept (schema v2):

    schedules + schedule_targets   db/schedules.py
    runs                           the run log (records part of the system database): a background, scheduled or
                                   API job is one row there, from queued to running to success / failed. The same row
                                   is completed by the run log when the report has run, so every execution is one row.
    run_files                      the file a run made: jobs/output/<file> in the store, or a big export on disk
    run_deliveries                 each send of the file: e-mail, SFTP, an API, a network folder

The scheduler and the /server screens still read and write one dict per run (the version 1 shape):
    {"id", "jobId", "jobLabel", "kind", "reportId", "folder", "title", "format", "parameters", "owner", "status",
     "queuedAt", "startedAt", "finishedAt", "rowCount", "error", "file", "size", "spooled", "email", "delivered"}

(Version 1 kept them in rs_job_schedules / rs_job_runs, and before that jobs/schedules.json and jobs/runs.json:
db/upgrade.py moves those at start-up and keeps the old ones.) All reads and writes go through one lock, because
the scheduler thread and the web requests both change them."""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

from backend.app.db import entities as ent, get_records, model, schedules as schedule_tables
from backend.app.db.entities import get_entities
from backend.app.storage.store import get_store

MAX_RUNS = 500
RUN_ID_RE = re.compile(r"^[a-z]+-\d{14}-[0-9a-f]{8}$")
LOCK = threading.RLock()

OUTPUT_PREFIX = "jobs/output"
JOB_KINDS = ("manual", "background", "scheduled", "api")       # runType of a job (interactive runs are not jobs)

# What a run or schedule holds when a field is empty (the table leaves empty fields out).
RUN_DEFAULTS = {"jobId": "", "jobLabel": "", "startedAt": "", "finishedAt": "", "rowCount": 0, "error": "", "file": "", "size": 0,
                "email": "", "parameters": {}, "spooled": False}
SCHEDULE_DEFAULTS = {"nextRunAt": "", "lastRun": None, "parameters": {}, "enabled": False}


def _with_defaults(item: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    return {**defaults, **item}


def load_schedules() -> list[dict[str, Any]]:
    with LOCK:
        items = schedule_tables.load_schedules()
        return sorted((_with_defaults(item, SCHEDULE_DEFAULTS) for item in items), key=lambda job: (job.get("createdAt") or "", job["id"]))


def save_schedules(items: list[dict[str, Any]]) -> None:
    with LOCK:
        schedule_tables.save_schedules(items)


# ----------------------------------------------------------------- job runs <-> rows of runs
def _channel(run: dict[str, Any]) -> tuple[str, str]:
    owner = str(run.get("owner") or "")
    if owner.startswith("apikey:"):
        return "api", "api-key"
    return ("schedule" if run.get("jobId") else "server"), "user"


def _row(run: dict[str, Any], *, first: bool) -> dict[str, Any]:
    channel, actor_type = _channel(run)
    kind = run.get("kind") if run.get("kind") in JOB_KINDS else ("scheduled" if run.get("jobId") else "background")
    row = {
        "id": run["id"], "runType": kind, "channel": channel, "actor": run.get("owner") or "?", "actorType": actor_type,
        "scheduleId": run.get("jobId") or None, "scheduleName": run.get("jobLabel") or None,
        "reportId": run.get("reportId") or None, "reportTitle": run.get("title") or None, "folder": run.get("folder") or None,
        "format": run.get("format") or None, "status": run.get("status") or "queued",
        "queuedAt": run.get("queuedAt") or None,
        "startedAt": run.get("startedAt") or run.get("queuedAt") or model.now(),   # a queued run: its queue time
        "finishedAt": run.get("finishedAt") or None, "rowCount": run.get("rowCount"), "errorMessage": run.get("error") or None,
    }
    if first:                         # the values asked for, sensitive ones masked; later writes keep what is stored
        row["parameters"] = model.masked_parameters(run.get("parameters") or {}, set(run.get("_sensitive") or ()))
    return row


def _file_row(run: dict[str, Any]) -> dict[str, Any] | None:
    key = output_key(run)
    if key is None:
        return None
    if run.get("spooled"):
        from backend.app.rendering.spool import export_path  # noqa: PLC0415
        storage, location = "disk", str(export_path(run["file"]))
    else:
        storage, location = "store", key
    return {"runId": run["id"], "storage": storage, "location": location, "fileName": run["file"],
            "sizeBytes": run.get("size") or 0, "createdAt": run.get("finishedAt") or None}


def _delivery_rows(run: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    if run.get("email"):
        text = str(run["email"])
        rows.append({"deliveryId": f"{run['id']}-email", "runId": run["id"], "targetId": "email", "targetType": "email",
                     "status": "sent" if text.startswith("sent") else "failed", "message": text[:500],
                     "finishedAt": run.get("finishedAt") or None})
    for item in run.get("delivered") or []:
        status = "skipped" if item.get("skipped") else ("sent" if item.get("ok") else "failed")
        rows.append({"deliveryId": f"{run['id']}-{item.get('id')}"[:64], "runId": run["id"], "targetId": str(item.get("id"))[:16],
                     "targetType": {"http": "api"}.get(item.get("type"), item.get("type") or "?"), "status": status,
                     "message": str(item.get("result") or "")[:500], "bytesSent": run.get("size") if status == "sent" else None,
                     "finishedAt": run.get("finishedAt") or None, "item": item})
    return rows


def _job_run(row: dict[str, Any], file_row: dict[str, Any] | None, deliveries: list[dict[str, Any]]) -> dict[str, Any]:
    queued = row.get("status") == "queued"
    run = {
        "id": row["id"], "jobId": row.get("scheduleId") or "", "jobLabel": row.get("scheduleName") or "",
        "kind": row.get("runType") or "", "reportId": row.get("reportId") or "", "folder": row.get("folder") or "",
        "title": row.get("reportTitle") or row.get("reportId") or "", "format": row.get("format") or "",
        "parameters": row.get("parameters") or {}, "owner": row.get("actor") or "", "status": row.get("status") or "",
        "queuedAt": row.get("queuedAt") or row.get("startedAt") or "", "startedAt": "" if queued else (row.get("startedAt") or ""),
        "finishedAt": row.get("finishedAt") or "", "rowCount": row.get("rowCount") or 0, "error": row.get("errorMessage") or "",
        "file": "", "size": 0, "email": "",
    }
    if file_row:
        run.update({"file": file_row["fileName"], "size": file_row.get("sizeBytes") or 0, "spooled": file_row["storage"] == "disk"})
    delivered = []
    for item in deliveries:
        if item["targetType"] == "email":
            run["email"] = item.get("message") or ""
        else:
            delivered.append(item.get("item") or {"id": item["targetId"], "type": item["targetType"], "ok": item["status"] == "sent",
                                                  "skipped": item["status"] == "skipped", "result": item.get("message") or ""})
    if delivered:
        run["delivered"] = delivered
    return run


def load_runs() -> list[dict[str, Any]]:
    """The jobs (background, scheduled, API runs) of the Runs list, newest first."""
    with LOCK:
        records = get_records()
        rows = [row for kind in JOB_KINDS for row in records.runs(limit=MAX_RUNS, runType=kind) if not row.get("removedAt")]
        db = get_entities()
        files = {item["runId"]: item for item in db.find(ent.RUN_FILES)}
        sent: dict[str, list[dict[str, Any]]] = {}
        for item in db.find(ent.RUN_DELIVERIES):
            sent.setdefault(item["runId"], []).append(item)
        runs = [_with_defaults(_job_run(row, files.get(row["id"]), sent.get(row["id"], [])), RUN_DEFAULTS) for row in rows]
        runs.sort(key=lambda run: (run.get("queuedAt") or "", run["id"]), reverse=True)
        return runs[:MAX_RUNS]


def output_key(run: dict[str, Any]) -> str | None:
    name = run.get("file") or ""
    if not RUN_ID_RE.fullmatch(str(run.get("id", ""))) or not re.fullmatch(r"[a-z]+-\d{14}-[0-9a-f]{8}\.(pdf|xlsx|csv)", name):
        return None
    return f"{OUTPUT_PREFIX}/{name}"


def write_output(name: str, body: bytes) -> None:
    get_store().write_bytes(f"{OUTPUT_PREFIX}/{name}", body)


def read_output(run: dict[str, Any]) -> bytes | None:
    """The file of a normal run (kept in the system store). Big exports are on disk: see output_path."""
    if run.get("spooled"):
        return None
    key = output_key(run)
    return get_store().read_bytes(key) if key else None


def output_path(run: dict[str, Any]) -> Path | None:
    """The file of a big export (written to disk by rendering/spool.py), when it is still there."""
    if not run.get("spooled") or output_key(run) is None:
        return None
    from backend.app.rendering.spool import export_path  # noqa: PLC0415
    path = export_path(run["file"])
    return path if path.is_file() else None


def output_keys() -> list[str]:
    return get_store().keys(OUTPUT_PREFIX)


def _remove_file(run: dict[str, Any]) -> None:
    get_entities().delete(ent.RUN_FILES, run["id"])
    key = output_key(run)
    if key is None:
        return
    if run.get("spooled"):
        path = output_path(run)
        if path is not None:
            path.unlink(missing_ok=True)
        return
    get_store().delete(key)


def _write(run: dict[str, Any]) -> None:
    records = get_records()
    first = records.run(run["id"]) is None
    records.put_run(_row(run, first=first))
    db = get_entities()
    file_row = _file_row(run)
    if file_row is not None:
        db.put(ent.RUN_FILES, file_row)
    elif not first:
        db.delete(ent.RUN_FILES, run["id"])
    for item in _delivery_rows(run):
        db.put(ent.RUN_DELIVERIES, item)


def save_run(run: dict[str, Any], keep_for_job: int | None = None) -> None:
    """Insert or update a job's run. Output beyond a schedule's `keep` count, and of runs beyond MAX_RUNS, is removed
    (the rows stay in the run log)."""
    with LOCK:
        _write(run)
        runs = load_runs()
        if keep_for_job is not None and run.get("jobId"):
            done = [r for r in runs if r.get("jobId") == run["jobId"] and r.get("file")]
            for old in done[keep_for_job:]:
                _remove_file(old)
        for old in runs[MAX_RUNS:]:
            _remove_file(old)


def delete_runs(predicate) -> int:
    """Remove runs from the Runs list with their files and sends. The run log keeps the row (removedAt is set), so
    who ran what stays recorded."""
    with LOCK:
        db = get_entities()
        records = get_records()
        removed = 0
        for run in load_runs():
            if predicate(run) and run.get("status") not in ("queued", "running"):
                _remove_file(run)
                db.delete_where(ent.RUN_DELIVERIES, {"runId": run["id"]})
                records.put_run({"id": run["id"], "removedAt": model.now()})
                removed += 1
        return removed


def upgrade_runs(store: Any) -> int:
    """rs_job_runs -> rows of runs (+ run_files, run_deliveries), once (the old table is kept)."""
    from backend.app.storage.routed import UPGRADE_KEY  # noqa: PLC0415
    state = store.read_json(UPGRADE_KEY, None) or {}
    if state.get("step4b"):
        return 0
    moved = 0
    with LOCK:
        for old in get_entities().find(ent.JOB_RUNS):
            if get_records().run(old["id"]) is None:
                _write(_with_defaults(old, RUN_DEFAULTS))
                moved += 1
    store.write_json(UPGRADE_KEY, {**(store.read_json(UPGRADE_KEY, None) or {}), "step4b": model.iso(model.now()), "jobRuns": moved})
    if moved:
        from backend.app.logs import LOG  # noqa: PLC0415
        LOG.info("Schema v2 (runs): %d background runs moved into runs, run_files and run_deliveries", moved)
    return moved
