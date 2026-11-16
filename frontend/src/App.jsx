import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { api, hasToken, setToken } from './api.js';
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
import TableEditor from './TableEditor.jsx';
import AppSwitcher from './shared/AppSwitcher.jsx';
import ThemeToggle from './shared/ThemeToggle.jsx';
import TemplateGallery from './TemplateGallery.jsx';
import { AdaptersPanel, Outline, Palette, ServerPanel, WorkspacePanel, publishState } from './panels.jsx';
import { ZOOM_LIMITS, bandWidth, clampZoom, emptyDesign, findElement, getBand, setBand } from './designModel.js';
import ComponentWizard from './ComponentWizard.jsx';
import PublishDialog from './PublishDialog.jsx';
import { MRD_ACCEPT, buildMrd, downloadText, fileKind, parseMrd } from './mrd.js';
import { blankReport, initialState, reducer, reportPayload } from './store.js';
import { readClipboard } from './arrange.js';

const DEMO_COLUMNS = ['Identifier', 'Branch', 'Name', 'Amount', 'LastUpdateDateTime'];

function Login({ onDone, hint }) {
  const [value, setValue] = useState('');
  return (
    <div className="login">
      <h2>Admin token</h2>
      <p className="muted">{hint}</p>
      <input type="password" value={value} onChange={(e) => setValue(e.target.value)} placeholder="REPORTING_ADMIN_TOKEN" autoFocus />
      <button className="btn primary" onClick={() => { setToken(value); onDone(); }}>Connect</button>
    </div>
  );
}

export default function App() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [ready, setReady] = useState(false);
  const [needToken, setNeedToken] = useState(false);
  const [tab, setTab] = useState('setup');
  const [leftTab, setLeftTab] = useState('workspace');
  const [drawer, setDrawer] = useState(null); // small screens: 'left' | 'right' panel shown over the canvas
  const [publishing, setPublishing] = useState(null); // { folders, folder, replace, busy }
  const [zoom, setZoom] = useState(1);
  const [message, setMessage] = useState({ text: 'Ready', kind: '' });
  const [refreshKey, setRefreshKey] = useState(0);
  const [importInfo, setImportInfo] = useState(null);

  const [errorPopup, setErrorPopup] = useState(''); // errors open a popup with a hint (and stay in the status line)
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
    if (!hasToken()) await api.bootstrap().catch(() => false);
    try { await api.folders(); setNeedToken(false); setReady(true); notify('Connected to the server', 'ok'); }
    catch (error) { setNeedToken(true); setReady(false); notify(error.status === 401 ? 'An admin or designer token is needed' : error.message, 'error'); }
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
    try { load(await api.workspaceReport(summary.id)); notify(`Opened ${summary.id} from your workspace`, 'ok'); }
    catch (error) { notify(`Could not open the report: ${error.message}`, 'error'); }
  };

  /** Open a published report: its workspace copy if there is one, else a new workspace copy of the server version. */
  const openFromServer = async (item) => {
    if (state.dirty && !window.confirm('You have unsaved changes. Discard them and open another report?')) return;
    try {
      const workspaceCopy = await api.workspaceReport(item.id).catch((error) => { if (error.status === 404) return null; throw error; });
      if (workspaceCopy) {
        load(workspaceCopy);
        setLeftTab('workspace');
        notify(`${item.id} is already in your workspace — opened that copy. Publish it to update the server.`, 'ok');
        return;
      }
      const published = await api.report(item.id);
      const saved = await api.saveWorkspace({ ...published, design: published.design }, true);
      load(published, saved.report);
      setRefreshKey((n) => n + 1);
      setLeftTab('workspace');
      notify(`Copied ${item.id} from the server into your workspace`, 'ok');
    } catch (error) { notify(`Could not open the server report: ${error.message}`, 'error'); }
  };

  /** Open file…: a .mrd report file, or a Jaspersoft Studio .jrxml (converted). Nothing is saved until Save. */
  const openFile = async (file) => {
    if (!file) return;
    const text = await file.text();
    const kind = fileKind(text);
    if (kind === 'jrxml') { importJrxml(text, file.name); return; }
    if (kind !== 'mrd') { notify(`${file.name} is not a .mrd or .jrxml report file`, 'error'); return; }
    if (state.dirty && !window.confirm('You have unsaved changes. Opening a file will discard them. Continue?')) return;
    try {
      const definition = parseMrd(text);
      const inWorkspace = await api.workspaceReport(definition.id).then(() => true).catch((error) => { if (error.status === 404) return false; throw error; });
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

  // /designer?open=<ID> (from /server → "Open in Designer"): open that published report once connected
  useEffect(() => {
    if (!ready) return;
    const id = new URLSearchParams(window.location.search).get('open');
    if (!id) return;
    window.history.replaceState(null, '', window.location.pathname);
    openFromServer({ id });
  }, [ready]); // eslint-disable-line react-hooks/exhaustive-deps -- only once, right after connecting

  const importJrxml = async (text, name) => {
    if (state.dirty && !window.confirm('You have unsaved changes. Importing a JRXML will discard them. Continue?')) return;
    const file = { name };
    try {
      const result = await api.importJrxml(text);
      dispatch({ type: 'LOAD', report: { ...blankReport(), ...result.report, extra: {} }, design: result.design });
      dispatch({ type: 'REPORT', patch: {} });
      setImportInfo({ name: file.name, warnings: result.warnings, hints: result.hints, columns: result.report.columns.length, parameters: result.report.parameters.length });
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
      const result = await api.saveWorkspace(reportPayload(state), report.isNew);
      dispatch({ type: 'SAVED', id: report.id, status: result.report });
      setRefreshKey((n) => n + 1);
      const where = result.report.onServer ? 'The server still has the last published version.' : 'Not on the server yet.';
      notify(result.readyToPublish
        ? `Saved in your workspace ✓  ${where} Use “Publish to server” to update it.`
        : `Saved in your workspace as a draft. Before publishing: ${result.problem}`, result.readyToPublish ? 'ok' : '');
      return result;
    } catch (error) { notify(`Save failed: ${error.message}`, 'error'); return null; }
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
      const result = await api.publish(report.id, { folder: publishing.folder, replace: publishing.replace });
      dispatch({ type: 'STATUS', status: result.workspace });
      setPublishing(null);
      setRefreshKey((n) => n + 1);
      notify(`Published to the server ✓  ${publishing.folder} / ${report.id}  —  /generate/${report.id}.pdf`, 'ok');
    } catch (error) {
      if (error.status === 409) setPublishing((p) => ({ ...p, busy: false, onServer: true, error: error.message }));
      else { setPublishing((p) => ({ ...p, busy: false, error: error.message })); }
    }
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

  if (needToken) return <Login hint="On your own computer the token is picked up automatically. On a deployed server enter REPORTING_ADMIN_TOKEN or REPORTING_DESIGNER_TOKEN." onDone={connect} />;
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
  };

  const { report, design, selection, tool, past, future, dirty } = state;
  return (
    <div className="app">
      <header className="toolbar">
        <button type="button" className="btn drawer-btn" aria-label="Reports, adapters and outline" aria-expanded={drawer === 'left'} onClick={() => setDrawer(drawer === 'left' ? null : 'left')}>☰</button>
        <span className="brand"><span className="brand-mark">M</span><span className="lbl">Report Designer</span></span>
        <AppSwitcher current="/designer" />
        <span className="doc-title" title={report.title || 'Untitled'}>{report.title || 'Untitled'}{dirty ? ' •' : ''}</span>
        <div className="spacer" />
        <label className="btn" title="Open a .mrd report file, or import a Jaspersoft Studio .jrxml">📂<span className="lbl"> Open file…</span>
          <input type="file" accept={MRD_ACCEPT} hidden onChange={(e) => { openFile(e.target.files[0]); e.target.value = ''; }} />
        </label>
        <button className="btn" onClick={saveFile} title="Download this report as one .mrd file (query, parameters, data adapter, design and images)">💾<span className="lbl"> Save as file</span></button>
        <button className="btn hide-narrow" onClick={loadDemo} title="Demo design with sample fields">Demo design</button>
        <button className="btn" disabled={!past.length} onClick={() => dispatch({ type: 'UNDO' })} title="Undo (Ctrl+Z)">↶<span className="lbl"> Undo</span></button>
        <button className="btn" disabled={!future.length} onClick={() => dispatch({ type: 'REDO' })} title="Redo (Ctrl+Y)">↷<span className="lbl"> Redo</span></button>
        <div className="zoom">
          <button className="btn sm" title="Zoom out (Ctrl −, Ctrl + wheel)" onClick={() => setZoom((z) => clampZoom(z - ZOOM_LIMITS.step))}>−</button>
          <button className="btn sm zoom-value" title="Reset to 100% (Ctrl 0)" onClick={() => setZoom(1)}>{Math.round(zoom * 100)}%</button>
          <button className="btn sm" title="Zoom in (Ctrl +, Ctrl + wheel)" onClick={() => setZoom((z) => clampZoom(z + ZOOM_LIMITS.step))}>+</button>
        </div>
        {(() => { const s = report.isNew ? { icon: '○', label: 'Not saved yet', cls: 'draft' } : publishState(report.status); return <span className={`pub-chip ${s.cls}`} title={s.label}>{s.icon} {report.isNew ? 'Not saved' : s.cls === 'published' ? 'Published' : s.cls === 'changed' ? 'Changed' : 'Draft'}</span>; })()}
        <button className="btn" onClick={save} title="Save in your designer workspace (Ctrl+S). The server is not changed.">Save</button>
        <button className="btn primary" onClick={startPublish} title="Copy this report to a folder on the report server">⇪<span className="lbl"> Publish</span></button>
        <ThemeToggle />
        <button type="button" className="btn drawer-btn" aria-label="Palette and properties" aria-expanded={drawer === 'right'} onClick={() => setDrawer(drawer === 'right' ? null : 'right')}>⚙</button>
      </header>

      <div className={`workspace ${drawer ? `show-${drawer}` : ''}`}>
        {drawer && <button type="button" className="drawer-backdrop" aria-label="Close the panel" onClick={() => setDrawer(null)} />}
        <aside className="left">
          <div className="dock">
            <div className="dock-tabs">
              <button className={leftTab === 'workspace' ? 'on' : ''} onClick={() => setLeftTab('workspace')} title="Reports saved inside the designer">Workspace</button>
              <button className={leftTab === 'server' ? 'on' : ''} onClick={() => setLeftTab('server')} title="Published reports on the report server">Server</button>
              <button className={leftTab === 'adapters' ? 'on' : ''} onClick={() => setLeftTab('adapters')}>Data Adapters</button>
            </div>
            {leftTab === 'workspace' && <WorkspacePanel report={report} onOpen={open} onNew={(folder) => { if (state.dirty && !window.confirm('You have unsaved changes. Discard them?')) return; dispatch({ type: 'NEW', folder }); setTab('setup'); setTemplates(true); }} notify={notify} refreshKey={refreshKey} onChanged={() => setRefreshKey((n) => n + 1)} />}
            {leftTab === 'server' && <ServerPanel report={report} onOpenFromServer={openFromServer} openMenu={openMenu} notify={notify} refreshKey={refreshKey} onChanged={() => setRefreshKey((n) => n + 1)} />}
            {leftTab === 'adapters' && <AdaptersPanel notify={notify} openMenu={openMenu} refreshKey={refreshKey} onChanged={() => setRefreshKey((n) => n + 1)} />}
          </div>
          <div className="dock">
            <div className="dock-tabs"><button className="on">Outline</button></div>
            <Outline report={report} design={design} selection={selection} dispatch={dispatch} actions={actions} openMenu={openMenu} onDetectFields={() => setTab('setup')} onEditExpression={editExpression} />
          </div>
        </aside>

        <main className="center">
          <div className="tabs">
            {[['setup', 'Setup'], ['design', 'Design'], ['source', 'Source'], ['preview', 'Preview']].map(([id, label]) => (
              <button key={id} className={tab === id ? 'on' : ''} onClick={() => setTab(id)}>{label}</button>
            ))}
          </div>
          <div className="stage">
            {tab === 'setup' && <SetupForm state={state} dispatch={dispatch} notify={notify} onTemplates={() => setTemplates(true)} refreshKey={refreshKey} />}
            {tab === 'design' && <Canvas design={design} selection={selection} tool={tool} zoom={zoom} setZoom={setZoom} dispatch={dispatch} actions={actions} openMenu={openMenu} onEditExpression={editExpression} onPercentage={onPercentage} onWizard={onWizard} onOpenTable={setTableId} prefs={canvasPrefs} setPrefs={setCanvasPrefs} hasClipboard={Boolean(readClipboard()?.items?.length)} onQuery={() => setQueryTab('query')} onTemplates={() => setTemplates(true)} />}
            {tab === 'source' && <SourceView state={state} dispatch={dispatch} notify={notify} />}
            {tab === 'preview' && <Preview state={state} notify={notify} />}
          </div>
        </main>

        <aside className="right">
          <div className="dock"><div className="dock-tabs"><button className="on">Palette</button></div><Palette tool={tool} dispatch={dispatch} /></div>
          <div className="dock grow"><div className="dock-tabs"><button className="on">Properties</button></div><Properties state={state} dispatch={dispatch} notify={notify} actions={actions} onEditExpression={editExpression} onOpenQuery={(t) => setQueryTab(t || 'query')} onOpenTable={setTableId} /></div>
        </aside>
      </div>
      <footer className={`status ${message.kind}`}>{message.text}</footer>
      {errorPopup && <ErrorDialog message={errorPopup} onClose={() => setErrorPopup('')} onAction={errorAction} />}
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
        <ExpressionEditor element={findElement(design, exprId).element} report={report} design={design} onSave={saveExpression} onClose={() => setExprId(null)} />
      )}
      {publishing && <PublishDialog publishing={publishing} setPublishing={setPublishing} report={report} design={design} onConfirm={confirmPublish} />}
      {importInfo && (
        <div className="modal-back" onClick={() => setImportInfo(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>JRXML imported</h3>
            <p><b>{importInfo.name}</b> — {importInfo.parameters} parameters, {importInfo.columns} fields.</p>
            {importInfo.hints?.dataAdapter && <p>Original Data Adapter: <code>{importInfo.hints.dataAdapter}</code>. Choose your adapter in the <b>Data Adapters</b> tab on the left, or in Setup.</p>}
            {importInfo.warnings.length ? (<><p><b>Please check these yourself:</b></p><ul>{importInfo.warnings.map((w) => <li key={w}>{w}</li>)}</ul></>) : <p className="ok">No warnings.</p>}
            <p className="muted">Nothing has been saved yet. Review it in the Design tab, then Save.</p>
            <button className="btn primary" onClick={() => setImportInfo(null)}>OK</button>
          </div>
        </div>
      )}
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
