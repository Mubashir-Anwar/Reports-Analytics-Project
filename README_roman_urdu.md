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

### Data sources har hisse ke apne

Data source (data adapter) jis hisse mein bana ho sirf wahin chalta hai: Designer, Server, Analytics aur Query sab ke apne hain.
Har hisse mein **Connect** ka button hai, Home par bhi (har hisse ke card par), aur **Admin → Data sources** mein sab ek sath.
Report publish karte waqt chuna jata hai ke Server par woh kis data source se parhe. Purane data sources (is se pehle ke) sab
hisson mein chalte hain jab tak Admin unhein **Give to one part** ya **Split per part** na kare.

## Local setup

Python 3.11+ (PowerShell, project folder mein):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m backend.app.setup
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Ya sab kuch ek sath: `.\setup.ps1`. Setup command environment check karti hai, `.env` khud banati hai aur ek **setup admin**
deti hai jo **24 ghante** chalta hai. `/login` par us se sign in karein, **Create new admin** se apna super admin banayein.
Jaise hi aap ka apna admin banta hai, setup admin khud hi hat jata hai (24 ghante hon ya na hon).
Waqt khatam ho jaye to command dobara chalayein. Har setting `docs/SETTINGS.md` mein hai. `.env`, `data/` aur private secrets kabhi share ya commit na karein.

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
- Ek hi hisse ki **kai bands**: Outline mein band ke **＋** se (ya right-click → Add another band) Detail 2, Detail 3, Summary 2…
  banti hain; har band ki apni **Print When Expression** ho sakti hai. JRXML ki saari detail bands bhi import hoti hain.
- **Apne fonts** (Urdu bhi): **File → Fonts** ya kisi bhi font list mein **＋ Add a font** se `.ttf` file dein. Canvas, PDF aur
  Excel mein chalta hai. Urdu text PDF mein khud jurta aur right-to-left likha jata hai. Naskh aur Nastaliq
  dono jurte hain (uharfbuzz package se); Nastaliq ke liye box thora ooncha rakhein.
- **Format pattern**: Text Field → Pattern ke **…** se Number, Currency, Date waghera chunein, ya apna pattern likh kar
  **Save as custom** karein; woh **Custom** mein har report aur har designer ke liye rehta hai.
- **Outline** aur canvas par right-click menus: band banana/hatana, parameters, variables, calculated fields, groups.
- **Sub-datasets**: Dataset & Query → **Datasets**; Table ya Crosstab apna dataset parh sakta hai (Table editor:
  Table par double-click).

### Query page: drag & drop aur views
Result **Tree**, **Columns** ya **JSON** mein dekhein. Koi bhi value pakar kar Query helper ya query box par chhor dein:
field aur value khud likhe jate hain aur data type data wala hi rehta hai (`"123"` text, `123` number, date poora din).

### Query languages

| Adapter | Language |
|---|---|
| MongoDB | Aggregation pipeline (JSON), Mongo shell (`db.getCollection("x").aggregate([...])`), JavaScript pipeline, Python pipeline (`pipeline = [...]`), Jaspersoft `runCommand` |
| PostgreSQL, MySQL, MariaDB, SQL Server, Oracle | SQL: sirf ek `SELECT` / `WITH`; `$P{x}` bound value, `$X{IN, column, Param}` list; read-only transaction |
| Snowflake, ClickHouse, Elasticsearch, Redis | Wahi SQL. Redis mein har key prefix ek table banta hai (`user:1` → `user`) |
| Files & Sheets: CSV, Excel, JSON, Parquet, Google Sheets, SQLite / DuckDB file | File upload karein (ya Sheet ka link dein); har file / sheet / tab ek table hai, DuckDB SQL se parhi jati hai |

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
3. **Query se**: Query page par **Use in Analytics** dabane se naya dataset usi query ke saath khulta hai.
   **ML lab**: regression, random forest, forecast, groups waghera; models ka muqabla, "what if" aur dashboard par lagana.
   **Dashboard**: card, column / bar / line / area / pie / donut / scatter chart, **map**, ML, table aur slicer. Toolbar mein **Auto refresh** (10 second se 1 ghanta). Kisi bar par click
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
  token le, phir page mein `<script src=".../assets/embed/sdk.js"></script>` aur `<mrs-report token="emb_…"></mrs-report>`. Dashboard ke liye `<mrs-dashboard token-url="…" refresh="60">`;
  poora tareeqa aur code: Analytics → **Embed in a website**.
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
- Admin checklist ek command mein: `python tools/harden_env.py --https` (sirf jo settings `.env` mein nahi hain woh
  add hoti hain, koi secret print nahi hota, purani file ki copy rehti hai). `REPORTING_SECRETS_KEY` ki copy sambhal kar
  rakhein. Storage Admin → System database se badlein (pehle data copy hota hai). E-mail test karne ke liye
  `--test-mailbox` aur `python tools/test_mailbox.py` (mail `.eml` file ban kar isi PC par save hoti hai).
- Poori checklist: DEPLOYMENT.md → "Keeping the data safe".
- Sanity / load / security test (sirf test server par): `python tools/sanity_test.py setup | load | security`,
  tareeqa TESTING.md → 12.

## Load aur security test ke natije (sanity test)

- Ek instance (ek process) lagbhag **10 report-jaisi requests/second** sambhalta hai. 100 log ek saath: koi error nahi,
  wait 1–6 s. 1000 log: kuch crash nahi hua, memory 600 MB se kam rahi, lekin wait 35–50 s tha. Zyada logon ke liye
  **instances barhayein** (`deploy/docker-compose.yml`, nginx). Har 50–100 active logon par ek instance rakhein.
- Security run: har route bina sign-in aur bina rights ke, injection, API key ka ghalat istemal, path traversal, cookies
  aur headers, aur har page / JS file / jawab mein password, key ya DB URL ki talaash: **0 FAIL**.
- Is test se mile aur theek kiye gaye masle: ek saath bane accounts ke rights ghum ho jana, ek naam ke kai accounts,
  event loop par database ka kaam, audit trail ka takraav, aur sign-in ki timing se username ka pata chalna.
