"""Schema v2 step 4a: schedules and schedule_targets.

The scheduler and the /server screens keep using one dict per schedule (jobs/store.py):

    {"id", "label", "reportId", "folder", "format", "parameters", "recurrence": {"type", …}, "timezone", "enabled",
     "keep", "owner", "createdAt", "updatedAt", "nextRunAt", "lastRun": {…},
     "email": {"to", "subject", "message", "attach"}, "deliver": [{"id", "type": sftp|http|folder, …}]}

Stored as
    schedules          one row per schedule; linked to the server report (server_report_id) and its owner (owner_id)
    schedule_targets   one row per destination: e-mail is one more target type next to sftp, api (http) and folder.
                       Passwords never go here: they are rows of the secrets table (owner schedule_target).
"""
from __future__ import annotations

import json
from typing import Any

from backend.app.db import entities as ent
from backend.app.db import model

EMAIL_TARGET = "email"
_TYPE_IN_TABLE = {"http": "api"}                 # the table says "api"; the service keeps its "http"
_TYPE_IN_SERVICE = {value: key for key, value in _TYPE_IN_TABLE.items()}
_TARGET_COLUMNS = ("id", "type", "label", "fileName", "enabled")
_SCHEDULE_COLUMNS = {"id", "label", "reportId", "format", "parameters", "recurrence", "timezone", "enabled", "keep", "owner",
                     "createdAt", "updatedAt", "nextRunAt", "email", "deliver"}


def _db() -> ent.EntityBackend:
    return ent.get_entities()


def _store() -> Any:
    from backend.app.storage.store import get_store  # noqa: PLC0415
    return get_store()


class _Links:
    """server_report_id and owner_id for the rows being written (looked up once per save)."""

    def __init__(self, db: ent.EntityBackend) -> None:
        self.db = db
        self._reports: dict[tuple[str, str], str | None] = {}
        self._users: dict[str, str] | None = None

    def report(self, folder: str, code: str) -> str | None:
        key = (folder or "", code)
        if key not in self._reports:
            found = None
            try:
                folder_id = _store().folder_id("reports", folder or "") if folder else None
            except AttributeError:                # a store without folder ids
                folder_id = None
            where = {"reportCode": code, **({"folderId": folder_id} if folder_id else {})}
            rows = self.db.find(ent.SERVER_REPORTS, where) if code else []
            if len(rows) == 1 or (folder_id and rows):
                found = rows[0]["serverReportId"]
            self._reports[key] = found
        return self._reports[key]

    def user(self, username: str) -> str | None:
        if self._users is None:
            self._users = {row["username"].casefold(): row["userId"] for row in self.db.find(ent.USERS_V2)}
        return self._users.get(str(username or "").casefold())


def _schedule_row(job: dict[str, Any], links: _Links) -> dict[str, Any]:
    recurrence = dict(job.get("recurrence") or {})
    last = job.get("lastRun") if isinstance(job.get("lastRun"), dict) else None
    row = {
        "scheduleId": job["id"], "name": str(job.get("label") or "")[:120] or None,
        "serverReportId": links.report(str(job.get("folder") or ""), str(job.get("reportId") or "")),
        "reportCode": job.get("reportId"), "ownerId": links.user(job.get("owner", "")), "ownerName": job.get("owner"),
        "outputFormat": job.get("format"), "parameters": job.get("parameters") or {},
        "repeatType": recurrence.pop("type", None) or "once", "repeatRule": recurrence,
        "timeZone": job.get("timezone"), "isEnabled": bool(job.get("enabled")), "keepLastRuns": job.get("keep"),
        "nextRunAt": job.get("nextRunAt") or None, "lastRunId": (last or {}).get("runId"),
        "createdAt": job.get("createdAt") or None, "updatedAt": job.get("updatedAt") or None,
    }
    # anything else the service keeps (folder, lastRun, newer fields) goes to the extra column
    row.update({name: value for name, value in job.items() if name not in _SCHEDULE_COLUMNS})
    return row


def _target_rows(job: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    if isinstance(job.get("email"), dict):
        rows.append({"scheduleId": job["id"], "targetId": EMAIL_TARGET, "position": 0, "targetType": "email", "label": "E-mail",
                     "settings": job["email"], "fileNamePattern": None, "isEnabled": bool(job["email"].get("to"))})
    for position, target in enumerate(job.get("deliver") or [], 1):
        rows.append({"scheduleId": job["id"], "targetId": str(target["id"]), "position": position,
                     "targetType": _TYPE_IN_TABLE.get(target.get("type"), target.get("type")),
                     "label": str(target.get("label") or "")[:80] or None,
                     "settings": {k: v for k, v in target.items() if k not in _TARGET_COLUMNS},
                     "fileNamePattern": target.get("fileName"), "isEnabled": target.get("enabled", True) is not False})
    return rows


def _job(row: dict[str, Any], targets: list[dict[str, Any]]) -> dict[str, Any]:
    known = set(ent.SCHEDULES.fields)
    job = {"id": row["scheduleId"], "label": row.get("name") or row.get("reportCode") or "", "reportId": row.get("reportCode") or "",
           "format": row.get("outputFormat") or "pdf", "parameters": row.get("parameters") or {},
           "recurrence": {"type": row.get("repeatType") or "once", **(row.get("repeatRule") or {})},
           "timezone": row.get("timeZone") or "", "enabled": bool(row.get("isEnabled")), "keep": row.get("keepLastRuns") or 10,
           "owner": row.get("ownerName") or "", "createdAt": model.iso(row["createdAt"]) if row.get("createdAt") else "",
           "updatedAt": model.iso(row["updatedAt"]) if row.get("updatedAt") else "",
           "nextRunAt": model.iso(row["nextRunAt"]) if row.get("nextRunAt") else ""}
    job.update({name: value for name, value in row.items() if name not in known and name != "orgId"})
    deliver = []
    for target in sorted(targets, key=lambda item: item.get("position") or 0):
        if target["targetType"] == "email":
            job["email"] = target.get("settings") or {}
            continue
        deliver.append({"id": target["targetId"], "type": _TYPE_IN_SERVICE.get(target["targetType"], target["targetType"]),
                        "label": target.get("label") or "", "fileName": target.get("fileNamePattern") or "",
                        "enabled": target.get("isEnabled", True) is not False, **(target.get("settings") or {})})
    if deliver:
        job["deliver"] = deliver
    return job


def load_schedules() -> list[dict[str, Any]]:
    db = _db()
    targets: dict[str, list[dict[str, Any]]] = {}
    for target in db.find(ent.SCHEDULE_TARGETS):
        targets.setdefault(target["scheduleId"], []).append(target)
    return [_job(row, targets.get(row["scheduleId"], [])) for row in db.find(ent.SCHEDULES)]


def save_schedules(items: list[dict[str, Any]]) -> None:
    """Make the tables equal to this list (a removed schedule takes its targets along; its secrets are dropped by
    jobs/delivery.py). Only rows that changed are written."""
    db = _db()
    links = _Links(db)
    current = {row["scheduleId"]: row for row in db.find(ent.SCHEDULES)}
    current_targets = {(row["scheduleId"], row["targetId"]): row for row in db.find(ent.SCHEDULE_TARGETS)}
    wanted_targets: set[tuple[str, str]] = set()
    for job in items:
        row = _schedule_row(job, links)
        if not _same(ent.SCHEDULES, current.get(job["id"]), row):
            db.put(ent.SCHEDULES, row)
        for target in _target_rows(job):
            key = (target["scheduleId"], target["targetId"])
            wanted_targets.add(key)
            if not _same(ent.SCHEDULE_TARGETS, current_targets.get(key), target):
                db.put(ent.SCHEDULE_TARGETS, target)
    for key in set(current_targets) - wanted_targets:
        db.delete(ent.SCHEDULE_TARGETS, *key)
    for schedule_id in set(current) - {job["id"] for job in items}:
        db.delete(ent.SCHEDULES, schedule_id)


def _same(entity: ent.Entity, stored: dict[str, Any] | None, wanted: dict[str, Any]) -> bool:
    if stored is None:
        return False
    a = ent.encode(entity, {**stored, "orgId": stored.get("orgId") or model.org_id()})
    b = ent.encode(entity, {**wanted, "orgId": stored.get("orgId") or model.org_id()})
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def upgrade_schedules(store: Any) -> int:
    """rs_job_schedules -> schedules + schedule_targets, once (the old table is kept)."""
    from backend.app.storage.routed import UPGRADE_KEY  # noqa: PLC0415
    state = store.read_json(UPGRADE_KEY, None) or {}
    if state.get("step4a"):
        return 0
    db = _db()
    old = db.find(ent.JOB_SCHEDULES)
    moved = 0
    if old and not db.find(ent.SCHEDULES):
        save_schedules(old)
        moved = len(old)
    store.write_json(UPGRADE_KEY, {**(store.read_json(UPGRADE_KEY, None) or {}), "step4a": model.iso(model.now()), "schedules": moved})
    if moved:
        from backend.app.logs import LOG  # noqa: PLC0415
        LOG.info("Schema v2 (scheduler): %d schedules moved into schedules and schedule_targets", moved)
    return moved
