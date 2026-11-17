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

import json
import uuid
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

ITEMS: list[dict] = []
FOLDER: list = []


def folder(name: str, description: str = "") -> None:
    global FOLDER
    FOLDER = []
    ITEMS.append({"name": name, "description": description, "item": FOLDER})


def req(name: str, method: str, path: str, *, body=None, raw_body: str | None = None, expect=(200,), auth: str = "session",
        save: dict | None = None, checks: str = "", redirects: bool = True, description: str = "") -> None:
    """auth: session (the signed-in cookie) | admin (X-Admin-Token) | key (X-API-Key) | none."""
    headers = []
    if auth == "admin":
        headers.append({"key": "X-Admin-Token", "value": "{{adminToken}}"})
    elif auth == "key":
        headers.append({"key": "X-API-Key", "value": "{{apiKey}}"})
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
req("Public API refuses without a key", "GET", "/api/v1/reports", auth="none", expect=(401,))

folder("02 Sign in", "Signs in with {{username}} / {{password}}; Postman keeps the session cookie for the next requests.")
req("Sign in", "POST", "/api/server/login", body={"username": "{{username}}", "password": "{{password}}"},
    checks="pm.test('has rights', function () { pm.expect(pm.response.json().rights).to.be.an('array'); });")
req("Who am I (session)", "GET", "/api/server/me")
req("Home sends to the first allowed page", "GET", "/", expect=(303,), redirects=False)
for page in ("/designer", "/server", "/admin", "/ui", "/analytics"):
    req(f"Page {page}", "GET", page)
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
req("Create workspace folder", "POST", "/api/admin/workspace-folders", body={"path": "Postman"})
req("Remove a draft left by an earlier run", "DELETE", f"/api/admin/workspace/{REPORT}", expect=(200, 404))
req("Save draft PM_ORDERS", "PUT", f"/api/admin/workspace/{REPORT}?create=true", body=definition())
req("List drafts", "GET", "/api/admin/workspace")
req("Get draft", "GET", f"/api/admin/workspace/{REPORT}")
req("Preview draft (20 rows)", "POST", "/api/admin/drafts/preview",
    body={"definition": definition(), "parameters": {"Branch": "LHR"}, "connectionId": "pm_mongo"},
    checks="pm.test('two LHR rows', function () { pm.expect(pm.response.json().rowCount).to.eql(2); });")
req("Run draft to CSV", "POST", "/api/admin/drafts/run",
    body={"definition": definition(), "parameters": {"Branch": "LHR"}, "connectionId": "pm_mongo", "format": "csv"})
req("Publish draft to the server", "POST", f"/api/admin/workspace/{REPORT}/publish", body={"folder": "Postman", "replace": True})

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
req("Create user pm_runner", "POST", "/api/admin/users", body={"username": "pm_runner", "password": "Postman-pass-2026", "rights": ["runner"]})
req("List users", "GET", "/api/admin/users")
req("Change rights", "PUT", "/api/admin/users/pm_runner", body={"rights": ["runner", "server"]})

folder("08 Report Server")
req("Repository", "GET", "/api/server/repository")
req("Folder contents", "GET", "/api/server/folders/contents?path=Postman")
req("Create subfolder", "POST", "/api/server/folders", body={"path": "Postman/Sub"})
req("Report details", "GET", f"/api/server/reports/{REPORT}")
req("Edit title", "PUT", f"/api/server/reports/{REPORT}", body={"title": "Postman Orders (server edit)"})
req("Versions", "GET", f"/api/server/reports/{REPORT}/versions", save={"versionNumber": "pm.response.json().versions[pm.response.json().versions.length - 1].version"})
req("One version", "GET", f"/api/server/reports/{REPORT}/versions/{{{{versionNumber}}}}")
req("Restore the first version", "POST", f"/api/server/reports/{REPORT}/versions/{{{{versionNumber}}}}/restore", body={})
req("Copy", "POST", f"/api/server/reports/{REPORT}/copy", body={"folder": "Postman/Sub", "id": "PM_ORDERS_COPY"})
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

folder("09 Schedules")
req("Schedule options", "GET", "/api/server/schedules/info")
req("Create schedule", "POST", "/api/server/schedules",
    body={"label": "PM daily", "reportId": REPORT, "format": "csv", "parameters": {"Branch": "LHR"},
          "recurrence": {"type": "daily", "time": "08:00"}, "timezone": "Asia/Karachi", "keep": 5},
    save={"scheduleId": "pm.response.json().schedule.id"})
req("List schedules", "GET", "/api/server/schedules")
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

folder("12 Public API v1 (API key)")
req("Who am I (key)", "GET", "/api/v1/whoami", auth="key",
    checks="pm.test('is the Postman key', function () { pm.expect(pm.response.json().name).to.eql('Postman site'); });")
req("Reports the key may run", "GET", "/api/v1/reports", auth="key")
req("One report with parameters", "GET", f"/api/v1/reports/{REPORT}", auth="key")
req("Run now (JSON in, file out)", "POST", f"/api/v1/reports/{REPORT}/run", auth="key",
    body={"format": "csv", "parameters": {"Branch": "LHR", "CNIC": "4220112345671"}, "onBehalfOf": "postman-user"})
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

folder("13 Logs and audit (Admin)")
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
req("Delete user", "DELETE", "/api/admin/users/pm_runner")
req("Delete data adapter", "DELETE", "/api/admin/connections/pm_mongo")
req("Sign out", "POST", "/api/server/logout", body={})


def main() -> None:
    OUT.mkdir(exist_ok=True)
    collection = {
        "info": {"_postman_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "mongo-reporting-service")), "name": "Reporting Service — all APIs",
                 "description": __doc__, "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
        "item": ITEMS,
    }
    (OUT / "Reporting-Service.postman_collection.json").write_text(json.dumps(collection, indent=2, ensure_ascii=False), encoding="utf-8")
    environment = {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, "mongo-reporting-local")), "name": "Reporting Service — local",
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
