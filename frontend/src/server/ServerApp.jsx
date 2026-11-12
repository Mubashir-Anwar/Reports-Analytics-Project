import { useCallback, useEffect, useMemo, useState } from 'react';
import ContextMenu from '../ContextMenu.jsx';
import { confirmFolderDelete } from '../folderDelete.js';
import { Notice, Topbar, downloadBlob } from '../portal/Shell.jsx';
import ResourcePanel from './ResourcePanel.jsx';
import UploadDialog from './UploadDialog.jsx';
import { roleAtLeast, serverApi, whenSignedOut } from './serverApi.js';

/* ---------------------------------------------------------------- sign in */
function LoginForm({ onSignedIn, message }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(message);

  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    try {
      const user = await serverApi.login(username.trim(), password);
      setPassword('');
      onSignedIn(user);
    } catch (failure) { setError({ text: failure.message, kind: 'error' }); }
    finally { setBusy(false); }
  };

  return (
    <div className="login-wrap">
      <form className="card login" onSubmit={submit} autoComplete="on">
        <span className="brand-mark" aria-hidden="true">M</span>
        <div><p className="eyebrow">Report server</p><h1>Sign in</h1><p className="muted">Sign in to browse the repository and run published reports.</p></div>
        <label className="field"><span>Username</span><input name="username" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required autoFocus /></label>
        <label className="field"><span>Password</span><input name="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
        <Notice message={error} />
        <button className="btn primary" type="submit" disabled={busy} style={{ justifyContent: 'center', padding: '10px' }}>{busy ? 'Signing in…' : 'Sign in'}</button>
        <p className="muted" style={{ fontSize: 12 }}>Accounts are created in <a href="/admin">Admin → Users</a>. First setup: user <code>admin</code> with the admin token as the password, until an admin user exists.</p>
      </form>
    </div>
  );
}

/* ---------------------------------------------------------------- repository */
function Repository({ user, onUser }) {
  const [folders, setFolders] = useState([]);
  const [reports, setReports] = useState([]);
  const [folder, setFolder] = useState('');
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState('');
  const [tab, setTab] = useState('run');
  const [message, setMessage] = useState(null);
  const [loading, setLoading] = useState(true);
  const [menu, setMenu] = useState(null);
  const [clipboard, setClipboard] = useState(null); // { mode: 'copy' | 'cut', id, title }
  const [uploading, setUploading] = useState(false);
  const [version, setVersion] = useState(0); // reloads the resource panel after a change
  const designer = roleAtLeast(user, 'designer');
  const admin = roleAtLeast(user, 'admin');

  const load = useCallback(async () => {
    try {
      const data = await serverApi.repository();
      setFolders(data.folders);
      setReports(data.reports);
      onUser(data.user);
      setFolder((current) => (current && !data.folders.includes(current) ? '' : current));
    } catch (error) { if (error.status !== 401) setMessage({ text: error.message, kind: 'error' }); }
    finally { setLoading(false); }
  }, [onUser]);
  useEffect(() => { load(); }, [load]);

  const inFolder = (r, path) => r.folder === path || r.folder.startsWith(`${path}/`);
  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return reports.filter((r) => (!folder || inFolder(r, folder)) && (!needle || `${r.title} ${r.id} ${r.description}`.toLowerCase().includes(needle)));
  }, [reports, folder, search]);

  const done = async (text, select) => {
    await load();
    setVersion((v) => v + 1);
    if (select !== undefined) setSelectedId(select);
    setMessage({ text, kind: 'ok' });
  };
  const fail = (error) => setMessage({ text: error.message, kind: 'error' });
  const open = (id, nextTab = 'run') => { setSelectedId(id); setTab(nextTab); };

  /* ---------- actions */
  const createFolder = async (parent = folder) => {
    const name = window.prompt(parent ? `New folder inside ${parent}` : 'Name of the new folder');
    if (!name?.trim()) return;
    const path = parent ? `${parent}/${name.trim()}` : name.trim();
    try { await serverApi.createFolder(path); setFolder(path); await done(`Folder ${path} created.`); } catch (error) { fail(error); }
  };
  const deleteFolder = async (path = folder) => {
    try {
      const answer = confirmFolderDelete(path, await serverApi.folderContents(path));
      if (!answer) return;
      if (answer.problem) { fail(new Error(answer.problem)); return; }
      const result = await serverApi.deleteFolder(path, answer.recursive);
      if (folder === path || folder.startsWith(`${path}/`)) setFolder('');
      if (result.reportsDeleted?.includes(selectedId)) setSelectedId('');
      const extra = [result.subfoldersDeleted ? `${result.subfoldersDeleted} subfolder(s)` : '', result.reportsDeleted?.length ? `${result.reportsDeleted.length} report(s)` : ''].filter(Boolean).join(' and ');
      await done(`Folder /${path} deleted${extra ? ` with ${extra}` : ''}.`);
    } catch (error) { fail(error); }
  };
  const remove = async (r) => {
    if (!window.confirm(`Delete “${r.title}” (${r.id}) from the server? The designer's workspace copy is kept and can be published again.`)) return;
    try { await serverApi.deleteReport(r.id); await done(`“${r.title}” was deleted from the server.`, r.id === selectedId ? '' : selectedId); } catch (error) { fail(error); }
  };
  const download = async (r) => {
    try { downloadBlob(await serverApi.download(r.id), `${r.id}.mrd`); setMessage({ text: `Downloaded ${r.id}.mrd — one file with the whole report. Open it in the Designer or upload it to another server.`, kind: 'ok' }); }
    catch (error) { fail(error); }
  };
  const paste = async (target) => {
    if (!clipboard) return;
    const where = target || 'General';
    try {
      if (clipboard.mode === 'cut') {
        await serverApi.move(clipboard.id, where);
        setClipboard(null);
        await done(`Moved “${clipboard.title}” to /${where}.`, clipboard.id);
      } else {
        const result = await serverApi.copy(clipboard.id, where);
        await done(`Copied “${clipboard.title}” to /${where} as ${result.report.id}.`, result.report.id);
      }
    } catch (error) { fail(error); }
  };
  const openInDesigner = (r) => window.open(`/designer?open=${encodeURIComponent(r.id)}`, '_blank', 'noopener');

  const reportMenu = (r) => [
    { header: `${r.title} (${r.id})` },
    { label: 'Run', icon: '▶', onClick: () => open(r.id, 'run') },
    { label: 'Edit', icon: '✎', disabled: !designer, hint: designer ? '' : 'Needs a designer account', onClick: () => open(r.id, 'overview') },
    { label: 'Parameters', icon: '$P', onClick: () => open(r.id, 'parameters') },
    { label: 'Data Source', icon: '⛁', onClick: () => open(r.id, 'datasource') },
    { label: 'Open in Designer', icon: '🎨', disabled: !designer, onClick: () => openInDesigner(r) },
    { label: 'Download (.mrd)', icon: '⤓', disabled: !designer, onClick: () => download(r) },
    '-',
    { label: 'Copy', icon: '⧉', shortcut: 'Ctrl+C', disabled: !designer, onClick: () => { setClipboard({ mode: 'copy', id: r.id, title: r.title }); setMessage({ text: `“${r.title}” copied — right-click a folder → Paste.`, kind: 'ok' }); } },
    { label: 'Cut', icon: '✂', shortcut: 'Ctrl+X', disabled: !designer, onClick: () => { setClipboard({ mode: 'cut', id: r.id, title: r.title }); setMessage({ text: `“${r.title}” cut — right-click a folder → Paste to move it.`, kind: 'ok' }); } },
    { label: 'Paste into this folder', icon: '📋', disabled: !designer || !clipboard, onClick: () => paste(r.folder) },
    '-',
    { label: 'History', icon: '🕘', onClick: () => open(r.id, 'history') },
    { label: 'Properties', icon: '⚙', onClick: () => open(r.id, 'overview') },
    { label: 'Delete', icon: '✕', danger: true, disabled: !admin, hint: admin ? '' : 'Needs an admin account', onClick: () => remove(r) },
  ];
  const folderMenu = (path) => [
    { header: path ? `/${path}` : 'Repository' },
    { label: 'Add Resource: upload .mrd / .jrxml', icon: '⤒', disabled: !designer, onClick: () => { setFolder(path); setUploading(true); } },
    { label: 'New folder', icon: '📁', disabled: !designer, onClick: () => createFolder(path) },
    { label: clipboard ? `Paste “${clipboard.title}”${clipboard.mode === 'cut' ? ' (move)' : ''}` : 'Paste', icon: '📋', disabled: !designer || !clipboard, onClick: () => paste(path) },
    '-',
    { label: 'Delete folder…', icon: '✕', danger: true, disabled: !admin || !path || path === 'General', hint: !admin ? 'Deleting needs an admin account' : path === 'General' ? 'General cannot be deleted' : 'Also deletes its subfolders and reports (after you confirm)', onClick: () => deleteFolder(path) },
  ];
  const showMenu = (items) => (event) => { event.preventDefault(); event.stopPropagation(); setMenu({ x: event.clientX, y: event.clientY, items }); };

  // Ctrl+C / Ctrl+X / Ctrl+V on the selected report (outside text fields)
  useEffect(() => {
    const onKey = (event) => {
      if (!designer || !(event.ctrlKey || event.metaKey) || ['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName)) return;
      const key = event.key.toLowerCase();
      const r = reports.find((x) => x.id === selectedId);
      if ((key === 'c' || key === 'x') && r && !window.getSelection()?.toString()) { setClipboard({ mode: key === 'c' ? 'copy' : 'cut', id: r.id, title: r.title }); setMessage({ text: `“${r.title}” ${key === 'c' ? 'copied' : 'cut'}.`, kind: 'ok' }); }
      if (key === 'v' && clipboard) paste(folder || r?.folder);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  return (
    <main className="page">
      <div className="page-head">
        <div><p className="eyebrow">Repository</p><h1>Published reports</h1><p className="muted">Right-click a report or a folder for its actions. Only published or uploaded reports are on the server.</p></div>
        <div className="row">
          {designer && <button className="btn primary" onClick={() => setUploading(true)}>⤒ Add Resource</button>}
          <button className="btn" onClick={load}>↻ Refresh</button>
        </div>
      </div>
      {user.bootstrap && <Notice message={{ kind: 'warn', text: 'You are signed in with the first-setup admin login. Create a real admin user in Admin → Users; this login then stops working.' }} />}
      {message && <div style={{ marginTop: 10 }}><Notice message={message} /></div>}
      {clipboard && <div className="clip-bar">📋 {clipboard.mode === 'cut' ? 'Cut' : 'Copied'}: <b>{clipboard.title}</b> — right-click a folder → Paste <button className="btn quiet sm" onClick={() => setClipboard(null)}>✕</button></div>}
      <div className="server-grid" style={{ marginTop: 14 }}>
        <aside className="card" onContextMenu={showMenu(folderMenu(''))}>
          <div className="card-head"><div><p className="eyebrow">Folders</p><h2>Repository</h2></div>{designer && <button className="btn sm quiet" onClick={() => createFolder()}>+ Folder</button>}</div>
          <input type="search" placeholder="⌕  Find a report" value={search} onChange={(e) => setSearch(e.target.value)} style={{ marginBottom: 10 }} />
          <ul className="tree">
            <li><button className={folder === '' ? 'on' : ''} onClick={() => setFolder('')} onContextMenu={showMenu(folderMenu(''))}>🗄 All reports<span className="count">{reports.length}</span></button></li>
            {folders.map((path) => (
              <li key={path}><button className={folder === path ? 'on' : ''} style={{ paddingLeft: 8 + path.split('/').length * 14 }} onClick={() => setFolder(path)} onContextMenu={showMenu(folderMenu(path))} title={path}>{folder === path ? '📂' : '📁'} {path.split('/').pop()}<span className="count">{reports.filter((r) => inFolder(r, path)).length}</span></button></li>
            ))}
          </ul>
        </aside>
        <section className="card" onContextMenu={showMenu(folderMenu(folder))}>
          <div className="card-head">
            <div><p className="eyebrow">/ {folder ? folder.split('/').join(' / ') : ''}</p><h2>{folder ? folder.split('/').pop() : 'All reports'}</h2></div>
            <div className="row">
              {designer && clipboard && <button className="btn sm" onClick={() => paste(folder)} title="Paste here">📋 Paste</button>}
              {admin && folder && folder !== 'General' && <button className="btn danger sm" onClick={() => deleteFolder()}>Delete folder</button>}
            </div>
          </div>
          <div className="stack">
            {loading && <div className="empty">Loading…</div>}
            {!loading && !visible.length && <div className="empty">{reports.length ? 'No published reports here.' : 'Nothing is published yet. Open the Designer, save a report and use “Publish to server”, or Add Resource → upload a .mrd file.'}</div>}
            {visible.map((r) => (
              <button
                key={r.id}
                className={`report-card ${r.id === selectedId ? 'on' : ''} ${clipboard?.mode === 'cut' && clipboard.id === r.id ? 'cut' : ''}`}
                onClick={() => open(r.id, tab)}
                onDoubleClick={() => open(r.id, 'run')}
                onContextMenu={(e) => { setSelectedId(r.id); showMenu(reportMenu(r))(e); }}
              >
                <strong>{r.title}</strong>
                <small>{r.folder} · {r.formats.map((f) => f.toUpperCase()).join(', ')}{r.parameters.length ? ` · ${r.parameters.length} question${r.parameters.length > 1 ? 's' : ''}` : ''}</small>
                {r.description && <small>{r.description}</small>}
              </button>
            ))}
          </div>
        </section>
        {selectedId
          ? <ResourcePanel key={`${selectedId}-${version}`} reportId={selectedId} tab={tab} setTab={setTab} onChanged={load}
              menu={(event, info) => setMenu({ x: event.clientX, y: event.clientY, items: reportMenu(reports.find((r) => r.id === info.id) || info) })} />
          : <section className="card" style={{ textAlign: 'center', padding: '48px 18px' }}><div style={{ fontSize: 30, opacity: 0.5 }}>▤</div><h2>Choose a report</h2><p className="muted">Run it, or check its parameters, data source, query and history. Right-click for more.</p></section>}
      </div>
      <ContextMenu menu={menu} onClose={() => setMenu(null)} />
      {uploading && (
        <UploadDialog folders={folders} folder={folder} onClose={() => setUploading(false)}
          onDone={async (result) => {
            setUploading(false);
            setFolder(result.report.folder);
            await done(`${result.replaced ? 'Replaced' : 'Uploaded'} ${result.report.id} in /${result.report.folder}.${result.warnings?.length ? ` Check: ${result.warnings.join('; ')}` : ''}`, result.report.id);
            setTab('overview');
          }} />
      )}
    </main>
  );
}

/* ---------------------------------------------------------------- app */
export default function ServerApp() {
  const [user, setUser] = useState(undefined); // undefined = checking, null = signed out
  const [loginMessage, setLoginMessage] = useState(null);

  useEffect(() => {
    whenSignedOut(() => { setUser(null); setLoginMessage({ text: 'Your session has ended. Please sign in again.', kind: 'error' }); });
    serverApi.me().then(setUser).catch(() => setUser(null));
  }, []);
  const keepUser = useCallback((next) => setUser((current) => (current && next && current.username === next.username && current.role === next.role ? current : next)), []);

  const logout = async () => {
    try { await serverApi.logout(); } catch { /* signed out anyway */ }
    setUser(null);
    setLoginMessage({ text: 'You have signed out.', kind: 'ok' });
  };

  return (
    <>
      <Topbar current="/server" subtitle="Report Server">
        {user && (<><span>Signed in as <b>{user.username}</b> <span className="chip">{user.role}</span></span><button className="btn sm" onClick={logout}>Sign out</button></>)}
      </Topbar>
      {user === undefined && <div className="login-wrap"><p className="muted">Checking your session…</p></div>}
      {user === null && <LoginForm key={loginMessage?.text || 'login'} message={loginMessage} onSignedIn={(u) => { setLoginMessage(null); setUser(u); }} />}
      {user && <Repository user={user} onUser={keepUser} />}
    </>
  );
}
