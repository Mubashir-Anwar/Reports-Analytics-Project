# Settings (environment variables)

Every setting is optional. `python -m backend.app.setup` makes a `.env` with the two random secrets this installation
needs; add a line from the list below only to change a default. **`.env` is never committed to Git.**

Data sources (MongoDB, PostgreSQL, MySQL, MariaDB, SQL Server, Oracle, Snowflake, ClickHouse, Elasticsearch, Redis, files, Google Sheets, SQLite and DuckDB files) are not settings: they are added in the product
(Server → Data sources). There is no built-in or default data source.

```ini
# ------------------------------------------------------------------ management access
# Admin token: only for automation (X-Admin-Token on /api/admin/*). People never sign in with it.
# Empty: the service makes one by itself. Set a long random value to call the API from scripts.
REPORTING_ADMIN_TOKEN=
# Optional designer token (at least 24 characters) for automation that builds or previews reports through the
# API: it cannot change data sources, delete reports or folders, or read logs. People use their own accounts.
# Generate: python -c "import secrets; print(secrets.token_urlsafe(32))"
REPORTING_DESIGNER_TOKEN=

# Sign-in: every page (/designer, /server, /admin, /ui, /analytics) uses a user account and its rights.
# The first account is made by the setup command: python -m backend.app.setup (it works for 24 hours).

# ------------------------------------------------------------------ which paths are served
# Comma-separated list of the areas this instance serves.
#   runner    = /ui           (report runner page)
#   admin     = /admin        (management page; also the admin-only API)
#   designer  = /designer     (report designer)
#   server    = /server       (Report Server: repository, runs, schedules, dashboards)
#   analytics = /analytics    (datasets and dashboards)
#   docs      = /docs, /redoc, /openapi.json
# /generate, /reports and /health are always served. Example for a public reporting instance:
#   REPORTING_ENABLED_AREAS=runner
REPORTING_ENABLED_AREAS=runner,admin,designer,server,analytics,docs
# Optional: only these IPs / CIDR ranges may open /admin, /designer and /api/admin/* (empty = any).
# Example: REPORTING_ADMIN_ALLOWED_IPS=10.0.0.0/8,192.168.1.0/24,127.0.0.1
REPORTING_ADMIN_ALLOWED_IPS=
# Reverse proxies whose X-Forwarded-For header is trusted for the caller IP (empty = use the direct peer).
# Example behind nginx on the same machine: REPORTING_TRUSTED_PROXIES=127.0.0.1
REPORTING_TRUSTED_PROXIES=

# ------------------------------------------------------------------ report access (/generate, /reports)
# required (default): a signed link, the X-Report-Token header or the admin token is needed.
# open: no check (only inside a private network; the old behaviour).
REPORTING_ACCESS_MODE=required
# Secret shared by the website and this service (at least 32 characters). The X-Report-Token header
# must equal it, and signed links are HMAC-SHA256 signed with it. Empty = only the admin token works.
# Do not write the real secret here: keep it in .env (gitignored) or the server's private settings.
# Generate: python -c "import secrets; print(secrets.token_urlsafe(48))"
REPORTING_ACCESS_SECRET=
# The longest time a signed link may stay valid (seconds).
REPORTING_SIGNED_LINK_MAX_SECONDS=3600

# ------------------------------------------------------------------ storage
# Where saved reports, connection profiles, folders, logs and the run history live.
# On a server, map this path to a persistent, writable volume.
REPORTING_DATA_DIR=./data

# ------------------------------------------------------------------ logs and audit
# Service log file (rotating). Default: REPORTING_DATA_DIR/logs/service.log. Shown on /admin → Logs.
REPORTING_LOG_TO_FILE=true
REPORTING_LOG_FILE=
REPORTING_LOG_MAX_BYTES=5000000
REPORTING_LOG_BACKUPS=5
LOG_LEVEL=INFO
# The report run history is saved locally under REPORTING_DATA_DIR/audit (also on /admin → Logs).
# MongoDB audit inserts are optional and off by default, so read-only users work.
AUDIT_DATABASE=Cascade-Local
AUDIT_COLLECTION=report_execution_audit
AUDIT_TO_MONGODB=false
# true => a report is not released when its audit record cannot be saved locally (fail-closed)
AUDIT_REQUIRED=false

# ------------------------------------------------------------------ query limits
REPORT_MAX_TIME_MS=30000
REPORT_MAX_ROWS=10000
REPORT_ALLOW_DISK_USE=false

# Optional: path to a .ttf font for Unicode/Urdu text in designer PDFs (used for every font name)
REPORT_PDF_FONT_PATH=
# Optional: a .ttf used only for Urdu / Arabic text when neither the report's font nor an uploaded font has those letters.
# Default: a font of the machine (Windows: Arial; the Docker image: Noto Naskh Arabic).
REPORT_RTL_FONT_PATH=

# ------------------------------------------------------------------ many users and big reports (DEPLOYMENT.md → "Large reports and many users")
# Reports made at the same time in this process (query + PDF/Excel). Default: CPU count, between 2 and 8.
REPORT_MAX_CONCURRENT=
# At the same time for one user / API key (API keys, tokens and links share half of all places).
REPORT_MAX_PER_CALLER=2
# How long a request waits for a free place before "busy, try again" (503 + Retry-After).
REPORT_QUEUE_WAIT_SECONDS=15
# Background CSV / Excel runs bigger than REPORT_MAX_ROWS are streamed to disk in batches (not held in memory).
REPORT_EXPORT_MAX_ROWS=1000000
REPORT_EXPORT_MAX_TIME_MS=600000
# Where those files are written (default <data dir>/exports). Shared disk when several servers run behind a load balancer.
REPORTING_EXPORT_DIR=
# Background workers per process (default 2, at most 8).
REPORTING_JOB_WORKERS=2
# Requests that can be inside a handler at once (default 200, 40 to 2000). Most of them only wait (for a report place,
# the database, a password check), so this is not CPU work; REPORT_MAX_CONCURRENT limits that.
REPORTING_WORKER_THREADS=200

# Turn on every Admin checklist item in .env (only missing values are added, nothing secret is printed):
#   python tools/harden_env.py [--https] [--test-mailbox]

# ------------------------------------------------------------------ secrets to keep (DEPLOYMENT.md → "What to keep")
# Signs sign-in sessions and API-key links. Unset = derived from REPORTING_ADMIN_TOKEN (changing that token then signs everybody out).
REPORTING_SESSION_SECRET=
# Encrypts data-source passwords at rest (Fernet key or a passphrase of 32+ characters). Losing it = re-enter adapter passwords.
REPORTING_SECRETS_KEY=

# ------------------------------------------------------------------ analytics: datasets and dashboards (DEPLOYMENT.md → "Analytics")
# Where imported datasets are kept as Parquet files (default <data dir>/analytics). Back it up, or re-import after a restore.
# Several servers behind a load balancer: a shared disk, so every server reads the same files.
REPORTING_ANALYTICS_DIR=
# Most records one dataset import may read, and how long it may take.
ANALYTICS_IMPORT_MAX_ROWS=5000000
ANALYTICS_IMPORT_MAX_TIME_MS=1800000
# Most rows one visual returns, and how long its query may run.
ANALYTICS_QUERY_MAX_ROWS=5000
ANALYTICS_QUERY_TIMEOUT_SECONDS=30
# Memory and CPU threads one analytics query may use (DuckDB), e.g. 512MB, 2GB.
ANALYTICS_MEMORY_LIMIT=1GB
ANALYTICS_THREADS=2
# Imports running at the same time in this process, and the shortest scheduled refresh interval.
ANALYTICS_REFRESH_WORKERS=1
ANALYTICS_MIN_REFRESH_MINUTES=15
# Python scripts (the "Python scripts" right): time and memory of one script, and rows it reads per dataset / returns.
ANALYTICS_PYTHON_TIMEOUT_SECONDS=120
ANALYTICS_PYTHON_MEMORY_MB=2048
ANALYTICS_PYTHON_MAX_ROWS=1000000
# Pipelines (the "Pipelines (ETL)" right): longest time one pipeline run may take.
ANALYTICS_PIPELINE_TIMEOUT_SECONDS=900

# ------------------------------------------------------------------ where the service keeps its own data (DEPLOYMENT.md)
# files (default) | sql | mongodb | postgresql, and the database's URL. Admin → System database can write these two.
REPORTING_STORAGE=files
REPORTING_STORAGE_URL=
# The organisation ID written on every row of the system database.
REPORTING_ORG_ID=

# ------------------------------------------------------------------ web address, cookies and sessions
# The address people use, for links in e-mails (password links). Empty = no such links are sent.
REPORTING_PUBLIC_URL=
# true behind HTTPS: cookies only over HTTPS, and the HSTS header.
REPORTING_SECURE_COOKIES=false
REPORTING_HSTS=false
# Hours until a sign-in ends; one sign-in per person at a time, and the grace seconds for the older one.
REPORTING_SESSION_HOURS=8
REPORTING_SINGLE_SESSION=true
REPORTING_SINGLE_SESSION_GRACE=10
# Largest request (upload) in MB; nginx's client_max_body_size must allow it too.
REPORTING_MAX_BODY_MB=25
# Request log (Admin → Logs) and the days it is kept.
REPORTING_REQUEST_LOG=on
REPORTING_REQUEST_LOG_DAYS=30

# ------------------------------------------------------------------ schedules and e-mail
# off on every instance but one; seconds between looks for due schedules; days a finished output is kept.
REPORTING_SCHEDULER=on
REPORTING_SCHEDULER_POLL=20
REPORTING_OUTPUT_DAYS=30
REPORTING_TIMEZONE=UTC
# Folders a schedule may write files to, separated by ; (empty = none), and how many deliveries run at the same time (2-32).
REPORTING_DELIVERY_FOLDERS=
REPORTING_DELIVERY_WORKERS=16
# true: a webhook may call an address inside the private network.
REPORTING_WEBHOOK_ALLOW_PRIVATE=false
SMTP_HOST=
SMTP_PORT=587
# starttls | ssl | none
SMTP_SECURITY=starttls
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=

# ------------------------------------------------------------------ tests and tools
# 1 = do not read the .env file (the real environment only).
REPORTING_SKIP_ENV_FILE=
```

Older installations: `MONGODB_URI` and `MONGODB_DATABASE` still give a data source with the ID `default`. It can be
deleted like any other one. New installations should not use them.

## Docker Compose (`deploy/.env`)

`deploy/docker-compose.yml` reads these from `deploy/.env` (`python deploy/make_env.py` makes it with random secrets and never changes a value that is there; never commit it):

| Name | Needed | What it is |
|---|---|---|
| `STORAGE_DB_PASSWORD` | yes | Password of the PostgreSQL system database |
| `REPORTING_ADMIN_TOKEN` | yes | Long random value, for automation (the same in every instance) |
| `REPORTING_SESSION_SECRET` | yes | Long random value: signs the sign-ins |
| `REPORTING_SECRETS_KEY` | yes | Long random value: encrypts data source passwords |
| `REPORTING_ACCESS_SECRET` | no | Signed report links of a website |
| `REPORTING_PUBLIC_URL` | no | The address people use, e.g. `https://reports.example.com` |
| `REPORTING_TIMEZONE` | no | e.g. `Asia/Karachi` |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | no | E-mail for scheduled reports |

Random values: `python -c "import secrets; print(secrets.token_urlsafe(48))"`
