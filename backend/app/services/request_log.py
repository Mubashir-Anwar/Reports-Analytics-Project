"""Every request that reaches the server, kept in the system database (Report Server → Logs → Requests).

One record per request: when, method, path, the names of the query parameters (never their values: they can hold
customer data), status, time taken, size of the answer, caller (user / API key / token / anonymous) and address.
Records are written by a background thread in batches, so a request never waits for the database; they are stored
per day ("requests/YYYY-MM-DD.jsonl" in the system storage: files, SQL or MongoDB, like the run log) and days
older than REPORTING_REQUEST_LOG_DAYS (default 30) are removed. REPORTING_REQUEST_LOG=off switches it off.

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
        store = get_store()
        lines: dict[str, list[str]] = {}
        for item in batch:
            request = item.pop("_request", None)
            try:
                item["actor"], item["actorType"] = self.identify(request) if request is not None else ("", "")
            except Exception:  # noqa: BLE001
                item["actor"], item["actorType"] = "?", "unknown"
            lines.setdefault(hour_key(item["at"][:10], item["at"][11:13]), []).append(
                json.dumps(item, ensure_ascii=False, separators=(",", ":")))
        for key, rows in lines.items():
            for row in rows:
                store.append_line(key, row)
                self.written += 1
        today = datetime.now(timezone.utc).date().isoformat()
        if self._last_cleanup != today:
            self._last_cleanup = today
            self.cleanup()

    @staticmethod
    def cleanup() -> int:
        """Remove the days just past the keep time (the 7 days before the cutoff, so a stopped server catches up)."""
        store = get_store()
        if not hasattr(store, "delete_lines"):
            return 0
        oldest_kept = datetime.now(timezone.utc).date() - timedelta(days=keep_days())
        removed = 0
        for back in range(1, 8):
            day = (oldest_kept - timedelta(days=back)).isoformat()
            removed += sum(1 for hour in range(24) if store.delete_lines(hour_key(day, hour)))
        return removed

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
        store = get_store()
        stored = (line for hour in range(24) for chunk in store.lines(hour_key(day, hour)) for line in str(chunk).splitlines())
        for line in stored:
            try:
                item = json.loads(line)
            except ValueError:
                continue
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
