import { useEffect, useMemo, useState } from 'react';
import { api } from './api.js';
import { selectedIds, toggleInSelection } from './arrange.js';
import { confirmFolderDelete } from './folderDelete.js';
import { BUILTIN_VARIABLES, ELEMENT_LABELS, PALETTE_GROUPS, bandKeys, bandLabel, bandPresent, describeElement, getBand, groupOfBand } from './designModel.js';

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

/** All folders including the parents of every folder, reports' folders and adapters' folders. */
function completeFolders(folders, reports, adapters) {
  const set = new Set();
  const add = (path) => { if (!path) return; const parts = path.split('/'); parts.forEach((_, i) => set.add(parts.slice(0, i + 1).join('/'))); };
  folders.forEach(add);
  reports.forEach((r) => add(r.folder || 'General'));
  adapters.forEach((a) => add(a.folder));
  return [...set].sort((a, b) => a.localeCompare(b));
}

const COLLAPSE_KEY = 'reporting-designer-collapsed';
function readCollapsed() {
  try { return JSON.parse(localStorage.getItem(COLLAPSE_KEY) || '{}'); } catch { return {}; }
}

/* ---------------------------------------------------------------- Server (repository) */
/** Like Jaspersoft Studio's Repository Explorer: Data Adapters on top, then folders inside folders,
    each holding its data adapters and reports (▤). */
export function ServerPanel({ report, onOpenFromServer, notify, refreshKey, onChanged, openMenu }) {
  const [folders, setFolders] = useState([]);
  const [reports, setReports] = useState([]);
  const [adapters, setAdapters] = useState([]);
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [status, setStatus] = useState({});
  const [menu, setMenu] = useState('');

  const load = async () => {
    try {
      const [f, r, c] = await Promise.all([api.folders(), api.reports(), api.connections()]);
      setFolders(f.folders);
      setReports(r.reports);
      setAdapters(c.connections);
    } catch (error) {
      notify(`Could not load the repository: ${error.message}`, 'error');
    }
  };
  useEffect(() => { load(); }, [refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!menu) return undefined;
    const close = () => setMenu('');
    document.addEventListener('click', close);
    return () => document.removeEventListener('click', close);
  }, [menu]);

  const allFolders = useMemo(() => completeFolders(folders, reports, adapters), [folders, reports, adapters]);
  const toggle = (key) => {
    const next = { ...collapsed, [key]: !collapsed[key] };
    setCollapsed(next);
    try { localStorage.setItem(COLLAPSE_KEY, JSON.stringify(next)); } catch { /* storage blocked */ }
  };
  const childrenOf = (parent) => allFolders.filter((path) => {
    const depth = path.split('/').length;
    return parent ? path.startsWith(`${parent}/`) && depth === parent.split('/').length + 1 : depth === 1;
  });

  const createFolder = async (parent) => {
    const name = window.prompt(parent ? `New folder inside ${parent}` : 'Name of the new top-level folder (for example cascade)');
    if (!name) return;
    const path = parent ? `${parent}/${name.trim()}` : name.trim();
    try {
      await api.createFolder(path);
      if (parent && collapsed[parent]) toggle(parent);
      await load(); notify(`Folder ${path} created`, 'ok');
    } catch (error) { notify(error.message, 'error'); }
  };
  const deleteFolder = async (path) => {
    try {
      const answer = confirmFolderDelete(path, await api.folderContents(path));
      if (!answer) return;
      if (answer.problem) { notify(answer.problem, 'error'); return; }
      const result = await api.deleteFolder(path, answer.recursive);
      await load();
      onChanged?.();
      const extra = [result.subfoldersDeleted ? `${result.subfoldersDeleted} subfolder(s)` : '', result.reportsDeleted?.length ? `${result.reportsDeleted.length} report(s)` : ''].filter(Boolean).join(' and ');
      notify(`Folder ${path} deleted${extra ? ` with ${extra}` : ''}`, 'ok');
    } catch (error) { notify(error.status === 403 ? 'Deleting a server folder needs the admin token' : error.message, 'error'); }
  };
  const folderMenu = (path) => (event) => {
    if (!openMenu) return;
    event.preventDefault();
    event.stopPropagation();
    openMenu(event, [
      { header: `/${path}` },
      { label: 'New subfolder', icon: '📁', onClick: () => createFolder(path) },
      { label: 'Refresh', icon: '↻', onClick: () => { load(); onChanged?.(); } },
      '-',
      { label: 'Delete folder…', icon: '✕', danger: true, disabled: path === 'General', hint: path === 'General' ? 'General cannot be deleted' : 'Also deletes its subfolders and reports (after you confirm)', onClick: () => deleteFolder(path) },
    ]);
  };
  const reportMenu = (item) => (event) => {
    if (!openMenu) return;
    event.preventDefault();
    event.stopPropagation();
    openMenu(event, [
      { header: `${item.title} (${item.id})` },
      { label: 'Open in my workspace', icon: '✎', onClick: () => onOpenFromServer(item) },
      { label: 'Open on /server', icon: '🌐', onClick: () => window.open('/server', '_blank', 'noopener') },
      '-',
      { label: 'Remove from the server', icon: '✕', danger: true, onClick: () => removeReport(item) },
    ]);
  };
  const removeReport = async (item) => {
    if (!window.confirm(`Remove '${item.title}' from the server? Your workspace copy is kept and can be published again.`)) return;
    try { await api.deleteReport(item.id); await load(); onChanged?.(); notify('Report removed from the server', 'ok'); } catch (error) { notify(error.status === 403 ? 'Removing a report from the server needs the admin token' : error.message, 'error'); }
  };
  const test = async (id) => {
    setStatus((s) => ({ ...s, [id]: { state: 'busy', text: 'Testing…' } }));
    try { const r = await api.testConnection(id); setStatus((s) => ({ ...s, [id]: { state: 'ok', text: r.message || 'Connected' } })); }
    catch (error) { setStatus((s) => ({ ...s, [id]: { state: 'err', text: error.message } })); }
  };

  const adapterRow = (a, depth) => {
    const s = status[a.id];
    return (
      <li key={`a:${a.id}`} className="tree-row adapter-node" style={{ paddingLeft: 6 + depth * 14 }} title={`${a.name} · ${a.database || 'no database'} — click to test the connection`} onClick={() => test(a.id)}>
        <span className="node-label"><DbLogo type={a.type} /> {a.name}</span>
        <span className={`dot ${s ? s.state : a.configured ? '' : 'err'}`} title={s ? s.text : a.configured ? 'Not tested yet' : 'No MongoDB URL saved'} />
      </li>
    );
  };
  const folderNode = (path, depth) => {
    const subfolders = childrenOf(path);
    const folderAdapters = adapters.filter((a) => (a.folder || '') === path);
    const folderReports = reports.filter((r) => (r.folder || 'General') === path);
    const empty = !subfolders.length && !folderAdapters.length && !folderReports.length;
    const isOpen = !collapsed[path];
    return (
      <li key={path}>
        <div className="tree-row folder" style={{ paddingLeft: 6 + depth * 14 }} onClick={() => toggle(path)} onContextMenu={folderMenu(path)} title={`${path} — right-click for actions`}>
          <span className="node-label"><span className="caret">{empty ? '' : isOpen ? '▾' : '▸'}</span>{isOpen && !empty ? '📂' : '📁'} {path.split('/').pop()}</span>
          <span className="node-actions" onClick={(e) => e.stopPropagation()}>
            <button className="icon" title="New…" onClick={(e) => { e.stopPropagation(); setMenu(menu === path ? '' : path); }}>＋</button>
            {path !== 'General' && <button className="icon" title="Delete folder" onClick={() => deleteFolder(path)}>✕</button>}
          </span>
          {menu === path && (
            <div className="node-menu" onClick={(e) => e.stopPropagation()}>
              <button onClick={() => { setMenu(''); createFolder(path); }}>📁 New subfolder</button>
              <small>Reports get here only with “Publish to server”. Data adapters: add them on the Data Adapters tab.</small>
            </div>
          )}
        </div>
        {isOpen && !empty && (
          <ul className="tree nested flat">
            {subfolders.map((child) => folderNode(child, depth + 1))}
            {folderAdapters.map((a) => adapterRow(a, depth + 1))}
            {folderReports.map((item) => (
              <li key={item.id} className={`tree-row report ${report.id === item.id && !report.isNew ? 'active' : ''}`} style={{ paddingLeft: 6 + (depth + 1) * 14 }} onClick={() => onOpenFromServer(item)} onContextMenu={reportMenu(item)} title={`Published: ${item.title} — /generate/${item.id}.pdf. Click to open it in your workspace.`}>
                <span className="node-label">🌐 {item.title}</span>
                <button className="icon" title="Remove from the server (admin)" onClick={(e) => { e.stopPropagation(); removeReport(item); }}>✕</button>
              </li>
            ))}
          </ul>
        )}
      </li>
    );
  };
  const topAdapters = adapters.filter((a) => !a.folder);

  return (
    <div className="panel-body">
      <div className="panel-actions">
        <button className="btn sm" onClick={() => createFolder('')}>+ Server folder</button>
        <button className="btn sm" onClick={() => { load(); onChanged?.(); }} title="Refresh">↻</button>
      </div>
      <ul className="tree repo">
        <li>
          <div className="tree-row section" onClick={() => toggle('::adapters')}>
            <span className="node-label"><span className="caret">{collapsed['::adapters'] ? '▸' : '▾'}</span>🗄 Data Adapters <small>({topAdapters.length})</small></span>
          </div>
          {!collapsed['::adapters'] && (
            <ul className="tree nested flat">
              {topAdapters.map((a) => adapterRow(a, 1))}
              {!topAdapters.length && <li className="muted pad">None at the top level.</li>}
            </ul>
          )}
        </li>
        <li>
          <div className="tree-row section" onClick={() => toggle('::repo')}>
            <span className="node-label"><span className="caret">{collapsed['::repo'] ? '▸' : '▾'}</span>🖧 Repository</span>
          </div>
          {!collapsed['::repo'] && (
            <ul className="tree nested flat">
              {childrenOf('').map((path) => folderNode(path, 1))}
              {!allFolders.length && <li className="muted pad">No folders yet. Use “+ Folder”.</li>}
            </ul>
          )}
        </li>
      </ul>
      <p className="hint">This is the report server: only published reports are here. Click a 🌐 report to open it in your workspace; publish changes with “Publish to server”. Click a data adapter to test it.</p>
    </div>
  );
}

/* ---------------------------------------------------------------- Workspace (the designer's own saves) */
/** Publish state of a workspace report: draft (only here), published, or changed since it was published. */
export function publishState(item) {
  if (!item) return { icon: '○', label: 'Not saved', cls: 'draft' };
  if (!item.onServer) return { icon: '●', label: 'Draft — not on the server', cls: 'draft' };
  if (item.changedSincePublish) return { icon: '✎', label: 'Changed since it was published', cls: 'changed' };
  return { icon: '✓', label: item.publishedAt ? `Published ${new Date(item.publishedAt).toLocaleString()}` : 'On the server', cls: 'published' };
}

export function WorkspacePanel({ report, onOpen, onNew, notify, refreshKey, onChanged }) {
  const [folders, setFolders] = useState(['General']);
  const [reports, setReports] = useState([]);
  const [collapsed, setCollapsed] = useState(() => readCollapsed().workspace || {});

  const load = async () => {
    try {
      const data = await api.workspace();
      setFolders(data.folders);
      setReports(data.reports);
    } catch (error) { notify(`Could not load the workspace: ${error.message}`, 'error'); }
  };
  useEffect(() => { load(); }, [refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const allFolders = useMemo(() => completeFolders(folders, reports, []), [folders, reports]);
  const toggle = (key) => {
    const next = { ...collapsed, [key]: !collapsed[key] };
    setCollapsed(next);
    try { localStorage.setItem(COLLAPSE_KEY, JSON.stringify({ ...readCollapsed(), workspace: next })); } catch { /* storage blocked */ }
  };
  const childrenOf = (parent) => allFolders.filter((path) => {
    const depth = path.split('/').length;
    return parent ? path.startsWith(`${parent}/`) && depth === parent.split('/').length + 1 : depth === 1;
  });
  const createFolder = async (parent) => {
    const name = window.prompt(parent ? `New workspace folder inside ${parent}` : 'Name of the new workspace folder');
    if (!name) return;
    const path = parent ? `${parent}/${name.trim()}` : name.trim();
    try { await api.createWorkspaceFolder(path); await load(); notify(`Folder ${path} created in your workspace`, 'ok'); } catch (error) { notify(error.message, 'error'); }
  };
  const deleteFolder = async (path) => {
    if (!window.confirm(`Delete the empty workspace folder '${path}'?`)) return;
    try { await api.deleteWorkspaceFolder(path); await load(); notify('Folder deleted', 'ok'); } catch (error) { notify(error.message, 'error'); }
  };
  const removeReport = async (item) => {
    const note = item.onServer ? ' The published copy stays on the server.' : ' It was never published, so this removes it completely.';
    if (!window.confirm(`Delete '${item.title}' from your workspace?${note}`)) return;
    try { await api.deleteWorkspace(item.id); await load(); onChanged?.(); notify('Removed from the workspace', 'ok'); } catch (error) { notify(error.message, 'error'); }
  };

  const folderNode = (path, depth) => {
    const subfolders = childrenOf(path);
    const items = reports.filter((r) => (r.folder || 'General') === path);
    const empty = !subfolders.length && !items.length;
    const isOpen = !collapsed[path];
    return (
      <li key={path}>
        <div className="tree-row folder" style={{ paddingLeft: 6 + depth * 14 }} onClick={() => toggle(path)} title={path}>
          <span className="node-label"><span className="caret">{empty ? '' : isOpen ? '▾' : '▸'}</span>{isOpen && !empty ? '📂' : '📁'} {path.split('/').pop()}</span>
          <span className="node-actions" onClick={(e) => e.stopPropagation()}>
            <button className="icon" title="New report in this folder" onClick={() => onNew(path)}>＋</button>
            <button className="icon" title="New subfolder" onClick={() => createFolder(path)}>📁</button>
            {path !== 'General' && <button className="icon" title="Delete folder" onClick={() => deleteFolder(path)}>✕</button>}
          </span>
        </div>
        {isOpen && !empty && (
          <ul className="tree nested flat">
            {subfolders.map((child) => folderNode(child, depth + 1))}
            {items.map((item) => {
              const s = publishState(item);
              return (
                <li key={item.id} className={`tree-row report ${report.id === item.id && !report.isNew ? 'active' : ''}`} style={{ paddingLeft: 6 + (depth + 1) * 14 }} onClick={() => onOpen(item)} title={`${item.title} (${item.id}) — ${s.label}`}>
                  <span className="node-label"><span className={`pub ${s.cls}`}>{s.icon}</span> {item.title}</span>
                  <button className="icon" title="Delete from workspace" onClick={(e) => { e.stopPropagation(); removeReport(item); }}>✕</button>
                </li>
              );
            })}
          </ul>
        )}
      </li>
    );
  };

  return (
    <div className="panel-body">
      <div className="panel-actions">
        <button className="btn primary sm" onClick={() => onNew()}>+ New report</button>
        <button className="btn sm" onClick={() => createFolder('')}>+ Folder</button>
        <button className="btn sm" onClick={load} title="Refresh">↻</button>
      </div>
      <ul className="tree repo">{childrenOf('').map((path) => folderNode(path, 0))}</ul>
      <p className="hint"><b>My workspace:</b> Save (Ctrl+S) keeps reports here only. <span className="pub draft">●</span> draft · <span className="pub published">✓</span> published · <span className="pub changed">✎</span> changed since publish. Use “Publish to server” to put a report on the server.</p>
    </div>
  );
}

/* ---------------------------------------------------------------- Data adapters */
/** Database logos for the data adapters (simple drawings in each database's colours). */
const DB_STYLE = {
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

const ADAPTER_TYPES = [
  { id: 'mongodb', name: 'MongoDB Connection', text: 'A MongoDB server (mongodb:// or mongodb+srv:// URL). Reports run their aggregation query on it.' },
  { id: 'postgresql', name: 'PostgreSQL', text: 'SQL SELECT queries on PostgreSQL.', soon: true },
  { id: 'mysql', name: 'MySQL / MariaDB', text: 'SQL SELECT queries on MySQL or MariaDB.', soon: true },
  { id: 'sqlserver', name: 'Microsoft SQL Server', text: 'SQL SELECT queries on SQL Server.', soon: true },
  { id: 'oracle', name: 'Oracle', text: 'SQL SELECT queries on Oracle Database.', soon: true },
];

const adapterId = (name) => name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 39);

/** Like Jaspersoft Studio's "Data Adapter Wizard": 1) pick the type, 2) fill in the connection, Test, Finish. */
function AdapterWizard({ initial, folders, existingIds, onClose, onSaved, notify }) {
  const editing = Boolean(initial?.id);
  const [step, setStep] = useState(initial?.id || initial?.name ? 2 : 1); // edit / duplicate skip the type page
  const [form, setForm] = useState({ name: '', uri: '', database: '', folder: '', ...initial, id: initial?.id || '' });
  const [result, setResult] = useState(null); // { ok, text }
  const [busy, setBusy] = useState('');
  const id = editing ? form.id : adapterId(form.name);
  const duplicateId = !editing && id && existingIds.includes(id);

  const test = async () => {
    setBusy('test'); setResult(null);
    try { const r = await api.testConnectionValues({ id: id || 'test', uri: form.uri.trim(), database: form.database.trim() }); setResult({ ok: true, text: `Successful: ${r.message || 'connected'}` }); }
    catch (error) { setResult({ ok: false, text: error.message }); }
    finally { setBusy(''); }
  };
  const finish = async () => {
    setBusy('save');
    try {
      await api.saveConnection({ id, name: form.name.trim(), uri: form.uri.trim(), database: form.database.trim(), folder: form.folder });
      notify(`Data adapter “${form.name.trim()}” ${editing ? 'updated' : 'created'}`, 'ok');
      onSaved();
    } catch (error) { setResult({ ok: false, text: error.status === 403 ? 'Saving a data adapter needs the admin token' : error.message }); setBusy(''); }
  };

  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <div className="modal wizard" role="dialog" aria-label="Data Adapter Wizard">
        <div className="wizard-head">
          <h3>{editing ? `Edit Data Adapter — ${initial.name}` : 'Data Adapter Wizard'}</h3>
          <p className="muted">{step === 1 ? 'Create a new data adapter: choose its type.' : 'MongoDB Connection: enter the connection details, test them, then Finish.'}</p>
        </div>
        {step === 1 && (
          <ul className="wizard-types">
            {ADAPTER_TYPES.map((t) => (
              <li key={t.id} className={t.soon ? 'soon' : 'on'} aria-disabled={t.soon || undefined} title={t.soon ? 'Planned: SQL data adapters are the next phase (see ARCHITECTURE.md)' : ''} onDoubleClick={() => !t.soon && setStep(2)}>
                <span className="t-icon"><DbLogo type={t.id} size={26} /></span><span><b>{t.name}{t.soon ? <em className="soon-tag">coming next</em> : null}</b><small>{t.text}</small></span>
              </li>
            ))}
          </ul>
        )}
        {step === 2 && (
          <div className="wizard-form">
            <label className="modal-field">Name<input autoFocus value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="KMBL UAT MongoDB" /></label>
            {!editing && id && <small className={duplicateId ? 'err' : 'muted'}>ID: {id}{duplicateId ? ' — already used, choose another name' : ''}</small>}
            <label className="modal-field">MongoDB URL
              <input type="password" autoComplete="new-password" value={form.uri} onChange={(e) => { setForm({ ...form, uri: e.target.value }); setResult(null); }} placeholder="mongodb://user:password@host:27017/Database" />
              <small className="muted">{editing ? 'The saved URL is never shown. Enter it again (it replaces the old one).' : 'Saved with its password in a private file on the server; never shown again. For a remote MongoDB connect the VPN first.'}</small>
            </label>
            <label className="modal-field">Database <small>(optional when the URL has one)</small><input value={form.database} onChange={(e) => setForm({ ...form, database: e.target.value })} /></label>
            <label className="modal-field">Folder
              <select value={form.folder || ''} onChange={(e) => setForm({ ...form, folder: e.target.value })}>
                <option value="">Data Adapters (top level)</option>
                {folders.map((f) => <option key={f} value={f}>{f.split('/').join(' / ')}</option>)}
              </select>
            </label>
            {result && <p className={result.ok ? 'ok' : 'err'}>{result.ok ? '✓ ' : '✕ '}{result.text}</p>}
          </div>
        )}
        <div className="row wizard-buttons">
          {step === 2 && <button className="btn" style={{ marginRight: 'auto' }} disabled={Boolean(busy) || !form.uri.trim()} onClick={test}>{busy === 'test' ? 'Testing…' : 'Test'}</button>}
          {step === 2 && !editing && <button className="btn" disabled={Boolean(busy)} onClick={() => setStep(1)}>‹ Back</button>}
          {step === 1 && <button className="btn" onClick={() => setStep(2)}>Next ›</button>}
          {step === 2 && <button className="btn primary" disabled={Boolean(busy) || !form.name.trim() || !form.uri.trim() || duplicateId} onClick={finish}>{busy === 'save' ? 'Saving…' : 'Finish'}</button>}
          <button className="btn" disabled={Boolean(busy)} onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

/** Like Jaspersoft Studio's Repository Explorer → Data Adapters: a tree with right-click Create / Edit / Duplicate / Test / Delete. */
export function AdaptersPanel({ notify, refreshKey, onChanged, openMenu }) {
  const [list, setList] = useState([]);
  const [folders, setFolders] = useState([]);
  const [status, setStatus] = useState({});
  const [wizard, setWizard] = useState(null); // null | {} (new) | adapter (edit) | { ...copy }
  const [selected, setSelected] = useState('');
  const [collapsed, setCollapsed] = useState(() => readCollapsed().adapters || {});

  const load = async () => {
    try {
      const [c, f] = await Promise.all([api.connections(), api.folders()]);
      setList(c.connections);
      setFolders(completeFolders(f.folders, [], c.connections));
    } catch (error) { notify(`Could not load data adapters: ${error.message}`, 'error'); }
  };
  useEffect(() => { load(); }, [refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const toggle = (key) => {
    const next = { ...collapsed, [key]: !collapsed[key] };
    setCollapsed(next);
    try { localStorage.setItem(COLLAPSE_KEY, JSON.stringify({ ...readCollapsed(), adapters: next })); } catch { /* storage blocked */ }
  };

  const test = async (item) => {
    setStatus((s) => ({ ...s, [item.id]: { state: 'busy', text: 'Testing…' } }));
    try { const r = await api.testConnection(item.id); setStatus((s) => ({ ...s, [item.id]: { state: 'ok', text: r.message || 'Connected' } })); notify(`${item.name}: connection works`, 'ok'); }
    catch (error) { setStatus((s) => ({ ...s, [item.id]: { state: 'err', text: error.message } })); notify(`${item.name}: ${error.message}`, 'error'); }
  };
  const move = async (item, folder) => {
    try { await api.moveConnection(item.id, folder); await load(); onChanged?.(); notify(`${item.name} moved to ${folder || 'Data Adapters'}`, 'ok'); }
    catch (error) { notify(error.status === 403 ? 'Moving a data adapter needs the admin token' : error.message, 'error'); }
  };
  const remove = async (item) => {
    if (!window.confirm(`Delete the data adapter “${item.name}”? Reports that use it can then only run on another adapter.`)) return;
    try { await api.deleteConnection(item.id); await load(); onChanged?.(); notify(`Data adapter ${item.name} deleted`, 'ok'); }
    catch (error) { notify(error.status === 403 ? 'Deleting a data adapter needs the admin token' : error.message, 'error'); }
  };

  const adapterMenu = (item) => [
    { header: `🍃 ${item.name}` },
    { label: 'Edit Data Adapter…', icon: '✎', onClick: () => setWizard({ id: item.id, name: item.name, database: item.database || '', folder: item.folder || '', uri: '' }) },
    { label: 'Duplicate…', icon: '⧉', onClick: () => setWizard({ name: `Copy of ${item.name}`, database: item.database || '', folder: item.folder || '', uri: '' }) },
    { label: 'Test connection', icon: '⚡', onClick: () => test(item) },
    { label: 'Move to folder', icon: '📁', items: [{ label: 'Data Adapters (top level)', disabled: !item.folder, onClick: () => move(item, '') }, ...folders.map((f) => ({ label: f.split('/').join(' / '), disabled: f === item.folder, onClick: () => move(item, f) }))] },
    '-',
    { label: 'Create Data Adapter…', icon: '＋', onClick: () => setWizard({ folder: item.folder || '' }) },
    { label: 'Delete', icon: '✕', danger: true, disabled: item.id === 'default', hint: item.id === 'default' ? 'The default adapter comes from the service settings' : '', onClick: () => remove(item) },
  ];
  const rootMenu = (folder = '') => [{ label: 'Create Data Adapter…', icon: '＋', onClick: () => setWizard(folder ? { folder } : {}) }, { label: 'Refresh', icon: '↻', onClick: load }];
  const menu = (items) => (event) => { event.preventDefault(); event.stopPropagation(); openMenu(event, items()); };

  const row = (item, depth) => {
    const s = status[item.id];
    return (
      <li
        key={item.id}
        className={`tree-row adapter-node ${selected === item.id ? 'active' : ''}`}
        style={{ paddingLeft: 6 + depth * 14 }}
        onClick={() => setSelected(item.id)}
        onDoubleClick={() => setWizard({ id: item.id, name: item.name, database: item.database || '', folder: item.folder || '', uri: '' })}
        onContextMenu={(e) => { setSelected(item.id); menu(() => adapterMenu(item))(e); }}
        title={`${item.name} · ${item.database || 'database from the URL'} · ${item.configured ? 'configured' : 'no URL saved'}${s ? ` · ${s.text}` : ''} — double-click to edit, right-click for more`}
      >
        <span className="node-label"><span className="caret" /><DbLogo type={item.type} /> {item.name} <small>{item.database || ''}</small></span>
        <span className={`dot ${s ? s.state : item.configured ? '' : 'err'}`} />
      </li>
    );
  };
  const groups = folders.filter((f) => list.some((a) => a.folder === f));
  const top = list.filter((a) => !a.folder).sort((a, b) => a.name.localeCompare(b.name));
  const chosen = list.find((a) => a.id === selected);

  return (
    <div className="panel-body">
      <div className="panel-actions">
        <button className="btn primary sm" onClick={() => setWizard({})}>＋ Create Data Adapter</button>
        <button className="btn sm" onClick={load} title="Refresh">↻</button>
      </div>
      <ul className="tree repo">
        <li>
          <div className="tree-row section" onClick={() => toggle('::root')} onContextMenu={menu(() => rootMenu())}>
            <span className="node-label"><span className="caret">{collapsed['::root'] ? '▸' : '▾'}</span>🗄 Data Adapters <small>({list.length})</small></span>
          </div>
          {!collapsed['::root'] && (
            <ul className="tree nested flat">
              {top.map((item) => row(item, 1))}
              {groups.map((folder) => (
                <li key={folder}>
                  <div className="tree-row folder" style={{ paddingLeft: 20 }} onClick={() => toggle(folder)} onContextMenu={menu(() => rootMenu(folder))} title={folder}>
                    <span className="node-label"><span className="caret">{collapsed[folder] ? '▸' : '▾'}</span>{collapsed[folder] ? '📁' : '📂'} {folder.split('/').join(' / ')}</span>
                  </div>
                  {!collapsed[folder] && <ul className="tree nested flat">{list.filter((a) => a.folder === folder).sort((a, b) => a.name.localeCompare(b.name)).map((item) => row(item, 2))}</ul>}
                </li>
              ))}
              {!list.length && <li className="muted pad">No data adapters yet. Right-click → Create Data Adapter.</li>}
            </ul>
          )}
        </li>
      </ul>
      {chosen && (
        <div className="adapter-detail">
          <b><DbLogo type={chosen.type} /> {chosen.name}</b>
          <small>ID {chosen.id} · {chosen.database || 'database from the URL'} · {chosen.configured ? 'URL saved' : 'no URL saved'}</small>
          {status[chosen.id] && <small className={`adapter-status ${status[chosen.id].state}`}>{status[chosen.id].text}</small>}
          <div className="row"><button className="btn sm" onClick={() => test(chosen)}>Test</button><button className="btn sm" onClick={() => setWizard({ id: chosen.id, name: chosen.name, database: chosen.database || '', folder: chosen.folder || '', uri: '' })}>Edit…</button></div>
        </div>
      )}
      <p className="hint">Right-click an adapter: Edit, Duplicate, Test, Move, Delete. Choose the adapter for a run on the Preview tab. Saving, moving and deleting adapters needs the admin token.</p>
      {wizard && (
        <AdapterWizard
          initial={wizard}
          folders={folders}
          existingIds={list.map((a) => a.id)}
          notify={notify}
          onClose={() => setWizard(null)}
          onSaved={() => { setWizard(null); load(); onChanged?.(); }}
        />
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- Outline */
const ELEMENT_ICONS = { staticText: 'T', textField: '𝑓', line: '─', rectangle: '▭', image: '🖼', chart: '▦', ellipse: '◯', frame: '⬚', break: '⤓', barcode: '▥', table: '▤', crosstab: '⊞', list: '☰', subreport: '⧉', note: '🗒' };
const OUTLINE_KEY = 'reporting-designer-outline';
function readOutline() {
  try { return JSON.parse(localStorage.getItem(OUTLINE_KEY) || '{}'); } catch { return {}; }
}

/** Inline rename box (Enter = save, Esc = cancel), like renaming a node in Jaspersoft Studio's Outline. */
function RenameBox({ value, onDone }) {
  const [text, setText] = useState(value);
  const [error, setError] = useState('');
  const finish = (save) => {
    const problem = save ? onDone(text) : onDone(null);
    if (problem) setError(problem);
  };
  return (
    <span className="rename-box" onClick={(e) => e.stopPropagation()}>
      <input
        autoFocus
        value={text}
        onChange={(e) => { setText(e.target.value); setError(''); }}
        onFocus={(e) => e.target.select()}
        onKeyDown={(e) => { e.stopPropagation(); if (e.key === 'Enter') finish(true); if (e.key === 'Escape') finish(false); }}
        onBlur={() => finish(true)}
      />
      {error && <small className="err">{error}</small>}
    </span>
  );
}

/** A folder-like Outline node (Parameters, Fields, a band…) with a caret. */
function OutlineBranch({ id, closed, toggle, icon, title, count, active, muted, onSelect, onMenu, onDoubleClick, children, empty, titleText }) {
  const open = !closed[id];
  return (
    <li>
      <div
        className={`tree-row outline-node ${active ? 'active' : ''} ${muted ? 'absent' : ''}`}
        onClick={onSelect || (() => toggle(id))}
        onContextMenu={onMenu}
        onDoubleClick={onDoubleClick}
        title={titleText}
      >
        <span className="node-label">
          <span className="caret" onClick={(e) => { e.stopPropagation(); if (!empty) toggle(id); }}>{empty ? '' : open ? '▾' : '▸'}</span>
          <span className="node-icon">{icon}</span>{title}{count !== undefined && <small> ({count})</small>}
        </span>
      </div>
      {open && !empty && <ul className="tree nested outline-children">{children}</ul>}
    </li>
  );
}

function OutlineLeaf({ icon, children, active, onSelect, onMenu, draggable: drag, onDragStart, titleText, muted, onDoubleClick }) {
  return (
    <li
      className={`tree-row outline-leaf ${active ? 'active' : ''} ${drag ? 'draggable' : ''} ${muted ? 'builtin' : ''}`}
      onClick={onSelect}
      onContextMenu={onMenu}
      onDoubleClick={onDoubleClick}
      draggable={drag}
      onDragStart={onDragStart}
      title={titleText}
    >
      <span className="node-label"><span className="caret" /><span className="node-icon">{icon}</span>{children}</span>
    </li>
  );
}

/**
 * Like Jaspersoft Studio's Outline: the report with its Parameters, Fields, Variables and then every band in print
 * order (Title … Background). Deleted (absent) bands stay listed in grey; right-click → Create Band brings them back.
 * Every node has a right-click menu (create / delete / rename / properties); Del deletes, F2 renames.
 */
export function Outline({ report, design, selection, dispatch, actions, openMenu, onDetectFields, onEditExpression }) {
  const [closed, setClosed] = useState(readOutline);
  const [renaming, setRenaming] = useState(null); // { kind, name }
  const select = (s) => dispatch({ type: 'SELECT', selection: s });
  const is = (kind, key, value) => selection.kind === kind && selection[key] === value;
  const toggle = (key) => {
    const next = { ...closed, [key]: !closed[key] };
    setClosed(next);
    try { localStorage.setItem(OUTLINE_KEY, JSON.stringify(next)); } catch { /* storage blocked */ }
  };
  const menu = (items, selectFirst) => (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (selectFirst) select(selectFirst);
    openMenu(event, items());
  };
  const startRename = (kind, name) => () => setRenaming({ kind, name });
  const renameDone = (text) => {
    if (text === null) { setRenaming(null); return ''; }
    const problem = actions.rename(renaming.kind, renaming.name, text);
    if (!problem) setRenaming(null);
    return problem;
  };
  const label = (kind, name, content) => (renaming && renaming.kind === kind && renaming.name === name
    ? <RenameBox value={name} onDone={renameDone} />
    : content);

  const onKeyDown = (event) => {
    if (renaming || ['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName)) return;
    if (event.key === 'F2' && ['param', 'variable', 'calc', 'group'].includes(selection.kind)) {
      event.preventDefault();
      setRenaming({ kind: selection.kind, name: selection.name });
    }
  };

  const groupsByName = Object.fromEntries(design.groups.map((g) => [g.name, g]));

  return (
    <div className="panel-body outline" tabIndex={-1} onKeyDown={onKeyDown}>
      <ul className="tree">
        <li>
          <div className={`tree-row outline-node root ${selection.kind === 'report' ? 'active' : ''}`} onClick={() => select({ kind: 'report' })} onContextMenu={menu(actions.reportMenu, { kind: 'report' })} title="Right-click: create parameters, variables, groups and bands">
            <span className="node-label"><span className="node-icon">▤</span><b>{report.title || 'Untitled'}</b></span>
          </div>
          <ul className="tree nested outline-children">
            <OutlineBranch closed={closed} toggle={toggle} id="params" icon="🅿" title="Parameters" count={report.parameters.length} empty={!report.parameters.length} onMenu={menu(actions.parametersMenu)} titleText="Right-click → Create Parameter">
              {report.parameters.map((p) => (
                <OutlineLeaf key={p.name} icon="$P" active={is('param', 'name', p.name)} onSelect={() => select({ kind: 'param', name: p.name })} onMenu={menu(() => actions.parameterMenu(p.name, startRename('param', p.name)), { kind: 'param', name: p.name })} onDoubleClick={startRename('param', p.name)} titleText={`${p.label || p.name} · ${p.type}`}>
                  {label('param', p.name, <>{p.name} <small>{p.type}{p.prompt === false ? ' · computed' : ''}</small></>)}
                </OutlineLeaf>
              ))}
            </OutlineBranch>
            <OutlineBranch closed={closed} toggle={toggle} id="fields" icon="🅵" title="Fields" count={report.columns.length + design.calculatedFields.length} empty={!report.columns.length && !design.calculatedFields.length} onMenu={menu(() => [...actions.fieldsMenu(onDetectFields), ...actions.calcsMenu()])} titleText="Fields come from the query (Setup tab). Right-click → Create Calculated Field">
              {report.columns.map((c) => (
                <OutlineLeaf key={c} icon="$F" draggable onDragStart={(e) => e.dataTransfer.setData('application/x-field', c)} onMenu={menu(() => actions.fieldMenu(c))} onDoubleClick={() => actions.fieldMenu(c)[1].onClick()} titleText="Drag onto a band · double-click = place in Detail with a label · right-click for more">
                  {c}
                </OutlineLeaf>
              ))}
              {design.calculatedFields.map((c) => (
                <OutlineLeaf key={`calc:${c.name}`} icon="ƒ" draggable onDragStart={(e) => e.dataTransfer.setData('application/x-field', c.name)} active={is('calc', 'name', c.name)} onSelect={() => select({ kind: 'calc', name: c.name })} onMenu={menu(() => actions.calcMenu(c.name, startRename('calc', c.name)), { kind: 'calc', name: c.name })} onDoubleClick={startRename('calc', c.name)} titleText={`Calculated field: ${c.expression || '(no expression yet)'}`}>
                  {label('calc', c.name, <>{c.name} <small>calculated</small></>)}
                </OutlineLeaf>
              ))}
            </OutlineBranch>
            <OutlineBranch closed={closed} toggle={toggle} id="variables" icon="🆅" title="Variables" count={design.variables.length + BUILTIN_VARIABLES.length} onMenu={menu(actions.variablesMenu)} titleText="Right-click → Create Variable">
              {BUILTIN_VARIABLES.map((v) => (
                <OutlineLeaf key={v} icon="$V" muted onMenu={menu(() => actions.builtinMenu(v))} titleText="Built-in variable (right-click to place it)">{v}</OutlineLeaf>
              ))}
              {design.variables.map((v) => (
                <OutlineLeaf key={v.name} icon="$V" active={is('variable', 'name', v.name)} onSelect={() => select({ kind: 'variable', name: v.name })} onMenu={menu(() => actions.variableMenu(v.name, startRename('variable', v.name)), { kind: 'variable', name: v.name })} onDoubleClick={startRename('variable', v.name)} titleText={`${v.calculation} of ${v.field || '—'} · reset ${v.resetType === 'Group' ? `on group ${v.resetGroup}` : 'per report'}`}>
                  {label('variable', v.name, <>{v.name} <small>{v.calculation}</small></>)}
                </OutlineLeaf>
              ))}
            </OutlineBranch>
            {bandKeys(design).map((key) => {
              const band = getBand(design, key);
              const present = bandPresent(band);
              const groupName = groupOfBand(key);
              const group = groupName ? groupsByName[groupName] : null;
              const title = group && renaming?.kind === 'group' && renaming.name === groupName && key.startsWith('groupHeader:')
                ? <RenameBox value={groupName} onDone={renameDone} />
                : <>{bandLabel(key)}{group ? <small> by {group.field}</small> : null}</>;
              return (
                <OutlineBranch closed={closed} toggle={toggle}
                  key={key}
                  id={`band:${key}`}
                  icon={present ? '▭' : '▯'}
                  title={title}
                  muted={!present}
                  empty={!band.elements.length}
                  active={is('band', 'key', key)}
                  onSelect={() => select({ kind: 'band', key })}
                  onMenu={menu(() => [
                    ...actions.bandMenu(key),
                    ...(groupName ? [{ label: `Rename group ${groupName}`, icon: '✎', onClick: startRename('group', groupName) }] : []),
                  ], { kind: 'band', key })}
                  onDoubleClick={() => { if (!present) actions.createBandAt(key); }}
                  titleText={present ? `${bandLabel(key)} · ${band.height}px · ${band.elements.length} element(s) — right-click for Add element / Delete Band` : `${bandLabel(key)} is not in the report — double-click or right-click → Create Band`}
                >
                  {band.elements.map((el) => (
                    <OutlineLeaf key={el.id} icon={el.locked ? '🔒' : ELEMENT_ICONS[el.type]} active={selectedIds(selection).includes(el.id)} onSelect={(e) => select(e?.shiftKey || e?.ctrlKey || e?.metaKey ? toggleInSelection(selection, el.id) : { kind: 'element', id: el.id })} onMenu={menu(() => actions.elementMenu(el.id), selectedIds(selection).includes(el.id) ? null : { kind: 'element', id: el.id })} onDoubleClick={['staticText', 'textField'].includes(el.type) ? () => onEditExpression(el.id) : undefined} titleText={`${ELEMENT_LABELS[el.type]} · ${Math.round(el.x)},${Math.round(el.y)} · ${Math.round(el.width)}×${Math.round(el.height)}`}>
                      {describeElement(el).slice(0, 34)}
                    </OutlineLeaf>
                  ))}
                </OutlineBranch>
              );
            })}
          </ul>
        </li>
      </ul>
      <p className="hint">Right-click any node: Create / Delete Band, Add element, Create Parameter / Variable / Group, Rename (F2), Delete (Del). Grey bands are not in the report.</p>
    </div>
  );
}

/* ---------------------------------------------------------------- Palette */
const PALETTE_HELP = {
  textField: 'Shows data: $F{field}, $P{parameter}, $V{variable} or an expression',
  staticText: 'Fixed text such as a label or heading',
  image: 'A PNG/JPEG picture (logo, scanned form, stamp)',
  break: 'Page break: what is below it in the band starts on a new page',
  rectangle: 'A box with fill and border',
  ellipse: 'A circle or ellipse with fill and border',
  line: 'A horizontal, vertical or diagonal line',
  frame: 'A bordered container: elements inside it move with it',
  barcode: 'QR code, Code 128, Code 39 or EAN-13 of a value',
  chart: 'Bar, line or pie chart of a field',
  pageNumber: 'The current page number',
  totalPages: 'The total number of pages',
  pageXofY: '“Page 3 of 10”',
  currentDate: 'Today’s date',
  currentTime: 'The time the report was run',
  percentage: 'A field as a percentage of its report total',
};
const PALETTE_KEY = 'reporting-designer-palette';

/** Like Jaspersoft Studio's Palette: Basic and Composite Elements, collapsible, with a search box. */
export function Palette({ tool, dispatch }) {
  const [closed, setClosed] = useState(() => { try { return JSON.parse(localStorage.getItem(PALETTE_KEY) || '{}'); } catch { return {}; } });
  const [filter, setFilter] = useState('');
  const toggle = (title) => {
    const next = { ...closed, [title]: !closed[title] };
    setClosed(next);
    try { localStorage.setItem(PALETTE_KEY, JSON.stringify(next)); } catch { /* storage blocked */ }
  };
  const needle = filter.trim().toLowerCase();
  return (
    <div className="panel-body palette-panel">
      <input className="palette-search" type="search" placeholder="Find an element…" value={filter} onChange={(e) => setFilter(e.target.value)} />
      {PALETTE_GROUPS.map((group) => {
        const items = group.items.filter(([, , label]) => !needle || label.toLowerCase().includes(needle));
        if (!items.length) return null;
        const open = needle || !closed[group.title];
        return (
          <div key={group.title} className="palette-group">
            <button className="palette-head" onClick={() => toggle(group.title)}>{open ? '▾' : '▸'} {group.title}</button>
            {open && (
              <div className="palette">
                {items.map(([type, icon, label]) => (
                  <button
                    key={type}
                    className={`palette-item ${tool === type ? 'armed' : ''}`}
                    draggable
                    title={`${PALETTE_HELP[type] || label} — click, then click a band; or drag onto a band`}
                    onDragStart={(e) => e.dataTransfer.setData('application/x-palette', type)}
                    onClick={() => dispatch({ type: 'TOOL', tool: tool === type ? null : type })}
                  >
                    <span className="p-icon">{icon}</span>{label}
                  </button>
                ))}
              </div>
            )}
          </div>
        );
      })}
      <p className="hint">Click an element and then a band, or drag it onto a band. Esc cancels.</p>
    </div>
  );
}
