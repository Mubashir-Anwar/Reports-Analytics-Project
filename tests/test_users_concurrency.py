"""Many admins adding and changing accounts at the same moment must not lose anyone's rights.

Found by tools/sanity_test.py: 1000 bot users made 8 at a time, and 7 of them ended up with no rights. Every change
wrote the whole list of accounts back, so a change made from an older copy of the list undid the others. Now one
account is written at a time (people.save_user / delete_user) under one lock, and sign-in reads only its own account.
"""
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.db import people  # noqa: E402
from test_workspace_and_server import ADMIN, client  # noqa: E402,F401

HEADERS = {"X-Admin-Token": ADMIN}
PASSWORD = "Passw0rd-long-enough"


def test_accounts_made_at_the_same_time_keep_their_rights(client):
    kinds = [["designer", "server"], ["server"], ["analytics", "server"], ["query"]]

    def make(n):
        return client.post("/api/admin/users", headers=HEADERS, json={
            "username": f"crowd_{n:03d}", "email": f"crowd_{n:03d}@example.com", "password": PASSWORD, "rights": kinds[n % 4]}).status_code

    def change(n):        # other admins edit accounts while new ones are made
        return client.put(f"/api/admin/users/crowd_{n:03d}", headers=HEADERS, json={"fullName": f"Crowd {n}"}).status_code

    with ThreadPoolExecutor(max_workers=16) as pool:
        made = list(pool.map(make, range(60)))
        changed = list(pool.map(change, range(0, 60, 3)))
    assert made == [200] * 60 and changed == [200] * 20
    for n in range(60):
        user = people.find_user(f"crowd_{n:03d}")
        assert user is not None, n
        assert sorted(user["rights"]) == sorted(kinds[n % 4]), (n, user["rights"])


def test_the_same_name_twice_at_once_makes_one_account(client):
    def make(_):
        return client.post("/api/admin/users", headers=HEADERS, json={
            "username": "twin", "email": "twin@example.com", "password": PASSWORD, "rights": ["server"]}).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = sorted(pool.map(make, range(8)))
    assert codes.count(200) == 1 and codes.count(409) == 7
    assert sum(1 for user in people.load_users() if user["username"] == "twin") == 1


def test_one_account_is_written_without_touching_the_others(client):
    assert client.post("/api/admin/users", headers=HEADERS, json={
        "username": "solo_a", "email": "a@example.com", "password": PASSWORD, "rights": ["server"]}).status_code == 200
    stale = people.find_user("solo_a")
    assert client.post("/api/admin/users", headers=HEADERS, json={
        "username": "solo_b", "email": "b@example.com", "password": PASSWORD, "rights": ["analytics"]}).status_code == 200
    stale["fullName"] = "Changed from an old copy"
    people.save_user(stale)                       # solo_b was made after this copy was read: it must stay
    assert people.find_user("solo_b")["rights"] == ["analytics"]
    assert people.find_user("solo_a")["fullName"] == "Changed from an old copy"
    assert client.delete("/api/admin/users/solo_a", headers=HEADERS).status_code == 200
    assert people.find_user("solo_a") is None and people.find_user("solo_b") is not None
