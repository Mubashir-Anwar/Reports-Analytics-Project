# Analysis — reporting and analytics

**Analysis** is a browser-based reporting product in the style of Jaspersoft Studio + JasperReports Server, with
Power BI–style analytics. Designers build band-based reports, publish them to a server repository, and people or
other systems run them as PDF, Excel or CSV. Analytics imports query results into fast columnar datasets and shows
them on interactive dashboards. "Analysis" is the default product name; a super admin can rename it and change the
logo in **Admin → Branding**.

| Path | What it is | Right needed |
|---|---|---|
| `/home` | What you can do here, by your rights, next step, recent work (first page after signing in) | any |
| `/designer` | Report designer: bands, elements, queries, preview, publish | Designer |
| `/server` | Report Server: folders, reports and dashboards, ZIP export, runs, schedules, versions, folder permissions | Report Server |
| `/analytics` | Datasets, model (relationships), dashboards, maps, machine learning (ML lab), embedding guide; import `.mdash` and Power BI `.pbit` / `.pbix` | Analytics (+ Designer or Admin for datasets) |
| `/admin` | Users and rights, API keys, data sources, analytics, branding, system database, logs and audit | Admin |
| `/ui` | Report runner | Run reports |
| `/api/v1/…`, `/generate/…` | Public API and report URLs for websites (API keys) | — |

## Quick start (development)

```bash
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m backend.app.setup                           # checks the environment, makes .env and the first sign-in
python -m uvicorn backend.app.main:app --reload --reload-dir backend --host 127.0.0.1 --port 8000
```

`--reload-dir backend` restarts the service only when its own code changes. Without it, `--reload` watches every
`.py` file under the folder, including other copies of the project (git worktrees in `.claude/worktrees`), and a
restart cuts off a Preview or Run that is in progress ("The reporting server is not answering").

Or all of it at once: `.\setup.ps1` (Windows) or `sh setup.sh` (Linux, macOS) makes the virtual environment, installs the
packages and runs the setup command.

Open `http://127.0.0.1:8000/login` and sign in with the setup admin the command printed. It works for **24 hours**: press
**Create new admin** on the home page and use your own account from then on. The setup admin is removed the moment your
own admin exists, whether the 24 hours are over or not. Time over too early? Run the setup command
again. Every setting: [docs/SETTINGS.md](docs/SETTINGS.md).

Frontend (React 18 + Vite; the build goes to `ui/app/`, which the service serves):

```bash
cd frontend
npm install
npm run dev     # http://localhost:5173, API calls proxied to 127.0.0.1:8000
npm run build
```

Checks (CI runs the same): `ruff check .` and `python -m pytest -q`.

## Project layout

```text
backend/app/        FastAPI service: routers/ (one module per area), services/, security/, storage/ and db/
                    (system storage), connectors/ (MongoDB + SQL), queries/, rendering/ (fill engine, PDF/Excel/CSV),
                    expressions/ (Jasper expressions), importers/ (JRXML), jobs/ (scheduler), analytics/ (DuckDB,
                    Parquet, dashboards, .mdash and Power BI import)
frontend/src/       Designer (top level), server/, admin/, analytics/, portal/ (page shell), shared/ (theme, nav,
                    folders, adapters)
ui/                 Served as is: app/ (built React pages), the /ui runner (index.html, runner.*), embed/ (embed
                    SDK), brand/ (logo, theme)
tests/              pytest suite (service_names.py: every module's names in one namespace, for older tests)
postman/            Collection and environment for every endpoint (built by tools/postman/build_collection.py)
tools/              Postman builder and demo data, embed example, theme generator, harden_env.py (Admin checklist
                    settings into .env), test_mailbox.py, load_test.py, sanity_test.py (load + security test)
deploy/             docker-compose (PostgreSQL + 2 instances + nginx HTTPS), nginx.conf
Dockerfile          Multi-stage image (frontend build, then the Python service; no data inside)
data/               Runtime data (local only: never in git or the image)
```

## Documentation

| Document | For |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Backend and frontend teams: every part, why it exists and how the parts work together |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Operations: settings, security, storage, several instances, API keys, embedding, analytics |
| [TESTING.md](TESTING.md) | Testing the product by hand, step by step |
| [postman/README.md](postman/README.md) | Running every API from Postman or Newman |
| [README_roman_urdu.md](README_roman_urdu.md) | The same overview in Roman Urdu |
