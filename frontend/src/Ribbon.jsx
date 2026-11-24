// The Designer's ribbon: a header (report name with the File menu, Saved / Draft status, Design · Setup · Preview · Source,
// Ctrl K search, Undo, Redo, Check, light / dark, Help, Save, Publish), seven slim tabs (Home, Insert, Format, Arrange, Page,
// Data & Formulas, Review & Share, View) with one row of grouped buttons each, and a collapse button. Every button calls the same
// actions as the menus and shortcuts (outlineActions.js), so nothing here has its own logic. Ctrl K reads these buttons.
import { useEffect, useRef, useState } from 'react';
import { selectedIds } from './arrange.js';
import { FONT_NAMES, PAGE_PRESETS, PALETTE_GROUPS, RESIZE_MODES, ZOOM_LIMITS, clampZoom, contentRight, findElement, mapElements, pagePreset } from './designModel.js';
import HelpDialog from './HelpDialogs.jsx';
import { MRD_WITH_IMAGES_ACCEPT } from './mrd.js';
import { publishState } from './panels.jsx';
import { LookPanel, useLook } from './shared/ThemeMenu.jsx';
import CommandPalette from './CommandPalette.jsx';
import FunctionsDialog from './FunctionsDialog.jsx';

const KEY = 'designer-ribbon';
const P = {
  newDoc: 'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M12 12v6M9 15h6',
  open: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v1H3zM3 10h18l-2 9H5z',
  save: 'M5 3h11l3 3v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zM7 3v5h8V3M7 21v-7h10v7',
  download: 'M12 4v11M7 10l5 5 5-5M5 20h14',
  publish: 'M7 18a5 5 0 0 1-.6-9.96A6 6 0 0 1 18 8a4.5 4.5 0 0 1 0 9M12 12v9M9 15l3-3 3 3',
  demo: 'M12 3l1.8 4.7L18.5 9.5 13.8 11.3 12 16l-1.8-4.7L5.5 9.5l4.7-1.8zM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z',
  layouts: 'M4 4h16v6H4zM4 14h7v6H4zM15 14h5v6h-5z',
  importDesign: 'M4 3h10l4 4v5M14 3v4h4M4 3v18h7M8 9h5M8 13h3M16 15v7M13 19l3 3 3-3',
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
  bAll: 'M4 4h16v16H4z',
  bBottom: 'M4 20h16M4 4h.01M8 4h.01M12 4h.01M16 4h.01M20 4h.01M4 8h.01M4 12h.01M4 16h.01M20 8h.01M20 12h.01M20 16h.01',
  bTop: 'M4 4h16M4 20h.01M8 20h.01M12 20h.01M16 20h.01M20 20h.01M4 8h.01M4 12h.01M4 16h.01M20 8h.01M20 12h.01M20 16h.01',
  bSides: 'M4 4v16M20 4v16M8 4h.01M12 4h.01M16 4h.01M8 20h.01M12 20h.01M16 20h.01',
  bNone: 'M4 4h.01M8 4h.01M12 4h.01M16 4h.01M20 4h.01M4 20h.01M8 20h.01M12 20h.01M16 20h.01M20 20h.01M4 12h.01M20 12h.01',
  hide: 'M3 3l18 18M10.6 10.6a2 2 0 0 0 2.8 2.8M9.9 5.1A10 10 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4.1M6.1 6.1C3.6 7.8 2 12 2 12s3.5 7 10 7a9.7 9.7 0 0 0 5.9-1.9',
  stretch: 'M12 3v18M8 7l4-4 4 4M8 17l4 4 4-4M4 12h3M17 12h3',
  column: 'M9 3h6v18H9zM4 3v18M20 3v18',
  similar: 'M4 4h7v7H4zM13 13h7v7h-7zM13 4h7v7h-7zM4 13h7v7H4z',
  brush: 'M14 4l6 6-8 8H6v-6zM4 20h4',
  reset: 'M4 4v6h6M20 12a8 8 0 1 1-2.3-5.7L20 9',
  portrait: 'M7 3h10v18H7z',
  landscape: 'M3 7h18v10H3z',
  margins: 'M4 4h16v16H4zM8 4v16M16 4v16M4 8h16M4 16h16',
  pageSize: 'M6 3h9l4 4v14H6zM15 3v4h4M9 13h6M9 17h6',
  fitWidth: 'M3 12h18M6 9l-3 3 3 3M18 9l3 3-3 3M3 4v16M21 4v16',
  newPage: 'M6 3h12v18H6zM9 8h6M9 12h6M12 15v4M10 17h4',
  fx: 'M14 4h-2a3 3 0 0 0-3 3v13M6 11h7M15 13l5 6M20 13l-5 6',
  book: 'M4 4h6a2 2 0 0 1 2 2v14a2 2 0 0 0-2-2H4zM20 4h-6a2 2 0 0 0-2 2v14a2 2 0 0 1 2-2h6z',
  sigma: 'M18 5H6l6 7-6 7h12',
  check: 'M4 12l5 5L20 6',
  note: 'M5 4h14v11l-5 5H5zM14 20v-5h5',
  link: 'M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1',
  help: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14M12 17.5h.01',
  keyboard: 'M3 6h18v12H3zM7 10h.01M11 10h.01M15 10h.01M17 14H7',
  star: 'M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z',
  chat: 'M4 5h16v11H9l-5 4z',
  support: 'M4 13a8 8 0 0 1 16 0M4 13v4h3v-5H4M20 13v4h-3v-5h3M17 17a4 4 0 0 1-4 3h-1',
  variable: 'M5 4c-2 4-2 12 0 16M19 4c2 4 2 12 0 16M9 9l6 6M15 9l-6 6',
};
const NUMBER_PATTERNS = [
  ['', 'No pattern'], ['#,##0', '1,234'], ['#,##0.00', '1,234.56'], ['0.00%', '12.34%'], ['Rs. #,##0.00', 'Rs. 1,234.56'],
  ['dd/MM/yyyy', '31/12/2026'], ['dd-MM-yyyy', '31-12-2026'], ['dd MMM yyyy', '31 Dec 2026'], ['dd/MM/yyyy HH:mm', '31/12/2026 14:05'], ['hh:mm a', '02:05 PM'],
];

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

// [id, label, tint] — the tint colours the chosen tab (see --tint-* in shared/theme.css)
const TABS = [['home', 'Home', 'b'], ['insert', 'Insert', 'g'], ['format', 'Format', 'v'], ['arrange', 'Arrange', 'a'], ['page', 'Page', 'c'],
  ['data', 'Data & Formulas', 'b'], ['share', 'Review & Share', 'g'], ['view', 'View', 'v']];
const VIEWS = [['design', 'Design'], ['setup', 'Setup'], ['preview', 'Preview'], ['source', 'Source']];
const HELP_ITEMS = [['help', 'help', 'Help: making a report step by step'], ['keyboard', 'shortcuts', 'Keyboard Shortcuts'], ['book', 'functions', 'Function List'],
  ['star', 'whatsnew', "What's New"], ['chat', 'feedback', 'Feedback'], ['support', 'support', 'Contact Support']];
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
const MARGINS = [['Normal', 30], ['Narrow', 15], ['Wide', 50], ['None', 0]];

function readPrefs() {
  try {
    const saved = { tab: 'home', collapsed: false, ...JSON.parse(localStorage.getItem(KEY) || '{}') };
    return TABS.some(([id]) => id === saved.tab) ? saved : { ...saved, tab: 'home' };   // tabs of the old ribbon (File…) are gone
  } catch { return { tab: 'home', collapsed: false }; }
}

/**
 * state/dispatch/actions: the designer store; view/setView: Setup | Design | Source | Preview; the on… handlers are
 * App's own (open a file, save, publish…). panels / setPanels: show or hide the side panels.
 */
export default function Ribbon({
  state, dispatch, actions, zoom, setZoom, prefs, setPrefs, view, setView, drawer, setDrawer, panels, setPanels, hasClipboard,
  onNew, onOpenFile, onImportDesign, onSave, onSaveFile, onSaveJrxml, onPublish, onDemo, onTemplates, onQuery, onAdapters, onResetLayout, panelSide, onEditExpression, onGo,
}) {
  const [ui, setUi] = useState(readPrefs);
  const [dialog, setDialog] = useState(null);
  const [linkCopied, setLinkCopied] = useState(false);
  const [lookOpen, setLook] = useState(false);
  const [borderWidth, setBorderWidth] = useState(0.5);
  const lookBox = useRef(null);
  const [menu, setMenu] = useState(null);          // 'file' | 'help' | null
  const [searching, setSearching] = useState(false);
  const menuBox = useRef(null);
  const searchSource = useRef(null);
  const [look, saveLook] = useLook();
  const darkNow = look.mode === 'dark' || (look.mode === 'system' && window.ReportingLook?.isDark());
  // Wide screens: show or hide the docked panel. Tablet/phone: the panel slides in over the report.
  const narrow = () => typeof window !== 'undefined' && window.matchMedia('(max-width: 1100px)').matches;
  const DEFAULT_SIDE = { explorer: 'left', outline: 'left', palette: 'right', properties: 'right' };
  const sideOf = (name) => panelSide?.(name) || DEFAULT_SIDE[name];   // panels can be moved to the other side
  const togglePanel = (name) => {
    if (narrow()) { setDrawer(drawer === sideOf(name) ? null : sideOf(name)); return; }
    setDrawer(null);
    setPanels((p) => ({ ...p, [name]: !p[name] }));
  };
  const panelOn = (name) => (narrow() ? drawer === sideOf(name) : panels[name]);
  // The Theme panel floats over the page (the ribbon body scrolls sideways and would cut it off).
  const lookPlace = () => {
    const box = lookBox.current?.getBoundingClientRect();
    if (!box) return undefined;
    const width = Math.min(360, window.innerWidth - 16);
    return { position: 'fixed', top: Math.round(box.bottom + 4), left: Math.round(Math.max(8, Math.min(box.left, window.innerWidth - width - 8))),
      width, maxHeight: `calc(100vh - ${Math.round(box.bottom + 16)}px)`, overflowY: 'auto', zIndex: 60 };
  };
  useEffect(() => { try { localStorage.setItem(KEY, JSON.stringify(ui)); } catch { /* private mode */ } }, [ui]);
  // the File and Help menus close on a click outside them
  useEffect(() => {
    if (!menu) return undefined;
    const close = (event) => { if (!menuBox.current?.contains(event.target)) setMenu(null); };
    const esc = (event) => { if (event.key === 'Escape') setMenu(null); };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', esc); };
  }, [menu]);
  // Ctrl+K (or Ctrl+/ ) opens the action search from anywhere in the Designer
  useEffect(() => {
    const onKey = (event) => {
      if ((event.ctrlKey || event.metaKey) && (event.key.toLowerCase() === 'k' || event.key === '/')) { event.preventDefault(); setMenu(null); setSearching(true); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  // the chosen tab is always in view when the tab row scrolls sideways (phones, narrow windows)
  const tabsRow = useRef(null);
  useEffect(() => { tabsRow.current?.querySelector('[aria-selected="true"]')?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' }); }, [ui.tab]);
  useEffect(() => {
    if (!lookOpen) return undefined;
    const close = (event) => { if (!lookBox.current?.contains(event.target)) setLook(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [lookOpen]);

  const { report, design, selection, tool, past, future, dirty } = state;
  const ids = selectedIds(selection);
  const count = ids.length;
  const element = selection.kind === 'element' ? findElement(design, selection.id)?.element : null;
  const lockedAll = count > 0 && ids.every((id) => findElement(design, id)?.element.locked);
  const none = count === 0;
  const textual = Boolean(element) && ['staticText', 'textField', 'barcode', 'table', 'crosstab'].includes(element.type);
  const chosenEls = ids.map((id) => findElement(design, id)?.element).filter(Boolean);
  const hiddenAll = count > 0 && chosenEls.every((el) => String(el.printWhenExpression || '').trim() === 'false');
  const textEls = chosenEls.filter((el) => ['staticText', 'textField'].includes(el.type));
  const stretchAll = textEls.length > 0 && textEls.every((el) => el.stretch);
  const fieldEls = chosenEls.filter((el) => el.type === 'textField');
  const samePattern = fieldEls.length && fieldEls.every((el) => (el.pattern || '') === (fieldEls[0].pattern || '')) ? (fieldEls[0].pattern || '') : null;
  const setPattern = (pattern) => {
    const fieldIds = fieldEls.map((el) => el.id);
    dispatch({ type: 'DESIGN', key: `pattern-${fieldIds.join()}`, update: (d) => mapElements(d, (el) => (fieldIds.includes(el.id) ? { ...el, pattern } : el)) });
  };
  const set = (patch) => setPrefs((p) => ({ ...p, ...patch }));
  const pick = (tab) => setUi((current) => ({ tab, collapsed: current.tab === tab ? current.collapsed : false }));
  const status = report.isNew ? { cls: 'draft', text: 'Not saved' } : (() => { const s = publishState(report.status); return { cls: s.cls, text: s.cls === 'published' ? 'Published' : s.cls === 'changed' ? 'Changed' : 'Draft' }; })();
  const inDesign = view === 'design';
  const toDesign = (fn) => () => { if (!inDesign) setView('design'); fn(); };
  // Page Layout, Formulas, Review and Share
  const { page } = design;
  const margins = page.margins || {};
  const setPage = (values) => dispatch({ type: 'DESIGN', key: `page-${Object.keys(values).join()}`, update: (d) => ({ ...d, page: { ...d.page, ...values } }) });
  const preset = pagePreset(page);
  const sameMargins = ['top', 'right', 'bottom', 'left'].every((side) => margins[side] === margins.top) ? margins.top : null;
  const exprTarget = element && ['staticText', 'textField'].includes(element.type) ? element : null;
  let noteCount = 0;
  mapElements(design, (el) => { if (el.type === 'note') noteCount += 1; return el; });
  const runPath = `/generate/${report.id}.pdf${report.folder ? `?Folder=${encodeURIComponent(report.folder)}` : ''}`;
  const runLink = `${window.location.origin}${runPath}`;

  const body = {
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
      </>
    ),
    format: (
      <>
        <Group label="Select">
          <Big icon="column" label="Column" title="Select column: everything under the selected element in every band (header, detail, footer)" onClick={toDesign(() => actions.selectColumn())} disabled={none} />
          <Stack>
            <Small icon="similar" label="Similar" title="Every element of the same kind in the same band" onClick={toDesign(() => actions.selectSimilar())} disabled={none} />
            <Small icon="selectAll" label="All in band" title="Select all in the band (Ctrl+A)" onClick={toDesign(() => actions.selectAll())} />
          </Stack>
        </Group>
        <Group label="Borders">
          <Row>
            <Ico icon="bAll" title="Border on all sides" disabled={none} onClick={() => actions.setBorders('all', borderWidth)} />
            <Ico icon="bBottom" title="Bottom border only" disabled={none} onClick={() => actions.setBorders('bottom', borderWidth)} />
            <Ico icon="bTop" title="Top border only" disabled={none} onClick={() => actions.setBorders('top', borderWidth)} />
            <Ico icon="bSides" title="Left and right borders" disabled={none} onClick={() => actions.setBorders('sides', borderWidth)} />
            <Ico icon="bNone" title="No border" disabled={none} onClick={() => actions.setBorders('none')} />
          </Row>
          <Row>
            <select className="rb-select" aria-label="Border width" title="Border width used by the buttons above" value={borderWidth} onChange={(e) => setBorderWidth(Number(e.target.value))}>
              {[0.25, 0.5, 1, 1.5, 2, 3].map((w) => <option key={w} value={w}>{w} px</option>)}
            </select>
            <label className={`rb-ico rb-color ${none ? 'off' : ''}`} title="Border colour">
              <RIcon name="bAll" size={15} /><i style={{ background: element?.borderColor || '#000000' }} />
              <input type="color" disabled={none} value={element?.borderColor || '#000000'} onChange={(e) => actions.patchSelected({ borderColor: e.target.value })} />
            </label>
          </Row>
        </Group>
        <Group label="Show">
          <Big icon="hide" label={hiddenAll ? 'Show' : 'Hide'} title="Hide: keep the element in the design but never print it (Print When Expression = false). Click again to show it." onClick={() => actions.toggleHidden()} disabled={none} on={hiddenAll} />
          <Big icon="stretch" label="Stretch" title="Stretch with overflow: long data makes the row taller instead of being cut (Static Text and Text Field)" onClick={() => actions.toggleStretch()} disabled={!textEls.length} on={stretchAll} />
        </Group>
        <Group label="Number / date">
          <Stack>
            <select className="rb-select wide" aria-label="Pattern" title="Pattern of the selected Text Fields (number or date format)" disabled={!fieldEls.length}
              value={samePattern ?? ''} onChange={(e) => setPattern(e.target.value)}>
              {samePattern === null && fieldEls.length > 0 && <option value="" disabled>(different patterns)</option>}
              {NUMBER_PATTERNS.map(([value, label]) => <option key={value} value={value}>{label}{value ? `  ·  ${value}` : ''}</option>)}
              {samePattern && !NUMBER_PATTERNS.some(([value]) => value === samePattern) && <option value={samePattern}>{samePattern}</option>}
            </select>
            <span className="rb-hint">{fieldEls.length ? `${fieldEls.length} text field${fieldEls.length === 1 ? '' : 's'}` : 'Select Text Fields'}</span>
          </Stack>
        </Group>
        <Group label="Format painter">
          <Big icon="brush" label="Match format" title="Give the look of the last clicked element (font, colours, borders, alignment, padding) to the other selected elements" onClick={() => actions.matchFormat()} disabled={count < 2} />
        </Group>
        <div className="rb-note">{count ? `${count} element${count === 1 ? '' : 's'} selected — changes go to all of them` : 'Click elements on the canvas (Shift or Ctrl + click, or drag a box) — then format them all at once'}</div>
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
        <Group label="Resizing">
          <Stack>
            <span className="rb-hint" title="When you drag the edge of a box wider">Wider box moves…</span>
            <select className="rb-select wide" aria-label="Resizing a box" value={prefs.resizeMode || 'auto'}
              title={RESIZE_MODES.find(([id]) => id === (prefs.resizeMode || 'auto'))?.[2]} onChange={(e) => set({ resizeMode: e.target.value })}>
              {RESIZE_MODES.map(([id, label, help]) => <option key={id} value={id} title={help}>{label}</option>)}
            </select>
            <span className="rb-hint">Alt while dragging: the other way</span>
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
          {onResetLayout && (
            <Big icon="reset" label="Reset layout" title="Put every panel back in its first place and size. (Drag a panel's title bar to move it; its buttons minimise, maximise, float or move it.)"
              onClick={onResetLayout} />
          )}
        </Group>
        <Group label="Look">
          <div className="rb-look" ref={lookBox}>
            <Big icon="palette" label="Theme" title="Colours, light or dark, title bar, spacing" on={lookOpen} onClick={() => setLook(!lookOpen)} />
            {lookOpen && <LookPanel onDone={() => setLook(false)} style={lookPlace()} />}
          </div>
          <Big icon={ui.collapsed ? 'chevronDown' : 'chevronUp'} label={ui.collapsed ? 'Show ribbon' : 'Hide ribbon'} onClick={() => setUi((u) => ({ ...u, collapsed: !u.collapsed }))} />
        </Group>
      </>
    ),
    page: (
      <>
        <Group label="Page Setup">
          <div className="rb-look">
            <label className="rb-big" title="Page size"><RIcon name="pageSize" size={24} /><span>Size</span>
              <select className="rb-select" aria-label="Page size" value={preset} onChange={(e) => {
                const found = PAGE_PRESETS.find(([id]) => id === e.target.value);
                if (found) setPage({ width: found[2], height: found[3] });
              }}>
                {preset === 'custom' && <option value="custom">Custom ({Math.round(page.width)}×{Math.round(page.height)})</option>}
                {PAGE_PRESETS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
              </select>
            </label>
          </div>
          <Stack>
            <Small icon="portrait" label="Portrait" on={page.height >= page.width} onClick={() => page.width > page.height && setPage({ width: page.height, height: page.width })} />
            <Small icon="landscape" label="Landscape" on={page.width > page.height} onClick={() => page.height > page.width && setPage({ width: page.height, height: page.width })} />
            <Small icon="fitWidth" label="Fit width" title="Make the page just wide enough for every element (plus the margins)" onClick={() => setPage({ width: Math.max(100, contentRight(design) + (margins.left || 0) + (margins.right || 0)) })} />
          </Stack>
        </Group>
        <Group label="Margins">
          <Stack>
            {MARGINS.slice(0, 2).map(([name, value]) => <Small key={name} icon="margins" label={`${name} (${value})`} on={sameMargins === value} onClick={() => setPage({ margins: { top: value, right: value, bottom: value, left: value } })} />)}
          </Stack>
          <Stack>
            {MARGINS.slice(2).map(([name, value]) => <Small key={name} icon="margins" label={`${name} (${value})`} on={sameMargins === value} onClick={() => setPage({ margins: { top: value, right: value, bottom: value, left: value } })} />)}
          </Stack>
          <div className="rb-margins" title="Margins in points (72 = 1 inch)">
            {['top', 'bottom', 'left', 'right'].map((side) => (
              <label key={side}>{side[0].toUpperCase() + side.slice(1)}
                <input className="rb-num" type="number" min={0} max={500} value={margins[side] ?? 0}
                  onChange={(e) => { const v = Number(e.target.value); if (v >= 0 && v <= 500) setPage({ margins: { ...margins, [side]: v } }); }} />
              </label>
            ))}
          </div>
        </Group>
        <Group label="Print">
          <Stack>
            <Small icon="newPage" label="Title on new page" title="Title alone on the first page, without page header/footer" on={Boolean(page.titleNewPage)} onClick={() => setPage({ titleNewPage: !page.titleNewPage })} />
            <Small icon="newPage" label="Summary on new page" on={Boolean(page.summaryNewPage)} onClick={() => setPage({ summaryNewPage: !page.summaryNewPage })} />
          </Stack>
          <Stack>
            <Small icon="stretch" label="Float column footer" title="Column footer right under the last row" on={Boolean(page.floatColumnFooter)} onClick={() => setPage({ floatColumnFooter: !page.floatColumnFooter })} />
            <Small icon="distV" label="Ignore pagination" title="All records on one long page" on={Boolean(page.ignorePagination)} onClick={() => setPage({ ignorePagination: !page.ignorePagination })} />
          </Stack>
        </Group>
        <Group label="When no data">
          <Stack>
            <select className="rb-select wide" aria-label="When no data" title="What prints when the query returns no rows" value={page.whenNoDataType || 'Auto'} onChange={(e) => setPage({ whenNoDataType: e.target.value })}>
              <option value="Auto">Auto</option><option value="NoDataSection">No Data Section</option><option value="AllSectionsNoDetail">All Sections, No Detail</option>
              <option value="BlankPage">Blank Page</option><option value="NoPages">No Pages</option>
            </select>
            <span className="rb-hint">Excel / CSV layout</span>
            <select className="rb-select wide" aria-label="Excel / CSV layout" value={page.sheetLayout || 'table'} onChange={(e) => setPage({ sheetLayout: e.target.value })}>
              <option value="mixed">Headings exact, data as table</option><option value="grid">Exactly like the PDF</option><option value="table">Data table only</option>
            </select>
          </Stack>
        </Group>
        <Group label="More">
          <Big icon="properties" label="Page Format" title="Every page setting: Properties → Page Format" onClick={() => { dispatch({ type: 'SELECT', selection: { kind: 'report' } }); if (!panelOn('properties')) togglePanel('properties'); }} />
        </Group>
      </>
    ),
    formulas: (
      <>
        <Group label="Function Library">
          <Big icon="fx" label="Expression" title={exprTarget ? 'Open the Expression editor of the selected text' : 'Select a Static Text or Text Field first'} disabled={!exprTarget} onClick={() => onEditExpression?.(exprTarget.id)} primary />
          <Big icon="book" label="Function List" title="Every function an expression can use, with examples" onClick={() => setDialog('functions')} />
          <Big icon="fx" label="Custom Functions" title="Make your own functions, e.g. FullName(first, last), and use them in every expression" onClick={() => setDialog('customFunctions')} />
        </Group>
        <Group label="Defined Names">
          <Stack>
            <Small icon="sigma" label="New variable" title="Sum, Count, Average… of a field" onClick={() => actions.createVariable()} />
            <Small icon="variable" label="New calculated field" title="A field made from an expression" onClick={() => actions.createCalc()} />
            <Small icon="params" label="New parameter" title="A value asked when the report runs" onClick={() => actions.createParameter()} />
          </Stack>
          <Stack>
            <span className="rb-hint">{plural(design.variables.length, 'variable')}</span>
            <span className="rb-hint">{plural(design.calculatedFields.length, 'calculated field')}</span>
            <span className="rb-hint">{plural(report.parameters.length, 'parameter')}</span>
            <span className="rb-hint">{plural((design.functions || []).length, 'custom function')}</span>
          </Stack>
        </Group>
        <Group label="Calculation">
          <Stack>
            <Small icon="filter" label="Filter expression" onClick={() => onQuery('filter')} />
            <Small icon="sort" label="Sort" onClick={() => onQuery('sort')} />
            <Small icon="check" label="Check report" title="List every problem the designer can find" onClick={() => setDialog('check')} />
          </Stack>
        </Group>
      </>
    ),
    review: (
      <>
        <Group label="Proofing">
          <Big icon="check" label="Check report" title="List every problem the designer can find, with Go there" onClick={() => setDialog('check')} primary />
          <Big icon="eye" label="Preview" title="Run the report with real records" on={view === 'preview'} onClick={() => setView('preview')} />
        </Group>
        <Group label="Notes">
          <Big icon="note" label="New note" title="A note on the canvas — only visible in the designer, never printed" on={tool === 'note'} onClick={() => { if (!inDesign) setView('design'); dispatch({ type: 'TOOL', tool: tool === 'note' ? null : 'note' }); }} />
          <Stack>
            <span className="rb-hint" title="Notes are only visible in the designer, never printed">{plural(noteCount, 'note')}</span>
          </Stack>
        </Group>
        <Group label="Protect · Source">
          <Big icon={lockedAll ? 'unlock' : 'lock'} label={lockedAll ? 'Unlock' : 'Lock'} title="Lock: cannot be moved or resized (Ctrl+L)" onClick={() => actions.toggleLock()} disabled={none} on={lockedAll} />
          <Big icon="code" label="Source" title="The report definition as text (with Validate)" on={view === 'source'} onClick={() => setView('source')} />
        </Group>
      </>
    ),
    share: (
      <>
        <Group label="Share">
          <Big icon="publish" label="Publish" title="Copy this report to a folder on the report server" onClick={onPublish} primary />
          <Big icon="download" label="Save as file" title="Download one .mrd file to send to someone" onClick={onSaveFile} />
          <Big icon="download" label="JRXML" title="Save as JRXML: download a .jrxml to open in Jaspersoft Studio" onClick={onSaveJrxml} />
        </Group>
        <Group label="Link">
          <Big icon="link" label={linkCopied ? 'Copied' : 'Copy link'} title={report.isNew ? 'Save and publish the report first' : `Copy ${runLink}`} disabled={report.isNew} onClick={() => { navigator.clipboard?.writeText(runLink).then(() => setLinkCopied(true), () => {}); setTimeout(() => setLinkCopied(false), 2000); }} />
          <Stack>
            <code className="rb-link" title={report.isNew ? 'Publish the report to get its link' : `${runPath}: add X-API-Key (or sign in) to run it`}>{report.isNew ? 'Not published yet' : runPath}</code>
            <span className="rb-hint" title="Change the ending for another format; needs an API key or sign-in">.pdf · .xlsx · .csv</span>
          </Stack>
        </Group>
      </>
    ),
  };

  const tabBody = {
    home: body.home, insert: body.insert, format: body.format, arrange: body.arrange, page: body.page,
    data: <>{body.data}{body.formulas}</>,
    share: <>{body.review}{body.share}</>,
    view: body.view,
  };
  const fileItems = (
    <>
      <button type="button" role="menuitem" onClick={() => { setMenu(null); onNew(); }} title="New report (starts with the layout gallery)"><RIcon name="newDoc" /><span>New report</span></button>
      <label className="rb-menuitem" role="menuitem" title="Open a .mrd report file, or import a Jaspersoft Studio .jrxml — choose its PNG/JPEG pictures together with it (Ctrl+click)"><RIcon name="open" /><span>Open file (.mrd, .jrxml)</span>
        <input type="file" accept={MRD_WITH_IMAGES_ACCEPT} multiple hidden onChange={(e) => { const files = Array.from(e.target.files || []); e.target.value = ''; setMenu(null); onOpenFile(files); }} />
      </label>
      <button type="button" role="menuitem" onClick={() => { setMenu(null); onSave(); }} title="Save in your designer workspace. The server is not changed."><RIcon name="save" /><span>Save</span><kbd>Ctrl S</kbd></button>
      <button type="button" role="menuitem" onClick={() => { setMenu(null); onSaveFile(); }} title="Download this report as one .mrd file (query, parameters, adapter, design, images)"><RIcon name="download" /><span>Save as file (.mrd)</span></button>
      <button type="button" role="menuitem" onClick={() => { setMenu(null); onSaveJrxml?.(); }} title="Download a Jaspersoft Studio .jrxml of this report, to open and edit in Studio"><RIcon name="download" /><span>Save as JRXML (Jaspersoft)</span></button>
      <button type="button" role="menuitem" onClick={() => { setMenu(null); onPublish(); }} title="Copy this report to a folder on the report server"><RIcon name="publish" /><span>Publish to the server</span></button>
      <hr />
      <button type="button" role="menuitem" onClick={() => { setMenu(null); onTemplates(); }} title="Apply a layout template"><RIcon name="layouts" /><span>Layouts</span></button>
      <button type="button" role="menuitem" onClick={() => { setMenu(null); onImportDesign(); }} title="Rebuild an existing design — PDF, Illustrator .ai, Word, or a PNG/JPEG scan — as this report's layout"><RIcon name="importDesign" /><span>Import design (PDF, AI, Word, scan)</span></button>
      <button type="button" role="menuitem" onClick={() => { setMenu(null); onDemo(); }} title="A demo design with sample fields"><RIcon name="demo" /><span>Demo design</span></button>
    </>
  );
  const helpItems = HELP_ITEMS.map(([icon, id, label]) => (
    <button key={id} type="button" role="menuitem" onClick={() => { setMenu(null); setDialog(id); }}><RIcon name={icon} /><span>{label}</span></button>
  ));
  const tint = TABS.find(([id]) => id === ui.tab)?.[2] || 'b';

  return (
    <header className={`ribbon ${ui.collapsed ? 'collapsed' : ''} ${view !== 'design' ? 'step-view' : ''}`}>
      <div className="rb-bar" ref={menuBox}>
        <button type="button" className="rb-quick drawer-btn" aria-label="Repository explorer and outline" aria-expanded={drawer === 'left'} onClick={() => setDrawer(drawer === 'left' ? null : 'left')}><RIcon name="panelL" /></button>
        <div className="rb-filewrap">
          <button type="button" className="rb-doc" aria-haspopup="menu" aria-expanded={menu === 'file'} onClick={() => setMenu(menu === 'file' ? null : 'file')}
            title={`File menu — ${report.title || 'Untitled'}${report.folder ? ` in ${report.folder}` : ''}`}>
            <b>{report.title || 'Untitled'}</b>{dirty && <span className="rb-dirty" title="Not saved">●</span>}
            {report.folder && <small className="hide-narrow">in {report.folder}</small>}<span className="rb-caret" aria-hidden="true">▾</span>
          </button>
          {menu === 'file' && <div className="rb-menu" role="menu" aria-label="File">{fileItems}</div>}
        </div>
        <span className={`pub-chip ${status.cls}`} title={status.text}>{status.text}</span>
        <div className="rb-views" role="tablist" aria-label="View">
          {VIEWS.map(([id, label]) => (
            <button key={id} type="button" role="tab" aria-selected={view === id} className={view === id ? 'on' : ''} onClick={() => setView(id)}>{label}</button>
          ))}
        </div>
        <button type="button" className="rb-search" onClick={() => { setMenu(null); setSearching(true); }} title="Find any action by name (Ctrl+K)">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
          <span className="lbl">Search any action</span><kbd>Ctrl K</kbd>
        </button>
        <div className="rb-quickbar">
          <button type="button" className="rb-quick rb-undo" title="Undo (Ctrl+Z)" aria-label="Undo" disabled={!past.length} onClick={() => dispatch({ type: 'UNDO' })}><RIcon name="undo" /></button>
          <button type="button" className="rb-quick rb-undo" title="Redo (Ctrl+Y)" aria-label="Redo" disabled={!future.length} onClick={() => dispatch({ type: 'REDO' })}><RIcon name="redo" /></button>
          <button type="button" className="rb-check" title="Check report: list every problem the designer can find" onClick={() => setDialog('check')}><RIcon name="check" size={15} /><span className="lbl">Check</span></button>
          <button type="button" className="rb-quick" title={darkNow ? 'Switch to light mode' : 'Switch to dark mode'} aria-label={darkNow ? 'Switch to light mode' : 'Switch to dark mode'}
            onClick={() => saveLook({ mode: darkNow ? 'light' : 'dark' })}><span className="rb-moon" aria-hidden="true">{darkNow ? '☀' : '☾'}</span></button>
          <div className="rb-helpwrap">
            <button type="button" className="rb-quick" title="Help" aria-label="Help" aria-haspopup="menu" aria-expanded={menu === 'help'} onClick={() => setMenu(menu === 'help' ? null : 'help')}><RIcon name="help" /></button>
            {menu === 'help' && <div className="rb-menu right" role="menu" aria-label="Help">{helpItems}</div>}
          </div>
          <button type="button" className="rb-quick" title="Save (Ctrl+S)" aria-label="Save" onClick={onSave}><RIcon name="save" /></button>
          <button type="button" className="btn primary rb-publish" title="Copy this report to a folder on the report server" onClick={onPublish}><RIcon name="publish" size={16} /><span className="lbl">Publish</span></button>
          <button type="button" className="rb-quick drawer-btn" aria-label="Palette and properties" aria-expanded={drawer === 'right'} onClick={() => setDrawer(drawer === 'right' ? null : 'right')}><RIcon name="panelR" /></button>
        </div>
      </div>
      <div className="rb-tabrow">
        <div className="rb-tabs" role="tablist" aria-label="Ribbon" ref={tabsRow}>
          {TABS.map(([id, label, t]) => (
            <button key={id} type="button" role="tab" aria-selected={ui.tab === id} className={`tint-${t} ${ui.tab === id ? 'on' : ''}`}
              onClick={() => pick(id)} onDoubleClick={() => setUi((u) => ({ ...u, collapsed: !u.collapsed }))}>{label}</button>
          ))}
        </div>
        <button type="button" className="rb-quick rb-collapse" title={ui.collapsed ? 'Show the ribbon' : 'Hide the ribbon (double-click a tab does the same)'} aria-label={ui.collapsed ? 'Show the ribbon' : 'Hide the ribbon'}
          onClick={() => setUi((u) => ({ ...u, collapsed: !u.collapsed }))}><RIcon name={ui.collapsed ? 'chevronDown' : 'chevronUp'} /></button>
      </div>
      {!ui.collapsed && <div key={ui.tab} className={`rb-body tint-${tint}`} role="tabpanel" aria-label={TABS.find(([id]) => id === ui.tab)?.[1]}>{tabBody[ui.tab] || tabBody.home}</div>}
      {searching && (
        <>
          <div ref={searchSource} hidden>
            {TABS.map(([id, label]) => <div key={id} data-cmd-tab={label}>{tabBody[id]}</div>)}
            <div data-cmd-tab="File">{fileItems}</div>
            <div data-cmd-tab="Help">{helpItems}</div>
            <div data-cmd-tab="View">{VIEWS.map(([id, label]) => <button key={id} type="button" onClick={() => setView(id)} title={`Show the ${label} view`}><span>{label}</span></button>)}</div>
          </div>
          <CommandPalette sourceRef={searchSource} onClose={() => setSearching(false)} />
        </>
      )}
      {dialog === 'customFunctions' && (
        <FunctionsDialog functions={design.functions || []} onClose={() => setDialog(null)}
          onSave={(functions) => { dispatch({ type: 'DESIGN', update: (d) => ({ ...d, functions }) }); setDialog(null); }} />
      )}
      {dialog && dialog !== 'customFunctions' && <HelpDialog which={dialog} report={report} design={design} onGo={onGo} onOpen={setDialog} onClose={() => setDialog(null)} />}
    </header>
  );
}
