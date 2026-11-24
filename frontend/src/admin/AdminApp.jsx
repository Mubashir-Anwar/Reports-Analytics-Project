import { useCallback, useEffect, useState } from 'react';
import { api } from '../api.js';
import { signOut } from '../shared/session.js';
import AnalyticsTab from './AnalyticsTab.jsx';
import ApiKeysTab from './ApiKeysTab.jsx';
import { Notice, Topbar } from '../portal/Shell.jsx';
import DataSourcesTab from './DataSourcesTab.jsx';
import DeveloperTab from './DeveloperTab.jsx';
import SystemDbTab from './SystemDbTab.jsx';
import BrandingTab from './BrandingTab.jsx';
import LogsTab from './LogsTab.jsx';
import OverviewTab from './OverviewTab.jsx';
import UsersTab from './UsersTab.jsx';

const TABS = [['overview', 'Overview'], ['users', 'Users & rights'], ['keys', 'API keys'], ['developer', 'API & Postman'], ['sources', 'Data sources'], ['analytics', 'Analytics'], ['systemdb', 'System database'], ['branding', 'Name & look'], ['logs', 'Logs']];
/** Side navigation of the new look: icon (SVG path), colour tint and the group a tab sits in. */
export const TAB_LOOK = {
  overview: { tint: 'b', group: '', path: 'M4 13h6V4H4zM14 20h6v-9h-6zM4 20h6v-4H4zM14 7h6V4h-6z' },
  users: { tint: 'v', group: 'People', path: 'M16 19v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1M9.5 10a3 3 0 1 0 0-6 3 3 0 0 0 0 6M21 19v-1a4 4 0 0 0-3-3.8M15.5 4.2a3 3 0 0 1 0 5.6' },
  keys: { tint: 'b', group: 'Apps', path: 'M15 7a4 4 0 1 1-3.9 4.9L4 19v-3h3v-2h2l1.1-1.1A4 4 0 0 1 15 7zM16 10h.01' },
  developer: { tint: 'b', group: 'Apps', path: 'M8 8l-4 4 4 4M16 8l4 4-4 4M13.5 5l-3 14' },
  sources: { tint: 'g', group: 'Data', path: 'M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6' },
  analytics: { tint: 'c', group: 'Data', path: 'M4 20V10M10 20V4M16 20v-7M22 20H2' },
  systemdb: { tint: 'c', group: 'Service', path: 'M3 5h18v6H3zM3 13h18v6H3zM7 8h.01M7 16h.01' },
  branding: { tint: 'a', group: 'Service', path: 'M12 3a9 9 0 1 0 0 18c1 0 1.5-.8 1.5-1.6 0-.9-.7-1.4-.7-2.2 0-.9.7-1.7 1.7-1.7H17a4 4 0 0 0 4-4c0-4.7-4-8.5-9-8.5zM7.5 11h.01M10 7h.01M15 7.5h.01' },
  logs: { tint: 'a', group: 'Service', path: 'M6 3h9l4 4v14H6zM14 3v5h5M9 12h7M9 16h7' },
};
export function TabIcon({ id }) {
  return <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={TAB_LOOK[id].path} /></svg>;
}

/** /admin needs a signed-in account with the Admin right (the server sends anyone else to /login or a "no access" page). */

export default function AdminApp() {
  const [state, setState] = useState('checking'); // checking | denied | ready
  const [who, setWho] = useState(null);
  const [message, setMessage] = useState(null);
  const [tab, setTab] = useState(() => (TABS.some(([id]) => `#${id}` === window.location.hash) ? window.location.hash.slice(1) : 'overview'));

  const connect = useCallback(async () => {
    setState('checking');
    try {
      const me = await api.whoami();
      if (me.role !== 'admin') throw new Error('Your account does not have the Admin right.');
      setWho(me);
      setState('ready');
    } catch (error) {
      setMessage({ text: error.message, kind: 'error' });
      setState('denied');
    }
  }, []);
  useEffect(() => { connect(); }, [connect]);
  useEffect(() => {   // links such as /admin#developer from the account menu, or #keys inside a tab
    const follow = () => { const id = window.location.hash.slice(1); if (TABS.some(([tabId]) => tabId === id)) { setTab(id); setMessage(null); } };
    window.addEventListener('hashchange', follow);
    return () => window.removeEventListener('hashchange', follow);
  }, []);
  useEffect(() => { if (state === 'ready') window.history.replaceState(null, '', `#${tab}`); }, [tab, state]);

  const notify = useCallback((text, kind = 'ok') => setMessage(text ? { text, kind } : null), []);

  return (
    <>
      <Topbar current="/admin" />
      {state === 'checking' && <div className="login-wrap"><p className="muted">Connecting…</p></div>}
      {state === 'denied' && <div className="login-wrap"><div className="card login"><Notice message={message} /><button className="btn" onClick={signOut}>Sign out and use another account</button></div></div>}
      {state === 'ready' && (
        <main className="page adm-new">
          <div className="adm-split">
            <nav className="adm-nav" role="tablist" aria-label="Administration" aria-orientation="vertical">
              <div className="adm-nav-head"><p className="eyebrow">Administration</p><b>Control room</b><small>{who.version}</small></div>
              {TABS.map(([id, label], index) => {
                const look = TAB_LOOK[id];
                const groupStart = look.group && TAB_LOOK[TABS[index - 1]?.[0]]?.group !== look.group;
                return (
                  <div key={id} className="adm-nav-item" style={{ '--i': index }}>
                    {groupStart && <p className="adm-nav-group">{look.group}</p>}
                    <button role="tab" aria-selected={tab === id} className={`tint-${look.tint} ${tab === id ? 'on' : ''}`} onClick={() => { setTab(id); setMessage(null); }}>
                      <span className="adm-ico"><TabIcon id={id} /></span>{label}
                    </button>
                  </div>
                );
              })}
            </nav>
            <div className="adm-main" key={tab}>
              <div className="page-head adm-head">
                <div>
                  <p className="eyebrow">{tab === 'overview' ? 'Administration' : TAB_LOOK[tab].group || 'Administration'}</p>
                  <h1>{tab === 'overview' ? 'Server administration' : TABS.find(([id]) => id === tab)[1]}</h1>
                  {tab === 'overview' && <p className="muted">Users and rights, API keys and Postman, the data sources reports read, analytics datasets and dashboards, the system database the service keeps its own data in, and the logs. Reports are designed in the <a href="/designer">Designer</a> and published to the <a href="/server">Server</a>.</p>}
                </div>
                <span className={`adm-head-ico tint-${TAB_LOOK[tab].tint}`} aria-hidden="true"><TabIcon id={tab} /></span>
              </div>
              {message && <div style={{ marginBottom: 14 }}><Notice message={message} /></div>}
              {tab === 'overview' && <OverviewTab who={who} notify={notify} onOpen={(id) => { setTab(id); setMessage(null); }} />}
              {tab === 'users' && <UsersTab notify={notify} />}
              {tab === 'keys' && <ApiKeysTab notify={notify} />}
              {tab === 'sources' && <DataSourcesTab notify={notify} />}
              {tab === 'analytics' && <AnalyticsTab notify={notify} />}
              {tab === 'developer' && <DeveloperTab notify={notify} />}
              {tab === 'systemdb' && <SystemDbTab notify={notify} />}
              {tab === 'branding' && <BrandingTab who={who} notify={notify} />}
              {tab === 'logs' && <LogsTab />}
            </div>
          </div>
        </main>
      )}
    </>
  );
}
