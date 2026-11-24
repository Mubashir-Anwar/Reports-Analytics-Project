// /admin → Limits: how many reports at once, rows and time limits, downloads, analytics and API keys
// (GET / PUT /api/admin/limits). A saved value wins over the environment variable and works without a restart;
// "Reset" goes back to the environment variable (or the built-in default).
import { useCallback, useEffect, useMemo, useState } from 'react';
import { request } from '../api.js';

const GROUP_LOOK = {
  reports: { icon: '📄', tint: 'b', hint: 'Protect the server when many people run reports at once.' },
  downloads: { icon: '📦', tint: 'g', hint: 'Files asked for with X-Deliver: later.' },
  analytics: { icon: '📊', tint: 'c', hint: 'Dataset imports and dashboard queries (DuckDB).' },
  api: { icon: '🔑', tint: 'v', hint: 'API keys and request sizes.' },
};
const SOURCE_LABEL = { admin: 'Set here', environment: 'From .env', default: 'Default' };
const fmt = (value) => (typeof value === 'number' ? value.toLocaleString() : String(value ?? ''));

export default function LimitsTab({ notify }) {
  const [data, setData] = useState(null);
  const [draft, setDraft] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(() => request('/api/admin/limits').then((value) => { setData(value); setDraft({}); setError(''); })
    .catch((failure) => setError(failure.message)), []);
  useEffect(() => { load(); }, [load]);

  const byGroup = useMemo(() => {
    const groups = {};
    (data?.limits || []).forEach((item) => { (groups[item.group] ||= []).push(item); });
    return groups;
  }, [data]);

  if (error) return <section className="card"><p className="muted">{error}</p></section>;
  if (!data) return <section className="card"><p className="muted">Loading…</p></section>;

  const dirty = Object.keys(draft);
  const problem = (item, raw) => {
    if (raw === undefined || raw === null) return '';
    const text = String(raw).trim();
    if (item.kind === 'memory') return /^\d{1,6}\s*(KB|MB|GB|TB)$/i.test(text) ? '' : 'Like 512MB or 2GB';
    if (!/^\d+$/.test(text)) return 'A whole number';
    const number = Number(text);
    return number < item.low || number > item.high ? `${item.low.toLocaleString()} – ${item.high.toLocaleString()}` : '';
  };
  const invalid = dirty.some((key) => draft[key] !== null && problem(data.limits.find((item) => item.key === key), draft[key]));

  const save = async (values) => {
    setBusy(true);
    try {
      const value = await request('/api/admin/limits', { method: 'PUT', body: { values } });
      setData(value); setDraft({});
      notify(`Saved. Every server uses the new limits within ${value.liveSeconds} seconds, no restart needed.`, 'ok');
    } catch (failure) { notify(failure.message, 'error'); } finally { setBusy(false); }
  };

  return (
    <div className="lim">
      <section className="card lim-intro">
        <div>
          <p className="eyebrow">Live limits</p>
          <h2>Control how hard the server works</h2>
          <p className="muted">Each limit starts from its environment variable (.env) or the built-in default. A value saved here wins and takes effect on every server within {data.liveSeconds} seconds. <b>Reset</b> goes back to .env.</p>
          {data.updatedAt && <p className="muted lim-when">Last changed {new Date(data.updatedAt).toLocaleString()} by {data.updatedBy}</p>}
        </div>
        <div className="lim-legend">
          {Object.entries(SOURCE_LABEL).map(([id, label]) => <span key={id} className={`lim-src src-${id}`}>{label}</span>)}
        </div>
      </section>

      {data.groups.filter((group) => byGroup[group.id]).map((group, groupIndex) => {
        const look = GROUP_LOOK[group.id] || { icon: '⚙️', tint: 'a', hint: '' };
        return (
          <section key={group.id} className={`card lim-group tint-${look.tint}`} style={{ animationDelay: `${groupIndex * 70}ms` }}>
            <div className="lim-group-head">
              <span className="lim-group-ico" aria-hidden="true">{look.icon}</span>
              <div><h3>{group.label}</h3><p className="muted">{look.hint}</p></div>
            </div>
            <div className="lim-grid">
              {byGroup[group.id].map((item, index) => {
                const editing = item.key in draft;
                const raw = editing ? draft[item.key] : item.value;
                const resetting = editing && draft[item.key] === null;
                const issue = resetting ? '' : problem(item, editing ? raw : undefined);
                return (
                  <div key={item.key} className={`lim-item${editing ? ' is-dirty' : ''}${issue ? ' is-bad' : ''}`} style={{ animationDelay: `${groupIndex * 70 + index * 30}ms` }}>
                    <div className="lim-item-top">
                      <label htmlFor={`lim-${item.key}`}>{item.label}</label>
                      <span className={`lim-src src-${resetting ? 'environment' : editing ? 'admin' : item.source}`}>{resetting ? 'Reset on save' : editing ? 'Changed' : SOURCE_LABEL[item.source]}</span>
                    </div>
                    <div className="lim-input">
                      <input
                        id={`lim-${item.key}`} value={resetting ? fmt(item.fallback) : String(raw ?? '')} disabled={resetting}
                        inputMode={item.kind === 'memory' ? 'text' : 'numeric'}
                        onChange={(e) => setDraft({ ...draft, [item.key]: e.target.value })}
                        aria-invalid={Boolean(issue)} aria-describedby={`lim-help-${item.key}`}
                      />
                      {item.unit && <span className="lim-unit">{item.unit}</span>}
                    </div>
                    <p id={`lim-help-${item.key}`} className="lim-help">{issue ? <b className="lim-bad">{issue}</b> : item.help}</p>
                    <div className="lim-item-foot">
                      <code title="Environment variable">{item.key}</code>
                      {item.source === 'admin' && !editing && <button type="button" className="lim-reset" onClick={() => setDraft({ ...draft, [item.key]: null })} title={`Back to ${fmt(item.fallback)}`}>Reset</button>}
                      {editing && <button type="button" className="lim-reset" onClick={() => { const next = { ...draft }; delete next[item.key]; setDraft(next); }}>Undo</button>}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        );
      })}

      <div className={`lim-bar${dirty.length ? ' is-open' : ''}`} role="region" aria-label="Unsaved limits">
        <span><b>{dirty.length}</b> {dirty.length === 1 ? 'limit' : 'limits'} changed</span>
        <button type="button" className="btn" disabled={busy} onClick={() => setDraft({})}>Undo all</button>
        <button type="button" className="btn primary" disabled={busy || invalid || !dirty.length} onClick={() => save(draft)}>{busy ? 'Saving…' : 'Save limits'}</button>
      </div>
    </div>
  );
}
