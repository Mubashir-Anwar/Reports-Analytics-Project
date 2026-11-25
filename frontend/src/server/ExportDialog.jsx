// Export ZIP: every report (.mrd) and dashboard (.mdash) in one file, in the same folders as on the server.
import { useState } from 'react';
import { FolderPicker } from '../shared/folders.jsx';
import PopDialog from './PopDialog.jsx';
import { serverApi } from './serverApi.js';

export default function ExportDialog({ folder, folders, reports, dashboards, onClose, onDone }) {
  const [scope, setScope] = useState(folder || '');
  const [withReports, setWithReports] = useState(true);
  const [withDashboards, setWithDashboards] = useState(true);
  const inside = (item) => !scope || item.folder === scope || item.folder.startsWith(`${scope}/`);
  const chosen = [...(withReports ? reports.filter(inside) : []), ...(withDashboards ? dashboards.filter(inside) : [])];
  const counts = new Map();
  chosen.forEach((item) => counts.set(item.folder, (counts.get(item.folder) || 0) + 1));
  const start = (event) => {
    event.preventDefault();
    serverApi.exportZip({ folder: scope, reports: withReports, dashboards: withDashboards });
    onDone(`The ZIP with ${chosen.length} item(s) is downloading. Open index.html in it to see which folder each item is in.`);
  };
  return (
    <PopDialog label="Export ZIP" icon="🎁" tint="g" title="Export as ZIP" subtitle="Every report (.mrd) and dashboard (.mdash) in the same folders as here, with an index. No data and no passwords." onClose={onClose} onSubmit={start}
      footer={<>
        <span className="grow">{chosen.length} item{chosen.length === 1 ? '' : 's'}</span>
        <button type="button" className="pop-btn" onClick={onClose}>Cancel</button>
        <button type="submit" className="pop-btn primary" disabled={!chosen.length}>Download ZIP</button>
      </>}>
      <div className="pop-field"><span>What</span><FolderPicker folders={folders} value={scope} onChange={setScope} rootLabel="Everything" rootHint="the whole server" label="What" /></div>
      <label className="pop-switch"><span>Reports<small>{reports.filter(inside).length} here</small></span><input type="checkbox" checked={withReports} onChange={(e) => setWithReports(e.target.checked)} /></label>
      <label className="pop-switch"><span>Dashboards<small>{dashboards.filter(inside).length} here</small></span><input type="checkbox" checked={withDashboards} onChange={(e) => setWithDashboards(e.target.checked)} /></label>
      <div className="pm-from" style={{ maxHeight: 200, overflow: 'auto' }}>
        {[...counts].sort(([a], [b]) => a.localeCompare(b)).map(([name, count]) => <div key={name}>📁 {name === 'General' ? 'Top level' : `/${name}`} <span className="pm-tag">{count}</span></div>)}
        {!chosen.length && <span>Nothing here that you may export (it needs the Manage permission on the folder).</span>}
      </div>
    </PopDialog>
  );
}
