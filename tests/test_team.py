"""One browser per account, who is working now, and the team chat."""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import service_names as core  # noqa: E402
from backend.app.security import shared_state  # noqa: E402

ADMIN = "admin-token-for-tests-0123456789"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORTING_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPORTING_ADMIN_TOKEN", ADMIN)
    for key in ("REPORTING_ENABLED_AREAS", "REPORTING_ADMIN_ALLOWED_IPS", "REPORTING_SESSION_SECRET", "REPORTING_SINGLE_SESSION",
                "REPORTING_SINGLE_SESSION_GRACE"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / "private_secrets.json").write_text(json.dumps({"admin_token": ADMIN}), encoding="utf-8")
    made = core.make_app()
    admin = {"X-Admin-Token": ADMIN}
    with TestClient(made) as setup:
        for name in ("boss", "ali", "sara"):
            assert setup.post("/api/admin/users", headers=admin,
                              json={"username": name, "email": f"{name}@example.com", "password": f"{name}-pass-1", "role": "admin" if name == "boss" else "designer"}).status_code == 200
    return made


def browser(app, name, agent="Mozilla/5.0 (Windows NT 10.0) Chrome/120.0"):
    client = TestClient(app, headers={"User-Agent": agent})
    answer = client.post("/api/server/login", json={"username": name, "password": f"{name}-pass-1"})
    assert answer.status_code == 200, answer.text
    return client, answer.json()


def test_signing_in_on_another_browser_signs_the_first_one_out_after_ten_seconds(app, monkeypatch):
    edge, first = browser(app, "ali", "Mozilla/5.0 (Windows NT 10.0) Chrome/120.0 Edg/120.0")
    assert first["replacedOther"] is None
    assert edge.get("/api/server/me").json()["replaced"] is None

    firefox, second = browser(app, "ali", "Mozilla/5.0 (Windows NT 10.0; rv:120.0) Gecko Firefox/120.0")
    assert second["replacedOther"]["by"].startswith("Edge on Windows") and second["replacedOther"]["seconds"] == 10

    notice = edge.get("/api/server/me").json()["replaced"]          # the old browser is told, and still works for now
    assert notice["by"].startswith("Firefox on Windows") and 0 < notice["secondsLeft"] <= 10
    beat = edge.post("/api/team/heartbeat", json={"tab": "t1", "app": "designer"}).json()
    assert beat["session"]["replaced"]["by"].startswith("Firefox")
    assert firefox.get("/api/server/me").json()["replaced"] is None

    later = shared_state._now() + 11
    monkeypatch.setattr(shared_state, "_now", lambda: later)
    assert edge.get("/api/server/me").status_code == 401             # 10 seconds later: signed out
    assert firefox.get("/api/server/me").status_code == 200

    # another account is not affected, and signing out frees the account
    sara, _ = browser(app, "sara")
    assert sara.get("/api/server/me").status_code == 200
    firefox.post("/api/server/logout")
    _, again = browser(app, "ali")
    assert again["replacedOther"] is None


def test_several_browsers_can_be_allowed(app, monkeypatch):
    monkeypatch.setenv("REPORTING_SINGLE_SESSION", "false")
    one, _ = browser(app, "ali")
    two, answer = browser(app, "ali")
    assert answer["replacedOther"] is None
    assert one.get("/api/server/me").json()["replaced"] is None and two.get("/api/server/me").status_code == 200


def test_presence_shows_who_works_where(app):
    ali, _ = browser(app, "ali")
    sara, _ = browser(app, "sara")
    assert TestClient(app).post("/api/team/heartbeat", json={"tab": "x"}).status_code == 401
    assert ali.post("/api/team/heartbeat", json={"tab": "a1", "app": "designer", "place": "SALES", "title": "Daily sales"}).status_code == 200
    seen = sara.post("/api/team/heartbeat", json={"tab": "s1", "app": "designer", "place": "SALES", "title": "Daily sales"}).json()
    assert seen["me"] == "sara"
    assert seen["people"] == [{"username": "ali", "places": [{"app": "designer", "place": "SALES", "title": "Daily sales",
                                                             "since": seen["people"][0]["places"][0]["since"]}]}]
    ali.request("DELETE", "/api/team/heartbeat?tab=a1")
    assert sara.post("/api/team/heartbeat", json={"tab": "s1", "app": "analytics"}).json()["people"] == []


def test_chat_to_everyone_and_direct_messages_are_private(app):
    ali, _ = browser(app, "ali")
    sara, _ = browser(app, "sara")
    boss, _ = browser(app, "boss")
    assert ali.post("/api/team/messages", json={"text": "  "}).status_code == 422
    assert ali.post("/api/team/messages", json={"text": "hi", "to": "nobody"}).status_code == 422
    assert ali.post("/api/team/messages", json={"text": "hi", "to": "ali"}).status_code == 422
    hello = ali.post("/api/team/messages", json={"text": "Hello team", "place": "Daily sales"}).json()
    secret = ali.post("/api/team/messages", json={"text": "Sara, I am editing SALES", "to": "sara"}).json()
    assert secret["seq"] == hello["seq"] + 1

    assert [m["text"] for m in sara.get("/api/team/messages").json()["messages"]] == ["Hello team", "Sara, I am editing SALES"]
    assert [m["text"] for m in boss.get("/api/team/messages").json()["messages"]] == ["Hello team"]   # not the direct one
    assert [m["text"] for m in sara.get(f"/api/team/messages?after={hello['seq']}").json()["messages"]] == ["Sara, I am editing SALES"]
    assert sara.post("/api/team/heartbeat", json={"tab": "s"}).json()["lastMessage"] == secret["seq"]
    assert boss.post("/api/team/heartbeat", json={"tab": "b"}).json()["lastMessage"] == hello["seq"]
    assert set(ali.get("/api/team/users").json()["users"]) == {"boss", "sara"}
