# Postman: every API of the reporting service

| File | What it is |
|---|---|
| `Reporting-Service.postman_collection.json` | 111 requests in 14 folders covering all 103 endpoints, in an order that runs end to end (create → use → clean up). Every request checks its status; IDs, the API key and links are passed on through environment variables. |
| `Local.postman_environment.json` | The variables. Fill in `password` and `adminToken`; the rest has local defaults. |

Regenerate both after changing the API: `python tools/postman/build_collection.py`.

## In Postman

1. **Import** both files (File → Import).
2. Choose the environment **Reporting Service — local** and fill in:
   * `baseUrl` — e.g. `http://127.0.0.1:8000`
   * `username` / `password` — a super admin (during first setup: `admin` and the admin token)
   * `adminToken` — `REPORTING_ADMIN_TOKEN` (for the token-based report requests)
   * `mongoUri` — a MongoDB the server can reach (default `mongodb://127.0.0.1:27017`)
3. Create the demo data once: `python tools/postman/seed_demo.py` (database `rs_postman_demo`, 5 orders).
4. Run the whole collection (**Run collection**) or single requests. Everything it creates is named
   `PM_…` / `pm_…` / `Postman` and is deleted in folder 14.

## From the command line (Newman, the Postman runner)

```bash
npx newman run postman/Reporting-Service.postman_collection.json -e postman/Local.postman_environment.json --env-var password=… --env-var adminToken=…
```

Last full run: 111 requests, 117 checks, 0 failures (about 12 s), and the same again on a second run.

Note: `Health` may answer **503** when the server's default data adapter (`MONGODB_URI`) is not configured;
the service itself is up. Do not run the collection against production: it creates and deletes reports, a user,
an API key and a data adapter.
