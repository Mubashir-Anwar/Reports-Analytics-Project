"""Every request is recorded in the system database and shown on Report Server → Logs (administrators only)."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_access_rights import make_user, sign_in  # noqa: E402
from test_workspace_and_server import client  # noqa: E402,F401
from backend.app.services import request_log  # noqa: E402


def test_requests_are_recorded_and_listed_for_admins(client):
    client.app.state.ctx.request_log.inline = True
    make_user(client, "rita", ["admin", "server"])
    make_user(client, "vera", ["server"])
    sign_in(client, "rita")
    client.get("/api/server/repository")
    client.get("/api/server/reports/NOPE?Branch=secret-value")            # 404, query value never stored
    client.get("/assets/brand/downloads.js")                                # static files are not recorded
    client.get("/api/deliveries/abc")                                       # the panel's checks are not recorded
    today = datetime.now(timezone.utc).date().isoformat()
    found = client.get("/api/server/logs/requests").json()
    assert found["day"] == today and found["days"][0] == today and found["recorder"]["enabled"]
    paths = [r["path"] for r in found["records"]]
    assert "/api/server/repository" in paths and "/api/server/reports/NOPE" in paths
    assert not any(p.startswith(("/assets/", "/api/deliveries")) for p in paths)
    nope = next(r for r in found["records"] if r["path"] == "/api/server/reports/NOPE")
    assert nope["status"] == 404 and nope["query"] == ["Branch"] and nope["actor"] == "rita" and nope["actorType"] == "user"
    assert "secret-value" not in str(found)
    errors = client.get("/api/server/logs/requests?status=errors").json()
    assert errors["records"] and all(r["status"] >= 400 for r in errors["records"]) and errors["totals"]["errors"] == len(errors["records"])
    assert client.get("/api/server/logs/requests?day=2020-1-1").status_code == 422
    for path in ("/api/server/logs/runs", "/api/server/logs/service", "/api/server/logs/audit", "/api/server/logs/downloads"):
        assert client.get(path).status_code == 200, path
    sign_in(client, "vera")
    assert client.get("/api/server/logs/requests").status_code == 403


def test_old_days_are_removed(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORTING_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPORTING_STORAGE", "files")
    monkeypatch.setenv("REPORTING_REQUEST_LOG_DAYS", "2")
    from backend.app.storage.store import get_store
    store = get_store()
    store.append_line(request_log.hour_key("2001-01-01", 3), '{"at": "x"}')
    old = (datetime.now(timezone.utc).date().toordinal() - 4)
    day = datetime.fromordinal(old).date().isoformat()
    store.append_line(request_log.hour_key(day, 5), '{"at": "x"}')
    assert request_log.RequestLog.cleanup() == 1
    assert list(store.lines(request_log.hour_key(day, 5))) == []
