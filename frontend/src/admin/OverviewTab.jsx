// /admin → Overview: the repository, users and data sources at a glance, and a production checklist.
import { useEffect, useMemo, useState } from 'react';
import { api } from '../api.js';
import { AREAS, DbLogo, adapterTypeLabel } from '../shared/adapters.jsx';
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

      <div className="ov-grid">
        <ChecklistCard status={status} notify={notify} />
        <SourcesCard connections={data?.connections.connections} drivers={status?.drivers} onOpen={onOpen} notify={notify} />
      </div>
      <FlowCard />
    </>
  );
}


/** A ring that fills with the share of checks that are ready. */
function Ring({ done, total }) {
  const r = 30, c = 2 * Math.PI * r, share = total ? done / total : 0;
  const tone = share === 1 ? 'ok' : share >= 0.5 ? 'mid' : 'low';
  return (
    <svg className={`ov-ring ${tone}`} viewBox="0 0 76 76" width="76" height="76" role="img" aria-label={`${done} of ${total} ready`}>
      <circle cx="38" cy="38" r={r} className="track" />
      <circle cx="38" cy="38" r={r} className="fill" strokeDasharray={`${c * share} ${c}`} transform="rotate(-90 38 38)" />
      <text x="38" y="36" textAnchor="middle" className="big">{Math.round(share * 100)}%</text>
      <text x="38" y="50" textAnchor="middle" className="small">{done}/{total}</text>
    </svg>
  );
}

const GROUP_NAMES = { security: ['🛡️', 'Security'], operations: ['⚙️', 'Operations'] };

function ChecklistCard({ status, notify }) {
  const [onlyTodo, setOnlyTodo] = useState(false);
  const [open, setOpen] = useState('');
  const checks = status?.checks || [];
  const ok = checks.filter((c) => c.ok).length;
  const todo = checks.filter((c) => !c.ok);
  const envLines = todo.map((c) => c.fix).filter(Boolean).join('\n');
  const copy = (text, what) => { navigator.clipboard?.writeText(text); notify(`Copied ${what}`, 'ok'); };
  return (
    <section className="card ov-card">
      <div className="ov-head">
        {status ? <Ring done={ok} total={checks.length} /> : <span className="ov-ring-wait" />}
        <div>
          <p className="eyebrow">Production checklist</p>
          <h2>{status ? (todo.length ? `${todo.length} thing${todo.length > 1 ? 's' : ''} to do` : 'Ready for production 🎉') : 'Checking…'}</h2>
          <p className="muted">{status ? `Version ${status.version} · storage ${status.storage}` : ''}</p>
        </div>
      </div>
      {status && (
        <div className="ov-tools">
          <div className="ov-seg" role="radiogroup" aria-label="Show">
            <button type="button" role="radio" aria-checked={!onlyTodo} className={!onlyTodo ? 'on' : ''} onClick={() => setOnlyTodo(false)}>All {checks.length}</button>
            <button type="button" role="radio" aria-checked={onlyTodo} className={onlyTodo ? 'on' : ''} onClick={() => setOnlyTodo(true)}>To do {todo.length}</button>
          </div>
          {envLines && <button type="button" className="btn sm" onClick={() => copy(envLines, 'the .env lines')} title={envLines}>Copy .env lines for the to-dos</button>}
        </div>
      )}
      {Object.entries(GROUP_NAMES).map(([group, [icon, name]]) => {
        const items = checks.filter((c) => (c.group || 'operations') === group && (!onlyTodo || !c.ok));
        if (!items.length) return null;
        const done = checks.filter((c) => (c.group || 'operations') === group && c.ok).length;
        const all = checks.filter((c) => (c.group || 'operations') === group).length;
        return (
          <div key={group} className="ov-group">
            <h3><span aria-hidden="true">{icon}</span>{name}<small>{done}/{all}</small><span className="ov-bar"><i style={{ width: `${(done / all) * 100}%` }} /></span></h3>
            <ul className="ov-checks">
              {items.map((c) => (
                <li key={c.id} className={c.ok ? 'ok' : 'todo'}>
                  <button type="button" className="ov-check" aria-expanded={open === c.id} disabled={c.ok} onClick={() => setOpen(open === c.id ? '' : c.id)}>
                    <span className="check-icon" aria-hidden="true">{c.ok ? '✓' : '!'}</span>
                    <span className="ov-check-text"><b>{c.label}</b><small>{c.value}</small></span>
                    {!c.ok && <span className="ov-fix-tag">How to fix {open === c.id ? '▴' : '▾'}</span>}
                  </button>
                  {open === c.id && !c.ok && (
                    <div className="ov-fix">
                      <p>{c.hint}</p>
                      {c.fix && <div className="ov-env"><pre>{c.fix}</pre><button type="button" className="btn sm" onClick={() => copy(c.fix, `${c.label} setting`)}>Copy</button></div>}
                      <small className="muted">Put it in .env (or the server environment), then restart. Details: DEPLOYMENT.md → “Keeping the data safe”.</small>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </div>
        );
      })}
      {onlyTodo && !todo.length && status && <p className="ov-empty">Nothing left to do 🎉</p>}
    </section>
  );
}

function SourcesCard({ connections, drivers, onOpen, notify }) {
  const [health, setHealth] = useState({}); // id → { state: busy|ok|err, text }
  const [testing, setTesting] = useState(false);
  const [showDrivers, setShowDrivers] = useState(false);
  const list = connections || [];
  const byType = useMemo(() => {
    const map = {};
    list.forEach((c) => { const t = c.type || 'mongodb'; map[t] = (map[t] || 0) + 1; });
    return Object.entries(map).sort((a, b) => b[1] - a[1]);
  }, [list]);
  const byArea = useMemo(() => {
    const map = { designer: 0, server: 0, analytics: 0, query: 0, '': 0 };
    list.forEach((c) => { map[c.area || ''] = (map[c.area || ''] || 0) + 1; });
    return map;
  }, [list]);
  const missing = list.filter((c) => !c.configured);
  const unused = list.filter((c) => !c.usedBy?.length && !Object.values(c.uses || {}).some((u) => u?.length));
  const failed = Object.values(health).filter((h) => h.state === 'err').length;
  const passed = Object.values(health).filter((h) => h.state === 'ok').length;
  const testAll = async () => {
    setTesting(true);
    const targets = list.filter((c) => c.configured);
    setHealth(Object.fromEntries(targets.map((c) => [c.id, { state: 'busy' }])));
    for (let i = 0; i < targets.length; i += 4) {        // four at a time
      await Promise.all(targets.slice(i, i + 4).map(async (c) => {
        try { const r = await api.testConnection(c.id); setHealth((h) => ({ ...h, [c.id]: { state: 'ok', text: r.message || 'Connected' } })); }
        catch (e) { setHealth((h) => ({ ...h, [c.id]: { state: 'err', text: e.message } })); }
      }));
    }
    setTesting(false);
    notify('Every data source was tested', 'ok');
  };
  const max = Math.max(1, ...byType.map(([, n]) => n));
  return (
    <section className="card ov-card">
      <div className="card-head">
        <div><p className="eyebrow">Data sources</p><h2>{connections ? `${list.length} connected` : 'Loading…'}</h2></div>
        <div className="ov-actions">
          <button type="button" className="btn sm" disabled={testing || !list.length} onClick={testAll}>{testing ? 'Testing…' : '⚡ Test all'}</button>
          <button type="button" className="btn sm" onClick={() => onOpen('sources')}>Open</button>
        </div>
      </div>
      {(passed + failed > 0) && <p className={`ov-health ${failed ? 'bad' : 'good'}`}>{failed ? `✕ ${failed} cannot connect` : '✓ All reachable'}{passed ? ` · ${passed} ok` : ''}</p>}
      <div className="ov-areas">
        {Object.entries(AREAS).map(([key, a]) => <div key={key}><b>{byArea[key] || 0}</b><small>{a.name}</small></div>)}
        {byArea[''] > 0 && <div title="Older data sources every part may use"><b>{byArea['']}</b><small>Shared</small></div>}
      </div>
      <ul className="ov-types">
        {byType.map(([type, n]) => (
          <li key={type}><DbLogo type={type} size={18} /><span>{adapterTypeLabel(type)}</span><span className="ov-bar"><i style={{ width: `${(n / max) * 100}%` }} /></span><b>{n}</b></li>
        ))}
      </ul>
      <ul className="ov-checks ov-src">
        <li className={missing.length ? 'todo' : 'ok'}><div className="ov-check static"><span className="check-icon">{missing.length ? '!' : '✓'}</span>
          <span className="ov-check-text"><b>{missing.length ? `${missing.length} without a saved connection` : 'Every data source has a connection'}</b>{missing.length > 0 && <small>{missing.map((c) => c.name).join(', ')}</small>}</span></div></li>
        <li className={unused.length ? 'info' : 'ok'}><div className="ov-check static"><span className="check-icon">{unused.length ? 'i' : '✓'}</span>
          <span className="ov-check-text"><b>{unused.length ? `${unused.length} not used anywhere` : 'Every data source is in use'}</b>{unused.length > 0 && <small>{unused.slice(0, 6).map((c) => c.name).join(', ')}{unused.length > 6 ? ' …' : ''} — they can be deleted when nobody needs them</small>}</span></div></li>
      </ul>
      {Object.keys(health).length > 0 && (
        <ul className="ov-live">
          {list.filter((c) => health[c.id]).map((c) => (
            <li key={c.id} className={health[c.id].state} title={health[c.id].text}><DbLogo type={c.type || 'mongodb'} size={14} /><span>{c.name}</span><em>{health[c.id].state === 'busy' ? '…' : health[c.id].state === 'ok' ? '●' : '✕'}</em></li>
          ))}
        </ul>
      )}
      {drivers && (
        <details className="ov-drivers" open={showDrivers} onToggle={(e) => setShowDrivers(e.target.open)}>
          <summary>Database drivers · {drivers.filter((d) => d.installed).length}/{drivers.length} installed</summary>
          <ul>{drivers.map((d) => <li key={d.type} className={d.installed ? 'ok' : 'todo'}><DbLogo type={d.type} size={14} /><span>{d.label}</span><code>{d.installed ? d.version : `pip install ${d.package}`}</code></li>)}</ul>
        </details>
      )}
    </section>
  );
}

const FLOW = [
  ['🎨', 'Designer', '/designer', 'Build the report: data, fields, layout. Saving never changes the server.'],
  ['🚀', 'Publish', null, 'Copy the report into a server folder (Publish in the designer).'],
  ['🗂️', 'Server', '/server', 'People sign in and run what their folder rights allow (right-click a folder → Permissions).'],
  ['📊', 'Analytics', '/analytics', 'Datasets, dashboards, ETL pipelines and Python scripts on the same data.'],
  ['🔎', 'Query', '/query', 'Look into any data source with SQL, the builder or Mongo queries.'],
];

function FlowCard() {
  return (
    <section className="card ov-flow">
      <div className="card-head"><div><p className="eyebrow">How reports reach users</p><h2>From the designer to everyone</h2></div></div>
      <ol>
        {FLOW.map(([icon, name, href, text], i) => (
          <li key={name} style={{ '--i': i }}>
            <span className="ov-flow-ico" aria-hidden="true">{icon}</span>
            <b>{href ? <a href={href}>{name}</a> : name}</b>
            <small>{text}</small>
          </li>
        ))}
      </ol>
    </section>
  );
}
