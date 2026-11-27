"""A wrong sign-in for a name that does not exist does the same password work as one for a real account, so the
time of the answer does not tell which usernames exist (found by tools/sanity_test.py security)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.routers import server_portal  # noqa: E402
from test_workspace_and_server import ADMIN, client  # noqa: E402,F401


def test_unknown_names_cost_a_password_check(client, monkeypatch):
    spent = []
    monkeypatch.setattr(server_portal, "spend_password_check", lambda password: spent.append(password))
    assert client.post("/api/admin/users", headers={"X-Admin-Token": ADMIN}, json={
        "username": "timing_real", "email": "t@example.com", "password": "Passw0rd-long-enough", "rights": ["server"]}).status_code == 200
    unknown = client.post("/api/server/login", json={"username": "timing_nobody", "password": "wrong-password-1"})
    real = client.post("/api/server/login", json={"username": "timing_real", "password": "wrong-password-1"})
    assert unknown.status_code == real.status_code == 401
    assert spent == ["wrong-password-1"]                     # only the unknown name needed the stand-in check
    assert set(unknown.json()["detail"]) == set(real.json()["detail"])
