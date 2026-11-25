"""The setup command's temporary admin (24 hours), and no default data source."""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.security.users import find_user, load_users, needs_first_admin, save_users  # noqa: E402
from backend.app.setup import main, make_env_file, make_setup_admin  # noqa: E402
from test_workspace_and_server import ADMIN, client, login, setup_login, token  # noqa: E402,F401

PASSWORD = "Setup-pass-0123456789"


def test_the_setup_admin_signs_in_creates_an_admin_and_ends(client):  # noqa: F811
    assert "backend.app.setup" in login(client, "x", "y").json()["detail"]        # no accounts: how to make the first
    assert setup_login(client).status_code == 200
    me = client.get("/api/server/me").json()
    assert me["temporary"] and me["superAdmin"] and me["expiresAt"]
    assert needs_first_admin()
    made = client.post("/api/admin/users", json={"username": "boss", "email": "b@example.com", "password": "boss-pass-1",
                                                 "rights": [], "superAdmin": True})
    assert made.status_code == 200 and not needs_first_admin()
    assert made.json()["removedSetupAdmins"] == ["setup_admin"]                    # at once, not after 24 hours
    assert client.get("/api/server/me").status_code == 401 and find_user("setup_admin") is None
    assert login(client, "setup_admin", PASSWORD).status_code == 401
    make_setup_admin("setup_admin", PASSWORD, 24, force=True)                      # a lost password: it can be made again
    assert login(client, "setup_admin", PASSWORD).status_code == 200
    assert client.post("/api/admin/users", json={"username": "ann", "email": "a@example.com", "password": "ann-pass-1",
                                                 "rights": ["server"]}).json()["removedSetupAdmins"] == []   # not an admin
    assert client.put("/api/admin/users/setup_admin", json={"superAdmin": True}).status_code == 409
    # the temporary one does not count as "an active super admin is left"
    assert client.request("DELETE", "/api/admin/users/boss", json={}).status_code == 409
    over = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    save_users([{**u, "expiresAt": over} if u["username"] == "setup_admin" else u for u in load_users()])
    assert client.get("/api/server/me").status_code == 401                         # the open session ends too
    assert login(client, "setup_admin", PASSWORD).status_code == 403
    with pytest.raises(ValueError):
        make_setup_admin("setup_admin", PASSWORD, 24)                              # a permanent super admin exists
    make_setup_admin("setup_admin", PASSWORD, 24, force=True)
    assert login(client, "setup_admin", PASSWORD).status_code == 200


def test_the_command_prints_a_password_that_works(client, capsys):  # noqa: F811
    assert main([]) == 0
    out = capsys.readouterr().out
    password = next(line.split(":", 1)[1].split()[0] for line in out.splitlines() if "Password" in line)
    assert login(client, "setup_admin", password).status_code == 200
    assert main(["--check"]) == 0
    with pytest.raises(ValueError):
        save_users([*load_users(), {"username": "ali", "rights": ["server"]}])
        make_setup_admin("ali", PASSWORD, 24)                                      # never turns a normal account into one
    assert not find_user("ali").get("temporary")


def test_env_file_is_made_once(tmp_path, monkeypatch):
    for name in ("REPORTING_SKIP_ENV_FILE", "REPORTING_SECRETS_KEY", "REPORTING_SESSION_SECRET", "REPORTING_STORAGE_URL"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / ".env"
    assert make_env_file(path)
    text = path.read_text(encoding="utf-8")
    assert "REPORTING_SECRETS_KEY=" in text and "MONGODB" not in text
    assert not make_env_file(path) and path.read_text(encoding="utf-8") == text


def test_no_default_data_source(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import service_names as core
    monkeypatch.setenv("REPORTING_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPORTING_ADMIN_TOKEN", ADMIN)
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.delenv("MONGODB_DATABASE", raising=False)
    fresh = TestClient(core.make_app())
    assert fresh.get("/api/data-sources", headers=token(ADMIN)).json()["connections"] == []
    assert fresh.get("/health").json()["status"] == "ok"
    # an older installation with MONGODB_URI keeps its default one until it is deleted
    monkeypatch.setenv("MONGODB_URI", "mongodb://main/a")
    monkeypatch.setenv("MONGODB_DATABASE", "a")
    assert [c["id"] for c in fresh.get("/api/data-sources", headers=token(ADMIN)).json()["connections"]] == ["default"]
    assert fresh.delete("/api/data-sources/default", headers=token(ADMIN)).status_code == 200
    assert fresh.get("/api/data-sources", headers=token(ADMIN)).json()["connections"] == []
    assert fresh.delete("/api/data-sources/default", headers=token(ADMIN)).status_code == 404
    assert os.getenv("MONGODB_URI")


def test_no_env_example_in_the_repository():
    root = Path(__file__).resolve().parent.parent
    assert not (root / ".env.example").exists() and not (root / "deploy" / ".env.example").exists()
