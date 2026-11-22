"""Typed tables for the service's own objects (accounts, API keys, folder permissions, report versions, history).

Each entity has real columns, the same on every backend:

    files    <data dir>/records/entities/<name>.json
    SQL      rs_<name> (SQL Server, Oracle, MySQL, PostgreSQL, SQLite)  — composite primary keys, indexes
    MongoDB  rs_<name> collection, _id built from the key, indexes on the lookup fields

Schema v2 tables have plain names without the rs_ prefix (folders, workspace_reports, server_reports,
report_versions, report_history): the service keeps them in a database of its own ("reporting" by default).
On files they are <data dir>/records/tables/<table>.json, or one file per row for the tables that hold report
definitions (<table>/<folderId>/<reportCode>.json), so saving one report never rewrites the others.

JSON columns are stored as text everywhere, exactly as given (report definitions contain "$match"-style keys).
Fields a record has that the entity does not list go into an "extra" JSON column, so nothing is ever lost.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.app.db import model


@dataclass(frozen=True)
class Entity:
    name: str
    fields: dict[str, tuple[str, int | None, bool]]          # name -> (str|int|bool|time|json, size, required)
    key: tuple[str, ...]
    indexes: tuple[tuple[str, ...], ...] = field(default_factory=tuple)
    table_name: str = ""                                     # schema v2: the plain table name (no rs_ prefix)
    row_files: tuple[str, ...] = ()                          # files backend: one file per row, at <table>/<these fields>.json

    @property
    def table(self) -> str:
        return self.table_name or f"rs_{self.name}"


USERS = Entity("users", {
    "username": ("str", 64, True), "orgId": ("str", 64, True), "rights": ("json", None, False),
    "superAdmin": ("bool", None, False), "role": ("str", 16, False), "disabled": ("bool", None, False),
    "salt": ("str", 64, False), "hash": ("str", 128, False), "iterations": ("int", None, False),
    "sessionVersion": ("int", None, False), "createdAt": ("time", None, False), "createdBy": ("str", 128, False),
    "lastLoginAt": ("time", None, False),
}, key=("username",))

API_KEYS = Entity("api_keys", {
    "id": ("str", 32, True), "orgId": ("str", 64, True), "name": ("str", 64, True), "hash": ("str", 64, True),
    "folders": ("json", None, False), "allowedIps": ("json", None, False), "expiresAt": ("time", None, False),
    "disabled": ("bool", None, False), "createdBy": ("str", 128, False), "createdAt": ("time", None, False),
    "lastUsedAt": ("time", None, False),
}, key=("id",))

FOLDER_PERMISSIONS = Entity("folder_permissions", {
    "folder": ("str", 240, True), "orgId": ("str", 64, True), "users": ("json", None, False), "roles": ("json", None, False),
}, key=("folder",))

REPORT_VERSIONS = Entity("report_versions", {
    "reportId": ("str", 80, True), "version": ("int", None, True), "orgId": ("str", 64, True), "savedAt": ("time", None, False),
    "action": ("str", 32, False), "user": ("str", 128, False), "folder": ("str", 240, False), "title": ("str", 200, False),
    "definition": ("json", None, False),
}, key=("reportId", "version"))

REPORT_HISTORY = Entity("report_history", {
    "id": ("str", 32, True), "reportId": ("str", 80, True), "orgId": ("str", 64, True), "at": ("time", None, True),
    "action": ("str", 32, True), "user": ("str", 128, False), "detail": ("str", 300, False),
}, key=("id",), indexes=(("reportId", "at"),))

JOB_SCHEDULES = Entity("job_schedules", {
    "id": ("str", 40, True), "orgId": ("str", 64, True), "label": ("str", 120, False), "reportId": ("str", 80, True),
    "format": ("str", 8, True), "parameters": ("json", None, False), "recurrence": ("json", None, True), "timezone": ("str", 64, False),
    "enabled": ("bool", None, False), "keep": ("int", None, False), "email": ("json", None, False), "owner": ("str", 128, True),
    "createdAt": ("time", None, False), "updatedAt": ("time", None, False), "nextRunAt": ("time", None, False),
    "lastRun": ("json", None, False),
}, key=("id",), indexes=(("owner",), ("nextRunAt",)))

JOB_RUNS = Entity("job_runs", {
    "id": ("str", 40, True), "orgId": ("str", 64, True), "jobId": ("str", 40, False), "jobLabel": ("str", 120, False),
    "kind": ("str", 16, False), "reportId": ("str", 80, False), "title": ("str", 200, False), "format": ("str", 8, False),
    "parameters": ("json", None, False), "owner": ("str", 128, True), "status": ("str", 16, True), "queuedAt": ("time", None, False),
    "startedAt": ("time", None, False), "finishedAt": ("time", None, False), "rowCount": ("int", None, False),
    "error": ("str", 500, False), "file": ("str", 64, False), "size": ("int", None, False), "email": ("str", 500, False),
}, key=("id",), indexes=(("queuedAt",), ("jobId", "queuedAt"), ("owner", "status")))

_DEFINITION = {
    "folder": ("str", 240, True), "reportId": ("str", 80, True), "orgId": ("str", 64, True), "title": ("str", 200, False),
    "dataSource": ("str", 64, False), "queryLanguage": ("str", 32, False), "collection": ("str", 200, False),
    "updatedAt": ("time", None, False), "definition": ("json", None, True),
}
# Published reports (the server repository) and designer drafts: one row per report, the full definition as JSON.
REPORTS = Entity("reports", dict(_DEFINITION), key=("folder", "reportId"), indexes=(("reportId",),))
DRAFTS = Entity("drafts", dict(_DEFINITION), key=("folder", "reportId"), indexes=(("reportId",),))
# Folders that exist even when empty ("reports" and "workspace" areas; "list" = the saved folder list).
FOLDERS = Entity("folders", {"area": ("str", 16, True), "path": ("str", 240, True), "orgId": ("str", 64, True)}, key=("area", "path"))
# Data adapters (no secrets here: URLs and passwords stay in the private secrets document).
DATA_SOURCES = Entity("data_sources", {
    "id": ("str", 40, True), "orgId": ("str", 64, True), "position": ("int", None, True), "name": ("str", 120, True),
    "type": ("str", 16, False), "storage": ("str", 8, False), "database": ("str", 128, False), "folder": ("str", 240, False),
}, key=("id",))

# Analytics (Phase 4): datasets imported to Parquet files, relationships between them (the model), dashboards, refresh log.
# The data itself is never stored here: only definitions and status. Parquet files live in REPORTING_ANALYTICS_DIR.
ANALYTICS_DATASETS = Entity("analytics_datasets", {
    "id": ("str", 40, True), "orgId": ("str", 64, True), "name": ("str", 120, True), "folder": ("str", 240, False),
    "description": ("str", 500, False), "connectionId": ("str", 64, True), "queryLanguage": ("str", 32, True),
    "collection": ("str", 200, False), "querySource": ("json", None, True), "columns": ("json", None, False),
    "refreshEveryMinutes": ("int", None, False), "status": ("str", 16, False), "rowCount": ("int", None, False),
    "sizeBytes": ("int", None, False), "refreshedAt": ("time", None, False), "nextRefreshAt": ("time", None, False),
    "lastError": ("str", 500, False), "file": ("str", 80, False), "createdBy": ("str", 128, False),
    "createdAt": ("time", None, False), "updatedBy": ("str", 128, False), "updatedAt": ("time", None, False),
}, key=("id",), indexes=(("name",), ("nextRefreshAt",)))
ANALYTICS_RELATIONSHIPS = Entity("analytics_relationships", {
    "id": ("str", 40, True), "orgId": ("str", 64, True), "fromDataset": ("str", 40, True), "fromColumn": ("str", 120, True),
    "toDataset": ("str", 40, True), "toColumn": ("str", 120, True), "active": ("bool", None, False),
    "createdBy": ("str", 128, False), "createdAt": ("time", None, False),
}, key=("id",), indexes=(("fromDataset",), ("toDataset",)))
ANALYTICS_DASHBOARDS = Entity("analytics_dashboards", {
    "id": ("str", 40, True), "orgId": ("str", 64, True), "name": ("str", 120, True), "folder": ("str", 240, False),
    "description": ("str", 500, False), "tiles": ("json", None, False), "filters": ("json", None, False),
    "owner": ("str", 128, False), "createdAt": ("time", None, False), "updatedBy": ("str", 128, False),
    "updatedAt": ("time", None, False),
}, key=("id",), indexes=(("name",),))
ANALYTICS_REFRESHES = Entity("analytics_refreshes", {
    "id": ("str", 40, True), "orgId": ("str", 64, True), "datasetId": ("str", 40, True), "trigger": ("str", 16, False),
    "actor": ("str", 128, False), "status": ("str", 16, True), "startedAt": ("time", None, False),
    "finishedAt": ("time", None, False), "rowCount": ("int", None, False), "sizeBytes": ("int", None, False),
    "durationMs": ("int", None, False), "error": ("str", 500, False),
}, key=("id",), indexes=(("datasetId", "startedAt"),))

# Analytics → Queries: saved DAX / SQL queries over the datasets.
ANALYTICS_QUERIES = Entity("analytics_queries", {
    "id": ("str", 40, True), "orgId": ("str", 64, True), "name": ("str", 120, True), "folder": ("str", 240, False),
    "description": ("str", 500, False), "language": ("str", 8, True), "text": ("json", None, True),
    "owner": ("str", 128, False), "createdAt": ("time", None, False), "updatedBy": ("str", 128, False),
    "updatedAt": ("time", None, False),
}, key=("id",), indexes=(("name",),))

# ------------------------------------------------------------------ schema v2, step 1: folders and reports
# Folders are a tree with ids (renaming or moving one is one update); a workspace file and a server report are
# separate tables, linked both ways; versions and history belong to a server report by its id.
FOLDER_TREE = Entity("v2_folders", {
    "folderId": ("str", 32, True), "orgId": ("str", 64, True), "area": ("str", 16, True),          # workspace / server
    "parentId": ("str", 32, False), "name": ("str", 64, True), "fullPath": ("str", 240, True),
    "createdBy": ("str", 128, False), "createdAt": ("time", None, False),
}, key=("folderId",), indexes=(("area", "fullPath"), ("parentId",)), table_name="folders")

WORKSPACE_REPORTS = Entity("v2_workspace_reports", {
    "workspaceReportId": ("str", 32, True), "orgId": ("str", 64, True), "folderId": ("str", 32, True),
    "reportCode": ("str", 80, True), "title": ("str", 200, False), "definition": ("json", None, True),
    "publishStatus": ("str", 16, False),                      # draft (never published) / published / changed
    "versionNo": ("int", None, False),                        # +1 on every save: a save from an older copy is stopped
    "lockedBy": ("str", 128, False), "lockedAt": ("time", None, False),   # who has it open for editing now
    "serverReportId": ("str", 32, False), "publishedAt": ("time", None, False),
    "createdBy": ("str", 128, False), "createdAt": ("time", None, False),
    "updatedBy": ("str", 128, False), "updatedAt": ("time", None, False),
}, key=("workspaceReportId",), indexes=(("folderId", "reportCode"), ("reportCode",)), table_name="workspace_reports",
   row_files=("folderId", "reportCode"))

SERVER_REPORTS = Entity("v2_server_reports", {
    "serverReportId": ("str", 32, True), "orgId": ("str", 64, True), "folderId": ("str", 32, True),
    "reportCode": ("str", 80, True), "title": ("str", 200, False), "description": ("str", 500, False),
    "dataSourceId": ("str", 64, False), "queryLanguage": ("str", 32, False), "collectionName": ("str", 200, False),
    "currentVersion": ("int", None, False), "definition": ("json", None, True),
    "publishedBy": ("str", 128, False), "publishedAt": ("time", None, False),
    "workspaceReportId": ("str", 32, False), "updatedAt": ("time", None, False),
}, key=("serverReportId",), indexes=(("folderId", "reportCode"), ("reportCode",)), table_name="server_reports",
   row_files=("folderId", "reportCode"))

REPORT_VERSIONS_V2 = Entity("v2_report_versions", {
    "serverReportId": ("str", 32, True), "versionNo": ("int", None, True), "orgId": ("str", 64, True),
    "reportCode": ("str", 80, True), "folderPath": ("str", 240, False), "title": ("str", 200, False),
    "action": ("str", 32, False), "savedBy": ("str", 128, False), "savedAt": ("time", None, False),
    "definition": ("json", None, False),
}, key=("serverReportId", "versionNo"), indexes=(("reportCode",),), table_name="report_versions",
   row_files=("serverReportId", "versionNo"))

REPORT_HISTORY_V2 = Entity("v2_report_history", {
    "historyId": ("str", 32, True), "orgId": ("str", 64, True), "serverReportId": ("str", 32, False),
    "reportCode": ("str", 80, True), "folderPath": ("str", 240, False), "happenedAt": ("time", None, True),
    "action": ("str", 32, True), "userName": ("str", 128, False), "detail": ("str", 300, False),
}, key=("historyId",), indexes=(("serverReportId", "happenedAt"), ("reportCode",)), table_name="report_history")

V2_STEP1 = (FOLDER_TREE, WORKSPACE_REPORTS, SERVER_REPORTS, REPORT_VERSIONS_V2, REPORT_HISTORY_V2)

# ------------------------------------------------------------------ schema v2, step 2: people and access
# A user is a row with an id; each app right is a row of its own; teams (user_groups) have members; folder access
# is given to a user, a group or a role on a folder id; API keys have their folders and networks as rows.
USERS_V2 = Entity("v2_users", {
    "userId": ("str", 32, True), "orgId": ("str", 64, True), "username": ("str", 64, True),
    "fullName": ("str", 120, False), "email": ("str", 254, False), "phone": ("str", 32, False),
    "role": ("str", 16, False),                                 # viewer / designer / admin (from the rights)
    "isSuperAdmin": ("bool", None, False), "isDisabled": ("bool", None, False),
    "passwordHash": ("str", 128, False), "passwordSalt": ("str", 64, False), "passwordIterations": ("int", None, False),
    "passwordChangedAt": ("time", None, False), "mustChangePassword": ("bool", None, False),
    "sessionVersion": ("int", None, False),                     # +1 signs the user out everywhere
    "createdAt": ("time", None, False), "createdBy": ("str", 128, False), "lastLoginAt": ("time", None, False),
}, key=("userId",), indexes=(("username",), ("email",), ("phone",)), table_name="users")

USER_RIGHTS = Entity("v2_user_rights", {
    "userId": ("str", 32, True), "rightName": ("str", 16, True), "orgId": ("str", 64, True),   # designer, server, runner…
    "grantedBy": ("str", 128, False), "grantedAt": ("time", None, False),
}, key=("userId", "rightName"), table_name="user_rights")

USER_GROUPS = Entity("v2_user_groups", {
    "groupId": ("str", 32, True), "orgId": ("str", 64, True), "name": ("str", 64, True), "description": ("str", 300, False),
    "createdBy": ("str", 128, False), "createdAt": ("time", None, False),
}, key=("groupId",), indexes=(("name",),), table_name="user_groups")

USER_GROUP_MEMBERS = Entity("v2_user_group_members", {
    "groupId": ("str", 32, True), "userId": ("str", 32, True), "orgId": ("str", 64, True),
    "addedBy": ("str", 128, False), "addedAt": ("time", None, False),
}, key=("groupId", "userId"), indexes=(("userId",),), table_name="user_group_members")

FOLDER_ACCESS = Entity("v2_folder_permissions", {
    "permissionId": ("str", 32, True), "orgId": ("str", 64, True),
    "folderId": ("str", 32, False),                             # empty = the root (every folder)
    "principalType": ("str", 8, True),                          # user / group / role
    "principalId": ("str", 32, True),                           # user_id, group_id or the role name
    "accessLevel": ("str", 8, True),                            # none (hidden) / run / manage
    "grantedBy": ("str", 128, False), "grantedAt": ("time", None, False),
}, key=("permissionId",), indexes=(("folderId",), ("principalType", "principalId")), table_name="folder_permissions")

PASSWORD_RESETS = Entity("v2_password_resets", {
    "resetId": ("str", 32, True), "orgId": ("str", 64, True), "userId": ("str", 32, True),
    "method": ("str", 16, True),                                # email_link / admin_approval
    "status": ("str", 16, True),                                # waiting / approved / rejected / used / expired
    "tokenHash": ("str", 64, False),                            # the link itself is never stored
    "requestedAt": ("time", None, True), "expiresAt": ("time", None, False),
    "decidedBy": ("str", 128, False), "decidedAt": ("time", None, False), "usedAt": ("time", None, False),
    "clientIp": ("str", 64, False),
}, key=("resetId",), indexes=(("userId", "requestedAt"), ("status",)), table_name="password_resets")

API_KEYS_V2 = Entity("v2_api_keys", {
    "apiKeyId": ("str", 32, True), "orgId": ("str", 64, True), "name": ("str", 64, True),
    "keyHash": ("str", 64, True),                               # the key itself is never stored
    "createdBy": ("str", 128, False), "createdAt": ("time", None, False), "expiresAt": ("time", None, False),
    "lastUsedAt": ("time", None, False), "isDisabled": ("bool", None, False),
}, key=("apiKeyId",), table_name="api_keys")

API_KEY_FOLDERS = Entity("v2_api_key_folders", {
    "apiKeyId": ("str", 32, True), "folderId": ("str", 32, True), "orgId": ("str", 64, True),
}, key=("apiKeyId", "folderId"), table_name="api_key_folders")

API_KEY_NETWORKS = Entity("v2_api_key_networks", {
    "apiKeyId": ("str", 32, True), "ipRange": ("str", 64, True), "orgId": ("str", 64, True),   # e.g. 10.0.0.0/24
}, key=("apiKeyId", "ipRange"), table_name="api_key_networks")

# ------------------------------------------------------------------ schema v2, step 3: secrets
# Every password, URL with a password, key and token: one row each, linked to its owner, encrypted with
# REPORTING_SECRETS_KEY (Fernet) when it is set. Nothing else in the database holds a secret.
SECRETS = Entity("v2_secrets", {
    "secretId": ("str", 32, True), "orgId": ("str", 64, True),
    "ownerType": ("str", 24, True),          # system / data_source / schedule_target
    "ownerId": ("str", 120, True),           # "service", the data source id, or "<schedule id>/<target id>"
    "secretName": ("str", 120, True),        # admin_token, uri, password, privateKey, header:Authorization …
    "encryptedValue": ("json", None, True),  # the value (JSON), encrypted when keyVersion is fernet-v1
    "keyVersion": ("str", 16, True),         # fernet-v1 / plain (no REPORTING_SECRETS_KEY)
    "updatedBy": ("str", 128, False), "updatedAt": ("time", None, False),
}, key=("secretId",), indexes=(("ownerType", "ownerId"),), table_name="secrets")

V2_STEP2 = (USERS_V2, USER_RIGHTS, USER_GROUPS, USER_GROUP_MEMBERS, FOLDER_ACCESS, PASSWORD_RESETS,
            API_KEYS_V2, API_KEY_FOLDERS, API_KEY_NETWORKS)

ENTITIES = (USERS, API_KEYS, FOLDER_PERMISSIONS, REPORT_VERSIONS, REPORT_HISTORY, JOB_SCHEDULES, JOB_RUNS,
            REPORTS, DRAFTS, FOLDERS, DATA_SOURCES,
            ANALYTICS_DATASETS, ANALYTICS_RELATIONSHIPS, ANALYTICS_DASHBOARDS, ANALYTICS_REFRESHES, ANALYTICS_QUERIES,
            *V2_STEP1, *V2_STEP2, SECRETS)


# ------------------------------------------------------------------ record <-> stored values
def encode(entity: Entity, record: dict[str, Any]) -> dict[str, Any]:
    """Known fields cleaned to their types; anything else goes to "extra"."""
    out: dict[str, Any] = {}
    record = {"orgId": model.org_id(), **record}
    for name, (kind, size, required) in entity.fields.items():
        value = record.get(name)
        if value is None or (value == "" and kind != "str"):
            if required:
                raise ValueError(f"{entity.name}.{name} is required")
            out[name] = None
        elif kind == "str":
            text = str(value)
            if required and not text:
                raise ValueError(f"{entity.name}.{name} is required")
            if size and len(text) > size:
                raise ValueError(f"{entity.name}.{name} is longer than {size} characters")
            out[name] = text or None          # Oracle stores '' as NULL: treat both the same everywhere
        elif kind == "int":
            out[name] = int(value)
        elif kind == "bool":
            out[name] = bool(value)
        elif kind == "time":
            out[name] = model.iso(value)
        else:
            out[name] = value
    extra = {name: value for name, value in record.items() if name not in entity.fields}
    out["extra"] = extra or None
    return out


def decode(entity: Entity, stored: dict[str, Any]) -> dict[str, Any]:
    """Back to the record the service uses: empty fields are left out (as in the older JSON documents)."""
    record = {name: stored.get(name) for name in entity.fields if stored.get(name) is not None}
    record.pop("orgId", None)
    record.update(stored.get("extra") or {})
    return record


def key_of(entity: Entity, record: dict[str, Any]) -> tuple:
    return tuple(record[name] for name in entity.key)


# ------------------------------------------------------------------ backends
class EntityBackend:
    def ensure(self) -> None:
        raise NotImplementedError

    def get(self, entity: Entity, *key: Any) -> dict[str, Any] | None:
        raise NotImplementedError

    def find(self, entity: Entity, where: dict[str, Any] | None = None, order: tuple[str, bool] | None = None,
             limit: int | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    def put(self, entity: Entity, record: dict[str, Any]) -> None:
        raise NotImplementedError

    def delete(self, entity: Entity, *key: Any) -> bool:
        raise NotImplementedError

    def delete_where(self, entity: Entity, where: dict[str, Any]) -> int:
        raise NotImplementedError

    def replace_all(self, entity: Entity, records: list[dict[str, Any]]) -> None:
        """Make the stored set equal to these records (small lists: users, keys, permissions)."""
        wanted = {key_of(entity, encode(entity, item)): item for item in records}
        for current in self.find(entity):
            if key_of(entity, encode(entity, current)) not in wanted:
                self.delete(entity, *key_of(entity, encode(entity, current)))
        for item in wanted.values():
            self.put(entity, item)


_FILE_LOCK = threading.RLock()


def _write_private(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # accounts and key hashes: private
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=1)
    temporary.replace(path)


class FileEntities(EntityBackend):
    def __init__(self, root: Path):
        self.folder = root / "records" / "entities"
        self.tables = root / "records" / "tables"            # schema v2

    def ensure(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)

    def _path(self, entity: Entity) -> Path:
        return self.tables / f"{entity.table}.json" if entity.table_name else self.folder / f"{entity.name}.json"

    # ---- one file per row (tables with report definitions): <table>/<row_files values>.json
    def _row_path(self, entity: Entity, stored: dict[str, Any]) -> Path:
        parts = [str(stored.get(name) or "_") for name in entity.row_files]
        return self.tables.joinpath(entity.table, *parts[:-1], f"{parts[-1]}.json")

    def _row_candidates(self, entity: Entity, where: dict[str, Any]) -> list[Path]:
        base = self.tables / entity.table
        prefix = []
        for name in entity.row_files:
            if name not in where or where[name] is None:
                break
            prefix.append(str(where[name]))
        if len(prefix) == len(entity.row_files):
            path = base.joinpath(*prefix[:-1], f"{prefix[-1]}.json")
            return [path] if path.is_file() else []
        start = base.joinpath(*prefix)
        return sorted(start.rglob("*.json")) if start.is_dir() else []

    @staticmethod
    def _read_row(path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _load(self, entity: Entity) -> dict[str, dict[str, Any]]:
        if entity.row_files:
            rows = (self._read_row(path) for path in self._row_candidates(entity, {}))
            return {self._id(key_of(entity, row)): row for row in rows if row}
        path = self._path(entity)
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8")).get("items", {})

    def _save(self, entity: Entity, items: dict[str, dict[str, Any]]) -> None:
        _write_private(self._path(entity), {"items": items})

    @staticmethod
    def _id(key: tuple) -> str:
        return "␟".join(str(part) for part in key)

    def get(self, entity, *key):
        if entity.row_files:
            found = self.find(entity, dict(zip(entity.key, key)), limit=1)
            return found[0] if found else None
        with _FILE_LOCK:
            stored = self._load(entity).get(self._id(key))
        return decode(entity, stored) if stored else None

    def find(self, entity, where=None, order=None, limit=None):
        with _FILE_LOCK:
            if entity.row_files:
                rows = [row for row in (self._read_row(path) for path in self._row_candidates(entity, where or {})) if row]
            else:
                rows = list(self._load(entity).values())
        rows = [row for row in rows if all(row.get(name) == value for name, value in (where or {}).items())]
        if order:
            rows.sort(key=lambda row: (row.get(order[0]) is None, row.get(order[0])), reverse=order[1])
        return [decode(entity, row) for row in rows[:limit] if row]

    def put(self, entity, record):
        stored = encode(entity, record)
        with _FILE_LOCK:
            if entity.row_files:
                key = key_of(entity, stored)
                target = self._row_path(entity, stored)
                here = self._read_row(target) if target.is_file() else None
                moved = len(entity.key) == 1 and not (here and key_of(entity, here) == key)   # an update in place needs no search
                for old in self._row_candidates(entity, {}) if moved else []:
                    row = self._read_row(old)        # the same row stored under another folder / code: it moved
                    if row and key_of(entity, row) == key and old != self._row_path(entity, stored):
                        old.unlink(missing_ok=True)
                _write_private(self._row_path(entity, stored), stored)
                return
            items = self._load(entity)
            items[self._id(key_of(entity, stored))] = stored
            self._save(entity, items)

    def delete(self, entity, *key):
        with _FILE_LOCK:
            if entity.row_files:
                removed = False
                for path in self._row_candidates(entity, dict(zip(entity.key, key))):
                    row = self._read_row(path)
                    if row and key_of(entity, row) == tuple(key):
                        path.unlink(missing_ok=True)
                        removed = True
                return removed
            items = self._load(entity)
            found = items.pop(self._id(key), None)
            if found is not None:
                self._save(entity, items)
        return found is not None

    def delete_where(self, entity, where):
        with _FILE_LOCK:
            if entity.row_files:
                count = 0
                for path in self._row_candidates(entity, where):
                    row = self._read_row(path)
                    if row and all(row.get(n) == value for n, value in where.items()):
                        path.unlink(missing_ok=True)
                        count += 1
                return count
            items = self._load(entity)
            kept = {k: v for k, v in items.items() if not all(v.get(n) == value for n, value in where.items())}
            if len(kept) != len(items):
                self._save(entity, kept)
        return len(items) - len(kept)


class SqlEntities(EntityBackend):
    def __init__(self, url: str):
        from backend.app.db import sql as sql_backend
        self.sql = sql_backend
        self.engine = sql_backend.engine_for(url)
        self.tables = {entity.name: sql_backend.entity_table(entity) for entity in ENTITIES}

    def ensure(self) -> None:
        self.sql.apply_migrations(self.engine, "entities", self.sql.ENTITY_MIGRATIONS)

    def _row(self, entity, stored):
        values = {}
        for name, (kind, _size, _required) in entity.fields.items():
            value = stored[name]
            if kind == "time":
                value = self.sql._db_time(value)
            elif kind == "json" and value is not None:
                value = json.dumps(value, ensure_ascii=False)
            values[self.sql._snake(name)] = value
        values["extra"] = json.dumps(stored["extra"], ensure_ascii=False) if stored.get("extra") else None
        return values

    def _record(self, entity, row):
        mapping = row._mapping
        stored = {}
        for name, (kind, _size, _required) in entity.fields.items():
            value = mapping[self.sql._snake(name)]
            if value is not None:
                if kind == "time":
                    value = model.iso(value)
                elif kind == "json":
                    value = json.loads(value)
                elif kind == "bool":
                    value = bool(value)
                elif kind == "int":
                    value = int(value)
            stored[name] = value
        stored["extra"] = json.loads(mapping["extra"]) if mapping["extra"] else None
        return decode(entity, stored)

    def _match(self, entity, table, values: dict[str, Any]):
        clauses = []
        for name, value in values.items():
            column = table.c[self.sql._snake(name)]
            kind = entity.fields[name][0]
            if kind == "time":
                value = self.sql._db_time(value)
            clauses.append(column.is_(None) if value is None else column == value)
        return clauses

    def get(self, entity, *key):
        table = self.tables[entity.name]
        query = self.sql.sa.select(table).where(*self._match(entity, table, dict(zip(entity.key, key))))
        with self.engine.connect() as connection:
            row = connection.execute(query).first()
        return self._record(entity, row) if row else None

    def find(self, entity, where=None, order=None, limit=None):
        table = self.tables[entity.name]
        query = self.sql.sa.select(table).where(*self._match(entity, table, where or {}))
        if order:
            column = table.c[self.sql._snake(order[0])]
            query = query.order_by(column.desc() if order[1] else column.asc())
        if limit:
            query = query.limit(limit)
        with self.engine.connect() as connection:
            return [self._record(entity, row) for row in connection.execute(query)]

    def put(self, entity, record):
        table = self.tables[entity.name]
        stored = encode(entity, record)
        values = self._row(entity, stored)
        key = dict(zip(entity.key, key_of(entity, stored)))
        for _attempt in range(3):
            try:
                with self.engine.begin() as connection:
                    changed = connection.execute(table.update().where(*self._match(entity, table, key)).values(**values)).rowcount
                    if not changed:
                        connection.execute(table.insert().values(**values))
                return
            except self.sql.sa.exc.IntegrityError:
                continue                       # inserted by another instance at the same moment: update it now

    def delete(self, entity, *key):
        table = self.tables[entity.name]
        with self.engine.begin() as connection:
            return connection.execute(table.delete().where(*self._match(entity, table, dict(zip(entity.key, key))))).rowcount > 0

    def delete_where(self, entity, where):
        table = self.tables[entity.name]
        with self.engine.begin() as connection:
            return connection.execute(table.delete().where(*self._match(entity, table, where))).rowcount


class MongoEntities(EntityBackend):
    def __init__(self, url: str):
        from backend.app.db.mongo import database_for
        self.db = database_for(url)

    def ensure(self) -> None:
        from pymongo import ASCENDING
        for entity in ENTITIES:
            collection = self.db[entity.table]
            for fields in ((entity.key,) if len(entity.key) > 1 else ()) + entity.indexes:
                collection.create_index([(name, ASCENDING) for name in fields], name=f"ix_{entity.table}_{'_'.join(fields)}"[:120])
        self.db["rs_schema_version"].update_one({"_id": "entities"}, {"$max": {"version": 8}, "$set": {"appliedAt": model.now()}}, upsert=True)

    @staticmethod
    def _id(key: tuple) -> str:
        return "␟".join(str(part) for part in key)

    @staticmethod
    def _doc(entity, stored):
        doc = dict(stored)
        for name, (kind, _size, _required) in entity.fields.items():
            if kind == "json" and doc.get(name) is not None:
                doc[name] = json.dumps(doc[name], ensure_ascii=False)     # text: keeps "$match"-style keys as they are
            elif kind == "time" and doc.get(name):
                doc[name] = model.parse_time(doc[name])
        if doc.get("extra"):
            doc["extra"] = json.dumps(doc["extra"], ensure_ascii=False)
        doc["_id"] = MongoEntities._id(key_of(entity, stored))
        return doc

    @staticmethod
    def _record(entity, doc):
        stored = {}
        for name, (kind, _size, _required) in entity.fields.items():
            value = doc.get(name)
            if value is not None:
                if kind == "json":
                    value = json.loads(value)
                elif kind == "time":
                    value = model.iso(value)
            stored[name] = value
        stored["extra"] = json.loads(doc["extra"]) if doc.get("extra") else None
        return decode(entity, stored)

    def _query(self, entity, where):
        query = {}
        for name, value in (where or {}).items():
            kind = entity.fields[name][0]
            query[name] = model.parse_time(value) if kind == "time" and value else value
        return query

    def get(self, entity, *key):
        doc = self.db[entity.table].find_one({"_id": self._id(key)})
        return self._record(entity, doc) if doc else None

    def find(self, entity, where=None, order=None, limit=None):
        from pymongo import ASCENDING, DESCENDING
        cursor = self.db[entity.table].find(self._query(entity, where))
        if order:
            cursor = cursor.sort(order[0], DESCENDING if order[1] else ASCENDING)
        if limit:
            cursor = cursor.limit(limit)
        return [self._record(entity, doc) for doc in cursor]

    def put(self, entity, record):
        doc = self._doc(entity, encode(entity, record))
        self.db[entity.table].replace_one({"_id": doc["_id"]}, doc, upsert=True)

    def delete(self, entity, *key):
        return self.db[entity.table].delete_one({"_id": self._id(key)}).deleted_count > 0

    def delete_where(self, entity, where):
        return self.db[entity.table].delete_many(self._query(entity, where)).deleted_count


# ------------------------------------------------------------------ access
_CACHE: dict[str, EntityBackend] = {}
_LOCK = threading.Lock()


def get_entities() -> EntityBackend:
    from backend.app.storage.files import data_directory
    from backend.app.storage.store import storage_settings
    backend, url = storage_settings()
    if backend == "files":
        found = FileEntities(data_directory())
        found.ensure()
        return found
    key = f"{backend}|{url}"
    with _LOCK:
        if key not in _CACHE:
            found = MongoEntities(url) if backend == "mongodb" else SqlEntities(url)
            found.ensure()
            _CACHE[key] = found
        return _CACHE[key]


def reset_cache() -> None:
    with _LOCK:
        _CACHE.clear()
