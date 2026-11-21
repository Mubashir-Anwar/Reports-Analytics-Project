import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from 'react';
import ContextMenu from '../ContextMenu.jsx';
import { Notice, Topbar } from '../portal/Shell.jsx';
import { BRAND } from '../shared/brand.js';
import { Logo } from '../shared/NavBar.jsx';
import { Breadcrumbs, FolderDeleteDialog, FolderTree, NewFolderDialog, SubfolderTiles } from '../shared/folders.jsx';
import PermissionsDialog from './PermissionsDialog.jsx';
import ServerGuide, { markPermissionsSet, readGuideHidden, showGuideAgain } from './ServerGuide.jsx';
import ResourcePanel from './ResourcePanel.jsx';
import { RunsList, SchedulesList } from './Schedules.jsx';
import LogsView from './LogsView.jsx';
import UploadDialog from './UploadDialog.jsx';

const DashboardViewer = lazy(() => import('./DashboardViewer.jsx'));     // charts load only when a dashboard is opened
import { safeNext } from '../shared/session.js';
import { roleAtLeast, serverApi, splitRef, whenSignedOut } from './serverApi.js';

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
      if (user.replacedOther) {
        try { sessionStorage.setItem('team-note', `You were also signed in on ${user.replacedOther.by}. That browser is signed out in ${Math.round(user.replacedOther.seconds)} seconds: one account works in one browser at a time.`); } catch { /* private mode */ }
      }
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
        <details className="forgot">
          <summary>Forgot your password?</summary>
          <ol>
            <li><b>Users:</b> ask an administrator. In Admin → Users &amp; rights they set a new password for you.</li>
            <li><b>Super admin</b> (or no administrator can sign in): on the server, in the service folder, run
              <code className="block">python -m backend.app.reset_password &lt;username&gt;</code>
              Docker: <code>docker compose exec reporting python -m backend.app.reset_password &lt;username&gt;</code>.
              It asks for a new password (or <code>--generate</code> makes one), enables the account again and signs out its old sessions. No database editing is needed.</li>
          </ol>
        </details>
        <p className="muted" style={{ fontSize: 12 }}>Accounts and their rights are set in Admin → Users &amp; rights. First setup: user <code>admin</code> with the admin token as the password, until a super admin exists.</p>
      </form>
    </div>
  );
}

/* ---------------------------------------------------------------- repository */
function Repository({ user, onUser }) {
  const [folders, setFolders] = useState([]);
  const [reports, setReports] = useState([]);
  const [dashboards, setDashboards] = useState([]);
  const [openDash, setOpenDash] = useState(null);   // the dashboard being viewed (replaces the three columns)
  const [folder, setFolder] = useState('');
  const [sideWidth, setSideWidth] = useState(readSide);
  useEffect(() => { try { if (sideWidth) localStorage.setItem(SIDE_KEY, String(sideWidth)); else localStorage.removeItem(SIDE_KEY); } catch { /* private mode */ } }, [sideWidth]);
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState('');
  const [tab, setTab] = useState('run');
  const [message, setMessage] = useState(null);
  const [loading, setLoading] = useState(true);
  const [menu, setMenu] = useState(null);
  const [clipboard, setClipboard] = useState(null); // { mode: 'copy' | 'cut', id, title, kind: 'report' | 'dashboard' }
  const [uploading, setUploading] = useState(false);
  const [version, setVersion] = useState(0); // reloads the resource panel after a change
  const [view, setView] = useState('repository'); // repository | schedules | runs
  const [folderAccess, setFolderAccess] = useState({}); // folder → 'none' | 'run' | 'manage' for this user
  const [permFolder, setPermFolder] = useState(null); // folder whose Permissions dialog is open ('' = root)
  const [scheduleInfo, setScheduleInfo] = useState(null);
  const [runsVersion, setRunsVersion] = useState(0);
  const [newFolder, setNewFolder] = useState(null); // parent path while the New folder dialog is open
  const [guideHidden, setGuideHidden] = useState(readGuideHidden);
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
      setDashboards(data.dashboards || []);
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
  const visibleDashboards = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return dashboards.filter((d) => (!folder || (needle ? inFolder(d, folder) : d.folder === folder)) && (!needle || `${d.name} ${d.description}`.toLowerCase().includes(needle)));
  }, [dashboards, folder, search]);

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
  const countIn = (path) => reports.filter((r) => inFolder(r, path)).length + dashboards.filter((d) => inFolder(d, path)).length;
  const [deleting, setDeleting] = useState(null); // { path, contents } while the Delete folder dialog is open
  const deleteFolder = async (path = folder) => {
    try { setDeleting({ path, contents: await serverApi.folderContents(path) }); } catch (error) { fail(error); }
  };
  const confirmDelete = async (answer) => {
    const { path } = deleting;
    const result = await serverApi.deleteFolder(path, answer);     // an error stays in the dialog
    if (folder === path || folder.startsWith(`${path}/`)) setFolder(answer.moveTo || '');
    const [selFolder] = splitRef(selectedId);
    if (selectedId && (selFolder === path || selFolder.startsWith(`${path}/`))) setSelectedId('');
    const extra = [result.subfoldersDeleted ? `${result.subfoldersDeleted} subfolder(s)` : '', result.reportsDeleted?.length ? `${result.reportsDeleted.length} report(s)` : ''].filter(Boolean).join(' and ');
    await done(answer.moveTo !== undefined ? `Folder /${path} deleted; what was inside is now in ${answer.moveTo ? `/${answer.moveTo}` : 'the top level'}.`
      : `Folder /${path} deleted${extra ? ` with ${extra}` : ''}.`);
  };
  const remove = async (r) => {
    if (!window.confirm(`Delete “${r.title}” (${r.id}) from /${r.folder}? The designer's workspace copy is kept and can be published again.`)) return;
    try { await serverApi.deleteReport(r.ref); await done(`“${r.title}” was deleted from the server.`, r.ref === selectedId ? '' : selectedId); } catch (error) { fail(error); }
  };
  const download = async (r) => {
    try { serverApi.download(r.ref); setMessage({ text: `Downloaded ${r.id}.mrd — one file with the whole report. Open it in the Designer or upload it to another server.`, kind: 'ok' }); }
    catch (error) { fail(error); }
  };
  const paste = async (target) => {
    if (!clipboard) return;
    const where = target || 'General';
    try {
      if (clipboard.kind === 'dashboard') {
        await serverApi.moveDashboard(clipboard.id, where);
        setClipboard(null);
        await done(`Moved the dashboard “${clipboard.title}” to /${where}.`);
      } else if (clipboard.mode === 'cut') {
        const moved = await serverApi.move(clipboard.id, where);
        setClipboard(null);
        await done(`Moved “${clipboard.title}” to /${where}.`, moved.report.ref);
      } else {
        const result = await serverApi.copy(clipboard.id, where);
        await done(`Copied “${clipboard.title}” to /${where} as ${result.report.id}.`, result.report.ref);
      }
    } catch (error) { fail(error); }
  };
  const openInDesigner = (r) => window.open(`/designer?open=${encodeURIComponent(r.id)}&folder=${encodeURIComponent(r.folder)}`, '_blank', 'noopener');

  const reportMenu = (r) => [
    { header: `${r.title} (${r.id})${r.folder ? ` · /${r.folder}` : ''}` },
    { label: 'Run', icon: '▶', onClick: () => open(r.ref, 'run') },
    { label: 'Edit', icon: '✎', disabled: !canManage(r), hint: canManage(r) ? '' : designer ? 'Needs the Manage permission on this folder' : 'Needs a designer account', onClick: () => open(r.ref, 'overview') },
    { label: 'Schedule…', icon: '⏰', onClick: () => { setView('repository'); open(r.ref, 'schedules'); } },
    { label: 'Parameters', icon: '$P', onClick: () => open(r.ref, 'parameters') },
    { label: 'Data Source', icon: '⛁', onClick: () => open(r.ref, 'datasource') },
    { label: 'Open in Designer', icon: '🎨', disabled: !canManage(r), onClick: () => openInDesigner(r) },
    { label: 'Download (.mrd)', icon: '⤓', disabled: !canManage(r), onClick: () => download(r) },
    '-',
    { label: 'Copy', icon: '⧉', shortcut: 'Ctrl+C', disabled: !designer, onClick: () => { setClipboard({ mode: 'copy', id: r.ref, title: r.title }); setMessage({ text: `“${r.title}” copied — right-click a folder → Paste.`, kind: 'ok' }); } },
    { label: 'Cut', icon: '✂', shortcut: 'Ctrl+X', disabled: !canManage(r), onClick: () => { setClipboard({ mode: 'cut', id: r.ref, title: r.title }); setMessage({ text: `“${r.title}” cut — right-click a folder → Paste to move it.`, kind: 'ok' }); } },
    { label: 'Paste into this folder', icon: '📋', disabled: !canManageFolder(r.folder) || !clipboard, onClick: () => paste(r.folder) },
    '-',
    { label: 'History', icon: '🕘', onClick: () => open(r.ref, 'history') },
    { label: 'Properties', icon: '⚙', onClick: () => open(r.ref, 'overview') },
    { label: 'Delete', icon: '✕', danger: true, disabled: !admin, hint: admin ? '' : 'Needs an admin account', onClick: () => remove(r) },
  ];
  const removeDashboard = async (d) => {
    if (!window.confirm(`Delete the dashboard “${d.name}” from the server and Analytics? Its datasets stay. This cannot be undone.`)) return;
    try { await serverApi.deleteDashboard(d.id); if (openDash?.id === d.id) setOpenDash(null); await done(`The dashboard “${d.name}” was deleted.`); } catch (error) { fail(error); }
  };
  const downloadDashboard = async (d) => {
    try {
      const { saveDashboardFile } = await import('../analytics/api.js');
      saveDashboardFile(await serverApi.dashboardFile(d.id), d.name);
      setMessage({ text: `Downloaded ${d.name}.mdash: the dashboard and its datasets' definitions (no data). Upload it on another server with Add Resource.`, kind: 'ok' });
    } catch (error) { fail(error); }
  };
  const canManageDash = (d) => designer && d.access === 'manage';
  const dashboardMenu = (d) => [
    { header: `${d.name} (dashboard)` },
    { label: 'Open', icon: '📊', onClick: () => setOpenDash(d) },
    { label: 'Open in Analytics', icon: '✎', disabled: !user.rights?.includes('analytics'), hint: user.rights?.includes('analytics') ? 'Change it there' : 'Needs the Analytics right', onClick: () => window.open(`/analytics#dash/${encodeURIComponent(d.id)}`, '_blank', 'noopener') },
    { label: 'Download (.mdash)', icon: '⤓', disabled: !canManageDash(d), hint: canManageDash(d) ? 'The dashboard and its datasets (no data)' : 'Needs the Manage permission on this folder', onClick: () => downloadDashboard(d) },
    '-',
    { label: 'Cut', icon: '✂', disabled: !canManageDash(d), onClick: () => { setClipboard({ mode: 'cut', id: d.id, title: d.name, kind: 'dashboard' }); setMessage({ text: `“${d.name}” cut — right-click a folder → Paste to move it.`, kind: 'ok' }); } },
    { label: 'Paste into this folder', icon: '📋', disabled: !canManageFolder(d.folder) || !clipboard, onClick: () => paste(d.folder) },
    '-',
    { label: 'Delete', icon: '✕', danger: true, disabled: !admin || d.access !== 'manage', hint: admin ? '' : 'Needs an admin account', onClick: () => removeDashboard(d) },
  ];
  const folderMenu = (path) => [
    { header: path ? `/${path}` : 'Repository' },
    { label: 'Add Resource: upload .mrd / .jrxml / .mdash', icon: '⤒', disabled: !canManageFolder(path), onClick: () => { setFolder(path); setUploading(true); } },
    { label: 'New folder', icon: '📁', disabled: !canManageFolder(path), onClick: () => createFolder(path) },
    { label: clipboard ? `Paste “${clipboard.title}”${clipboard.mode === 'cut' ? ' (move)' : ''}` : 'Paste', icon: '📋', disabled: !canManageFolder(path) || !clipboard, onClick: () => paste(path) },
    '-',
    { label: 'Permissions…', icon: '🔒', disabled: !admin, hint: admin ? 'Who may see, run or change this folder' : 'Needs an admin account', onClick: () => setPermFolder(path) },
    { label: 'Delete folder…', icon: '✕', danger: true, disabled: !admin || !path, hint: !admin ? 'Deleting needs an admin account' : 'Shows what is inside; move it or delete it', onClick: () => deleteFolder(path) },
  ];
  const showMenu = (items) => (event) => { event.preventDefault(); event.stopPropagation(); setMenu({ x: event.clientX, y: event.clientY, items }); };

  // Ctrl+C / Ctrl+X / Ctrl+V on the selected report (outside text fields)
  useEffect(() => {
    const onKey = (event) => {
      if (!designer || !(event.ctrlKey || event.metaKey) || ['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName)) return;
      const key = event.key.toLowerCase();
      const r = reports.find((x) => x.ref === selectedId);
      if ((key === 'c' || key === 'x') && r && !window.getSelection()?.toString()) { setClipboard({ mode: key === 'c' ? 'copy' : 'cut', id: r.ref, title: r.title, kind: 'report' }); setMessage({ text: `“${r.title}” ${key === 'c' ? 'copied' : 'cut'}.`, kind: 'ok' }); }
      if (key === 'v' && clipboard) paste(folder || r?.folder);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  return (
    <main className="page">
      <div className="page-head">
        <div><p className="eyebrow">Report server</p><h1>{{ repository: 'Reports and dashboards', schedules: 'Schedules', runs: 'Runs and outputs', logs: 'Logs' }[view]}</h1>
          <p className="muted">{{ repository: 'Right-click a report, a dashboard or a folder for its actions. Reports are published from the Designer, dashboards are made in Analytics; both can be uploaded as files.', schedules: 'Reports that run by themselves at set times. Create one from a report → Schedules.', runs: 'Background runs and scheduled outputs. Files are kept on the server until they are replaced or deleted.', logs: 'Every request that reached the server (kept in the system database), report runs, the service log and the audit trail.' }[view]}</p></div>
        <div className="row">
          <div className="segmented view-switch" role="tablist">
            {[['repository', '🗄 Repository'], ['schedules', '⏰ Schedules'], ['runs', '⟳ Runs'], ...(admin ? [['logs', '📜 Logs']] : [])].map(([id, label]) => (
              <button key={id} role="tab" aria-selected={view === id} className={view === id ? 'on' : ''} onClick={() => setView(id)}>{label}</button>
            ))}
          </div>
          {view === 'repository' && designer && <button className="btn primary" onClick={() => setUploading(true)}>⤒ Add Resource</button>}
          {view === 'repository' && <button className="btn" onClick={load}>↻ Refresh</button>}
          {view === 'repository' && guideHidden && <button className="btn quiet" onClick={() => { showGuideAgain(); setGuideHidden(false); }} title="Show the Get started steps again">? Get started</button>}
        </div>
      </div>
      {user.bootstrap && <Notice message={{ kind: 'warn', text: 'You are signed in with the first-setup admin login. Create a real admin user in Admin → Users; this login then stops working.' }} />}
      {message && <div style={{ marginTop: 10 }}><Notice message={message} /></div>}
      {clipboard && <div className="clip-bar">📋 {clipboard.mode === 'cut' ? 'Cut' : 'Copied'}: <b>{clipboard.title}</b> — right-click a folder → Paste <button className="btn quiet sm" onClick={() => setClipboard(null)}>✕</button></div>}
      {view === 'repository' && !openDash && !loading && !guideHidden && (
        <ServerGuide folders={folders} reports={reports} designer={designer} admin={admin} onHide={() => setGuideHidden(true)}
          onFolder={() => createFolder('')} onUpload={() => setUploading(true)} onPermissions={() => setPermFolder(folder)} onSchedules={() => setView('schedules')} />
      )}
      {view === 'schedules' && <section className="card"><SchedulesList info={scheduleInfo} onMessage={showMessage} /></section>}
      {view === 'runs' && <section className="card"><RunsList onMessage={showMessage} version={runsVersion} /></section>}
      {view === 'logs' && admin && <section className="card"><LogsView /></section>}
      {view === 'repository' && openDash && (
        <Suspense fallback={<section className="card"><p className="muted">Loading the dashboard…</p></section>}>
          <DashboardViewer key={openDash.id} entry={openDash} mayManage={canManageDash(openDash)} onBack={() => setOpenDash(null)} onMessage={showMessage} />
        </Suspense>
      )}
      {view === 'repository' && !openDash && <div className={`server-grid ${selectedId ? 'has-selection' : ''}`} style={{ marginTop: 14, ...(sideWidth ? { '--side-w': `min(${sideWidth}px, 45vw)` } : {}) }}>
        <aside className="card" onContextMenu={showMenu(folderMenu(''))}>
          <div className="card-head"><div><p className="eyebrow">Folders</p><h2>Repository</h2></div>{designer && <button className="btn sm quiet" onClick={() => createFolder()}>+ Folder</button>}</div>
          <input type="search" placeholder="⌕  Find a report or dashboard" value={search} onChange={(e) => setSearch(e.target.value)} style={{ marginBottom: 10 }} />
          <FolderTree folders={folders} selected={folder} onSelect={setFolder} count={countIn} rootLabel="Everything" rootCount={reports.length + dashboards.length}
            onMenu={(event, path) => showMenu(folderMenu(path))(event)} storageKey="server-folders-open" />
          <SideResizer width={sideWidth} onWidth={setSideWidth} />
        </aside>
        <section className="card" onContextMenu={showMenu(folderMenu(folder))}>
          <div className="card-head">
            <div><Breadcrumbs path={folder} onSelect={setFolder} rootLabel="Everything" /><h2>{folder ? folder.split('/').pop() : 'Everything'}</h2></div>
            <div className="row">
              {designer && canManageFolder(folder) && <button className="btn sm" onClick={() => createFolder(folder)} title={folder ? `New folder inside /${folder}` : 'New top-level folder'}>+ Folder</button>}
              {designer && clipboard && <button className="btn sm" onClick={() => paste(folder)} title="Paste here">📋 Paste</button>}
              {admin && <button className="btn sm" onClick={() => setPermFolder(folder)} title="Who may see, run or change this folder">🔒 Permissions</button>}
              {admin && folder && <button className="btn danger sm" onClick={() => deleteFolder()}>Delete folder</button>}
            </div>
          </div>
          {!search.trim() && <SubfolderTiles folders={folders} parent={folder} count={countIn} itemWord="item" onOpen={setFolder} onMenu={(event, path) => showMenu(folderMenu(path))(event)} />}
          <div className="stack">
            {loading && <div className="empty">Loading…</div>}
            {!loading && !visible.length && !visibleDashboards.length && (
              <div className="empty">
                {!reports.length && !dashboards.length ? 'Nothing is here yet. Publish a report from the Designer, make a dashboard in Analytics, or Add Resource → upload a .mrd or .mdash file.'
                  : search.trim() ? `Nothing matches “${search.trim()}”${folder ? ` in /${folder}` : ''}.` : folder ? 'Nothing directly in this folder.' : 'Nothing here.'}
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
                key={r.ref}
                className={`report-card ${r.ref === selectedId ? 'on' : ''} ${clipboard?.mode === 'cut' && clipboard.id === r.ref ? 'cut' : ''}`}
                onClick={() => open(r.ref, tab)}
                onDoubleClick={() => open(r.ref, 'run')}
                onContextMenu={(e) => { setSelectedId(r.ref); showMenu(reportMenu(r))(e); }}
              >
                <strong>{r.title}</strong>
                <small>{!r.folder || r.folder === 'General' ? 'Top level' : r.folder} · {r.formats.map((f) => f.toUpperCase()).join(', ')}{r.parameters.length ? ` · ${r.parameters.length} question${r.parameters.length > 1 ? 's' : ''}` : ''}</small>
                {r.description && <small>{r.description}</small>}
              </button>
            ))}
            {visibleDashboards.map((d) => (
              <button key={d.id} className={`report-card dashboard-card ${clipboard?.mode === 'cut' && clipboard.id === d.id ? 'cut' : ''}`}
                onClick={() => setOpenDash(d)} onContextMenu={showMenu(dashboardMenu(d))}>
                <strong><span className="kind-badge" aria-hidden="true">📊</span> {d.name}</strong>
                <small>{!d.folder || d.folder === 'General' ? 'Top level' : d.folder} · dashboard · {d.tiles} visual{d.tiles === 1 ? '' : 's'}{d.owner ? ` · by ${d.owner}` : ''}</small>
                {d.description && <small>{d.description}</small>}
              </button>
            ))}
          </div>
        </section>
        {selectedId
          ? <ResourcePanel key={`${selectedId}-${version}`} reportId={selectedId} tab={tab} setTab={setTab} onChanged={load}
              scheduleInfo={scheduleInfo} onBackground={() => setRunsVersion((n) => n + 1)} onBack={() => setSelectedId('')}
              menu={(event, info) => setMenu({ x: event.clientX, y: event.clientY, items: reportMenu(reports.find((r) => r.ref === info.ref) || info) })} />
          : (
            <section className="card resource hide-mobile choose-hint">
              <div className="choose-icon" aria-hidden="true">▤</div>
              <h2>Choose a report</h2>
              <p className="muted">Click a report in the middle. Then you can:</p>
              <ul>
                <li><b>▶ Run</b> it now and download PDF / Excel / CSV <small>(double-click does this too)</small></li>
                <li><b>⏰ Schedule</b> it: every morning, every Monday, month end…</li>
                <li>See its <b>parameters</b>, <b>data source</b>, <b>query</b>, <b>versions</b> and <b>history</b></li>
                <li><b>Right-click</b> it to copy, move, download or delete</li>
              </ul>
            </section>
          )}
      </div>}
      <ContextMenu menu={menu} onClose={() => setMenu(null)} />
      {newFolder !== null && <NewFolderDialog folders={folders} parent={newFolder} onCreate={saveNewFolder} onClose={() => setNewFolder(null)} title="New folder on the server" />}
      {deleting && <FolderDeleteDialog path={deleting.path} contents={deleting.contents} folders={folders} onConfirm={confirmDelete} onClose={() => setDeleting(null)} />}
      {permFolder !== null && <PermissionsDialog folder={permFolder} onClose={() => setPermFolder(null)} onSaved={() => { markPermissionsSet(); load(); }} />}
      {uploading && (
        <UploadDialog folders={folders} folder={folder} onClose={() => setUploading(false)}
          onDone={async (result) => {
            setUploading(false);
            if (result.dashboard) {
              setFolder(result.dashboard.folder);
              await done(`${result.replaced ? 'Replaced' : 'Uploaded'} the dashboard “${result.dashboard.name}” in /${result.dashboard.folder}: ${result.created.length} new dataset(s) importing now, ${result.reused.length} already here.${result.warnings?.length ? ` Check: ${result.warnings.join('; ')}` : ''}`);
              return;
            }
            setFolder(result.report.folder);
            await done(`${result.replaced ? 'Replaced' : 'Uploaded'} ${result.report.id} in /${result.report.folder}.${result.warnings?.length ? ` Check: ${result.warnings.join('; ')}` : ''}`, result.report.ref);
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


const SIDE_KEY = 'server-folders-width';
const SIDE_MIN = 180;
const sideMax = () => Math.max(SIDE_MIN, Math.min(560, Math.round(window.innerWidth * 0.45)));
const readSide = () => { try { const w = Number(localStorage.getItem(SIDE_KEY)); return w >= SIDE_MIN ? w : null; } catch { return null; } };

/** The right edge of the Folders panel: drag it (mouse or touch) or use ← → to make the panel wider; double-click = normal. */
function SideResizer({ width, onWidth }) {
  const [dragging, setDragging] = useState(false);
  const clamp = (w) => Math.round(Math.min(sideMax(), Math.max(SIDE_MIN, w)));
  const start = (event) => {
    const aside = event.currentTarget.parentElement;
    const startX = event.clientX;
    const startW = aside.getBoundingClientRect().width;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setDragging(true);
    const move = (e) => onWidth(clamp(startW + e.clientX - startX));
    const stop = () => { setDragging(false); window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', stop); window.removeEventListener('pointercancel', stop); };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', stop);
    window.addEventListener('pointercancel', stop);
    event.preventDefault();
  };
  const key = (event) => {
    const step = event.shiftKey ? 40 : 10;
    const now = width || event.currentTarget.parentElement.getBoundingClientRect().width;
    if (event.key === 'ArrowRight') { event.preventDefault(); onWidth(clamp(now + step)); }
    if (event.key === 'ArrowLeft') { event.preventDefault(); onWidth(clamp(now - step)); }
  };
  return (
    <div className={`side-resizer ${dragging ? 'dragging' : ''}`} role="separator" aria-orientation="vertical" aria-label="Width of the Folders panel"
      aria-valuenow={width || undefined} aria-valuemin={SIDE_MIN} tabIndex={0} title="Drag to make the Folders panel wider or narrower (double-click: normal width)"
      onPointerDown={start} onKeyDown={key} onDoubleClick={() => onWidth(null)} />
  );
}

export default function ServerApp() {
  const [user, setUser] = useState(undefined); // undefined = checking, null = signed out
  const [loginMessage, setLoginMessage] = useState(() => (new URLSearchParams(window.location.search).get('reason') === 'elsewhere'
    ? { text: 'You were signed out here because your account signed in on another browser (one account works in one browser at a time). Sign in again to continue here; the other browser will then be signed out.', kind: 'error' }
    : null));

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
