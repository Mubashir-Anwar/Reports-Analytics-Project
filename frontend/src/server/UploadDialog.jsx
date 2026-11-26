// Add Resource → Upload: put a .mrd report file (or a .jrxml), or a .mdash dashboard file, into a server folder.
import { useEffect, useRef, useState } from 'react';
import { notADashboardFile } from '../analytics/api.js';
import { FolderPicker } from '../shared/folders.jsx';
import PopDialog from './PopDialog.jsx';
import { serverApi } from './serverApi.js';
import { splitReportFiles } from '../mrd.js';

const KIND = {
  mrd: ['.mrd', 'Report'], jrxml: ['.jrxml', 'JRXML report, converted on upload'], dashboard: ['.mdash', 'Dashboard'],
};
const ACCEPT = '.mrd,.jrxml,.xml,.mdash,.png,.jpg,.jpeg';

/** What a .mrd file reads from: {adapter, sql, reads} ({} when it cannot be told; the server checks the file anyway). */
function mrdSource(text) {
  try {
    const node = new DOMParser().parseFromString(text, 'application/xml').querySelector('[encoding="json"]');
    const report = JSON.parse(node.textContent);
    return {
      adapter: report.connectionId || '', sql: report.queryLanguage === 'sql',
      reads: report.queryLanguage !== 'none' && Boolean(String(report.querySource || '').trim() || String(report.collection || '').trim()),
    };
  } catch { return {}; }
}

export default function UploadDialog({ folders, folder, onClose, onDone }) {
  const [file, setFile] = useState(null); // { name, text, kind }
  const [target, setTarget] = useState(folder && folder !== 'General' ? folder : '');
  const [replace, setReplace] = useState(false);
  const [newId, setNewId] = useState('');
  const [connectionId, setConnectionId] = useState('');
  const [collection, setCollection] = useState('');
  const [connections, setConnections] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [over, setOver] = useState(false);
  const input = useRef(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  useEffect(() => { serverApi.connections().then((d) => { setConnections(d.connections); setLoaded(true); }).catch(() => {}); }, []);

  // the adapters this file can read from; a .jrxml file names none of this server, and a .mrd may name one that is not here
  const isSql = (c) => (c.type || 'mongodb') !== 'mongodb';
  const usable = connections.filter((c) => (file?.kind === 'jrxml' ? !isSql(c) : file?.kind === 'mrd' && file.sql !== undefined ? isSql(c) === file.sql : true));
  const inFile = file?.kind === 'mrd' && connections.find((c) => c.id === file.adapter);
  const needsAdapter = loaded && (file?.kind === 'jrxml' || (file?.kind === 'mrd' && file.reads === true && !inFile));
  const onlyOne = needsAdapter && usable.length === 1 ? usable[0].id : '';
  const adapter = connectionId || onlyOne;

  const choose = async (list) => {
    setConnectionId('');
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
    setFile({ name: chosen.name, text, kind, images: kind === 'jrxml' ? images : {}, ...(kind === 'mrd' ? mrdSource(text) : {}) });
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
        onDone(await serverApi.uploadDashboard({ content: file.text, folder: target.trim() || 'General', replace, ...(adapter ? { connectionId: adapter } : {}) }));
        return;
      }
      const result = await serverApi.upload({
        content: file.text, folder: target.trim() || 'General', replace,
        ...(Object.keys(file.images || {}).length ? { resources: file.images } : {}),
        ...(newId.trim() ? { newId: newId.trim() } : {}), ...(adapter ? { connectionId: adapter } : {}), ...(collection.trim() ? { collection: collection.trim() } : {}),
      });
      onDone(result);
    } catch (error) { setMessage({ text: error.message, kind: 'error' }); }
    finally { setBusy(false); }
  };

  const isDashboard = file?.kind === 'dashboard';
  const pictures = Object.keys(file?.images || {});
  const onFiles = (event) => { if (event.target.files.length) choose([...event.target.files]); event.target.value = ''; };
  return (
    <PopDialog label="Add resource" icon="📦" tint="v" title="Add a resource" subtitle="Drop in a report or dashboard file. It is live in its folder right away." busy={busy} onClose={onClose} onSubmit={upload}
      footer={<>
        <button type="button" className="pop-btn" disabled={busy} onClick={onClose}>Cancel</button>
        <button type="submit" className="pop-btn primary" disabled={busy || !file?.kind || (needsAdapter && !adapter)}>{busy ? 'Uploading…' : 'Upload'}</button>
      </>}>
      {file?.kind ? (
        <div className={`up-file ${file.kind}`}>
          <i>{KIND[file.kind][0]}</i>
          <div>
            <b>{file.name}</b>
            <small>{KIND[file.kind][1]}{isDashboard ? ` · ${file.id} · ${file.datasets} dataset${file.datasets === 1 ? '' : 's'}` : ` · ID ${file.id || 'not given'}`}</small>
            {pictures.length > 0 && <small>🖼 {pictures.join(', ')}</small>}
          </div>
          <button type="button" className="pop-btn small" disabled={busy} onClick={() => input.current?.click()}>Change</button>
          <input ref={input} type="file" hidden accept={ACCEPT} multiple onChange={onFiles} />
        </div>
      ) : (
        <label className={`up-drop ${over ? 'over' : ''}`} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); if (e.dataTransfer.files.length) choose([...e.dataTransfer.files]); }}>
          <i aria-hidden="true">📂</i>
          <b>{over ? 'Let go!' : 'Drop your file here'}</b>
          <p>or click to choose it from this computer</p>
          <div className="up-kinds"><span className="mrd">.mrd report</span><span className="jrxml">.jrxml report</span><span className="dashboard">.mdash dashboard</span><span className="img">＋ PNG / JPEG of a .jrxml</span></div>
          <input type="file" aria-label="Report or dashboard file" accept={ACCEPT} multiple onChange={onFiles} />
        </label>
      )}
      {file?.kind === 'jrxml' && !pictures.length && <p className="pop-note">Does it use pictures? Choose the .jrxml and its PNG/JPEG files together (Ctrl+click): they are saved inside the report.</p>}
      {message?.text && <p className="pop-error" role="alert">{message.text}</p>}
      <div className="pop-field"><span>Folder</span><FolderPicker folders={folders} value={target} onChange={setTarget} allowNew disabled={busy} /></div>
      {file?.kind && <>
        <div className="pop-field"><span>{isDashboard ? 'Data adapter of its datasets' : 'Data adapter'}</span>
          <div className="up-adapters" role="group" aria-label="Data adapter">
            {!needsAdapter && <button type="button" className={adapter ? '' : 'on'} aria-pressed={!adapter} onClick={() => setConnectionId('')}><b>As in the file</b><small>{inFile ? inFile.name : 'keep what it says'}</small></button>}
            {usable.map((c) => <button key={c.id} type="button" className={adapter === c.id ? 'on' : ''} aria-pressed={adapter === c.id} onClick={() => setConnectionId(c.id)}><b>{c.name}</b><small>{c.type || 'mongodb'}{c.folder ? ` · /${c.folder}` : ''}</small></button>)}
          </div>
          {needsAdapter && <small>{!usable.length ? 'This server has no data source it can read from yet. Add one under Data sources first.'
            : file.kind === 'mrd' && file.adapter ? `“${file.adapter}” in the file is not on this server: choose where it reads from.` : 'Choose where this report reads its data from.'}</small>}
        </div>
        {!isDashboard && (
          <div className="pop-two">
            <label className="pop-field"><span>New ID</span><input value={newId} onChange={(e) => setNewId(e.target.value)} placeholder={file.id || 'keep the file’s ID'} /><small>Optional</small></label>
            {file.kind === 'jrxml' && <label className="pop-field"><span>Collection</span><input value={collection} onChange={(e) => setCollection(e.target.value)} placeholder="from its query" /><small>Only if the query does not name one</small></label>}
          </div>
        )}
        <label className="pop-switch"><span>Replace what is there<small>{isDashboard ? 'If this dashboard is already on the server' : 'If a report with this ID is already in the folder'}</small></span>
          <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} /></label>
      </>}
    </PopDialog>
  );
}
