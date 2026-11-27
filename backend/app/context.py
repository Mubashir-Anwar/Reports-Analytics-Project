"""Shared service state and helpers (formerly the top of make_app())."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from backend.app.config import Settings
from backend.app.constants import (
    PROJECT_ROOT,
    ROLE_TOKEN_MIN_LENGTH,
    SERVICE_VERSION,
)
from backend.app.definitions import discover_reports
from backend.app.jobs.runner import JobRunner
from backend.app.logs import LOG, configure_file_logging
from backend.app.security.access import enabled_areas
from backend.app.security.secrets import ensure_admin_token
from backend.app.services import bind_services
from backend.app.storage.files import (
    data_directory,
    initialize_data_directory,
)


def set_worker_threads() -> int:
    """How many requests can be inside a handler at once (REPORTING_WORKER_THREADS, default 200).

    Every handler runs in a worker thread (offload.ThreadedRoute). AnyIO's default is 40, and a report waiting
    for a place in the RunGate (up to 15 s) or a sign-in hashing its password holds one of them. Found by
    tools/sanity_test.py load: with 250+ users the 40 threads were all taken and even /ready waited 6-38 s in line.
    Most of those threads only wait, so more of them cost little; how much real work runs at once is still set by
    REPORT_MAX_CONCURRENT (reports) and the sign-in limit."""
    import anyio.to_thread  # noqa: PLC0415
    try:
        wanted = int(os.getenv("REPORTING_WORKER_THREADS", "200"))
    except ValueError:
        wanted = 200
    wanted = max(40, min(wanted, 2000))
    anyio.to_thread.current_default_thread_limiter().total_tokens = wanted
    return wanted


def create_context() -> SimpleNamespace:
    """Start-up work and the state shared by every endpoint: settings, the repository cache, connection
    clients, and the helper functions the routers call (each router unpacks the ones it needs)."""
    settings = Settings.from_env()
    initialize_data_directory()
    log_path = configure_file_logging()
    LOG.info("Reporting service %s starting; data directory %s; log file %s", SERVICE_VERSION, data_directory(), log_path or "off")
    ensure_admin_token()
    from backend.app.db.upgrade import upgrade_storage  # noqa: PLC0415
    upgrade_storage()          # older JSON documents (users, keys, permissions, versions, history) -> typed tables
    designer_token = os.getenv("REPORTING_DESIGNER_TOKEN", "").strip()
    if designer_token and len(designer_token) < ROLE_TOKEN_MIN_LENGTH:
        LOG.warning("REPORTING_DESIGNER_TOKEN is ignored: it must be at least %d characters", ROLE_TOKEN_MIN_LENGTH)
    LOG.info("Enabled areas: %s", ", ".join(sorted(enabled_areas())) or "none")
    reports_by_id = discover_reports()
    if os.getenv("REPORTING_ACCESS_MODE", "required").strip().lower() == "open":
        LOG.warning("REPORTING_ACCESS_MODE=open: /generate and /reports need no credentials; keep this service off public networks")
    elif not os.getenv("REPORTING_ACCESS_SECRET", "").strip():
        LOG.warning("REPORTING_ACCESS_SECRET is not set: /generate and /reports accept only the admin token")
    state: dict[str, Any] = {"clients": {}, "connection_signatures": {}}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        set_worker_threads()
        ctx.analytics_refresher.recover()  # imports cut off by a restart
        ctx.job_runner.start()  # scheduled reports and background runs (its loop also starts due dataset refreshes)
        yield
        ctx.job_runner.stop()
        ctx.analytics_refresher.stop()
        for client in state["clients"].values():
            client.close()

    areas = enabled_areas()
    docs_on = "docs" in areas
    ui_directory = PROJECT_ROOT / "ui"

    def react_page(name: str) -> HTMLResponse:
        """/designer, /server and /admin are pages of one React build (frontend/ -> ui/app)."""
        page = ui_directory / "app" / name
        if not page.exists():
            raise HTTPException(status_code=404, detail="The web app is not built. Run `npm install && npm run build` in frontend/")
        return HTMLResponse(page.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})

    ctx = SimpleNamespace(**{name: value for name, value in locals().items() if not name.startswith('__')})
    bind_services(ctx)  # the helpers in backend/app/services, with ctx filled in
    ctx.job_runner = JobRunner(ctx)
    from backend.app.analytics.refresher import Refresher  # noqa: PLC0415
    ctx.analytics_refresher = Refresher(ctx)
    from backend.app.limits import LiveSettings  # noqa: PLC0415
    from backend.app.services.capacity import RunGate  # noqa: PLC0415
    ctx.settings = LiveSettings(settings)        # Admin → Limits wins over the environment, without a restart
    ctx.run_gate = RunGate(settings.max_concurrent, settings.max_per_caller, settings.queue_wait_seconds, live=True)
    LOG.info("At most %d reports at once in this process (%d per user), waiting up to %d s for a place",
             settings.max_concurrent, settings.max_per_caller, settings.queue_wait_seconds)
    try:  # the repository revision we started with (see services/versions.refresh_if_changed)
        from backend.app.storage.store import get_store
        state["revision"] = (get_store().read_json("meta/repository-revision.json") or {}).get("revision")
    except (OSError, ValueError):
        pass
    return ctx
