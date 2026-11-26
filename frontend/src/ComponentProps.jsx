// Properties of the data components (a classic report designer's Table, Crosstab, List, Subreport) and the designer Note.
import { useEffect, useState } from 'react';
import { api } from './api.js';
import { groupOfBand } from './designModel.js';

function Row({ label, children, title }) {
  return <label className="prop-row" title={title}><span>{label}</span>{children}</label>;
}
function Num({ value, onChange, min, max, step = 1 }) {
  return <input type="number" value={Number.isFinite(value) ? value : ''} min={min} max={max} step={step} onChange={(e) => onChange(e.target.value === '' ? 0 : Number(e.target.value))} />;
}
function Color({ value, onChange, allowNone, fallback = '#ffffff' }) {
  return (
    <span className="inline">
      <input type="color" value={value || fallback} onChange={(e) => onChange(e.target.value)} />
      {allowNone && <button type="button" className={`btn sm ${value ? '' : 'on'}`} onClick={() => onChange(null)} title="No color">none</button>}
    </span>
  );
}

export const FOOTERS = [['none', '—'], ['Sum', 'Sum'], ['Count', 'Count'], ['Average', 'Average'], ['Min', 'Min'], ['Max', 'Max'], ['text', 'Text']];
const PATTERN_CHOICES = ['#,##0', '#,##0.00', '0.00%', 'dd/MM/yyyy', 'dd/MM/yyyy HH:mm', 'HH:mm'];

/** A table column for a field: header = field name, numbers right-aligned by name guess. */
export function columnFor(field, width) {
  const numeric = /amount|total|sum|price|qty|quantity|count|balance|value|rate|percent|number|no$/i.test(field);
  const dated = /date|time|day$|_at$|on$/i.test(field);
  return { header: field, expression: `$F{${field}}`, width, pattern: numeric ? '#,##0.00' : dated ? 'dd/MM/yyyy' : '', align: numeric ? 'right' : 'left', footer: numeric ? 'Sum' : 'none', footerText: '' };
}

/** Height on the canvas: header + one detail row + footer (the printed table grows with its records). */
export function tableHeight(t) {
  const footer = (t.columns || []).some((c) => c.footer && c.footer !== 'none') ? (t.footerHeight || 0) : 0;
  return Math.max(16, (t.headerHeight || 0) + (t.rowHeight || 16) + footer);
}

export function TableProps({ element, patch, fields, onOpenEditor }) {
  const columns = element.columns || [];
  const setColumns = (next) => {
    const draft = { ...element, columns: next };
    patch({ columns: next, width: Math.max(10, next.reduce((sum, c) => sum + (Number(c.width) || 0), 0)), height: tableHeight(draft) });
  };
  const change = (index, values) => setColumns(columns.map((c, i) => (i === index ? { ...c, ...values } : c)));
  const move = (index, by) => {
    const next = [...columns];
    const [item] = next.splice(index, 1);
    next.splice(Math.max(0, Math.min(next.length, index + by)), 0, item);
    setColumns(next);
  };
  const missing = fields.filter((f) => !columns.some((c) => c.expression === `$F{${f}}`));
  const style = (values) => patch({ ...values, height: tableHeight({ ...element, ...values }) });
  return (
    <>
      {onOpenEditor && <button type="button" className="btn primary wide" onClick={onOpenEditor}>Open the table editor…</button>}
      <div className="prop-section">Columns <small>({columns.length}, total width {Math.round(columns.reduce((s, c) => s + (c.width || 0), 0))}px)</small></div>
      <datalist id="table-patterns">{PATTERN_CHOICES.map((p) => <option key={p} value={p} />)}</datalist>
      <div className="col-editor">
        {columns.map((col, index) => (
          // eslint-disable-next-line react/no-array-index-key -- columns have no id; order is the identity
          <div className="col-card" key={index}>
            <div className="col-card-head">
              <b>{index + 1}.</b>
              <input value={col.header} placeholder="Header" onChange={(e) => change(index, { header: e.target.value })} title="Column header" />
              <button type="button" className="btn sm" disabled={!index} onClick={() => move(index, -1)} title="Move left">◀</button>
              <button type="button" className="btn sm" disabled={index === columns.length - 1} onClick={() => move(index, 1)} title="Move right">▶</button>
              <button type="button" className="btn sm danger-text" onClick={() => setColumns(columns.filter((_, i) => i !== index))} title="Remove column">✕</button>
            </div>
            <input className="mono" value={col.expression} placeholder="$F{Field}  or  =$F{A} * 2" onChange={(e) => change(index, { expression: e.target.value })} title="Detail expression (one value per record)" />
            <div className="col-card-grid">
              <label>W<Num value={col.width} min={5} max={5000} onChange={(width) => change(index, { width: Math.max(5, width) })} /></label>
              <label>Pattern<input list="table-patterns" value={col.pattern} onChange={(e) => change(index, { pattern: e.target.value })} /></label>
              <label>Align<select value={col.align} onChange={(e) => change(index, { align: e.target.value })}><option value="left">Left</option><option value="center">Center</option><option value="right">Right</option></select></label>
              <label>Footer<select value={col.footer || 'none'} onChange={(e) => change(index, { footer: e.target.value })}>{FOOTERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
            </div>
            {col.footer === 'text' && <input value={col.footerText || ''} placeholder="Footer text, e.g. Total" onChange={(e) => change(index, { footerText: e.target.value })} />}
          </div>
        ))}
      </div>
      <div className="row-actions">
        <button type="button" className="btn sm" onClick={() => setColumns([...columns, { ...columnFor(missing[0] || 'Field', 90), ...(missing[0] ? {} : { expression: '', header: 'Column' }) }])}>＋ Column</button>
        <button type="button" className="btn sm" disabled={!missing.length} onClick={() => setColumns([...columns, ...missing.map((f) => columnFor(f, 90))])} title="One column per field that is not in the table yet">＋ All fields ({missing.length})</button>
        <button type="button" className="btn sm" disabled={!columns.length} onClick={() => { const w = Math.floor(element.width / columns.length); setColumns(columns.map((c) => ({ ...c, width: w }))); }} title="Same width for every column">Equal widths</button>
      </div>
      <div className="prop-section">Look</div>
      <div className="prop-grid">
        <Row label="Header height"><Num value={element.headerHeight} min={0} max={300} onChange={(headerHeight) => style({ headerHeight })} /></Row>
        <Row label="Row height"><Num value={element.rowHeight} min={4} max={300} onChange={(rowHeight) => style({ rowHeight: Math.max(4, rowHeight) })} /></Row>
        <Row label="Footer height"><Num value={element.footerHeight} min={0} max={300} onChange={(footerHeight) => style({ footerHeight })} /></Row>
        <Row label="Grid line"><Num value={element.gridWidth} min={0} max={5} step={0.25} onChange={(gridWidth) => patch({ gridWidth })} /></Row>
        <Row label="Header color"><Color value={element.headerBackColor} allowNone onChange={(headerBackColor) => patch({ headerBackColor })} /></Row>
        <Row label="Header text"><Color value={element.headerColor} fallback="#000000" onChange={(headerColor) => patch({ headerColor })} /></Row>
        <Row label="Zebra rows"><Color value={element.zebraColor} allowNone fallback="#f8fafc" onChange={(zebraColor) => patch({ zebraColor })} /></Row>
        <Row label="Grid color"><Color value={element.gridColor} fallback="#94a3b8" onChange={(gridColor) => patch({ gridColor })} /></Row>
      </div>
      <label className="check-row"><input type="checkbox" checked={element.repeatHeader !== false} onChange={(e) => patch({ repeatHeader: e.target.checked })} /> Repeat the header on every page</label>
      <p className="hint">A table: one row per record. Long values wrap and the row grows (nothing is cut). Totals use the column's own pattern. In Excel the totals are real numbers.</p>
    </>
  );
}

export function CrosstabProps({ element, patch, fields }) {
  const fieldSelect = (key, optional) => (
    <select value={element[key] || ''} onChange={(e) => patch({ [key]: e.target.value })}>
      <option value="">{optional ? '— (Count only)' : 'Choose…'}</option>
      {fields.map((f) => <option key={f}>{f}</option>)}
    </select>
  );
  return (
    <>
      <div className="prop-section">Groups and measure</div>
      <Row label="Row group (down)">{fieldSelect('rowField')}</Row>
      <Row label="Column group (across)">{fieldSelect('columnField')}</Row>
      <Row label="Measure field">{fieldSelect('measureField', true)}</Row>
      <div className="prop-grid">
        <Row label="Calculation"><select value={element.calculation} onChange={(e) => patch({ calculation: e.target.value })}>{['Sum', 'Count', 'Average', 'Min', 'Max'].map((c) => <option key={c}>{c}</option>)}</select></Row>
        <Row label="Pattern"><input list="table-patterns" value={element.pattern} onChange={(e) => patch({ pattern: e.target.value })} /></Row>
      </div>
      <datalist id="table-patterns">{PATTERN_CHOICES.map((p) => <option key={p} value={p} />)}</datalist>
      <Row label="Corner text"><input value={element.cornerText} placeholder={element.rowField || 'Row group'} onChange={(e) => patch({ cornerText: e.target.value })} /></Row>
      <label className="check-row"><input type="checkbox" checked={element.showRowTotals !== false} onChange={(e) => patch({ showRowTotals: e.target.checked })} /> Total column (right)</label>
      <label className="check-row"><input type="checkbox" checked={element.showColumnTotals !== false} onChange={(e) => patch({ showColumnTotals: e.target.checked })} /> Total row (bottom)</label>
      <div className="prop-section">Cells</div>
      <div className="prop-grid">
        <Row label="Row header width"><Num value={element.rowHeaderWidth} min={20} max={3000} onChange={(rowHeaderWidth) => patch({ rowHeaderWidth: Math.max(20, rowHeaderWidth) })} /></Row>
        <Row label="Cell width"><Num value={element.cellWidth} min={15} max={2000} onChange={(cellWidth) => patch({ cellWidth: Math.max(15, cellWidth) })} /></Row>
        <Row label="Header height"><Num value={element.headerHeight} min={0} max={300} onChange={(headerHeight) => patch({ headerHeight })} /></Row>
        <Row label="Row height"><Num value={element.rowHeight} min={4} max={300} onChange={(rowHeight) => patch({ rowHeight: Math.max(4, rowHeight) })} /></Row>
        <Row label="Header color"><Color value={element.headerBackColor} allowNone onChange={(headerBackColor) => patch({ headerBackColor })} /></Row>
        <Row label="Totals color"><Color value={element.totalBackColor} allowNone onChange={(totalBackColor) => patch({ totalBackColor })} /></Row>
        <Row label="Grid line"><Num value={element.gridWidth} min={0} max={5} step={0.25} onChange={(gridWidth) => patch({ gridWidth })} /></Row>
        <Row label="Grid color"><Color value={element.gridColor} fallback="#94a3b8" onChange={(gridColor) => patch({ gridColor })} /></Row>
      </div>
      <p className="hint">One row per value of the row group, one column per value of the column group, and the measure in each cell. Columns that do not fit in the box width continue in a second block below (like the classic crosstab column breaks).</p>
    </>
  );
}

export function ListProps() {
  return <p className="hint pad-top">A list: <b>the elements you put inside the List box are printed once for every record</b> (e.g. a card per customer). Draw Text Fields inside it; they move with the List. Choose which records in the <b>Dataset</b> tab. Background / border: Appearance and Borders tabs.</p>;
}

export function NoteProps({ element, patch }) {
  return (
    <>
      <Row label="Note"><textarea rows={5} value={element.text || ''} onChange={(e) => patch({ text: e.target.value })} /></Row>
      <Row label="Color"><Color value={element.backColor} fallback="#fef3c7" onChange={(backColor) => patch({ backColor })} /></Row>
      <p className="hint">A note is only shown in the designer (for you and your team). It is never printed or exported.</p>
    </>
  );
}

/** Which records a Table / Crosstab / List shows (classic reports: the component's dataset run). */
export function DatasetProps({ element, patch, fields, bandKey, report, design, onOpenDatasets }) {
  const inGroup = Boolean(groupOfBand(bandKey)) || bandKey === 'detail';
  const canHaveDataset = ['table', 'crosstab'].includes(element.type);
  const datasets = design?.datasets || [];
  const dataset = datasets.find((d) => d.name === element.datasetName);
  const mapping = element.datasetParameters || [];
  const setMapping = (name, expression) => patch({ datasetParameters: [...mapping.filter((m) => m.name !== name), { name, expression }] });
  const chooseDataset = (name) => {
    const next = datasets.find((d) => d.name === name);
    patch({
      datasetName: name, scope: 'report', sortField: '', filterExpression: '',
      datasetParameters: (next?.parameters || []).map((p) => ({ name: p.name, expression: report.columns.includes(p.name) ? `$F{${p.name}}` : '' })),
    });
  };
  return (
    <>
      {canHaveDataset && (
        <>
          <div className="prop-section">Dataset</div>
          <Row label="Reads">
            <span className="inline">
              <select value={element.datasetName || ''} onChange={(e) => chooseDataset(e.target.value)}>
                <option value="">Main report query</option>
                {datasets.map((d) => <option key={d.name} value={d.name}>Dataset {d.name}</option>)}
              </select>
              <button type="button" className="btn sm" onClick={onOpenDatasets} title="Create or edit datasets">…</button>
            </span>
          </Row>
          {element.datasetName && !dataset && <p className="hint warn">The dataset {element.datasetName} does not exist any more.</p>}
          {dataset && dataset.parameters.map((p) => (
            <Row key={p.name} label={`$P{${p.name}}`} title={`Value for the dataset parameter ${p.name} (${p.type})`}>
              <input className="mono" value={mapping.find((m) => m.name === p.name)?.expression || ''}
                placeholder={report.parameters.some((rp) => rp.name === p.name) ? 'empty = the report parameter' : '$F{Id}'} onChange={(e) => setMapping(p.name, e.target.value)} />
            </Row>
          ))}
          {dataset && <p className="hint">The expressions use the <b>report's</b> fields and parameters (e.g. <code>$F&#123;OrderId&#125;</code> of the current record). The columns and the filter below use the dataset's fields.</p>}
        </>
      )}
      <div className="prop-section">Records</div>
      {!element.datasetName && <Row label="Scope">
        <select value={element.scope || 'report'} onChange={(e) => patch({ scope: e.target.value })}>
          <option value="report">All records of the report</option>
          <option value="group">Only the records of the current group</option>
        </select>
      </Row>}
      {!element.datasetName && element.scope === 'group' && !inGroup && <p className="hint warn">“Current group” works in a Group Header / Footer or in Detail; here it shows all records.</p>}
      <Row label="Filter (print when true)">
        <textarea rows={2} className="mono" value={element.filterExpression || ''} placeholder='$F{Status}.equals("Active")' onChange={(e) => patch({ filterExpression: e.target.value })} />
      </Row>
      <div className="prop-grid">
        <Row label="Sort by"><select value={element.sortField || ''} onChange={(e) => patch({ sortField: e.target.value })}><option value="">Report order</option>{fields.map((f) => <option key={f}>{f}</option>)}</select></Row>
        <Row label="Order"><select value={element.sortOrder || 'Ascending'} onChange={(e) => patch({ sortOrder: e.target.value })}><option>Ascending</option><option>Descending</option></select></Row>
      </div>
      <p className="hint">The records come from {dataset ? `the dataset ${dataset.name}` : "the report's own query"}. In the filter, <code>$F&#123;…&#125;</code> is the record and <code>$P&#123;…&#125;</code> a report parameter.</p>
    </>
  );
}

/** Published reports on the server (for the Subreport picker), loaded once. */
let reportsCache = null;
export function useServerReports() {
  const [reports, setReports] = useState(reportsCache || []);
  const [error, setError] = useState('');
  useEffect(() => {
    if (reportsCache) return;
    api.reports().then((data) => {
      reportsCache = (data.reports || []).map((r) => ({ id: r.id, title: r.title || r.id, folder: r.folder || 'General', parameters: (r.parameters || []).filter((p) => !p.expression) }));
      setReports(reportsCache);
    }).catch((e) => setError(e.message));
  }, []);
  return { reports, error, reload: () => { reportsCache = null; setReports([]); } };
}

/** Default parameter mapping: a field or parameter of the same name, else empty. */
export function defaultMapping(target, report, design) {
  const fields = [...report.columns, ...design.calculatedFields.map((c) => c.name)];
  const params = report.parameters.map((p) => p.name);
  return (target?.parameters || []).map((p) => ({
    name: p.name,
    expression: fields.includes(p.name) ? `$F{${p.name}}` : params.includes(p.name) ? `$P{${p.name}}` : '',
  }));
}

export function SubreportProps({ element, patch, report, design }) {
  const { reports, error } = useServerReports();
  const target = reports.find((r) => r.id === element.reportId);
  const mapping = element.parameters || [];
  const names = [...new Set([...(target?.parameters || []).map((p) => p.name), ...mapping.map((m) => m.name)])];
  const setExpression = (name, expression) => {
    const rest = mapping.filter((m) => m.name !== name);
    patch({ parameters: [...rest, { name, expression }].filter((m) => names.includes(m.name)) });
  };
  return (
    <>
      <Row label="Report (published on the server)">
        <select value={element.reportId || ''} onChange={(e) => { const next = reports.find((r) => r.id === e.target.value); patch({ reportId: e.target.value, parameters: defaultMapping(next, report, design) }); }}>
          <option value="">Choose a report…</option>
          {reports.filter((r) => r.id !== report.id).map((r) => <option key={r.id} value={r.id}>{r.folder} / {r.title} ({r.id})</option>)}
        </select>
      </Row>
      {error && <p className="err">{error}</p>}
      {element.reportId && !target && reports.length > 0 && <p className="hint warn">{element.reportId} is not published on the server (publish it first).</p>}
      <label className="check-row"><input type="checkbox" checked={element.showTitle !== false} onChange={(e) => patch({ showTitle: e.target.checked })} /> Print the subreport's Title band</label>
      <div className="prop-section">Parameters <small>(value sent to the subreport)</small></div>
      {!names.length && <p className="hint">The chosen report has no parameters.</p>}
      {names.map((name) => (
        <Row key={name} label={`$P{${name}}`} title={target?.parameters.find((p) => p.name === name)?.label || name}>
          <input className="mono" value={mapping.find((m) => m.name === name)?.expression || ''} placeholder="$F{Identifier}" onChange={(e) => setExpression(name, e.target.value)} />
        </Row>
      ))}
      <p className="hint">A subreport: when this report runs, the subreport runs <b>its own query</b> with these parameter values and prints its bands here (Title, Column Header, Detail, groups, Summary). In the Detail band it runs once per record, e.g. the orders of each customer.</p>
    </>
  );
}
