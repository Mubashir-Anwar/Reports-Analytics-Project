// My account: your own name, e-mail and phone, and a new password (the current one first).
// Opened from the user menu; opened by itself after an administrator set your password (mustChangePassword).
import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
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
  const [done, setDone] = useState(false);

  useEffect(() => {
    accountApi.get().then((data) => { setAccount(data); setForm({ fullName: data.fullName, email: data.email, phone: data.phone }); })
      .catch((error) => setMessage({ kind: 'error', text: error.message }));
  }, []);

  const saveProfile = async (event) => {
    event.preventDefault();
    setBusy('profile');
    try { setAccount(await accountApi.save(form)); setMessage({ tab: 'profile', kind: 'ok', text: 'Saved.' }); }
    catch (error) { setMessage({ tab: 'profile', kind: 'error', text: error.message }); }
    finally { setBusy(''); }
  };
  const savePassword = async (event) => {
    event.preventDefault();
    if (pw.next !== pw.again) { setMessage({ tab: 'password', kind: 'error', text: 'The two new passwords are not the same.' }); return; }
    setBusy('password');
    try {
      await accountApi.password(pw.current, pw.next);
      setPw({ current: '', next: '', again: '' });
      setDone(true);                                    // nothing left to do here: the dialog closes
    } catch (error) { setMessage({ tab: 'password', kind: 'error', text: error.message }); }
    finally { setBusy(''); }
  };
  const mustChange = forced && account?.mustChangePassword;
  useEffect(() => {
    const escape = (event) => { if (event.key === 'Escape' && !mustChange) onClose(); };
    document.addEventListener('keydown', escape);
    return () => document.removeEventListener('keydown', escape);
  }, [mustChange, onClose]);
  useEffect(() => {
    if (!done) return undefined;
    const timer = setTimeout(onClose, 2600);
    return () => clearTimeout(timer);
  }, [done, onClose]);

  if (done) return createPortal(<div className="acct-done" role="status"><span aria-hidden="true">✓</span>Your password is changed.</div>, document.body);
  const note = (tab) => message && message.tab === tab && <p className={`acct-note ${message.kind}`} role="status">{message.text}</p>;

  return createPortal(   // on <body>: inside the dark navigation bar its light text colours would apply
    <div className="acct-back" onMouseDown={(e) => e.target === e.currentTarget && !mustChange && onClose()}>
      <div className="acct" role="dialog" aria-modal="true" aria-label={mustChange ? 'Choose your own password' : 'My account'}>
        <header className="acct-head">
          <span className="acct-avatar" aria-hidden="true">{(account?.username || '?').slice(0, 2).toUpperCase()}</span>
          <div><small>{mustChange ? 'Choose your own password' : 'My account'}</small><h2>{account ? account.username : 'Loading…'}</h2>
            {account && !mustChange && <div className="acct-chips">{account.superAdmin ? <span>Super admin</span> : account.rights.map((right) => <span key={right}>{right}</span>)}{account.groups.map((group) => <span key={`g${group}`}>Team: {group}</span>)}</div>}</div>
          {!mustChange && <button type="button" className="acct-x" onClick={onClose} aria-label="Close">✕</button>}
        </header>
        {!account && message && <p className={`acct-note ${message.kind}`} role="status">{message.text}</p>}
        {mustChange && <p className="acct-note warn">An administrator set your password. Choose your own to go on.</p>}
        {account && !mustChange && (
          <form className="acct-part" onSubmit={saveProfile}>
            <h3>My details</h3>
            <div className="acct-grid">
              <label><span>Full name</span><input value={form.fullName} onChange={(e) => setForm({ ...form, fullName: e.target.value })} maxLength={120} autoComplete="name" /></label>
              <label><span>E-mail</span><input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required autoComplete="email" /></label>
              <label><span>Phone (optional)</span><input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} autoComplete="tel" placeholder="+92 300 1234567" /></label>
            </div>
            {note('profile')}
            <button className="acct-btn" type="submit" disabled={busy === 'profile'}>{busy === 'profile' ? 'Saving…' : 'Save my details'}</button>
          </form>
        )}
        {account && (
          <form className="acct-part" onSubmit={savePassword}>
            {!mustChange && <h3>Change password</h3>}
            <div className="acct-grid">
              <label className="wide"><span>Current password</span><PasswordInput value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} autoComplete="current-password" required autoFocus={mustChange} /></label>
              <label><span>New password</span><PasswordInput value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} autoComplete="new-password" minLength={8} required /></label>
              <label><span>New password again</span><PasswordInput value={pw.again} onChange={(e) => setPw({ ...pw, again: e.target.value })} autoComplete="new-password" minLength={8} required /></label>
            </div>
            <small className="acct-hint">At least 8 characters.</small>
            {note('password')}
            <button className="acct-btn primary" type="submit" disabled={busy === 'password'}>{busy === 'password' ? 'Changing…' : 'Change password'}</button>
          </form>
        )}
      </div>
    </div>, document.body);
}
