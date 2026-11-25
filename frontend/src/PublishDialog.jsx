// "Publish to server": choose the repository folder in a tree (like Jaspersoft Studio's publish wizard)
// and check a summary of what goes to the server.
import { useMemo, useState } from 'react';
import { TargetPicker } from './shared/TakeAdapter.jsx';
import { bandKeys, bandLabel, bandPresent, getBand } from './designModel.js';

/** All folders and their parents, sorted, as tree rows { path, name, depth }. */
function treeRows(folders, extra) {
  const all = new Set(['General']);
  [...folders, extra].filter(Boolean).forEach((path) => {
    const parts = path.split('/').filter(Boolean);
    parts.forEach((_, i) => all.add(parts.slice(0, i + 1).join('/')));
  });
  return [...all].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }))
    .sort((a, b) => (a === 'General' ? -1 : b === 'General' ? 1 : 0))   // General = the top level, first
    .map((path) => ({ path, name: path === 'General' ? 'Top level (no folder)' : path.split('/').pop(), depth: path === 'General' ? 0 : path.split('/').length - 1 }));
}

export default function PublishDialog({ publishing, setPublishing, report, design, onConfirm }) {
  const [newName, setNewName] = useState('');
  const set = (patch) => setPublishing({ ...publishing, ...patch, error: '' });
  const folder = publishing.folder.trim();
  const rows = useMemo(() => treeRows(publishing.folders, folder), [publishing.folders, folder]);
  const isNewFolder = folder && !publishing.folders.includes(folder);

  const summary = useMemo(() => {
    const present = bandKeys(design).filter((key) => bandPresent(getBand(design, key)));
    const elements = present.flatMap((key) => getBand(design, key).elements);
    const count = (type) => elements.filter((el) => el.type === type).length;
    return {
      bands: present.map(bandLabel),
      elements: elements.length,
      components: [['table', 'Table'], ['crosstab', 'Crosstab'], ['list', 'List'], ['chart', 'Chart'], ['image', 'Image'], ['subreport', 'Subreport']]
        .map(([type, label]) => [label, count(type)]).filter(([, n]) => n),
      subreports: [...new Set(elements.filter((el) => el.type === 'subreport' && el.reportId).map((el) => el.reportId))],
    };
  }, [design]);

  const addFolder = () => {
    const name = newName.trim().replace(/\//g, '-');
    if (!name) return;
    set({ folder: folder && folder !== 'General' ? `${folder}/${name}` : name });
    setNewName('');
  };

  return (
    <div className="modal-back" onClick={() => !publishing.busy && setPublishing(null)}>
      <div className="modal publish-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Publish to server">
        <h3>Publish to server</h3>
        <div className="publish-grid">
          <div>
            <div className="prop-section">Server folder</div>
            <ul className="folder-tree" role="tree">
              {rows.map((row) => (
                <li key={row.path} role="treeitem" aria-selected={row.path === folder}>
                  <button type="button" className={row.path === folder ? 'on' : ''} style={{ paddingLeft: 6 + row.depth * 16 }} onClick={() => set({ folder: row.path })} title={row.path}>
                    {row.path === folder ? '📂' : '📁'} {row.name}{!publishing.folders.includes(row.path) && row.path !== 'General' ? <small> (new)</small> : null}
                  </button>
                </li>
              ))}
            </ul>
            <div className="inline new-folder">
              <input value={newName} placeholder={folder && folder !== 'General' ? `New folder in ${folder}` : 'New folder at the top level'} onChange={(e) => setNewName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && addFolder()} />
              <button type="button" className="btn sm" disabled={!newName.trim()} onClick={addFolder}>＋ Folder</button>
            </div>
            <label className="modal-field">Path<input value={publishing.folder} onChange={(e) => set({ folder: e.target.value })} /></label>
          </div>
          <div>
            <div className="prop-section">Summary</div>
            <dl className="publish-summary">
              <dt>Report</dt><dd><b>{report.title}</b> <code>{report.id}</code></dd>
              <dt>Goes to</dt><dd>{folder === 'General' ? 'the top level' : `/${folder || '…'}`}{isNewFolder && folder !== 'General' ? ' (new folder)' : ''}</dd>
              <dt>Data adapter</dt><dd><code>{report.connectionId || '—'}</code>{report.collection ? <> · collection <code>{report.collection}</code></> : null}</dd>
              <dt>Questions</dt><dd>{report.parameters.length ? report.parameters.map((p) => p.label || p.name).join(', ') : 'none'}</dd>
              <dt>Fields</dt><dd>{report.columns.length}</dd>
              <dt>Design</dt><dd>{summary.bands.length} bands, {summary.elements} elements{summary.components.length ? ` · ${summary.components.map(([l, n]) => `${n} ${l}`).join(', ')}` : ''}</dd>
              {summary.subreports.length > 0 && <><dt>Subreports</dt><dd>{summary.subreports.join(', ')} <small>(must be on the server too)</small></dd></>}
              <dt>Run it at</dt><dd><code>/server</code> · <code>/generate/{report.id}.pdf{folder ? `?Folder=${folder}` : ''}</code></dd>
            </dl>
            {publishing.sources?.length > 0 && <div className="publish-sources">
              <div className="prop-section">Data source on the Server</div>
              <small className="muted">The Designer’s data adapters work only in the Designer. Choose what the report reads from on the Server.</small>
              {publishing.sources.map((source) => <TargetPicker key={source.id} source={source} area="server" value={publishing.picked[source.id]} onChange={(value) => set({ picked: { ...publishing.picked, [source.id]: value } })} />)}
            </div>}
            {publishing.onServer && (
              <label className="modal-check warn-box"><input type="checkbox" checked={publishing.replace} onChange={(e) => set({ replace: e.target.checked })} /> {report.id} is already on the server — replace that version</label>
            )}
          </div>
        </div>
        {publishing.error && <p className="err">{publishing.error}</p>}
        <div className="row">
          <button className="btn" disabled={publishing.busy} onClick={() => setPublishing(null)}>Cancel</button>
          <button className="btn primary" disabled={publishing.busy || !folder || (publishing.sources || []).some((s) => !publishing.picked[s.id]) || (publishing.onServer && !publishing.replace)} onClick={onConfirm}>{publishing.busy ? 'Publishing…' : 'Publish'}</button>
        </div>
      </div>
    </div>
  );
}
