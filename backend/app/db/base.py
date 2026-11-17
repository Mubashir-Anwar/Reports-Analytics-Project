"""What every system-database backend offers (files, SQL, MongoDB). The rules live here; backends only store."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.app.db import model


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

    def runs(self, *, limit: int = 200, since: datetime | str | None = None, until: datetime | str | None = None,
             **filters: Any) -> list[dict[str, Any]]:
        return self._find("runs", self._filters(filters, model.RUN_FILTERS), _time(since), _time(until), _limit(limit))

    def audit(self, action: str, *, actor: str, actor_type: str = "user", target: str = "", details: Any = None,
              client_ip: str = "") -> dict[str, Any]:
        """Append one audit event at the end of the chain (retries when another instance appends at the same time)."""
        if actor_type not in model.ACTOR_TYPES:
            raise ValueError(f"Unknown actor type {actor_type!r}")
        for _attempt in range(20):
            last = self._last_event()
            event = model.clean({
                "id": model.new_id(), "seq": (last["seq"] + 1) if last else 1, "orgId": model.org_id(), "at": model.now(),
                "actor": actor or "?", "actorType": actor_type, "action": action, "target": target, "details": details,
                "clientIp": client_ip, "prevHash": last["hash"] if last else None, "hash": "-",
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


def _time(value: datetime | str | None) -> datetime | None:
    return model.parse_time(value) if value else None


def _limit(value: int) -> int:
    return max(1, min(int(value or 200), 5000))
