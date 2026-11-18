"""Many users at once (report places), big exports written to disk, the product name, and the password recovery CLI."""
import csv
import dataclasses
import sys
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app import reset_password  # noqa: E402
from backend.app.rendering import spool  # noqa: E402
from backend.app.services.capacity import RunGate, caller_of  # noqa: E402
from test_access_rights import ADMIN_HEADERS, make_user, sign_in  # noqa: E402
from test_workspace_and_server import ADMIN, client, login  # noqa: E402,F401


# ---------------------------------------------------------------- report places
def test_a_caller_gets_its_share_then_429():
    gate = RunGate(limit=4, per_caller=1, wait_seconds=0)
    with gate.slot("user:ali"):
        with pytest.raises(HTTPException) as caught:
            with gate.slot("user:ali"):
                pass
        assert caught.value.status_code == 429 and caught.value.headers["Retry-After"]
        with gate.slot("user:sara"):           # somebody else still gets a place
            assert gate.stats()["running"] == 2
    stats = gate.stats()
    assert stats["running"] == 0 and stats["served"] == 2 and stats["rejectedPerCaller"] == 1 and stats["callers"] == 0


def test_a_key_may_use_half_of_the_places():
    gate = RunGate(limit=6, per_caller=2, wait_seconds=0)
    assert gate.share("user:ali") == 2 and gate.share("api-key:abc") == 3


def test_full_server_waits_then_503():
    gate = RunGate(limit=1, per_caller=1, wait_seconds=0.05)
    with gate.slot("user:ali"):
        with pytest.raises(HTTPException) as caught:
            with gate.slot("user:sara"):
                pass
        assert caught.value.status_code == 503
    assert gate.stats()["rejectedBusy"] == 1
    with gate.slot("user:sara"):                # free again
        pass


def test_a_waiting_request_gets_the_place_when_it_frees():
    gate = RunGate(limit=1, per_caller=1, wait_seconds=5)
    entered, release = threading.Event(), threading.Event()

    def first():
        with gate.slot("user:a"):
            entered.set()
            release.wait(5)

    worker = threading.Thread(target=first)
    worker.start()
    entered.wait(5)
    threading.Timer(0.1, release.set).start()
    with gate.slot("user:b"):
        assert gate.stats()["running"] == 1
    worker.join(5)
    assert gate.stats()["served"] == 2 and gate.stats()["peak"] == 1


def test_caller_names():
    assert caller_of("admin-token", user="ali") == "user:ali"
    assert caller_of("api-key:abc") == "api-key:abc"
    assert caller_of("access-token", address="10.0.0.5") == "access-token@10.0.0.5"


def test_generate_answers_503_when_every_place_is_taken(client):
    ctx = client.app.state.ctx
    ctx.run_gate = RunGate(limit=1, per_caller=1, wait_seconds=0)
    headers = {"X-Admin-Token": ADMIN}
    assert client.get("/generate/R1.csv", headers=headers).status_code == 200
    with ctx.run_gate.slot("user:somebody-else"):
        busy = client.get("/generate/R1.csv", headers=headers)
    assert busy.status_code == 503 and busy.headers["Retry-After"]
    assert client.get("/generate/R1.csv", headers=headers).status_code == 200


# ---------------------------------------------------------------- big exports
def test_spool_csv_and_xlsx(tmp_path):
    rows = ({"Name": f"=cmd{i}" if i == 0 else f"n{i}", "Amount": i} for i in range(5000))
    count = spool.write_csv(tmp_path / "out.csv", ["Name", "Amount"], {"Name": "Customer"}, rows)
    assert count == 5000 and not (tmp_path / "out.csv.part").exists()
    with (tmp_path / "out.csv").open(encoding="utf-8-sig") as handle:
        lines = list(csv.reader(handle))
    assert lines[0] == ["Customer", "Amount"] and len(lines) == 5001 and not lines[1][0].startswith("=")

    from openpyxl import load_workbook
    count = spool.write_xlsx(tmp_path / "out.xlsx", "Big: report?", ["Name"], {}, ({"Name": f"n{i}"} for i in range(300)))
    sheet = load_workbook(tmp_path / "out.xlsx", read_only=True).active
    assert count == 300 and sheet.title == "Big report" and sum(1 for _ in sheet.iter_rows()) == 301


def test_background_csv_over_the_row_limit_is_written_to_disk(client, monkeypatch, tmp_path):
    ctx = client.app.state.ctx
    monkeypatch.setenv("REPORTING_EXPORT_DIR", str(tmp_path / "exports"))
    ctx.settings = dataclasses.replace(ctx.settings, max_rows=10, export_max_rows=1000)
    produced = []

    def many(*_args, limit, **_kwargs):
        for index in range(250):
            produced.append(index)
            yield {"Name": f"row {index}"}

    def capped(*_args, max_rows, **_kwargs):           # the normal run finds more than REPORT_MAX_ROWS
        raise HTTPException(status_code=413, detail="too many")

    monkeypatch.setattr(ctx, "run_report_rows", capped)
    monkeypatch.setattr(ctx, "iter_report_rows", many)
    runner = ctx.job_runner
    runner.inline = True
    assert login(client, "admin", ADMIN).status_code == 200
    done = client.post("/api/server/reports/R1/background", json={"format": "csv", "parameters": {}})
    assert done.status_code == 200, done.text
    run = done.json()["run"]
    assert run["status"] == "success" and run["rowCount"] == 250
    files = list((tmp_path / "exports").iterdir())
    assert len(files) == 1 and files[0].stat().st_size > 0
    body = client.get(f"/api/server/runs/{run['id']}/file")
    assert body.status_code == 200 and body.text.count("\n") == 251
    assert client.request("DELETE", f"/api/server/runs/{run['id']}", json={}).status_code == 200
    assert list((tmp_path / "exports").iterdir()) == []      # the file goes with the run


# ---------------------------------------------------------------- name and capacity endpoints
def test_branding_default_rename_and_rights(client):
    assert client.get("/api/branding").json()["name"] == "Analysis"
    make_user(client, "adam", ["admin"])
    sign_in(client, "adam")
    assert client.put("/api/admin/branding", json={"name": "Acme Reports", "tagline": "x"}).status_code == 403
    make_user(client, "boss", ["admin"], super_admin=True)
    sign_in(client, "boss")
    assert client.put("/api/admin/branding", json={"name": "<script>", "tagline": ""}).status_code == 422
    saved = client.put("/api/admin/branding", json={"name": "Acme Reports", "tagline": "Numbers"})
    assert saved.status_code == 200 and saved.json()["name"] == "Acme Reports"
    assert client.get("/api/branding").json() == {**client.get("/api/branding").json(), "name": "Acme Reports", "tagline": "Numbers"}


def test_capacity_endpoint(client):
    data = client.get("/api/admin/capacity", headers=ADMIN_HEADERS).json()
    assert {"now", "background", "limits", "exportDirectory"} <= set(data)
    assert data["now"]["limit"] >= 1 and data["limits"]["exportMaxRows"] >= data["limits"]["maxRows"]
    assert client.get("/api/admin/capacity").status_code in (401, 403)


# ---------------------------------------------------------------- forgotten password
def test_reset_password_cli(client, capsys):
    make_user(client, "boss", ["admin"], super_admin=True)
    assert reset_password.main(["boss", "--generate"]) == 0
    printed = capsys.readouterr().out
    new_password = next(line.split(": ", 1)[1] for line in printed.splitlines() if "New password:" in line)
    client.post("/api/server/logout", json={})
    assert login(client, "boss", new_password).status_code == 200
    assert reset_password.main(["nobody", "--generate"]) != 0
