"""HTML pages: /, /ui, /admin, /designer, /server, /analytics.

Each page needs a signed-in user with the matching right. Signed out: the browser is sent to the /login
sign-in page, which comes back to the page afterwards (?next=). Signed in without the right: a short
"no access" page that links to the parts this user may open. The APIs behind the pages check the same rights.
"""
from __future__ import annotations

import html
from types import SimpleNamespace
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from backend.app.constants import BRAND_NAME, RIGHT_LABELS

# Which page each right opens, in the order the home page picks the first one.
APP_PAGES = (("designer", "/designer"), ("server", "/server"), ("analytics", "/analytics"), ("runner", "/ui"), ("admin", "/admin"))

_STYLE = """
:root{color-scheme:light dark;--bg:#f6f7fb;--card:#fff;--ink:#14161f;--muted:#5b6173;--line:#e3e6ef;--accent:#4f46e5}
@media (prefers-color-scheme:dark){:root{--bg:#10121a;--card:#181b26;--ink:#e9ebf2;--muted:#9aa1b5;--line:#2a2f3e;--accent:#8b85ff}}
body{margin:0;font:15px/1.5 system-ui,Segoe UI,sans-serif;background:var(--bg);color:var(--ink);display:grid;place-items:center;min-height:100vh;padding:16px;box-sizing:border-box}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:28px;max-width:440px;width:100%}
h1{font-size:20px;margin:0 0 6px}p{color:var(--muted);margin:0 0 16px}
a.app{display:block;padding:10px 12px;border:1px solid var(--line);border-radius:10px;margin-bottom:8px;color:var(--ink);text-decoration:none}
a.app:hover{border-color:var(--accent)}
.brand{display:flex;align-items:center;gap:10px;margin-bottom:18px;color:var(--ink);text-decoration:none;font-weight:800;font-size:16px}.brand b{font-weight:500;color:var(--accent);margin-left:3px}button{margin-top:8px;background:none;border:1px solid var(--line);color:var(--ink);border-radius:8px;padding:8px 12px;cursor:pointer}
"""


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)} · {BRAND_NAME}</title><link rel='icon' type='image/svg+xml' href='/assets/brand/favicon.svg'>"
        f"<style>{_STYLE}</style></head><body><main class='card'><a class='brand' href='/'><img src='/assets/brand/logo.svg' alt='' width='34' height='34'>"
        f"<span>Folio<b>Reports</b></span></a>{body}</main>"
        "<script src='/assets/signout.js' defer></script></body></html>",
        status_code=status, headers={"Cache-Control": "no-store"})


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    """Add the pages endpoints to the app."""
    react_page = ctx.react_page
    ui_directory = ctx.ui_directory
    current_session = ctx.current_session

    def no_access(session: dict, right: str) -> HTMLResponse:
        allowed = [(page_right, path) for page_right, path in APP_PAGES if page_right in session["rights"]]
        links = "".join(f"<a class='app' href='{path}'>{html.escape(RIGHT_LABELS[page_right])}</a>" for page_right, path in allowed)
        text = "Your account may open:" if allowed else "Your account has no rights yet. Ask an administrator."
        return _page("No access", f"<h1>No access to {html.escape(RIGHT_LABELS[right])}</h1>"
                     f"<p>You are signed in as <b>{html.escape(session['username'])}</b>. {text}</p>{links}"
                     "<button type='button' data-signout>Sign out and use another account</button>", status=403)

    def guarded(request: Request, right: str, page: Response | None = None) -> Response:
        session = current_session(request)
        if session is None:
            target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse(url=f"/login?next={quote(target, safe='')}", status_code=303)
        if right not in session["rights"]:
            return no_access(session, right)
        return page

    @app.get("/", include_in_schema=False)
    def home(request: Request) -> RedirectResponse:
        session = current_session(request)
        if session is None:
            return RedirectResponse(url="/login", status_code=303)
        first = next((path for right, path in APP_PAGES if right in session["rights"]), "/server")
        return RedirectResponse(url=first, status_code=303)

    @app.get("/ui", include_in_schema=False)
    def report_ui(request: Request) -> Response:
        page = HTMLResponse((ui_directory / "index.html").read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})
        if current_session(request) is None and (request.headers.get("X-Report-Token") or request.headers.get("X-API-Key")):
            # A website's server proxies the runner (e.g. Grizzly) and adds its key: the page opens for its visitors,
            # and every /reports and /generate call through that proxy is checked against the same key.
            try:
                ctx.require_report_access(request)
                return page
            except HTTPException:
                pass
        return guarded(request, "runner", page)

    @app.get("/admin", include_in_schema=False)
    def admin_ui(request: Request) -> Response:
        return guarded(request, "admin", react_page("admin.html"))

    @app.get("/designer", include_in_schema=False)
    def designer_ui(request: Request) -> Response:
        return guarded(request, "designer", react_page("index.html"))

    @app.get("/analytics", include_in_schema=False)
    def analytics_ui(request: Request) -> Response:
        return guarded(request, "analytics", _page("Analytics", "<h1>Analytics</h1><p>Dashboards, models and visuals are "
                                                   "being built. Your account already has the Analytics right, so it will "
                                                   "open here.</p><a class='app' href='/'>Back</a>"))

    @app.get("/login", include_in_schema=False)
    def login_page() -> Response:
        """Sign-in for every part of the product (also when the Report Server area is switched off)."""
        return react_page("server.html")

    @app.get("/server", include_in_schema=False)
    def server_ui(request: Request) -> Response:
        """Also the sign-in page, so it opens without a session; signed in without the right: no access."""
        session = current_session(request)
        if session is not None and "server" not in session["rights"] and not request.query_params.get("next"):
            return no_access(session, "server")
        return react_page("server.html")
