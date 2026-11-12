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

function RunHistory({ records, search }) {
  const needle = search.trim().toLowerCase();
  const rows = records.filter((r) => !needle || JSON.stringify(r).toLowerCase().includes(needle));
  if (!rows.length) return <div className="empty">No report runs recorded yet.</div>;
  return (
    <div className="table-wrap" style={{ maxHeight: '62vh' }}>
      <table className="data">
        <thead><tr>{['Time (UTC)', 'Report', 'Format', 'Status', 'Rows', 'Duration', 'Data adapter', 'Access', 'User', 'Error'].map((h) => <th key={h}>{h}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, index) => (
            <tr key={`${r.requestedAt}-${index}`} className={r.status === 'success' ? '' : 'failed'}>
              <td>{String(r.requestedAt || '').replace('T', ' ').slice(0, 19)}</td><td>{r.reportId}</td><td>{(r.format || '').toUpperCase()}</td><td>{r.status}</td>
              <td>{r.rowCount}</td><td>{r.durationMs !== undefined ? `${r.durationMs} ms` : ''}</td><td>{r.connectionId || 'report default'}</td><td>{r.access}</td>
              <td>{r.GeneratedBy || ''}</td><td>{r.errorMessage || r.errorCode || ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function LogsTab() {
  const [view, setView] = useState('service');
  const [level, setLevel] = useState('INFO');
  const [lines, setLines] = useState('300');
  const [search, setSearch] = useState('');
  const [auto, setAuto] = useState(false);
  const [data, setData] = useState({ entries: [], records: [] });
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
        <div><p className="eyebrow">Monitoring</p><h2>Logs</h2><p className="muted">Service log (requests, sign-ins, publishing, warnings, errors) and the report run history. Query strings, tokens, passwords and MongoDB URLs are never written to the log.</p></div>
        <div className="row"><button className="btn" onClick={load}>Refresh</button><button className="btn quiet" onClick={download}>Download log file</button></div>
      </div>
      <div className="log-toolbar">
        <div className="segmented" role="tablist">
          <button className={view === 'service' ? 'on' : ''} onClick={() => setView('service')}>Service log</button>
          <button className={view === 'audit' ? 'on' : ''} onClick={() => setView('audit')}>Report runs</button>
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
      {view === 'service' ? <ServiceLog entries={data.entries} /> : <RunHistory records={data.records} search={search} />}
    </section>
  );
}
