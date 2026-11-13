// The four parts of the product, reachable from every page (the designer's toolbar has no top navigation bar).
import { useEffect, useRef, useState } from 'react';

export const APPS = [
  { href: '/designer', label: 'Designer', icon: '🎨', text: 'Build reports and publish them' },
  { href: '/server', label: 'Report Server', icon: '🗄', text: 'Folders, run, schedules, permissions (sign in)' },
  { href: '/ui', label: 'Run reports', icon: '▶', text: 'The simple report runner page' },
  { href: '/admin', label: 'Admin', icon: '⚙', text: 'Users, data sources, checklist, logs (admin token)' },
];

/** "Apps ▾" menu. `current` is the path of this page; links open in the same tab (Ctrl/middle-click: new tab). */
export default function AppSwitcher({ current, className = 'btn' }) {
  const [open, setOpen] = useState(false);
  const box = useRef(null);
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
      <button type="button" className={className} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)} title="Go to the Report Server, Admin or the runner">
        ▦<span className="lbl"> Apps</span> ▾
      </button>
      {open && (
        <div className="app-menu" role="menu">
          {APPS.map((app) => (
            <a key={app.href} role="menuitem" href={app.href} className={app.href === current ? 'on' : ''} aria-current={app.href === current ? 'page' : undefined}>
              <span className="app-icon" aria-hidden="true">{app.icon}</span>
              <span><b>{app.label}</b><small>{app.text}</small></span>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
