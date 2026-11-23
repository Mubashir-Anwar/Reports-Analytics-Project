"""Scheduled reports and background runs (JasperReports Server: Schedule, Output, Notifications)."""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.jobs import mailer, schedule, store  # noqa: E402
from test_workspace_and_server import ADMIN, client, login  # noqa: E402,F401

UTC = timezone.utc


# ---------------------------------------------------------------- recurrence and relative dates
def test_next_run_daily_weekly_monthly_in_a_time_zone():
    after = datetime(2026, 11, 13, 4, 0, tzinfo=UTC)  # 09:00 in Karachi (UTC+5)
    daily = schedule.clean_recurrence({"type": "daily", "time": "08:00"})
    assert schedule.next_run(daily, "Asia/Karachi", after) == datetime(2026, 11, 14, 3, 0, tzinfo=UTC)
    assert schedule.next_run(daily, "UTC", after) == datetime(2026, 11, 13, 8, 0, tzinfo=UTC)
    weekly = schedule.clean_recurrence({"type": "weekly", "time": "07:30", "weekdays": [0, 4]})  # Mon, Fri
    assert schedule.next_run(weekly, "UTC", after) == datetime(2026, 11, 13, 7, 30, tzinfo=UTC)  # 13 Nov 2026 is a Friday
    assert schedule.next_run(weekly, "UTC", datetime(2026, 11, 13, 8, 0, tzinfo=UTC)) == datetime(2026, 11, 16, 7, 30, tzinfo=UTC)
    last = schedule.clean_recurrence({"type": "monthly", "time": "23:00", "day": "last"})
    assert schedule.next_run(last, "UTC", datetime(2027, 2, 10, tzinfo=UTC)) == datetime(2027, 2, 28, 23, 0, tzinfo=UTC)
    day31 = schedule.clean_recurrence({"type": "monthly", "time": "06:00", "day": 31})
    assert schedule.next_run(day31, "UTC", datetime(2026, 11, 1, tzinfo=UTC)) == datetime(2026, 11, 30, 6, 0, tzinfo=UTC)
    every = schedule.clean_recurrence({"type": "minutes", "every": 15})
    assert schedule.next_run(every, "UTC", after) == after + timedelta(minutes=15)
    once = schedule.clean_recurrence({"type": "once", "at": "2026-11-13T10:00"})
    assert schedule.next_run(once, "UTC", after) == datetime(2026, 11, 13, 10, 0, tzinfo=UTC)
    assert schedule.next_run(once, "UTC", datetime(2026, 11, 14, tzinfo=UTC)) is None


@pytest.mark.parametrize("bad", [
    {"type": "hourly"}, {"type": "minutes", "every": 1}, {"type": "daily", "time": "25:00"},
    {"type": "weekly", "time": "08:00", "weekdays": []}, {"type": "monthly", "time": "08:00", "day": 0},
    {"type": "once", "at": "tomorrow"},
])
def test_bad_recurrences_are_refused(bad):
    with pytest.raises(ValueError):
        schedule.clean_recurrence(bad)


def test_relative_dates():
    today = date(2026, 3, 15)
    r = schedule.resolve_relative
    assert r("{today}", today) == "2026-03-15"
    assert r("{today-1}", today) == r("{yesterday}", today) == "2026-03-14"
    assert r("{ monthStart }", today) == "2026-03-01" and r("{monthEnd}", today) == "2026-03-31"
    assert r("{prevMonthStart}", today) == "2026-02-01" and r("{prevMonthEnd}", today) == "2026-02-28"
    assert r("{weekStart}", today) == "2026-03-09" and r("{prevYearEnd}", today) == "2025-12-31"
    assert r("{today+7}", today) == "2026-03-22"
    assert r("KHI", today) == "KHI" and r("{unknown}", today) == "{unknown}"


def test_job_checks():
    base = {"reportId": "R1", "format": "pdf", "recurrence": {"type": "daily", "time": "08:00"}}
    job = schedule.clean_job({**base, "email": {"to": "a@x.com; b@y.org"}}, report_ids={"R1"}, allow_email=True)
    assert job["email"]["to"] == ["a@x.com", "b@y.org"] and job["timezone"] == "UTC" and job["keep"] == 10
    with pytest.raises(ValueError, match="published"):
        schedule.clean_job({**base, "reportId": "NOPE"}, report_ids={"R1"}, allow_email=True)
    with pytest.raises(ValueError, match="e-mail address"):
        schedule.clean_job({**base, "email": {"to": ["not-an-address"]}}, report_ids={"R1"}, allow_email=True)
    with pytest.raises(ValueError, match="designers"):
        schedule.clean_job({**base, "email": {"to": ["a@x.com"]}}, report_ids={"R1"}, allow_email=False)
    with pytest.raises(ValueError, match="time zone"):
        schedule.clean_job({**base, "timezone": "Mars/Base"}, report_ids={"R1"}, allow_email=True)


# ---------------------------------------------------------------- API, runner, files, e-mail
def make_user(client, username, role):
    admin = {"X-Admin-Token": ADMIN}
    assert client.post("/api/admin/users", headers=admin, json={"username": username, "email": f"{username}@example.com", "password": "Passw0rd-long-enough", "role": role}).status_code in (200, 201)


def test_schedule_runs_when_due_and_keeps_the_file(client, monkeypatch):
    runner = client.app.state.ctx.job_runner
    runner.inline = True
    sent = []
    monkeypatch.setattr(mailer, "configured", lambda: True)
    monkeypatch.setattr(mailer, "send", lambda to, subject, text, attachment=None: sent.append((to, subject, attachment[0] if attachment else None)))
    assert login(client, "admin", ADMIN).status_code == 200
    info = client.get("/api/server/schedules/info").json()
    assert info["canSchedule"] and info["emailConfigured"]

    payload = {"reportId": "R1", "format": "csv", "label": "Daily R1", "recurrence": {"type": "daily", "time": "08:00"},
               "timezone": "Asia/Karachi", "keep": 2, "email": {"to": ["boss@example.com"], "subject": "R1 daily"}}
    created = client.post("/api/server/schedules", json=payload)
    assert created.status_code == 200, created.text
    job = created.json()["schedule"]
    assert job["describe"] == "Every day at 08:00" and job["nextRunAt"] and job["owner"] == "admin"
    assert client.post("/api/server/schedules", json={**payload, "reportId": "MISSING"}).status_code == 422
    assert client.post("/api/server/schedules", json={**payload, "format": "docx"}).status_code == 422

    # the scheduler tick: nothing is due now, everything is due tomorrow
    assert runner.tick() == []
    runs = runner.tick(datetime.now(UTC) + timedelta(days=2))
    assert len(runs) == 1 and runs[0]["status"] == "success" and runs[0]["rowCount"] == 2, runs
    assert sent == [(["boss@example.com"], "R1 daily", "R1.csv")]
    listed = client.get("/api/server/schedules").json()["schedules"][0]
    assert listed["lastRun"]["status"] == "success" and listed["nextRunAt"] > job["nextRunAt"]
    file = client.get(f"/api/server/runs/{runs[0]['id']}/file")
    assert file.status_code == 200 and "a" in file.text and file.headers["content-type"].startswith("text/csv")

    # keep = 2: the third run removes the oldest file
    for _ in range(2):
        client.post(f"/api/server/schedules/{job['id']}/run", json={})
    all_runs = client.get(f"/api/server/runs?jobId={job['id']}").json()["runs"]
    assert len(all_runs) == 3 and [bool(r["file"]) for r in all_runs] == [True, True, False]
    assert client.get(f"/api/server/runs/{all_runs[-1]['id']}/file").status_code == 404
    assert len(store.output_keys()) == 2

    # pause, resume, delete with outputs
    assert client.post(f"/api/server/schedules/{job['id']}/enabled", json={"enabled": False}).json()["schedule"]["nextRunAt"] == ""
    assert runner.tick(datetime.now(UTC) + timedelta(days=9)) == []
    assert client.post(f"/api/server/schedules/{job['id']}/enabled", json={"enabled": True}).json()["schedule"]["enabled"]
    assert client.request("DELETE", f"/api/server/schedules/{job['id']}?outputs=true", json={}).json()["runsRemoved"] == 3
    assert store.output_keys() == []


def test_background_runs_and_who_sees_what(client, monkeypatch):
    runner = client.app.state.ctx.job_runner
    runner.inline = True
    make_user(client, "viewer1", "viewer")
    make_user(client, "viewer2", "viewer")
    assert login(client, "viewer1", "Passw0rd-long-enough").status_code == 200
    # viewers may run in the background, but not schedule
    assert client.post("/api/server/schedules", json={"reportId": "R1", "recurrence": {"type": "daily", "time": "08:00"}}).status_code == 403
    run = client.post("/api/server/reports/R1/background", json={"format": "pdf", "parameters": {}})
    assert run.status_code == 200 and run.json()["run"]["status"] == "success"
    run_id = run.json()["run"]["id"]
    assert client.get(f"/api/server/runs/{run_id}/file").content.startswith(b"%PDF")
    assert client.post("/api/server/reports/NOPE/background", json={}).status_code == 404
    # cookie writes must be JSON (no cross-site form posts)
    assert client.post("/api/server/reports/R1/background", data="format=pdf", headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 415

    client.post("/api/server/logout", json={})
    assert login(client, "viewer2", "Passw0rd-long-enough").status_code == 200
    assert client.get("/api/server/runs").json()["runs"] == []
    assert client.get(f"/api/server/runs/{run_id}/file").status_code == 404
    client.post("/api/server/logout", json={})
    assert login(client, "admin", ADMIN).status_code == 200
    assert [r["id"] for r in client.get("/api/server/runs").json()["runs"]] == [run_id]  # the admin sees all
    assert client.request("DELETE", f"/api/server/runs/{run_id}", json={}).status_code == 200


def test_failed_runs_and_restart_recovery(client, monkeypatch):
    runner = client.app.state.ctx.job_runner
    runner.inline = True
    assert login(client, "admin", ADMIN).status_code == 200

    def broken(*_a, **_k):
        raise RuntimeError("database down")
    monkeypatch.setattr(client.app.state.ctx, "run_report_rows", broken)
    failed = client.post("/api/server/reports/R1/background", json={"format": "csv"}).json()["run"]
    assert failed["status"] == "failed" and "database down" in failed["error"] and not failed["file"]

    store.save_run({**failed, "id": schedule.new_id("run"), "status": "running", "error": ""})
    runner._recover()
    assert all(r["status"] != "running" for r in store.load_runs())


def test_a_background_run_is_one_row_of_runs(client):
    """Schema v2: the job and its run log are the same row; its file is in run_files; deleting it keeps the log row."""
    from backend.app.db import entities as ent
    from backend.app.db import get_records
    runner = client.app.state.ctx.job_runner
    runner.inline = True
    assert login(client, "admin", ADMIN).status_code == 200
    run_id = client.post("/api/server/reports/R1/background", json={"format": "csv", "parameters": {}}).json()["run"]["id"]
    rows = [row for row in get_records().runs(reportId="R1") if row["startedAt"]]
    assert [row["id"] for row in rows] == [run_id]                               # one row, not two
    row = rows[0]
    assert row["runType"] == "background" and row["status"] == "success" and row["queuedAt"] and row["outputSha256"]
    assert row["channel"] == "server" and row["actor"] == "admin" and row["durationMs"] is not None
    stored = ent.get_entities().get(ent.RUN_FILES, run_id)
    assert stored["storage"] == "store" and stored["location"] == f"jobs/output/{run_id}.csv" and stored["sizeBytes"] > 0
    assert client.request("DELETE", f"/api/server/runs/{run_id}", json={}).status_code == 200
    assert client.get("/api/server/runs").json()["runs"] == [] and ent.get_entities().get(ent.RUN_FILES, run_id) is None
    assert get_records().run(run_id)["removedAt"]                               # who ran what stays in the log
