import { fieldRefs } from './fieldCheck.js';
import { Highlighted } from './ExprHighlight.jsx';
import { isJavaExpression, toEditorHtml } from './richText.js';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  cssFont, PAGE_LIMITS, ZOOM_LIMITS, bandKeys, bandKind, bandLabel, bandWidth, clampZoom, contentBottom, contentRight, defaultElement,
  BACKDROP_TYPES, CONTAINER_TYPES, bandPresent, describeElement, getBand, resizeElement, resizeRule, setBand,
  safeMarkupHtml,
} from './designModel.js';
import { isRightToLeft, useFonts } from './fonts.jsx';
import {
  guideTargets, nearestSnap, patchElements, selectedIds, selectionOf, settleBands, snapToGrid, toggleInSelection,
} from './arrange.js';

const MIN_W = 10;
const MIN_H = 1;
const MIN_BAND_VIEW = 14; // a 0px band is still shown as a thin strip so it can be selected and dragged open
const SNAP_PX = 5; // smart guides catch within this many screen pixels

const DASH = { solid: 'solid', dashed: 'dashed', dotted: 'dotted' };

/** A QR / barcode look-alike for the canvas (the real code is drawn in the PDF). */
function BarcodeView({ element }) {
  if ((element.barcodeType || 'qr') === 'qr') {
    return <span className="el-barcode qr" title={element.expression}><span>QR</span><small>{element.expression || '(value)'}</small></span>;
  }
  return <span className="el-barcode bars" title={element.expression}><span className="bars-art" /><small>{element.expression || '(value)'}</small></span>;
}

/** Design view of a Table: header, one detail row with the expressions, footer (the print has one row per record). */
function TableView({ element, zoom }) {
  const columns = element.columns || [];
  if (!columns.length) return <span className="el-hint">▤ Table — add columns in Properties</span>;
  const total = columns.reduce((sum, c) => sum + c.width, 0) || 1;
  const hasFooter = columns.some((c) => c.footer && c.footer !== 'none') && element.footerHeight > 0;
  const line = element.gridWidth ? `${Math.max(0.5, element.gridWidth * zoom)}px solid ${element.gridColor || '#94a3b8'}` : '1px dashed #cbd5e1';
  const row = (kind, height, cell) => (
    <div className={`tv-row tv-${kind}`} style={{ height: height * zoom }}>
      {columns.map((c, i) => (
        // eslint-disable-next-line react/no-array-index-key
        <span key={i} style={{ width: `${(c.width / total) * 100}%`, justifyContent: { left: 'flex-start', center: 'center', right: 'flex-end' }[c.align] || 'flex-start', borderRight: line, borderBottom: line, background: kind === 'detail' ? undefined : element.headerBackColor || undefined, color: kind === 'header' ? element.headerColor : undefined }}>{cell(c)}</span>
      ))}
    </div>
  );
  return (
    <div className="table-view" style={{ borderLeft: line, borderTop: line }}>
      {element.headerHeight > 0 && row('header', element.headerHeight, (c) => c.header)}
      {row('detail', element.rowHeight || 16, (c) => c.expression || '—')}
      {hasFooter && row('footer', element.footerHeight, (c) => (c.footer === 'text' ? c.footerText : c.footer !== 'none' ? `${c.footer}(…)` : ''))}
    </div>
  );
}

/** Design view of a Crosstab: corner, column group, row group, measure, totals. */
function CrosstabView({ element, zoom }) {
  const h = (element.rowHeight || 16) * zoom;
  const head = (element.headerHeight || element.rowHeight || 16) * zoom;
  const rhw = `${Math.min(45, (element.rowHeaderWidth / Math.max(element.width, 1)) * 100)}%`;
  const measure = element.calculation === 'Count' ? 'Count' : `${element.calculation}(${element.measureField || '?'})`;
  return (
    <div className="xtab-view">
      <div className="xtab-row" style={{ height: head }}>
        <span className="xh" style={{ width: rhw, background: element.headerBackColor || undefined }}>{element.cornerText || element.rowField || 'Rows'}</span>
        <span className="xh" style={{ background: element.headerBackColor || undefined }}>{element.columnField || 'Columns'} ▸</span>
        {element.showRowTotals !== false && <span className="xh xt" style={{ background: element.totalBackColor || undefined }}>Total</span>}
      </div>
      <div className="xtab-row" style={{ height: h }}>
        <span className="xh" style={{ width: rhw, background: element.headerBackColor || undefined }}>{element.rowField || 'Rows'} ▾</span>
        <span className="xc">{measure}</span>
        {element.showRowTotals !== false && <span className="xc xt" style={{ background: element.totalBackColor || undefined }}>Σ</span>}
      </div>
      {element.showColumnTotals !== false && (
        <div className="xtab-row" style={{ height: h }}>
          <span className="xh" style={{ width: rhw, background: element.totalBackColor || undefined }}>Total</span>
          <span className="xc xt" style={{ background: element.totalBackColor || undefined }}>Σ</span>
          {element.showRowTotals !== false && <span className="xc xt" style={{ background: element.totalBackColor || undefined }}>Σ</span>}
        </div>
      )}
    </div>
  );
}

/** Ribbon → Hide sets Print When Expression to `false`: the element stays in the design but is never printed. */
const isHidden = (element) => String(element.printWhenExpression || '').trim() === 'false';

/** Typing a Static Text right on the canvas: Enter keeps it, Shift+Enter is a new line, Esc cancels. */
function InlineText({ element, onCommit, onCancel, onMore }) {
  const [value, setValue] = useState(element.text || '');
  const done = useRef(false);
  const finish = (keep) => {
    if (done.current) return;
    done.current = true;
    if (keep && value !== (element.text || '')) onCommit(value); else onCancel();
  };
  return (
    <div className="inline-text" onPointerDown={(e) => e.stopPropagation()} onDoubleClick={(e) => e.stopPropagation()}>
      <textarea
        autoFocus
        value={value}
        onFocus={(e) => e.target.select()}
        onChange={(e) => setValue(e.target.value)}
        onBlur={() => finish(true)}
        onKeyDown={(e) => {
          e.stopPropagation();                         // Delete / arrows edit the text, not the design
          if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); finish(true); }
          if (e.key === 'Escape') { e.preventDefault(); finish(false); }
        }}
      />
      <div className="inline-text-help">
        Enter = OK · Shift+Enter = new line · Esc = cancel ·{' '}
        <button type="button" className="linkish" onMouseDown={(e) => { e.preventDefault(); done.current = true; onMore(); }}>show data ($F/$P/$V)…</button>
      </div>
    </div>
  );
}

/** Paragraph (line spacing, indents) and Rotation of a text element, as CSS for the canvas. */
function paragraphCss(element, zoom) {
  const size = element.fontSize * zoom;
  const amount = element.lineSpacingSize || 1;
  const lineHeight = {
    '1_1_2': `${size * 1.8}px`, Double: `${size * 2.4}px`, Proportional: `${size * 1.2 * amount}px`,
    Fixed: `${Math.max(amount, 1) * zoom}px`, AtLeast: `${Math.max(size * 1.2, amount * zoom)}px`,
  }[element.lineSpacing] || (isRightToLeft(element.fontName) ? 'normal' : undefined);
  return {
    lineHeight,
    ...(element.leftIndent ? { paddingLeft: element.leftIndent * zoom } : {}),
    ...(element.rightIndent ? { paddingRight: element.rightIndent * zoom } : {}),
    ...(element.firstLineIndent ? { textIndent: element.firstLineIndent * zoom } : {}),
  };
}

const BAND_ADD = { detail: 'Detail band', summary: 'Summary band', title: 'Title band', groupHeader: 'Group Header band', groupFooter: 'Group Footer band' };
const ROTATION_CSS = {
  Left: { writingMode: 'vertical-rl', transform: 'rotate(180deg)', height: '100%' },
  Right: { writingMode: 'vertical-rl', height: '100%' },
  UpsideDown: { transform: 'rotate(180deg)', width: '100%' },
};

function ElementView({ element, selected, primary, zoom, onPointerDown, onResizeDown, onContextMenu, onDoubleClick, known, editing, onCommitText, onCancelText, onMoreText }) {
  // fields this text field uses that the report does not have: shown red, explained in the tooltip
  const lost = known && element.type === 'textField' ? fieldRefs(element.expression).filter((f) => !known.has(f)) : [];
  const borderStyle = DASH[element.lineStyle] || 'solid';
  const sides = element.borders || null;
  const sideBorder = (side) => (sides && sides[side] ? `${sides[side]}px ${borderStyle} ${element.borderColor || '#000'}` : undefined);
  const style = {
    left: element.x * zoom,
    top: element.y * zoom,
    width: element.width * zoom,
    height: Math.max(element.height * zoom, 2),
    fontSize: element.fontSize * zoom,
    fontWeight: element.bold ? 700 : 400,
    fontStyle: element.italic ? 'italic' : 'normal',
    fontFamily: cssFont(element.fontName),
    unicodeBidi: 'plaintext',                      // Urdu / Arabic lines read right to left, as in the PDF
    textDecoration: element.underline ? 'underline' : undefined,
    textAlign: element.align === 'justified' ? 'justify' : element.align,
    ...(['staticText', 'textField'].includes(element.type) ? { display: 'flex', flexDirection: 'column', justifyContent: { top: 'flex-start', middle: 'center', bottom: 'flex-end' }[element.vAlign || 'top'] } : {}),
    color: element.color,
    background: (!element.transparent && element.backColor) || undefined,
    ...(['staticText', 'textField'].includes(element.type) ? paragraphCss(element, zoom) : {}),
    border: element.borderWidth && !['line', 'break'].includes(element.type) ? `${element.borderWidth * zoom}px ${borderStyle} ${element.borderColor || '#000'}` : undefined,
    ...(sides ? { borderTop: sideBorder('top'), borderRight: sideBorder('right'), borderBottom: sideBorder('bottom'), borderLeft: sideBorder('left') } : {}),
    ...(element.type === 'ellipse' ? { borderRadius: '50%' } : {}),
    ...(['staticText', 'textField'].includes(element.type) && element.padding !== undefined ? { padding: Math.max(0, element.padding - 2) * zoom } : {}),
  };
  let body = null;
  if (element.type === 'line') {
    body = <div className="el-line" style={{ borderTop: `${Math.max(element.borderWidth || 1, 1)}px ${borderStyle} ${element.borderColor || element.color}` }} />;
  } else if (element.type === 'break') {
    body = <span className="el-break-label">— Page Break —</span>;
  } else if (element.type === 'barcode') {
    body = <BarcodeView element={element} />;
  } else if (element.type === 'frame' || element.type === 'ellipse') {
    body = null;
  } else if (element.type === 'table') {
    body = <TableView element={element} zoom={zoom} />;
  } else if (element.type === 'crosstab') {
    body = <CrosstabView element={element} zoom={zoom} />;
  } else if (element.type === 'list') {
    body = <span className="el-tag">☰ List · repeated per record{element.filterExpression ? ' · filtered' : ''}</span>;
  } else if (element.type === 'subreport') {
    body = <span className="el-hint el-sub">⧉ Subreport<br /><b>{element.reportId || '(choose a report)'}</b>{(element.parameters || []).length ? <small>{element.parameters.map((p) => p.name).join(', ')}</small> : null}</span>;
  } else if (element.type === 'note') {
    body = <span className="el-text el-note-text">{element.text}</span>;
  } else if (element.type === 'image') {
    body = element.src ? <img src={element.src} alt="" draggable={false} /> : <span className="el-hint">{element.expression ? 'Image from data' : 'Image'}</span>;
  } else if (element.type === 'chart') {
    body = <span className="el-hint">▦ {element.chartType} chart{element.title ? ` · ${element.title}` : ''}</span>;
  } else if (element.type === 'staticText' && element.markup && element.markup !== 'none') {
    // eslint-disable-next-line react/no-danger -- sanitised by safeMarkupHtml (allow-list of formatting tags only)
    body = <span className="el-text el-markup" dangerouslySetInnerHTML={{ __html: safeMarkupHtml(element.text, element.markup) }} />;
  } else if (element.type === 'textField' && (element.markup === 'html' || element.markup === 'styled') && !isJavaExpression(element.expression)) {
    // formatted text with data: the words as they print, the data as chips (sanitised, see richText.js)
    // eslint-disable-next-line react/no-danger
    body = <span className={`el-text el-markup ${lost.length ? 'missing-field' : ''}`} dangerouslySetInnerHTML={{ __html: toEditorHtml(element.expression, element.markup) }} />;
  } else if (element.type !== 'rectangle') {
    body = (
      <span className={`el-text ${element.type === 'textField' ? 'is-field' : ''} ${lost.length ? 'missing-field' : ''}`}>
        {element.type === 'textField' && element.expression ? <Highlighted text={element.expression} known={known ? { F: known } : null} /> : describeElement(element)}
        {element.type === 'textField' && element.markup && element.markup !== 'none' && <small className="el-badge">{element.markup}</small>}
      </span>
    );
  }
  if (body && ROTATION_CSS[element.rotation] && ['staticText', 'textField'].includes(element.type)) {
    body = <span className="el-rotated" style={ROTATION_CSS[element.rotation]}>{body}</span>;
  }
  return (
    <div
      data-id={element.id}
      className={`el el-${element.type} ${lost.length ? 'has-missing-field' : ''} ${selected ? 'selected' : ''} ${selected && !primary ? 'secondary' : ''} ${element.locked ? 'locked' : ''} ${isHidden(element) ? 'el-hidden' : ''}`}
      style={style}
      onPointerDown={onPointerDown}
      onContextMenu={onContextMenu}
      onDoubleClick={onDoubleClick}
      title={`${element.label ? `${element.label} · ` : ''}${element.type} · ${Math.round(element.x)},${Math.round(element.y)} · ${Math.round(element.width)}×${Math.round(element.height)}${element.locked ? ' · locked' : ''}${isHidden(element) ? ' · hidden (not printed)' : ''}${element.stretch ? ' · stretches with long text' : ''}${lost.length ? `
⚠ Not in Setup → Fields: ${lost.join(', ')} — prints empty. Double-click to fix it (or add it to Fields).` : ''}`}
    >
      {editing ? <InlineText element={element} onCommit={onCommitText} onCancel={onCancelText} onMore={onMoreText} /> : body}
      {element.stretch && <span className="el-stretch" aria-hidden="true">↕</span>}
      {element.locked && selected && <span className="el-lock" title="Locked: unlock it to move or resize">🔒</span>}
      {primary && !element.locked && <span className="handle" onPointerDown={onResizeDown} />}
    </div>
  );
}

/** Tick step for the rulers so labels are at least ~50 screen px apart. */
function rulerStep(zoom) {
  return [5, 10, 20, 50, 100, 200, 500, 1000].find((step) => step * zoom >= 50) || 1000;
}

/** Ruler ticks as one SVG path; `from`/`to` in report px, origin 0 at the band's left/top edge. */
function RulerTicks({ from, to, zoom, vertical }) {
  const step = rulerStep(zoom);
  const minor = step / 5;
  const parts = [];
  const labels = [];
  for (let v = Math.ceil(from / minor) * minor; v <= to; v += minor) {
    const pos = (v - from) * zoom;
    const major = Math.abs(v % step) < 1e-6;
    const len = major ? 10 : (Math.abs(v % (step / 2)) < 1e-6 ? 6 : 3);
    parts.push(vertical ? `M18 ${pos.toFixed(1)}h-${len}` : `M${pos.toFixed(1)} 18v-${len}`);
    if (major) labels.push([pos, Math.round(v)]);
  }
  const size = (to - from) * zoom;
  return (
    <svg className="ruler-svg" width={vertical ? 18 : size} height={vertical ? size : 18} aria-hidden="true">
      <path d={parts.join('')} />
      {labels.map(([pos, value]) => (vertical
        ? <text key={value} x={2} y={pos + 3} transform={`rotate(-90 2 ${pos + 3})`} textAnchor="end">{value}</text>
        : <text key={value} x={pos + 2} y={8}>{value}</text>))}
    </svg>
  );
}

/** Move an element from one band to another, growing the target band if it does not fit. */
function transferElement(design, fromKey, toKey, elementId, y) {
  const source = getBand(design, fromKey);
  const element = source.elements.find((el) => el.id === elementId);
  if (!element) return design;
  const moved = { ...element, y: Math.max(0, Math.round(y)) };
  let next = setBand(design, fromKey, { ...source, elements: source.elements.filter((el) => el.id !== elementId) });
  const target = getBand(next, toKey);
  next = setBand(next, toKey, {
    ...target,
    height: Math.max(target.height, Math.ceil(moved.y + moved.height)),
    elements: [...target.elements, moved],
  });
  return next;
}

const ICON_BTN = (label, title, onClick, disabled, on) => (
  <button type="button" className={`tb-btn ${on ? 'on' : ''}`} title={title} disabled={disabled} onClick={onClick}>{label}</button>
);

/** Arrange / view toolbar above the canvas (a classic report designer's alignment toolbar + Photoshop-like view toggles). */
function CanvasToolbar({ count, lockedAll, actions, prefs, setPrefs, hasClipboard, onQuery, onTemplates }) {
  const set = (patch) => setPrefs((p) => ({ ...p, ...patch }));
  const none = count === 0;
  const few = count < 2;
  return (
    <div className="canvas-toolbar" role="toolbar" aria-label="Arrange">
      <span className="tb-group">
        {ICON_BTN(<span className="tb-text">🛢 Dataset &amp; Query</span>, 'Dataset and Query: data adapter, query, fields, parameters, filter and sort ', onQuery, !onQuery)}
        {ICON_BTN(<span className="tb-text">▦ Layouts</span>, 'Apply a layout template (blank page, letter, table, form, certificate)', onTemplates, !onTemplates)}
      </span>
      <span className="tb-group" title={count > 1 ? 'Align the selected elements to each other' : 'Align the selected element inside its band'}>
        {ICON_BTN(<span className="ic ic-al" />, 'Align left', () => actions.align('left'), none)}
        {ICON_BTN(<span className="ic ic-ac" />, 'Align horizontal centers', () => actions.align('hcenter'), none)}
        {ICON_BTN(<span className="ic ic-ar" />, 'Align right', () => actions.align('right'), none)}
        {ICON_BTN(<span className="ic ic-at" />, 'Align top', () => actions.align('top'), none)}
        {ICON_BTN(<span className="ic ic-am" />, 'Align vertical middles', () => actions.align('vmiddle'), none)}
        {ICON_BTN(<span className="ic ic-ab" />, 'Align bottom', () => actions.align('bottom'), none)}
      </span>
      <span className="tb-group">
        {ICON_BTN(<span className="ic ic-dh" />, 'Distribute horizontally (3 or more elements: equal gaps)', () => actions.distribute('h'), count < 3)}
        {ICON_BTN(<span className="ic ic-dv" />, 'Distribute vertically (3 or more elements: equal gaps)', () => actions.distribute('v'), count < 3)}
      </span>
      <span className="tb-group">
        {ICON_BTN('W', 'Same width as the primary (last clicked) element', () => actions.matchSize('width'), few)}
        {ICON_BTN('H', 'Same height as the primary element', () => actions.matchSize('height'), few)}
        {ICON_BTN('W·H', 'Same size as the primary element', () => actions.matchSize('both'), few)}
      </span>
      <span className="tb-group">
        {ICON_BTN('⬆', 'Bring to front', () => actions.order(true), none)}
        {ICON_BTN('⬇', 'Send to back', () => actions.order(false), none)}
        {ICON_BTN(lockedAll ? '🔒' : '🔓', lockedAll ? 'Unlock (Ctrl+L)' : 'Lock: cannot be moved or resized (Ctrl+L)', () => actions.toggleLock(), none, lockedAll)}
      </span>
      <span className="tb-group">
        {ICON_BTN('⧉', 'Copy (Ctrl+C)', () => actions.copy(), none)}
        {ICON_BTN('✂', 'Cut (Ctrl+X)', () => actions.cut(), none)}
        {ICON_BTN('📋', 'Paste (Ctrl+V)', () => actions.paste(), !hasClipboard)}
      </span>
      <span className="tb-spacer" />
      <span className="tb-group">
        {ICON_BTN('#', 'Show grid', () => set({ showGrid: !prefs.showGrid }), false, prefs.showGrid)}
        {ICON_BTN('⊹', 'Snap to grid (hold Alt while dragging to move freely)', () => set({ snapGrid: !prefs.snapGrid }), false, prefs.snapGrid)}
        <select className="tb-select" title="Grid size (px)" value={prefs.gridSize} onChange={(e) => set({ gridSize: Number(e.target.value) })}>
          {[2, 5, 8, 10, 20, 25].map((n) => <option key={n} value={n}>{n}px</option>)}
        </select>
        {ICON_BTN('⫶', 'Smart guides: snap to the edges and centers of other elements and to ruler guides', () => set({ smartGuides: !prefs.smartGuides }), false, prefs.smartGuides)}
        {ICON_BTN('📏', 'Rulers (drag from the top ruler to add a guide; drag a guide back onto it to remove)', () => set({ showRulers: !prefs.showRulers }), false, prefs.showRulers)}
      </span>
      {count > 1 && <span className="tb-count">{count} selected</span>}
    </div>
  );
}

export default function Canvas({ knownFields: knownList, design, selection, tool, zoom, setZoom, dispatch, actions, openMenu, onEditExpression, onPercentage, onWizard, onOpenTable, prefs, setPrefs, hasClipboard, onQuery, onTemplates, showToolbar = true }) {
  const known = useMemo(() => (knownList ? new Set(knownList) : null), [knownList]);
  const bandRefs = useRef({});
  const scrollRef = useRef(null);
  const pageRef = useRef(null);
  const suppressClick = useRef(false);
  const [dragInfo, setDragInfo] = useState(null); // { kind: 'band'|'page'|'element'|'resize'|'guide', text, target }
  const [snapLines, setSnapLines] = useState(null); // { x: [px], y: [{ key, y }] }
  const [marquee, setMarquee] = useState(null); // client-space rectangle
  const [guideDrag, setGuideDrag] = useState(null); // { index, x, remove }
  const [editingText, setEditingText] = useState(null); // id of the Static Text being typed on the canvas

  // Ctrl + mouse wheel zooms the canvas (non-passive listener so the browser page zoom is not triggered).
  useEffect(() => {
    const node = scrollRef.current;
    if (!node || !setZoom) return undefined;
    const onWheel = (event) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      setZoom((z) => clampZoom(z + (event.deltaY < 0 ? ZOOM_LIMITS.step : -ZOOM_LIMITS.step)));
    };
    node.addEventListener('wheel', onWheel, { passive: false });
    return () => node.removeEventListener('wheel', onWheel);
  }, [setZoom]);

  const width = bandWidth(design);
  const { margins } = design.page;
  const ids = selectedIds(selection);
  const idSet = new Set(ids);
  const primaryId = selection.kind === 'element' ? selection.id : null;
  const guides = design.page.guides || [];

  const select = (next) => dispatch({ type: 'SELECT', selection: next });

  const addElement = (bandKey, element) => {
    dispatch({
      type: 'DESIGN',
      update: (d) => {
        const band = getBand(d, bandKey);
        const height = Math.max(band.height, 30, Math.ceil(element.y + element.height));
        const elements = BACKDROP_TYPES.has(element.type) ? [element, ...band.elements] : [...band.elements, element];
        return setBand(d, bandKey, { ...band, height, elements });
      },
    });
    select({ kind: 'element', id: element.id });
    dispatch({ type: 'TOOL', tool: null });
  };

  /** Put a new Palette element in a band: Break spans the band; Percentage first asks for its field. */
  const placeNew = (bandKey, element) => {
    if (element.type === 'percentage') {
      dispatch({ type: 'TOOL', tool: null });
      onPercentage(bandKey, element);
      return;
    }
    if (element.type === 'break') { element.x = 0; element.width = width; }
    if (['table', 'crosstab', 'subreport'].includes(element.type) && onWizard) {
      dispatch({ type: 'TOOL', tool: null });
      onWizard(bandKey, { ...element, x: Math.min(element.x, Math.max(0, width - 60)) });
      return;
    }
    if (prefs.snapGrid && element.type !== 'break') {
      element.x = snapToGrid(element.x, prefs.gridSize);
      element.y = snapToGrid(element.y, prefs.gridSize);
    }
    element.x = Math.min(element.x, Math.max(0, width - element.width));
    addElement(bandKey, element);
  };

  const localPoint = (bandKey, event) => {
    const rect = bandRefs.current[bandKey].getBoundingClientRect();
    return { x: Math.max(0, (event.clientX - rect.left) / zoom), y: Math.max(0, (event.clientY - rect.top) / zoom) };
  };

  /** Band whose on-screen area contains the pointer's vertical position. */
  const bandAt = (clientY) => {
    for (const key of bandKeys(design)) {
      const node = bandRefs.current[key];
      if (!node) continue;
      const rect = node.getBoundingClientRect();
      if (clientY >= rect.top && clientY < rect.bottom) return { key, rect };
    }
    return null;
  };

  const onBandClick = (bandKey, event) => {
    if (suppressClick.current) return;
    // With a Palette tool armed a click anywhere in the band (also on an image or another element) places it there.
    if (tool && event.target.closest?.('.handle, .band-resize')) return;
    if (!tool && event.target !== event.currentTarget) return;
    if (tool) {
      const point = localPoint(bandKey, event);
      placeNew(bandKey, defaultElement(tool, Math.round(point.x), Math.round(point.y)));
    } else {
      select({ kind: 'band', key: bandKey });
    }
  };

  const onDrop = (bandKey, event) => {
    event.preventDefault();
    const point = localPoint(bandKey, event);
    const palette = event.dataTransfer.getData('application/x-palette');
    const field = event.dataTransfer.getData('application/x-field');
    let element = null;
    if (palette) element = defaultElement(palette, Math.round(point.x), Math.round(point.y));
    else if (field) {
      element = { ...defaultElement('textField', Math.round(point.x), Math.round(point.y)), expression: `$F{${field}}`, width: 110, height: 16 };
    }
    if (element) placeNew(bandKey, element);
  };

  const contextMenu = (items, selectFirst) => (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (selectFirst) select(selectFirst);
    openMenu(event, items());
  };

  /** Right-click on an element keeps a multi-selection that contains it. */
  const elementContextMenu = (id) => (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (!idSet.has(id)) select({ kind: 'element', id });
    else if (primaryId !== id) select(selectionOf(ids, id));
    openMenu(event, actions.elementMenu(id));
  };

  /* ---------- moving / resizing elements ---------- */
  const beginDrag = (bandKey, element, mode) => (event) => {
    if (event.button !== 0) return; // right button opens the menu instead of dragging
    if (tool && mode === 'move') return; // placing a new element: the click goes to the band
    event.stopPropagation();
    event.preventDefault();
    scrollRef.current?.focus({ preventScroll: true });

    // Shift / Ctrl + click adds or removes the element from the selection (no drag).
    if (mode === 'move' && (event.shiftKey || event.ctrlKey || event.metaKey)) {
      select(toggleInSelection(selection, element.id));
      return;
    }
    // Dragging (moving or resizing) an element of a multi-selection works on the whole selection.
    const groupIds = idSet.has(element.id) ? ids : [element.id];
    select(groupIds.length > 1 ? selectionOf(groupIds, element.id) : { kind: 'element', id: element.id });
    if (element.locked) return;
    // Resizing one of several selected elements: all of them grow or shrink by the same amount.
    const resizeGroup = mode !== 'move' && groupIds.length > 1 && element.type !== 'table';
    const groupSizes = resizeGroup
      ? Object.fromEntries(bandKeys(design).flatMap((key) => getBand(design, key).elements)
        .filter((el) => groupIds.includes(el.id) && !el.locked).map((el) => [el.id, { w: el.width, h: el.height, type: el.type }]))
      : null;

    dispatch({ type: 'SNAPSHOT' });
    const start = { x: event.clientX, y: event.clientY, ex: element.x, ey: element.y, ew: element.width, eh: element.height };
    const grabOffsetY = (event.clientY - bandRefs.current[bandKey].getBoundingClientRect().top) / zoom - element.y;
    const base = design; // resize and moves are always computed from the design as it was when the drag started

    // What moves: the selected unlocked elements, plus the elements that lie inside a moving Frame.
    const moving = new Map(); // id -> { bandKey, x, y, w, h }
    if (mode === 'move') {
      for (const key of bandKeys(base)) {
        for (const el of getBand(base, key).elements) {
          if (groupIds.includes(el.id) && !el.locked) moving.set(el.id, { bandKey: key, x: el.x, y: el.y, w: el.width, h: el.height });
        }
      }
      for (const [id, item] of [...moving]) {
        const frame = getBand(base, item.bandKey).elements.find((el) => el.id === id);
        if (!CONTAINER_TYPES.has(frame?.type)) continue;
        for (const el of getBand(base, item.bandKey).elements) {
          if (el.id === id || el.locked || moving.has(el.id)) continue;
          if (el.x >= frame.x - 0.5 && el.y >= frame.y - 0.5 && el.x + el.width <= frame.x + frame.width + 0.5 && el.y + el.height <= frame.y + frame.height + 0.5) {
            moving.set(el.id, { bandKey: item.bandKey, x: el.x, y: el.y, w: el.width, h: el.height });
          }
        }
      }
    }
    const items = [...moving.entries()];
    const oneBand = items.every(([, item]) => item.bandKey === bandKey);
    // the group's box, for clamping and smart guides
    const box = items.length
      ? { left: Math.min(...items.map(([, i]) => i.x)), right: Math.max(...items.map(([, i]) => i.x + i.w)), top: Math.min(...items.map(([, i]) => i.y)), bottom: Math.max(...items.map(([, i]) => i.y + i.h)) }
      : null;
    const targets = prefs.smartGuides ? guideTargets(base, bandKey, mode === 'move' ? [...moving.keys()] : [element.id]) : { xs: [], ys: [] };
    const threshold = SNAP_PX / zoom;

    const move = (e) => {
      let dx = (e.clientX - start.x) / zoom;
      let dy = (e.clientY - start.y) / zoom;
      const free = e.altKey && mode === 'move'; // Alt: no snapping while moving
      const lines = { x: [], y: [] };
      if (mode === 'move') {
        // 1) grid snap of the dragged element's top-left corner
        if (prefs.snapGrid && !free) {
          dx = snapToGrid(start.ex + dx, prefs.gridSize) - start.ex;
          dy = snapToGrid(start.ey + dy, prefs.gridSize) - start.ey;
        }
        // 2) smart guides win when an edge or center is close to one
        if (prefs.smartGuides && !free && box) {
          const sx = nearestSnap(box.left + (e.clientX - start.x) / zoom, box.right - box.left, targets.xs, threshold);
          if (sx) { dx = (e.clientX - start.x) / zoom + sx.delta; lines.x.push(sx.line); }
          if (oneBand) {
            const sy = nearestSnap(box.top + (e.clientY - start.y) / zoom, box.bottom - box.top, targets.ys, threshold);
            if (sy) { dy = (e.clientY - start.y) / zoom + sy.delta; lines.y.push({ key: bandKey, y: sy.line }); }
          }
        }
        // the whole group stays inside the band width
        if (box) dx = Math.min(Math.max(dx, -box.left), Math.max(-box.left, width - box.right));
        const patches = Object.fromEntries(items.map(([id, item]) => [id, { x: Math.round(item.x + dx), y: Math.round(item.y + dy) }]));
        dispatch({ type: 'LIVE', update: () => patchElements(base, patches) });
        const over = oneBand ? bandAt(e.clientY) : null;
        setDragInfo(over && over.key !== bandKey
          ? { kind: 'element', target: over.key, text: `→ ${bandLabel(over.key)}` }
          : { kind: 'move', text: `x ${Math.round(start.ex + dx)}  y ${Math.round(start.ey + dy)}${items.length > 1 ? `  ·  ${items.length} elements` : ''}${free ? '  ·  free (Alt)' : ''}` });
      } else if (element.type === 'table') {
        // a Table keeps its column proportions: the columns are scaled to the new width
        let right = start.ex + start.ew + dx;
        if (prefs.snapGrid) right = snapToGrid(right, prefs.gridSize);
        const newWidth = Math.round(Math.min(Math.max(MIN_W * Math.max(1, (element.columns || []).length), right - start.ex), width - start.ex));
        const factor = newWidth / Math.max(1, start.ew);
        const columns = (element.columns || []).map((c) => ({ ...c, width: Math.max(5, Math.round(c.width * factor)) }));
        setDragInfo({ kind: 'resize', text: `Table width ${newWidth}px · columns scaled` });
        dispatch({ type: 'LIVE', update: () => patchElements(base, { [element.id]: { width: newWidth, columns } }) });
      } else if (resizeGroup) {
        let right = start.ex + start.ew + dx;
        let bottom = start.ey + start.eh + dy;
        if (prefs.snapGrid) { right = snapToGrid(right, prefs.gridSize); bottom = snapToGrid(bottom, prefs.gridSize); }
        if (prefs.smartGuides) {
          const sx = nearestSnap(right, 0, targets.xs, threshold);
          if (sx) { right = sx.line; lines.x.push(sx.line); }
          const sy = nearestSnap(bottom, 0, targets.ys, threshold);
          if (sy) { bottom = sy.line; lines.y.push({ key: bandKey, y: sy.line }); }
        }
        const dw = Math.round(right - start.ex - start.ew);
        const dh = Math.round(bottom - start.ey - start.eh);
        const patches = Object.fromEntries(Object.entries(groupSizes).map(([id, s]) => [id, {
          width: Math.max(MIN_W, s.w + dw),
          height: s.type === 'line' ? s.h : Math.max(MIN_H, s.h + dh),
        }]));
        setDragInfo({ kind: 'resize', text: `${Object.keys(patches).length} elements · width ${dw >= 0 ? '+' : ''}${dw}px · height ${dh >= 0 ? '+' : ''}${dh}px` });
        dispatch({ type: 'LIVE', update: () => patchElements(base, patches) });
      } else {
        // A table column moves the columns to its right (and may widen the page); a box in a letter changes only
        // itself. View → Canvas → Resizing chooses; Alt flips it for one drag.
        const rule = BACKDROP_TYPES.has(element.type) ? { follow: false, widen: false } : resizeRule(base, bandKey, element, prefs.resizeMode, e.altKey);
        const shift = rule.follow;
        const maxWidth = rule.widen ? PAGE_LIMITS.max : width - start.ex;
        let right = start.ex + start.ew + dx;
        let bottom = start.ey + start.eh + dy;
        if (prefs.snapGrid) { right = snapToGrid(right, prefs.gridSize); bottom = snapToGrid(bottom, prefs.gridSize); }
        if (prefs.smartGuides) {
          const sx = nearestSnap(start.ex + start.ew + dx, 0, targets.xs, threshold);
          if (sx) { right = sx.line; lines.x.push(sx.line); }
          const sy = nearestSnap(start.ey + start.eh + dy, 0, targets.ys, threshold);
          if (sy) { bottom = sy.line; lines.y.push({ key: bandKey, y: sy.line }); }
        }
        const size = { width: Math.round(Math.min(Math.max(MIN_W, right - start.ex), maxWidth)), height: Math.round(Math.max(MIN_H, bottom - start.ey)) };
        setDragInfo({ kind: 'resize', text: `${size.width} × ${size.height}px${shift ? ` · columns to the right follow${rule.widen ? ', the page may widen' : ''} (Alt = this box only)` : ' · this box only, inside the page (Alt = columns follow)'}` });
        dispatch({ type: 'LIVE', update: () => resizeElement(base, bandKey, element.id, size, shift, rule.widen) });
      }
      setSnapLines(lines.x.length || lines.y.length ? lines : null);
    };
    const up = (e) => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      setDragInfo(null);
      setSnapLines(null);
      if (mode !== 'move') {
        const keys = resizeGroup ? bandKeys(base).filter((key) => getBand(base, key).elements.some((el) => groupSizes[el.id])) : [bandKey];
        dispatch({ type: 'LIVE', update: (d) => settleBands(d, keys) });
        return;
      }
      const over = oneBand ? bandAt(e.clientY) : null;
      if (over && over.key !== bandKey) {
        // carry the elements to the band under the pointer, keeping their offsets to the dragged one
        const y = (e.clientY - over.rect.top) / zoom - grabOffsetY;
        dispatch({
          type: 'LIVE',
          update: (d) => items.reduce((next, [id, item]) => transferElement(next, bandKey, over.key, id, y + (item.y - start.ey)), d),
        });
      } else {
        dispatch({ type: 'LIVE', update: (d) => settleBands(d, items.map(([, item]) => item.bandKey)) });
      }
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };

  /* ---------- rubber-band (marquee) selection ---------- */
  const beginMarquee = (event) => {
    if (event.button !== 0 || tool) return;
    if (event.target.closest?.('.el, .handle, .band-resize, .page-resize, .ruler-h, .ruler-v, .guide, .canvas-toolbar')) return;
    const startX = event.clientX;
    const startY = event.clientY;
    const additive = event.shiftKey || event.ctrlKey || event.metaKey;
    const before = additive ? ids : [];
    let moved = false;
    const rectOf = (e) => ({ left: Math.min(startX, e.clientX), top: Math.min(startY, e.clientY), right: Math.max(startX, e.clientX), bottom: Math.max(startY, e.clientY) });
    const hits = (rect) => [...(scrollRef.current?.querySelectorAll('.el[data-id]') || [])].filter((node) => {
      const r = node.getBoundingClientRect();
      return r.left < rect.right && r.right > rect.left && r.top < rect.bottom && r.bottom > rect.top;
    }).map((node) => node.dataset.id);
    const move = (e) => {
      if (!moved && Math.hypot(e.clientX - startX, e.clientY - startY) < 4) return;
      moved = true;
      const rect = rectOf(e);
      setMarquee(rect);
      setDragInfo({ kind: 'marquee', text: `${hits(rect).length} element(s) — Shift adds to the selection` });
    };
    const up = (e) => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      if (!moved) return;
      setMarquee(null);
      setDragInfo(null);
      suppressClick.current = true;
      setTimeout(() => { suppressClick.current = false; }, 0);
      const found = hits(rectOf(e));
      if (found.length || !additive) select(selectionOf([...before, ...found]));
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };

  /* ---------- ruler guides ---------- */
  const originX = () => (pageRef.current ? pageRef.current.getBoundingClientRect().left + margins.left * zoom : 0);
  const beginGuide = (index) => (event) => {
    if (event.button !== 0) return;
    event.stopPropagation();
    event.preventDefault();
    const pageTop = pageRef.current?.getBoundingClientRect().top ?? 0;
    const at = (e) => {
      let x = (e.clientX - originX()) / zoom;
      if (prefs.snapGrid && !e.altKey) x = snapToGrid(x, prefs.gridSize);
      return Math.round(Math.min(Math.max(x, -margins.left), design.page.width - margins.left));
    };
    const startX = at(event);
    setGuideDrag({ index, x: index === null ? startX : guides[index], remove: false });
    const move = (e) => {
      const x = at(e);
      const remove = index !== null && e.clientY < pageTop;
      setGuideDrag({ index, x, remove });
      setDragInfo({ kind: 'guide', text: remove ? 'Release to remove the guide' : `Guide at x ${x}` });
    };
    const up = (e) => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      setGuideDrag(null);
      setDragInfo(null);
      const x = at(e);
      const remove = index !== null && e.clientY < pageTop;
      const rest = guides.filter((_, i) => i !== index);
      // a click on the ruler without dragging below it adds nothing
      if (index === null && e.clientY < pageTop) return;
      dispatch({ type: 'DESIGN', update: (d) => ({ ...d, page: { ...d.page, guides: remove ? rest : [...rest, x].sort((a, b) => a - b) } }) });
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };
  const removeGuide = (index) => (event) => {
    event.stopPropagation();
    dispatch({ type: 'DESIGN', update: (d) => ({ ...d, page: { ...d.page, guides: (d.page.guides || []).filter((_, i) => i !== index) } }) });
  };

  /* ---------- bands and page ---------- */
  const beginBandResize = (bandKey) => (event) => {
    event.stopPropagation();
    event.preventDefault();
    dispatch({ type: 'SNAPSHOT' });
    select({ kind: 'band', key: bandKey });
    const startY = event.clientY;
    const band = getBand(design, bandKey);
    const startHeight = band.height;
    const floor = contentBottom(band); // like Classic reports: a band cannot be smaller than its elements
    const move = (e) => {
      let height = startHeight + (e.clientY - startY) / zoom;
      if (prefs.snapGrid && !e.altKey) height = snapToGrid(height, prefs.gridSize);
      height = Math.round(Math.min(Math.max(floor, height), 1500));
      setDragInfo({ kind: 'band', target: bandKey, text: `${bandLabel(bandKey)} · ${height}px` });
      dispatch({ type: 'LIVE', update: (d) => setBand(d, bandKey, { ...getBand(d, bandKey), height }) });
    };
    const up = () => {
      setDragInfo(null);
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };

  const fitBand = (bandKey) => (event) => {
    event.stopPropagation();
    dispatch({ type: 'DESIGN', update: (d) => setBand(d, bandKey, { ...getBand(d, bandKey), height: contentBottom(getBand(d, bandKey)) }) });
  };

  const beginPageResize = (event) => {
    event.stopPropagation();
    event.preventDefault();
    dispatch({ type: 'SNAPSHOT' });
    select({ kind: 'report' });
    const startX = event.clientX;
    const startWidth = design.page.width;
    // never narrower than the content, so no element ends up outside the page
    const floor = Math.max(PAGE_LIMITS.min, contentRight(design) + margins.left + margins.right);
    const move = (e) => {
      const pageWidth = Math.round(Math.min(Math.max(floor, startWidth + (e.clientX - startX) / zoom), PAGE_LIMITS.max));
      setDragInfo({ kind: 'page', text: `Page width ${pageWidth}px` });
      dispatch({ type: 'LIVE', update: (d) => ({ ...d, page: { ...d.page, width: pageWidth } }) });
    };
    const up = () => {
      setDragInfo(null);
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };

  const selectedEls = ids.map((id) => bandKeys(design).map((k) => getBand(design, k).elements.find((el) => el.id === id)).find(Boolean)).filter(Boolean);
  const lockedAll = selectedEls.length > 0 && selectedEls.every((el) => el.locked);
  const grid = prefs.showGrid ? Math.max(2, prefs.gridSize) * zoom : 0;
  useFonts();                                       // own fonts load for the canvas
  const gridStyle = grid >= 4
    ? { backgroundImage: 'linear-gradient(to right, var(--grid-line) 1px, transparent 1px), linear-gradient(to bottom, var(--grid-line) 1px, transparent 1px)', backgroundSize: `${grid}px ${grid}px` }
    : {};
  const shown = bandKeys(design).filter((key) => bandPresent(getBand(design, key)));
  const guideLines = guides.map((x, index) => ({ x: guideDrag?.index === index ? guideDrag.x : x, index, removing: guideDrag?.index === index && guideDrag.remove }));
  if (guideDrag && guideDrag.index === null) guideLines.push({ x: guideDrag.x, index: null, removing: false });

  return (
    <div className="canvas-wrap">
      {showToolbar && <CanvasToolbar count={selectedEls.length} lockedAll={lockedAll} actions={actions} prefs={prefs} setPrefs={setPrefs} hasClipboard={hasClipboard} onQuery={onQuery} onTemplates={onTemplates} />}
      <div
        ref={scrollRef}
        className={`canvas-scroll ${prefs.showRulers ? 'with-rulers' : ''}`}
        tabIndex={-1}
        onPointerDown={beginMarquee}
        onClick={(e) => { if (!suppressClick.current && (e.target === e.currentTarget || e.target.classList.contains('page'))) select({ kind: 'report' }); }}
        onContextMenu={(e) => { if (e.target === e.currentTarget || e.target.classList.contains('page')) contextMenu(actions.canvasMenu, { kind: 'report' })(e); }}
      >
        {prefs.showRulers && (
          <div className="ruler-h" style={{ width: design.page.width * zoom }} onPointerDown={beginGuide(null)} title="Drag down to add a vertical guide">
            <RulerTicks from={-margins.left} to={design.page.width - margins.left} zoom={zoom} />
          </div>
        )}
        <div ref={pageRef} className="page" style={{ width: design.page.width * zoom, paddingTop: margins.top * zoom, paddingBottom: margins.bottom * zoom }}>
          <span className="page-size">{Math.round(design.page.width)} × {Math.round(design.page.height)} px{design.page.ignorePagination ? ' · single long page' : ''}</span>
          {!shown.length && <div className="page-empty">This report has no bands. Right-click here or in the Outline → <b>Create Band</b>.</div>}
          {shown.map((key) => {
            const band = getBand(design, key);
            const bandSelected = selection.kind === 'band' && selection.key === key;
            const dropTarget = dragInfo?.kind === 'element' && dragInfo.target === key;
            const bandHeight = Math.max(band.height * zoom, MIN_BAND_VIEW);
            return (
              <div className="band-row" key={key} style={{ paddingLeft: margins.left * zoom }}>
                {prefs.showRulers && <div className="ruler-v" style={{ height: bandHeight }}><RulerTicks from={0} to={bandHeight / zoom} zoom={zoom} vertical /></div>}
                <div
                  ref={(node) => { bandRefs.current[key] = node; }}
                  data-band={key}
                  className={`band ${bandSelected ? 'selected' : ''} ${tool ? 'armed' : ''} ${band.height === 0 ? 'band-hidden' : ''} ${dropTarget ? 'drop-target' : ''}`}
                  style={{ width: width * zoom, height: bandHeight, ...(band.height ? gridStyle : {}) }}
                  onClick={(event) => onBandClick(key, event)}
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={(event) => onDrop(key, event)}
                  onContextMenu={contextMenu(() => actions.bandMenu(key), { kind: 'band', key })}
                >
                  <span className="band-name">{bandLabel(key)}{band.height === 0 ? ' · 0px' : ''}{band.printWhenExpression ? <i title={`Prints when ${band.printWhenExpression}`}> · when…</i> : null}</span>
                  {bandSelected && (
                    <span className="band-tools">
                      <button type="button" title={`Add another band after ${bandLabel(key)}`} onClick={(e) => { e.stopPropagation(); actions.addBandAt(key); }}>＋ Add {BAND_ADD[bandKind(key)] || 'band'}</button>
                    </span>
                  )}
                  {band.elements.map((element) => (
                    <ElementView
                      key={element.id}
                      known={known}
                      element={element}
                      zoom={zoom}
                      selected={idSet.has(element.id)}
                      primary={element.id === primaryId}
                      onPointerDown={beginDrag(key, element, 'move')}
                      onResizeDown={beginDrag(key, element, 'resize')}
                      onContextMenu={elementContextMenu(element.id)}
                      editing={editingText === element.id}
                      onCommitText={(text) => { setEditingText(null); dispatch({ type: 'DESIGN', update: (d) => patchElements(d, { [element.id]: { text } }) }); }}
                      onCancelText={() => setEditingText(null)}
                      onMoreText={() => { setEditingText(null); onEditExpression(element.id); }}
                      onDoubleClick={element.type === 'staticText' && !element.locked && (!element.markup || element.markup === 'none') ? (e) => { e.stopPropagation(); select({ kind: 'element', id: element.id }); setEditingText(element.id); }
                        : ['staticText', 'textField'].includes(element.type) ? (e) => { e.stopPropagation(); onEditExpression(element.id); }
                        : element.type === 'table' && onOpenTable ? (e) => { e.stopPropagation(); onOpenTable(element.id); } : undefined}
                    />
                  ))}
                  {snapLines?.y.filter((line) => line.key === key).map((line) => <span key={`sy${line.y}`} className="snap-line h" style={{ top: line.y * zoom }} />)}
                  <span
                    className="band-resize"
                    onPointerDown={beginBandResize(key)}
                    onDoubleClick={fitBand(key)}
                    title={`${bandLabel(key)} height: drag to change · double-click = fit to content`}
                  />
                </div>
              </div>
            );
          })}
          {guideLines.map((g) => (
            <span
              key={`g${g.index ?? 'new'}`}
              className={`guide ${g.removing ? 'removing' : ''}`}
              style={{ left: (margins.left + g.x) * zoom }}
              onPointerDown={g.index === null ? undefined : beginGuide(g.index)}
              onDoubleClick={g.index === null ? undefined : removeGuide(g.index)}
              title={`Guide at x ${g.x} · drag to move · drag onto the ruler or double-click to remove`}
            />
          ))}
          {snapLines?.x.map((x) => <span key={`sx${x}`} className="snap-line v" style={{ left: (margins.left + x) * zoom }} />)}
          <span className="page-resize" onPointerDown={beginPageResize} title="Page width: drag to change" />
        </div>
      </div>
      {marquee && <div className="marquee" style={{ left: marquee.left, top: marquee.top, width: marquee.right - marquee.left, height: marquee.bottom - marquee.top }} />}
      {dragInfo && <div className="drag-info">{dragInfo.text}</div>}
    </div>
  );
}
