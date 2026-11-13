// Table editor (Jaspersoft Studio opens a table in its own editor tab): the table's Column Header, Detail and
// Column Footer cells side by side. Click a cell to edit it, drag a column edge to resize, add / move / delete columns,
// and choose the dataset the table reads.
import { useRef, useState } from 'react';
import { columnFor, FOOTERS, tableHeight } from './ComponentProps.jsx';
import { componentFields } from './DatasetsEditor.jsx';
import { findElement, mapElements } from './designModel.js';

const SECTIONS = [['header', 'Column Header'], ['detail', 'Detail'], ['footer', 'Column Footer']];
const PATTERNS = ['', '#,##0', '#,##0.00', '0.00%', 'dd/MM/yyyy', 'dd/MM/yyyy HH:mm', 'HH:mm'];

export default function TableEditor({ state, dispatch, elementId, onClose, onOpenDatasets }) {
  const { design, report } = state;
  const found = findElement(design, elementId);
  const [selected, setSelected] = useState({ column: 0, section: 'detail' });
  const [scale, setScale] = useState(1.25);
  const drag = useRef(null);
  if (!found || found.element.type !== 'table') {
    return (
      <div className="modal-back"><div className="modal"><p>This table no longer exists.</p><button className="btn" onClick={onClose}>Close</button></div></div>
    );
  }
  const table = found.element;
  const columns = table.columns || [];
  const fields = componentFields(table, report, design);
  const datasets = design.datasets || [];
  const dataset = datasets.find((d) => d.name === table.datasetName);

  const patch = (values, key) => dispatch({
    type: 'DESIGN', key,
    update: (d) => mapElements(d, (el) => (el.id === elementId ? { ...el, ...values, height: tableHeight({ ...el, ...values }) } : el)),
  });
  const setColumns = (next, key) => patch({ columns: next, width: Math.max(10, next.reduce((s, c) => s + (Number(c.width) || 0), 0)) }, key);
  const change = (index, values, key) => setColumns(columns.map((c, i) => (i === index ? { ...c, ...values } : c)), key || `tcol-${index}-${Object.keys(values).join()}`);
  const index = Math.min(selected.column, columns.length - 1);
  const col = columns[index];

  const insert = (at, column) => {
    const next = [...columns];
    next.splice(at, 0, column);
    setColumns(next);
    setSelected({ column: at, section: 'detail' });
  };
  const remove = () => { if (!col) return; setColumns(columns.filter((_, i) => i !== index)); setSelected({ ...selected, column: Math.max(0, index - 1) }); };
  const move = (by) => {
    const target = index + by;
    if (!col || target < 0 || target >= columns.length) return;
    const next = [...columns];
    const [item] = next.splice(index, 1);
    next.splice(target, 0, item);
    setColumns(next);
    setSelected({ ...selected, column: target });
  };
  const missing = fields.filter((f) => !columns.some((c) => c.expression === `$F{${f}}`));
  /** The next field that is not in the table yet, else an empty column to fill in. */
  const newColumn = () => (missing[0] ? columnFor(missing[0], 90) : { ...columnFor('Column', 90), expression: '', footer: 'none', pattern: '' });
  const chooseDataset = (name) => {
    const next = datasets.find((d) => d.name === name);
    const mapping = (next?.parameters || []).map((p) => ({
      name: p.name,
      expression: report.columns.includes(p.name) ? `$F{${p.name}}` : '',
    }));
    // the columns read fields of the chosen dataset: start with one column per field when the names do not match
    const usable = columns.filter((c) => [...c.expression.matchAll(/\$F\{([^}]+)\}/g)].every((m) => (next ? next.fields : componentFields({}, report, design)).includes(m[1])));
    const nextColumns = usable.length ? usable : (next ? next.fields : report.columns).slice(0, 8).map((f) => columnFor(f, 90));
    patch({ datasetName: name, datasetParameters: mapping, scope: 'report', filterExpression: '', sortField: '',
      columns: nextColumns, width: Math.max(10, nextColumns.reduce((s, c) => s + c.width, 0)) });
    setSelected({ column: 0, section: 'detail' });
  };
  const setMapping = (name, expression) => {
    const rest = (table.datasetParameters || []).filter((m) => m.name !== name);
    patch({ datasetParameters: [...rest, { name, expression }] }, `tmap-${name}`);
  };

  /* column resize by dragging the right edge of a cell */
  const startResize = (i) => (e) => {
    e.preventDefault();
    e.stopPropagation();
    drag.current = { i, x: e.clientX, width: columns[i].width };
    const onMove = (ev) => {
      const width = Math.max(5, Math.round(drag.current.width + (ev.clientX - drag.current.x) / scale));
      change(drag.current.i, { width }, `tcol-resize-${drag.current.i}`);
    };
    const onUp = () => { window.removeEventListener('pointermove', onMove); window.removeEventListener('pointerup', onUp); drag.current = null; };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };
  const insertField = (field) => {
    if (!col) { insert(0, columnFor(field, 90)); return; }
    if (selected.section === 'detail') change(index, { expression: `${col.expression || ''}${col.expression ? ' + ' : ''}$F{${field}}` });
    else if (selected.section === 'header') change(index, { header: field });
  };

  const heights = { header: table.headerHeight || 0, detail: table.rowHeight || 16, footer: table.footerHeight || 0 };
  const hasFooter = columns.some((c) => c.footer && c.footer !== 'none');
  const cellText = (c, section) => {
    if (section === 'header') return c.header || '';
    if (section === 'detail') return c.expression || '';
    if (c.footer === 'text') return c.footerText || '';
    return c.footer && c.footer !== 'none' ? `${c.footer}(${c.expression.replace(/^\$F\{(.+)\}$/, '$1')})` : '';
  };
  const cellStyle = (c, section) => ({
    width: c.width * scale,
    minHeight: Math.max(heights[section], 12) * scale,
    textAlign: c.align || 'left',
    background: section === 'detail' ? undefined : table.headerBackColor || undefined,
    color: section === 'detail' ? undefined : table.headerBackColor ? table.headerColor || '#000000' : undefined,
    fontWeight: section === 'detail' ? 400 : 600,
    fontSize: Math.max(9, (table.fontSize || 9) * scale),
  });

  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal table-editor" role="dialog" aria-label="Table editor">
        <header className="te-head">
          <div>
            <h3>Table editor</h3>
            <small className="muted">{found.bandKey} band · {columns.length} columns · {Math.round(table.width)} px wide · reads {dataset ? <>dataset <b>{dataset.name}</b></> : 'the main report query'}</small>
          </div>
          <button className="icon" onClick={onClose} title="Close">✕</button>
        </header>

        <div className="te-toolbar">
          <label className="inline">Dataset
            <select value={table.datasetName || ''} onChange={(e) => chooseDataset(e.target.value)}>
              <option value="">Main report query</option>
              {datasets.map((d) => <option key={d.name} value={d.name}>{d.name} ({d.fields.length} fields)</option>)}
            </select>
          </label>
          <button className="btn sm" onClick={onOpenDatasets} title="Create or edit datasets">Datasets…</button>
          <span className="sep" />
          <button className="btn sm" onClick={() => insert(col ? index : 0, newColumn())}>＋ Left</button>
          <button className="btn sm" onClick={() => insert(col ? index + 1 : 0, newColumn())}>＋ Right</button>
          <button className="btn sm" disabled={!col || index === 0} onClick={() => move(-1)} title="Move left">◀</button>
          <button className="btn sm" disabled={!col || index === columns.length - 1} onClick={() => move(1)} title="Move right">▶</button>
          <button className="btn sm danger-text" disabled={!col} onClick={remove}>Delete column</button>
          <button className="btn sm" disabled={!missing.length} onClick={() => setColumns([...columns, ...missing.map((f) => columnFor(f, 90))])}>＋ All fields ({missing.length})</button>
          <button className="btn sm" disabled={!columns.length} onClick={() => { const w = Math.floor(table.width / columns.length); setColumns(columns.map((c) => ({ ...c, width: w }))); }}>Equal widths</button>
          <span className="grow" />
          <label className="inline">Zoom<input type="range" min="0.6" max="2" step="0.05" value={scale} onChange={(e) => setScale(Number(e.target.value))} /></label>
        </div>

        <div className="te-body">
          <div className="te-canvas">
            {!columns.length && <p className="muted pad">No columns yet. Click a field on the right, or “＋ All fields”.</p>}
            {SECTIONS.filter(([key]) => key !== 'footer' || hasFooter || table.footerHeight).map(([key, label]) => (
              <div className="te-section" key={key}>
                <div className="te-label">{label}</div>
                <div className="te-row" style={{ borderColor: table.gridColor || undefined }}>
                  {columns.map((c, i) => (
                    // eslint-disable-next-line react/no-array-index-key -- columns have no id; order is the identity
                    <div key={i} role="button" tabIndex={0}
                      className={`te-cell ${i === index && selected.section === key ? 'on' : ''} ${i === index ? 'col-on' : ''} ${key === 'detail' ? 'mono' : ''}`}
                      style={cellStyle(c, key)} onClick={() => setSelected({ column: i, section: key })}
                      onKeyDown={(e) => e.key === 'Enter' && setSelected({ column: i, section: key })}>
                      <span>{cellText(c, key) || <i className="muted">—</i>}</span>
                      <span className="te-resize" onPointerDown={startResize(i)} title="Drag to change the width" />
                    </div>
                  ))}
                </div>
              </div>
            ))}
            <p className="hint">The Detail row prints once per record of the {dataset ? `dataset ${dataset.name}` : 'report'}; long values wrap and the row grows. Click a cell to edit it. Drag a column's right edge to resize.</p>
          </div>

          <aside className="te-side">
            {col ? (
              <>
                <div className="prop-section">Column {index + 1} · {SECTIONS.find(([k]) => k === selected.section)[1]}</div>
                <div className="te-seg">
                  {SECTIONS.map(([k, l]) => <button key={k} className={selected.section === k ? 'on' : ''} onClick={() => setSelected({ ...selected, section: k })}>{l.replace('Column ', '')}</button>)}
                </div>
                {selected.section === 'header' && (
                  <label className="block">Header text<input value={col.header} onChange={(e) => change(index, { header: e.target.value })} /></label>
                )}
                {selected.section === 'detail' && (
                  <>
                    <label className="block">Expression<textarea className="mono" rows={3} value={col.expression} onChange={(e) => change(index, { expression: e.target.value })} placeholder="$F{Field}   or   =$F{Qty} * $F{Price}" /></label>
                    <label className="block">Pattern<select value={col.pattern || ''} onChange={(e) => change(index, { pattern: e.target.value })}>{PATTERNS.map((p) => <option key={p} value={p}>{p || 'none'}</option>)}</select></label>
                  </>
                )}
                {selected.section === 'footer' && (
                  <>
                    <label className="block">Footer<select value={col.footer || 'none'} onChange={(e) => change(index, { footer: e.target.value })}>{FOOTERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
                    {col.footer === 'text' && <label className="block">Text<input value={col.footerText || ''} onChange={(e) => change(index, { footerText: e.target.value })} placeholder="Total" /></label>}
                  </>
                )}
                <div className="form-grid two">
                  <label>Width<input type="number" min={5} value={col.width} onChange={(e) => change(index, { width: Math.max(5, Number(e.target.value) || 5) })} /></label>
                  <label>Align<select value={col.align} onChange={(e) => change(index, { align: e.target.value })}><option value="left">Left</option><option value="center">Center</option><option value="right">Right</option></select></label>
                </div>
              </>
            ) : <p className="muted">Add a column to start.</p>}

            <div className="prop-section">Fields {dataset ? `of ${dataset.name}` : ''}</div>
            <div className="chips">
              {fields.map((f) => <button key={f} className="chip-btn" onClick={() => insertField(f)} title={col ? 'Put this field in the selected cell' : 'Add a column'}>{f}</button>)}
              {!fields.length && <span className="muted">No fields: read them in Dataset and Query.</span>}
            </div>

            {dataset && dataset.parameters.length > 0 && (
              <>
                <div className="prop-section">Dataset parameters</div>
                {dataset.parameters.map((p) => (
                  <label className="block" key={p.name}>$P&#123;{p.name}&#125; <small>({p.type})</small>
                    <input className="mono" value={(table.datasetParameters || []).find((m) => m.name === p.name)?.expression || ''}
                      placeholder={report.parameters.some((rp) => rp.name === p.name) ? `empty = $P{${p.name}} of the report` : '$F{Id}'}
                      onChange={(e) => setMapping(p.name, e.target.value)} />
                  </label>
                ))}
              </>
            )}

            <div className="prop-section">Look</div>
            <div className="form-grid two">
              <label>Header height<input type="number" min={0} value={table.headerHeight} onChange={(e) => patch({ headerHeight: Math.max(0, Number(e.target.value) || 0) })} /></label>
              <label>Row height<input type="number" min={4} value={table.rowHeight} onChange={(e) => patch({ rowHeight: Math.max(4, Number(e.target.value) || 4) })} /></label>
              <label>Footer height<input type="number" min={0} value={table.footerHeight} onChange={(e) => patch({ footerHeight: Math.max(0, Number(e.target.value) || 0) })} /></label>
              <label>Header color<input type="color" value={table.headerBackColor || '#ffffff'} onChange={(e) => patch({ headerBackColor: e.target.value }, 'thead-color')} /></label>
            </div>
          </aside>
        </div>
        <footer className="row te-foot">
          <span className="muted">Changes apply straight away (Ctrl+Z undoes them).</span>
          <button className="btn primary" onClick={onClose}>Done</button>
        </footer>
      </div>
    </div>
  );
}
