"""What every system-database backend offers (files, SQL, MongoDB). The rules live here; backends only store."""
from __future__ import annotations

import random
import threading
import time
from datetime import datetime
from typing import Any

from backend.app.db import model

# One audit append at a time in this process: the threads of one server no longer race each other for the next
# number (and sleep between tries); the retry below is then only for other instances on the same database.
_AUDIT_LOCK = threading.Lock()


class DuplicateSeq(Exception):
    """Another instance wrote the same audit sequence number first; read the end of the chain again."""


class RecordsBackend:
    name = "?"

    # ---- implemented by each backend
    def ensure_schema(self) -> None:
        raise NotImplementedError

    def schema_version(self) -> int:
        raise NotImplementedError

    def _insert_run(self, run: dict[str, Any]) -> None:
        raise NotImplementedError

    def _find(self, kind: str, filters: dict[str, Any], since: datetime | None, until: datetime | None, limit: int,
              newest_first: bool = True) -> list[dict[str, Any]]:
        raise NotImplementedError

    def _insert_many(self, kind: str, records: list[dict[str, Any]]) -> None:
        """requests / api_calls: append these rows."""
        raise NotImplementedError

    def _delete_before(self, kind: str, cutoff: datetime) -> int:
        """requests / api_calls: remove the rows older than cutoff."""
        raise NotImplementedError

    def _upsert_run(self, run: dict[str, Any]) -> None:
        """Insert, or replace the row with this id."""
        raise NotImplementedError

    def _delete_run(self, run_id: str) -> None:
        raise NotImplementedError

    def _last_event(self) -> dict[str, Any] | None:
        raise NotImplementedError

    def _insert_event(self, event: dict[str, Any]) -> None:
        """Insert; raise DuplicateSeq when the seq is taken."""
        raise NotImplementedError

    def _events_from(self, seq: int, limit: int) -> list[dict[str, Any]]:
        raise NotImplementedError

    def close(self) -> None:
        pass

    # ---- the shared API
    def add_run(self, run: dict[str, Any]) -> dict[str, Any]:
        record = {"id": model.new_id(), "orgId": model.org_id(), **{k: v for k, v in run.items() if v is not None}}
        record = model.clean(record, model.RUN_FIELDS)
        if record["channel"] not in model.CHANNELS:
            raise ValueError(f"Unknown channel {record['channel']!r}")
        if record["status"] not in model.STATUSES:
            raise ValueError(f"Unknown status {record['status']!r}")
        self._insert_run(record)
        return record

    def put_run(self, run: dict[str, Any]) -> dict[str, Any]:
        """Write a run that changes over time (a job: queued, running, success / failed). Fields left out or None
        keep what the row already has."""
        existing = self.run(str(run["id"])) or {}
        record = model.clean({"orgId": model.org_id(), **existing, **{k: v for k, v in run.items() if v is not None}}, model.RUN_FIELDS)
        if record["channel"] not in model.CHANNELS:
            raise ValueError(f"Unknown channel {record['channel']!r}")
        if record["status"] not in model.STATUSES:
            raise ValueError(f"Unknown status {record['status']!r}")
        self._upsert_run(record)
        return record

    def run(self, run_id: str) -> dict[str, Any] | None:
        found = self._find("runs", {"id": run_id}, None, None, 1)
        return found[0] if found else None

    def delete_run(self, run_id: str) -> None:
        self._delete_run(run_id)

    def runs(self, *, limit: int = 200, since: datetime | str | None = None, until: datetime | str | None = None,
             **filters: Any) -> list[dict[str, Any]]:
        return self._find("runs", self._filters(filters, model.RUN_FILTERS), _time(since), _time(until), _limit(limit))

    # ---- request log and API calls
    def add_requests(self, items: list[dict[str, Any]]) -> int:
        rows = [model.clean({"id": model.new_id(), "orgId": model.org_id(), **item}, model.REQUEST_FIELDS) for item in items]
        if rows:
            self._insert_many("requests", rows)
        return len(rows)

    def requests(self, *, limit: int = 500, since: datetime | str | None = None, until: datetime | str | None = None,
                 **filters: Any) -> list[dict[str, Any]]:
        return self._find("requests", self._filters(filters, model.REQUEST_FILTERS), _time(since), _time(until),
                          max(1, min(int(limit or 500), 100_000)))

    def add_api_call(self, call: dict[str, Any]) -> dict[str, Any]:
        record = model.clean({"id": model.new_id(), "orgId": model.org_id(), "calledAt": model.now(),
                              **{k: v for k, v in call.items() if v is not None}}, model.API_CALL_FIELDS)
        self._insert_many("api_calls", [record])
        return record

    def api_calls(self, *, limit: int = 200, since: datetime | str | None = None, until: datetime | str | None = None,
                  **filters: Any) -> list[dict[str, Any]]:
        return self._find("api_calls", self._filters(filters, model.API_CALL_FILTERS), _time(since), _time(until), _limit(limit))

    def remove_before(self, kind: str, cutoff: datetime | str) -> int:
        if kind not in ("requests", "api_calls"):
            raise ValueError("Only the request log and the API calls are removed by age")
        return self._delete_before(kind, _time(cutoff))

    def audit(self, action: str, *, actor: str, actor_type: str = "user", target: str = "", details: Any = None,
              client_ip: str = "", target_type: str = "") -> dict[str, Any]:
        """Append one audit event at the end of the chain (retries when another instance appends at the same time)."""
        if actor_type not in model.ACTOR_TYPES:
            raise ValueError(f"Unknown actor type {actor_type!r}")
        with _AUDIT_LOCK:
            return self._append_event(action, actor, actor_type, target, details, client_ip, target_type)

    def _append_event(self, action: str, actor: str, actor_type: str, target: str, details: Any, client_ip: str,
                      target_type: str) -> dict[str, Any]:
        for attempt in range(60):
            if attempt:                                  # another writer took the number: wait a little (random, growing)
                time.sleep(random.uniform(0, min(0.25, 0.004 * 2 ** min(attempt, 6))))  # noqa: S311 - timing jitter, not security
            last = self._last_event()
            event = model.clean({
                "id": model.new_id(), "seq": (last["seq"] + 1) if last else 1, "orgId": model.org_id(), "at": model.now(),
                "actor": actor or "?", "actorType": actor_type, "action": action, "target": target, "details": details,
                "clientIp": client_ip, "prevHash": last["hash"] if last else None, "hash": "-",
                "targetType": target_type or (_target_type(action) if target else None), "targetId": target or None,
            }, model.AUDIT_FIELDS)
            event["hash"] = model.event_hash(event)
            try:
                self._insert_event(event)
                return event
            except DuplicateSeq:
                continue
        raise RuntimeError("The audit log is busy; the event could not be written")

    def events(self, *, limit: int = 200, since: datetime | str | None = None, until: datetime | str | None = None,
               **filters: Any) -> list[dict[str, Any]]:
        return self._find("events", self._filters(filters, model.AUDIT_FILTERS), _time(since), _time(until), _limit(limit))

    def verify_chain(self, batch: int = 1000) -> dict[str, Any]:
        """Re-check every audit event's hash and link. {"ok", "events", "problems"}."""
        problems: list[str] = []
        count, seq, previous = 0, 1, None
        while True:
            chunk = self._events_from(seq, batch)
            if not chunk:
                break
            problems += model.chain_problems(([previous] if previous else []) + chunk)
            count += len(chunk)
            previous, seq = chunk[-1], chunk[-1]["seq"] + 1
            if len(problems) > 50:
                break
        return {"ok": not problems, "events": count, "problems": sorted(set(problems))[:50]}

    @staticmethod
    def _filters(given: dict[str, Any], allowed: tuple[str, ...]) -> dict[str, Any]:
        unknown = set(given) - set(allowed)
        if unknown:
            raise ValueError(f"Cannot filter on {', '.join(sorted(unknown))}")
        return {key: str(value) for key, value in given.items() if value not in (None, "")}


# the first part of an action ("report.published") names what it was done to
_TARGET_TYPES = {"report": "report", "reports": "report", "folder": "folder", "user": "user", "users": "user", "apikey": "api_key",
                 "api_key": "api_key", "schedule": "schedule", "connection": "data_source", "data_source": "data_source",
                 "permissions": "folder", "group": "user_group", "user_group": "user_group", "password": "user", "auth": "user"}


def _target_type(action: str) -> str | None:
    return _TARGET_TYPES.get(action.split(".", 1)[0].replace("-", "_").lower())


def _time(value: datetime | str | None) -> datetime | None:
    return model.parse_time(value) if value else None


def _limit(value: int) -> int:
    return max(1, min(int(value or 200), 5000))
