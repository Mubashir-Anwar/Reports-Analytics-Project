// Design tab made easy (like Setup): start cards for an empty page, and a small "steps" strip with an example and a
// one-click button for each step. A step is ticked by itself when the design has it; the strip can be hidden.
import { useState } from 'react';
import { addElementToBand, bandKeys, bandPresent, bandWidth, defaultElement, getBand, placeField } from './designModel.js';
import { applyTemplate } from './templates.js';

const HIDE_KEY = 'design-guide-hidden';
const readHidden = () => { try { return localStorage.getItem(HIDE_KEY) === '1'; } catch { return false; } };
const writeHidden = (hidden) => { try { if (hidden) localStorage.setItem(HIDE_KEY, '1'); else localStorage.removeItem(HIDE_KEY); } catch { /* private mode */ } };

export const hasBands = (design) => bandKeys(design).some((key) => bandPresent(getBand(design, key)));
const allElements = (design) => bandKeys(design).flatMap((key) => getBand(design, key).elements);
const usesField = (design) => allElements(design).some((el) => /\$F\{/.test(`${el.expression || ''} ${el.text || ''}`) || ['table', 'crosstab', 'chart', 'list'].includes(el.type));
const noDataReport = (report) => !report.columns.length && !String(report.collection || '').trim() && !String(report.query || '').trim();

/* ---------- empty page: how do you want to start? ---------- */
export function DesignStart({ report, dispatch, onTemplates, onImportDesign, onSetup, notify }) {
  const fields = report.columns;
  const title = report.title && report.title !== 'New report' ? report.title : 'My report';
  const apply = (id, name) => {
    dispatch({ type: 'DESIGN', update: (d) => applyTemplate(id, fields, title, d) });
    dispatch({ type: 'SELECT', selection: { kind: 'report' } });
    notify?.(`“${name}” is on the page — click anything to change it (Ctrl+Z undoes it)`, 'ok');
  };
  const cards = [
    fields.length
      ? { icon: '▤', title: 'A list of records', text: `Headings and one row per record, from your ${fields.length} field${fields.length === 1 ? '' : 's'}.`, example: `e.g. ${fields.slice(0, 3).join(' · ')}${fields.length > 3 ? ' …' : ''}`, run: () => apply('table', 'Table report'), primary: true }
      : { icon: '▤', title: 'A list of records', text: 'First choose the data in Setup; then this makes the columns for you.', example: 'Opens Setup → choose a data adapter and a query', run: onSetup },
    { icon: '✉', title: 'A letter or form', text: 'Letterhead, date, address, body and signature — one per record.', example: 'e.g. offer letter, fee challan, profile sheet', run: () => apply('letter', 'Letter') },
    { icon: '⤓', title: 'Use my own design', text: 'Upload a PDF, Word or image of your page; it is rebuilt here and you mark the fields.', example: 'e.g. the bank’s statement format', run: onImportDesign },
    { icon: '▦', title: 'More layouts…', text: 'Form, certificate, landscape and other ready pages.', example: 'All layouts with a picture of each', run: onTemplates },
    { icon: '▭', title: 'Blank page', text: 'Title, Detail and Page Footer only. Design everything yourself.', example: 'Drag elements from the Palette', run: () => apply('blank', 'Blank A4') },
  ];
  return (
    <div className="design-start">
      <div className="design-start-inner">
        <p className="eyebrow">Design · step 1</p>
        <h2>How should the page look?</h2>
        <p className="muted">Pick a start. Everything can be changed afterwards on the page — nothing is fixed.</p>
        <div className="start-cards">
          {cards.map((c) => (
            <button key={c.title} type="button" className={`start-card ${c.primary ? 'primary' : ''}`} disabled={!c.run} onClick={() => c.run?.()}>
              <span className="start-icon" aria-hidden="true">{c.icon}</span>
              <b>{c.title}</b>
              <span>{c.text}</span>
              <small>{c.example}</small>
            </button>
          ))}
        </div>
        <details className="start-parts">
          <summary>What are Title, Detail, Page Footer…? (the parts of a page)</summary>
          <ul>
            <li><b>Title</b> — once, at the top of the first page. <i>e.g. “Monthly sales”, the logo</i></li>
            <li><b>Page Header</b> — top of every page. <i>e.g. company name, date</i></li>
            <li><b>Column Header</b> — headings above the rows. <i>e.g. Customer · City · Amount</i></li>
            <li><b>Detail</b> — repeated for every record. <i>e.g. $F{'{Customer}'} · $F{'{City}'} · $F{'{Amount}'}</i></li>
            <li><b>Page Footer</b> — bottom of every page. <i>e.g. Page 1 of 5</i></li>
            <li><b>Summary</b> — once, at the end. <i>e.g. grand total, a chart</i></li>
          </ul>
          <p className="muted">Want one part only? Right-click a part in the Outline → <b>Create Band</b>.</p>
        </details>
      </div>
    </div>
  );
}

/* ---------- the steps strip above the page ---------- */
export function DesignSteps({ report, design, dispatch, previewed, onPreview, onSetup, notify }) {
  const [hidden, setHidden] = useState(readHidden);
  const [open, setOpen] = useState(false);
  const fields = report.columns;
  const noData = noDataReport(report);
  const width = bandWidth(design);
  const top = ['title', 'pageHeader'].some((k) => getBand(design, k).elements.length);
  const placed = usesField(design);
  const footer = ['pageFooter', 'lastPageFooter'].some((k) => getBand(design, k).elements.length);
  const unplaced = fields.filter((f) => !allElements(design).some((el) => String(el.expression || '').includes(`$F{${f}}`)));

  const addTitle = () => {
    const text = report.title && report.title !== 'New report' ? report.title : 'Report title';
    dispatch({ type: 'DESIGN', update: (d) => addElementToBand(d, 'title', 'staticText', { text, x: 0, y: 4, width, height: 30, fontSize: 18, bold: true }).design });
    notify?.('Title added — double-click it to type your own text', 'ok');
  };
  const placeAll = () => {
    dispatch({ type: 'DESIGN', update: (d) => unplaced.reduce((acc, f) => placeField(acc, f).design, d) });
    notify?.(`${unplaced.length} field(s) placed: headings in Column Header, values in Detail`, 'ok');
  };
  const addPageNumber = () => {
    const el = { ...defaultElement('pageXofY'), x: Math.max(0, width - 140), y: 4, height: 14, fontSize: 8, color: '#64748b' };
    dispatch({ type: 'DESIGN', update: (d) => addElementToBand(d, 'pageFooter', 'textField', el).design });
    notify?.('Page number added at the bottom of every page', 'ok');
  };

  const steps = [
    { key: 'title', done: top, title: 'A title at the top', example: 'e.g. “Monthly sales — Karachi branch”', action: ['＋ Add a title', addTitle] },
    noData
      ? { key: 'fields', done: true, optional: true, title: 'Data fields (optional)', example: 'This report has no data: it prints once as fixed text. Choose data in Setup to fill it from records.', action: ['Setup →', onSetup] }
      : { key: 'fields', done: placed, title: 'Put the data on the page',
        example: fields.length ? `Drag a field from Outline → Fields into Detail; it prints as $F{${fields[0]}} for every record.` : 'Choose the data in Setup first; then its fields appear in Outline → Fields.',
        action: fields.length ? [unplaced.length === fields.length ? `＋ Place all ${fields.length} fields` : `＋ Place ${unplaced.length} more`, unplaced.length ? placeAll : null] : ['Setup →', onSetup] },
    { key: 'footer', done: footer, title: 'Page number at the bottom', example: 'e.g. “Page 2 of 7” in the Page Footer', action: ['＋ Add page number', addPageNumber] },
    { key: 'preview', done: previewed, title: 'Look at it with real data', example: 'Preview shows the real records; come back here to fix anything.', action: ['▶ Preview', onPreview] },
  ];
  const doneCount = steps.filter((s) => s.done).length;
  const next = steps.find((s) => !s.done);

  if (hidden) {
    return <div className="design-steps mini"><button type="button" className="link-btn" onClick={() => { writeHidden(false); setHidden(false); }}>? Design steps ({doneCount}/{steps.length})</button></div>;
  }
  return (
    <div className={`design-steps ${open ? 'open' : ''}`}>
      <div className="design-steps-head">
        <span className="steps-count" title={`${doneCount} of ${steps.length} done`}>{doneCount}/{steps.length}</span>
        <div className="steps-chips" role="list">
          {steps.map((s, i) => (
            <span key={s.key} role="listitem" className={`step-chip ${s.done ? 'done' : s === next ? 'now' : ''}`} title={s.example}>
              <span className="dot">{s.done ? '✓' : i + 1}</span>{s.title}
            </span>
          ))}
        </div>
        {next?.action?.[1] && <button type="button" className="btn sm primary" onClick={next.action[1]} title={next.example}>{next.action[0]}</button>}
        {!next && <span className="ok-text">✓ Ready — Publish it when you like</span>}
        <button type="button" className="btn sm quiet" onClick={() => setOpen((v) => !v)} aria-expanded={open}>{open ? 'Less' : 'Help'}</button>
        <button type="button" className="btn sm quiet" title="Hide the steps (they come back from “? Design steps”)" onClick={() => { writeHidden(true); setHidden(true); }}>✕</button>
      </div>
      {open && (
        <div className="design-steps-body">
          <ol>
            {steps.map((s) => (
              <li key={s.key} className={s.done ? 'done' : ''}>
                <b>{s.done ? '✓ ' : ''}{s.title}</b> <span className="muted">{s.example}</span>
                {!s.done && s.action?.[1] && <button type="button" className="btn sm" onClick={s.action[1]}>{s.action[0]}</button>}
              </li>
            ))}
          </ol>
          <p className="tips">
            <b>Tips:</b> double-click a text to type · drag from the <b>Palette</b> (right) to add a box, line, image or chart ·
            right-click anything for more · <b>Ctrl+Z</b> undoes · text with <code>$F{'{Name}'}</code> is filled from the data,
            <code>$P{'{FromDate}'}</code> from a parameter, and <code>=…</code> is a formula.
          </p>
        </div>
      )}
    </div>
  );
}
