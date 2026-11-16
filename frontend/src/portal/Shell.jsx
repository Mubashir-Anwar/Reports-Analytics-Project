/** Top bar shared by the React /server and /admin pages. On phones the links fold into a menu button. */
import { useState } from 'react';
import { useAllowedApps } from '../shared/AppSwitcher.jsx';
import ThemeToggle from '../shared/ThemeToggle.jsx';

export function Topbar({ current, subtitle, children }) {
  const [open, setOpen] = useState(false);
  const { apps } = useAllowedApps(); // only the parts this account may open
  const links = apps.map((app) => [app.href, app.href === '/server' ? 'Server' : app.label]);
  return (
    <header className={`topbar ${open ? 'open' : ''}`}>
      <a className="brand" href="/"><span className="brand-mark">M</span><span>Mongo Reports<small>{subtitle}</small></span></a>
      <button type="button" className="nav-toggle" aria-expanded={open} aria-controls="main-nav" aria-label="Menu" onClick={() => setOpen(!open)}>{open ? '✕' : '☰'}</button>
      <nav className="nav" id="main-nav" aria-label="Main navigation">
        {links.map(([href, label]) => (
          <a key={href} href={href} className={href === current ? 'on' : ''} aria-current={href === current ? 'page' : undefined}>{label}</a>
        ))}
      </nav>
      <div className="topbar-side">{children}<ThemeToggle /></div>
    </header>
  );
}

/** A status line: { text, kind: '' | 'ok' | 'error' | 'warn' } or null. */
export function Notice({ message, children }) {
  if (!message?.text && !children) return null;
  return <div className={`notice ${message?.kind || ''}`} role="status">{message?.text}{children}</div>;
}

export const formatWhen = (iso) => (iso ? new Date(iso).toLocaleString() : 'never');

/** Save a Blob as a file in the browser. */
export function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 300_000);
  return url;
}
