import { useEffect, useState } from 'react';
import { api } from './api.js';
import { autoLayout, inferColumns } from './designModel.js';
import { AdapterOptions } from './panels.jsx';

export default function SetupForm({ state, dispatch, notify, onAutoLayout }) {
  const { report, design } = state;
  const [busy, setBusy] = useState(false);
  const [adapters, setAdapters] = useState(null); // null = loading
  const set = (patch) => dispatch({ type: 'REPORT', patch });
  const slug = (title) => title.replace(/[^A-Za-z0-9]+/g, '_').replace(/^_+|_+$/g, '').toUpperCase().slice(0, 60);

  const loadAdapters = () => api.connections().then((r) => setAdapters(r.connections)).catch((error) => {
    setAdapters([]);
    notify(`Could not load data adapters: ${error.message}`, 'error');
  });
  const [folders, setFolders] = useState([]);
  useEffect(() => {
    loadAdapters();
    api.workspace().then((r) => setFolders(r.folders || [])).catch(() => setFolders([]));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const detect = async () => {
    setBusy(true);
    try {
      // Parsed without the parameter check: $P{...} names the report does not have yet are added as questions.
      const result = await api.parseQuery(report.queryLanguage, report.querySource);
      const columns = inferColumns(result.pipeline);
      const known = new Set(report.parameters.map((p) => p.name));
      const added = (result.parameterNames || []).filter((name) => !known.has(name));
      const patch = {};
      if (added.length) patch.parameters = [...report.parameters, ...added.map((name) => ({ name, type: 'string', label: name, required: true }))];
      if (result.collection && !report.collection.trim()) patch.collection = result.collection;
      if (columns.length) patch.columns = columns;
      if (Object.keys(patch).length) set(patch);
      const extras = [
        patch.collection ? `collection ${patch.collection}` : '',
        added.length ? `new parameter(s) ${added.join(', ')} (type string — change it below if needed)` : '',
      ].filter(Boolean).join('; ');
      if (!columns.length) {
        notify(`No columns found in the last $project/$group/$count stage. Type them below instead.${extras ? ` Added: ${extras}.` : ''}`, 'error');
      } else {
        notify(`${result.stageCount} stages parsed, ${columns.length} fields found${extras ? ` · added ${extras}` : ''}`, 'ok');
      }
    } catch (error) { notify(`The query could not be parsed: ${error.message}`, 'error'); } finally { setBusy(false); }
  };

  const layout = () => {
    if (!report.columns.length) { notify('Detect the fields first', 'error'); return; }
    if (design.bands.detail.elements.length && !window.confirm('Replace the current design with a new automatic layout?')) return;
    dispatch({ type: 'DESIGN', update: (d) => autoLayout(report.columns, {}, report.title, d) });
    onAutoLayout();
  };

  const addParam = () => set({ parameters: [...report.parameters, { name: `Param${report.parameters.length + 1}`, type: 'string', label: '', required: false }] });
  const patchParam = (index, values) => set({ parameters: report.parameters.map((p, i) => (i === index ? { ...p, ...values } : p)) });
  const known = (adapters || []).some((a) => a.id === report.connectionId);
  const selected = (adapters || []).find((a) => a.id === report.connectionId);

  return (
    <div className="setup">
      <section className="card">
        <h3><span className="step">1</span> Report details</h3>
        <div className="form-grid">
          <label>Report name<input value={report.title} onChange={(e) => set({ title: e.target.value, ...(report.isNew ? { id: slug(e.target.value) } : {}) })} /></label>
          <label>Report ID <small>(used in the URL: /generate/ID.pdf)</small><input value={report.id} disabled={!report.isNew} onChange={(e) => set({ id: e.target.value.replace(/[^A-Za-z0-9_-]/g, '') })} /></label>
          <label>Workspace folder <small>(where Save keeps it in the designer; the server folder is chosen when you publish)</small>
            <input list="folder-choices" value={report.folder} onChange={(e) => set({ folder: e.target.value })} placeholder="General" />
            <datalist id="folder-choices">{folders.map((f) => <option key={f} value={f} />)}</datalist>
          </label>
          <label className="wide">Description<input value={report.description} onChange={(e) => set({ description: e.target.value })} /></label>
        </div>
      </section>

      <section className="card">
        <h3><span className="step">2</span> Data adapter and query</h3>
        <div className="form-grid">
          <label>Data adapter <small>(the MongoDB connection this report reads from)</small>
            <span className="inline">
              <select value={report.connectionId} onChange={(e) => set({ connectionId: e.target.value })} disabled={adapters === null}>
                {adapters === null && <option value={report.connectionId}>Loading…</option>}
                {adapters !== null && <option value="">Choose a data adapter…</option>}
                {adapters !== null && report.connectionId && !known && <option value={report.connectionId}>{report.connectionId} (not registered)</option>}
                <AdapterOptions adapters={adapters} describe={(a) => `${a.name}${a.database ? ` · ${a.database}` : ''} [${a.id}]${a.configured ? '' : ' — not configured'}`} />
              </select>
              <button type="button" className="btn sm" onClick={loadAdapters} title="Reload the adapter list">↻</button>
            </span>
            {selected && !selected.configured && <small className="err">This adapter has no saved MongoDB URL yet. Add it in the Data Adapters tab or on /admin → Data sources.</small>}
            {adapters !== null && report.connectionId && !known && <small className="err">This adapter ID is not registered on this server. Choose one from the list.</small>}
            {adapters !== null && !report.connectionId && <small className="muted">Used by /generate and /ui. In Preview you can still run on any adapter.</small>}
          </label>
          <label>MongoDB collection<input value={report.collection} onChange={(e) => set({ collection: e.target.value })} placeholder="Edith-KMBLGL-Workflows" /></label>
          <label>Query style
            <select value={report.queryLanguage} onChange={(e) => set({ queryLanguage: e.target.value })}>
              <option value="mongosh">Mongo Shell</option><option value="aggregation-json">Aggregation JSON</option>
              <option value="javascript">JavaScript pipeline</option><option value="python">Python pipeline</option>
            </select>
          </label>
        </div>
        <label className="block">Aggregation query <small>(parameters: $P&#123;StartDate&#125;; db.collection.aggregate([...]) or a Jaspersoft Studio &#123;"runCommand": &#123;"aggregate": …, "pipeline": [...]&#125;&#125; query)</small>
          <textarea className="code" rows={12} spellCheck={false} value={report.querySource} onChange={(e) => set({ querySource: e.target.value })} placeholder={'db.getCollection("Orders").aggregate([\n  {"$match": {"Date": {"$gte": $P{StartDate}, "$lte": $P{EndDate}}}},\n  {"$project": {"Identifier": 1, "Branch": 1, "Amount": 1}}\n])'} />
        </label>
        <div className="row">
          <button className="btn primary" onClick={detect} disabled={busy || !report.querySource.trim()}>Detect fields</button>
          <span className="muted">The query is only parsed as data here, not executed.</span>
        </div>
        <label className="block">Fields (columns) <small>— if detection fails, type them separated by commas</small>
          <input value={report.columns.join(', ')} onChange={(e) => set({ columns: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) })} />
        </label>
      </section>

      <section className="card">
        <h3><span className="step">3</span> Questions (parameters)</h3>
        <table className="grid">
          <thead><tr><th>Name (URL key)</th><th>Type</th><th>Label</th><th>Required</th><th /></tr></thead>
          <tbody>
            {report.parameters.map((p, i) => (
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
          <button className="btn" onClick={addParam}>+ Add question</button>
          <button className="btn" onClick={() => set({ parameters: [{ name: 'StartDate', type: 'date', label: 'Start date', required: true }, { name: 'EndDate', type: 'date', label: 'End date', required: true }] })}>Start/End date preset</button>
        </div>
      </section>

      <section className="card">
        <h3><span className="step">4</span> Layout</h3>
        <p className="muted">Builds a table layout from the fields straight away. Then change bands, groups, charts and elements in the Design tab, as in Jaspersoft Studio.</p>
        <button className="btn primary" onClick={layout}>Auto-generate layout from fields →</button>
      </section>
    </div>
  );
}
