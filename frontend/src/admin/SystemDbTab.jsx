// /admin → System database: where the service keeps its own data (users, rights, reports, folders, run log,
// audit trail). A super admin picks a relational or non-relational database, tests it, copies the data and
// saves the choice; the service uses it after the next restart. The old database is not changed.
import PasswordInput from '../shared/PasswordInput.jsx';
import { useEffect, useState } from 'react';
import { request } from '../api.js';
import { DbLogo } from '../shared/adapters.jsx';

const ORDER = ['sqlserver', 'postgresql', 'mysql', 'oracle', 'sqlite', 'mongodb'];
const LOGO = { sqlserver: 'sqlserver', postgresql: 'postgresql', mysql: 'mysql', oracle: 'oracle', sqlite: 'postgresql', mongodb: 'mongodb' };
const EMPTY = { kind: 'sqlserver', host: 'localhost', port: '', database: 'reporting', username: '', password: '', windowsLogin: true, service: '', url: '' };

export default function SystemDbTab({ notify }) {
  const [info, setInfo] = useState(null);
  const [form, setForm] = useState(EMPTY);
  const [mode, setMode] = useState('form'); // form | url
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState('');
  const [copy, setCopy] = useState(true);

  const load = () => request('/api/admin/system-db').then(setInfo).catch((error) => notify(error.message, 'error'));
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (change) => { setForm((current) => ({ ...current, ...change })); setResult(null); };
  const body = () => (mode === 'url' ? { url: form.url } : { ...form, url: '' });
  const kinds = info?.kinds || {};
  const kind = kinds[form.kind] || {};

  const test = async () => {
    setBusy('test');
    try { setResult(await request('/api/admin/system-db/test', { method: 'POST', body: body() })); }
    catch (error) { setResult({ ok: false, message: error.message }); }
    finally { setBusy(''); }
  };
  const apply = async () => {
    const what = copy ? 'copy the current data into it and use it' : 'use it with the data that is already there';
    if (!window.confirm(`Switch the system database to ${result.label}?\n\nThis will ${what} after the next restart. The current database is kept as it is.`)) return;
    setBusy('switch');
    try {
      const done = await request('/api/admin/system-db/switch', { method: 'POST', body: { ...body(), copy } });
      const copied = done.copied ? ` Copied: ${done.copied.rows} rows, ${done.copied.documents + done.copied.files} documents, ${done.copied.runs} runs, ${done.copied.events} audit events.` : '';
      notify(`${done.message}${copied}`, done.saved ? 'ok' : 'warn');
      setResult(null);
      load();
    } catch (error) { notify(error.message, 'error'); }
    finally { setBusy(''); }
  };

  if (!info) return <p className="muted">Loading…</p>;
  const may = info.youMayChange;
  return (
    <div className="grid-2">
      <section className="card">
        <div className="card-head"><div><p className="eyebrow">System database</p><h2>Where the service keeps its data</h2></div></div>
        <div className="sysdb-now">
          <span className="sysdb-logo"><DbLogo type={info.backend === 'mongodb' ? 'mongodb' : info.backend === 'files' ? 'postgresql' : (info.label || '').toLowerCase().includes('sql server') ? 'sqlserver' : (info.label || '').toLowerCase().includes('mysql') ? 'mysql' : (info.label || '').toLowerCase().includes('oracle') ? 'oracle' : 'postgresql'} size={34} /></span>
          <div>
            <b>{info.label}</b> <span className="chip">{info.family}</span>
            <small>{info.url || 'data/ folder on this server'}</small>
          </div>
        </div>
        {info.pending && <div className="notice warn" style={{ marginTop: 12 }}>Saved for the next start: <b>{info.pending.label}</b> ({info.pending.url}). <b>Restart the service</b> to use it; until then changes still go to {info.label}.</div>}
        <p className="muted" style={{ marginTop: 14 }}>Users and rights, API keys, published reports, folders, designer drafts, data adapters, schedules, versions, the run log and the audit trail are kept here. The databases your <em>reports</em> read are set under <b>Data sources</b>.</p>
        <ul className="checklist">
          <li className="ok"><span className="check-icon">1</span><div><b>Create an empty database</b><small>e.g. <code>CREATE DATABASE reporting</code> on SQL Server, PostgreSQL or MySQL; on Oracle a schema (user):
            <code>CREATE USER REPORTING IDENTIFIED BY …; GRANT CREATE SESSION, CREATE TABLE TO REPORTING; ALTER USER REPORTING QUOTA UNLIMITED ON USERS;</code> MongoDB creates it by itself.</small></div></li>
          <li className="ok"><span className="check-icon">2</span><div><b>Test the connection</b><small>The tables (users, reports, runs, …) or collections are created on the first test.</small></div></li>
          <li className="ok"><span className="check-icon">3</span><div><b>Copy and save</b><small>The current data is copied; .env is updated (a copy of the old file is kept). Restart the service.</small></div></li>
        </ul>
        {info.managedByEnvironment && <p className="notice warn">REPORTING_STORAGE comes from the server's environment (Docker / service settings), not from .env: after testing, change it there.</p>}
      </section>

      <section className="card">
        <div className="card-head">
          <div><p className="eyebrow">Choose a database</p><h2>Relational or non-relational</h2></div>
          <div className="segmented"><button type="button" className={mode === 'form' ? 'on' : ''} onClick={() => setMode('form')}>Fields</button><button type="button" className={mode === 'url' ? 'on' : ''} onClick={() => setMode('url')}>URL</button></div>
        </div>
        {!may && <p className="notice warn">Only a super admin may change the system database.</p>}
        {mode === 'form' ? (
          <>
            <div className="db-picker" role="radiogroup" aria-label="Database type">
              {ORDER.filter((id) => kinds[id]).map((id) => (
                <button key={id} type="button" role="radio" aria-checked={form.kind === id} className={`db-choice ${form.kind === id ? 'on' : ''}`}
                  onClick={() => set({ kind: id, port: '', windowsLogin: id === 'sqlserver' ? form.windowsLogin : false,
                    database: id === 'sqlite' ? 'data/reporting.db' : id === 'oracle' ? 'REPORTING' : 'reporting', ...(id === 'oracle' && !form.username ? { username: 'REPORTING' } : {}) })}>
                  <DbLogo type={LOGO[id]} size={22} /><span><b>{kinds[id].label}</b><small>{kinds[id].family === 'relational' ? 'SQL' : 'NoSQL'}</small></span>
                </button>
              ))}
            </div>
            <div className="form-grid-2">
              {form.kind !== 'sqlite' && <label className="field"><span>Host{form.kind === 'sqlserver' ? ' (or host\\instance)' : ''}</span><input value={form.host} onChange={(e) => set({ host: e.target.value })} /></label>}
              {form.kind !== 'sqlite' && <label className="field"><span>Port</span><input value={form.port} placeholder={String(kind.port || '')} onChange={(e) => set({ port: e.target.value.replace(/\D/g, '') })} /></label>}
              {form.kind !== 'oracle' && <label className="field"><span>{form.kind === 'sqlite' ? 'File' : 'Database'}</span><input value={form.database} onChange={(e) => set({ database: e.target.value })} />
                {form.kind !== 'sqlite' && <span className="hint">A database of its own (default name reporting): every table of the service goes inside it — folders, reports, users, runs, analytics.</span>}</label>}
              {form.kind === 'oracle' && <label className="field"><span>Service name</span><input value={form.service} placeholder="XEPDB1" onChange={(e) => set({ service: e.target.value })} /></label>}
              {form.kind === 'oracle' && <label className="field"><span>Schema</span><input value={form.database} onChange={(e) => set({ database: e.target.value.toUpperCase() })} />
                <span className="hint">On Oracle the service's own place is a schema (a user), REPORTING by default: every table goes inside it. Sign in as that user.</span></label>}
            </div>
            {form.kind === 'sqlserver' && <label className="check-line"><input type="checkbox" checked={form.windowsLogin} onChange={(e) => set({ windowsLogin: e.target.checked })} /> Windows sign-in (Trusted connection, ODBC Driver 18)</label>}
            {form.kind !== 'sqlite' && !(form.kind === 'sqlserver' && form.windowsLogin) && (
              <div className="form-grid-2">
                <label className="field"><span>User</span><input value={form.username} autoComplete="off" onChange={(e) => set({ username: e.target.value })} /></label>
                <label className="field"><span>Password</span><PasswordInput value={form.password} autoComplete="new-password" onChange={(e) => set({ password: e.target.value })} /></label>
              </div>
            )}
          </>
        ) : (
          <label className="field"><span>Database URL</span>
            <input value={form.url} placeholder="mongodb://user:pw@host:27017/reporting" onChange={(e) => set({ url: e.target.value })} />
            <span className="hint">sqlserver://user:pw@host:1433/reporting · mssql+pyodbc://@localhost/reporting?driver=ODBC+Driver+18+for+SQL+Server&amp;trusted_connection=yes · postgresql://… · mysql://… · oracle://REPORTING:pw@host:1521/?service_name=XEPDB1&amp;schema=REPORTING · sqlite:///data/reporting.db · mongodb://…</span>
          </label>
        )}
        <div className="row-actions" style={{ marginTop: 14 }}>
          <button className="btn" disabled={!may || busy !== ''} onClick={test}>{busy === 'test' ? 'Testing…' : 'Test connection'}</button>
        </div>
        {result && (
          <div className={`notice ${result.ok ? 'ok' : 'error'}`} style={{ marginTop: 12 }}>
            <b>{result.ok ? '✓ ' : '✕ '}{result.label || 'Connection'}</b> {result.message}
            {result.ok && result.existing && <small style={{ display: 'block', marginTop: 4 }}>Already there: {result.existing.users} users, {result.existing.reports} reports, {result.existing.runs} runs.</small>}
          </div>
        )}
        {result?.ok && (
          <div className="sysdb-apply">
            <label className="check-line"><input type="checkbox" checked={copy} onChange={(e) => setCopy(e.target.checked)} /> Copy the current data into it (existing rows there are kept)</label>
            <button className="btn primary" disabled={!may || busy !== ''} onClick={apply}>{busy === 'switch' ? 'Copying…' : `Use ${result.label}`}</button>
          </div>
        )}
      </section>
    </div>
  );
}
