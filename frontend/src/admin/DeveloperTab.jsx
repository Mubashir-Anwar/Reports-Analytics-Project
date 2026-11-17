// /admin → API & Postman: the Postman collection and environment for this server, and ready-to-copy curl
// commands for a chosen report (the API key is pasted by the admin; it is never shown again after creation).
import { useEffect, useMemo, useState } from 'react';
import { api, request } from '../api.js';
import { downloadBlob } from '../portal/Shell.jsx';

const origin = window.location.origin;

async function download(path, name, notify) {
  try {
    const response = await request(path, { raw: true });
    downloadBlob(await response.blob(), name);
  } catch (error) { notify(error.message, 'error'); }
}

function Snippet({ title, text, notify }) {
  return (
    <div className="snippet">
      <div className="snippet-head"><b>{title}</b>
        <button type="button" className="btn sm" onClick={() => navigator.clipboard.writeText(text).then(() => notify(`Copied: ${title}`), () => notify('Copy failed: select the text and copy it', 'warn'))}>Copy</button>
      </div>
      <pre>{text}</pre>
    </div>
  );
}

export default function DeveloperTab({ notify }) {
  const [reports, setReports] = useState([]);
  const [reportId, setReportId] = useState('');
  const [key, setKey] = useState('');
  const [values, setValues] = useState({});
  useEffect(() => {
    api.reports().then((data) => {
      const list = data.reports || [];
      setReports(list);
      if (list.length) setReportId(list[0].id);
    }).catch((error) => notify(error.message, 'error'));
  }, [notify]);

  const report = reports.find((item) => item.id === reportId);
  const params = useMemo(() => (report?.parameters || []).filter((p) => p.prompt !== false && !p.expression), [report]);
  const apiKey = key.trim() || 'mrk_…';
  const filled = Object.fromEntries(params.map((p) => [p.name, values[`${reportId}.${p.name}`] ?? (p.default ?? '')]));
  const query = new URLSearchParams(Object.entries(filled).filter(([, v]) => v !== '')).toString();
  const json = JSON.stringify({ parameters: filled, limit: 100, offset: 0 });
  const runJson = JSON.stringify({ parameters: filled, format: 'pdf' });
  const q = (text) => text.replace(/"/g, '\\"');
  const id = reportId || '<REPORT_ID>';

  return (
    <div className="grid-2">
      <section className="card">
        <div className="card-head"><div><p className="eyebrow">Postman</p><h2>Every API of this server</h2></div></div>
        <p className="muted">The collection has every endpoint (sign-in, Designer, Server, Admin, Public API v1, embedding) in an order that runs end to end. The environment is filled in for <b>{origin}</b>; secrets stay empty.</p>
        <div className="row-actions">
          <button className="btn primary" onClick={() => download('/api/admin/postman/collection.json', 'Reporting-Service.postman_collection.json', notify)}>⬇ Collection</button>
          <button className="btn" onClick={() => download('/api/admin/postman/environment.json', 'Reporting-Service.postman_environment.json', notify)}>⬇ Environment</button>
        </div>
        <ol className="steps" style={{ marginTop: 14 }}>
          <li>Postman → <b>Import</b> both files, then choose the environment.</li>
          <li>Fill in <code>password</code> (your sign-in) and <code>apiKey</code> (from <a href="#keys">API keys</a>).</li>
          <li>Single requests: folder <b>12 Public API v1</b>. The whole collection creates and deletes test data (<code>PM_…</code>): never run it on production.</li>
        </ol>
      </section>

      <section className="card">
        <div className="card-head"><div><p className="eyebrow">curl</p><h2>Call a report from any website or server</h2></div></div>
        <div className="form-grid-2">
          <label className="field"><span>Report</span>
            <select value={reportId} onChange={(e) => setReportId(e.target.value)}>
              {reports.map((item) => <option key={item.id} value={item.id}>{item.id} — {item.title} ({item.folder})</option>)}
            </select>
          </label>
          <label className="field"><span>API key (only used on this page)</span><input value={key} placeholder="mrk_…" autoComplete="off" onChange={(e) => setKey(e.target.value)} /></label>
          {params.map((p) => (
            <label key={p.name} className="field"><span>{p.label || p.name}</span>
              <input value={filled[p.name] ?? ''} onChange={(e) => setValues({ ...values, [`${reportId}.${p.name}`]: e.target.value })} />
            </label>
          ))}
        </div>
        <Snippet notify={notify} title="PDF by URL (GET)" text={`curl -H "X-API-Key: ${apiKey}" "${origin}/generate/${id}.pdf${query ? `?${query}` : ''}" -o ${id}.pdf`} />
        <Snippet notify={notify} title="Data as JSON (POST)" text={`curl -X POST "${origin}/api/v1/reports/${id}/data" -H "X-API-Key: ${apiKey}" -H "Content-Type: application/json" -d "${q(json)}"`} />
        <Snippet notify={notify} title="File from JSON (POST)" text={`curl -X POST "${origin}/api/v1/reports/${id}/run" -H "X-API-Key: ${apiKey}" -H "Content-Type: application/json" -d "${q(runJson)}" -o ${id}.pdf`} />
        <Snippet notify={notify} title="Reports this key may run" text={`curl -H "X-API-Key: ${apiKey}" "${origin}/api/v1/reports"`} />
        <p className="muted" style={{ fontSize: 12 }}>The report ID is the first column of the report list (not the title). A wrong ID answers 404 with “Did you mean …?”.</p>
      </section>
    </div>
  );
}
