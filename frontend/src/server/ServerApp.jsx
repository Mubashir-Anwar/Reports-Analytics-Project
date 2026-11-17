import { useCallback, useEffect, useMemo, useState } from 'react';
import ContextMenu from '../ContextMenu.jsx';
import { confirmFolderDelete } from '../folderDelete.js';
import { Notice, Topbar, downloadBlob } from '../portal/Shell.jsx';
import { BRAND } from '../shared/brand.js';
import { Logo } from '../shared/NavBar.jsx';
import { Breadcrumbs, FolderTree, NewFolderDialog, SubfolderTiles } from '../shared/folders.jsx';
import PermissionsDialog from './PermissionsDialog.jsx';
import ResourcePanel from './ResourcePanel.jsx';
import { RunsList, SchedulesList } from './Schedules.jsx';
import UploadDialog from './UploadDialog.jsx';
import { safeNext } from '../shared/session.js';
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
        <Logo size={52} className="login-logo" />
        <div><p className="eyebrow">{BRAND.name}</p><h1>Sign in</h1><p className="muted">One sign-in for the Designer, Report Server, Analytics, Run reports and Admin. You see the parts your account has rights for.</p></div>
        <label className="field"><span>Username</span><input name="username" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required autoFocus /></label>
        <label className="field"><span>Password</span><input name="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
        <Notice message={error} />
        <button className="btn primary" type="submit" disabled={busy} style={{ justifyContent: 'center', padding: '10px' }}>{busy ? 'Signing in…' : 'Sign in'}</button>
        <p className="muted" style={{ fontSize: 12 }}>Accounts and their rights are set in Admin → Users &amp; rights. First setup: user <code>admin</code> with the admin token as the password, until a super admin exists.</p>
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
  const [view, setView] = useState('repository'); // repository | schedules | runs
  const [folderAccess, setFolderAccess] = useState({}); // folder → 'none' | 'run' | 'manage' for this user
  const [permFolder, setPermFolder] = useState(null); // folder whose Permissions dialog is open ('' = root)
  const [scheduleInfo, setScheduleInfo] = useState(null);
  const [runsVersion, setRunsVersion] = useState(0);
  const [newFolder, setNewFolder] = useState(null); // parent path while the New folder dialog is open
  useEffect(() => { serverApi.scheduleInfo().then(setScheduleInfo).catch(() => setScheduleInfo(null)); }, []);
  const showMessage = useCallback((m) => setMessage(m), []);
  const designer = roleAtLeast(user, 'designer');
  const admin = roleAtLeast(user, 'admin');
  // folder permissions: what this user may do with a report / in a folder (the server checks it again)
  const canManage = (r) => designer && (!r?.access || r.access === 'manage');
  const canManageFolder = (path) => admin || (designer && (!path ? true : (folderAccess[path] || 'manage') === 'manage'));

  const load = useCallback(async () => {
    try {
      const data = await serverApi.repository();
      setFolders(data.folders);
      setReports(data.reports);
      setFolderAccess(data.folderAccess || {});
      onUser(data.user);
      setFolder((current) => (current && !data.folders.includes(current) ? '' : current));
    } catch (error) { if (error.status !== 401) setMessage({ text: error.message, kind: 'error' }); }
    finally { setLoading(false); }
  }, [onUser]);
  useEffect(() => { load(); }, [load]);

  const inFolder = (r, path) => r.folder === path || r.folder.startsWith(`${path}/`);
  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    // a folder shows its own reports (subfolders are tiles); searching looks inside the subfolders too
    return reports.filter((r) => (!folder || (needle ? inFolder(r, folder) : r.folder === folder)) && (!needle || `${r.title} ${r.id} ${r.description}`.toLowerCase().includes(needle)));
  }, [reports, folder, search]);

  const done = async (text, select) => {
    await load();
    setVersion((v) => v + 1);
    if (select !== undefined) setSelectedId(select);
    setMessage({ text, kind: 'ok' });
  };
  const fail = (error) => setMessage({ text: error.message, kind: 'error' });
  const open = (id, nextTab = 'run') => {
    setSelectedId(id);
    setTab(nextTab);
    if (window.matchMedia?.('(max-width: 760px)').matches) window.requestAnimationFrame(() => document.querySelector('.server-grid')?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  };

  /* ---------- actions */
  const createFolder = (parent = folder) => setNewFolder(parent);
  const saveNewFolder = async (path) => { await serverApi.createFolder(path); setFolder(path); await done(`Folder /${path} created.`); };
  const countIn = (path) => reports.filter((r) => inFolder(r, path)).length;
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
    { label: 'Edit', icon: '✎', disabled: !canManage(r), hint: canManage(r) ? '' : designer ? 'Needs the Manage permission on this folder' : 'Needs a designer account', onClick: () => open(r.id, 'overview') },
    { label: 'Schedule…', icon: '⏰', onClick: () => { setView('repository'); open(r.id, 'schedules'); } },
    { label: 'Parameters', icon: '$P', onClick: () => open(r.id, 'parameters') },
    { label: 'Data Source', icon: '⛁', onClick: () => open(r.id, 'datasource') },
    { label: 'Open in Designer', icon: '🎨', disabled: !canManage(r), onClick: () => openInDesigner(r) },
    { label: 'Download (.mrd)', icon: '⤓', disabled: !canManage(r), onClick: () => download(r) },
    '-',
    { label: 'Copy', icon: '⧉', shortcut: 'Ctrl+C', disabled: !designer, onClick: () => { setClipboard({ mode: 'copy', id: r.id, title: r.title }); setMessage({ text: `“${r.title}” copied — right-click a folder → Paste.`, kind: 'ok' }); } },
    { label: 'Cut', icon: '✂', shortcut: 'Ctrl+X', disabled: !canManage(r), onClick: () => { setClipboard({ mode: 'cut', id: r.id, title: r.title }); setMessage({ text: `“${r.title}” cut — right-click a folder → Paste to move it.`, kind: 'ok' }); } },
    { label: 'Paste into this folder', icon: '📋', disabled: !canManageFolder(r.folder) || !clipboard, onClick: () => paste(r.folder) },
    '-',
    { label: 'History', icon: '🕘', onClick: () => open(r.id, 'history') },
    { label: 'Properties', icon: '⚙', onClick: () => open(r.id, 'overview') },
    { label: 'Delete', icon: '✕', danger: true, disabled: !admin, hint: admin ? '' : 'Needs an admin account', onClick: () => remove(r) },
  ];
  const folderMenu = (path) => [
    { header: path ? `/${path}` : 'Repository' },
    { label: 'Add Resource: upload .mrd / .jrxml', icon: '⤒', disabled: !canManageFolder(path), onClick: () => { setFolder(path); setUploading(true); } },
    { label: 'New folder', icon: '📁', disabled: !canManageFolder(path), onClick: () => createFolder(path) },
    { label: clipboard ? `Paste “${clipboard.title}”${clipboard.mode === 'cut' ? ' (move)' : ''}` : 'Paste', icon: '📋', disabled: !canManageFolder(path) || !clipboard, onClick: () => paste(path) },
    '-',
    { label: 'Permissions…', icon: '🔒', disabled: !admin, hint: admin ? 'Who may see, run or change this folder' : 'Needs an admin account', onClick: () => setPermFolder(path) },
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
        <div><p className="eyebrow">Report server</p><h1>{{ repository: 'Published reports', schedules: 'Schedules', runs: 'Runs and outputs' }[view]}</h1>
          <p className="muted">{{ repository: 'Right-click a report or a folder for its actions. Only published or uploaded reports are on the server.', schedules: 'Reports that run by themselves at set times. Create one from a report → Schedules.', runs: 'Background runs and scheduled outputs. Files are kept on the server until they are replaced or deleted.' }[view]}</p></div>
        <div className="row">
          <div className="segmented view-switch" role="tablist">
            {[['repository', '🗄 Repository'], ['schedules', '⏰ Schedules'], ['runs', '⟳ Runs']].map(([id, label]) => (
              <button key={id} role="tab" aria-selected={view === id} className={view === id ? 'on' : ''} onClick={() => setView(id)}>{label}</button>
            ))}
          </div>
          {view === 'repository' && designer && <button className="btn primary" onClick={() => setUploading(true)}>⤒ Add Resource</button>}
          {view === 'repository' && <button className="btn" onClick={load}>↻ Refresh</button>}
        </div>
      </div>
      {user.bootstrap && <Notice message={{ kind: 'warn', text: 'You are signed in with the first-setup admin login. Create a real admin user in Admin → Users; this login then stops working.' }} />}
      {message && <div style={{ marginTop: 10 }}><Notice message={message} /></div>}
      {clipboard && <div className="clip-bar">📋 {clipboard.mode === 'cut' ? 'Cut' : 'Copied'}: <b>{clipboard.title}</b> — right-click a folder → Paste <button className="btn quiet sm" onClick={() => setClipboard(null)}>✕</button></div>}
      {view === 'schedules' && <section className="card"><SchedulesList info={scheduleInfo} onMessage={showMessage} /></section>}
      {view === 'runs' && <section className="card"><RunsList onMessage={showMessage} version={runsVersion} /></section>}
      {view === 'repository' && <div className={`server-grid ${selectedId ? 'has-selection' : ''}`} style={{ marginTop: 14 }}>
        <aside className="card" onContextMenu={showMenu(folderMenu(''))}>
          <div className="card-head"><div><p className="eyebrow">Folders</p><h2>Repository</h2></div>{designer && <button className="btn sm quiet" onClick={() => createFolder()}>+ Folder</button>}</div>
          <input type="search" placeholder="⌕  Find a report" value={search} onChange={(e) => setSearch(e.target.value)} style={{ marginBottom: 10 }} />
          <FolderTree folders={folders} selected={folder} onSelect={setFolder} count={countIn} rootCount={reports.length}
            onMenu={(event, path) => showMenu(folderMenu(path))(event)} storageKey="server-folders-open" />
        </aside>
        <section className="card" onContextMenu={showMenu(folderMenu(folder))}>
          <div className="card-head">
            <div><Breadcrumbs path={folder} onSelect={setFolder} /><h2>{folder ? folder.split('/').pop() : 'All reports'}</h2></div>
            <div className="row">
              {designer && canManageFolder(folder) && <button className="btn sm" onClick={() => createFolder(folder)} title={folder ? `New folder inside /${folder}` : 'New top-level folder'}>+ Folder</button>}
              {designer && clipboard && <button className="btn sm" onClick={() => paste(folder)} title="Paste here">📋 Paste</button>}
              {admin && <button className="btn sm" onClick={() => setPermFolder(folder)} title="Who may see, run or change this folder">🔒 Permissions</button>}
              {admin && folder && folder !== 'General' && <button className="btn danger sm" onClick={() => deleteFolder()}>Delete folder</button>}
            </div>
          </div>
          {!search.trim() && <SubfolderTiles folders={folders} parent={folder} count={countIn} onOpen={setFolder} onMenu={(event, path) => showMenu(folderMenu(path))(event)} />}
          <div className="stack">
            {loading && <div className="empty">Loading…</div>}
            {!loading && !visible.length && (
              <div className="empty">
                {!reports.length ? 'Nothing is published yet. Open the Designer, save a report and use “Publish to server”, or Add Resource → upload a .mrd file.'
                  : search.trim() ? `No report matches “${search.trim()}”${folder ? ` in /${folder}` : ''}.` : folder ? 'No reports directly in this folder.' : 'No reports.'}
                {designer && folder && !search.trim() && canManageFolder(folder) && (
                  <div className="row" style={{ justifyContent: 'center', marginTop: 10 }}>
                    <button className="btn sm" onClick={() => setUploading(true)}>⤒ Add a report here</button>
                    <button className="btn sm" onClick={() => createFolder(folder)}>+ Subfolder</button>
                  </div>
                )}
              </div>
            )}
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
              scheduleInfo={scheduleInfo} onBackground={() => setRunsVersion((n) => n + 1)} onBack={() => setSelectedId('')}
              menu={(event, info) => setMenu({ x: event.clientX, y: event.clientY, items: reportMenu(reports.find((r) => r.id === info.id) || info) })} />
          : <section className="card resource hide-mobile" style={{ textAlign: 'center', padding: '48px 18px' }}><div style={{ fontSize: 30, opacity: 0.5 }}>▤</div><h2>Choose a report</h2><p className="muted">Run it, or check its parameters, data source, query and history. Right-click for more.</p></section>}
      </div>}
      <ContextMenu menu={menu} onClose={() => setMenu(null)} />
      {newFolder !== null && <NewFolderDialog folders={folders} parent={newFolder} onCreate={saveNewFolder} onClose={() => setNewFolder(null)} title="New folder on the server" />}
      {permFolder !== null && <PermissionsDialog folder={permFolder} onClose={() => setPermFolder(null)} onSaved={load} />}
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
/** After signing in: back to the page that sent the user here (?next=), else /server, or their first app without the Report Server right. */
function leaveLogin(user) {
  const next = safeNext(new URLSearchParams(window.location.search).get('next'));
  if (next) { window.location.assign(next); return true; }
  if (window.location.pathname === '/login' || !user.rights?.includes('server')) { window.location.assign('/'); return true; }
  return false;
}

export default function ServerApp() {
  const [user, setUser] = useState(undefined); // undefined = checking, null = signed out
  const [loginMessage, setLoginMessage] = useState(null);

  useEffect(() => {
    whenSignedOut(() => { setUser(null); setLoginMessage({ text: 'Your session has ended. Please sign in again.', kind: 'error' }); });
    serverApi.me().then((u) => { if (!leaveLogin(u)) setUser(u); }).catch(() => setUser(null));
  }, []);
  const keepUser = useCallback((next) => setUser((current) => (current && next && current.username === next.username && current.role === next.role ? current : next)), []);

  return (
    <>
      <Topbar current="/server" />
      {user === undefined && <div className="login-wrap"><p className="muted">Checking your session…</p></div>}
      {user === null && <LoginForm key={loginMessage?.text || 'login'} message={loginMessage} onSignedIn={(u) => { setLoginMessage(null); if (!leaveLogin(u)) setUser(u); }} />}
      {user && <Repository user={user} onUser={keepUser} />}
    </>
  );
}
