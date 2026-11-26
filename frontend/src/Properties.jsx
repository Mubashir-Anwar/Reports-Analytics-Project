import { useState } from 'react';
import {
  ELEMENT_LABELS, MARKUPS, PAGE_LIMITS, PAGE_PRESETS, bandLabel, bandNumber, bandPresent, sectionBands, bandPurpose, contentBottom, contentRight, findElement, getBand, pagePreset,
  mapElements, resizeElement, resizeRule, safeMarkupHtml, setBand,
} from './designModel.js';
import { FontSelect } from './fonts.jsx';
import { selectedIds } from './arrange.js';
import { CrosstabProps, DatasetProps, ListProps, NoteProps, SubreportProps, TableProps } from './ComponentProps.jsx';
import { componentFields } from './DatasetsEditor.jsx';
import PatternDialog, { patternSample } from './PatternDialog.jsx';
import RichTextEditor from './RichTextEditor.jsx';
import { HAS_DATA, htmlToPlain, isJavaExpression, plainToHtml, tokenGroups } from './richText.js';

/** Properties every element type has: with a multi-selection they are applied to all selected elements. */
const COMMON_KEYS = new Set(['x', 'y', 'height', 'color', 'backColor', 'borderColor', 'borderWidth', 'lineStyle', 'borders', 'printWhenExpression',
  'fontSize', 'fontName', 'bold', 'italic', 'underline', 'align', 'vAlign', 'padding', 'markup', 'stretch', 'locked',
  'positionType', 'stretchType', 'transparent', 'printRepeatedValues', 'removeLineWhenBlank', 'printInFirstWholeBand',
  'printWhenDetailOverflows', 'printWhenGroupChanges', 'textAdjust', 'rotation', 'lineSpacing', 'lineSpacingSize',
  'firstLineIndent', 'leftIndent', 'rightIndent', 'spacingBefore', 'spacingAfter', 'tabStopWidth']);

/** Appearance: Position Type and Stretch Type ("" = how this designer always worked). */
const POSITION_TYPES = [['', 'Automatic (moves down below a growing text)'], ['Float', 'Float'], ['FixRelativeToTop', 'Fix Relative To Top'], ['FixRelativeToBottom', 'Fix Relative To Bottom']];
const STRETCH_TYPES = [['', 'Automatic (grows with a text on its line)'], ['NoStretch', 'No Stretch'], ['RelativeToTallestObject', 'Relative To Tallest Object'],
  ['RelativeToBandHeight', 'Relative To Band Height'], ['ContainerHeight', 'Container Height'], ['ContainerBottom', 'Container Bottom']];
const TEXT_ADJUSTS = [['CutText', 'Cut Text'], ['StretchHeight', 'Stretch Height'], ['ScaleFont', 'Scale Font']];
const LINE_SPACINGS = [['Single', 'Single'], ['1_1_2', '1.5 Lines'], ['Double', 'Double'], ['AtLeast', 'At Least'], ['Fixed', 'Fixed'], ['Proportional', 'Proportional']];
const ROTATIONS = [['None', 'Abc', 'None'], ['Left', '⟲', 'Left (reads upwards)'], ['Right', '⟳', 'Right (reads downwards)'], ['UpsideDown', '⇅', 'Upside down']];

/** A row of toggle buttons (text alignment, rotation). */
function Toggles({ value, options, onChange }) {
  return (
    <span className="toggles">
      {options.map(([id, label, title]) => <button type="button" key={id} title={title || label} className={`btn sm ${value === id ? 'on' : ''}`} onClick={() => onChange(id)}>{label}</button>)}
    </span>
  );
}

/** Text Field / Static Text: Text Adjust, alignment, rotation, font, markup and paragraph (like classic report designers). */
function TextFormat({ element, patch }) {
  const adjust = element.textAdjust || (element.stretch ? 'StretchHeight' : 'CutText');
  const spacing = element.lineSpacing || 'Single';
  return (
    <>
      <Row label="Text Adjust">
        <select value={adjust} onChange={(e) => patch({ textAdjust: e.target.value, stretch: e.target.value === 'StretchHeight' })}>
          {TEXT_ADJUSTS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
        </select>
      </Row>
      <p className="hint">{adjust === 'StretchHeight' ? 'Long text makes the box and its band taller, so nothing is cut off.' : adjust === 'ScaleFont' ? 'Long text gets a smaller font until it fits in the box.' : 'Text that does not fit in the box is cut off.'}</p>
      <div className="prop-grid">
        <Row label="Horizontal"><Toggles value={element.align} onChange={(align) => patch({ align })} options={[['left', '⇤', 'Left'], ['center', '↔', 'Center'], ['right', '⇥', 'Right'], ['justified', '☰', 'Justified']]} /></Row>
        <Row label="Vertical"><Toggles value={element.vAlign || 'top'} onChange={(vAlign) => patch({ vAlign })} options={[['top', '⤒', 'Top'], ['middle', '↕', 'Middle'], ['bottom', '⤓', 'Bottom']]} /></Row>
      </div>
      <Row label="Rotation"><Toggles value={element.rotation || 'None'} onChange={(rotation) => patch({ rotation })} options={ROTATIONS} /></Row>
      <div className="prop-section">Font</div>
      <div className="prop-grid">
        <Row label="Font name">
          <FontSelect value={element.fontName} onChange={(fontName) => patch({ fontName })} />
        </Row>
        <Row label="Size"><Num value={element.fontSize} min={4} max={96} onChange={(fontSize) => patch({ fontSize })} /></Row>
      </div>
      <span className="toggles font-toggles">
        <button type="button" className={`btn sm ${element.bold ? 'on' : ''}`} title="Bold" onClick={() => patch({ bold: !element.bold })}><b>B</b></button>
        <button type="button" className={`btn sm ${element.italic ? 'on' : ''}`} title="Italic" onClick={() => patch({ italic: !element.italic })}><i>I</i></button>
        <button type="button" className={`btn sm ${element.underline ? 'on' : ''}`} title="Underline" onClick={() => patch({ underline: !element.underline })}><u>U</u></button>
      </span>
      <Row label="Markup">
        <select value={element.markup || 'none'} onChange={(e) => patch({ markup: e.target.value })}>
          {MARKUPS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
        </select>
      </Row>
      {element.markup && element.markup !== 'none' && (
        <p className="hint">Formatted text: <code>&lt;b&gt;</code> <code>&lt;i&gt;</code> <code>&lt;u&gt;</code> <code>&lt;br&gt;</code> <code>&lt;font color size face&gt;</code> <code>&lt;span style&gt;</code> <code>&lt;p&gt;</code> <code>&lt;ul&gt;&lt;li&gt;</code>{element.markup === 'styled' ? <> and classic <code>&lt;style forecolor isBold size&gt;</code></> : null}. Excel and CSV get the plain text.</p>
      )}
      <div className="prop-section">Paragraph</div>
      <div className="prop-grid">
        <Row label="Line Spacing">
          <select value={spacing} onChange={(e) => patch({ lineSpacing: e.target.value })}>
            {LINE_SPACINGS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
          </select>
        </Row>
        <Row label={spacing === 'Proportional' ? 'Line Spacing Size (×)' : 'Line Spacing Size (px)'}>
          <Num value={element.lineSpacingSize ?? 1} min={0} max={500} step={spacing === 'Proportional' ? 0.1 : 1} onChange={(lineSpacingSize) => patch({ lineSpacingSize })} />
        </Row>
        <Row label="First Line Indent"><Num value={element.firstLineIndent ?? 0} min={-500} max={500} onChange={(firstLineIndent) => patch({ firstLineIndent })} /></Row>
        <Row label="Left Indent"><Num value={element.leftIndent ?? 0} min={0} max={500} onChange={(leftIndent) => patch({ leftIndent })} /></Row>
        <Row label="Right Indent"><Num value={element.rightIndent ?? 0} min={0} max={500} onChange={(rightIndent) => patch({ rightIndent })} /></Row>
        <Row label="Padding"><Num value={element.padding ?? 2} min={0} max={50} onChange={(padding) => patch({ padding })} /></Row>
        <Row label="Spacing Before"><Num value={element.spacingBefore ?? 0} min={0} max={500} onChange={(spacingBefore) => patch({ spacingBefore })} /></Row>
        <Row label="Spacing After"><Num value={element.spacingAfter ?? 0} min={0} max={500} onChange={(spacingAfter) => patch({ spacingAfter })} /></Row>
        <Row label="Tab Stop Width"><Num value={element.tabStopWidth ?? 40} min={1} max={500} onChange={(tabStopWidth) => patch({ tabStopWidth })} /></Row>
      </div>
      {['AtLeast', 'Fixed', 'Proportional'].includes(spacing) ? null : <p className="hint">Line Spacing Size is used by At Least, Fixed and Proportional.</p>}
    </>
  );
}

/** "Format words": the text becomes formatted text (markup HTML), edited with the B / I / U editor. */
function FormatWords({ onClick }) {
  return (
    <div className="format-words">
      <button type="button" className="btn sm" onClick={onClick} title="Bold, italic, underline, colour or highlight on some words, and fields between the words — all in this one box">
        <b>B</b> <i>I</i> <u>U</u> Format some words
      </button>
      <small className="muted">Instead of several boxes next to each other.</small>
    </div>
  );
}

/** The formatted text of a Static Text / Text Field, edited in place. */
function RichField({ label, value, groups, onChange, onEditor, onPlain }) {
  return (
    <div className="prop-rich">
      <div className="prop-rich-head">
        <span>{label}</span>
        <button type="button" className="link-btn" onClick={onEditor} title="The big editor, with every field, parameter, variable and function">Open big editor</button>
        <button type="button" className="link-btn" title="Back to plain text: the formatting of the words is removed"
          onClick={() => { if (window.confirm('Remove the bold, italic, colours and sizes of the words? The text stays.')) onPlain(); }}>Plain text</button>
      </div>
      <RichTextEditor value={value} groups={groups} onChange={onChange} label={label} />
      <p className="hint">Select words, then <b>B</b> <i>I</i> <u>U</u>, a colour or a size. <b>＋ Field</b> puts the data where the cursor is; <b>ƒx</b> a formula, e.g. <code>$F&#123;Fee&#125; * 1.17</code>. Values from the database are printed as text.</p>
    </div>
  );
}

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

/** Common classic patterns (Text Field → Pattern). */
const PATTERNS = [
  ['#,##0', 'Number 1,234'], ['#,##0.00', 'Number 1,234.56'], ['0.00%', 'Percent 12.34%'], ['Rs. #,##0.00', 'Currency Rs. 1,234.56'],
  ['dd/MM/yyyy', 'Date 31/12/2026'], ['dd-MM-yyyy', 'Date 31-12-2026'], ['dd MMM yyyy', 'Date 31 Dec 2026'],
  ['dd/MM/yyyy HH:mm', 'Date and time'], ['HH:mm', 'Time 14:05'], ['hh:mm a', 'Time 02:05 PM'],
];

/** Text Field → Expression: click an example to use it (built from this report's own fields and parameters). */
function ExpressionExamples({ fields, params, onPick }) {
  const f = fields[0] || 'Name';
  const g = fields.find((name) => /amount|total|price|qty|quantity|salary|balance|value/i.test(name)) || fields[1] || 'Amount';
  const p = params?.[0]?.name;
  const examples = [
    ...(fields.length ? [
      [`$F{${f}}`, 'the value of a field'],
      [`${f}: $F{${f}}`, 'text and a field together'],
      [`=$F{${g}} * 2`, 'a formula (starts with =)'],
    ] : []),
    ...(p ? [[`$P{${p}}`, 'a parameter the user enters']] : []),
    ['Page $V{PAGE_NUMBER} of $V{PAGE_COUNT}', 'page number'],
    ['$V{CURRENT_DATE}', 'today’s date'],
  ];
  return (
    <div className="expr-examples">
      <span>Examples — click one to use it:</span>
      {examples.map(([value, help]) => <button key={value} type="button" title={help} onClick={() => onPick(value)}><code>{value}</code></button>)}
    </div>
  );
}

export default function Properties({ state, dispatch, notify, actions, onEditExpression, onEditProperty = () => {}, onOpenQuery, onOpenTable, resizeMode = 'auto' }) {
  const { design, report, selection } = state;
  const [shiftColumns, setShiftColumns] = useState(null);   // null = follow View → Canvas → Resizing
  const [elementTab, setElementTab] = useState('');
  const [patternOpen, setPatternOpen] = useState(false);
  const design$ = (update, key) => dispatch({ type: 'DESIGN', update, key });

  /* ---------- element (tabs like a classic report designer's Properties view) ---------- */
  if (selection.kind === 'element') {
    const found = findElement(design, selection.id);
    if (!found) return <div className="panel-body muted pad">Element not found.</div>;
    const { element, bandKey } = found;
    const multi = selectedIds(selection);
    // Like classic report designers: with several elements selected a change goes to all of them
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
      if (!['image/png', 'image/jpeg'].includes(file.type)) { notify('Use a PNG or JPEG picture', 'error'); return; }
      const reader = new FileReader();
      // the file name is kept with the picture: the server's resource page lists it
      reader.onload = () => patch({ src: String(reader.result), srcName: file.name, srcUrl: '' });
      reader.readAsDataURL(file);
    };
    /** From URL: the browser downloads the picture once and stores it in the report (the URL is kept as its origin). */
    const fromUrl = async (address) => {
      const url = String(address || '').trim();
      if (!/^https?:\/\//i.test(url)) { notify('Give a web address that starts with http:// or https://', 'error'); return; }
      try {
        const response = await fetch(url, { mode: 'cors', credentials: 'omit' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const blob = await response.blob();
        if (!['image/png', 'image/jpeg'].includes(blob.type)) { notify(`That address gives ${blob.type || 'an unknown type'}, not a PNG or JPEG picture`, 'error'); return; }
        if (blob.size > 2_400_000) { notify('The image must be smaller than 2.4 MB', 'error'); return; }
        const name = decodeURIComponent(new URL(url).pathname.split('/').pop() || '') || 'image';
        const data = await new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(String(r.result)); r.onerror = reject; r.readAsDataURL(blob); });
        patch({ src: data, srcName: name, srcUrl: url });
        notify(`Picture ${name} stored in the report (from ${new URL(url).host})`, 'ok');
      } catch {
        notify('The browser could not download that picture (the site may not allow it). Save it on your computer and choose the file instead.', 'error');
      }
    };
    const fieldNames = [...report.columns, ...design.calculatedFields.map((c) => c.name)];
    const specificTab = { textField: 'Text Field', staticText: 'Static Text', image: 'Image', chart: 'Chart', barcode: 'Barcode', table: 'Table', crosstab: 'Crosstab', list: 'List', subreport: 'Subreport', note: 'Note' }[element.type];
    const datasetTab = ['table', 'crosstab', 'list'].includes(element.type);
    const tabs = [['appearance', 'Appearance'], ...(element.type === 'barcode' || ['table', 'crosstab'].includes(element.type) ? [['text', 'Text']] : []),...(specificTab ? [['specific', specificTab]] : []), ...(datasetTab ? [['dataset', 'Dataset']] : []), ...(boxed && !['table', 'crosstab', 'subreport', 'note'].includes(element.type) ? [['borders', 'Borders']] : []), ['advanced', 'Advanced']];
    const tab = tabs.some(([id]) => id === elementTab) ? elementTab : (specificTab ? 'specific' : 'appearance');
    const borders = element.borders || { top: 0, right: 0, bottom: 0, left: 0 };
    const setSide = (side, value) => {
      const next = { ...borders, [side]: Math.max(0, value) };
      patch({ borders: Object.values(next).some(Boolean) ? next : null });
    };
    const expressionRow = (label, key, placeholder) => (
      <Row label={label}><span className="expr-row"><textarea rows={2} value={element[key] || ''} placeholder={placeholder} onChange={(e) => patch({ [key]: e.target.value })}
        onDoubleClick={key === 'expression' && element.type !== 'barcode' ? () => onEditExpression(element.id) : key !== 'expression' ? () => onEditProperty({ id: element.id, key, label }) : undefined}
        title={element.type === 'barcode' && key === 'expression' ? undefined : 'Double-click to open the Expression Editor'} />
        {element.type === 'barcode' && key === 'expression' ? null : <button type="button" className="btn sm" title="Expression Editor: Fields, Parameters, Variables, functions"
          onClick={() => (key === 'expression' ? onEditExpression(element.id) : onEditProperty({ id: element.id, key, label }))}>…</button>}</span></Row>
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
            <div className="prop-section">Location</div>
            <div className="prop-grid">
              <Row label="X (px)"><Num value={element.x} onChange={(x) => patch({ x })} /></Row>
              <Row label="Y (px)"><Num value={element.y} onChange={(y) => patch({ y })} /></Row>
            </div>
            <Row label="Position Type">
              <select value={element.positionType || ''} onChange={(e) => patch({ positionType: e.target.value || undefined })}>
                {POSITION_TYPES.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
              </select>
            </Row>
            <div className="prop-section">Size</div>
            <div className="prop-grid">
              <Row label="W (px)"><Num value={element.width} min={1} onChange={(width) => design$((d) => {
                const backdrop = ['frame', 'rectangle', 'ellipse', 'image'].includes(element.type);
                const rule = resizeRule(d, bandKey, element, resizeMode);
                const follow = !backdrop && (shiftColumns ?? rule.follow);
                return resizeElement(d, bandKey, element.id, { width: Math.max(1, width) }, follow, follow && (shiftColumns ? resizeMode !== 'free' : rule.widen));
              }, `el-${element.id}-width`)} /></Row>
              <Row label="H (px)"><Num value={element.height} min={0} onChange={(height) => patch({ height })} /></Row>
            </div>
            {text && <label className="check-row" title="Like a table column: the same column in the header and the columns to the right move when the width changes. On the canvas, hold Alt while resizing to change only this element."><input type="checkbox" checked={shiftColumns ?? resizeRule(design, bandKey, element, resizeMode).follow} onChange={(e) => setShiftColumns(e.target.checked)} /> Width change moves the columns to the right</label>}
            <Row label="Stretch Type">
              <select value={element.stretchType || ''} onChange={(e) => patch({ stretchType: e.target.value || undefined })}>
                {STRETCH_TYPES.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
              </select>
            </Row>
            {(element.positionType || element.stretchType) && (
              <p className="hint">
                {{ Float: 'Float: moves down when an element above it grows.', FixRelativeToTop: 'Fix Relative To Top: stays where it is, even when an element above grows.', FixRelativeToBottom: 'Fix Relative To Bottom: keeps its distance from the bottom of the band.' }[element.positionType] || ''}
                {' '}
                {{ NoStretch: 'No Stretch: keeps its height.', RelativeToTallestObject: 'Relative To Tallest Object: as tall as the tallest growing element beside it.', RelativeToBandHeight: 'Relative To Band Height: grows as much as the band grows.', ContainerHeight: 'Container Height: grows as much as the band grows.', ContainerBottom: 'Container Bottom: reaches down to the bottom of the band.' }[element.stretchType] || ''}
              </p>
            )}
            <div className="prop-section">Color</div>
            <div className="prop-grid">
              {(hasFont || element.type === 'chart') && <Row label={element.type === 'chart' ? 'Series color' : 'Forecolor'}><input type="color" value={element.type === 'chart' && element.color === '#000000' ? '#2563eb' : element.color} onChange={(e) => patch({ color: e.target.value })} /></Row>}
              {boxed && element.type !== 'image' && (
                <Row label="Backcolor"><input type="color" value={element.backColor || '#ffffff'} onChange={(e) => patch({ backColor: e.target.value, transparent: false })} /></Row>
              )}
            </div>
            {boxed && element.type !== 'image' && (
              <label className="check-row" title="Mode = Transparent: the background colour is not painted">
                <input type="checkbox" checked={!element.backColor || Boolean(element.transparent)} onChange={(e) => patch(e.target.checked ? { transparent: true } : { transparent: false, backColor: element.backColor || '#ffffff' })} /> Transparent
              </label>
            )}
            {!['break', 'note'].includes(element.type) && (
              <>
                <div className="prop-section">Style and Print Details</div>
                <Row label="Label"><input value={element.label || ''} placeholder="A name for this element in the designer" onChange={(e) => patch({ label: e.target.value })} /></Row>
                <Row label="Key"><input value={element.key || ''} placeholder="Element key (kept in the .jrxml / .mrd)" onChange={(e) => patch({ key: e.target.value })} /></Row>
                <label className="check-row" title="Off: a value equal to the one printed just before is left out (e.g. a branch name only on its first row)">
                  <input type="checkbox" checked={element.printRepeatedValues !== false} onChange={(e) => patch({ printRepeatedValues: e.target.checked })} /> Print Repeated Values
                </label>
                <label className="check-row" title="When this element is not printed and nothing else is on its line, the line closes up and what is below moves up">
                  <input type="checkbox" checked={Boolean(element.removeLineWhenBlank)} onChange={(e) => patch({ removeLineWhenBlank: e.target.checked })} /> Remove Line When Blank
                </label>
                <label className="check-row" title="With Print Repeated Values off: print the value again on the first row of every new page">
                  <input type="checkbox" checked={Boolean(element.printInFirstWholeBand)} onChange={(e) => patch({ printInFirstWholeBand: e.target.checked })} /> Print In First Whole Band
                </label>
                <div className="prop-section">Print When</div>
                <label className="check-row" title="Classic reports print it again when a band continues on the next page. Bands here are never split over two pages, so this has no effect yet; it is kept for imported files.">
                  <input type="checkbox" checked={Boolean(element.printWhenDetailOverflows)} onChange={(e) => patch({ printWhenDetailOverflows: e.target.checked })} /> Detail Overflows
                </label>
                <Row label="Group Changes">
                  <select value={element.printWhenGroupChanges || ''} onChange={(e) => patch({ printWhenGroupChanges: e.target.value })} title="With Print Repeated Values off: print the value again when this group starts">
                    <option value="">—</option>
                    {design.groups.map((g) => <option key={g.name} value={g.name}>{g.name}</option>)}
                  </select>
                </Row>
                {expressionRow('Print When Expression', 'printWhenExpression', '$F{Amount} > 0')}
                <p className="hint">Printed only when this is true, e.g. <code>$F&#123;Status&#125;.equals("Approved")</code> or <code>$V&#123;PAGE_NUMBER&#125; == 1</code>. Empty = always.</p>
              </>
            )}
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
              <FontSelect value={element.fontName} onChange={(fontName) => patch({ fontName })} />
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
                  <p className="hint">Formatted text: <code>&lt;b&gt;</code> <code>&lt;i&gt;</code> <code>&lt;u&gt;</code> <code>&lt;br&gt;</code> <code>&lt;font color size face&gt;</code> <code>&lt;span style&gt;</code> <code>&lt;p&gt;</code> <code>&lt;ul&gt;&lt;li&gt;</code>{element.markup === 'styled' ? <> and classic <code>&lt;style forecolor isBold size&gt;</code></> : null}. Excel and CSV get the plain text.</p>
                )}
                <label className="check-row" title="Stretch With Overflow: when the value is longer than the box, the box and its band grow so nothing is cut off. Boxes on the same line grow with it; boxes below move down.">
                  <input type="checkbox" checked={Boolean(element.stretch)} onChange={(e) => patch({ stretch: e.target.checked })} /> Stretch with overflow (long data is never cut)
                </label>
              </>
            )}
          </>
        )}

        {tab === 'specific' && element.type === 'staticText' && (
          <>
            {element.markup === 'html' ? (
              <RichField label="Text" value={element.text} groups={tokenGroups(report, design)} onEditor={() => onEditExpression(element.id)}
                onPlain={() => patch({ markup: 'none', text: htmlToPlain(element.text) })}
                onChange={(html) => (HAS_DATA.test(html)
                  ? patch({ type: 'textField', expression: html, pattern: '', blankWhenNull: true, text: undefined }, `rich-${element.id}`)
                  : patch({ text: html }, `rich-${element.id}`))} />
            ) : (
              <>
                <Row label="Text"><span className="expr-row"><textarea rows={4} value={element.text} onChange={(e) => patch({ text: e.target.value })} /><button type="button" className="btn sm" title="Open the editor (or double-click the element)" onClick={() => onEditExpression(element.id)}>…</button></span></Row>
                <FormatWords onClick={() => patch({ markup: 'html', text: element.markup === 'styled' ? safeMarkupHtml(element.text, 'styled') : plainToHtml(element.text) })} />
              </>
            )}
            {actions?.makeField && <Row label=""><span className="row-buttons">
              <button type="button" className="btn sm" title="Becomes a Text Field with this text as its expression; then add $F{…}, $P{…} or a formula" onClick={() => actions.convertText([element.id], 'textField')}>ƒx Convert to Text Field</button>
              <button type="button" className="btn sm" title="This text is filled from the data instead: it becomes a Text Field of a field, same place and style" onClick={() => actions.makeField(element.id)}>$F Make it a field…</button>
            </span></Row>}
            <TextFormat element={element} patch={patch} />
          </>
        )}
        {tab === 'specific' && element.type === 'textField' && (
          <>
            {element.markup === 'html' && !isJavaExpression(element.expression) ? (
              <RichField label="Text with data" value={element.expression} groups={tokenGroups(report, design)} onEditor={() => onEditExpression(element.id)}
                onPlain={() => patch({ markup: 'none', expression: htmlToPlain(element.expression) })}
                onChange={(html) => patch({ expression: html }, `rich-${element.id}`)} />
            ) : (
              <>
                {expressionRow('Expression', 'expression', '$F{Name}  or  =$F{A} * 2')}
                {isJavaExpression(element.expression)
                  ? null
                  : <FormatWords onClick={() => patch({ markup: 'html', expression: element.markup === 'styled' ? safeMarkupHtml(element.expression, 'styled') : plainToHtml(element.expression) })} />}
              </>
            )}
            <ExpressionExamples fields={report.columns} params={report.parameters} onPick={(value) => patch({ expression: value })} />
            {actions?.convertText && <Row label=""><button type="button" className="btn sm" title="Back to fixed text: the expression is printed as written" onClick={() => actions.convertText([element.id], 'staticText')}>T Convert to Static Text</button></Row>}
            <div className="prop-grid">
              <Row label="Evaluation Time">
                <select value={element.evaluationTime || 'Now'} onChange={(e) => patch({ evaluationTime: e.target.value })} title="Report / Auto: $V{…} gets its value at the end of the report — for “Page X of Y”, grand totals in the title or a percentage of the total">
                  <option value="Now">Now</option>
                  <option value="Report">Report</option>
                  <option value="Auto">Auto</option>
                </select>
              </Row>
              <Row label="Blank When NULL"><input type="checkbox" checked={element.blankWhenNull} onChange={(e) => patch({ blankWhenNull: e.target.checked })} /></Row>
            </div>
            <Row label="Pattern">
              <span className="expr-row">
                <input list="pattern-choices" value={element.pattern || ''} placeholder="#,##0.00  |  dd/MM/yyyy" spellCheck={false} onChange={(e) => patch({ pattern: e.target.value })} />
                <datalist id="pattern-choices">{PATTERNS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</datalist>
                <button type="button" className="btn sm" title="Format pattern: ready ones, or your own" onClick={() => setPatternOpen(true)}>…</button>
              </span>
            </Row>
            {element.pattern && <p className="hint pattern-preview">Sample: <b>{patternSample(element.pattern)}</b></p>}
            <Row label="Pattern Expression">
              <textarea rows={2} spellCheck={false} value={element.patternExpression || ''} placeholder={'$F{Currency}.equals("USD") ? "$ #,##0.00" : "Rs #,##0.00"'} onChange={(e) => patch({ patternExpression: e.target.value })} />
            </Row>
            <p className="hint">Pattern Expression gives a pattern per record and wins over Pattern (when it gives one). Use $F, $P, $V and <code>? :</code>.</p>
            <TextFormat element={element} patch={patch} />
            {patternOpen && <PatternDialog value={element.pattern} onClose={() => setPatternOpen(false)} onDone={(pattern) => { patch({ pattern }); setPatternOpen(false); }} />}
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
            {expressionRow('Or from the data (an expression)', 'expression', '$F{Photo}  (base64 text of a PNG/JPEG)')}
            <Row label="Or from a web address">
              <input type="url" placeholder="https://…/logo.png" onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); fromUrl(e.currentTarget.value); } }}
                onBlur={(e) => { if (e.currentTarget.value.trim() && e.currentTarget.value.trim() !== element.srcUrl) fromUrl(e.currentTarget.value); }} />
            </Row>
            {!(element.expression && !element.src) && (element.src || element.srcName || element.reference) && (
              <p className="hint img-origin">
                {element.src ? <>Stored in the report: <b>{element.srcName || 'picture'}</b> ({Math.round((element.src.length * 3) / 4 / 1024)} KB)</> : <b className="danger-text">Missing picture{element.srcName ? `: ${element.srcName}` : ''} — choose the file above</b>}
                {element.srcUrl && <> · from <a href={element.srcUrl} target="_blank" rel="noopener noreferrer">{new URL(element.srcUrl).host}</a></>}
                {element.reference && <> · original expression <code>{element.reference}</code></>}
              </p>
            )}
            {element.src && <button className="btn sm" onClick={() => patch({ src: '', srcUrl: '' })}>Remove picture</button>}
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

        {tab === 'specific' && element.type === 'table' && <TableProps element={element} patch={patch} fields={componentFields(element, report, design)} onOpenEditor={() => onOpenTable?.(element.id)} />}
        {tab === 'specific' && element.type === 'crosstab' && <CrosstabProps element={element} patch={patch} fields={componentFields(element, report, design)} />}
        {tab === 'specific' && element.type === 'list' && <ListProps />}
        {tab === 'specific' && element.type === 'subreport' && <SubreportProps element={element} patch={patch} report={report} design={design} />}
        {tab === 'specific' && element.type === 'note' && <NoteProps element={element} patch={patch} />}
        {tab === 'dataset' && datasetTab && <DatasetProps element={element} patch={patch} fields={componentFields(element, report, design)} bandKey={bandKey} report={report} design={design} onOpenDatasets={() => onOpenQuery?.('datasets')} />}

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
            {['break', 'note'].includes(element.type) && expressionRow('Print When expression', 'printWhenExpression', '$F{Amount} > 0')}
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
          <p className="hint">Like classic report designers: a deleted band is still listed in grey in the Outline. Create it again to put elements in it.</p>
        </div>
      );
    }
    return (
      <div className="panel-body props">
        <div className="prop-title">{bandLabel(selection.key)} <small>band</small></div>
        <p className="hint band-purpose">{bandPurpose(selection.key)}</p>
        <Row label="Height (px)"><Num value={band.height} min={0} max={1500} onChange={setHeight} /></Row>
        <Row label="Print When Expression">
          <span className="expr-row">
            <textarea rows={2} value={band.printWhenExpression || ''} placeholder='$F{Amount} > 0   (empty = always)' title="Double-click to open the Expression Editor"
              onDoubleClick={() => onEditProperty({ bandKey: selection.key, key: 'printWhenExpression', label: 'Print When Expression' })}
              onChange={(e) => { const value = e.target.value; design$((d) => setBand(d, selection.key, { ...getBand(d, selection.key), printWhenExpression: value }), `band-when-${selection.key}`); }} />
            <button type="button" className="btn sm" title="Expression Editor: Fields, Parameters, Variables, functions"
              onClick={() => onEditProperty({ bandKey: selection.key, key: 'printWhenExpression', label: 'Print When Expression' })}>…</button>
          </span>
        </Row>
        <div className="row-actions">
          <button className="btn sm" onClick={() => actions.addBandAt(selection.key)} title="A second, third… band of this kind: it prints right after this one">＋ Add another</button>
          {sectionBands(design, selection.key).length > 1 && <>
            <button className="btn sm" disabled={bandNumber(selection.key) === 1} onClick={() => actions.moveBandBy(selection.key, -1)} title="Print this band earlier">↑ Up</button>
            <button className="btn sm" disabled={bandNumber(selection.key) === sectionBands(design, selection.key).length} onClick={() => actions.moveBandBy(selection.key, 1)} title="Print this band later">↓ Down</button>
          </>}
        </div>
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
            <p className="hint">Not asked (<code>isForPrompting="false"</code>): the user is not asked; the value is calculated from the parameters above it. Use <code>$P&#123;{param.name}&#125;</code> in the query. <code>getYear()</code>, <code>getMonth()</code>, <code>getDate()</code> and <code>GregorianCalendar</code> work. Times are in UTC.</p>
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
          {page.ignorePagination
            ? <p className="hint">All records on one tall page. Very long reports (over ~5 m) continue on a few more tall pages — a PDF page cannot be taller. Excel and CSV always give every record in one sheet.</p>
            : page.height < 400 && (
              <p className="hint warn">
                The page is only {Math.round(page.height)} pt tall, so each page holds just a few rows. Tick “Ignore pagination” for all records on one page, or use a normal page size:{' '}
                <button type="button" className="btn sm" onClick={() => setPage({ height: page.width > 842 ? 595 : 842 })}>{page.width > 842 ? 'Landscape height (595)' : 'A4 height (842)'}</button>
              </p>
            )}
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
          <option value="mixed">Headings pixel-perfect, data as a table (recommended)</option>
          <option value="grid">Everything pixel-perfect like the PDF (classic report style)</option>
          <option value="table">Data table only (headings as plain rows)</option>
        </select>
      </Row>
      <p className="hint">{page.sheetLayout === 'grid'
        ? 'Excel and CSV get every band in its place: headings, page header, images, lines, details and totals — exactly like the PDF.'
        : page.sheetLayout === 'mixed'
          ? 'Excel: the Title, Page Header and Column Header look exactly as designed (positions, merged cells, borders, colours, images — e.g. a report-number box on the right). Below them every record is one row in the Detail columns, with a filter on the headings and the top frozen, so the data can be sorted and filtered. CSV gets only the table.'
          : 'Excel gets the Title and Page Header texts on top (one per row), then one header row and one row per record. CSV gets only the table.'}</p>
      </>
      )}
      <p className="hint">Select an element or band to see its properties here. Zoom: Ctrl +, Ctrl −, Ctrl 0, or Ctrl + mouse wheel.</p>
    </div>
  );
}
