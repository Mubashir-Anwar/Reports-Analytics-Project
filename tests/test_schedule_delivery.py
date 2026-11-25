"""After a schedule runs, its file goes to SFTP, an API (HTTP POST) or a network folder; passwords stay private."""
import email
import email.policy
import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.jobs import delivery  # noqa: E402
from backend.app.security.secrets import _load_private_secrets  # noqa: E402
from test_workspace_and_server import ADMIN, client, login, setup_login  # noqa: E402,F401

RUN = {"id": "run-20261122080000-0a1b2c3d", "reportId": "R1", "title": "Published one", "format": "csv", "status": "success",
       "rowCount": 2, "finishedAt": "2026-11-22T08:00:05+00:00"}
DAILY = {"reportId": "R1", "format": "csv", "label": "Daily", "recurrence": {"type": "daily", "time": "08:00"}, "timezone": "UTC"}


def test_file_names_from_a_template():
    assert delivery.file_name("{reportId}_{date}.{format}", RUN) == "R1_2026-11-22.csv"
    assert delivery.file_name("Sales/{title} {time}", RUN) == "Sales_Published one 080005.csv"   # / is not a folder
    with pytest.raises(ValueError, match="file name"):
        delivery.clean_targets([{"type": "folder", "folder": "x", "fileName": "{nope}.csv"}], is_admin=True)


def test_targets_are_checked_and_secrets_kept_apart(monkeypatch, tmp_path):
    targets, secrets = delivery.clean_targets([
        {"type": "sftp", "host": "sftp.bank.com", "username": "reports", "password": "s3cret", "folder": "/in"},
        {"type": "http", "url": "https://93.184.216.34/upload", "headers": [{"name": "Authorization", "value": "Bearer abc"}]},
    ])
    assert targets[0]["port"] == 22 and "password" not in targets[0] and secrets[targets[0]["id"]] == {"password": "s3cret"}
    assert targets[1]["headers"] == [{"name": "Authorization"}] and secrets[targets[1]["id"]] == {"header:Authorization": "Bearer abc"}
    with pytest.raises(ValueError, match="internal address"):
        delivery.clean_targets([{"type": "http", "url": "http://127.0.0.1:9/x"}])
    with pytest.raises(ValueError, match="administrators"):
        delivery.clean_targets([{"type": "folder", "folder": str(tmp_path)}])
    monkeypatch.delenv("REPORTING_DELIVERY_FOLDERS", raising=False)
    with pytest.raises(ValueError, match="REPORTING_DELIVERY_FOLDERS"):
        delivery.clean_targets([{"type": "folder", "folder": str(tmp_path)}], is_admin=True)
    monkeypatch.setenv("REPORTING_DELIVERY_FOLDERS", str(tmp_path / "allowed"))
    with pytest.raises(ValueError, match="inside one of"):
        delivery.clean_targets([{"type": "folder", "folder": str(tmp_path / "other")}], is_admin=True)
    ok, _ = delivery.clean_targets([{"type": "folder", "folder": str(tmp_path / "allowed" / "daily")}], is_admin=True)
    assert ok[0]["folder"].endswith("daily")


def test_folder_and_api_delivery(monkeypatch, tmp_path):
    monkeypatch.setenv("REPORTING_DELIVERY_FOLDERS", str(tmp_path))
    monkeypatch.setenv("REPORTING_WEBHOOK_ALLOW_PRIVATE", "true")
    got = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server name
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            head = ("Content-Type: " + self.headers["Content-Type"] + "\r\n\r\n").encode()
            message = email.message_from_bytes(head + raw, policy=email.policy.HTTP)
            parts = {part.get_param("name", header="content-disposition"): part for part in message.iter_parts()}
            got.update(auth=self.headers["Authorization"], name=parts["file"].get_filename(), body=parts["file"].get_payload(decode=True),
                       run=json.loads(parts["run"].get_payload(decode=True)))
            self.send_response(201)
            self.end_headers()

        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        targets, secrets = delivery.clean_targets([
            {"type": "folder", "folder": str(tmp_path / "out"), "fileName": "{reportId}_{date}"},
            {"type": "http", "url": f"http://127.0.0.1:{server.server_port}/in", "headers": [{"name": "Authorization", "value": "Bearer abc"}]},
        ], is_admin=True)
        monkeypatch.setattr(delivery, "_secrets", lambda: {"job1": secrets})
        results = delivery.deliver_run({"id": "job1", "deliver": targets}, RUN, b"Name\na\nb\n")
    finally:
        server.shutdown()
    assert [r["ok"] for r in results] == [True, True], results
    assert (tmp_path / "out" / "R1_2026-11-22.csv").read_bytes() == b"Name\na\nb\n"
    assert got["auth"] == "Bearer abc" and got["name"] == "R1_2026-11-22.csv" and got["body"] == b"Name\na\nb\n"
    assert got["run"]["rowCount"] == 2 and "HTTP 201" in results[1]["result"]
    # a failed report sends nothing
    skipped = delivery.deliver_run({"id": "job1", "deliver": targets}, {**RUN, "status": "failed"}, None)
    assert all(r["skipped"] for r in skipped)


class FakeSftp:
    def __init__(self):
        self.files = {}

    def putfo(self, stream, path, file_size=0, confirm=True):
        self.files[path] = stream.read()

    def posix_rename(self, old, new):
        self.files[new] = self.files.pop(old)

    def listdir(self, path):
        return []

    def close(self):
        pass


class FakeTransport:
    def close(self):
        pass


def test_sftp_upload_uses_a_temporary_name_and_remembers_the_host_key(monkeypatch):
    fake = FakeSftp()
    used = []
    monkeypatch.setattr(delivery, "_sftp_client", lambda target, secret: (used.append(secret) or FakeTransport(), fake, "SHA256:abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"))
    remembered = []
    monkeypatch.setattr(delivery, "remember_host_key", lambda job, target, key: remembered.append(key))
    targets, secrets = delivery.clean_targets([{"type": "sftp", "host": "sftp.bank.com", "username": "u", "password": "pw", "folder": "/in/daily"}])
    monkeypatch.setattr(delivery, "_secrets", lambda: {"job1": secrets})
    results = delivery.deliver_run({"id": "job1", "deliver": targets}, RUN, b"data")
    assert results[0]["ok"] and fake.files == {"/in/daily/R1_2026-11-22.csv": b"data"}
    assert used == [{"password": "pw"}] and remembered == ["SHA256:abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"]


def test_schedule_api_never_shows_the_password_and_delivers_after_the_run(client, monkeypatch, tmp_path):
    monkeypatch.setenv("REPORTING_DELIVERY_FOLDERS", str(tmp_path))
    runner = client.app.state.ctx.job_runner
    runner.inline = True
    assert setup_login(client).status_code == 200
    assert "folder" in client.get("/api/server/schedules/info").json()["delivery"]["types"]
    payload = {**DAILY, "deliver": [{"type": "sftp", "host": "sftp.bank.com", "username": "u", "password": "pw", "folder": "/in"},
                                    {"type": "folder", "folder": str(tmp_path / "share")}]}
    created = client.post("/api/server/schedules", json=payload)
    assert created.status_code == 200, created.text
    job = created.json()["schedule"]
    assert "pw" not in json.dumps(job) and job["deliver"][0]["hasPassword"]
    assert "pw" not in json.dumps(client.get("/api/server/schedules").json())
    assert _load_private_secrets()["delivery_secrets"][job["id"]][job["deliver"][0]["id"]] == {"password": "pw"}
    # saving again without the password keeps it; removing the target forgets it
    again = client.put(f"/api/server/schedules/{job['id']}", json={**DAILY, "deliver": job["deliver"]})
    assert again.status_code == 200 and again.json()["schedule"]["deliver"][0]["hasPassword"]

    monkeypatch.setattr(delivery, "_send_sftp", lambda target, secret, name, source: (f"uploaded {name} with {secret['password']}", "SHA256:x"))
    monkeypatch.setattr(delivery, "remember_host_key", lambda *_a: None)
    runs = runner.tick(datetime.now(timezone.utc) + timedelta(days=2))
    delivered = runs[0]["delivered"]
    assert [d["ok"] for d in delivered] == [True, True], delivered
    assert "with pw" in delivered[0]["result"] and list((tmp_path / "share").iterdir())

    only_folder = client.put(f"/api/server/schedules/{job['id']}", json={**DAILY, "deliver": [job["deliver"][1]]})
    assert only_folder.status_code == 200
    assert job["id"] not in _load_private_secrets().get("delivery_secrets", {})
    client.request("DELETE", f"/api/server/schedules/{job['id']}", json={})


def test_designers_cannot_choose_a_server_folder(client, monkeypatch, tmp_path):
    monkeypatch.setenv("REPORTING_DELIVERY_FOLDERS", str(tmp_path))
    assert client.post("/api/admin/users", headers={"X-Admin-Token": ADMIN},
                       json={"username": "dee", "email": "dee@example.com", "password": "Passw0rd-long-enough", "role": "designer"}).status_code in (200, 201)
    assert login(client, "dee", "Passw0rd-long-enough").status_code == 200
    refused = client.post("/api/server/schedules", json={**DAILY, "deliver": [{"type": "folder", "folder": str(tmp_path)}]})
    assert refused.status_code in (403, 422)
