"""The system database behaves the same on every backend: files, SQLite, PostgreSQL, MySQL and MongoDB.

SQLite always runs. MongoDB runs when MONGO_TEST_URL is set (e.g. mongodb://127.0.0.1:27017), PostgreSQL with
PG_TEST_URL, MySQL with MYSQL_TEST_URL, SQL Server with MSSQL_TEST_URL and Oracle with ORACLE_TEST_URL (CI starts
them). Each test gets its own database or schema, or its tables are removed afterwards.
"""
import json
import os
import secrets
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import service_names as core  # noqa: E402
import test_storage  # noqa: E402
from backend.app import db as db_package  # noqa: E402
from backend.app.db import model  # noqa: E402
from backend.app.storage import store as store_module  # noqa: E402
from test_workspace_and_server import ADMIN, PUBLISHED, FakeMongoClient, login, token  # noqa: E402

MONGO_URL = os.getenv("MONGO_TEST_URL", "")
PG_URL = os.getenv("PG_TEST_URL", "")
MYSQL_URL = os.getenv("MYSQL_TEST_URL", "")
SHARED_SQL = {"mysql": MYSQL_URL, "sqlserver": os.getenv("MSSQL_TEST_URL", ""), "oracle": os.getenv("ORACLE_TEST_URL", "")}


def _forget():
    from backend.app.db import sql as sql_backend
    from backend.app.db import entities
    store_module._STORES.clear()
    db_package.reset_cache()
    entities.reset_cache()
    for engine in sql_backend._ENGINES.values():
        engine.dispose()
    sql_backend._ENGINES.clear()


@pytest.fixture(params=["files", "sqlite", "mongodb", "postgresql", "mysql", "sqlserver", "oracle"])
def backend(request, tmp_path, monkeypatch):
    """Point the service's storage at one backend; yields its name. Everything is removed afterwards."""
    kind = request.param
    monkeypatch.setenv("REPORTING_DATA_DIR", str(tmp_path / "data"))
    cleanup = None
    if kind == "files":
        monkeypatch.setenv("REPORTING_STORAGE", "files")
    elif kind == "sqlite":
        monkeypatch.setenv("REPORTING_STORAGE", "sql")
        monkeypatch.setenv("REPORTING_STORAGE_URL", f"sqlite:///{(tmp_path / 'system.db').as_posix()}")
    elif kind == "mongodb":
        if not MONGO_URL:
            pytest.skip("set MONGO_TEST_URL to test the MongoDB backend")
        name = f"rs_test_{secrets.token_hex(4)}"
        monkeypatch.setenv("REPORTING_STORAGE", "mongodb")
        monkeypatch.setenv("REPORTING_STORAGE_URL", f"{MONGO_URL.rstrip('/')}/{name}")

        def cleanup():
            from pymongo import MongoClient
            MongoClient(MONGO_URL).drop_database(name)
    elif kind == "postgresql":
        if not PG_URL:
            pytest.skip("set PG_TEST_URL to test PostgreSQL")
        import psycopg
        schema = f"rs_test_{secrets.token_hex(4)}"
        with psycopg.connect(PG_URL, autocommit=True) as connection:
            connection.execute(f"CREATE SCHEMA {schema}")
        separator = "&" if "?" in PG_URL else "?"
        monkeypatch.setenv("REPORTING_STORAGE", "sql")
        monkeypatch.setenv("REPORTING_STORAGE_URL", f"{PG_URL}{separator}options={quote(f'-csearch_path={schema}')}")

        def cleanup():
            with psycopg.connect(PG_URL, autocommit=True) as connection:
                connection.execute(f"DROP SCHEMA {schema} CASCADE")
    else:
        url = SHARED_SQL[kind]
        if not url:
            pytest.skip(f"set the {kind} test URL to test it")
        monkeypatch.setenv("REPORTING_STORAGE", "sql")
        monkeypatch.setenv("REPORTING_STORAGE_URL", url)

        def cleanup():                     # one shared test database: remove our tables
            from backend.app.db import sql as sql_backend
            import backend.app.storage.sql_store  # noqa: F401 - registers rs_documents / rs_lines
            sql_backend.metadata.drop_all(sql_backend.engine_for(url))
        cleanup()
    _forget()
    yield kind
    _forget()
    if cleanup:
        cleanup()


def records():
    return db_package.get_records()


# ------------------------------------------------------------------ document store
def test_document_store_contract(backend):
    test_storage.test_documents_folders_lines_and_files(store_module.get_store())


def test_large_files_are_kept_whole(backend):
    store = store_module.get_store()
    big = secrets.token_bytes(17 * 1024 * 1024) if backend == "mongodb" else secrets.token_bytes(300_000)  # MongoDB: GridFS
    store.write_bytes("jobs/output/big.pdf", big)
    assert store.read_bytes("jobs/output/big.pdf") == big
    store.write_bytes("jobs/output/big.pdf", b"small now")
    assert store.read_bytes("jobs/output/big.pdf") == b"small now"
    assert store.delete("jobs/output/big.pdf") and store.read_bytes("jobs/output/big.pdf") is None


# ------------------------------------------------------------------ report runs
def run(**extra):
    return {"startedAt": model.now(), "channel": "api", "actor": "site-a", "actorType": "api-key", "status": "success",
            "reportId": "R1", "format": "pdf", "rowCount": 3, **extra}


def test_runs_are_stored_and_filtered(backend):
    rec = records()
    assert rec.schema_version() == model.SCHEMA_VERSION
    base = datetime(2026, 1, 1, 9, 0, 0, 123000, tzinfo=timezone.utc)
    first = rec.add_run(run(startedAt=base, parameters={"Branch": "LHR", "Dates": ["2026-01-01"]}))
    rec.add_run(run(startedAt=base + timedelta(minutes=1), status="failed", errorCode="503", reportId="R2", actor="ali", actorType="user"))
    rec.add_run(run(startedAt=base + timedelta(minutes=2), channel="schedule", actor="ali", actorType="user"))
    newest = rec.runs()
    assert [r["startedAt"] for r in newest] == ["2026-01-01T09:02:00.123Z", "2026-01-01T09:01:00.123Z", "2026-01-01T09:00:00.123Z"]
    assert [r["reportId"] for r in rec.runs(status="failed")] == ["R2"]
    assert len(rec.runs(actor="ali")) == 2 and len(rec.runs(reportId="R1", channel="api")) == 1
    assert len(rec.runs(since=base + timedelta(minutes=1))) == 2 and len(rec.runs(until=base + timedelta(minutes=1))) == 1
    assert len(rec.runs(limit=1)) == 1
    stored = next(r for r in newest if r["id"] == first["id"])
    assert stored["parameters"] == {"Branch": "LHR", "Dates": ["2026-01-01"]} and stored["orgId"] == "default"
    assert stored["rowCount"] == 3 and stored["errorCode"] is None


def test_bad_runs_are_refused(backend):
    rec = records()
    with pytest.raises(ValueError):
        rec.add_run(run(channel="somewhere"))
    with pytest.raises(ValueError):
        rec.add_run(run(actor=""))
    with pytest.raises(ValueError):
        rec.runs(password="x")
    long = rec.add_run(run(errorMessage="x" * 2000, parameters={"$where": 1, "a.b": 2}))
    assert len(long["errorMessage"]) == 500 and long["parameters"] == {"where": 1, "a_b": 2}


# ------------------------------------------------------------------ audit events
def test_audit_chain(backend):
    rec = records()
    for index in range(5):
        rec.audit("user.changed", actor="boss", target=f"user{index}", details={"rights": {"to": ["designer"]}}, client_ip="10.0.0.1")
    events = rec.events()
    assert [e["seq"] for e in events] == [5, 4, 3, 2, 1]
    assert events[-1]["prevHash"] is None and events[0]["prevHash"] == events[1]["hash"]
    assert rec.events(target="user2")[0]["details"] == {"rights": {"to": ["designer"]}}
    assert rec.verify_chain() == {"ok": True, "events": 5, "problems": []}


def tamper(backend, seq, how):
    """Change or remove one stored event behind the service's back."""
    if backend == "files":
        path = Path(os.environ["REPORTING_DATA_DIR"]) / "records" / "audit-events.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        kept = []
        for line in lines:
            event = json.loads(line)
            if event["seq"] == seq:
                if how == "remove":
                    continue
                event["actor"] = "someone else"
            kept.append(json.dumps(event))
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    elif backend == "mongodb":
        events = records().events_collection
        if how == "remove":
            events.delete_one({"seq": seq})
        else:
            events.update_one({"seq": seq}, {"$set": {"actor": "someone else"}})
    else:
        from backend.app.db.sql import audit_events
        with records().engine.begin() as connection:
            if how == "remove":
                connection.execute(audit_events.delete().where(audit_events.c.seq == seq))
            else:
                connection.execute(audit_events.update().where(audit_events.c.seq == seq).values(actor="someone else"))


@pytest.mark.parametrize("how, problem", [("change", "event 2 was changed"), ("remove", "event 2 is missing")])
def test_a_changed_or_removed_event_is_detected(backend, how, problem):
    rec = records()
    for _ in range(4):
        rec.audit("auth.login", actor="ali")
    tamper(backend, 2, how)
    result = rec.verify_chain()
    assert not result["ok"] and any(problem in item for item in result["problems"])


def test_concurrent_audit_writes_keep_one_chain(backend):
    rec = records()
    errors = []

    def write(worker):
        try:
            for index in range(8):
                rec.audit("auth.login", actor=f"w{worker}", target=str(index))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert sorted(e["seq"] for e in rec.events(limit=100)) == list(range(1, 33))
    assert rec.verify_chain()["ok"]


def test_migrations_can_run_again(backend):
    rec = records()
    rec.audit("auth.login", actor="ali")
    rec.ensure_schema()
    rec.ensure_schema()
    assert rec.schema_version() == model.SCHEMA_VERSION and rec.verify_chain()["events"] == 1


def test_masking():
    assert model.mask("4220112345671") == "42*********71"
    assert model.mask("123") == "***" and model.mask(["12345"]) == ["12*45"]
    assert model.masked_parameters({"CNIC": "4220112345671", "Branch": "LHR"}, {"CNIC"}) == {"CNIC": "42*********71", "Branch": "LHR"}


# ------------------------------------------------------------------ the whole service on each backend
def test_whole_service_records_runs_and_audit(backend, monkeypatch):
    monkeypatch.setenv("REPORTING_ADMIN_TOKEN", ADMIN)
    store = store_module.get_store()
    store.write_json("connections.json", {"connections": [{"id": "default", "name": "Main", "storage": "file", "database": "a"}]})
    store.write_json("private_secrets.json", {"admin_token": ADMIN, "mongo_connections": {"default": {"uri": "mongodb://main/a", "database": "a"}}},
                     private=True)
    monkeypatch.setattr(core.mongodb, "MongoClient", FakeMongoClient)
    monkeypatch.setattr(core.mongodb, "_execute_report_pipeline", lambda *_a, **_k: [{"Name": "a"}, {"Name": "b"}])
    app = TestClient(core.make_app())
    draft = {**PUBLISHED, "id": "SYS1", "folder": "Sales",
             "parameters": [{"name": "CNIC", "type": "string", "sensitive": True}, {"name": "Branch", "type": "string"}]}
    assert app.put("/api/admin/workspace/SYS1?create=true", headers=token(ADMIN), json=draft).status_code == 200
    assert app.post("/api/admin/workspace/SYS1/publish", headers=token(ADMIN), json={}).status_code == 200
    response = app.get("/generate/SYS1.csv?CNIC=4220112345671&Branch=LHR", headers={"X-Admin-Token": ADMIN})
    assert response.status_code == 200
    assert login(app, "admin", ADMIN).status_code == 200
    assert app.get("/api/server/me").status_code == 200 and app.get("/admin").status_code == 200   # signed-in pages
    assert login(app, "admin", "wrong").status_code == 401
    assert app.post("/api/admin/users", headers=token(ADMIN), json={"username": "ali", "email": "ali@example.com", "password": "Passw0rd-long-enough", "rights": ["runner"]}).status_code == 200

    run_record = app.get("/api/admin/audit?reportId=SYS1", headers=token(ADMIN)).json()["records"][0]
    assert run_record["parameters"] == {"CNIC": "42*********71", "Branch": "LHR"}       # sensitive value masked
    assert run_record["rowCount"] == 2 and run_record["folder"] == "Sales" and run_record["outputBytes"] == len(response.content)
    events = app.get("/api/admin/audit-events", headers=token(ADMIN)).json()["events"]
    actions = [e["action"] for e in reversed(events)]
    assert actions == ["report.published", "auth.login", "auth.login_failed", "user.created"]
    assert events[0]["details"] == {"rights": ["runner"], "superAdmin": False} and events[0]["actor"] == "admin token"
    assert app.get("/api/admin/audit-events/verify", headers=token(ADMIN)).json()["ok"] is True
    assert app.get("/api/admin/audit-events", headers=token(ADMIN), params={"actor": "nobody"}).json()["events"] == []


def test_migrate_a_data_folder_into_this_backend(backend, tmp_path, monkeypatch):
    """A files installation (reports, users, run log, audit trail and the older run history) moved into a database."""
    if backend == "files":
        pytest.skip("the source is the files backend")
    import io

    from backend.app.storage import migrate as migrate_module
    url = os.environ["REPORTING_STORAGE_URL"]
    source_dir = tmp_path / "source"
    monkeypatch.setenv("REPORTING_DATA_DIR", str(source_dir))
    monkeypatch.setenv("REPORTING_STORAGE", "files")
    _forget()
    source = store_module.get_store()
    source.write_json("reports/General/OLD.json", {"id": "OLD", "title": "Old"})
    source.write_json("users.json", {"users": [{"username": "ali"}]}, private=True)
    source.make_dir("reports/Empty")
    source.append_line("audit/report-executions.jsonl", json.dumps({
        "requestedAt": "2025-11-09 18:11:39.809860+00:00", "access": "admin-token", "reportId": "OLD", "format": "xlsx",
        "status": "failed", "rowCount": 0, "errorCode": "503", "parameters": {"StartDate": "2025-01-09"}, "durationMs": 4}))
    source.append_line("audit/report-executions.jsonl", "not json")
    from backend.app.db import entities as ent
    ent.get_entities().put(ent.USERS, {"username": "boss", "superAdmin": True, "salt": "s", "hash": "h"})   # a typed row
    ent.get_entities().put(ent.JOB_SCHEDULES, {"id": "job-20260101000000-0123abcd", "reportId": "OLD", "format": "pdf",
                                               "recurrence": {"type": "daily", "at": "08:00"}, "owner": "boss"})
    files = db_package.get_records()
    files.add_run(run(reportId="OLD"))
    files.audit("auth.login", actor="ali")
    files.audit("user.created", actor="ali", target="sara")

    _, store, rec = migrate_module.target_for(url)
    counts = migrate_module.migrate(store, out=io.StringIO(), records=rec)
    assert counts["runs"] == 1 and counts["legacyRuns"] == 1 and counts["recordsSkipped"] == 1 and counts["events"] == 2
    assert store.read_json("reports/General/OLD.json")["id"] == "OLD" and "Empty" in store.dirs("reports")
    target_entities = migrate_module.entities_for(rec)
    boss = target_entities.find(ent.USERS_V2, {"username": "boss"})[0]          # copied as a step 2 row, with its rights
    assert counts["typedRows"] >= 5 and boss["isSuperAdmin"] is True
    assert {row["rightName"] for row in target_entities.find(ent.USER_RIGHTS, {"userId": boss["userId"]})} >= {"admin", "designer"}
    assert target_entities.get(ent.JOB_SCHEDULES, "job-20260101000000-0123abcd")["recurrence"] == {"type": "daily", "at": "08:00"}
    legacy = rec.runs(status="failed")[0]
    assert legacy["startedAt"] == "2025-11-09T18:11:39.809Z" and legacy["format"] == "xlsx" and legacy["actor"] == "admin-token"
    assert rec.verify_chain() == {"ok": True, "events": 2, "problems": []}     # the copied chain is still intact
    again = migrate_module.migrate(store, out=io.StringIO(), records=rec)
    assert again["runs"] == again["legacyRuns"] == again["events"] == 0


# ------------------------------------------------------------------ typed entities (accounts, keys, permissions, versions, history)
def test_entities_contract(backend):
    from backend.app.db import entities as ent
    store = ent.get_entities()
    user = {"username": "ali", "rights": ["designer", "server"], "superAdmin": False, "disabled": False, "salt": "ab", "hash": "cd",
            "iterations": 600000, "sessionVersion": 0, "createdAt": "2026-01-01T10:00:00.000Z", "futureField": {"x": 1}}
    store.put(ent.USERS, user)
    stored = store.get(ent.USERS, "ali")
    assert stored == user                                               # bool False, lists and unknown fields survive
    store.put(ent.USERS, {**user, "disabled": True, "sessionVersion": 3})
    assert store.get(ent.USERS, "ali")["disabled"] is True and len(store.find(ent.USERS)) == 1
    store.replace_all(ent.USERS, [{"username": "sara", "role": "viewer"}])
    assert [u["username"] for u in store.find(ent.USERS)] == ["sara"] and store.get(ent.USERS, "ali") is None
    assert store.get(ent.USERS, "sara") == {"username": "sara", "role": "viewer"}   # older account: no rights field invented

    definition = {"id": "R1", "querySource": '[{"$match": {"a.b": 1}}]', "pipeline": [{"$match": {"x.y": {"$gte": 2}}}]}
    for number in (1, 2, 3):
        store.put(ent.REPORT_VERSIONS, {"reportId": "R1", "version": number, "savedAt": model.now(), "definition": definition})
    assert store.get(ent.REPORT_VERSIONS, "R1", 2)["definition"] == definition    # "$" and "." keys kept exactly
    assert [v["version"] for v in store.find(ent.REPORT_VERSIONS, {"reportId": "R1"}, order=("version", True), limit=2)] == [3, 2]
    assert store.delete(ent.REPORT_VERSIONS, "R1", 1) and not store.delete(ent.REPORT_VERSIONS, "R1", 1)
    store.put(ent.FOLDER_PERMISSIONS, {"folder": "/", "users": {"ali": "none"}, "roles": {}})
    store.put(ent.FOLDER_PERMISSIONS, {"folder": "Finance", "users": {}, "roles": {"viewer": "run"}})
    assert store.delete_where(ent.FOLDER_PERMISSIONS, {"folder": "/"}) == 1
    with pytest.raises(ValueError):
        store.put(ent.USERS, {"username": ""})


def test_analytics_entities(backend):
    """Datasets, relationships, dashboards and import history keep nested JSON ("$" and "." keys) on every backend."""
    from backend.app.db import entities as ent
    store = ent.get_entities()
    dataset = {"id": "ds_0123456789ab", "name": "Orders", "folder": "Analytics", "connectionId": "default",
               "queryLanguage": "aggregation-json", "collection": "orders", "querySource": '[{"$match": {"a.b": 1}}]',
               "columns": [{"name": "customer.city", "kind": "text", "role": "dimension"}], "refreshEveryMinutes": 60,
               "status": "ready", "rowCount": 20000, "sizeBytes": 253000, "nextRefreshAt": "2026-01-01T11:00:00.000Z"}
    store.put(ent.ANALYTICS_DATASETS, dataset)
    assert store.get(ent.ANALYTICS_DATASETS, dataset["id"]) == dataset
    store.put(ent.ANALYTICS_DATASETS, {**dataset, "status": "refreshing"})
    assert store.get(ent.ANALYTICS_DATASETS, dataset["id"])["status"] == "refreshing" and len(store.find(ent.ANALYTICS_DATASETS)) == 1
    relation = {"id": "rel_0123456789ab", "fromDataset": dataset["id"], "fromColumn": "Branch", "toDataset": "ds_b", "toColumn": "Code",
                "active": True, "createdBy": "maya", "createdAt": "2026-01-01T10:00:00.000Z"}
    store.put(ent.ANALYTICS_RELATIONSHIPS, relation)
    assert store.get(ent.ANALYTICS_RELATIONSHIPS, relation["id"]) == relation
    tiles = [{"id": "t1", "type": "column", "x": 0, "y": 0, "w": 6, "h": 4, "dataset": dataset["id"],
              "groupBy": [{"column": "Date", "bucket": "month"}], "values": [{"column": "Amount", "agg": "sum"}]}]
    board = {"id": "dash_0123456789ab", "name": "Sales", "folder": "Analytics", "tiles": tiles,
             "filters": [{"column": "customer.city", "op": "in", "values": ["Lahore"]}], "owner": "maya", "updatedAt": "2026-01-01T10:00:00.000Z"}
    store.put(ent.ANALYTICS_DASHBOARDS, board)
    assert store.get(ent.ANALYTICS_DASHBOARDS, board["id"]) == board
    for number in range(3):
        store.put(ent.ANALYTICS_REFRESHES, {"id": f"rf_00000000000{number}", "datasetId": dataset["id"], "trigger": "manual",
                                            "actor": "maya", "status": "success", "startedAt": f"2026-01-01T10:00:0{number}.000Z"})
    found = store.find(ent.ANALYTICS_REFRESHES, {"datasetId": dataset["id"]}, order=("startedAt", True), limit=2)
    assert [item["id"] for item in found] == ["rf_000000000002", "rf_000000000001"]
    assert store.delete(ent.ANALYTICS_DASHBOARDS, board["id"]) and store.get(ent.ANALYTICS_DASHBOARDS, board["id"]) is None


def test_older_documents_move_into_the_tables(backend):
    """users.json, api-keys.json, folder-permissions.json, versions/ and history/ become rows once; originals kept."""
    from backend.app.db import entities as ent
    from backend.app.db.upgrade import move_documents
    store = store_module.get_store()
    store.write_json("users.json", {"users": [{"username": "boss", "role": "admin", "salt": "s", "hash": "h", "sessionVersion": 0}]}, private=True)
    store.write_json("api-keys.json", {"keys": [{"id": "0123456789ab", "name": "Site", "hash": "f" * 64, "folders": [], "disabled": False}]}, private=True)
    store.write_json("folder-permissions.json", {"folders": {"": {"users": {"ali": "run"}, "roles": {}}, "HR": {"users": {}, "roles": {"viewer": "none"}}}})
    store.write_json("versions/R1/00001.json", {"version": 1, "savedAt": "2026-01-01T10:00:00+00:00", "action": "published", "user": "boss",
                                                "folder": "General", "title": "R1", "definition": {"id": "R1", "q": [{"$match": {}}]}})
    store.write_json("history/R1.json", [{"at": "2026-01-01T10:00:00+00:00", "action": "published", "user": "boss", "detail": "new"},
                                         {"at": "2026-01-02T10:00:00+00:00", "action": "edited", "user": "boss", "detail": "title"}])
    entities = ent.get_entities()
    counts = move_documents(store, entities)
    assert counts == {"users": 1, "apiKeys": 1, "permissions": 2, "versions": 1, "history": 2, "schedules": 0, "jobRuns": 0}
    assert entities.get(ent.USERS, "boss")["role"] == "admin" and entities.get(ent.API_KEYS, "0123456789ab")["name"] == "Site"
    assert {row["folder"] for row in entities.find(ent.FOLDER_PERMISSIONS)} == {"/", "HR"}
    assert entities.get(ent.REPORT_VERSIONS, "R1", 1)["definition"] == {"id": "R1", "q": [{"$match": {}}]}
    assert [row["action"] for row in entities.find(ent.REPORT_HISTORY, {"reportId": "R1"}, order=("id", False))] == ["published", "edited"]
    assert store.read_json("users.json") is None and store.read_json("migrated/users.json")["users"][0]["username"] == "boss"
    assert not any(move_documents(store, entities).values())

    # then into the step 2 tables, once: rights become rows, rules point at folder ids and user ids
    from backend.app.db import people
    store.delete("meta/schema-v2.json")
    copied = people.upgrade_people(store)
    assert copied["users"] == 1 and copied["apiKeys"] == 1 and copied["folderRules"] == 2
    boss = people.load_users()[0]
    assert boss["username"] == "boss" and boss["superAdmin"] is True and "admin" in boss["rights"]
    assert people.load_keys()[0]["name"] == "Site"
    assert people.load_rules()["HR"]["roles"] == {"viewer": "none"} and "HR" in store.dirs("reports")
    assert "" not in people.load_rules()              # the root rule was for "ali", who has no account: nothing to point at
    assert not any(people.upgrade_people(store).values())


def test_people_and_access_tables(backend):
    """users with e-mail and phone, rights as rows, teams, rules by id, API key folders and networks."""
    from backend.app.db import entities as ent
    from backend.app.db import people
    from backend.app.security import permissions
    people.save_users([{"username": "ali", "rights": ["server", "runner"], "email": "Ali@Example.com", "phone": "+92 300 1234567",
                        "fullName": "Ali Khan", "salt": "s", "hash": "h"},
                       {"username": "sara", "rights": ["designer", "server"], "email": "sara@example.com"}])
    users = {item["username"]: item for item in people.load_users()}
    assert users["ali"]["email"] == "Ali@Example.com" and users["ali"]["rights"] == ["runner", "server"] and users["ali"]["userId"]
    row = ent.get_entities().get(ent.USERS_V2, users["sara"]["userId"])
    assert row["role"] == "designer" and row["isSuperAdmin"] is False
    assert people.taken("email", " ali@example.COM ") and people.taken("phone", "+923001234567") and people.taken("username", "ALI")
    assert not people.taken("email", "ali@example.com", except_username="ali") and not people.taken("phone", "")

    team = people.save_group("Finance", "Money people", ["ali"], by="boss")
    assert team["members"] == ["ali"] and people.groups_of("ali") == [team["groupId"]]
    with pytest.raises(ValueError):
        people.save_group("finance", "", [])
    permissions.save_rules_for("Finance/Monthly", permissions.clean_entry({"groups": {team["groupId"]: "none"}, "users": {"sara": "run"}}))
    rules = permissions.load_rules()
    assert rules["Finance/Monthly"]["groups"] == {team["groupId"]: "none"} and rules["Finance/Monthly"]["users"] == {"sara": "run"}
    assert permissions.explain({"username": "ali", "role": "viewer"}, "Finance/Monthly/Q1") == (0, "team rule on /Finance/Monthly")
    assert permissions.explain({"username": "sara", "role": "designer"}, "Finance/Monthly")[1] == "user rule on /Finance/Monthly"
    store = store_module.get_store()
    store.move_dir("reports/Finance", "reports/Money")                          # the rules follow the folder id
    assert "Money/Monthly" in permissions.load_rules()

    people.save_keys([{"id": "0123456789ab", "name": "Site", "hash": "f" * 64, "folders": ["Money"], "allowedIps": ["10.0.0.0/24"],
                       "webhookUrl": "https://x.example/hook"}])
    key = people.load_keys()[0]
    assert key["folders"] == ["Money"] and key["allowedIps"] == ["10.0.0.0/24"] and key["webhookUrl"] == "https://x.example/hook"
    people.touch_key("0123456789ab", "2026-01-01T00:00:00Z")
    assert people.load_keys()[0]["lastUsedAt"] == "2026-01-01T00:00:00.000Z"

    people.save_users([users["sara"]])                                            # ali deleted: his rows go too
    assert [item["username"] for item in people.load_users()] == ["sara"]
    assert people.load_groups()[0]["members"] == [] and not ent.get_entities().find(ent.USER_RIGHTS, {"userId": users["ali"]["userId"]})
    assert people.delete_group(team["groupId"]) and "groups" not in str(permissions.load_rules().get("Money/Monthly", {}).get("groups") or "")


# ------------------------------------------------------------------ reports, drafts, folders, data adapters in tables
def test_reports_drafts_folders_and_adapters_are_rows(backend):
    from backend.app.db import entities as ent
    store = store_module.get_store()
    definition = {"id": "R1", "title": "Sales", "connectionId": "main", "queryLanguage": "mongosh", "collection": "orders",
                  "querySource": '[{"$match": {"a.b": 1}}]'}
    store.write_json("reports/Sales/East/R1.json", definition)
    store.write_json("workspace/Sales/R1.json", {**definition, "_workspace": {"createdAt": "x"}})
    rows = store._entities().find(ent.SERVER_REPORTS)
    folders = {row["folderId"]: row for row in store._entities().find(ent.FOLDER_TREE)}
    assert [(folders[r["folderId"]]["fullPath"], r["reportCode"], r["title"], r["dataSourceId"], r["queryLanguage"], r["collectionName"])
            for r in rows] == [("Sales/East", "R1", "Sales", "main", "mongosh", "orders")]
    east = folders[rows[0]["folderId"]]
    assert east["area"] == "server" and east["name"] == "East" and folders[east["parentId"]]["fullPath"] == "Sales"
    draft = store._entities().find(ent.WORKSPACE_REPORTS)[0]
    assert draft["publishStatus"] == "draft" and draft["versionNo"] == 1 and folders[draft["folderId"]]["area"] == "workspace"
    assert store.read_json("reports/Sales/East/R1.json") == definition and store.modified_at("reports/Sales/East/R1.json")
    assert store.keys("reports") == ["reports/Sales/East/R1.json"] and store.keys("workspace") == ["workspace/Sales/R1.json"]
    assert [key for key, _ in store.read_many("reports/Sales")] == ["reports/Sales/East/R1.json"]
    report_id = store.report_id_of("reports/Sales/East/R1.json")
    store.write_json("reports/Sales/East/R1.json", {**definition, "title": "Sales 2"})
    assert store.report_id_of("reports/Sales/East/R1.json") == report_id                    # a save keeps the id

    store.move_json("reports/Sales/East/R1.json", "reports/Finance/R9.json", {**definition, "id": "R9"})
    assert store.report_id_of("reports/Finance/R9.json") == report_id and not store.exists("reports/Sales/East/R1.json")
    store.move_dir("reports/Finance", "reports/Sales/Money")                               # one folder row changes
    assert store.report_id_of("reports/Sales/Money/R9.json") == report_id and "Finance" not in store.dirs("reports")
    with pytest.raises(ValueError):
        store.move_dir("reports/Sales", "reports/Sales/Money/Inner")
    assert store.set_row_fields("workspace/Sales/R1.json", lockedBy="ali") and store.report_row("workspace/Sales/R1.json")["lockedBy"] == "ali"

    assert not store.has_dir("workspace")
    store.make_dir("workspace")
    store.make_dir("reports/Empty")
    assert store.has_dir("workspace") and {"Sales", "Sales/Money", "Empty"} <= set(store.dirs("reports"))
    assert store.remove_dir("reports/Sales") is False and store.remove_dir("reports/Sales", recursive=True) is True
    assert store.keys("reports") == [] and "Sales" not in store.dirs("reports") and store.keys("workspace")   # drafts kept

    if backend != "files":                                                         # files keep connections.json
        store.write_json("connections.json", {"connections": [{"id": "main", "name": "Main", "type": "mongodb", "storage": "file", "database": "a"},
                                                              {"id": "pg", "name": "PG", "type": "postgresql", "storage": "file", "uriExtra": "x"}]})
        assert [r["id"] for r in sorted(store._entities().find(ent.DATA_SOURCES), key=lambda r: r["position"])] == ["main", "pg"]
        assert store.read_json("connections.json")["connections"][1] == {"id": "pg", "name": "PG", "type": "postgresql", "storage": "file", "uriExtra": "x"}
        store.write_json("connections.json", {"connections": []})
        assert store.read_json("connections.json") == {"connections": []}            # emptied, not "never set up"
    store.write_json("folders.json", {"folders": ["A", "A/B"]})
    assert store.read_json("folders.json") == {"folders": ["A", "A/B"]}


def test_documents_from_the_earlier_version_move_into_the_tables(backend):
    from backend.app.storage.routed import RoutedStore
    store = store_module.get_store()
    plain = super(RoutedStore, store)              # the document layer underneath, as the earlier version wrote it
    plain.write_json("reports/General/OLD.json", {"id": "OLD", "title": "Old one"})
    plain.write_json("workspace/General/OLD.json", {"id": "OLD"})
    plain.make_dir("reports/Empty")
    plain.make_dir("workspace")
    plain.write_json("connections.json", {"connections": [{"id": "default", "name": "Main", "storage": "file", "database": "a"}]})
    plain.write_json("folders.json", {"folders": ["Finance"]})
    plain.delete("meta/schema-v2.json")
    counts = store.upgrade_v2()
    assert counts["reports"] == 1 and counts["drafts"] == 1 and counts["folderList"] == 1
    assert counts["dataSources"] == (0 if backend == "files" else 1)
    row = store.report_row("reports/General/OLD.json")
    assert row["title"] == "Old one" and plain.read_json("reports/General/OLD.json") is None
    assert plain.read_json("migrated/reports/General/OLD.json") == {"id": "OLD", "title": "Old one"}          # original kept
    assert {"Empty", "Finance"} <= set(store.dirs("reports")) and store.has_dir("workspace")
    assert store.read_json("connections.json")["connections"][0]["id"] == "default" and store.read_json("folders.json") == {"folders": ["Finance"]}
    assert not any(store.upgrade_v2().values())                                    # a second start changes nothing


def test_tables_of_the_previous_version_are_copied_once(backend):
    """rs_reports / rs_drafts / rs_folders and versions / history by report ID become v2 rows with ids."""
    if backend == "files":
        pytest.skip("on files the previous version kept documents (test above)")
    from backend.app.db import entities as ent
    from backend.app.storage.routed import RoutedStore, ghost_id
    store = store_module.get_store()
    plain, entities = super(RoutedStore, store), store._entities()
    entities.put(ent.REPORTS, {"folder": "Sales", "reportId": "R1", "title": "Sales", "definition": {"id": "R1", "title": "Sales"}})
    entities.put(ent.DRAFTS, {"folder": "Sales", "reportId": "R1", "definition": {
        "id": "R1", "_workspace": {"publishedAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-02T00:00:00Z"}}})
    entities.put(ent.FOLDERS, {"area": "reports", "path": "Empty"})
    entities.put(ent.FOLDERS, {"area": "workspace", "path": "/"})
    entities.put(ent.FOLDERS, {"area": "list", "path": "Listed"})
    plain.write_json("folders.json", {"storedIn": "rs_folders"})
    for number in (3, 5):
        entities.put(ent.REPORT_VERSIONS, {"reportId": "R1", "version": number, "folder": "Sales", "title": f"v{number}",
                                           "savedAt": "2026-01-01T00:00:00Z", "definition": {"id": "R1"}})
    entities.put(ent.REPORT_VERSIONS, {"reportId": "GONE", "version": 1, "folder": "Old", "savedAt": "2026-01-01T00:00:00Z", "definition": {}})
    entities.put(ent.REPORT_HISTORY, {"id": "h1", "reportId": "R1", "at": "2026-01-01T00:00:00Z", "action": "saved", "user": "ali"})
    plain.delete("meta/schema-v2.json")
    counts = store.upgrade_v2()
    assert counts["reports"] == 1 and counts["drafts"] == 1 and counts["versions"] == 3 and counts["history"] == 1
    server_id = store.report_id_of("reports/Sales/R1.json")
    draft = store.report_row("workspace/Sales/R1.json")
    assert draft["publishStatus"] == "changed" and draft["serverReportId"] == server_id
    assert {"Empty", "Listed", "Sales"} <= set(store.dirs("reports")) and store.has_dir("workspace")
    assert store.read_json("folders.json") == {"folders": ["Listed"]}
    versions = entities.find(ent.REPORT_VERSIONS_V2, {"serverReportId": server_id})
    assert sorted(row["versionNo"] for row in versions) == [1, 2] and entities.get(ent.SERVER_REPORTS, server_id)["currentVersion"] == 2
    assert entities.find(ent.REPORT_VERSIONS_V2, {"serverReportId": ghost_id("Old", "GONE")})[0]["reportCode"] == "GONE"
    assert entities.get(ent.REPORT_HISTORY_V2, "h1")["serverReportId"] == server_id
    assert not any(store.upgrade_v2().values())                                    # once


def test_secrets_table(backend, monkeypatch):
    """private_secrets.json becomes one row per secret, linked to its owner; encrypted rows when a key is set."""
    from backend.app.db import entities as ent
    from backend.app.security import secrets as secret_store
    store = store_module.get_store()
    store.write_json("private_secrets.json", {"admin_token": ADMIN, "mongo_connections": {"default": {"uri": "mongodb://u:pw@main/a"}},
                                              "delivery_secrets": {"job1": {"t1": {"password": "sftp-pw"}}}}, private=True)
    assert secret_store.upgrade_secrets() == 3 and secret_store.upgrade_secrets() == 0          # once
    assert store.read_json("private_secrets.json") is None and store.read_json("migrated/private_secrets.json")["admin_token"] == ADMIN
    rows = {(row["ownerType"], row["ownerId"], row["secretName"]): row for row in ent.get_entities().find(ent.SECRETS)}
    assert set(rows) == {("system", "service", "admin_token"), ("data_source", "default", "uri"), ("schedule_target", "job1/t1", "password")}
    assert rows[("data_source", "default", "uri")]["keyVersion"] == "plain"
    loaded = secret_store._load_private_secrets()
    assert loaded["mongo_connections"]["default"]["uri"] == "mongodb://u:pw@main/a" and loaded["delivery_secrets"]["job1"]["t1"]["password"] == "sftp-pw"

    monkeypatch.setenv("REPORTING_SECRETS_KEY", "a-long-passphrase-for-the-tests-0123456789")
    assert secret_store._load_private_secrets()["admin_token"] == ADMIN                       # encrypts every row
    assert secret_store.secrets_encrypted()
    assert all("pw" not in str(row["encryptedValue"]) for row in ent.get_entities().find(ent.SECRETS))
    before = {row["secretId"]: row["encryptedValue"] for row in ent.get_entities().find(ent.SECRETS)}
    loaded = secret_store._load_private_secrets()
    loaded["delivery_secrets"] = {}                                                            # the schedule was deleted
    secret_store._save_private_secrets(loaded, by="admin")
    after = {row["secretId"]: row["encryptedValue"] for row in ent.get_entities().find(ent.SECRETS)}
    assert len(after) == 2 and all(after[key] == before[key] for key in after)                # unchanged rows are not rewritten
    monkeypatch.setenv("REPORTING_SECRETS_KEY", "another-wrong-passphrase-0123456789abcdef")
    with pytest.raises(RuntimeError, match="wrong key"):
        secret_store._load_private_secrets()
