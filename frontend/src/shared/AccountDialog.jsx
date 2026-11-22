// My account: your own name, e-mail and phone, and a new password (the current one first).
// Opened from the user menu; opened by itself after an administrator set your password (mustChangePassword).
import { useEffect, useState } from 'react';
import PasswordInput from './PasswordInput.jsx';

async function call(path, method = 'GET', body) {
  const response = await fetch(path, { method, credentials: 'same-origin', headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : data.detail?.message || `HTTP ${response.status}`);
  return data;
}

export const accountApi = {
  get: () => call('/api/server/account'),
  save: (change) => call('/api/server/account', 'PUT', change),
  password: (currentPassword, newPassword) => call('/api/server/account/password', 'POST', { currentPassword, newPassword }),
};

export default function AccountDialog({ onClose, forced = false }) {
  const [account, setAccount] = useState(null);
  const [form, setForm] = useState({ fullName: '', email: '', phone: '' });
  const [pw, setPw] = useState({ current: '', next: '', again: '' });
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState('');

  useEffect(() => {
    accountApi.get().then((data) => { setAccount(data); setForm({ fullName: data.fullName, email: data.email, phone: data.phone }); })
      .catch((error) => setMessage({ kind: 'error', text: error.message }));
  }, []);

  const saveProfile = async (event) => {
    event.preventDefault();
    setBusy('profile');
    try { setAccount(await accountApi.save(form)); setMessage({ kind: 'ok', text: 'Saved.' }); }
    catch (error) { setMessage({ kind: 'error', text: error.message }); }
    finally { setBusy(''); }
  };
  const savePassword = async (event) => {
    event.preventDefault();
    if (pw.next !== pw.again) { setMessage({ kind: 'error', text: 'The two new passwords are not the same.' }); return; }
    setBusy('password');
    try {
      await accountApi.password(pw.current, pw.next);
      setPw({ current: '', next: '', again: '' });
      setAccount({ ...account, mustChangePassword: false });
      setMessage({ kind: 'ok', text: 'Your password is changed.' });
    } catch (error) { setMessage({ kind: 'error', text: error.message }); }
    finally { setBusy(''); }
  };
  const mustChange = forced && account?.mustChangePassword;

  return (
    <div className="dialog-back" onMouseDown={(e) => e.target === e.currentTarget && !mustChange && onClose()}>
      <div className="card dialog account-dialog" role="dialog" aria-label="My account">
        <div className="card-head">
          <div><p className="eyebrow">My account</p><h2>{account ? account.username : 'Loading…'}</h2>
            {account && <p className="muted">{account.superAdmin ? 'Super admin' : `Rights: ${account.rights.join(', ') || 'none'}`}{account.groups.length ? ` · Teams: ${account.groups.join(', ')}` : ''}</p>}</div>
          {!mustChange && <button className="btn quiet sm" onClick={onClose} aria-label="Close">✕</button>}
        </div>
        {mustChange && <p className="notice warn">An administrator set your password. Choose your own now.</p>}
        {account && !mustChange && (
          <form className="stack" onSubmit={saveProfile}>
            <div className="form-grid-2">
              <label className="field"><span>Full name</span><input value={form.fullName} onChange={(e) => setForm({ ...form, fullName: e.target.value })} maxLength={120} autoComplete="name" /></label>
              <label className="field"><span>E-mail</span><input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required autoComplete="email" /></label>
              <label className="field"><span>Phone (optional)</span><input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} autoComplete="tel" placeholder="+92 300 1234567" /></label>
            </div>
            <div className="row"><button className="btn" type="submit" disabled={busy === 'profile'}>{busy === 'profile' ? 'Saving…' : 'Save my details'}</button></div>
          </form>
        )}
        {account && (
          <form className="stack" onSubmit={savePassword}>
            <h3>Change password</h3>
            <div className="form-grid-2">
              <label className="field"><span>Current password</span><PasswordInput value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} autoComplete="current-password" required /></label>
              <span />
              <label className="field"><span>New password</span><PasswordInput value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} autoComplete="new-password" minLength={8} required /></label>
              <label className="field"><span>New password again</span><PasswordInput value={pw.again} onChange={(e) => setPw({ ...pw, again: e.target.value })} autoComplete="new-password" minLength={8} required /></label>
            </div>
            <div className="row"><button className="btn primary" type="submit" disabled={busy === 'password'}>{busy === 'password' ? 'Changing…' : 'Change password'}</button></div>
          </form>
        )}
        {message && <p className={`notice ${message.kind === 'error' ? 'error' : message.kind}`} role="status">{message.text}</p>}
      </div>
    </div>
  );
}
