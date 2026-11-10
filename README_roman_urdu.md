# MongoDB Reporting Service

Yeh FastAPI service MongoDB aggregation se CSV, Excel (XLSX) aur PDF report results banati hai. `/admin` se report definition XML file export/import ki ja sakti hai; is file mein query, parameters, folder, layout aur format settings hoti hain. MongoDB connection ka password portable report file mein nahi hota.

## Local setup

Python 3.11+ aur MongoDB ke saath PowerShell mein project folder khol kar:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env` mein default MongoDB URI/database set karein. Misal ke taur par local Mongo ke liye `MONGODB_URI=mongodb://localhost:27017` aur `MONGODB_DATABASE=cas-cade` ho sakta hai. `REPORTING_ADMIN_TOKEN` khali chhor sakte hain: app pehli run par ek random token bana kar `data/private_secrets.json` mein save karti hai. `/admin` ko `127.0.0.1` ya `localhost` se kholne par browser token khud uthata hai; copy/paste ki zaroorat nahi. `.env` aur private secrets file share ya source control mein commit na karein.

Service start karein:

```powershell
uvicorn app:app --host 127.0.0.1 --port 8000
```

Browser mein:

- `http://127.0.0.1:8000/ui` reports chalane ke liye.
- `http://127.0.0.1:8000/admin` folders, reports aur Mongo connections manage karne ke liye. Local admin token automatically load hota hai.
- `http://127.0.0.1:8000/reports` report IDs aur URL parameters ke liye.
- `http://127.0.0.1:8000/health` default Mongo connection status ke liye.
- `http://127.0.0.1:8000/docs` API documentation ke liye.

## MCBCC SBP report

Initial report definition `data/reports/MCBCC_SBP_REPORT.json` mein hai. Aggregation ka main collection `Edith-KMBLGL-Workflows` hai. Pipeline `LastUpdateDateTime` par `StartDate` aur `EndDate` ka filter lagati hai. End date ka poora din UTC mein shamil hota hai. Is report ke `$lookup` collections bhi isi Mongo database mein hone chahiye.

Runner form se report chala sakte hain, ya website ka existing URL format rakh sakte hain:

```text
http://127.0.0.1:8000/generate/MCBCC_SBP_REPORT.csv?StartDate=2025-10-27&EndDate=2025-10-31&GeneratedBy=demo-user&GeneratedByCode=local-test&Folder=demo&Generate
```

`.csv` ko `.xlsx` ya `.pdf` se badal kar format chunein. XML report definition transfer file hoti hai, generated data output nahi. `GeneratedBy`, `GeneratedByCode`, `Folder` aur `Generate` audit metadata hain; yeh report filter ya access check nahi karte. Website ka pehle se maujood access control hi requests ko authorize kare.

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

## Grizzly deployment tayyari

Deployment ko ASGI app `app:app` chahiye. Start command yeh rakhein; platform ka port environment variable use karein:

```text
uvicorn app:app --host 0.0.0.0 --port $PORT
```

Grizzly environment mein `MONGODB_URI`, `MONGODB_DATABASE`, `REPORTING_ADMIN_TOKEN` aur audit/query limit variables configure karein. Public deployment par local browser bootstrap token share nahi karta; admin token platform ke private environment settings mein rakhein. `REPORTING_DATA_DIR` ko persistent writable storage par set karein; isi mein reports, `connections.json`, `private_secrets.json` aur local audit file save hote hain. Deploy ke baad `/health`, `/ui` aur `/admin` check karein. Extra MongoDB connection URLs admin UI se save ho sakte hain, lekin persistent storage private aur access-controlled honi chahiye.

## Service ki hadain

Reports MongoDB aggregation pipeline se define hoti hain; Mongo Shell/JS/Python editor modes unhi literal stages ko input format ke taur par parse karte hain, arbitrary code execute nahi karte. Read-only pipeline mein `$out`/`$merge` aur JavaScript chalane wale `$function`/`$accumulator`/`$where` allowed nahi. Pipeline MongoDB server par `maxTimeMS`, `allowDiskUse` setting aur output row cap ke saath chalti hai. Canvas coordinate-based A4 designer hai; CSV aur Excel phir bhi table format hain, aur PNG/JPEG filhaal canvas input hai—PNG output, scheduler aur dashboards alag development items hain.


## Report Designer (Jasper Studio jaisa) — `/designer`

React (Vite) based designer. Build ho chuka hai (`ui/designer/`), is liye Node ke baghair bhi `uvicorn app:app` ke baad `http://127.0.0.1:8000/designer` khul jata hai. Local par admin token khud mil jata hai; deployed server par `REPORTING_ADMIN_TOKEN` daalna hoga.

**Panels:** left par **Server** (folders/reports ka repository tree, new/open/delete) aur **Data Adapters** (MongoDB connections: test, save, report ke liye chunna), neeche **Outline** (Parameters, Fields, Calculated Fields, Variables, Groups, Bands aur un ke elements). Right par **Palette** (Static Text, Text Field, Line, Rectangle, Image, Chart) aur **Properties**. Beech mein 3 tabs: **Setup** (form-based: naam, adapter, collection, query, fields detect, parameters, auto layout), **Design** (bands wala canvas, drag/resize, Fields ko band par drag karna) aur **Preview** (PDF; sample data ya live Mongo ke 20 rows).

**Design model:** bands = Title, Page Header, Column Header, Group Header/Footer (har group ke liye), Detail, Column Footer, Page Footer, Summary. Expressions: `$F{field}`, `$P{param}`, `$V{variable}`; `=` se shuru hone wali expression mein +,-,*,/,% (code execute nahi hota). Variables: Sum/Count/Average/Min/Max/First, reset Report ya Group par. Built-in: `PAGE_NUMBER`, `PAGE_COUNT`, `REPORT_COUNT`, `CURRENT_DATE`. Patterns: `#,##0.00`, `Rs. #,##0`, `dd/MM/yyyy`. Charts: bar/line/pie.

**Save:** report JSON mein `design` key banti hai. PDF format par `/generate/ID.pdf` is design se render hota hai; CSV/XLSX tabular hi rehte hain. Design na ho to purana PDF layout chalta hai.

**Shortcuts:** Ctrl+S save, Ctrl+Z/Y undo-redo, Delete, arrow keys (Shift = 10px), Esc tool band.

**Frontend badalna ho to:**
```powershell
cd frontend
npm install
npm run dev      # http://localhost:5173 (API 127.0.0.1:8000 par proxy)
npm run build    # ../ui/designer mein output
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

## Java Expressions (jexpr.py)
Jasper wali Java expressions ab safe tareeqe se chalti hain (Java ya eval ke baghair): ternary `? :`, `==`, `&&`, `||`, `!`, `+ - * / %`, string methods (`equals`, `trim`, `isEmpty`, `length`, `substring`, `contains`...), `new DecimalFormat(p).format(x)`, `new SimpleDateFormat(p).format/parse`, `new Date()`, `Double.parseDouble`, `Integer.parseInt`, `new Double(x)`, `Math.*`, `String.valueOf`. Designer mein expression `=` se shuru karein, misal: `=$F{Status} == 0 ? "InProgress" : "Other"`. Koi bhi masla ho to us cell ka text khali rehta hai (poori report fail nahi hoti).
- JRXML import ab expressions ko seedha `=...` mein rakhta hai.
- Importer ab `findQuery`/`collectionName` wali queries, `"aggregate": $P{CollectionName}` (jahan CollectionName = `"Edith-"+$P{ProductCode}+"-PaymentPlans"`) aur 14400px tak ke pages sambhalta hai.
- Collection template (`Edith-$P{ProductCode}-X`): parameter ki value sirf letters/numbers/`_`/`-` ho sakti hai.
- Dotted fields (`Fields.HBLIPF194`) nested documents se padhi jati hain.
