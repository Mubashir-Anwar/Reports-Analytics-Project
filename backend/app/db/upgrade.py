"""Start-up step: move the older JSON documents into the typed tables (once; safe to run again).

    users.json                 -> rs_users
    api-keys.json              -> rs_api_keys
    folder-permissions.json    -> rs_folder_permissions   (root folder "" stored as "/")
    versions/<ID>/<n>.json     -> rs_report_versions
    history/<ID>.json          -> rs_report_history
    jobs/schedules.json        -> rs_job_schedules
    jobs/runs.json             -> rs_job_runs           (the output files stay in jobs/output/)

Every document is written into its table first and only then moved aside to migrated/<key> (kept, never
deleted), so a crash half-way loses nothing and the next start finishes the job. Keys and ids are fixed by the
content, so two instances starting at the same time write the same rows.
"""
from __future__ import annotations

import hashlib
from typing import Any

from backend.app.db import entities as ent, model
from backend.app.logs import LOG


def _archive(store: Any, key: str, payload: Any, private: bool) -> None:
    store.write_json(f"migrated/{key}", payload, private=private)
    store.delete(key)


def _history_id(report_id: str, index: int, at: str) -> str:
    try:
        millis = int(model.parse_time(at).timestamp() * 1000)
    except (TypeError, ValueError):
        millis = 0
    return f"{millis:012x}{hashlib.sha256(f'{report_id}|{index}'.encode()).hexdigest()[:20]}"


def move_documents(store: Any, entities: ent.EntityBackend) -> dict[str, int]:
    counts = {"users": 0, "apiKeys": 0, "permissions": 0, "versions": 0, "history": 0, "schedules": 0, "jobRuns": 0}

    raw = store.read_json("users.json")
    if isinstance(raw, dict):
        for user in raw.get("users", []):
            if isinstance(user, dict) and user.get("username"):
                entities.put(ent.USERS, user)
                counts["users"] += 1
        _archive(store, "users.json", raw, True)

    raw = store.read_json("api-keys.json")
    if isinstance(raw, dict):
        for key in raw.get("keys", []):
            if isinstance(key, dict) and key.get("id"):
                entities.put(ent.API_KEYS, key)
                counts["apiKeys"] += 1
        _archive(store, "api-keys.json", raw, True)

    raw = store.read_json("folder-permissions.json")
    if isinstance(raw, dict):
        for folder, entry in (raw.get("folders") or {}).items():
            if isinstance(entry, dict):
                entities.put(ent.FOLDER_PERMISSIONS, {"folder": folder or "/", "users": entry.get("users") or {}, "roles": entry.get("roles") or {}})
                counts["permissions"] += 1
        _archive(store, "folder-permissions.json", raw, False)

    for key, payload in store.read_many("versions"):
        parts = key.split("/")
        if len(parts) == 3 and parts[2][:-5].isdigit() and isinstance(payload, dict):
            entities.put(ent.REPORT_VERSIONS, {"reportId": parts[1], "version": int(parts[2][:-5]), "savedAt": payload.get("savedAt"),
                                               "action": str(payload.get("action") or "")[:32], "user": str(payload.get("user") or "")[:128],
                                               "folder": payload.get("folder"), "title": str(payload.get("title") or "")[:200],
                                               "definition": payload.get("definition")})
            counts["versions"] += 1
            _archive(store, key, payload, False)

    for key, payload in store.read_many("history"):
        report_id = key.split("/", 1)[1][:-5] if key.count("/") == 1 else ""
        if not report_id or not isinstance(payload, list):
            continue
        for index, item in enumerate(payload):
            if isinstance(item, dict) and item.get("at"):
                entities.put(ent.REPORT_HISTORY, {"id": _history_id(report_id, index, item["at"]), "reportId": report_id, "at": item["at"],
                                                  "action": str(item.get("action") or "?")[:32], "user": str(item.get("user") or "")[:128],
                                                  "detail": str(item.get("detail") or "")[:300]})
                counts["history"] += 1
        _archive(store, key, payload, False)

    raw = store.read_json("jobs/schedules.json")
    if isinstance(raw, dict):
        for job in raw.get("schedules", []):
            if isinstance(job, dict) and job.get("id"):
                entities.put(ent.JOB_SCHEDULES, job)
                counts["schedules"] += 1
        _archive(store, "jobs/schedules.json", raw, False)

    raw = store.read_json("jobs/runs.json")
    if isinstance(raw, dict):
        for run in raw.get("runs", []):
            if isinstance(run, dict) and run.get("id") and run.get("queuedAt"):
                entities.put(ent.JOB_RUNS, {**run, "error": str(run.get("error") or "")[:500], "email": str(run.get("email") or "")[:500]})
                counts["jobRuns"] += 1
        _archive(store, "jobs/runs.json", raw, False)

    if any(counts.values()):
        LOG.info("Moved older documents into the typed tables: %s", counts)
    return counts


def upgrade_storage() -> dict[str, int]:
    """Called once at start-up: tables exist, older documents are moved."""
    from backend.app.storage.store import get_store  # noqa: PLC0415
    store = get_store()
    counts = move_documents(store, ent.get_entities())
    counts.update(store.upgrade_v2())                   # reports, drafts, folders (and adapters on a database) -> v2 tables
    return counts
