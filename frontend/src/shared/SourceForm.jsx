// Connection fields of the newer data sources: Snowflake, Elasticsearch, Redis (a server and a login), the uploaded
// ones (Files & Sheets, SQLite, DuckDB) and Google Sheets. onChange gets { uri, database } (uri '' = not ready).
import { useEffect, useRef, useState } from 'react';
import { api } from '../api.js';
import PasswordInput from './PasswordInput.jsx';

const enc = encodeURIComponent;
const ACCEPT = {
  files: '.csv,.tsv,.txt,.xlsx,.xlsm,.json,.ndjson,.jsonl,.parquet',
  sqlite: '.sqlite,.sqlite3,.db',
  duckdb: '.duckdb,.ddb,.db',
};
export const UPLOAD_KINDS = ['files', 'sqlite', 'duckdb'];
export const OWN_FORM = ['snowflake', 'elasticsearch', 'redis', 'gsheets', ...UPLOAD_KINDS];

const readBase64 = (file) => new Promise((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
  reader.onerror = () => reject(new Error(`${file.name} could not be read`));
  reader.readAsDataURL(file);
});
const size = (bytes) => (bytes > 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`);

/** The URL the server keeps for these kinds (the same shapes connectors/sql.py checks). */
export function sourceUrl(type, f, id) {
  const auth = f.user ? `${enc(f.user)}${f.password ? `:${enc(f.password)}` : ''}@` : (f.password ? `:${enc(f.password)}@` : '');
  if (type === 'snowflake') {
    if (!f.account || !f.database || !f.user) return '';
    const q = new URLSearchParams(Object.entries({ warehouse: f.warehouse, role: f.role }).filter(([, v]) => v)).toString();
    return `snowflake://${auth}${f.account.trim()}/${enc(f.database.trim())}${f.schema ? `/${enc(f.schema.trim())}` : ''}${q ? `?${q}` : ''}`;
  }
  if (type === 'elasticsearch') return f.host ? `elasticsearch://${auth}${f.host.trim()}:${f.port || 9200}${f.tls ? '?secure=true' : ''}` : '';
  if (type === 'redis') return f.host ? `${f.tls ? 'rediss' : 'redis'}://${auth}${f.host.trim()}:${f.port || 6379}/${f.db || 0}` : '';
  if (type === 'gsheets') return f.sheetId ? `gsheets://docs.google.com/${f.sheetId}` : '';
  if (UPLOAD_KINDS.includes(type)) return id && f.tables > 0 ? `${type}://local/${id}` : '';
  return '';
}

const DEFAULTS = {
  snowflake: { account: '', database: '', schema: 'PUBLIC', warehouse: '', role: '', user: '', password: '' },
  elasticsearch: { host: 'localhost', port: '9200', user: '', password: '', tls: false },
  redis: { host: 'localhost', port: '6379', db: '0', user: '', password: '', tls: false },
  gsheets: { link: '', sheetId: '', tabs: [] },
};

/** Fields for one of OWN_FORM. id: the data source ID (uploads are kept under it). */
export default function SourceForm({ type, id, editing, onChange, notify = () => {} }) {
  const [f, setF] = useState(() => ({ ...(DEFAULTS[type] || {}), tables: 0 }));
  const [files, setFiles] = useState({ files: [], tables: [] });
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [over, setOver] = useState(false);
  const input = useRef(null);
  const set = (patch) => setF((current) => ({ ...current, ...patch }));
  const uploads = UPLOAD_KINDS.includes(type);

  useEffect(() => { setF({ ...(DEFAULTS[type] || {}), tables: 0 }); setError(''); }, [type]);
  useEffect(() => {          // an existing source: list what is already uploaded
    if (!uploads || !id || !editing) return;
    api.sourceFiles(id).then((r) => { setFiles(r); set({ tables: (r.tables || []).length || (r.files || []).length }); }).catch(() => {});
  }, [type, id, editing]); // eslint-disable-line react-hooks/exhaustive-deps -- when the source changes
  useEffect(() => {
    const uri = sourceUrl(type, f, id);
    onChange({ uri, database: type === 'gsheets' ? f.sheetId : uploads ? id : type === 'snowflake' ? f.database : '' });
  }, [type, f, id]); // eslint-disable-line react-hooks/exhaustive-deps -- onChange is a new function each render

  const upload = async (list) => {
    if (!id) { setError('Give the data source a name first: the files are kept under its ID'); return; }
    setError('');
    for (const file of [...list]) {
      setBusy(`Reading ${file.name}…`);
      try {
        const r = await api.uploadSourceFile(id, { kind: type, name: file.name, file: await readBase64(file) });
        setFiles(r);
        set({ tables: (r.tables || []).length || (r.files || []).length });
        notify(`${file.name} added${r.tables?.length ? ` — ${r.tables.length} table${r.tables.length > 1 ? 's' : ''}` : ''}`, 'ok');
      } catch (e) { setError(`${file.name}: ${e.message}`); }
    }
    setBusy('');
  };
  const remove = async (name) => {
    setBusy(`Removing ${name}…`);
    try { const r = await api.deleteSourceFile(id, name); setFiles(r); set({ tables: (r.tables || []).length }); }
    catch (e) { setError(e.message); }
    setBusy('');
  };
  const readSheet = async () => {
    setBusy('Reading the Sheet…'); setError('');
    try { const r = await api.readSheet(f.link); set({ sheetId: r.sheetId, tabs: r.tables || [] }); }
    catch (e) { set({ sheetId: '', tabs: [] }); setError(e.message); }
    setBusy('');
  };
  const text = (key, label, extra = {}) => (
    <label className="aw-field">{label}<input value={f[key] ?? ''} onChange={(e) => set({ [key]: e.target.value })} {...extra} /></label>
  );
  const login = (
    <>
      {text('user', type === 'redis' ? 'Username (optional)' : 'Username', { autoComplete: 'off', placeholder: type === 'snowflake' ? 'REPORT_READER' : '' })}
      <label className="aw-field">Password<PasswordInput autoComplete="new-password" value={f.password} onChange={(e) => set({ password: e.target.value })} /></label>
    </>
  );

  return (
    <div className="sf">
      {type === 'snowflake' && (
        <div className="pg-grid">
          {text('account', 'Account', { placeholder: 'xy12345.eu-central-1', autoFocus: true })}
          {text('warehouse', 'Warehouse', { placeholder: 'REPORTING_WH' })}
          {text('database', 'Database', { placeholder: 'SALES' })}
          {text('schema', 'Schema')}
          {text('role', 'Role (optional)', { placeholder: 'REPORTER' })}
          <span />
          {login}
        </div>
      )}
      {(type === 'elasticsearch' || type === 'redis') && (
        <div className="pg-grid">
          {text('host', 'Host', { placeholder: type === 'redis' ? 'cache.example.com' : 'search.example.com' })}
          {text('port', 'Port', { inputMode: 'numeric' })}
          {type === 'redis' && text('db', 'Database number', { inputMode: 'numeric' })}
          <label className="check-line"><input type="checkbox" checked={f.tls} onChange={(e) => set({ tls: e.target.checked })} /> {type === 'redis' ? 'TLS (rediss://)' : 'HTTPS'}</label>
          {login}
        </div>
      )}
      {type === 'redis' && <p className="sf-note">Every key prefix becomes a table: <code>user:1</code>, <code>user:2</code> → table <b>user</b>; the fields of a hash (or of a JSON value) become its columns. Only read commands are sent (SCAN, HGETALL, GET).</p>}
      {type === 'elasticsearch' && <p className="sf-note">Each index is a table, queried with Elasticsearch SQL. Give a user with the <code>read</code> privilege only.</p>}
      {type === 'snowflake' && <p className="sf-note">Give a role that may only SELECT; each query has a time limit and nothing is committed.</p>}

      {type === 'gsheets' && (
        <>
          <div className="sf-sheet">
            <label className="aw-field">Google Sheet link<input value={f.link} autoFocus onChange={(e) => set({ link: e.target.value, sheetId: '', tabs: [] })} placeholder="https://docs.google.com/spreadsheets/d/…/edit" /></label>
            <button type="button" className="btn" disabled={!f.link.trim() || Boolean(busy)} onClick={readSheet}>{busy || 'Read the Sheet'}</button>
          </div>
          <p className="sf-note">Share the Sheet as <b>Anyone with the link · Viewer</b>. Each tab becomes a table; it is read again when the copy is older than 5 minutes.</p>
          {f.tabs.length > 0 && <TableChips tables={f.tabs} />}
        </>
      )}

      {uploads && (
        <>
          <div className={`sf-drop${over ? ' over' : ''}${busy ? ' busy' : ''}`} role="button" tabIndex={0}
            onClick={() => !busy && input.current?.click()} onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && input.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); upload(e.dataTransfer.files); }}>
            <span className="sf-drop-icon" aria-hidden="true">⇪</span>
            <b>{busy || (type === 'files' ? 'Drop CSV, Excel, JSON or Parquet files' : `Drop the ${type === 'sqlite' ? 'SQLite' : 'DuckDB'} database file`)}</b>
            <small>{type === 'files' ? 'or click to choose · every file and every Excel sheet becomes a table · up to 100 MB each' : 'or click to choose · opened read-only · up to 100 MB'}</small>
            <input ref={input} type="file" hidden multiple={type === 'files'} accept={ACCEPT[type]} onChange={(e) => { upload(e.target.files); e.target.value = ''; }} />
          </div>
          {(files.files || []).length > 0 && (
            <ul className="sf-files">
              {files.files.map((file) => (
                <li key={file.name}><span className="sf-ext">{file.name.split('.').pop()}</span><b>{file.name}</b><small>{size(file.bytes)}</small>
                  {type === 'files' && <button type="button" className="sf-x" title="Remove this file" disabled={Boolean(busy)} onClick={() => remove(file.name)}>✕</button>}</li>
              ))}
            </ul>
          )}
          {(files.tables || []).length > 0 && <TableChips tables={files.tables} />}
        </>
      )}
      {error && <p className="aw-result err">✕ {error}</p>}
    </div>
  );
}

function TableChips({ tables }) {
  return (
    <div className="sf-tables">
      <small>Tables for your SQL</small>
      <ul>{tables.map((t) => <li key={t.name} title={(t.columns || []).join(', ')}><code>{t.name}</code><span>{t.rows?.toLocaleString?.() ?? ''} rows · {(t.columns || []).length} cols</span></li>)}</ul>
    </div>
  );
}
