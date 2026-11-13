"""Where schedules, runs and their output files are kept: <data>/jobs/schedules.json, runs.json and output/.

All reads and writes go through one lock, because the scheduler thread and the web requests both change them."""
from __future__ import annotations

import re
import threading
from typing import Any

from backend.app.storage.store import get_store

MAX_RUNS = 500
RUN_ID_RE = re.compile(r"^[a-z]+-\d{14}-[0-9a-f]{8}$")
LOCK = threading.RLock()


OUTPUT_PREFIX = "jobs/output"


def _read(name: str, key: str) -> list[dict[str, Any]]:
    raw = get_store().read_json(f"jobs/{name}")
    items = raw.get(key, []) if isinstance(raw, dict) else []
    return [item for item in items if isinstance(item, dict)]


def load_schedules() -> list[dict[str, Any]]:
    with LOCK:
        return _read("schedules.json", "schedules")


def save_schedules(items: list[dict[str, Any]]) -> None:
    with LOCK:
        get_store().write_json("jobs/schedules.json", {"schedules": items})


def load_runs() -> list[dict[str, Any]]:
    with LOCK:
        return _read("runs.json", "runs")


def output_key(run: dict[str, Any]) -> str | None:
    name = run.get("file") or ""
    if not RUN_ID_RE.fullmatch(str(run.get("id", ""))) or not re.fullmatch(r"[a-z]+-\d{14}-[0-9a-f]{8}\.(pdf|xlsx|csv)", name):
        return None
    return f"{OUTPUT_PREFIX}/{name}"


def write_output(name: str, body: bytes) -> None:
    get_store().write_bytes(f"{OUTPUT_PREFIX}/{name}", body)


def read_output(run: dict[str, Any]) -> bytes | None:
    key = output_key(run)
    return get_store().read_bytes(key) if key else None


def output_keys() -> list[str]:
    return get_store().keys(OUTPUT_PREFIX)


def _remove_file(run: dict[str, Any]) -> None:
    key = output_key(run)
    if key is not None:
        get_store().delete(key)


def save_run(run: dict[str, Any], keep_for_job: int | None = None) -> None:
    """Insert or replace a run (newest first). Old runs beyond MAX_RUNS, and output beyond a schedule's
    `keep` count, are removed with their files."""
    with LOCK:
        runs = [r for r in _read("runs.json", "runs") if r.get("id") != run["id"]]
        runs.insert(0, run)
        if keep_for_job is not None and run.get("jobId"):
            done = [r for r in runs if r.get("jobId") == run["jobId"] and r.get("file")]
            for old in done[keep_for_job:]:
                _remove_file(old)
                old["file"] = ""
                old["size"] = 0
        for old in runs[MAX_RUNS:]:
            _remove_file(old)
        get_store().write_json("jobs/runs.json", {"runs": runs[:MAX_RUNS]})


def delete_runs(predicate) -> int:
    with LOCK:
        runs = _read("runs.json", "runs")
        keep = []
        removed = 0
        for run in runs:
            if predicate(run) and run.get("status") not in ("queued", "running"):
                _remove_file(run)
                removed += 1
            else:
                keep.append(run)
        get_store().write_json("jobs/runs.json", {"runs": keep})
        return removed
