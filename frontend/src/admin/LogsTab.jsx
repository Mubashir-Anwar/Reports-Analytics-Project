import { useCallback, useEffect, useState } from 'react';
import { api } from '../api.js';
import { downloadBlob } from '../portal/Shell.jsx';

function ServiceLog({ entries }) {
  if (!entries.length) return <div className="empty">No log entries match these filters yet.</div>;
  return (
    <div className="log-lines">
      {[...entries].reverse().map((entry, index) => (
        <div key={`${entry.time}-${index}`} className={`log-line level-${entry.level.toLowerCase()}`}>
          <span>{entry.time}</span><span className="lvl">{entry.level}</span><span>{entry.logger.replace('mongo_reporting_service', 'service')}</span><pre>{entry.message}</pre>
        </div>
      ))}
    </div>
  );
}

const when = (iso) => String(iso || '').replace('T', ' ').slice(0, 19);
const CHANNEL = { runner: 'Run reports', server: 'Report Server', 'designer-preview': 'Designer preview', 'designer-run': 'Designer run',
  schedule: 'Schedule', api: 'API', 'signed-link': 'Signed link' };
const paramText = (values) => Object.entries(values || {}).map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(',') : v}`).join(' · ');

/** One row per report execution (system database: runs). */
function RunHistory({ records, search }) {
  const needle = search.trim().toLowerCase();
  const rows = records.filter((r) => !needle || JSON.stringify(r).toLowerCase().includes(needle));
  if (!rows.length) return <div className="empty">No report runs recorded yet.</div>;
  return (
    <div className="table-wrap" style={{ maxHeight: '62vh' }}>
      <table className="data">
        <thead><tr>{['Time (UTC)', 'Report', 'From', 'Who', 'Format', 'Status', 'Rows', 'Duration', 'Data source', 'Parameters', 'Error'].map((h) => <th key={h}>{h}</th>)}</tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className={r.status === 'success' ? '' : 'failed'}>
              <td>{when(r.startedAt)}</td><td title={r.folder ? `/${r.folder}` : ''}>{r.reportId}</td><td>{CHANNEL[r.channel] || r.channel}</td>
              <td title={r.actorType}>{r.actor}{r.onBehalfOf ? <small> for {r.onBehalfOf}</small> : null}</td>
              <td>{(r.format || '').toUpperCase()}</td><td>{r.status}</td><td>{r.rowCount ?? ''}</td>
              <td>{r.durationMs != null ? `${r.durationMs} ms` : ''}</td><td>{r.dataSource || ''}</td>
              <td className="mono">{paramText(r.parameters)}</td><td>{r.errorMessage || r.errorCode || ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Append-only audit trail (audit_events): each event carries the hash of the one before it. */
function AuditTrail({ events, search, verify }) {
  const needle = search.trim().toLowerCase();
  const rows = events.filter((e) => !needle || JSON.stringify(e).toLowerCase().includes(needle));
  return (
    <>
      {verify && (
        <div className={`notice ${verify.ok ? 'ok' : 'error'}`} style={{ marginBottom: 10 }}>
          {verify.ok ? `Chain intact: all ${verify.events} events are unchanged and in order.` : `The audit trail was changed: ${verify.problems.join('; ')}`}
        </div>
      )}
      {!rows.length ? <div className="empty">No audit events yet.</div> : (
        <div className="table-wrap" style={{ maxHeight: '62vh' }}>
          <table className="data">
            <thead><tr>{['#', 'Time (UTC)', 'Action', 'Who', 'What', 'Details', 'From'].map((h) => <th key={h}>{h}</th>)}</tr></thead>
            <tbody>
              {rows.map((e) => (
                <tr key={e.id} className={e.action.endsWith('failed') ? 'failed' : ''}>
                  <td>{e.seq}</td><td>{when(e.at)}</td><td><code>{e.action}</code></td><td title={e.actorType}>{e.actor}</td><td>{e.target || ''}</td>
                  <td className="mono">{e.details && Object.keys(e.details).length ? JSON.stringify(e.details) : ''}</td><td>{e.clientIp || ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

export default function LogsTab() {
  const [view, setView] = useState('service');
  const [level, setLevel] = useState('INFO');
  const [lines, setLines] = useState('300');
  const [search, setSearch] = useState('');
  const [auto, setAuto] = useState(false);
  const [data, setData] = useState({ entries: [], records: [], events: [] });
  const [verify, setVerify] = useState(null);
  const [status, setStatus] = useState('');

  const load = useCallback(async () => {
    setStatus('Loading…');
    try {
      if (view === 'service') {
        const result = await api.logs({ lines, level, search: search.trim() });
        setData((d) => ({ ...d, entries: result.entries }));
        setStatus(result.enabled
          ? `${result.entries.length} entries from ${result.file}, newest first · ${new Date().toLocaleTimeString()}`
          : 'Writing the log file is switched off (REPORTING_LOG_TO_FILE=false); logs go to the console only.');
      } else if (view === 'events') {
        const result = await api.auditEvents(lines);
        setData((d) => ({ ...d, events: result.events }));
        setStatus(`${result.events.length} most recent audit events, newest first · ${new Date().toLocaleTimeString()}`);
      } else {
        const result = await api.audit(lines);
        setData((d) => ({ ...d, records: result.records }));
        setStatus(`${result.records.length} most recent report runs, newest first · ${new Date().toLocaleTimeString()}`);
      }
    } catch (error) { setStatus(error.message); }
  }, [view, level, lines, search]);
  useEffect(() => { load(); }, [view, level, lines]); // eslint-disable-line react-hooks/exhaustive-deps -- search applies on Enter/blur
  useEffect(() => {
    if (!auto) return undefined;
    const timer = window.setInterval(load, 10_000);
    return () => window.clearInterval(timer);
  }, [auto, load]);

  const download = async () => {
    try { downloadBlob(await api.logFile(), 'service.log'); } catch (error) { setStatus(error.message); }
  };

  return (
    <section className="card">
      <div className="card-head">
        <div><p className="eyebrow">Monitoring</p><h2>Logs</h2><p className="muted">Service log (requests, warnings, errors), every report run (who, from where, rows, time; sensitive parameters masked) and the tamper-evident audit trail (sign-ins, users and rights, API keys, publishing, deletes, permissions, data sources). Tokens, passwords and database URLs are never recorded.</p></div>
        <div className="row"><button className="btn" onClick={load}>Refresh</button>
          {view === 'events' && <button className="btn" onClick={async () => { try { setVerify(await api.verifyAudit()); } catch (error) { setStatus(error.message); } }}>Verify chain</button>}
          <button className="btn quiet" onClick={download}>Download log file</button></div>
      </div>
      <div className="log-toolbar">
        <div className="segmented" role="tablist">
          <button className={view === 'service' ? 'on' : ''} onClick={() => setView('service')}>Service log</button>
          <button className={view === 'audit' ? 'on' : ''} onClick={() => setView('audit')}>Report runs</button>
          <button className={view === 'events' ? 'on' : ''} onClick={() => setView('events')}>Audit trail</button>
        </div>
        <label className="field"><span>Level</span>
          <select value={level} disabled={view !== 'service'} onChange={(e) => setLevel(e.target.value)}>
            <option value="DEBUG">Debug and up</option><option value="INFO">Info and up</option><option value="WARNING">Warnings and errors</option><option value="ERROR">Errors only</option>
          </select>
        </label>
        <label className="field grow"><span>Search</span><input type="search" value={search} placeholder="Text to find, e.g. /generate, a report ID or a user" onChange={(e) => setSearch(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') load(); }} onBlur={load} /></label>
        <label className="field"><span>Lines</span><select value={lines} onChange={(e) => setLines(e.target.value)}><option>100</option><option>300</option><option>1000</option></select></label>
        <label className="check"><input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> Auto-refresh (10 s)</label>
      </div>
      <p className="muted" style={{ marginBottom: 10 }}>{status}</p>
      {view === 'service' && <ServiceLog entries={data.entries} />}
      {view === 'audit' && <RunHistory records={data.records} search={search} />}
      {view === 'events' && <AuditTrail events={data.events} search={search} verify={verify} />}
    </section>
  );
}
