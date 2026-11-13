# MongoDB Reporting Service

Yeh FastAPI service MongoDB aggregation se CSV, Excel (XLSX) aur PDF report results banati hai. `/admin` se report definition XML file export/import ki ja sakti hai; is file mein query, parameters, folder, layout aur format settings hoti hain. MongoDB connection ka password portable report file mein nahi hota.

**Code ka structure:** backend `backend/app/` package mein hai (routers, security, storage, connectors, queries, rendering), frontend `frontend/` mein. Har hissa kya karta hai aur kyun — [ARCHITECTURE.md](ARCHITECTURE.md). Chalane ka command wahi hai: `uvicorn app:app` (ya `uvicorn backend.app.main:app`).

## Teen hisse: Designer → Server, aur Admin

| Path | Kis ke liye | Login |
|---|---|---|
| `/designer` | Report banana/edit karna (Jaspersoft Studio jaisa) | Admin ya designer token |
| `/server` | Published reports ki repository: folders dekhna, report chalana | Username/password (sign in / sign out) |
| `/admin` | Sirf admin kaam: **Users**, **Data sources**, **Logs**, Overview | Admin token |

**Designer workspace:** Designer mein **Save** (Ctrl+S) report ko sirf designer ke apne workspace (`REPORTING_DATA_DIR/workspace/`) mein rakhta hai — server par kuch nahi badalta. Draft adhoora bhi save ho sakta hai. **Publish to server** button report ko server ki repository (`REPORTING_DATA_DIR/reports/`) ke kisi folder mein copy karta hai; pehle se published report ho to "Replace" tick karna hota hai. `/server`, `/ui`, `/reports` aur `/generate` sirf published reports dikhate/chalate hain. Workspace tree mein ● = draft (server par nahi), ✓ = published, ✎ = publish ke baad badla gaya. Designer ke **Server** tab se published report ko workspace mein khol sakte hain. Preview (live data ke saath bhi) publish kiye baghair chalta hai. Workspace pehli dafa khulne par server ki maujooda reports ki copy se shuru hota hai.

**Server login:** Users `/admin` → **Users** tab se bante hain (roles: `viewer` = reports chalana, `designer` = folder bhi bana sakta hai, `admin` = server se report/folder delete bhi). Passwords PBKDF2 hash ke saath `REPORTING_DATA_DIR/users.json` mein hote hain. Pehli setup: jab tak koi admin user nahi bana, `/server` par username `admin` aur password = admin token se sign in ho sakta hai; admin user banate hi yeh band ho jata hai. Session HttpOnly cookie hai (default 8 ghante, `REPORTING_SESSION_HOURS`); HTTPS proxy ke peeche `REPORTING_SECURE_COOKIES=true` rakhein. Password/role badalne par us user ke sab sessions khatam ho jate hain. 10 ghalat koshishon par 10 minute ke liye sign-in ruk jata hai. `REPORTING_ENABLED_AREAS` mein `server` na ho to `/server` band.

## Local setup

Python 3.11+ aur MongoDB ke saath PowerShell mein project folder khol kar:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env` mein default MongoDB URI/database set karein. Misal ke taur par local Mongo ke liye `MONGODB_URI=mongodb://localhost:27017` aur `MONGODB_DATABASE=cas-cade` ho sakta hai. `REPORTING_ADMIN_TOKEN` khali chhor sakte hain: app pehli run par ek random token bana kar `data/private_secrets.json` mein save karti hai. Apne computer par `.env` mein `REPORTING_LOCAL_BOOTSTRAP=true` set karein; phir `/admin` ko `127.0.0.1` ya `localhost` se kholne par browser token khud uthata hai, copy/paste ki zaroorat nahi. Yeh setting default `false` hai aur server ya reverse proxy (nginx/IIS) ke peeche hamesha `false` rehni chahiye: proxy ki har request `127.0.0.1` se aati hai, is liye `true` hone par proxy ke zariye koi bhi admin token le sakta hai. `false` hone par token `data/private_secrets.json` ke `admin_token` se ya `REPORTING_ADMIN_TOKEN` se lekar admin page par enter karein. `.env` aur private secrets file share ya source control mein commit na karein.

Service start karein:

```powershell
uvicorn app:app --host 127.0.0.1 --port 8000
```

Browser mein:

- `http://127.0.0.1:8000/ui` reports chalane ke liye.
- `http://127.0.0.1:8000/designer` reports banane, workspace mein save karne aur server par publish karne ke liye.
- `http://127.0.0.1:8000/server` sign in karke published reports ki repository dekhne aur chalane ke liye.
- `http://127.0.0.1:8000/admin` sirf admin kaam: users, Mongo data sources aur logs. `REPORTING_LOCAL_BOOTSTRAP=true` ho to local admin token automatically load hota hai.
- `http://127.0.0.1:8000/reports` report IDs aur URL parameters ke liye.
- `http://127.0.0.1:8000/health` default Mongo connection status ke liye.
- `http://127.0.0.1:8000/docs` API documentation ke liye.

## MCBCC SBP report

Initial report definition `data/reports/MCBCC_SBP_REPORT.json` mein hai. Aggregation ka main collection `Edith-KMBLGL-Workflows` hai. Pipeline `LastUpdateDateTime` par `StartDate` aur `EndDate` ka filter lagati hai. End date ka poora din UTC mein shamil hota hai. Is report ke `$lookup` collections bhi isi Mongo database mein hone chahiye.

Runner form se report chala sakte hain, ya website ka existing URL format rakh sakte hain:

```text
http://127.0.0.1:8000/generate/MCBCC_SBP_REPORT.csv?StartDate=2025-10-27&EndDate=2025-10-31&GeneratedBy=demo-user&GeneratedByCode=local-test&Folder=demo&Generate
```

`.csv` ko `.xlsx` ya `.pdf` se badal kar format chunein. XML report definition transfer file hoti hai, generated data output nahi. `GeneratedBy`, `GeneratedByCode`, `Folder` aur `Generate` audit metadata hain; yeh report filter nahi karte. Signed link mein yeh bhi sign hote hain, is liye audit mein unhein badla nahi ja sakta.

## Report access (`/generate` aur `/reports`)

Default `REPORTING_ACCESS_MODE=required` hai: in dono endpoints ko neeche mein se koi ek chahiye, warna `401`.

1. **Signed link** (sirf `/generate`): website user ke browser ko aisa link deti hai jo `REPORTING_ACCESS_SECRET` se sign ho. Link badla jaye, doosra format/parameter lagaya jaye, ya waqt guzar jaye to `403`.
2. **`X-Report-Token: <REPORTING_ACCESS_SECRET>` header**: jab website ka server khud service ko call karke file aage bhejta hai. Yeh secret kabhi browser tak na bhejein.
3. **`X-Admin-Token`**: admin page aur `/ui` runner (runner usi browser tab mein admin page par Connect hone ke baad token istemal karta hai).

`REPORTING_ACCESS_MODE=open` purana behaviour hai (koi check nahi); sirf band private network mein istemal karein.

Signed link banane ka tareeqa (website ke **server** par):

1. Normal link banayein, aakhir mein `Expires=<Unix seconds>` lagayein (abhi se zyada se zyada `REPORTING_SIGNED_LINK_MAX_SECONDS`, default 3600).
2. `/generate/...` se shuru hone wale path aur poori query ka HMAC-SHA256 (hex) secret se banayein.
3. Usay sab se aakhir mein `&Signature=<hex>` ke taur par jor dein. Signature ke baad query mein kuch na badlein ya order na badlein.

```python
import hashlib, hmac, time
from urllib.parse import urlencode

secret = b"<REPORTING_ACCESS_SECRET>"
query = urlencode({"StartDate": "2025-10-27", "EndDate": "2025-10-31", "GeneratedBy": "ali",
                   "Expires": int(time.time()) + 300})
path_and_query = f"/generate/KMBL_REPORT.pdf?{query}"
signature = hmac.new(secret, path_and_query.encode(), hashlib.sha256).hexdigest()
link = f"https://reports.example.com{path_and_query}&Signature={signature}"
```

Service ke saamne reverse proxy koi path prefix (misal `/reporting`) hata kar bhejta ho tab bhi sign hamesha `/generate/...` se shuru hone wala path karein. Audit record ke `access` field mein likha hota hai ke request `signed-link`, `report-token`, `admin-token` ya `open` se aayi.

CSV mein `=`, `+`, `-`, `@`, tab ya carriage return se shuru hone wali **text** values ke aage `'` lag jata hai, taa ke Excel unhein formula ke taur par na chalaye (asal numbers jaise `-5` waise hi rehte hain). XLSX mein aisi values hamesha text cells hoti hain. Collection naam `$P{...}` parameter se bane to us parameter ki `allowedValues` dena lazmi hai.

## UI se nayi report banana

Admin UI mein **New report** chunein aur **Design the report** canvas se shuru karein. A4 page zoom in/out hota hai; Ctrl+mouse-wheel ya buttons zoom karte hain, Space+drag canvas ko pan karta hai, aur objects ko drag karke rakh ya neeche ke kone se resize kar sakte hain. Selected object ke X/Y/width/height pixel values inspector mein set hote hain. PNG/JPEG image, heading, text blocks, data table aur URL question ke live value fields page par rakh sakte hain; PDF mein saved positions aur request ke parameter values render hote hain. Excel aur CSV ab bhi tabular data outputs hain. **Questions for the report user** mein multiple URL parameters define karein; har parameter query editor mein foran dikhne lagta hai. Query editor MongoDB aggregation JSON, Mongo Shell wrapper, JavaScript `pipeline = [...]`, aur Python `pipeline = [...]` styles leta hai. `//` ya `/* ... */` comments JSON/JavaScript/Shell mein aur `#` comments Python mein supported hain; **Comment / uncomment** ya Ctrl+/ selected lines ko toggle karta hai. Parameter markers alag rang mein show hote hain. JavaScript/Python code execute nahi hota; sirf literal pipeline data parse hota hai, phir server MongoDB aggregation chalata hai. **Download formats** mein report ke allowed results aur default format choose karein. Folder delete sirf khaali folder par hota hai; pehle uski reports ya subfolders move/delete karne honge. **Export report file** ek `.report.xml` template download karta hai. Doosre server par **Import file** se template import karein, wahan ka MongoDB source map karein aur destination folder choose karein. Imported report mein query, parameters, canvas positions, layout, folder aur formats aa jate hain; secret connection URL nahi aata. UI se save hui report `REPORTING_DATA_DIR/reports/<folder>/<report-id>.json` mein save hoti hai aur server restart ke baad bhi available rehti hai. Python module wali purani static reports load karni hon to `REPORTING_LOAD_BUILTIN_REPORTS=true` set karein.

Mongo Shell style JSON se zyada flexible object literal accept karti hai: single-quoted keys, unquoted keys, trailing commas, `NumberInt(...)`/`NumberLong(...)`, `ISODate(...)` aur `ObjectId(...)`. Jasper ke `$P{Name}` aur `$P!{Name}` ko URL parameter ki typed value samjha jata hai; `$P!` ka raw text insertion yahan enable nahi. Pehle matching parameter **Questions for the report user** mein define karein. `aggregation-json` mode strict JSON hi leta hai; purani shell query ke liye **Mongo Shell** mode choose karein.

Parameter example:

```json
[
  {"name":"StartDate","type":"date","label":"Start date","required":true},
  {"name":"EndDate","type":"date","label":"End date","required":true},
  {"name":"BranchCode","type":"string","required":false}
]
```

Pipeline mein parameter ko yeh marker de kar use karein:

```json
{"$match":{"BranchCode":{"$param":"BranchCode"}}}
```

Date range ke liye UI ka **Date range mapping** use karein, jaise:

```json
{"field":"LastUpdateDateTime","startParameter":"StartDate","endParameter":"EndDate"}
```

Service matching `$match` stage mein `$gte`/`$lte` inject kar deti hai. Agar parameter ko kahin aur date/time ke saath lagana ho to marker par boundary dein: `{"$param":"StartDate","boundary":"start"}` ya `{"$param":"EndDate","boundary":"end"}`. Date parameters URL mein `YYYY-MM-DD` hon.

Report save karne ke baad **Preview** mein test parameter values JSON dein. Preview zyada se zyada 20 rows browser mein dikhata hai. Full report URL endpoint se download hoti hai, jis par service ka maximum row limit bhi lagu hota hai.

## Multiple MongoDB connections

Admin ke **Data sources** tab mein data source ka naam aur MongoDB connection URL dein. URL mein database path ho to database field dene ki zaroorat nahi; misal:

- Name: `Branch reporting`
- URL: `mongodb://user:password@localhost:27017/branch-reports`

**Test connection** URL ko foran check karke success ya MongoDB ka error dikhata hai. **Save data source** ke baad URI `REPORTING_DATA_DIR/private_secrets.json` mein rakhi jati hai; connections aur report definitions file mein URI/password wapas expose nahi hote. Default connection ab bhi `.env` ki `MONGODB_URI` aur `MONGODB_DATABASE` values se chal sakti hai.

Mongo user ko un collections par read permission chahiye jo report aur `$lookup` use karti hain. Report runs ka audit `REPORTING_DATA_DIR/audit/report-executions.jsonl` mein locally save hota hai, is liye reporting MongoDB user ko write permission ki zaroorat nahi. `AUDIT_TO_MONGODB=true` sirf tab set karein jab aap optional MongoDB audit copy bhi rakhna chahte hon; is write ka fail hona report download ko nahi rokta.

## PostgreSQL data adapter (SQL reports)

Designer → **Data Adapters** → **Create Data Adapter** → **PostgreSQL** (double-click) → Host, Port, Database, Username, Password, SSL → **Test** → **Finish**. Password server ki private file mein jata hai, wapas kabhi nahi dikhta. Database user ko sirf **SELECT** ki ijazat dein.

Setup (ya Design toolbar → **Dataset & Query**) mein yeh adapter chunein: query khud **SQL** ho jati hai aur collection ka khaana chhup jata hai.

```sql
SELECT o.branch, o.customer, o.amount, o.placed_on
FROM orders o
WHERE o.placed_on BETWEEN $P{StartDate} AND $P{EndDate}
  AND $X{IN, o.branch, Branches}
ORDER BY o.branch, o.placed_on
```

- `$P{Name}` — parameter ki value (bound value; SQL mein text ki tarah nahi judti, is liye SQL injection nahi ho sakti).
- `$X{IN, column, Param}` — comma se alag values ki list (`KHI, LHR`); khaali chhorein to filter nahi lagta (Jasper jaisa). `$X{NOTIN, …}` ulta.
- `$P!{…}` allowed nahi (wo text ko seedha SQL mein jodta hai).
- Sirf ek `SELECT` / `WITH … SELECT` statement. INSERT/UPDATE/DELETE/DROP, `SELECT … INTO`, `FOR UPDATE` mana hain; aur query hamesha **read-only transaction** mein chalti hai, time limit `REPORT_MAX_TIME_MS` ke saath.
- **Read fields** database se columns poochta hai (`LIMIT 0`, koi row nahi padhi jati) aur query ke naye `$P{}` parameters khud jod deta hai — phir unka type (date waghera) Parameters mein theek karein.

Baqi sab (templates, Table/Crosstab, Preview, Publish, `/server`, `/generate`) MongoDB reports jaisa hi chalta hai.

**MySQL / MariaDB, SQL Server, Oracle** bhi isi tarah: wizard mein type chunein (port khud badal jata hai; Oracle mein "Database" ki jagah **Service name**). SQL Server mein SQL login (username/password) chahiye; wahan read-only transaction nahi hoti, is liye har query ke baad rollback hota hai — user ko sirf SELECT ki ijazat zaroor dein.

## Sub-datasets aur Table editor

- Design toolbar → **Dataset & Query** → **Datasets** → **New dataset**: naam, adapter (khaali = report wala), query, **Read fields**, parameters.
- Table ya Crosstab → Properties → **Dataset** tab → "Reads": apna dataset chunein aur har dataset parameter ke liye value dein, jaise `$F{OrderId}` (main report ka field). Khaali chhorein to report ka isi naam wala parameter jata hai.
- Canvas par Table ko **double-click** = **Table editor**: Column Header / Detail / Column Footer cells, click karke badlein, column ka kinara kheench kar chauda karein, column add/move/delete, fields par click karke cell mein daalein.

## Schedules aur background runs (`/server`)

- Report → **Run** tab → **⟳ Run in background**: file server par banti hai, **Runs** (neeche/upar ka switch) se download karein. Bari reports ke liye.
- Report → **Schedules** tab → **Schedule this report** (designer/admin): Once / har N minute / Daily / Weekly / Monthly, time zone, parameters, format, kitni files rakhni hain, e-mail (SMTP set ho to).
- Date parameters relative ho sakte hain: `{yesterday}`, `{today-7}`, `{monthStart}`, `{prevMonthStart}`, `{prevMonthEnd}` … — job chalne wale din hisaab hota hai.
- Upar **⏰ Schedules** aur **⟳ Runs** mein sab schedules aur outputs (Run now, Pause, Edit, Delete, Download).

## Versions (wapas purana version)

Server report ki har save (publish, edit, copy, move, upload, restore) ek **version** banti hai (aakhri 50). `/server` → report → **Versions** → **View** ya **↺ Restore**. Restore se naya version banta hai, kuch zaaya nahi hota; designer ki workspace copy bhi saath badalti hai.

## Production: PostgreSQL storage, kai instances, HTTPS

- `REPORTING_STORAGE=postgresql` + `REPORTING_STORAGE_URL=postgresql://…` — reports, users, versions, schedules, outputs, audit sab database mein; kai instances ek saath chal sakte hain (logout aur login limit bhi sab par lagti hai).
- Purana `data/` folder copy karna: `python -m backend.app.storage.migrate` (DEPLOYMENT.md dekhein).
- Tayyar setup: `deploy/docker-compose.yml` (PostgreSQL + 2 app instances + nginx HTTPS). `deploy/.env.example` ko `deploy/.env` bana kar secrets bharein — yeh file git mein kabhi na daalein.
- `/ready` load balancer ke liye; pages par CSP/HSTS jaise security headers khud lagte hain.

## Naya UI: mobile, tablet, dark mode

- `/server` aur `/admin` phone par: upar ☰ menu, neeche **Repository / Schedules / Runs** ka tab bar, report kholne par poori screen aur **‹ All reports** se wapas.
- Designer tablet/phone par: ☰ (bayan panel: Workspace, Server, Adapters, Outline) aur ⚙ (dayan: Palette, Properties) drawers ban jate hain; canvas ko poori jagah milti hai.
- Har page par ◐/☀/☾ button: system / light / dark theme (browser yaad rakhta hai).

## Grizzly deployment tayyari

Deployment ko ASGI app `app:app` chahiye. Start command yeh rakhein; platform ka port environment variable use karein:

```text
uvicorn app:app --host 0.0.0.0 --port $PORT
```

Grizzly environment mein `MONGODB_URI`, `MONGODB_DATABASE`, `REPORTING_ADMIN_TOKEN` aur audit/query limit variables configure karein. Deployment par `REPORTING_LOCAL_BOOTSTRAP` set na karein (ya `false` rakhein); admin token platform ke private environment settings mein rakhein. `REPORTING_DATA_DIR` ko persistent writable storage par set karein; isi mein reports, `connections.json`, `private_secrets.json` aur local audit file save hote hain. Deploy ke baad `/health`, `/ui` aur `/admin` check karein. Extra MongoDB connection URLs admin UI se save ho sakte hain, lekin persistent storage private aur access-controlled honi chahiye.

## Service ki hadain

Reports MongoDB aggregation pipeline se define hoti hain; Mongo Shell/JS/Python editor modes unhi literal stages ko input format ke taur par parse karte hain, arbitrary code execute nahi karte. Read-only pipeline mein `$out`/`$merge` aur JavaScript chalane wale `$function`/`$accumulator`/`$where` allowed nahi. Pipeline MongoDB server par `maxTimeMS`, `allowDiskUse` setting aur output row cap ke saath chalti hai. Canvas coordinate-based A4 designer hai; CSV aur Excel phir bhi table format hain, aur PNG/JPEG filhaal canvas input hai—PNG output, scheduler aur dashboards alag development items hain.


## Report Designer (Jasper Studio jaisa) — `/designer`

React (Vite) based designer. Build ho chuka hai (`ui/app/`; isi ek React build se `/designer`, `/server` aur `/admin` teeno chalte hain), is liye Node ke baghair bhi `uvicorn app:app` ke baad `http://127.0.0.1:8000/designer` khul jata hai. Local par `REPORTING_LOCAL_BOOTSTRAP=true` ho to admin token khud mil jata hai; warna aur deployed server par `REPORTING_ADMIN_TOKEN` daalna hoga.

**Panels:** left par **Workspace**, **Server** aur **Data Adapters**, neeche **Outline**. Right par **Palette** (Static Text, Text Field, Line, Rectangle, Image, Chart) aur **Properties**. Beech mein 3 tabs: **Setup** (naam, adapter, collection, query, fields detect, parameters, auto layout), **Design** (bands wala canvas) aur **Preview**.

**Outline (Jaspersoft Studio jaisa):** report ke neeche Parameters, Fields (calculated fields bhi), Variables, aur phir saare bands print order mein: Title, Page Header, Column Header, Group Header, Detail 1, Group Footer, Column Footer, Page Footer, Last Page Footer, Summary, No Data, Background — har band ke andar us ke elements. Jo band report mein nahi (delete ho chuka) wo grey dikhta hai aur canvas par nahi aata. Har node par **right-click** menu:
- Report: Create Parameter / Variable / Calculated Field / Group, Create Band.
- Band: Create Band ya Delete Band, Add element (Static Text, Text Field…), Fit band to content, group ke bands par Rename / Delete group.
- Element: Properties, Duplicate, Bring to Front / Send to Back, Move to band, Delete.
- Parameter / Variable / Calculated field: Rename (F2 ya double-click; `$P{…}`/`$V{…}`/`$F{…}` expressions aur query mein bhi badal jata hai), Place in band, Delete.
- Field: Place in Detail (label ke saath Column Header mein), Group by, Create Variable (Sum).
Canvas par band ya element ko right-click karne se wahi menu aata hai. Del se select ki hui cheez delete hoti hai; Ctrl+Z undo.

**Data Adapters:** tree (folders ke saath); right-click → Create Data Adapter (wizard: type → URL/database/folder → Test → Finish), Edit, Duplicate, Test connection, Move to folder, Delete.

**Preview:** upar Data Adapter (ya sample data), Records (pehle 20 ya saare), hara ▶ Run button aur Export (PDF / Excel / CSV); baayen **Input Parameters**; beech mein bhari hui report. Preview tab kholte hi sample data se report ban jati hai; draft save kiye baghair chalti hai.

**Design model:** bands = Title, Page Header, Column Header, Group Header/Footer (har group ke liye), Detail, Column Footer, Page Footer, Last Page Footer (aakhri page par Page Footer ki jagah), Summary, No Data (koi row na ho to sirf yahi band print hota hai), Background (har page par baqi bands ke peeche). Expressions: `$F{field}`, `$P{param}`, `$V{variable}`; `=` se shuru hone wali expression mein +,-,*,/,% (code execute nahi hota). Variables: Sum/Count/Average/Min/Max/First, reset Report ya Group par. Built-in: `PAGE_NUMBER`, `PAGE_COUNT`, `REPORT_COUNT`, `CURRENT_DATE`. Patterns: `#,##0.00`, `Rs. #,##0`, `dd/MM/yyyy`. Charts: bar/line/pie.

**Save:** report JSON mein `design` key banti hai. PDF format par `/generate/ID.pdf` is design se render hota hai; CSV/XLSX tabular hi rehte hain. Design na ho to purana PDF layout chalta hai.

**Shortcuts:** Ctrl+S save, Ctrl+Z/Y undo-redo, Delete, arrow keys (Shift = 10px), Esc tool band.

**Frontend badalna ho to:**
```powershell
cd frontend
npm install
npm run dev      # http://localhost:5173 (API 127.0.0.1:8000 par proxy)
npm run build    # ../ui/app mein output (designer, server, admin)
```

**Tests:** `python -m pip install -r requirements-dev.txt` phir `python -m pytest -q tests`.

**Hudood (abhi):** PDF mein built-in Helvetica hai; Urdu/Unicode ke liye `REPORT_PDF_FONT_PATH` mein .ttf dein. Subreports, crosstab, `.jrxml` import aur band ke andar text auto-stretch abhi nahi hain. Group ke liye rows field ke hisaab se renderer khud sort karta hai.

## AUDIT_REQUIRED
`AUDIT_REQUIRED=true` par report tabhi release hoti hai jab local audit record (`data/audit/report-executions.jsonl`) save ho jaye; warna `503`.

## JRXML Import (Jaspersoft Studio se)
Designer ke toolbar mein **Import JRXML** button se `.jrxml` file chunein. Importer Java ya Mongo chalaye baghair file ko translate karta hai: parameters (required/optional), table ke columns, header/detail bands, styles (rang/border), expressions (`$F{}`/`$P{}`), groups/variables, sort, page size aur `ignorePagination`. Import ke baad report abhi save nahi hoti; warnings dekh kar Save karein.
- Optional multi-value filter (khali = sab): query mein `{"$paramIn": "Branch"}` — comma-separated values `$in` ban jati hain, khali value `$nin: []`.
- CSV/XLSX design ke mutabiq banti hain (header labels, styled header, number formats, freeze panes); PDF band renderer se.
- Abhi support nahi: StretchHeight, subreports, crosstab, JRXML export wapas.
- Test: `python -m pytest tests` (19 tests) — fixture `tests/fixtures/abpl_accounts_opened.jrxml`.

## Java Expressions (backend/app/expressions/jexpr.py)
Jasper wali Java expressions ab safe tareeqe se chalti hain (Java ya eval ke baghair): ternary `? :`, `==`, `&&`, `||`, `!`, `+ - * / %`, string methods (`equals`, `trim`, `isEmpty`, `length`, `substring`, `contains`...), `new DecimalFormat(p).format(x)`, `new SimpleDateFormat(p).format/parse`, `new Date()`, `Double.parseDouble`, `Integer.parseInt`, `new Double(x)`, `Math.*`, `String.valueOf`. Designer mein expression `=` se shuru karein, misal: `=$F{Status} == 0 ? "InProgress" : "Other"`. Koi bhi masla ho to us cell ka text khali rehta hai (poori report fail nahi hoti).
- JRXML import ab expressions ko seedha `=...` mein rakhta hai.
- Importer ab `findQuery`/`collectionName` wali queries, `"aggregate": $P{CollectionName}` (jahan CollectionName = `"Edith-"+$P{ProductCode}+"-PaymentPlans"`) aur 14400px tak ke pages sambhalta hai.
- Collection template (`Edith-$P{ProductCode}-X`): parameter ki value sirf letters/numbers/`_`/`-` ho sakti hai.
- Dotted fields (`Fields.HBLIPF194`) nested documents se padhi jati hain.

## Data ki hifazat (security)

- **`REPORTING_SECRETS_KEY`** zaroor set karein (32+ characters ka passphrase ya Fernet key): adapters ke passwords aur admin token encrypt ho kar save hote hain. Key ko data folder / database backup se alag, password manager mein rakhein.
- Har data adapter ke liye database mein **sirf-parhne wala (read-only) user** banayein, jo sirf reporting wali tables/collections dekh sake.
- MongoDB query mein `$out`/`$merge`, JavaScript, `$currentOp` jaisi server info aur `system.*` collections mana hain; SQL mein sirf ek SELECT.
- Background/scheduled report files `REPORTING_OUTPUT_DAYS` (default 30 din) baad khud mit jati hain.
- Production mein HTTPS, `REPORTING_SECURE_COOKIES=true`, `REPORTING_HSTS=true`; Docker image mein `/docs` band hai.
- Abhi har signed-in `/server` user har published report dekh sakta hai (folder-wise permissions abhi nahi).
- Poori checklist: DEPLOYMENT.md → "Keeping the data safe".
