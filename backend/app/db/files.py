"""Files backend (one installation, no database server): JSON lines in <data dir>/records/."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from backend.app.db import model
from backend.app.db.base import DuplicateSeq, RecordsBackend
from backend.app.storage.files import data_directory

_LOCK = threading.RLock()   # one process writes the files; several processes need a database backend


class FileRecords(RecordsBackend):
    name = "files"

    def __init__(self, root: Path | None = None):
        self._root = root
        self._tail: dict[str, Any] | None = None
        self._tail_size = -1

    @property
    def folder(self) -> Path:
        return (self._root or data_directory()) / "records"

    def _path(self, kind: str) -> Path:
        return self.folder / ("report-runs.jsonl" if kind == "runs" else "audit-events.jsonl")

    def ensure_schema(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        version = self.folder / "schema-version.json"
        if not version.exists():
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

    def _find(self, kind, filters, since, until, limit, newest_first=True):
        time_field = "startedAt" if kind == "runs" else "at"
        matches = []
        for item in self._read(kind):
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
