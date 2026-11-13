"""Background jobs: scheduled reports and "run in background" requests.

One scheduler thread checks the schedules every REPORTING_SCHEDULER_POLL seconds (default 20) and hands due jobs to
a small worker pool (REPORTING_JOB_WORKERS, default 2). Run several app instances? Turn the scheduler on in only
one of them (REPORTING_SCHEDULER=off in the others); background runs work in every instance."""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from backend.app.connectors.mongo_fallback import _row_value
from backend.app.definitions import validate_parameters
from backend.app.jobs import mailer, schedule, store
from backend.app.logs import LOG
from backend.app.rendering.outputs import render_report_output
from backend.app.storage import audit as audit_store


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime | None) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds") if moment else ""


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
        while not self._stop.wait(self.poll):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - the scheduler must keep running
                LOG.exception("Scheduler tick failed")

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
        return [self.enqueue(job["reportId"], job["format"], job["parameters"], job["owner"], "scheduled", job) for job in due]

    # ------------------------------------------------------------- running
    def enqueue(self, report_id: str, output_format: str, parameters: dict[str, str], owner: str, kind: str,
                job: dict[str, Any] | None = None) -> dict[str, Any]:
        definition = self.ctx.reports_by_id.get(report_id)
        run = {
            "id": schedule.new_id("run"), "jobId": job["id"] if job else "", "jobLabel": job["label"] if job else "",
            "kind": kind, "reportId": report_id, "title": definition.title if definition else report_id,
            "format": output_format, "parameters": dict(parameters), "owner": owner, "status": "queued", "queuedAt": _iso(_now()),
            "startedAt": "", "finishedAt": "", "rowCount": 0, "error": "", "file": "", "size": 0, "email": "",
        }
        store.save_run(run)
        if self.inline:
            self.execute(run, job)
            return next((r for r in store.load_runs() if r["id"] == run["id"]), run)
        self.executor.submit(self.execute, run, job)
        return run

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
            definition = ctx.reports_by_id.get(run["reportId"])
            if definition is None:
                raise ValueError("The report is no longer published on the server")
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
            rows = ctx.run_report_rows(definition, parameters, definition.connection_id, max_rows=ctx.settings.max_rows, limit_rows=False)
            rows = [{key: _row_value(row, key) for key in definition.columns} for row in rows]
            body, _media = render_report_output(definition, parameters, rows, run["format"], ctx.subreport_loader(),
                                                ctx.dataset_loader(definition.connection_id, definition.report_id))
            name = f"{run['id']}.{run['format']}"
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
            try:
                audit_store._append_report_audit(audit)
            except OSError:
                LOG.exception("Job audit record could not be saved")
        if job and job.get("email", {}).get("to"):
            run["email"] = self._mail(job, run, body)
        store.save_run(run, keep_for_job=job.get("keep", 10) if job else None)
        if job:
            self._record_last_run(job["id"], run)

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
        attachment = (f"{run['reportId']}.{run['format']}", body) if ok and email.get("attach", True) else None
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
                    job["lastRun"] = {"runId": run["id"], "status": run["status"], "at": run["finishedAt"], "error": run["error"]}
            store.save_schedules(schedules)
