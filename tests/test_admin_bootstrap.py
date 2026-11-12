"""/api/admin/bootstrap must never hand out the admin token through a reverse proxy."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import reporting_core as core  # noqa: E402

TOKEN = "bootstrap-test-token"


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORTING_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPORTING_ADMIN_TOKEN", TOKEN)
    app = core.make_app()

    def factory(client_ip="127.0.0.1", base_url="http://127.0.0.1:8000"):
        return TestClient(app, base_url=base_url, client=(client_ip, 50123))

    return factory


def _bootstrap(client, **headers):
    return client.get("/api/admin/bootstrap", headers=headers)


def test_disabled_by_default(make_client, monkeypatch):
    monkeypatch.delenv("REPORTING_LOCAL_BOOTSTRAP", raising=False)
    with make_client() as client:
        response = _bootstrap(client)
    assert response.status_code == 404
    assert TOKEN not in response.text


@pytest.mark.parametrize("value", ["false", "0", "yes", ""])
def test_only_exact_true_enables(make_client, monkeypatch, value):
    monkeypatch.setenv("REPORTING_LOCAL_BOOTSTRAP", value)
    with make_client() as client:
        assert _bootstrap(client).status_code == 404


def test_enabled_local_browser_gets_token(make_client, monkeypatch):
    monkeypatch.setenv("REPORTING_LOCAL_BOOTSTRAP", "true")
    for base_url in ("http://127.0.0.1:8000", "http://localhost:8000"):
        with make_client(base_url=base_url) as client:
            response = _bootstrap(client)
        assert response.status_code == 200
        assert response.json() == {"token": TOKEN}
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("header", [
    {"X-Forwarded-For": "203.0.113.9"},
    {"Forwarded": "for=203.0.113.9"},
    {"X-Real-IP": "203.0.113.9"},
    {"X-Forwarded-Host": "reports.example.com"},
    {"X-Forwarded-Proto": "https"},
    {"Via": "1.1 nginx"},
])
def test_enabled_but_proxied_request_is_refused(make_client, monkeypatch, header):
    monkeypatch.setenv("REPORTING_LOCAL_BOOTSTRAP", "true")
    with make_client() as client:
        response = _bootstrap(client, **header)
    assert response.status_code == 404
    assert TOKEN not in response.text


def test_enabled_remote_client_or_foreign_host_is_refused(make_client, monkeypatch):
    monkeypatch.setenv("REPORTING_LOCAL_BOOTSTRAP", "true")
    with make_client(client_ip="203.0.113.9") as client:
        assert _bootstrap(client).status_code == 404
    with make_client() as client:
        assert _bootstrap(client, Host="reports.example.com").status_code == 404


def test_default_nginx_proxy_scenario_no_longer_leaks(make_client, monkeypatch):
    # nginx `proxy_pass http://127.0.0.1:8000;` sends Host: 127.0.0.1:8000 and no X-Forwarded-For.
    # With the default setting this must not return the token.
    monkeypatch.delenv("REPORTING_LOCAL_BOOTSTRAP", raising=False)
    with make_client() as client:
        for host in ("127.0.0.1:8000", "localhost"):
            response = _bootstrap(client, Host=host)
            assert response.status_code == 404
            assert TOKEN not in response.text


def test_admin_api_still_works_with_typed_token(make_client, monkeypatch):
    monkeypatch.delenv("REPORTING_LOCAL_BOOTSTRAP", raising=False)
    with make_client() as client:
        assert client.get("/api/admin/reports", headers={"X-Admin-Token": TOKEN}).status_code == 200
