"""Where schedules, runs and their output files are kept: rs_job_schedules, rs_job_runs and the store's jobs/output/.

(Older installations kept them in jobs/schedules.json and jobs/runs.json; db/upgrade.py moves those at start-up.)
All reads and writes go through one lock, because the scheduler thread and the web requests both change them."""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

from backend.app.db.entities import JOB_RUNS, JOB_SCHEDULES, get_entities
from backend.app.storage.store import get_store

MAX_RUNS = 500
RUN_ID_RE = re.compile(r"^[a-z]+-\d{14}-[0-9a-f]{8}$")
LOCK = threading.RLock()

OUTPUT_PREFIX = "jobs/output"

# What a run or schedule holds when a field is empty (the table leaves empty fields out).
RUN_DEFAULTS = {"jobId": "", "jobLabel": "", "startedAt": "", "finishedAt": "", "rowCount": 0, "error": "", "file": "", "size": 0,
                "email": "", "parameters": {}, "spooled": False}
SCHEDULE_DEFAULTS = {"nextRunAt": "", "lastRun": None, "parameters": {}, "enabled": False}


def _with_defaults(item: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    return {**defaults, **item}


def load_schedules() -> list[dict[str, Any]]:
    with LOCK:
        items = get_entities().find(JOB_SCHEDULES)
        return sorted((_with_defaults(item, SCHEDULE_DEFAULTS) for item in items), key=lambda job: (job.get("createdAt") or "", job["id"]))


def save_schedules(items: list[dict[str, Any]]) -> None:
    with LOCK:
        get_entities().replace_all(JOB_SCHEDULES, items)


def load_runs() -> list[dict[str, Any]]:
    """Newest first."""
    with LOCK:
        runs = [_with_defaults(item, RUN_DEFAULTS) for item in get_entities().find(JOB_RUNS)]
        return sorted(runs, key=lambda run: (run.get("queuedAt") or "", run["id"]), reverse=True)


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
    key = output_key(run)
    if key is None:
        return
    if run.get("spooled"):
        path = output_path(run)
        if path is not None:
            path.unlink(missing_ok=True)
        return
    get_store().delete(key)


def save_run(run: dict[str, Any], keep_for_job: int | None = None) -> None:
    """Insert or replace a run. Old runs beyond MAX_RUNS, and output beyond a schedule's `keep` count, are
    removed with their files."""
    with LOCK:
        entities = get_entities()
        entities.put(JOB_RUNS, run)
        runs = load_runs()
        if keep_for_job is not None and run.get("jobId"):
            done = [r for r in runs if r.get("jobId") == run["jobId"] and r.get("file")]
            for old in done[keep_for_job:]:
                _remove_file(old)
                entities.put(JOB_RUNS, {**old, "file": "", "size": 0})
        for old in runs[MAX_RUNS:]:
            _remove_file(old)
            entities.delete(JOB_RUNS, old["id"])


def delete_runs(predicate) -> int:
    with LOCK:
        entities = get_entities()
        removed = 0
        for run in load_runs():
            if predicate(run) and run.get("status") not in ("queued", "running"):
                _remove_file(run)
                entities.delete(JOB_RUNS, run["id"])
                removed += 1
        return removed
