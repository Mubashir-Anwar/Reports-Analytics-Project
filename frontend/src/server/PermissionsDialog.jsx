// Folder permissions (JasperReports Server: right-click a folder → Permissions). Administrators only.
import { useEffect, useState } from 'react';
import { FolderPicker } from '../shared/folders.jsx';
import PopDialog from './PopDialog.jsx';
import { serverApi } from './serverApi.js';

const LEVELS = [['none', 'None', 'The folder is hidden'], ['run', 'Run', 'See and run'], ['manage', 'Manage', 'Also edit, upload, move']];
const LEVEL_LABEL = { none: 'None', run: 'Run', manage: 'Manage' };
const KINDS = { user: 'Person', team: 'Team', role: 'Role' };
const key = (rows) => rows.map((r) => `${r.kind}:${r.name}:${r.level}`).sort().join('|');

export default function PermissionsDialog({ folder: first, folders = [], onClose, onSaved }) {
  const [folder, setFolder] = useState(first);
  const [next, setNext] = useState(null); // where to go while there are unsaved rules: {path} or {close: true}
  const [saved, setSaved] = useState([]);
  const [adding, setAdding] = useState(false);
  const [find, setFind] = useState('');
  const [data, setData] = useState(null);
  const [rows, setRows] = useState([]); // { kind: 'user' | 'role' | 'team', name (group id for a team), level }
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);

  const fill = (result) => {
    setData(result);
    const list = [
      ...Object.entries(result.rules.roles).map(([name, level]) => ({ kind: 'role', name, level })),
      ...Object.entries(result.rules.groups || {}).map(([name, level]) => ({ kind: 'team', name, level })),
      ...Object.entries(result.rules.users).map(([name, level]) => ({ kind: 'user', name, level })),
    ];
    setRows(list);
    setSaved(list);
  };
  useEffect(() => {
    let live = true;
    setData(null); setRows([]); setSaved([]); setAdding(false); setFind('');
    serverApi.permissions(folder).then((result) => live && fill(result)).catch((e) => live && setMessage({ kind: 'error', text: e.message }));
    return () => { live = false; };
  }, [folder]); // eslint-disable-line react-hooks/exhaustive-deps
  const dirty = key(rows) !== key(saved);
  const goOn = (where) => { setNext(null); setMessage(null); if (where.close) onClose(); else setFolder(where.path); };
  const goTo = (path) => { if (path === folder) setNext(null); else if (dirty) setNext({ path }); else goOn({ path }); };
  const leave = () => { if (dirty) setNext({ close: true }); else onClose(); };

  const change = (index, patch) => setRows(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  const usedUsers = new Set(rows.filter((r) => r.kind === 'user').map((r) => r.name.toLowerCase()));
  const freeUsers = (data?.users || []).filter((u) => !usedUsers.has(u.toLowerCase()));
  const freeRoles = ['viewer', 'designer'].filter((role) => !rows.some((r) => r.kind === 'role' && r.name === role));
  const teams = data?.groups || [];
  const teamName = (id) => teams.find((g) => g.groupId === id)?.name || '(deleted team)';
  const freeTeams = teams.filter((g) => !rows.some((r) => r.kind === 'team' && r.name === g.groupId));

  const save = async () => {
    setBusy(true);
    setMessage(null);
    const rules = { users: {}, roles: {}, groups: {} };
    rows.forEach((row) => { if (row.name) rules[{ user: 'users', role: 'roles', team: 'groups' }[row.kind]][row.name] = row.level; });
    try {
      fill(await serverApi.setPermissions(folder, rules));
      setMessage({ kind: 'ok', text: 'Saved. The rules apply to this folder and every folder below it.' });
      setNext(null);
      onSaved?.();
      return true;
    } catch (e) { setMessage({ kind: 'error', text: e.message }); return false; } finally { setBusy(false); }
  };

  const add = (row) => { setRows([...rows, row]); setMessage(null); setAdding(false); setFind(''); };
  const hit = (text) => text.toLowerCase().includes(find.trim().toLowerCase());
  const choices = [
    ['People', freeUsers.filter(hit).map((u) => ({ kind: 'user', name: u, text: u, level: 'run' }))],
    ['Teams', freeTeams.filter((g) => hit(g.name)).map((g) => ({ kind: 'team', name: g.groupId, text: g.name, note: `${g.members.length} member${g.members.length === 1 ? '' : 's'}`, level: 'run' }))],
    ['Everyone with a role', freeRoles.filter((r) => hit(`every ${r}`)).map((r) => ({ kind: 'role', name: r, text: `Every ${r}`, level: 'none' }))],
  ].filter(([, list]) => list.length);
  const label = (row) => (row.kind === 'team' ? teamName(row.name) : row.kind === 'role' ? `Every ${row.name}` : row.name);
  const roleOf = (row) => (row.kind === 'user' && data?.effective.find((e) => e.username.toLowerCase() === row.name.toLowerCase())?.role) || '';
  const anyone = freeUsers.length + freeTeams.length + freeRoles.length > 0;

  return (
    <PopDialog label="Folder permissions" icon="🔐" tint="b" title="Who gets in?" subtitle="Rules count for the folder and everything below it. The nearest folder wins; in one folder: person, then team, then role." wide busy={busy} onClose={leave}
      footer={<>
        <span className="grow">{dirty ? 'Unsaved changes' : ''}</span>
        <button type="button" className="pop-btn" disabled={busy} onClick={leave}>Close</button>
        <button type="button" className="pop-btn primary" disabled={busy || !data || !dirty} onClick={save}>{busy ? 'Saving…' : 'Save permissions'}</button>
      </>}>
      {next && dirty && (
        <div className="pm-ask" role="alert"><span>The rules of {folder ? `/${folder}` : 'all folders'} are not saved yet.</span>
          <button type="button" className="pop-btn small" onClick={() => setNext(null)}>Keep editing</button>
          <button type="button" className="pop-btn small" disabled={busy} onClick={() => goOn(next)}>Drop them</button>
          <button type="button" className="pop-btn small primary" disabled={busy} onClick={async () => { const where = next; if (await save()) goOn(where); }}>Save</button>
        </div>
      )}
      <div className="pop-field"><span>Folder</span><FolderPicker folders={folders} value={folder} onChange={goTo} rootLabel="All folders" rootHint="rules for the whole server" disabled={busy} /></div>
      {message?.text && <p className={`pop-error ${message.kind}`} role="status">{message.text}</p>}
      {!data && !message && <div className="pm-none">Loading…</div>}
      {data && (
        <>
          <div className="pm-rules">
            {!rows.length && <div className="pm-none">{data.inherited.length ? 'No rules of its own: the folders above decide.' : 'No rules here: viewers can run, designers can manage.'}</div>}
            {rows.map((row, index) => (
              <div className="pm-rule" key={`${row.kind}:${row.name}`}>
                <span className={`pm-face ${row.kind}`} aria-hidden="true">{(row.kind === 'role' ? row.name : label(row)).slice(0, 2)}</span>
                <div className="pm-who"><b>{label(row)}</b><small>{KINDS[row.kind]}{roleOf(row) ? ` · ${roleOf(row)}` : ''}</small></div>
                <div className="pm-levels" role="group" aria-label={`Permission of ${label(row)}`}>
                  {LEVELS.map(([value, text, hint]) => <button key={value} type="button" title={hint} aria-pressed={row.level === value} className={`${value} ${row.level === value ? 'on' : ''}`} onClick={() => { setMessage(null); change(index, { level: value }); }}>{text}</button>)}
                </div>
                <button type="button" className="pm-out" aria-label={`Remove the rule of ${label(row)}`} title="Remove this rule" onClick={() => { setMessage(null); setRows(rows.filter((_, i) => i !== index)); }}>✕</button>
              </div>
            ))}
          </div>
          <button type="button" className="pm-add" aria-expanded={adding} disabled={!anyone} onClick={() => { setAdding(!adding); setFind(''); }}>{anyone ? '＋ Add a person, team or role' : 'Everyone has a rule here'}</button>
          {adding && (
            <div className="pm-panel" onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); setAdding(false); } }}>
              <input autoFocus value={find} onChange={(e) => setFind(e.target.value)} placeholder="Search a name" aria-label="Search a person, team or role" />
              <div className="pm-found">
                {choices.map(([head, list]) => [<h3 key={head}>{head}</h3>, ...list.map((c) => (
                  <button key={`${c.kind}:${c.name}`} type="button" onClick={() => add({ kind: c.kind, name: c.name, level: c.level })}>
                    <span className={`pm-face ${c.kind}`} aria-hidden="true">{(c.kind === 'role' ? c.name : c.text).slice(0, 2)}</span><b>{c.text}{c.note && <small>{c.note}</small>}</b>
                  </button>))])}
                {!choices.length && <div className="pm-none">Nobody matches “{find}”.</div>}
              </div>
              {!teams.length && <p className="pop-note">Teams are made in Admin → Users.</p>}
            </div>
          )}
          <p className="pop-note"><b>None</b> hides the folder, <b>Run</b> is see and run, <b>Manage</b> is also edit, upload and move. A viewer gets at most Run; administrators always get everything.</p>
          {data.inherited.length > 0 && (
            <div className="pm-from"><b>From the folders above</b>
              {data.inherited.map((item) => (
                <div key={item.folder}><code>/{item.folder}</code>
                  {[...Object.entries(item.roles).map(([n, l]) => [`every ${n}`, l]), ...Object.entries(item.groups || {}).map(([n, l]) => [teamName(n), l]), ...Object.entries(item.users).map(([n, l]) => [n, l])]
                    .map(([name, level]) => <span key={name} className={`pm-tag ${level}`}>{name}: {LEVEL_LABEL[level]}</span>)}
                </div>
              ))}
            </div>
          )}
          <details className="pm-all">
            <summary>Who gets what here{dirty ? ' (saved rules)' : ''}</summary>
            <ul>{data.effective.map((row) => (
              <li key={row.username}><span className="pm-face user" aria-hidden="true">{row.username.slice(0, 2)}</span><div className="pm-who"><b>{row.username}</b><small>{row.role} · {row.why}</small></div><span className={`pm-tag ${row.level}`}>{LEVEL_LABEL[row.level]}</span></li>
            ))}</ul>
          </details>
        </>
      )}
    </PopDialog>
  );
}
