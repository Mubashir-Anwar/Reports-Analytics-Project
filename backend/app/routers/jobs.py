"""/server portal: report schedules (JasperReports Server "Schedule"), background runs and their output files.

Everyone signed in can run a report in the background. Creating schedules needs a designer or admin account.
Users see their own schedules and runs; administrators see everybody's."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response

from backend.app.definitions import AmbiguousReport, clean_folder, validate_parameters
from backend.app.jobs import delivery, mailer, schedule, store
from backend.app.jobs.runner import JobRunner
from backend.app.logs import LOG
from backend.app.security import permissions
from backend.app.security.users import role_at_least
from backend.app.services import access

MEDIA = {"pdf": "application/pdf", "csv": "text/csv; charset=utf-8",
         "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    json_body = ctx.json_body
    require_json_request = ctx.require_json_request
    require_session = ctx.require_session
    reports_by_id = ctx.reports_by_id
    runner: JobRunner = ctx.job_runner

    def visible(item: dict[str, Any], session: dict[str, Any]) -> bool:
        return session["role"] == "admin" or item.get("owner") == session["username"]

    def view(job: dict[str, Any]) -> dict[str, Any]:
        try:
            definition = runner.report(job["reportId"], job.get("folder"))
        except AmbiguousReport:
            definition = None
        return {**job, "describe": schedule.describe(job["recurrence"]), "title": definition.title if definition else job["reportId"],
                "published": definition is not None, "deliver": delivery.public_view(job.get("deliver") or [], job.get("id", ""))}

    def find_job(job_id: str, session: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        items = store.load_schedules()
        job = next((j for j in items if j.get("id") == job_id), None)
        if job is None or not visible(job, session):
            raise HTTPException(status_code=404, detail="Schedule not found")
        return items, job

    def find_run(run_id: str, session: dict[str, Any]) -> dict[str, Any]:
        run = next((r for r in store.load_runs() if r.get("id") == run_id), None)
        if run is None or not visible(run, session):
            raise HTTPException(status_code=404, detail="Run not found")
        return run

    def checked(payload: dict[str, Any], session: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
        """The schedule as it will be saved; its new delivery secrets are in job["_secrets"] (saved apart, never in it)."""
        try:
            may_run = lambda d: d.editor_config is not None and ctx.report_level(session, d) >= permissions.RUN  # noqa: E731
            job = schedule.clean_job(payload, report_ids={d.report_id for d in reports_by_id.values() if may_run(d)},
                                     allow_email=role_at_least(session["role"], "designer"))
            # "folder" names which report when the same ID is published in several folders
            definition = reports_by_id.find(job["reportId"], clean_folder(payload.get("folder")), may_run)
            if definition is None:
                raise ValueError("Choose a report that is published on the server")
            job["folder"] = definition.folder
            # parameter values are checked now (relative dates as of today), so mistakes show up when saving
            prompted = {spec.name for spec in definition.parameters if spec.prompt}
            today = datetime.now(schedule.zone(job["timezone"])).date()
            pairs = [(k, str(schedule.resolve_relative(v, today))) for k, v in job["parameters"].items() if k in prompted]
            validate_parameters(definition, pairs)
            enabled = (definition.editor_config or {}).get("formats", ["pdf", "xlsx", "csv"])
            if job["format"] not in enabled:
                raise ValueError(f"The {job['format'].upper()} format is not enabled for this report")
            job["deliver"], job["_secrets"] = delivery.clean_targets(payload.get("deliver"), (previous or {}).get("deliver"),
                                                                     is_admin=session["role"] == "admin")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return job

    @app.get("/api/server/schedules/info")
    def schedules_info(request: Request) -> dict[str, Any]:
        session = require_session(request)
        return {"emailConfigured": mailer.configured(), "timezone": schedule.default_timezone(),
                "schedulerOn": runner.scheduler_on, "canSchedule": role_at_least(session["role"], "designer"),
                "canEmail": role_at_least(session["role"], "designer"),
                "delivery": {"types": ["sftp", "http", *(["folder"] if session["role"] == "admin" and delivery.allowed_roots() else [])],
                             "folders": [str(p) for p in delivery.allowed_roots()] if session["role"] == "admin" else [],
                             "fileNameTokens": list(delivery.NAME_TOKENS), "defaultFileName": delivery.DEFAULT_NAME}}

    @app.get("/api/server/schedules")
    def list_schedules(request: Request, reportId: str = "", folder: str = "") -> dict[str, Any]:  # noqa: N803 - query name
        session = require_session(request)
        where = clean_folder(folder)
        items = [view(j) for j in store.load_schedules() if visible(j, session) and (not reportId or j.get("reportId") == reportId)
                 and (where is None or j.get("folder") in (None, "", where))]
        return {"schedules": sorted(items, key=lambda j: j.get("nextRunAt") or "9999")}

    @app.post("/api/server/schedules")
    async def create_schedule(request: Request) -> dict[str, Any]:
        session = require_session(request, "designer")
        require_json_request(request)
        job = checked(await json_body(request), session)
        now = datetime.now(timezone.utc)
        following = schedule.next_run(job["recurrence"], job["timezone"], now)
        if following is None:
            raise HTTPException(status_code=422, detail="This time is already in the past")
        new_secrets = job.pop("_secrets", {})
        job.update({"id": schedule.new_id("job"), "owner": session["username"], "createdAt": now.isoformat(timespec="seconds"),
                    "updatedAt": now.isoformat(timespec="seconds"), "nextRunAt": following.isoformat(timespec="seconds"), "lastRun": None})
        with store.LOCK:
            items = store.load_schedules()
            if sum(1 for j in items if j.get("owner") == session["username"]) >= 200:
                raise HTTPException(status_code=422, detail="At most 200 schedules per user")
            store.save_schedules([*items, job])
        delivery.save_secrets(job["id"], job["deliver"], new_secrets)
        LOG.info("Schedule %s for %s created by %s (%s)", job["id"], job["reportId"], session["username"], schedule.describe(job["recurrence"]))
        return {"schedule": view(job)}

    @app.put("/api/server/schedules/{job_id}")
    async def update_schedule(job_id: str, request: Request) -> dict[str, Any]:
        session = require_session(request, "designer")
        require_json_request(request)
        payload = await json_body(request)
        with store.LOCK:
            items, job = find_job(job_id, session)
            cleaned = checked(payload, session, job)
            new_secrets = cleaned.pop("_secrets", {})
            now = datetime.now(timezone.utc)
            following = schedule.next_run(cleaned["recurrence"], cleaned["timezone"], now)
            job.update(cleaned)
            job.update({"updatedAt": now.isoformat(timespec="seconds"), "nextRunAt": following.isoformat(timespec="seconds") if following else ""})
            if following is None:
                job["enabled"] = False
            store.save_schedules(items)
        delivery.save_secrets(job_id, job.get("deliver") or [], new_secrets)
        return {"schedule": view(job)}

    @app.post("/api/server/schedules/{job_id}/enabled")
    async def enable_schedule(job_id: str, request: Request) -> dict[str, Any]:
        session = require_session(request, "designer")
        require_json_request(request)
        on = (await json_body(request)).get("enabled") is True
        with store.LOCK:
            items, job = find_job(job_id, session)
            following = schedule.next_run(job["recurrence"], job["timezone"], datetime.now(timezone.utc)) if on else None
            if on and following is None:
                raise HTTPException(status_code=422, detail="This one-time schedule is over; change its time first")
            job["enabled"] = on
            job["nextRunAt"] = following.isoformat(timespec="seconds") if following else ""
            store.save_schedules(items)
        return {"schedule": view(job)}

    @app.delete("/api/server/schedules/{job_id}")
    def delete_schedule(job_id: str, request: Request, outputs: bool = False) -> dict[str, Any]:
        session = require_session(request, "designer")
        with store.LOCK:
            items, job = find_job(job_id, session)
            store.save_schedules([j for j in items if j is not job])
        delivery.forget_secrets(job_id)
        removed = store.delete_runs(lambda r: r.get("jobId") == job_id) if outputs else 0
        LOG.info("Schedule %s deleted by %s", job_id, session["username"])
        return {"deleted": job_id, "runsRemoved": removed}

    @app.post("/api/server/schedules/delivery/test")
    async def test_delivery(request: Request) -> dict[str, Any]:
        """Try one delivery target before saving: SFTP logs in and opens the folder; nothing is uploaded.
        Body: {"target": {...}, "jobId": "<schedule whose saved password to use>"}."""
        session = require_session(request, "designer")
        require_json_request(request)
        payload = await json_body(request)
        previous: list[dict[str, Any]] = []
        saved: dict[str, str] = {}
        target = payload.get("target")
        if payload.get("jobId"):
            _items, job = find_job(str(payload["jobId"]), session)
            previous = job.get("deliver") or []
            saved = delivery._secrets().get(job["id"], {}).get(str((target or {}).get("id") or ""), {})
        try:
            cleaned, secret = delivery.clean_targets([target], previous, is_admin=session["role"] == "admin")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        from starlette.concurrency import run_in_threadpool  # noqa: PLC0415
        return await run_in_threadpool(delivery.test_target, cleaned[0], {**saved, **secret.get(cleaned[0]["id"], {})})

    @app.post("/api/server/schedules/{job_id}/run")
    def run_schedule_now(job_id: str, request: Request) -> dict[str, Any]:
        session = require_session(request, "designer")
        require_json_request(request)
        _items, job = find_job(job_id, session)
        definition = ctx.require_report(session, job["reportId"], permissions.RUN, folder=job.get("folder") or None)
        return {"run": runner.enqueue(job["reportId"], job["format"], job["parameters"], session["username"], "manual", job, definition.folder)}

    @app.post("/api/server/reports/{report_id}/background")
    async def run_in_background(report_id: str, request: Request) -> dict[str, Any]:
        """Like Run, but the file is made in the background and kept under Runs (for big reports)."""
        session = require_session(request)
        require_json_request(request)
        access.require_server_reports(session)
        definition = ctx.require_report(session, report_id, permissions.RUN, folder=clean_folder(request.query_params.get("folder")))
        payload = await json_body(request)
        output_format = str(payload.get("format", "pdf")).lower()
        raw = payload.get("parameters") or {}
        if not isinstance(raw, dict):
            raise HTTPException(status_code=422, detail="parameters must be a JSON object")
        prompted = {spec.name for spec in definition.parameters if spec.prompt}
        values = {str(k): ",".join(map(str, v)) if isinstance(v, list) else str(v) for k, v in raw.items() if k in prompted and v not in (None, "")}
        try:
            if output_format not in (definition.editor_config or {}).get("formats", ["pdf", "xlsx", "csv"]):
                raise ValueError(f"The {output_format.upper()} format is not enabled for this report")
            validate_parameters(definition, list(values.items()))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        active = [r for r in store.load_runs() if r.get("owner") == session["username"] and r.get("status") in ("queued", "running")]
        if len(active) >= 5:
            raise HTTPException(status_code=429, detail="You already have 5 runs waiting; wait for them to finish")
        return {"run": runner.enqueue(report_id, output_format, values, session["username"], "background", None, definition.folder)}

    @app.get("/api/server/runs")
    def list_runs(request: Request, jobId: str = "", limit: int = 100) -> dict[str, Any]:  # noqa: N803 - query name
        session = require_session(request)
        runs = [r for r in store.load_runs() if visible(r, session) and (not jobId or r.get("jobId") == jobId)]
        return {"runs": runs[:max(1, min(limit, 500))]}

    @app.get("/api/server/runs/{run_id}")
    def get_run(run_id: str, request: Request) -> dict[str, Any]:
        return {"run": find_run(run_id, require_session(request))}

    @app.get("/api/server/runs/{run_id}/file")
    def run_file(run_id: str, request: Request) -> Response:
        run = find_run(run_id, require_session(request))
        on_disk = store.output_path(run)
        if on_disk is not None:                    # a big export: sent from disk in pieces, never read into memory
            stamp = re.sub(r"\D", "", str(run.get("finishedAt") or ""))[:14]
            return FileResponse(on_disk, media_type=MEDIA[run["format"]], filename=f"{run['reportId']}-{stamp}.{run['format']}")
        body = store.read_output(run)
        if body is None:
            raise HTTPException(status_code=404, detail="This run has no file (it failed, or the file was removed)")
        stamp = (run.get("finishedAt") or "")[:16].replace(":", "").replace("-", "").replace("T", "-")
        return Response(body, media_type=MEDIA[run["format"]],
                        headers={"Content-Disposition": f'attachment; filename="{run["reportId"]}-{stamp}.{run["format"]}"'})

    @app.delete("/api/server/runs/{run_id}")
    def delete_run(run_id: str, request: Request) -> dict[str, Any]:
        session = require_session(request)
        run = find_run(run_id, session)
        if run.get("status") in ("queued", "running"):
            raise HTTPException(status_code=409, detail="This run has not finished yet")
        store.delete_runs(lambda r: r.get("id") == run_id)
        return {"deleted": run_id}
