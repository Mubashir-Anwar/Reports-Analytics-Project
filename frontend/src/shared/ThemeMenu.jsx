// The theme button of the navigation bar: presets (Classic, Office, Sheets, Insight, Midnight, Ocean, High contrast),
// or mode, accent colour, title bar, contrast and density one by one. Same settings on every page (ui/brand/theme.js).
import { useEffect, useRef, useState } from 'react';
import '../../../ui/brand/theme.js';

const LOOK = window.ReportingLook;
const BAR = { indigo: '#312e81', blue: '#1a4fb4', green: '#107c41', teal: '#0f5e58', orange: '#b23c0a', rose: '#9f1239', violet: '#5b21b6' };
const DARK_ACCENT = { indigo: '#818cf8', blue: '#60a5fa', green: '#4ade80', teal: '#2dd4bf', orange: '#fb923c', rose: '#fb7185', violet: '#a78bfa' };

export function useLook() {
  const [look, setLook] = useState(LOOK.read);
  useEffect(() => LOOK.subscribe(setLook), []);
  return [look, (change) => setLook(LOOK.save(change))];
}

function Swatch({ look }) {
  const dark = look.mode === 'dark' || (look.mode === 'system' && LOOK.isDark());
  const accent = look.contrast === 'high' && dark ? '#fde047' : (dark ? DARK_ACCENT : Object.fromEntries(LOOK.ACCENTS.map((a) => [a.id, a.color])))[look.accent];
  const bar = look.contrast === 'high' ? '#000' : look.bar === 'accent' ? BAR[look.accent] : '#0c1425';
  const bg = look.contrast === 'high' ? (dark ? '#000' : '#fff') : dark ? '#0b1120' : '#eef1f6';
  const panel = dark ? (look.contrast === 'high' ? '#161616' : '#1a2438') : '#ffffff';
  return (
    <span className="lk-swatch" style={{ background: bg }} aria-hidden="true">
      <i style={{ background: bar }} />
      <span><i style={{ background: panel }} /><i style={{ background: `linear-gradient(${panel} 0 55%, ${accent} 55% 75%, ${panel} 75%)` }} /></span>
    </span>
  );
}

export function LookPanel({ onDone, style }) {
  const [look, save] = useLook();
  const preset = LOOK.presetOf(look);
  return (
    <div className="lk-panel" role="dialog" aria-label="Theme" style={style}>
      <h3>Themes</h3>
      <div className="lk-presets">
        {LOOK.PRESETS.map((item) => (
          <button key={item.id} type="button" className={`lk-preset ${preset === item.id ? 'on' : ''}`} aria-pressed={preset === item.id}
            onClick={() => save(item.look)} title={item.text}>
            <Swatch look={{ ...look, ...item.look }} />
            <b>{item.label}</b><small>{item.text}</small>
          </button>
        ))}
      </div>
      <h3>Mode</h3>
      <div className="lk-seg" role="group" aria-label="Mode">
        {[['system', '◐ Auto'], ['light', '☀ Light'], ['dark', '☾ Dark']].map(([id, label]) => (
          <button key={id} type="button" className={look.mode === id ? 'on' : ''} aria-pressed={look.mode === id} onClick={() => save({ mode: id })}>{label}</button>
        ))}
      </div>
      <h3>Accent colour</h3>
      <div className="lk-row" role="group" aria-label="Accent colour">
        {LOOK.ACCENTS.map((item) => (
          <button key={item.id} type="button" className={`lk-dot ${look.accent === item.id ? 'on' : ''}`} style={{ background: item.color }}
            title={item.label} aria-label={item.label} aria-pressed={look.accent === item.id} onClick={() => save({ accent: item.id })} />
        ))}
      </div>
      <h3>Title bar</h3>
      <div className="lk-seg" role="group" aria-label="Title bar">
        {[['dark', 'Dark'], ['accent', 'Accent colour']].map(([id, label]) => (
          <button key={id} type="button" className={look.bar === id ? 'on' : ''} aria-pressed={look.bar === id} onClick={() => save({ bar: id })}>{label}</button>
        ))}
      </div>
      <h3>Spacing</h3>
      <div className="lk-seg" role="group" aria-label="Spacing">
        {[['comfortable', 'Comfortable'], ['compact', 'Compact']].map(([id, label]) => (
          <button key={id} type="button" className={look.density === id ? 'on' : ''} aria-pressed={look.density === id} onClick={() => save({ density: id })}>{label}</button>
        ))}
      </div>
      <label className="lk-check"><input type="checkbox" checked={look.contrast === 'high'} onChange={(e) => save({ contrast: e.target.checked ? 'high' : 'normal' })} />High contrast (strongest text and lines)</label>
      {onDone && <button type="button" className="btn" onClick={onDone}>Done</button>}
    </div>
  );
}

export default function ThemeMenu() {
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
    <div className="lk" ref={box}>
      <button type="button" className="fx-icon-btn" aria-haspopup="dialog" aria-expanded={open} title="Theme: colours, light or dark" aria-label="Theme" onClick={() => setOpen(!open)}>
        <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M12 3a9 9 0 1 0 0 18c1.1 0 1.7-.8 1.7-1.7 0-.5-.2-.9-.5-1.2-.3-.3-.5-.7-.5-1.2 0-.9.8-1.7 1.7-1.7H16a5 5 0 0 0 5-5C21 6.6 17 3 12 3z" />
          <circle cx="7.5" cy="11.5" r="1.2" fill="currentColor" /><circle cx="10" cy="7.5" r="1.2" fill="currentColor" /><circle cx="14.5" cy="7.5" r="1.2" fill="currentColor" />
        </svg>
      </button>
      {open && <LookPanel />}
    </div>
  );
}
