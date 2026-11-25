// Data sources: every data adapter of every kind (MongoDB, PostgreSQL, MySQL/MariaDB, SQL Server, Oracle), with the
// same wizard as the designer, a test per adapter or for all, which reports use it, folders and delete. Shown on
// Server, Analytics and Query with the data sources of that part only, and on Admin with those of every part.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../api.js';
import { Notice } from '../portal/Shell.jsx';
import { ADAPTER_TYPES, AREAS, AdapterWizard, DbLogo, adapterTypeLabel } from '../shared/adapters.jsx';

function describeTest(checked) {
  if (checked.readChecked?.length) return `Connected · report read verified: ${checked.readChecked.join(', ')}`;
  return checked.message || 'Connected';
}

export default function DataSourcesPanel({ notify, folders: knownFolders, area = '', onChanged }) {
  const [adapters, setAdapters] = useState(null);
  const [folders, setFolders] = useState([]);
  const [tests, setTests] = useState({}); // id -> { text, kind }
  const [wizard, setWizard] = useState(null); // null | {} new | adapter (edit) | { ...copy } (duplicate)
  const [filter, setFilter] = useState('all');
  const [part, setPart] = useState('all');   // Admin: all parts or one; '' = shared by every part
  const [search, setSearch] = useState('');
  const [openUsage, setOpenUsage] = useState('');

  const load = useCallback(async () => {
    try {
      const [c, f] = await Promise.all([api.connections(area), knownFolders ? { folders: knownFolders } : !area || area === 'server' ? api.folders().catch(() => ({ folders: [] })) : { folders: [] }]);
      setAdapters(c.connections);
      setFolders(f.folders);
    } catch (error) { setAdapters([]); notify(error.message, 'error'); }
  }, [notify, knownFolders, area]);
  useEffect(() => { load(); }, [load]);

  const test = async (adapter) => {
    setTests((t) => ({ ...t, [adapter.id]: { text: 'Testing…', kind: '' } }));
    try {
      const result = await api.testConnection(adapter.id);
      setTests((t) => ({ ...t, [adapter.id]: { text: describeTest(result), kind: 'ok' } }));
      return true;
    } catch (error) {
      setTests((t) => ({ ...t, [adapter.id]: { text: error.message, kind: 'error' } }));
      return false;
    }
  };
  const testAll = async () => {
    const list = adapters.filter((a) => a.configured);
    const results = await Promise.all(list.map(test));
    const ok = results.filter(Boolean).length;
    notify(`${ok} of ${list.length} data sources answered${list.length - ok ? `; ${list.length - ok} failed (see below)` : ''}.`, list.length - ok ? 'error' : 'ok');
  };
  const move = async (adapter, folder) => {
    try { await api.moveConnection(adapter.id, folder); await load(); notify(`“${adapter.name}” moved to ${folder || 'the top level'}.`); }
    catch (error) { notify(error.message, 'error'); }
  };
  const remove = async (adapter) => {
    if (!window.confirm(`Delete the data source “${adapter.name}”? Its saved password is deleted too.`)) return;
    try { await api.deleteConnection(adapter.id); await load(); onChanged?.(); notify(`Data source “${adapter.name}” deleted.`); }
    catch (error) { notify(error.message, 'error'); }
  };

  const giveTo = async (adapter, to) => {
    try { await api.setConnectionArea(adapter.id, to); await load(); notify(`“${adapter.name}” now belongs to ${AREAS[to].name} only.`); }
    catch (error) { notify(error.message, 'error'); }
  };
  const split = async (adapter) => {
    if (!window.confirm(`Make a separate data source of “${adapter.name}” for Designer, Server, Analytics and Query? Reports, drafts and datasets keep working, each with the one of its own part.`)) return;
    try { await api.splitConnection(adapter.id); await load(); notify(`“${adapter.name}” is now four separate data sources, one per part.`); }
    catch (error) { notify(error.message, 'error'); }
  };
  const usesOf = (a) => Object.entries(a.uses || {}).filter(([, names]) => names.length);
  const useText = (a) => usesOf(a).map(([key, names]) => `${names.length} ${{ server: ['published report', 'published reports'], designer: ['draft', 'drafts'], analytics: ['dataset or query', 'datasets and queries'] }[key][names.length > 1 ? 1 : 0]}`).join(', ');

  const shown = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return (adapters || [])
      .filter((a) => area || part === 'all' || (a.area || '') === part)
      .filter((a) => filter === 'all' || (a.type || 'mongodb') === filter)
      .filter((a) => !needle || `${a.name} ${a.id} ${a.database} ${a.folder}`.toLowerCase().includes(needle))
      .sort((a, b) => (a.folder || '').localeCompare(b.folder || '') || a.name.localeCompare(b.name));
  }, [adapters, filter, search, part, area]);
  const counts = useMemo(() => {
    const out = {};
    (adapters || []).forEach((a) => { out[a.type || 'mongodb'] = (out[a.type || 'mongodb'] || 0) + 1; });
    return out;
  }, [adapters]);

  return (
    <section className="card">
      <div className="card-head">
        <div><p className="eyebrow">Data sources</p><h2>{area ? `Data sources of ${AREAS[area].name}` : 'Data sources of every part'}</h2>
          <p className="muted">{area ? `Connect your databases here. They are only for ${AREAS[area].name} (${AREAS[area].use}); the other parts have their own.` : 'Each data source belongs to one part (Designer, Server, Analytics or Query) and works only there.'} Passwords are stored on the server (encrypted when REPORTING_SECRETS_KEY is set) and never shown again.</p></div>
        <div className="row">
          <button className="btn" onClick={testAll} disabled={!adapters?.some((a) => a.configured)}>⟳ Test all</button>
          <button className="btn primary" onClick={() => setWizard({})}>＋ New data source</button>
        </div>
      </div>

      {!area && (
        <div className="segmented wrap ds-parts" role="tablist" aria-label="Filter by part">
          {[['all', 'All'], ...Object.entries(AREAS).map(([id, a]) => [id, a.name]), ...((adapters || []).some((a) => !a.area) ? [['', 'Shared (older)']] : [])].map(([id, label]) => (
            <button key={id} className={part === id ? 'on' : ''} onClick={() => setPart(id)}>{label} ({(adapters || []).filter((a) => id === 'all' || (a.area || '') === id).length})</button>
          ))}
        </div>
      )}
      <div className="row ds-filters">
        <div className="segmented wrap" role="tablist" aria-label="Filter by database type">
          <button className={filter === 'all' ? 'on' : ''} onClick={() => setFilter('all')}>All ({adapters?.length || 0})</button>
          {ADAPTER_TYPES.filter((t) => counts[t.id]).map((t) => (
            <button key={t.id} className={filter === t.id ? 'on' : ''} onClick={() => setFilter(t.id)}><DbLogo type={t.id} /> {adapterTypeLabel(t.id)} ({counts[t.id]})</button>
          ))}
        </div>
        <input type="search" placeholder="⌕ Find a data source" value={search} onChange={(e) => setSearch(e.target.value)} style={{ maxWidth: 260 }} />
      </div>

      <div className="stack" style={{ marginTop: 12 }}>
        {adapters === null && <div className="empty">Loading…</div>}
        {adapters?.length === 0 && <div className="empty">No data sources yet. “＋ New data source” starts the wizard (MongoDB, PostgreSQL, MySQL, SQL Server, Oracle).</div>}
        {shown.map((a) => (
          <div key={a.id} className="ds-item">
            <span className="ds-logo"><DbLogo type={a.type || 'mongodb'} size={30} /></span>
            <div className="ds-main">
              <strong>{a.name} <code>{a.id}</code> <span className={`ds-part ${a.area || 'shared'}`} title={a.area ? `Works only in ${AREAS[a.area].name}` : 'Made before every part had its own data sources: works in all of them'}>{a.area ? AREAS[a.area].name : 'Shared by all parts'}</span></strong>
              <small>{adapterTypeLabel(a.type)} · {a.database || 'database not set'} · {a.credentialSource}</small>
              <div className="row ds-meta">
                <span className={`status-chip ${a.configured ? 'ok' : 'error'}`}>{a.configured ? 'Configured' : 'Setup needed'}</span>
                <button type="button" className="link-btn" onClick={() => setOpenUsage(openUsage === a.id ? '' : a.id)} disabled={!usesOf(a).length}>
                  {usesOf(a).length ? `Used by ${useText(a)}` : 'Not used yet'}
                </button>
                {(!a.area || a.area === 'server') && (!area || area === 'server') && <label className="ds-folder">📁
                  <select aria-label={`Folder of ${a.name}`} value={a.folder || ''} onChange={(e) => move(a, e.target.value)}>
                    <option value="">Top level</option>
                    {folders.map((f) => <option key={f} value={f}>{f.split('/').join(' / ')}</option>)}
                  </select>
                </label>}
                {!area && !a.area && <>
                  <select aria-label={`Part of ${a.name}`} value="" onChange={(e) => e.target.value && giveTo(a, e.target.value)}>
                    <option value="">Give to one part…</option>{Object.entries(AREAS).map(([id, x]) => <option key={id} value={id}>{x.name}</option>)}
                  </select>
                  <button type="button" className="link-btn" onClick={() => split(a)} title="A separate data source for each part; everything keeps working">Split per part</button>
                </>}
              </div>
              {openUsage === a.id && <p className="muted ds-usage">{usesOf(a).map(([key, names]) => `${AREAS[key].name}: ${names.join(', ')}`).join(' · ')}</p>}
              {tests[a.id] && <Notice message={tests[a.id]} />}
            </div>
            <div className="row ds-actions">
              <button className="btn sm" onClick={() => test(a)} disabled={!a.configured}>Test</button>
              <button className="btn sm" onClick={() => setWizard(a)} title="Change the connection (enter it again; the old password is replaced)">✎ Edit</button>
              <button className="btn sm quiet" onClick={() => setWizard({ type: a.type, name: `${a.name} copy`, database: a.database, folder: a.folder, area: a.area })} title="A new data source with the same settings">⧉</button>
              <button className="btn sm danger" onClick={() => remove(a)} disabled={usesOf(a).length > 0}
                  title={usesOf(a).length ? 'Choose another data source where it is used first' : 'Delete this data source'}>✕</button>
            </div>
          </div>
        ))}
        {adapters?.length > 0 && !shown.length && <div className="empty">No data source matches.</div>}
      </div>

      {wizard && (
        <AdapterWizard initial={wizard} folders={folders} existingIds={(adapters || []).filter((a) => area && (!a.area || a.area === area)).map((a) => a.id)} area={area || (part !== 'all' ? part : '')} areas={area ? null : Object.keys(AREAS)} notify={notify}
          onClose={() => setWizard(null)} onSaved={async () => { setWizard(null); await load(); onChanged?.(); }} />
      )}
    </section>
  );
}
