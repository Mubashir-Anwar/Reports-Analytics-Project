"""Background jobs: scheduled reports and "run in background" requests.

One scheduler thread checks the schedules every REPORTING_SCHEDULER_POLL seconds (default 20) and hands due jobs to
a small worker pool (REPORTING_JOB_WORKERS, default 2). Run several app instances? Turn the scheduler on in only
one of them (REPORTING_SCHEDULER=off in the others); background runs work in every instance."""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from itertools import chain, islice
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from backend.app.connectors.errors import MongoReadAccessError
from backend.app.connectors.mongo_fallback import _row_value
from backend.app.definitions import AmbiguousReport, validate_parameters
from backend.app.jobs import delivery, mailer, schedule, store
from backend.app.logs import LOG
from backend.app.rendering import spool
from backend.app.rendering.outputs import render_report_output
from backend.app.security import api_keys, permissions, webhooks

MAIL_ATTACH_MAX = 15 * 1_048_576  # bigger files are not attached to e-mails (download them on /server, Runs)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime | None) -> str:
    # milliseconds: runs queued in the same second still sort in the order they were queued
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds") if moment else ""


class JobRunner:
    def __init__(self, ctx: SimpleNamespace):
        self.ctx = ctx
        self.workers = max(1, min(8, int(os.getenv("REPORTING_JOB_WORKERS", "2") or 2)))
        self.poll = max(5, int(os.getenv("REPORTING_SCHEDULER_POLL", "20") or 20))
        self.scheduler_on = os.getenv("REPORTING_SCHEDULER", "on").strip().lower() not in ("off", "false", "0", "no")
        self.executor = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="report-job")
        self.inline = False  # tests: run jobs in the calling thread
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------- life cycle
    def start(self) -> None:
        self._recover()
        if self.scheduler_on and self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="report-scheduler", daemon=True)
            self._thread.start()
            LOG.info("Report scheduler started (every %d s, %d workers)", self.poll, self.workers)

    def stop(self) -> None:
        self._stop.set()
        self.executor.shutdown(wait=False, cancel_futures=True)

    def _recover(self) -> None:
        """Runs that were queued or running when the service stopped will not finish: mark them failed."""
        for run in store.load_runs():
            if run.get("status") in ("queued", "running"):
                store.save_run({**run, "status": "failed", "error": "The service was restarted before the run finished",
                                "finishedAt": _iso(_now())})

    def _loop(self) -> None:
        last_cleanup = 0.0
        while not self._stop.wait(self.poll):
            try:
                self.tick()
                refresher = getattr(self.ctx, "analytics_refresher", None)
                if refresher is not None:
                    refresher.tick()                   # analytics datasets with a refresh schedule
                if time.monotonic() - last_cleanup > 3600:
                    last_cleanup = time.monotonic()
                    self.remove_old_outputs()
            except Exception:  # noqa: BLE001 - the scheduler must keep running
                LOG.exception("Scheduler tick failed")

    @staticmethod
    def remove_old_outputs(now: datetime | None = None) -> int:
        """Report files hold business data: runs older than REPORTING_OUTPUT_DAYS (default 30) are deleted with
        their files, so outputs do not pile up on the server."""
        days = max(1, int(os.getenv("REPORTING_OUTPUT_DAYS", "30") or 30))
        cutoff = _iso((now or _now()) - timedelta(days=days))
        removed = store.delete_runs(lambda run: (run.get("finishedAt") or run.get("queuedAt") or "") < cutoff)
        if removed:
            LOG.info("Removed %d report runs older than %d days (with their files)", removed, days)
        return removed

    # ------------------------------------------------------------- scheduling
    def tick(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """Start every enabled schedule whose time has come and move it to its next time. Returns the runs."""
        now = now or _now()
        due: list[dict[str, Any]] = []
        with store.LOCK:
            schedules = store.load_schedules()
            changed = False
            for job in schedules:
                at = job.get("nextRunAt")
                if not job.get("enabled") or not at or datetime.fromisoformat(at) > now:
                    continue
                following = schedule.next_run(job["recurrence"], job["timezone"], now)
                job["nextRunAt"] = _iso(following)
                if following is None:
                    job["enabled"] = False
                changed = True
                due.append(dict(job))
            if changed:
                store.save_schedules(schedules)
        return [self.enqueue(job["reportId"], job["format"], job["parameters"], job["owner"], "scheduled", job, job.get("folder"))
                for job in due]

    def report(self, report_id: str, folder: str | None) -> Any:
        """The report of a schedule or run: <folder>/<ID>. Older records have no folder: the only report with the ID."""
        return self.ctx.reports_by_id.find(report_id, folder or None)

    # ------------------------------------------------------------- running
    def enqueue(self, report_id: str, output_format: str, parameters: dict[str, str], owner: str, kind: str,
                job: dict[str, Any] | None = None, folder: str | None = None) -> dict[str, Any]:
        try:
            definition = self.report(report_id, folder)
        except AmbiguousReport:
            definition = None
        run = {
            "id": schedule.new_id("run"), "jobId": job["id"] if job else "", "jobLabel": job["label"] if job else "",
            "kind": kind, "reportId": report_id, "folder": definition.folder if definition else (folder or ""),
            "title": definition.title if definition else report_id,
            "format": output_format, "parameters": dict(parameters), "owner": owner, "status": "queued", "queuedAt": _iso(_now()),
            "startedAt": "", "finishedAt": "", "rowCount": 0, "error": "", "file": "", "size": 0, "email": "",
            # stored masked (CNIC, account numbers); the run itself uses the values given here
            "_sensitive": sorted(self.ctx.sensitive_parameters(definition)) if definition else [],
        }
        store.save_run(run)
        if self.inline:
            self.execute(run, job)
            return next((r for r in store.load_runs() if r["id"] == run["id"]), run)
        self.executor.submit(self.execute, run, job)
        return run

    def _quiet_report(self, run: dict[str, Any]) -> Any:
        try:
            return self.report(run["reportId"], run.get("folder"))
        except AmbiguousReport:
            return None

    def execute(self, run: dict[str, Any], job: dict[str, Any] | None = None) -> None:
        ctx = self.ctx
        started = time.monotonic()
        run = {**run, "status": "running", "startedAt": _iso(_now())}
        store.save_run(run)
        audit: dict[str, Any] = {"requestedAt": _now(), "path": f"/jobs/{run['jobId'] or run['id']}", "reportId": run["reportId"],
                                 "status": "failed", "rowCount": 0, "access": run["kind"], "format": run["format"],
                                 "GeneratedBy": run["owner"]}
        body = b""
        try:
            try:
                definition = self.report(run["reportId"], run.get("folder"))
            except AmbiguousReport as exc:
                raise ValueError(f"{exc.detail} — edit the schedule and choose the report again") from None
            owner = ctx.session_for_user(run["owner"])
            # checked again at run time: the owner may have lost the permission (or the account) since scheduling
            if definition is None or owner is None or ctx.report_level(owner, definition) < permissions.RUN:
                raise ValueError("The report is no longer published on the server, or its owner may no longer run it")
            if owner.get("apiKey") and not api_keys.folder_allowed(owner["apiKey"], ctx.report_folder(definition)):
                raise ValueError("The API key may no longer run reports in this folder")
            enabled = (definition.editor_config or {}).get("formats", ["pdf", "xlsx", "csv"])
            if run["format"] not in enabled:
                raise ValueError(f"The {run['format'].upper()} format is not enabled for this report")
            tz = ZoneInfo(job["timezone"]) if job else ZoneInfo(schedule.default_timezone())
            today = datetime.now(tz).date()
            prompted = {spec.name for spec in definition.parameters if spec.prompt}
            raw = (job or {}).get("parameters") or run.get("parameters") or {}
            pairs = [(k, str(schedule.resolve_relative(v, today))) for k, v in raw.items() if k in prompted]
            parameters, _ = validate_parameters(definition, pairs)
            audit["parameters"] = {k: v.isoformat() if isinstance(v, date) else v for k, v in parameters.items()}
            audit["connectionId"] = definition.connection_id
            name = f"{run['id']}.{run['format']}"
            rows = self._first_rows(definition, parameters, run["format"])
            if isinstance(rows, tuple):           # more than REPORT_MAX_ROWS: a big CSV / Excel written to disk
                first, rest = rows
                count = self._spool(definition, run["format"], name, chain(first, rest))
                size = spool.export_path(name).stat().st_size
                run.update({"status": "success", "rowCount": count, "file": name, "size": size, "spooled": True})
                audit.update({"status": "success", "rowCount": count})
            else:
                rows = [{key: _row_value(row, key) for key in definition.columns} for row in rows]
                body, _media = render_report_output(definition, parameters, rows, run["format"],
                                                    ctx.subreport_loader(ctx.report_allowed_for(owner), definition.folder),
                                                    ctx.dataset_loader(definition.connection_id, definition.report_id))
                store.write_output(name, body)
                run.update({"status": "success", "rowCount": len(rows), "file": name, "size": len(body)})
                audit.update({"status": "success", "rowCount": len(rows)})
        except HTTPException as exc:
            run.update({"status": "failed", "error": str(exc.detail)})
            audit["errorCode"] = str(exc.status_code)
        except Exception as exc:  # noqa: BLE001 - every failure is recorded on the run
            LOG.warning("Job run %s (%s) failed: %s", run["id"], run["reportId"], exc)
            run.update({"status": "failed", "error": str(exc)[:500] or exc.__class__.__name__})
            audit["errorCode"] = "job_error"
        finally:
            run["finishedAt"] = _iso(_now())
            audit["durationMs"] = int((time.monotonic() - started) * 1000)
            by_key = run["owner"].startswith("apikey:")
            ctx.log_run_from_audit(audit, channel="api" if by_key else ("schedule" if run.get("jobId") else "server"),
                                   actor=run["owner"], actor_type="api-key" if by_key else "user",
                                   definition=self._quiet_report(run), body=body or None, run_id=run["id"])
        if job and job.get("email", {}).get("to"):
            if run.get("spooled") and run.get("size", 0) <= MAIL_ATTACH_MAX:
                body = spool.export_path(run["file"]).read_bytes()
            run["email"] = self._mail(job, run, body)
        if job and job.get("deliver"):                      # SFTP / an API / a network folder
            source: Any = None
            if run["status"] == "success":
                source = spool.export_path(run["file"]) if run.get("spooled") else (body or store.read_output(run))
            run["delivered"] = delivery.deliver_run(job, run, source)
        store.save_run(run, keep_for_job=job.get("keep", 10) if job else None)
        if job:
            self._record_last_run(job["id"], run)
        if run["owner"].startswith("apikey:"):
            self._notify_key(run)

    def _first_rows(self, definition: Any, parameters: dict[str, Any], output_format: str) -> Any:
        """The records of a run: first the normal run (up to REPORT_MAX_ROWS, a list for the normal, designed file).
        Only when that says "too many records" and the file is a CSV / Excel: (the first records, the rest still being
        read) for a file written to disk. Small reports never take the streaming path."""
        ctx = self.ctx
        max_rows = ctx.settings.max_rows
        try:
            return ctx.run_report_rows(definition, parameters, definition.connection_id, max_rows=max_rows, limit_rows=False)
        except HTTPException as too_many:
            if too_many.status_code != 413 or output_format not in ("csv", "xlsx") or ctx.settings.export_max_rows <= max_rows:
                raise
            try:
                stream = ctx.iter_report_rows(definition, parameters, definition.connection_id, limit=ctx.settings.export_max_rows)
                first = list(islice(stream, max_rows + 1))
            except MongoReadAccessError:
                raise too_many from None          # no aggregate permission: only the normal (limited) run is possible
        if len(first) <= max_rows:
            stream.close()
            return first
        LOG.info("Report %s has more than %d records: writing the %s to disk while reading", definition.report_id, max_rows,
                 output_format.upper())
        return first, stream

    @staticmethod
    def _spool(definition: Any, output_format: str, name: str, rows: Any) -> int:
        columns = list(definition.columns)
        layout = (definition.editor_config or {}).get("layout", {}) or {}
        labels = layout.get("columnLabels", {}) or {}
        plain = ({key: _row_value(row, key) for key in columns} for row in rows)
        path = spool.export_path(name)
        if output_format == "csv":
            return spool.write_csv(path, columns, labels, plain)
        return spool.write_xlsx(path, str(layout.get("title") or definition.title), columns, labels, plain)

    @staticmethod
    def _notify_key(run: dict[str, Any]) -> None:
        """The API key that asked for this run gets its webhook call (when it has one)."""
        key = api_keys.key_for_owner(run["owner"][7:])
        if not key or not key.get("webhookUrl") or not key.get("webhookSecret"):
            return
        job = {name: run.get(name) for name in ("id", "reportId", "title", "format", "status", "rowCount", "error", "size",
                                                "queuedAt", "startedAt", "finishedAt")}
        job["downloadUrl"] = f"/api/v1/jobs/{run['id']}/file" if run.get("status") == "success" else None
        webhooks.deliver_later(key["webhookUrl"], key["webhookSecret"], "job.finished", {"job": job})

    def _mail(self, job: dict[str, Any], run: dict[str, Any], body: bytes) -> str:
        email = job["email"]
        ok = run["status"] == "success"
        subject = email.get("subject") or f"{run['title']} — {datetime.now(timezone.utc).date().isoformat()}"
        if not ok:
            subject = f"FAILED: {subject}"
        lines = [email.get("message") or f"The scheduled report \"{run['title']}\" has run.", "",
                 f"Status: {run['status']}", f"Records: {run['rowCount']}"]
        if not ok:
            lines.append(f"Error: {run['error']}")
        attachment = (f"{run['reportId']}.{run['format']}", body) if ok and body and email.get("attach", True) else None
        if ok and email.get("attach", True) and not body:
            lines.append(f"The file is too big to send by e-mail ({run.get('size', 0) // 1_048_576} MB): download it on /server, Runs.")
        try:
            mailer.send(email["to"], subject, "\n".join(lines), attachment)
            return f"sent to {len(email['to'])}"
        except Exception as exc:  # noqa: BLE001 - the report itself succeeded; the e-mail problem is shown on the run
            LOG.warning("E-mail for job %s failed: %s", job["id"], exc)
            return f"not sent: {str(exc)[:200]}"

    @staticmethod
    def _record_last_run(job_id: str, run: dict[str, Any]) -> None:
        with store.LOCK:
            schedules = store.load_schedules()
            for job in schedules:
                if job["id"] == job_id:
                    job["lastRun"] = {"runId": run["id"], "status": run["status"], "at": run["finishedAt"], "error": run["error"],
                                      "rowCount": run.get("rowCount", 0), "file": bool(run.get("file"))}
            store.save_schedules(schedules)
