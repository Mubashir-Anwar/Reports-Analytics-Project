import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api.js';
import { Notice, formatWhen } from '../portal/Shell.jsx';
import PasswordInput from '../shared/PasswordInput.jsx';

const RIGHT_HELP = {
  admin: 'Users, API keys, data sources, logs',
  designer: 'Build, preview and publish reports',
  query: 'Query workspace: browse data sources and try MongoDB / SQL queries (read-only)',
  server: 'Report Server: folders, run, schedules (folder permissions apply)',
  analytics: 'Dashboards and models',
  runner: 'Running reports by URL (/generate, /reports, collections)',
};
const DEFAULT_RIGHTS = [{ id: 'admin', label: 'Admin' }, { id: 'designer', label: 'Designer' }, { id: 'query', label: 'Query' }, { id: 'server', label: 'Report Server' },
  { id: 'analytics', label: 'Analytics' }, { id: 'runner', label: 'Run reports by URL' }];
const LEVEL_LABEL = { none: 'None', run: 'Run', manage: 'Manage' };
const EMPTY_FORM = { username: '', fullName: '', email: '', phone: '', password: '', rights: ['runner'], superAdmin: false };

/** Ticks for the rights; a super admin has all of them, so the ticks are shown on and locked. */
function RightsPicker({ rights, superAdmin, allRights, canGrantAdmin, onChange, compact }) {
  return (
    <div className={`rights ${compact ? 'compact' : ''}`} role="group" aria-label="Rights">
      {allRights.map((right) => {
        const locked = superAdmin || (right.id === 'admin' && !canGrantAdmin);
        return (
          <label key={right.id} className={`right-chip ${superAdmin || rights.includes(right.id) ? 'on' : ''}`} title={RIGHT_HELP[right.id]}>
            <input type="checkbox" checked={superAdmin || rights.includes(right.id)} disabled={locked}
              onChange={(e) => onChange(e.target.checked ? [...rights, right.id] : rights.filter((item) => item !== right.id))} />
            {right.label}
          </label>
        );
      })}
    </div>
  );
}

/** Asks the server, a moment after typing stops, whether a username / e-mail / phone is already used. */
function useTakenCheck(values, exceptUser = '') {
  const [answer, setAnswer] = useState({});
  const timer = useRef(null);
  const key = JSON.stringify([values, exceptUser]);
  useEffect(() => {
    clearTimeout(timer.current);
    const asked = Object.fromEntries(Object.entries(values).filter(([, value]) => String(value || '').trim()));
    if (!Object.keys(asked).length) { setAnswer({}); return undefined; }
    timer.current = setTimeout(() => {
      api.usersAvailable({ ...asked, exceptUser }).then(setAnswer).catch(() => setAnswer({}));
    }, 350);
    return () => clearTimeout(timer.current);
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  return answer;
}

function FieldNote({ check, takenText }) {
  if (!check) return null;
  if (check.taken) return <small className="field-bad" role="alert">✕ {takenText}</small>;
  if (check.problem) return <small className="field-bad">✕ {check.problem}</small>;
  return <small className="field-ok">✓ free</small>;
}

/** Name, e-mail and phone of an existing user (opened from the list). */
function ContactEditor({ user, onSave, onClose }) {
  const [form, setForm] = useState({ fullName: user.fullName || '', email: user.email || '', phone: user.phone || '' });
  const check = useTakenCheck({ email: form.email === user.email ? '' : form.email, phone: form.phone === user.phone ? '' : form.phone }, user.username);
  const blocked = check.email?.taken || check.phone?.taken || check.email?.problem;
  return (
    <form className="contact-edit" onSubmit={(e) => { e.preventDefault(); if (!blocked) onSave(form); }}>
      <label className="field"><span>Full name</span><input value={form.fullName} onChange={(e) => setForm({ ...form, fullName: e.target.value })} maxLength={120} /></label>
      <label className="field"><span>E-mail</span><input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        <FieldNote check={check.email} takenText="another account uses this e-mail" /></label>
      <label className="field"><span>Phone</span><input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} placeholder="+92 300 1234567" />
        <FieldNote check={check.phone} takenText="another account uses this phone" /></label>
      <div className="row"><button className="btn sm primary" type="submit" disabled={blocked}>Save</button><button className="btn sm quiet" type="button" onClick={onClose}>Cancel</button></div>
    </form>
  );
}

/** Teams (user groups): folder permissions can be given to a whole team. */
function TeamsCard({ users, notify }) {
  const [groups, setGroups] = useState([]);
  const [editing, setEditing] = useState(null);     // { groupId?, name, description, members }
  const load = useCallback(() => api.userGroups().then((data) => setGroups(data.groups)).catch((error) => notify(error.message, 'error')), [notify]);
  useEffect(() => { load(); }, [load]);
  const save = async (event) => {
    event.preventDefault();
    try {
      await (editing.groupId ? api.updateUserGroup(editing.groupId, editing) : api.createUserGroup(editing));
      notify(`Team ${editing.name} saved.`);
      setEditing(null);
      await load();
    } catch (error) { notify(error.message, 'error'); }
  };
  const remove = async (group) => {
    if (!window.confirm(`Delete the team ${group.name}? Its folder rules go too; the users stay.`)) return;
    try { await api.deleteUserGroup(group.groupId); notify(`Team ${group.name} deleted.`); await load(); }
    catch (error) { notify(error.message, 'error'); }
  };
  const toggle = (username) => setEditing({ ...editing, members: editing.members.includes(username) ? editing.members.filter((u) => u !== username) : [...editing.members, username] });
  return (
    <section className="card">
      <div className="card-head">
        <div><p className="eyebrow">Teams</p><h2>User groups</h2>
          <p className="muted">Put people in a team (Finance, Branch Karachi …) and give the team access to folders in Report Server → Permissions.</p></div>
        <button className="btn" onClick={() => setEditing({ name: '', description: '', members: [] })}>＋ New team</button>
      </div>
      {editing && (
        <form className="stack team-edit" onSubmit={save}>
          <div className="form-grid-2">
            <label className="field"><span>Name</span><input value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} maxLength={64} required autoFocus /></label>
            <label className="field"><span>Description</span><input value={editing.description || ''} onChange={(e) => setEditing({ ...editing, description: e.target.value })} maxLength={300} /></label>
          </div>
          <div className="field"><span>Members</span>
            <div className="rights">{users.map((user) => (
              <label key={user.username} className={`right-chip ${editing.members.includes(user.username) ? 'on' : ''}`}>
                <input type="checkbox" checked={editing.members.includes(user.username)} onChange={() => toggle(user.username)} />{user.fullName || user.username}
              </label>
            ))}</div>
          </div>
          <div className="row"><button className="btn primary" type="submit">Save team</button><button className="btn quiet" type="button" onClick={() => setEditing(null)}>Cancel</button></div>
        </form>
      )}
      <div className="stack">
        {!groups.length && !editing && <div className="empty">No teams yet.</div>}
        {groups.map((group) => (
          <div key={group.groupId} className="item">
            <div><strong>{group.name}</strong><small>{group.description || ''}{group.description ? ' · ' : ''}{group.members.length ? group.members.join(', ') : 'no members'}</small></div>
            <div className="row">
              <button className="btn sm" onClick={() => setEditing({ ...group })}>Edit</button>
              <button className="btn sm danger" onClick={() => remove(group)}>Delete</button>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

/** Forgot-password requests that wait for an administrator, and the last ones. */
function ResetRequestsCard({ notify }) {
  const [data, setData] = useState({ requests: [], waiting: 0 });
  const [link, setLink] = useState(null);
  const load = useCallback(() => api.passwordResets().then(setData).catch((error) => notify(error.message, 'error')), [notify]);
  useEffect(() => { load(); }, [load]);
  const decide = async (item, decision) => {
    try {
      const result = await api.decidePasswordReset(item.resetId, decision);
      if (result.link) setLink(result);
      notify(decision === 'approve' ? `Approved: give ${item.username} the link.` : `Request of ${item.username} rejected.`);
      await load();
    } catch (error) { notify(error.message, 'error'); }
  };
  const recent = data.requests.slice(0, 8);
  return (
    <section className="card">
      <div className="card-head">
        <div><p className="eyebrow">Forgot password</p><h2>Reset requests {data.waiting > 0 && <span className="chip accent">{data.waiting} waiting</span>}</h2>
          <p className="muted">A user who forgot the password can ask on the sign-in page. Approve to get a one-time link (24 hours) to pass on; it never goes by e-mail from here.</p></div>
        <button className="btn" onClick={load}>Refresh</button>
      </div>
      {link && (
        <div className="notice ok">Link for <b>{link.username}</b> (works once, {link.hours} hours):
          <code className="block selectable">{link.link}</code>
          <div className="row"><button className="btn sm" onClick={() => navigator.clipboard?.writeText(link.link).then(() => notify('Link copied.'))}>Copy</button>
            <button className="btn sm quiet" onClick={() => setLink(null)}>Done</button></div>
        </div>
      )}
      <div className="stack">
        {!recent.length && <div className="empty">No requests.</div>}
        {recent.map((item) => (
          <div key={item.resetId} className="item">
            <div><strong>{item.fullName || item.username}</strong>
              <small>{item.method === 'email_link' ? 'e-mailed link' : 'asks an administrator'} · {formatWhen(item.requestedAt)} · {item.status}{item.decidedBy ? ` by ${item.decidedBy}` : ''}{item.clientIp ? ` · from ${item.clientIp}` : ''}</small></div>
            {item.status === 'waiting' && item.method === 'admin_approval' && (
              <div className="row"><button className="btn sm primary" onClick={() => decide(item, 'approve')}>Approve</button>
                <button className="btn sm danger" onClick={() => decide(item, 'reject')}>Reject</button></div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

/** Who can see what: every user on every server folder, and the rule that decides it. */
function AccessReportCard({ notify }) {
  const [data, setData] = useState(null);
  const [filter, setFilter] = useState('');
  const load = () => api.accessReport().then(setData).catch((error) => notify(error.message, 'error'));
  const rows = useMemo(() => {
    const text = filter.trim().toLowerCase();
    return (data?.rows || []).filter((row) => !text || `${row.username} ${row.fullName} /${row.folder}`.toLowerCase().includes(text));
  }, [data, filter]);
  return (
    <section className="card wide-card">
      <div className="card-head">
        <div><p className="eyebrow">Access</p><h2>Who can see what</h2><p className="muted">Every active user on every Report Server folder: None (hidden), Run or Manage, and the rule that decides it.</p></div>
        <button className="btn" onClick={load}>{data ? 'Refresh' : 'Show'}</button>
      </div>
      {data && (
        <>
          <input className="search" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter by user or folder…" aria-label="Filter" />
          <div className="table-wrap" style={{ maxHeight: 420 }}>
            <table className="grid-table">
              <thead><tr><th>User</th><th>Folder</th><th>Access</th><th>Because of</th></tr></thead>
              <tbody>
                {rows.slice(0, 1000).map((row) => (
                  <tr key={`${row.username}|${row.folder}`}><td>{row.fullName ? `${row.fullName} (${row.username})` : row.username}</td><td>/{row.folder}</td>
                    <td><span className={`level-chip ${row.level}`}>{LEVEL_LABEL[row.level]}</span></td><td className="muted">{row.why}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          {rows.length > 1000 && <small className="muted">The first 1000 of {rows.length}: filter to see the rest.</small>}
        </>
      )}
    </section>
  );
}

export default function UsersTab({ notify }) {
  const [data, setData] = useState({ users: [], rights: DEFAULT_RIGHTS, bootstrapLogin: false, youAreSuperAdmin: false });
  const [form, setForm] = useState(EMPTY_FORM);
  const [formMessage, setFormMessage] = useState(null);
  const [busy, setBusy] = useState(false);
  const [contact, setContact] = useState('');      // username whose name / e-mail / phone is being edited

  const load = useCallback(() => api.users().then(setData).catch((error) => notify(error.message, 'error')), [notify]);
  useEffect(() => { load(); }, [load]);
  const allRights = data.rights?.length ? data.rights : DEFAULT_RIGHTS;
  const isSuper = data.youAreSuperAdmin;
  // Only a super admin may change administrators (Admin right or super admin).
  const canEdit = (user) => isSuper || !(user.superAdmin || user.rights.includes('admin'));
  const check = useTakenCheck({ username: form.username, email: form.email, phone: form.phone });
  const blocked = ['username', 'email', 'phone'].some((field) => check[field]?.taken || check[field]?.problem);

  const update = async (user, change, text) => {
    try { await api.updateUser(user.username, change); await load(); notify(text); return true; }
    catch (error) { notify(error.message, 'error'); await load(); return false; }
  };
  const remove = async (user) => {
    if (!window.confirm(`Delete the user ${user.username}? They can no longer sign in.`)) return;
    try { await api.deleteUser(user.username); await load(); notify(`User ${user.username} deleted.`); }
    catch (error) { notify(error.message, 'error'); }
  };
  const resetPassword = (user) => {
    const password = window.prompt(`New password for ${user.username} (at least 8 characters). They choose their own at the next sign-in:`);
    if (password) update(user, { password }, `Password of ${user.username} changed; they are signed out everywhere.`);
  };
  const create = async (event) => {
    event.preventDefault();
    if (blocked) return;
    setBusy(true);
    try {
      await api.createUser({ ...form, username: form.username.trim(), email: form.email.trim(), phone: form.phone.trim() });
      setFormMessage({ text: `User ${form.username.trim()} can now sign in at /login (and chooses a new password then).`, kind: 'ok' });
      setForm({ ...EMPTY_FORM, rights: form.rights });
      await load();
    } catch (error) { setFormMessage({ text: error.message, kind: 'error' }); }
    finally { setBusy(false); }
  };

  return (
    <div className="grid-2">
      <section className="card">
        <div className="card-head">
          <div><p className="eyebrow">Accounts</p><h2>Users and rights</h2>
            <p className="muted">Each account sees only the parts it has a right for. A super admin has every right and is the only one who may manage administrators; there can be several. Changing rights signs that user out.</p></div>
          <button className="btn" onClick={load}>Refresh</button>
        </div>
        {data.bootstrapLogin && <div style={{ marginBottom: 12 }}><Notice message={{ kind: 'warn', text: 'No super admin exists yet. Until you create one, /login accepts the username "admin" with the admin token as the password. Create a super admin to switch that off.' }} /></div>}
        <div className="stack">
          {!data.users.length && <div className="empty">No users yet. Create the first one on the right.</div>}
          {data.users.map((user) => (
            <div key={user.username} className="item user-item">
              <div>
                <strong>{user.fullName ? `${user.fullName} · ` : ''}{user.username}{user.superAdmin ? <span className="chip accent"> super admin</span> : null}{user.disabled ? ' (disabled)' : ''}</strong>
                <small>{user.email || <span className="field-bad">no e-mail</span>}{user.phone ? ` · ${user.phone}` : ''} · last sign-in: {formatWhen(user.lastLoginAt)} · created {formatWhen(user.createdAt)}{user.mustChangePassword ? ' · must choose a new password' : ''}</small>
              </div>
              {contact === user.username && (
                <ContactEditor user={user} onClose={() => setContact('')}
                  onSave={async (change) => { if (await update(user, change, `Details of ${user.username} saved.`)) setContact(''); }} />
              )}
              <RightsPicker compact rights={user.rights} superAdmin={user.superAdmin} allRights={allRights} canGrantAdmin={isSuper}
                onChange={(rights) => (canEdit(user) ? update(user, { rights }, `Rights of ${user.username} saved.`) : notify('Only a super admin may change an administrator.', 'error'))} />
              <div className="row">
                {isSuper && (
                  <label className="right-chip" title="Every right, and may manage administrators">
                    <input type="checkbox" checked={user.superAdmin} onChange={(e) => update(user, { superAdmin: e.target.checked, rights: user.rights }, `${user.username} is ${e.target.checked ? 'now' : 'no longer'} a super admin.`)} />
                    Super admin
                  </label>
                )}
                {canEdit(user) && <button className="btn sm quiet" onClick={() => setContact(contact === user.username ? '' : user.username)}>Name, e-mail, phone</button>}
                {canEdit(user) && <button className="btn sm quiet" onClick={() => resetPassword(user)}>Set password</button>}
                {canEdit(user) && <button className="btn sm" onClick={() => update(user, { disabled: !user.disabled }, `${user.username} ${user.disabled ? 'enabled' : 'disabled'}.`)}>{user.disabled ? 'Enable' : 'Disable'}</button>}
                {canEdit(user) && <button className="btn sm danger" onClick={() => remove(user)}>Delete</button>}
              </div>
            </div>
          ))}
        </div>
      </section>
      <form className="card stack" onSubmit={create}>
        <div><p className="eyebrow">Add</p><h2>New user</h2></div>
        <label className="field"><span>Username (to sign in)</span><input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} maxLength={64} autoComplete="off" placeholder="e.g. ali.khan" required />
          <FieldNote check={check.username} takenText="this username is already taken" /></label>
        <label className="field"><span>Full name</span><input value={form.fullName} onChange={(e) => setForm({ ...form, fullName: e.target.value })} maxLength={120} autoComplete="off" placeholder="e.g. Ali Khan" /></label>
        <label className="field"><span>E-mail</span><input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} autoComplete="off" placeholder="ali.khan@company.com" required />
          <FieldNote check={check.email} takenText="another account uses this e-mail" /></label>
        <label className="field"><span>Phone (optional)</span><input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} autoComplete="off" placeholder="+92 300 1234567" />
          <FieldNote check={check.phone} takenText="another account uses this phone" /></label>
        <label className="field"><span>First password</span><PasswordInput value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} minLength={8} autoComplete="new-password" placeholder="At least 8 characters" required />
          <small className="muted">The user chooses their own at the first sign-in.</small></label>
        <div className="field"><span>What this account may open</span>
          <RightsPicker rights={form.rights} superAdmin={form.superAdmin} allRights={allRights} canGrantAdmin={isSuper} onChange={(rights) => setForm({ ...form, rights })} />
          <small className="muted">{allRights.map((right) => `${right.label}: ${RIGHT_HELP[right.id]}`).join(' · ')}</small>
        </div>
        {isSuper && (
          <label className="right-chip"><input type="checkbox" checked={form.superAdmin} onChange={(e) => setForm({ ...form, superAdmin: e.target.checked })} /> Super admin (every right, manages administrators)</label>
        )}
        <div className="row"><button className="btn primary" type="submit" disabled={busy || blocked}>{busy ? 'Creating…' : 'Create user'}</button></div>
        <Notice message={formMessage} />
      </form>
      <TeamsCard users={data.users} notify={notify} />
      <ResetRequestsCard notify={notify} />
      <AccessReportCard notify={notify} />
    </div>
  );
}
