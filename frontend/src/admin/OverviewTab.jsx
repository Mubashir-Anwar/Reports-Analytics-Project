// /admin → Overview: the repository, users and data sources at a glance, and a production checklist.
import { useEffect, useState } from 'react';
import { api } from '../api.js';
import { DbLogo, adapterTypeLabel } from '../shared/adapters.jsx';

export default function OverviewTab({ who, notify, onOpen }) {
  const [data, setData] = useState(null);
  const [status, setStatus] = useState(null);
  useEffect(() => {
    Promise.all([api.reports(), api.folders(), api.users(), api.connections()])
      .then(([reports, folders, users, connections]) => setData({ reports, folders, users, connections }))
      .catch((error) => notify(error.message, 'error'));
    api.systemStatus().then(setStatus).catch(() => setStatus(null));
  }, [notify]);

  const value = (pick) => (data ? pick(data) : '–');
  const byType = {};
  (data?.connections.connections || []).forEach((c) => { byType[c.type || 'mongodb'] = (byType[c.type || 'mongodb'] || 0) + 1; });
  const byRole = {};
  (data?.users.users || []).forEach((u) => { byRole[u.role] = (byRole[u.role] || 0) + 1; });
  const unused = (data?.connections.connections || []).filter((c) => !c.usedBy?.length).length;
  const needsSetup = (data?.connections.connections || []).filter((c) => !c.configured);
  const okCount = status ? status.checks.filter((c) => c.ok).length : 0;

  return (
    <>
      <div className="stats">
        <button type="button" className="card stat stat-link" onClick={() => window.location.assign('/server')}>
          <p className="eyebrow">Server repository</p><strong>{value((d) => d.reports.reports.length)}</strong>
          <span>published reports · {value((d) => d.folders.folders.length)} folders · open /server ↗</span>
        </button>
        <button type="button" className="card stat stat-link" onClick={() => onOpen('users')}>
          <p className="eyebrow">Users</p><strong>{value((d) => d.users.users.length)}</strong>
          <span>{data ? (Object.entries(byRole).map(([r, n]) => `${n} ${r}${n > 1 ? 's' : ''}`).join(' · ') || 'no users yet') : '–'}</span>
        </button>
        <button type="button" className="card stat stat-link" onClick={() => onOpen('sources')}>
          <p className="eyebrow">Data sources</p><strong>{value((d) => d.connections.connections.length)}</strong>
          <span className="type-row">{Object.entries(byType).map(([t, n]) => <span key={t} title={adapterTypeLabel(t)}><DbLogo type={t} /> {n}</span>)}</span>
        </button>
        <section className="card stat">
          <p className="eyebrow">Service</p><strong>{who.version}</strong>
          <span>{status ? `Storage: ${status.storage} · ` : ''}areas: {who.areas.join(', ')}</span>
        </section>
      </div>

      <div className="grid-2">
        <section className="card">
          <div className="card-head"><div><p className="eyebrow">Production checklist</p><h2>{status ? `${okCount} of ${status.checks.length} ready` : 'Checking…'}</h2></div></div>
          {status && (
            <ul className="checklist">
              {status.checks.map((c) => (
                <li key={c.id} className={c.ok ? 'ok' : 'todo'}>
                  <span className="check-icon" aria-hidden="true">{c.ok ? '✓' : '!'}</span>
                  <div><b>{c.label}</b> — {c.value}{!c.ok && <small>{c.hint}</small>}</div>
                </li>
              ))}
            </ul>
          )}
          <p className="muted">Details: DEPLOYMENT.md → “Keeping the data safe”.</p>
        </section>
        <section className="card">
          <div className="card-head"><div><p className="eyebrow">Needs attention</p><h2>Data sources</h2></div><button className="btn sm" onClick={() => onOpen('sources')}>Open</button></div>
          <ul className="checklist">
            <li className={needsSetup.length ? 'todo' : 'ok'}><span className="check-icon">{needsSetup.length ? '!' : '✓'}</span>
              <div><b>{needsSetup.length ? `${needsSetup.length} without a saved connection` : 'Every data source has a connection'}</b>{needsSetup.length > 0 && <small>{needsSetup.map((c) => c.name).join(', ')}</small>}</div></li>
            <li className="ok"><span className="check-icon">i</span><div><b>{unused} not used by any published report</b><small>They can be deleted when nobody needs them.</small></div></li>
          </ul>
          <h3 style={{ marginTop: 16 }}>How reports reach users</h3>
          <ol className="steps">
            <li><b>Designer</b> (<a href="/designer">/designer</a>) — reports are built in the designer's workspace; saving never changes the server.</li>
            <li><b>Publish to server</b> — the designer copies a report into a server folder.</li>
            <li><b>Server</b> (<a href="/server">/server</a>) — users sign in (Users tab), see the folders their permissions allow and run reports. Folder permissions: right-click a folder on /server → Permissions.</li>
          </ol>
        </section>
      </div>
    </>
  );
}
