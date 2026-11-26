// The navigation bar of every React page (Designer, Server, Admin): logo, the parts this account may open,
// the page's own buttons, theme and the account menu.
import { useEffect, useRef, useState } from 'react';
import '../../../ui/brand/navbar.css';
import { useBrand } from './brand.js';
import { signOut, useMe } from './session.js';
import AccountDialog from './AccountDialog.jsx';
import ThemeMenu from './ThemeMenu.jsx';
import TeamBar from './TeamBar.jsx';
import { SetupAdminChip } from './SetupAdmin.jsx';
import { copyrightLine } from './SiteFooter.jsx';

const ICONS = {
  home: 'M3 11l9-8 9 8M5.5 9.5V20h13V9.5M10 20v-6h4v6',
  designer: 'M4 20h4l10.5-10.5a2.1 2.1 0 0 0-3-3L5 17v3zM13.5 6.5l3 3',
  server: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
  analytics: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
  runner: 'M7 4.5v15l12-7.5z',
  admin: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
  api: 'M8 9l-4 3 4 3M16 9l4 3-4 3M13.5 6l-3 12',
  query: 'M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v6c0 1.7 3.6 3 8 3M20 6v4M4 12v6c0 1.7 3.6 3 8 3M15 18l2 2 4-4',
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

/** The product mark (same drawing as /assets/brand/logo.svg): PixelFX rising pixel bars on a dark tile. */
const MARK_CELLS = [[5,5,'#222842'], [5,12,'#222842'], [5,19,'#222842'], [5,26,'#222842'], [5,33,'#222842'], [5,40,'#7b8cff'], [5,47,'#7b8cff'], [5,54,'#7b8cff'], [12,5,'#222842'], [12,12,'#222842'], [12,19,'#222842'], [12,26,'#222842'], [12,33,'#222842'], [12,40,'#7b8cff'], [12,47,'#7b8cff'], [12,54,'#7b8cff'], [19,5,'#222842'], [19,12,'#222842'], [19,19,'#222842'], [19,26,'#222842'], [19,33,'#222842'], [19,40,'#222842'], [19,47,'#222842'], [19,54,'#222842'], [26,5,'#222842'], [26,12,'#222842'], [26,19,'#222842'], [26,26,'#7b8cff'], [26,33,'#7b8cff'], [26,40,'#7b8cff'], [26,47,'#7b8cff'], [26,54,'#7b8cff'], [33,5,'#222842'], [33,12,'#ffd84d'], [33,19,'#222842'], [33,26,'#7b8cff'], [33,33,'#7b8cff'], [33,40,'#7b8cff'], [33,47,'#7b8cff'], [33,54,'#7b8cff'], [40,5,'#222842'], [40,12,'#222842'], [40,19,'#222842'], [40,26,'#222842'], [40,33,'#222842'], [40,40,'#222842'], [40,47,'#222842'], [40,54,'#222842'], [47,5,'#222842'], [47,12,'#ff4f9a'], [47,19,'#7b8cff'], [47,26,'#7b8cff'], [47,33,'#7b8cff'], [47,40,'#7b8cff'], [47,47,'#7b8cff'], [47,54,'#7b8cff'], [54,5,'#222842'], [54,12,'#ff4f9a'], [54,19,'#7b8cff'], [54,26,'#7b8cff'], [54,33,'#7b8cff'], [54,40,'#7b8cff'], [54,47,'#7b8cff'], [54,54,'#7b8cff']];
export function Logo({ size = 30, className = 'fx-logo', src }) {
  const brand = useBrand();
  const own = src === undefined ? brand.logo : src;
  if (own) return <img className={`${className} fx-logo-own`} src={own} width={size} height={size} alt="" aria-hidden="true" style={{ objectFit: 'contain' }} />;
  return (
    <svg className={`${className} fx-logo-px`} width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <rect width="64" height="64" rx="14" fill="#0d1020" />
      {MARK_CELLS.map(([x, y, fill], i) => <rect key={i} x={x} y={y} width="6" height="6" fill={fill} style={fill === '#222842' ? undefined : { '--i': i % 8 }} />)}
    </svg>
  );
}

const HOME = { href: '/home', label: 'Home', icon: 'home', text: 'What you can do here' };
export const NAV_APPS = [
  { href: '/designer', right: 'designer', label: 'Designer', icon: 'designer', text: 'Build reports and publish them' },
  { href: '/server', right: 'server', label: 'Server', icon: 'server', text: 'Folders, run, schedules, permissions' },
  { href: '/query', right: 'query', label: 'Query', icon: 'query', text: 'Browse data sources and try queries' },
  { href: '/analytics', right: 'analytics', label: 'Analytics', icon: 'analytics', text: 'Dashboards and models' },
  { href: '/admin', right: 'admin', label: 'Admin', icon: 'admin', text: 'Users, API keys, data sources, system DB' },
];

// Slim bar: icons only and a lower bar, so the Designer canvas gets more room. Kept per browser.
const readSlim = () => { try { return localStorage.getItem('nav-slim') === '1'; } catch { return false; } };
const showSlim = (slim) => { document.documentElement.dataset.nav = slim ? 'slim' : ''; };
showSlim(readSlim());

const initials = (name = '') => name.split(/[\s._-]+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join('').toUpperCase() || '?';

function UserMenu({ me }) {
  const brand = useBrand();
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
  const [account, setAccount] = useState(null);   // null | 'open' | 'forced' (an admin set the password)
  useEffect(() => {
    let must = false;
    try { must = sessionStorage.getItem('must-change-password') === '1'; sessionStorage.removeItem('must-change-password'); } catch { /* private mode */ }
    if (must) setAccount('forced');
  }, []);
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
          <button type="button" role="menuitem" onClick={() => { setOpen(false); setAccount('open'); }}><Icon name="key" />My account and password</button>
          {isAdmin && <a role="menuitem" href="/admin#developer"><Icon name="api" />API, Postman and curl</a>}
          {isAdmin && <a role="menuitem" href="/admin#keys"><Icon name="key" />API keys</a>}
          {me.superAdmin && <a role="menuitem" href="/admin#branding"><Icon name="brand" />Name and logo</a>}
          {isAdmin && <div className="fx-sep" />}
          <button type="button" role="menuitem" className="fx-danger" onClick={signOut}><Icon name="out" />Sign out</button>
          <div className="fx-menu-copy">{copyrightLine(brand.name)}</div>
        </div>
      )}
      {account && <AccountDialog forced={account === 'forced'} onClose={() => setAccount(null)} />}
    </div>
  );
}

/**
 * current: this page's path ('/designer', '/server', '/admin'); context: optional text next to the links
 * (the report name in the Designer); children: the page's own buttons on the right.
 */
export default function NavBar({ current, context, children }) {
  useEffect(() => {   // Home starts with the part used last
    if (!['/designer', '/analytics', '/query', '/server'].includes(current)) return;
    try { localStorage.setItem('last-part', current.slice(1)); } catch { /* storage off */ }
  }, [current]);
  const [open, setOpen] = useState(false);
  const [slim, setSlim] = useState(readSlim);
  useEffect(() => {
    showSlim(slim);
    try { localStorage.setItem('nav-slim', slim ? '1' : '0'); } catch { /* storage off */ }
  }, [slim]);
  const me = useMe();
  const brand = useBrand();
  const apps = me ? [HOME, ...NAV_APPS.filter((app) => me.rights?.includes(app.right))] : [];
  const here = [HOME, ...NAV_APPS].find((app) => app.href === current);
  return (
    <header className={`fx-nav ${open ? 'open' : ''} ${slim ? 'slim' : ''}`}>
      <a className="fx-brand" href="/" title={`${brand.name} — home`}>
        <Logo />
        <span className="fx-name"><b>{brand.first}{brand.second && <span>{brand.second}</span>}</b><small>{here ? here.label : brand.tagline}</small></span>
      </a>
      {apps.length > 0 && <button type="button" className="fx-icon-btn fx-burger" aria-expanded={open} aria-controls="fx-links" aria-label="Menu" onClick={() => setOpen(!open)}>{open ? '✕' : '☰'}</button>}
      {apps.length > 0 && <span className="fx-divider" aria-hidden="true" />}
      <nav className="fx-links" id="fx-links" aria-label="Main navigation">
        {apps.map((app) => (
          <a key={app.href} href={app.href} className={`fx-link ${app.href === current ? 'on' : ''}`} aria-current={app.href === current ? 'page' : undefined} title={slim ? `${app.label}: ${app.text}` : app.text}>
            <Icon name={app.icon} /><span>{app.label}</span>
          </a>
        ))}
      </nav>
      {context && <div className="fx-context">{context}</div>}
      <div className="fx-actions">
        {children}
        {me?.temporary && <SetupAdminChip me={me} />}
        {me && <TeamBar app={here?.icon || 'server'} />}
        <ThemeMenu />
        <button type="button" className="fx-icon-btn fx-slim-btn" aria-pressed={slim} onClick={() => setSlim(!slim)}
          title={slim ? 'Full bar: names next to the icons' : 'Slim bar: icons only, more room for the page'} aria-label={slim ? 'Full navigation bar' : 'Slim navigation bar'}>
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d={slim ? 'M7 10l5 5 5-5' : 'M7 14l5-5 5 5'} />
          </svg>
        </button>
        <UserMenu me={me} />
      </div>
    </header>
  );
}
