"""Dataset refreshes in the background: by hand, on a schedule (refreshEveryMinutes) or through the API.

At most ANALYTICS_REFRESH_WORKERS imports run at the same time (default 1), so imports never take the server's
memory or the source database's capacity away from reports. A dataset already queued or refreshing is not queued
twice. The report scheduler's loop calls tick() to start the refreshes that are due.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException

from backend.app.analytics import importer, settings, store
from backend.app.connectors.errors import ConnectorQueryError, MongoReadAccessError
from backend.app.logs import LOG


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds")


def next_refresh(dataset: dict[str, Any], after: datetime | None = None) -> str:
    every = int(dataset.get("refreshEveryMinutes") or 0)
    return _iso((after or _now()) + timedelta(minutes=every)) if every else ""


def friendly_error(exc: BaseException) -> str:
    if isinstance(exc, HTTPException):
        return str(exc.detail)[:500]
    if isinstance(exc, (ValueError, ConnectorQueryError, MongoReadAccessError)):
        return str(exc)[:500]
    return f"The import failed ({type(exc).__name__}); see the service log"


class Refresher:
    def __init__(self, ctx: SimpleNamespace):
        self.ctx = ctx
        self.executor = ThreadPoolExecutor(max_workers=settings.refresh_workers(), thread_name_prefix="analytics-refresh")
        self.inline = False                     # tests: refresh in the calling thread
        self._busy: set[str] = set()
        self._lock = threading.Lock()

    def recover(self) -> None:
        """Imports that were running when the service stopped will not finish."""
        try:
            for item in store.datasets():
                if item.get("status") in ("queued", "refreshing"):
                    store.save_dataset({**item, "status": "ready" if item.get("file") else "failed",
                                        "lastError": "The service was restarted during the refresh; refresh again"})
        except Exception:  # noqa: BLE001 - a broken system database is reported elsewhere
            LOG.exception("Could not check the analytics datasets at start-up")

    def busy(self, dataset_id: str) -> bool:
        with self._lock:
            return dataset_id in self._busy

    def request(self, dataset: dict[str, Any], trigger: str, actor: str) -> dict[str, Any]:
        """Queue a refresh. Returns the dataset with status "queued" (or as it is when already busy)."""
        with self._lock:
            if dataset["id"] in self._busy:
                return dataset
            self._busy.add(dataset["id"])
        queued = store.save_dataset({**dataset, "status": "queued"})
        if self.inline:
            self._run(dataset["id"], trigger, actor)
            return store.dataset(dataset["id"]) or queued
        self.executor.submit(self._run, dataset["id"], trigger, actor)
        return queued

    def _run(self, dataset_id: str, trigger: str, actor: str) -> None:
        started = _now()
        record = {"id": store.new_id("rf"), "datasetId": dataset_id, "trigger": trigger, "actor": actor[:128],
                  "status": "running", "startedAt": _iso(started)}
        try:
            dataset = store.dataset(dataset_id)
            if dataset is None:
                record["status"] = "gone"
                return
            store.save_dataset({**dataset, "status": "refreshing"})
            try:
                result = importer.run_import(self.ctx, dataset)
            except Exception as exc:  # noqa: BLE001 - every failure is written to the dataset and the log
                if not isinstance(exc, (ValueError, HTTPException, ConnectorQueryError, MongoReadAccessError)):
                    LOG.exception("Refresh of dataset %s failed", dataset_id)
                message = friendly_error(exc)
                current = store.dataset(dataset_id)
                if current is None:                      # deleted while it was importing: do not bring it back
                    record["status"] = "gone"
                    return
                store.save_dataset({**current, "status": "failed" if not current.get("file") else "ready",
                                    "lastError": message, "nextRefreshAt": next_refresh(current)})
                record.update({"status": "failed", "error": message})
                self._log_run(dataset, "failed", started, error=message)
                return
            current = store.dataset(dataset_id)
            if current is None:                          # deleted while it was importing: drop the new file too
                importer.remove_old_files(dataset_id, keep="")
                record["status"] = "gone"
                return
            store.save_dataset({**current, "status": "ready", "file": result["file"], "rowCount": result["rowCount"],
                                "sizeBytes": result["sizeBytes"], "columns": result["columns"], "refreshedAt": _iso(_now()),
                                "lastError": "", "nextRefreshAt": next_refresh(current)})
            importer.remove_old_files(dataset_id, result["file"])
            record.update({"status": "success", "rowCount": result["rowCount"], "sizeBytes": result["sizeBytes"],
                           "durationMs": result["durationMs"]})
            self._log_run(dataset, "success", started, rows=result["rowCount"], duration=result["durationMs"])
            LOG.info("Dataset %s refreshed: %d records, %d bytes, %d ms", dataset_id, result["rowCount"], result["sizeBytes"],
                     result["durationMs"])
        finally:
            with self._lock:
                self._busy.discard(dataset_id)
            record.setdefault("finishedAt", _iso(_now()))
            if record["status"] == "running":
                record["status"] = "failed"
            try:
                if record["status"] != "gone":
                    store.add_refresh(record)
            except Exception:  # noqa: BLE001
                LOG.exception("Could not save the refresh record of %s", dataset_id)

    def _log_run(self, dataset: dict[str, Any], status: str, started: datetime, *, rows: int | None = None,
                 duration: int | None = None, error: str = "") -> None:
        """Imports read business data, so they are in the run log too (channel "analytics")."""
        try:
            self.ctx.log_run(channel="analytics", actor="analytics refresh", actor_type="system", status=status,
                             started_at=started, report_id=f"dataset:{dataset['id']}"[:80],
                             data_source=dataset.get("connectionId", ""), row_count=rows, duration_ms=duration,
                             error_code="refresh_failed" if error else "", error_message=error[:300])
        except Exception:  # noqa: BLE001 - the run log is not required for analytics
            LOG.debug("Run log for dataset %s not written", dataset.get("id"), exc_info=True)

    def tick(self, now: datetime | None = None) -> list[str]:
        """Start the scheduled refreshes that are due. Returns their dataset IDs."""
        now = now or _now()
        started = []
        for item in store.datasets():
            due = item.get("nextRefreshAt")
            if not item.get("refreshEveryMinutes") or not due or item.get("status") in ("queued", "refreshing"):
                continue
            try:
                if datetime.fromisoformat(str(due).replace("Z", "+00:00")) > now:
                    continue
            except ValueError:
                pass
            self.request(item, "schedule", "scheduler")
            started.append(item["id"])
        return started

    def stop(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)
