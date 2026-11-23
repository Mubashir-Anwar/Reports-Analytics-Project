"""Every request that reaches the server, kept in the system database (Report Server → Logs → Requests).

One record per request: when, method, path, the names of the query parameters (never their values: they can hold
customer data), status, time taken, size of the answer, caller (user / API key / token / anonymous) and address.
Records are written by a background thread in batches, so a request never waits for the database; they are rows of
the request_log table (schema v2; files: records/request_log/<day>.jsonl) and rows older than
REPORTING_REQUEST_LOG_DAYS (default 30) are removed. REPORTING_REQUEST_LOG=off switches it off.
Version 1 kept them as documents per hour (requests/YYYY-MM-DD/HH.jsonl): the days still kept are copied at start-up.

Static files and the download panel's status checks are not recorded (they would bury everything else).
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from backend.app.db import get_records
from backend.app.logs import LOG
from backend.app.storage.store import get_store

PREFIX = "requests/"
SKIP_PREFIXES = ("/assets/", "/favicon")
SKIP_EXACT = {"/health", "/ready", "/api/deliveries", "/api/team/heartbeat"}  # polled every few seconds
_DELIVERY = re.compile(r"^/api/deliveries/[^/]+")


def enabled() -> bool:
    return os.getenv("REPORTING_REQUEST_LOG", "on").strip().lower() not in ("off", "false", "0", "no")


def keep_days() -> int:
    try:
        return max(1, int(os.getenv("REPORTING_REQUEST_LOG_DAYS", "30") or 30))
    except ValueError:
        return 30


def check_day(day: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or ""):
        raise ValueError("day must be YYYY-MM-DD")
    return day


def hour_key(day: str, hour: int | str) -> str:
    """requests/2026-11-19/14.jsonl: one log per hour (the database stores keep the newest 5000 lines per log)."""
    return f"{PREFIX}{check_day(day)}/{int(hour):02d}.jsonl"


def _has_v1_documents(store: Any) -> bool:
    """Are there version 1 hour documents (requests/<day>/<hour>.jsonl) at all? One listing instead of 744 reads."""
    check = getattr(store, "has_lines", None)
    if check is None:
        return True                                    # a store that cannot tell: look the slow way
    try:
        return bool(check(PREFIX.rstrip("/")))
    except Exception:  # noqa: BLE001
        return True


def _row(item: dict[str, Any]) -> dict[str, Any]:
    """The recorder's item (and a version 1 line) as a request_log row."""
    return {"requestedAt": item["at"], "method": str(item.get("method") or "?")[:8], "path": str(item.get("path") or "")[:300],
            "queryParamNames": item.get("query") or [], "httpStatus": int(item.get("status") or 0),
            "durationMs": item.get("durationMs"), "bytesSent": item.get("bytes"), "caller": item.get("actor") or None,
            "callerType": item.get("actorType") or None, "clientIp": item.get("client") or None, "userAgent": item.get("agent") or None}


def _item(row: dict[str, Any]) -> dict[str, Any]:
    """A request_log row in the shape the Logs screen reads."""
    return {"at": row["requestedAt"], "method": row["method"], "path": row["path"], "query": row.get("queryParamNames") or [],
            "status": row["httpStatus"], "durationMs": row.get("durationMs") or 0, "bytes": row.get("bytesSent"),
            "client": row.get("clientIp") or "", "agent": row.get("userAgent") or "", "actor": row.get("caller") or "",
            "actorType": row.get("callerType") or ""}


class RequestLog:
    def __init__(self, identify: Callable[[Any], tuple[str, str]]):
        self.identify = identify
        self.queue: queue.Queue = queue.Queue(maxsize=20_000)
        self.dropped = 0
        self.written = 0
        self._thread: threading.Thread | None = None
        self._last_cleanup = ""
        self.inline = False            # tests: write at once

    # ------------------------------------------------------------------ recording
    def wanted(self, method: str, path: str) -> bool:
        if not enabled() or path.startswith(SKIP_PREFIXES) or path in SKIP_EXACT:
            return False
        return not (method == "GET" and _DELIVERY.match(path) and not path.endswith("/file"))

    def record(self, request: Any, status: int, duration_ms: int, client: str, size: int | None) -> None:
        path = request.url.path
        if not self.wanted(request.method, path):
            return
        item = {"at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "method": request.method,
                "path": _DELIVERY.sub("/api/deliveries/…", path)[:300],
                "query": sorted({key for key in request.query_params.keys()})[:20],
                "status": int(status), "durationMs": int(duration_ms), "bytes": size, "client": client,
                "agent": (request.headers.get("user-agent") or "")[:160], "_request": request}
        if self.inline:
            self._write([item])
            return
        try:
            self.queue.put_nowait(item)
        except queue.Full:
            self.dropped += 1
            return
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="request-log", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        while True:
            batch = [self.queue.get()]
            while len(batch) < 500:
                try:
                    batch.append(self.queue.get_nowait())
                except queue.Empty:
                    break
            try:
                self._write(batch)
            except Exception:  # noqa: BLE001 - logging must never stop the service
                LOG.exception("Could not save %d request records", len(batch))

    def _write(self, batch: list[dict[str, Any]]) -> None:
        rows = []
        for item in batch:
            request = item.pop("_request", None)
            try:
                item["actor"], item["actorType"] = self.identify(request) if request is not None else ("", "")
            except Exception:  # noqa: BLE001
                item["actor"], item["actorType"] = "?", "unknown"
            rows.append(_row(item))
        self.written += get_records().add_requests(rows)
        today = datetime.now(timezone.utc).date().isoformat()
        if self._last_cleanup != today:
            self._last_cleanup = today
            self.cleanup()

    @staticmethod
    def cleanup() -> int:
        """Remove the rows older than the keep time (and version 1 hour documents of the 7 days before the cutoff)."""
        oldest_kept = datetime.now(timezone.utc).date() - timedelta(days=keep_days())
        removed = get_records().remove_before("requests", datetime.combine(oldest_kept, datetime.min.time(), timezone.utc))
        store = get_store()
        if hasattr(store, "delete_lines") and _has_v1_documents(store):
            for back in range(1, 8):
                day = (oldest_kept - timedelta(days=back)).isoformat()
                removed += sum(1 for hour in range(24) if store.delete_lines(hour_key(day, hour)))
        return removed

    @staticmethod
    def upgrade() -> int:
        """Version 1 hour documents of the kept days -> rows of request_log, once per day (the documents are kept until
        they are older than the keep time)."""
        store = get_store()
        if not _has_v1_documents(store):
            return 0                                   # a new installation, or everything already removed
        records = get_records()
        done = store.read_json("meta/request-log-v2.json", None) or {}
        copied = 0
        for day in RequestLog.days():
            if day in done:
                continue
            rows = []
            for hour in range(24):
                for chunk in store.lines(hour_key(day, hour)):
                    for line in str(chunk).splitlines():
                        try:
                            item = json.loads(line)
                            rows.append(_row(item))
                        except (ValueError, TypeError, KeyError):
                            continue
            if rows:
                copied += records.add_requests(rows)
            done[day] = len(rows)
        store.write_json("meta/request-log-v2.json", done)
        return copied

    # ------------------------------------------------------------------ reading
    @staticmethod
    def days() -> list[str]:
        """The days that can hold records (newest first)."""
        today = datetime.now(timezone.utc).date()
        return [(today - timedelta(days=back)).isoformat() for back in range(keep_days() + 1)]

    @staticmethod
    def read(day: str, limit: int = 500, status: str = "", search: str = "", method: str = "", actor: str = "") -> dict[str, Any]:
        """One day's requests, newest first, with totals for that day (after the filters)."""
        limit = max(1, min(int(limit), 5000))
        search = search.strip().casefold()[:200]
        rows: list[dict[str, Any]] = []
        totals = {"requests": 0, "errors": 0, "slow": 0, "durationMs": 0}
        by_path: dict[str, int] = {}
        start = datetime.combine(datetime.fromisoformat(check_day(day)).date(), datetime.min.time(), timezone.utc)
        stored = get_records().requests(since=start, until=start + timedelta(days=1), limit=100_000)
        for row in reversed(stored):                          # oldest first, like the day's log
            item = _item(row)
            code = int(item.get("status", 0))
            if status == "errors" and code < 400 or status == "ok" and code >= 400 or status.isdigit() and str(code) != status:
                continue
            if method and item.get("method") != method.upper():
                continue
            if actor and actor.casefold() not in str(item.get("actor", "")).casefold():
                continue
            if search and search not in f"{item.get('path')} {item.get('actor')} {item.get('client')} {code}".casefold():
                continue
            totals["requests"] += 1
            totals["errors"] += code >= 400
            totals["slow"] += int(item.get("durationMs", 0)) >= 2000
            totals["durationMs"] += int(item.get("durationMs", 0))
            by_path[item.get("path", "")] = by_path.get(item.get("path", ""), 0) + 1
            rows.append(item)
        rows.reverse()
        busiest = sorted(by_path.items(), key=lambda pair: -pair[1])[:10]
        average = totals["durationMs"] // totals["requests"] if totals["requests"] else 0
        return {"day": day, "records": rows[:limit], "more": len(rows) > limit,
                "totals": {**totals, "averageMs": average}, "busiest": [{"path": p, "count": c} for p, c in busiest]}

    def stats(self) -> dict[str, Any]:
        return {"enabled": enabled(), "waiting": self.queue.qsize(), "written": self.written, "dropped": self.dropped,
                "keepDays": keep_days()}
