"""Security checks: nothing is open without credentials, uploads cannot attack the XML parser, report pipelines
cannot read server internals, secrets are encrypted at rest, passwords use a strong hash, old outputs go away."""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import reporting_core as core  # noqa: E402
from backend.app.constants import LEGACY_PASSWORD_ITERATIONS, PASSWORD_ITERATIONS  # noqa: E402
from backend.app.jobs import store as job_store  # noqa: E402
from backend.app.jobs.runner import JobRunner  # noqa: E402
from backend.app.queries.mongo_query import _validate_read_only_pipeline  # noqa: E402
from backend.app.security import secrets as secret_store  # noqa: E402
from backend.app.security.users import _hash_password, load_users, save_users  # noqa: E402
from backend.app.storage.store import get_store  # noqa: E402
from test_workspace_and_server import ADMIN, client, login  # noqa: E402,F401

# pages are public HTML shells (their data comes from protected APIs); login/logout and probes are public by design
PUBLIC = {("GET", "/"), ("GET", "/ui"), ("GET", "/designer"), ("GET", "/server"), ("GET", "/admin"), ("GET", "/ready"),
          ("GET", "/health"), ("POST", "/api/server/logout"), ("GET", "/docs"), ("GET", "/redoc"),
          ("GET", "/openapi.json"), ("GET", "/docs/oauth2-redirect")}


def test_no_data_endpoint_answers_without_credentials(client):
    client.cookies.clear()
    opened = []
    for route in client.app.routes:
        path = getattr(route, "path", "")
        if path.startswith("/assets"):
            continue
        for method in (getattr(route, "methods", None) or set()) - {"HEAD", "OPTIONS"}:
            response = client.request(method, re.sub(r"\{[^}]+\}", "R1", path), json={})
            if response.status_code < 400 and (method, path) not in PUBLIC:
                opened.append((method, path, response.status_code))
    assert opened == []


@pytest.mark.parametrize("payload", [
    '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
    '<mongoReportDefinition version="1"><definition encoding="json">&b;</definition></mongoReportDefinition>',
    '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><jasperReport>&e;</jasperReport>',
])
def test_uploads_with_dtd_or_entities_are_refused(client, payload):
    assert login(client, "admin", ADMIN).status_code == 200
    response = client.post("/api/server/upload", json={"fileName": "x.mrd", "content": payload, "folder": "General"})
    assert response.status_code == 422 and re.search(r"DTD|Entit|DOCTYPE", response.json()["detail"]), response.text


@pytest.mark.parametrize("pipeline,message", [
    ([{"$currentOp": {}}], "server information"),
    ([{"$match": {}}, {"$indexStats": {}}], "server information"),
    ([{"$lookup": {"from": "system.users", "as": "u", "localField": "a", "foreignField": "b"}}], "system collections"),
    ([{"$lookup": {"from": {"db": "admin", "coll": "x"}, "as": "u", "pipeline": []}}], "same database"),
    ([{"$unionWith": "system.views"}], "system collections"),
    ([{"$unionWith": {"coll": "x", "db": "other"}}], "same database"),
    ([{"$merge": "x"}], "read-only"),
])
def test_pipelines_cannot_read_server_internals(pipeline, message):
    with pytest.raises(ValueError, match=message):
        _validate_read_only_pipeline(pipeline)
    _validate_read_only_pipeline([{"$lookup": {"from": "Orders", "as": "o", "localField": "a", "foreignField": "b"}}])


def test_secrets_are_encrypted_at_rest(client, monkeypatch):
    path = Path(core.data_directory()) / "private_secrets.json"
    assert "mongodb://main/a" in path.read_text()             # plain text without a key (the old behaviour)
    monkeypatch.setenv("REPORTING_SECRETS_KEY", "a-long-passphrase-for-the-tests-0123456789")
    assert secret_store._load_private_secrets()["admin_token"] == ADMIN   # first read with a key encrypts the file
    stored = json.loads(path.read_text())
    assert stored["format"] == "fernet-v1" and "mongodb://" not in path.read_text() and ADMIN not in path.read_text()
    saved = client.post("/api/admin/connections", headers={"X-Admin-Token": ADMIN},
                        json={"type": "postgresql", "name": "Secret PG", "uri": "postgresql://u:VerySecret-pw@db/x"})
    assert saved.status_code == 200 and "VerySecret-pw" not in path.read_text()
    assert secret_store._load_private_secrets()["mongo_connections"]["secret_pg"]["uri"].endswith("@db/x")
    monkeypatch.setenv("REPORTING_SECRETS_KEY", "another-wrong-passphrase-0123456789abcdef")
    with pytest.raises(RuntimeError, match="wrong key"):
        secret_store._load_private_secrets()
    monkeypatch.delenv("REPORTING_SECRETS_KEY")
    with pytest.raises(RuntimeError, match="REPORTING_SECRETS_KEY"):
        secret_store._load_private_secrets()


def test_old_password_hashes_are_upgraded_at_sign_in(client):
    salt, digest = _hash_password("Legacy-password-123", iterations=LEGACY_PASSWORD_ITERATIONS)
    save_users([{"username": "old", "role": "viewer", "salt": salt, "hash": digest, "sessionVersion": 0}])
    assert login(client, "old", "Legacy-password-123").status_code == 200
    user = load_users()[0]
    assert user["iterations"] == PASSWORD_ITERATIONS and user["hash"] != digest
    client.post("/api/server/logout", json={})
    assert login(client, "old", "Legacy-password-123").status_code == 200
    assert login(client, "old", "wrong").status_code == 401


def test_old_report_outputs_are_deleted(client, monkeypatch):
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(timespec="seconds")
    new = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for run_id, finished in (("run-20240101000000-aaaaaaaa", old), ("run-20990101000000-bbbbbbbb", new)):
        job_store.write_output(f"{run_id}.pdf", b"%PDF")
        job_store.save_run({"id": run_id, "status": "success", "finishedAt": finished, "file": f"{run_id}.pdf", "owner": "a"})
    assert JobRunner.remove_old_outputs() == 1
    assert [r["id"] for r in job_store.load_runs()] == ["run-20990101000000-bbbbbbbb"]
    assert get_store().keys("jobs/output") == ["jobs/output/run-20990101000000-bbbbbbbb.pdf"]


def test_docs_are_off_when_the_areas_say_so(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORTING_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPORTING_ADMIN_TOKEN", ADMIN)
    monkeypatch.setenv("REPORTING_ENABLED_AREAS", "runner,admin,designer,server")  # the Docker image default
    app = TestClient(core.make_app())
    assert app.get("/docs").status_code == 404 and app.get("/openapi.json").status_code == 404
    assert "reports" not in app.get("/ready").json()
