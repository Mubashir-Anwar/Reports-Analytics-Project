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
6. **Must be false on a server**: `REPORTING_LOCAL_BOOTSTRAP=false`.
7. **Start with one worker**:
   `python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1`
   (`app:app` still works: the root `app.py` imports the same app.)
   Saved reports and data adapters are cached inside the process; several workers would not see each
   other's changes. Put nginx/IIS in front for HTTPS.
8. **Check**: `GET /health` → `{"status": "ok", ...}`; open `/admin` with the admin token; check the Logs tab.

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

With this setup, run the reporting service with `REPORTING_ENABLED_AREAS=runner` on an address only Grizzly
can reach (for example `127.0.0.1:8000`), and keep `/admin` and `/designer` on a separate internal instance.
If your pages are on the same domain behind nginx, you can also link to `/ui` or show it in an `<iframe>`.

These Java snippets are examples for your Grizzly code and were not compiled here (no JDK on this machine).

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
* Keep `--workers 1` (the image default) until the storage moves to a database (ARCHITECTURE.md §9).
