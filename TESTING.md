# Testing Analysis yourself

A checklist for a test server (for example your own PC). Tick each line; every step says what you should see.
It takes about an hour. Do **not** run section 8 (the Postman collection) against production: it creates and
deletes test reports, a user, an API key and a data adapter (all named `PM_…` / `pm_…` / `Postman`).

## 0. Start

1. Get the latest code (`git pull`) and the packages: `.venv\Scripts\python.exe -m pip install -r requirements.txt`.
2. Start the server (a restart is needed after every update):
   `.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000`
3. The first lines of the log show the storage. With `REPORTING_STORAGE=sql` you may see one line
   *"Moved reports, drafts, folders and data adapters into their tables"* the first time; that is expected.
4. Open http://127.0.0.1:8000/ready → `{"status": "ready", "storage": "sql", …}`.

## 1. Sign-in and rights

| Step | Expected |
|---|---|
| Open http://127.0.0.1:8000/designer while signed out | You land on **Sign in** |
| Sign in as your super admin (ABCD) | You go back to the Designer; the bar at the top shows the product logo and name (default **Analysis**) and all five parts |
| Admin → Users & rights: create `t_designer` with only **Designer** | Saved; the chips show Designer only |
| Sign out (your name at the top right → Sign out), sign in as `t_designer` | Only the Designer opens; the top bar shows only Designer |
| As `t_designer`, open /admin, /server, /ui | "No access" page for each, with a link to the Designer |
| Sign in as ABCD again; give `t_designer` **Run reports** too | `t_designer` (after signing in again) can open /ui |
| Try to make `t_designer` an Admin while signed in as a non-super admin | Refused: only a super admin manages administrators |

## 2. Designer

| Step | Expected |
|---|---|
| New report on a data adapter you have (or import a .jrxml / .mrd) | The fields are read; the preview shows rows |
| Parameters: tick **Sensitive** on an ID-like parameter (CNIC) | Saved with the report |
| Collection name with a parameter (e.g. `Edith-$P{ProductCode}-PaymentPlans`) | Preview asks for ProductCode; any letters/numbers/_/- work |
| Preview, then run PDF / Excel / CSV | Files download |
| Publish to a folder | It appears on the Report Server |

## 3. Report Server (/server)

| Step | Expected |
|---|---|
| **+ Folder** → choose *Inside* and a name (try `a/b`: refused) | The folder opens in the tree; its parents open too; breadcrumbs at the top |
| Click a folder tile, then a breadcrumb | Subfolders are tiles; the breadcrumb goes back up |
| Open the folder, run the report | The file downloads |
| **Run in background** | It shows under Runs; when done, Download works |
| Schedule it (daily) and **Run now** | A run appears; e-mail only if SMTP is set |
| Permissions on the folder: set a user to **none** | That user no longer sees the folder or its reports |
| Edit the title, then **Versions** → restore version 1 | The old title is back; History lists both |

## 4. Admin (/admin)

| Step | Expected |
|---|---|
| Overview | The checklist shows Storage = SQL Server (shared, several instances) |
| Data sources: Test each adapter | "OK" for the reachable ones |
| API keys: create one (folders = a test folder) | The key `mrk_…` is shown **once**; copy it |
| Same key: **Embedding** → `http://127.0.0.1:8770`; **Rate limit** → 60 | Shown under the key |
| Logs → **Report runs** | Every run from sections 2–3 with who, from where (Designer run, Report Server, Schedule…), rows, time; Sensitive values masked like `42*********71` |
| **API & Postman** → ⬇ Collection, ⬇ Environment | Two files; the environment already has this server's address and your username |
| **API & Postman** → pick a report, paste the key, fill the questions | Ready curl commands (PDF by URL, data as JSON, file from JSON) |
| **System database** (super admin) | Shows where the data is kept now (SQL Server). Test another database, e.g. MongoDB `reporting_system`: "Connected" (do **not** press *Use* unless you mean to move) |
| Logs → **Audit trail** → **Verify chain** | Sign-ins (also the failed ones), users created, keys, publishing… and "Chain intact" |

## 5. The database (SQL Server)

Run in SSMS or `sqlcmd -S localhost -E -d reporting`:

```sql
SELECT folder, report_id, title, data_source FROM rs_reports;            -- your published reports
SELECT username, super_admin, rights FROM rs_users;                       -- accounts and rights (no passwords)
SELECT TOP 20 started_at, channel, actor, report_id, status, row_count FROM rs_report_runs ORDER BY started_at DESC;
SELECT TOP 20 seq, at, action, actor, target FROM rs_audit_events ORDER BY seq DESC;
```

Expected: the same things you did in sections 1–4. A backup from before the table move is in
`C:\Program Files\Microsoft SQL Server\MSSQL16.MSSQLSERVER\MSSQL\Backup\reporting_before_tables_2026-11-17.bak`.

## 6. Getting report data with Postman (one request)

1. Admin → **API & Postman** → ⬇ Collection and ⬇ Environment, then Postman → **Import** both (or import the same files from `postman/`).
2. Environment **Reporting Service — local**: `baseUrl` = `http://127.0.0.1:8000`, `apiKey` = the key from section 4.
3. Open **12 Public API v1 → Reports the key may run** → Send: your reports with their parameters.
4. Open **Report data as JSON (page 1)**. Change the URL to your report ID and the body to its parameters, e.g.

   ```json
   {"parameters": {"Identifier": "123", "ProductCode": "CAR"}, "limit": 100, "offset": 0}
   ```

   Send → `{"columns": […], "rows": [{…}], "total": …, "more": …}`. Use `offset` for the next page.
5. The same with a file: **Run now (JSON in, file out)** with `"format": "pdf"` → Postman shows the PDF
   (Send and Download to save it).
6. Without Postman: `curl -X POST http://127.0.0.1:8000/api/v1/reports/<ID>/data -H "X-API-Key: mrk_…" -H "Content-Type: application/json" -d "{\"parameters\": {…}}"`

## 7. Embedding (a report inside another website)

1. In `tools/embed/example.html` replace `REPLACE_REPORTING_SERVER` with `127.0.0.1:8000` (and `https://` with `http://`).
2. Get a token (Postman **Embed token for the website's page**, or):
   `curl -X POST http://127.0.0.1:8000/api/v1/embed-tokens -H "X-API-Key: mrk_…" -H "Content-Type: application/json" -d "{\"report\": \"<ID>\", \"parameters\": {}}"`
   and paste it into the page.
3. Serve the page as another website: `.venv\Scripts\python.exe -m http.server 8770 --directory tools\embed` and open
   http://127.0.0.1:8770/example.html → the report shows inside the page; parameters you fixed in the token are locked.
4. Open http://localhost:8770/example.html (a website not on the list) → the frame stays **empty**.

5. A dashboard: Analytics → **Embed in a website** shows the code for your dashboard (token request, `<mrs-dashboard>`
   tag, refresh time). With `refresh="10"` the time in its corner changes every 10 seconds.

## 8. Every API at once (Postman collection)

1. Seed the demo data once: `.venv\Scripts\python.exe tools\postman\seed_demo.py` (database `rs_postman_demo` in your local MongoDB).
2. Environment: `username` = ABCD, `password` = ABCD's password, `adminToken` = `REPORTING_ADMIN_TOKEN` from `.env`,
   `mongoUri` = `mongodb://127.0.0.1:27017`.
3. **Run collection** (all 19 folders, in order). Expected: 299 requests, 0 failed (Health may be 503 when no default
   MongoDB is configured — the service itself is fine). Folder 12c removes the analytics datasets and dashboard it made; the last folder removes everything else.

## 9. A website calling by URL (Grizzly / any server)

```bash
curl -H "X-API-Key: mrk_…" "http://127.0.0.1:8000/generate/<ID>.pdf?Identifier=123" -o report.pdf
```

Expected: the PDF. Use the **report ID** (shown under each report on Run reports, e.g. `FAL_KFS`), not the title. A wrong ID → 404
*"Report ID 'KFS_FAL' is not published. Did you mean FAL_KFS?"*. With a key limited to other folders → 404; with a disabled
key → 401; over the rate limit → 429.

When something does not match the "Expected" column, note the step and what you saw (a screenshot helps), and
the time: the service log (Admin → Logs) and the run log have the details.

## 10. Moving the system database (only when you mean it)

Admin → **System database** → choose the type (SQL Server, PostgreSQL, MySQL, Oracle, SQLite or MongoDB), fill in the
fields (or paste a URL) → **Test connection** → **Use …**. The current data is copied (users, rights, keys, reports,
folders, drafts, adapters, schedules, versions, run log, audit trail); `.env` is updated and the old one is kept as
`.env.before-storage-<time>`. Restart the service. The old database is not changed, so you can switch back the same way.

## 11. Analytics (dashboards)

Needs the **Analytics** right; making datasets and relationships also needs **Designer** or **Admin**.

1. Open `/analytics` → **Datasets + New**. Choose a data adapter, a collection and a query (or `[{"$match": {}}]`),
   press **Test query** → the first records show with nested fields as `customer.city`-style columns.
2. **Create and import** → the dataset shows *Queued*, then *Ready* with its record count and size. The **Columns** tab
   lists each column as a dimension or a measure; change a label or role and save. **Refresh history** shows the import.
3. Make a second dataset (for example branches with a `Code` column). **Model** → relate `Branch` to `Code`.
4. **Dashboards + New** → **Edit** → add a card (KPI), a column chart by month, a bar chart using a column of the second
   dataset, a donut, a table and a slicer. Drag and resize the tiles, **Save**.
5. Click a bar → the other visuals filter to it and a chip shows the filter; tick a value in the slicer → both filters
   apply. **Clear all** removes them.
6. Delete a dataset a dashboard uses → it asks first (the server answers 409 without `force=true`).
7. Signed in as a user without Designer/Admin: dashboards open and filter, but there is no **+ New** for datasets.
8. **On /server**: the dashboard is listed in its folder next to the reports (📊). Click it → it opens read-only with
   working filters and slicers; **← Back** returns. Right-click → **Download (.mdash)**, then **Add Resource** → choose that
   file and another folder → the copy appears there (its datasets are reused, or imported when they are not on this server).
   Right-click → **Cut**, right-click a folder → **Paste** moves it. Folder **Permissions** set to *none* for a user hide the
   dashboard on /server and in /analytics. Deleting a folder lists its dashboards in the confirmation and deletes them.
9. **Admin → Analytics** (an account with only the Admin right is enough): the cards show datasets, problems,
   dashboards, imported records and free disk space. Change a dataset's **Schedule** (a shorter one than
   `ANALYTICS_MIN_REFRESH_MINUTES` is not offered) → "next …" appears; **Import now** → the status turns to Importing,
   then Ready, and the import is listed under **Recent imports**. **Change owner** of a dashboard to another user.
   **Delete** a dataset a dashboard uses → the confirmation names the dashboard. **Remove leftover files** is enabled
   only when there are files no dataset uses.
10. **Dashboard template (.pbit)**: export a report as a template (`.pbit`) from the desktop BI tool that made it. In Analytics →
   **Import** choose the `.pbit` → a dialog lists its pages (with what changes on each) and its tables. Match each table
   to a dataset (or a new one on a data adapter), press **Import** → one dashboard per page opens, with its visuals at
   about the same place. Then import a `.pbix` the same way: the dialog says it is a report file (not a template), warns that
   measures, column types and relationships are not in the file, and lists only the columns the visuals use; in the
   browser's network tab the request carries only `parts` (`Report/…`), never the data.
11. With an API key whose folders include the dataset's folder: `GET /api/v1/analytics/datasets` lists it and
   `POST /api/v1/analytics/query` returns rows; a dataset in another folder answers 404.

## 12. Sanity, load and security test (`tools/sanity_test.py`)

Only on a **test server** with its own data folder and storage database: the tool creates 1000 users, 21 API keys, a
data source, two reports and a dashboard (all named `sanity_*` / `SANITY_*`) and then puts the server under load.

1. Start a test server with every checklist item on (HTTPS, `REPORTING_SECRETS_KEY`, `REPORTING_SESSION_SECRET`,
   MongoDB / SQL storage, docs off, e-mail to `python tools/test_mailbox.py`), and a read-only database user for the data.
2. **setup**: `SANITY_ADMIN_PASSWORD=… python tools/sanity_test.py setup --base https://host:port --admin setup_admin --mongo-uri mongodb://reader:…@host/db --insecure`
   (`--insecure` only for a self-signed test certificate). It writes `sanity_state.json` (users, keys, IDs).
3. **load**: `python tools/sanity_test.py load --base … --stages 10,50,100,250,500,1000 --seconds 60 --think 3 --pid <server pid>`.
   Virtual users join stage by stage; each signs in and repeats Designer previews, Report Server runs (CSV, Excel,
   PDF, a 9 000-row CSV), dashboard queries, Query aggregates and API-key calls by URL, some with
   `X-Deliver: later` (answered at once with a status URL, collected when ready). Per stage it prints requests per
   second, p50 / p95 / p99 / max, `429` / `503` (the server protecting itself), the delivery queue wait, and the
   server's memory and CPU. Results also go to `--out` as JSON.
4. **security**: `python tools/sanity_test.py security --base … --secret <db password> --secret <secrets key> …`.
   It calls every route anonymously and as a user without rights, tries to raise its own rights, uses API keys outside
   their folders / changed by one letter / in the URL, sends NoSQL and SQL injection in parameters, dangerous MongoDB
   stages (`$out`, `$merge`, `$function`, `system.users`, `$currentOp`) in Query and in report previews, path
   traversal, a 60 MB body, checks cookie flags and security headers, a session after sign-out, and scans every
   answer, page and JavaScript file for the `--secret` texts and for database URLs with a password.
   Expected: no **FAIL** lines.
5. Afterwards drop the test storage database and the data user, or throw the test server away.

What it found and what was fixed is in ARCHITECTURE.md, "Many admins at once".
