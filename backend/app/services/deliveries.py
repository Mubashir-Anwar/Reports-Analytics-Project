"""Report files made after the request has been answered ("deliver later").

Any report run that returns a file (/generate, the public API's run, the designer's Run and Preview downloads,
the /server Run, embedding's download) can be asked to deliver later by sending the header

    X-Deliver: later

The answer is then immediate, 202 {"delivery": {...}} with a status URL and a file URL, and the file is made by
a background worker on the server. So:

* leaving or reloading the page does not cancel the report (the browser picks the file up when it is ready,
  on whatever page the user is on);
* many downloads can be asked for at once: they wait in a queue for a free place (services/capacity.py) instead
  of being refused with 429/503;
* an API client (Postman, a script) can fire many runs at once and collect each file when it is ready.

Only the caller that asked can see the status and download the file: a delivery is tied to the caller's
credentials (session cookie, API key, Authorization header). Files are kept on disk next to the exports for
REPORTING_DELIVERY_KEEP_MINUTES (default 60) after they are ready, then removed. Deliveries live in this process:
with several worker processes, use the background jobs API (/api/v1/reports/{id}/jobs) instead.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import secrets
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from starlette.requests import Request
from starlette.responses import Response

from backend.app.constants import SESSION_COOKIE
from backend.app.logs import LOG
from backend.app.rendering import spool

HEADER = "x-deliver"
BUSY_CODES = (429, 503)
MAX_PER_CALLER = 50            # waiting + running deliveries one caller may have
MAX_WAITING = 500              # waiting + running deliveries in this process
QUEUE_WAIT_SECONDS = 30 * 60   # how long a delivery waits for a free place before it gives up
RETRY_SECONDS = 2.0            # how often a waiting delivery tries for a place again

# The routes that make a report file and may deliver it later (path templates as registered).
DEFERRABLE = {
    "/generate/{report_file}",
    "/api/v1/reports/{report_id}/run",
    "/api/admin/drafts/run",
    "/api/admin/reports/{report_id}/run",
    "/api/admin/designer/render",
    "/api/server/reports/{report_id}/run",
    "/api/embed/run",
}


def has_credentials(request: Request) -> bool:
    return bool(request.cookies.get(SESSION_COOKIE) or request.headers.get("authorization") or request.headers.get("x-api-key")
                or request.query_params.get("token"))


def wants_later(request: Request) -> bool:
    """Asked for, and the caller sent credentials (an anonymous request is answered, and refused, at once)."""
    return request.headers.get(HEADER, "").strip().lower() == "later" and has_credentials(request)


def caller_key(request: Request) -> str:
    """Who asked, as a hash of the credentials they sent (never stored as such)."""
    parts = [request.cookies.get(SESSION_COOKIE, ""), request.headers.get("authorization", ""),
             request.headers.get("x-api-key", ""), request.query_params.get("token", "")]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _keep_seconds() -> int:
    from backend.app import limits  # noqa: PLC0415
    return int(limits.value("REPORTING_DELIVERY_KEEP_MINUTES")) * 60


class Deliveries:
    def __init__(self, workers: int | None = None):
        count = workers or max(2, min(32, int(os.getenv("REPORTING_DELIVERY_WORKERS", "16") or 16)))
        self.executor = ThreadPoolExecutor(max_workers=count, thread_name_prefix="delivery")
        self._items: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.inline = False      # tests: make the file in the calling thread

    # ------------------------------------------------------------------ asking
    def submit(self, request: Request, make: Callable[[], Any]) -> Response:
        """Queue `make` (returns the handler's Response) and answer 202 with where to collect the file."""
        self.sweep()
        owner = caller_key(request)
        with self._lock:
            mine = [d for d in self._items.values() if d["owner"] == owner and d["status"] in ("queued", "running")]
            busy = sum(1 for d in self._items.values() if d["status"] in ("queued", "running"))
            if busy >= MAX_WAITING:
                raise HTTPException(status_code=503, detail="The server has too many downloads waiting; try again shortly",
                                    headers={"Retry-After": "10"})
            if len(mine) >= MAX_PER_CALLER:
                raise HTTPException(status_code=429, detail=f"You already have {MAX_PER_CALLER} downloads waiting; wait for some to finish")
            ident = secrets.token_urlsafe(24)
            segments = [s for s in request.url.path.split("/") if s]
            label = segments[-2] if segments[-1] in ("run", "render") and len(segments) > 1 else segments[-1]
            label = re.sub(r"[^\w.\- ]", "_", label)[:80]
            item = {"id": ident, "owner": owner, "status": "queued", "label": label, "path": request.url.path,
                    "method": request.method, "createdAt": _now(), "startedAt": "", "finishedAt": "", "filename": "",
                    "mediaType": "", "size": 0, "error": "", "statusCode": 0, "waitedForPlace": False,
                    "_file": "", "_done": 0.0}
            self._items[ident] = item
        LOG.info("Delivery %s queued for %s", ident[:8], request.url.path)
        if self.inline:
            self._work(ident, make)
        else:
            self.executor.submit(self._work, ident, make)
        from fastapi.responses import JSONResponse  # noqa: PLC0415
        return JSONResponse(status_code=202, content={"delivery": self.view(self._items[ident])},
                            headers={"Location": f"/api/deliveries/{ident}"})

    # ------------------------------------------------------------------ working
    def _work(self, ident: str, make: Callable[[], Any]) -> None:
        started = time.monotonic()
        self._update(ident, status="running", startedAt=_now())
        while True:
            try:
                response = make()
                self._store(ident, response)
                return
            except HTTPException as exc:
                # only "busy, try again" answers carry Retry-After; a 503 for an unreachable database does not and fails at once
                busy = exc.status_code in BUSY_CODES and "retry-after" in {k.lower() for k in (exc.headers or {})}
                if busy and time.monotonic() - started < QUEUE_WAIT_SECONDS:
                    # every place is taken: wait in the queue instead of failing (the point of "later")
                    self._update(ident, status="queued", waitedForPlace=True)
                    time.sleep(RETRY_SECONDS)
                    self._update(ident, status="running")
                    continue
                self._update(ident, status="failed", error=str(exc.detail)[:1000], statusCode=exc.status_code, finishedAt=_now(), _done=time.time())
                return
            except Exception as exc:  # noqa: BLE001 - every failure is shown on the delivery
                LOG.exception("Delivery %s failed", ident[:8])
                self._update(ident, status="failed", error=str(exc)[:500] or exc.__class__.__name__, statusCode=500, finishedAt=_now(), _done=time.time())
                return

    def _store(self, ident: str, response: Any) -> None:
        if not isinstance(response, Response):
            raise ValueError("This request does not make a file")
        if response.status_code >= 400:
            detail = bytes(getattr(response, "body", b"") or b"").decode("utf-8", "replace")[:1000]
            self._update(ident, status="failed", error=detail, statusCode=response.status_code, finishedAt=_now(), _done=time.time())
            return
        folder = spool.export_directory() / "deliveries"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{ident}.bin"
        if isinstance(response, FileResponse):
            shutil.copyfile(response.path, target)
        elif isinstance(response, StreamingResponse):
            async def drain() -> None:
                with target.open("wb") as handle:
                    async for chunk in response.body_iterator:
                        handle.write(chunk.encode("utf-8") if isinstance(chunk, str) else chunk)
            asyncio.run(drain())
        else:
            target.write_bytes(response.body)
        disposition = response.headers.get("content-disposition", "")
        name = re.search(r'filename="?([^";]+)"?', disposition)
        self._update(ident, status="ready", finishedAt=_now(), size=target.stat().st_size,
                     filename=name.group(1) if name else f"report-{ident[:8]}",
                     mediaType=response.headers.get("content-type", "application/octet-stream"),
                     _file=str(target), _done=time.time())

    def _update(self, ident: str, **change: Any) -> None:
        with self._lock:
            if ident in self._items:
                self._items[ident].update(change)

    # ------------------------------------------------------------------ collecting
    def find(self, request: Request, ident: str) -> dict[str, Any]:
        with self._lock:
            item = self._items.get(ident)
            if item is None or not secrets.compare_digest(item["owner"], caller_key(request)):
                raise HTTPException(status_code=404, detail="No such download (it may have expired)")
            return dict(item)

    def file(self, request: Request, ident: str) -> Response:
        item = self.find(request, ident)
        if item["status"] in ("queued", "running"):
            raise HTTPException(status_code=409, detail="The file is not ready yet", headers={"Retry-After": "2"})
        if item["status"] == "failed":
            raise HTTPException(status_code=item["statusCode"] or 500, detail=item["error"] or "The report failed")
        if not item["_file"] or not Path(item["_file"]).exists():
            raise HTTPException(status_code=404, detail="The file has expired")
        self._update(ident, downloadedAt=_now())
        return FileResponse(item["_file"], media_type=item["mediaType"], filename=item["filename"])

    def forget(self, request: Request, ident: str) -> dict[str, Any]:
        item = self.find(request, ident)
        with self._lock:
            self._items.pop(ident, None)
        if item["_file"]:
            Path(item["_file"]).unlink(missing_ok=True)
        return {"deleted": ident, "status": item["status"]}

    def mine(self, request: Request) -> list[dict[str, Any]]:
        owner = caller_key(request)
        with self._lock:
            items = [self.view(d) for d in self._items.values() if d["owner"] == owner]
        return sorted(items, key=lambda d: d["createdAt"], reverse=True)

    def stats(self) -> dict[str, int]:
        with self._lock:
            counts: dict[str, int] = {}
            for item in self._items.values():
                counts[item["status"]] = counts.get(item["status"], 0) + 1
        return counts

    def sweep(self) -> None:
        """Remove ready/failed deliveries older than the keep time, with their files."""
        cutoff = time.time() - _keep_seconds()
        with self._lock:
            old = [d for d in self._items.values() if d["status"] in ("ready", "failed") and d["_done"] < cutoff]
            for item in old:
                self._items.pop(item["id"], None)
        for item in old:
            if item["_file"]:
                Path(item["_file"]).unlink(missing_ok=True)

    @staticmethod
    def view(item: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in item.items() if not k.startswith("_") and k != "owner"}
        out["statusUrl"] = f"/api/deliveries/{item['id']}"
        out["fileUrl"] = f"/api/deliveries/{item['id']}/file" if item["status"] == "ready" else None
        return out


DELIVERIES = Deliveries()
