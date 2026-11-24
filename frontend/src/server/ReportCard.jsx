// The new look of the Report Server's middle column: filter chips (All, Reports, Dashboards, Scheduled, Failed),
// a "Running now" strip, and report cards with their status and a small bar chart of their last runs.
// Runs and schedules come from the same calls as the Runs and Schedules views; when the user may not read them
// the cards simply show no history.
import { useEffect, useMemo, useState } from 'react';
import { serverApi } from './serverApi.js';

const BARS = 12;
const TINTS = ['b', 'v', 'c', 'a', 'g'];
const tintOf = (text) => TINTS[[...String(text)].reduce((n, ch) => n + ch.charCodeAt(0), 0) % TINTS.length];
const initials = (title) => String(title || '?').split(/[\s_-]+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('') || '?';
const failed = (run) => run.status === 'failed' || (run.delivered || []).some((d) => !d.ok && !d.skipped);
const busy = (run) => run.status === 'queued' || run.status === 'running';
const when = (value) => {
  if (!value) return '';
  const t = new Date(value);
  const days = Math.floor((Date.now() - t.getTime()) / 86400000);
  if (t.toDateString() === new Date().toDateString()) return `today ${t.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
  if (days < 2) return 'yesterday';
  if (days < 7) return `${days} days ago`;
  return t.toLocaleDateString();
};

/** Runs (newest first) and schedules of every report this user can see; refreshes every few seconds while one runs. */
export function useActivity(version) {
  const [activity, setActivity] = useState({ runs: [], schedules: [] });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    Promise.allSettled([serverApi.runs(), serverApi.schedules()]).then(([r, s]) => {
      if (!live) return;
      setActivity({ runs: r.status === 'fulfilled' ? r.value.runs || [] : [], schedules: s.status === 'fulfilled' ? s.value.schedules || [] : [] });
    });
    return () => { live = false; };
  }, [version, tick]);
  const running = activity.runs.filter(busy);
  useEffect(() => {
    if (!running.length) return undefined;
    const timer = setTimeout(() => setTick((n) => n + 1), 4000);
    return () => clearTimeout(timer);
  }, [running.length, tick]);
  return { ...activity, running };
}

/** What the cards need per report: its runs (a run knows only the report ID), schedules and a status. */
export function reportFacts(activity, r) {
  const runs = activity.runs.filter((run) => run.reportId === r.id);
  const schedules = activity.schedules.filter((job) => job.reportId === r.id && (!job.folder || job.folder === r.folder));
  const last = runs.find((run) => !busy(run));
  const status = runs.some(busy) ? 'Running' : last && failed(last) ? 'Failed' : schedules.some((j) => j.enabled !== false) ? 'Scheduled' : 'Live';
  return { runs, schedules, last, status };
}

export const KIND_FILTERS = [
  ['all', 'All', () => true],
  ['reports', 'Reports', (item) => item.kind === 'report'],
  ['dashboards', 'Dashboards', (item) => item.kind === 'dashboard'],
  ['scheduled', 'Scheduled', (item) => item.facts?.schedules.length > 0],
  ['failed', 'Failed', (item) => item.facts?.status === 'Failed'],
];

export function KindChips({ items, value, onChange }) {
  return (
    <div className="srv-chips" role="group" aria-label="Show">
      {KIND_FILTERS.map(([id, label, test]) => {
        const count = items.filter(test).length;
        if (id !== 'all' && id !== value && !count) return null;
        return (
          <button key={id} type="button" className={`srv-chip ${value === id ? 'on' : ''} ${id === 'failed' ? 'bad' : ''}`} aria-pressed={value === id} onClick={() => onChange(id)}>
            {label}<small>{count}</small>
          </button>
        );
      })}
    </div>
  );
}

/** "Running now: Account statement · PDF" with a moving bar; click opens the Runs view. */
export function RunningStrip({ running, onOpen }) {
  if (!running.length) return null;
  const first = running[0];
  return (
    <button type="button" className="srv-running" onClick={onOpen} title="Open Runs and outputs">
      <span className="srv-ping" aria-hidden="true"><i /></span>
      <span className="srv-running-text"><b>Running now:</b> {first.title || first.reportId}{first.format ? ` · ${first.format.toUpperCase()}` : ''}{running.length > 1 ? ` and ${running.length - 1} more` : ''}</span>
      <span className="srv-progress" aria-hidden="true"><i /></span>
      <span className="srv-running-go">Runs →</span>
    </button>
  );
}

/** Last runs as small bars, oldest on the left: height = how long it took, red = failed, accent = the newest. */
function RunBars({ runs }) {
  const shown = runs.slice(0, BARS).reverse();
  if (!shown.length) return <div className="srv-bars empty" aria-hidden="true">{Array.from({ length: BARS }, (_, i) => <i key={i} />)}</div>;
  const took = shown.map((run) => (run.startedAt && run.finishedAt ? Math.max(1, new Date(run.finishedAt) - new Date(run.startedAt)) : 1));
  const most = Math.max(...took);
  return (
    <div className="srv-bars" role="img" aria-label={`Last ${shown.length} run${shown.length > 1 ? 's' : ''}: ${shown.filter(failed).length} failed`}>
      {Array.from({ length: BARS - shown.length }, (_, i) => <i key={`none-${i}`} className="none" />)}
      {shown.map((run, i) => (
        <i key={run.id || i} className={failed(run) ? 'bad' : busy(run) ? 'busy' : i === shown.length - 1 ? 'last' : ''}
          style={{ height: `${Math.round(25 + (took[i] / most) * 75)}%`, animationDelay: `${0.2 + i * 0.03}s` }}
          title={`${run.status}${run.finishedAt ? ` · ${when(run.finishedAt)}` : ''}${run.error ? ` · ${run.error}` : ''}`} />
      ))}
    </div>
  );
}

export function ReportCard({ r, facts, index, selected, cut, onClick, onDoubleClick, onContextMenu }) {
  const meta = useMemo(() => {
    const parts = [];
    if (facts.status === 'Running') parts.push('Running now…');
    else if (facts.last) parts.push(`${facts.status === 'Failed' ? 'Last run failed' : 'Ran'} ${when(facts.last.finishedAt || facts.last.queuedAt)}`);
    else parts.push('Not run yet');
    if (facts.schedules.length) parts.push(`${facts.schedules.length} schedule${facts.schedules.length > 1 ? 's' : ''}`);
    return parts.join(' · ');
  }, [facts]);
  return (
    <button type="button" className={`report-card srv-card ${selected ? 'on' : ''} ${cut ? 'cut' : ''}`} style={{ '--i': Math.min(index, 12) }}
      onClick={onClick} onDoubleClick={onDoubleClick} onContextMenu={onContextMenu}>
      <span className="srv-card-top">
        <span className={`srv-mark tint-${tintOf(r.id)}`} aria-hidden="true">{initials(r.title)}</span>
        <span className="srv-card-name"><strong>{r.title}</strong><small>{!r.folder || r.folder === 'General' ? 'Top level' : r.folder}</small></span>
        <span className={`srv-tag ${facts.status.toLowerCase()}`}><i aria-hidden="true" />{facts.status}</span>
      </span>
      <RunBars runs={facts.runs} />
      <span className="srv-card-foot">
        <small>{meta}</small>
        <span className="srv-formats">{r.formats.map((f) => <em key={f}>{f.toUpperCase()}</em>)}</span>
      </span>
      {r.parameters.length > 0 && <small className="srv-q">{r.parameters.length} question{r.parameters.length > 1 ? 's' : ''} before it runs</small>}
      {r.description && <small className="srv-desc">{r.description}</small>}
    </button>
  );
}

export function DashboardCard({ d, index, cut, onClick, onContextMenu }) {
  return (
    <button type="button" className={`report-card dashboard-card srv-card ${cut ? 'cut' : ''}`} style={{ '--i': Math.min(index, 12) }} onClick={onClick} onContextMenu={onContextMenu}>
      <span className="srv-card-top">
        <span className="srv-mark tint-v" aria-hidden="true">📊</span>
        <span className="srv-card-name"><strong>{d.name}</strong><small>{!d.folder || d.folder === 'General' ? 'Top level' : d.folder}</small></span>
        <span className="srv-tag dash"><i aria-hidden="true" />Dashboard</span>
      </span>
      <span className="srv-dash-art" aria-hidden="true">{Array.from({ length: Math.max(1, Math.min(d.tiles || 1, 6)) }, (_, i) => <i key={i} style={{ animationDelay: `${0.2 + i * 0.06}s` }} />)}</span>
      <span className="srv-card-foot"><small>{d.tiles} visual{d.tiles === 1 ? '' : 's'}{d.owner ? ` · by ${d.owner}` : ''}</small></span>
      {d.description && <small className="srv-desc">{d.description}</small>}
    </button>
  );
}
