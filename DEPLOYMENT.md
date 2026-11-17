# Deploying the MongoDB Reporting Service

This guide covers running the service on a server: which paths to expose and to whom, logs, and how a
Java/Grizzly application connects to it. All settings are environment variables; `.env.example` lists
every one with a comment.

## 1. Paths and who needs them

| Path | What it is | Who should reach it | Protected by |
|---|---|---|---|
| `/generate/<ID>.<pdf\|xlsx\|csv>` | Runs a report, returns the file | Your website / Java server (and users through it) | Signed link, `X-Report-Token`, or admin token |
| `/reports` | List of reports and their questions | Your website / Java server, the `/ui` page | Same as `/generate` |
| `/reports/connections/<id>/status` | Is a report's data adapter reachable (used by `/ui`) | The `/ui` page | Same as `/generate` |
| `/health` | Liveness check for load balancers | Load balancer / monitoring | Open (shows only status and database name) |
| `/ui`, `/assets/*` | Report runner page | End users (usually through your app or proxy) | Page is open; its data calls need report access |
| `/designer` | Report designer | Report designers only | `REPORTING_ADMIN_TOKEN` or `REPORTING_DESIGNER_TOKEN` |
| `/admin` | Management page (data sources, logs) | Administrators only | `REPORTING_ADMIN_TOKEN` |
| `/api/admin/*` | API behind `/admin` and `/designer` | Administrators and designers | Admin or designer token (per endpoint) |
| `/docs`, `/redoc`, `/openapi.json` | API documentation | Developers only | Switch off in production |

### Roles

* **Admin token** (`REPORTING_ADMIN_TOKEN`): everything, including data sources (MongoDB URLs), deleting
  reports and folders, and reading logs.
* **Designer token** (`REPORTING_DESIGNER_TOKEN`, at least 24 characters): open `/designer`, create and save
  reports, preview and run them on any data adapter, import JRXML. Cannot add/change/delete data sources,
  delete reports or folders, or read logs (these answer `403`).
* **Report access** (`REPORTING_ACCESS_SECRET`): only `/generate` and `/reports` — for your website/Java server.

### Switching areas off per instance

`REPORTING_ENABLED_AREAS` decides which areas this instance serves (`runner`, `admin`, `designer`, `docs`).
A switched-off area answers `404`, as if it did not exist. `/generate`, `/reports` and `/health` are always on.

| Instance | Setting |
|---|---|
| Internal all-in-one | `REPORTING_ENABLED_AREAS=runner,admin,designer` |
| Public reporting only (website calls `/generate`) | `REPORTING_ENABLED_AREAS=runner` |
| Designers' instance (no data-source or log access at all) | `REPORTING_ENABLED_AREAS=designer` |

### Restricting management paths to your network

`REPORTING_ADMIN_ALLOWED_IPS=10.0.0.0/8,192.168.1.0/24` lets only those addresses open `/admin`,
`/designer` and `/api/admin/*` (others get `403`). Behind a reverse proxy, either let uvicorn read the
real client IP (it trusts `X-Forwarded-For` from `127.0.0.1` by default), or set
`REPORTING_TRUSTED_PROXIES` to the proxy's address.

You can also enforce the same rules in the reverse proxy. nginx example:

```nginx
server {
    listen 443 ssl;
    server_name reports.example.com;

    # management: office network only
    location ~ ^/(admin|designer|api/admin/) {
        allow 10.0.0.0/8;
        deny all;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
    # API docs: not in production
    location ~ ^/(docs|redoc|openapi.json) { deny all; }

    # everything else: runner, /reports, /generate, /health
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;
    }
}
```

## 2. Server checklist

1. **Python 3.11+** and `pip install -r requirements.txt`.
2. **Build the designer** once (it is already built into `ui/app`; one React build serves /designer, /server and /admin; rebuild after changing `frontend/`):
   `cd frontend && npm ci && npm run build`.
3. **Data folder**: set `REPORTING_DATA_DIR` to a persistent, writable folder (not inside the code folder
   if the code is replaced on deploy). It holds reports, folders, data adapters, `private_secrets.json`
   (MongoDB URLs), logs and the run history. Back it up.
4. **Move your reports**: copy `data/reports/`, `data/folders.json` and `data/connections.json` to the
   server's data folder. Re-enter each data adapter's MongoDB URL on `/admin → Data sources` (or copy
   `data/private_secrets.json` over a secure channel). Never commit `private_secrets.json` or `.env`.
5. **Secrets** in the server's environment (not in files in the code folder):
   `REPORTING_ADMIN_TOKEN`, `REPORTING_DESIGNER_TOKEN` (optional), `REPORTING_ACCESS_SECRET`.
   Generate each with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
6. **Accounts and rights**: sign in at `/login` as `admin` with the admin token (first setup only), then create
   one or more **super admins** in Admin → Users & rights. Give every other account only the rights it needs:
   Admin, Designer, Report Server, Analytics, Run reports. Pages and APIs check these rights; the admin token is
   for automation only and is never handed to a browser.
7. **Start with one worker**:
   `python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1`
   (`app:app` still works: the root `app.py` imports the same app.)
   Saved reports and data adapters are cached inside the process; several workers would not see each
   other's changes. Put nginx/IIS in front for HTTPS.
8. **Check**: `GET /health` → `{"status": "ok", ...}`; sign in at `/login`, open `/admin`; check the Logs tab.
9. **Client websites** (run reports by URL): create one key per website in Admin → API keys (limit it to
   folders and the website server's IP). The website's server calls
   `GET /generate/<ID>.pdf?Param=…` with `X-API-Key: mrk_…`, or `POST /api/v1/links` for a short-lived signed URL
   to give a browser. The key must never be put in a web page.

On Windows, run the start command as a service (for example with NSSM or the Task Scheduler "at startup").
A `Dockerfile` is included: mount a volume at `/data` and pass the secrets as environment variables:

```bash
docker build -t mongo-reporting .
docker run -d -p 8000:8000 -v reporting-data:/data -e REPORTING_ADMIN_TOKEN=... -e REPORTING_ACCESS_SECRET=... -e REPORTING_ENABLED_AREAS=runner,admin,designer mongo-reporting
```

Note: starter reports bundled in the image are copied into `/data` only when they are missing there, so a
bundled report you delete comes back on the next start. Remove it from the image as well.

## 3. Repository folders and data adapters

`/admin` (Reports library) and `/designer` (Server tab) show the same tree as Jaspersoft Studio's
Repository Explorer: **Data adapters** at the top level, then folders inside folders (for example
`cascade / KMBLGL / Data Source`, `Reports`, `Images`). A data adapter can be saved at the top level or inside
any folder, and moved later without re-entering its URL (`PUT /api/admin/connections/<id>/folder`, admin
token). The folder only organises the adapter; its ID stays unique on the server, and a report or run can use
any adapter wherever it is. A folder that still holds reports, adapters or subfolders cannot be deleted.

No data adapter is preselected for Preview or Run: pick any one each time. The adapter saved on the report is
used only by `/generate` and `/ui`.

## 4. Logs

* **Service log**: `<REPORTING_DATA_DIR>/logs/service.log`, rotated at 5 MB, 5 old files kept
  (`REPORTING_LOG_FILE`, `REPORTING_LOG_MAX_BYTES`, `REPORTING_LOG_BACKUPS`). One line per request
  (method, path, status, time, client IP) plus warnings and errors with tracebacks. Query strings,
  headers, tokens and MongoDB URLs are never written.
* **Report run history**: `<REPORTING_DATA_DIR>/audit/report-executions.jsonl` — every `/generate` and
  every Run from `/admin` or `/designer`: report, format, parameters, rows, duration, data adapter, status, error.
* **Viewing**: `/admin → Logs` (admin token only) shows both, with level filter, search, auto-refresh
  and download. The same data is at `GET /api/admin/logs`, `/api/admin/logs/download` and `/api/admin/audit`.
* Set `REPORTING_LOG_TO_FILE=false` when the platform collects stdout instead (the console log is unchanged).

## 5. Connecting from a Java / Grizzly application

Grizzly is an HTTP server. There are two ways to use this service from it.

### A. Your Java app shows its own screens and calls `/generate` (recommended)

Your Java server asks this service for the file and passes it to the user's browser. The browser never
sees a token. Add the logged-in user as `GeneratedBy` so the run history shows who asked.

```java
// Server-to-server: X-Report-Token header
HttpClient http = HttpClient.newHttpClient();
String query = "StartDate=2025-10-01&EndDate=2025-10-31&GeneratedBy=" + URLEncoder.encode(userName, UTF_8);
HttpRequest request = HttpRequest.newBuilder(URI.create("http://reports-host:8000/generate/CUU_TSQ_Performance.pdf?" + query))
        .header("X-Report-Token", System.getenv("REPORTING_ACCESS_SECRET"))
        .GET().build();
HttpResponse<byte[]> response = http.send(request, HttpResponse.BodyHandlers.ofByteArray());
// response.statusCode() == 200 -> write response.body() to the user with Content-Type application/pdf
```

Or give the browser a **signed link** that expires (the browser downloads directly from this service):

```java
String path = "/generate/CUU_TSQ_Performance.pdf";
long expires = Instant.now().getEpochSecond() + 300;               // valid 5 minutes (max REPORTING_SIGNED_LINK_MAX_SECONDS)
String query = "StartDate=2025-10-01&EndDate=2025-10-31&Expires=" + expires;
Mac mac = Mac.getInstance("HmacSHA256");
mac.init(new SecretKeySpec(System.getenv("REPORTING_ACCESS_SECRET").getBytes(UTF_8), "HmacSHA256"));
String signature = HexFormat.of().formatHex(mac.doFinal((path + "?" + query).getBytes(UTF_8)));
String link = "https://reports.example.com" + path + "?" + query + "&Signature=" + signature;
```

The signature covers the path and the query exactly as sent, with `Signature` last.

### B. Show the ready-made `/ui` runner page inside your Java app

A browser has to load `/ui` itself, so Grizzly must pass those requests on (reverse proxy) and add the
report token on the server side, after your own login check. The runner page then works without any token
in the browser. Forward only the runner paths: `/ui`, `/assets/*`, `/reports`, `/reports/*`, `/generate/*`.

```java
/** Grizzly handler that forwards the runner page to the reporting service for logged-in users. */
public class ReportsProxy extends HttpHandler {
    private final HttpClient http = HttpClient.newHttpClient();
    private final String upstream = "http://127.0.0.1:8000";
    private final String reportToken = System.getenv("REPORTING_ACCESS_SECRET");

    @Override
    public void service(Request request, Response response) throws Exception {
        Session session = request.getSession(false);
        if (session == null || session.getAttribute("user") == null) { response.sendError(401); return; }  // your login
        String path = request.getRequestURI();
        boolean allowed = path.equals("/ui") || path.startsWith("/assets/") || path.equals("/reports")
                || path.startsWith("/reports/") || path.startsWith("/generate/");
        if (!allowed) { response.sendError(404); return; }
        String query = request.getQueryString();
        HttpRequest upstreamRequest = HttpRequest.newBuilder(URI.create(upstream + path + (query == null ? "" : "?" + query)))
                .header("X-Report-Token", reportToken).GET().build();
        HttpResponse<InputStream> answer = http.send(upstreamRequest, HttpResponse.BodyHandlers.ofInputStream());
        response.setStatus(answer.statusCode());
        answer.headers().firstValue("content-type").ifPresent(response::setContentType);
        answer.headers().firstValue("content-disposition").ifPresent(value -> response.setHeader("Content-Disposition", value));
        try (InputStream in = answer.body(); OutputStream out = response.getOutputStream()) { in.transferTo(out); }
    }
}

// registration
server.getServerConfiguration().addHttpHandler(new ReportsProxy(), "/ui", "/assets/*", "/reports", "/reports/*", "/generate/*");
```

Prefer a per-website **API key** over the shared report token: create it in Admin → API keys (limit it to the
folders the website may run and to the Grizzly server's IP) and send `.header("X-API-Key", apiKey)` instead of
`X-Report-Token`. Disabling the key cuts that website off at once, and its runs are logged under the key's name.
The proxied `/ui` page opens for the website's visitors because the proxy adds the key; on the reporting
server itself `/ui` needs a signed-in user with the Run reports right.

With this setup, run the reporting service with `REPORTING_ENABLED_AREAS=runner` on an address only Grizzly
can reach (for example `127.0.0.1:8000`), and keep `/admin` and `/designer` on a separate internal instance.
If your pages are on the same domain behind nginx, you can also link to `/ui` or show it in an `<iframe>`.

These Java snippets are examples for your Grizzly code and were not compiled here (no JDK on this machine).

## 6. Public API v1 (for client websites and other systems)

Create one API key per website in **Admin → API keys** (limit it to folders and the website server's IP; optional
webhook URL and requests per minute). The key stays on the website's server: `X-API-Key: mrk_…`.

| Call | What it does |
|---|---|
| `GET /api/v1/whoami` | Checks the key: name, folders, rate limit |
| `GET /api/v1/reports`, `GET /api/v1/reports/{id}` | Reports the key may run, with their parameters (type, required, allowed values) |
| `POST /api/v1/reports/{id}/run` | Runs now: `{"format": "pdf", "parameters": {"Identifier": "123"}, "onBehalfOf": "ali"}` → the file |
| `POST /api/v1/reports/{id}/jobs` | Runs in the background (202 + `Location`); at most 5 waiting/running per key |
| `GET /api/v1/jobs/{id}`, `GET /api/v1/jobs/{id}/file` | Status, then the file |
| `POST /api/v1/links` | A short-lived signed URL for a browser (parameters cannot be changed) |
| `GET /api/v1/openapi.json` | This API as OpenAPI 3 (always on, no data) |

* **Webhook**: when a background job of the key finishes, its webhook URL gets `POST` `{"event": "job.finished",
  "job": {…, "downloadUrl"}}` with `X-Reporting-Timestamp` and `X-Reporting-Signature: sha256=HMAC(secret,
  "<timestamp>.<body>")`. The secret is shown once in Admin. Internal addresses are refused unless
  `REPORTING_WEBHOOK_ALLOW_PRIVATE=true`; redirects are not followed.
* **Rate limit**: `REPORTING_API_RATE_PER_MINUTE` (default 120, `0` = off) or per key; over it → `429` with `Retry-After`.
* Every call is in the run log (channel `api`, actor = the key's name, `onBehalfOf` = the website's own user).
* **Postman**: `postman/` has a collection for every endpoint and how to run it (see `postman/README.md`).

## Docker

The image is built from a clean checkout; it builds the React pages itself and contains **no** `data/`
(reports, adapters, users and secrets live on the `/data` volume).

```bash
docker build -t mongo-reporting .
docker run -d --name reporting -p 8000:8000 -v reporting-data:/data --env-file .env mongo-reporting
```

* The `default` data adapter comes from `MONGODB_URI` / `MONGODB_DATABASE`; add more on `/admin → Data sources`.
* The container's health check only tests that the process answers (`backend/app/healthcheck.py`). Use the JSON of
  `GET /health` (status `ok` / `unavailable`) for readiness and alerting about MongoDB.
* `GET /ready` answers 200 when the storage works (files or a database) — use it for the load balancer.
* With `REPORTING_STORAGE=files` keep one instance and `--workers 1`. For several instances use a database
  (below): the one the client already runs.

## Where the service keeps its own data: files, any SQL database or MongoDB

The service's own data (reports, drafts, folders, users and rights, API keys, versions, schedules, outputs, the
**run log** and the **audit trail**) can live in whichever database the client already has. Report data adapters
are separate: they only read the client's business data.

| `REPORTING_STORAGE` | `REPORTING_STORAGE_URL` | Notes |
|---|---|---|
| `files` (default) | — | The data folder. One instance. |
| `sql` | `sqlserver://user:pw@host:1433/reporting` | SQL Server 2016+ |
| `sql` | `oracle://user:pw@host:1521/?service_name=ORCLPDB1` | Oracle 12c+ |
| `sql` | `mysql://user:pw@host:3306/reporting` | MySQL 8 / MariaDB |
| `sql` | `postgresql://user:pw@host:5432/reporting` | PostgreSQL 12+ |
| `mongodb` | `mongodb://user:pw@host:27017/reporting_system` (or `mongodb+srv://…/db`) | MongoDB 5+; a single server is enough |
| `postgresql` | `postgresql://…` | The older PostgreSQL tables (`reporting_documents`); still supported |

Every table or collection starts with `rs_`, so they can share a database with other applications. The service
creates them at start-up and records the schema version in `rs_schema_version` (later versions add numbered
steps; nothing is ever dropped). The database account needs to create tables/collections once, then read and
write them. `/admin → Overview` shows which storage is in use.

What is kept (and what is not):

| Record | Where | Kept | Never kept |
|---|---|---|---|
| Report run: who, channel (runner, server, designer preview/run, schedule, API, signed link), report, data source, parameters, status, error, rows, duration, output size and SHA-256, client IP, the website's own user | `rs_report_runs` | every execution | report data rows; values of parameters marked **Sensitive** are stored masked (`42*********71`) |
| Published reports and designer drafts (folder, ID, title, data source, query language, the definition), folders, data adapters (no passwords) | `rs_reports`, `rs_drafts`, `rs_folders`, `rs_data_sources` | current state | data adapter URLs and passwords (they stay in the private secrets, encrypted with `REPORTING_SECRETS_KEY`) |
| Accounts, rights, API keys (hashes only), folder permissions, report versions and history, schedules and background/scheduled runs | `rs_users`, `rs_api_keys`, `rs_folder_permissions`, `rs_report_versions`, `rs_report_history`, `rs_job_schedules`, `rs_job_runs` | current state (versions: last 50 per report; history: last 200) | plain passwords or API keys |
| Audit event: sign-ins (also failed), sign-outs, users and rights, API keys, publish, delete, move, upload, version restore, folder permissions, data sources, schedules | `rs_audit_events` | append-only, each with the hash of the one before (`/admin → Logs → Audit trail → Verify chain`) | passwords, tokens, API keys, database URLs; draft autosaves |

Moving an existing data folder into a database (one time; the folder is not changed, and running it twice copies
nothing twice):

```bash
REPORTING_DATA_DIR=./data python -m backend.app.storage.migrate --url sqlserver://user:pw@host:1433/reporting
```

Then start every instance with the `REPORTING_STORAGE` and `REPORTING_STORAGE_URL` it prints.

## Several instances: PostgreSQL storage, HTTPS and a load balancer (`deploy/`)

`deploy/docker-compose.yml` starts PostgreSQL (the service's own storage), two app instances and nginx (HTTPS,
load balancing, 25 MB uploads):

```bash
cp deploy/.env.example deploy/.env          # fill in the secrets; never commit deploy/.env
mkdir -p deploy/certs                        # fullchain.pem + privkey.pem
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

Moving an existing `data/` folder into PostgreSQL (one time; the folder is not changed):

```bash
REPORTING_DATA_DIR=./data python -m backend.app.storage.migrate --url postgresql://reporting:PASSWORD@host:5432/reporting
```

| Variable | Default | Meaning |
|---|---|---|
| `REPORTING_STORAGE` | `files` | `files`, `sql`, `mongodb` or `postgresql` (see "Where the service keeps its own data") |
| `REPORTING_STORAGE_URL` | — | The storage database (not a report data adapter) |
| `REPORTING_ORG_ID` | `default` | Written on every run and audit record (one value per client installation) |
| `REPORTING_SESSION_SECRET` | random per process | **Set it, the same in every instance**, or sign-ins only work on one |
| `REPORTING_SCHEDULER` | `on` | Run the report scheduler here; set `off` on every instance but one |
| `REPORTING_SCHEDULER_POLL` / `REPORTING_JOB_WORKERS` | `20` s / `2` | How often schedules are checked / reports made at the same time |
| `REPORTING_TIMEZONE` | `UTC` | Default time zone for new schedules and relative dates (e.g. `Asia/Karachi`) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_SECURITY` | — / 587 / — / — / — / `starttls` | E-mail for scheduled reports |
| `REPORTING_MAX_BODY_MB` | `25` | Larger request bodies get 413 |
| `REPORTING_HSTS` | `false` | Send Strict-Transport-Security on HTTPS (directly or via a trusted proxy) |
| `REPORTING_TRUSTED_PROXIES` | — | Proxies whose `X-Forwarded-For` / `X-Forwarded-Proto` are believed |
| `REPORTING_SECURE_COOKIES` | `false` | Session cookie only over HTTPS (set `true` behind an HTTPS proxy) |

What every instance shares through the storage: reports, drafts, folders, data adapters and their secrets, users,
history, versions, schedules, run outputs, the audit log, sign-outs and the sign-in limit. Each instance notices a
publish or delete made by another within about three seconds. Service logs stay per container.

**Security headers.** Every page gets a Content-Security-Policy (only this site's scripts; inline styles; data/blob
images; blob frames for the PDF preview), `X-Frame-Options: SAMEORIGIN`, `X-Content-Type-Options: nosniff`,
`Referrer-Policy: same-origin` and a Permissions-Policy. Put the service behind HTTPS and set `REPORTING_HSTS=true`.

**Data adapters for SQL databases.** Give each adapter a database user that can only `SELECT`. PostgreSQL, MySQL and
Oracle queries run in a read-only transaction; SQL Server has no such mode, so every query is rolled back after it
is read — a SELECT-only login is the real protection there.

## Keeping the data safe (security checklist)

Checked in the security review (tests: `tests/test_security.py`, `tests/test_storage.py`):

| Area | What the service does | What you do |
|---|---|---|
| Sign-in and roles | Every data API needs a token or a `/server` session (a test calls every route without credentials); constant-time token checks; PBKDF2-SHA256 with 600 000 iterations (older hashes are upgraded at the next sign-in); 10 failed sign-ins per address in 10 minutes, on all instances | Create named users in Admin → Users; give designer/admin roles only to people who build reports |
| Secrets at rest | Adapter URLs/passwords and the admin token are in `private_secrets.json` (mode 0600). With **`REPORTING_SECRETS_KEY`** they are encrypted (Fernet: AES + HMAC); an existing plain file is encrypted at the next start | **Set `REPORTING_SECRETS_KEY`** (a Fernet key or a passphrase of 32+ characters) and keep it outside the data folder / database backups. Losing it means re-entering the adapter passwords |
| Report databases | MongoDB pipelines: no writes (`$out`, `$merge`), no JavaScript, no server internals (`$currentOp`, `$indexStats`, …), no `system.*` collections or other databases. SQL: one read-only statement, bound values, read-only transaction (SQL Server: rolled back) | Give every adapter a **read-only database user** that can see only the reporting collections / tables |
| Uploads | `.mrd` / `.jrxml` are parsed with defusedxml (no DTD, entities or external references); 12 MB file and 25 MB request limits | — |
| Browser | CSP, `X-Frame-Options`, `nosniff`, HttpOnly + SameSite=Strict session cookie, JSON-only writes (no cross-site forms), HSTS when enabled | HTTPS in front (`deploy/nginx.conf`), `REPORTING_SECURE_COOKIES=true`, `REPORTING_HSTS=true` |
| Report files | Background/scheduled outputs are visible only to their owner (and admins) and are deleted after **`REPORTING_OUTPUT_DAYS`** (default 30) | Lower it if the reports hold sensitive data |
| API docs | `/docs` and `/openapi.json` are off in the Docker image | Keep them off in production |
| Exports | Excel/CSV cells that start with `=`, `+`, `-`, `@` are neutralised (no formula injection) | — |
| Backups | — | Back up the storage database (or the data folder) and keep `REPORTING_SECRETS_KEY`, `REPORTING_SESSION_SECRET` and the admin token in a password manager, separately |

### Folder permissions (which team sees which reports)

`/server` → right-click a folder (or the **🔒 Permissions** button) → rules for users or roles (administrators only):

| Level | Gives |
|---|---|
| **None** | The folder and its reports are hidden (they answer 404, as if they did not exist) |
| **Run** | See, run, run in the background, schedule (designers) |
| **Manage** | Also edit, copy/move into, upload into, download the `.mrd`, versions — designer and admin roles only |

A rule applies to the folder and all its subfolders; the nearest folder with a rule wins, and in one folder a user
rule wins over a role rule. Without rules the role decides (viewer = Run, designer = Manage). A viewer never gets
more than Run; administrators always have everything. Example for two teams: on the root give `viewer` **None**, on
`/Finance` give the finance users **Run**, on `/HR` the HR users. Subreports and scheduled jobs are checked too (a
job stops working when its owner loses the permission). The dialog shows, per user, what they get and why.

Known limits: the permissions are for `/server` sign-ins. Token access (`/generate` with the admin token or a
signed link, the designer token) is not limited by folders — keep those tokens for trusted systems. Designers can
query any registered data adapter, so adapter users must be limited in the database itself.
