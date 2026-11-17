"""Public API v1 (for client websites): catalog, run, background jobs, webhooks, rate limit, OpenAPI."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.db import get_records  # noqa: E402
from backend.app.security import webhooks  # noqa: E402
from test_access_rights import ADMIN_HEADERS, publish  # noqa: E402
from test_workspace_and_server import client  # noqa: E402,F401


@pytest.fixture
def site(client):
    """A client website's key, limited to the General folder; one report outside it (HR)."""
    publish(client, "HR1", "HR")
    publish(client, "P1", "General", [{"name": "Branch", "type": "string", "required": True, "allowedValues": ["LHR", "KHI"]}])
    made = client.post("/api/admin/api-keys", headers=ADMIN_HEADERS, json={"name": "Portal", "folders": ["General"]}).json()
    client.app.state.ctx.job_runner.inline = True
    client.cookies.clear()
    client.key = made
    client.key_headers = {"X-API-Key": made["secret"]}
    return client


def test_whoami_and_catalog(site):
    me = site.get("/api/v1/whoami", headers=site.key_headers).json()
    assert me["type"] == "api-key" and me["name"] == "Portal" and me["folders"] == ["General"] and me["rateLimitPerMinute"] == 120
    reports = site.get("/api/v1/reports", headers=site.key_headers).json()["reports"]
    assert {r["id"] for r in reports} == {"R1", "P1"}                       # HR1 is outside the key's folders
    p1 = site.get("/api/v1/reports/P1", headers=site.key_headers).json()
    assert p1["parameters"] == [{"name": "Branch", "type": "string", "label": "Branch", "description": "", "required": True,
                                 "maxLength": 200, "allowedValues": ["LHR", "KHI"]}]
    assert p1["formats"] == ["csv", "pdf"] and p1["folder"] == "General"
    assert site.get("/api/v1/reports/HR1", headers=site.key_headers).status_code == 404
    assert site.get("/api/v1/reports").status_code == 401


def test_run_now(site):
    response = site.post("/api/v1/reports/P1/run", headers=site.key_headers, json={"format": "csv", "parameters": {"Branch": "LHR"}, "onBehalfOf": "ali"})
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    run = get_records().runs(reportId="P1")[0]
    assert run["channel"] == "api" and run["actor"] == "Portal" and run["onBehalfOf"] == "ali" and run["parameters"] == {"Branch": "LHR"}
    assert site.post("/api/v1/reports/P1/run", headers=site.key_headers, json={"format": "csv", "parameters": {"Branch": "ISB"}}).status_code == 422
    assert site.post("/api/v1/reports/P1/run", headers=site.key_headers, json={"format": "doc"}).status_code == 422
    assert site.post("/api/v1/reports/P1/run", headers=site.key_headers, json={"format": "csv", "parameters": {"Expires": "1"}}).status_code == 422
    assert site.post("/api/v1/reports/HR1/run", headers=site.key_headers, json={"format": "csv"}).status_code == 404


def test_background_job_and_download(site):
    started = site.post("/api/v1/reports/P1/jobs", headers=site.key_headers, json={"format": "csv", "parameters": {"Branch": "KHI"}})
    assert started.status_code == 202, started.text
    job = started.json()["job"]
    assert started.headers["location"] == f"/api/v1/jobs/{job['id']}"
    status = site.get(f"/api/v1/jobs/{job['id']}", headers=site.key_headers).json()["job"]
    assert status["status"] == "success" and status["rowCount"] == 2 and status["downloadUrl"] == f"/api/v1/jobs/{job['id']}/file"
    file = site.get(status["downloadUrl"], headers=site.key_headers)
    assert file.status_code == 200 and b"Name" in file.content
    assert get_records().runs(channel="api")[0]["actorType"] == "api-key"
    assert site.post("/api/v1/reports/P1/jobs", headers=site.key_headers, json={"parameters": {"Branch": "ISB"}}).status_code == 422
    other = site.post("/api/admin/api-keys", headers=ADMIN_HEADERS, json={"name": "Other"}).json()["secret"]
    assert site.get(f"/api/v1/jobs/{job['id']}", headers={"X-API-Key": other}).status_code == 404    # another key's job
    assert site.post("/api/v1/reports/P1/jobs", headers=ADMIN_HEADERS, json={"parameters": {"Branch": "KHI"}}).status_code == 403


def test_webhook_is_signed_and_sent_when_the_job_finishes(site, monkeypatch):
    monkeypatch.setenv("REPORTING_WEBHOOK_ALLOW_PRIVATE", "true")
    changed = site.put(f"/api/admin/api-keys/{site.key['key']['id']}", headers=ADMIN_HEADERS, json={"webhookUrl": "http://127.0.0.1:9/hook"})
    assert changed.status_code == 200 and changed.json()["webhookSecret"].startswith("whsec_")
    assert changed.json()["key"]["webhookSecretSet"] is True and "webhookSecret" not in changed.json()["key"]
    secret = changed.json()["webhookSecret"]
    sent = []
    monkeypatch.setattr(webhooks, "deliver_later", lambda url, key, event, payload, on_done=None: sent.append((url, key, event, payload)))
    job = site.post("/api/v1/reports/P1/jobs", headers=site.key_headers, json={"parameters": {"Branch": "LHR"}}).json()["job"]
    assert sent and sent[0][0] == "http://127.0.0.1:9/hook" and sent[0][1] == secret and sent[0][2] == "job.finished"
    assert sent[0][3]["job"]["id"] == job["id"] and sent[0][3]["job"]["status"] == "success"
    body = json.dumps({"event": "job.finished", **sent[0][3]}).encode()
    assert webhooks.signature(secret, "1700000000", body).startswith("sha256=")       # what the website recomputes


@pytest.mark.parametrize("url, reason", [("ftp://example.com/x", "https://"), ("http://127.0.0.1/x", "internal address"),
                                         ("http://user:pw@example.com/x", "no user name")])
def test_webhook_urls_are_checked(site, url, reason):
    response = site.put(f"/api/admin/api-keys/{site.key['key']['id']}", headers=ADMIN_HEADERS, json={"webhookUrl": url})
    assert response.status_code == 422 and reason in response.json()["detail"]


def test_rate_limit_per_key(site):
    assert site.put(f"/api/admin/api-keys/{site.key['key']['id']}", headers=ADMIN_HEADERS, json={"ratePerMinute": 2}).status_code == 200
    assert site.get("/api/v1/whoami", headers=site.key_headers).status_code == 200
    assert site.get("/api/v1/reports", headers=site.key_headers).status_code == 200
    limited = site.get("/api/v1/reports", headers=site.key_headers)
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) >= 1


def test_public_openapi(site):
    spec = site.get("/api/v1/openapi.json").json()
    assert "/api/v1/reports/{report_id}/run" in spec["paths"] and "/generate/{report_file}" in spec["paths"]
    assert not any(path.startswith("/api/admin") for path in spec["paths"])
    assert spec["components"]["securitySchemes"]["ApiKey"]["name"] == "X-API-Key"
