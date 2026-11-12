import { useCallback, useEffect, useState } from 'react';
import { api } from '../api.js';
import { Notice } from '../portal/Shell.jsx';

const EMPTY = { id: '', name: '', uri: '', database: '', folder: '' };

function toConnectionId(name) {
  const id = name.toLowerCase().replace(/[^a-z0-9_-]+/g, '_').replace(/^[_-]+|[_-]+$/g, '').slice(0, 40);
  return id && /^[a-z0-9]/.test(id) ? id : `mongo_${id}`.slice(0, 40);
}
function describeTest(checked) {
  const identity = checked.identityReported
    ? (checked.authenticatedUsers?.length ? ` · as ${checked.authenticatedUsers.join(', ')}` : ' · no authenticated user reported') : '';
  const warning = checked.databaseWarning ? ` · ${checked.databaseWarning}` : '';
  return checked.readChecked?.length ? `Connected · report read verified: ${checked.readChecked.join(', ')}${identity}${warning}` : `${checked.message}${identity}${warning}`;
}
function FolderSelect({ folders, value, onChange, label }) {
  return (
    <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">Top level (Data adapters)</option>
      {folders.map((f) => <option key={f} value={f}>{f.split('/').join(' / ')}</option>)}
    </select>
  );
}

export default function DataSourcesTab({ notify }) {
  const [connections, setConnections] = useState([]);
  const [folders, setFolders] = useState([]);
  const [tests, setTests] = useState({}); // id -> { text, kind }
  const [form, setForm] = useState(EMPTY);
  const [showUri, setShowUri] = useState(false);
  const [formMessage, setFormMessage] = useState(null);
  const [busy, setBusy] = useState('');

  const load = useCallback(async () => {
    try {
      const [c, f] = await Promise.all([api.connections(), api.folders()]);
      setConnections([...c.connections].sort((a, b) => (a.folder || '').localeCompare(b.folder || '') || a.name.localeCompare(b.name)));
      setFolders(f.folders);
    } catch (error) { notify(error.message, 'error'); }
  }, [notify]);
  useEffect(() => { load(); }, [load]);

  const test = async (connection) => {
    setTests((t) => ({ ...t, [connection.id]: { text: 'Checking the MongoDB server and report read access…', kind: '' } }));
    try {
      const text = describeTest(await api.testConnection(connection.id));
      setTests((t) => ({ ...t, [connection.id]: { text, kind: 'ok' } }));
    }
    catch (error) { setTests((t) => ({ ...t, [connection.id]: { text: error.message, kind: 'error' } })); }
  };
  const move = async (connection, folder) => {
    try { await api.moveConnection(connection.id, folder); await load(); notify(`“${connection.name}” moved to ${folder || 'the top level'}.`); }
    catch (error) { notify(error.message, 'error'); }
  };
  const remove = async (connection) => {
    if (!window.confirm(`Delete data source “${connection.name}”?`)) return;
    try { await api.deleteConnection(connection.id); await load(); notify('Data source removed.'); }
    catch (error) { notify(error.message, 'error'); }
  };
  const payload = () => ({ ...form, id: form.id || toConnectionId(form.name.trim()), name: form.name.trim(), uri: form.uri.trim(), database: form.database.trim() });
  const testForm = async () => {
    setBusy('test');
    setFormMessage({ text: 'Checking the MongoDB server…', kind: '' });
    try {
      const text = describeTest(await api.testConnectionValues(payload()));
      setFormMessage({ text, kind: 'ok' });
    }
    catch (error) { setFormMessage({ text: error.message, kind: 'error' }); }
    finally { setBusy(''); }
  };
  const save = async (event) => {
    event.preventDefault();
    setBusy('save');
    try {
      await api.saveConnection(payload());
      await load();
      setFormMessage({ text: `Data source “${form.name.trim()}” saved in the private settings file.`, kind: 'ok' });
      setForm(EMPTY);
      setShowUri(false);
    } catch (error) { setFormMessage({ text: error.message, kind: 'error' }); }
    finally { setBusy(''); }
  };

  return (
    <div className="grid-2">
      <section className="card">
        <div className="card-head"><div><p className="eyebrow">Registered</p><h2>Data sources</h2></div><button className="btn" onClick={load}>Refresh</button></div>
        <div className="stack">
          {!connections.length && <div className="empty">No data sources configured.</div>}
          {connections.map((c) => (
            <div key={c.id} className="item" style={{ display: 'grid' }}>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <div><strong>{c.name} · {c.id}</strong><small>{c.database || 'Database not set'} · {c.credentialSource || 'Private local file'} · {c.configured ? 'ready to test' : 'setup needed'}</small></div>
                <div className="row">
                  <button className="btn sm" onClick={() => test(c)}>Test connection</button>
                  <button className="btn sm quiet" onClick={() => { setForm({ id: c.id, name: c.name, uri: '', database: c.database || '', folder: c.folder || '' }); setFormMessage({ text: `Enter the new URL for “${c.name}”; it replaces the old one.`, kind: '' }); }}>Update URL</button>
                  {c.id !== 'default' && <button className="btn sm danger" onClick={() => remove(c)}>Delete</button>}
                </div>
              </div>
              <label className="row" style={{ fontSize: 12, color: 'var(--muted)' }}>Folder <span style={{ minWidth: 220 }}><FolderSelect folders={folders} value={c.folder || ''} onChange={(folder) => move(c, folder)} label={`Folder of ${c.name}`} /></span></label>
              {tests[c.id] && <Notice message={tests[c.id]} />}
            </div>
          ))}
        </div>
      </section>
      <form className="card stack" onSubmit={save}>
        <div><p className="eyebrow">{form.id ? 'Update' : 'Add'}</p><h2>{form.id ? `Update “${form.name}”` : 'Add a MongoDB data source'}</h2></div>
        <label className="field"><span>Data source name</span><input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} maxLength={80} placeholder="e.g. Branch reports" required /></label>
        <label className="field"><span>Save in folder</span><FolderSelect folders={folders} value={form.folder} onChange={(folder) => setForm({ ...form, folder })} label="Folder of the data source" /><span className="hint">Optional, for example cascade / KMBLGL / Data Source.</span></label>
        <label className="field"><span>MongoDB connection URL</span>
          <div className="row" style={{ flexWrap: 'nowrap' }}>
            <input type={showUri ? 'text' : 'password'} autoComplete="new-password" value={form.uri} onChange={(e) => setForm({ ...form, uri: e.target.value })} placeholder="mongodb://user:password@host:27017/DatabaseName" required />
            <button type="button" className="btn sm quiet" onClick={() => setShowUri(!showUri)}>{showUri ? 'Hide' : 'Show'}</button>
          </div>
          <span className="hint">The URL with its password is saved in a private file on the server; it is never shown again on screen.</span>
        </label>
        <label className="field"><span>Database name (optional)</span><input value={form.database} onChange={(e) => setForm({ ...form, database: e.target.value })} placeholder="Leave empty if the URL already contains the database" /></label>
        <div className="row">
          <button type="button" className="btn" disabled={Boolean(busy) || !form.uri} onClick={testForm}>{busy === 'test' ? 'Testing…' : 'Test connection'}</button>
          <button type="submit" className="btn primary" disabled={Boolean(busy)}>{busy === 'save' ? 'Saving…' : 'Save data source'}</button>
          {form.id && <button type="button" className="btn quiet" onClick={() => { setForm(EMPTY); setFormMessage(null); }}>Cancel</button>}
        </div>
        <Notice message={formMessage} />
      </form>
    </div>
  );
}
