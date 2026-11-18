# Postman: every API of Analysis

| File | What it is |
|---|---|
| `Reporting-Service.postman_collection.json` | 178 requests in 16 folders covering 157 of the 158 endpoints (incl. the public API, embedding and analytics; only the system-database switch is left out, because it changes where the server keeps its data), in an order that runs end to end (create → use → clean up). Every request checks its status; IDs, the API key and links are passed on through environment variables. |
| `Local.postman_environment.json` | The variables. Fill in `password` and `adminToken`; the rest has local defaults. |

Regenerate both after changing the API: `python tools/postman/build_collection.py`.

## In Postman

Admins can also download both files from the running server: **Admin → API & Postman** (the environment comes with
that server's address and their username filled in).


1. **Import** both files (File → Import).
2. Choose the environment **Reporting Service — local** and fill in:
   * `baseUrl` — e.g. `http://127.0.0.1:8000`
   * `username` / `password` — a super admin (during first setup: `admin` and the admin token)
   * `adminToken` — `REPORTING_ADMIN_TOKEN` (for the token-based report requests)
   * `mongoUri` — a MongoDB the server can reach (default `mongodb://127.0.0.1:27017`)
3. Create the demo data once: `python tools/postman/seed_demo.py` (database `rs_postman_demo`, 5 orders).
4. Run the whole collection (**Run collection**) or single requests. Everything it creates is named
   `PM_…` / `pm_…` / `Postman` and is deleted in the last folder ("Clean up").

## From the command line (Newman, the Postman runner)

```bash
npx newman run postman/Reporting-Service.postman_collection.json -e postman/Local.postman_environment.json --env-var password=… --env-var adminToken=…
```

Last full run: 126 requests, 126 test scripts, 140 checks, 0 failures (before the product-name, capacity and analytics requests were added; run it again to confirm 178).

Folder **12c Analytics** imports the demo orders into two datasets, relates them, builds a dashboard and queries it with the
session and with the API key. It also opens the dashboard on the Report Server, downloads it as a `.mdash` file and
uploads it again (the datasets are reused), and uses the Admin → Analytics calls (import now, schedule, owner, clean-up). The import runs in the background: the request "Wait until both datasets are ready" repeats
itself (up to 30 times, a second apart) until both are ready, so run it as part of the collection, not on its own.
The signed-in user needs the Analytics right plus Designer or Admin (a super admin has all of them).

Note: `Health` may answer **503** when the server's default data adapter (`MONGODB_URI`) is not configured;
the service itself is up. Do not run the collection against production: it creates and deletes reports, a user,
an API key and a data adapter.
