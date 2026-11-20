// The navigation bar of every React page (Designer, Server, Admin): logo, the parts this account may open,
// the page's own buttons, theme and the account menu. The Run reports page has the same bar in plain HTML.
import { useEffect, useRef, useState } from 'react';
import '../../../ui/brand/navbar.css';
import { useBrand } from './brand.js';
import { signOut, useMe } from './session.js';
import ThemeMenu from './ThemeMenu.jsx';
import TeamBar from './TeamBar.jsx';

const ICONS = {
  designer: 'M4 20h4l10.5-10.5a2.1 2.1 0 0 0-3-3L5 17v3zM13.5 6.5l3 3',
  server: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
  analytics: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
  runner: 'M7 4.5v15l12-7.5z',
  admin: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
  api: 'M8 9l-4 3 4 3M16 9l4 3-4 3M13.5 6l-3 12',
  out: 'M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 16l4-4-4-4M14 12H4',
  brand: 'M4 20V10M10 20V4M16 20v-7M3 3h18',
  key: 'M15 7a4 4 0 1 1-3.9 4.9L4 19v2h3v-2h2v-2h2l1.1-1.1A4 4 0 0 1 15 7z',
};

export function Icon({ name, size = 16 }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={ICONS[name]} />
    </svg>
  );
}

/** The product mark (same drawing as /assets/brand/logo.svg): a circle, a bar and a tall bar whose overlaps multiply. */
export function Logo({ size = 30, className = 'fx-logo' }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <g style={{ isolation: 'isolate' }}>
        <circle cx="21" cy="45" r="13" fill="#e23c8f" />
        <rect x="28" y="22" width="16" height="36" rx="8" fill="#2ea3dd" style={{ mixBlendMode: 'multiply' }} />
        <rect x="40" y="6" width="16" height="52" rx="8" fill="#f6a11a" style={{ mixBlendMode: 'multiply' }} />
      </g>
    </svg>
  );
}

export const NAV_APPS = [
  { href: '/designer', right: 'designer', label: 'Designer', icon: 'designer', text: 'Build reports and publish them' },
  { href: '/server', right: 'server', label: 'Server', icon: 'server', text: 'Folders, run, schedules, permissions' },
  { href: '/ui', right: 'runner', label: 'Run reports', icon: 'runner', text: 'Pick a report, fill in, download' },
  { href: '/analytics', right: 'analytics', label: 'Analytics', icon: 'analytics', text: 'Dashboards and models' },
  { href: '/admin', right: 'admin', label: 'Admin', icon: 'admin', text: 'Users, API keys, data sources, system DB' },
];

const initials = (name = '') => name.split(/[\s._-]+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join('').toUpperCase() || '?';

function UserMenu({ me }) {
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
  if (!me) return null;
  const isAdmin = me.rights?.includes('admin');
  return (
    <div className="fx-user" ref={box}>
      <button type="button" className="fx-user-btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)} title={`Signed in as ${me.username}`}>
        <span className="fx-avatar" aria-hidden="true">{initials(me.username)}</span>
        <span className="fx-user-name">{me.username}</span><span className="fx-caret" aria-hidden="true">▾</span>
      </button>
      {open && (
        <div className="fx-menu" role="menu">
          <div className="fx-menu-head">
            <span className="fx-avatar" aria-hidden="true">{initials(me.username)}</span>
            <div><b>{me.username}</b><small>{(me.rights || []).length} of 5 parts</small><br /><span className="fx-badge">{me.superAdmin ? 'Super admin' : isAdmin ? 'Admin' : me.role === 'designer' ? 'Designer' : 'User'}</span></div>
          </div>
          {isAdmin && <a role="menuitem" href="/admin#developer"><Icon name="api" />API, Postman and curl</a>}
          {isAdmin && <a role="menuitem" href="/admin#keys"><Icon name="key" />API keys</a>}
          {me.superAdmin && <a role="menuitem" href="/admin#branding"><Icon name="brand" />Name and logo</a>}
          {isAdmin && <div className="fx-sep" />}
          <button type="button" role="menuitem" className="fx-danger" onClick={signOut}><Icon name="out" />Sign out</button>
        </div>
      )}
    </div>
  );
}

/**
 * current: this page's path ('/designer', '/server', '/admin'); context: optional text next to the links
 * (the report name in the Designer); children: the page's own buttons on the right.
 */
export default function NavBar({ current, context, children }) {
  const [open, setOpen] = useState(false);
  const me = useMe();
  const brand = useBrand();
  const apps = me ? NAV_APPS.filter((app) => me.rights?.includes(app.right)) : [];
  const here = NAV_APPS.find((app) => app.href === current);
  return (
    <header className={`fx-nav ${open ? 'open' : ''}`}>
      <a className="fx-brand" href="/" title={`${brand.name} — home`}>
        <Logo />
        <span className="fx-name"><b>{brand.first}{brand.second && <span>{brand.second}</span>}</b><small>{here ? here.label : brand.tagline}</small></span>
      </a>
      <button type="button" className="fx-icon-btn fx-burger" aria-expanded={open} aria-controls="fx-links" aria-label="Menu" onClick={() => setOpen(!open)}>{open ? '✕' : '☰'}</button>
      {apps.length > 0 && <span className="fx-divider" aria-hidden="true" />}
      <nav className="fx-links" id="fx-links" aria-label="Main navigation">
        {apps.map((app) => (
          <a key={app.href} href={app.href} className={`fx-link ${app.href === current ? 'on' : ''}`} aria-current={app.href === current ? 'page' : undefined} title={app.text}>
            <Icon name={app.icon} /><span>{app.label}</span>
          </a>
        ))}
      </nav>
      {context && <div className="fx-context">{context}</div>}
      <div className="fx-actions">
        {children}
        {me && <TeamBar app={here?.icon || 'server'} />}
        <ThemeMenu />
        <UserMenu me={me} />
      </div>
    </header>
  );
}
