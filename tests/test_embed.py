"""Embedding: tokens, the viewer page and who may frame it, fixed parameters, the viewer's API, the run log."""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.db import get_records  # noqa: E402
from backend.app.security import embed_tokens  # noqa: E402
from test_access_rights import ADMIN_HEADERS  # noqa: E402
from test_public_api import site  # noqa: E402,F401
from test_workspace_and_server import client  # noqa: E402,F401

PORTAL = "https://portal.example.com"


@pytest.fixture
def embed(site):
    assert site.put(f"/api/admin/api-keys/{site.key['key']['id']}", headers=ADMIN_HEADERS,
                    json={"embedOrigins": [PORTAL + "/", "http://localhost:3000"]}).status_code == 200
    issued = site.post("/api/v1/embed-tokens", headers=site.key_headers,
                       json={"report": "P1", "parameters": {"Branch": "LHR"}, "onBehalfOf": "ali", "expiresInSeconds": 600})
    assert issued.status_code == 200, issued.text
    site.token = issued.json()["token"]
    site.viewer = {"X-Embed-Token": site.token}
    return site


def test_a_token_needs_listed_websites_and_a_known_report(site):
    refused = site.post("/api/v1/embed-tokens", headers=site.key_headers, json={"report": "P1"})
    assert refused.status_code == 422 and "Embedding" in refused.json()["detail"]
    site.put(f"/api/admin/api-keys/{site.key['key']['id']}", headers=ADMIN_HEADERS, json={"embedOrigins": [PORTAL]})
    assert site.post("/api/v1/embed-tokens", headers=site.key_headers, json={"report": "HR1"}).status_code == 404
    assert site.post("/api/v1/embed-tokens", headers=site.key_headers, json={"report": "P1", "parameters": {"Nope": 1}}).status_code == 422
    assert site.post("/api/v1/embed-tokens", headers=ADMIN_HEADERS, json={"report": "P1"}).status_code == 403
    bad = site.put(f"/api/admin/api-keys/{site.key['key']['id']}", headers=ADMIN_HEADERS, json={"embedOrigins": ["https://portal.example.com/path"]})
    assert bad.status_code == 422


def test_the_viewer_may_only_be_framed_by_the_listed_websites(embed):
    page = embed.get(f"/embed/view?token={embed.token}")
    assert page.status_code == 200 and "/assets/embed/viewer.js" in page.text
    policy = page.headers["content-security-policy"]
    assert f"frame-ancestors http://localhost:3000 {PORTAL};" in policy + ";" or f"frame-ancestors http://localhost:3000 {PORTAL}" in policy
    assert "x-frame-options" not in page.headers                       # it would refuse every other website
    assert embed.get("/login").headers["x-frame-options"] == "SAMEORIGIN"   # every other page stays unframeable
    changed = embed.token[:-1] + ("0" if embed.token[-1] != "0" else "1")
    refused = embed.get(f"/embed/view?token={changed}")
    assert refused.status_code == 401 and "frame-ancestors 'none'" in refused.headers["content-security-policy"]
    assert embed.get("/assets/embed/sdk.js").status_code == 200


def test_fixed_parameters_are_locked(embed):
    info = embed.get("/api/embed/report", headers=embed.viewer).json()
    branch = next(p for p in info["parameters"] if p["name"] == "Branch")
    assert branch["locked"] is True and branch["value"] == "LHR" and info["formats"] == ["csv", "pdf"]
    preview = embed.post("/api/embed/preview", headers=embed.viewer, json={"parameters": {"Branch": "KHI"}})   # tries to change it
    assert preview.status_code == 200 and preview.json()["rowCount"] == 2
    run = get_records().runs(channel="embed")[0]
    assert run["parameters"] == {"Branch": "LHR"} and run["actor"] == "Portal" and run["onBehalfOf"] == "ali"


def test_download_from_the_viewer(embed):
    file = embed.post("/api/embed/run", headers=embed.viewer, json={"format": "csv"})
    assert file.status_code == 200 and file.headers["content-type"].startswith("text/csv")
    run = get_records().runs(channel="embed", reportId="P1")[0]
    assert run["format"] == "csv" and run["status"] == "success" and run["outputBytes"] == len(file.content)
    assert embed.post("/api/embed/run", headers=embed.viewer, json={"format": "doc"}).status_code == 422
    events = [event["action"] for event in get_records().events()]
    assert "embed.token_issued" in events


def test_tokens_stop_working(embed, monkeypatch):
    assert embed.get("/api/embed/report").status_code == 401                                        # no token
    with monkeypatch.context() as later:
        later.setattr(time, "time", lambda: 4_000_000_000)                                            # long after expiry
        assert embed.get("/api/embed/report", headers=embed.viewer).status_code == 401
    embed.put(f"/api/admin/api-keys/{embed.key['key']['id']}", headers=ADMIN_HEADERS, json={"folders": ["Elsewhere"]})
    assert embed.get("/api/embed/report", headers=embed.viewer).status_code == 404                   # folder no longer allowed
    embed.put(f"/api/admin/api-keys/{embed.key['key']['id']}", headers=ADMIN_HEADERS, json={"folders": ["General"], "disabled": True})
    assert embed.get("/api/embed/report", headers=embed.viewer).status_code == 401                   # key disabled


def test_token_format():
    with pytest.raises(ValueError):
        embed_tokens.read("emb_abc.def")
    with pytest.raises(ValueError):
        embed_tokens.clean_origins(["ftp://x.com"])
    assert embed_tokens.clean_origins(["https://a.com/", "https://a.com", "http://b.com:8080"]) == ["http://b.com:8080", "https://a.com"]
