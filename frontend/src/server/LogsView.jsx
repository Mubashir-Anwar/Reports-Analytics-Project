// Report Server → Logs (administrators): every request that reached the server, report runs, the data taken out
// through the API, the service log, the audit trail and what is being made in the background right now. Read from
// the system database (request_log, runs, api_calls, audit_events).
import { useCallback, useEffect, useState } from 'react';
import { Notice, formatWhen } from '../portal/Shell.jsx';
import { serverApi } from './serverApi.js';

const KINDS = [
  ['requests', 'Requests'],
  ['runs', 'Report runs'],
  ['api-calls', 'API calls'],
  ['service', 'Service log'],
  ['audit', 'Audit trail'],
  ['now', 'Right now'],
];

const ms = (value) => (value == null ? '' : value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${value} ms`);
const bytes = (value) => (value == null ? '' : value >= 1_048_576 ? `${(value / 1_048_576).toFixed(1)} MB` : value >= 1024 ? `${Math.round(value / 1024)} KB` : `${value} B`);
const statusClass = (code) => (code >= 500 ? 'bad' : code >= 400 ? 'warn' : 'ok');

function toCsv(rows, columns) {
  const cell = (v) => {
    const text = v == null ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v);
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  return [columns.join(','), ...rows.map((r) => columns.map((c) => cell(r[c])).join(','))].join('\n');
}
function saveCsv(rows, columns, name) {
  const url = URL.createObjectURL(new Blob([toCsv(rows, columns)], { type: 'text/csv' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 3000);
}

export default function LogsView() {
  const [kind, setKind] = useState('requests');
  const [day, setDay] = useState('');
  const [status, setStatus] = useState('');
  const [search, setSearch] = useState('');
  const [level, setLevel] = useState('INFO');
  const [data, setData] = useState(null);
  const [message, setMessage] = useState(null);
  const [loading, setLoading] = useState(false);
  const [auto, setAuto] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const query = new URLSearchParams();
      if (kind === 'requests') {
        if (day) query.set('day', day);
        if (status) query.set('status', status);
        if (search) query.set('search', search);
        query.set('limit', '1000');
      } else if (kind === 'runs') {
        query.set('limit', '500');
        if (status) query.set('status', status);
        if (search) query.set('reportId', search);
      } else if (kind === 'service') {
        query.set('lines', '1000');
        query.set('level', level);
        if (search) query.set('search', search);
      } else if (kind === 'audit') {
        query.set('limit', '500');
        if (search) query.set('actor', search);
      } else if (kind === 'api-calls') {
        query.set('limit', '500');
        if (search) query.set('reportId', search);
      }
      setData(await serverApi.logs(kind === 'now' ? 'downloads' : kind, query));
      setMessage(null);
    } catch (error) { setMessage({ kind: 'error', text: error.message }); }
    finally { setLoading(false); }
  }, [kind, day, status, search, level]);

  useEffect(() => { setData(null); }, [kind]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!auto) return undefined;
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [auto, load]);

  const records = data?.records || data?.events || data?.entries || data?.calls || [];
  const runPill = (s) => (s === 'success' ? 'ok' : s === 'failed' ? 'bad' : 'warn');

  return (
    <div className="stack logs-view">
      <div className="row logs-bar">
        <div className="segmented" role="tablist">
          {KINDS.map(([id, label]) => (
            <button key={id} role="tab" aria-selected={kind === id} className={kind === id ? 'on' : ''} onClick={() => { setKind(id); setStatus(''); setSearch(''); }}>{label}</button>
          ))}
        </div>
        {kind === 'requests' && (
          <select value={day} onChange={(e) => setDay(e.target.value)} aria-label="Day">
            {(data?.days || []).map((d, i) => <option key={d} value={i === 0 ? '' : d}>{i === 0 ? `Today (${d})` : d}</option>)}
          </select>
        )}
        {(kind === 'requests' || kind === 'runs') && (
          <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
            {kind === 'requests'
              ? [['', 'All answers'], ['errors', 'Errors (4xx/5xx)'], ['ok', 'Successful'], ['429', '429 too many'], ['503', '503 busy'], ['500', '500 server error']].map(([v, l]) => <option key={v} value={v}>{l}</option>)
              : [['', 'All runs'], ['success', 'Succeeded'], ['failed', 'Failed'], ['queued', 'Waiting'], ['running', 'Running']].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        )}
        {kind === 'service' && (
          <select value={level} onChange={(e) => setLevel(e.target.value)} aria-label="Level">
            {['DEBUG', 'INFO', 'WARNING', 'ERROR'].map((l) => <option key={l}>{l}</option>)}
          </select>
        )}
        {kind !== 'now' && (
          <input type="search" placeholder={{ requests: 'Path, user or address', runs: 'Report ID', 'api-calls': 'Report ID', service: 'Text in the log', audit: 'User' }[kind]}
            value={search} onChange={(e) => setSearch(e.target.value)} />
        )}
        <label className="check"><input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> Every 5 s</label>
        <button className="btn" onClick={load} disabled={loading}>{loading ? 'Loading…' : '↻ Refresh'}</button>
        {kind === 'requests' && records.length > 0 && (
          <button className="btn" onClick={() => saveCsv(records, ['at', 'method', 'path', 'query', 'status', 'durationMs', 'bytes', 'actor', 'actorType', 'client', 'agent'], `requests-${data.day}.csv`)}>⤓ CSV</button>
        )}
        {(kind === 'runs' || kind === 'api-calls') && records.length > 0 && (
          <button className="btn" onClick={() => saveCsv(records, Object.keys(records[0]), kind === 'runs' ? 'report-runs.csv' : 'api-calls.csv')}>⤓ CSV</button>
        )}
      </div>
      <Notice message={message} />

      {kind === 'requests' && data?.totals && (
        <div className="log-stats">
          <div><b>{data.totals.requests}</b><span>requests</span></div>
          <div><b className={data.totals.errors ? 'warn' : ''}>{data.totals.errors}</b><span>errors</span></div>
          <div><b>{ms(data.totals.averageMs)}</b><span>average time</span></div>
          <div><b className={data.totals.slow ? 'warn' : ''}>{data.totals.slow}</b><span>slower than 2 s</span></div>
          {data.recorder && <div><b>{data.recorder.enabled ? 'on' : 'off'}</b><span>recording · kept {data.recorder.keepDays} days</span></div>}
          {data.busiest?.length > 0 && (
            <div className="log-busiest"><span>Most called</span>{data.busiest.slice(0, 5).map((b) => <code key={b.path}>{b.count}× {b.path}</code>)}</div>
          )}
        </div>
      )}

      {kind === 'api-calls' && data?.totals && (
        <div className="log-stats">
          <div><b>{data.totals.calls}</b><span>calls that took data out</span></div>
          <div><b>{data.totals.rows}</b><span>rows sent</span></div>
          <div><b>{bytes(data.totals.bytes)}</b><span>sent in files</span></div>
        </div>
      )}

      {kind === 'now' && data && (
        <div className="log-stats">
          <div><b>{data.capacity.running}</b><span>reports being made (of {data.capacity.limit})</span></div>
          <div><b>{data.capacity.waiting}</b><span>waiting for a place</span></div>
          <div><b>{data.deliveries.queued || 0}</b><span>downloads in the queue</span></div>
          <div><b>{data.deliveries.running || 0}</b><span>downloads being made</span></div>
          <div><b>{data.deliveries.ready || 0}</b><span>files ready to collect</span></div>
          <div><b>{data.capacity.served}</b><span>served since start · peak {data.capacity.peak}</span></div>
          <div><b className={data.capacity.rejectedBusy ? 'warn' : ''}>{data.capacity.rejectedBusy + data.capacity.rejectedPerCaller}</b><span>turned away (busy / per user)</span></div>
        </div>
      )}

      {kind !== 'now' && (
        <div className="table-wrap logs-table">
          <table className="grid-table">
            <thead>
              {kind === 'requests' && <tr><th>Time</th><th>Method</th><th>Path</th><th>Status</th><th>Took</th><th>Size</th><th>Who</th><th>Address</th></tr>}
              {kind === 'runs' && <tr><th>Time</th><th>Report</th><th>Format</th><th>Status</th><th>Rows</th><th>Took</th><th>Who</th><th>Channel</th></tr>}
              {kind === 'api-calls' && <tr><th>Time</th><th>Key / caller</th><th>Endpoint</th><th>Report</th><th>Status</th><th>Rows</th><th>Size</th><th>Address</th></tr>}
              {kind === 'service' && <tr><th>Time</th><th>Level</th><th>Message</th></tr>}
              {kind === 'audit' && <tr><th>Time</th><th>Action</th><th>Target</th><th>Who</th><th>Address</th></tr>}
            </thead>
            <tbody>
              {kind === 'requests' && records.map((r, i) => (
                <tr key={`${r.at}-${i}`}>
                  <td className="nowrap">{formatWhen(r.at)}</td><td>{r.method}</td>
                  <td className="mono" title={r.agent}>{r.path}{r.query?.length ? <span className="muted">?{r.query.join('&')}</span> : null}</td>
                  <td><span className={`pill ${statusClass(r.status)}`}>{r.status}</span></td>
                  <td className="nowrap">{ms(r.durationMs)}</td><td className="nowrap">{bytes(r.bytes)}</td>
                  <td>{r.actor}<span className="muted"> {r.actorType !== 'user' ? r.actorType : ''}</span></td><td className="mono">{r.client}</td>
                </tr>
              ))}
              {kind === 'runs' && records.map((r, i) => (
                <tr key={`${r.startedAt}-${i}`}>
                  <td className="nowrap">{formatWhen(r.startedAt)}</td><td title={r.reportTitle}>{r.reportId}</td><td>{(r.format || '').toUpperCase()}</td>
                  <td><span className={`pill ${runPill(r.status)}`}>{r.status}</span>{r.errorCode ? <span className="muted" title={r.errorMessage}> {r.errorCode}</span> : null}</td>
                  <td>{r.rowCount}</td><td className="nowrap">{ms(r.durationMs)}</td><td>{r.actor}</td>
                  <td>{r.channel}{r.runType && r.runType !== 'interactive' ? <span className="muted"> · {r.runType}</span> : null}</td>
                </tr>
              ))}
              {kind === 'api-calls' && records.map((r) => (
                <tr key={r.id}>
                  <td className="nowrap">{formatWhen(r.calledAt)}</td>
                  <td>{r.caller}{r.apiKeyId ? <span className="muted mono"> {r.apiKeyId}</span> : <span className="muted"> {r.callerType}</span>}</td>
                  <td className="mono">{r.method} {r.endpoint}</td><td title={r.folder}>{r.reportId}</td>
                  <td><span className={`pill ${statusClass(r.httpStatus)}`}>{r.httpStatus}</span></td>
                  <td>{r.rowsReturned}</td><td className="nowrap">{bytes(r.bytesReturned)}</td><td className="mono">{r.clientIp}</td>
                </tr>
              ))}
              {kind === 'service' && records.slice().reverse().map((r, i) => (
                <tr key={i}><td className="nowrap">{r.time}</td><td><span className={`pill ${r.level === 'ERROR' || r.level === 'CRITICAL' ? 'bad' : r.level === 'WARNING' ? 'warn' : 'ok'}`}>{r.level}</span></td><td className="mono wrap"><span className="muted">[{r.logger}]</span> {r.message}</td></tr>
              ))}
              {kind === 'audit' && records.map((r, i) => (
                <tr key={`${r.at}-${i}`}><td className="nowrap">{formatWhen(r.at)}</td><td>{r.action}</td><td className="mono">{r.target}</td><td>{r.actor}</td><td className="mono">{r.clientIp}</td></tr>
              ))}
              {!records.length && !loading && <tr><td colSpan={8} className="muted">Nothing recorded{kind === 'requests' ? ' on this day (with these filters)' : ''}.</td></tr>}
            </tbody>
          </table>
          {data?.more && <p className="muted">Showing the newest {records.length}. Narrow it with the filters, or download the CSV.</p>}
        </div>
      )}
    </div>
  );
}
