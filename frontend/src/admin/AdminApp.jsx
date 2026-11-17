import { useCallback, useEffect, useState } from 'react';
import { api } from '../api.js';
import { signOut } from '../shared/session.js';
import ApiKeysTab from './ApiKeysTab.jsx';
import { Notice, Topbar } from '../portal/Shell.jsx';
import DataSourcesTab from './DataSourcesTab.jsx';
import DeveloperTab from './DeveloperTab.jsx';
import SystemDbTab from './SystemDbTab.jsx';
import LogsTab from './LogsTab.jsx';
import OverviewTab from './OverviewTab.jsx';
import UsersTab from './UsersTab.jsx';

const TABS = [['overview', 'Overview'], ['users', 'Users & rights'], ['keys', 'API keys'], ['developer', 'API & Postman'], ['sources', 'Data sources'], ['systemdb', 'System database'], ['logs', 'Logs']];

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
        <main className="page">
          <div className="page-head">
            <div><p className="eyebrow">Administration</p><h1>Server administration</h1><p className="muted">Users and rights, API keys and Postman, the data sources reports read, the system database Folio Reports keeps its own data in, and the logs. Reports are designed in the <a href="/designer">Designer</a> and published to the <a href="/server">Server</a>.</p></div>
          </div>
          <nav className="tabs" role="tablist">
            {TABS.map(([id, label]) => <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? 'on' : ''} onClick={() => { setTab(id); setMessage(null); }}>{label}</button>)}
          </nav>
          {message && <div style={{ marginBottom: 14 }}><Notice message={message} /></div>}
          {tab === 'overview' && <OverviewTab who={who} notify={notify} onOpen={(id) => { setTab(id); setMessage(null); }} />}
          {tab === 'users' && <UsersTab notify={notify} />}
          {tab === 'keys' && <ApiKeysTab notify={notify} />}
          {tab === 'sources' && <DataSourcesTab notify={notify} />}
          {tab === 'developer' && <DeveloperTab notify={notify} />}
          {tab === 'systemdb' && <SystemDbTab notify={notify} />}
          {tab === 'logs' && <LogsTab />}
        </main>
      )}
    </>
  );
}
