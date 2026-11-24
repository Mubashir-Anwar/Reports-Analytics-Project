"""How many reports this process makes at the same time (so many callers cannot fill the server's memory).

Every full report run (/generate, the public API, /server Run, the designer's Run, embedding, JSON data) takes a
place before it reads any data:

* at most REPORT_MAX_CONCURRENT runs at once in this process (default: CPU count, 2 to 8);
* at most REPORT_MAX_PER_CALLER of them for the same signed-in user (default 2), and at most half of all places for
  one API key / token (a website's server sends all its visitors' reports with one key), so nobody takes every place;
* when every place is taken a request waits up to REPORT_QUEUE_WAIT_SECONDS (default 15) and then gets
  503 "busy" with Retry-After, instead of piling up and running the server out of memory.

Background runs and schedules do not use these places: they have their own fixed pool (REPORTING_JOB_WORKERS).
With several worker processes (uvicorn --workers N) each process has its own places: N × REPORT_MAX_CONCURRENT.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

from fastapi import HTTPException

BUSY_RETRY_SECONDS = 10


class RunGate:
    def __init__(self, limit: int, per_caller: int, wait_seconds: float, live: bool = False):
        self.limit = max(1, int(limit))
        self.per_caller = max(1, int(per_caller))
        self.wait_seconds = max(0.0, float(wait_seconds))
        self.live = live                       # follow Admin → Limits (the app); tests pass fixed numbers
        self._lock = threading.Lock()
        self._free = threading.Condition(self._lock)
        self._busy = 0                         # places taken (a count, so the limit can change while running)
        self._by_caller: dict[str, int] = {}
        self.running = 0
        self.waiting = 0
        self.peak = 0
        self.served = 0
        self.rejected_busy = 0
        self.rejected_caller = 0
        self.started_at = time.time()

    @contextmanager
    def slot(self, caller: str) -> Iterator[None]:
        """Hold one place while the report is made. 429 when this caller already has its share, 503 when full."""
        caller = caller or "anonymous"
        self._follow_limits()
        share = self.share(caller)
        with self._lock:
            if self._by_caller.get(caller, 0) >= share:
                self.rejected_caller += 1
                raise HTTPException(
                    status_code=429,
                    detail=(f"You already have {share} report(s) being made. Wait for them to finish, "
                            "or run big reports in the background."),
                    headers={"Retry-After": str(BUSY_RETRY_SECONDS)})
            self._by_caller[caller] = self._by_caller.get(caller, 0) + 1
            self.waiting += 1
        acquired = False
        try:
            acquired = self._take_place()
        finally:
            with self._lock:
                self.waiting -= 1
                if acquired:
                    self.running += 1
                    self.peak = max(self.peak, self.running)
                else:
                    self._release_caller(caller)
                    self.rejected_busy += 1
        if not acquired:
            raise HTTPException(
                status_code=503,
                detail=(f"The report server is busy ({self.limit} reports are being made). Try again in a few seconds, "
                        "or run the report in the background."),
                headers={"Retry-After": str(BUSY_RETRY_SECONDS)})
        try:
            yield
        finally:
            with self._lock:
                self._busy -= 1
                self._free.notify()
                self.running -= 1
                self.served += 1
                self._release_caller(caller)

    def _take_place(self) -> bool:
        """Wait (up to wait_seconds) until fewer than limit places are taken, then take one."""
        deadline = time.monotonic() + self.wait_seconds
        with self._free:
            while self._busy >= self.limit:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._free.wait(left)
            self._busy += 1
            return True

    def resize(self, limit: int, per_caller: int, wait_seconds: float) -> None:
        """New numbers from Admin → Limits: a larger limit lets waiting requests in at once; a smaller one lets
        the reports already running finish."""
        with self._free:
            self.limit, self.per_caller, self.wait_seconds = max(1, int(limit)), max(1, int(per_caller)), max(0.0, float(wait_seconds))
            self._free.notify_all()

    def _follow_limits(self) -> None:
        if not self.live:
            return
        from backend.app import limits  # noqa: PLC0415
        wanted = (int(limits.value("REPORT_MAX_CONCURRENT")), int(limits.value("REPORT_MAX_PER_CALLER")),
                  float(limits.value("REPORT_QUEUE_WAIT_SECONDS")))
        if wanted != (self.limit, self.per_caller, self.wait_seconds):
            self.resize(*wanted)

    def share(self, caller: str) -> int:
        """How many places one caller may hold: a user its own few, a key or token (many visitors) up to half."""
        if caller.startswith("user:"):
            return min(self.per_caller, self.limit)
        return max(self.per_caller, (self.limit + 1) // 2)

    def _release_caller(self, caller: str) -> None:
        left = self._by_caller.get(caller, 0) - 1
        if left > 0:
            self._by_caller[caller] = left
        else:
            self._by_caller.pop(caller, None)

    def stats(self) -> dict[str, Any]:
        self._follow_limits()
        with self._lock:
            return {"limit": self.limit, "perCaller": self.per_caller, "waitSeconds": self.wait_seconds,
                    "running": self.running, "waiting": self.waiting, "peak": self.peak, "served": self.served,
                    "rejectedBusy": self.rejected_busy, "rejectedPerCaller": self.rejected_caller,
                    "callers": len(self._by_caller)}


def caller_of(access: str, user: str = "", address: str = "") -> str:
    """One name per caller: the user, the API key, or (shared tokens and links) the token kind and the address."""
    if user:
        return f"user:{user}"
    access = access or "anonymous"
    if access.startswith(("user:", "api-key:", "embed:")) or ":" in access:
        return access
    return f"{access}@{address}" if address else access
