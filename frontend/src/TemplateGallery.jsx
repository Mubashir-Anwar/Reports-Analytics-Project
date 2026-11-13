// "Choose a layout template" (like Jaspersoft Studio's report templates). Nothing changes until one is chosen.
import { useState } from 'react';
import { TEMPLATES, applyTemplate } from './templates.js';

/** Small drawings of each template. */
function Thumb({ id }) {
  const line = (x, y, w, c = '#94a3b8', h = 3) => <rect x={x} y={y} width={w} height={h} rx="1" fill={c} />;
  const portrait = id !== 'blank-landscape' && id !== 'certificate';
  return (
    <svg viewBox={portrait ? '0 0 60 80' : '0 0 80 60'} className="tpl-thumb" aria-hidden="true">
      <rect x="0.5" y="0.5" width={portrait ? 59 : 79} height={portrait ? 79 : 59} rx="2" fill="#fff" stroke="#cbd5e1" />
      {id === 'blank' && <>{line(6, 6, 30, '#cbd5e1')}{line(36, 73, 18, '#e2e8f0', 2)}</>}
      {id === 'blank-landscape' && <>{line(6, 6, 40, '#cbd5e1')}{line(56, 53, 18, '#e2e8f0', 2)}</>}
      {id === 'letter' && <><rect x="6" y="6" width="14" height="7" fill="#bfdbfe" />{line(30, 7, 24, '#334155')}{line(6, 16, 48, '#e2e8f0', 1)}{line(38, 20, 16)}{line(6, 24, 20, '#334155')}{line(6, 28, 16)}{[34, 38, 42, 46, 50].map((y) => <g key={y}>{line(6, y, 48, '#cbd5e1', 2)}</g>)}{line(6, 66, 16, '#334155', 1)}</>}
      {id === 'table' && <>{line(6, 6, 34, '#334155', 4)}{line(6, 16, 48, '#93c5fd', 5)}{[24, 29, 34, 39, 44, 49, 54].map((y) => <g key={y}>{line(6, y, 48, '#e2e8f0', 3)}</g>)}</>}
      {id === 'form' && <>{line(6, 6, 30, '#334155', 4)}{[16, 22, 28, 34, 46, 52, 58, 64].map((y) => <g key={y}>{line(6, y, 14, '#cbd5e1')}{line(23, y, 31)}</g>)}{line(6, 41, 48, '#e2e8f0', 1)}</>}
      {id === 'certificate' && <><rect x="3" y="3" width="74" height="54" fill="none" stroke="#1e3a8a" strokeWidth="1.5" />{line(18, 11, 44, '#1e3a8a', 4)}{line(28, 21, 24, '#94a3b8', 2)}{line(20, 27, 40, '#334155', 5)}{line(22, 36, 36, '#cbd5e1', 2)}{line(10, 48, 18, '#334155', 1)}{line(52, 48, 18, '#334155', 1)}</>}
    </svg>
  );
}

export default function TemplateGallery({ report, design, onApply, onClose }) {
  const [chosen, setChosen] = useState('letter');
  const template = TEMPLATES.find((t) => t.id === chosen);
  const hasContent = Object.values(design.bands).some((band) => band?.elements?.length)
    || Object.values(design.bands.groupHeaders || {}).some((b) => b.elements.length);
  const missingFields = template.needsFields && !report.columns.length;

  const apply = (id = chosen) => {
    const picked = TEMPLATES.find((t) => t.id === id);
    if (picked.needsFields && !report.columns.length) return;
    if (hasContent && !window.confirm('Replace the current design with this template? (Ctrl+Z undoes it)')) return;
    onApply(applyTemplate(id, report.columns, report.title, design), picked.name);
  };
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
        {missingFields && <p className="err">The table template needs the fields: Setup → Read fields first.</p>}
        <div className="row">
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={missingFields} onClick={() => apply()}>Use “{template.name}”</button>
        </div>
      </div>
    </div>
  );
}
