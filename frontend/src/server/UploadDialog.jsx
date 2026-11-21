// Add Resource → Upload: put a .mrd report file (or a Jaspersoft Studio .jrxml), or a .mdash dashboard file, into a server folder.
import { useEffect, useState } from 'react';
import { Notice } from '../portal/Shell.jsx';
import { notADashboardFile } from '../analytics/api.js';
import { serverApi } from './serverApi.js';
import { splitReportFiles } from '../mrd.js';

export default function UploadDialog({ folders, folder, onClose, onDone }) {
  const [file, setFile] = useState(null); // { name, text, kind }
  const [target, setTarget] = useState(folder || '');
  const [replace, setReplace] = useState(false);
  const [newId, setNewId] = useState('');
  const [connectionId, setConnectionId] = useState('');
  const [collection, setCollection] = useState('');
  const [connections, setConnections] = useState([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  useEffect(() => { serverApi.connections().then((d) => setConnections(d.connections)).catch(() => {}); }, []);

  const choose = async (list) => {
    // the report file, plus the PNG/JPEG pictures a .jrxml uses (chosen together with it)
    const { report: chosen, images, skipped } = await splitReportFiles(list);
    if (!chosen) { setFile(null); setMessage({ text: 'Choose the .mrd, .jrxml or .mdash file too (pictures go together with a .jrxml)', kind: 'error' }); return; }
    const lower = chosen.name.toLowerCase();
    if (/\.(pbi[xtp]|zip)$/.test(lower) || chosen.size > 24_000_000) {
      setFile(null);
      setMessage({ text: await notADashboardFile(chosen) || `This file is larger than the 25 MB the server accepts in one upload`, kind: 'error' });
      return;
    }
    const text = await chosen.text();
    let dashboard = null;
    try { const parsed = JSON.parse(text); if (parsed?.format === 'analysis-dashboard') dashboard = parsed; } catch { /* not JSON: a report file */ }
    if (dashboard) {
      setFile({ name: chosen.name, text, kind: 'dashboard', id: dashboard.dashboard?.name || '', datasets: (dashboard.datasets || []).length });
      setMessage(null);
      return;
    }
    const kind = /<jasperReport[\s>]/.test(text) ? 'jrxml' : /<mongoReportDefinition[\s>]/.test(text) ? 'mrd' : '';
    setFile({ name: chosen.name, text, kind, images: kind === 'jrxml' ? images : {} });
    setMessage(!kind ? { text: 'This is not a .mrd, .jrxml or .mdash file', kind: 'error' }
      : skipped.length ? { text: `Left out (over 2.4 MB): ${skipped.join(', ')}`, kind: 'error' } : null);
    const id = text.match(/reportId="([^"]+)"/)?.[1] || text.match(/<jasperReport[^>]*\sname="([^"]+)"/)?.[1] || '';
    setNewId('');
    setFile((f) => ({ ...f, id }));
  };

  const upload = async (event) => {
    event.preventDefault();
    setBusy(true);
    try {
      if (file.kind === 'dashboard') {
        onDone(await serverApi.uploadDashboard({ content: file.text, folder: target.trim() || 'General', replace, ...(connectionId ? { connectionId } : {}) }));
        return;
      }
      const result = await serverApi.upload({
        content: file.text, folder: target.trim() || 'General', replace,
        ...(Object.keys(file.images || {}).length ? { resources: file.images } : {}),
        ...(newId.trim() ? { newId: newId.trim() } : {}), ...(connectionId ? { connectionId } : {}), ...(collection.trim() ? { collection: collection.trim() } : {}),
      });
      onDone(result);
    } catch (error) { setMessage({ text: error.message, kind: 'error' }); }
    finally { setBusy(false); }
  };

  return (
    <div className="dialog-back" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <form className="card dialog" onSubmit={upload} role="dialog" aria-label="Add resource">
        <div className="card-head"><div><p className="eyebrow">Add resource</p><h2>Upload a report or dashboard file</h2></div></div>
        <p className="muted" style={{ fontSize: 13 }}>A <b>.mrd</b> file holds the whole report (query, parameters, data adapter, design and images) — make one in the Designer with “Save as file” or download it here. A Jaspersoft Studio <b>.jrxml</b> is converted on upload. A <b>.mdash</b> file holds a dashboard with its datasets' definitions (no data): they are imported again here.</p>
        <label className="field"><span>File</span><input type="file" accept=".mrd,.jrxml,.xml,.mdash,.png,.jpg,.jpeg" multiple onChange={(e) => choose(e.target.files)} required /></label>
        <p className="muted" style={{ fontSize: 12, marginTop: -6 }}>For a .jrxml, select its PNG/JPEG pictures together with it (Ctrl+click): they are saved inside the report, and the report's “Files in this report” list shows which file each image uses.</p>
        {file?.kind && <p className="muted" style={{ fontSize: 12 }}>{file.kind === 'dashboard' ? <>Dashboard <b>{file.id}</b> · {file.datasets} dataset(s)</> : <>{file.kind === 'mrd' ? 'Report file' : 'Jaspersoft JRXML'} · ID <code>{file.id || '?'}</code>{Object.keys(file.images || {}).length ? <> · pictures: {Object.keys(file.images).join(', ')}</> : null}</>}</p>}
        <label className="field"><span>Folder</span>
          <input list="upload-folders" value={target} onChange={(e) => setTarget(e.target.value)} placeholder="Empty = top level (no folder)" />
          <datalist id="upload-folders">{folders.map((f) => <option key={f} value={f} />)}</datalist>
        </label>
        <div className="params" style={{ margin: 0 }}>
          {file?.kind !== 'dashboard' && <label className="field"><span>New ID (optional)</span><input value={newId} onChange={(e) => setNewId(e.target.value)} placeholder={file?.id || 'keep the file’s ID'} /></label>}
          <label className="field"><span>{file?.kind === 'dashboard' ? 'Data adapter for its datasets' : 'Data adapter'}</span>
            <select value={connectionId} onChange={(e) => setConnectionId(e.target.value)}>
              <option value="">{file?.kind === 'jrxml' ? 'default' : 'as in the file'}</option>
              {connections.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.id})</option>)}
            </select>
          </label>
          {file?.kind === 'jrxml' && <label className="field"><span>Collection (if not in the query)</span><input value={collection} onChange={(e) => setCollection(e.target.value)} /></label>}
        </div>
        <label className="check"><input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} /> {file?.kind === 'dashboard' ? 'Replace the dashboard if it is already on this server' : 'Replace a report with the same ID'}</label>
        <Notice message={message} />
        <div className="row" style={{ justifyContent: 'flex-end' }}>
          <button type="button" className="btn" disabled={busy} onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy || !file?.kind}>{busy ? 'Uploading…' : 'Upload'}</button>
        </div>
      </form>
    </div>
  );
}
