// Ctrl+K: find any Designer action by name. The ribbon renders every tab (plus the File and Help menus) into a hidden
// box while this is open; the list is read from those buttons and choosing one clicks it, so every action keeps one
// place where its logic lives (the ribbon button) and nothing can be missing from the search.
import { useEffect, useMemo, useRef, useState } from 'react';

function readActions(root) {
  if (!root) return [];
  const seen = new Set();
  const found = [];
  root.querySelectorAll('button, label.rb-big').forEach((el) => {
    const own = el.querySelector(':scope > span')?.textContent?.trim();
    const label = (own || el.getAttribute('aria-label') || el.getAttribute('title') || el.textContent || '').trim().replace(/\s+/g, ' ');
    if (!label) return;
    const where = [el.closest('[data-cmd-tab]')?.getAttribute('data-cmd-tab'), el.closest('.rb-group')?.getAttribute('aria-label')].filter(Boolean).join(' · ');
    const key = `${label}|${where}`;
    if (seen.has(key)) return;
    seen.add(key);
    const title = el.getAttribute('title') || '';
    found.push({ key, label, where, title: title === label ? '' : title, el, off: Boolean(el.disabled) });
  });
  return found;
}

export default function CommandPalette({ sourceRef, onClose }) {
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const [items, setItems] = useState([]);
  const list = useRef(null);
  useEffect(() => { setItems(readActions(sourceRef.current)); }, [sourceRef]);
  const shown = useMemo(() => {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return items.slice(0, 60);
    return items.filter((item) => {
      const text = `${item.label} ${item.where} ${item.title}`.toLowerCase();
      return words.every((w) => text.includes(w));
    }).sort((a, b) => Number(!a.label.toLowerCase().startsWith(words[0])) - Number(!b.label.toLowerCase().startsWith(words[0]))).slice(0, 60);
  }, [items, query]);
  useEffect(() => { setActive(0); }, [query]);
  useEffect(() => { list.current?.querySelector('[aria-selected="true"]')?.scrollIntoView?.({ block: 'nearest' }); }, [active]);
  // a greyed-out action (nothing selected yet, nothing to undo…) is listed so it can be found, but does nothing
  const run = (item) => { if (!item || item.off) return; item.el.click(); onClose(); };   // click first: closing removes the hidden buttons
  const onKey = (event) => {
    if (event.key === 'Escape') { event.preventDefault(); onClose(); }
    else if (event.key === 'ArrowDown') { event.preventDefault(); setActive((i) => Math.min(shown.length - 1, i + 1)); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); setActive((i) => Math.max(0, i - 1)); }
    else if (event.key === 'Enter') { event.preventDefault(); run(shown[active]); }
  };
  return (
    <div className="cmdk-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="cmdk" role="dialog" aria-label="Search any action">
        <div className="cmdk-input">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
          <input autoFocus value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={onKey} placeholder="Type an action: border, page size, publish, parameter…"
            aria-label="Action to find" role="combobox" aria-expanded="true" aria-controls="cmdk-list" aria-activedescendant={shown[active] ? `cmdk-${active}` : undefined} />
          <kbd>Esc</kbd>
        </div>
        <ul className="cmdk-list" id="cmdk-list" role="listbox" ref={list}>
          {shown.map((item, i) => (
            <li key={item.key} id={`cmdk-${i}`} role="option" aria-selected={i === active} className={`${i === active ? 'on' : ''} ${item.off ? 'off' : ''}`}
              onMouseMove={() => setActive(i)} onMouseDown={(e) => { e.preventDefault(); run(item); }}>
              <b>{item.label}</b>{(item.off || item.title) && <small>{item.off ? 'Select something on the page first (or nothing to do yet)' : item.title}</small>}<span className="cmdk-where">{item.where}</span>
            </li>
          ))}
          {!shown.length && <li className="cmdk-none">Nothing called “{query}”. Try another word.</li>}
        </ul>
        <div className="cmdk-foot">↑ ↓ to move · Enter to do it · every button of the ribbon, the File menu and Help is here</div>
      </div>
    </div>
  );
}
