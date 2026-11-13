// Folder permissions (JasperReports Server: right-click a folder → Permissions). Administrators only.
import { useEffect, useState } from 'react';
import { Notice } from '../portal/Shell.jsx';
import { serverApi } from './serverApi.js';

const LEVELS = [['none', 'None — hidden'], ['run', 'Run — see and run'], ['manage', 'Manage — also edit, upload, move']];
const LEVEL_LABEL = { none: 'None', run: 'Run', manage: 'Manage' };

export default function PermissionsDialog({ folder, onClose, onSaved }) {
  const [data, setData] = useState(null);
  const [rows, setRows] = useState([]); // { kind: 'user' | 'role', name, level }
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);

  const fill = (result) => {
    setData(result);
    setRows([
      ...Object.entries(result.rules.roles).map(([name, level]) => ({ kind: 'role', name, level })),
      ...Object.entries(result.rules.users).map(([name, level]) => ({ kind: 'user', name, level })),
    ]);
  };
  useEffect(() => { serverApi.permissions(folder).then(fill).catch((e) => setMessage({ kind: 'error', text: e.message })); }, [folder]);

  const change = (index, patch) => setRows(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  const usedUsers = new Set(rows.filter((r) => r.kind === 'user').map((r) => r.name.toLowerCase()));
  const freeUsers = (data?.users || []).filter((u) => !usedUsers.has(u.toLowerCase()));
  const freeRoles = ['viewer', 'designer'].filter((role) => !rows.some((r) => r.kind === 'role' && r.name === role));

  const save = async () => {
    setBusy(true);
    const rules = { users: {}, roles: {} };
    rows.forEach((row) => { if (row.name) rules[row.kind === 'user' ? 'users' : 'roles'][row.name] = row.level; });
    try {
      fill(await serverApi.setPermissions(folder, rules));
      setMessage({ kind: 'ok', text: 'Saved. The rules apply to this folder and every folder below it.' });
      onSaved?.();
    } catch (e) { setMessage({ kind: 'error', text: e.message }); } finally { setBusy(false); }
  };

  return (
    <div className="dialog-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="card dialog permissions-dialog" role="dialog" aria-label="Folder permissions">
        <div className="card-head">
          <div><p className="eyebrow">Permissions</p><h2>{folder ? `/${folder}` : 'Repository root (all folders)'}</h2></div>
          <button className="btn quiet sm" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <p className="muted">A rule here applies to this folder and its subfolders. The nearest folder with a rule wins; a rule for a user wins over a rule for their role. Viewers can get at most <b>Run</b>; administrators always have every permission.</p>
        {!data && !message && <div className="empty">Loading…</div>}
        {data && (
          <>
            <div className="perm-rows">
              {!rows.length && <div className="empty">No rules on this folder: {data.inherited.length ? 'the rules of the parent folders apply.' : 'everyone works by their role (viewer = Run, designer = Manage).'}</div>}
              {rows.map((row, index) => (
                // eslint-disable-next-line react/no-array-index-key -- the position is the identity
                <div className="perm-row" key={index}>
                  <span className="chip">{row.kind}</span>
                  <b>{row.name}</b>
                  <select value={row.level} onChange={(e) => change(index, { level: e.target.value })} aria-label={`Permission of ${row.name}`}>
                    {LEVELS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                  <button className="btn quiet sm" onClick={() => setRows(rows.filter((_, i) => i !== index))} title="Remove this rule">✕</button>
                </div>
              ))}
            </div>
            <div className="row">
              <select value="" onChange={(e) => e.target.value && setRows([...rows, { kind: 'user', name: e.target.value, level: 'run' }])} aria-label="Add a rule for a user" disabled={!freeUsers.length}>
                <option value="">＋ Rule for a user…</option>
                {freeUsers.map((u) => <option key={u} value={u}>{u}</option>)}
              </select>
              <select value="" onChange={(e) => e.target.value && setRows([...rows, { kind: 'role', name: e.target.value, level: 'none' }])} aria-label="Add a rule for a role" disabled={!freeRoles.length}>
                <option value="">＋ Rule for a role…</option>
                {freeRoles.map((r) => <option key={r} value={r}>every {r}</option>)}
              </select>
            </div>
            {data.inherited.length > 0 && (
              <p className="muted">From parent folders: {data.inherited.map((item) => `/${item.folder || '(root)'} — ${[...Object.entries(item.roles).map(([n, l]) => `${n}s ${l}`), ...Object.entries(item.users).map(([n, l]) => `${n} ${l}`)].join(', ')}`).join(' · ')}</p>
            )}
            <details>
              <summary className="muted">Who gets what here (saved rules)</summary>
              <div className="table-wrap" style={{ marginTop: 8 }}>
                <table className="grid-table">
                  <thead><tr><th>User</th><th>Role</th><th>Access</th><th>Because of</th></tr></thead>
                  <tbody>
                    {data.effective.map((row) => (
                      <tr key={row.username}><td>{row.username}</td><td>{row.role}</td><td><span className={`level-chip ${row.level}`}>{LEVEL_LABEL[row.level]}</span></td><td className="muted">{row.why}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          </>
        )}
        <Notice message={message} />
        <div className="row" style={{ justifyContent: 'flex-end' }}>
          <button className="btn" onClick={onClose}>Close</button>
          <button className="btn primary" disabled={busy || !data} onClick={save}>{busy ? 'Saving…' : 'Save permissions'}</button>
        </div>
      </div>
    </div>
  );
}
