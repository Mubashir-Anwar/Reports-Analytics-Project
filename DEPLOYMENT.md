# Deploying Analysis

This guide covers running **Analysis** (the reporting and analytics service; the name can be changed in
Admin → Branding) on a server: which paths to expose and to whom, logs, and how a
Java/Grizzly application connects to it. All settings are environment variables; `docs/SETTINGS.md` lists
every one with a comment.

## 1. Paths and who needs them

| Path | What it is | Who should reach it | Protected by |
|---|---|---|---|
| `/generate/<ID>.<pdf\|xlsx\|csv>` | Runs a report, returns the file | Your website / Java server (and users through it) | Signed link, `X-Report-Token`, or admin token |
| `/reports` | List of reports and their questions | Your website / Java server, the `/ui` page | Same as `/generate` |
| `/reports/connections/<id>/status` | Is a report's data adapter reachable (used by `/ui`) | The `/ui` page | Same as `/generate` |
| `/health`, `/ready` | Liveness and readiness checks | Load balancer / monitoring | Open, but only `{"status": …}`; version, database name and error text need a sign-in or `X-Admin-Token` |
| `/ui`, `/assets/*` | Report runner page | End users (usually through your app or proxy) | Page is open; its data calls need report access |
| `/designer` | Report designer | Report designers only | `REPORTING_ADMIN_TOKEN` or `REPORTING_DESIGNER_TOKEN` |
| `/admin` | Management page (data sources, logs) | Administrators only | `REPORTING_ADMIN_TOKEN` |
| `/api/admin/*` | API behind `/admin` and `/designer` | Administrators and designers | Admin or designer token (per endpoint) |
| `/analytics`, `/api/analytics/*` | Datasets, model and dashboards | Signed-in users with the Analytics right (datasets and the model: also Designer or Admin) | Sign-in session |
| `/docs`, `/redoc`, `/openapi.json` | API documentation | Developers only | Signed-in users (or `X-Admin-Token`) only; switch off in production |

### Roles

* **Admin token** (`REPORTING_ADMIN_TOKEN`): everything, including data sources (MongoDB URLs), deleting
  reports and folders, and reading logs.
* **Designer token** (`REPORTING_DESIGNER_TOKEN`, at least 24 characters): open `/designer`, create and save
  reports, preview and run them on the Designer's data sources (and Query's), connect those, import JRXML. Cannot
  touch the data sources of the Server or Analytics, delete reports or folders, or read logs (these answer `403`).
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
6. **Accounts and rights**: run `python -m backend.app.setup` on the server (an admin for 24 hours), sign in at `/login` with it, then create
   one or more **super admins** in Admin → Users & rights (the first one removes the setup admin at once). Give every other account only the rights it needs:
   Admin, Designer, Report Server, Analytics, Run reports. Pages and APIs check these rights; the admin token is
   for automation only and is never handed to a browser.
7. **Start with one worker**:
   `python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1`
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
docker build -t analysis .
docker run -d -p 8000:8000 -v analysis-data:/data -e REPORTING_ADMIN_TOKEN=... -e REPORTING_SECRETS_KEY=... -e REPORTING_ENABLED_AREAS=runner,admin,designer analysis
```


## 3. Repository folders and data adapters

`/admin` (Reports library) and `/designer` (Server tab) show the same tree as Jaspersoft Studio's
Repository Explorer: **Data adapters** at the top level, then folders inside folders (for example
`cascade / KMBLGL / Data Source`, `Reports`, `Images`). A data adapter can be saved at the top level or inside
any folder, and moved later without re-entering its URL (`PUT /api/admin/connections/<id>/folder`, admin
token). The folder only organises the adapter; its ID stays unique on the server, and a report on the
Server can use any of the Server's data sources wherever it is. A folder that still holds reports, adapters or subfolders cannot be deleted.

**Each part has its own data sources.** A data source has an `area` (`designer`, `server`, `analytics` or `query`) and works
only there; the right for that part connects and changes it (Analytics needs the Designer right too), the Admin right covers
all (`/admin → Data sources`, or the connect buttons of each part and of Home).

* Publishing (Designer → Server) asks which of the Server's data sources the report reads from, or connects the same database
  there as a separate one. The choice is remembered. Over the API: `POST …/publish {"connections": {"<designer id>": "<server id>"}}`;
  without it the remembered one, or one with exactly the same connection, is used, else the answer is 422.
* A server report opened in the Designer, and a query taken from Query to the Designer or Analytics, switch the same way.
* Data sources from before this rule (and those an administrator saves over the API without `area`) are **shared**: they work in
  every part. `/admin → Data sources` gives one to a single part, or splits it into one per part (published reports, drafts,
  datasets and saved queries keep working, each with the one of its own part).

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

### How API keys work

An API key lets **another system** (a client website's server, a Java/Grizzly app, an ETL job) run reports without a
person signing in. People use `/login`; systems use keys.

1. **Create**: Admin → API keys → *New key*. Give it a name (the website), and limit it:
   * **Folders**: the key can only see and run reports in those folders (and their subfolders); everything else answers 404.
   * **Allowed IPs**: only requests from the website server's address (or network, e.g. `10.20.0.0/24`) are accepted.
   * **Expires**: optional end date; **Requests per minute**: optional own rate limit; **Webhook URL**; **Embedding** websites.
2. **Copy once**: the key looks like `mrk_<12-hex id>_<secret>`. It is shown **only once**. The server stores only the
   SHA-256 of the secret, so nobody (not even an admin or someone with the database) can read it back. Lost it? Create a
   new key and disable the old one.
3. **Use**: the website's **server** sends it on every call, `X-API-Key: mrk_…` (or `Authorization: Bearer mrk_…`).
   The key must never be put in a web page, JavaScript or a mobile app. For a browser, the server asks for a
   short-lived signed link (`POST /api/v1/links`) or an embed token (`POST /api/v1/embed-tokens`) and gives that out.
4. **Each call is checked**: key exists → not disabled → not expired → caller's IP allowed → under its rate limit
   (`429`) → report is in its folders (`404`) → a free report place (`429`/`503`, see "Large reports and many users").
   Signed links carry the key's id, so disabling or expiring the key also stops its links.
5. **Audit**: every run is logged with the key's name as actor (and `onBehalfOf`, the website's own user); creating,
   changing and disabling keys are in the audit trail. *Last used* is shown in Admin → API keys.
6. **Rotate**: create the new key, put it on the website server, check *Last used* moves to it, then disable the old one.

The signed links of keys are signed with `REPORTING_SESSION_SECRET` (or, when it is not set, a value derived from
`REPORTING_ADMIN_TOKEN`). Changing that secret makes every link already given out invalid; keys themselves keep working.

### Calls

Create one API key per website in **Admin → API keys** (limit it to folders and the website server's IP; optional
webhook URL and requests per minute). The key stays on the website's server: `X-API-Key: mrk_…`.

| Call | What it does |
|---|---|
| `GET /api/v1/whoami` | Checks the key: name, folders, rate limit |
| `GET /api/v1/reports`, `GET /api/v1/reports/{id}` | Reports the key may run, with their parameters (type, required, allowed values) |
| `POST /api/v1/reports/{id}/run` | Runs now: `{"format": "pdf", "parameters": {"Identifier": "123"}, "onBehalfOf": "ali"}` → the file |
| `POST /api/v1/reports/{id}/data` | The records as JSON: `{"parameters": {…}, "limit": 100, "offset": 0}` → `{"columns", "rows", "total", "more"}` |
| `POST /api/v1/reports/{id}/jobs` | Runs in the background (202 + `Location`); at most 5 waiting/running per key |
| `GET /api/v1/jobs/{id}`, `GET /api/v1/jobs/{id}/file` | Status, then the file |
| `POST /api/v1/links` | A short-lived signed URL for a browser (parameters cannot be changed) |
| `GET /api/v1/openapi.json` | This API as OpenAPI 3 (always on; needs `X-API-Key` or a sign-in) |
| `GET /api/v1/analytics/datasets` | Analytics datasets in the key's folders: columns, status, record count |
| `GET /api/v1/analytics/dashboards`, `GET /api/v1/analytics/dashboards/{id}` | Dashboards in the key's folders, with their tiles (visual definitions) to draw them in your own page |
| `POST /api/v1/analytics/query` | One visual: `{"dataset", "groupBy": [{"column", "bucket"}], "values": [{"column", "agg"}], "filters": […]}` → `{"columns", "rows"}` |
| `POST /api/v1/analytics/datasets/{id}/refresh` | Import the dataset again now (202; runs in the background) |

* **Webhook**: when a background job of the key finishes, its webhook URL gets `POST` `{"event": "job.finished",
  "job": {…, "downloadUrl"}}` with `X-Reporting-Timestamp` and `X-Reporting-Signature: sha256=HMAC(secret,
  "<timestamp>.<body>")`. The secret is shown once in Admin. Internal addresses are refused unless
  `REPORTING_WEBHOOK_ALLOW_PRIVATE=true`; redirects are not followed.
* **Rate limit**: `REPORTING_API_RATE_PER_MINUTE` (default 120, `0` = off) or per key; over it → `429` with `Retry-After`.
* Every call is in the run log (channel `api`, actor = the key's name, `onBehalfOf` = the website's own user).
* **Postman**: `postman/` has a collection for every endpoint and how to run it (see `postman/README.md`).

## 7. Embedding: a report inside the client website's page

1. **Admin → API keys → Embedding**: list the websites that may show reports for that key
   (`https://portal.example.com`; scheme, host and port only).
2. The website's **server** asks for a token for each page view (the API key never goes to a browser):
   `POST /api/v1/embed-tokens` with `X-API-Key` and `{"report": "KFS_FAL", "parameters": {"Identifier": "123"},
   "onBehalfOf": "customer-42", "expiresInSeconds": 900}` → `{"token": "emb_…"}`. The given parameters are
   **fixed**: the viewer shows them locked and ignores other values for them. Tokens live 1 minute to 1 hour.
3. The page shows it:

   ```html
   <script src="https://reports.example.com/assets/embed/sdk.js"></script>
   <mrs-report token="emb_…"></mrs-report>
   ```

   (or an `<iframe src="https://reports.example.com/embed/view?token=emb_…">`). The viewer shows the open parameters,
   a preview (first 200 rows), the PDF inline and Excel/CSV downloads; the frame follows its content's height.
   Events on the element: `mrs-ready`, `mrs-ran`, `mrs-error`; methods `run("pdf")`, `preview()`, `setToken(…)`.
   A complete example page: `tools/embed/example.html`.

### A dashboard in the website's page

Analytics → **Embed in a website** shows these steps with the code for the chosen dashboard. The website's server asks
`POST /api/v1/embed-tokens` with `{"dashboard": "dash_…", "expiresInSeconds": 3600}` (optional fixed
`"filters": [{"column": "Branch", "op": "in", "values": ["LHR"]}]`; name the `dataset` too when the dashboard uses
several), and the page shows it:

```html
<script src="https://reports.example.com/assets/embed/sdk.js"></script>
<mrs-dashboard token-url="/dashboard-token" refresh="60" theme="light" height="640"></mrs-dashboard>
```

`token-url` is an address of the website that answers the token; it is asked again before a token ends. `refresh` is
in seconds (0 = never, at least 10); it asks the dataset again, while new records come with the dataset's own import
schedule. Without `height` the frame follows its content. Events: `mrs-ready`, `mrs-error`; methods `reload()`,
`setToken(…)`.

Safety: the viewer page may only be framed by the listed websites (`Content-Security-Policy: frame-ancestors …`;
every other page of the service stays unframeable). Each use checks the key again (enabled, not expired, folders,
requests per minute); changing or deleting the key ends its embedded reports at once. Every preview and download
is in the run log with channel `embed`, the key's name and `onBehalfOf`; issuing a token is in the audit trail.

## Docker

The image is built from a clean checkout; it builds the React pages itself and contains **no** `data/`
(reports, adapters, users, secrets and analytics datasets live on the `/data` volume). `.dockerignore` keeps
`.env`, `data/`, tests, tools and caches out of the build.

```bash
docker build -t analysis .
docker run -d --name analysis -p 8000:8000 -v analysis-data:/data --env-file .env analysis
```

* The `default` data adapter comes from `MONGODB_URI` / `MONGODB_DATABASE`; add more on `/admin → Data sources`.
* The packages are exactly the tested versions (`constraints.txt`; the ranges are in `requirements.txt`).
* The image carries Noto Naskh Arabic, so Urdu / Arabic text prints in a PDF even when the report's font has no such
  letters. Fonts uploaded in Admin (e.g. Noto Nastaliq Urdu) come first; `REPORT_RTL_FONT_PATH` sets another one.
* The service runs as user 10001. A volume made by an older image may belong to root: once,
  `docker run --rm -u 0 -v analysis-data:/data analysis chown -R 10001 /data`.
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
| `sql` | `oracle://REPORTING:pw@host:1521/?service_name=ORCLPDB1&schema=REPORTING` | Oracle 12c+; the tables go into the schema (user) `REPORTING` |
| `sql` | `mysql://user:pw@host:3306/reporting` | MySQL 8 / MariaDB |
| `sql` | `postgresql://user:pw@host:5432/reporting` | PostgreSQL 12+ |
| `mongodb` | `mongodb://user:pw@host:27017/reporting_system` (or `mongodb+srv://…/db`) | MongoDB 5+; a single server is enough |
| `postgresql` | `postgresql://…` | The older PostgreSQL tables (`reporting_documents`); still supported |

The service gets **a database of its own** (default name `reporting`), so the tables have plain names without a
prefix: `users`, `user_rights`, `folders`, `server_reports`, `workspace_reports`, `schedules`, `runs`, `api_calls`,
`request_log`, `audit_events`, `secrets` and the others (schema v2). The service creates them at start-up and records
the schema version in `rs_schema_version` (later versions add numbered steps; nothing is ever dropped, and the
version 1 `rs_…` tables of an older installation are copied once and kept, so the previous version still works).
The database account needs to create tables/collections once, then read and write them. `/admin → Overview` shows
which storage is in use.

**Oracle:** a schema is a user, so the service's place is the schema `REPORTING`. A DBA creates it once:

```sql
CREATE USER REPORTING IDENTIFIED BY "a-long-password";
GRANT CREATE SESSION, CREATE TABLE TO REPORTING;
ALTER USER REPORTING QUOTA UNLIMITED ON USERS;
```

Connect as `REPORTING`. `schema=REPORTING` in the URL (the Admin form adds it) sets
`ALTER SESSION SET CURRENT_SCHEMA` on every connection, so the tables land in `REPORTING` even when another account
is used (that account then needs the rights to create and use tables in `REPORTING`).

What is kept (and what is not):

| Record | Where | Kept | Never kept |
|---|---|---|---|
| Report run: who, channel (runner, server, designer preview/run, schedule, API, signed link), report, data source, parameters, status, error, rows, duration, output size and SHA-256, client IP, the website's own user | `rs_report_runs` | every execution | report data rows; values of parameters marked **Sensitive** are stored masked (`42*********71`) |
| Published reports and designer drafts (folder, ID, title, data source, query language, the definition), folders, data adapters (no passwords) | `rs_reports`, `rs_drafts`, `rs_folders`, `rs_data_sources` | current state | data adapter URLs and passwords (they stay in the private secrets, encrypted with `REPORTING_SECRETS_KEY`) |
| Accounts, rights, API keys (hashes only), folder permissions, report versions and history, schedules and background/scheduled runs | `rs_users`, `rs_api_keys`, `rs_folder_permissions`, `rs_report_versions`, `rs_report_history`, `rs_job_schedules`, `rs_job_runs` | current state (versions: last 50 per report; history: last 200) | plain passwords or API keys |
| Analytics: datasets (adapter, query, column model, status, record count, file name), relationships, dashboards (tiles and filters), import history | `rs_analytics_datasets`, `rs_analytics_relationships`, `rs_analytics_dashboards`, `rs_analytics_refreshes` | current state (import history: last 200 per dataset) | the imported records themselves: those are Parquet files in `data/analytics` (or `REPORTING_ANALYTICS_DIR`) |
| Audit event: sign-ins (also failed), sign-outs, users and rights, API keys, publish, delete, move, upload, version restore, folder permissions, data sources, schedules | `rs_audit_events` | append-only, each with the hash of the one before (`/admin → Logs → Audit trail → Verify chain`) | passwords, tokens, API keys, database URLs; draft autosaves |

Moving an existing data folder into a database (one time; the folder is not changed, and running it twice copies
nothing twice):

```bash
REPORTING_DATA_DIR=./data python -m backend.app.storage.migrate --url sqlserver://user:pw@host:1433/reporting
```

Then start every instance with the `REPORTING_STORAGE` and `REPORTING_STORAGE_URL` it prints.

**From the Admin page (no command line):** a super admin opens **Admin → System database**, chooses SQL Server,
PostgreSQL, MySQL/MariaDB, Oracle, SQLite or MongoDB (fields or a pasted URL), presses **Test connection** (this
creates the `rs_…` tables/collections) and then **Use …**. Any direction works (files → SQL, SQL Server → MongoDB,
MongoDB → PostgreSQL…): the running service copies its data (documents, typed rows, run log, audit trail with its
hash chain) into the new database, writes `REPORTING_STORAGE` / `REPORTING_STORAGE_URL` into `.env` (the old file is
kept as `.env.before-storage-<time>`) and the change takes effect after a restart. The old database is not touched.
When the setting comes from the real environment (Docker, a Windows service), the page says so and the setting is
changed there instead. Every switch is in the audit trail (`storage.switched`).

## Several instances: PostgreSQL storage, HTTPS and a load balancer (`deploy/`)

`deploy/docker-compose.yml` starts PostgreSQL (the service's own storage), two app instances and nginx (HTTPS,
load balancing, 25 MB uploads):

```bash
python deploy/make_env.py --url https://reports.example.com   # deploy/.env with random secrets; run again: values stay
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
| Sign-in and roles | Every data API needs a token or a `/server` session (a test calls every route without credentials); constant-time token checks; PBKDF2-SHA256 with 600 000 iterations (older hashes are upgraded at the next sign-in); an account is locked after 5 wrong passwords for 15 minutes (Admin → Limits → Sign-in, or `REPORTING_LOGIN_MAX_FAILURES` / `REPORTING_LOGIN_LOCK_MINUTES`; the sign-in page shows the last 3 tries; a new password unlocks it) and an address after twice that many, on all instances | Create named users in Admin → Users; give designer/admin roles only to people who build reports |
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

## Analytics (datasets and dashboards)

`/analytics` turns data from any data adapter into dashboards. It is on when `REPORTING_ENABLED_AREAS` contains
`analytics` (the default).

1. **Dataset**: a data adapter plus a query (aggregation JSON, mongosh, JavaScript or SQL). Its records are imported
   into a compressed Parquet file (DuckDB) under `data/analytics/` (`REPORTING_ANALYTICS_DIR`). Nested fields become
   columns such as `customer.city`. Dashboards read this file, never the source database, so a busy dashboard puts no
   load on MongoDB or SQL Server. A dataset is imported again by hand, on a schedule (every N minutes, at least
   `ANALYTICS_MIN_REFRESH_MINUTES`), or by `POST /api/v1/analytics/datasets/{id}/refresh`. A failed import keeps the
   previous file, so dashboards keep working, and the error shows on the dataset.
2. **Model**: relationships between datasets on a shared column (for example orders `Branch` = branches `Code`), even
   when the datasets come from different databases. A visual can then group or filter one dataset by columns of the other.
3. **Dashboards**: cards (KPI), column, bar, line, area, pie, donut, scatter, map, machine learning, table and slicer visuals on a 12-column
   grid (map: country names or codes, well-known cities, or latitude and longitude columns). **Auto refresh** in a dashboard's toolbar asks again every 10 seconds to every hour. Clicking a bar or a slice filters the other visuals; slicers and dashboard filters do the same.

4. **ML lab**: regression (linear, ridge, polynomial, logistic), decision tree, random forest, nearest neighbours,
   forecast, K-means groups, unusual records, correlation and PCA on a dataset; compares models on records kept aside,
   answers "what if", and adds the result to a dashboard. Large datasets are sampled; models are trained again on every
   query and are not stored.
5. **From a query**: Query page → **Use in Analytics** opens a new dataset with that query filled in.

| Right | May |
|---|---|
| Report Server | see and open the dashboards in the folders they may see (read-only, filters work), like reports |
| Report Server + Designer/Admin with Manage on the folder | also download a dashboard (`.mdash`), upload one, move it; delete needs Admin |
| Analytics | open dashboards, filter them, make dashboards and change their own |
| Analytics + Designer or Admin | also make and import datasets and change the model |
| Admin / super admin | also change or delete anybody's dashboard |

**Admin → Analytics** (the Admin right is enough; the Analytics right is not needed) is where administrators
control it: every dataset with its source, status, records, size, last import, schedule and the dashboards using it,
with **Import now**, a schedule picker (or *Only by hand* to pause) and **Delete**; every dashboard with its owner and
**Change owner** (when someone leaves; users that no longer exist are marked); the recent imports with their errors;
disk space used and free; the limits in force; and **Remove leftover files** for Parquet files no dataset uses any more.
Each action is in the audit trail. Who may *see* a dashboard is set with the folder permissions on the Report Server;
who may *make* datasets with the rights in Users & rights.

**Dashboards on the Report Server.** A dashboard lives in a repository folder (default **Analytics**) and is listed on
`/server` next to the reports. The folder permissions apply to it exactly as to reports (also in `/analytics`), and
deleting a folder deletes its dashboards (their datasets stay). **Download (.mdash)** gives one JSON file with the
dashboard, its datasets' definitions (adapter ID, query, column model) and their relationships; no data and no
adapter addresses or passwords. **Add Resource** (or **Import** in Analytics) uploads it into any folder: datasets that
are already on the server with the same query are reused, others are created and imported; another data adapter can
be chosen when the file's adapter is not on this server.

**Power BI (.pbit, .pbix).** Analytics → Dashboards → **Import** also takes a Power BI template (`.pbit`) or report
(`.pbix`). A `.pbix` keeps its data model and data in Microsoft's closed format, so the browser takes only its report
part (pages and visuals) out of the file and sends that — no data is uploaded and the file size does not matter. Its
tables and columns are then known only from the visuals' fields: measures (no DAX), column types and relationships are
missing, so visuals on measures are left out and two tables must be related in **Model**. For the full model, save a
template in Power BI Desktop (*File → Export → Power BI template*) and import the `.pbit`. A dialog lists the pages (each becomes a dashboard) with what changes on each, and every Power BI table the
chosen pages use: match it to a dataset here (suggested when the names are the same), a new dataset on one of this
server's data adapters (the collection or SQL table is suggested from the template's Power Query source), or leave it
out. Converted: cards, column/bar/line/area/pie/donut/scatter charts, tables, matrices (as tables) and slicers at about
the same place; summaries of columns, date levels (year, quarter, month, day), simple measures (`SUM`, `AVERAGE`,
`MIN`, `MAX`, `COUNT`, `COUNTA`, `DISTINCTCOUNT`, `MEDIAN` of one column, `COUNTROWS` of a table) and the
relationships. Not converted: other DAX, Power Query steps, page and report filters, formatting, text boxes and images.
Columns are matched by name (ignoring case); a field the chosen dataset does not have is left out and named in the
result. API: `POST /api/analytics/powerbi/inspect` and `POST /api/analytics/powerbi/import` (the file as base64, at
most 15 MB; modellers only).

Every change to a dataset, relationship or dashboard is in the audit trail (`analytics.…`); every import is in the
run log (channel `analytics`) and in the dataset's import history. API keys see the datasets and dashboards in their
folders (folder **Analytics** when none is set).

| Setting | Default | What it limits |
|---|---|---|
| `ANALYTICS_IMPORT_MAX_ROWS` / `ANALYTICS_IMPORT_MAX_TIME_MS` | 5 000 000 / 30 min | records and time of one import |
| `ANALYTICS_QUERY_MAX_ROWS` / `ANALYTICS_QUERY_TIMEOUT_SECONDS` | 5 000 / 30 s | rows and time of one visual |
| `ANALYTICS_MEMORY_LIMIT` / `ANALYTICS_THREADS` | 1GB / 2 | memory and CPU of one query (DuckDB spills to disk above it) |
| `ANALYTICS_REFRESH_WORKERS` | 1 | imports running at once in this process |
| `ANALYTICS_PYTHON_TIMEOUT_SECONDS` / `ANALYTICS_PYTHON_MEMORY_MB` | 120 s / 2048 | time and memory of one Python script (its own process) |
| `ANALYTICS_PYTHON_MAX_ROWS` | 1 000 000 | rows a Python script reads from one dataset and may return |

**Python scripts** (the *Python scripts* right, with Analytics): a script reads up to five datasets with pandas,
scikit-learn and scipy and can be saved as a dataset that runs again on every refresh. Each run is a separate Python
process with an empty environment, a temporary folder, the time and memory limits above, and guard rails (no network,
no files of the server, no other programs, no ctypes). Those guard rails stop mistakes and curious scripts; they are
not a container. Give the right to people you trust with the data, or run the service in Docker for a hard boundary.
Every run is in the audit trail (`analytics.python.run`).

Size the server for it: the Parquet file is usually 5–20× smaller than the source data (20 000 orders ≈ 250 KB), and
a query reads only the columns it uses. Several servers behind a load balancer need `REPORTING_ANALYTICS_DIR` on a
shared disk; imports run on whichever server received the request or the schedule.

## Large reports and many users

A report server must not fall over when many people press *Run* at once, or when one report asks for years of data.
The service protects itself in four layers:

| Layer | What happens | Setting (`.env`) |
|---|---|---|
| **1. Report places** | Every report run (query + PDF/Excel/CSV) needs a *place*. There are a fixed number per process; a request that finds none waits up to 15 s in line, then gets `503 Busy, try again` with `Retry-After`. Memory use therefore has a ceiling: places × rows per run, however many requests arrive. | `REPORT_MAX_CONCURRENT` (default: CPU count, 2–8), `REPORT_QUEUE_WAIT_SECONDS` |
| **2. Fair share** | One user may hold only 2 places at once; all API keys, tokens and links together at most half of them, so one website (or a script in a loop) cannot lock out everybody else. Over it → `429`. | `REPORT_MAX_PER_CALLER` |
| **3. Direct runs are capped** | A run in the browser or by URL reads at most `REPORT_MAX_ROWS` records and stops after `REPORT_MAX_TIME_MS` (the database cancels the query). Above the cap the answer is `413` with: *run it in the background as CSV or Excel*. PDF is always capped: a 100 000-page PDF helps nobody. Plus the per-key rate limit. | `REPORT_MAX_ROWS` (10 000), `REPORT_MAX_TIME_MS` (30 s), `REPORTING_API_RATE_PER_MINUTE` |
| **4. Big exports stream to disk** | A **background** CSV/Excel run (Run in background, schedules, `POST /api/v1/reports/{id}/jobs`) reads the database in batches of 2 000 with a cursor (MongoDB batch cursor, PostgreSQL server-side cursor, MySQL unbuffered cursor, Oracle array fetch) and writes each batch straight into the file. Memory stays a few MB whether the result is 10 000 or 1 000 000 rows / several GB. The file is downloaded from disk (`GET /api/v1/jobs/{id}/file`), and files over 15 MB are not e-mailed (the e-mail gets the link). | `REPORT_EXPORT_MAX_ROWS` (1 000 000), `REPORT_EXPORT_MAX_TIME_MS` (10 min), `REPORTING_JOB_WORKERS` (2), `REPORTING_EXPORT_DIR` |

Watch it in **Admin → Overview → Capacity**: places in use, waiting, turned away, background queue, and the limits.

**Sizing** (rule of thumb): one place needs about *rows × columns × 200 bytes* of RAM while it renders (10 000 × 20
columns ≈ 40 MB, a PDF a little more). With 8 places, keep ~1 GB per worker process free for reports. If people get
`503` often, add CPU/RAM and raise `REPORT_MAX_CONCURRENT`, or run more instances behind the load balancer (see
"Several instances" above; then point `REPORTING_EXPORT_DIR` at a shared disk). Excel has a hard limit of 1 048 576 rows
per sheet; ask for CSV beyond that.

**On the database side** (this matters as much as the app): give reports **indexes** on the fields they filter by
(dates, branch, identifier), a **read-only** user, and, for MongoDB, run reports against a **secondary** /
analytics node (`readPreference=secondaryPreferred` in the adapter URL) so a heavy report never slows the
application that writes the data. Ask report designers to filter by a date range parameter instead of reading
"everything".

## If a super admin forgets the password

No database editing is needed. Anybody who can open a shell on the server (and so could read `.env` anyway) runs:

```bash
python -m backend.app.reset_password                       # list the accounts, super admins first
python -m backend.app.reset_password admin                 # type a new password twice (hidden, 12+ characters)
python -m backend.app.reset_password admin --generate      # or: print a strong random password once
python -m backend.app.reset_password admin --make-super-admin   # also make the account a super admin again
docker compose exec reporting python -m backend.app.reset_password admin   # in Docker
```

It works with every system database (files, SQL Server, PostgreSQL, MySQL, Oracle, SQLite, MongoDB), enables the
account again, signs out all its sessions and writes the audit event `user.password_recovered`. Other people: a
super admin resets their password in Admin → Users & rights. The sign-in page shows both ways under *Forgot your
password?*. Tip: keep **two** super admins, so one can always reset the other.

## What to keep (deployment record)

Keep one document per environment (staging, production) in your password manager or a restricted wiki. **Never** in
Git, e-mail or chat. `docs/SETTINGS.md` lists every setting.

| Keep | Why | If lost |
|---|---|---|
| `REPORTING_ADMIN_TOKEN` | Automation | Set a new one in `.env` and restart (sessions are signed out unless `REPORTING_SESSION_SECRET` is set) |
| `REPORTING_SESSION_SECRET` | Signs sign-ins and API-key links | Set a new one: everybody signs in again, links given out stop working |
| `REPORTING_SECRETS_KEY` | Decrypts data-source passwords | Re-enter every data adapter's password in Admin → Data sources |
| `REPORTING_ACCESS_SECRET`, `REPORTING_DESIGNER_TOKEN` | Older token access | Make new ones, give them to the systems that use them |
| `REPORTING_STORAGE` / `REPORTING_STORAGE_URL` | Where the service keeps its own data (system database) | You cannot find reports, users and keys |
| Data adapter connection details (host, database, read-only user) | To re-create adapters | Ask the DBA |
| API keys: **name, owner, which website/IP, folders, created** (not the key itself, which is only on the website server) | To know whom to call when rotating | Create a new key for that website |
| Super admin user names (2+) | Recovery | `reset_password` (above) |
| Server: host names, ports, TLS certificate renewal date, reverse proxy config, service/Docker command | Restart and renewal | — |
| Limits you chose: `REPORT_MAX_*`, `REPORT_EXPORT_*`, `REPORTING_JOB_WORKERS`, `REPORTING_OUTPUT_DAYS`, `ANALYTICS_*`, `REPORTING_LOGIN_*` | Capacity decisions | Defaults apply |
| Backups: what is backed up (storage database or data folder, **excluding** `data/exports`; `data/analytics` is optional: datasets can be imported again from their sources), how often, where, and the last restore test | Disaster recovery | — |

Back up the secrets **separately** from the database backup: a stolen backup without `REPORTING_SECRETS_KEY` does
not reveal the data-source passwords.
