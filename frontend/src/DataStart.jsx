// Step 1 of a new report: "Where does the data live?". Pick a saved data adapter on the right with one click, or
// make a new one on the left (type cards → connection form → Test → Save). Either way the report's adapter is set
// (and the query language follows: SQL for PostgreSQL/MySQL/SQL Server/Oracle, Mongo shell for MongoDB), then the
// next step opens (the layout gallery, then Setup's Query and Fields).
import { useEffect, useMemo, useState } from 'react';
import { api } from './api.js';
import { ADAPTER_TYPES, DbLogo, SQL_KINDS, adapterId, adapterTypeLabel, sqlUrl } from './shared/adapters.jsx';
import PasswordInput from './shared/PasswordInput.jsx';

const STEPS = ['Data', 'Fields', 'Layout', 'Design', 'Preview & publish'];
const SHORT = { mongodb: 'MongoDB', postgresql: 'PostgreSQL', mysql: 'MySQL / MariaDB', sqlserver: 'SQL Server', oracle: 'Oracle' };

/** A short burst of coloured bits from the Test button when the connection works. */
function Confetti() {
  const bits = useMemo(() => Array.from({ length: 22 }, (_, i) => ({
    x: Math.round(Math.cos((i / 22) * Math.PI * 2) * (60 + (i % 4) * 18)),
    y: Math.round(Math.sin((i / 22) * Math.PI * 2) * (40 + (i % 3) * 16)) - 30,
    r: (i * 47) % 360,
    c: ['b', 'v', 'c', 'a', 'g'][i % 5],
  })), []);
  return <span className="ds-confetti" aria-hidden="true">{bits.map((b, i) => <i key={i} className={`tint-${b.c}`} style={{ '--x': `${b.x}px`, '--y': `${b.y}px`, '--r': `${b.r}deg` }} />)}</span>;
}

/** Patch for the report when it switches to this adapter: the query language follows the adapter's kind. */
export function adapterPatch(report, adapter) {
  const patch = { connectionId: adapter.id };
  const sql = Boolean(adapter.type && adapter.type !== 'mongodb');
  if (sql && report.queryLanguage !== 'sql') Object.assign(patch, { queryLanguage: 'sql', collection: '' });
  if (!sql && report.queryLanguage === 'sql') patch.queryLanguage = 'mongosh';
  return patch;
}

export default function DataStart({ report, dispatch, notify, onNext, onClose }) {
  const [adapters, setAdapters] = useState(null);
  const [folders, setFolders] = useState([]);
  const [filter, setFilter] = useState('');
  const [type, setType] = useState('mongodb');
  const [form, setForm] = useState({ name: '', uri: '', database: '', folder: '' });
  const [pg, setPg] = useState({ host: 'localhost', port: '5432', database: '', user: '', password: '', sslmode: 'prefer' });
  const [result, setResult] = useState(null); // { ok, text, chips }
  const [busy, setBusy] = useState('');
  const [picked, setPicked] = useState(report.connectionId || '');
  const [pickTest, setPickTest] = useState({}); // id → { state: busy|ok|err, text }

  const load = async () => {
    try {
      const [c, f] = await Promise.all([api.connections(), api.folders()]);
      setAdapters(c.connections || []);
      setFolders([...new Set([...(f.folders || []), ...(c.connections || []).map((a) => a.folder).filter(Boolean)])].sort());
    } catch (error) { setAdapters([]); notify(`Could not load data adapters: ${error.message}`, 'error'); }
  };
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps -- once, when the screen opens
  useEffect(() => {
    const onKey = (event) => { if (event.key === 'Escape' && !busy) onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [busy, onClose]);

  const isSql = type !== 'mongodb';
  const kind = SQL_KINDS[type] || SQL_KINDS.postgresql;
  const uri = isSql ? sqlUrl(type, pg) : form.uri.trim();
  const database = isSql ? pg.database.trim() : form.database.trim();
  const id = adapterId(form.name);
  const duplicate = Boolean(id) && (adapters || []).some((a) => a.id === id);
  const chooseType = (next) => {
    setType(next); setResult(null);
    if (SQL_KINDS[next] && Object.values(SQL_KINDS).some((k) => k.port === pg.port)) setPg((p) => ({ ...p, port: SQL_KINDS[next].port, sslmode: 'prefer' }));
  };
  const setPgField = (patch) => { setPg((p) => ({ ...p, ...patch })); setResult(null); };

  const test = async () => {
    setBusy('test'); setResult(null);
    try {
      const r = await api.testConnectionValues({ type, id: id || 'test', uri, database });
      // short parts of the server's answer ("PostgreSQL 16.2", "database reports") become chips; a long sentence stays a note
      const parts = String(r.message || '').split('·').map((s) => s.replace(/^Connected:?\s*/i, '').trim()).filter(Boolean);
      const chips = [SHORT[type], r.database || database, ...parts.filter((p) => p.length <= 40)];
      setResult({ ok: true, text: 'Connected', chips: [...new Set(chips.filter(Boolean))], note: parts.filter((p) => p.length > 40).join(' · ') });
    } catch (error) { setResult({ ok: false, text: error.message }); }
    finally { setBusy(''); }
  };

  const use = (adapter, next = true) => {
    dispatch({ type: 'REPORT', patch: adapterPatch(report, adapter) });
    notify(`Data adapter: ${adapter.name}${adapter.type && adapter.type !== 'mongodb' ? ' — write the query in SQL' : ''}`, 'ok');
    if (next) onNext();
  };

  const save = async () => {
    setBusy('save');
    try {
      await api.saveConnection({ type, id, name: form.name.trim(), uri, database, folder: form.folder });
      notify(`Data adapter “${form.name.trim()}” created`, 'ok');
      use({ id, name: form.name.trim(), type });
    } catch (error) {
      setResult({ ok: false, text: error.status === 403 ? 'Saving a data adapter needs the admin token (Server → sign in as admin)' : error.message });
      setBusy('');
    }
  };

  const testSaved = async (adapter) => {
    setPickTest((t) => ({ ...t, [adapter.id]: { state: 'busy' } }));
    try { const r = await api.testConnection(adapter.id); setPickTest((t) => ({ ...t, [adapter.id]: { state: 'ok', text: r.message || 'Connected' } })); }
    catch (error) { setPickTest((t) => ({ ...t, [adapter.id]: { state: 'err', text: error.message } })); }
  };

  const groups = useMemo(() => {
    const words = filter.toLowerCase().split(/\s+/).filter(Boolean);
    const map = new Map();
    (adapters || []).filter((a) => words.every((w) => `${a.name} ${a.id} ${a.database || ''} ${a.folder || ''} ${adapterTypeLabel(a.type)}`.toLowerCase().includes(w)))
      .forEach((a) => { const k = a.folder || ''; if (!map.has(k)) map.set(k, []); map.get(k).push(a); });
    return [...map.entries()].sort(([a], [b]) => (a === '' ? -1 : b === '' ? 1 : a.localeCompare(b)));
  }, [adapters, filter]);
  const pickedAdapter = (adapters || []).find((a) => a.id === picked);
  const canSave = form.name.trim() && uri && !duplicate && !busy;

  return (
    <div className="ds-back" role="dialog" aria-label="New report: data adapter">
      <div className="ds">
        <header className="ds-head">
          <ol className="ds-steps" aria-label="Steps">
            {STEPS.map((s, i) => <li key={s} className={i === 0 ? 'on' : ''}><span>{i + 1}</span>{s}</li>)}
          </ol>
          <button type="button" className="ds-x" onClick={onClose} title="Close (Esc): choose the adapter later in Setup" aria-label="Close">✕</button>
          <p className="ds-kicker">Step 1 of 5 · Data adapter</p>
          <h2>Where does the data live?</h2>
          <p className="muted">Use a saved data adapter, or connect a new database. You can change it later in Setup → Data adapter.</p>
        </header>

        <div className="ds-split">
          <section className="ds-new" aria-label="Connect a new database">
            <h3><span className="ds-num tint-b">+</span>Connect a new database</h3>
            <ul className="ds-types" role="radiogroup" aria-label="Database type">
              {ADAPTER_TYPES.map((t, i) => (
                <li key={t.id} style={{ '--i': i }}>
                  <button type="button" role="radio" aria-checked={type === t.id} className={type === t.id ? 'on' : ''} onClick={() => chooseType(t.id)} title={t.text}>
                    <DbLogo type={t.id} size={30} /><b>{SHORT[t.id]}</b>
                  </button>
                </li>
              ))}
            </ul>

            <div className="ds-form" key={isSql ? 'sql' : 'mongo'}>
              <label className="ds-field ds-wide">Name
                <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder={isSql ? `Finance ${SHORT[type].split(' ')[0]}` : 'KMBL UAT MongoDB'} />
                {id && <small className={duplicate ? 'err' : 'muted'}>ID: {id}{duplicate ? ' — already used: pick it on the right, or choose another name' : ''}</small>}
              </label>
              {isSql ? (
                <>
                  <label className="ds-field ds-wide">Host<input value={pg.host} onChange={(e) => setPgField({ host: e.target.value })} placeholder="db.example.com" /></label>
                  <label className="ds-field">Port<input value={pg.port} inputMode="numeric" onChange={(e) => setPgField({ port: e.target.value.replace(/[^0-9]/g, '') })} /></label>
                  <label className="ds-field">{kind.database}<input value={pg.database} onChange={(e) => setPgField({ database: e.target.value })} placeholder={type === 'oracle' ? 'FREEPDB1' : 'reports'} /></label>
                  <label className="ds-field">Username<input value={pg.user} autoComplete="off" onChange={(e) => setPgField({ user: e.target.value })} placeholder="report_reader" /></label>
                  <label className="ds-field">Password<PasswordInput autoComplete="new-password" value={pg.password} onChange={(e) => setPgField({ password: e.target.value })} /></label>
                  {kind.ssl && (
                    <label className="ds-field">SSL
                      <select value={pg.sslmode} onChange={(e) => setPgField({ sslmode: e.target.value })}>{kind.ssl.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
                    </label>
                  )}
                </>
              ) : (
                <>
                  <label className="ds-field ds-wide">MongoDB URL
                    <PasswordInput autoComplete="new-password" value={form.uri} onChange={(e) => { setForm({ ...form, uri: e.target.value }); setResult(null); }} placeholder="mongodb://user:password@host:27017/Database" />
                  </label>
                  <label className="ds-field">Database <small>(optional when the URL has one)</small><input value={form.database} onChange={(e) => { setForm({ ...form, database: e.target.value }); setResult(null); }} /></label>
                </>
              )}
              <label className="ds-field">Folder
                <select value={form.folder} onChange={(e) => setForm({ ...form, folder: e.target.value })}>
                  <option value="">Data Adapters (top level)</option>
                  {folders.map((f) => <option key={f} value={f}>{f.split('/').join(' / ')}</option>)}
                </select>
              </label>
              <small className="muted ds-wide">Saved with its password in a private file on the server and never shown again. Give the report user read-only rights: reports only read.{isSql ? '' : ' For a remote MongoDB connect the VPN first.'}</small>
            </div>

            <div className="ds-testrow">
              <button type="button" className={`ds-test ${busy === 'test' ? 'busy' : ''} ${result?.ok ? 'ok' : ''}`} disabled={Boolean(busy) || !uri} onClick={test}>
                {busy === 'test' ? <span className="ds-spin" aria-hidden="true" /> : <span aria-hidden="true">⚡</span>}
                {busy === 'test' ? 'Testing…' : result?.ok ? 'Connected ✓' : 'Test connection'}
                {result?.ok && <Confetti />}
              </button>
              {result && !result.ok && <p className="ds-err" role="alert">✕ {result.text}</p>}
            </div>
            {result?.ok && (
              <ul className="ds-chips" aria-label="What the test found">
                {result.chips.map((c, i) => <li key={c} style={{ '--i': i }}>{c}</li>)}
              </ul>
            )}
            {result?.ok && result.note && <p className="ds-note muted">{result.note}</p>}
          </section>

          <aside className="ds-saved" aria-label="Saved data adapters">
            <h3><span className="ds-num tint-g">✓</span>Use a saved adapter</h3>
            <input className="ds-search" type="search" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Search adapters…" aria-label="Search adapters" />
            {adapters === null && <p className="muted">Loading…</p>}
            {adapters !== null && !adapters.length && <p className="muted">No data adapters yet: connect one on the left.</p>}
            {groups.map(([folder, list]) => (
              <div key={folder || '(top)'} className="ds-group">
                <h4>{folder ? `📁 ${folder.split('/').join(' / ')}` : 'Data Adapters'}</h4>
                <ul>
                  {list.map((a, i) => {
                    const t = pickTest[a.id];
                    return (
                      <li key={a.id} style={{ '--i': i }}>
                        <button type="button" className={`ds-adapter ${picked === a.id ? 'on' : ''} ${a.configured === false ? 'off' : ''}`} aria-pressed={picked === a.id}
                          onClick={() => setPicked(a.id)} onDoubleClick={() => a.configured !== false && use(a)}>
                          <DbLogo type={a.type || 'mongodb'} size={22} />
                          <span><b>{a.name}</b><small>{adapterTypeLabel(a.type)}{a.database ? ` · ${a.database}` : ''}{a.configured === false ? ' · not configured' : ''}</small></span>
                          {t?.state === 'ok' && <em className="ds-dot ok" title={t.text}>●</em>}
                          {t?.state === 'err' && <em className="ds-dot err" title={t.text}>●</em>}
                        </button>
                        {picked === a.id && (
                          <div className="ds-pickbar">
                            <button type="button" className="btn sm" disabled={t?.state === 'busy'} onClick={() => testSaved(a)}>{t?.state === 'busy' ? 'Testing…' : 'Test'}</button>
                            {t && t.state !== 'busy' && <small className={t.state === 'ok' ? 'ok' : 'err'}>{t.text}</small>}
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </aside>
        </div>

        <footer className="ds-foot">
          <button type="button" className="btn" onClick={onNext} disabled={Boolean(busy)} title="Go on without an adapter: choose it later in Setup → Data adapter">Skip for now</button>
          {pickedAdapter && <button type="button" className="btn" disabled={Boolean(busy) || pickedAdapter.configured === false} onClick={() => use(pickedAdapter)}>Use “{pickedAdapter.name}” →</button>}
          <button type="button" className="ds-go" disabled={!canSave} onClick={save} title={canSave ? 'Save the new adapter, use it for this report, then choose the layout' : 'Fill in the name and the connection first'}>
            {busy === 'save' ? 'Saving…' : 'Save, then fields and layout →'}
          </button>
        </footer>
      </div>
    </div>
  );
}
