// The Designer's ribbon (like Excel / Word / Tableau): tabs File, Home, Insert, Arrange, Data and View with grouped
// buttons, a quick-access row (report name, Undo, Redo, Save, Publish) and a collapse button. Every button calls the
// same actions as the menus and shortcuts (outlineActions.js), so nothing here has its own logic.
import { useEffect, useRef, useState } from 'react';
import { selectedIds } from './arrange.js';
import { FONT_NAMES, PALETTE_GROUPS, ZOOM_LIMITS, clampZoom, findElement } from './designModel.js';
import { MRD_ACCEPT } from './mrd.js';
import { publishState } from './panels.jsx';
import { LookPanel } from './shared/ThemeMenu.jsx';

const KEY = 'designer-ribbon';
const P = {
  newDoc: 'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M12 12v6M9 15h6',
  open: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v1H3zM3 10h18l-2 9H5z',
  save: 'M5 3h11l3 3v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zM7 3v5h8V3M7 21v-7h10v7',
  download: 'M12 4v11M7 10l5 5 5-5M5 20h14',
  publish: 'M7 18a5 5 0 0 1-.6-9.96A6 6 0 0 1 18 8a4.5 4.5 0 0 1 0 9M12 12v9M9 15l3-3 3 3',
  demo: 'M12 3l1.8 4.7L18.5 9.5 13.8 11.3 12 16l-1.8-4.7L5.5 9.5l4.7-1.8zM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z',
  layouts: 'M4 4h16v6H4zM4 14h7v6H4zM15 14h5v6h-5z',
  paste: 'M9 4h6v3H9zM8 5H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2M9 13h6M9 17h4',
  cut: 'M6 7a3 3 0 1 0 0 .01M6 17a3 3 0 1 0 0 .01M8.5 8.5 20 20M8.5 15.5 20 4',
  copy: 'M9 9h11v11H9zM5 15H4V4h11v1',
  duplicate: 'M8 8h12v12H8zM4 16V4h12M14 11v6M11 14h6',
  undo: 'M9 14 4 9l5-5M4 9h10a6 6 0 0 1 0 12h-3',
  redo: 'M15 14l5-5-5-5M20 9H10a6 6 0 0 0 0 12h3',
  alignL: 'M4 4v16M8 7h12M8 12h8M8 17h10',
  alignC: 'M12 4v16M6 7h12M8 12h8M5 17h14',
  alignR: 'M20 4v16M4 7h12M8 12h8M6 17h10',
  alignT: 'M4 4h16M7 8v12M12 8v8M17 8v10',
  alignM: 'M4 12h16M7 6v12M12 8v8M17 5v14',
  alignB: 'M4 20h16M7 4v12M12 8v8M17 6v10',
  textL: 'M4 6h16M4 10h10M4 14h16M4 18h10',
  textC: 'M4 6h16M7 10h10M4 14h16M7 18h10',
  textR: 'M4 6h16M10 10h10M4 14h16M10 18h10',
  textJ: 'M4 6h16M4 10h16M4 14h16M4 18h16',
  vTop: 'M4 4h16M12 8v12M8 12l4-4 4 4',
  vMid: 'M4 12h16M12 3v5M12 16v5M9 6l3 2 3-2M9 18l3-2 3 2',
  vBot: 'M4 20h16M12 4v12M8 12l4 4 4-4',
  fill: 'M5 11 12 4l7 7-7 7zM19 15s2 2.2 2 3.5a2 2 0 0 1-4 0c0-1.3 2-3.5 2-3.5M3 21h18',
  selectAll: 'M4 4h4M12 4h0M16 4h4v4M20 12v0M20 16v4h-4M12 20h0M8 20H4v-4M4 12v0M4 8V4M8 8h8v8H8z',
  trash: 'M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6',
  lock: 'M6 11h12v9H6zM8 11V8a4 4 0 0 1 8 0v3',
  unlock: 'M6 11h12v9H6zM8 11V8a4 4 0 0 1 7.5-2',
  front: 'M8 8h12v12H8zM4 4h10v4M4 4v10h4',
  back: 'M4 4h12v12H4zM20 8v12H8',
  distH: 'M4 4v16M20 4v16M9 8h6v8H9z',
  distV: 'M4 4h16M4 20h16M8 9h8v6H8z',
  sameW: 'M4 12h16M7 9l-3 3 3 3M17 9l3 3-3 3M4 5v14M20 5v14',
  sameH: 'M12 4v16M9 7l3-3 3 3M9 17l3 3 3-3M5 4h14M5 20h14',
  sameWH: 'M5 5h14v14H5zM9 9h6v6H9z',
  db: 'M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
  params: 'M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M8 10v4M16 16v4',
  filter: 'M4 5h16l-6 7v6l-4 2v-8z',
  sort: 'M7 4v16M4 17l3 3 3-3M17 20V4M14 7l3-3 3 3',
  datasets: 'M4 5h16v5H4zM4 14h16v5H4zM8 7.5h0M8 16.5h0',
  plug: 'M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-12 0zM12 17v4',
  eye: 'M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
  code: 'M8 8l-4 4 4 4M16 8l4 4-4 4M14 5l-4 14',
  setup: 'M4 6h16M4 12h16M4 18h16M8 4v4M16 10v4M10 16v4',
  canvas: 'M4 5h16v14H4zM4 9h16M8 9v10',
  zoomIn: 'M10 4a6 6 0 1 1 0 12 6 6 0 0 1 0-12zM21 21l-6.5-6.5M10 7v6M7 10h6',
  zoomOut: 'M10 4a6 6 0 1 1 0 12 6 6 0 0 1 0-12zM21 21l-6.5-6.5M7 10h6',
  grid: 'M4 4h16v16H4zM4 9.3h16M4 14.6h16M9.3 4v16M14.6 4v16',
  magnet: 'M6 4v8a6 6 0 0 0 12 0V4h-4v8a2 2 0 0 1-4 0V4zM6 8h4M14 8h4',
  guides: 'M4 12h16M12 4v16M4 4l3 3M20 20l-3-3',
  ruler: 'M3 17 17 3l4 4L7 21zM7 13l2 2M10 10l2 2M13 7l2 2',
  panelL: 'M4 4h16v16H4zM9 4v16',
  explorer: 'M3 6h6l2 2h10v11H3zM3 10h18',
  outline: 'M5 6h14M9 12h10M9 18h10M5 12h.01M5 18h.01',
  paletteBox: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z',
  properties: 'M4 7h9M17 7h3M4 17h3M11 17h9M15 5v4M9 15v4',
  panelR: 'M4 4h16v16H4zM15 4v16',
  palette: 'M12 3a9 9 0 1 0 0 18c1.1 0 1.7-.8 1.7-1.7 0-.5-.2-.9-.5-1.2-.3-.3-.5-.7-.5-1.2 0-.9.8-1.7 1.7-1.7H16a5 5 0 0 0 5-5C21 6.6 17 3 12 3z',
  chevronUp: 'M6 15l6-6 6 6',
  chevronDown: 'M6 9l6 6 6-6',
  table: 'M4 5h16v14H4zM4 10h16M4 15h16M10 5v14',
};

export function RIcon({ name, size = 18 }) {
  return (
    <svg className="rb-svg" viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={P[name]} />
    </svg>
  );
}

const Big = ({ icon, label, title, onClick, disabled, on, primary }) => (
  <button type="button" className={`rb-big ${on ? 'on' : ''} ${primary ? 'primary' : ''}`} title={title || label} onClick={onClick} disabled={disabled} aria-pressed={on === undefined ? undefined : on}>
    <RIcon name={icon} size={24} /><span>{label}</span>
  </button>
);
const Small = ({ icon, label, title, onClick, disabled, on }) => (
  <button type="button" className={`rb-sm ${on ? 'on' : ''}`} title={title || label} onClick={onClick} disabled={disabled} aria-pressed={on === undefined ? undefined : on}>
    <RIcon name={icon} size={16} /><span>{label}</span>
  </button>
);
const Ico = ({ icon, title, onClick, disabled, on, children }) => (
  <button type="button" className={`rb-ico ${on ? 'on' : ''}`} title={title} aria-label={title} onClick={onClick} disabled={disabled} aria-pressed={on === undefined ? undefined : on}>
    {children || <RIcon name={icon} size={16} />}
  </button>
);
const Group = ({ label, children, className = '' }) => (
  <div className={`rb-group ${className}`} role="group" aria-label={label}>
    <div className="rb-items">{children}</div>
    <div className="rb-label">{label}</div>
  </div>
);
const Stack = ({ children }) => <div className="rb-stack">{children}</div>;
const Row = ({ children }) => <div className="rb-row">{children}</div>;

const TABS = [['file', 'File'], ['home', 'Home'], ['insert', 'Insert'], ['arrange', 'Arrange'], ['data', 'Data'], ['view', 'View']];

function readPrefs() {
  try { return { tab: 'home', collapsed: false, ...JSON.parse(localStorage.getItem(KEY) || '{}') }; } catch { return { tab: 'home', collapsed: false }; }
}

/**
 * state/dispatch/actions: the designer store; view/setView: Setup | Design | Source | Preview; the on… handlers are
 * App's own (open a file, save, publish…). panels / setPanels: show or hide the side panels.
 */
export default function Ribbon({
  state, dispatch, actions, zoom, setZoom, prefs, setPrefs, view, setView, drawer, setDrawer, panels, setPanels, hasClipboard,
  onNew, onOpenFile, onSave, onSaveFile, onPublish, onDemo, onTemplates, onQuery, onAdapters,
}) {
  const [ui, setUi] = useState(readPrefs);
  const [look, setLook] = useState(false);
  const lookBox = useRef(null);
  // Wide screens: show or hide the docked panel. Tablet/phone: the panel slides in over the report.
  const narrow = () => typeof window !== 'undefined' && window.matchMedia('(max-width: 1100px)').matches;
  const SIDE = { explorer: 'left', outline: 'left', palette: 'right', properties: 'right' };
  const togglePanel = (name) => {
    if (narrow()) { setDrawer(drawer === SIDE[name] ? null : SIDE[name]); return; }
    setDrawer(null);
    setPanels((p) => ({ ...p, [name]: !p[name] }));
  };
  const panelOn = (name) => (narrow() ? drawer === SIDE[name] : panels[name]);
  // The Theme panel floats over the page (the ribbon body scrolls sideways and would cut it off).
  const lookPlace = () => {
    const box = lookBox.current?.getBoundingClientRect();
    if (!box) return undefined;
    const width = Math.min(360, window.innerWidth - 16);
    return { position: 'fixed', top: Math.round(box.bottom + 4), left: Math.round(Math.max(8, Math.min(box.left, window.innerWidth - width - 8))),
      width, maxHeight: `calc(100vh - ${Math.round(box.bottom + 16)}px)`, overflowY: 'auto', zIndex: 60 };
  };
  useEffect(() => { try { localStorage.setItem(KEY, JSON.stringify(ui)); } catch { /* private mode */ } }, [ui]);
  useEffect(() => {
    if (!look) return undefined;
    const close = (event) => { if (!lookBox.current?.contains(event.target)) setLook(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [look]);

  const { report, design, selection, tool, past, future, dirty } = state;
  const ids = selectedIds(selection);
  const count = ids.length;
  const element = selection.kind === 'element' ? findElement(design, selection.id)?.element : null;
  const lockedAll = count > 0 && ids.every((id) => findElement(design, id)?.element.locked);
  const none = count === 0;
  const textual = Boolean(element) && ['staticText', 'textField', 'barcode', 'table', 'crosstab'].includes(element.type);
  const set = (patch) => setPrefs((p) => ({ ...p, ...patch }));
  const pick = (tab) => setUi((current) => ({ tab, collapsed: current.tab === tab ? current.collapsed : false }));
  const status = report.isNew ? { cls: 'draft', text: 'Not saved' } : (() => { const s = publishState(report.status); return { cls: s.cls, text: s.cls === 'published' ? 'Published' : s.cls === 'changed' ? 'Changed' : 'Draft' }; })();
  const inDesign = view === 'design';
  const toDesign = (fn) => () => { if (!inDesign) setView('design'); fn(); };

  const body = {
    file: (
      <>
        <Group label="Report">
          <Big icon="newDoc" label="New" title="New report (starts with the layout gallery)" onClick={onNew} />
          <label className="rb-big" title="Open a .mrd report file, or import a Jaspersoft Studio .jrxml"><RIcon name="open" size={24} /><span>Open file</span>
            <input type="file" accept={MRD_ACCEPT} hidden onChange={(e) => { onOpenFile(e.target.files[0]); e.target.value = ''; }} />
          </label>
          <Big icon="save" label="Save" title="Save in your designer workspace (Ctrl+S). The server is not changed." onClick={onSave} />
          <Big icon="download" label="Save as file" title="Download this report as one .mrd file (query, parameters, adapter, design, images)" onClick={onSaveFile} />
        </Group>
        <Group label="Server">
          <Big icon="publish" label="Publish" title="Copy this report to a folder on the report server" onClick={onPublish} primary />
        </Group>
        <Group label="Start from">
          <Big icon="layouts" label="Layouts" title="Apply a layout template (blank page, letter, table, form, certificate)" onClick={onTemplates} />
          <Big icon="demo" label="Demo design" title="A demo design with sample fields" onClick={onDemo} />
        </Group>
      </>
    ),
    home: (
      <>
        <Group label="Clipboard">
          <Big icon="paste" label="Paste" title="Paste (Ctrl+V)" onClick={toDesign(() => actions.paste())} disabled={!hasClipboard} />
          <Stack>
            <Small icon="cut" label="Cut" title="Cut (Ctrl+X)" onClick={() => actions.cut()} disabled={none} />
            <Small icon="copy" label="Copy" title="Copy (Ctrl+C)" onClick={() => actions.copy()} disabled={none} />
            <Small icon="duplicate" label="Duplicate" title="Duplicate (Ctrl+D)" onClick={() => actions.duplicateSelected()} disabled={none} />
          </Stack>
        </Group>
        <Group label="History">
          <Stack>
            <Small icon="undo" label="Undo" title="Undo (Ctrl+Z)" onClick={() => dispatch({ type: 'UNDO' })} disabled={!past.length} />
            <Small icon="redo" label="Redo" title="Redo (Ctrl+Y)" onClick={() => dispatch({ type: 'REDO' })} disabled={!future.length} />
          </Stack>
        </Group>
        <Group label="Font" className="rb-font">
          <Row>
            <select className="rb-select wide" aria-label="Font" title="Font" value={element?.fontName || 'SansSerif'} disabled={!textual} onChange={(e) => actions.patchSelected({ fontName: e.target.value })}>
              {FONT_NAMES.map((name) => <option key={name}>{name}</option>)}
            </select>
            <input className="rb-num" type="number" min={4} max={96} aria-label="Font size" title="Font size" value={element?.fontSize ?? ''} disabled={!textual}
              onChange={(e) => { const size = Number(e.target.value); if (size >= 4 && size <= 96) actions.patchSelected({ fontSize: size }); }} />
            <Ico title="Bigger font" disabled={!textual} onClick={() => actions.patchSelected({ fontSize: Math.min(96, (element?.fontSize || 10) + 1) })}><b className="rb-txt">A<sup>+</sup></b></Ico>
            <Ico title="Smaller font" disabled={!textual} onClick={() => actions.patchSelected({ fontSize: Math.max(4, (element?.fontSize || 10) - 1) })}><b className="rb-txt sm">A<sup>−</sup></b></Ico>
          </Row>
          <Row>
            <Ico title="Bold" disabled={!textual} on={Boolean(element?.bold)} onClick={() => actions.patchSelected({ bold: !element?.bold })}><b className="rb-txt">B</b></Ico>
            <Ico title="Italic" disabled={!textual} on={Boolean(element?.italic)} onClick={() => actions.patchSelected({ italic: !element?.italic })}><i className="rb-txt serif">I</i></Ico>
            <Ico title="Underline" disabled={!textual} on={Boolean(element?.underline)} onClick={() => actions.patchSelected({ underline: !element?.underline })}><u className="rb-txt">U</u></Ico>
            <span className="rb-sep" />
            <label className={`rb-ico rb-color ${none ? 'off' : ''}`} title="Text colour (Forecolor)">
              <b className="rb-txt">A</b><i style={{ background: element?.color || '#000000' }} />
              <input type="color" disabled={none} value={element?.color || '#000000'} onChange={(e) => actions.patchSelected({ color: e.target.value })} />
            </label>
            <label className={`rb-ico rb-color ${none ? 'off' : ''}`} title="Fill (Backcolor)">
              <RIcon name="fill" size={15} /><i style={{ background: element?.backColor || 'transparent' }} />
              <input type="color" disabled={none} value={element?.backColor || '#ffffff'} onChange={(e) => actions.patchSelected({ backColor: e.target.value })} />
            </label>
            <Ico title="No fill (transparent)" disabled={none} onClick={() => actions.patchSelected({ backColor: null })}><span className="rb-nofill" /></Ico>
          </Row>
        </Group>
        <Group label="Paragraph">
          <Row>
            {[['left', 'textL', 'Align text left'], ['center', 'textC', 'Center text'], ['right', 'textR', 'Align text right'], ['justified', 'textJ', 'Justify']].map(([value, icon, title]) => (
              <Ico key={value} icon={icon} title={title} disabled={!textual} on={element?.align === value} onClick={() => actions.patchSelected({ align: value })} />
            ))}
          </Row>
          <Row>
            {[['top', 'vTop', 'Text at the top'], ['middle', 'vMid', 'Text in the middle'], ['bottom', 'vBot', 'Text at the bottom']].map(([value, icon, title]) => (
              <Ico key={value} icon={icon} title={title} disabled={!textual} on={(element?.vAlign || 'top') === value && textual} onClick={() => actions.patchSelected({ vAlign: value })} />
            ))}
          </Row>
        </Group>
        <Group label="Editing">
          <Stack>
            <Small icon="selectAll" label="Select all" title="Select all in the band (Ctrl+A)" onClick={toDesign(() => actions.selectAll())} />
            <Small icon={lockedAll ? 'unlock' : 'lock'} label={lockedAll ? 'Unlock' : 'Lock'} title="Lock: cannot be moved or resized (Ctrl+L)" onClick={() => actions.toggleLock()} disabled={none} on={lockedAll} />
            <Small icon="trash" label="Delete" title="Delete (Del)" onClick={() => actions.removeSelected()} disabled={none} />
          </Stack>
        </Group>
        <Group label="Report">
          <Big icon="save" label="Save" title="Save in your workspace (Ctrl+S)" onClick={onSave} />
          <Big icon="publish" label="Publish" title="Copy this report to a folder on the report server" onClick={onPublish} primary />
        </Group>
      </>
    ),
    insert: (
      <>
        {PALETTE_GROUPS.map((group) => (
          <Group key={group.title} label={group.title}>
            <div className={`rb-gallery ${group.items.length > 6 ? 'many' : ''}`}>
              {group.items.map(([type, icon, label]) => (
                <button key={type} type="button" className={`rb-tile ${tool === type ? 'on' : ''}`} draggable
                  title={`${label}: click it, then click a band on the canvas (or drag it onto a band). Esc cancels.`}
                  aria-pressed={tool === type}
                  onDragStart={(e) => e.dataTransfer.setData('application/x-palette', type)}
                  onClick={() => { if (!inDesign) setView('design'); dispatch({ type: 'TOOL', tool: tool === type ? null : type }); }}>
                  <span className="rb-tile-icon">{icon}</span><span>{label}</span>
                </button>
              ))}
            </div>
          </Group>
        ))}
      </>
    ),
    arrange: (
      <>
        <Group label={count > 1 ? 'Align to each other' : 'Align in the band'}>
          <Row>
            <Ico icon="alignL" title="Align left" disabled={none} onClick={() => actions.align('left')} />
            <Ico icon="alignC" title="Align centers" disabled={none} onClick={() => actions.align('hcenter')} />
            <Ico icon="alignR" title="Align right" disabled={none} onClick={() => actions.align('right')} />
          </Row>
          <Row>
            <Ico icon="alignT" title="Align top" disabled={none} onClick={() => actions.align('top')} />
            <Ico icon="alignM" title="Align middles" disabled={none} onClick={() => actions.align('vmiddle')} />
            <Ico icon="alignB" title="Align bottom" disabled={none} onClick={() => actions.align('bottom')} />
          </Row>
        </Group>
        <Group label="Distribute">
          <Big icon="distH" label="Across" title="Equal gaps from left to right (3 or more elements)" onClick={() => actions.distribute('h')} disabled={count < 3} />
          <Big icon="distV" label="Down" title="Equal gaps from top to bottom (3 or more elements)" onClick={() => actions.distribute('v')} disabled={count < 3} />
        </Group>
        <Group label="Same size">
          <Stack>
            <Small icon="sameW" label="Width" title="Same width as the last clicked element" onClick={() => actions.matchSize('width')} disabled={count < 2} />
            <Small icon="sameH" label="Height" title="Same height as the last clicked element" onClick={() => actions.matchSize('height')} disabled={count < 2} />
            <Small icon="sameWH" label="Both" title="Same size as the last clicked element" onClick={() => actions.matchSize('both')} disabled={count < 2} />
          </Stack>
        </Group>
        <Group label="Order">
          <Big icon="front" label="To front" title="Bring to front" onClick={() => actions.order(true)} disabled={none} />
          <Big icon="back" label="To back" title="Send to back" onClick={() => actions.order(false)} disabled={none} />
          <Big icon={lockedAll ? 'unlock' : 'lock'} label={lockedAll ? 'Unlock' : 'Lock'} title="Lock: cannot be moved or resized (Ctrl+L)" onClick={() => actions.toggleLock()} disabled={none} on={lockedAll} />
        </Group>
        <div className="rb-note">{count ? `${count} element${count === 1 ? '' : 's'} selected` : 'Select elements on the canvas (Shift or Ctrl + click for several)'}</div>
      </>
    ),
    data: (
      <>
        <Group label="Query">
          <Big icon="db" label="Dataset & Query" title="Data adapter, query, fields, parameters, filter and sort" onClick={() => onQuery('query')} primary />
          <Stack>
            <Small icon="params" label="Parameters" onClick={() => onQuery('parameters')} />
            <Small icon="filter" label="Filter" title="Filter expression" onClick={() => onQuery('filter')} />
            <Small icon="sort" label="Sort" title="Sort fields" onClick={() => onQuery('sort')} />
          </Stack>
        </Group>
        <Group label="Sources">
          <Big icon="datasets" label="Datasets" title="Extra datasets for Tables, Crosstabs and Lists" onClick={() => onQuery('datasets')} />
          <Big icon="plug" label="Data adapters" title="The databases this report can read" onClick={onAdapters} />
        </Group>
        <Group label="Look at it">
          <Big icon="setup" label="Setup" title="ID, title, folder, adapter, query language, formats" on={view === 'setup'} onClick={() => setView('setup')} />
          <Big icon="eye" label="Preview" title="Run the report with real records" on={view === 'preview'} onClick={() => setView('preview')} />
          <Big icon="code" label="Source" title="The report definition as text" on={view === 'source'} onClick={() => setView('source')} />
        </Group>
      </>
    ),
    view: (
      <>
        <Group label="Zoom">
          <Big icon="zoomOut" label="Out" title="Zoom out (Ctrl −)" onClick={() => setZoom((z) => clampZoom(z - ZOOM_LIMITS.step))} />
          <button type="button" className="rb-big rb-zoom" title="Back to 100% (Ctrl 0)" onClick={() => setZoom(1)}><b>{Math.round(zoom * 100)}%</b><span>100%</span></button>
          <Big icon="zoomIn" label="In" title="Zoom in (Ctrl +)" onClick={() => setZoom((z) => clampZoom(z + ZOOM_LIMITS.step))} />
        </Group>
        <Group label="Canvas">
          <Stack>
            <Small icon="grid" label="Grid" on={prefs.showGrid} onClick={() => set({ showGrid: !prefs.showGrid })} />
            <Small icon="magnet" label="Snap to grid" title="Snap to grid (hold Alt while dragging to move freely)" on={prefs.snapGrid} onClick={() => set({ snapGrid: !prefs.snapGrid })} />
            <label className="rb-sm rb-inline" title="Grid size">Grid
              <select className="rb-select" value={prefs.gridSize} onChange={(e) => set({ gridSize: Number(e.target.value) })}>
                {[2, 5, 8, 10, 20, 25].map((n) => <option key={n} value={n}>{n}px</option>)}
              </select>
            </label>
          </Stack>
          <Stack>
            <Small icon="guides" label="Smart guides" title="Snap to the edges and centers of other elements" on={prefs.smartGuides} onClick={() => set({ smartGuides: !prefs.smartGuides })} />
            <Small icon="ruler" label="Rulers" on={prefs.showRulers} onClick={() => set({ showRulers: !prefs.showRulers })} />
          </Stack>
        </Group>
        <Group label="Panels">
          <Stack>
            <Small icon="explorer" label="Repository explorer" title="Show or hide the Repository explorer: Workspace, Server and Data Adapters" on={panelOn('explorer')} onClick={() => togglePanel('explorer')} />
            <Small icon="outline" label="Outline" title="Show or hide the Outline of the report" on={panelOn('outline')} onClick={() => togglePanel('outline')} />
          </Stack>
          <Stack>
            <Small icon="paletteBox" label="Palette" title="Show or hide the Palette of elements" on={panelOn('palette')} onClick={() => togglePanel('palette')} />
            <Small icon="properties" label="Properties" title="Show or hide the Properties" on={panelOn('properties')} onClick={() => togglePanel('properties')} />
          </Stack>
        </Group>
        <Group label="Look">
          <div className="rb-look" ref={lookBox}>
            <Big icon="palette" label="Theme" title="Colours, light or dark, title bar, spacing" on={look} onClick={() => setLook(!look)} />
            {look && <LookPanel onDone={() => setLook(false)} style={lookPlace()} />}
          </div>
          <Big icon={ui.collapsed ? 'chevronDown' : 'chevronUp'} label={ui.collapsed ? 'Show ribbon' : 'Hide ribbon'} onClick={() => setUi((u) => ({ ...u, collapsed: !u.collapsed }))} />
        </Group>
      </>
    ),
  };

  return (
    <header className={`ribbon ${ui.collapsed ? 'collapsed' : ''}`}>
      <div className="rb-bar">
        <button type="button" className="rb-quick drawer-btn" aria-label="Repository explorer and outline" aria-expanded={drawer === 'left'} onClick={() => setDrawer(drawer === 'left' ? null : 'left')}><RIcon name="panelL" /></button>
        <span className="rb-doc" title={`${report.title || 'Untitled'}${report.folder ? ` — ${report.folder}` : ''}`}>
          <RIcon name="table" size={16} /><b>{report.title || 'Untitled'}</b>{dirty && <span className="rb-dirty" title="Not saved">●</span>}
          {report.folder && <small className="hide-narrow">in {report.folder}</small>}
        </span>
        <div className="rb-tabs" role="tablist" aria-label="Ribbon">
          {TABS.map(([id, label]) => (
            <button key={id} type="button" role="tab" aria-selected={ui.tab === id} className={`${ui.tab === id ? 'on' : ''} ${id === 'file' ? 'file' : ''}`}
              onClick={() => pick(id)} onDoubleClick={() => setUi((u) => ({ ...u, collapsed: !u.collapsed }))}>{label}</button>
          ))}
        </div>
        <div className="rb-quickbar">
          <button type="button" className="rb-quick" title="Undo (Ctrl+Z)" aria-label="Undo" disabled={!past.length} onClick={() => dispatch({ type: 'UNDO' })}><RIcon name="undo" /></button>
          <button type="button" className="rb-quick" title="Redo (Ctrl+Y)" aria-label="Redo" disabled={!future.length} onClick={() => dispatch({ type: 'REDO' })}><RIcon name="redo" /></button>
          <span className={`pub-chip ${status.cls}`} title={status.text}>{status.text}</span>
          <button type="button" className="rb-quick" title="Save (Ctrl+S)" aria-label="Save" onClick={onSave}><RIcon name="save" /></button>
          <button type="button" className="btn primary rb-publish" title="Copy this report to a folder on the report server" onClick={onPublish}><RIcon name="publish" size={16} /><span className="lbl">Publish</span></button>
          <button type="button" className="rb-quick" title={ui.collapsed ? 'Show the ribbon' : 'Hide the ribbon (double-click a tab does the same)'} aria-label={ui.collapsed ? 'Show the ribbon' : 'Hide the ribbon'}
            onClick={() => setUi((u) => ({ ...u, collapsed: !u.collapsed }))}><RIcon name={ui.collapsed ? 'chevronDown' : 'chevronUp'} /></button>
          <button type="button" className="rb-quick drawer-btn" aria-label="Palette and properties" aria-expanded={drawer === 'right'} onClick={() => setDrawer(drawer === 'right' ? null : 'right')}><RIcon name="panelR" /></button>
        </div>
      </div>
      {!ui.collapsed && <div className="rb-body" role="tabpanel" aria-label={TABS.find(([id]) => id === ui.tab)?.[1]}>{body[ui.tab]}</div>}
    </header>
  );
}
