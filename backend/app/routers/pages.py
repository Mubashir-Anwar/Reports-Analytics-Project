"""HTML pages: /, /ui, /admin, /designer, /server."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    """Add the pages endpoints to the app."""
    react_page = ctx.react_page
    ui_directory = ctx.ui_directory

    @app.get("/", include_in_schema=False)
    def home() -> RedirectResponse:
        return RedirectResponse(url="/ui")

    @app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
    def report_ui() -> HTMLResponse:
        return HTMLResponse((ui_directory / "index.html").read_text(encoding="utf-8"))

    @app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
    def admin_ui() -> HTMLResponse:
        return react_page("admin.html")

    @app.get("/designer", response_class=HTMLResponse, include_in_schema=False)
    def designer_ui() -> HTMLResponse:
        return react_page("index.html")

    @app.get("/server", response_class=HTMLResponse, include_in_schema=False)
    def server_ui() -> HTMLResponse:
        return react_page("server.html")
