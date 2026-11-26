// /analytics: dashboards, datasets and the model. Needs the Analytics right; datasets and the model also the
// Designer or Admin right. Addresses: #dash/<id>, #ds/<id>, #ds/new, #model, #q/<id>, #q/new (DAX / SQL queries),
// #sources (its data sources), #ml (ML lab), #embed/<dashboard> (how to show it in a website).
import SiteFooter from '../shared/SiteFooter.jsx';
import PixelField from '../shared/PixelField.jsx';
import Resizer, { useStoredWidth } from '../shared/Resizer.jsx';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Notice, Topbar } from '../portal/Shell.jsx';
import { signOut } from '../shared/session.js';
import { setTeamPlace } from '../shared/TeamBar.jsx';
import { bi, describeBytes, notADashboardFile, queries, saveDashboardFile } from './api.js';
import AnalyticsRibbon from './AnalyticsRibbon.jsx';
import QueryEditor, { modeLabel } from './QueryEditor.jsx';
import Dashboard from './Dashboard.jsx';
import DatasetEditor from './DatasetEditor.jsx';
import DataSourcesPanel from '../shared/DataSourcesPanel.jsx';
import EmbedGuide from './EmbedGuide.jsx';
import MlLab from './MlLab.jsx';
import ModelView from './ModelView.jsx';
import PowerBiImport, { readBase64 } from './PowerBiImport.jsx';
import { readPbixReport } from './pbix.js';

const readHash = () => {
  const [kind, id] = window.location.hash.slice(1).split('/');
  return ['dash', 'ds', 'model', 'q', 'ml', 'embed', 'sources'].includes(kind) ? { kind, id: id || '' } : { kind: 'home', id: '' };
};

/** A number (or "a/b", "a · b") that counts up from 0 when the page opens. */
function CountUp({ value }) {
  const text = String(value);
  const parts = text.split(/(\d[\d,]*)/);
  const [t, setT] = useState(() => (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 1 : 0));
  useEffect(() => {
    if (t >= 1) return undefined;
    const began = performance.now();
    let frame;
    const step = (now) => { const k = Math.min(1, (now - began) / 900); setT(1 - (1 - k) ** 3); if (k < 1) frame = requestAnimationFrame(step); };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [text]); // eslint-disable-line react-hooks/exhaustive-deps
  return <>{parts.map((part, i) => (/^\d/.test(part) ? Math.round(Number(part.replace(/,/g, '')) * t).toLocaleString() : part))}</>;
}

/** Download a dashboard as one .mdash file: with its data (like a Power BI file) or definitions only. */
function DownloadMenu({ onPick }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="bi-dl" onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}>
      <button type="button" className="btn" aria-expanded={open} onClick={() => setOpen(!open)} title="Save this dashboard as one file (.mdash)">⤓ Download ▾</button>
      {open && (
        <span className="bi-dl-menu px-in" role="menu">
          <button type="button" role="menuitem" onClick={() => { setOpen(false); onPick(true); }}>
            <b>📦 With data</b><small>The dashboard, its datasets and their records. Opens on any server, even without the database (up to 15 MB of data).</small>
          </button>
          <button type="button" role="menuitem" onClick={() => { setOpen(false); onPick(false); }}>
            <b>📄 Definitions only</b><small>Small file. The other server imports the data from its own database.</small>
          </button>
        </span>
      )}
    </span>
  );
}

export default function AnalyticsApp() {
  const [info, setInfo] = useState(null);
  const [denied, setDenied] = useState('');
  const [view, setView] = useState(readHash);
  const [datasets, setDatasets] = useState([]);
  const [relationships, setRelationships] = useState([]);
  const [dashboards, setDashboards] = useState([]);
  const [savedQueries, setSavedQueries] = useState([]);
  const [message, setMessage] = useState(null);
  const [sideWidth, setSideWidth] = useStoredWidth('bi-side-width', 250, 190, 520);
  const notify = useCallback((text, kind = 'ok') => setMessage(text ? { text, kind } : null), []);

  const reload = useCallback(async () => {
    const [d, r, b, q] = await Promise.all([bi.datasets(), bi.relationships(), bi.dashboards(), queries.list().catch(() => ({ queries: [] }))]);
    setDatasets(d.datasets); setRelationships(r.relationships); setDashboards(b.dashboards); setSavedQueries(q.queries);
  }, []);

  const reloadInfo = useCallback(() => bi.info().then(setInfo).catch(() => {}), []);
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
  const downloadDashboard = async (entry, withData = false) => {
    try {
      saveDashboardFile(await bi.dashboardFile(entry.id, withData), entry.name);
      notify(withData ? `Downloaded “${entry.name} (with data).mdash”: it opens on another server even without the database.`
        : `Downloaded “${entry.name}.mdash”: definitions only; the other server imports the data from its database.`, 'ok');
    } catch (error) { notify(error.message, 'error'); }
  };

  if (denied) {
    return (<><Topbar current="/analytics" /><div className="login-wrap"><div className="card login"><Notice message={{ text: denied, kind: 'error' }} /><button type="button" className="btn" onClick={signOut}>Sign out and use another account</button></div></div></>);
  }
  useEffect(() => {
    const names = { dash: 'Dashboard', ds: 'Dataset', q: 'Query', model: 'Model', ml: 'ML lab' };
    const item = ({ dash: dashboards, ds: datasets, q: savedQueries }[view.kind] || []).find((x) => x.id === view.id);
    setTeamPlace(names[view.kind] ? { place: `${view.kind}/${view.id || ''}`, title: `${names[view.kind]}${item ? ` · ${item.name || item.title || item.id}` : ''}` } : {});
  }, [view, datasets, dashboards, savedQueries]);
  if (!info) return (<><Topbar current="/analytics" /><div className="login-wrap"><p className="muted">Connecting…</p></div></>);

  const current = (kind, id) => view.kind === kind && view.id === id;
  const refreshAll = async () => {
    const ready = datasets.filter((d) => d.status === 'ready' || d.status === 'failed');
    if (!ready.length || !window.confirm(`Import all ${ready.length} dataset(s) again from their data adapters? It runs in the background.`)) return;
    const results = await Promise.allSettled(ready.map((d) => bi.refresh(d.id)));
    const failed = results.filter((r) => r.status === 'rejected').length;
    notify(failed ? `${ready.length - failed} dataset(s) importing; ${failed} could not start.` : `${ready.length} dataset(s) importing again.`, failed ? 'warn' : 'ok');
    reload();
  };
  const folderList = [...new Set(['Analytics', ...datasets.map((d) => d.folder), ...dashboards.map((d) => d.folder), ...savedQueries.map((q) => q.folder)].filter(Boolean))].sort();
  return (
    <>
      <Topbar current="/analytics" />
      <AnalyticsRibbon info={info} view={view} go={go} onNewDashboard={newDashboard} onImport={() => picker.current?.click()}
        dashName={view.kind === 'dash' ? dashboards.find((d) => d.id === view.id)?.name : ''}
        onDownload={(withData) => { const entry = dashboards.find((d) => d.id === view.id); if (entry) downloadDashboard(entry, withData); }}
        onRefreshAll={refreshAll} onReload={reload} readyCount={datasets.filter((d) => d.status === 'ready').length} />
      <div className="bi bi-new" style={{ '--bi-side': `${sideWidth}px` }}>
        <Resizer width={sideWidth} onWidth={setSideWidth} normal={250} min={190} max={520} label="Contents width" className="bi-resizer"
          style={{ left: sideWidth - 5, right: 'auto' }} />
        <aside className="bi-side" aria-label="Analytics contents">
          <div className="bi-group">
            <div className="bi-group-head"><span>Dashboards <em className="bi-count">{dashboards.length}</em></span><span className="row tight">
              {info.mayModel && <button type="button" className="btn small" onClick={() => picker.current?.click()} title="Import a dashboard file (.mdash) or a Power BI report (.pbix) or template (.pbit)">Import</button>}
              <button type="button" className="btn small" onClick={newDashboard} title="New dashboard">+ New</button></span>
              <input ref={picker} type="file" accept=".mdash,.pbit,.pbix" hidden onChange={(e) => { importFile(e.target.files[0]); e.target.value = ''; }} />
            </div>
            {dashboards.length === 0 && <p className="muted small">No dashboards yet.</p>}
            {dashboards.map((d, i) => (
              <a key={d.id} href={`#dash/${d.id}`} className={`bi-link ${current('dash', d.id) ? 'on' : ''}`} style={{ '--i': i }}>
                <span><i className="bi-mark tint-c" aria-hidden="true">▦</i>{d.name}</span><small className="muted">{d.tiles} visual{d.tiles === 1 ? '' : 's'}</small>
              </a>
            ))}
          </div>
          <div className="bi-group">
            <div className="bi-group-head"><span>Datasets <em className="bi-count">{datasets.length}</em></span>{info.mayModel && <a className="btn small" href="#ds/new">+ New</a>}</div>
            {datasets.length === 0 && <p className="muted small">No datasets yet.</p>}
            {datasets.map((d, i) => (
              <a key={d.id} href={`#ds/${d.id}`} className={`bi-link ${current('ds', d.id) ? 'on' : ''}`} style={{ '--i': i }}>
                <span><i className="bi-mark tint-g" aria-hidden="true">⛁<i className={`dot ${d.status}`} /></i>{d.name}</span>
                <small className="muted">{d.status === 'ready' ? `${(d.rowCount || 0).toLocaleString()} rows · ${describeBytes(d.sizeBytes)}` : d.status}</small>
              </a>
            ))}
          </div>
          <div className="bi-group">
            <div className="bi-group-head"><span title="DAX · SQL · MongoDB">Queries <em className="bi-count">{savedQueries.length}</em></span>{info.mayModel && <a className="btn small" href="#q/new">+ New</a>}</div>
            {savedQueries.length === 0 && <p className="muted small">{info.mayModel ? 'Write DAX / SQL over the datasets, or a MongoDB / SQL query on a data adapter, and keep it here.' : 'No saved queries.'}</p>}
            {savedQueries.map((q, i) => (
              <a key={q.id} href={`#q/${q.id}`} className={`bi-link ${current('q', q.id) ? 'on' : ''}`} style={{ '--i': i }}>
                <span><i className="bi-mark tint-b" aria-hidden="true">ƒ</i>{q.name}</span><small className="muted" title={modeLabel(q)}>{q.language === 'source' ? (q.queryLanguage === 'sql' ? 'DB SQL' : 'Mongo') : q.language.toUpperCase()}</small>
              </a>
            ))}
          </div>
          {info.mayModel && <a href="#sources" className={`bi-link model ${view.kind === 'sources' ? 'on' : ''}`}><span><i className="bi-mark tint-g" aria-hidden="true">⛁</i>Data sources</span><small className="muted">{info.adapters.length || 'connect'}</small></a>}
          <a href="#model" className={`bi-link model ${view.kind === 'model' ? 'on' : ''}`}><span><i className="bi-mark tint-v" aria-hidden="true">⇄</i>Model (relationships)</span><small className="muted">{relationships.length}</small></a>
          <a href="#ml" className={`bi-link model ${view.kind === 'ml' ? 'on' : ''}`}><span><i className="bi-mark tint-c" aria-hidden="true">✦</i>ML lab</span><small className="muted">predict</small></a>
          <a href="#embed" className={`bi-link model ${view.kind === 'embed' ? 'on' : ''}`}><span><i className="bi-mark tint-b" aria-hidden="true">⧉</i>Embed in a website</span><small className="muted">guide</small></a>
        </aside>

        <main className="bi-main">
          {message && <div style={{ marginBottom: 12 }}><Notice message={message} /></div>}
          {view.kind === 'home' && <Home info={info} datasets={datasets} dashboards={dashboards} queries={savedQueries} relationships={relationships} onNew={newDashboard} />}
          {view.kind === 'dash' && (
            <Dashboard key={view.id} dashboardId={view.id} datasets={datasets} relationships={relationships} notify={notify}
              onSaved={reload} onDeleted={() => { reload(); go(''); }}
              actions={(() => {
                const entry = dashboards.find((d) => d.id === view.id);
                const embed = entry ? <a className="btn" href={`#embed/${entry.id}`} title="Show this dashboard inside another website">Embed</a> : null;
                return entry && (info.mayModel || entry.mayChange)
                  ? <>{embed}<DownloadMenu onPick={(withData) => downloadDashboard(entry, withData)} /></>
                  : embed;
              })()} />
          )}
          {view.kind === 'ds' && (
            <DatasetEditor key={view.id} datasetId={view.id} info={info} onConnected={reloadInfo} notify={notify}
              folders={[...new Set(['Analytics', ...datasets.map((d) => d.folder), ...dashboards.map((d) => d.folder)].filter(Boolean))].sort()}
              onSaved={(newId) => { reload(); if (newId) go(`ds/${newId}`); }} onDeleted={() => { reload(); go(''); }} />
          )}
          {powerBi && <PowerBiImport {...powerBi} datasets={datasets} onDone={powerBiDone} onCancel={() => setPowerBi(null)} />}
          {view.kind === 'q' && (
            <QueryEditor key={view.id || 'new'} queryId={view.id || 'new'} datasets={datasets} adapters={info.adapters || []} mayModel={info.mayModel} notify={notify} folders={folderList}
              onDatasetMade={() => reload()}
              onSaved={(newId) => { reload(); if (newId && newId !== view.id) go(`q/${newId}`); }} onDeleted={() => { reload(); go(''); }} />
          )}
          {view.kind === 'sources' && info.mayModel && <DataSourcesPanel area="analytics" notify={notify} onChanged={reloadInfo} />}
          {view.kind === 'ml' && <MlLab datasets={datasets} dashboards={dashboards} notify={notify} onAdded={reload} go={go} />}
          {view.kind === 'embed' && <EmbedGuide dashboards={dashboards} dashboardId={view.id} notify={notify} go={go} />}
          {view.kind === 'model' && <ModelView datasets={datasets} relationships={relationships} mayModel={info.mayModel} notify={notify} onChanged={reload} />}
        </main>
      </div>
      <SiteFooter />
    </>
  );
}

function Home({ info, datasets, dashboards, queries, relationships, onNew }) {
  const ready = datasets.filter((d) => d.status === 'ready');
  const rows = ready.reduce((n, d) => n + (d.rowCount || 0), 0);
  const busy = datasets.filter((d) => d.status === 'queued' || d.status === 'refreshing').length;
  const failed = datasets.filter((d) => d.status === 'failed').length;
  const kpis = [
    ['Dashboards', dashboards.length, dashboards.length ? `${dashboards.reduce((n, d) => n + (d.tiles || 0), 0)} visuals in all` : 'None yet', 'c'],
    ['Datasets ready', `${ready.length}/${datasets.length}`, busy ? `${busy} importing now` : failed ? `${failed} failed to import` : datasets.length ? 'All imported' : 'None yet', failed ? 'a' : 'g'],
    ['Rows imported', rows.toLocaleString(), ready.length ? describeBytes(ready.reduce((n, d) => n + (d.sizeBytes || 0), 0)) : 'Nothing imported yet', 'b'],
    ['Queries & links', `${queries.length} · ${relationships.length}`, 'Saved queries · relationships in the model', 'v'],
  ];
  const steps = [
    ...(info.mayModel ? [{ done: info.adapters.length > 0, title: 'Data source', tint: 'b', text: info.adapters.length ? `${info.adapters.length} connected for Analytics` : 'Connect a database for Analytics.', action: <a className="btn small" href="#sources">{info.adapters.length ? 'Data sources' : 'Connect a database'}</a> }] : []),
    { done: datasets.length > 0, title: 'Datasets', tint: 'g', text: datasets.length ? `${datasets.length} dataset(s), ${ready.length} ready` : 'Choose a data adapter and a query; its records are imported for fast analysis.',
      action: info.mayModel ? <a className="btn small" href="#ds/new">New dataset</a> : <small className="muted">Made by people with the Designer or Admin right.</small> },
    { done: datasets.length > 1 && relationships.length > 0, title: 'Model', tint: 'v', text: 'Relate datasets on a shared column to use them together (optional).', action: <a className="btn small" href="#model">Open the model</a> },
    { done: dashboards.length > 0, title: 'Dashboards', tint: 'c', text: dashboards.length ? `${dashboards.length} dashboard(s)` : 'Add visuals and arrange them on the grid.', action: <button type="button" className="btn small primary" onClick={onNew}>New dashboard</button> },
  ];
  return (
    <div className="stack bi-home">
      <div className="page-head bi-hero bi-hero-px px-in"><div><p className="px-kicker"><b>04</b> — Analytics</p><h1>Dashboards from your data<span className="px-caret" aria-hidden="true" /></h1>
        <p className="muted">Import data from any data adapter into a dataset, relate datasets in the model, then build dashboards with charts, cards, tables and slicers. Clicking a bar or a slice filters the rest of the dashboard.</p>
        <div className="bi-hero-go">
          {info.mayModel && <button type="button" className="btn primary" onClick={onNew}>＋ New dashboard</button>}
          {info.mayModel && <a className="btn" href="#ds/new">＋ Dataset</a>}
          <a className="btn" href="#model">⇄ Model</a>
        </div></div>
        <span className="bi-hero-art bi-hero-pixels" aria-hidden="true"><PixelField cell={9} gap={3} bars={10} label="Analytics" /></span>
      </div>
      <div className="bi-kpis">
        {kpis.map(([label, value, note, tint], i) => (
          <div key={label} className={`bi-kpi tint-${tint}`} style={{ '--i': i }}><small>{label}</small><strong><CountUp value={value} /></strong><span>{note}</span></div>
        ))}
      </div>
      <ol className="bi-steps">
        {steps.map((step, i) => (
          <li key={step.title} className={`tint-${step.tint} ${step.done ? 'done' : ''}`} style={{ '--i': i }}>
            <span className="bi-step-num" aria-hidden="true">{step.done ? '✓' : i + 1}</span>
            <b>{step.title}</b><span>{step.text}</span>{step.action}
          </li>
        ))}
      </ol>
      {dashboards.length > 0 && (
        <section className="bi-recent">
          <h2>Your dashboards</h2>
          <div className="bi-recent-grid">
            {dashboards.slice(0, 8).map((d, i) => (
              <a key={d.id} href={`#dash/${d.id}`} className="bi-dash-card" style={{ '--i': i }}>
                <span className="bi-dash-art" aria-hidden="true">{Array.from({ length: Math.max(1, Math.min(d.tiles || 1, 6)) }, (_, k) => <i key={k} />)}</span>
                <b>{d.name}</b><small className="muted">{d.tiles} visual{d.tiles === 1 ? '' : 's'}{d.folder ? ` · ${d.folder}` : ''}</small>
              </a>
            ))}
          </div>
        </section>
      )}
      <p className="muted small">Limits on this server: imports up to {info.limits.importMaxRows.toLocaleString()} records per dataset; a visual returns up to {info.limits.queryMaxRows.toLocaleString()} rows and stops after {info.limits.queryTimeoutSeconds} s.</p>
    </div>
  );
}
