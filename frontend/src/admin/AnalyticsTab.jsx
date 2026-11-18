// /admin → Analytics: every dataset and dashboard in one place for administrators (GET /api/admin/analytics).
// Import now, change or pause a dataset's schedule, delete it, give a dashboard a new owner, remove leftover files.
// Limits come from .env (ANALYTICS_*); this page shows them. Needs the Admin right only (not the Analytics right).
import { useCallback, useEffect, useState } from 'react';
import { request } from '../api.js';

const n = (value) => Number(value || 0).toLocaleString();
const when = (value) => (value ? new Date(value).toLocaleString() : '—');
function bytes(value) {
  if (value == null) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let size = Number(value);
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
  return `${size.toFixed(unit && size < 10 ? 1 : 0)} ${units[unit]}`;
}
const SCHEDULES = [[0, 'Only by hand'], [15, 'Every 15 minutes'], [30, 'Every 30 minutes'], [60, 'Every hour'], [180, 'Every 3 hours'],
  [360, 'Every 6 hours'], [720, 'Every 12 hours'], [1440, 'Every day'], [10080, 'Every week']];
const STATUS = { ready: ['ok', 'Ready'], failed: ['error', 'Failed'], queued: ['queued', 'Queued'], refreshing: ['running', 'Importing'], new: ['', 'Not imported'] };
const api = (path, method = 'GET', body) => request(path, method === 'GET' ? {} : { method, body: body ?? {} });

export default function AnalyticsTab({ notify }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');

  const load = useCallback(async () => {
    try { setData(await api('/api/admin/analytics')); setError(''); } catch (failure) { setError(failure.message); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const working = Boolean(data?.datasets.some((d) => d.busy || ['queued', 'refreshing'].includes(d.status)));
  useEffect(() => {                                   // follow imports while they run
    if (!working) return undefined;
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [working, load]);

  const act = async (key, call, done) => {
    setBusy(key);
    try { const result = await call(); if (done) notify(typeof done === 'function' ? done(result) : done, 'ok'); await load(); }
    catch (failure) { notify(failure.message, 'error'); } finally { setBusy(''); }
  };
  const refresh = (d) => act(`r${d.id}`, () => api(`/api/admin/analytics/datasets/${d.id}/refresh`, 'POST'), `Importing “${d.name}” now.`);
  const schedule = (d, minutes) => act(`s${d.id}`, () => api(`/api/admin/analytics/datasets/${d.id}/schedule`, 'PUT', { refreshEveryMinutes: minutes }),
    minutes ? `“${d.name}” is imported ${SCHEDULES.find(([m]) => m === minutes)?.[1].toLowerCase() || `every ${minutes} minutes`}.` : `Scheduled imports of “${d.name}” are paused.`);
  const remove = (d) => {
    const users = d.dashboards.length ? `\n\nIt is used by ${d.dashboards.length} dashboard(s): ${d.dashboards.slice(0, 6).join(', ')}. Their visuals on it will show an error.` : '';
    if (!window.confirm(`Delete the dataset “${d.name}” and its imported file?${users}\n\nThis cannot be undone.`)) return;
    act(`d${d.id}`, () => api(`/api/admin/analytics/datasets/${d.id}${d.dashboards.length ? '?force=true' : ''}`, 'DELETE'), `Dataset “${d.name}” deleted.`);
  };
  const owner = (b) => {
    const name = window.prompt(`New owner of the dashboard “${b.name}” (a username). The owner may change and delete it.`, b.ownerExists ? b.owner : '');
    if (!name?.trim()) return;
    act(`o${b.id}`, () => api(`/api/admin/analytics/dashboards/${b.id}/owner`, 'PUT', { owner: name.trim() }), (r) => `“${b.name}” now belongs to ${r.dashboard.owner}.`);
  };
  const cleanup = () => act('cleanup', () => api('/api/admin/analytics/cleanup', 'POST'),
    (r) => `Removed ${r.removed} leftover file(s), ${bytes(r.freedBytes)} freed.${r.stillOpen ? ` ${r.stillOpen} still open; try again later.` : ''}`);

  if (error) return <section className="card"><p className="eyebrow">Analytics</p><p className="muted">{error}</p></section>;
  if (!data) return <section className="card"><p className="eyebrow">Analytics</p><p className="muted">Loading…</p></section>;
  const { totals, storage, limits } = data;
  const lowDisk = storage.freeBytes != null && storage.freeBytes < 2 * 1024 ** 3;

  return (
    <div className="stack admin-bi">
      {!data.enabled && <div className="notice warn">Analytics is switched off on this server (REPORTING_ENABLED_AREAS has no <code>analytics</code>). Its data is kept and shown here.</div>}
      <div className="stats">
        <div className="card stat"><span>Datasets</span><strong>{totals.datasets}</strong><span>{totals.ready} ready · {totals.busy} importing · {totals.scheduled} on a schedule</span></div>
        <div className={`card stat ${totals.failed ? 'stat-warn' : ''}`}><span>Problems</span><strong>{totals.failed}</strong><span>{totals.failed ? 'datasets whose last import failed (see below)' : 'every last import worked'}</span></div>
        <div className="card stat"><span>Dashboards</span><strong>{totals.dashboards}</strong><span>{totals.relationships} relationship(s) in the model</span></div>
        <div className="card stat"><span>Imported records</span><strong>{n(totals.rows)}</strong><span>in {storage.files} file(s), {bytes(storage.bytes)} on disk</span></div>
        <div className={`card stat ${lowDisk ? 'stat-warn' : ''}`}><span>Free disk space</span><strong>{bytes(storage.freeBytes)}</strong><span>where the files are kept{lowDisk ? ' — getting low' : ''}</span></div>
      </div>

      <section className="card">
        <div className="card-head"><div><p className="eyebrow">Datasets</p><h2>Imports and schedules</h2></div>
          <button type="button" className="btn sm" onClick={load}>↻ Refresh</button></div>
        {data.datasets.length === 0 ? <div className="empty">No datasets yet. They are made in Analytics by people with the Analytics and Designer (or Admin) rights.</div> : (
          <div className="table-wrap">
            <table className="data bi-admin-table">
              <thead><tr>{['Dataset', 'Source', 'Status', 'Records', 'Size', 'Last import', 'Schedule', 'Used by', ''].map((h) => <th key={h}>{h}</th>)}</tr></thead>
              <tbody>
                {data.datasets.map((d) => {
                  const [tone, label] = d.busy ? ['running', 'Importing'] : STATUS[d.status] || ['', d.status];
                  const options = SCHEDULES.some(([m]) => m === d.refreshEveryMinutes) ? SCHEDULES : [...SCHEDULES, [d.refreshEveryMinutes, `Every ${d.refreshEveryMinutes} minutes`]];
                  return (
                    <tr key={d.id}>
                      <td><b>{d.name}</b><br /><small className="muted">/{d.folder} · by {d.createdBy || '?'}</small></td>
                      <td><small>{d.connectionId}{d.collection ? ` · ${d.collection}` : ''}<br /><span className="muted">{d.queryLanguage}</span></small></td>
                      <td><span className={`status-chip ${tone}`}>{label}</span>{d.lastError && <><br /><small className="bi-error" title={d.lastError}>{d.lastError.slice(0, 120)}</small></>}</td>
                      <td>{n(d.rowCount)}</td>
                      <td>{bytes(d.sizeBytes)}</td>
                      <td><small>{when(d.refreshedAt)}</small></td>
                      <td>
                        <select aria-label={`Schedule of ${d.name}`} value={d.refreshEveryMinutes || 0} disabled={busy === `s${d.id}`}
                          onChange={(e) => schedule(d, Number(e.target.value))}>
                          {options.filter(([m]) => !m || m >= limits.minRefreshMinutes).map(([m, text]) => <option key={m} value={m}>{text}</option>)}
                        </select>
                        {d.nextRefreshAt && <><br /><small className="muted">next {when(d.nextRefreshAt)}</small></>}
                      </td>
                      <td><small>{d.dashboards.length ? d.dashboards.join(', ') : <span className="muted">no dashboard</span>}</small></td>
                      <td className="nowrap">
                        <button type="button" className="btn sm" disabled={d.busy || busy === `r${d.id}`} onClick={() => refresh(d)}>Import now</button>{' '}
                        <button type="button" className="btn sm danger" disabled={busy === `d${d.id}`} onClick={() => remove(d)}>Delete</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card">
        <div className="card-head"><div><p className="eyebrow">Dashboards</p><h2>Owners</h2></div></div>
        <p className="muted small">The owner of a dashboard may change and delete it (administrators may change any). Give a dashboard a new owner when someone leaves. Who may <i>see</i> a dashboard is set by the folder permissions on the Report Server.</p>
        {data.dashboards.length === 0 ? <div className="empty">No dashboards yet.</div> : (
          <div className="table-wrap">
            <table className="data">
              <thead><tr>{['Dashboard', 'Folder', 'Owner', 'Visuals', 'Last changed', ''].map((h) => <th key={h}>{h}</th>)}</tr></thead>
              <tbody>
                {data.dashboards.map((b) => (
                  <tr key={b.id}>
                    <td><b>{b.name}</b></td><td>/{b.folder}</td>
                    <td>{b.owner || '—'}{!b.ownerExists && <> <span className="status-chip error" title="This user no longer exists">no such user</span></>}</td>
                    <td>{b.tiles}</td><td><small>{when(b.updatedAt)}{b.updatedBy ? ` by ${b.updatedBy}` : ''}</small></td>
                    <td className="nowrap"><button type="button" className="btn sm" disabled={busy === `o${b.id}`} onClick={() => owner(b)}>Change owner</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card">
        <div className="card-head"><div><p className="eyebrow">History</p><h2>Recent imports</h2></div></div>
        {data.recentImports.length === 0 ? <div className="empty">No imports yet.</div> : (
          <div className="table-wrap" style={{ maxHeight: '40vh' }}>
            <table className="data">
              <thead><tr>{['Started', 'Dataset', 'Why', 'By', 'Status', 'Records', 'Took', 'Error'].map((h) => <th key={h}>{h}</th>)}</tr></thead>
              <tbody>
                {data.recentImports.map((r) => (
                  <tr key={r.id} className={r.status === 'failed' ? 'failed' : ''}>
                    <td>{when(r.startedAt)}</td><td>{r.dataset}</td><td>{r.trigger}</td><td>{r.actor}</td><td>{r.status}</td>
                    <td>{r.rowCount != null ? n(r.rowCount) : ''}</td><td>{r.durationMs != null ? `${(r.durationMs / 1000).toFixed(1)} s` : ''}</td><td>{r.error || ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card">
        <div className="card-head"><div><p className="eyebrow">Storage and limits</p><h2>Files and settings</h2></div>
          <button type="button" className="btn sm" disabled={!storage.strayFiles || busy === 'cleanup'} onClick={cleanup}
            title="Files no dataset uses any more (left after a crash or a delete while a file was open)">
            Remove {storage.strayFiles} leftover file(s){storage.strayFiles ? ` (${bytes(storage.strayBytes)})` : ''}</button></div>
        <p className="muted small">Imported data is kept in <code>{storage.directory}</code>. Back it up with the data folder, or import the datasets again after a restore. The limits below are set in <code>.env</code> (DEPLOYMENT.md → “Analytics”) and need a restart to change.</p>
        <table className="cap-table">
          <tbody>
            <tr><td>ANALYTICS_IMPORT_MAX_ROWS</td><td>{n(limits.importMaxRows)}</td><td>records one import may read</td></tr>
            <tr><td>ANALYTICS_IMPORT_MAX_TIME_MS</td><td>{n(limits.importMaxTimeMs)}</td><td>time one import may take</td></tr>
            <tr><td>ANALYTICS_QUERY_MAX_ROWS</td><td>{n(limits.queryMaxRows)}</td><td>rows one visual returns</td></tr>
            <tr><td>ANALYTICS_QUERY_TIMEOUT_SECONDS</td><td>{limits.queryTimeoutSeconds}</td><td>time one visual may take</td></tr>
            <tr><td>ANALYTICS_MEMORY_LIMIT / ANALYTICS_THREADS</td><td>{limits.memoryLimit} / {limits.threads}</td><td>memory and CPU of one query</td></tr>
            <tr><td>ANALYTICS_REFRESH_WORKERS</td><td>{limits.refreshWorkers}</td><td>imports at the same time</td></tr>
            <tr><td>ANALYTICS_MIN_REFRESH_MINUTES</td><td>{limits.minRefreshMinutes}</td><td>shortest schedule</td></tr>
          </tbody>
        </table>
      </section>
    </div>
  );
}
