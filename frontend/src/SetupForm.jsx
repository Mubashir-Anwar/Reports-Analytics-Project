import { useState } from 'react';
import { api } from './api.js';
import { autoLayout, inferColumns } from './designModel.js';

export default function SetupForm({ state, dispatch, notify, onAutoLayout }) {
  const { report, design } = state;
  const [busy, setBusy] = useState(false);
  const set = (patch) => dispatch({ type: 'REPORT', patch });
  const slug = (title) => title.replace(/[^A-Za-z0-9]+/g, '_').replace(/^_+|_+$/g, '').toUpperCase().slice(0, 60);

  const detect = async () => {
    setBusy(true);
    try {
      const result = await api.parseQuery(report.queryLanguage, report.querySource, report.parameters);
      const columns = inferColumns(result.pipeline);
      if (!columns.length) {
        notify('Aakhri $project/$group/$count se columns nahi mile. Neeche manually likhein.', 'error');
      } else {
        set({ columns });
        notify(`${result.stageCount} stages parse hue, ${columns.length} fields mile`, 'ok');
      }
    } catch (error) { notify(`Query parse nahi hui: ${error.message}`, 'error'); } finally { setBusy(false); }
  };

  const layout = () => {
    if (!report.columns.length) { notify('Pehle fields detect karein', 'error'); return; }
    if (design.bands.detail.elements.length && !window.confirm('Maujooda design ki jagah naya auto layout banana hai?')) return;
    dispatch({ type: 'DESIGN', update: (d) => autoLayout(report.columns, {}, report.title, d) });
    onAutoLayout();
  };

  const addParam = () => set({ parameters: [...report.parameters, { name: `Param${report.parameters.length + 1}`, type: 'string', label: '', required: false }] });
  const patchParam = (index, values) => set({ parameters: report.parameters.map((p, i) => (i === index ? { ...p, ...values } : p)) });

  return (
    <div className="setup">
      <section className="card">
        <h3><span className="step">1</span> Report details</h3>
        <div className="form-grid">
          <label>Report name<input value={report.title} onChange={(e) => set({ title: e.target.value, ...(report.isNew ? { id: slug(e.target.value) } : {}) })} /></label>
          <label>Report ID <small>(URL mein: /generate/ID.pdf)</small><input value={report.id} disabled={!report.isNew} onChange={(e) => set({ id: e.target.value.replace(/[^A-Za-z0-9_-]/g, '') })} /></label>
          <label>Folder<input value={report.folder} onChange={(e) => set({ folder: e.target.value })} placeholder="General" /></label>
          <label className="wide">Description<input value={report.description} onChange={(e) => set({ description: e.target.value })} /></label>
        </div>
      </section>

      <section className="card">
        <h3><span className="step">2</span> Data adapter aur query</h3>
        <div className="form-grid">
          <label>Data adapter ID <small>(left panel ke Data Adapters tab se chunein)</small><input value={report.connectionId} onChange={(e) => set({ connectionId: e.target.value })} /></label>
          <label>MongoDB collection<input value={report.collection} onChange={(e) => set({ collection: e.target.value })} placeholder="Edith-KMBLGL-Workflows" /></label>
          <label>Query style
            <select value={report.queryLanguage} onChange={(e) => set({ queryLanguage: e.target.value })}>
              <option value="mongosh">Mongo Shell</option><option value="aggregation-json">Aggregation JSON</option>
              <option value="javascript">JavaScript pipeline</option><option value="python">Python pipeline</option>
            </select>
          </label>
        </div>
        <label className="block">Aggregation query <small>(parameters: $P&#123;StartDate&#125;)</small>
          <textarea className="code" rows={12} spellCheck={false} value={report.querySource} onChange={(e) => set({ querySource: e.target.value })} placeholder={'db.getCollection("Orders").aggregate([\n  {"$match": {"Date": {"$gte": $P{StartDate}, "$lte": $P{EndDate}}}},\n  {"$project": {"Identifier": 1, "Branch": 1, "Amount": 1}}\n])'} />
        </label>
        <div className="row">
          <button className="btn primary" onClick={detect} disabled={busy || !report.querySource.trim()}>Detect fields</button>
          <span className="muted">Query sirf data ki tarah parse hoti hai, execute nahi.</span>
        </div>
        <label className="block">Fields (columns) <small>— detect na ho to comma se alag likhein</small>
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
                <td><input type="checkbox" checked={Boolean(p.required)} onChange={(e) => patchParam(i, { required: e.target.checked })} /></td>
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
        <p className="muted">Fields se tabular layout foran ban jata hai. Phir Design tab mein bands, groups, charts aur elements ko Jasper Studio ki tarah badal sakte hain.</p>
        <button className="btn primary" onClick={layout}>Auto-generate layout from fields →</button>
      </section>
    </div>
  );
}
