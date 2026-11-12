// Add Resource → Upload: put a .mrd report file (or a Jaspersoft Studio .jrxml) into a server folder.
import { useEffect, useState } from 'react';
import { Notice } from '../portal/Shell.jsx';
import { serverApi } from './serverApi.js';

export default function UploadDialog({ folders, folder, onClose, onDone }) {
  const [file, setFile] = useState(null); // { name, text, kind }
  const [target, setTarget] = useState(folder || 'General');
  const [replace, setReplace] = useState(false);
  const [newId, setNewId] = useState('');
  const [connectionId, setConnectionId] = useState('');
  const [collection, setCollection] = useState('');
  const [connections, setConnections] = useState([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  useEffect(() => { serverApi.connections().then((d) => setConnections(d.connections)).catch(() => {}); }, []);

  const choose = async (chosen) => {
    if (!chosen) return;
    const text = await chosen.text();
    const kind = /<jasperReport[\s>]/.test(text) ? 'jrxml' : /<mongoReportDefinition[\s>]/.test(text) ? 'mrd' : '';
    setFile({ name: chosen.name, text, kind });
    setMessage(kind ? null : { text: 'This is not a .mrd or .jrxml report file', kind: 'error' });
    const id = text.match(/reportId="([^"]+)"/)?.[1] || text.match(/<jasperReport[^>]*\sname="([^"]+)"/)?.[1] || '';
    setNewId('');
    setFile((f) => ({ ...f, id }));
  };

  const upload = async (event) => {
    event.preventDefault();
    setBusy(true);
    try {
      const result = await serverApi.upload({
        content: file.text, folder: target.trim() || 'General', replace,
        ...(newId.trim() ? { newId: newId.trim() } : {}), ...(connectionId ? { connectionId } : {}), ...(collection.trim() ? { collection: collection.trim() } : {}),
      });
      onDone(result);
    } catch (error) { setMessage({ text: error.message, kind: 'error' }); }
    finally { setBusy(false); }
  };

  return (
    <div className="dialog-back" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <form className="card dialog" onSubmit={upload} role="dialog" aria-label="Add resource">
        <div className="card-head"><div><p className="eyebrow">Add resource</p><h2>Upload a report file</h2></div></div>
        <p className="muted" style={{ fontSize: 13 }}>A <b>.mrd</b> file holds the whole report (query, parameters, data adapter, design and images) — make one in the Designer with “Save as file” or download it here. A Jaspersoft Studio <b>.jrxml</b> is converted on upload.</p>
        <label className="field"><span>File</span><input type="file" accept=".mrd,.jrxml,.xml" onChange={(e) => choose(e.target.files[0])} required /></label>
        {file?.kind && <p className="muted" style={{ fontSize: 12 }}>{file.kind === 'mrd' ? 'Report file' : 'Jaspersoft JRXML'} · ID <code>{file.id || '?'}</code></p>}
        <label className="field"><span>Folder</span>
          <input list="upload-folders" value={target} onChange={(e) => setTarget(e.target.value)} required />
          <datalist id="upload-folders">{folders.map((f) => <option key={f} value={f} />)}</datalist>
        </label>
        <div className="params" style={{ margin: 0 }}>
          <label className="field"><span>New ID (optional)</span><input value={newId} onChange={(e) => setNewId(e.target.value)} placeholder={file?.id || 'keep the file’s ID'} /></label>
          <label className="field"><span>Data adapter</span>
            <select value={connectionId} onChange={(e) => setConnectionId(e.target.value)}>
              <option value="">{file?.kind === 'jrxml' ? 'default' : 'as in the file'}</option>
              {connections.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.id})</option>)}
            </select>
          </label>
          {file?.kind === 'jrxml' && <label className="field"><span>Collection (if not in the query)</span><input value={collection} onChange={(e) => setCollection(e.target.value)} /></label>}
        </div>
        <label className="check"><input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} /> Replace a report with the same ID</label>
        <Notice message={message} />
        <div className="row" style={{ justifyContent: 'flex-end' }}>
          <button type="button" className="btn" disabled={busy} onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy || !file?.kind}>{busy ? 'Uploading…' : 'Upload'}</button>
        </div>
      </form>
    </div>
  );
}
