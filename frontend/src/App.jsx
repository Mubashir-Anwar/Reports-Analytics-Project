import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { api } from './api.js';
import { signOut } from './shared/session.js';
import Canvas from './Canvas.jsx';
import ContextMenu from './ContextMenu.jsx';
import ExpressionEditor from './ExpressionEditor.jsx';
import { makeActions } from './outlineActions.js';
import Preview from './Preview.jsx';
import Properties from './Properties.jsx';
import SetupForm from './SetupForm.jsx';
import DatasetQueryDialog from './DatasetQueryDialog.jsx';
import SourceView from './SourceView.jsx';
import ErrorDialog from './ErrorDialog.jsx';
import { checkSetup, explainWarning } from './errorHelp.js';
import { knownFields } from './fieldCheck.js';
import { setTeamPlace } from './shared/TeamBar.jsx';
import TableEditor from './TableEditor.jsx';
import NavBar from './shared/NavBar.jsx';
import Ribbon from './Ribbon.jsx';
import './ribbon.css';
import TemplateGallery from './TemplateGallery.jsx';
import { AdaptersPanel, Outline, Palette, ServerPanel, WorkspacePanel } from './panels.jsx';
import { DockSide, FloatingPanel, floatDropProps, useDockLayout, useNarrow } from './DockLayout.jsx';
import { ZOOM_LIMITS, bandWidth, clampZoom, emptyDesign, findElement, getBand, setBand } from './designModel.js';
import ComponentWizard from './ComponentWizard.jsx';
import PublishDialog from './PublishDialog.jsx';
import { buildMrd, downloadText, fileKind, parseMrd, splitReportFiles } from './mrd.js';
import { blankReport, initialState, reducer, reportPayload } from './store.js';
import { readClipboard } from './arrange.js';

const DEMO_COLUMNS = ['Identifier', 'Branch', 'Name', 'Amount', 'LastUpdateDateTime'];

/** Shown when the signed-in account may not use the designer (the page itself already checks; this covers a session that changed). */
function NoAccess({ text }) {
  return (
    <div className="login">
      <h2>No access to the Designer</h2>
      <p className="muted">{text}</p>
      <button className="btn primary" onClick={signOut}>Sign out and use another account</button>
    </div>
  );
}

export default function App() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [ready, setReady] = useState(false);
  const [denied, setDenied] = useState('');
  const [tab, setTab] = useState('setup');
  const [leftTab, setLeftTab] = useState('workspace');
  const [drawer, setDrawer] = useState(null); // small screens: 'left' | 'right' panel shown over the canvas
  // wide screens: each panel can be hidden on its own (Ribbon → View → Panels) for a bigger canvas; kept per browser
  const [panels, setPanels] = useState(() => {
    const all = { explorer: true, outline: true, palette: true, properties: true };
    try {
      const saved = JSON.parse(localStorage.getItem('designer-panels') || '{}');
      if ('left' in saved || 'right' in saved) {          // saved by the earlier two-button version
        return { explorer: saved.left !== false, outline: saved.left !== false, palette: saved.right !== false, properties: saved.right !== false };
      }
      return { ...all, ...saved };
    } catch { return all; }
  });
  useEffect(() => { try { localStorage.setItem('designer-panels', JSON.stringify(panels)); } catch { /* ignore */ } }, [panels]);
  const dock = useDockLayout();
  const narrow = useNarrow();
  const floatingNames = narrow ? [] : Object.keys(dock.layout.floating).filter((name) => panels[name]);
  const sideShown = (side) => dock.layout.order[side].some((name) => panels[name])
    || (narrow && Object.entries(dock.layout.floating).some(([name, f]) => panels[name] && (f.from || 'right') === side));
  const showLeft = sideShown('left');
  const showRight = sideShown('right');
  const hidePanel = (name) => setPanels((p) => ({ ...p, [name]: false }));
  const [publishing, setPublishing] = useState(null); // { folders, folder, replace, busy }
  const [clash, setClash] = useState(null); // Save hit an ID already in the workspace: { id, newId, error, busy }
  const [zoom, setZoom] = useState(1);
  const [message, setMessage] = useState({ text: 'Ready', kind: '' });
  const [refreshKey, setRefreshKey] = useState(0);
  const [importInfo, setImportInfo] = useState(null);

  const [errorPopup, setErrorPopup] = useState('');
  const [setupFocus, setSetupFocus] = useState(null); // where an error's "Go there" points: Setup scrolls to it and highlights it // errors open a popup with a hint (and stay in the status line)
  const notify = useCallback((text, kind = '') => {
    setMessage({ text, kind });
    if (kind === 'error' && text) setErrorPopup(String(text));
  }, []);

  // Canvas view options (grid, snap, smart guides, rulers) are kept per browser, like Studio's preferences.
  const [canvasPrefs, setCanvasPrefs] = useState(() => {
    const defaults = { showGrid: true, snapGrid: false, gridSize: 10, smartGuides: true, showRulers: true };
    try { return { ...defaults, ...JSON.parse(localStorage.getItem('reporting-designer-canvas') || '{}') }; } catch { return defaults; }
  });
  useEffect(() => { try { localStorage.setItem('reporting-designer-canvas', JSON.stringify(canvasPrefs)); } catch { /* ignore */ } }, [canvasPrefs]);

  // Right-click menus (Outline and canvas) read the newest state through this ref.
  const stateRef = useRef(state);
  stateRef.current = state;
  // dialogs and tabs the Outline / canvas menus can open (read through a ref, so the menus always call the newest ones)
  const uiRef = useRef({});
  const actions = useMemo(() => makeActions({ get: () => stateRef.current, dispatch, notify, ui: uiRef }), [notify]);
  const [queryTab, setQueryTab] = useState(null); // Dataset and Query dialog: null = closed, else its tab
  const [templates, setTemplates] = useState(false);
  const [menu, setMenu] = useState(null);
  const openMenu = useCallback((event, items) => setMenu({ x: event.clientX, y: event.clientY, items }), []);
  const closeMenu = useCallback(() => setMenu(null), []);

  // Palette → Percentage: ask for the field, then add "<field>_TOTAL" (Sum) and a Text Field field / total
  const [percent, setPercent] = useState(null); // { bandKey, element, field }
  const onPercentage = useCallback((bandKey, element) => setPercent({ bandKey, element, field: '' }), []);
  const confirmPercentage = () => {
    const { bandKey, element, field } = percent;
    const base = `${field.replace(/[^A-Za-z0-9_]/g, '_').replace(/^([^A-Za-z_])/, '_$1').slice(0, 50)}_TOTAL`;
    const existing = state.design.variables.find((v) => v.field === field && v.calculation === 'Sum' && v.resetType === 'Report');
    const taken = new Set([...state.design.variables.map((v) => v.name), 'PAGE_NUMBER', 'PAGE_COUNT', 'REPORT_COUNT', 'CURRENT_DATE']);
    let name = existing ? existing.name : base;
    for (let n = 2; !existing && taken.has(name); n += 1) name = `${base}${n}`;
    const textField = {
      ...element, type: 'textField', expression: `=$F{${field}} / $V{${name}}`, pattern: '0.00%', blankWhenNull: true,
      evaluationTime: 'Auto', align: 'right',
    };
    dispatch({
      type: 'DESIGN',
      update: (d) => {
        const band = getBand(d, bandKey);
        const variables = existing ? d.variables : [...d.variables, { name, calculation: 'Sum', field, resetType: 'Report', resetGroup: '' }];
        return setBand({ ...d, variables }, bandKey, { ...band, height: Math.max(band.height, Math.ceil(textField.y + textField.height)), elements: [...band.elements, textField] });
      },
    });
    dispatch({ type: 'SELECT', selection: { kind: 'element', id: textField.id } });
    notify(`Percentage of ${field} added${existing ? '' : ` with the variable ${name} (report total)`}`, 'ok');
    setPercent(null);
  };

  // Table / Crosstab / Subreport wizards (open when the component is dropped on a band)
  const [wizard, setWizard] = useState(null); // { bandKey, element }
  const onWizard = useCallback((bandKey, element) => setWizard({ bandKey, element }), []);
  const finishWizard = (element) => {
    const { bandKey } = wizard;
    dispatch({
      type: 'DESIGN',
      update: (d) => {
        const band = getBand(d, bandKey);
        return setBand(d, bandKey, { ...band, height: Math.max(band.height, Math.ceil(element.y + element.height)), elements: [...band.elements, element] });
      },
    });
    dispatch({ type: 'SELECT', selection: { kind: 'element', id: element.id } });
    notify(`${element.type === 'table' ? 'Table' : element.type === 'crosstab' ? 'Crosstab' : 'Subreport'} added — fine-tune it in Properties; Preview shows it with the records`, 'ok');
    setWizard(null);
  };

  // Table editor (double-click a Table on the canvas, or Properties → Table → Open the table editor)
  const [tableId, setTableId] = useState(null);

  // Expression Editor (double-click a Text Field / Static Text on the canvas or in the Outline)
  const [exprId, setExprId] = useState(null);
  const applyLayout = (next, name) => {
    dispatch({ type: 'DESIGN', update: () => next });
    dispatch({ type: 'SELECT', selection: { kind: 'report' } });
    setTemplates(false);
    setTab('design');
    notify(`Layout “${name}” applied — change anything on the canvas (Ctrl+Z undoes it)`, 'ok');
  };
  const editExpression = useCallback((id) => { dispatch({ type: 'SELECT', selection: { kind: 'element', id } }); setExprId(id); }, []);
  const saveExpression = (values) => {
    const found = findElement(state.design, exprId);
    if (found) {
      dispatch({
        type: 'DESIGN',
        update: (d) => {
          const band = getBand(d, found.bandKey);
          return setBand(d, found.bandKey, {
            ...band,
            elements: band.elements.map((el) => {
              if (el.id !== exprId) return el;
              const next = { ...el, ...values };
              if (values.type === 'textField') delete next.text;
              return next;
            }),
          });
        },
      });
      if (values.type === 'textField') notify('The Static Text now shows data, so it became a Text Field', 'ok');
    }
    setExprId(null);
  };

  const connect = useCallback(async () => {
    try { await api.folders(); setDenied(''); setReady(true); notify('Connected to the server', 'ok'); }
    catch (error) {
      setReady(false);
      if (error.status === 403) setDenied(error.message); // signed in, but without the Designer right
      else if (error.status !== 401) notify(error.message, 'error'); // 401: api.js already sends the browser to /login
    }
  }, [notify]);
  useEffect(() => { connect(); }, [connect]);

  const load = (item, status) => {
    const { design, layout, workspace, ...rest } = item; // eslint-disable-line no-unused-vars
    dispatch({
      type: 'LOAD',
      report: {
        isNew: false, id: item.id, title: item.title || item.id, description: item.description || '', folder: item.folder || 'General',
        connectionId: item.connectionId || 'default', collection: item.collection || '',
        queryLanguage: item.queryLanguage || 'mongosh', querySource: item.querySource || '',
        parameters: item.parameters || [], columns: item.columns || [], extra: rest, status: status || workspace || null,
      },
      design: design || emptyDesign(),
    });
    setTab(design ? 'design' : 'setup');
  };

  /** Open a report from the designer's workspace. */
  const open = async (summary) => {
    if (state.dirty && !window.confirm('You have unsaved changes. Discard them and open another report?')) return;
    try { load(await api.workspaceReport(summary.id, summary.folder)); notify(`Opened ${summary.id} from your workspace`, 'ok'); }
    catch (error) { notify(`Could not open the report: ${error.message}`, 'error'); }
  };

  /** Open a published report (<folder>/<ID>): its workspace copy in the same folder if there is one, else a new
   *  workspace copy of the server version. */
  const openFromServer = async (item) => {
    if (state.dirty && !window.confirm('You have unsaved changes. Discard them and open another report?')) return;
    try {
      const workspaceCopy = await api.workspaceReport(item.id, item.folder).catch((error) => { if (error.status === 404) return null; throw error; });
      if (workspaceCopy) {
        load(workspaceCopy);
        setLeftTab('workspace');
        notify(`${item.id} is already in your workspace — opened that copy. Publish it to update the server.`, 'ok');
        return;
      }
      const published = await api.report(item.id, item.folder);
      const saved = await api.saveWorkspace({ ...published, design: published.design }, true);
      load(published, saved.report);
      setRefreshKey((n) => n + 1);
      setLeftTab('workspace');
      notify(`Copied ${item.id} from the server into your workspace`, 'ok');
    } catch (error) { notify(`Could not open the server report: ${error.message}`, 'error'); }
  };

  /** Open file…: a .mrd report file, or a Jaspersoft Studio .jrxml (converted). Nothing is saved until Save. */
  const openFile = async (chosen) => {
    // one report file, or a .jrxml together with the pictures it uses
    const { report: file, images, skipped } = await splitReportFiles(chosen instanceof File ? [chosen] : chosen);
    if (!file) { notify('Choose a .mrd or .jrxml report file (pictures can be chosen together with a .jrxml)', 'error'); return; }
    if (skipped.length) notify(`Left out (over 2.4 MB): ${skipped.join(', ')}`, 'error');
    const text = await file.text();
    const kind = fileKind(text);
    if (kind === 'jrxml') { importJrxml(text, file.name, images); return; }
    if (kind !== 'mrd') { notify(`${file.name} is not a .mrd or .jrxml report file`, 'error'); return; }
    if (state.dirty && !window.confirm('You have unsaved changes. Opening a file will discard them. Continue?')) return;
    try {
      const definition = parseMrd(text);
      const inWorkspace = await api.workspaceReport(definition.id, definition.folder || 'General').then(() => true).catch((error) => { if (error.status === 404) return false; throw error; });
      if (inWorkspace && !window.confirm(`${definition.id} is already in your workspace. Open the file anyway? When you Save, the file's version replaces the workspace copy.`)) return;
      load(definition);
      dispatch({ type: 'REPORT', patch: { isNew: !inWorkspace, status: null } });
      notify(`Opened ${file.name} — ${inWorkspace ? 'Save replaces your workspace copy' : 'Save to add it to your workspace'}`, 'ok');
    } catch (error) { notify(`Could not open ${file.name}: ${error.message}`, 'error'); }
  };

  /** Save as file: the whole report (query, parameters, adapter, design, images) as one .mrd file. */
  const saveFile = () => {
    const payload = reportPayload(state);
    downloadText(buildMrd(payload), `${payload.id || 'report'}.mrd`);
    notify(`Downloaded ${payload.id || 'report'}.mrd — open it again with “Open file…”, or upload it on /server (Add Resource)`, 'ok');
  };

  // /designer?open=<ID>&folder=<folder> (from /server → "Open in Designer"): open that published report once connected
  useEffect(() => {
    if (!ready) return;
    const query = new URLSearchParams(window.location.search);
    const id = query.get('open');
    if (!id) return;
    window.history.replaceState(null, '', window.location.pathname);
    openFromServer({ id, folder: query.get('folder') || undefined });
  }, [ready]); // eslint-disable-line react-hooks/exhaustive-deps -- only once, right after connecting

  const importJrxml = async (text, name, images = {}) => {
    if (state.dirty && !window.confirm('You have unsaved changes. Importing a JRXML will discard them. Continue?')) return;
    const file = { name };
    try {
      const result = await api.importJrxml(text, images);
      dispatch({ type: 'LOAD', report: { ...blankReport(), ...result.report, extra: {} }, design: result.design });
      dispatch({ type: 'REPORT', patch: {} });
      setImportInfo({ name: file.name, warnings: result.warnings, places: result.warningElements || {}, hints: result.hints, columns: result.report.columns.length, parameters: result.report.parameters.length });
      setTab('design');
      notify(`JRXML imported: ${result.report.id} — review it, then Save`, 'ok');
    } catch (error) { notify(`JRXML import failed: ${error.message}`, 'error'); }
  };

  /** Save inside the designer workspace. The server is not changed; that needs "Publish to server". */
  const save = async () => {
    const { report } = state;
    if (!report.id) {
      notify('Give the report an ID first (Setup tab)', 'error');
      setTab('setup');
      return null;
    }
    try {
      return await saveAs(report.id, report.isNew);
    } catch (error) {
      if (error.status === 409 && report.isNew) { // the ID is already in the workspace: ask replace / new ID
        setClash({ id: report.id, newId: `${report.id}_copy`, error: '', busy: false });
        return null;
      }
      notify(`Save failed: ${error.message}`, 'error');
      return null;
    }
  };
  /** Save the open report under `id` (create=true refuses an ID that is already in the workspace). */
  const saveAs = async (id, create) => {
    // the draft was saved in status.folder before; Setup may have moved it to another folder
    const result = await api.saveWorkspace({ ...reportPayload(state), id }, create, state.report.status?.folder);
    dispatch({ type: 'SAVED', id, status: result.report });
    setRefreshKey((n) => n + 1);
    const where = result.report.onServer ? 'The server still has the last published version.' : 'Not on the server yet.';
    notify(result.readyToPublish
      ? `Saved in your workspace as ${id} ✓  ${where} Use “Publish to server” to update it.`
      : `Saved in your workspace as a draft. Before publishing: ${result.problem}`, result.readyToPublish ? 'ok' : '');
    return result;
  };
  const resolveClash = async (replace) => {
    const id = replace ? clash.id : clash.newId.trim();
    if (!replace && !/^[A-Za-z0-9_-]{1,80}$/.test(id)) { setClash({ ...clash, error: 'Use letters, numbers, _ and - only (no spaces).' }); return; }
    setClash({ ...clash, busy: true, error: '' });
    try {
      await saveAs(id, !replace);
      setClash(null);
    } catch (error) {
      setClash({ ...clash, busy: false, error: error.status === 409 ? `${id} is also already in your workspace — choose another ID.` : error.message });
    }
  };

  /** Publish: save to the workspace if needed, then copy the report into a server folder. */
  const startPublish = async () => {
    let saved = null;
    if (state.dirty || state.report.isNew) {
      saved = await save();
      if (!saved) return;
      if (!saved.readyToPublish) { setTab('setup'); return; }
    }
    try {
      const repo = await api.folders();
      const status = saved?.report || state.report.status || {}; // the fresh save knows whether it is on the server
      setPublishing({ folders: repo.folders, folder: status.publishedFolder || state.report.folder || 'General', replace: false, busy: false, onServer: Boolean(status.onServer) });
    } catch (error) { notify(`Could not read the server folders: ${error.message}`, 'error'); }
  };
  const confirmPublish = async () => {
    const { report } = state;
    setPublishing((p) => ({ ...p, busy: true }));
    try {
      const result = await api.publish(report.id, { folder: publishing.folder, replace: publishing.replace }, report.status?.folder || report.folder);
      dispatch({ type: 'STATUS', status: result.workspace });
      setPublishing(null);
      setRefreshKey((n) => n + 1);
      notify(`Published to the server ✓  ${publishing.folder} / ${report.id}  —  /generate/${report.id}.pdf?Folder=${publishing.folder}`, 'ok');
    } catch (error) {
      if (error.status === 409) setPublishing((p) => ({ ...p, busy: false, onServer: true, error: error.message }));
      else { setPublishing((p) => ({ ...p, busy: false, error: error.message })); }
    }
  };

  /** File → New: an empty report (in the given folder), then the layout gallery. */
  const newReport = (folder) => {
    if (state.dirty && !window.confirm('You have unsaved changes. Discard them?')) return;
    dispatch({ type: 'NEW', folder });
    setTab('setup');
    setTemplates(true);
  };

  const loadDemo = () => {
    dispatch({ type: 'NEW' });
    dispatch({ type: 'REPORT', patch: { title: 'Demo report', id: 'DEMO_REPORT', collection: 'demo', columns: DEMO_COLUMNS, parameters: [{ name: 'StartDate', type: 'date', label: 'Start date', required: true }, { name: 'EndDate', type: 'date', label: 'End date', required: true }], querySource: '' } });
    dispatch({ type: 'DESIGN', update: () => demoDesign() });
    setTab('design');
  };

  // keyboard: Ctrl+S save, Ctrl+Z/Y, Delete, arrows
  useEffect(() => {
    const onKey = (event) => {
      const tag = (event.target.tagName || '').toLowerCase();
      const typing = ['input', 'textarea', 'select'].includes(tag);
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); save(); return; }
      if (typing) return;
      // Del in the Outline deletes a band, parameter, variable, calculated field or group (also outside the Design tab)
      if (event.key === 'Delete' && ['band', 'param', 'variable', 'calc', 'group'].includes(state.selection.kind) && event.target.closest?.('.outline, .canvas-scroll')) {
        event.preventDefault();
        actions.deleteSelection(state.selection);
        return;
      }
      if (event.key === 'Delete' && state.selection.kind === 'element' && tab !== 'design' && event.target.closest?.('.outline')) {
        event.preventDefault();
        actions.deleteSelection(state.selection);
        return;
      }
      if (tab !== 'design') return;
      // zoom: Ctrl + / Ctrl - / Ctrl 0 (100%)
      if ((event.ctrlKey || event.metaKey) && ['+', '=', 'Add'].includes(event.key)) { event.preventDefault(); setZoom((z) => clampZoom(z + ZOOM_LIMITS.step)); return; }
      if ((event.ctrlKey || event.metaKey) && ['-', '_', 'Subtract'].includes(event.key)) { event.preventDefault(); setZoom((z) => clampZoom(z - ZOOM_LIMITS.step)); return; }
      if ((event.ctrlKey || event.metaKey) && event.key === '0') { event.preventDefault(); setZoom(1); return; }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') { event.preventDefault(); dispatch({ type: event.shiftKey ? 'REDO' : 'UNDO' }); return; }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'y') { event.preventDefault(); dispatch({ type: 'REDO' }); return; }
      const { selection, design } = state;
      if (event.key === 'Escape') {
        if (state.tool) dispatch({ type: 'TOOL', tool: null });
        else if (selection.kind === 'element') { const found = findElement(design, selection.id); dispatch({ type: 'SELECT', selection: found ? { kind: 'band', key: found.bandKey } : { kind: 'report' } }); }
        return;
      }
      const ctrl = event.ctrlKey || event.metaKey;
      const key = event.key.toLowerCase();
      // clipboard and selection shortcuts, like Jaspersoft Studio / Photoshop
      if (ctrl && key === 'v') { event.preventDefault(); actions.paste(); return; }
      if (ctrl && key === 'a') { event.preventDefault(); actions.selectAll(); return; }
      if (selection.kind !== 'element' || !findElement(design, selection.id)) return;
      if (ctrl && key === 'c') { event.preventDefault(); actions.copy(); return; }
      if (ctrl && key === 'x') { event.preventDefault(); actions.cut(); return; }
      if (ctrl && key === 'd') { event.preventDefault(); actions.duplicateSelected(); return; }
      if (ctrl && key === 'l') { event.preventDefault(); actions.toggleLock(); return; }
      if (event.key === 'Delete' || event.key === 'Backspace') { event.preventDefault(); actions.removeSelected(); return; }
      // arrows: 1px, Shift = 10px, Ctrl = one grid step
      const step = ctrl ? canvasPrefs.gridSize : (event.shiftKey ? 10 : 1);
      const delta = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[event.key];
      if (delta) { event.preventDefault(); actions.nudge(...delta); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  uiRef.current = {
    openQuery: (which) => setQueryTab(which || 'query'),
    openTemplates: () => setTemplates(true),
    showSource: () => setTab('source'),
    preview: () => setTab('preview'),
    saveFile,
  };

  // the team bar shows others which report this is, and warns when someone else has the same one open
  useEffect(() => { setTeamPlace({ place: state.report.id, title: state.report.id ? `${state.report.title || state.report.id}` : '' }); }, [state.report.id, state.report.title]);
  if (denied) return <NoAccess text={denied} />;
  if (!ready) return <div className="login"><p>Connecting to the server…</p></div>;

  /** The fix buttons of the error popup. */
  const errorAction = (id) => {
    const language = { 'use-sql': 'sql', 'use-json': 'aggregation-json', 'use-mongosh': 'mongosh' }[id];
    if (language) {
      dispatch({ type: 'REPORT', patch: { queryLanguage: language, ...(language === 'sql' ? { collection: '' } : {}) } });
      setQueryTab(null);
      setTab('setup');
      notify(language === 'sql' ? 'Query language: SQL — choose a SQL data adapter and write a SELECT' : `Query language: ${language === 'mongosh' ? 'Mongo Shell' : 'Aggregation JSON'}`, 'ok');
    } else if (id === 'setup') { setQueryTab(null); setTab('setup'); }
    else if (id === 'adapters') { setLeftTab('adapters'); setDrawer('left'); }
    else if (id === 'parameters') setQueryTab('parameters');
    else if (id === 'design') { setQueryTab(null); setTab('design'); }
    else if (id.startsWith('add-param:')) {
      const name = id.slice('add-param:'.length).trim();
      const { parameters } = stateRef.current.report;
      if (!parameters.some((p) => p.name === name)) {
        dispatch({ type: 'REPORT', patch: { parameters: [...parameters, { name, type: 'string', label: name, required: false }] } });
      }
      setQueryTab(null); setTab('setup');
      notify(`Parameter ${name} added (text, not required) — set its type in Setup → 3 Parameters if it is a date or number, then Run again.`, 'ok');
    } else if (id.startsWith('make-optional:')) {
      const names = id.slice('make-optional:'.length).split(',').map((n) => n.trim());
      const { parameters } = stateRef.current.report;
      const hit = parameters.filter((p) => names.includes(p.name) || names.includes(p.label));
      dispatch({ type: 'REPORT', patch: { parameters: parameters.map((p) => (hit.includes(p) ? { ...p, required: false } : p)) } });
      notify(`${hit.map((p) => p.name).join(', ')} may now stay empty — press Run again. Save to keep it.`, 'ok');
    }
  };

  /** Add a field name to Setup → Fields (from the Expression Editor or the Fields list). */
  const addField = (name) => {
    const clean = String(name || '').trim();
    if (!clean) return;
    if (state.report.columns.includes(clean)) { notify(`${clean} is already in Fields`, 'ok'); return; }
    dispatch({ type: 'REPORT', patch: { columns: [...state.report.columns, clean] } });
    notify(`Added ${clean} to Fields (Setup → Fields). Save to keep it.`, 'ok');
  };

  /** "Go there" of the error popup: open the tab / section and highlight the field, row or query line. */
  const goToProblem = (focus) => {
    if (focus.target === 'design') {
      setQueryTab(null);
      setTab('design');
      if (focus.elementId && findElement(state.design, focus.elementId)) dispatch({ type: 'SELECT', selection: { kind: 'element', id: focus.elementId } });
      if (focus.note) notify(focus.note, 'ok');
      return;
    }
    if (focus.target === 'datasets') { setQueryTab('datasets'); return; }
    setQueryTab(null);
    setTab('setup');
    setSetupFocus({ ...focus, nonce: Date.now() });
  };

  const { report, design, selection, tool, past, future, dirty } = state;
  const renderPanel = (name) => {
    if (name === 'explorer') {
      return (
        <>
          <div className="dock-tabs">
            <button className={leftTab === 'workspace' ? 'on' : ''} onClick={() => setLeftTab('workspace')} title="Reports saved inside the designer">Workspace</button>
            <button className={leftTab === 'server' ? 'on' : ''} onClick={() => setLeftTab('server')} title="Published reports on the report server">Server</button>
            <button className={leftTab === 'adapters' ? 'on' : ''} onClick={() => setLeftTab('adapters')}>Data Adapters</button>
          </div>
          {leftTab === 'workspace' && <WorkspacePanel report={report} onOpen={open} onNew={newReport} notify={notify} refreshKey={refreshKey} onChanged={() => setRefreshKey((n) => n + 1)} />}
          {leftTab === 'server' && <ServerPanel report={report} onOpenFromServer={openFromServer} openMenu={openMenu} notify={notify} refreshKey={refreshKey} onChanged={() => setRefreshKey((n) => n + 1)} />}
          {leftTab === 'adapters' && <AdaptersPanel notify={notify} openMenu={openMenu} refreshKey={refreshKey} onChanged={() => setRefreshKey((n) => n + 1)} />}
        </>
      );
    }
    if (name === 'outline') return <Outline report={report} design={design} selection={selection} dispatch={dispatch} actions={actions} openMenu={openMenu} onDetectFields={() => setTab('setup')} onEditExpression={editExpression} />;
    if (name === 'palette') return <Palette tool={tool} dispatch={dispatch} />;
    return <Properties state={state} dispatch={dispatch} notify={notify} actions={actions} onEditExpression={editExpression} onOpenQuery={(t) => setQueryTab(t || 'query')} onOpenTable={setTableId} />;
  };
  const widths = { '--left-w': `${showLeft ? dock.layout.width.left : 0}px`, '--right-w': `${showRight ? dock.layout.width.right : 0}px` };
  return (
    <div className="app">
      <NavBar current="/designer" />
      <Ribbon
        state={state} dispatch={dispatch} actions={actions} zoom={zoom} setZoom={setZoom} prefs={canvasPrefs} setPrefs={setCanvasPrefs}
        view={tab} setView={setTab} drawer={drawer} setDrawer={setDrawer} panels={panels} setPanels={setPanels}
        hasClipboard={Boolean(readClipboard()?.items?.length)}
        onNew={() => newReport(state.report.folder)} onOpenFile={openFile} onSave={save} onSaveFile={saveFile} onPublish={startPublish}
        onDemo={loadDemo} onTemplates={() => setTemplates(true)} onQuery={(which) => setQueryTab(which || 'query')}
        onAdapters={() => { setLeftTab('adapters'); setPanels((p) => ({ ...p, explorer: true })); setDrawer('left'); }}
        onResetLayout={() => { dock.reset(); setPanels({ explorer: true, outline: true, palette: true, properties: true }); }}
        panelSide={dock.sideOf} onEditExpression={editExpression} onGo={goToProblem}
      />

      <div className={`workspace ${drawer ? `show-${drawer}` : ''} ${showLeft ? '' : 'hide-left'} ${showRight ? '' : 'hide-right'}`} style={widths}>
        {drawer && <button type="button" className="drawer-backdrop" aria-label="Close the panel" onClick={() => setDrawer(null)} />}
        <DockSide side="left" ops={dock} visible={panels} render={renderPanel} onHide={hidePanel} narrow={narrow} />

        <main className="center" {...floatDropProps(dock)}>
          <div className="tabs">
            {[['setup', 'Setup'], ['design', 'Design'], ['source', 'Source'], ['preview', 'Preview']].map(([id, label]) => (
              <button key={id} className={tab === id ? 'on' : ''} onClick={() => setTab(id)}>{label}</button>
            ))}
          </div>
          <div className="stage">
            {tab === 'setup' && <SetupForm state={state} dispatch={dispatch} notify={notify} onTemplates={() => setTemplates(true)} refreshKey={refreshKey} focus={setupFocus} onFocus={goToProblem} onClearFocus={() => setSetupFocus(null)} />}
            {tab === 'design' && <Canvas knownFields={report.columns.length ? knownFields(report, design) : null} design={design} selection={selection} tool={tool} zoom={zoom} setZoom={setZoom} dispatch={dispatch} actions={actions} openMenu={openMenu} onEditExpression={editExpression} onPercentage={onPercentage} onWizard={onWizard} onOpenTable={setTableId} prefs={canvasPrefs} setPrefs={setCanvasPrefs} hasClipboard={Boolean(readClipboard()?.items?.length)} onQuery={() => setQueryTab('query')} onTemplates={() => setTemplates(true)} showToolbar={false} />}
            {tab === 'source' && <SourceView state={state} dispatch={dispatch} notify={notify} />}
            {tab === 'preview' && <Preview state={state} notify={notify} />}
          </div>
        </main>

        <DockSide side="right" ops={dock} visible={panels} render={renderPanel} onHide={hidePanel} narrow={narrow} />
      </div>
      {floatingNames.map((name) => <FloatingPanel key={name} name={name} ops={dock} render={renderPanel} onHide={hidePanel} />)}
      <footer className={`status ${message.kind}`}>{message.text}</footer>
      {errorPopup && <ErrorDialog message={errorPopup} problems={checkSetup(state.report, state.design)} onClose={() => setErrorPopup('')} onAction={errorAction} onGo={goToProblem} />}
      <ContextMenu menu={menu} onClose={closeMenu} />
      {percent && (
        <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && setPercent(null)}>
          <div className="modal" role="dialog" aria-label="Percentage">
            <h3>Percentage</h3>
            <p className="muted">Shows the field of each record as a percentage of its total over the whole report (Evaluation Time Auto, pattern 0.00%).</p>
            <label className="modal-field">Field
              <select autoFocus value={percent.field} onChange={(e) => setPercent({ ...percent, field: e.target.value })}>
                <option value="">Choose a numeric field…</option>
                {[...report.columns, ...design.calculatedFields.map((c) => c.name)].map((f) => <option key={f} value={f}>{f}</option>)}
              </select>
            </label>
            <div className="row">
              <button className="btn" onClick={() => setPercent(null)}>Cancel</button>
              <button className="btn primary" disabled={!percent.field} onClick={confirmPercentage}>OK</button>
            </div>
          </div>
        </div>
      )}
      {tableId && <TableEditor state={state} dispatch={dispatch} elementId={tableId} onClose={() => setTableId(null)} onOpenDatasets={() => { setTableId(null); setQueryTab('datasets'); }} />}
      {queryTab && <DatasetQueryDialog state={state} dispatch={dispatch} notify={notify} refreshKey={refreshKey} initialTab={queryTab} onClose={() => setQueryTab(null)} />}
      {templates && <TemplateGallery report={report} design={design} onApply={applyLayout} onClose={() => setTemplates(false)} />}
      {wizard && <ComponentWizard wizard={wizard} report={report} design={design} bandWidth={bandWidth(design)} onCancel={() => setWizard(null)} onDone={finishWizard} />}
      {exprId && findElement(design, exprId) && (
        <ExpressionEditor element={findElement(design, exprId).element} report={report} design={design} onSave={saveExpression} onClose={() => setExprId(null)} onAddField={addField} />
      )}
      {publishing && <PublishDialog publishing={publishing} setPublishing={setPublishing} report={report} design={design} onConfirm={confirmPublish} />}
      {clash && (
        <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && !clash.busy && setClash(null)}>
          <form className="modal clash-dialog" role="dialog" aria-label="Report ID already exists" onSubmit={(e) => { e.preventDefault(); resolveClash(false); }}>
            <h3>⚠ {clash.id} is already in your workspace</h3>
            <p>A report with the ID <b>{clash.id}</b> was saved before (for example, you opened the same file or JRXML again). What should happen?</p>
            <div className="clash-choice">
              <b>Replace it</b>
              <p className="muted">The saved copy of {clash.id} is overwritten with what is open now. The server copy (if published) does not change until you publish.</p>
              <button type="button" className="btn danger" disabled={clash.busy} onClick={() => resolveClash(true)}>Replace {clash.id}</button>
            </div>
            <div className="clash-choice">
              <b>Keep both — save this one with a new ID</b>
              <label className="modal-field">New report ID
                <input autoFocus value={clash.newId} disabled={clash.busy} onChange={(e) => setClash({ ...clash, newId: e.target.value, error: '' })} />
              </label>
              <button type="submit" className="btn primary" disabled={clash.busy || !clash.newId.trim()}>Save as {clash.newId.trim() || '…'}</button>
            </div>
            {clash.error && <p className="error-text" role="alert">{clash.error}</p>}
            <div className="row"><button type="button" className="btn" disabled={clash.busy} onClick={() => setClash(null)}>Cancel — don't save</button></div>
          </form>
        </div>
      )}
      {importInfo && <ImportDialog info={importInfo} onGo={(focus) => { setImportInfo(null); goToProblem(focus); }} onClose={() => setImportInfo(null)} />}
    </div>
  );
}

function demoDesign() {
  const d = emptyDesign();
  const text = (extra) => ({ id: `d-${Math.random().toString(36).slice(2, 8)}`, type: 'textField', x: 0, y: 1, width: 100, height: 16, fontSize: 9, bold: false, italic: false, align: 'left', color: '#000000', backColor: null, borderColor: null, borderWidth: 0, pattern: '', blankWhenNull: true, ...extra });
  const label = (extra) => ({ ...text({}), type: 'staticText', bold: true, backColor: '#e2e8f0', y: 2, height: 18, ...extra });
  d.groups = [{ name: 'Branch', field: 'Branch' }];
  d.variables = [{ name: 'BranchTotal', calculation: 'Sum', field: 'Amount', resetType: 'Group', resetGroup: 'Branch' }, { name: 'GrandTotal', calculation: 'Sum', field: 'Amount', resetType: 'Report', resetGroup: '' }];
  d.bands.title = { height: 50, elements: [text({ type: 'staticText', text: 'Branch-wise Amount Report', x: 0, y: 8, width: 535, height: 28, fontSize: 18, bold: true })] };
  d.bands.pageHeader = { height: 20, elements: [text({ expression: 'Generated: $V{CURRENT_DATE}', pattern: 'dd/MM/yyyy', x: 335, width: 200, align: 'right', fontSize: 8, color: '#64748b' })] };
  d.bands.columnHeader = { height: 22, elements: [label({ type: 'staticText', text: 'Identifier', x: 0, width: 110 }), label({ type: 'staticText', text: 'Name', x: 110, width: 170 }), label({ type: 'staticText', text: 'Date', x: 280, width: 120 }), label({ type: 'staticText', text: 'Amount', x: 400, width: 135, align: 'right' })] };
  d.bands.groupHeaders.Branch = { height: 20, elements: [text({ expression: 'Branch: $F{Branch}', width: 535, bold: true, backColor: '#f1f5f9', y: 2 })] };
  d.bands.detail = { height: 18, elements: [text({ expression: '$F{Identifier}', width: 110 }), text({ expression: '$F{Name}', x: 110, width: 170 }), text({ expression: '$F{LastUpdateDateTime}', pattern: 'dd/MM/yyyy', x: 280, width: 120 }), text({ expression: '$F{Amount}', pattern: '#,##0.00', x: 400, width: 135, align: 'right' })] };
  d.bands.groupFooters.Branch = { height: 22, elements: [text({ expression: 'Branch total: $V{BranchTotal}', pattern: '#,##0.00', x: 250, width: 285, align: 'right', bold: true, y: 3 })] };
  d.bands.pageFooter = { height: 24, elements: [text({ expression: 'Page $V{PAGE_NUMBER} of $V{PAGE_COUNT}', x: 335, y: 5, width: 200, align: 'right', fontSize: 8, color: '#475569' })] };
  d.bands.summary = { height: 230, elements: [text({ expression: 'Grand total: $V{GrandTotal}', pattern: '#,##0.00', width: 300, bold: true, fontSize: 11, y: 6, height: 18 }), { ...text({}), type: 'chart', x: 0, y: 34, width: 400, height: 180, chartType: 'bar', categoryField: 'Branch', valueField: 'Amount', aggregate: 'Sum', title: 'Amount by branch' }] };
  return d;
}

/** After a JRXML import: each warning in plain words — what happened, where, what to do — with a Go there button. */
function ImportDialog({ info, onGo, onClose }) {
  const items = info.warnings.map((w) => ({ raw: w, ...explainWarning(w, info.places?.[w]) }));
  const order = { todo: 0, fixed: 1, info: 2 };
  items.sort((a, b) => order[a.level] - order[b.level]);
  const todo = items.filter((i) => i.level === 'todo').length;
  const label = { todo: 'To do', fixed: 'Fixed for you — check', info: 'For your information' };
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal import-dialog" onClick={(e) => e.stopPropagation()}>
        <h3>JRXML imported</h3>
        <p><b>{info.name}</b> — {info.parameters} parameters, {info.columns} fields.</p>
        <ol className="import-next">
          <li>Choose the data adapter{info.hints?.dataAdapter ? <> (in Jaspersoft it was <code>{info.hints.dataAdapter}</code>)</> : null}: <button type="button" className="linkish" onClick={() => onGo({ target: 'adapter', note: 'Choose the data adapter this report reads from.' })}>Setup → Data adapter</button></li>
          {items.length > 0 && <li>{todo ? `Fix the ${todo} thing${todo > 1 ? 's' : ''} marked “To do” below` : 'Look at the items below'} (each has a Go there button).</li>}
          <li>Preview it, then Save.</li>
        </ol>
        {items.length ? (
          <ul className="import-warnings">
            {items.map((item) => (
              <li key={item.raw} className={item.level}>
                <div className="iw-head"><span className={`iw-tag ${item.level}`}>{label[item.level]}</span><b>{item.title}</b></div>
                {item.text && <p>{item.text}</p>}
                {item.code && <pre className="iw-code">{item.code}</pre>}
                {item.where && <p className="iw-where"><span>Where</span><code>{item.where}</code></p>}
                {item.todo && <p className="iw-todo"><span>What to do</span>{item.todo}</p>}
                {item.focus && <button type="button" className="btn sm primary-soft" onClick={() => onGo({ ...item.focus, note: `${item.title}. ${item.todo}` })}>➜ Go there</button>}
              </li>
            ))}
          </ul>
        ) : <p className="ok">No warnings: everything was imported.</p>}
        <p className="muted">Nothing has been saved yet.</p>
        <button className="btn primary" onClick={onClose}>OK</button>
      </div>
    </div>
  );
}
