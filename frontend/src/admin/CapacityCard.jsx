// /admin → Overview: how busy the report server is right now and the limits that protect it (GET /api/admin/capacity).
import { useEffect, useState } from 'react';
import { request } from '../api.js';

const n = (value) => Number(value || 0).toLocaleString();

export default function CapacityCard() {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let alive = true;
    const load = () => request('/api/admin/capacity').then((value) => { if (alive) { setData(value); setError(''); } }).catch((failure) => alive && setError(failure.message));
    load();
    const timer = setInterval(load, 5000);
    return () => { alive = false; clearInterval(timer); };
  }, []);
  if (error) return <section className="card"><p className="eyebrow">Capacity</p><p className="muted">{error}</p></section>;
  if (!data) return <section className="card"><p className="eyebrow">Capacity</p><p className="muted">Loading…</p></section>;
  const { now, background, limits } = data;
  const used = Math.round((now.running / Math.max(1, now.limit)) * 100);
  return (
    <section className="card capacity">
      <div className="card-head"><div><p className="eyebrow">Capacity · this server process</p><h2>{now.running} of {now.limit} report places in use</h2></div></div>
      <div className="cap-meter" role="meter" aria-valuemin={0} aria-valuemax={now.limit} aria-valuenow={now.running} aria-label="Report places in use">
        <span style={{ width: `${used}%` }} className={used >= 90 ? 'hot' : used >= 60 ? 'warm' : ''} />
      </div>
      <ul className="cap-list">
        <li><b>{now.waiting}</b> waiting for a place (up to {limits.queueWaitSeconds} s, then “busy, try again”)</li>
        <li><b>{n(now.served)}</b> made since start · peak {now.peak} at once</li>
        <li><b>{n(now.rejectedBusy)}</b> turned away because all places were taken · <b>{n(now.rejectedPerCaller)}</b> because one caller already had {limits.maxPerCaller}</li>
        <li><b>{background.running}</b> background runs working, <b>{background.queued}</b> queued ({background.workers} workers)</li>
      </ul>
      <details>
        <summary>Limits (change them in <a href="#limits">Admin → Limits</a>, no restart needed)</summary>
        <table className="cap-table">
          <tbody>
            <tr><td>REPORT_MAX_CONCURRENT</td><td>{limits.maxConcurrent}</td><td>reports made at the same time (per process)</td></tr>
            <tr><td>REPORT_MAX_PER_CALLER</td><td>{limits.maxPerCaller}</td><td>at the same time for one user</td></tr>
            <tr><td>REPORT_QUEUE_WAIT_SECONDS</td><td>{limits.queueWaitSeconds}</td><td>how long a request waits for a place</td></tr>
            <tr><td>REPORT_MAX_ROWS</td><td>{n(limits.maxRows)}</td><td>records in one direct run (PDF, Excel, CSV, JSON)</td></tr>
            <tr><td>REPORT_MAX_TIME_MS</td><td>{n(limits.queryTimeMs)}</td><td>query time limit of a direct run</td></tr>
            <tr><td>REPORT_EXPORT_MAX_ROWS</td><td>{n(limits.exportMaxRows)}</td><td>records in a background CSV / Excel (written to disk)</td></tr>
            <tr><td>REPORT_EXPORT_MAX_TIME_MS</td><td>{n(limits.exportTimeMs)}</td><td>query time limit of a background export</td></tr>
          </tbody>
        </table>
        <p className="muted" style={{ fontSize: 12 }}>Big exports are kept in <code>{data.exportDirectory}</code>.</p>
      </details>
    </section>
  );
}
