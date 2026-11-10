import { useEffect, useState } from 'react';
import { api } from './api.js';

const today = () => new Date().toISOString().slice(0, 10);

export default function Preview({ state, notify }) {
  const { report, design } = state;
  const [values, setValues] = useState({});
  const [mode, setMode] = useState('sample');
  const [busy, setBusy] = useState(false);
  const [url, setUrl] = useState('');

  useEffect(() => () => { if (url) URL.revokeObjectURL(url); }, [url]);

  const valueOf = (p) => values[p.name] ?? (p.type === 'date' ? today() : p.type === 'boolean' ? 'true' : '');

  const run = async () => {
    setBusy(true);
    try {
      const parameters = Object.fromEntries(report.parameters.map((p) => [p.name, valueOf(p)]));
      let columns = report.columns;
      let rows;
      if (mode === 'live') {
        if (report.isNew) throw new Error('Live data ke liye pehle report save karein');
        const live = await api.preview(report.id, parameters);
        columns = live.columns;
        rows = live.rows;
        notify(`Live data se ${live.rowCount} rows (preview max 20)`, 'ok');
      }
      const blob = await api.renderPdf({ design, columns, parameters, rows, title: report.title, reportId: report.id });
      setUrl(URL.createObjectURL(blob));
    } catch (error) {
      notify(`Preview nahi bana: ${error.message}`, 'error');
    } finally { setBusy(false); }
  };

  return (
    <div className="preview">
      <div className="preview-bar">
        <div className="seg">
          <label><input type="radio" checked={mode === 'sample'} onChange={() => setMode('sample')} /> Sample data (VPN ki zaroorat nahi)</label>
          <label><input type="radio" checked={mode === 'live'} onChange={() => setMode('live')} /> Live MongoDB (saved report, 20 rows)</label>
        </div>
        {report.parameters.map((p) => (
          <label key={p.name} className="inline-field">{p.label || p.name}
            <input type={p.type === 'date' ? 'date' : 'text'} value={valueOf(p)} onChange={(e) => setValues({ ...values, [p.name]: e.target.value })} />
          </label>
        ))}
        <button className="btn primary" onClick={run} disabled={busy}>{busy ? 'Rendering…' : 'Generate preview'}</button>
        {url && <a className="btn" href={url} target="_blank" rel="noreferrer">Open PDF ↗</a>}
      </div>
      {url ? <iframe title="PDF preview" src={url} className="pdf-frame" /> : <div className="empty">“Generate preview” dabayen — design PDF ban kar yahin dikhega.</div>}
    </div>
  );
}
