// Light / dark / system theme for every page. The choice is kept per browser; "system" follows the OS setting.
import { useEffect, useState } from 'react';

const KEY = 'reporting-theme';
const ORDER = ['system', 'light', 'dark'];
const ICON = { system: '◐', light: '☀', dark: '☾' };
const LABEL = { system: 'Theme: follows the system', light: 'Theme: light', dark: 'Theme: dark' };

function read() {
  try { return ORDER.includes(localStorage.getItem(KEY)) ? localStorage.getItem(KEY) : 'system'; } catch { return 'system'; }
}

export function applyTheme(choice = read()) {
  if (choice === 'system') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', choice);
}

export default function ThemeToggle({ className = 'theme-toggle' }) {
  const [choice, setChoice] = useState(read);
  useEffect(() => { applyTheme(choice); try { localStorage.setItem(KEY, choice); } catch { /* private mode */ } }, [choice]);
  const next = ORDER[(ORDER.indexOf(choice) + 1) % ORDER.length];
  return (
    <button type="button" className={className} onClick={() => setChoice(next)} title={`${LABEL[choice]} — click for ${next}`} aria-label={LABEL[choice]}>
      {ICON[choice]}
    </button>
  );
}

applyTheme(); // before the first paint of the page that imports this module
