import { useEffect, useState } from 'react';
import { api } from './api.js';
import { inferColumns } from './designModel.js';
import { AdapterOptions, DbLogo } from './panels.jsx';

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
export function QueryEditor({ state, dispatch, notify, refreshKey }) {
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

  const readSqlFields = async () => {
    setBusy(true);
    try {
      const result = await api.queryFields(report.connectionId, report.querySource);
      const knownParams = new Set(report.parameters.map((p) => p.name));
      const added = result.parameterNames.filter((name) => !knownParams.has(name));
      set({ columns: result.columns, ...(added.length ? { parameters: [...report.parameters, ...added.map((name) => ({ name, type: 'string', label: name, required: true }))] } : {}) });
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
      if (added.length) patch.parameters = [...report.parameters, ...added.map((name) => ({ name, type: 'string', label: name, required: true }))];
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

  return (
    <>
      <div className="form-grid">
        <label>Data adapter <small>(the database connection this report reads from)</small>
          <span className="inline">
            {selected && <DbLogo type={selected.type || 'mongodb'} />}
            <select value={report.connectionId} onChange={(e) => chooseAdapter(e.target.value)} disabled={adapters === null}>
              {adapters === null && <option value={report.connectionId}>Loading…</option>}
              {adapters !== null && <option value="">Choose a data adapter…</option>}
              {adapters !== null && report.connectionId && !known && <option value={report.connectionId}>{report.connectionId} (not registered)</option>}
              <AdapterOptions adapters={adapters} describe={(a) => `${a.name}${a.database ? ` · ${a.database}` : ''}${a.configured ? '' : ' — not configured'}`} />
            </select>
            <button type="button" className="btn sm" onClick={loadAdapters} title="Reload the adapter list">↻</button>
          </span>
          {selected && !selected.configured && <small className="err">This adapter has no saved connection URL yet. Add it in the Data Adapters tab or on /admin → Data sources.</small>}
          {adapters !== null && report.connectionId && !known && <small className="err">This adapter ID is not registered on this server. Choose one from the list.</small>}
        </label>
        <label>Query language
          <select value={report.queryLanguage} onChange={(e) => chooseLanguage(e.target.value)}>
            <optgroup label="MongoDB">
              <option value="mongosh">Mongo Shell</option><option value="aggregation-json">Aggregation JSON</option>
              <option value="javascript">JavaScript pipeline</option><option value="python">Python pipeline</option>
            </optgroup>
            <optgroup label="SQL databases">
              <option value="sql">SQL — PostgreSQL, MySQL, SQL Server, Oracle</option>
            </optgroup>
          </select>
          {kindMismatch && <small className="err">{isSql ? 'This is a SQL query: choose a SQL data adapter above (PostgreSQL, MySQL, SQL Server or Oracle).' : 'This adapter is a SQL database: choose a MongoDB adapter, or Query language SQL.'}</small>}
        </label>
        {!isSql && (
          <label>Collection name <small>(only the name, e.g. Orders; filled in from a Jasper runCommand query)</small>
            <input value={report.collection} onChange={(e) => set({ collection: e.target.value })} onBlur={(e) => { const clean = cleanCollection(e.target.value); if (clean !== e.target.value) set({ collection: clean }); }} placeholder="Collection name" />
          </label>
        )}
      </div>
      {isSql ? (
        <label className="block">SQL query <small>(one SELECT; parameters: $P&#123;StartDate&#125;; a list filter: $X&#123;IN, branch, Branches&#125; — an empty list = no filter)</small>
          <textarea className="code" rows={12} spellCheck={false} value={report.querySource} onChange={(e) => set({ querySource: e.target.value })} placeholder={'SELECT o.id, o.branch, o.amount, o.placed_on\nFROM orders o\nWHERE o.placed_on BETWEEN $P{StartDate} AND $P{EndDate}\n  AND $X{IN, o.branch, Branches}\nORDER BY o.placed_on'} />
        </label>
      ) : (
        <label className="block">Query <small>(parameters: $P&#123;StartDate&#125;; db.collection.aggregate([...]) or a Jaspersoft Studio &#123;"runCommand": &#123;"aggregate": …, "pipeline": [...]&#125;&#125; query)</small>
          <textarea className="code" rows={12} spellCheck={false} value={report.querySource} onChange={(e) => set({ querySource: e.target.value })} placeholder={'db.getCollection("Orders").aggregate([\n  {"$match": {"Date": {"$gte": $P{StartDate}, "$lte": $P{EndDate}}}},\n  {"$project": {"Identifier": 1, "Branch": 1, "Amount": 1}}\n])'} />
        </label>
      )}
      <div className="row">
        <button className="btn primary" onClick={isSql ? readSqlFields : detect} disabled={busy || !report.querySource.trim() || (isSql && !report.connectionId)}>Read fields</button>
        <span className="muted">{isSql ? 'Asks the database for the columns (LIMIT 0): no rows are read, nothing is changed.' : 'The query is only parsed here, not executed.'}</span>
      </div>
      <label className="block">Fields <small>— if reading fails, type them separated by commas</small>
        <input value={report.columns.join(', ')} onChange={(e) => set({ columns: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) })} />
      </label>
    </>
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

/** Report parameters (input controls). */
export function ParametersEditor({ state, dispatch }) {
  const { report } = state;
  const set = (patch) => dispatch({ type: 'REPORT', patch });
  const addParam = () => set({ parameters: [...report.parameters, { name: `Param${report.parameters.length + 1}`, type: 'string', label: '', required: false }] });
  const patchParam = (index, values) => set({ parameters: report.parameters.map((p, i) => (i === index ? { ...p, ...values } : p)) });
  // Parameters used in the collection name (e.g. Edith-$P{ProductCode}-PaymentPlans) must list their allowed values.
  const inCollection = new Set([...String(report.collection || '').matchAll(/\$P\{(\w+)\}/g)].map((m) => m[1]));
  // With fixed text around it (Edith-…-PaymentPlans) the user may type the value; a bare $P{X} needs a list.
  const shaped = String(report.collection || '').replace(/$P{w+}/g, '').replace(/[^A-Za-z0-9]/g, '').length >= 3;
  const missing = shaped ? [] : report.parameters.filter((p) => inCollection.has(p.name) && !(p.allowedValues || []).length);
  return (
    <>
      {missing.length > 0 && (
        <p className="hint warn">
          The collection name <code>{report.collection}</code> is built from {missing.map((p) => p.name).join(', ')}.
          The name has no fixed text, so for safety list the values a user may choose under “Allowed values” (e.g. <code>CAR, BIKE</code>), or put fixed text around the parameter (e.g. <code>Orders-$P&#123;Branch&#125;</code>).
        </p>
      )}
      <table className="grid">
        <thead><tr><th>Name (URL key)</th><th>Type</th><th>Label</th><th>Allowed values</th><th>Required</th><th title="CNIC, account number…: kept masked (42*****71) in the run log">Sensitive</th><th /></tr></thead>
        <tbody>
          {report.parameters.map((p, i) => (
            // eslint-disable-next-line react/no-array-index-key -- a name can be edited, the position is the identity
            <tr key={i}>
              <td><input value={p.name} onChange={(e) => patchParam(i, { name: e.target.value.replace(/[^A-Za-z0-9_]/g, '') })} /></td>
              <td><select value={p.type} onChange={(e) => patchParam(i, { type: e.target.value })}>{['date', 'string', 'integer', 'number', 'boolean'].map((t) => <option key={t}>{t}</option>)}</select></td>
              <td><input value={p.label || ''} onChange={(e) => patchParam(i, { label: e.target.value })} /></td>
              <td>{p.prompt === false ? <small>—</small> : (
                <AllowedValuesInput values={p.allowedValues} needed={inCollection.has(p.name) && !shaped}
                  onChange={(list) => patchParam(i, list.length ? { allowedValues: list } : { allowedValues: undefined })} />
              )}</td>
              <td>{p.prompt === false ? <small title="Computed from other parameters">ƒ computed</small> : <input type="checkbox" checked={Boolean(p.required)} onChange={(e) => patchParam(i, { required: e.target.checked })} />}</td>
              <td><input type="checkbox" aria-label={`${p.name} is sensitive`} title="Masked in the run log (e.g. 42*********71)" checked={p.sensitive === true}
                onChange={(e) => patchParam(i, e.target.checked ? { sensitive: true } : { sensitive: undefined })} /></td>
              <td><button className="icon" onClick={() => set({ parameters: report.parameters.filter((_, j) => j !== i) })}>✕</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row">
        <button className="btn" onClick={addParam}>+ Add parameter</button>
        <button className="btn" onClick={() => set({ parameters: [{ name: 'StartDate', type: 'date', label: 'Start date', required: true }, { name: 'EndDate', type: 'date', label: 'End date', required: true }] })}>Start/End date preset</button>
      </div>
    </>
  );
}

export default function SetupForm({ state, dispatch, notify, onTemplates, refreshKey }) {
  const { report } = state;
  const set = (patch) => dispatch({ type: 'REPORT', patch });
  const [folders, setFolders] = useState([]);
  useEffect(() => { api.workspace().then((r) => setFolders(r.folders || [])).catch(() => setFolders([])); }, []);
  // The ID follows the name until somebody types an ID of their own (only while the report is new).
  const auto = report.isNew && !report.idManual;

  return (
    <div className="setup">
      <section className="card">
        <h3><span className="step">1</span> Report details</h3>
        <div className="form-grid">
          <label>Report name<input value={report.title} onChange={(e) => set({ title: e.target.value, ...(auto ? { id: reportIdFrom(e.target.value) } : {}) })} placeholder="e.g. Branch wise amount report" /></label>
          <label>Report ID <small>{report.isNew ? (auto ? '(made from the name — type to choose your own)' : '(your own ID)') : '(fixed once saved: /generate/ID.pdf)'}</small>
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
                <option key={f} value={f}>{f.split('/').join(' / ')}</option>
              ))}
              <option value="__new__">＋ New folder…</option>
            </select>
          </label>
          <label className="wide">Description<input value={report.description} onChange={(e) => set({ description: e.target.value })} /></label>
        </div>
      </section>

      <section className="card">
        <h3><span className="step">2</span> Data adapter and query</h3>
        <QueryEditor state={state} dispatch={dispatch} notify={notify} refreshKey={refreshKey} />
      </section>

      <section className="card">
        <h3><span className="step">3</span> Parameters</h3>
        <ParametersEditor state={state} dispatch={dispatch} />
      </section>

      <section className="card">
        <h3><span className="step">4</span> Layout</h3>
        <p className="muted">Start from a template: a blank page, a letter (one letter per record), a table from the fields, a form, or a certificate. Nothing is changed until you choose one; you can also design everything yourself in the Design tab.</p>
        <button className="btn primary" onClick={onTemplates}>Choose a layout template…</button>
      </section>
    </div>
  );
}
