// The theme button of the navigation bar: presets (Classic, Office, Sheets, Insight, Midnight, Ocean, High contrast),
// or mode, accent colour, title bar, contrast and density one by one. Same settings on every page (ui/brand/theme.js).
import { useEffect, useRef, useState } from 'react';
import '../../../ui/brand/theme.js';

const LOOK = window.ReportingLook;
const BAR = { bright: '#2340d6', indigo: '#312e81', blue: '#1a4fb4', green: '#107c41', teal: '#0f5e58', orange: '#b23c0a', rose: '#9f1239', violet: '#5b21b6' };
const DARK_ACCENT = { bright: '#7d94ff', indigo: '#818cf8', blue: '#60a5fa', green: '#4ade80', teal: '#2dd4bf', orange: '#fb923c', rose: '#fb7185', violet: '#a78bfa' };

export function useLook() {
  const [look, setLook] = useState(LOOK.read);
  useEffect(() => LOOK.subscribe(setLook), []);
  return [look, (change) => setLook(LOOK.save(change))];
}

function colours(look) {
  const dark = look.mode === 'dark' || (look.mode === 'system' && LOOK.isDark());
  const accent = look.contrast === 'high' && dark ? '#fde047' : (dark ? DARK_ACCENT : Object.fromEntries(LOOK.ACCENTS.map((a) => [a.id, a.color])))[look.accent];
  const bar = look.contrast === 'high' ? '#000' : look.bar === 'accent' ? BAR[look.accent] : '#0c1425';
  const bg = look.contrast === 'high' ? (dark ? '#000' : '#fff') : dark ? '#0f1117' : '#f6f7fb';
  const panel = dark ? (look.contrast === 'high' ? '#161616' : '#171a23') : '#ffffff';
  const ink = dark ? '#e8ebf5' : '#0f172a';
  return { dark, accent, bar, bg, panel, ink };
}

const MINI = [3, 5, 4, 7, 6];   // the little pixel chart in every swatch

/** A tiny screen of the look: title bar, side list and a pixel bar chart in the accent colour. */
function Swatch({ look, big = false }) {
  const c = colours(look);
  return (
    <span className={`lk-swatch ${big ? 'big' : ''}`} style={{ background: c.bg, '--sw-accent': c.accent, '--sw-panel': c.panel, '--sw-ink': c.ink }} aria-hidden="true">
      <i className="lk-sw-bar" style={{ background: c.bar }}><b /><b /><b /></i>
      <span className="lk-sw-body">
        <i className="lk-sw-side" />
        <span className="lk-sw-chart">
          {MINI.map((h, col) => (
            <span key={col} className="lk-sw-col">
              {Array.from({ length: 7 }, (_, row) => <i key={row} className={6 - row < h ? 'on' : ''} style={{ '--d': `${(col * 7 + row) * 12}ms` }} />)}
            </span>
          ))}
        </span>
      </span>
    </span>
  );
}

function Seg({ label, value, options, onPick }) {
  const at = Math.max(0, options.findIndex(([id]) => id === value));
  return (
    <div className="lk-seg" role="group" aria-label={label} style={{ '--n': options.length, '--at': at }}>
      <span className="lk-seg-block" aria-hidden="true" />
      {options.map(([id, text]) => (
        <button key={id} type="button" className={value === id ? 'on' : ''} aria-pressed={value === id} onClick={() => onPick(id)}>{text}</button>
      ))}
    </div>
  );
}

export function LookPanel({ onDone, style }) {
  const [look, save] = useLook();
  const preset = LOOK.presetOf(look);
  const [pop, setPop] = useState(0);        // replays the pixel flash on the preview after each change
  const change = (value) => { save(value); setPop((n) => n + 1); };
  const current = LOOK.PRESETS.find((item) => item.id === preset);
  const accentName = LOOK.ACCENTS.find((item) => item.id === look.accent)?.label || look.accent;
  return (
    <div className="lk-panel px-in" role="dialog" aria-label="Theme" style={style}>
      <div className="lk-head">
        <p className="lk-kicker"><b>◆</b> Your vibe<span className="lk-caret" aria-hidden="true" /></p>
        <div className="lk-now" key={pop}>
          <Swatch look={look} big />
          <div><b>{current ? current.label : 'Custom mix'}</b><small>{colours(look).dark ? '☾ dark' : '☀ light'} · {accentName.toLowerCase()} · {look.density}</small></div>
        </div>
      </div>
      <h3><span>01</span> Themes</h3>
      <div className="lk-presets">
        {LOOK.PRESETS.map((item, i) => (
          <button key={item.id} type="button" className={`lk-preset ${preset === item.id ? 'on' : ''}`} aria-pressed={preset === item.id}
            onClick={() => change(item.look)} title={item.text} style={{ '--i': i }}>
            <Swatch look={{ ...look, ...item.look }} />
            <b>{item.label}</b><small>{item.text}</small>
            {preset === item.id && <em className="lk-on-tag" aria-hidden="true">✓ on</em>}
          </button>
        ))}
      </div>
      <h3><span>02</span> Mode</h3>
      <Seg label="Mode" value={look.mode} onPick={(id) => change({ mode: id })} options={[['system', '◐ Auto'], ['light', '☀ Light'], ['dark', '☾ Dark']]} />
      <h3><span>03</span> Accent</h3>
      <div className="lk-row" role="group" aria-label="Accent colour">
        {LOOK.ACCENTS.map((item) => (
          <button key={item.id} type="button" className={`lk-dot ${look.accent === item.id ? 'on' : ''}`} style={{ '--dot': item.color }}
            title={item.label} aria-label={item.label} aria-pressed={look.accent === item.id} onClick={() => change({ accent: item.id })} />
        ))}
      </div>
      <h3><span>04</span> Title bar</h3>
      <Seg label="Title bar" value={look.bar} onPick={(id) => change({ bar: id })} options={[['dark', 'Dark'], ['accent', 'Accent']]} />
      <h3><span>05</span> Spacing</h3>
      <Seg label="Spacing" value={look.density} onPick={(id) => change({ density: id })} options={[['comfortable', 'Comfy'], ['compact', 'Compact']]} />
      <label className="lk-switch">
        <input type="checkbox" checked={look.contrast === 'high'} onChange={(e) => change({ contrast: e.target.checked ? 'high' : 'normal' })} />
        <span className="lk-switch-track" aria-hidden="true"><i /></span>
        <span>High contrast <small>strongest text and lines</small></span>
      </label>
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
