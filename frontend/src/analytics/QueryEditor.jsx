// Analytics → Queries. Two kinds, both saved and run here (Ctrl+Enter):
//   over the datasets (imported, fast):  DAX, SQL (DuckDB; joins, window functions)
//   on a data adapter itself (live):     MongoDB as Mongo Shell / Aggregation JSON / Python pipeline / JavaScript,
//                                        or the database's own SQL (PostgreSQL, MySQL, SQL Server, Oracle)
// A database query reads only (no $out, $merge, INSERT …) and shows the first rows; "Make a dataset" imports all of it.
// Addresses: #q/new, #q/<id>. Modellers write, save and run database queries; everyone with Analytics runs saved
// DAX / SQL queries.
import { useCallback, useEffect, useRef, useState } from 'react';
import { bi, queries } from './api.js';

export const MODES = [
  ['dax', 'DAX', 'datasets'],
  ['sql', 'SQL over the datasets', 'datasets'],
  ['src:mongosh', 'MongoDB — Mongo Shell', 'mongo'],
  ['src:aggregation-json', 'MongoDB — Aggregation JSON', 'mongo'],
  ['src:python', 'MongoDB — Python pipeline', 'mongo'],
  ['src:javascript', 'MongoDB — JavaScript pipeline', 'mongo'],
  ['src:sql', 'SQL on the database (PostgreSQL, MySQL, SQL Server, Oracle)', 'sqldb'],
];
const modeOf = (q) => (q.language === 'source' ? `src:${q.queryLanguage}` : q.language);
const kindOf = (mode) => MODES.find(([id]) => id === mode)?.[2] || 'datasets';
export const modeLabel = (q) => MODES.find(([id]) => id === modeOf(q))?.[1] || q.language;

const EXAMPLES = {
  dax: [
    ['All rows', (t) => `EVALUATE '${t}'`],
    ['Top 10', (t, n) => `EVALUATE TOPN(10, '${t}', '${t}'[${n}], DESC)`],
    ['Total by a column', (t, n, d) => `EVALUATE\nSUMMARIZECOLUMNS(\n    '${t}'[${d}],\n    "Total", SUM('${t}'[${n}]),\n    "Rows", COUNTROWS('${t}')\n)\nORDER BY [Total] DESC`],
    ['Filtered', (t, n, d) => `EVALUATE\nFILTER('${t}', '${t}'[${n}] > 0 && NOT ISBLANK('${t}'[${d}]))`],
    ['Totals in one row', (t, n) => `EVALUATE\nROW(\n    "Total", SUM('${t}'[${n}]),\n    "Average", AVERAGE('${t}'[${n}]),\n    "Rows", COUNTROWS('${t}')\n)`],
    ['Measure with a filter', (t, n, d) => `EVALUATE\nSUMMARIZECOLUMNS(\n    '${t}'[${d}],\n    "Big ones", CALCULATE(SUM('${t}'[${n}]), '${t}'[${n}] >= 100),\n    "Share", DIVIDE(SUM('${t}'[${n}]), 1)\n)`],
    ['Column of a related table', (t, n) => `EVALUATE\nSUMMARIZECOLUMNS(\n    RELATED('Other'[Name]),\n    "Total", SUM('${t}'[${n}])\n)\n// Model: relate '${t}' to 'Other' first`],
  ],
  sql: [
    ['All rows', (t) => `SELECT *\nFROM "${t}"\nLIMIT 100`],
    ['Total by a column', (t, n, d) => `SELECT "${d}", SUM("${n}") AS total, COUNT(*) AS rows\nFROM "${t}"\nGROUP BY "${d}"\nORDER BY total DESC`],
    ['Running total', (t, n, d) => `SELECT "${d}", "${n}",\n       SUM("${n}") OVER (ORDER BY "${d}") AS running\nFROM "${t}"`],
    ['Join two datasets', (t, n, d) => `SELECT a."${d}", SUM(a."${n}") AS total\nFROM "${t}" a\nJOIN "Other" b ON b."${d}" = a."${d}"\nGROUP BY a."${d}"`],
  ],
  'src:mongosh': [
    ['All (first 100)', (c) => `db.getCollection("${c}").aggregate([\n  { $limit: 100 }\n])`],
    ['Filter + fields', (c) => `db.getCollection("${c}").aggregate([\n  { $match: { Status: "Approved", CreatedAt: { $gte: ISODate("2024-01-01") } } },\n  { $project: { _id: 0, Identifier: 1, Branch: 1, Amount: 1 } }\n])`],
    ['Total by a field', (c) => `db.getCollection("${c}").aggregate([\n  { $group: { _id: "$Branch", total: { $sum: "$Amount" }, count: { $sum: 1 } } },\n  { $sort: { total: -1 } }\n])`],
    ['Join (lookup)', (c) => `db.getCollection("${c}").aggregate([\n  { $lookup: { from: "Other", localField: "Identifier", foreignField: "WorkflowIdentifier", as: "other" } },\n  { $unwind: { path: "$other", preserveNullAndEmptyArrays: true } },\n  { $limit: 100 }\n])`],
  ],
  'src:aggregation-json': [
    ['All (first 100)', () => '[\n  { "$limit": 100 }\n]'],
    ['Total by a field', () => '[\n  { "$group": { "_id": "$Branch", "total": { "$sum": "$Amount" } } },\n  { "$sort": { "total": -1 } }\n]'],
  ],
  'src:python': [
    ['All (first 100)', () => 'pipeline = [\n    {"$limit": 100},\n]'],
    ['Total by a field', () => 'pipeline = [\n    {"$match": {"Status": "Approved", "Active": True}},\n    {"$group": {"_id": "$Branch", "total": {"$sum": "$Amount"}}},\n    {"$sort": {"total": -1}},\n]'],
  ],
  'src:javascript': [
    ['All (first 100)', () => 'const pipeline = [\n  { $limit: 100 }\n];'],
    ['Total by a field', () => 'const pipeline = [\n  { $group: { _id: "$Branch", total: { $sum: "$Amount" } } },\n  { $sort: { total: -1 } }\n];'],
  ],
  'src:sql': [
    ['First 100 rows', () => 'SELECT *\nFROM orders\nFETCH FIRST 100 ROWS ONLY'],
    ['Total by a column', () => 'SELECT branch, SUM(amount) AS total, COUNT(*) AS orders\nFROM orders\nGROUP BY branch\nORDER BY total DESC'],
  ],
};

const HINTS = {
  dax: "DAX: EVALUATE a table — 'Sales', FILTER, TOPN, SUMMARIZECOLUMNS, SUMMARIZE, ADDCOLUMNS, SELECTCOLUMNS, CALCULATETABLE, VALUES, ROW — with SUM, AVERAGE, COUNTROWS, DISTINCTCOUNT, CALCULATE, DIVIDE, IF, SWITCH… RELATED('Branches'[Region]) uses a relationship of the Model.",
  sql: 'SQL (DuckDB) over the datasets by name, e.g. "Sales". Joins, window functions and WITH are allowed; nothing can be changed.',
  'src:mongosh': 'Mongo Shell: db.getCollection("Orders").aggregate([ … ]) or only [ … ]. Names without quotes, ISODate(…), ObjectId(…) are fine. Read-only: $out / $merge are refused.',
  'src:aggregation-json': 'Aggregation JSON: the pipeline as strict JSON — [ { "$match": { … } } ]. Choose the collection above.',
  'src:python': 'Python (PyMongo style): pipeline = [ {"$match": {"Active": True}} ] — True / False / None are fine. Only the list is read; no code runs.',
  'src:javascript': 'JavaScript: const pipeline = [ { $match: { … } } ]; — only the list is read; no code runs.',
  'src:sql': "The database's own SQL (one SELECT). Read-only; the first rows are shown.",
};

function toCsv(result) {
  const cell = (v) => {
    const text = v == null ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v);
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  return [result.columns.map((c) => cell(c.name)).join(','), ...result.rows.map((r) => r.map(cell).join(','))].join('\n');
}

export default function QueryEditor({ queryId, datasets, adapters = [], mayModel, notify, onSaved, onDeleted, onDatasetMade, folders }) {
  const isNew = queryId === 'new' || !queryId;
  const [saved, setSaved] = useState(null);
  const [name, setName] = useState('New query');
  const [folder, setFolder] = useState('Analytics');
  const [mode, setMode] = useState('dax');
  const [text, setText] = useState('');
  const [connectionId, setConnectionId] = useState('');
  const [collection, setCollection] = useState('');
  const [limit, setLimit] = useState(1000);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [running, setRunning] = useState(false);
  const [showSql, setShowSql] = useState(false);
  const [openTable, setOpenTable] = useState('');
  const editor = useRef(null);
  const ready = datasets.filter((d) => d.status === 'ready');
  const kind = kindOf(mode);
  const live = kind !== 'datasets';
  const fitting = adapters.filter((a) => (kind === 'mongo' ? (a.type || 'mongodb') === 'mongodb' : (a.type || 'mongodb') !== 'mongodb'));

  useEffect(() => {
    setResult(null); setError('');
    if (isNew) {
      const first = ready[0];
      setSaved(null); setName('New query'); setMode('dax'); setConnectionId(''); setCollection('');
      setText(first ? `EVALUATE '${first.name}'` : 'EVALUATE ');
      return;
    }
    queries.get(queryId).then(({ query }) => {
      setSaved(query); setName(query.name); setFolder(query.folder || 'Analytics'); setMode(modeOf(query)); setText(query.text);
      setConnectionId(query.connectionId || ''); setCollection(query.collection || '');
    }).catch((e) => notify(e.message, 'error'));
  }, [queryId]); // eslint-disable-line react-hooks/exhaustive-deps

  // a live mode needs an adapter of its kind: pick the first one that fits
  useEffect(() => {
    if (live && !fitting.some((a) => a.id === connectionId)) setConnectionId(fitting[0]?.id || '');
  }, [mode, adapters]); // eslint-disable-line react-hooks/exhaustive-deps

  const body = () => (live
    ? { language: 'source', queryLanguage: mode.slice(4), connectionId, collection: kind === 'mongo' ? collection : '', text }
    : { language: mode, text });
  const dirty = !saved || saved.name !== name || modeOf(saved) !== mode || saved.text !== text || (saved.folder || 'Analytics') !== folder
    || (live && ((saved.connectionId || '') !== connectionId || (saved.collection || '') !== collection));
  const mayEdit = mayModel && (isNew || saved?.mayChange);

  const run = useCallback(async () => {
    setRunning(true); setError('');
    try {
      const answer = mayModel ? await queries.run({ ...body(), limit }) : await queries.runSaved(queryId);
      setResult(answer);
    } catch (e) { setError(e.message); setResult(null); }
    finally { setRunning(false); }
  }, [mode, text, connectionId, collection, limit, mayModel, queryId]); // eslint-disable-line react-hooks/exhaustive-deps

  // a saved query opened by someone who may only run it: run straight away
  useEffect(() => { if (saved && !mayModel && saved.language !== 'source') run(); }, [saved]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async (asNew = false) => {
    try {
      const payload = { name: name.trim() || 'Query', folder, ...body() };
      const { query } = !asNew && saved ? await queries.save(saved.id, payload) : await queries.create(asNew ? { ...payload, name: `${payload.name} (copy)` } : payload);
      setSaved(query);
      notify(`Query “${query.name}” saved.`, 'ok');
      onSaved?.(query.id);
    } catch (e) { notify(e.message, 'error'); }
  };
  const remove = async () => {
    if (!saved || !window.confirm(`Delete the query “${saved.name}”? This cannot be undone.`)) return;
    try { await queries.remove(saved.id); notify('Query deleted.', 'ok'); onDeleted?.(); } catch (e) { notify(e.message, 'error'); }
  };
  /** A database query → a dataset (all rows imported, refreshed on schedule) usable by DAX, SQL and dashboards. */
  const makeDataset = async () => {
    const title = window.prompt('Name of the new dataset (DAX / SQL use it as the table name)', name.trim() || 'Dataset');
    if (!title) return;
    try {
      const { dataset } = await bi.createDataset({ name: title.trim(), folder, connectionId, queryLanguage: mode.slice(4), collection: kind === 'mongo' ? collection : '', querySource: text });
      notify(`Dataset “${dataset.name}” is being imported. When it is ready, use it in DAX as '${dataset.name}'.`, 'ok');
      onDatasetMade?.(dataset.id);
    } catch (e) { notify(e.message, 'error'); }
  };
  const exportCsv = () => {
    if (!result) return;
    const url = URL.createObjectURL(new Blob([toCsv(result)], { type: 'text/csv' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `${(name || 'query').replace(/[^\w.-]+/g, '_')}.csv`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 3000);
  };

  // insert text at the cursor (a table or column clicked in the list)
  const insert = (snippet) => {
    const box = editor.current;
    if (!box || !mayEdit) return;
    const { selectionStart: s, selectionEnd: e } = box;
    const next = text.slice(0, s) + snippet + text.slice(e);
    setText(next);
    requestAnimationFrame(() => { box.focus(); box.selectionStart = box.selectionEnd = s + snippet.length; });
  };
  /** Select a line of the query (from an error "query line 12"). */
  const goToLine = (line) => {
    const box = editor.current;
    if (!box) return;
    const lines = text.split('\n');
    const at = Math.min(Math.max(1, line), lines.length);
    const start = lines.slice(0, at - 1).reduce((n, l) => n + l.length + 1, 0);
    box.focus();
    box.setSelectionRange(start, start + lines[at - 1].length);
    box.scrollTop = Math.max(0, (at - 4) * (parseFloat(getComputedStyle(box).lineHeight) || 18));
  };
  const errorLine = Number((error.match(/query line (\d+)/i) || [])[1] || 0);
  const tableRef = (t) => (mode === 'dax' ? `'${t}'` : `"${t}"`);
  const columnRef = (t, c) => (mode === 'dax' ? `'${t}'[${c}]` : `"${c}"`);
  const example = (index) => {
    if (live) { setText(EXAMPLES[mode][index][1](collection || 'Orders')); return; }
    const ds = ready[0];
    if (!ds) { notify('Import a dataset first (Datasets → + New), or choose a MongoDB / database query above.', 'warn'); return; }
    const numeric = ds.columns.find((c) => c.kind === 'number')?.name || ds.columns[0]?.name || 'Value';
    const label = ds.columns.find((c) => c.kind === 'text')?.name || ds.columns[0]?.name || 'Name';
    setText(EXAMPLES[mode][index][1](ds.name, numeric, label));
  };
  const changeMode = (next) => {
    const switchingKind = kindOf(mode) !== kindOf(next);
    setMode(next);
    setResult(null); setError('');
    const first = EXAMPLES[next]?.[0];
    if (!first) return;
    const fill = () => {
      if (kindOf(next) !== 'datasets') { setText(first[1](collection || 'Orders')); return; }
      const ds = ready[0];
      setText(ds ? first[1](ds.name, ds.columns.find((c) => c.kind === 'number')?.name || 'Amount', ds.columns[0]?.name || 'Name') : '');
    };
    // an empty query follows the language; a written one is only replaced when the user says so
    if (!text.trim()) fill();
    else if (switchingKind && window.confirm('Replace the query with an example in the new language? (Cancel keeps your text.)')) fill();
  };
  const keys = (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); run(); }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); if (mayEdit) save(); }
    if (event.key === 'Tab' && !event.shiftKey) { event.preventDefault(); insert('    '); }
  };

  // the ribbon's Query tab
  useEffect(() => {
    const handle = (event) => {
      const { cmd } = event.detail || {};
      if (cmd === 'run') run();
      if (cmd === 'save' && mayEdit) save();
      if (cmd === 'saveAs' && mayEdit) save(true);
      if (cmd === 'csv') exportCsv();
      if (cmd === 'dataset' && live && mayEdit) makeDataset();
      if (cmd === 'mode' && event.detail.mode && mayEdit) changeMode(event.detail.mode);
      if ((cmd === 'dax' || cmd === 'sql') && mayEdit) changeMode(cmd);
    };
    window.addEventListener('bi-query', handle);
    return () => window.removeEventListener('bi-query', handle);
  });
  useEffect(() => { window.dispatchEvent(new CustomEvent('bi-query-state', { detail: { mode, live } })); }, [mode, live]);

  return (
    <div className="qe">
      <div className="page-head qe-head">
        <div className="qe-title">
          <p className="eyebrow">Query · {MODES.find(([id]) => id === mode)?.[1]}</p>
          {mayEdit ? <input className="qe-name" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} aria-label="Query name" />
            : <h1>{saved?.name || name}</h1>}
          {saved && <p className="muted small">by {saved.owner} · changed {new Date(saved.updatedAt).toLocaleString()}{dirty && mayEdit ? ' · not saved' : ''}</p>}
        </div>
        <div className="row">
          {mayEdit && <select value={folder} onChange={(e) => setFolder(e.target.value)} aria-label="Folder">{[...new Set([folder, ...folders])].map((f) => <option key={f}>{f}</option>)}</select>}
          <button type="button" className="btn primary" onClick={run} disabled={running || (live && !connectionId)} title="Run (Ctrl+Enter)">{running ? 'Running…' : '▶ Run'}</button>
          {mayEdit && <button type="button" className="btn" onClick={() => save()} disabled={!dirty && Boolean(saved)} title="Save (Ctrl+S)">Save</button>}
          {mayEdit && saved && <button type="button" className="btn" onClick={() => save(true)}>Save as copy</button>}
          {mayEdit && live && <button type="button" className="btn" onClick={makeDataset} disabled={!connectionId || !text.trim()} title="Import all the rows as a dataset, for DAX, SQL and dashboards">⇣ Make a dataset</button>}
          {saved?.mayChange && <button type="button" className="btn danger" onClick={remove}>Delete</button>}
        </div>
      </div>

      {mayEdit && (
        <div className="qe-source">
          <label className="field"><span>Language</span>
            <select value={mode} onChange={(e) => changeMode(e.target.value)}>
              <optgroup label="Over the datasets (imported, fast)">
                {MODES.filter(([, , k]) => k === 'datasets').map(([id, label]) => <option key={id} value={id}>{label}</option>)}
              </optgroup>
              <optgroup label="On a data adapter (live, read-only)">
                {MODES.filter(([, , k]) => k !== 'datasets').map(([id, label]) => <option key={id} value={id}>{label}</option>)}
              </optgroup>
            </select>
          </label>
          {live && (
            <label className="field"><span>Data adapter</span>
              <select value={connectionId} onChange={(e) => setConnectionId(e.target.value)}>
                {fitting.length === 0 && <option value="">No {kind === 'mongo' ? 'MongoDB' : 'SQL'} data adapter (Admin → Data sources)</option>}
                {fitting.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.type || 'mongodb'})</option>)}
              </select>
            </label>
          )}
          {live && kind === 'mongo' && (
            <label className="field"><span>Collection <small className="muted">(or db.getCollection("X") in Mongo Shell)</small></span>
              <input value={collection} onChange={(e) => setCollection(e.target.value.trim())} placeholder="e.g. Orders" />
            </label>
          )}
          {live && (
            <label className="field qe-limit"><span>Rows shown</span>
              <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}>{[100, 1000, 5000, 10000].map((n) => <option key={n} value={n}>{n.toLocaleString()}</option>)}</select>
            </label>
          )}
        </div>
      )}

      <div className="qe-body">
        {mayEdit && (
          <aside className="qe-tables" aria-label="Datasets and columns">
            {!live && (
              <>
                <div className="qe-side-head">Tables <small className="muted">click to insert</small></div>
                {ready.length === 0 && <p className="muted small">No dataset is ready yet.</p>}
                {ready.map((d) => (
                  <div key={d.id} className="qe-table">
                    <div className="qe-table-row">
                      <button type="button" className="qe-twisty" onClick={() => setOpenTable(openTable === d.id ? '' : d.id)} aria-expanded={openTable === d.id}>{openTable === d.id ? '▾' : '▸'}</button>
                      <button type="button" className="qe-ins" onClick={() => insert(tableRef(d.name))} title={`Insert ${tableRef(d.name)}`}>{d.name}</button>
                      <small className="muted">{(d.rowCount || 0).toLocaleString()}</small>
                    </div>
                    {openTable === d.id && d.columns.map((c) => (
                      <button key={c.name} type="button" className="qe-ins qe-col" onClick={() => insert(columnRef(d.name, c.name))} title={`Insert ${columnRef(d.name, c.name)}`}>
                        <span className="qe-kind">{{ number: '#', text: 'Aa', date: '📅', bool: '✓' }[c.kind] || '·'}</span>{c.name}
                      </button>
                    ))}
                  </div>
                ))}
              </>
            )}
            {live && (
              <>
                <div className="qe-side-head">Live query</div>
                <p className="muted small">Runs on the database now and shows the first rows. To use it in DAX, SQL or a dashboard, press <b>Make a dataset</b>: all rows are imported and refreshed on a schedule.</p>
                {result?.columns?.length > 0 && (
                  <>
                    <div className="qe-side-head">Fields found</div>
                    {result.columns.map((c) => (
                      <button key={c.name} type="button" className="qe-ins qe-col" onClick={() => insert(kind === 'mongo' ? `"$${c.name}"` : c.name)} title="Insert">
                        <span className="qe-kind">{{ number: '#', text: 'Aa', date: '📅' }[c.type] || '·'}</span>{c.name}
                      </button>
                    ))}
                  </>
                )}
              </>
            )}
            <div className="qe-side-head" style={{ marginTop: 12 }}>Examples</div>
            {(EXAMPLES[mode] || []).map(([label], i) => <button key={label} type="button" className="qe-ins" onClick={() => example(i)}>{label}</button>)}
          </aside>
        )}
        <div className="qe-main">
          <textarea ref={editor} className="qe-text" value={text} spellCheck={false} readOnly={!mayEdit}
            onChange={(e) => setText(e.target.value)} onKeyDown={keys} aria-label="Query"
            placeholder={EXAMPLES[mode]?.[0]?.[1](kind === 'datasets' ? 'Sales' : 'Orders', 'Amount', 'Branch')} />
          <div className="qe-hint muted small">{HINTS[mode]} Ctrl+Enter runs.</div>
          {error && (
            <div className="notice error qe-error">
              <span>{error}</span>
              {errorLine > 0 && <button type="button" className="btn small" onClick={() => goToLine(errorLine)}>Go to line {errorLine}</button>}
            </div>
          )}
          {result && (
            <div className="qe-result">
              <div className="row qe-result-bar">
                <b>{result.rowCount.toLocaleString()} row{result.rowCount === 1 ? '' : 's'}{result.truncated ? ' (first ones only)' : ''}</b>
                <span className="muted small">{result.durationMs} ms{result.language === 'source' ? ` · live from ${result.connectionId}${result.collection ? ` / ${result.collection}` : ''}` : ''}</span>
                {result.language === 'dax' && <button type="button" className="btn small" onClick={() => setShowSql(!showSql)}>{showSql ? 'Hide' : 'Show'} the SQL</button>}
                <button type="button" className="btn small" onClick={exportCsv}>⤓ CSV</button>
              </div>
              {showSql && result.language === 'dax' && <pre className="qe-sql">{result.sql}</pre>}
              <div className="table-wrap qe-grid">
                <table className="grid-table">
                  <thead><tr>{result.columns.map((c) => <th key={c.name} title={c.type}>{c.name}</th>)}</tr></thead>
                  <tbody>{result.rows.map((row, i) => <tr key={i}>{row.map((v, j) => <td key={j} className={typeof v === 'number' ? 'num' : ''}>{v == null ? <span className="muted">(blank)</span> : typeof v === 'object' ? JSON.stringify(v) : String(v)}</td>)}</tr>)}</tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
