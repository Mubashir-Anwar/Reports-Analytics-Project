"""On a database, the document keys of reports, drafts, folders and data adapters go to real tables.

The rest of the service keeps using document keys (reports/<folder>/<ID>.json, workspace/…, connections.json,
folders.json); this mixin, placed in front of SqlStore and MongoStore, turns them into rows:

    reports/<folder>/<ID>.json     rs_reports       (folder, reportId, title, dataSource, queryLanguage, …, definition)
    workspace/<folder>/<ID>.json   rs_drafts        (same columns)
    reports/… and workspace/… dirs rs_folders       (area "reports" / "workspace"; the root is "/")
    folders.json                   rs_folders       (area "list")
    connections.json               rs_data_sources  (one row per adapter, in order)

Every other key stays a document. move_routed_documents() moves rows written by the earlier version (stored as
documents) into these tables once; the originals are kept under migrated/.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable

from backend.app.db import entities as ent, model

_DEFINITION_KEY = re.compile(r"^(reports|workspace)/(.+)/([^/]+)\.json$")
_AREA_ENTITY = {"reports": ent.REPORTS, "workspace": ent.DRAFTS}
_LIST_MARKER = {"connections.json": ("connections", "rs_data_sources"), "folders.json": ("folders", "rs_folders")}
ROOT = "/"


def _path(folder: str) -> str:
    return folder.strip("/") or ROOT


def _unpath(path: str) -> str:
    return "" if path == ROOT else path


class RoutedStore:
    """Mixin: needs self.url, a document store underneath (super()) and self._entities()."""

    _entity_backend: ent.EntityBackend | None = None

    def _entities(self) -> ent.EntityBackend:
        if self._entity_backend is None:
            found = ent.MongoEntities(self.url) if self.backend == "mongodb" else ent.SqlEntities(self.url)
            found.ensure()
            self._entity_backend = found
        return self._entity_backend

    @staticmethod
    def _route(key: str) -> tuple[str, str, str] | None:
        match = _DEFINITION_KEY.fullmatch(key)
        return (match.group(1), match.group(2), match.group(3)) if match else None

    @staticmethod
    def _row(folder: str, report_id: str, payload: Any) -> dict[str, Any]:
        data = payload if isinstance(payload, dict) else {}
        return {"folder": folder, "reportId": report_id, "title": str(data.get("title") or "")[:200] or None,
                "dataSource": str(data.get("connectionId") or "")[:64] or None,
                "queryLanguage": str(data.get("queryLanguage") or "")[:32] or None,
                "collection": str(data.get("collection") or "")[:200] or None, "updatedAt": model.now(), "definition": payload}

    def _definitions(self, area: str) -> list[dict[str, Any]]:
        return self._entities().find(_AREA_ENTITY[area])

    # ---- JSON documents
    def read_json(self, key: str, default: Any = None) -> Any:
        from backend.app.storage.store import _clean_key
        key = _clean_key(key)
        routed = self._route(key)
        if routed:
            row = self._entities().get(_AREA_ENTITY[routed[0]], routed[1], routed[2])
            return default if row is None else row["definition"]
        if key in _LIST_MARKER:
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
            self._entities().put(_AREA_ENTITY[routed[0]], self._row(routed[1], routed[2], payload))
            return
        if key in _LIST_MARKER and isinstance(payload, dict) and isinstance(payload.get(_LIST_MARKER[key][0]), list):
            self._write_list(key, payload[_LIST_MARKER[key][0]])
            super().write_json(key, {"storedIn": _LIST_MARKER[key][1]})       # the list now lives in its table
            return
        super().write_json(key, payload, private=private)

    def _read_list(self, key: str) -> list[Any]:
        if key == "connections.json":
            rows = sorted(self._entities().find(ent.DATA_SOURCES), key=lambda row: row.get("position", 0))
            return [{name: value for name, value in row.items() if name != "position"} for row in rows]
        return sorted(row["path"] for row in self._entities().find(ent.FOLDERS, {"area": "list"}))

    def _write_list(self, key: str, items: list[Any]) -> None:
        if key == "connections.json":
            rows = [{**item, "position": index} for index, item in enumerate(items) if isinstance(item, dict) and item.get("id")]
            self._entities().replace_all(ent.DATA_SOURCES, rows)
        else:
            entities = self._entities()
            wanted = {str(item) for item in items if str(item).strip("/")}
            for row in entities.find(ent.FOLDERS, {"area": "list"}):
                if row["path"] not in wanted:
                    entities.delete(ent.FOLDERS, "list", row["path"])
            for path in wanted:
                entities.put(ent.FOLDERS, {"area": "list", "path": path})

    def read_many(self, prefix: str, suffix: str = ".json") -> list[tuple[str, Any]]:
        found = dict(super().read_many(prefix, suffix))
        for key, row in self._routed_rows(prefix, suffix):
            found[key] = row["definition"]
        return sorted(found.items())

    def _routed_rows(self, prefix: str, suffix: str) -> Iterable[tuple[str, dict[str, Any]]]:
        from backend.app.storage.store import _prefix
        start = _prefix(prefix)
        for area in _AREA_ENTITY:
            if not (f"{area}/".startswith(start) or start.startswith(f"{area}/")):
                continue
            for row in self._definitions(area):
                key = f"{area}/{row['folder']}/{row['reportId']}.json"
                if key.startswith(start) and key.endswith(suffix):
                    yield key, row

    def exists(self, key: str) -> bool:
        from backend.app.storage.store import _clean_key
        routed = self._route(_clean_key(key))
        if routed:
            return self._entities().get(_AREA_ENTITY[routed[0]], routed[1], routed[2]) is not None
        return super().exists(key)

    def delete(self, key: str) -> bool:
        from backend.app.storage.store import _clean_key
        routed = self._route(_clean_key(key))
        if routed:
            return self._entities().delete(_AREA_ENTITY[routed[0]], routed[1], routed[2])
        return super().delete(key)

    def keys(self, prefix: str = "", suffix: str = "") -> list[str]:
        return sorted(set(super().keys(prefix, suffix)) | {key for key, _ in self._routed_rows(prefix, suffix)})

    def modified_at(self, key: str) -> datetime | None:
        from backend.app.storage.store import _clean_key
        routed = self._route(_clean_key(key))
        if routed:
            row = self._entities().get(_AREA_ENTITY[routed[0]], routed[1], routed[2])
            return model.parse_time(row["updatedAt"]) if row and row.get("updatedAt") else None
        return super().modified_at(key)

    # ---- folders
    @staticmethod
    def _area_of(prefix: str) -> tuple[str, str] | None:
        """("reports", "Sales/East") for "reports/Sales/East"; None for keys outside the two areas."""
        parts = prefix.strip("/").split("/", 1)
        if parts[0] in _AREA_ENTITY:
            return parts[0], parts[1] if len(parts) > 1 else ""
        return None

    def dirs(self, prefix: str) -> list[str]:
        found = set(super().dirs(prefix))
        area = self._area_of(prefix)
        if area:
            name, base = area
            base_slash = f"{base}/" if base else ""
            paths = [_unpath(row["path"]) for row in self._entities().find(ent.FOLDERS, {"area": name})]
            paths += [row["folder"] for row in self._definitions(name)]
            for path in paths:
                if base and not path.startswith(base_slash):
                    continue
                parts = path[len(base_slash):].split("/") if path else []
                found.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
        return sorted(item for item in found if item)

    def make_dir(self, key: str) -> None:
        from backend.app.storage.store import _clean_key
        area = self._area_of(_clean_key(key))
        if area:
            self._entities().put(ent.FOLDERS, {"area": area[0], "path": _path(area[1])})
            return
        super().make_dir(key)

    def remove_dir(self, key: str, *, recursive: bool = False) -> bool:
        from backend.app.storage.store import _clean_key
        area = self._area_of(_clean_key(key))
        if not area:
            return super().remove_dir(key, recursive=recursive)
        name, base = area
        inside = (lambda path: path == base or path.startswith(f"{base}/")) if base else (lambda path: True)
        rows = [row for row in self._definitions(name) if inside(row["folder"])]
        if rows and not recursive:
            return False
        if not super().remove_dir(key, recursive=recursive):
            return False
        entities = self._entities()
        for row in rows:
            entities.delete(_AREA_ENTITY[name], row["folder"], row["reportId"])
        for row in entities.find(ent.FOLDERS, {"area": name}):
            if inside(_unpath(row["path"])) and (base or row["path"] != ROOT):
                entities.delete(ent.FOLDERS, name, row["path"])
        return True

    # ---- one-time move of documents written by the earlier version
    def move_routed_documents(self) -> dict[str, int]:
        counts = {"reports": 0, "drafts": 0, "folders": 0, "dataSources": 0, "folderList": 0}
        for area in _AREA_ENTITY:
            for key, payload in super().read_many(area, ".json"):
                routed = self._route(key)
                if routed and payload is not None:
                    self._entities().put(_AREA_ENTITY[area], self._row(routed[1], routed[2], payload))
                    super().write_json(f"migrated/{key}", payload)
                    super().delete(key)
                    counts["reports" if area == "reports" else "drafts"] += 1
            old_folders = super().dirs(area)
            for path in old_folders:
                self._entities().put(ent.FOLDERS, {"area": area, "path": _path(path)})
                counts["folders"] += 1
            started = super().has_dir(area)                 # the area's own marker (e.g. "the workspace was started")
            if old_folders or started:
                super().remove_dir(area, recursive=True)     # only folder markers are left there now
            if started:
                self._entities().put(ent.FOLDERS, {"area": area, "path": ROOT})
        for key, (field, table) in _LIST_MARKER.items():
            stored = super().read_json(key, None)
            if isinstance(stored, dict) and stored.get("storedIn") != table and isinstance(stored.get(field), list):
                super().write_json(f"migrated/{key}", stored)
                self.write_json(key, stored)
                counts["dataSources" if key == "connections.json" else "folderList"] += len(stored[field])
        if any(counts.values()):
            from backend.app.logs import LOG
            LOG.info("Moved reports, drafts, folders and data adapters into their tables: %s", counts)
        return counts

    def has_dir(self, key: str) -> bool:
        from backend.app.storage.store import _clean_key
        area = self._area_of(_clean_key(key))
        if area:
            return self._entities().get(ent.FOLDERS, area[0], _path(area[1])) is not None
        return super().has_dir(key)
