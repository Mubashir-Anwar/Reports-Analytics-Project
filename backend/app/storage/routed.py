"""Reports, drafts and folders live in the schema v2 tables, on every backend (files, SQL, MongoDB).

The rest of the service keeps using document keys; this mixin, placed in front of every document store, turns them
into rows with ids:

    reports/<folder>/<ID>.json     server_reports     (server_report_id, folder_id, report_code, title, data source, …)
    workspace/<folder>/<ID>.json   workspace_reports  (workspace_report_id, folder_id, report_code, publish_status,
                                                       version_no, locked_by, server_report_id, …)
    reports/… and workspace/… dirs folders            (folder_id, area server / workspace, parent_id, name, full_path)

On a database (not on files) the data adapter list is rows as well:

    connections.json               rs_data_sources    (one row per adapter, in order)

folders.json (the folders an admin added on purpose) stays a document; each of them is a folders row too.

Every other key stays a document. A row keeps its id when it moves (move_json) and a folder keeps its id when it is
renamed or moved (move_dir). upgrade_v2() copies what earlier versions wrote (documents, files on disk and the
rs_reports / rs_drafts / rs_folders tables) into these tables once; the originals are kept (migrated/ or the old
tables), so going back to the previous version still works.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any, Iterable

from backend.app.db import entities as ent
from backend.app.db import model

_DEFINITION_KEY = re.compile(r"^(reports|workspace)/(.+)/([^/]+)\.json$")
_AREA = {"reports": "server", "workspace": "workspace"}                    # key prefix -> folders.area
_TABLE = {"reports": ent.SERVER_REPORTS, "workspace": ent.WORKSPACE_REPORTS}
_ID = {"reports": "serverReportId", "workspace": "workspaceReportId"}
_OLD_TABLE = {"reports": ent.REPORTS, "workspace": ent.DRAFTS}
_LIST_MARKER = {"connections.json": ("connections", "rs_data_sources")}
WORKSPACE_META = "_workspace"
UPGRADE_KEY = "meta/schema-v2.json"
OLD_ROOT = "/"


def ghost_id(folder: str, report_code: str) -> str:
    """A fixed id for versions and history of a report that is no longer on the server (same on every instance)."""
    return hashlib.sha256(f"{folder}/{report_code}".encode()).hexdigest()[:32]


def _time(value: Any) -> str | None:
    """A time from a definition's own metadata; text that is not a time is left out."""
    try:
        return model.iso(value)
    except (TypeError, ValueError):
        return None


def _without_meta(payload: Any) -> Any:
    return {key: value for key, value in payload.items() if key != WORKSPACE_META} if isinstance(payload, dict) else payload


def publish_status(payload: Any) -> str:
    meta = payload.get(WORKSPACE_META) if isinstance(payload, dict) else None
    meta = meta if isinstance(meta, dict) else {}
    published = str(meta.get("publishedAt") or "")
    if not published:
        return "draft"
    return "changed" if str(meta.get("updatedAt") or "") > published else "published"


class RoutedStore:
    """Mixin: needs a document store underneath (super()) with self.backend (and self.url on a database)."""

    _entity_backend: ent.EntityBackend | None = None

    def _entities(self) -> ent.EntityBackend:
        if self._entity_backend is None:
            if self.backend == "files":
                found: ent.EntityBackend = ent.FileEntities(self.root)
            elif self.backend == "mongodb":
                found = ent.MongoEntities(self.url)
            else:
                found = ent.SqlEntities(self.url)
            found.ensure()
            self._entity_backend = found
        return self._entity_backend

    @property
    def _lists_routed(self) -> bool:
        return self.backend != "files"            # on files connections.json and folders.json stay files

    @staticmethod
    def _route(key: str) -> tuple[str, str, str] | None:
        match = _DEFINITION_KEY.fullmatch(key)
        return (match.group(1), match.group(2), match.group(3)) if match else None

    # ---- folders (a tree per area)
    def _folder_rows(self, area: str) -> list[dict[str, Any]]:
        return self._entities().find(ent.FOLDER_TREE, {"area": _AREA[area]})

    def _folder(self, area: str, path: str) -> dict[str, Any] | None:
        found = self._entities().find(ent.FOLDER_TREE, {"area": _AREA[area], "fullPath": path}, limit=1)
        return found[0] if found else None

    def _ensure_folder(self, area: str, path: str) -> str:
        """The id of this folder, made (with its parents) when it is missing."""
        parent_id: str | None = None
        done: list[str] = []
        row = None
        for name in path.split("/"):
            done.append(name)
            full = "/".join(done)
            row = self._folder(area, full)
            if row is None:
                row = {"folderId": model.new_id(), "area": _AREA[area], "parentId": parent_id, "name": name[:64],
                       "fullPath": full, "createdAt": model.now()}
                self._entities().put(ent.FOLDER_TREE, row)
            parent_id = row["folderId"]
        return row["folderId"] if row else ""

    def _paths(self, area: str) -> dict[str, str]:
        return {row["folderId"]: row["fullPath"] for row in self._folder_rows(area)}

    def folder_id(self, area: str, path: str, *, create: bool = False) -> str | None:
        """The id of a folder ("reports" = the server tree, "workspace"), made when create is set."""
        path = path.strip("/")
        if not path:
            return None
        if create:
            return self._ensure_folder(area, path)
        row = self._folder(area, path)
        return row["folderId"] if row else None

    def folder_paths(self, area: str) -> dict[str, str]:
        """{folder_id: full path} of one tree."""
        return self._paths(area)

    # ---- report rows
    def _row_at(self, area: str, folder: str, code: str) -> dict[str, Any] | None:
        place = self._folder(area, folder)
        if place is None:
            return None
        found = self._entities().find(_TABLE[area], {"folderId": place["folderId"], "reportCode": code}, limit=1)
        return found[0] if found else None

    def _server_id(self, folder: str, code: str) -> str | None:
        row = self._row_at("reports", folder, code)
        return row["serverReportId"] if row else None

    def _new_row(self, area: str, folder_id: str, folder: str, code: str, payload: Any, existing: dict[str, Any] | None) -> dict[str, Any]:
        data = payload if isinstance(payload, dict) else {}
        now = model.now()
        row: dict[str, Any] = {**(existing or {}), _ID[area]: (existing or {}).get(_ID[area]) or model.new_id(),
                               "folderId": folder_id, "reportCode": code, "title": str(data.get("title") or "")[:200] or None,
                               "definition": payload, "updatedAt": now}
        if area == "reports":
            workspace = self._folder("workspace", folder)
            draft = self._entities().find(ent.WORKSPACE_REPORTS, {"folderId": workspace["folderId"], "reportCode": code}, limit=1) if workspace else []
            row.update({"description": str(data.get("description") or "")[:500] or None,
                        "dataSourceId": str(data.get("connectionId") or "")[:64] or None,
                        "queryLanguage": str(data.get("queryLanguage") or "")[:32] or None,
                        "collectionName": str(data.get("collection") or "")[:200] or None,
                        "publishedAt": now, "workspaceReportId": draft[0]["workspaceReportId"] if draft else None})
        else:
            meta = data.get(WORKSPACE_META) if isinstance(data.get(WORKSPACE_META), dict) else {}
            published_folder = str(meta.get("publishedFolder") or folder)
            same = existing is not None and _without_meta(existing.get("definition")) == _without_meta(payload)
            row.update({"publishStatus": publish_status(data),            # +1 when the design changed (not on publish)
                        "versionNo": ((existing or {}).get("versionNo") or 0) + (0 if same else 1),
                        "publishedAt": _time(meta.get("publishedAt")), "updatedBy": str(meta.get("updatedBy") or "")[:100] or None,
                        "serverReportId": self._server_id(published_folder, code) if meta.get("publishedAt") else None,
                        "createdAt": (existing or {}).get("createdAt") or _time(meta.get("createdAt")) or now})
        return row

    def _definitions(self, area: str) -> Iterable[tuple[str, dict[str, Any]]]:
        """(key, row) of every report of the area."""
        paths = self._paths(area)
        for row in self._entities().find(_TABLE[area]):
            folder = paths.get(row["folderId"])
            if folder:
                yield f"{area}/{folder}/{row['reportCode']}.json", row

    # ---- JSON documents
    def read_json(self, key: str, default: Any = None) -> Any:
        from backend.app.storage.store import _clean_key
        key = _clean_key(key)
        routed = self._route(key)
        if routed:
            row = self._row_at(*routed)
            return default if row is None else row["definition"]
        if self._lists_routed and key in _LIST_MARKER:
            stored = super().read_json(key, None)
            if isinstance(stored, dict) and stored.get("storedIn") == _LIST_MARKER[key][1]:
                return {_LIST_MARKER[key][0]: self._read_list(key)}
            return default if stored is None else stored
        return super().read_json(key, default)

    def write_json(self, key: str, payload: Any, *, private: bool = False) -> None:
        from backend.app.storage.store import _clean_key
        key = _clean_key(key)
        routed = self._route(key)
        if routed:
            area, folder, code = routed
            folder_id = self._ensure_folder(area, folder)
            existing = self._row_at(area, folder, code)
            self._entities().put(_TABLE[area], self._new_row(area, folder_id, folder, code, payload, existing))
            return
        if self._lists_routed and key in _LIST_MARKER and isinstance(payload, dict) and isinstance(payload.get(_LIST_MARKER[key][0]), list):
            self._write_list(key, payload[_LIST_MARKER[key][0]])
            super().write_json(key, {"storedIn": _LIST_MARKER[key][1]})       # the list now lives in its table
            return
        super().write_json(key, payload, private=private)

    def move_json(self, old_key: str, new_key: str, payload: Any) -> None:
        """Move a report or draft to another folder or ID: the row keeps its id (and so its versions and history)."""
        from backend.app.storage.store import _clean_key
        old, new = self._route(_clean_key(old_key)), self._route(_clean_key(new_key))
        if not old or not new or old[0] != new[0]:
            self.write_json(new_key, payload)
            if old_key != new_key:
                self.delete(old_key)
            return
        area, folder, code = new
        existing = self._row_at(*old)
        clash = self._row_at(*new)
        if clash is not None and existing is not None and clash[_ID[area]] != existing[_ID[area]]:
            self._entities().delete(_TABLE[area], clash[_ID[area]])
        self._entities().put(_TABLE[area], self._new_row(area, self._ensure_folder(area, folder), folder, code, payload, existing))

    def report_id_of(self, key: str) -> str | None:
        """The id of the report or draft stored at this key (server_report_id / workspace_report_id)."""
        from backend.app.storage.store import _clean_key
        routed = self._route(_clean_key(key))
        row = self._row_at(*routed) if routed else None
        return row[_ID[routed[0]]] if row and routed else None

    def report_row(self, key: str) -> dict[str, Any] | None:
        """The whole row (without the definition) of the report or draft at this key."""
        from backend.app.storage.store import _clean_key
        routed = self._route(_clean_key(key))
        row = self._row_at(*routed) if routed else None
        return {name: value for name, value in row.items() if name != "definition"} if row else None

    def set_row_fields(self, key: str, **values: Any) -> bool:
        """Change a few columns of a report or draft (e.g. who is editing it) without touching the definition."""
        from backend.app.storage.store import _clean_key
        routed = self._route(_clean_key(key))
        row = self._row_at(*routed) if routed else None
        if row is None:
            return False
        self._entities().put(_TABLE[routed[0]], {**row, **values})
        return True

    def _read_list(self, key: str) -> list[Any]:
        rows = sorted(self._entities().find(ent.DATA_SOURCES), key=lambda row: row.get("position", 0))
        return [{name: value for name, value in row.items() if name != "position"} for row in rows]

    def _write_list(self, key: str, items: list[Any]) -> None:
        rows = [{**item, "position": index} for index, item in enumerate(items) if isinstance(item, dict) and item.get("id")]
        self._entities().replace_all(ent.DATA_SOURCES, rows)

    def read_many(self, prefix: str, suffix: str = ".json") -> list[tuple[str, Any]]:
        found = dict(super().read_many(prefix, suffix))
        for key, row in self._routed_rows(prefix, suffix):
            found[key] = row["definition"]
        return sorted(found.items())

    def _routed_rows(self, prefix: str, suffix: str) -> Iterable[tuple[str, dict[str, Any]]]:
        from backend.app.storage.store import _prefix
        start = _prefix(prefix)
        for area in _TABLE:
            if not (f"{area}/".startswith(start) or start.startswith(f"{area}/")):
                continue
            for key, row in self._definitions(area):
                if key.startswith(start) and key.endswith(suffix):
                    yield key, row

    def exists(self, key: str) -> bool:
        from backend.app.storage.store import _clean_key
        routed = self._route(_clean_key(key))
        if routed:
            return self._row_at(*routed) is not None
        return super().exists(key)

    def delete(self, key: str) -> bool:
        from backend.app.storage.store import _clean_key
        routed = self._route(_clean_key(key))
        if routed:
            row = self._row_at(*routed)
            return bool(row) and self._entities().delete(_TABLE[routed[0]], row[_ID[routed[0]]])
        return super().delete(key)

    def keys(self, prefix: str = "", suffix: str = "") -> list[str]:
        return sorted(set(super().keys(prefix, suffix)) | {key for key, _ in self._routed_rows(prefix, suffix)})

    def modified_at(self, key: str) -> datetime | None:
        from backend.app.storage.store import _clean_key
        routed = self._route(_clean_key(key))
        if routed:
            row = self._row_at(*routed)
            return model.parse_time(row["updatedAt"]) if row and row.get("updatedAt") else None
        return super().modified_at(key)

    # ---- folders
    @staticmethod
    def _area_of(prefix: str) -> tuple[str, str] | None:
        """("reports", "Sales/East") for "reports/Sales/East"; None for keys outside the two areas."""
        parts = prefix.strip("/").split("/", 1)
        if parts[0] in _TABLE:
            return parts[0], parts[1] if len(parts) > 1 else ""
        return None

    def dirs(self, prefix: str) -> list[str]:
        found = set(super().dirs(prefix))
        area = self._area_of(prefix)
        if area:
            name, base = area
            base_slash = f"{base}/" if base else ""
            for path in self._paths(name).values():
                if base and not path.startswith(base_slash):
                    continue
                parts = path[len(base_slash):].split("/")
                found.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
        return sorted(item for item in found if item)

    def make_dir(self, key: str) -> None:
        from backend.app.storage.store import _clean_key
        area = self._area_of(_clean_key(key))
        if area and area[1]:
            self._ensure_folder(*area)
            return
        super().make_dir(key)                        # an area's own marker ("the workspace was started") stays a document

    def has_dir(self, key: str) -> bool:
        from backend.app.storage.store import _clean_key
        area = self._area_of(_clean_key(key))
        if area and area[1]:
            return self._folder(*area) is not None
        return super().has_dir(key)

    def remove_dir(self, key: str, *, recursive: bool = False) -> bool:
        from backend.app.storage.store import _clean_key
        key = _clean_key(key)
        area = self._area_of(key)
        if not area:
            return super().remove_dir(key, recursive=recursive)
        name, base = area
        inside = (lambda path: path == base or path.startswith(f"{base}/")) if base else (lambda path: True)
        paths = self._paths(name)
        rows = [row for row in self._entities().find(_TABLE[name]) if inside(paths.get(row["folderId"], ""))]
        if rows and not recursive:
            return False
        if super().has_dir(key) and not super().remove_dir(key, recursive=recursive):
            return False
        for row in rows:
            self._entities().delete(_TABLE[name], row[_ID[name]])
        for folder_id, path in paths.items():
            if inside(path):
                self._entities().delete(ent.FOLDER_TREE, folder_id)
        return True

    def move_dir(self, old_key: str, new_key: str) -> None:
        """Rename or move a folder: one folder row changes (its subfolders follow); reports keep their ids."""
        from backend.app.storage.store import _clean_key
        old, new = self._area_of(_clean_key(old_key)), self._area_of(_clean_key(new_key))
        if not old or not new or old[0] != new[0] or not old[1] or not new[1]:
            raise ValueError("A folder can only move inside its own area")
        area, source, target = old[0], old[1], new[1]
        if target == source or target.startswith(f"{source}/"):
            raise ValueError("A folder cannot move into itself")
        if self._folder(area, target) is not None:
            raise ValueError(f"{target} already exists")
        moving = self._folder(area, source)
        if moving is None:
            raise ValueError(f"{source} does not exist")
        parent = target.rsplit("/", 1)[0] if "/" in target else ""
        entities = self._entities()
        entities.put(ent.FOLDER_TREE, {**moving, "parentId": self._ensure_folder(area, parent) if parent else None,
                                       "name": target.rsplit("/", 1)[-1][:64], "fullPath": target})
        for row in self._folder_rows(area):
            if row["fullPath"].startswith(f"{source}/"):
                entities.put(ent.FOLDER_TREE, {**row, "fullPath": target + row["fullPath"][len(source):]})

    # ---- one-time upgrade from what earlier versions wrote
    def upgrade_v2(self) -> dict[str, int]:
        counts = {"reports": 0, "drafts": 0, "folders": 0, "versions": 0, "history": 0, "dataSources": 0, "folderList": 0}
        state = super().read_json(UPGRADE_KEY, None) or {}
        # 1. documents and files on disk (reports/…, workspace/…): always looked at, so a report file dropped into the
        #    data folder is taken in at the next start; the file is kept under migrated/
        for area in _TABLE:
            for key, payload in super().read_many(area, ".json"):
                routed = self._route(key)
                if routed and payload is not None:
                    self.write_json(key, payload)
                    super().write_json(f"migrated/{key}", payload)
                    super().delete(key)
                    counts["reports" if area == "reports" else "drafts"] += 1
            for path in super().dirs(area):
                self._ensure_folder(area, path)
                counts["folders"] += 1
            if super().dirs(area):
                started = super().has_dir(area)
                super().remove_dir(area, recursive=True)        # only empty folders are left there now
                if started:
                    super().make_dir(area)
        # 2. the rs_reports / rs_drafts / rs_folders tables of the previous version (databases), once; they are kept
        if not state.get("step1") and self.backend != "files":
            entities = self._entities()
            listed: list[str] = []
            for area, table in _OLD_TABLE.items():
                for row in entities.find(table):
                    key = f"{area}/{row['folder']}/{row['reportId']}.json"
                    if self._row_at(area, row["folder"], row["reportId"]) is None:
                        self.write_json(key, row["definition"])
                        counts["reports" if area == "reports" else "drafts"] += 1
            for row in entities.find(ent.FOLDERS):
                if row["area"] in _TABLE and row["path"] != OLD_ROOT:
                    self._ensure_folder(row["area"], row["path"])
                    counts["folders"] += 1
                elif row["area"] in _TABLE:
                    super().make_dir(row["area"])               # "the workspace was started"
                elif row["area"] == "list":
                    listed.append(row["path"])
            stored = super().read_json("folders.json", None)
            if isinstance(stored, dict) and stored.get("storedIn") == "rs_folders":     # v1 kept the list in rs_folders
                super().write_json("folders.json", {"folders": sorted(listed, key=str.casefold)})
                counts["folderList"] += len(listed)
        # 3. the adapter list still stored as a document (databases); the folder list: each listed folder is a row
        if self._lists_routed:
            stored = super().read_json("connections.json", None)
            if isinstance(stored, dict) and stored.get("storedIn") != "rs_data_sources" and isinstance(stored.get("connections"), list):
                super().write_json("migrated/connections.json", stored)
                self.write_json("connections.json", stored)
                counts["dataSources"] += len(stored["connections"])
        listed_now = super().read_json("folders.json", None)
        if isinstance(listed_now, dict) and isinstance(listed_now.get("folders"), list):
            for path in listed_now["folders"]:
                if str(path).strip("/") and self._folder("reports", str(path).strip("/")) is None:
                    self._ensure_folder("reports", str(path).strip("/"))
                    counts["folderList"] += 1
        # 4. versions and history of the previous version: by report id now (rs_report_versions / rs_report_history kept)
        if not state.get("step1"):
            counts["versions"], counts["history"] = self._upgrade_versions_and_history()
            super().write_json(UPGRADE_KEY, {**state, "step1": model.iso(model.now()), "counts": counts})
        if any(counts.values()):
            from backend.app.logs import LOG
            LOG.info("Schema v2 (folders and reports): %s", counts)
        return counts

    def _upgrade_versions_and_history(self) -> tuple[int, int]:
        entities = self._entities()
        ids: dict[tuple[str, str], str] = {}

        def report_id(folder: str, code: str) -> str:
            if (folder, code) not in ids:
                ids[(folder, code)] = self._server_id(folder, code) or ghost_id(folder, code)
            return ids[(folder, code)]

        versions = 0
        numbers: dict[str, int] = {}
        for row in sorted(entities.find(ent.REPORT_VERSIONS), key=lambda r: (r["reportId"], r["version"])):
            folder = str(row.get("folder") or "General")
            owner = report_id(folder, row["reportId"])
            numbers[owner] = numbers.get(owner, 0) + 1
            entities.put(ent.REPORT_VERSIONS_V2, {
                "serverReportId": owner, "versionNo": numbers[owner], "reportCode": row["reportId"], "folderPath": folder,
                "title": row.get("title"), "action": row.get("action"), "savedBy": row.get("user"), "savedAt": row.get("savedAt"),
                "definition": row.get("definition"), "oldVersion": row["version"]})
            versions += 1
        for owner, number in numbers.items():
            row = entities.get(ent.SERVER_REPORTS, owner)
            if row is not None:
                entities.put(ent.SERVER_REPORTS, {**row, "currentVersion": number})
        history = 0
        codes: dict[str, list[str]] = {}
        for key, row in self._definitions("reports"):
            codes.setdefault(row["reportCode"], []).append(key.split("/", 1)[1].rsplit("/", 1)[0])
        for row in entities.find(ent.REPORT_HISTORY):
            folder = row.get("folder")
            if folder is None:                                  # written before reports had a folder: the only one with the ID
                places = codes.get(row["reportId"], [])
                folder = places[0] if len(places) == 1 else "General"
            entities.put(ent.REPORT_HISTORY_V2, {
                "historyId": row["id"], "serverReportId": report_id(folder, row["reportId"]), "reportCode": row["reportId"],
                "folderPath": folder, "happenedAt": row["at"], "action": row.get("action") or "?", "userName": row.get("user"),
                "detail": row.get("detail")})
            history += 1
        return versions, history
