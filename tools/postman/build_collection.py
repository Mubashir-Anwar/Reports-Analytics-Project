"""Build the Postman collection and environment for every API of the reporting service.

    python tools/postman/build_collection.py            -> postman/Reporting-Service.postman_collection.json
                                                           postman/Local.postman_environment.json

The collection is a complete end-to-end run (create -> use -> clean up), so it can be run as a whole in Postman
("Run collection") or from the command line:

    npx newman run postman/Reporting-Service.postman_collection.json -e postman/Local.postman_environment.json

It needs a data adapter it can read: by default a local MongoDB with the demo database rs_postman_demo
(tools/postman/seed_demo.py creates it). Everything it creates is named PM_… / pm_… / "Postman" and removed at
the end. Fill in the environment: baseUrl, username/password (a super admin, or "admin" + the admin token during
first setup), adminToken (for the token-based requests) and mongoUri.
"""
from __future__ import annotations

import base64
import io
import json
import uuid
import zipfile
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "postman"
REPORT = "PM_ORDERS"
QUERY = '[{"$match": {"Branch": {"$param": "Branch"}}}, {"$project": {"_id": 0, "Name": 1, "Branch": 1, "Amount": 1, "CNIC": 1}}]'
DESIGN = {"bands": {"columnHeader": {"height": 20, "elements": [
    {"type": "staticText", "x": 0, "y": 0, "width": 160, "height": 16, "text": "Name"},
    {"type": "staticText", "x": 170, "y": 0, "width": 100, "height": 16, "text": "Amount"}]},
    "detail": {"height": 20, "elements": [
        {"type": "textField", "x": 0, "y": 0, "width": 160, "height": 16, "expression": "$F{Name}"},
        {"type": "textField", "x": 170, "y": 0, "width": 100, "height": 16, "expression": "$F{Amount}"}]}}}


def definition(report_id: str = REPORT, folder: str = "Postman", title: str = "Postman Orders") -> dict:
    return {"id": report_id, "title": title, "description": "Created by the Postman collection", "folder": folder,
            "connectionId": "pm_mongo", "collection": "orders", "queryLanguage": "aggregation-json", "querySource": QUERY,
            "columns": ["Name", "Branch", "Amount", "CNIC"], "formats": ["pdf", "xlsx", "csv"], "defaultFormat": "pdf",
            "parameters": [{"name": "Branch", "type": "string", "label": "Branch", "required": True, "allowedValues": ["LHR", "KHI", "ISB"]},
                           {"name": "CNIC", "type": "string", "label": "CNIC", "required": False, "sensitive": True}],
            "design": DESIGN}


JRXML = """<?xml version="1.0" encoding="UTF-8"?>
<jasperReport xmlns="http://jasperreports.sourceforge.net/jasperreports" name="PM_JRXML" pageWidth="595" pageHeight="842" columnWidth="555" leftMargin="20" rightMargin="20" topMargin="20" bottomMargin="20">
  <queryString language="MongoDbQuery"><![CDATA[{ "runCommand": { "aggregate": "orders", "pipeline": [ {"$project": {"Name": 1, "Amount": 1}} ] } }]]></queryString>
  <field name="Name" class="java.lang.String"/><field name="Amount" class="java.lang.Integer"/>
  <detail><band height="20"><textField><reportElement x="0" y="0" width="200" height="16"/><textFieldExpression><![CDATA[$F{Name}]]></textFieldExpression></textField></band></detail>
</jasperReport>"""

def blank_png(width: int = 200, height: int = 280) -> str:
    """A white page as a PNG data URL (no imaging library needed); the importer wants at least 50 x 50 pixels."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    rows = b"".join(b"\x00" + b"\xff" * width * 3 for _ in range(height))
    raw = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


PAGE_PNG = blank_png()
RUNNER_PASSWORD = "Postman-pass-2026"
RUNNER_NEW_PASSWORD = "Postman-pass-2026-new"

ITEMS: list[dict] = []
FOLDER: list = []


def folder(name: str, description: str = "") -> None:
    global FOLDER
    FOLDER = []
    ITEMS.append({"name": name, "description": description, "item": FOLDER})


def req(name: str, method: str, path: str, *, body=None, raw_body: str | None = None, expect=(200,), auth: str = "session",
        save: dict | None = None, checks: str = "", redirects: bool = True, description: str = "",
        extra_headers: dict | None = None) -> None:
    """auth: session (the signed-in cookie) | admin (X-Admin-Token) | key (X-API-Key) | none."""
    headers = []
    if auth == "admin":
        headers.append({"key": "X-Admin-Token", "value": "{{adminToken}}"})
    elif auth == "key":
        headers.append({"key": "X-API-Key", "value": "{{apiKey}}"})
    for key, value in (extra_headers or {}).items():
        headers.append({"key": key, "value": value})
    if body is not None or raw_body is not None or method in ("POST", "PUT", "DELETE"):
        headers.append({"key": "Content-Type", "value": "application/json"})
    if raw_body is None and method in ("POST", "PUT", "DELETE"):
        raw_body = json.dumps(body if body is not None else {}, indent=2, ensure_ascii=False)
    label = json.dumps(f"{name}: HTTP {' or '.join(map(str, expect))}")       # JSON quoting is valid JavaScript
    script = [f"pm.test({label}, function () {{ pm.expect({list(expect)}).to.include(pm.response.code); }});"]
    for variable, expression in (save or {}).items():
        script.append(f"try {{ pm.environment.set('{variable}', {expression}); }} catch (e) {{ console.log('could not save {variable}', e); }}")
    if checks:
        script.append(checks)
    path_part, _, query = path.partition("?")
    url = {"raw": "{{baseUrl}}" + path, "host": ["{{baseUrl}}"], "path": [part for part in path_part.split("/") if part]}
    if query:
        url["query"] = [{"key": pair.split("=", 1)[0], "value": pair.split("=", 1)[1] if "=" in pair else ""} for pair in query.split("&")]
    item = {"name": name, "request": {"method": method, "header": headers, "url": url, "description": description},
            "event": [{"listen": "test", "script": {"type": "text/javascript", "exec": script}}]}
    if auth in ("admin", "key", "none"):
        item["request"]["auth"] = {"type": "noauth"}
    if raw_body is not None:
        item["request"]["body"] = {"mode": "raw", "raw": raw_body, "options": {"raw": {"language": "json"}}}
    if not redirects:
        item["protocolProfileBehavior"] = {"followRedirects": False}
    FOLDER.append(item)


# ------------------------------------------------------------------ the run, in order
folder("01 Health and pages (signed out)")
req("Health", "GET", "/health", auth="none", expect=(200, 503),
    description="503 = the default data adapter (MONGODB_URI) is not configured or not reachable; the service itself is up.")
req("Ready (load balancer)", "GET", "/ready", auth="none")
req("Sign-in page", "GET", "/login", auth="none")
req("Designer page sends a signed-out user to sign-in", "GET", "/designer", auth="none", expect=(303,), redirects=False,
    checks="pm.test('redirects to /login', function () { pm.expect(pm.response.headers.get('Location')).to.include('/login'); });")
req("Admin API refuses without sign-in", "GET", "/api/admin/users", auth="none", expect=(401,))
req("Forgot password: which ways the sign-in page offers", "GET", "/api/server/password-help", auth="none",
    checks="pm.test('admin approval is offered', function () { pm.expect(pm.response.json().adminApproval).to.eql(true); });")
req("Public API refuses without a key", "GET", "/api/v1/reports", auth="none", expect=(401,))

folder("02 Sign in", "Signs in with {{username}} / {{password}}; Postman keeps the session cookie for the next requests.")
req("Sign in", "POST", "/api/server/login", body={"username": "{{username}}", "password": "{{password}}"},
    checks="pm.test('has rights', function () { pm.expect(pm.response.json().rights).to.be.an('array'); });")
req("Who am I (session)", "GET", "/api/server/me")
req("Home sends to the first allowed page", "GET", "/", expect=(303,), redirects=False)
for page in ("/designer", "/query", "/server", "/admin", "/analytics"):
    req(f"Page {page}", "GET", page)
req("The old Run reports page (/ui) goes home", "GET", "/ui", expect=(303,), redirects=False,
    checks="pm.test('redirects to /', function () { pm.expect(pm.response.headers.get('Location')).to.eql('/'); });")
req("Admin whoami", "GET", "/api/admin/whoami")
req("System status and production checklist", "GET", "/api/admin/system/status")

folder("03 Data sources (Admin)")
req("Test a connection before saving", "POST", "/api/admin/connections/test",
    body={"type": "mongodb", "uri": "{{mongoUri}}", "database": "rs_postman_demo"})
req("Save data adapter pm_mongo", "POST", "/api/admin/connections",
    body={"id": "pm_mongo", "name": "Postman Mongo", "type": "mongodb", "uri": "{{mongoUri}}", "database": "rs_postman_demo", "folder": ""})
req("List data adapters", "GET", "/api/admin/connections")
req("Test saved adapter", "POST", "/api/admin/connections/pm_mongo/test")
req("Move adapter to a folder", "PUT", "/api/admin/connections/pm_mongo/folder", body={"folder": "Postman"})

folder("04 Repository folders (Admin)")
req("Create folder Postman", "POST", "/api/admin/folders", body={"path": "Postman"})
req("List folders", "GET", "/api/admin/folders")
req("Folder contents", "GET", "/api/admin/folders/contents?path=Postman")

folder("05 Designer")
req("Parse a query", "POST", "/api/admin/query/parse", body={"language": "aggregation-json", "source": QUERY, "parameters": [{"name": "Branch"}]},
    checks="pm.test('two stages', function () { pm.expect(pm.response.json().stageCount).to.eql(2); });")
req("Read SQL fields (needs a SQL adapter: refused for MongoDB)", "POST", "/api/admin/query/fields",
    body={"querySource": "SELECT 1 AS A", "connectionId": "pm_mongo"}, expect=(422,))
req("Validate a definition", "POST", "/api/admin/designer/validate", body={"definition": definition()})
req("Render a design to PDF", "POST", "/api/admin/designer/render",
    body={"design": DESIGN, "columns": ["Name", "Amount"], "rows": [{"Name": "Ali", "Amount": 1}], "parameters": {}, "title": "Render", "format": "pdf"})
req("Import a JRXML (Jaspersoft Studio)", "POST", "/api/admin/jrxml/import", body={"xml": JRXML})
req("Save as JRXML (for Jaspersoft Studio)", "POST", "/api/admin/jrxml/export", body=definition(),
    checks="pm.test('a jasperReport file', function () { const b = pm.response.json(); pm.expect(b.xml).to.include('<jasperReport'); "
           "pm.expect(b.fileName).to.include('.jrxml'); });")
req("Import an existing design (PNG, PDF, Word, .ai)", "POST", "/api/admin/designer/import-design",
    body={"file": PAGE_PNG, "name": "page.png", "mode": "elements", "page": 1, "textOnTop": True},
    description="Nothing is saved: the answer is the page size, a background picture and the elements found.")
req("Custom function: try it", "POST", "/api/admin/designer/functions/try",
    body={"functions": [{"name": "FullName", "params": ["first", "last"], "body": "first + \" \" + last"}],
          "expression": "FullName($F{First}, \"Khan\")", "values": {"First": "Ali"}},
    checks="pm.test('Ali Khan', function () { pm.expect(pm.response.json().value).to.eql('Ali Khan'); });")
req("Custom function: a wrong one is refused", "POST", "/api/admin/designer/functions/try",
    body={"functions": [{"name": "Bad", "params": [], "body": "1 +"}], "expression": "Bad()"}, expect=(422,))
req("Create workspace folder", "POST", "/api/admin/workspace-folders", body={"path": "Postman"})
req("Remove a draft left by an earlier run", "DELETE", f"/api/admin/workspace/{REPORT}", expect=(200, 404))
req("Save draft PM_ORDERS", "PUT", f"/api/admin/workspace/{REPORT}?create=true", body=definition())
req("List drafts", "GET", "/api/admin/workspace")
req("Get draft", "GET", f"/api/admin/workspace/{REPORT}")
req("Mark the draft as open in the designer", "POST", f"/api/admin/workspace/{REPORT}/editing", body={"open": True},
    description="The designer sends this every 30 s while the draft is open; the answer says who else is editing it.")
req("Mark the draft as closed", "POST", f"/api/admin/workspace/{REPORT}/editing", body={"open": False})
req("Workspace folder contents (what deleting it would touch)", "GET", "/api/admin/workspace-folders/contents?path=Postman",
    checks="pm.test('lists the draft', function () { pm.expect(pm.response.json().reports.length).to.be.above(0); });")
req("Preview draft (20 rows)", "POST", "/api/admin/drafts/preview",
    body={"definition": definition(), "parameters": {"Branch": "LHR"}, "connectionId": "pm_mongo"},
    checks="pm.test('two LHR rows', function () { pm.expect(pm.response.json().rowCount).to.eql(2); });")
req("Run draft to CSV", "POST", "/api/admin/drafts/run",
    body={"definition": definition(), "parameters": {"Branch": "LHR"}, "connectionId": "pm_mongo", "format": "csv"})
req("Publish draft to the server", "POST", f"/api/admin/workspace/{REPORT}/publish", body={"folder": "Postman", "replace": True})

folder("05b Query workspace (/query)",
       "Browse a data adapter and try queries without making a report: MongoDB find / aggregate / fields, and SQL. "
       "Everything is read-only ($out, $merge, $where and $function are refused; SQL runs one read-only statement).")
req("MongoDB: collections of pm_mongo", "POST", "/api/query/mongo/collections", body={"connectionId": "pm_mongo"},
    checks="pm.test('has orders', function () { pm.expect(pm.response.json().collections.map(c => c.name)).to.include('orders'); });")
req("MongoDB: find with filter, projection and sort", "POST", "/api/query/mongo/find",
    body={"connectionId": "pm_mongo", "collection": "orders", "filter": '{ Branch: "LHR" }', "projection": "{ _id: 0, Name: 1, Amount: 1 }",
          "sort": "{ Amount: -1 }", "skip": 0, "limit": 50},
    checks="pm.test('two LHR orders', function () { const b = pm.response.json(); pm.expect(b.total).to.eql(2); pm.expect(b.documents.length).to.eql(2); });")
req("MongoDB: aggregate (stages as shell text) and the code", "POST", "/api/query/mongo/aggregate",
    body={"connectionId": "pm_mongo", "collection": "orders", "limit": 50,
          "stages": ['{ $group: { _id: "$Branch", total: { $sum: "$Amount" } } }', "{ $sort: { total: -1 } }"]},
    checks="pm.test('rows and code', function () { const b = pm.response.json(); pm.expect(b.documents.length).to.be.above(0); "
           "pm.expect(b.code).to.include('aggregate'); });")
req("MongoDB: a writing stage is refused", "POST", "/api/query/mongo/aggregate",
    body={"connectionId": "pm_mongo", "collection": "orders", "stages": ['{ $out: "copy" }']}, expect=(422,))
req("MongoDB: fields in a sample", "POST", "/api/query/mongo/fields", body={"connectionId": "pm_mongo", "collection": "orders"},
    checks="pm.test('has Amount', function () { pm.expect(pm.response.json().fields.map(f => f.path)).to.include('Amount'); });")
req("SQL: schema (needs a SQL adapter: refused for MongoDB)", "POST", "/api/query/sql/schema", body={"connectionId": "pm_mongo"},
    expect=(422,), description="With a SQL adapter the answer has schemas, tables, columns, primary and foreign keys.")
req("SQL: run a SELECT (needs a SQL adapter: refused for MongoDB)", "POST", "/api/query/sql/run",
    body={"connectionId": "pm_mongo", "sql": "SELECT 1 AS one", "parameters": {}, "limit": 200}, expect=(422,),
    description="With a SQL adapter: one read-only statement, rolled back; $P{Name} values come from parameters.")

folder("06 Published reports (Admin)")
req("List published reports", "GET", "/api/admin/reports")
req("Get report", "GET", f"/api/admin/reports/{REPORT}")
req("Report as .mrd (XML)", "GET", f"/api/admin/reports/{REPORT}/definition.xml", save={"mrdXml": "JSON.stringify(pm.response.text())"})
req("Preview report", "POST", f"/api/admin/reports/{REPORT}/preview", body={"parameters": {"Branch": "KHI"}})
req("Run report to Excel", "POST", f"/api/admin/reports/{REPORT}/run", body={"parameters": {"Branch": "KHI"}, "format": "xlsx"})
req("Update report", "PUT", f"/api/admin/reports/{REPORT}", body=definition(title="Postman Orders v2"))
req("Create report PM_ADMIN", "POST", "/api/admin/reports", body=definition("PM_ADMIN", title="Postman admin report"))
req("Import .mrd", "POST", "/api/admin/reports/import",
    raw_body='{"xml": {{mrdXml}}, "folder": "Postman", "replace": true, "connectionId": "pm_mongo"}')

folder("07 Users and rights (Admin)")
req("New user: is the name or e-mail already taken?", "GET", "/api/admin/users/available?username=pm_runner&email=pm_runner%40example.com",
    checks="pm.test('answers per field', function () { pm.expect(pm.response.json().username).to.have.property('taken'); });")
req("Remove pm_runner left by an earlier run", "DELETE", "/api/admin/users/pm_runner", expect=(200, 404))
req("Create user pm_runner", "POST", "/api/admin/users",
    body={"username": "pm_runner", "password": RUNNER_PASSWORD, "rights": ["runner"], "fullName": "Postman Runner",
          "email": "pm_runner@example.com"})
req("List users", "GET", "/api/admin/users")
req("Change rights", "PUT", "/api/admin/users/pm_runner", body={"rights": ["runner", "server"]})
req("Create team PM team", "POST", "/api/admin/user-groups", body={"name": "PM team", "description": "Made by Postman", "members": ["pm_runner"]},
    save={"groupId": "pm.response.json().group.groupId"})
req("Teams", "GET", "/api/admin/user-groups")
req("Change the team", "PUT", "/api/admin/user-groups/{{groupId}}", body={"name": "PM team", "description": "Edited", "members": ["pm_runner"]})

folder("07b My account and forgotten passwords",
       "Signs in as pm_runner for My account, asks for a password reset as a visitor, approves it as {{username}} and uses "
       "the one-time link. Ends signed in as {{username}} again.")
req("Sign in as pm_runner", "POST", "/api/server/login", body={"username": "pm_runner", "password": RUNNER_PASSWORD})
req("My account", "GET", "/api/server/account",
    checks="pm.test('is pm_runner', function () { pm.expect(pm.response.json().username).to.eql('pm_runner'); });")
req("Change my name and phone", "PUT", "/api/server/account", body={"fullName": "Postman Runner (edited)", "email": "pm_runner@example.com"})
req("Change my password: a wrong current password is refused", "POST", "/api/server/account/password",
    body={"currentPassword": "not-it", "newPassword": RUNNER_NEW_PASSWORD}, expect=(403,))
req("Team chat: who is online (heartbeat)", "POST", "/api/team/heartbeat", body={"tab": "postman", "app": "server", "place": "", "title": "Postman"})
req("Team chat: people I can write to", "GET", "/api/team/users")
req("Team chat: send a message", "POST", "/api/team/messages", body={"text": "Hello from Postman", "to": None, "place": ""})
req("Team chat: messages", "GET", "/api/team/messages?after=0",
    checks="pm.test('has the message', function () { pm.expect(pm.response.json().messages.map(m => m.text)).to.include('Hello from Postman'); });")
req("Team chat: leave", "DELETE", "/api/team/heartbeat?tab=postman", body={})
req("Sign out pm_runner", "POST", "/api/server/logout", body={})
req("Forgot password (a visitor asks; the answer never says whether the account exists)", "POST", "/api/server/forgot-password",
    auth="none", body={"username": "pm_runner", "method": "admin_approval"})
req("Sign in again as {{username}}", "POST", "/api/server/login", body={"username": "{{username}}", "password": "{{password}}"})
req("Password reset requests", "GET", "/api/admin/password-resets",
    save={"resetId": "pm.response.json().requests.find(r => r.username === 'pm_runner' && r.status === 'waiting').resetId"})
req("Approve it: a one-time link to pass on", "POST", "/api/admin/password-resets/{{resetId}}/approve", body={},
    save={"resetToken": "pm.response.json().link.split('reset=')[1]"})
req("The link sets a new password", "POST", "/api/server/reset-password", auth="none",
    raw_body='{"token": "{{resetToken}}", "password": "' + RUNNER_NEW_PASSWORD + '"}')
req("The link works once", "POST", "/api/server/reset-password", auth="none",
    raw_body='{"token": "{{resetToken}}", "password": "' + RUNNER_NEW_PASSWORD + '-2"}', expect=(400,))
req("A decided request cannot be decided again", "POST", "/api/admin/password-resets/{{resetId}}/reject", body={}, expect=(409,))

folder("08 Report Server")
req("Repository", "GET", "/api/server/repository")
req("Folder contents", "GET", "/api/server/folders/contents?path=Postman")
req("Create subfolder", "POST", "/api/server/folders", body={"path": "Postman/Sub"})
req("Report details", "GET", f"/api/server/reports/{REPORT}")
req("Edit title", "PUT", f"/api/server/reports/{REPORT}", body={"title": "Postman Orders (server edit)"})
req("Versions", "GET", f"/api/server/reports/{REPORT}/versions", save={"versionNumber": "pm.response.json().versions[pm.response.json().versions.length - 1].version"})
req("One version", "GET", f"/api/server/reports/{REPORT}/versions/{{{{versionNumber}}}}")
req("Restore the first version", "POST", f"/api/server/reports/{REPORT}/versions/{{{{versionNumber}}}}/restore", body={})
req("Copy", "POST", f"/api/server/reports/{REPORT}/copy", body={"folder": "Postman/Sub", "newId": "PM_ORDERS_COPY"},
    description="newId gives the copy its own ID. Without it a copy into another folder keeps the ID (like JasperReports Server): "
                "the same ID is then in two folders, see '11 Report access'.")
req("Move the copy", "POST", "/api/server/reports/PM_ORDERS_COPY/move", body={"folder": "Postman"})
req("Download .mrd", "GET", f"/api/server/reports/{REPORT}/file")
req("Upload .mrd as a new report", "POST", "/api/server/upload",
    raw_body='{"fileName": "orders.mrd", "content": {{mrdXml}}, "folder": "Postman/Sub", "newId": "PM_UPLOADED"}')
req("Run now (PDF)", "POST", f"/api/server/reports/{REPORT}/run", body={"format": "pdf", "parameters": {"Branch": "LHR"}})
req("Run in the background", "POST", f"/api/server/reports/{REPORT}/background", body={"format": "csv", "parameters": {"Branch": "ISB"}},
    save={"serverRunId": "pm.response.json().run.id"})
req("Runs", "GET", "/api/server/runs")
req("One run", "GET", "/api/server/runs/{{serverRunId}}")
req("Run file (ready, or still running)", "GET", "/api/server/runs/{{serverRunId}}/file", expect=(200, 409))
req("Data adapters (server)", "GET", "/api/server/connections")
req("Test adapter (server)", "POST", "/api/server/connections/pm_mongo/test")
req("Folder permissions", "GET", "/api/server/permissions?path=Postman")
req("Set folder permissions", "PUT", "/api/server/permissions", body={"path": "Postman", "rules": {"users": {"pm_runner": "run"}, "roles": {}}})
req("Who can see what (every user on every folder with a rule)", "GET", "/api/admin/access-report",
    checks="pm.test('pm_runner may run in Postman', function () { pm.expect(pm.response.json().rows.some(r => "
           "r.username === 'pm_runner' && r.folder === 'Postman')).to.eql(true); });")

folder("09 Schedules")
req("Schedule options", "GET", "/api/server/schedules/info")
req("Create schedule", "POST", "/api/server/schedules",
    body={"label": "PM daily", "reportId": REPORT, "format": "csv", "parameters": {"Branch": "LHR"},
          "recurrence": {"type": "daily", "time": "08:00"}, "timezone": "Asia/Karachi", "keep": 5},
    save={"scheduleId": "pm.response.json().schedule.id"})
req("List schedules", "GET", "/api/server/schedules")
req("Try a delivery target: an unknown type is refused", "POST", "/api/server/schedules/delivery/test",
    body={"target": {"type": "ftp", "host": "example.com"}}, expect=(422,),
    description="SFTP: logs in and opens the folder; HTTP: checks the address; folder: checks it can write. Nothing is uploaded.")
req("Edit schedule", "PUT", "/api/server/schedules/{{scheduleId}}",
    body={"label": "PM daily (edited)", "reportId": REPORT, "format": "pdf", "parameters": {"Branch": "KHI"},
          "recurrence": {"type": "weekly", "time": "09:30", "weekdays": [0, 2]}, "timezone": "Asia/Karachi", "keep": 3})
req("Switch schedule off", "POST", "/api/server/schedules/{{scheduleId}}/enabled", body={"enabled": False})
req("Run schedule now", "POST", "/api/server/schedules/{{scheduleId}}/run", body={})

folder("10 API keys (Admin)")
req("Create API key", "POST", "/api/admin/api-keys", body={"name": "Postman site", "folders": ["Postman"]},
    save={"apiKey": "pm.response.json().secret", "apiKeyId": "pm.response.json().key.id"})
req("List API keys", "GET", "/api/admin/api-keys")
req("Set its rate limit", "PUT", "/api/admin/api-keys/{{apiKeyId}}", body={"ratePerMinute": 600})

folder("11 Report access (website, token)")
req("Reports (admin token)", "GET", "/reports", auth="admin")
req("Generate CSV by URL (admin token)", "GET", f"/generate/{REPORT}.csv?Branch=LHR", auth="admin")
req("Generate PDF by URL (API key)", "GET", f"/generate/{REPORT}.pdf?Branch=KHI", auth="key")
req("Adapter status for the runner", "GET", "/reports/connections/pm_mongo/status", auth="admin")

SAME_ID = ("A report is <folder>/<ID>: the same ID can be published in several folders. Folder= (the report's server folder) "
           "says which one; without it an ID that is in several folders is refused with 409.")
req("Same ID in a second folder: copy PM_ORDERS into Postman/Sub", "POST", f"/api/server/reports/{REPORT}/copy?folder=Postman",
    body={"folder": "Postman/Sub"}, description=SAME_ID,
    checks="pm.test('keeps the ID', function () { pm.expect(pm.response.json().report.ref).to.eql('Postman/Sub/PM_ORDERS'); });")
req("Same ID: without Folder the ID is unclear (409)", "GET", f"/generate/{REPORT}.csv?Branch=LHR", auth="key", expect=(409,),
    description=SAME_ID, checks="pm.test('asks for Folder', function () { pm.expect(pm.response.json().detail).to.include('Folder='); });")
req("Same ID: Folder chooses the report", "GET", f"/generate/{REPORT}.xlsx?Folder=Postman/Sub/&Branch=LHR", auth="key",
    description=SAME_ID + " A trailing or leading / in Folder is fine.")
req("Same ID: a folder without that report (404)", "GET", f"/generate/{REPORT}.csv?Folder=Postman/Other&Branch=LHR", auth="key",
    expect=(404,), checks="pm.test('names the folders', function () { pm.expect(pm.response.json().detail).to.include('Postman/Sub'); });")
req("Same ID: public API takes \"folder\" in the body", "POST", f"/api/v1/reports/{REPORT}/run", auth="key",
    body={"format": "csv", "folder": "Postman/Sub", "parameters": {"Branch": "LHR"}})
req("Same ID: delete the second copy (?folder=)", "DELETE", f"/api/server/reports/{REPORT}?folder=Postman/Sub")

folder("12 Public API v1 (API key)")
req("Who am I (key)", "GET", "/api/v1/whoami", auth="key",
    checks="pm.test('is the Postman key', function () { pm.expect(pm.response.json().name).to.eql('Postman site'); });")
req("Reports the key may run", "GET", "/api/v1/reports", auth="key")
req("Wrong report ID: the answer names the right one", "GET", "/generate/ORDERS_PM.csv", auth="key", expect=(404,),
    checks="pm.test('suggests PM_ORDERS', function () { pm.expect(pm.response.json().detail).to.include('Did you mean PM_ORDERS'); });")
req("One report with parameters", "GET", f"/api/v1/reports/{REPORT}", auth="key")
req("Run now (JSON in, file out)", "POST", f"/api/v1/reports/{REPORT}/run", auth="key",
    body={"format": "csv", "parameters": {"Branch": "LHR", "CNIC": "4220112345671"}, "onBehalfOf": "postman-user"})
req("Report data as JSON (page 1)", "POST", f"/api/v1/reports/{REPORT}/data", auth="key",
    body={"parameters": {"Branch": "KHI"}, "limit": 1, "offset": 0},
    checks="pm.test('JSON rows with paging', function () { const b = pm.response.json(); pm.expect(b.columns).to.include('Name'); "
           "pm.expect(b.rows.length).to.eql(1); pm.expect(b.total).to.eql(2); pm.expect(b.more).to.eql(true); pm.expect(b.rows[0].Branch).to.eql('KHI'); });")
req("Report data as JSON (page 2)", "POST", f"/api/v1/reports/{REPORT}/data", auth="key",
    body={"parameters": {"Branch": "KHI"}, "limit": 1, "offset": 1},
    checks="pm.test('last page', function () { pm.expect(pm.response.json().more).to.eql(false); });")
req("Wrong parameter value is refused", "POST", f"/api/v1/reports/{REPORT}/run", auth="key", body={"format": "csv", "parameters": {"Branch": "XYZ"}},
    expect=(422,))
req("Start a background job", "POST", f"/api/v1/reports/{REPORT}/jobs", auth="key", body={"format": "xlsx", "parameters": {"Branch": "KHI"}},
    expect=(202,), save={"jobId": "pm.response.json().job.id"})
req("Job status", "GET", "/api/v1/jobs/{{jobId}}", auth="key")
req("Job file (ready, or still running)", "GET", "/api/v1/jobs/{{jobId}}/file", auth="key", expect=(200, 409))
req("Signed link for a browser", "POST", "/api/v1/links", auth="key", body={"report": REPORT, "format": "pdf", "parameters": {"Branch": "ISB"}, "expiresInSeconds": 300},
    save={"signedLink": "pm.response.json().url.substring(1)"})          # the URL starts with "/"
req("Open the signed link (no key)", "GET", "/{{signedLink}}", auth="none")
req("Public OpenAPI description", "GET", "/api/v1/openapi.json", auth="none")

folder("12b Embedding (a report inside the website's page)")
req("Allow a website to embed (Admin)", "PUT", "/api/admin/api-keys/{{apiKeyId}}", body={"embedOrigins": ["https://portal.example.com"]})
req("Embed token for the website's page (API key)", "POST", "/api/v1/embed-tokens", auth="key",
    body={"report": REPORT, "parameters": {"Branch": "LHR"}, "onBehalfOf": "postman-visitor", "expiresInSeconds": 600},
    save={"embedToken": "pm.response.json().token"})
req("Viewer page (framed only by the listed website)", "GET", "/embed/view?token={{embedToken}}", auth="none",
    checks="pm.test('framing limited to the website', function () { pm.expect(pm.response.headers.get('Content-Security-Policy')).to.include('frame-ancestors https://portal.example.com'); });")
for title, method, path, body, expect in (
        ("Viewer: report and its locked parameters", "GET", "/api/embed/report", None, (200,)),
        ("Viewer: preview (the fixed Branch cannot be changed)", "POST", "/api/embed/preview", {"parameters": {"Branch": "KHI"}}, (200,)),
        ("Viewer: download Excel", "POST", "/api/embed/run", {"format": "xlsx", "parameters": {}}, (200,))):
    req(title, method, path, body=body, auth="none", expect=expect)
    FOLDER[-1]["request"]["header"].append({"key": "X-Embed-Token", "value": "{{embedToken}}"})
FOLDER[3]["event"][0]["script"]["exec"].append(
    "pm.test('Branch is locked to LHR', function () { const b = pm.response.json().parameters.find(p => p.name === 'Branch'); pm.expect(b.locked).to.eql(true); pm.expect(b.value).to.eql('LHR'); });")
FOLDER[4]["event"][0]["script"]["exec"].append(
    "pm.test('only LHR rows', function () { pm.response.json().rows.forEach(r => pm.expect(r.Branch).to.eql('LHR')); });")
req("SDK script for the website", "GET", "/assets/embed/sdk.js", auth="none")

folder("12c Analytics (datasets, model, dashboards)",
       "Imports the demo orders into a dataset (DuckDB + Parquet), relates a second dataset, builds a dashboard, queries it "
       "with the session and with the API key, then removes everything. Datasets and the model need the Analytics right "
       "plus Designer or Admin.")


def power_bi_layout() -> dict:
    """The report part of a small Power BI file: one page with a column chart of Orders (sum of Amount by Branch)."""
    def column(name):
        return {"Column": {"Expression": {"SourceRef": {"Source": "o"}}, "Property": name}}
    single = {"visualType": "clusteredColumnChart", "projections": {"Category": [{"queryRef": "c"}], "Y": [{"queryRef": "y"}]},
              "prototypeQuery": {"Version": 2, "From": [{"Name": "o", "Entity": "Orders"}], "Select": [
                  {**column("Branch"), "Name": "c"}, {"Aggregation": {"Expression": column("Amount"), "Function": 0}, "Name": "y"}]}}
    layout = {"sections": [{"displayName": "Branches", "width": 1280, "height": 720, "visualContainers": [
        {"x": 0, "y": 0, "width": 1280, "height": 400, "config": json.dumps({"name": "c1", "singleVisual": single})}]}]}
    return layout


def power_bi_report_parts() -> dict:
    """What the browser sends for a .pbix: only its report part, as {name: base64}."""
    return {"Report/Layout": base64.b64encode(json.dumps(power_bi_layout()).encode("utf-16-le")).decode()}


def power_bi_template() -> str:
    """A small Power BI template (.pbit) as base64: one table Orders (Branch, Amount), one page with a column chart."""
    layout = power_bi_layout()
    model = {"model": {"tables": [{"name": "Orders", "columns": [{"name": "Branch", "dataType": "string"},
                                                                {"name": "Amount", "dataType": "double"}]}]}}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("DataModelSchema", json.dumps(model).encode("utf-16-le"))
        archive.writestr("Report/Layout", json.dumps(layout).encode("utf-16-le"))
    return base64.b64encode(out.getvalue()).decode()


ORDERS_SOURCE = '[{"$project": {"_id": 0, "Name": 1, "Branch": 1, "Amount": 1}}]'
BRANCH_SOURCE = '[{"$group": {"_id": "$Branch"}}, {"$project": {"_id": 0, "Code": "$_id"}}]'
SUM_BY_BRANCH = {"dataset": "{{dsOrders}}", "groupBy": [{"column": "Branch"}], "values": [{"column": "Amount", "agg": "sum"}],
                 "sort": [{"by": "value:0", "dir": "desc"}]}
req("Analytics: limits, visuals, aggregations", "GET", "/api/analytics/info",
    checks="pm.test('may build datasets', function () { pm.expect(pm.response.json().mayModel).to.eql(true); });")
req("Test a dataset query (first records, nothing saved)", "POST", "/api/analytics/datasets/test",
    body={"connectionId": "pm_mongo", "queryLanguage": "aggregation-json", "collection": "orders", "querySource": ORDERS_SOURCE},
    checks="pm.test('has columns', function () { pm.expect(pm.response.json().columns.length).to.be.above(0); });")
req("Create dataset PM orders (imports in the background)", "POST", "/api/analytics/datasets", expect=(201,),
    body={"name": "PM orders", "folder": "Postman", "connectionId": "pm_mongo", "queryLanguage": "aggregation-json",
          "collection": "orders", "querySource": ORDERS_SOURCE, "refreshEveryMinutes": 0},
    save={"dsOrders": "pm.response.json().dataset.id"})
req("Create dataset PM branches", "POST", "/api/analytics/datasets", expect=(201,),
    body={"name": "PM branches", "folder": "Postman", "connectionId": "pm_mongo", "queryLanguage": "aggregation-json",
          "collection": "orders", "querySource": BRANCH_SOURCE, "refreshEveryMinutes": 0},
    save={"dsBranches": "pm.response.json().dataset.id"})
req("Wait until both datasets are ready (repeats itself)", "GET", "/api/analytics/datasets",
    checks="const mine = pm.response.json().datasets.filter(d => [pm.environment.get('dsOrders'), pm.environment.get('dsBranches')].includes(d.id));\n"
           "const tries = Number(pm.environment.get('analyticsTries') || 0);\n"
           "if (mine.some(d => d.status !== 'ready' && d.status !== 'failed') && tries < 30) { pm.environment.set('analyticsTries', tries + 1); "
           "postman.setNextRequest(pm.info.requestName); }\n"
           "else { pm.environment.unset('analyticsTries'); pm.test('both imported', function () { mine.forEach(d => pm.expect(d.status).to.eql('ready')); }); }")
FOLDER[-1]["event"].append({"listen": "prerequest", "script": {"type": "text/javascript", "exec": ["setTimeout(function () {}, 1000);"]}})
req("One dataset with its columns", "GET", "/api/analytics/datasets/{{dsOrders}}")
req("Change a column's role and format", "PUT", "/api/analytics/datasets/{{dsOrders}}",
    body={"name": "PM orders", "folder": "Postman", "connectionId": "pm_mongo", "queryLanguage": "aggregation-json", "collection": "orders",
          "querySource": ORDERS_SOURCE, "refreshEveryMinutes": 0,
          "columns": [{"name": "Amount", "kind": "number", "role": "measure", "label": "Amount (PKR)", "format": "number"}]},
    checks="pm.test('source unchanged, no re-import', function () { pm.expect(pm.response.json().sourceChanged).to.eql(false); });")
req("First records of the imported data", "GET", "/api/analytics/datasets/{{dsOrders}}/rows?limit=5")
req("Import history", "GET", "/api/analytics/datasets/{{dsOrders}}/refreshes")
req("Relate orders.Branch to branches.Code", "POST", "/api/analytics/relationships", expect=(201,),
    body={"fromDataset": "{{dsOrders}}", "fromColumn": "Branch", "toDataset": "{{dsBranches}}", "toColumn": "Code"},
    save={"relationshipId": "pm.response.json().relationship.id"})
req("Relationships", "GET", "/api/analytics/relationships")
req("How two columns join, before saving", "POST", "/api/analytics/relationships/check",
    body={"fromDataset": "{{dsOrders}}", "fromColumn": "Branch", "toDataset": "{{dsBranches}}", "toColumn": "Code"})
req("Suggested relationships", "GET", "/api/analytics/relationships/suggest")
req("Change the relationship", "PUT", "/api/analytics/relationships/{{relationshipId}}", body={"active": True})
req("One visual: amount by branch", "POST", "/api/analytics/query", body=SUM_BY_BRANCH,
    checks="pm.test('rows by branch', function () { pm.expect(pm.response.json().rows.length).to.be.above(0); });")
req("A wrong column is refused", "POST", "/api/analytics/query",
    body={"dataset": "{{dsOrders}}", "groupBy": [{"column": "Nope"}], "values": [{"column": "Amount", "agg": "sum"}]}, expect=(422,))
req("Create dashboard PM sales", "POST", "/api/analytics/dashboards", expect=(201,),
    body={"name": "PM sales", "folder": "Postman", "tiles": [
        {"id": "kpi", "type": "kpi", "title": "Total", "x": 0, "y": 0, "w": 3, "h": 2, "dataset": "{{dsOrders}}", "values": [{"column": "Amount", "agg": "sum"}]},
        {"id": "col", "type": "column", "title": "By branch", "x": 3, "y": 0, "w": 9, "h": 5, "dataset": "{{dsOrders}}",
         "groupBy": [{"dataset": "{{dsBranches}}", "column": "Code"}], "values": [{"column": "Amount", "agg": "sum"}]}]},
    save={"dashboardId": "pm.response.json().dashboard.id"})
req("Dashboards", "GET", "/api/analytics/dashboards")
req("One dashboard", "GET", "/api/analytics/dashboards/{{dashboardId}}")
req("Rename the dashboard", "PUT", "/api/analytics/dashboards/{{dashboardId}}", body={"name": "PM sales (edited)", "folder": "Postman"})
req("All visuals of a dashboard, filtered (cross-filter from a click)", "POST", "/api/analytics/query-batch",
    body={"queries": [{"dataset": "{{dsOrders}}", "values": [{"column": "Amount", "agg": "sum"}]}, SUM_BY_BRANCH],
          "filters": [{"dataset": "{{dsOrders}}", "column": "Branch", "op": "in", "values": ["LHR"], "fromTile": "col"}]},
    checks="pm.test('two results', function () { pm.expect(pm.response.json().results.length).to.eql(2); });")
req("A dataset used by a dashboard is not deleted", "DELETE", "/api/analytics/datasets/{{dsBranches}}", expect=(409,))
req("Download the dashboard as one file (.mdash)", "GET", "/api/analytics/dashboards/{{dashboardId}}/file",
    save={"dashFile": "JSON.stringify(pm.response.text())"},
    checks="pm.test('dashboard file', function () { pm.expect(pm.response.json().format).to.eql('analysis-dashboard'); });")
req("Report Server: dashboards are listed with the reports", "GET", "/api/server/repository",
    checks="pm.test('lists PM sales', function () { pm.expect(pm.response.json().dashboards.map(d => d.id)).to.include(pm.environment.get('dashboardId')); });")
req("Report Server: open the dashboard (read-only)", "GET", "/api/server/dashboards/{{dashboardId}}")
req("Report Server: its visuals", "POST", "/api/server/dashboards/{{dashboardId}}/query-batch",
    body={"queries": [{"dataset": "{{dsOrders}}", "values": [{"column": "Amount", "agg": "sum"}]}]})
req("Report Server: download (.mdash)", "GET", "/api/server/dashboards/{{dashboardId}}/file")
req("Report Server: upload the file into Postman/Copies (its datasets are reused)", "POST", "/api/server/dashboards/upload", expect=(201,),
    raw_body='{"content": {{dashFile}}, "folder": "Postman/Copies"}', save={"dashboardCopyId": "pm.response.json().dashboard.id"},
    checks="pm.test('datasets reused', function () { pm.expect(pm.response.json().reused.length).to.eql(2); });")
req("Report Server: move the copy", "POST", "/api/server/dashboards/{{dashboardCopyId}}/move", body={"folder": "Postman"})
req("Report Server: delete the copy", "DELETE", "/api/server/dashboards/{{dashboardCopyId}}")
req("Analytics: import the file again (modellers)", "POST", "/api/analytics/dashboards/import", expect=(201,),
    raw_body='{"content": {{dashFile}}, "folder": "Postman"}', save={"dashboardCopyId": "pm.response.json().dashboard.id"})
req("Delete the imported copy", "DELETE", "/api/analytics/dashboards/{{dashboardCopyId}}")
req("Power BI template (.pbit): its pages, visuals and tables", "POST", "/api/analytics/powerbi/inspect",
    body={"content": power_bi_template(), "name": "PM sales.pbit"},
    checks="pm.test('one page, one table', function () { var b = pm.response.json(); pm.expect(b.pages[0].visuals).to.eql(1); "
           "pm.expect(b.tables[0].name).to.eql('Orders'); });")
req("Power BI report (.pbix): its report part only", "POST", "/api/analytics/powerbi/inspect",
    body={"parts": power_bi_report_parts(), "name": "PM sales.pbix"},
    checks="pm.test('report part read', function () { var b = pm.response.json(); pm.expect(b.kind).to.eql('pbix'); "
           "pm.expect(b.tables[0].columns.map(c => c.name)).to.include('Amount'); });")
req("Power BI template: import its page as a dashboard on PM orders", "POST", "/api/analytics/powerbi/import", expect=(201,),
    body={"content": power_bi_template(), "name": "PM sales.pbit", "folder": "Postman", "pages": [0],
          "tables": {"Orders": {"dataset": "{{dsOrders}}"}}},
    save={"dashboardCopyId": "pm.response.json().dashboards[0].id"},
    checks="pm.test('dataset reused', function () { pm.expect(pm.response.json().reused).to.include(pm.environment.get('dsOrders')); });")
req("Delete the dashboard from Power BI", "DELETE", "/api/analytics/dashboards/{{dashboardCopyId}}")
req("Public API: datasets in the key's folders", "GET", "/api/v1/analytics/datasets", auth="key",
    checks="pm.test('sees PM orders', function () { pm.expect(pm.response.json().datasets.map(d => d.name)).to.include('PM orders'); });")
req("Public API: dashboards", "GET", "/api/v1/analytics/dashboards", auth="key")
req("Public API: one dashboard (tiles to draw it yourself)", "GET", "/api/v1/analytics/dashboards/{{dashboardId}}", auth="key")
req("Public API: one visual", "POST", "/api/v1/analytics/query", auth="key", body=SUM_BY_BRANCH)
req("Public API: import again now", "POST", "/api/v1/analytics/datasets/{{dsOrders}}/refresh", auth="key", expect=(202,))
req("Import again now (session)", "POST", "/api/analytics/datasets/{{dsBranches}}/refresh", expect=(202,))
req("Query in DAX (total by branch)", "POST", "/api/analytics/queries/run",
    body={"language": "dax", "text": "EVALUATE SUMMARIZECOLUMNS('PM orders'[Branch], \"Total\", SUM('PM orders'[Amount]))\nORDER BY [Total] DESC"},
    checks="pm.test('rows per branch', function () { pm.expect(pm.response.json().columns.map(c => c.name)).to.eql(['Branch', 'Total']); });")
req("Query in SQL (DuckDB SELECT)", "POST", "/api/analytics/queries/run",
    body={"language": "sql", "text": "SELECT Branch, COUNT(*) AS n FROM \"PM orders\" GROUP BY Branch"})
req("Query reading a file is refused", "POST", "/api/analytics/queries/run",
    body={"language": "sql", "text": "SELECT * FROM read_csv('/etc/passwd')"}, expect=(422,))
req("Save the query", "POST", "/api/analytics/queries",
    body={"name": "PM totals", "folder": "Postman", "language": "dax", "text": "EVALUATE ROW(\"Total\", SUM('PM orders'[Amount]))"},
    save={"queryId": "pm.response.json().query.id"})
req("Saved queries", "GET", "/api/analytics/queries")
req("One saved query", "GET", "/api/analytics/queries/{{queryId}}")
req("Run the saved query", "POST", "/api/analytics/queries/{{queryId}}/run", body={})
req("Rename the saved query", "PUT", "/api/analytics/queries/{{queryId}}", body={"name": "PM totals (all)"})
req("Delete the saved query", "DELETE", "/api/analytics/queries/{{queryId}}")
req("Admin: every dataset and dashboard, disk use, limits", "GET", "/api/admin/analytics",
    checks="pm.test('has totals', function () { pm.expect(pm.response.json().totals.datasets).to.be.above(0); });")
req("Admin: import a dataset now", "POST", "/api/admin/analytics/datasets/{{dsBranches}}/refresh", expect=(202,))
req("Admin: a schedule shorter than the minimum is refused", "PUT", "/api/admin/analytics/datasets/{{dsOrders}}/schedule",
    body={"refreshEveryMinutes": 1}, expect=(422,))
req("Admin: import every day", "PUT", "/api/admin/analytics/datasets/{{dsOrders}}/schedule", body={"refreshEveryMinutes": 1440})
req("Admin: give the dashboard a new owner", "PUT", "/api/admin/analytics/dashboards/{{dashboardId}}/owner", body={"owner": "pm_runner"})
req("Admin: remove leftover files", "POST", "/api/admin/analytics/cleanup", body={})
req("Delete dashboard", "DELETE", "/api/analytics/dashboards/{{dashboardId}}")
req("Delete relationship", "DELETE", "/api/analytics/relationships/{{relationshipId}}")
req("Admin: delete dataset PM branches", "DELETE", "/api/admin/analytics/datasets/{{dsBranches}}?force=true")
req("Delete dataset PM orders", "DELETE", "/api/analytics/datasets/{{dsOrders}}?force=true")

folder("12d Many downloads at once (CSV, Excel, PDF)",
       "Load test. Run this folder with the Collection Runner and set Iterations (e.g. 20) to hit the server that many times; "
       "or open one request and use Send repeatedly. 'Run now' requests wait for the file. 'Deliver later' requests "
       "(header X-Deliver: later) are answered at once with 202 and a ticket; the server makes the file in the background, "
       "queueing when every place is busy, and you collect it from /api/deliveries/{id}/file. Works on /generate, "
       "/api/v1/reports/{id}/run, /api/server/reports/{id}/run, /api/admin/reports/{id}/run and the designer's runs.")
LATER = {"X-Deliver": "later"}
for fmt, label in (("csv", "CSV"), ("xlsx", "Excel"), ("pdf", "PDF")):
    req(f"Run now: {label} (file in the answer)", "POST", f"/api/v1/reports/{REPORT}/run", auth="key",
        body={"format": fmt, "parameters": {"Branch": "KHI"}},
        checks=f"pm.test('a {label} file', function () {{ pm.expect(pm.response.headers.get('Content-Disposition')).to.include('.{fmt}'); }});")
req("Run now: CSV by URL", "GET", f"/generate/{REPORT}.csv?Branch=LHR", auth="key")
for fmt, label in (("csv", "CSV"), ("xlsx", "Excel"), ("pdf", "PDF")):
    req(f"Deliver later: {label} (202 + ticket)", "POST", f"/api/v1/reports/{REPORT}/run", auth="key", extra_headers=LATER,
        body={"format": fmt, "parameters": {"Branch": "KHI"}}, expect=(202,),
        save={f"delivery_{fmt}": "pm.response.json().delivery.id"})
req("Deliver later: Excel by URL", "GET", f"/generate/{REPORT}.xlsx?Branch=ISB", auth="key", extra_headers=LATER, expect=(202,),
    save={"delivery_url": "pm.response.json().delivery.id"})
req("Deliver later as the signed-in user (Report Server Run)", "POST", f"/api/server/reports/{REPORT}/run", extra_headers=LATER,
    body={"format": "xlsx", "parameters": {"Branch": "KHI"}}, expect=(202,), save={"delivery_session": "pm.response.json().delivery.id"})
req("Wait until the key's downloads are ready (repeats itself)", "GET", "/api/deliveries", auth="key",
    checks="const items = pm.response.json().deliveries;\n"
           "const tries = Number(pm.environment.get('deliveryTries') || 0);\n"
           "if (items.some(d => d.status === 'queued' || d.status === 'running') && tries < 60) { pm.environment.set('deliveryTries', tries + 1); "
           "postman.setNextRequest(pm.info.requestName); }\n"
           "else { pm.environment.unset('deliveryTries'); pm.test('all made', function () { items.forEach(d => pm.expect(d.status).to.eql('ready')); }); }")
FOLDER[-1]["event"].append({"listen": "prerequest", "script": {"type": "text/javascript", "exec": ["setTimeout(function () {}, 500);"]}})
for fmt, label in (("csv", "CSV"), ("xlsx", "Excel"), ("pdf", "PDF")):
    req(f"Collect the {label} file", "GET", f"/api/deliveries/{{{{delivery_{fmt}}}}}/file", auth="key", expect=(200, 409))
req("Collect the Excel file asked for by URL", "GET", "/api/deliveries/{{delivery_url}}/file", auth="key", expect=(200, 409))
req("One download's status (signed-in user)", "GET", "/api/deliveries/{{delivery_session}}")
req("Someone else cannot collect it (the key is not the session)", "GET", "/api/deliveries/{{delivery_session}}/file", auth="key", expect=(404,))
req("Forget a download (removes its file)", "DELETE", "/api/deliveries/{{delivery_url}}", auth="key", expect=(200, 404))
req("Capacity after the burst", "GET", "/api/admin/capacity")

folder("13 Logs, audit and system (Admin)")
req("System database (where the service keeps its data)", "GET", "/api/admin/system-db",
    checks="pm.test('names the database', function () { pm.expect(pm.response.json().label).to.be.a('string'); });")
req("System database: a wrong choice is refused", "POST", "/api/admin/system-db/test", body={"kind": "excel"}, expect=(422,))
req("Postman collection for this server", "GET", "/api/admin/postman/collection.json")
req("Postman environment for this server", "GET", "/api/admin/postman/environment.json",
    checks="pm.test('base URL filled in', function () { pm.expect(pm.response.json().values.find(v => v.key === 'baseUrl').value).to.eql(pm.environment.get('baseUrl')); });")
req("Product name (public: sign-in page, navigation bar)", "GET", "/api/branding", auth="none",
    checks="pm.test('has a name', function () { pm.expect(pm.response.json().name).to.be.a('string'); });")
req("Product name: a wrong name is refused (super admin)", "PUT", "/api/admin/branding", body={"name": "<x>", "tagline": ""}, expect=(422,))
req("Capacity: report places in use, background queue, limits", "GET", "/api/admin/capacity",
    checks="pm.test('has limits', function () { pm.expect(pm.response.json().limits.maxConcurrent).to.be.above(0); });")
req("Report Server logs: every request today (system database)", "GET", "/api/server/logs/requests?limit=50",
    checks="pm.test('requests are recorded', function () { pm.expect(pm.response.json().totals.requests).to.be.above(0); });")
req("Report Server logs: only the errors", "GET", "/api/server/logs/requests?status=errors&limit=50")
req("Report Server logs: report runs", "GET", "/api/server/logs/runs?limit=50")
req("Report Server logs: service log", "GET", "/api/server/logs/service?lines=50&level=WARNING")
req("Report Server logs: audit trail", "GET", "/api/server/logs/audit?limit=50")
req("Report Server logs: data taken out through API calls", "GET", "/api/server/logs/api-calls?limit=50",
    checks="pm.test('API calls are recorded', function () { pm.expect(pm.response.json().totals.calls).to.be.above(0); });")
req("Report Server logs: downloads and capacity right now", "GET", "/api/server/logs/downloads")
req("Service log", "GET", "/api/admin/logs?lines=50")
req("Download service log", "GET", "/api/admin/logs/download")
req("Report runs", "GET", "/api/admin/audit?limit=50")
req("Audit trail", "GET", "/api/admin/audit-events?limit=50")
req("Verify the audit chain", "GET", "/api/admin/audit-events/verify",
    checks="pm.test('chain intact', function () { pm.expect(pm.response.json().ok).to.eql(true); });")

folder("14 Clean up")
req("Delete schedule", "DELETE", "/api/server/schedules/{{scheduleId}}")
req("Delete background run", "DELETE", "/api/server/runs/{{serverRunId}}", expect=(200, 409))
req("Delete uploaded report", "DELETE", "/api/server/reports/PM_UPLOADED")
req("Delete copied report", "DELETE", "/api/server/reports/PM_ORDERS_COPY")
req("Delete PM_ADMIN", "DELETE", "/api/admin/reports/PM_ADMIN")
req("Delete draft", "DELETE", f"/api/admin/workspace/{REPORT}")
req("Delete workspace folder", "DELETE", "/api/admin/workspace-folders", body={"path": "Postman"}, expect=(200, 409))
req("Move the data adapter out of the folder", "PUT", "/api/admin/connections/pm_mongo/folder", body={"folder": ""})
req("Delete server folder with its reports", "DELETE", "/api/server/folders", body={"path": "Postman", "recursive": True})
req("Delete admin folder (already gone)", "DELETE", "/api/admin/folders", body={"path": "Postman", "recursive": True}, expect=(200, 404))
req("Delete API key", "DELETE", "/api/admin/api-keys/{{apiKeyId}}")
req("Delete team", "DELETE", "/api/admin/user-groups/{{groupId}}")
req("Delete user", "DELETE", "/api/admin/users/pm_runner")
req("Delete data adapter", "DELETE", "/api/admin/connections/pm_mongo")
req("Sign out", "POST", "/api/server/logout", body={})


def main() -> None:
    OUT.mkdir(exist_ok=True)
    collection = {
        "info": {"_postman_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "mongo-reporting-service")), "name": "Analysis — all APIs",
                 "description": __doc__, "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
        "item": ITEMS,
    }
    (OUT / "Reporting-Service.postman_collection.json").write_text(json.dumps(collection, indent=2, ensure_ascii=False), encoding="utf-8")
    environment = {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, "mongo-reporting-local")), "name": "Analysis — local",
                   "values": [{"key": key, "value": value, "type": kind, "enabled": True} for key, value, kind in (
                       ("baseUrl", "http://127.0.0.1:8000", "default"), ("username", "admin", "default"),
                       ("password", "", "secret"), ("adminToken", "", "secret"),
                       ("mongoUri", "mongodb://127.0.0.1:27017", "default"), ("apiKey", "", "secret"), ("apiKeyId", "", "default"))],
                   "_postman_variable_scope": "environment"}
    (OUT / "Local.postman_environment.json").write_text(json.dumps(environment, indent=2), encoding="utf-8")
    count = sum(len(group["item"]) for group in ITEMS)
    print(f"{count} requests in {len(ITEMS)} folders -> {OUT}")


if __name__ == "__main__":
    main()
