import { useCallback, useEffect, useState } from 'react';
import { api } from '../api.js';
import { Notice, formatWhen } from '../portal/Shell.jsx';

const RIGHT_HELP = {
  admin: 'Users, API keys, data sources, logs',
  designer: 'Build, preview and publish reports',
  server: 'Report Server: folders, run, schedules (folder permissions apply)',
  analytics: 'Dashboards and models',
  runner: 'Run reports page and running reports by URL',
};
const DEFAULT_RIGHTS = [{ id: 'admin', label: 'Admin' }, { id: 'designer', label: 'Designer' }, { id: 'server', label: 'Report Server' },
  { id: 'analytics', label: 'Analytics' }, { id: 'runner', label: 'Run reports' }];

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

export default function UsersTab({ notify }) {
  const [data, setData] = useState({ users: [], rights: DEFAULT_RIGHTS, bootstrapLogin: false, youAreSuperAdmin: false });
  const [form, setForm] = useState({ username: '', password: '', rights: ['runner'], superAdmin: false });
  const [formMessage, setFormMessage] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => api.users().then(setData).catch((error) => notify(error.message, 'error')), [notify]);
  useEffect(() => { load(); }, [load]);
  const allRights = data.rights?.length ? data.rights : DEFAULT_RIGHTS;
  const isSuper = data.youAreSuperAdmin;
  // Only a super admin may change administrators (Admin right or super admin).
  const canEdit = (user) => isSuper || !(user.superAdmin || user.rights.includes('admin'));

  const update = async (user, change, text) => {
    try { await api.updateUser(user.username, change); await load(); notify(text); }
    catch (error) { notify(error.message, 'error'); await load(); }
  };
  const remove = async (user) => {
    if (!window.confirm(`Delete the user ${user.username}? They can no longer sign in.`)) return;
    try { await api.deleteUser(user.username); await load(); notify(`User ${user.username} deleted.`); }
    catch (error) { notify(error.message, 'error'); }
  };
  const resetPassword = (user) => {
    const password = window.prompt(`New password for ${user.username} (at least 8 characters):`);
    if (password) update(user, { password }, `Password of ${user.username} changed; they are signed out everywhere.`);
  };
  const create = async (event) => {
    event.preventDefault();
    setBusy(true);
    try {
      await api.createUser({ ...form, username: form.username.trim() });
      setFormMessage({ text: `User ${form.username.trim()} can now sign in at /login.`, kind: 'ok' });
      setForm({ username: '', password: '', rights: form.rights, superAdmin: false });
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
                <strong>{user.username}{user.superAdmin ? <span className="chip accent"> super admin</span> : null}{user.disabled ? ' (disabled)' : ''}</strong>
                <small>Last sign-in: {formatWhen(user.lastLoginAt)} · created {formatWhen(user.createdAt)}</small>
              </div>
              <RightsPicker compact rights={user.rights} superAdmin={user.superAdmin} allRights={allRights} canGrantAdmin={isSuper}
                onChange={(rights) => (canEdit(user) ? update(user, { rights }, `Rights of ${user.username} saved.`) : notify('Only a super admin may change an administrator.', 'error'))} />
              <div className="row">
                {isSuper && (
                  <label className="right-chip" title="Every right, and may manage administrators">
                    <input type="checkbox" checked={user.superAdmin} onChange={(e) => update(user, { superAdmin: e.target.checked, rights: user.rights }, `${user.username} is ${e.target.checked ? 'now' : 'no longer'} a super admin.`)} />
                    Super admin
                  </label>
                )}
                {canEdit(user) && <button className="btn sm quiet" onClick={() => resetPassword(user)}>Reset password</button>}
                {canEdit(user) && <button className="btn sm" onClick={() => update(user, { disabled: !user.disabled }, `${user.username} ${user.disabled ? 'enabled' : 'disabled'}.`)}>{user.disabled ? 'Enable' : 'Disable'}</button>}
                {canEdit(user) && <button className="btn sm danger" onClick={() => remove(user)}>Delete</button>}
              </div>
            </div>
          ))}
        </div>
      </section>
      <form className="card stack" onSubmit={create}>
        <div><p className="eyebrow">Add</p><h2>New user</h2></div>
        <label className="field"><span>Username</span><input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} maxLength={64} autoComplete="off" placeholder="e.g. ali.khan" required /></label>
        <label className="field"><span>Password</span><input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} minLength={8} autoComplete="new-password" placeholder="At least 8 characters" required /></label>
        <div className="field"><span>What this account may open</span>
          <RightsPicker rights={form.rights} superAdmin={form.superAdmin} allRights={allRights} canGrantAdmin={isSuper} onChange={(rights) => setForm({ ...form, rights })} />
          <small className="muted">{allRights.map((right) => `${right.label}: ${RIGHT_HELP[right.id]}`).join(' · ')}</small>
        </div>
        {isSuper && (
          <label className="right-chip"><input type="checkbox" checked={form.superAdmin} onChange={(e) => setForm({ ...form, superAdmin: e.target.checked })} /> Super admin (every right, manages administrators)</label>
        )}
        <div className="row"><button className="btn primary" type="submit" disabled={busy}>{busy ? 'Creating…' : 'Create user'}</button></div>
        <Notice message={formMessage} />
      </form>
    </div>
  );
}
