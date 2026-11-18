# Analysis — reporting aur analytics (Roman Urdu guide)

**Analysis** browser mein reports banane, chalane aur dashboards dekhne ka product hai (Jaspersoft Studio + JasperReports
Server jaisa, saath mein Power BI jaisi Analytics). Product ka naam default "Analysis" hai; super admin **Admin →
Branding** mein naam aur logo badal sakta hai.

Backend: Python (FastAPI), `backend/app/`. Frontend: React (Vite), `frontend/` (build `ui/app/` mein jata hai). Har
hissa kya karta hai aur kyun: [ARCHITECTURE.md](ARCHITECTURE.md). Server par lagana: [DEPLOYMENT.md](DEPLOYMENT.md).
Khud test karna: [TESTING.md](TESTING.md). English mein mukhtasar: [README.md](README.md).

## Hisse (har user ko sirf woh hissa nazar aata hai jis ka right ho)

| Path | Kaam | Right |
|---|---|---|
| `/designer` | Report banana: bands, elements, query, preview; apne workspace mein save, server par publish | Designer |
| `/server` | Report Server: folders, published reports aur dashboards, chalana, schedules, versions, permissions | Report Server |
| `/analytics` | Datasets (Parquet mein import), model (relationships), dashboards; `.mdash` aur Power BI `.pbit` / `.pbix` import | Analytics (datasets ke liye Designer ya Admin bhi) |
| `/admin` | Users aur rights, API keys, data sources, Analytics, branding, system database, logs aur audit | Admin |
| `/ui` | Report runner (parameters bhar kar PDF / Excel / CSV) | Run reports |

Sab pages `/login` se sign in hote hain. **Super admin** ke paas sab rights hain aur sirf woh administrators bana,
badal ya delete kar sakta hai (kam az kam ek super admin hamesha rehta hai).

## Local setup

Python 3.11+ (PowerShell, project folder mein):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

`.env` ki har setting `.env.example` mein comment ke saath likhi hai. `REPORTING_ADMIN_TOKEN` khali ho to pehli run
par service khud token bana kar `data/private_secrets.json` mein rakhti hai. Pehli dafa `/login` par username `admin`
aur password = admin token; phir **Admin → Users & rights** mein super admin banayein, us ke baad yeh pehli-dafa wala
login band ho jata hai. `.env`, `data/` aur private secrets kabhi share ya commit na karein.

Frontend badalna ho to:

```powershell
cd frontend
npm install
npm run dev      # http://localhost:5173 (API 127.0.0.1:8000 par proxy)
npm run build    # ../ui/app mein output (designer, server, admin, analytics)
```

Tests: `python -m pytest -q` aur lint: `ruff check .` (CI dono chalata hai).

## Designer (`/designer`)

- **Ribbon → View → Panels**: **Repository explorer** (Workspace, Server, Data Adapters), **Outline**, **Palette** aur
  **Properties** — har panel alag se chhupa ya dikha sakte hain. Tablet/phone par panels drawer ban jate hain.
- Beech mein tabs: **Setup** (naam, adapter, collection, query, fields, parameters), **Design** (bands wala canvas),
  **Source** (report ka JSON) aur **Preview** (sample ya live data, Export PDF / Excel / CSV).
- **Save** sirf designer ke workspace mein rakhta hai; **Publish** report ko server ke kisi folder mein bhejta hai.
  **Save as file** / Open file: `.mrd` (poori report ek file mein). Jaspersoft Studio ki `.jrxml` bhi import hoti hai.
- Bands: Title, Page Header, Column Header, Group Header/Footer, Detail, Column Footer, Page Footer, Last Page Footer,
  Summary, No Data, Background. Elements: Text Field, Static Text, Image, Break, Rectangle, Ellipse, Line, Frame,
  Barcode, Chart, Table, Crosstab, List, Subreport, Note. Expressions Jasper jaisi (`$F{}`, `$P{}`, `$V{}`, Java-jaisi
  syntax) — safe interpreter se chalti hain, koi code execute nahi hota.
- **Outline** aur canvas par right-click menus: band banana/hatana, parameters, variables, calculated fields, groups.
- **Sub-datasets**: Dataset & Query → **Datasets**; Table ya Crosstab apna dataset parh sakta hai (Table editor:
  Table par double-click).

### Query languages

| Adapter | Language |
|---|---|
| MongoDB | Aggregation pipeline (JSON), Mongo shell (`db.getCollection("x").aggregate([...])`), JavaScript pipeline, Python pipeline (`pipeline = [...]`), Jaspersoft `runCommand` |
| PostgreSQL, MySQL/MariaDB, SQL Server, Oracle | SQL: sirf ek `SELECT` / `WITH`; `$P{x}` bound value, `$X{IN, column, Param}` list; read-only transaction |

JavaScript/Python sirf data ke taur par parhe jate hain (literal pipeline), chalaye nahi jate. MongoDB mein `$out`,
`$merge`, `$function`, `$where` waghera mana hain. Analytics ke datasets bhi yahi sab languages lete hain.

## Report Server (`/server`)

- Folders, reports aur dashboards; right-click menus (Run, Edit, Copy, Cut/Paste, Download `.mrd`/`.mdash`, Delete).
- **Add Resource**: `.mrd`, `.jrxml` ya `.mdash` file upload.
- **Run** (foran ya background mein), **Schedules** (Once / har N minute / Daily / Weekly / Monthly, e-mail; date
  parameters relative jaise `{yesterday}`, `{monthStart}`), **Runs** (outputs download).
- **Versions**: har save ek version (aakhri 50); View ya Restore.
- **Folder permissions** (admin): folder par right-click → Permissions → **None** (nazar hi nahi aata) / **Run** /
  **Manage**. Rule folder aur us ke sub-folders par lagta hai; sab se qareeb wala rule chalta hai; user ka rule role
  ke rule par bhari. Dashboards aur schedules par bhi lagta hai.

## Analytics (`/analytics`)

1. **Dataset**: kisi bhi data adapter ki query ka result `data/analytics` mein Parquet file ban kar import hota hai
   (dashboards source database par load nahi dalte). **Folder** sirf yeh batata hai ke dataset kahan rakha hai (report
   ki tarah); yahan koi API key nahi likhni — jo API key kuch folders tak mehdood ho, woh sirf unhi folders ke datasets
   dekhti hai. Refresh: haath se ya schedule (har 15 minute se har hafte tak).
2. **Model**: datasets ko common column se jorein (relationship).
3. **Dashboard**: card, column / bar / line / area / pie / donut / scatter chart, table aur slicer. Kisi bar par click
   karne se baqi visuals filter ho jate hain. Dashboards `/server` par bhi apne folder mein nazar aate hain.
4. **Import**: `.mdash` (doosre server se dashboard + datasets ki definitions, data nahi) ya Power BI template
   **`.pbit`** (har page ek dashboard; har Power BI table ke liye yahan ka dataset chunein). **`.pbix`** bhi chalti hai:
   browser us mein se sirf report wala hissa (pages aur visuals) nikal kar bhejta hai, data upload nahi hota. Lekin
   `.pbix` ka data model Microsoft ke band format mein hai, is liye measures, column types aur relationships nahi
   milte; poora model chahiye to Power BI Desktop mein *File → Export → Power BI template* karke `.pbit` import karein.
5. **Admin → Analytics**: har dataset ka status, records, size, schedule, *Import now*, delete, dashboard ka owner
   badalna, disk space aur limits (`ANALYTICS_*` settings).

## Websites aur doosre systems

- **API keys** (Admin → API keys): har website ke liye ek key; folders, IP aur rate limit tak mehdood. Website ka
  **server** key bhejta hai (`X-API-Key`), browser kabhi nahi.
- **Public API v1**: `/api/v1/reports`, `POST /api/v1/reports/{id}/run`, background jobs aur webhook, analytics
  datasets/dashboards/query. Tafseel: DEPLOYMENT.md section 6.
- **URL se report**: `GET /generate/<ID>.pdf?Param=…` (`X-API-Key` ke saath), ya browser ke liye `POST /api/v1/links`
  se chand minute wala signed link.
- **Embedding**: Admin → API keys → Embedding mein website likhein; website ka server `POST /api/v1/embed-tokens` se
  token le, phir page mein `<script src=".../assets/embed/sdk.js"></script>` aur `<mrs-report token="emb_…"></mrs-report>`.
- **Postman**: `postman/` mein har endpoint ki collection (tareeqa `postman/README.md`).

## Service ka apna data (storage)

`REPORTING_STORAGE`: `files` (default, `data/` folder), `sql` (SQL Server, Oracle, MySQL, PostgreSQL, SQLite),
`mongodb`, ya `postgresql`. Reports, users, versions, schedules, run log (`rs_report_runs`) aur audit trail
(`rs_audit_events`, hash chain — Admin → Logs → **Verify chain**) sab isi mein. Sensitive parameters (CNIC waghera)
run log mein masked rehte hain. Purana `data/` folder database mein: `python -m backend.app.storage.migrate --url …`.
Kai instances: `deploy/docker-compose.yml` (PostgreSQL + 2 app instances + nginx HTTPS + shared analytics volume).

## Data ki hifazat

- **`REPORTING_SECRETS_KEY`** zaroor set karein: adapters ke passwords aur admin token encrypt ho kar save hote hain.
- Har data adapter ke liye database mein **read-only user** banayein.
- Production: HTTPS, `REPORTING_SECURE_COOKIES=true`, `REPORTING_HSTS=true`; Docker image mein `/docs` band.
- Poori checklist: DEPLOYMENT.md → "Keeping the data safe".
