// The folder of a dataset, Python script, pipeline or query in Analytics: the folder tree of the Analytics Server,
// with New folder / Subfolder / Rename / Delete under it (Designer or Admin right and Manage on the folder).
// "Analytics" is the usual folder and stands at the top of the tree.
import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { FolderPicker } from '../shared/folders.jsx';
import { bi } from './api.js';

const HOME = 'Analytics';

export default function AnalyticsFolder({ value, onChange, folders: known = [], disabled = false, notify = () => {}, onFoldersChanged, mayManage = true }) {
  const [listed, setListed] = useState([]);
  const [removing, setRemoving] = useState(null);           // { path, moveTo, error, busy }
  const load = () => bi.folders().then((answer) => setListed(answer.folders || [])).catch(() => {});
  useEffect(() => { load(); }, []);
  const all = [...new Set([...listed, ...known])].filter((f) => f && f !== HOME && f !== 'General');
  const changed = async () => { await load(); onFoldersChanged?.(); };
  const inside = (path, folder) => path === folder || path.startsWith(`${folder}/`);

  const manage = mayManage ? {
    create: async (path) => {
      await bi.createFolder(path);
      await changed();
      notify(`Folder /${path} made`, 'ok');
    },
    rename: async (path, newPath) => {
      const answer = await bi.renameFolder(path, newPath);
      if (value && inside(value, path)) onChange(newPath + value.slice(path.length));
      await changed();
      notify(`/${path} is now /${newPath}${answer.moved ? ` (${answer.moved} item${answer.moved === 1 ? '' : 's'} moved with it)` : ''}`, 'ok');
    },
    remove: (path) => setRemoving({ path, moveTo: HOME, error: '', busy: false }),
  } : null;

  const confirm = async () => {
    setRemoving({ ...removing, busy: true, error: '' });
    try {
      const answer = await bi.deleteFolder(removing.path, removing.moveTo);
      if (value && inside(value, removing.path)) onChange(removing.moveTo || HOME);
      await changed();
      notify(`Folder /${removing.path} deleted${answer.moved ? ` · ${answer.moved} item${answer.moved === 1 ? '' : 's'} moved to /${answer.movedTo}` : ''}`, 'ok');
      setRemoving(null);
    } catch (failure) {
      setRemoving((current) => ({ ...current, busy: false, error: failure.message }));
    }
  };
  const targets = [HOME, ...all.filter((f) => removing && !inside(f, removing.path))];

  return (
    <>
      <FolderPicker folders={all} value={value === HOME ? '' : value || ''} disabled={disabled} allowNew rootLabel={HOME} rootHint="the usual folder"
        storageKey="bi-folder-open" manage={manage} onChange={(path) => onChange(path || HOME)} />
      {removing && createPortal(
        <div className="bi-modal-back" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget && !removing.busy) setRemoving(null); }}>
          <div className="bi-folder-delete" role="dialog" aria-modal="true" aria-labelledby="bi-folder-delete-title">
            <h3 id="bi-folder-delete-title">Delete the folder /{removing.path}?</h3>
            <p className="muted">Its subfolders go too. Datasets, dashboards and queries in it are not deleted: they move to the folder you choose.</p>
            <label className="field"><span>Move what is inside to</span>
              <select value={removing.moveTo} onChange={(e) => setRemoving({ ...removing, moveTo: e.target.value })}>
                {targets.map((f) => <option key={f} value={f}>/{f}</option>)}
              </select>
            </label>
            {removing.error && <p className="notice bad" role="alert">{removing.error}</p>}
            <div className="row end">
              <button type="button" className="btn" disabled={removing.busy} onClick={() => setRemoving(null)}>Cancel</button>
              <button type="button" className="btn danger" disabled={removing.busy} onClick={confirm}>{removing.busy ? 'Deleting…' : 'Delete folder'}</button>
            </div>
          </div>
        </div>, document.body)}
    </>
  );
}
