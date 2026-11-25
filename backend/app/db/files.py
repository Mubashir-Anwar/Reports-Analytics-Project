"""Files backend (one installation, no database server): JSON lines in <data dir>/records/."""
from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any

from backend.app.db import model
from backend.app.db.base import DuplicateSeq, RecordsBackend
from backend.app.storage.files import data_directory, long_path

_LOCK = threading.RLock()   # one process writes the files; several processes need a database backend


class FileRecords(RecordsBackend):
    name = "files"

    def __init__(self, root: Path | None = None):
        self._root = root
        self._tail: dict[str, Any] | None = None
        self._tail_size = -1

    @property
    def folder(self) -> Path:
        return long_path(self._root or data_directory()) / "records"

    def _day_path(self, kind: str, day: str) -> Path:
        return self.folder / ("request_log" if kind == "requests" else "api_calls") / f"{day}.jsonl"

    def _day_files(self, kind: str) -> list[Path]:
        folder = self.folder / ("request_log" if kind == "requests" else "api_calls")
        return sorted(folder.glob("????-??-??.jsonl")) if folder.is_dir() else []

    def _insert_many(self, kind: str, records: list[dict[str, Any]]) -> None:
        by_day: dict[str, list[str]] = {}
        time_field = model.RECORD_KINDS[kind][1]
        for record in records:
            by_day.setdefault(str(record[time_field])[:10], []).append(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        with _LOCK:
            for day, lines in by_day.items():
                path = self._day_path(kind, day)
                path.parent.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(descriptor, "a", encoding="utf-8", newline="\n") as stream:
                    stream.write("\n".join(lines) + "\n")

    def _delete_before(self, kind: str, cutoff) -> int:
        removed = 0
        with _LOCK:
            for path in self._day_files(kind):
                if path.stem < cutoff.date().isoformat():
                    path.unlink(missing_ok=True)
                    removed += 1
        return removed

    def _read_days(self, kind: str, since, until) -> list[dict[str, Any]]:
        found = []
        for path in self._day_files(kind):
            if (since and path.stem < since.date().isoformat()) or (until and path.stem > until.date().isoformat()):
                continue
            with path.open(encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(item, dict):
                        found.append(item)
        return found

    def _path(self, kind: str) -> Path:
        return self.folder / ("runs.jsonl" if kind == "runs" else "audit_events.jsonl")

    def ensure_schema(self) -> None:
        """Schema v2: runs.jsonl and audit_events.jsonl. Version 1 files (report-runs.jsonl, audit-events.jsonl)
        are copied once and kept, so the previous version still reads its own."""
        self.folder.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            for old, kind in (("report-runs.jsonl", "runs"), ("audit-events.jsonl", "events")):
                source, target = self.folder / old, self._path(kind)
                if source.is_file() and not target.exists():
                    temporary = target.with_suffix(".copying")
                    shutil.copyfile(source, temporary)
                    os.replace(temporary, target)
        version = self.folder / "schema-version.json"
        if self.schema_version() < model.SCHEMA_VERSION:
            version.write_text(json.dumps({"version": model.SCHEMA_VERSION}), encoding="utf-8")

    def schema_version(self) -> int:
        try:
            return int(json.loads((self.folder / "schema-version.json").read_text(encoding="utf-8"))["version"])
        except (OSError, ValueError, KeyError):
            return 0

    def _append(self, kind: str, record: dict[str, Any]) -> None:
        path = self._path(kind)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _read(self, kind: str) -> list[dict[str, Any]]:
        path = self._path(kind)
        if not path.is_file():
            return []
        found = []
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, dict):
                    found.append(item)
        return found

    def _insert_run(self, run: dict[str, Any]) -> None:
        with _LOCK:
            self._append("runs", run)

    def _upsert_run(self, run: dict[str, Any]) -> None:
        with _LOCK:
            self._append("runs", run)                  # a newer line with the same id replaces the older one

    def _delete_run(self, run_id: str) -> None:
        with _LOCK:
            self._append("runs", {"id": run_id, "_deleted": True})

    def _runs(self) -> list[dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for item in self._read("runs"):
            if item.get("id") is not None:
                latest.pop(item["id"], None)
                latest[item["id"]] = item
        return [item for item in latest.values() if not item.get("_deleted")]

    def _find(self, kind, filters, since, until, limit, newest_first=True):
        time_field = model.RECORD_KINDS[kind][1]
        matches = []
        source = (self._runs() if kind == "runs" else self._read(kind) if kind == "events" else self._read_days(kind, since, until))
        for item in source:
            if any(str(item.get(key)) != value for key, value in filters.items()):
                continue
            when = model.parse_time(item[time_field]) if item.get(time_field) else None
            if (since and (when is None or when < since)) or (until and (when is None or when >= until)):
                continue
            matches.append(item)
        matches.sort(key=lambda item: (item.get(time_field) or "", item.get("seq") or 0, item.get("id")), reverse=newest_first)
        return matches[:limit]

    def _last_event(self) -> dict[str, Any] | None:
        path = self._path("events")
        size = path.stat().st_size if path.is_file() else 0
        if size == self._tail_size:
            return self._tail
        last = None
        if size:
            with path.open("rb") as stream:                # read only the end of the file
                stream.seek(max(0, size - 65536))
                for line in reversed(stream.read().splitlines()):
                    try:
                        last = json.loads(line)
                        break
                    except ValueError:
                        continue
        self._tail, self._tail_size = last, size
        return last

    def _insert_event(self, event: dict[str, Any]) -> None:
        with _LOCK:
            last = self._last_event()
            if (last["seq"] if last else 0) + 1 != event["seq"]:
                raise DuplicateSeq
            self._append("events", event)
            self._tail, self._tail_size = event, self._path("events").stat().st_size

    def _events_from(self, seq: int, limit: int) -> list[dict[str, Any]]:
        events = sorted((item for item in self._read("events") if int(item.get("seq", 0)) >= seq), key=lambda item: item["seq"])
        return events[:limit]

    def audit(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        with _LOCK:                                         # one writer at a time inside this process
            return super().audit(*args, **kwargs)
