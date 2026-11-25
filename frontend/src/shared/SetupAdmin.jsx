// The setup admin (python -m backend.app.setup) is a super admin that ends by itself. These two say when, and lead to
// "create your own admin": a chip in the navigation bar of every page, and a banner on Home and Admin.
import { useEffect, useState } from 'react';
import './setup-admin.css';

const CREATE = '/admin?new=admin#users';

function useTimeLeft(until) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 30_000); return () => window.clearInterval(timer); }, []);
  const minutes = Math.max(0, Math.ceil((Date.parse(until) - now) / 60_000));
  const hours = Math.floor(minutes / 60);
  if (!minutes) return { text: 'now', soon: true };
  return { text: hours ? `${hours} h${minutes % 60 ? ` ${minutes % 60} min` : ''}` : `${minutes} min`, soon: hours < 2 };
}

const go = (event) => {   // already on Admin: a link that only changes the hash would not reopen the form
  if (window.location.pathname !== '/admin') return;
  event.preventDefault();
  window.history.replaceState(null, '', CREATE);
  window.location.reload();
};

export function SetupAdminChip({ me }) {
  const left = useTimeLeft(me.expiresAt);
  return (
    <a className={`sa-chip ${left.soon ? 'soon' : ''}`} href={CREATE} onClick={go} title={`This account will be disabled in ${left.text}. Click to create a new admin.`}>
      <span aria-hidden="true">⏳</span><b>{left.text}</b><span className="sa-chip-more">Create new admin</span>
    </a>
  );
}

export function SetupAdminBanner({ me }) {
  const left = useTimeLeft(me.expiresAt);
  return (
    <div className={`sa-banner ${left.soon ? 'soon' : ''}`} role="status">
      <span className="sa-ico" aria-hidden="true">⏳</span>
      <div>
        <b>This account will be disabled in {left.text}</b>
        <small>“{me.username}” is the temporary setup account ({new Date(me.expiresAt).toLocaleString()}). You can create a new admin and use that one from then on.</small>
      </div>
      <a className="btn primary" href={CREATE} onClick={go}>Create new admin →</a>
    </div>
  );
}
