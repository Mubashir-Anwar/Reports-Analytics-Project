import { useEffect, useRef, useState } from 'react';
import { api } from './api.js';
import { inferColumns } from './designModel.js';
import { AdapterOptions, DbLogo } from './panels.jsx';
import { checkSetup, explainError, optionalFilters, placeOf } from './errorHelp.js';
import { reportPayload } from './store.js';
import CodeArea, { selectLine } from './CodeArea.jsx';
import FieldsEditor from './FieldsEditor.jsx';
import { PATTERN_PRESETS, RELATIVE_DATES, isRelativeDate, resolveRelative, startValue, valueProblem } from './relativeDates.js';

/** Report ID from the report name: letters/digits/_ in capitals, starting with a letter or digit. */
export function reportIdFrom(title) {
  const id = String(title || '').normalize('NFKD').replace(/[^A-Za-z0-9]+/g, '_').replace(/^_+|_+$/g, '').toUpperCase().slice(0, 60);
  return id || '';
}

/** The collection name only: also accepts a pasted db.getCollection("X") / db.X.aggregate(…) / "aggregate": "X". */
export function cleanCollection(value) {
  const text = String(value || '').trim();
  const found = text.match(/getCollection\(\s*["'`]([^"'`]+)["'`]\s*\)/) || text.match(/^db\.([A-Za-z0-9_.$-]+)\.(?:aggregate|find)\b/)
    || text.match(/"(?:aggregate|collectionName)"\s*:\s*"([^"]+)"/);
  return (found ? found[1] : text).replace(/^["'`]|["'`]$/g, '').trim();
}

const useAdapters = (notify, refreshKey) => {
  const [adapters, setAdapters] = useState(null); // null = loading
  const load = () => api.connections().then((r) => setAdapters(r.connections)).catch((error) => {
    setAdapters([]);
    notify(`Could not load data adapters: ${error.message}`, 'error');
  });
  useEffect(() => { load(); }, [refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps -- reload after adapters change
  return [adapters, load];
};

/** Data adapter, collection, query and fields (Jasper: Dataset and Query → Query). */
/**
 * tests: show the Test connection / Check query / Try with data tools (Setup only).
 * wrap(key, content, { summary, issues, tested }): puts the adapter, query and fields parts in their own segments.
 */
export function QueryEditor({ state, dispatch, notify, refreshKey, markLine, markNote, tests = false, wrap, onFocus, onMeta, onDataScreen }) {
  const { report } = state;
  const [busy, setBusy] = useState(false);
  const [adapters, loadAdapters] = useAdapters(notify, refreshKey);
  const set = (patch) => dispatch({ type: 'REPORT', patch });
  const known = (adapters || []).some((a) => a.id === report.connectionId);
  const selected = (adapters || []).find((a) => a.id === report.connectionId);
  const isSql = report.queryLanguage === 'sql';
  /** Choosing an adapter of another kind switches the query language (SQL for PostgreSQL, Mongo shell for MongoDB). */
  const chooseAdapter = (connectionId) => {
    const adapter = (adapters || []).find((a) => a.id === connectionId);
    const sql = Boolean(adapter && adapter.type && adapter.type !== 'mongodb');
    const patch = { connectionId };
    if (sql && !isSql) Object.assign(patch, { queryLanguage: 'sql', collection: '' });
    if (adapter && !sql && isSql) patch.queryLanguage = 'mongosh';
    set(patch);
    if (adapter && sql !== isSql) notify(sql ? 'This adapter is a SQL database: write the query in SQL (SELECT …)' : 'This adapter is MongoDB: write an aggregation query', 'ok');
  };

  const adapterIsSql = Boolean(selected && selected.type && selected.type !== 'mongodb');
  const kindMismatch = Boolean(selected) && adapterIsSql !== isSql;
  /** Picking SQL keeps the adapter if it is a SQL one; otherwise it suggests the first SQL adapter. */
  const chooseLanguage = (language) => {
    const toSql = language === 'sql';
    const patch = { queryLanguage: language, ...(toSql ? { collection: '' } : {}) };
    const fits = (a) => ((a.type || 'mongodb') !== 'mongodb') === toSql;
    if (selected && !fits(selected)) {
      const other = (adapters || []).find((a) => fits(a) && a.configured) || (adapters || []).find(fits);
      if (other) { patch.connectionId = other.id; notify(`Data adapter switched to “${other.name}” (${toSql ? 'SQL' : 'MongoDB'}) to match the query language`, 'ok'); }
    }
    set(patch);
  };

  /** New parameters found in the query: Required, unless the report already treats an empty value as "all records". */
  const askAbout = (added) => {
    const fresh = added.map((name) => ({ name, type: 'string', label: name, required: true }));
    const filters = optionalFilters({ ...report, parameters: [...report.parameters, ...fresh] });
    return fresh.map((p) => (filters[p.name] ? { ...p, required: false } : p));
  };

  const readSqlFields = async () => {
    setBusy(true);
    try {
      const result = await api.queryFields(report.connectionId, report.querySource);
      const knownParams = new Set(report.parameters.map((p) => p.name));
      const added = result.parameterNames.filter((name) => !knownParams.has(name));
      set({ columns: result.columns, ...(added.length ? { parameters: [...report.parameters, ...askAbout(added)] } : {}) });
      notify(`${result.columns.length} fields read from the database${added.length ? ` · new parameter(s) ${added.join(', ')} (type string — change it in Parameters if needed)` : ''}`, 'ok');
    } catch (error) { notify(`The fields could not be read: ${error.message}`, 'error'); } finally { setBusy(false); }
  };

  const detect = async () => {
    setBusy(true);
    try {
      // Parsed without the parameter check: $P{...} names the report does not have yet are added as questions.
      const result = await api.parseQuery(report.queryLanguage, report.querySource);
      const columns = inferColumns(result.pipeline);
      const knownParams = new Set(report.parameters.map((p) => p.name));
      const added = (result.parameterNames || []).filter((name) => !knownParams.has(name));
      const patch = {};
      if (added.length) patch.parameters = [...report.parameters, ...askAbout(added)];
      if (result.collection && !report.collection.trim()) patch.collection = result.collection;
      if (columns.length) patch.columns = columns;
      if (Object.keys(patch).length) set(patch);
      const extras = [
        patch.collection ? `collection ${patch.collection}` : '',
        added.length ? `new parameter(s) ${added.join(', ')} (type string — change it in Parameters if needed)` : '',
      ].filter(Boolean).join('; ');
      if (!columns.length) {
        notify(`No columns found in the last $project/$group/$count stage. Type them below instead.${extras ? ` Added: ${extras}.` : ''}`, 'error');
      } else {
        notify(`${result.stageCount} stages parsed, ${columns.length} fields found${extras ? ` · added ${extras}` : ''}`, 'ok');
      }
    } catch (error) { notify(`The query could not be parsed: ${error.message}`, 'error'); } finally { setBusy(false); }
  };

  // ---- tests (Setup only): connection, query check, a run with test values; reset when what they tested changes
  const [connTest, setConnTest] = useState(null); // { state: busy|ok|error, text }
  const [queryTest, setQueryTest] = useState(null); // { state, text, error?, rows?, columns? }
  const [testValues, setTestValues] = useState({});
  useEffect(() => { setConnTest(null); }, [report.connectionId]);
  useEffect(() => { setQueryTest(null); }, [report.querySource, report.queryLanguage, report.collection, report.connectionId]);
  const asked = (report.parameters || []).filter((p) => p.prompt !== false && p.name);
  const testValueOf = (p) => testValues[p.name] ?? startValue(p);

  const testConnection = async () => {
    setConnTest({ state: 'busy', text: 'Connecting…' });
    try {
      const r = await api.testConnection(report.connectionId);
      setConnTest({ state: 'ok', text: r.message || 'Connected' });
    } catch (error) { setConnTest({ state: 'error', text: error.message, error: explainError(error.message) }); }
  };
  const checkQuery = async () => {
    setQueryTest({ state: 'busy', text: 'Checking the query…' });
    try {
      if (isSql) {
        const r = await api.queryFields(report.connectionId, report.querySource);
        setQueryTest({ state: 'ok', text: `The database accepts the query: ${r.columns.length} column(s) (no rows were read).` });
      } else {
        const r = await api.parseQuery(report.queryLanguage, report.querySource, report.parameters);
        setQueryTest({ state: 'ok', text: `The query is valid: ${r.stageCount} stage(s)${r.collection ? ` on ${r.collection}` : ''}. It was only checked, not run.` });
      }
    } catch (error) { setQueryTest({ state: 'error', text: error.message, error: explainError(error.message) }); }
  };
  const tryQuery = async () => {
    const empty = asked.filter((p) => p.required && !String(testValueOf(p)).trim());
    if (empty.length) { setQueryTest({ state: 'error', text: `Fill in the test value of ${empty.map((p) => p.label || p.name).join(', ')} first.` }); return; }
    setQueryTest({ state: 'busy', text: 'Reading the first records…' });
    try {
      const values = Object.fromEntries(asked.map((p) => [p.name, testValueOf(p)]));
      const data = await api.draftPreview(reportPayload(state), values, report.connectionId);
      setQueryTest({ state: 'ok', text: data.rowCount ? `It works: first ${data.rowCount} record(s) shown below.` : 'It works, but no records match these test values.', rows: data.rows.slice(0, 5), columns: data.columns });
    } catch (error) { setQueryTest({ state: 'error', text: error.message, error: explainError(error.message) }); }
  };

  const sourceIssues = [];
  if (selected && !selected.configured) sourceIssues.push({ level: 'error', text: 'This adapter has no saved connection URL yet', fix: 'Add it in the Data Adapters tab or on /admin → Data sources.' });
  if (adapters !== null && report.connectionId && !known) sourceIssues.push({ level: 'error', text: 'This adapter ID is not registered on this server', fix: 'Choose one from the list.' });
  if (kindMismatch) sourceIssues.push({ level: 'error', text: isSql ? 'SQL query, but a MongoDB adapter' : 'MongoDB query, but a SQL adapter', fix: isSql ? 'Choose a SQL data adapter.' : 'Choose a MongoDB adapter, or Query language SQL.' });
  const languageName = { mongosh: 'Mongo Shell', 'aggregation-json': 'Aggregation JSON', javascript: 'JavaScript', python: 'Python', sql: 'SQL' }[report.queryLanguage] || report.queryLanguage;
  const lineCount = report.querySource.trim() ? report.querySource.trim().split('\n').length : 0;
  const usedParams = [...new Set([...report.querySource.matchAll(/\$[PX]\{(?:IN,\s*[^,]+,\s*)?(\w+)\}/g)].map((m) => m[1]))];

  const readFieldsRow = (
    <div className="row" data-guide="fields">
      <button className="btn primary" onClick={isSql ? readSqlFields : detect} disabled={busy || !report.querySource.trim() || (isSql && !report.connectionId)}>Read fields</button>
      <span className="muted">{isSql ? 'Asks the database for the columns (LIMIT 0): no rows are read, nothing is changed.' : 'Reads the field names from the query ($project / $group). The query is only parsed, not executed.'}</span>
    </div>
  );

  const sourcePart = (
    <>
      <div className="form-grid">
        <label data-focus="adapter" data-guide="adapter">Data adapter <small>(the database connection this report reads from)</small>
          <span className="inline">
            {selected && <DbLogo type={selected.type || 'mongodb'} />}
            <select value={report.connectionId} onChange={(e) => chooseAdapter(e.target.value)} disabled={adapters === null}>
              {adapters === null && <option value={report.connectionId}>Loading…</option>}
              {adapters !== null && <option value="">Choose a data adapter…</option>}
              {adapters !== null && report.connectionId && !known && <option value={report.connectionId}>{report.connectionId} (not registered)</option>}
              <AdapterOptions adapters={adapters} describe={(a) => `${a.name}${a.database ? ` · ${a.database}` : ''}${a.configured ? '' : ' — not configured'}`} />
            </select>
            <button type="button" className="btn sm" onClick={loadAdapters} title="Reload the adapter list">↻</button>
            {onDataScreen && <button type="button" className="btn sm" onClick={onDataScreen} title="Open the data screen: saved adapters by folder, or connect a new database">＋ Connect / browse</button>}
          </span>
          {!wrap && selected && !selected.configured && <small className="err">This adapter has no saved connection URL yet. Add it in the Data Adapters tab or on /admin → Data sources.</small>}
          {!wrap && adapters !== null && report.connectionId && !known && <small className="err">This adapter ID is not registered on this server. Choose one from the list.</small>}
        </label>
        <label data-focus="language">Query language
          <select value={report.queryLanguage} onChange={(e) => chooseLanguage(e.target.value)}>
            <optgroup label="MongoDB">
              <option value="mongosh">Mongo Shell</option><option value="aggregation-json">Aggregation JSON</option>
              <option value="javascript">JavaScript pipeline</option><option value="python">Python pipeline</option>
            </optgroup>
            <optgroup label="SQL databases">
              <option value="sql">SQL — PostgreSQL, MySQL, SQL Server, Oracle</option>
            </optgroup>
          </select>
          {!wrap && kindMismatch && <small className="err">{isSql ? 'This is a SQL query: choose a SQL data adapter above (PostgreSQL, MySQL, SQL Server or Oracle).' : 'This adapter is a SQL database: choose a MongoDB adapter, or Query language SQL.'}</small>}
        </label>
        {!isSql && (
          <label data-focus="collection" data-guide="collection">Collection name <small>(only the name, e.g. Orders; filled in from a Jasper runCommand query)</small>
            <input value={report.collection} onChange={(e) => set({ collection: e.target.value })} onBlur={(e) => { const clean = cleanCollection(e.target.value); if (clean !== e.target.value) set({ collection: clean }); }} placeholder="Collection name" />
          </label>
        )}
      </div>
      {tests && (
        <div className="seg-test" data-guide="conn-test">
          <button type="button" className="btn" onClick={testConnection} disabled={!report.connectionId || !known || connTest?.state === 'busy'}>🔌 Test connection</button>
          <TestResult result={connTest} onFocus={onFocus} idle="Checks that the server can reach this database. Nothing is read or changed." />
        </div>
      )}
    </>
  );

  const queryPart = (
    <>
      {isSql ? (
        <div className="block query-block" data-guide="query">SQL query <small>(one SELECT; parameters: $P&#123;StartDate&#125;; a list filter: $X&#123;IN, branch, Branches&#125; — an empty list = no filter)</small>
          <CodeArea focusName="query" markLine={markLine} markNote={markNote} rows={14} value={report.querySource} onChange={(e) => set({ querySource: e.target.value })} placeholder={'SELECT o.id, o.branch, o.amount, o.placed_on\nFROM orders o\nWHERE o.placed_on BETWEEN $P{StartDate} AND $P{EndDate}\n  AND $X{IN, o.branch, Branches}\nORDER BY o.placed_on'} />
        </div>
      ) : (
        <div className="block query-block" data-guide="query">Query <small>(parameters: $P&#123;StartDate&#125;; db.collection.aggregate([...]) or a Jaspersoft Studio &#123;"runCommand": &#123;"aggregate": …, "pipeline": [...]&#125;&#125; query)</small>
          <CodeArea focusName="query" markLine={markLine} markNote={markNote} rows={14} value={report.querySource} onChange={(e) => set({ querySource: e.target.value })} placeholder={'db.getCollection("Orders").aggregate([\n  {"$match": {"Date": {"$gte": $P{StartDate}, "$lte": $P{EndDate}}}},\n  {"$project": {"Identifier": 1, "Branch": 1, "Amount": 1}}\n])'} />
        </div>
      )}
      {!tests && readFieldsRow}
      {tests && (
        <div className="seg-test" data-guide="query-test">
          <div className="row">
            <button type="button" className="btn" onClick={checkQuery} disabled={!report.querySource.trim() || (isSql && !report.connectionId) || queryTest?.state === 'busy'}>✔ Check query</button>
            <button type="button" className="btn" onClick={tryQuery} disabled={!report.querySource.trim() || !report.connectionId || !known || queryTest?.state === 'busy'}>▶ Try with data</button>
            <span className="muted">Check = syntax only. Try = reads up to 20 records with the test values below.</span>
          </div>
          {asked.length > 0 && (
            <div className="test-values">
              <b>Test values</b>
              {asked.map((p) => (
                <label key={p.name}>{p.label || p.name}{p.required ? ' *' : ''}
                  {p.allowedValues?.length ? (
                    <select value={testValueOf(p)} onChange={(e) => setTestValues((v) => ({ ...v, [p.name]: e.target.value }))}>
                      <option value="">—</option>{p.allowedValues.map((a) => <option key={a}>{a}</option>)}
                    </select>
                  ) : (
                    <input type={p.type === 'date' ? 'date' : p.type === 'integer' || p.type === 'number' ? 'number' : 'text'} value={testValueOf(p)}
                      onChange={(e) => setTestValues((v) => ({ ...v, [p.name]: e.target.value }))} />
                  )}
                </label>
              ))}
            </div>
          )}
          <TestResult result={queryTest} onFocus={onFocus} idle="Not tested yet." />
          {queryTest?.rows?.length > 0 && (
            <div className="test-rows">
              <table className="grid">
                <thead><tr>{queryTest.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
                <tbody>{queryTest.rows.map((row, i) => (
                  // eslint-disable-next-line react/no-array-index-key -- rows have no id
                  <tr key={i}>{queryTest.columns.map((c) => <td key={c}>{row[c] === null || row[c] === undefined ? <span className="muted">—</span> : String(typeof row[c] === 'object' ? JSON.stringify(row[c]) : row[c])}</td>)}</tr>
                ))}</tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </>
  );

  const fieldsPart = (
    <>
      {tests && readFieldsRow}
      <FieldsEditor columns={report.columns} design={state.design} calculated={(state.design?.calculatedFields || []).map((c) => c.name)}
        onChange={(columns) => set({ columns })} sample={queryTest?.rows?.[0]}
        formats={tests ? state.design?.fieldFormats || {} : undefined}
        onFormats={tests ? (fieldFormats) => dispatch({ type: 'DESIGN', key: 'field-formats', update: (d) => ({ ...d, fieldFormats }) }) : undefined} />
    </>
  );

  const queryState = queryTest && queryTest.state !== 'busy' ? queryTest.state : null;
  const connState = connTest && connTest.state !== 'busy' ? connTest.state : null;
  const partsKey = JSON.stringify({ source: { issues: sourceIssues, tested: connState }, query: { issues: [], tested: queryState }, fields: { issues: [] } });
  useEffect(() => { if (onMeta) onMeta(JSON.parse(partsKey)); }, [partsKey]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!wrap) return <>{sourcePart}{queryPart}{fieldsPart}</>;
  return (
    <>
      {wrap('source', sourcePart, {
        summary: selected ? `${selected.name}${selected.database ? ` · ${selected.database}` : ''} · ${languageName}${!isSql && report.collection ? ` · ${report.collection}` : ''}` : 'No adapter chosen',
        issues: sourceIssues, tested: connState,
      })}
      {wrap('query', queryPart, {
        summary: lineCount ? `${lineCount} line${lineCount > 1 ? 's' : ''}${usedParams.length ? ` · uses ${usedParams.join(', ')}` : ''}` : 'Empty',
        issues: [], tested: queryState,
      })}
      {wrap('fields', fieldsPart, {
        summary: report.columns.length ? `${report.columns.length} field${report.columns.length > 1 ? 's' : ''}: ${report.columns.slice(0, 4).join(', ')}${report.columns.length > 4 ? '…' : ''}` : 'No fields yet',
        issues: [],
      })}
    </>
  );
}

/** The outcome of a segment test: a one-line status, and for a failure the plain-words explanation and "Go there". */
function TestResult({ result, onFocus, idle }) {
  if (!result) return <p className="test-result idle">{idle}</p>;
  const info = result.error;
  return (
    <div className={`test-result ${result.state}`} role="status">
      <p><span className="dot" />{result.state === 'error' && info?.title ? <b>{info.title}: </b> : null}{result.text}</p>
      {result.state === 'error' && info && (
        <>
          {info.hint && <small>{info.hint}</small>}
          {info.steps?.length > 0 && <ol>{info.steps.map((s) => <li key={s}>{s}</li>)}</ol>}
          {info.focus && onFocus && <button type="button" className="btn sm" onClick={() => onFocus({ ...info.focus, note: `${info.title}. ${info.steps?.[0] || ''}` })}>Go there{info.place ? ` · ${info.place}` : ''}</button>}
        </>
      )}
    </div>
  );
}

/** Comma-separated list input: the text is kept while typing and turned into a list when the field is left. */
function AllowedValuesInput({ values, onChange, needed }) {
  const joined = (values || []).join(', ');
  const [text, setText] = useState(joined);
  useEffect(() => { setText(joined); }, [joined]);
  const commit = () => {
    const list = [...new Set(text.split(',').map((v) => v.trim()).filter(Boolean))];
    if (list.join(', ') !== joined) onChange(list);
    else setText(joined);
  };
  return (
    <input value={text} placeholder={needed ? 'needed, e.g. CAR, BIKE' : 'any value'}
      className={needed && !(values || []).length ? 'needs-value' : undefined}
      title="Comma-separated list. The user then picks one of these values instead of typing."
      onChange={(e) => setText(e.target.value)} onBlur={commit}
      onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); }} />
  );
}

/** A date that may be relative: Today, Start of this month… or a fixed day. '' = none. */
function DateValue({ value, onChange, none = 'None' }) {
  const relative = isRelativeDate(value);
  const mode = !value ? '' : relative ? String(value).replace(/\s+/g, '') : 'fixed';
  const known = RELATIVE_DATES.some(([v]) => v === mode);
  return (
    <span className="pe-date">
      <select value={mode === 'fixed' || known || !mode ? mode : 'fixed'} onChange={(e) => {
        const next = e.target.value;
        if (next === 'fixed') onChange(resolveRelative('{today}'));
        else onChange(next || undefined);
      }}>
        <option value="">{none}</option>
        {RELATIVE_DATES.map(([v, text]) => <option key={v} value={v}>{text}</option>)}
        <option value="fixed">A fixed date…</option>
      </select>
      {mode === 'fixed' && <input type="date" value={value} onChange={(e) => onChange(e.target.value || undefined)} />}
      {relative && <small className="muted">= {resolveRelative(value)} today</small>}
    </span>
  );
}

/** How many extra options a parameter has (for the "More" button). */
const optionCount = (p) => ['description', 'default', 'min', 'max', 'pattern', 'allowedValues', 'sensitive']
  .filter((k) => (Array.isArray(p[k]) ? p[k].length : p[k] !== undefined && p[k] !== '' && p[k] !== false && p[k] !== null)).length;

/** The extra options of one parameter: help text, default, limits, a text check, the list of values, sensitive. */
function ParameterOptions({ p, patch, needed }) {
  const isNumber = p.type === 'integer' || p.type === 'number';
  const preset = PATTERN_PRESETS.find(([re]) => re === (p.pattern || ''));
  const [custom, setCustom] = useState(Boolean(p.pattern) && !preset);
  if (p.prompt === false) {
    return <div className="pe-more"><p className="muted">Calculated from other parameters, never asked: <code>{p.expression}</code></p></div>;
  }
  return (
    <div className="pe-more">
      <label className="wide">Help text <small>(shown under the field when the user runs the report)</small>
        <input value={p.description || ''} onChange={(e) => patch({ description: e.target.value || undefined })} placeholder="e.g. The first day of the period" />
      </label>
      <label>Default value <small>(filled in for the user)</small>
        {p.type === 'date' ? <DateValue value={p.default} onChange={(v) => patch({ default: v })} />
          : p.type === 'boolean' ? (
            <select value={p.default === undefined || p.default === null ? '' : String(p.default)} onChange={(e) => patch({ default: e.target.value === '' ? undefined : e.target.value === 'true' })}>
              <option value="">None</option><option value="true">Yes</option><option value="false">No</option>
            </select>
          ) : p.allowedValues?.length ? (
            <select value={p.default ?? ''} onChange={(e) => patch({ default: e.target.value || undefined })}>
              <option value="">None</option>{p.allowedValues.map((v) => <option key={v}>{v}</option>)}
            </select>
          ) : (
            <input type={isNumber ? 'number' : 'text'} value={p.default ?? ''} placeholder="None"
              onChange={(e) => patch({ default: e.target.value === '' ? undefined : isNumber ? Number(e.target.value) : e.target.value })} />
          )}
      </label>
      {isNumber && (
        <span className="pe-pair">
          <label>Smallest<input type="number" value={p.min ?? ''} placeholder="any" onChange={(e) => patch({ min: e.target.value === '' ? undefined : Number(e.target.value) })} /></label>
          <label>Largest<input type="number" value={p.max ?? ''} placeholder="any" onChange={(e) => patch({ max: e.target.value === '' ? undefined : Number(e.target.value) })} /></label>
        </span>
      )}
      {p.type === 'date' && (
        <>
          <label>Earliest date allowed<DateValue value={p.min} none="Any" onChange={(v) => patch({ min: v })} /></label>
          <label>Latest date allowed<DateValue value={p.max} none="Any" onChange={(v) => patch({ max: v })} /></label>
        </>
      )}
      {p.type === 'string' && (
        <>
          <label>Check the text <small>(the value must look like this)</small>
            <select value={custom ? '__custom' : p.pattern || ''} onChange={(e) => {
              if (e.target.value === '__custom') { setCustom(true); return; }
              setCustom(false);
              const chosen = PATTERN_PRESETS.find(([re]) => re === e.target.value);
              patch({ pattern: e.target.value || undefined, patternMessage: chosen?.[2] || undefined });
            }}>
              {PATTERN_PRESETS.map(([re, text]) => <option key={text} value={re}>{text}</option>)}
              <option value="__custom">Custom (regular expression)…</option>
            </select>
            {custom && <input className="code" value={p.pattern || ''} placeholder="e.g. PK\d{4}" onChange={(e) => patch({ pattern: e.target.value || undefined })} />}
          </label>
          {p.pattern && (
            <label>Message when it does not match
              <input value={p.patternMessage || ''} placeholder={`${p.label || p.name} is not in the right format`} onChange={(e) => patch({ patternMessage: e.target.value || undefined })} />
            </label>
          )}
        </>
      )}
      {p.type !== 'boolean' && p.type !== 'date' && (
        <label className="wide">List of values <small>(comma-separated: the user picks one instead of typing)</small>
          <AllowedValuesInput values={p.allowedValues} needed={needed}
            onChange={(list) => patch(list.length ? { allowedValues: list } : { allowedValues: undefined })} />
        </label>
      )}
      <label className="pe-check"><input type="checkbox" checked={p.sensitive === true} onChange={(e) => patch({ sensitive: e.target.checked ? true : undefined })} />
        Sensitive <small>(CNIC, account number…: kept masked like 42*****71 in the run log)</small>
      </label>
    </div>
  );
}

/** What the user sees on the run screen, filled with the defaults; typing shows the same checks as the server. */
function RunFormPreview({ parameters }) {
  const asked = parameters.filter((p) => p.prompt !== false && p.name);
  const [values, setValues] = useState({});
  if (!asked.length) return null;
  const valueOf = (p) => values[p.name] ?? startValue(p);
  return (
    <div className="pe-preview">
      <b>What the user sees when running the report</b>
      <div className="pe-preview-form">
        {asked.map((p) => {
          const problem = valueProblem(p, valueOf(p));
          const set = (v) => setValues((s) => ({ ...s, [p.name]: v }));
          return (
            <label key={p.name} className={problem ? 'bad' : ''}>
              <span>{p.label || p.name}{p.required && <i aria-label="required"> *</i>}</span>
              {p.allowedValues?.length ? (
                <select value={valueOf(p)} onChange={(e) => set(e.target.value)}>{!p.required && <option value="">—</option>}{p.allowedValues.map((v) => <option key={v}>{v}</option>)}</select>
              ) : p.type === 'boolean' ? (
                <select value={valueOf(p)} onChange={(e) => set(e.target.value)}><option value="true">Yes</option><option value="false">No</option></select>
              ) : (
                <input type={p.type === 'date' ? 'date' : p.type === 'integer' || p.type === 'number' ? 'number' : 'text'} value={valueOf(p)}
                  min={p.type === 'date' && p.min ? resolveRelative(p.min) : p.min} max={p.type === 'date' && p.max ? resolveRelative(p.max) : p.max}
                  onChange={(e) => set(e.target.value)} />
              )}
              {p.description && <small>{p.description}</small>}
              {problem && <small className="err">{problem}</small>}
            </label>
          );
        })}
      </div>
    </div>
  );
}

/** Report parameters (input controls): one clear line each, the rest under "More", and a preview of the run form. */
export function ParametersEditor({ state, dispatch }) {
  const { report } = state;
  const set = (patch) => dispatch({ type: 'REPORT', patch });
  const [openRows, setOpenRows] = useState(() => new Set());
  const addParam = () => {
    set({ parameters: [...report.parameters, { name: `Param${report.parameters.length + 1}`, type: 'string', label: '', required: false }] });
    setOpenRows((s) => new Set([...s, report.parameters.length]));
  };
  const patchParam = (index, values) => set({ parameters: report.parameters.map((p, i) => {
    if (i !== index) return p;
    const next = { ...p, ...values };
    Object.keys(values).forEach((k) => { if (values[k] === undefined) delete next[k]; });
    return next;
  }) });
  // A new type keeps only the options that still make sense (no min/max on text, no pattern on numbers).
  const changeType = (index, type) => {
    const p = report.parameters[index];
    const drop = { default: undefined, min: undefined, max: undefined };
    if (type !== 'string') Object.assign(drop, { pattern: undefined, patternMessage: undefined });
    if (type === 'date' || type === 'boolean') drop.allowedValues = undefined;
    patchParam(index, { type, ...(p.type === type ? {} : drop) });
  };
  const toggle = (i) => setOpenRows((s) => { const n = new Set(s); if (n.has(i)) n.delete(i); else n.add(i); return n; });
  // Parameters used in the collection name (e.g. Edith-$P{ProductCode}-PaymentPlans) must list their allowed values.
  const inCollection = new Set([...String(report.collection || '').matchAll(/\$P\{(\w+)\}/g)].map((m) => m[1]));
  // With fixed text around it (Edith-…-PaymentPlans) the user may type the value; a bare $P{X} needs a list.
  const shaped = String(report.collection || '').replace(/\$P\{\w+\}/g, '').replace(/[^A-Za-z0-9]/g, '').length >= 3;
  const missing = shaped ? [] : report.parameters.filter((p) => inCollection.has(p.name) && !(p.allowedValues || []).length);
  return (
    <>
      {missing.length > 0 && (
        <p className="hint warn">
          The collection name <code>{report.collection}</code> is built from {missing.map((p) => p.name).join(', ')}.
          The name has no fixed text, so for safety list the values a user may choose under “More → List of values” (e.g. <code>CAR, BIKE</code>), or put fixed text around the parameter (e.g. <code>Orders-$P&#123;Branch&#125;</code>).
        </p>
      )}
      <div className="pe-list" data-guide="parameters">
        {report.parameters.length > 0 && (
          <div className="pe-row pe-headrow" aria-hidden="true"><span>Name (URL key)</span><span>Label (what the user sees)</span><span>Type</span><span>Required</span><span /><span /></div>
        )}
        {report.parameters.map((p, i) => {
          const needed = missing.includes(p);
          const open = openRows.has(i) || needed;
          const count = optionCount(p);
          return (
            // eslint-disable-next-line react/no-array-index-key -- a name can be edited, the position is the identity
            <div key={i} className={`pe-item ${open ? 'open' : ''}`} data-focus={`param:${p.name}`}>
              <div className="pe-row">
                <input aria-label="Name" value={p.name} onChange={(e) => patchParam(i, { name: e.target.value.replace(/[^A-Za-z0-9_]/g, '') })} />
                <input aria-label="Label" value={p.label || ''} placeholder={labelFrom(p.name)} onChange={(e) => patchParam(i, { label: e.target.value })} />
                <select aria-label="Type" value={p.type} onChange={(e) => changeType(i, e.target.value)}>
                  {[['string', 'Text'], ['date', 'Date'], ['integer', 'Whole number'], ['number', 'Number'], ['boolean', 'Yes / No']].map(([v, t]) => <option key={v} value={v}>{t}</option>)}
                </select>
                {p.prompt === false ? <small className="pe-computed" title="Computed from other parameters">ƒ computed</small>
                  : <label className="pe-req"><input type="checkbox" checked={Boolean(p.required)} onChange={(e) => patchParam(i, { required: e.target.checked })} /><span>Required</span></label>}
                <button type="button" className={`btn sm pe-more-btn ${open ? 'on' : ''}`} aria-expanded={open} onClick={() => toggle(i)}>
                  ⚙ More{count ? <span className="pe-count">{count}</span> : null}
                </button>
                <button type="button" className="icon" aria-label={`Delete ${p.name}`} onClick={() => set({ parameters: report.parameters.filter((_, j) => j !== i) })}>✕</button>
              </div>
              {open && <ParameterOptions p={p} needed={needed} patch={(values) => patchParam(i, values)} />}
            </div>
          );
        })}
        {!report.parameters.length && <p className="muted pe-empty">No parameters: the report runs without asking anything. Add one when the query uses $P&#123;…&#125;.</p>}
      </div>
      <div className="row">
        <button className="btn" onClick={addParam}>+ Add parameter</button>
        <button className="btn" onClick={() => set({ parameters: [
          { name: 'StartDate', type: 'date', label: 'Start date', required: true, default: '{monthStart}', description: 'The first day of the period' },
          { name: 'EndDate', type: 'date', label: 'End date', required: true, default: '{today}', max: '{today}', description: 'The last day of the period (not after today)' },
        ] })}>Start/End date preset</button>
      </div>
      <RunFormPreview parameters={report.parameters} />
    </>
  );
}

/** Scroll to the place an error points to and highlight it: a query line is selected, a field is selected in Fields. */
function useFocusPlace(root, focus) {
  useEffect(() => {
    if (!focus || !root.current) return undefined;
    const box = root.current;
    const esc = (v) => (window.CSS?.escape ? window.CSS.escape(v) : String(v).replace(/"/g, '\\"'));  // eslint-disable-line no-useless-escape
    const element = (focus.target === 'parameters' && focus.name && box.querySelector(`[data-focus="param:${esc(focus.name)}"]`))
      || box.querySelector(`[data-focus="${focus.target}"]`);
    if (!element) return undefined;
    const timer = setTimeout(() => {
      element.scrollIntoView({ block: 'center', behavior: 'smooth' });
      element.classList.add('focus-flash');
      if (focus.target === 'query' && element.querySelector('textarea')) {
        selectLine(element.querySelector('textarea'), focus.line || 1);
      } else if (focus.target === 'fields') {
        const chip = focus.name && [...element.querySelectorAll('[data-field]')].find((n) => n.dataset.field === focus.name);
        if (chip) {
          chip.closest('details')?.setAttribute('open', '');
          chip.scrollIntoView({ block: 'center' });
          chip.classList.add('focus-flash');
          setTimeout(() => chip.classList.remove('focus-flash'), 4200);
        } else element.querySelector('.fe-add input')?.focus({ preventScroll: true });
      } else {
        element.querySelector('input, select, textarea')?.focus({ preventScroll: true });
      }
    }, 60);
    const off = setTimeout(() => element.classList.remove('focus-flash'), 4200);
    return () => { clearTimeout(timer); clearTimeout(off); element.classList.remove('focus-flash'); };
  }, [focus?.nonce]); // eslint-disable-line react-hooks/exhaustive-deps
}

const SEGMENTS = [
  ['details', 'Report details', 'Name, ID and folder'],
  ['source', 'Data adapter', 'Which database the report reads'],
  ['query', 'Query', 'What data the report reads'],
  ['fields', 'Fields', 'The columns the query returns'],
  ['parameters', 'Parameters', 'What the user types before running'],
  ['layout', 'Layout', 'How the report looks'],
];
/** The segment a problem belongs to, from the place it points to. */
const segmentOf = (focus) => ({ adapter: 'source', language: 'source', collection: 'source', query: 'query', fields: 'fields', parameters: 'parameters', design: 'layout' }[focus?.target] || 'details');

/** ok | todo (not started / optional, grey) | warn | error from the segment's problems and its last test. */
function statusOf(issues, tested) {
  if (issues.some((p) => p.level === 'error') || tested === 'error') return 'error';
  if (issues.some((p) => p.level === 'warn')) return 'warn';
  if (issues.some((p) => p.level === 'todo')) return 'todo';
  return 'ok';
}
function badgeText(issues, tested) {
  const errors = issues.filter((p) => p.level === 'error').length;
  const warns = issues.filter((p) => p.level === 'warn').length;
  const parts = [];
  if (errors) parts.push(`${errors} error${errors > 1 ? 's' : ''}`);
  if (warns) parts.push(`${warns} warning${warns > 1 ? 's' : ''}`);
  if (tested === 'error') parts.push('test failed');
  if (parts.length) return parts.join(' · ');
  if (issues.some((p) => p.level === 'todo')) return issues.every((p) => /optional|No data yet/.test(p.text)) ? 'Optional' : 'To do';
  return tested === 'ok' ? 'Tested ✓' : 'Looks fine';
}

/** One collapsible part of Setup: number, title, one-line summary and status; its problems first, then its form. */
function Segment({ id, index, title, hint, summary, issues: allIssues, tested, open, onToggle, onFocus, hiddenSeg, bare, children }) {
  const status = statusOf(allIssues, tested);
  const issues = bare ? allIssues.filter((p) => p.level === 'error' || p.level === 'warn') : allIssues;
  return (
    <section hidden={hiddenSeg} className={`card seg ${status} ${open ? 'open' : ''} ${bare ? 'bare' : ''}`} id={`seg-${id}`} data-focus={id === 'details' || id === 'parameters' ? id : undefined}>
      {!bare && <button type="button" className="seg-head" aria-expanded={open} onClick={onToggle}>
        <span className={`step ${status}`}>{index + 1}</span>
        <span className="seg-title"><b>{title}</b><small>{open ? hint : summary}</small></span>
        <span className={`seg-badge ${status}`}>{badgeText(issues, tested)}</span>
        <span className="seg-chevron" aria-hidden="true">{open ? '▾' : '▸'}</span>
      </button>}
      <div className="seg-body" hidden={!open}>
        {issues.length > 0 && (
          <ul className="seg-issues">
            {issues.map((p) => (
              <li key={`${p.text}-${p.place || ''}`} className={p.level}>
                <div><span>{p.text}</span><small>{p.fix}{p.place ? ` · ${p.place}` : ''}</small></div>
                {p.focus && onFocus && <button type="button" className="btn sm" onClick={() => onFocus({ ...p.focus, note: `${p.text}. ${p.fix}` })}>Show me</button>}
              </li>
            ))}
          </ul>
        )}
        {children}
      </div>
    </section>
  );
}

/** "StartDate" → "Start date" (a label made from a parameter name). */
const labelFrom = (name) => String(name || '').replace(/_/g, ' ').replace(/([a-z0-9])([A-Z])/g, '$1 $2').toLowerCase().replace(/^./, (c) => c.toUpperCase());

/** A starter query for the chosen language and collection: the user then changes the names to their own. */
function starterQuery(report) {
  if (report.queryLanguage === 'sql') {
    return 'SELECT branch, amount, placed_on\nFROM orders\nWHERE placed_on BETWEEN $P{StartDate} AND $P{EndDate}\nORDER BY placed_on';
  }
  const collection = String(report.collection || '').trim() || 'Orders';
  return `db.getCollection("${collection}").aggregate([\n  {"$match": {"Date": {"$gte": $P{StartDate}, "$lte": $P{EndDate}}}},\n  {"$project": {"_id": 0, "Branch": 1, "Amount": 1, "Date": 1}}\n])`;
}

/**
 * Guided mode: every step as small tasks, one shown at a time. A task is
 * { key, ask (the question), why, example, examples (chips that fill the field), apply ({ label, run }), guide (the
 *   field to highlight below: data-guide), done, optional }.
 */
function guidedTasks(report, all, elements, { set, onTemplates }) {
  const errorsIn = (id, test) => (all[id]?.issues || []).filter((p) => p.level === 'error' && (!test || test(p)));
  const isSql = report.queryLanguage === 'sql';
  const params = report.parameters || [];
  const asked = params.filter((p) => p.prompt !== false);
  const auto = report.isNew && !report.idManual;
  const missingParams = [...new Set(errorsIn('parameters', (p) => /no parameter/.test(p.text)).map((p) => p.focus?.name).filter(Boolean))];
  const unlabeled = asked.filter((p) => !String(p.label || '').trim());
  const noData = !String(report.querySource || '').trim() && !String(report.collection || '').trim();
  const fillTitle = (title) => set({ title, ...(auto ? { id: reportIdFrom(title) } : {}) });
  return {
    details: [
      { key: 'name', ask: 'What should the report be called?', why: 'Pick a name people will recognise in the report list.',
        example: 'Branch wise sales', examples: ['Branch wise sales', 'Customer statement', 'Monthly salary slip'], onExample: fillTitle,
        guide: 'name', done: Boolean(String(report.title || '').trim()) && report.title !== 'New report' },
      { key: 'id', ask: 'Check the report ID', why: 'The ID is the report\'s address. Apps and links use it to run the report, so it cannot change once saved.',
        example: `/generate/${report.id || 'BRANCH_WISE_SALES'}.pdf`, guide: 'id', done: !errorsIn('details').length,
        apply: report.isNew && report.title ? { label: 'Make it from the name', run: () => set({ id: reportIdFrom(report.title), idManual: false }) } : null },
      { key: 'description', ask: 'Describe it in one line', why: 'Optional. It helps others find the right report later.', optional: true,
        example: 'Total sales of each branch for a date range, for the finance team.', guide: 'description',
        done: Boolean(String(report.description || '').trim()),
        apply: { label: 'Use this example', run: () => set({ description: 'Total sales of each branch for a date range, for the finance team.' }) } },
    ],
    source: [
      { optional: noData, key: 'adapter', ask: 'Which database should it read?', why: 'A data adapter is a saved connection to a database. Pick one from the list below.',
        example: 'Sales DB · MongoDB  — or —  Finance · PostgreSQL', guide: 'adapter', done: Boolean(report.connectionId) && !errorsIn('source', (p) => p.focus?.target !== 'collection').length },
      ...(isSql ? [] : [{ optional: noData, key: 'collection', ask: 'Which collection holds the data?', why: 'In MongoDB, a collection is like a table. Type only its name.',
        example: 'Orders', guide: 'collection', done: !errorsIn('source', (p) => p.focus?.target === 'collection').length }]),
      { key: 'conn-test', ask: 'Test the connection', why: 'Optional, but quick. It only checks that the server can reach the database. Nothing is read or changed.', optional: true,
        example: '● Connected', guide: 'conn-test', done: all.source?.tested === 'ok' },
    ],
    query: [
      { optional: noData, key: 'query', ask: 'Write the query', why: isSql ? 'One SELECT statement. A value the user types becomes $P{Name}.' : 'An aggregation pipeline. A value the user types becomes $P{Name}.',
        example: starterQuery(report), code: true, guide: 'query', done: Boolean(String(report.querySource || '').trim()),
        apply: !String(report.querySource || '').trim() ? { label: 'Start from this example', run: () => set({ querySource: starterQuery(report) }) } : null },
      { key: 'query-test', ask: 'Check it, then try it with data', why: '“Check query” finds typing mistakes. “Try with data” shows the first records with test values.', optional: true,
        example: '✓ It works: first 5 records shown below.', guide: 'query-test', done: all.query?.tested === 'ok' },
    ],
    fields: [
      { optional: noData, key: 'fields', ask: 'Read the fields from the query', why: 'Fields are the columns the report can print. Press “Read fields”; you can remove the ones you don’t need.',
        example: 'Branch · Amount · Date', guide: 'fields', done: (report.columns || []).length > 0 && !(all.fields?.issues || []).length },
    ],
    parameters: [
      { key: 'params', ask: missingParams.length ? `Add ${missingParams.join(', ')}` : 'What does the user type before running?',
        why: 'Every $P{…} in the query is a parameter: a value the user types or picks, like a start and end date.',
        example: 'StartDate (date) · EndDate (date) · Branch (text)', guide: 'parameters', done: !errorsIn('parameters').length,
        apply: missingParams.length ? { label: `Add ${missingParams.length > 1 ? 'them' : 'it'} for me`, run: () => set({ parameters: [...params, ...missingParams.map((name) => ({ name, label: labelFrom(name), type: /date/i.test(name) ? 'date' : 'string', required: true }))] }) } : null },
      { key: 'labels', ask: 'Give each parameter a friendly label', why: 'Optional. The label is what the user sees on the run screen.', optional: true,
        example: 'StartDate → “Start date”', guide: 'parameters', done: !unlabeled.length,
        apply: unlabeled.length ? { label: 'Make labels from the names', run: () => set({ parameters: params.map((p) => (p.prompt !== false && !String(p.label || '').trim() ? { ...p, label: labelFrom(p.name) } : p)) }) } : null },
    ],
    layout: [
      { key: 'template', ask: 'How should the report look?', why: 'Start from a template: a table, a letter per record, a form or a certificate. You can change everything later in the Design tab.',
        example: 'Table: one row per record, with totals at the end', guide: 'layout', done: elements > 0,
        apply: { label: 'Choose a template…', run: onTemplates } },
    ],
  };
}

/** Guided mode: one small task at a time, with an example and the field to fill highlighted below. */
function Coach({ step, tasks, taskIndex, onTask, onBack, onNext, onShow, allDone }) {
  const index = SEGMENTS.findIndex(([id]) => id === step);
  const [, title] = SEGMENTS[index];
  const task = tasks[Math.min(taskIndex, tasks.length - 1)];
  const lastTask = taskIndex >= tasks.length - 1;
  const lastStep = index === SEGMENTS.length - 1;
  const doneCount = tasks.filter((t) => t.done).length;
  return (
    <section className="coach" aria-live="polite">
      <div className="coach-top">
        <span className="coach-count">Step {index + 1} of {SEGMENTS.length} · {title}</span>
        {tasks.length > 1 && (
          <span className="coach-dots" aria-label={`${doneCount} of ${tasks.length} done`}>
            {tasks.map((t, i) => (
              <button key={t.key} type="button" className={`${t.done ? 'done' : ''} ${i === taskIndex ? 'on' : ''}`} title={t.ask} onClick={() => onTask(i)}>
                {t.done ? '✓' : i + 1}
              </button>
            ))}
          </span>
        )}
      </div>
      <div className={`coach-card ${task.done ? 'is-done' : ''}`} key={`${step}-${task.key}`}>
        <h3>{task.done && <span className="coach-ok">✓</span>}{task.ask}{task.optional && <small className="coach-opt">optional</small>}</h3>
        <p className="coach-why">{task.why}</p>
        <details className="coach-example" key={task.key}>
          <summary className="coach-example-label">See an example</summary>
          {task.code ? <pre>{task.example}</pre> : <span className="coach-example-text">{task.example}</span>}
          {task.examples && (
            <span className="coach-chips">
              {task.examples.map((e) => <button key={e} type="button" className="btn sm" onClick={() => task.onExample(e)}>{e}</button>)}
            </span>
          )}
          {task.apply && <button type="button" className="btn sm primary-soft coach-apply" onClick={task.apply.run}>{task.apply.label}</button>}
        </details>
        <button type="button" className="coach-where" onClick={onShow}>👇 {task.done ? 'Done. You can still change it below.' : 'Fill it in below'}</button>
      </div>
      <div className="coach-buttons">
        <button type="button" className="btn" onClick={onBack} disabled={index === 0 && taskIndex === 0}>← Back</button>
        {lastStep && lastTask ? (
          <span className={`coach-finish ${allDone ? 'ok' : ''}`}>{allDone ? '🎉 All done: open Preview to run the report.' : 'Almost there: the red steps above still need something.'}</span>
        ) : (
          <button type="button" className={`btn ${task.done || task.optional ? 'primary' : ''}`} onClick={onNext}>
            {task.done ? 'Next →' : task.optional ? 'Skip →' : 'Skip for now →'}
          </button>
        )}
      </div>
    </section>
  );
}

export default function SetupForm({ state, dispatch, notify, onTemplates, refreshKey, focus, onFocus, onClearFocus, onDataScreen }) {
  const root = useRef(null);
  useFocusPlace(root, focus);
  const problems = checkSetup(state.report, state.design);
  const { report } = state;
  const set = (patch) => dispatch({ type: 'REPORT', patch });
  const [folders, setFolders] = useState([]);
  useEffect(() => { api.workspace().then((r) => setFolders(r.folders || [])).catch(() => setFolders([])); }, []);
  // The ID follows the name until somebody types an ID of their own (only while the report is new).
  const auto = report.isNew && !report.idManual;

  const [queryMeta, setQueryMeta] = useState({});
  const byPart = Object.fromEntries(SEGMENTS.map(([id]) => [id, []]));
  problems.forEach((p) => byPart[segmentOf(p.focus)].push(p));
  // Open at first: every part with a problem; when all is fine, only the first part.
  const [open, setOpen] = useState(() => {
    const withProblems = SEGMENTS.map(([id]) => id).filter((id) => byPart[id].length);
    return new Set(withProblems.length ? withProblems : ['details']);
  });
  const toggle = (id) => setOpen((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const reveal = (id) => {
    setOpen((s) => new Set([...s, id]));
    setTimeout(() => document.getElementById(`seg-${id}`)?.scrollIntoView({ block: 'start', behavior: 'smooth' }), 30);
  };
  // "Go there" from anywhere (Preview errors, the problem lists) opens the part it points to.
  useEffect(() => { if (focus) { setOpen((s) => new Set([...s, segmentOf(focus)])); setCurrent(segmentOf(focus)); setTaskIndex(0); } }, [focus?.nonce]); // eslint-disable-line react-hooks/exhaustive-deps

  // Guided = one step at a time with a checklist (the default); All = every part on one page. Remembered per browser.
  const [mode, setModeState] = useState(() => { try { return localStorage.getItem('setup.mode') === 'all' ? 'all' : 'guided'; } catch { return 'guided'; } });
  const setMode = (value) => { setModeState(value); try { localStorage.setItem('setup.mode', value); } catch { /* private window */ } };
  const [current, setCurrent] = useState(() => SEGMENTS.find(([id]) => byPart[id].some((p) => p.level === 'error'))?.[0] || 'details');
  const guided = mode === 'guided';
  const [taskIndex, setTaskIndex] = useState(0);

  const countElements = (band) => (Array.isArray(band?.elements) ? band.elements.length
    : Object.values(band || {}).reduce((m, g) => m + (Array.isArray(g?.elements) ? g.elements.length : 0), 0));
  const elements = Object.values(state.design?.bands || {}).reduce((n, b) => n + countElements(b), 0);
  const computed = report.parameters.filter((p) => p.prompt === false).length;
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  const summaries = {
    details: `${report.title || 'No name'} · ${report.id || 'no ID'} · ${!report.folder || report.folder === 'General' ? 'top level' : report.folder.split('/').join(' / ')}`,
    parameters: report.parameters.length
      ? `${plural(report.parameters.length, 'parameter')}: ${report.parameters.slice(0, 4).map((p) => `${p.name}${p.required ? '*' : ''}`).join(', ')}${report.parameters.length > 4 ? '…' : ''}${computed ? ` · ${computed} computed` : ''}`
      : 'None: the report runs without asking anything',
    layout: elements ? `${plural(elements, 'element')} on the page · change them in the Design tab` : 'Empty page: choose a template or design it',
  };
  // The adapter, query and fields parts live in QueryEditor: it reports their own problems and test results here.
  const all = Object.fromEntries(SEGMENTS.map(([id]) => [id, {
    issues: [...byPart[id], ...(queryMeta[id]?.issues || [])], tested: queryMeta[id]?.tested,
  }]));
  const tasks = guidedTasks(report, all, elements, { set, onTemplates });
  const stepIndex = SEGMENTS.findIndex(([id]) => id === current);
  const stepTasks = tasks[current];
  const task = stepTasks[Math.min(taskIndex, stepTasks.length - 1)];
  const target = () => (task?.guide ? root.current?.querySelector(`[data-guide="${task.guide}"]`) : null);
  const showTarget = (focusIt) => setTimeout(() => {
    const el = target();
    if (!el) return;
    el.scrollIntoView({ block: focusIt ? 'center' : 'nearest', behavior: 'smooth' });
    if (focusIt) el.querySelector?.('input, select, textarea')?.focus({ preventScroll: true });
  }, 40);
  /** Open a step at a task: by default its first task still to do. */
  const goStep = (id, at) => {
    const list = tasks[id];
    const first = list.findIndex((t) => !t.done);
    setCurrent(id);
    setTaskIndex(at ?? (first < 0 ? 0 : first));
    setTimeout(() => root.current?.scrollIntoView({ block: 'start', behavior: 'smooth' }), 20);
  };
  const nextTask = () => {
    if (taskIndex < stepTasks.length - 1) { setTaskIndex(taskIndex + 1); return; }
    if (stepIndex < SEGMENTS.length - 1) goStep(SEGMENTS[stepIndex + 1][0], 0);
  };
  const prevTask = () => {
    if (taskIndex > 0) { setTaskIndex(taskIndex - 1); return; }
    if (stepIndex > 0) { const prev = SEGMENTS[stepIndex - 1][0]; goStep(prev, tasks[prev].length - 1); }
  };
  // On opening: start at the first task still to do.
  useEffect(() => { const first = stepTasks.findIndex((t) => !t.done); if (first > 0) setTaskIndex(first); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  // The field of the current task glows, so the user knows where to type.
  useEffect(() => {
    root.current?.querySelectorAll('.guide-target').forEach((el) => el.classList.remove('guide-target'));
    if (!guided) return;
    target()?.classList.add('guide-target');
  });
  useEffect(() => { if (guided) showTarget(false); }, [guided, current, taskIndex]); // eslint-disable-line react-hooks/exhaustive-deps

  const segment = (id, content, extra = {}) => {
    const index = SEGMENTS.findIndex(([k]) => k === id);
    const [, title, hint] = SEGMENTS[index];
    const issues = [...byPart[id], ...(extra.issues || [])];
    return (
      <Segment key={id} id={id} index={index} title={title} hint={hint} summary={extra.summary || summaries[id]} issues={issues}
        tested={extra.tested} open={guided ? id === current : open.has(id)} hiddenSeg={guided && id !== current}
        onToggle={() => (guided ? undefined : toggle(id))} onFocus={onFocus} bare={guided}>{content}</Segment>
    );
  };

  const body = (
    <>
      {segment('details', (
        <div className="form-grid">
          <label data-guide="name">Report name<input value={report.title} onChange={(e) => set({ title: e.target.value, ...(auto ? { id: reportIdFrom(e.target.value) } : {}) })} placeholder="e.g. Branch wise amount report" /></label>
          <label data-guide="id">Report ID <small>{report.isNew ? (auto ? '(made from the name — type to choose your own)' : '(your own ID)') : '(fixed once saved: /generate/ID.pdf)'}</small>
            <span className="inline">
              <input value={report.id} disabled={!report.isNew} onChange={(e) => set({ id: e.target.value.replace(/[^A-Za-z0-9_-]/g, ''), idManual: true })} placeholder="BRANCH_WISE_AMOUNT_REPORT" />
              {report.isNew && !auto && <button type="button" className="btn sm" title="Make the ID from the report name again" onClick={() => set({ id: reportIdFrom(report.title), idManual: false })}>↺ From name</button>}
            </span>
          </label>
          <label>Workspace folder <small>(where Save keeps it in the designer; the server folder is chosen when you publish)</small>
            <select value={report.folder || 'General'} onChange={(e) => {
              if (e.target.value !== '__new__') { set({ folder: e.target.value }); return; }
              const name = window.prompt('Name of the new folder (use / for a subfolder, e.g. Finance/Monthly)', report.folder && report.folder !== 'General' ? `${report.folder}/` : '');
              const clean = String(name || '').trim().replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
              if (!clean) return;
              if (!/^[A-Za-z0-9][A-Za-z0-9 _-]{0,63}(\/[A-Za-z0-9][A-Za-z0-9 _-]{0,63})*$/.test(clean)) { window.alert('Use letters, numbers, spaces, _ and - (each folder name starts with a letter or number).'); return; }
              setFolders((list) => [...new Set([...list, clean])].sort((a, b) => a.localeCompare(b)));
              set({ folder: clean });
            }}>
              {[...new Set(['General', ...folders, report.folder || 'General'])].sort((a, b) => (a === 'General' ? -1 : b === 'General' ? 1 : a.localeCompare(b))).map((f) => (
                <option key={f} value={f}>{f === 'General' ? 'Top level (no folder)' : f.split('/').join(' / ')}</option>
              ))}
              <option value="__new__">＋ New folder…</option>
            </select>
          </label>
          <label className="wide" data-guide="description">Description<input value={report.description} onChange={(e) => set({ description: e.target.value })} placeholder="What this report shows and who uses it (optional)" /></label>
        </div>
      ))}
      <QueryEditor state={state} dispatch={dispatch} notify={notify} refreshKey={refreshKey} tests wrap={segment} onFocus={onFocus} onMeta={setQueryMeta} onDataScreen={onDataScreen}
        markLine={focus?.target === 'query' ? focus.line : undefined} markNote={focus?.note} markFixed={Boolean(focus?.fixed)} />
      {segment('parameters', <ParametersEditor state={state} dispatch={dispatch} />)}
      {segment('layout', (
        <>
          <p className="muted">Start from a template: a blank page, a letter (one letter per record), a table from the fields, a form, or a certificate. Nothing is changed until you choose one; you can also design everything yourself in the Design tab.</p>
          <button className="btn primary" data-guide="layout" onClick={onTemplates}>Choose a layout template…</button>
        </>
      ))}
    </>
  );

  const errors = Object.values(all).reduce((n, m) => n + m.issues.filter((p) => p.level === 'error').length + (m.tested === 'error' ? 1 : 0), 0);
  const warns = Object.values(all).reduce((n, m) => n + m.issues.filter((p) => p.level === 'warn').length, 0);
  const todos = Object.values(all).reduce((n, m) => n + m.issues.filter((p) => p.level === 'todo').length, 0);
  const allOpen = open.size === SEGMENTS.length;

  return (
    <div className={`setup ${guided ? 'is-guided' : ''}`} ref={root}>
      <nav className="seg-nav" aria-label="Setup parts">
        <ol>
          {SEGMENTS.map(([id, title], i) => {
            const m = all[id] || { issues: [] };
            const status = statusOf(m.issues, m.tested);
            return (
              <li key={id}>
                <button type="button" className={`seg-chip ${status} ${guided && id === current ? 'current' : ''}`} onClick={() => (guided ? goStep(id) : reveal(id))} title={badgeText(m.issues, m.tested)}
                  aria-current={guided && id === current ? 'step' : undefined}>
                  <span className={`step ${status}`}>{status === 'ok' ? '✓' : status === 'todo' ? '○' : status === 'warn' ? '!' : '✕'}</span>
                  <span>{i + 1}. {title}</span>
                </button>
              </li>
            );
          })}
        </ol>
        <div className="seg-overall">
          {guided ? <span className={`seg-short ${errors ? 'err' : warns ? 'warn' : todos ? 'todo' : 'ok'}`}>{errors ? `${plural(errors, 'thing')} to fix` : warns ? `${plural(warns, 'warning')}` : todos ? 'Grey steps are still empty' : 'All set ✓'}</span> : <span className={errors ? 'err' : warns ? 'warn' : todos ? 'todo' : 'ok'}>
            {errors ? `${plural(errors, 'thing')} to fix before the report can run` : warns ? `Ready to run · ${plural(warns, 'warning')} to look at`
              : todos ? 'Ready to preview · the grey parts are not filled in yet (without data it prints once, as fixed text)' : 'All parts look fine: the report is ready to run'}
          </span>}
          <span className="seg-tools">
            {!guided && <button type="button" className="btn sm" onClick={() => setOpen(new Set(allOpen ? [] : SEGMENTS.map(([id]) => id)))}>{allOpen ? 'Collapse all' : 'Expand all'}</button>}
            <span className="seg-mode" role="group" aria-label="How to show the setup">
              <button type="button" className={guided ? 'on' : ''} aria-pressed={guided} onClick={() => setMode('guided')} title="One step at a time, with a checklist and hints">Guided</button>
              <button type="button" className={!guided ? 'on' : ''} aria-pressed={!guided} onClick={() => setMode('all')} title="Every part on one page">All parts</button>
            </span>
          </span>
        </div>
        {focus?.note && (
          <div className="fix-note" role="status">
            <p><b>Fix here:</b> {placeOf(focus)}<br />{focus.note}</p>
            <button type="button" className="icon" aria-label="Close" onClick={onClearFocus}>✕</button>
          </div>
        )}
      </nav>
      {guided && (
        <Coach step={current} tasks={stepTasks} taskIndex={Math.min(taskIndex, stepTasks.length - 1)} onTask={setTaskIndex} allDone={errors === 0}
          onBack={prevTask} onNext={nextTask} onShow={() => showTarget(true)} />
      )}
      <div className="setup-body" key={guided ? current : 'all'}>{body}</div>
    </div>
  );
}
