// /admin → Overview: the repository, users and data sources at a glance, and a production checklist.
import { useEffect, useState } from 'react';
import { api } from '../api.js';
import { DbLogo, adapterTypeLabel } from '../shared/adapters.jsx';
import CapacityCard from './CapacityCard.jsx';
import { BRAND } from '../shared/brand.js';
import { TabIcon } from './AdminApp.jsx';

export default function OverviewTab({ who, notify, onOpen }) {
  const [data, setData] = useState(null);
  const [status, setStatus] = useState(null);
  const [keys, setKeys] = useState(null);
  useEffect(() => { api.apiKeys().then((r) => setKeys((r.keys || r.apiKeys || []).length)).catch(() => setKeys(null)); }, []);
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
  const plural = (n, word) => (data ? `${n} ${word}${n === 1 ? '' : 's'}` : '–');
  const tiles = [
    { id: 'server', icon: 'overview', tint: 'b', title: 'Server repository', text: `Published reports in ${plural(data?.folders.folders.length || 0, 'folder')}; open the Report Server`, count: plural(data?.reports.reports.length || 0, 'report'), go: () => window.location.assign('/server') },
    { id: 'users', icon: 'users', tint: 'v', title: 'Users & rights', text: data ? (Object.entries(byRole).map(([r, n]) => `${n} ${r}${n > 1 ? 's' : ''}`).join(' · ') || 'No users yet') : 'People, roles and rights', count: plural(data?.users.users.length || 0, 'person').replace('persons', 'people'), go: () => onOpen('users') },
    { id: 'keys', icon: 'keys', tint: 'b', title: 'API keys', text: 'Keys for apps, which reports each may call, expiry', count: keys === null ? '–' : plural(keys, 'key'), go: () => onOpen('keys') },
    { id: 'developer', icon: 'developer', tint: 'b', title: 'API & Postman', text: 'Every endpoint with examples; download the Postman collection', count: 'Docs', go: () => onOpen('developer') },
    { id: 'sources', icon: 'sources', tint: 'g', title: 'Data sources', text: needsSetup.length ? `${needsSetup.length} without a saved connection` : 'Each part has its own: Designer, Server, Analytics, Query', count: plural(data?.connections.connections.length || 0, 'adapter'), go: () => onOpen('sources') },
    { id: 'analytics', icon: 'analytics', tint: 'c', title: 'Datasets & dashboards', text: 'Refresh times, who may see each dashboard, storage used', count: 'Analytics', go: () => onOpen('analytics') },
    { id: 'systemdb', icon: 'systemdb', tint: 'c', title: 'System database', text: 'Where the service keeps its own tables', count: status?.storage || '–', go: () => onOpen('systemdb') },
    { id: 'limits', icon: 'limits', tint: 'v', title: 'Limits', text: 'Reports at once, rows, time limits, analytics and API rate', count: 'Live', go: () => onOpen('limits') },
    { id: 'branding', icon: 'branding', tint: 'a', title: 'Name & look', text: 'Product name, logo, colours, light or dark by default', count: BRAND.name, go: () => onOpen('branding') },
    { id: 'logs', icon: 'logs', tint: 'a', title: 'Logs & audit', text: 'Requests, report runs, service log, sign-ins and changes', count: status ? `${okCount}/${status.checks.length} ready` : 'History', go: () => onOpen('logs') },
  ];

  return (
    <>
      <div className="adm-tiles">
        {tiles.map((t, index) => (
          <button key={t.id} type="button" className={`adm-tile tint-${t.tint}`} style={{ '--i': index }} onClick={t.go}>
            <span className="adm-tile-top"><span className="adm-tile-ico"><TabIcon id={t.icon} /></span><em>{t.count}</em></span>
            <b>{t.title}</b>
            <small>{t.text}</small>
            <span className="adm-tile-go" aria-hidden="true">→</span>
          </button>
        ))}
      </div>

      <CapacityCard />

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
          <div className="card-head"><div><p className="eyebrow">Needs attention</p><h2>Data sources</h2></div><button type="button" className="btn sm" onClick={() => onOpen('sources')}>Open</button></div>
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
