// A query or report goes from one part of the product to another (Query → Designer, Designer → Server…). Data sources
// do not go along, so the part it arrives in needs one of its own: pick one there, or connect the same database there.
import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { api } from '../api.js';
import { AREAS, DbLogo } from './adapters.jsx';
import './adapters.css';

export const SAME = '::same';   // "connect the same database there"

/** The choice for one data source: value is an id of `area`, SAME, or ''. */
export function TargetPicker({ source, area, value, onChange }) {
  return (
    <label className="aw-field"><span><DbLogo type={source.type} /> {source.name} <small>→ in {AREAS[area].name}</small></span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Choose a data source of {AREAS[area].name}…</option>
        {source.targets.map((t) => <option key={t.id} value={t.id}>{t.name}{t.database ? ` (${t.database})` : ''}</option>)}
        <option value={SAME}>＋ Connect the same database in {AREAS[area].name}</option>
      </select>
    </label>
  );
}

/** The id to use in `area` for what was picked (makes the separate data source for SAME). */
export async function takeInto(sourceId, area, value) {
  if (value === SAME) return (await api.copyConnection(sourceId, area)).connection.id;
  if (value !== sourceId) await api.useConnectionIn(sourceId, area, value);
  return value;
}

/** Asks which data source of `area` stands in for `source` ({id, name, type, suggested, targets}). */
export default function TakeAdapter({ source, area, onDone, onClose }) {
  const [value, setValue] = useState(source.suggested || source.targets[0]?.id || SAME);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const onKey = (event) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  const go = async () => {
    setBusy(true); setError('');
    try { onDone(await takeInto(source.id, area, value)); } catch (e) { setError(e.message); setBusy(false); }
  };
  return createPortal(
    <div className="aw-back" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <div className="aw-modal small" role="dialog" aria-modal="true" aria-label="Choose a data source">
        <div className="wizard-head"><h3>Which data source in {AREAS[area].name}?</h3>
          <p className="muted">Every part has its own data sources. The choice is remembered for next time.</p></div>
        <div className="wizard-form"><TargetPicker source={source} area={area} value={value} onChange={setValue} />
          {error && <p className="aw-result err">✕ {error}</p>}</div>
        <div className="aw-buttons"><span style={{ marginRight: 'auto' }} /><button type="button" className="btn" disabled={busy} onClick={onClose}>Cancel</button>
          <button type="button" className="btn primary" disabled={busy || !value} onClick={go}>{busy ? 'One moment…' : 'Continue →'}</button></div>
      </div>
    </div>, document.body);
}
