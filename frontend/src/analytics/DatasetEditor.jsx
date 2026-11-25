// A dataset: where its data comes from (data adapter + query, the designer's query languages), its refresh, its
// columns (the model: label, role, summary, format, hidden), a look at the imported data and the refresh history.
import { useCallback, useEffect, useState } from 'react';
import { AdapterWizard, DbLogo } from '../shared/adapters.jsx';
import { FolderPicker } from '../shared/folders.jsx';
import { takeHandoff } from '../query/handoff.js';
import { AGG_LABELS, bi, describeBytes, KIND_ICON } from './api.js';

const LANGUAGE_NAMES = { 'aggregation-json': 'Aggregation pipeline (JSON)', mongosh: 'Mongo shell (db.coll.aggregate…)',
  javascript: 'JavaScript pipeline (pipeline = [ … ];)', python: 'Python pipeline (pipeline = [ … ])', sql: 'SQL' };
// An example per language: shown when the query is empty or still the example of the language chosen before.
const EXAMPLES = {
  'aggregation-json': '[\n  { "$match": {} }\n]',
  mongosh: 'db.getCollection("orders").aggregate([\n  { $match: {} },\n  { $project: { _id: 0 } }\n])',
  javascript: 'pipeline = [\n  { $match: {} },\n  { $project: { _id: 0 } }\n];',
  python: 'pipeline = [\n    {"$match": {}},\n    {"$project": {"_id": 0}},\n]',
  sql: 'SELECT *\nFROM my_table',
};
const isExample = (text) => !text.trim() || Object.values(EXAMPLES).includes(text);
const SCHEDULES = [[0, 'Only by hand'], [15, 'Every 15 minutes'], [30, 'Every 30 minutes'], [60, 'Every hour'], [360, 'Every 6 hours'],
  [720, 'Every 12 hours'], [1440, 'Every day'], [10080, 'Every week']];
const STATUS = { new: ['Not imported', ''], queued: ['Waiting to import', 'warn'], refreshing: ['Importing…', 'warn'], ready: ['Ready', 'ok'], failed: ['Import failed', 'error'] };
const when = (iso) => (iso ? new Date(iso).toLocaleString() : '—');

const EMPTY = { name: '', folder: 'Analytics', description: '', connectionId: '', queryLanguage: 'aggregation-json', collection: '', querySource: '[\n  { "$match": {} }\n]', refreshEveryMinutes: 0 };

export default function DatasetEditor({ datasetId, info, folders = [], notify, onConnected, onSaved, onDeleted }) {
  const isNew = datasetId === 'new';
  const [item, setItem] = useState(isNew ? null : undefined);
  const [form, setForm] = useState(EMPTY);
  const [columns, setColumns] = useState([]);
  const [tab, setTab] = useState('source');
  const [test, setTest] = useState(null);
  const [rows, setRows] = useState(null);
  const [log, setLog] = useState(null);
  const [busy, setBusy] = useState('');
  const mayModel = info.mayModel;

  const load = useCallback(async () => {
    if (isNew) return;
    const { dataset } = await bi.dataset(datasetId);
    setItem(dataset);
    setColumns(dataset.columns);
    setForm((f) => (f.__id === dataset.id ? f : { __id: dataset.id, ...Object.fromEntries(Object.keys(EMPTY).map((k) => [k, dataset[k] ?? EMPTY[k]])) }));
    return dataset;
  }, [datasetId, isNew]);

  useEffect(() => {
    setTab('source'); setTest(null); setRows(null); setLog(null);
    if (isNew) {
      const given = takeHandoff('analytics');          // “Use in Analytics” of the Query page
      if (given) {
        setItem(null); setColumns([]);
        setForm({ ...EMPTY, name: given.name || given.collection || 'From Query', connectionId: given.connectionId, queryLanguage: given.queryLanguage, collection: given.collection || '', querySource: given.querySource });
        if (!info.adapters.some((a) => a.id === given.connectionId)) notify('The data source of this query is not available here: choose another one.', 'warn');
        else if (/\$[PX]!?\{/.test(given.querySource)) notify('This query has $P{…} parameters. A dataset has none: write values in their place, then Test.', 'warn');
        else notify('The query from the Query page is filled in: check the name and create the dataset.', 'ok');
        return;
      }
      const first = info.adapters[0];
      setItem(null); setColumns([]);
      setForm({ ...EMPTY, connectionId: first?.id || '', queryLanguage: first && first.type !== 'mongodb' ? 'sql' : 'aggregation-json',
        querySource: first && first.type !== 'mongodb' ? 'SELECT *\nFROM my_table' : EMPTY.querySource });
    } else {
      load().catch((e) => notify(e.message, 'error'));
    }
  }, [datasetId, isNew, load, notify]); // eslint-disable-line react-hooks/exhaustive-deps -- a new data source must not empty the form

  const [connecting, setConnecting] = useState(false);
  const connected = async (saved) => {
    setConnecting(false);
    await onConnected?.();
    const language = saved.type !== 'mongodb' ? 'sql' : (form.queryLanguage === 'sql' ? 'aggregation-json' : form.queryLanguage);
    set({ connectionId: saved.id, queryLanguage: language, ...(isExample(form.querySource) ? { querySource: EXAMPLES[language] } : {}) });
  };

  // while an import runs, look again every 2 seconds
  const working = item && (item.status === 'queued' || item.status === 'refreshing' || item.busy);
  useEffect(() => {
    if (!working) return undefined;
    const timer = setInterval(() => { load().then((d) => { if (d && d.status === 'ready') onSaved?.(); }).catch(() => {}); }, 2000);
    return () => clearInterval(timer);
  }, [working, load, onSaved]);

  useEffect(() => {
    if (isNew) return;
    if (tab === 'data') bi.rows(datasetId, 100).then(setRows).catch((e) => setRows({ error: e.message }));
    if (tab === 'history') bi.refreshes(datasetId).then((r) => setLog(r.refreshes)).catch((e) => notify(e.message, 'error'));
  }, [tab, datasetId, isNew, item?.refreshedAt, item?.status, notify]);

  if (item === undefined) return <section className="card"><p className="muted">Loading…</p></section>;
  const adapter = info.adapters.find((a) => a.id === form.connectionId);
  const isSql = adapter ? adapter.type !== 'mongodb' : form.queryLanguage === 'sql';
  const set = (change) => setForm((f) => ({ ...f, ...change }));
  const source = { connectionId: form.connectionId, queryLanguage: form.queryLanguage, collection: form.collection, querySource: form.querySource };

  const runTest = async () => {
    setBusy('test'); setTest(null);
    try { setTest(await bi.testSource(source)); } catch (error) { setTest({ error: error.message }); } finally { setBusy(''); }
  };
  const save = async () => {
    setBusy('save');
    try {
      const body = { name: form.name, folder: form.folder, description: form.description, refreshEveryMinutes: Number(form.refreshEveryMinutes), ...source };
      if (isNew) {
        const { dataset } = await bi.createDataset(body);
        notify(`Dataset “${dataset.name}” created; its data is being imported.`, 'ok');
        onSaved?.(dataset.id);
      } else {
        const { dataset, sourceChanged } = await bi.updateDataset(datasetId, body);
        setItem(dataset);
        notify(sourceChanged ? 'Saved; the data is being imported again with the new query.' : 'Saved.', 'ok');
        onSaved?.();
      }
    } catch (error) { notify(error.message, 'error'); } finally { setBusy(''); }
  };
  const saveModel = async () => {
    setBusy('model');
    try { const { dataset } = await bi.updateDataset(datasetId, { columns }); setItem(dataset); setColumns(dataset.columns); notify('Columns saved.', 'ok'); onSaved?.(); }
    catch (error) { notify(error.message, 'error'); } finally { setBusy(''); }
  };
  const refresh = async () => {
    try { const { dataset } = await bi.refresh(datasetId); setItem(dataset); } catch (error) { notify(error.message, 'error'); }
  };
  const remove = async () => {
    if (!window.confirm(`Delete the dataset “${item.name}” and its imported data? This cannot be undone.`)) return;
    try { await bi.deleteDataset(datasetId); notify('Dataset deleted.', 'ok'); onDeleted?.(); }
    catch (error) {
      if (error.status === 409 && window.confirm(`${error.message}\n\nDelete it anyway? Those visuals will show an error.`)) {
        await bi.deleteDataset(datasetId, true); notify('Dataset deleted.', 'ok'); onDeleted?.();
      } else notify(error.message, 'error');
    }
  };
  const patchColumn = (name, change) => setColumns((list) => list.map((c) => (c.name === name ? { ...c, ...change } : c)));
  const [label, kind] = STATUS[item?.status || 'new'] || STATUS.new;
  const schedules = SCHEDULES.filter(([m]) => m === 0 || m >= info.limits.minRefreshMinutes);

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <p className="eyebrow">Dataset</p>
          <h1>{isNew ? 'New dataset' : item.name}</h1>
          {!isNew && (
            <p className="muted ds-facts">
              <span className={`status-chip ${kind}`}>{label}</span>
              {' '}{(item.rowCount || 0).toLocaleString()} records · {describeBytes(item.sizeBytes)} · imported {when(item.refreshedAt)}
              {item.refreshEveryMinutes ? ` · next ${when(item.nextRefreshAt)}` : ''}
            </p>
          )}
        </div>
        {!isNew && mayModel && (
          <div className="row">
            <button type="button" className="btn" onClick={refresh} disabled={working}>{working ? 'Importing…' : 'Refresh now'}</button>
            <button type="button" className="btn danger" onClick={remove}>Delete</button>
          </div>
        )}
      </div>
      {item?.lastError && <div className="notice error" role="alert">Last import: {item.lastError}</div>}

      {!isNew && (
        <nav className="tabs" role="tablist">
          {[['source', 'Source'], ['columns', `Columns (${columns.length})`], ['data', 'Data'], ['history', 'Refresh history']].map(([id, name]) => (
            <button key={id} type="button" role="tab" aria-selected={tab === id} className={tab === id ? 'on' : ''} onClick={() => setTab(id)}>{name}</button>
          ))}
        </nav>
      )}

      {tab === 'source' && (
        <section className="card stack">
          {!mayModel && <p className="muted">Datasets are made by people with the Designer or Admin right.</p>}
          <div className="grid-2 tight">
            <label className="field"><span>Name</span><input value={form.name} maxLength={120} disabled={!mayModel} onChange={(e) => set({ name: e.target.value })} placeholder="Sales orders" /></label>
            <div className="field"><span>Folder</span>
              <FolderPicker folders={folders} value={form.folder} disabled={!mayModel} allowNew rootLabel="Analytics" rootHint="the usual folder" onChange={(folder) => set({ folder })} />
              <small className="hint">Where the dataset is filed, like a report on the server: choose a folder or type a new one. Nothing to enter here about API keys; an API key limited to some folders sees only the datasets in them.</small>
            </div>
          </div>
          <label className="field"><span>Description</span><input value={form.description} maxLength={500} disabled={!mayModel} onChange={(e) => set({ description: e.target.value })} /></label>
          {mayModel && (
            <>
              <div className="grid-2 tight">
                <label className="field"><span>Data adapter</span>
                  <select value={form.connectionId} onChange={(e) => {
                    const next = info.adapters.find((a) => a.id === e.target.value);
                    const language = next && next.type !== 'mongodb' ? 'sql' : (form.queryLanguage === 'sql' ? 'aggregation-json' : form.queryLanguage);
                    set({ connectionId: e.target.value, queryLanguage: language, ...(isExample(form.querySource) ? { querySource: EXAMPLES[language] } : {}) });
                  }}>
                    {info.adapters.length === 0 && <option value="">No data source yet: connect one</option>}
                    {info.adapters.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.type})</option>)}
                  </select>
                  <button type="button" className="link-btn" onClick={(e) => { e.preventDefault(); setConnecting(true); }}>＋ Connect a database</button>
                </label>
                <label className="field"><span>Query language</span>
                  <select value={form.queryLanguage} onChange={(e) => set({ queryLanguage: e.target.value, ...(isExample(form.querySource) ? { querySource: EXAMPLES[e.target.value] || '' } : {}) })}>
                    {info.languages.filter((l) => (isSql ? l === 'sql' : l !== 'sql')).map((l) => <option key={l} value={l}>{LANGUAGE_NAMES[l] || l}</option>)}
                  </select>
                </label>
              </div>
              {!isSql && (
                <label className="field"><span>Collection</span><input value={form.collection} maxLength={200} onChange={(e) => set({ collection: e.target.value })} placeholder="orders (or named in a mongosh / runCommand query)" /></label>
              )}
              <label className="field"><span>Query {adapter && <DbLogo type={adapter.type} size={12} />}</span>
                <textarea className="code-area" rows={10} spellCheck={false} value={form.querySource} onChange={(e) => set({ querySource: e.target.value })} />
              </label>
              <p className="muted small">Bring only the fields and records the dashboards need: filter and choose fields in the query ($match / $project, or WHERE and the column list). Everything the query returns is imported, up to {info.limits.importMaxRows.toLocaleString()} records.</p>
              <label className="field"><span>Refresh</span>
                <select value={form.refreshEveryMinutes} onChange={(e) => set({ refreshEveryMinutes: Number(e.target.value) })}>
                  {schedules.map(([m, name]) => <option key={m} value={m}>{name}</option>)}
                </select>
              </label>
              <div className="row">
                <button type="button" className="btn" onClick={runTest} disabled={busy !== '' || !form.connectionId}>{busy === 'test' ? 'Testing…' : 'Test the query'}</button>
                <span className="spacer" />
                <button type="button" className="btn primary" onClick={save} disabled={busy !== '' || !form.name.trim() || !form.connectionId}>
                  {busy === 'save' ? 'Saving…' : isNew ? 'Create and import' : 'Save'}
                </button>
              </div>
              {test?.error && <div className="notice error" role="alert">{test.error}</div>}
              {test && !test.error && <SampleTable data={test} caption={`First ${test.rowCount} records of the query (nothing is imported yet)`} />}
            </>
          )}
        </section>
      )}

      {tab === 'columns' && (
        <section className="card stack">
          {columns.length === 0 ? <p className="empty">The columns appear after the first import.</p> : (
            <>
              <p className="muted">Name the columns for people, say which are <b>measures</b> (numbers to add up) and which <b>dimensions</b> (to group and filter by), and hide what nobody needs.</p>
              <div className="table-wrap">
                <table className="grid-table col-model">
                  <thead><tr><th>Column</th><th>Type</th><th>Label</th><th>Role</th><th>Default summary</th><th>Hidden</th></tr></thead>
                  <tbody>
                    {columns.map((c) => (
                      <tr key={c.name}>
                        <td><code>{c.name}</code></td>
                        <td><span className="kind">{KIND_ICON[c.kind]}</span> {c.type}</td>
                        <td><input value={c.label} maxLength={120} disabled={!mayModel} aria-label={`Label of ${c.name}`} onChange={(e) => patchColumn(c.name, { label: e.target.value })} /></td>
                        <td><select value={c.role} disabled={!mayModel} aria-label={`Role of ${c.name}`} onChange={(e) => patchColumn(c.name, { role: e.target.value })}><option value="dimension">Dimension</option><option value="measure">Measure</option></select></td>
                        <td><select value={c.agg} disabled={!mayModel} aria-label={`Default summary of ${c.name}`} onChange={(e) => patchColumn(c.name, { agg: e.target.value })}>
                          {Object.entries(AGG_LABELS).filter(([a]) => a !== 'countrows' && (c.kind === 'number' || !['sum', 'avg', 'median'].includes(a))).map(([a, n]) => <option key={a} value={a}>{n}</option>)}
                        </select></td>
                        <td><input type="checkbox" checked={c.hidden} disabled={!mayModel} aria-label={`Hide ${c.name}`} onChange={(e) => patchColumn(c.name, { hidden: e.target.checked })} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {mayModel && <div className="row"><span className="spacer" /><button type="button" className="btn primary" onClick={saveModel} disabled={busy !== ''}>{busy === 'model' ? 'Saving…' : 'Save columns'}</button></div>}
            </>
          )}
        </section>
      )}

      {tab === 'data' && (
        <section className="card">
          {!rows ? <p className="muted">Loading…</p> : rows.error ? <div className="notice error">{rows.error}</div>
            : rows.rowCount === 0 ? <p className="empty">No imported data yet.</p> : <SampleTable data={rows} caption={`First ${rows.rowCount} of ${(item.rowCount || 0).toLocaleString()} imported records`} />}
        </section>
      )}

      {tab === 'history' && (
        <section className="card">
          {!log ? <p className="muted">Loading…</p> : log.length === 0 ? <p className="empty">No imports yet.</p> : (
            <div className="table-wrap">
              <table className="grid-table">
                <thead><tr><th>Started</th><th>How</th><th>By</th><th>Result</th><th className="num">Records</th><th className="num">Size</th><th className="num">Took</th></tr></thead>
                <tbody>{log.map((r) => (
                  <tr key={r.id}>
                    <td>{when(r.startedAt)}</td><td>{r.trigger}</td><td>{r.actor}</td>
                    <td><span className={`status-chip ${r.status === 'success' ? 'ok' : 'error'}`}>{r.status}</span>{r.error && <small className="muted"> {r.error}</small>}</td>
                    <td className="num">{(r.rowCount || 0).toLocaleString()}</td><td className="num">{describeBytes(r.sizeBytes)}</td>
                    <td className="num">{r.durationMs ? `${(r.durationMs / 1000).toFixed(1)} s` : '—'}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
        </section>
      )}
      {connecting && <AdapterWizard initial={{}} area="analytics" existingIds={info.adapters.map((a) => a.id)} notify={notify} onClose={() => setConnecting(false)} onSaved={connected} />}
    </div>
  );
}

function SampleTable({ data, caption }) {
  return (
    <div className="table-wrap sample">
      <table className="grid-table">
        <caption className="muted small">{caption}</caption>
        <thead><tr>{data.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>{data.rows.map((row, i) => <tr key={i}>{row.map((v, j) => <td key={j}>{v === null || v === undefined ? <span className="muted">null</span> : String(v)}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}
