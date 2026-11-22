// Data adapters shared by the designer, /admin and /server: logos, the <option> list grouped by folder, and the
// Data Adapter Wizard (MongoDB, PostgreSQL, MySQL/MariaDB, SQL Server, Oracle).
import PasswordInput from './PasswordInput.jsx';
import { useState } from 'react';
import { api } from '../api.js';
import './adapters.css';

/* ---------------------------------------------------------------- shared: adapter <option>s grouped by folder */
/** Options for an adapter <select>: top-level adapters first, then one <optgroup> per repository folder. */
export function AdapterOptions({ adapters, describe = (a) => a.name, disableUnconfigured = false }) {
  const groups = new Map();
  (adapters || []).forEach((a) => {
    const key = a.folder || '';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(a);
  });
  const keys = [...groups.keys()].sort((a, b) => (a === '' ? -1 : b === '' ? 1 : a.localeCompare(b)));
  return keys.map((key) => (
    <optgroup key={key || '(top)'} label={key ? `📁 ${key.split('/').join(' / ')}` : 'Data Adapters'}>
      {groups.get(key).map((a) => <option key={a.id} value={a.id} disabled={disableUnconfigured && !a.configured}>{describe(a)}</option>)}
    </optgroup>
  ));
}

/* ---------------------------------------------------------------- Data adapters */
/** Database logos for the data adapters (simple drawings in each database's colours). */
export const DB_STYLE = {
  mongodb: { label: 'MongoDB', color: '#00684A' },
  postgresql: { label: 'PostgreSQL', color: '#336791', text: 'PG' },
  mysql: { label: 'MySQL / MariaDB', color: '#00758F', text: 'My' },
  sqlserver: { label: 'Microsoft SQL Server', color: '#A91D22', text: 'MS' },
  oracle: { label: 'Oracle', color: '#C74634', text: 'Or' },
};
export function DbLogo({ type = 'mongodb', size = 14 }) {
  const style = DB_STYLE[type] || DB_STYLE.mongodb;
  if ((type || 'mongodb') === 'mongodb') {
    return (
      <svg className="db-logo" width={size} height={size} viewBox="0 0 16 16" role="img" aria-label={style.label}>
        <path d="M8 .6c.5 1.4 1.9 2.4 3 3.9 1.9 2.5 1.6 6.4-.6 8.4-.7.6-1.4 1-2 1.3l-.3 1.2h-.3l-.3-1.2c-.6-.3-1.3-.7-2-1.3C3.2 10.9 3 7 4.9 4.5 6.1 3 7.5 2 8 .6Z" fill={style.color} />
        <path d="M8 2.6v12.8" stroke="#E3FCF7" strokeWidth=".7" />
      </svg>
    );
  }
  return (
    <svg className="db-logo" width={size} height={size} viewBox="0 0 16 16" role="img" aria-label={style.label}>
      <rect width="16" height="16" rx="3.5" fill={style.color} />
      <text x="8" y="11.2" textAnchor="middle" fontSize="7.5" fontWeight="700" fill="#fff" fontFamily="system-ui, sans-serif">{style.text}</text>
    </svg>
  );
}

export const ADAPTER_TYPES = [
  { id: 'mongodb', name: 'MongoDB Connection', text: 'A MongoDB server (mongodb:// or mongodb+srv:// URL). Reports run their aggregation query on it.' },
  { id: 'postgresql', name: 'PostgreSQL', text: 'SQL SELECT queries on PostgreSQL (read-only), with $P{…} parameters like Jaspersoft Studio.' },
  { id: 'mysql', name: 'MySQL / MariaDB', text: 'SQL SELECT queries on MySQL 5.7+ or MariaDB (read-only transaction, time limit).' },
  { id: 'sqlserver', name: 'Microsoft SQL Server', text: 'SQL SELECT queries on SQL Server (SQL login; every query is rolled back).' },
  { id: 'oracle', name: 'Oracle', text: 'SQL SELECT queries on Oracle Database (read-only transaction; service name).' },
];

/** Per SQL type: URL scheme, default port, what the database field is called, the SSL choices. */
const SQL_KINDS = {
  postgresql: { scheme: 'postgresql', port: '5432', database: 'Database', ssl: [['prefer', 'prefer (default)'], ['require', 'require'], ['verify-full', 'verify-full'], ['disable', 'disable']] },
  mysql: { scheme: 'mysql', port: '3306', database: 'Database', ssl: [['prefer', 'off (default)'], ['require', 'require TLS']] },
  sqlserver: { scheme: 'sqlserver', port: '1433', database: 'Database', ssl: null },
  oracle: { scheme: 'oracle', port: '1521', database: 'Service name', ssl: null },
};

const adapterId = (name) => name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 39);

/** SQL connection fields → scheme://user:password@host:port/database (+ ?sslmode=… / ?ssl=true) */
export function sqlUrl(type, { host, port, database, user, password, sslmode }) {
  const kind = SQL_KINDS[type] || SQL_KINDS.postgresql;
  if (!host.trim() || !database.trim()) return '';
  const auth = user.trim() ? `${encodeURIComponent(user.trim())}${password ? `:${encodeURIComponent(password)}` : ''}@` : '';
  let suffix = '';
  if (type === 'postgresql' && sslmode && sslmode !== 'prefer') suffix = `?sslmode=${sslmode}`;
  if (type === 'mysql' && sslmode === 'require') suffix = '?ssl=true';
  return `${kind.scheme}://${auth}${host.trim()}:${(port || kind.port).toString().trim()}/${encodeURIComponent(database.trim())}${suffix}`;
}

/** Like Jaspersoft Studio's "Data Adapter Wizard": 1) pick the type, 2) fill in the connection, Test, Finish. */
export function AdapterWizard({ initial, folders, existingIds, onClose, onSaved, notify }) {
  const editing = Boolean(initial?.id);
  const [step, setStep] = useState(initial?.id || initial?.name ? 2 : 1); // edit / duplicate skip the type page
  const [type, setType] = useState(initial?.type || 'mongodb');
  const [form, setForm] = useState({ name: '', uri: '', database: '', folder: '', ...initial, id: initial?.id || '' });
  const [pg, setPg] = useState({ host: 'localhost', port: SQL_KINDS[initial?.type]?.port || '5432', database: initial?.database || '', user: '', password: '', sslmode: 'prefer' });
  const [result, setResult] = useState(null); // { ok, text }
  const [busy, setBusy] = useState('');
  const id = editing ? form.id : adapterId(form.name);
  const duplicateId = !editing && id && existingIds.includes(id);
  const typeInfo = ADAPTER_TYPES.find((t) => t.id === type) || ADAPTER_TYPES[0];
  const isSql = type !== 'mongodb';
  const sqlKind = SQL_KINDS[type] || SQL_KINDS.postgresql;
  const uri = isSql ? sqlUrl(type, pg) : form.uri.trim();
  const chooseType = (next) => {
    setType(next);
    // the port follows the database type unless the user typed another one
    if (SQL_KINDS[next] && Object.values(SQL_KINDS).some((k) => k.port === pg.port)) setPg((current) => ({ ...current, port: SQL_KINDS[next].port, sslmode: 'prefer' }));
  };
  const setPgField = (patch) => { setPg({ ...pg, ...patch }); setResult(null); };

  const test = async () => {
    setBusy('test'); setResult(null);
    try { const r = await api.testConnectionValues({ type, id: id || 'test', uri, database: isSql ? pg.database.trim() : form.database.trim() }); setResult({ ok: true, text: `Successful: ${r.message || 'connected'}` }); }
    catch (error) { setResult({ ok: false, text: error.message }); }
    finally { setBusy(''); }
  };
  const finish = async () => {
    setBusy('save');
    try {
      await api.saveConnection({ type, id, name: form.name.trim(), uri, database: isSql ? pg.database.trim() : form.database.trim(), folder: form.folder });
      notify(`Data adapter “${form.name.trim()}” ${editing ? 'updated' : 'created'}`, 'ok');
      onSaved();
    } catch (error) { setResult({ ok: false, text: error.status === 403 ? 'Saving a data adapter needs the admin token' : error.message }); setBusy(''); }
  };

  return (
    <div className="aw-back" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <div className="aw-modal" role="dialog" aria-label="Data Adapter Wizard">
        <div className="wizard-head">
          <h3>{editing ? `Edit Data Adapter — ${initial.name}` : 'Data Adapter Wizard'}</h3>
          <p className="muted">{step === 1 ? 'Create a new data adapter: choose its type.' : `${typeInfo.name}: enter the connection details, test them, then Finish.`}</p>
        </div>
        {step === 1 && (
          <ul className="wizard-types" role="listbox" aria-label="Data adapter type">
            {ADAPTER_TYPES.map((t) => (
              <li key={t.id} role="option" aria-selected={type === t.id} aria-disabled={t.soon || undefined}
                className={t.soon ? 'soon' : type === t.id ? 'on' : ''}
                title={t.soon ? 'Planned (see ARCHITECTURE.md §9.3)' : 'Double-click to continue'}
                onClick={() => !t.soon && chooseType(t.id)} onDoubleClick={() => { if (!t.soon) { chooseType(t.id); setStep(2); } }}>
                <span className="t-icon"><DbLogo type={t.id} size={26} /></span><span><b>{t.name}{t.soon ? <em className="soon-tag">coming next</em> : null}</b><small>{t.text}</small></span>
              </li>
            ))}
          </ul>
        )}
        {step === 2 && (
          <div className="wizard-form">
            <label className="aw-field">Name<input autoFocus value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder={isSql ? `Finance ${typeInfo.name.split(' ')[0]}` : 'KMBL UAT MongoDB'} /></label>
            {!editing && id && <small className={duplicateId ? 'err' : 'muted'}>ID: {id}{duplicateId ? ' — already used, choose another name' : ''}</small>}
            {isSql ? (
              <>
                <div className="pg-grid">
                  <label className="aw-field">Host<input value={pg.host} onChange={(e) => setPgField({ host: e.target.value })} placeholder="db.example.com" /></label>
                  <label className="aw-field">Port<input value={pg.port} inputMode="numeric" onChange={(e) => setPgField({ port: e.target.value.replace(/[^0-9]/g, '') })} /></label>
                  <label className="aw-field">{sqlKind.database}<input value={pg.database} onChange={(e) => setPgField({ database: e.target.value })} placeholder={type === 'oracle' ? 'FREEPDB1' : 'reports'} /></label>
                  {sqlKind.ssl && (
                    <label className="aw-field">SSL
                      <select value={pg.sslmode} onChange={(e) => setPgField({ sslmode: e.target.value })}>
                        {sqlKind.ssl.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                      </select>
                    </label>
                  )}
                  <label className="aw-field">Username<input value={pg.user} autoComplete="off" onChange={(e) => setPgField({ user: e.target.value })} placeholder="report_reader" /></label>
                  <label className="aw-field">Password<PasswordInput autoComplete="new-password" value={pg.password} onChange={(e) => setPgField({ password: e.target.value })} /></label>
                </div>
                <small className="muted">{editing ? 'The saved password is never shown: enter the connection again (it replaces the old one).' : 'Saved with the password in a private file on the server; never shown again.'} Give the report user read-only rights — reports only read, and every query runs in a read-only transaction.</small>
              </>
            ) : (
              <>
                <label className="aw-field">MongoDB URL
                  <PasswordInput autoComplete="new-password" value={form.uri} onChange={(e) => { setForm({ ...form, uri: e.target.value }); setResult(null); }} placeholder="mongodb://user:password@host:27017/Database" />
                  <small className="muted">{editing ? 'The saved URL is never shown. Enter it again (it replaces the old one).' : 'Saved with its password in a private file on the server; never shown again. For a remote MongoDB connect the VPN first.'}</small>
                </label>
                <label className="aw-field">Database <small>(optional when the URL has one)</small><input value={form.database} onChange={(e) => setForm({ ...form, database: e.target.value })} /></label>
              </>
            )}
            <label className="aw-field">Folder
              <select value={form.folder || ''} onChange={(e) => setForm({ ...form, folder: e.target.value })}>
                <option value="">Data Adapters (top level)</option>
                {folders.map((f) => <option key={f} value={f}>{f.split('/').join(' / ')}</option>)}
              </select>
            </label>
            {result && <p className={`aw-result ${result.ok ? 'ok' : 'err'}`}>{result.ok ? '✓ ' : '✕ '}{result.text}</p>}
          </div>
        )}
        <div className="aw-buttons">
          {step === 2 && <button className="btn" style={{ marginRight: 'auto' }} disabled={Boolean(busy) || !uri} onClick={test}>{busy === 'test' ? 'Testing…' : 'Test'}</button>}
          {step === 2 && !editing && <button className="btn" disabled={Boolean(busy)} onClick={() => setStep(1)}>‹ Back</button>}
          {step === 1 && <button className="btn" onClick={() => setStep(2)}>Next ›</button>}
          {step === 2 && <button className="btn primary" disabled={Boolean(busy) || !form.name.trim() || !uri || duplicateId} onClick={finish}>{busy === 'save' ? 'Saving…' : 'Finish'}</button>}
          <button className="btn" disabled={Boolean(busy)} onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}


/** "PostgreSQL", "MongoDB", … for a type id. */
export const adapterTypeLabel = (type) => (DB_STYLE[type || 'mongodb'] || DB_STYLE.mongodb).label;
