import { useCallback, useEffect, useState } from 'react';
import { api, clearToken, hasToken, setToken } from '../api.js';
import { Notice, Topbar } from '../portal/Shell.jsx';
import DataSourcesTab from './DataSourcesTab.jsx';
import LogsTab from './LogsTab.jsx';
import OverviewTab from './OverviewTab.jsx';
import UsersTab from './UsersTab.jsx';

const TABS = [['overview', 'Overview'], ['users', 'Users'], ['sources', 'Data sources'], ['logs', 'Logs']];

/** /admin needs the admin token (the designer token is refused). On this computer it is picked up automatically. */
function TokenForm({ onConnect, message }) {
  const [value, setValue] = useState('');
  return (
    <div className="login-wrap">
      <form className="card login" onSubmit={(e) => { e.preventDefault(); onConnect(value.trim()); }}>
        <span className="brand-mark" aria-hidden="true">M</span>
        <div><p className="eyebrow">Administration</p><h1>Admin token</h1><p className="muted">On your own computer the token is used automatically (REPORTING_LOCAL_BOOTSTRAP=true). On a deployed server enter REPORTING_ADMIN_TOKEN.</p></div>
        <label className="field"><span>Admin token</span><input type="password" autoComplete="current-password" value={value} onChange={(e) => setValue(e.target.value)} required autoFocus /></label>
        <Notice message={message} />
        <button className="btn primary" type="submit" style={{ justifyContent: 'center', padding: 10 }}>Connect</button>
      </form>
    </div>
  );
}

export default function AdminApp() {
  const [state, setState] = useState('checking'); // checking | need-token | ready
  const [who, setWho] = useState(null);
  const [message, setMessage] = useState(null);
  const [tab, setTab] = useState(() => (TABS.some(([id]) => `#${id}` === window.location.hash) ? window.location.hash.slice(1) : 'overview'));

  const connect = useCallback(async () => {
    setState('checking');
    if (!hasToken()) await api.bootstrap().catch(() => false);
    try {
      const me = await api.whoami();
      if (me.role !== 'admin') throw Object.assign(new Error('This is the designer token. /admin needs the admin token (REPORTING_ADMIN_TOKEN).'), { status: 403 });
      setWho(me);
      setState('ready');
    } catch (error) {
      clearToken();
      setMessage(error.status === 401 ? { text: 'The admin token is missing or wrong.', kind: 'error' } : { text: error.message, kind: 'error' });
      setState('need-token');
    }
  }, []);
  useEffect(() => { connect(); }, [connect]);
  useEffect(() => { if (state === 'ready') window.history.replaceState(null, '', `#${tab}`); }, [tab, state]);

  const disconnect = () => { clearToken(); setWho(null); setMessage({ text: 'Disconnected.', kind: '' }); setState('need-token'); };
  const notify = useCallback((text, kind = 'ok') => setMessage(text ? { text, kind } : null), []);

  return (
    <>
      <Topbar current="/admin" subtitle="Administration">
        {state === 'ready' && (<><span className="chip">admin</span><button className="btn sm" onClick={disconnect}>Disconnect</button></>)}
      </Topbar>
      {state === 'checking' && <div className="login-wrap"><p className="muted">Connecting…</p></div>}
      {state === 'need-token' && <TokenForm message={message} onConnect={(value) => { setToken(value); setMessage(null); connect(); }} />}
      {state === 'ready' && (
        <main className="page">
          <div className="page-head">
            <div><p className="eyebrow">Administration</p><h1>Server administration</h1><p className="muted">User accounts, MongoDB data sources and logs. Reports are designed in the <a href="/designer">Designer</a> and published to the <a href="/server">Server</a>.</p></div>
          </div>
          <nav className="tabs" role="tablist">
            {TABS.map(([id, label]) => <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? 'on' : ''} onClick={() => { setTab(id); setMessage(null); }}>{label}</button>)}
          </nav>
          {message && <div style={{ marginBottom: 14 }}><Notice message={message} /></div>}
          {tab === 'overview' && <OverviewTab who={who} notify={notify} />}
          {tab === 'users' && <UsersTab notify={notify} />}
          {tab === 'sources' && <DataSourcesTab notify={notify} />}
          {tab === 'logs' && <LogsTab />}
        </main>
      )}
    </>
  );
}
