import { useCallback, useEffect, useReducer, useState } from 'react';
import { api, hasToken, setToken } from './api.js';
import Canvas from './Canvas.jsx';
import Preview from './Preview.jsx';
import Properties from './Properties.jsx';
import SetupForm from './SetupForm.jsx';
import { AdaptersPanel, Outline, Palette, ServerPanel } from './panels.jsx';
import { emptyDesign, findElement, getBand, setBand } from './designModel.js';
import { blankReport, initialState, reducer } from './store.js';

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
  const [leftTab, setLeftTab] = useState('server');
  const [zoom, setZoom] = useState(1);
  const [message, setMessage] = useState({ text: 'Tayyar', kind: '' });
  const [refreshKey, setRefreshKey] = useState(0);
  const [importInfo, setImportInfo] = useState(null);

  const notify = useCallback((text, kind = '') => setMessage({ text, kind }), []);

  const connect = useCallback(async () => {
    if (!hasToken()) await api.bootstrap().catch(() => false);
    try { await api.folders(); setNeedToken(false); setReady(true); notify('Server se connected', 'ok'); }
    catch (error) { setNeedToken(true); setReady(false); notify(error.status === 401 ? 'Admin token chahiye' : error.message, 'error'); }
  }, [notify]);
  useEffect(() => { connect(); }, [connect]);

  const open = (item) => {
    if (state.dirty && !window.confirm('Unsaved changes hain. Chhod kar nayi report kholni hai?')) return;
    const { design, layout, ...rest } = item; // eslint-disable-line no-unused-vars
    dispatch({
      type: 'LOAD',
      report: {
        isNew: false, id: item.id, title: item.title, description: item.description || '', folder: item.folder || 'General',
        connectionId: item.connectionId || 'default', collection: item.collection || '',
        queryLanguage: item.queryLanguage || 'mongosh', querySource: item.querySource || '',
        parameters: item.parameters || [], columns: item.columns || [], extra: rest,
      },
      design: design || emptyDesign(),
    });
    setTab(design ? 'design' : 'setup');
  };

  const importJrxml = async (file) => {
    if (!file) return;
    if (state.dirty && !window.confirm('Unsaved changes hain. JRXML import karne se woh chali jayengi. Jaari rakhein?')) return;
    try {
      const result = await api.importJrxml(await file.text());
      dispatch({ type: 'LOAD', report: { ...blankReport(), ...result.report, extra: {} }, design: result.design });
      dispatch({ type: 'REPORT', patch: {} });
      setImportInfo({ name: file.name, warnings: result.warnings, hints: result.hints, columns: result.report.columns.length, parameters: result.report.parameters.length });
      setTab('design');
      notify(`JRXML import hui: ${result.report.id} — review kar ke Save karein`, 'ok');
    } catch (error) { notify(`JRXML import nahi hui: ${error.message}`, 'error'); }
  };

  const save = async () => {
    const { report, design } = state;
    if (!report.id || !report.collection || !report.querySource.trim() || !report.columns.length) {
      notify('Save ke liye Report ID, collection, query aur fields lazmi hain (Setup tab)', 'error');
      setTab('setup');
      return;
    }
    const base = { ...report.extra };
    delete base.pipeline; // backend re-parses the query source
    const payload = {
      ...base,
      id: report.id, title: report.title, description: report.description, folder: report.folder,
      connectionId: report.connectionId, collection: report.collection,
      queryLanguage: report.queryLanguage, querySource: report.querySource,
      parameters: report.parameters, columns: report.columns,
      formats: base.formats || ['csv', 'xlsx', 'pdf'], defaultFormat: base.defaultFormat || 'pdf',
      design,
    };
    try {
      await api.saveReport(payload, report.isNew);
      dispatch({ type: 'SAVED', id: report.id });
      setRefreshKey((n) => n + 1);
      notify(`Saved ✓  —  /generate/${report.id}.pdf`, 'ok');
    } catch (error) { notify(`Save nahi hui: ${error.message}`, 'error'); }
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
      if (typing || tab !== 'design') return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') { event.preventDefault(); dispatch({ type: event.shiftKey ? 'REDO' : 'UNDO' }); return; }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'y') { event.preventDefault(); dispatch({ type: 'REDO' }); return; }
      if (event.key === 'Escape') { dispatch({ type: 'TOOL', tool: null }); return; }
      const { selection, design } = state;
      if (selection.kind !== 'element') return;
      const found = findElement(design, selection.id);
      if (!found) return;
      const apply = (values, key) => dispatch({ type: 'DESIGN', key, update: (d) => { const band = getBand(d, found.bandKey); return setBand(d, found.bandKey, { ...band, elements: band.elements.map((el) => (el.id === selection.id ? { ...el, ...values } : el)) }); } });
      if (event.key === 'Delete' || event.key === 'Backspace') {
        event.preventDefault();
        dispatch({ type: 'DESIGN', update: (d) => { const band = getBand(d, found.bandKey); return setBand(d, found.bandKey, { ...band, elements: band.elements.filter((el) => el.id !== selection.id) }); } });
        dispatch({ type: 'SELECT', selection: { kind: 'band', key: found.bandKey } });
      }
      const step = event.shiftKey ? 10 : 1;
      const nudge = { ArrowLeft: { x: found.element.x - step }, ArrowRight: { x: found.element.x + step }, ArrowUp: { y: found.element.y - step }, ArrowDown: { y: found.element.y + step } }[event.key];
      if (nudge) { event.preventDefault(); apply(nudge, 'nudge'); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  if (needToken) return <Login hint="Local par token khud mil jata hai. Deployed server par REPORTING_ADMIN_TOKEN daalein." onDone={connect} />;
  if (!ready) return <div className="login"><p>Server se connect ho raha hai…</p></div>;

  const { report, design, selection, tool, past, future, dirty } = state;
  return (
    <div className="app">
      <header className="toolbar">
        <a className="brand" href="/ui"><span className="brand-mark">M</span>Report Designer</a>
        <span className="doc-title">{report.title || 'Untitled'}{dirty ? ' •' : ''}</span>
        <div className="spacer" />
        <label className="btn" title="Jaspersoft Studio .jrxml import karein">Import JRXML
          <input type="file" accept=".jrxml,.xml,text/xml,application/xml" hidden onChange={(e) => { importJrxml(e.target.files[0]); e.target.value = ''; }} />
        </label>
        <button className="btn" onClick={loadDemo} title="Sample fields ke saath demo design">Demo design</button>
        <button className="btn" disabled={!past.length} onClick={() => dispatch({ type: 'UNDO' })}>↶ Undo</button>
        <button className="btn" disabled={!future.length} onClick={() => dispatch({ type: 'REDO' })}>↷ Redo</button>
        <div className="zoom">
          <button className="btn sm" onClick={() => setZoom((z) => Math.max(0.4, +(z - 0.1).toFixed(2)))}>−</button>
          <span>{Math.round(zoom * 100)}%</span>
          <button className="btn sm" onClick={() => setZoom((z) => Math.min(2.5, +(z + 0.1).toFixed(2)))}>+</button>
        </div>
        <button className="btn primary" onClick={save}>Save</button>
      </header>

      <div className="workspace">
        <aside className="left">
          <div className="dock">
            <div className="dock-tabs">
              <button className={leftTab === 'server' ? 'on' : ''} onClick={() => setLeftTab('server')}>Server</button>
              <button className={leftTab === 'adapters' ? 'on' : ''} onClick={() => setLeftTab('adapters')}>Data Adapters</button>
            </div>
            {leftTab === 'server'
              ? <ServerPanel report={report} onOpen={open} onNew={() => { dispatch({ type: 'NEW' }); setTab('setup'); }} notify={notify} refreshKey={refreshKey} />
              : <AdaptersPanel report={report} dispatch={dispatch} notify={notify} />}
          </div>
          <div className="dock">
            <div className="dock-tabs"><button className="on">Outline</button></div>
            <Outline report={report} design={design} selection={selection} dispatch={dispatch} />
          </div>
        </aside>

        <main className="center">
          <div className="tabs">
            {[['setup', 'Setup'], ['design', 'Design'], ['preview', 'Preview']].map(([id, label]) => (
              <button key={id} className={tab === id ? 'on' : ''} onClick={() => setTab(id)}>{label}</button>
            ))}
          </div>
          <div className="stage">
            {tab === 'setup' && <SetupForm state={state} dispatch={dispatch} notify={notify} onAutoLayout={() => setTab('design')} />}
            {tab === 'design' && <Canvas design={design} selection={selection} tool={tool} zoom={zoom} dispatch={dispatch} />}
            {tab === 'preview' && <Preview state={state} notify={notify} />}
          </div>
        </main>

        <aside className="right">
          <div className="dock"><div className="dock-tabs"><button className="on">Palette</button></div><Palette tool={tool} dispatch={dispatch} /></div>
          <div className="dock grow"><div className="dock-tabs"><button className="on">Properties</button></div><Properties state={state} dispatch={dispatch} notify={notify} /></div>
        </aside>
      </div>
      <footer className={`status ${message.kind}`}>{message.text}</footer>
      {importInfo && (
        <div className="modal-back" onClick={() => setImportInfo(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>JRXML import ho gayi</h3>
            <p><b>{importInfo.name}</b> — {importInfo.parameters} parameters, {importInfo.columns} fields.</p>
            {importInfo.hints?.dataAdapter && <p>Original Data Adapter: <code>{importInfo.hints.dataAdapter}</code>. Left panel ke <b>Data Adapters</b> tab se apna adapter chunein.</p>}
            {importInfo.warnings.length ? (<><p><b>Yeh cheezen khud check karein:</b></p><ul>{importInfo.warnings.map((w) => <li key={w}>{w}</li>)}</ul></>) : <p className="ok">Koi warning nahi.</p>}
            <p className="muted">Abhi tak kuch save nahi hua. Design tab mein dekh kar Save karein.</p>
            <button className="btn primary" onClick={() => setImportInfo(null)}>Theek hai</button>
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
