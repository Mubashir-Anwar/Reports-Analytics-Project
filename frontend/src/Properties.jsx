import { useState } from 'react';
import {
  ELEMENT_LABELS, FONT_NAMES, MARKUPS, PAGE_LIMITS, PAGE_PRESETS, bandLabel, bandPresent, bandPurpose, contentBottom, contentRight, findElement, getBand, pagePreset,
  mapElements, resizeElement, setBand,
} from './designModel.js';
import { selectedIds } from './arrange.js';
import { CrosstabProps, DatasetProps, ListProps, NoteProps, SubreportProps, TableProps } from './ComponentProps.jsx';

/** Properties every element type has: with a multi-selection they are applied to all selected elements. */
const COMMON_KEYS = new Set(['x', 'y', 'height', 'color', 'backColor', 'borderColor', 'borderWidth', 'lineStyle', 'borders', 'printWhenExpression',
  'fontSize', 'fontName', 'bold', 'italic', 'underline', 'align', 'vAlign', 'padding', 'markup', 'stretch', 'locked']);

const clampPage = (value) => Math.round(Math.min(Math.max(Number(value) || PAGE_LIMITS.min, PAGE_LIMITS.min), PAGE_LIMITS.max));

function Row({ label, children }) {
  return <label className="prop-row"><span>{label}</span>{children}</label>;
}

function Num({ value, onChange, min, max, step = 1 }) {
  return <input type="number" value={Number.isFinite(value) ? value : ''} min={min} max={max} step={step} onChange={(e) => onChange(e.target.value === '' ? 0 : Number(e.target.value))} />;
}

function LineStyle({ value, onChange }) {
  return (
    <select value={value || 'solid'} onChange={(e) => onChange(e.target.value)}>
      <option value="solid">Solid ───</option><option value="dashed">Dashed - - -</option><option value="dotted">Dotted · · ·</option>
    </select>
  );
}

/** Common Jasper patterns (Text Field → Pattern). */
const PATTERNS = [
  ['#,##0', 'Number 1,234'], ['#,##0.00', 'Number 1,234.56'], ['0.00%', 'Percent 12.34%'], ['Rs. #,##0.00', 'Currency Rs. 1,234.56'],
  ['dd/MM/yyyy', 'Date 31/12/2026'], ['dd-MM-yyyy', 'Date 31-12-2026'], ['dd MMM yyyy', 'Date 31 Dec 2026'],
  ['dd/MM/yyyy HH:mm', 'Date and time'], ['HH:mm', 'Time 14:05'], ['hh:mm a', 'Time 02:05 PM'],
];

export default function Properties({ state, dispatch, notify, actions, onEditExpression, onOpenQuery }) {
  const { design, report, selection } = state;
  const [shiftColumns, setShiftColumns] = useState(true);
  const [elementTab, setElementTab] = useState('');
  const design$ = (update, key) => dispatch({ type: 'DESIGN', update, key });

  /* ---------- element (tabs like Jaspersoft Studio's Properties view) ---------- */
  if (selection.kind === 'element') {
    const found = findElement(design, selection.id);
    if (!found) return <div className="panel-body muted pad">Element not found.</div>;
    const { element, bandKey } = found;
    const multi = selectedIds(selection);
    // Like Jaspersoft Studio: with several elements selected a change goes to all of them
    // (type-specific properties only to the elements of the same type).
    const patch = (values, key) => design$((d) => {
      const common = Object.keys(values).every((k) => COMMON_KEYS.has(k));
      return mapElements(d, (el) => (multi.includes(el.id) && (common || el.type === element.type || el.id === element.id) ? { ...el, ...values } : el));
    }, key || `el-${multi.join()}-${Object.keys(values).join()}`);
    const remove = () => {
      design$((d) => { const band = getBand(d, bandKey); return setBand(d, bandKey, { ...band, elements: band.elements.filter((el) => el.id !== element.id) }); });
      dispatch({ type: 'SELECT', selection: { kind: 'band', key: bandKey } });
    };
    const text = ['staticText', 'textField'].includes(element.type);
    const hasFont = text || element.type === 'barcode';
    const boxed = !['line', 'break'].includes(element.type);
    const upload = (file) => {
      if (!file) return;
      if (file.size > 2_400_000) { notify('The image must be smaller than 2.4 MB', 'error'); return; }
      const reader = new FileReader();
      reader.onload = () => patch({ src: String(reader.result) });
      reader.readAsDataURL(file);
    };
    const fieldNames = [...report.columns, ...design.calculatedFields.map((c) => c.name)];
    const specificTab = { textField: 'Text Field', staticText: 'Static Text', image: 'Image', chart: 'Chart', barcode: 'Barcode', table: 'Table', crosstab: 'Crosstab', list: 'List', subreport: 'Subreport', note: 'Note' }[element.type];
    const datasetTab = ['table', 'crosstab', 'list'].includes(element.type);
    const tabs = [['appearance', 'Appearance'], ...(hasFont || ['table', 'crosstab'].includes(element.type) ? [['text', 'Text']] : []), ...(specificTab ? [['specific', specificTab]] : []), ...(datasetTab ? [['dataset', 'Dataset']] : []), ...(boxed && !['table', 'crosstab', 'subreport', 'note'].includes(element.type) ? [['borders', 'Borders']] : []), ['advanced', 'Advanced']];
    const tab = tabs.some(([id]) => id === elementTab) ? elementTab : (specificTab ? 'specific' : 'appearance');
    const borders = element.borders || { top: 0, right: 0, bottom: 0, left: 0 };
    const setSide = (side, value) => {
      const next = { ...borders, [side]: Math.max(0, value) };
      patch({ borders: Object.values(next).some(Boolean) ? next : null });
    };
    const expressionRow = (label, key, placeholder) => (
      <Row label={label}><span className="expr-row"><textarea rows={2} value={element[key] || ''} placeholder={placeholder} onChange={(e) => patch({ [key]: e.target.value })} />{key === 'expression' && element.type !== 'barcode' ? <button type="button" className="btn sm" title="Expression Editor: Fields, Parameters, Variables, functions (or double-click the element)" onClick={() => onEditExpression(element.id)}>…</button> : null}</span></Row>
    );

    return (
      <div className="panel-body props">
        <div className="prop-title">{ELEMENT_LABELS[element.type] || element.type} <small>in {bandLabel(bandKey)}</small>{element.locked && <small title="Locked"> 🔒</small>}</div>
        {multi.length > 1 && <p className="hint multi-hint"><b>{multi.length} elements selected.</b> Changes here go to all of them (type-specific ones to the {ELEMENT_LABELS[element.type] || element.type} elements). Use the toolbar above the canvas to align them.</p>}
        <div className="prop-tabs" role="tablist">
          {tabs.map(([id, label]) => <button key={id} role="tab" className={tab === id ? 'on' : ''} onClick={() => setElementTab(id)}>{label}</button>)}
        </div>

        {tab === 'appearance' && (
          <>
            <div className="prop-section">Location and size</div>
            <div className="prop-grid">
              <Row label="X"><Num value={element.x} onChange={(x) => patch({ x })} /></Row>
              <Row label="Y"><Num value={element.y} onChange={(y) => patch({ y })} /></Row>
              <Row label="Width"><Num value={element.width} min={1} onChange={(width) => design$((d) => resizeElement(d, bandKey, element.id, { width: Math.max(1, width) }, shiftColumns && !['frame', 'rectangle', 'ellipse', 'image'].includes(element.type)), `el-${element.id}-width`)} /></Row>
              <Row label="Height"><Num value={element.height} min={0} onChange={(height) => patch({ height })} /></Row>
            </div>
            {text && <label className="check-row" title="Like a table column: the same column in the header and the columns to the right move when the width changes. On the canvas, hold Alt while resizing to change only this element."><input type="checkbox" checked={shiftColumns} onChange={(e) => setShiftColumns(e.target.checked)} /> Width change moves the columns to the right</label>}
            <div className="prop-section">Colors</div>
            <div className="prop-grid">
              {(hasFont || element.type === 'chart') && <Row label={element.type === 'chart' ? 'Series color' : 'Forecolor'}><input type="color" value={element.type === 'chart' && element.color === '#000000' ? '#2563eb' : element.color} onChange={(e) => patch({ color: e.target.value })} /></Row>}
              {boxed && element.type !== 'image' && (
                <Row label="Backcolor"><span className="inline">
                  <input type="color" value={element.backColor || '#ffffff'} onChange={(e) => patch({ backColor: e.target.value })} />
                  <button className="btn sm" onClick={() => patch({ backColor: null })} title="Transparent">none</button></span></Row>
              )}
            </div>
            {element.type === 'line' && (
              <>
                <div className="prop-section">Line</div>
                <div className="prop-grid">
                  <Row label="Width"><Num value={element.borderWidth || 1} min={0.5} max={20} step={0.5} onChange={(borderWidth) => patch({ borderWidth })} /></Row>
                  <Row label="Color"><input type="color" value={element.borderColor || '#000000'} onChange={(e) => patch({ borderColor: e.target.value })} /></Row>
                </div>
                <Row label="Style"><LineStyle value={element.lineStyle} onChange={(lineStyle) => patch({ lineStyle })} /></Row>
              </>
            )}
            {element.type === 'break' && <p className="hint">Page Break: the part of the band below this line starts on a new page (e.g. put it at the bottom of the Detail band for one record per page).</p>}
            {element.type === 'frame' && <p className="hint">Elements placed inside the frame move with it on the canvas.</p>}
          </>
        )}

        {tab === 'text' && (hasFont || ['table', 'crosstab'].includes(element.type)) && (
          <>
            <Row label="Font name">
              <select value={element.fontName || 'SansSerif'} onChange={(e) => patch({ fontName: e.target.value })}>
                {FONT_NAMES.map((name) => <option key={name}>{name}</option>)}
              </select>
            </Row>
            <div className="prop-grid">
              <Row label="Font size"><Num value={element.fontSize} min={4} max={96} onChange={(fontSize) => patch({ fontSize })} /></Row>
              <Row label="Padding"><Num value={element.padding ?? 2} min={0} max={50} onChange={(padding) => patch({ padding })} /></Row>
            </div>
            <div className="prop-grid three">
              <Row label="Bold"><input type="checkbox" checked={element.bold} onChange={(e) => patch({ bold: e.target.checked })} /></Row>
              <Row label="Italic"><input type="checkbox" checked={element.italic} onChange={(e) => patch({ italic: e.target.checked })} /></Row>
              <Row label="Underline"><input type="checkbox" checked={Boolean(element.underline)} onChange={(e) => patch({ underline: e.target.checked })} /></Row>
            </div>
            <div className="prop-grid">
              <Row label="Horizontal align">
                <select value={element.align} onChange={(e) => patch({ align: e.target.value })}><option value="left">Left</option><option value="center">Center</option><option value="right">Right</option><option value="justified">Justified</option></select>
              </Row>
              <Row label="Vertical align"><select value={element.vAlign || 'top'} onChange={(e) => patch({ vAlign: e.target.value })}><option value="top">Top</option><option value="middle">Middle</option><option value="bottom">Bottom</option></select></Row>
            </div>
            {text && (
              <>
                <Row label="Markup">
                  <select value={element.markup || 'none'} onChange={(e) => patch({ markup: e.target.value })}>
                    {MARKUPS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
                  </select>
                </Row>
                {element.markup && element.markup !== 'none' && (
                  <p className="hint">Formatted text: <code>&lt;b&gt;</code> <code>&lt;i&gt;</code> <code>&lt;u&gt;</code> <code>&lt;br&gt;</code> <code>&lt;font color size face&gt;</code> <code>&lt;span style&gt;</code> <code>&lt;p&gt;</code> <code>&lt;ul&gt;&lt;li&gt;</code>{element.markup === 'styled' ? <> and Jasper <code>&lt;style forecolor isBold size&gt;</code></> : null}. Excel and CSV get the plain text.</p>
                )}
                <label className="check-row" title="Like Jasper's Stretch With Overflow: when the value is longer than the box, the box and its band grow so nothing is cut off. Boxes on the same line grow with it; boxes below move down.">
                  <input type="checkbox" checked={Boolean(element.stretch)} onChange={(e) => patch({ stretch: e.target.checked })} /> Stretch with overflow (long data is never cut)
                </label>
              </>
            )}
          </>
        )}

        {tab === 'specific' && element.type === 'staticText' && (
          <Row label="Text"><span className="expr-row"><textarea rows={4} value={element.text} onChange={(e) => patch({ text: e.target.value })} /><button type="button" className="btn sm" title="Open the editor (or double-click the element)" onClick={() => onEditExpression(element.id)}>…</button></span></Row>
        )}
        {tab === 'specific' && element.type === 'textField' && (
          <>
            {expressionRow('Expression', 'expression', '$F{Name}  or  =$F{A} * 2')}
            <Row label="Pattern">
              <span className="inline">
                <input list="pattern-choices" value={element.pattern} placeholder="#,##0.00  |  dd/MM/yyyy" onChange={(e) => patch({ pattern: e.target.value })} />
                <datalist id="pattern-choices">{PATTERNS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</datalist>
              </span>
            </Row>
            <Row label="Evaluation time">
              <select value={element.evaluationTime || 'Now'} onChange={(e) => patch({ evaluationTime: e.target.value })}>
                <option value="Now">Now (while filling)</option>
                <option value="Report">Report (final values)</option>
                <option value="Auto">Auto (fields now, variables final)</option>
              </select>
            </Row>
            <p className="hint">Report / Auto: <code>$V&#123;…&#125;</code> gets its value at the end of the report — use it for “Page X of Y”, grand totals in the title or a percentage of the total.</p>
            <Row label="Blank when null"><input type="checkbox" checked={element.blankWhenNull} onChange={(e) => patch({ blankWhenNull: e.target.checked })} /></Row>
          </>
        )}
        {tab === 'specific' && element.type === 'barcode' && (
          <>
            <Row label="Type">
              <select value={element.barcodeType || 'qr'} onChange={(e) => patch({ barcodeType: e.target.value })}>
                <option value="qr">QR Code</option><option value="code128">Code 128</option><option value="code39">Code 39</option><option value="ean13">EAN-13 (12–13 digits)</option>
              </select>
            </Row>
            {expressionRow('Value expression', 'expression', 'LOAN-$F{Identifier}')}
            {element.barcodeType && element.barcodeType !== 'qr' && <Row label="Show text under bars"><input type="checkbox" checked={element.showText !== false} onChange={(e) => patch({ showText: e.target.checked })} /></Row>}
            <p className="hint">The value can mix text and $F / $P / $V, e.g. <code>LOAN-$F&#123;Identifier&#125;</code>. Excel/CSV get the value as text.</p>
          </>
        )}
        {tab === 'specific' && element.type === 'image' && (
          <>
            <Row label="Image (PNG/JPEG)"><input type="file" accept="image/png,image/jpeg" onChange={(e) => upload(e.target.files[0])} /></Row>
            {element.src && <button className="btn sm" onClick={() => patch({ src: '' })}>Remove picture</button>}
            <p className="hint">Up to 2.4 MB per image, 8 MB per report. The picture keeps its proportions inside the box.</p>
          </>
        )}
        {tab === 'specific' && element.type === 'chart' && (
          <>
            <Row label="Type"><select value={element.chartType} onChange={(e) => patch({ chartType: e.target.value })}><option value="bar">bar</option><option value="line">line</option><option value="pie">pie</option><option value="spider">spider (radar, 3+ categories)</option></select></Row>
            <Row label="Category field"><select value={element.categoryField} onChange={(e) => patch({ categoryField: e.target.value })}><option value="">—</option>{fieldNames.map((f) => <option key={f}>{f}</option>)}</select></Row>
            <Row label="Value field"><select value={element.valueField} onChange={(e) => patch({ valueField: e.target.value })}><option value="">—</option>{fieldNames.map((f) => <option key={f}>{f}</option>)}</select></Row>
            <Row label="Aggregate"><select value={element.aggregate} onChange={(e) => patch({ aggregate: e.target.value })}><option>Sum</option><option>Count</option><option>Average</option></select></Row>
            <Row label="Title"><input value={element.title} onChange={(e) => patch({ title: e.target.value })} /></Row>
          </>
        )}

        {tab === 'specific' && element.type === 'table' && <TableProps element={element} patch={patch} fields={fieldNames} />}
        {tab === 'specific' && element.type === 'crosstab' && <CrosstabProps element={element} patch={patch} fields={fieldNames} />}
        {tab === 'specific' && element.type === 'list' && <ListProps />}
        {tab === 'specific' && element.type === 'subreport' && <SubreportProps element={element} patch={patch} report={report} design={design} />}
        {tab === 'specific' && element.type === 'note' && <NoteProps element={element} patch={patch} />}
        {tab === 'dataset' && datasetTab && <DatasetProps element={element} patch={patch} fields={fieldNames} bandKey={bandKey} />}

        {tab === 'borders' && boxed && (
          <>
            <div className="prop-section">All sides</div>
            <div className="prop-grid">
              <Row label="Width"><Num value={element.borderWidth} min={0} max={20} step={0.5} onChange={(borderWidth) => patch({ borderWidth })} /></Row>
              <Row label="Color"><input type="color" value={element.borderColor || '#000000'} onChange={(e) => patch({ borderColor: e.target.value })} /></Row>
            </div>
            <Row label="Style"><LineStyle value={element.lineStyle} onChange={(lineStyle) => patch({ lineStyle })} /></Row>
            {element.type !== 'ellipse' && (
              <>
                <div className="prop-section">Single sides <small>(e.g. only a bottom line under a field)</small></div>
                <div className="border-box">
                  <Num value={borders.top} min={0} max={20} step={0.5} onChange={(v) => setSide('top', v)} />
                  <div className="border-middle">
                    <Num value={borders.left} min={0} max={20} step={0.5} onChange={(v) => setSide('left', v)} />
                    <span className="border-sample" style={{ borderTop: `${borders.top}px solid`, borderRight: `${borders.right}px solid`, borderBottom: `${borders.bottom}px solid`, borderLeft: `${borders.left}px solid` }} />
                    <Num value={borders.right} min={0} max={20} step={0.5} onChange={(v) => setSide('right', v)} />
                  </div>
                  <Num value={borders.bottom} min={0} max={20} step={0.5} onChange={(v) => setSide('bottom', v)} />
                </div>
                {element.borders && <button className="btn sm" onClick={() => patch({ borders: null })}>Clear single sides</button>}
              </>
            )}
          </>
        )}

        {tab === 'advanced' && (
          <>
            {expressionRow('Print When expression', 'printWhenExpression', '$F{Amount} > 0')}
            <p className="hint">Like Jasper: the element is printed only when this is true, e.g. <code>$F&#123;Status&#125;.equals("Approved")</code> or <code>$V&#123;PAGE_NUMBER&#125; == 1</code>. Empty = always.</p>
            <div className="prop-section">Editing</div>
            <label className="check-row" title="A locked element cannot be moved or resized on the canvas (Ctrl+L). It still prints normally."><input type="checkbox" checked={Boolean(element.locked)} onChange={(e) => patch({ locked: e.target.checked })} /> Locked (cannot be moved or resized)</label>
            <div className="prop-section">Info</div>
            <p className="hint">ID <code>{element.id}</code> · band {bandLabel(bandKey)} · {Math.round(element.x)},{Math.round(element.y)} · {Math.round(element.width)}×{Math.round(element.height)}</p>
          </>
        )}
        <button className="btn danger" onClick={multi.length > 1 ? actions.removeSelected : remove}>{multi.length > 1 ? `Delete ${multi.length} elements` : 'Delete element'}</button>
      </div>
    );
  }

  /* ---------- band ---------- */
  if (selection.kind === 'band') {
    const band = getBand(design, selection.key);
    const setHeight = (height) => design$((d) => setBand(d, selection.key, { ...getBand(d, selection.key), height }), `band-${selection.key}`);
    if (!bandPresent(band)) {
      return (
        <div className="panel-body props">
          <div className="prop-title">{bandLabel(selection.key)} <small>band · not in the report</small></div>
          <p className="hint band-purpose">{bandPurpose(selection.key)}</p>
          <button className="btn primary" onClick={() => actions.createBandAt(selection.key)}>＋ Create Band</button>
          <p className="hint">Like Jaspersoft Studio: a deleted band is still listed in grey in the Outline. Create it again to put elements in it.</p>
        </div>
      );
    }
    return (
      <div className="panel-body props">
        <div className="prop-title">{bandLabel(selection.key)} <small>band</small></div>
        <p className="hint band-purpose">{bandPurpose(selection.key)}</p>
        <Row label="Height (px)"><Num value={band.height} min={0} max={1500} onChange={setHeight} /></Row>
        <div className="row-actions">
          <button className="btn sm" onClick={() => setHeight(contentBottom(band))} title="Make the band exactly as tall as its elements">Fit to content</button>
          <button className="btn sm danger-text" onClick={() => actions.deleteSelection({ kind: 'band', key: selection.key })} title="Remove the band and its elements from the report (Ctrl+Z to undo)">Delete Band</button>
        </div>
        <p className="hint">{band.elements.length} element(s). Drag the band's bottom line to change its height (double-click = fit). Elements can be dragged from one band to another. Right-click the band for more.</p>
      </div>
    );
  }

  /* ---------- group ---------- */
  if (selection.kind === 'group') {
    const group = design.groups.find((g) => g.name === selection.name);
    if (!group) return null;
    const rename = (name) => name && design$((d) => d); // names are fixed once created (bands are keyed by name)
    void rename;
    return (
      <div className="panel-body props">
        <div className="prop-title">Group · {group.name}</div>
        <Row label="Group by field"><select value={group.field} onChange={(e) => design$((d) => ({ ...d, groups: d.groups.map((g) => (g.name === group.name ? { ...g, field: e.target.value } : g)) }))}>{report.columns.map((c) => <option key={c}>{c}</option>)}{design.calculatedFields.map((c) => <option key={c.name}>{c.name}</option>)}</select></Row>
        <p className="hint">Rows are sorted and grouped by this field. The Group Header/Footer bands appear in the Design tab.</p>
        <button className="btn danger" onClick={() => {
          design$((d) => {
            const headers = { ...d.bands.groupHeaders }; const footers = { ...d.bands.groupFooters };
            delete headers[group.name]; delete footers[group.name];
            return { ...d, groups: d.groups.filter((g) => g.name !== group.name), variables: d.variables.filter((v) => v.resetGroup !== group.name), bands: { ...d.bands, groupHeaders: headers, groupFooters: footers } };
          });
          dispatch({ type: 'SELECT', selection: { kind: 'report' } });
        }}>Delete group</button>
      </div>
    );
  }

  /* ---------- variable ---------- */
  if (selection.kind === 'variable') {
    const variable = design.variables.find((v) => v.name === selection.name);
    if (!variable) return null;
    const patch = (values) => design$((d) => ({ ...d, variables: d.variables.map((v) => (v.name === variable.name ? { ...v, ...values } : v)) }), `var-${variable.name}`);
    return (
      <div className="panel-body props">
        <div className="prop-title">Variable · {variable.name}</div>
        <Row label="Calculation"><select value={variable.calculation} onChange={(e) => patch({ calculation: e.target.value })}>{['Sum', 'Count', 'Average', 'Min', 'Max', 'First'].map((c) => <option key={c}>{c}</option>)}</select></Row>
        <Row label="Field"><select value={variable.field} onChange={(e) => patch({ field: e.target.value })}><option value="">— (Count only)</option>{[...report.columns, ...design.calculatedFields.map((c) => c.name)].map((c) => <option key={c}>{c}</option>)}</select></Row>
        <Row label="Reset on"><select value={variable.resetType === 'Group' ? `G:${variable.resetGroup}` : 'Report'} onChange={(e) => patch(e.target.value === 'Report' ? { resetType: 'Report', resetGroup: '' } : { resetType: 'Group', resetGroup: e.target.value.slice(2) })}><option value="Report">Report</option>{design.groups.map((g) => <option key={g.name} value={`G:${g.name}`}>Group {g.name}</option>)}</select></Row>
        <p className="hint">Use <code>$V&#123;{variable.name}&#125;</code> in a Text Field. Show a group-reset variable in the Group Footer.</p>
        <button className="btn danger" onClick={() => { design$((d) => ({ ...d, variables: d.variables.filter((v) => v.name !== variable.name) })); dispatch({ type: 'SELECT', selection: { kind: 'report' } }); }}>Delete variable</button>
      </div>
    );
  }

  /* ---------- calculated field ---------- */
  if (selection.kind === 'calc') {
    const calc = design.calculatedFields.find((c) => c.name === selection.name);
    if (!calc) return null;
    return (
      <div className="panel-body props">
        <div className="prop-title">Calculated field · {calc.name}</div>
        <Row label="Expression"><textarea rows={3} value={calc.expression} placeholder="$F{Amount} * 0.17" onChange={(e) => design$((d) => ({ ...d, calculatedFields: d.calculatedFields.map((c) => (c.name === calc.name ? { ...c, expression: e.target.value } : c)) }), `calc-${calc.name}`)} /></Row>
        <p className="hint">Only numbers and $F tokens with + − × ÷ % are allowed. No code is executed.</p>
        <button className="btn danger" onClick={() => { design$((d) => ({ ...d, calculatedFields: d.calculatedFields.filter((c) => c.name !== calc.name) })); dispatch({ type: 'SELECT', selection: { kind: 'report' } }); }}>Delete</button>
      </div>
    );
  }

  /* ---------- parameter ---------- */
  if (selection.kind === 'param') {
    const param = report.parameters.find((p) => p.name === selection.name);
    if (!param) return null;
    const patch = (values) => dispatch({ type: 'REPORT', patch: { parameters: report.parameters.map((p) => (p.name === param.name ? { ...p, ...values } : p)) } });
    return (
      <div className="panel-body props">
        <div className="prop-title">Parameter · {param.name}</div>
        <Row label="Label"><input value={param.label || ''} onChange={(e) => patch({ label: e.target.value })} /></Row>
        <Row label="Type"><select value={param.type} onChange={(e) => patch({ type: e.target.value })}>{['date', 'string', 'integer', 'number', 'boolean'].map((t) => <option key={t}>{t}</option>)}</select></Row>
        <Row label="Ask the user (prompt)"><input type="checkbox" checked={param.prompt !== false} onChange={(e) => patch(e.target.checked ? { prompt: true, expression: undefined } : { prompt: false, required: false, expression: param.expression || '' })} /></Row>
        {param.prompt !== false && <Row label="Required"><input type="checkbox" checked={Boolean(param.required)} onChange={(e) => patch({ required: e.target.checked })} /></Row>}
        {param.prompt === false && (
          <>
            <Row label="Default value expression">
              <textarea
                rows={5}
                spellCheck={false}
                value={param.expression || ''}
                placeholder={'new java.util.GregorianCalendar(\n  $P{StartDate}.getYear() + 1900,\n  $P{StartDate}.getMonth(),\n  $P{StartDate}.getDate(), 0, 0, 0\n).getTime()'}
                onChange={(e) => patch({ expression: e.target.value })}
              />
            </Row>
            <p className="hint">Like Jasper's <code>isForPrompting="false"</code>: the user is not asked; the value is calculated from the parameters above it. Use <code>$P&#123;{param.name}&#125;</code> in the query. <code>getYear()</code>, <code>getMonth()</code>, <code>getDate()</code> and <code>GregorianCalendar</code> work. Times are in UTC.</p>
          </>
        )}
        <p className="hint">This name is the URL key. Use <code>$P&#123;{param.name}&#125;</code> in the query. For an optional multi-value filter (comma-separated values, empty = all) write <code>&#123;"$paramIn": "{param.name}"&#125;</code> in the query, for example <code>&#123;"BranchCode": &#123;"$paramIn": "{param.name}"&#125;&#125;</code>.</p>
        <button className="btn danger" onClick={() => { dispatch({ type: 'REPORT', patch: { parameters: report.parameters.filter((p) => p.name !== param.name) } }); dispatch({ type: 'SELECT', selection: { kind: 'report' } }); }}>Delete parameter</button>
      </div>
    );
  }

  /* ---------- report / page ---------- */
  const { page } = design;
  const setPage = (values) => design$((d) => ({ ...d, page: { ...d.page, ...values } }), 'page');
  const setMargin = (side, value) => design$((d) => ({ ...d, page: { ...d.page, margins: { ...d.page.margins, [side]: value } } }), `margin-${side}`);
  const reportTab = ['report', 'dataset', 'page', 'export'].includes(elementTab) ? elementTab : 'report';
  return (
    <div className="panel-body props">
      <div className="prop-title">Report: {report.title || 'Untitled'}</div>
      <div className="prop-tabs" role="tablist">
        {[['report', 'Report'], ['dataset', 'Dataset'], ['page', 'Page Format'], ['export', 'Excel / CSV']].map(([id, label]) => <button key={id} role="tab" className={reportTab === id ? 'on' : ''} onClick={() => setElementTab(id)}>{label}</button>)}
      </div>

      {reportTab === 'report' && (
        <>
          <Row label="Report name"><input value={report.title} onChange={(e) => dispatch({ type: 'REPORT', patch: { title: e.target.value } })} /></Row>
          <Row label="Report ID"><input value={report.id} disabled title={report.isNew ? 'Set it in Setup (made from the name)' : 'Fixed once saved: /generate/ID.pdf'} /></Row>
          <Row label="Description"><textarea rows={2} value={report.description} onChange={(e) => dispatch({ type: 'REPORT', patch: { description: e.target.value } })} /></Row>
          <Row label="Expression language"><input value="Java-like (safe subset)" disabled title="Expressions use $F, $P, $V and a safe Java subset; nothing is executed on the server as code" /></Row>
          <Row label="When no data">
            <select value={page.whenNoDataType || 'Auto'} onChange={(e) => setPage({ whenNoDataType: e.target.value })}>
              <option value="Auto">Auto (No Data band if there is one, else all sections)</option>
              <option value="NoDataSection">No Data Section</option>
              <option value="AllSectionsNoDetail">All Sections, No Detail</option>
              <option value="BlankPage">Blank Page</option>
              <option value="NoPages">No Pages</option>
            </select>
          </Row>
          <label className="check-row"><input type="checkbox" checked={Boolean(page.titleNewPage)} onChange={(e) => setPage({ titleNewPage: e.target.checked })} /> Title on a new page (alone, without page header/footer)</label>
          <label className="check-row"><input type="checkbox" checked={Boolean(page.summaryNewPage)} onChange={(e) => setPage({ summaryNewPage: e.target.checked })} /> Summary on a new page</label>
          <label className="check-row"><input type="checkbox" checked={Boolean(page.floatColumnFooter)} onChange={(e) => setPage({ floatColumnFooter: e.target.checked })} /> Float column footer (right under the last row)</label>
          <label className="check-row"><input type="checkbox" checked={Boolean(page.ignorePagination)} onChange={(e) => setPage({ ignorePagination: e.target.checked })} /> Ignore pagination (one long page)</label>
        </>
      )}

      {reportTab === 'dataset' && (
        <>
          <Row label="Data adapter"><input value={report.connectionId || '—'} disabled /></Row>
          <Row label="Collection"><input value={report.collection || '—'} disabled /></Row>
          <Row label="Fields"><input value={`${report.columns.length} field(s)`} disabled /></Row>
          <Row label="Parameters"><input value={`${report.parameters.length} parameter(s)`} disabled /></Row>
          <Row label="Filter expression"><input value={design.filterExpression || ''} disabled placeholder="none" /></Row>
          <Row label="Sort fields"><input value={(design.sort || []).map((s) => `${s.field} ${s.order === 'Descending' ? '↓' : '↑'}`).join(', ')} disabled placeholder="none" /></Row>
          <button className="btn sm primary" onClick={() => onOpenQuery?.('query')}>Edit query, filter and sort options…</button>
        </>
      )}

      {reportTab === 'page' && (
      <>
      <Row label="Page size">
        <select
          value={pagePreset(page)}
          onChange={(e) => {
            const preset = PAGE_PRESETS.find(([id]) => id === e.target.value);
            if (preset) setPage({ width: preset[2], height: preset[3] });
          }}
        >
          {PAGE_PRESETS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
          <option value="custom">Custom ({Math.round(page.width)}×{Math.round(page.height)})</option>
        </select>
      </Row>
      <div className="prop-grid">
        <Row label="Width (px)"><Num value={page.width} min={PAGE_LIMITS.min} max={PAGE_LIMITS.max} onChange={(width) => setPage({ width: clampPage(width) })} /></Row>
        <Row label="Height (px)"><Num value={page.height} min={PAGE_LIMITS.min} max={PAGE_LIMITS.max} onChange={(height) => setPage({ height: clampPage(height) })} /></Row>
      </div>
      <button
        className="btn sm"
        title="Make the page wide enough for every element"
        onClick={() => setPage({ width: clampPage(contentRight(design) + page.margins.left + page.margins.right) })}
      >Fit width to content</button>
      <p className="hint">You can also drag the page's right edge on the canvas to change its width.</p>
      <div className="prop-grid">
        {['top', 'right', 'bottom', 'left'].map((side) => <Row key={side} label={`Margin ${side}`}><Num value={page.margins[side]} min={0} max={300} onChange={(v) => setMargin(side, v)} /></Row>)}
      </div>
      </>
      )}

      {reportTab === 'export' && (
      <>
      <Row label="Excel / CSV layout">
        <select value={page.sheetLayout || 'table'} onChange={(e) => setPage({ sheetLayout: e.target.value })}>
          <option value="grid">Pixel-perfect like the PDF (Jasper style)</option>
          <option value="table">Data table (one header row, one row per record)</option>
        </select>
      </Row>
      <p className="hint">{page.sheetLayout === 'grid'
        ? 'Excel and CSV get every band in its place: headings, page header, images, lines, details and totals.'
        : 'Excel gets the Title and Page Header texts on top, then one header row and one row per record. CSV gets only the table.'}</p>
      </>
      )}
      <p className="hint">Select an element or band to see its properties here. Zoom: Ctrl +, Ctrl −, Ctrl 0, or Ctrl + mouse wheel.</p>
    </div>
  );
}
