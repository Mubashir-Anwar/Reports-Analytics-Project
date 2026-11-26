// Wizards that open when a Table, Crosstab or Subreport is dropped on a band (like classic report designers's).
import { useState } from 'react';
import { columnFor, defaultMapping, FOOTERS, tableHeight, useServerReports } from './ComponentProps.jsx';

export default function ComponentWizard({ wizard, report, design, bandWidth, onCancel, onDone }) {
  const { element } = wizard;
  const fields = [...report.columns, ...design.calculatedFields.map((c) => c.name)];
  const [picked, setPicked] = useState(() => fields.slice(0, 8));
  const [footers, setFooters] = useState(() => Object.fromEntries(fields.map((f) => [f, columnFor(f, 0).footer])));
  const [cross, setCross] = useState({ rowField: fields[0] || '', columnField: fields[1] || '', measureField: fields[2] || '', calculation: 'Sum' });
  const [subId, setSubId] = useState('');
  const { reports, error } = useServerReports();

  const toggle = (field) => setPicked((list) => (list.includes(field) ? list.filter((f) => f !== field) : [...fields.filter((f) => list.includes(f) || f === field)]));
  const room = Math.max(60, bandWidth - element.x);

  const finish = () => {
    if (element.type === 'table') {
      const width = Math.max(30, Math.floor(room / Math.max(1, picked.length)));
      const columns = picked.map((f) => ({ ...columnFor(f, width), footer: footers[f] || 'none' }));
      const draft = { ...element, columns, width: width * columns.length };
      onDone({ ...draft, height: tableHeight(draft) });
    } else if (element.type === 'crosstab') {
      onDone({ ...element, ...cross, width: Math.min(room, Math.max(element.width, 260)), cornerText: cross.rowField });
    } else {
      const target = reports.find((r) => r.id === subId);
      onDone({ ...element, reportId: subId, parameters: defaultMapping(target, report, design), width: Math.min(room, element.width) });
    }
  };
  const ready = element.type === 'table' ? picked.length > 0
    : element.type === 'crosstab' ? cross.rowField && cross.columnField && (cross.calculation === 'Count' || cross.measureField)
      : Boolean(subId);
  const title = { table: 'Table wizard', crosstab: 'Crosstab wizard', subreport: 'Subreport wizard' }[element.type];

  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onCancel()}>
      <div className="modal wizard-modal" role="dialog" aria-label={title}>
        <h3>{title}</h3>
        {!fields.length && element.type !== 'subreport' && <p className="err">Detect the fields first (Setup tab → Detect fields). You can also add columns later in Properties.</p>}

        {element.type === 'table' && (
          <>
            <p className="muted">Choose the fields (columns, in this order) and a total for the footer. Records come from the report's query; filter or sort them later in Properties → Dataset.</p>
            <div className="wizard-fields">
              {fields.map((f) => (
                <div key={f} className={`wizard-field ${picked.includes(f) ? 'on' : ''}`}>
                  <label><input type="checkbox" checked={picked.includes(f)} onChange={() => toggle(f)} /> {f}</label>
                  <select value={footers[f]} disabled={!picked.includes(f)} onChange={(e) => setFooters({ ...footers, [f]: e.target.value })} title="Footer total">
                    {FOOTERS.filter(([v]) => v !== 'text').map(([v, l]) => <option key={v} value={v}>{v === 'none' ? 'No total' : l}</option>)}
                  </select>
                </div>
              ))}
            </div>
            <div className="row-actions">
              <button type="button" className="btn sm" onClick={() => setPicked(fields)}>All</button>
              <button type="button" className="btn sm" onClick={() => setPicked([])}>None</button>
            </div>
          </>
        )}

        {element.type === 'crosstab' && (
          <>
            <p className="muted">A summary table: one row per value of the row group, one column per value of the column group, the measure in each cell, with totals.</p>
            {[['rowField', 'Row group (down)'], ['columnField', 'Column group (across)'], ['measureField', 'Measure']].map(([key, label]) => (
              <label key={key} className="modal-field">{label}
                <select value={cross[key]} onChange={(e) => setCross({ ...cross, [key]: e.target.value })}>
                  <option value="">Choose…</option>
                  {fields.map((f) => <option key={f}>{f}</option>)}
                </select>
              </label>
            ))}
            <label className="modal-field">Calculation
              <select value={cross.calculation} onChange={(e) => setCross({ ...cross, calculation: e.target.value })}>{['Sum', 'Count', 'Average', 'Min', 'Max'].map((c) => <option key={c}>{c}</option>)}</select>
            </label>
          </>
        )}

        {element.type === 'subreport' && (
          <>
            <p className="muted">Choose a report that is published on the server. It runs its own query; its parameters are filled from this report (same-named fields / parameters are matched for you — change them in Properties).</p>
            {error && <p className="err">{error}</p>}
            <label className="modal-field">Report
              <select autoFocus value={subId} onChange={(e) => setSubId(e.target.value)}>
                <option value="">{reports.length ? 'Choose a report…' : 'Loading…'}</option>
                {reports.filter((r) => r.id !== report.id).map((r) => <option key={r.id} value={r.id}>{r.folder} / {r.title} ({r.id})</option>)}
              </select>
            </label>
            {subId && (
              <ul className="wizard-map">
                {defaultMapping(reports.find((r) => r.id === subId), report, design).map((m) => <li key={m.name}><code>$P{'{'}{m.name}{'}'}</code> ← {m.expression ? <code>{m.expression}</code> : <i>set it in Properties</i>}</li>)}
              </ul>
            )}
          </>
        )}

        <div className="row">
          <button className="btn" onClick={onCancel}>Cancel</button>
          <button className="btn primary" disabled={!ready} onClick={finish}>Finish</button>
        </div>
      </div>
    </div>
  );
}
