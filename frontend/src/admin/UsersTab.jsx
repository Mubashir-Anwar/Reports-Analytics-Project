import { useCallback, useEffect, useState } from 'react';
import { api } from '../api.js';
import { Notice, formatWhen } from '../portal/Shell.jsx';

const ROLE_HELP = { viewer: 'Viewer — run reports', designer: 'Designer — run and change reports where folder permissions allow', admin: 'Admin — everything on /server' };

export default function UsersTab({ notify }) {
  const [data, setData] = useState({ users: [], roles: ['viewer', 'designer', 'admin'], bootstrapLogin: false });
  const [form, setForm] = useState({ username: '', password: '', role: 'viewer' });
  const [formMessage, setFormMessage] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => api.users().then(setData).catch((error) => notify(error.message, 'error')), [notify]);
  useEffect(() => { load(); }, [load]);

  const update = async (user, change, text) => {
    try { await api.updateUser(user.username, change); await load(); notify(text); }
    catch (error) { notify(error.message, 'error'); await load(); }
  };
  const remove = async (user) => {
    if (!window.confirm(`Delete the user ${user.username}? They can no longer sign in to /server.`)) return;
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
      setFormMessage({ text: `User ${form.username.trim()} can now sign in at /server.`, kind: 'ok' });
      setForm({ username: '', password: '', role: form.role });
      await load();
    } catch (error) { setFormMessage({ text: error.message, kind: 'error' }); }
    finally { setBusy(false); }
  };

  return (
    <div className="grid-2">
      <section className="card">
        <div className="card-head">
          <div><p className="eyebrow">Server accounts</p><h2>Users</h2><p className="muted">These accounts sign in at <a href="/server">/server</a>. Viewer: run reports. Designer: also create folders. Admin: also delete reports and folders.</p></div>
          <button className="btn" onClick={load}>Refresh</button>
        </div>
        {data.bootstrapLogin && <div style={{ marginBottom: 12 }}><Notice message={{ kind: 'warn', text: 'No admin user exists yet. Until you create one, /server accepts the username "admin" with the admin token as the password. Create an admin user to switch that off.' }} /></div>}
        <div className="stack">
          {!data.users.length && <div className="empty">No users yet. Create the first one on the right.</div>}
          {data.users.map((user) => (
            <div key={user.username} className="item">
              <div><strong>{user.username}{user.disabled ? ' (disabled)' : ''}</strong><small>Last sign-in: {formatWhen(user.lastLoginAt)} · created {formatWhen(user.createdAt)}</small></div>
              <div className="row">
                <select aria-label={`Role of ${user.username}`} value={user.role} onChange={(e) => update(user, { role: e.target.value }, `${user.username} is now ${e.target.value}.`)}>
                  {data.roles.map((role) => <option key={role} value={role}>{role}</option>)}
                </select>
                <button className="btn sm quiet" onClick={() => resetPassword(user)}>Reset password</button>
                <button className="btn sm" onClick={() => update(user, { disabled: !user.disabled }, `${user.username} ${user.disabled ? 'enabled' : 'disabled'}.`)}>{user.disabled ? 'Enable' : 'Disable'}</button>
                <button className="btn sm danger" onClick={() => remove(user)}>Delete</button>
              </div>
            </div>
          ))}
        </div>
      </section>
      <form className="card stack" onSubmit={create}>
        <div><p className="eyebrow">Add</p><h2>New user</h2></div>
        <label className="field"><span>Username</span><input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} maxLength={64} autoComplete="off" placeholder="e.g. ali.khan" required /></label>
        <label className="field"><span>Password</span><input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} minLength={8} autoComplete="new-password" placeholder="At least 8 characters" required /></label>
        <label className="field"><span>Role</span>
          <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>{data.roles.map((role) => <option key={role} value={role}>{ROLE_HELP[role] || role}</option>)}</select>
        </label>
        <div className="row"><button className="btn primary" type="submit" disabled={busy}>{busy ? 'Creating…' : 'Create user'}</button></div>
        <Notice message={formMessage} />
      </form>
    </div>
  );
}
