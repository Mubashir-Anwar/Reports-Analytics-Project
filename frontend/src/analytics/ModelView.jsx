// The model: datasets and the relationships between them. A relationship lets one visual use fields of two datasets
// (for example orders from MongoDB and branches from SQL Server, joined on the branch code).
import { useState } from 'react';
import { bi, KIND_ICON } from './api.js';

export default function ModelView({ datasets, relationships, mayModel, notify, onChanged }) {
  const ready = datasets.filter((d) => d.columns.length);
  const [form, setForm] = useState({ fromDataset: '', fromColumn: '', toDataset: '', toColumn: '' });
  const [busy, setBusy] = useState(false);
  const name = (id) => datasets.find((d) => d.id === id)?.name || id;
  const columnsOf = (id) => datasets.find((d) => d.id === id)?.columns || [];
  const linked = (id) => new Set(relationships.flatMap((r) => [r.fromDataset === id ? r.fromColumn : null, r.toDataset === id ? r.toColumn : null]).filter(Boolean));

  const add = async (event) => {
    event.preventDefault();
    setBusy(true);
    try {
      await bi.relate(form);
      notify(`${name(form.fromDataset)} and ${name(form.toDataset)} are now related.`, 'ok');
      setForm({ fromDataset: '', fromColumn: '', toDataset: '', toColumn: '' });
      onChanged();
    } catch (error) { notify(error.message, 'error'); } finally { setBusy(false); }
  };
  const remove = async (rel) => {
    if (!window.confirm(`Remove the relationship ${name(rel.fromDataset)}.${rel.fromColumn} = ${name(rel.toDataset)}.${rel.toColumn}?`)) return;
    try { await bi.unrelate(rel.id); onChanged(); } catch (error) { notify(error.message, 'error'); }
  };
  const side = (prefix, label) => (
    <div className="rel-side">
      <label className="field"><span>{label} dataset</span>
        <select value={form[`${prefix}Dataset`]} required onChange={(e) => setForm({ ...form, [`${prefix}Dataset`]: e.target.value, [`${prefix}Column`]: '' })}>
          <option value="">Choose…</option>
          {ready.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </select>
      </label>
      <label className="field"><span>Column</span>
        <select value={form[`${prefix}Column`]} required disabled={!form[`${prefix}Dataset`]} onChange={(e) => setForm({ ...form, [`${prefix}Column`]: e.target.value })}>
          <option value="">Choose…</option>
          {columnsOf(form[`${prefix}Dataset`]).map((c) => <option key={c.name} value={c.name}>{c.label || c.name}</option>)}
        </select>
      </label>
    </div>
  );

  return (
    <div className="stack">
      <div className="page-head"><div><p className="eyebrow">Model</p><h1>Datasets and relationships</h1>
        <p className="muted">Relate two datasets on a shared column (a code or ID) and one visual can use fields of both. Datasets may come from different databases: the join happens in the analytics engine.</p></div></div>

      <section className="card">
        <div className="card-head"><div><p className="eyebrow">Relationships</p><h2>{relationships.length} relationship{relationships.length === 1 ? '' : 's'}</h2></div></div>
        {relationships.length === 0 ? <p className="empty">No relationships yet.</p> : (
          <ul className="rel-list">
            {relationships.map((r) => (
              <li key={r.id}>
                <span className="rel-end"><b>{name(r.fromDataset)}</b><code>{r.fromColumn}</code></span>
                <span className="rel-link" aria-label="equals">=</span>
                <span className="rel-end"><b>{name(r.toDataset)}</b><code>{r.toColumn}</code></span>
                <span className="spacer" />
                {mayModel && <button type="button" className="btn small" onClick={() => remove(r)}>Remove</button>}
              </li>
            ))}
          </ul>
        )}
        {mayModel && ready.length >= 2 && (
          <form className="rel-form" onSubmit={add}>
            {side('from', 'From')}
            <span className="rel-link" aria-hidden="true">=</span>
            {side('to', 'To')}
            <button type="submit" className="btn primary" disabled={busy || !form.fromColumn || !form.toColumn}>Add relationship</button>
          </form>
        )}
        {mayModel && ready.length < 2 && <p className="muted">Import at least two datasets to relate them.</p>}
      </section>

      <div className="model-cards">
        {datasets.map((d) => {
          const keys = linked(d.id);
          return (
            <section key={d.id} className="card model-card">
              <h3>{d.name}</h3>
              <p className="muted small">{(d.rowCount || 0).toLocaleString()} records · {d.columns.length} columns</p>
              <ul>
                {d.columns.filter((c) => !c.hidden).slice(0, 30).map((c) => (
                  <li key={c.name} className={keys.has(c.name) ? 'key' : ''}>
                    <span className="kind">{KIND_ICON[c.kind]}</span>{c.label || c.name}
                    {keys.has(c.name) && <span className="chip">related</span>}
                    {c.role === 'measure' && <small className="muted"> Σ</small>}
                  </li>
                ))}
                {d.columns.length > 30 && <li className="muted small">… {d.columns.length - 30} more</li>}
              </ul>
            </section>
          );
        })}
      </div>
    </div>
  );
}
