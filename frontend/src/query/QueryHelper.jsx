// The query helper panel on the right of a result: drop a column header on Filter, Columns or Sort.
// Filter rows get an operator and a value; Apply writes the query (Mongo find or SQL) and runs it.
import { useState } from 'react';
import { FIELD_MIME, NO_VALUE, OPS, TYPES, newFilter, readDrag, withType } from './queryHelper.js';

function Zone({ title, hint, icon, onDropField, empty, children, count }) {
  const [over, setOver] = useState(false);
  return (
    <section className={`qh-zone ${over ? 'over' : ''}`}
      onDragOver={(e) => { if (e.dataTransfer.types.includes(FIELD_MIME)) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; setOver(true); } }}
      onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setOver(false); }}
      onDrop={(e) => { e.preventDefault(); setOver(false); const what = readDrag(e); if (what) onDropField(what); }}>
      <header><span className="qh-ico" aria-hidden="true">{icon}</span><b>{title}</b>{count > 0 && <span className="qh-count">{count}</span>}</header>
      {children}
      {empty && <div className="qh-drop">{hint}</div>}
    </section>
  );
}

export default function QueryHelper({ helper, onChange, onApply, onClear, columns = [], types = {}, kind, preview, onClose }) {
  const set = (patch) => onChange({ ...helper, ...patch });
  const addFilter = (what) => set({ filters: [...helper.filters, newFilter(what)] });
  const addField = (what) => { const field = what.field || what; if (!helper.fields.includes(field)) set({ fields: [...helper.fields, field] }); };
  const addSort = (what) => { const field = what.field || what; if (!helper.sort.some((s) => s.field === field)) set({ sort: [...helper.sort, { field, dir: 'asc' }] }); };
  const editFilter = (id, patch) => set({ filters: helper.filters.map((f) => (f.id === id ? { ...f, ...patch } : f)) });
  const pick = (onPick) => (
    <select className="qh-add" value="" onChange={(e) => { if (e.target.value) onPick(e.target.value); }} aria-label="Add a column">
      <option value="">＋ add…</option>
      {columns.map((c) => <option key={c} value={c}>{c}</option>)}
    </select>
  );
  const busy = helper.filters.length + helper.fields.length + helper.sort.length;

  return (
    <aside className="qh" aria-label="Query helper">
      <div className="qh-head">
        <b>🧩 Query helper</b>
        <span className="grow" />
        <button type="button" className="icon-btn" title="Hide the helper" onClick={onClose}>✕</button>
      </div>
      <p className="qh-tip">Drag a column, or a value itself, from the result and drop it here. Its data type comes along.</p>

      <Zone title={kind === 'sql' ? 'Where' : 'Filter'} icon="⚲" hint="Drop a column or a value to filter by it" empty={!helper.filters.length} onDropField={addFilter} count={helper.filters.length}>
        {helper.filters.length > 1 && (
          <div className="qh-match">
            Rows that match
            <div className="seg-toggle">
              <button type="button" className={helper.match !== 'or' ? 'on' : ''} onClick={() => set({ match: 'and' })}>all</button>
              <button type="button" className={helper.match === 'or' ? 'on' : ''} onClick={() => set({ match: 'or' })}>any</button>
            </div>
          </div>
        )}
        {helper.filters.map((f) => (
          <div key={f.id} className="qh-filter">
            <div className="qh-row">
              <code title={f.field}>{f.field}</code>
              <select className={`qh-type t-${withType(f, types).type}`} aria-label={`Data type of ${f.field}`} title="Data type of the value" value={f.type || ''} onChange={(e) => editFilter(f.id, { type: e.target.value })}>
                {TYPES.map(([key, label]) => <option key={key} value={key}>{key ? label : `${label}${withType({ ...f, type: '' }, types).type ? `: ${TYPES.find(([k]) => k === withType({ ...f, type: '' }, types).type)[1]}` : ''}`}</option>)}
              </select>
              <button type="button" className="icon-btn danger" aria-label={`Remove ${f.field}`} onClick={() => set({ filters: helper.filters.filter((x) => x.id !== f.id) })}>✕</button>
            </div>
            <div className="qh-row">
              <select value={f.op} onChange={(e) => editFilter(f.id, { op: e.target.value })}>{OPS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
              {!NO_VALUE.has(f.op) && (
                <input value={f.value} placeholder={f.op === 'in' ? 'a, b, c' : withType(f, types).type === 'date' ? '2026-01-31' : 'value'} onChange={(e) => editFilter(f.id, { value: e.target.value })}
                  onKeyDown={(e) => { if (e.key === 'Enter') onApply(); }} />
              )}
            </div>
          </div>
        ))}
        {columns.length > 0 && pick(addFilter)}
      </Zone>

      <Zone title={kind === 'sql' ? 'Columns' : 'Projection'} icon="▥" hint="Drop columns to show only those" empty={!helper.fields.length} onDropField={addField} count={helper.fields.length}>
        <div className="qh-chips">
          {helper.fields.map((f, i) => (
            <span key={f} className="qh-chip">
              {i > 0 && <button type="button" className="qh-mini" title="Move left" onClick={() => { const n = [...helper.fields]; [n[i - 1], n[i]] = [n[i], n[i - 1]]; set({ fields: n }); }}>‹</button>}
              {f}
              <button type="button" className="qh-mini" aria-label={`Remove ${f}`} onClick={() => set({ fields: helper.fields.filter((x) => x !== f) })}>✕</button>
            </span>
          ))}
        </div>
        {columns.length > 0 && pick(addField)}
      </Zone>

      <Zone title="Sort" icon="⇅" hint="Drop a column to sort by it" empty={!helper.sort.length} onDropField={addSort} count={helper.sort.length}>
        {helper.sort.map((s, i) => (
          <div key={s.field} className="qh-row qh-sort">
            <span className="qh-n">{i + 1}</span>
            <code title={s.field}>{s.field}</code>
            <button type="button" className="btn sm" onClick={() => set({ sort: helper.sort.map((x) => (x.field === s.field ? { ...x, dir: x.dir === 'desc' ? 'asc' : 'desc' } : x)) })}>
              {s.dir === 'desc' ? '↓ Z→A' : '↑ A→Z'}
            </button>
            <button type="button" className="icon-btn danger" aria-label={`Remove ${s.field}`} onClick={() => set({ sort: helper.sort.filter((x) => x.field !== s.field) })}>✕</button>
          </div>
        ))}
        {columns.length > 0 && pick(addSort)}
      </Zone>

      {busy > 0 && <pre className="qh-preview">{preview}</pre>}
      <div className="qh-actions">
        <button type="button" className="btn sm" disabled={!busy} onClick={onClear}>Clear</button>
        <button type="button" className="btn primary" onClick={onApply}>▶ Apply</button>
      </div>
    </aside>
  );
}
