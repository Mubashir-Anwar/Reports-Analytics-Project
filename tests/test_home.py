"""/api/home: each account sees its own parts only."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_access_rights import make_user, publish, sign_in  # noqa: E402
from test_workspace_and_server import client, setup_login  # noqa: E402,F401


def test_home_by_right(client):  # noqa: F811
    publish(client, "HR1", "HR")
    make_user(client, "dina", ["designer"])
    make_user(client, "vera", ["server"])
    client.cookies.clear()
    assert client.get("/api/home").status_code == 401
    setup_login(client)
    everything = client.get("/api/home").json()
    assert {"designer", "server", "query", "admin"} <= set(everything)
    assert everything["admin"]["users"] == 2                       # the temporary first admin does not count
    sign_in(client, "dina")
    mine = client.get("/api/home").json()
    assert "designer" in mine and not {"admin", "query", "analytics", "server"} & set(mine)
    sign_in(client, "vera")
    mine = client.get("/api/home").json()
    assert mine["server"]["reports"] >= 1 and not {"admin", "designer"} & set(mine)
    assert client.get("/home").status_code == 200
