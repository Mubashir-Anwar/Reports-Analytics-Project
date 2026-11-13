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

const useAdapters = (notify) => {
  const [adapters, setAdapters] = useState(null); // null = loading
  const load = () => api.connections().then((r) => setAdapters(r.connections)).catch((error) => {
    setAdapters([]);
    notify(`Could not load data adapters: ${error.message}`, 'error');
  });
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return [adapters, load];
};

/** Data adapter, collection, query and fields (Jasper: Dataset and Query → Query). */
export function QueryEditor({ state, dispatch, notify }) {
  const { report } = state;
  const [busy, setBusy] = useState(false);
  const [adapters, loadAdapters] = useAdapters(notify);
  const set = (patch) => dispatch({ type: 'REPORT', patch });
  const known = (adapters || []).some((a) => a.id === report.connectionId);
  const selected = (adapters || []).find((a) => a.id === report.connectionId);

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
            <select value={report.connectionId} onChange={(e) => set({ connectionId: e.target.value })} disabled={adapters === null}>
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
        <label>Collection name <small>(only the name, e.g. Orders; filled in from a Jasper runCommand query)</small>
          <input value={report.collection} onChange={(e) => set({ collection: e.target.value })} onBlur={(e) => { const clean = cleanCollection(e.target.value); if (clean !== e.target.value) set({ collection: clean }); }} placeholder="Collection name" />
        </label>
        <label>Query language
          <select value={report.queryLanguage} onChange={(e) => set({ queryLanguage: e.target.value })}>
            <option value="mongosh">Mongo Shell</option><option value="aggregation-json">Aggregation JSON</option>
            <option value="javascript">JavaScript pipeline</option><option value="python">Python pipeline</option>
          </select>
        </label>
      </div>
      <label className="block">Query <small>(parameters: $P&#123;StartDate&#125;; db.collection.aggregate([...]) or a Jaspersoft Studio &#123;"runCommand": &#123;"aggregate": …, "pipeline": [...]&#125;&#125; query)</small>
        <textarea className="code" rows={12} spellCheck={false} value={report.querySource} onChange={(e) => set({ querySource: e.target.value })} placeholder={'db.getCollection("Orders").aggregate([\n  {"$match": {"Date": {"$gte": $P{StartDate}, "$lte": $P{EndDate}}}},\n  {"$project": {"Identifier": 1, "Branch": 1, "Amount": 1}}\n])'} />
      </label>
      <div className="row">
        <button className="btn primary" onClick={detect} disabled={busy || !report.querySource.trim()}>Read fields</button>
        <span className="muted">The query is only parsed here, not executed.</span>
      </div>
      <label className="block">Fields <small>— if reading fails, type them separated by commas</small>
        <input value={report.columns.join(', ')} onChange={(e) => set({ columns: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) })} />
      </label>
    </>
  );
}

/** Report parameters (input controls). */
export function ParametersEditor({ state, dispatch }) {
  const { report } = state;
  const set = (patch) => dispatch({ type: 'REPORT', patch });
  const addParam = () => set({ parameters: [...report.parameters, { name: `Param${report.parameters.length + 1}`, type: 'string', label: '', required: false }] });
  const patchParam = (index, values) => set({ parameters: report.parameters.map((p, i) => (i === index ? { ...p, ...values } : p)) });
  return (
    <>
      <table className="grid">
        <thead><tr><th>Name (URL key)</th><th>Type</th><th>Label</th><th>Required</th><th /></tr></thead>
        <tbody>
          {report.parameters.map((p, i) => (
            // eslint-disable-next-line react/no-array-index-key -- a name can be edited, the position is the identity
            <tr key={i}>
              <td><input value={p.name} onChange={(e) => patchParam(i, { name: e.target.value.replace(/[^A-Za-z0-9_]/g, '') })} /></td>
              <td><select value={p.type} onChange={(e) => patchParam(i, { type: e.target.value })}>{['date', 'string', 'integer', 'number', 'boolean'].map((t) => <option key={t}>{t}</option>)}</select></td>
              <td><input value={p.label || ''} onChange={(e) => patchParam(i, { label: e.target.value })} /></td>
              <td>{p.prompt === false ? <small title="Computed from other parameters">ƒ computed</small> : <input type="checkbox" checked={Boolean(p.required)} onChange={(e) => patchParam(i, { required: e.target.checked })} />}</td>
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

export default function SetupForm({ state, dispatch, notify, onTemplates }) {
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
            <input list="folder-choices" value={report.folder} onChange={(e) => set({ folder: e.target.value })} placeholder="General" />
            <datalist id="folder-choices">{folders.map((f) => <option key={f} value={f} />)}</datalist>
          </label>
          <label className="wide">Description<input value={report.description} onChange={(e) => set({ description: e.target.value })} /></label>
        </div>
      </section>

      <section className="card">
        <h3><span className="step">2</span> Data adapter and query</h3>
        <QueryEditor state={state} dispatch={dispatch} notify={notify} />
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
