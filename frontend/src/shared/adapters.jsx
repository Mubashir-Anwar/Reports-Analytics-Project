// Data adapters shared by the designer, /admin and /server: logos, the <option> list grouped by folder, and the
// Data Adapter Wizard (databases, warehouses, NoSQL, Files & Sheets).
import PasswordInput from './PasswordInput.jsx';
import SourceForm, { OWN_FORM } from './SourceForm.jsx';
import { MongoOptions, SqlOptions, optionsPayload } from './ConnectionOptions.jsx';
import { useState } from 'react';
import { createPortal } from 'react-dom';
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
  mariadb: { label: 'MariaDB', color: '#003545', text: 'Ma' },
  snowflake: { label: 'Snowflake', color: '#29B5E8', text: '❄' },
  clickhouse: { label: 'ClickHouse', color: '#151515', text: 'CH' },
  elasticsearch: { label: 'Elasticsearch', color: '#00A9A5', text: 'ES' },
  redis: { label: 'Redis', color: '#D82C20', text: 'Rd' },
  sqlite: { label: 'SQLite file', color: '#0F80CC', text: 'SL' },
  duckdb: { label: 'DuckDB file', color: '#C9A200', text: 'Dk' },
  files: { label: 'Files (CSV, Excel, JSON)', color: '#1D6F42', text: 'XL' },
  gsheets: { label: 'Google Sheets', color: '#0F9D58', text: 'GS' },
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

/** The logo of a preset (a service that uses another type's driver). */
export function PresetLogo({ preset, size = 14 }) {
  return (
    <svg className="db-logo" width={size} height={size} viewBox="0 0 16 16" role="img" aria-label={preset.name}>
      <rect width="16" height="16" rx="8" fill={preset.color} />
      <text x="8" y="11" textAnchor="middle" fontSize="7" fontWeight="800" fill="#fff" fontFamily="system-ui, sans-serif">{preset.text}</text>
    </svg>
  );
}

/** The parts of the product; a data source belongs to one of them and works only there. */
export const AREAS = {
  designer: { name: 'Designer', use: 'reports you design' },
  server: { name: 'Server', use: 'published reports' },
  analytics: { name: 'Analytics', use: 'datasets and dashboards' },
  query: { name: 'Query', use: 'looking into a database' },
};

export const ADAPTER_TYPES = [
  { id: 'mongodb', group: 'nosql', name: 'MongoDB Connection', text: 'A MongoDB server (mongodb:// or mongodb+srv:// URL). Reports run their aggregation query on it.' },
  { id: 'postgresql', group: 'sql', name: 'PostgreSQL', text: 'SQL SELECT queries on PostgreSQL (read-only), with $P{…} parameters.' },
  { id: 'mysql', group: 'sql', name: 'MySQL', text: 'SQL SELECT queries on MySQL 5.7+ (read-only transaction, time limit).' },
  { id: 'mariadb', group: 'sql', name: 'MariaDB', text: 'SQL SELECT queries on MariaDB 10.1+ (read-only transaction, time limit).' },
  { id: 'sqlserver', group: 'sql', name: 'Microsoft SQL Server', text: 'SQL SELECT queries on SQL Server (SQL login; every query is rolled back).' },
  { id: 'oracle', group: 'sql', name: 'Oracle', text: 'SQL SELECT queries on Oracle Database (read-only transaction; service name).' },
  { id: 'snowflake', group: 'cloud', name: 'Snowflake', text: 'The Snowflake data cloud: account, warehouse and role; a time limit per query.' },
  { id: 'clickhouse', group: 'cloud', name: 'ClickHouse', text: 'ClickHouse over HTTP(S); the server runs every query read-only.' },
  { id: 'elasticsearch', group: 'nosql', name: 'Elasticsearch', text: 'Indexes as tables, queried with Elasticsearch SQL (/_sql).' },
  { id: 'redis', group: 'nosql', name: 'Redis', text: 'Key prefixes as tables (hashes and JSON values become columns); queried with SQL.' },
  { id: 'files', group: 'files', name: 'Files: CSV, Excel, JSON', text: 'Upload CSV, Excel, JSON or Parquet files; every file and sheet becomes a table for SQL.' },
  { id: 'gsheets', group: 'files', name: 'Google Sheets', text: 'A shared Google Sheet; every tab becomes a table, read again every 5 minutes.' },
  { id: 'sqlite', group: 'files', name: 'SQLite file', text: 'Upload a SQLite database file; opened read-only.' },
  { id: 'duckdb', group: 'files', name: 'DuckDB file', text: 'Upload a DuckDB database file; opened read-only.' },
];
export const TYPE_GROUPS = [
  ['sql', 'Relational databases'], ['cloud', 'Cloud & warehouses'], ['nosql', 'NoSQL & search'], ['files', 'Files & Sheets'],
];
/** Services that speak the protocol of one of the types above: the same driver with the usual port and SSL. */
export const PRESETS = [
  { id: 'cockroach', type: 'postgresql', name: 'CockroachDB', port: '26257', sslmode: 'require', color: '#6933FF', text: 'Co' },
  { id: 'redshift', type: 'postgresql', name: 'Amazon Redshift', port: '5439', sslmode: 'require', color: '#8C4FFF', text: 'RS' },
  { id: 'supabase', type: 'postgresql', name: 'Supabase', port: '5432', sslmode: 'require', color: '#3ECF8E', text: 'Su' },
  { id: 'neon', type: 'postgresql', name: 'Neon', port: '5432', sslmode: 'require', color: '#00E599', text: 'Ne' },
  { id: 'timescale', type: 'postgresql', name: 'TimescaleDB', port: '5432', sslmode: 'require', color: '#FDB515', text: 'Ts' },
  { id: 'yugabyte', type: 'postgresql', name: 'YugabyteDB', port: '5433', sslmode: 'prefer', color: '#FF6E42', text: 'Yb' },
  { id: 'aurora', type: 'mysql', name: 'Aurora / RDS MySQL', port: '3306', sslmode: 'require', color: '#FF9900', text: 'Au' },
  { id: 'tidb', type: 'mysql', name: 'TiDB', port: '4000', sslmode: 'require', color: '#E30C34', text: 'Ti' },
  { id: 'singlestore', type: 'mysql', name: 'SingleStore', port: '3306', sslmode: 'require', color: '#AA00FF', text: 'S2' },
  { id: 'azuresql', type: 'sqlserver', name: 'Azure SQL', port: '1433', sslmode: 'prefer', color: '#0078D4', text: 'Az' },
  { id: 'atlas', type: 'mongodb', name: 'MongoDB Atlas', color: '#00ED64', text: 'At', uri: 'mongodb+srv://user:password@cluster0.xxxxx.mongodb.net/' },
  { id: 'cosmos', type: 'mongodb', name: 'Azure Cosmos DB (Mongo API)', color: '#0078D4', text: 'Cs', uri: 'mongodb://account:key@account.mongo.cosmos.azure.com:10255/?ssl=true&retrywrites=false' },
];

/** Per SQL type: URL scheme, default port, what the database field is called, the SSL choices. */
export const SQL_KINDS = {
  postgresql: { scheme: 'postgresql', port: '5432', database: 'Database', ssl: [['prefer', 'prefer (default)'], ['require', 'require'], ['verify-full', 'verify-full'], ['disable', 'disable']] },
  mysql: { scheme: 'mysql', port: '3306', database: 'Database', ssl: [['prefer', 'off (default)'], ['require', 'require TLS']] },
  mariadb: { scheme: 'mariadb', port: '3306', database: 'Database', ssl: [['prefer', 'off (default)'], ['require', 'require TLS']] },
  clickhouse: { scheme: 'clickhouse', port: '8123', database: 'Database', ssl: [['prefer', 'HTTP (8123)'], ['require', 'HTTPS (8443)']] },
  sqlserver: { scheme: 'sqlserver', port: '1433', database: 'Database', ssl: null },
  oracle: { scheme: 'oracle', port: '1521', database: 'Service name', ssl: null },
};

export const adapterId = (name) => name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 39);

/** SQL connection fields → scheme://user:password@host:port/database (+ ?sslmode=… / ?ssl=true) */
export function sqlUrl(type, { host, port, database, user, password, sslmode, params = [] }) {
  const kind = SQL_KINDS[type] || SQL_KINDS.postgresql;
  if (!host.trim() || !database.trim()) return '';
  const auth = user.trim() ? `${encodeURIComponent(user.trim())}${password ? `:${encodeURIComponent(password)}` : ''}@` : '';
  let suffix = '';
  if (type === 'postgresql' && sslmode && sslmode !== 'prefer') suffix = `?sslmode=${sslmode}`;
  if ((type === 'mysql' || type === 'mariadb') && sslmode === 'require') suffix = '?ssl=true';
  if (type === 'clickhouse' && sslmode === 'require') suffix = '?secure=true';
  const extra = params.filter(([k]) => k).map(([k, v]) => `${encodeURIComponent(k)}=${v}`).join('&');   // Advanced tab
  if (extra) suffix += `${suffix ? '&' : '?'}${extra}`;
  return `${kind.scheme}://${auth}${host.trim()}:${(port || kind.port).toString().trim()}/${encodeURIComponent(database.trim())}${suffix}`;
}

/** Like a classic report designer's "Data Adapter Wizard": 1) pick the type, 2) fill in the connection, Test, Finish. */
/** area: the part the new data source is for; areas: let the person choose it (Admin). onSaved gets { id, name, type, area }. */
export function AdapterWizard({ initial, folders = [], existingIds = [], area: ownArea = '', areas = null, onClose, onSaved, notify = () => {} }) {
  const editing = Boolean(initial?.id);
  const [area, setArea] = useState(initial?.area || ownArea || areas?.[0] || '');
  const [step, setStep] = useState(initial?.id || initial?.name ? 2 : 1); // edit / duplicate skip the type page
  const [type, setType] = useState(initial?.type || 'mongodb');
  const [form, setForm] = useState({ name: '', uri: '', database: '', folder: '', ...initial, id: initial?.id || '' });
  const [pg, setPg] = useState({ host: 'localhost', port: SQL_KINDS[initial?.type]?.port || '5432', database: initial?.database || '', user: '', password: '', sslmode: 'prefer', params: [] });
  const [result, setResult] = useState(null); // { ok, text }
  const [busy, setBusy] = useState('');
  const [other, setOther] = useState({ uri: '', database: '' }); // SourceForm's answer for the newer kinds
  const [preset, setPreset] = useState('');
  const [search, setSearch] = useState('');
  const [opts, setOptsState] = useState({}); // Advanced Connection Options: certificate files, SSH tunnel (changes only)
  const savedOptions = (initial?.type || 'mongodb') === type ? initial?.options || {} : {};
  const setOpts = (next) => { setOptsState(next); setResult(null); };
  const id = editing ? form.id : adapterId(form.name);
  const duplicateId = !editing && id && existingIds.includes(id);
  const typeInfo = ADAPTER_TYPES.find((t) => t.id === type) || ADAPTER_TYPES[0];
  const isSql = type !== 'mongodb';
  const own = OWN_FORM.includes(type);
  const sqlKind = SQL_KINDS[type] || SQL_KINDS.postgresql;
  const uri = own ? other.uri : isSql ? sqlUrl(type, pg) : form.uri.trim();
  const presetInfo = PRESETS.find((p) => p.id === preset);
  const chooseType = (next, from = null) => {
    setType(next); setPreset(from?.id || ''); setResult(null);
    if (from?.port) { setPg((current) => ({ ...current, port: from.port, sslmode: from.sslmode || 'prefer' })); return; }
    if (from?.uri) setForm((current) => ({ ...current, uri: current.uri || '' }));
    // the port follows the database type unless the user typed another one
    if (SQL_KINDS[next] && Object.values(SQL_KINDS).some((k) => k.port === pg.port)) setPg((current) => ({ ...current, port: SQL_KINDS[next].port, sslmode: 'prefer' }));
  };
  const setPgField = (patch) => { setPg({ ...pg, ...patch }); setResult(null); };

  const test = async () => {
    setBusy('test'); setResult(null);
    try {
      const r = await api.testConnectionValues({ type, id: id || 'test', uri, database: own ? other.database : isSql ? pg.database.trim() : form.database.trim(), options: optionsPayload(opts) });
      setResult(r.status === 'hostKey' ? { ok: false, hostKey: r.fingerprint, text: r.message } : { ok: true, text: `Successful: ${r.message || 'connected'}` });
    }
    catch (error) { setResult({ ok: false, text: error.message }); }
    finally { setBusy(''); }
  };
  const finish = async () => {
    setBusy('save');
    try {
      const saved = await api.saveConnection({ type, id, name: form.name.trim(), uri, database: own ? other.database : isSql ? pg.database.trim() : form.database.trim(), folder: form.folder || '',
        options: optionsPayload(opts), ...(editing ? {} : { area, create: true }) });
      notify(`Data adapter “${form.name.trim()}” ${editing ? 'updated' : 'created'}`, 'ok');
      onSaved(saved.connection);
    } catch (error) { setResult({ ok: false, text: error.message }); setBusy(''); }
  };

  // in <body>: a fixed box inside an animated panel would be cut to that panel
  return createPortal(
    <div className="aw-back" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <div className="aw-modal" role="dialog" aria-label="Data Adapter Wizard">
        <div className="wizard-head">
          <h3>{editing ? `Edit Data Adapter — ${initial.name}` : 'Data Adapter Wizard'}</h3>
          <p className="muted">{step === 1 ? 'Create a new data adapter: choose its type.' : `${presetInfo?.name || typeInfo.name}: ${own && ['files', 'sqlite', 'duckdb'].includes(type) ? 'name it, upload the files, then Finish' : 'enter the connection details, test them, then Finish'}.`}</p>
          {area && (areas && !editing
            ? <div className="aw-areas" role="radiogroup" aria-label="Where it is used">{areas.map((key) => (
              <button key={key} type="button" role="radio" aria-checked={area === key} className={area === key ? 'on' : ''} onClick={() => setArea(key)}><b>{AREAS[key].name}</b><small>{AREAS[key].use}</small></button>))}</div>
            : <span className="aw-area">Only for {AREAS[area].name}</span>)}
        </div>
        {step === 1 && (
          <div className="wizard-pick">
            <input className="wizard-search" type="search" value={search} autoFocus onChange={(e) => setSearch(e.target.value)} placeholder="Search 26 sources: postgres, excel, redis, snowflake…" aria-label="Search data source types" />
            {TYPE_GROUPS.map(([group, title]) => {
              const words = search.toLowerCase().split(/\s+/).filter(Boolean);
              const hit = (text) => words.every((w) => text.toLowerCase().includes(w));
              const types = ADAPTER_TYPES.filter((t) => t.group === group && hit(`${t.name} ${t.text} ${t.id}`));
              const presets = PRESETS.filter((p) => ADAPTER_TYPES.find((t) => t.id === p.type)?.group === group && hit(`${p.name} ${p.type}`));
              if (!types.length && !presets.length) return null;
              return (
                <section key={group} className="wizard-group">
                  <h4>{title}</h4>
                  <ul className="wizard-types" role="listbox" aria-label={title}>
                    {types.map((t) => (
                      <li key={t.id} role="option" aria-selected={type === t.id && !preset} className={type === t.id && !preset ? 'on' : ''} title="Double-click to continue"
                        onClick={() => chooseType(t.id)} onDoubleClick={() => { chooseType(t.id); setStep(2); }}>
                        <span className="t-icon"><DbLogo type={t.id} size={26} /></span><span><b>{t.name}</b><small>{t.text}</small></span>
                      </li>
                    ))}
                    {presets.map((p) => (
                      <li key={p.id} role="option" aria-selected={preset === p.id} className={`preset${preset === p.id ? ' on' : ''}`} title={`Uses the ${DB_STYLE[p.type].label} driver · double-click to continue`}
                        onClick={() => chooseType(p.type, p)} onDoubleClick={() => { chooseType(p.type, p); setStep(2); }}>
                        <span className="t-icon"><PresetLogo preset={p} size={26} /></span><span><b>{p.name}</b><small>via {DB_STYLE[p.type].label}{p.port ? ` · port ${p.port}` : ''}{p.sslmode === 'require' ? ' · SSL' : ''}</small></span>
                      </li>
                    ))}
                  </ul>
                </section>
              );
            })}
          </div>
        )}
        {step === 2 && (
          <div className="wizard-form">
            <label className="aw-field">Name<input autoFocus value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder={own ? ({ files: 'Sales sheets', gsheets: 'Budget sheet', sqlite: 'Branch SQLite', duckdb: 'Warehouse DuckDB' }[type] || `Finance ${typeInfo.name.split(' ')[0]}`) : isSql ? `Finance ${(presetInfo?.name || typeInfo.name).split(' ')[0]}` : 'KMBL UAT MongoDB'} /></label>
            {!editing && id && <small className={duplicateId ? 'err' : 'muted'}>ID: {id}{duplicateId ? ' — already used, choose another name' : ''}</small>}
            {own ? (
              <>
                <SourceForm type={type} id={id} editing={editing} onChange={(v) => { setOther(v); setResult(null); }} notify={notify} />
                <SqlOptions type={type} opts={opts} onOpts={setOpts} saved={savedOptions} />
              </>
            ) : isSql ? (
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
                <SqlOptions type={type} opts={opts} onOpts={setOpts} saved={savedOptions} params={pg.params} onParams={(rows) => setPgField({ params: rows })} />
              </>
            ) : (
              <>
                <label className="aw-field">MongoDB URL
                  <PasswordInput autoComplete="new-password" value={form.uri} onChange={(e) => { setForm({ ...form, uri: e.target.value }); setResult(null); }} placeholder={presetInfo?.uri || 'mongodb://user:password@host:27017/Database'} />
                  <small className="muted">{editing ? 'The saved URL is never shown. Enter it again (it replaces the old one).' : 'Saved with its password in a private file on the server; never shown again. For a remote MongoDB connect the VPN first.'}</small>
                </label>
                <label className="aw-field">Database <small>(optional when the URL has one)</small><input value={form.database} onChange={(e) => setForm({ ...form, database: e.target.value })} /></label>
                <MongoOptions uri={form.uri} onUri={(value) => { setForm((current) => ({ ...current, uri: value })); setResult(null); }} opts={opts} onOpts={setOpts} saved={savedOptions} />
              </>
            )}
            {folders.length > 0 && area !== 'analytics' && area !== 'query' && <label className="aw-field">Folder
              <select value={form.folder || ''} onChange={(e) => setForm({ ...form, folder: e.target.value })}>
                <option value="">Data Adapters (top level)</option>
                {folders.map((f) => <option key={f} value={f}>{f.split('/').join(' / ')}</option>)}
              </select>
            </label>}
            {result && !result.hostKey && <p className={`aw-result ${result.ok ? 'ok' : 'err'}`}>{result.ok ? '✓ ' : '✕ '}{result.text}</p>}
            {result?.hostKey && (
              <div className="co-trust" role="alert">
                <p><b>New SSH server.</b> Its host key is <code>{result.hostKey}</code>. Check it with the server’s administrator
                  (<code>ssh-keygen -lf /etc/ssh/ssh_host_*_key.pub</code>): if it is the same, trust it.</p>
                <button type="button" className="btn primary small" onClick={() => {
                  const ssh = opts.ssh || { ...savedOptions.ssh, password: '', privateKey: '', passphrase: '' };
                  setOpts({ ...opts, ssh: { ...ssh, hostKey: result.hostKey } });
                  setResult({ ok: true, text: 'Host key trusted: press Test again' });
                }}>Trust this host key</button>
              </div>
            )}
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
    </div>, document.body);
}


/** "PostgreSQL", "MongoDB", … for a type id. */
export const adapterTypeLabel = (type) => (DB_STYLE[type || 'mongodb'] || DB_STYLE.mongodb).label;
