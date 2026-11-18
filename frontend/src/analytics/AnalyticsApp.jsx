// /analytics: dashboards, datasets and the model. Needs the Analytics right; datasets and the model also the
// Designer or Admin right. Addresses: #dash/<id>, #ds/<id>, #ds/new, #model.
import { useCallback, useEffect, useRef, useState } from 'react';
import { Notice, Topbar } from '../portal/Shell.jsx';
import { signOut } from '../shared/session.js';
import { bi, describeBytes, notADashboardFile, saveDashboardFile } from './api.js';
import Dashboard from './Dashboard.jsx';
import DatasetEditor from './DatasetEditor.jsx';
import ModelView from './ModelView.jsx';
import PowerBiImport, { readBase64 } from './PowerBiImport.jsx';
import { readPbixReport } from './pbix.js';

const readHash = () => {
  const [kind, id] = window.location.hash.slice(1).split('/');
  return ['dash', 'ds', 'model'].includes(kind) ? { kind, id: id || '' } : { kind: 'home', id: '' };
};

export default function AnalyticsApp() {
  const [info, setInfo] = useState(null);
  const [denied, setDenied] = useState('');
  const [view, setView] = useState(readHash);
  const [datasets, setDatasets] = useState([]);
  const [relationships, setRelationships] = useState([]);
  const [dashboards, setDashboards] = useState([]);
  const [message, setMessage] = useState(null);
  const notify = useCallback((text, kind = 'ok') => setMessage(text ? { text, kind } : null), []);

  const reload = useCallback(async () => {
    const [d, r, b] = await Promise.all([bi.datasets(), bi.relationships(), bi.dashboards()]);
    setDatasets(d.datasets); setRelationships(r.relationships); setDashboards(b.dashboards);
  }, []);

  useEffect(() => {
    bi.info().then((value) => { setInfo(value); return reload(); }).catch((error) => setDenied(error.message));
  }, [reload]);
  useEffect(() => {
    const follow = () => { setView(readHash()); setMessage(null); };
    window.addEventListener('hashchange', follow);
    return () => window.removeEventListener('hashchange', follow);
  }, []);
  const go = (hash) => { window.location.hash = hash; };

  const newDashboard = async () => {
    const name = window.prompt('Name of the new dashboard', 'New dashboard');
    if (!name) return;
    try {
      const { dashboard } = await bi.createDashboard({ name, tiles: [] });
      await reload();
      go(`dash/${dashboard.id}`);
    } catch (error) { notify(error.message, 'error'); }
  };

  // a dashboard file (.mdash) from another server or folder: its datasets, relationships and the dashboard
  const picker = useRef(null);
  const [powerBi, setPowerBi] = useState(null);       // {file, content | parts, info} while the Power BI dialog is open
  const importFile = async (file) => {
    if (!file) return;
    if (/\.pbit$/i.test(file.name)) {
      if (file.size > 15_000_000) { notify(`This template is ${describeBytes(file.size)}: at most 15 MB`, 'error'); return; }
      try {
        const content = await readBase64(file);
        const info = await bi.inspectPowerBi({ content, name: file.name });
        setPowerBi({ file, content, info });
      } catch (error) { notify(error.message, 'error'); }
      return;
    }
    if (/\.pbix$/i.test(file.name)) {
      // only the report part is taken out of the file (no data is uploaded, so the size does not matter)
      try {
        const parts = await readPbixReport(file);
        const info = await bi.inspectPowerBi({ parts, name: file.name });
        setPowerBi({ file, parts, info });
      } catch (error) { notify(error.message, 'error'); }
      return;
    }
    const refused = await notADashboardFile(file);
    if (refused) { notify(refused, 'error'); return; }
    const content = await file.text();
    const send = (connectionId) => bi.importDashboard({ content, ...(connectionId ? { connectionId } : {}) });
    try {
      let result;
      try { result = await send(''); } catch (error) {
        if (!/no data adapter/i.test(error.message) || !info.adapters.length) throw error;
        const choice = window.prompt(`${error.message}

Type the ID of the data adapter to use: ${info.adapters.map((a) => a.id).join(', ')}`, info.adapters[0].id);
        if (!choice) return;
        result = await send(choice.trim());
      }
      await reload();
      go(`dash/${result.dashboard.id}`);
      notify(`Imported “${result.dashboard.name}”: ${result.created.length} new dataset(s) importing, ${result.reused.length} reused.${result.warnings.length ? ` ${result.warnings.join(' ')}` : ''}`, 'ok');
    } catch (error) { notify(error.message, 'error'); }
  };
  const powerBiDone = async (result) => {
    setPowerBi(null);
    await reload();
    if (result.dashboards[0]) go(`dash/${result.dashboards[0].id}`);
    const parts = [`Imported ${result.dashboards.length} dashboard(s) from Power BI`,
      result.created.length ? `${result.created.length} new dataset(s) importing` : '', result.reused.length ? `${result.reused.length} dataset(s) reused` : '',
      result.relationships ? `${result.relationships} relationship(s)` : ''].filter(Boolean);
    const notes = result.notes.length ? ` Left out: ${result.notes.slice(0, 5).join('; ')}${result.notes.length > 5 ? ` and ${result.notes.length - 5} more` : ''}.` : '';
    window.setTimeout(() => notify(`${parts.join(', ')}.${notes}`, result.notes.length ? 'warn' : 'ok'), 60);
  };
  const downloadDashboard = async (entry) => {
    try { saveDashboardFile(await bi.dashboardFile(entry.id), entry.name); } catch (error) { notify(error.message, 'error'); }
  };

  if (denied) {
    return (<><Topbar current="/analytics" /><div className="login-wrap"><div className="card login"><Notice message={{ text: denied, kind: 'error' }} /><button type="button" className="btn" onClick={signOut}>Sign out and use another account</button></div></div></>);
  }
  if (!info) return (<><Topbar current="/analytics" /><div className="login-wrap"><p className="muted">Connecting…</p></div></>);

  const current = (kind, id) => view.kind === kind && view.id === id;
  return (
    <>
      <Topbar current="/analytics" />
      <div className="bi">
        <aside className="bi-side" aria-label="Analytics contents">
          <div className="bi-group">
            <div className="bi-group-head"><span>Dashboards</span><span className="row tight">
              {info.mayModel && <button type="button" className="btn small" onClick={() => picker.current?.click()} title="Import a dashboard file (.mdash) or a Power BI report (.pbix) or template (.pbit)">Import</button>}
              <button type="button" className="btn small" onClick={newDashboard} title="New dashboard">+ New</button></span>
              <input ref={picker} type="file" accept=".mdash,.pbit,.pbix" hidden onChange={(e) => { importFile(e.target.files[0]); e.target.value = ''; }} />
            </div>
            {dashboards.length === 0 && <p className="muted small">No dashboards yet.</p>}
            {dashboards.map((d) => (
              <a key={d.id} href={`#dash/${d.id}`} className={`bi-link ${current('dash', d.id) ? 'on' : ''}`}>
                <span>{d.name}</span><small className="muted">{d.tiles} visual{d.tiles === 1 ? '' : 's'}</small>
              </a>
            ))}
          </div>
          <div className="bi-group">
            <div className="bi-group-head"><span>Datasets</span>{info.mayModel && <a className="btn small" href="#ds/new">+ New</a>}</div>
            {datasets.length === 0 && <p className="muted small">No datasets yet.</p>}
            {datasets.map((d) => (
              <a key={d.id} href={`#ds/${d.id}`} className={`bi-link ${current('ds', d.id) ? 'on' : ''}`}>
                <span><i className={`dot ${d.status}`} aria-hidden="true" />{d.name}</span>
                <small className="muted">{d.status === 'ready' ? `${(d.rowCount || 0).toLocaleString()} rows · ${describeBytes(d.sizeBytes)}` : d.status}</small>
              </a>
            ))}
          </div>
          <a href="#model" className={`bi-link model ${view.kind === 'model' ? 'on' : ''}`}><span>Model (relationships)</span><small className="muted">{relationships.length}</small></a>
        </aside>

        <main className="bi-main">
          {message && <div style={{ marginBottom: 12 }}><Notice message={message} /></div>}
          {view.kind === 'home' && <Home info={info} datasets={datasets} dashboards={dashboards} onNew={newDashboard} />}
          {view.kind === 'dash' && (
            <Dashboard key={view.id} dashboardId={view.id} datasets={datasets} relationships={relationships} notify={notify}
              onSaved={reload} onDeleted={() => { reload(); go(''); }}
              actions={(() => {
                const entry = dashboards.find((d) => d.id === view.id);
                return entry && (info.mayModel || entry.mayChange)
                  ? <button type="button" className="btn" onClick={() => downloadDashboard(entry)} title="One .mdash file with the dashboard and its datasets' definitions (no data), to upload to another server or folder">Download</button>
                  : null;
              })()} />
          )}
          {view.kind === 'ds' && (
            <DatasetEditor key={view.id} datasetId={view.id} info={info} notify={notify}
              folders={[...new Set(['Analytics', ...datasets.map((d) => d.folder), ...dashboards.map((d) => d.folder)].filter(Boolean))].sort()}
              onSaved={(newId) => { reload(); if (newId) go(`ds/${newId}`); }} onDeleted={() => { reload(); go(''); }} />
          )}
          {powerBi && <PowerBiImport {...powerBi} datasets={datasets} onDone={powerBiDone} onCancel={() => setPowerBi(null)} />}
          {view.kind === 'model' && <ModelView datasets={datasets} relationships={relationships} mayModel={info.mayModel} notify={notify} onChanged={reload} />}
        </main>
      </div>
    </>
  );
}

function Home({ info, datasets, dashboards, onNew }) {
  const ready = datasets.filter((d) => d.status === 'ready').length;
  return (
    <div className="stack">
      <div className="page-head"><div><p className="eyebrow">Analytics</p><h1>Dashboards from your data</h1>
        <p className="muted">Import data from any data adapter into a dataset, relate datasets in the model, then build dashboards with charts, cards, tables and slicers. Clicking a bar or a slice filters the rest of the dashboard.</p></div></div>
      <ol className="bi-steps">
        <li className={datasets.length ? 'done' : ''}><b>1. Datasets</b><span>{datasets.length ? `${datasets.length} dataset(s), ${ready} ready` : 'Choose a data adapter and a query; its records are imported for fast analysis.'}</span>
          {info.mayModel ? <a className="btn small" href="#ds/new">New dataset</a> : <small className="muted">Made by people with the Designer or Admin right.</small>}</li>
        <li className={datasets.length > 1 ? 'done' : ''}><b>2. Model</b><span>Relate datasets on a shared column to use them together (optional).</span><a className="btn small" href="#model">Open the model</a></li>
        <li className={dashboards.length ? 'done' : ''}><b>3. Dashboards</b><span>{dashboards.length ? `${dashboards.length} dashboard(s)` : 'Add visuals and arrange them on the grid.'}</span><button type="button" className="btn small primary" onClick={onNew}>New dashboard</button></li>
      </ol>
      <p className="muted small">Limits on this server: imports up to {info.limits.importMaxRows.toLocaleString()} records per dataset; a visual returns up to {info.limits.queryMaxRows.toLocaleString()} rows and stops after {info.limits.queryTimeoutSeconds} s.</p>
    </div>
  );
}
