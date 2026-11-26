// "Choose a layout template" (like a classic report designer's report templates). Nothing changes until one is chosen.
// The Table report asks how it should look first (title place, headings, borders, column widths, Excel), with a
// small live preview; the answers are remembered for the next table.
import { useState } from 'react';
import { ROLES, TEMPLATES, applyTemplate, guessRoles } from './templates.js';
import { TABLE_OPTIONS } from './designModel.js';

const REMEMBER = 'designer.tableOptions';
/** Layouts people usually already have on paper: ask for their own design before the ready-made one. */
const OWN_DESIGN = { letter: 'letter', certificate: 'certificate', form: 'form', invoice: 'invoice', challan: 'challan' };

function rememberedOptions() {
  try { return { ...TABLE_OPTIONS, ...JSON.parse(window.localStorage.getItem(REMEMBER) || '{}') }; } catch { return { ...TABLE_OPTIONS }; }
}

/** Small drawings of each template. */
function Thumb({ id }) {
  const line = (x, y, w, c = '#94a3b8', h = 3) => <rect x={x} y={y} width={w} height={h} rx="1" fill={c} />;
  const portrait = !['blank-landscape', 'certificate', 'challan'].includes(id);
  return (
    <svg viewBox={portrait ? '0 0 60 80' : '0 0 80 60'} className="tpl-thumb" aria-hidden="true">
      <rect x="0.5" y="0.5" width={portrait ? 59 : 79} height={portrait ? 79 : 59} rx="2" fill="#fff" stroke="#cbd5e1" />
      {id === 'blank' && <>{line(6, 6, 30, '#cbd5e1')}{line(36, 73, 18, '#e2e8f0', 2)}</>}
      {id === 'blank-landscape' && <>{line(6, 6, 40, '#cbd5e1')}{line(56, 53, 18, '#e2e8f0', 2)}</>}
      {id === 'letter' && <><rect x="6" y="6" width="14" height="7" fill="#bfdbfe" />{line(30, 7, 24, '#334155')}{line(6, 16, 48, '#e2e8f0', 1)}{line(38, 20, 16)}{line(6, 24, 20, '#334155')}{line(6, 28, 16)}{[34, 38, 42, 46, 50].map((y) => <g key={y}>{line(6, y, 48, '#cbd5e1', 2)}</g>)}{line(6, 66, 16, '#334155', 1)}</>}
      {id === 'table' && <>{line(6, 6, 34, '#334155', 4)}{line(6, 16, 48, '#93c5fd', 5)}{[24, 29, 34, 39, 44, 49, 54].map((y) => <g key={y}>{line(6, y, 48, '#e2e8f0', 3)}</g>)}</>}
      {id === 'form' && <>{line(6, 6, 30, '#334155', 4)}{[16, 22, 28, 34, 46, 52, 58, 64].map((y) => <g key={y}>{line(6, y, 14, '#cbd5e1')}{line(23, y, 31)}</g>)}{line(6, 41, 48, '#e2e8f0', 1)}</>}
      {id === 'invoice' && <><rect x="6" y="5" width="12" height="6" fill="#bfdbfe" />{line(34, 6, 20, '#334155')}{line(6, 15, 18, '#1e3a8a', 4)}{line(38, 15, 16)}{line(6, 22, 22, '#334155')}{line(6, 29, 48, '#cbd5e1', 4)}{[35, 40, 45, 50].map((y) => <g key={y}>{line(6, y, 30, '#e2e8f0', 2)}{line(44, y, 10, '#e2e8f0', 2)}</g>)}{line(36, 57, 18, '#334155', 4)}{line(38, 70, 16, '#334155', 1)}</>}
      {id === 'challan' && <>{[3, 29, 55].map((x) => <g key={x}><rect x={x} y="4" width="22" height="52" fill="none" stroke="#334155" strokeWidth="0.6" />{line(x + 3, 8, 16, '#334155', 2)}{line(x + 3, 13, 16, '#334155', 3)}{[20, 25, 30, 35].map((y) => <g key={y}>{line(x + 3, y, 6, '#cbd5e1', 2)}{line(x + 10, y, 9, '#94a3b8', 2)}</g>)}{line(x + 3, 42, 16, '#e2e8f0', 4)}</g>)}</>}
      {id === 'statement' && <>{line(6, 6, 30, '#334155', 4)}<rect x="6" y="14" width="48" height="9" fill="#f8fafc" stroke="#cbd5e1" strokeWidth="0.5" />{line(9, 17, 20, '#334155', 3)}{line(6, 27, 48, '#e2e8f0', 4)}{[34, 39, 44, 49, 54].map((y) => <g key={y}>{line(6, y, 8, '#e2e8f0', 2)}{line(16, y, 20, '#e2e8f0', 2)}{line(44, y, 10, '#94a3b8', 2)}</g>)}{line(36, 62, 18, '#334155', 4)}</>}
      {id === 'certificate' && <><rect x="3" y="3" width="74" height="54" fill="none" stroke="#1e3a8a" strokeWidth="1.5" />{line(18, 11, 44, '#1e3a8a', 4)}{line(28, 21, 24, '#94a3b8', 2)}{line(20, 27, 40, '#334155', 5)}{line(22, 36, 36, '#cbd5e1', 2)}{line(10, 48, 18, '#334155', 1)}{line(52, 48, 18, '#334155', 1)}</>}
    </svg>
  );
}

/** One question with a few answers as buttons. */
function Choice({ label, value, options, onChange, hint }) {
  return (
    <div className="to-choice">
      <span className="to-label">{label}</span>
      <div className="to-buttons" role="radiogroup" aria-label={label}>
        {options.map(([v, text]) => (
          <button key={String(v)} type="button" role="radio" aria-checked={value === v} className={value === v ? 'on' : ''} onClick={() => onChange(v)}>{text}</button>
        ))}
      </div>
      {hint && <small className="muted">{hint}</small>}
    </div>
  );
}

const YES_NO = [[true, 'Yes'], [false, 'No']];
const FILLS = [['#e2e8f0', 'Grey'], ['#bfdbfe', 'Blue'], ['#dcfce7', 'Green'], ['', 'None']];

/** A small picture of the table with the chosen options (the first columns, three sample rows). */
function TablePreview({ o, columns, title, reportId }) {
  const cols = (columns.length ? columns : ['Field 1', 'Field 2', 'Field 3']).slice(0, 5);
  const head = { textAlign: o.headAlign, verticalAlign: o.headVAlign, fontWeight: o.headBold ? 700 : 400, background: o.headFill || 'transparent', border: o.headBorder ? '1px solid #64748b' : '1px solid transparent', height: 26 };
  const cell = { border: o.dataBorder ? '1px solid #64748b' : '1px solid transparent' };
  return (
    <div className="to-preview" aria-label="Preview">
      <div className="to-top">
        {o.showTitle && <b className="to-title" style={{ textAlign: o.titleAlign }}>{title || 'Report'}</b>}
        {o.infoBox && (
          <table className="to-info"><tbody>
            <tr><th>Report ID:</th><td>{reportId || 'REPORT_ID'}</td></tr>
            <tr><th>Run on:</th><td>19-Nov-2026 13:19</td></tr>
            <tr><th>Page:</th><td>1 of 3</td></tr>
          </tbody></table>
        )}
      </div>
      <table className="to-table" style={o.widths === 'equal' ? { width: '100%', tableLayout: 'fixed' } : undefined}>
        <thead><tr>{cols.map((c) => <th key={c} style={head}>{c}</th>)}{columns.length > 5 && <th style={head}>…</th>}</tr></thead>
        <tbody>{[1, 2, 3].map((r) => <tr key={r}>{cols.map((c) => <td key={c} style={cell}>{`${c.split('.').pop()} ${r}`}</td>)}{columns.length > 5 && <td style={cell}>…</td>}</tr>)}</tbody>
      </table>
      <div className="to-bottom">
        {o.totalRecords ? <small><b>Total records: 3</b></small> : <span />}
        {o.pageNumbers && <small>Page 1 of 3</small>}
      </div>
    </div>
  );
}

export default function TemplateGallery({ report, design, onApply, onClose, onImportDesign }) {
  const [chosen, setChosen] = useState('letter');
  const [step, setStep] = useState('pick');            // 'pick' → (Table report) 'options' | (Letter…) 'own'
  const [options, setOptions] = useState(rememberedOptions);
  const [roles, setRoles] = useState({});             // Invoice / Statement: which field is the invoice number, amount…
  const template = TEMPLATES.find((t) => t.id === chosen);
  const hasContent = Object.values(design.bands).some((band) => band?.elements?.length || band?.more?.length)
    || Object.values(design.bands.groupHeaders || {}).some((b) => b.elements.length);
  const missingFields = template.needsFields && !report.columns.length;
  const set = (patch) => setOptions((o) => ({ ...o, ...patch }));

  const apply = (id = chosen, fromOptions = false) => {
    const picked = TEMPLATES.find((t) => t.id === id);
    if (picked.needsFields && !report.columns.length) return;
    if (picked.hasOptions && !fromOptions) { setChosen(id); setStep('options'); return; }  // ask how it should look first
    if (picked.hasRoles && report.columns.length && !fromOptions) { setChosen(id); setRoles(guessRoles(id, report.columns)); setStep('roles'); return; }
    if (OWN_DESIGN[id] && onImportDesign && !fromOptions) { setChosen(id); setStep('own'); return; }  // their own design first?
    if (hasContent && !window.confirm('Replace the current design with this template? (Ctrl+Z undoes it)')) return;
    if (picked.hasOptions) {
      try { window.localStorage.setItem(REMEMBER, JSON.stringify(options)); } catch { /* not kept: fine */ }
    }
    onApply(applyTemplate(id, report.columns, report.title, design, { ...options, reportId: report.id, ...(picked.hasRoles ? { roles } : {}) }), picked.name);
  };

  if (step === 'roles') {
    const list = ROLES[chosen];
    const missing = list.filter(([key]) => !roles[key]);
    return (
      <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
        <div className="modal template-modal" role="dialog" aria-label={`${template.name}: which field is which`}>
          <h3>{template.name} — which field is which?</h3>
          <p className="muted">Guessed from the field names; change any that are wrong. Leave one empty and a placeholder is put there for you to change later.</p>
          <div className="role-grid">
            {list.map(([key, label, , hint]) => (
              <label key={key} className="role-row">
                <span><b>{label}</b>{hint && <small>{hint}</small>}</span>
                <select value={roles[key] || ''} onChange={(e) => setRoles((r) => ({ ...r, [key]: e.target.value }))}>
                  <option value="">— none (placeholder) —</option>
                  {report.columns.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </label>
            ))}
          </div>
          {missing.length > 0 && <p className="muted small">Not chosen: {missing.map(([, label]) => label).join(', ')}.</p>}
          <div className="row">
            <button className="btn" onClick={() => setStep('pick')}>← Other layouts</button>
            <button className="btn" onClick={onClose}>Cancel</button>
            <button className="btn primary" onClick={() => apply(chosen, true)}>Create the {template.name.toLowerCase()}</button>
          </div>
        </div>
      </div>
    );
  }

  if (step === 'own') {
    const what = OWN_DESIGN[chosen];
    return (
      <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
        <div className="modal template-modal own-design" role="dialog" aria-label={`Your own ${what} design`}>
          <h3>Do you already have a design for this {what}?</h3>
          <p className="muted">If your {what} already exists — as a PDF, Word file, Illustrator file, or even a scan or photo — it can be rebuilt here exactly as it looks. Then you choose which texts are filled from the data (name, date, amount…).</p>
          <div className="own-choices">
            <button type="button" className="own-card primary" onClick={() => onImportDesign(what)}>
              <b>Yes — use my own design</b>
              <span>PDF · Word .docx · Illustrator .ai · PNG / JPEG scan. The text of a scan or photo is read with OCR.</span>
            </button>
            <button type="button" className="own-card" onClick={() => apply(chosen, true)}>
              <Thumb id={chosen} />
              <b>No — start from the ready-made {template.name}</b>
              <span>{template.text}</span>
            </button>
          </div>
          <div className="row">
            <button className="btn" onClick={() => setStep('pick')}>← Other layouts</button>
            <button className="btn" onClick={onClose}>Cancel</button>
          </div>
        </div>
      </div>
    );
  }

  if (step === 'options') {
    return (
      <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
        <div className="modal template-modal table-options" role="dialog" aria-label="Table report options">
          <h3>Table report — how should it look?</h3>
          <p className="muted">{report.columns.length} fields from the query become the columns. Pick what you want; the preview changes at once. Everything can still be changed on the canvas later.</p>
          <div className="to-body">
            <div className="to-form">
              <fieldset>
                <legend>Top of the report</legend>
                <Choice label="Report name" value={options.showTitle} options={YES_NO} onChange={(v) => set({ showTitle: v })} />
                {options.showTitle && <Choice label="Name position" value={options.titleAlign} options={[['left', 'Left'], ['center', 'Centre'], ['right', 'Right']]} onChange={(v) => set({ titleAlign: v })} />}
                <Choice label="Info box (right)" value={options.infoBox} options={YES_NO} onChange={(v) => set({ infoBox: v })} hint="Report ID, run date and Page X of Y in a bordered box." />
              </fieldset>
              <fieldset>
                <legend>Column headings</legend>
                <Choice label="Across" value={options.headAlign} options={[['left', 'Left'], ['center', 'Centre'], ['right', 'Right']]} onChange={(v) => set({ headAlign: v })} />
                <Choice label="Up / down" value={options.headVAlign} options={[['top', 'Top'], ['middle', 'Middle'], ['bottom', 'Bottom']]} onChange={(v) => set({ headVAlign: v })} />
                <Choice label="Bold" value={options.headBold} options={YES_NO} onChange={(v) => set({ headBold: v })} />
                <Choice label="Background" value={options.headFill} options={FILLS} onChange={(v) => set({ headFill: v })} />
              </fieldset>
              <fieldset>
                <legend>Borders and widths</legend>
                <Choice label="Border on every heading" value={options.headBorder} options={YES_NO} onChange={(v) => set({ headBorder: v })} />
                <Choice label="Border on every data cell" value={options.dataBorder} options={YES_NO} onChange={(v) => set({ dataBorder: v })} />
                <Choice label="Column width" value={options.widths} options={[['text', 'Fit the heading text'], ['equal', 'Equal, fill the page']]} onChange={(v) => set({ widths: v })}
                  hint={options.widths === 'text' ? 'Long names (e.g. StageDDESales.Status) are never cut; the page grows if needed.' : 'Long headings wrap onto two lines.'} />
              </fieldset>
              <fieldset>
                <legend>Bottom and Excel</legend>
                <Choice label="Page numbers" value={options.pageNumbers} options={YES_NO} onChange={(v) => set({ pageNumbers: v })} />
                <Choice label="Total records" value={options.totalRecords} options={YES_NO} onChange={(v) => set({ totalRecords: v })} />
                <Choice label="Excel / CSV" value={options.sheetLayout}
                  options={[['mixed', 'Headings pixel-perfect + data table'], ['grid', 'All pixel-perfect'], ['table', 'Data table only']]} onChange={(v) => set({ sheetLayout: v })}
                  hint={{ mixed: 'Top part exactly like the PDF; the data is a filterable table (recommended).', grid: 'The whole sheet looks like the PDF, footers and totals too.', table: 'Only one heading row and the data; headings above as plain rows.' }[options.sheetLayout]} />
              </fieldset>
            </div>
            <TablePreview o={options} columns={report.columns} title={report.title} reportId={report.id} />
          </div>
          <div className="row">
            <button className="btn" onClick={() => setStep('pick')}>← Other layouts</button>
            <button className="btn" onClick={() => setOptions({ ...TABLE_OPTIONS })}>Reset</button>
            <button className="btn" onClick={onClose}>Cancel</button>
            <button className="btn primary" onClick={() => apply(chosen, true)}>Create the table</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal template-modal" role="dialog" aria-label="Layout templates">
        <h3>Choose a layout</h3>
        <p className="muted">Start from a template, then change anything on the canvas. Fields used in a template come from the report's fields{report.columns.length ? ` (${report.columns.length} found)` : ' — none yet, so placeholders are used'}.</p>
        <ul className="template-grid">
          {TEMPLATES.map((t) => (
            <li key={t.id}>
              <button type="button" className={t.id === chosen ? 'on' : ''} onClick={() => setChosen(t.id)} onDoubleClick={() => apply(t.id)}>
                <Thumb id={t.id} />
                <b>{t.name}</b>
                <small>{t.text}</small>
              </button>
            </li>
          ))}
        </ul>
        {onImportDesign && (
          <button type="button" className="own-banner" onClick={() => onImportDesign('')}>
            <b>Have your own design?</b> Import a PDF, Word, Illustrator file or a scan and it is rebuilt here, ready for fields →
          </button>
        )}
        {missingFields && <p className="err">The table template needs the fields: Setup → Read fields first.</p>}
        <div className="row">
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={missingFields} onClick={() => apply()}>{template.hasOptions ? `Next: options for “${template.name}” →` : template.hasRoles && report.columns.length ? `Next: choose the fields →` : OWN_DESIGN[chosen] && onImportDesign ? `Next: “${template.name}” →` : `Use “${template.name}”`}</button>
        </div>
      </div>
    </div>
  );
}
