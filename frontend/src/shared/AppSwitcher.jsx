// The parts of the product. Each page shows only the parts the signed-in account has the right for.
import { useEffect, useRef, useState } from 'react';
import { signOut, useMe } from './session.js';

export const APPS = [
  { href: '/designer', right: 'designer', label: 'Designer', icon: '🎨', text: 'Build reports and publish them' },
  { href: '/query', right: 'query', label: 'Query', icon: '🔎', text: 'Browse data sources and try queries' },
  { href: '/server', right: 'server', label: 'Report Server', icon: '🗄', text: 'Folders, run, schedules, permissions' },
  { href: '/analytics', right: 'analytics', label: 'Analytics', icon: '📊', text: 'Dashboards and models' },
  { href: '/admin', right: 'admin', label: 'Admin', icon: '⚙', text: 'Users, rights, API keys, data sources, logs' },
];

/** The apps this account may open ([] while asking or when signed out). */
export function useAllowedApps() {
  const me = useMe();
  return { me, apps: me ? APPS.filter((app) => me.rights?.includes(app.right)) : [] };
}

/** "Apps ▾" menu. `current` is the path of this page; links open in the same tab (Ctrl/middle-click: new tab). */
export default function AppSwitcher({ current, className = 'btn' }) {
  const [open, setOpen] = useState(false);
  const box = useRef(null);
  const { me, apps } = useAllowedApps();
  useEffect(() => {
    if (!open) return undefined;
    const close = (event) => { if (!box.current?.contains(event.target)) setOpen(false); };
    const escape = (event) => { if (event.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', escape); };
  }, [open]);
  return (
    <div className="app-switcher" ref={box}>
      <button type="button" className={className} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)} title={me ? `Signed in as ${me.username}` : 'Apps'}>
        ▦<span className="lbl"> Apps</span> ▾
      </button>
      {open && (
        <div className="app-menu" role="menu">
          {me && <div className="app-who">Signed in as <b>{me.username}</b>{me.superAdmin ? ' · super admin' : ''}</div>}
          {apps.map((app) => (
            <a key={app.href} role="menuitem" href={app.href} className={app.href === current ? 'on' : ''} aria-current={app.href === current ? 'page' : undefined}>
              <span className="app-icon" aria-hidden="true">{app.icon}</span>
              <span><b>{app.label}</b><small>{app.text}</small></span>
            </a>
          ))}
          {me && <button type="button" role="menuitem" className="app-signout" onClick={signOut}>Sign out</button>}
        </div>
      )}
    </div>
  );
}
