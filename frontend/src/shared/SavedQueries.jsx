// My queries: a user's own queries (MongoDB pipeline or SQL), private unless shared with named people or everyone.
// Used in Query (save, open, edit, delete, share) and in the Designer (use one as the report's query).
import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { api } from '../api.js';
import { useMe } from './session.js';
import './saved-queries.css';

export const SAVED_EVENT = 'saved-queries-changed';
const changed = () => window.dispatchEvent(new Event(SAVED_EVENT));

/** { mine, shared, me, loading, error, reload } — kept fresh when any page part saves or deletes one. */
export function useSavedQueries() {
  const [state, setState] = useState({ mine: [], shared: [], me: '', loading: true, error: '' });
  const reload = () => api.savedQueries().then((value) => setState({ ...value, loading: false, error: '' }))
    .catch((failure) => setState((s) => ({ ...s, loading: false, error: failure.message })));
  useEffect(() => {
    reload();
    window.addEventListener(SAVED_EVENT, reload);
    return () => window.removeEventListener(SAVED_EVENT, reload);
  }, []);
  return { ...state, reload };
}

/** The Mongo Shell text of a saved MongoDB query (what the Designer reads). */
export const mongoCode = (collection, stages) =>
  `db.getCollection("${collection}").aggregate([\n${stages.filter((s) => s.on !== false).map((s) => `  { ${s.op}: ${(s.body || '{}').trim().replace(/\n/g, '\n  ')} }`).join(',\n')}\n])`;

/**
 * Save (or change) a query: name, what it is for, who may use it. `query` = { kind, connectionId, collection, text, stages };
 * `existing` = the saved query being changed (its owner only) or null for a new one.
 * onSaved(savedQuery).
 */
export function SaveQueryDialog({ query, existing, onSaved, onClose, title }) {
  const [name, setName] = useState(existing?.name || '');
  const [description, setDescription] = useState(existing?.description || '');
  const [people, setPeople] = useState([]);
  const [shared, setShared] = useState(existing?.sharedWith || []);
  const [find, setFind] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.queryPeople().then((value) => setPeople(value.people || [])).catch(() => setPeople([])); }, []);
  useEffect(() => {
    const escape = (event) => { if (event.key === 'Escape' && !busy) onClose(); };
    document.addEventListener('keydown', escape);
    return () => document.removeEventListener('keydown', escape);
  }, [onClose, busy]);
  const everyone = shared.includes('*');
  const matches = useMemo(() => people.filter((p) => !shared.includes(p) && (!find.trim() || p.toLowerCase().includes(find.trim().toLowerCase()))).slice(0, 8), [people, shared, find]);
  const save = async (asNew) => {
    if (!name.trim()) { setError('Give the query a name'); return; }
    setBusy(true); setError('');
    const body = { ...query, name: name.trim(), description, sharedWith: shared };
    try {
      const answer = existing && !asNew ? await api.updateQuery(existing.id, body) : await api.saveQuery(body);
      changed();
      onSaved(answer.query);
    } catch (failure) { setError(failure.message); setBusy(false); }
  };
  return createPortal(
    <div className="sq-back" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
      <form className="sq-dialog px-in" role="dialog" aria-modal="true" aria-labelledby="sq-title" onSubmit={(event) => { event.preventDefault(); save(false); }}>
        <div className="sq-head">
          <p className="px-kicker"><b>{query.kind === 'sql' ? 'SQL' : 'Mongo'}</b> — my queries</p>
          <h2 id="sq-title">{title || (existing ? 'Change the saved query' : 'Save this query')}</h2>
          <button type="button" className="sq-x" aria-label="Close" onClick={onClose}>✕</button>
        </div>
        <label className="sq-field"><span>Name</span>
          <input autoFocus value={name} maxLength={120} onChange={(event) => { setName(event.target.value); setError(''); }} placeholder="e.g. Branch-wise amount" />
        </label>
        <label className="sq-field"><span>What it is for <em>optional</em></span>
          <input value={description} maxLength={500} onChange={(event) => setDescription(event.target.value)} />
        </label>
        <fieldset className="sq-share">
          <legend>Who can use it</legend>
          <div className="sq-seg" role="radiogroup">
            <button type="button" role="radio" aria-checked={!shared.length} className={!shared.length ? 'on' : ''} onClick={() => setShared([])}>🔒 Only me</button>
            <button type="button" role="radio" aria-checked={shared.length > 0 && !everyone} className={shared.length > 0 && !everyone ? 'on' : ''}
              onClick={() => setShared(shared.filter((p) => p !== '*'))}>👥 People I choose</button>
            <button type="button" role="radio" aria-checked={everyone} className={everyone ? 'on' : ''} onClick={() => setShared(['*'])}>🌐 Everyone</button>
          </div>
          {!everyone && (
            <div className="sq-people">
              {shared.map((p) => <span key={p} className="sq-chip">{p}<button type="button" aria-label={`Stop sharing with ${p}`} onClick={() => setShared(shared.filter((x) => x !== p))}>×</button></span>)}
              <div className="sq-find">
                <input value={find} onChange={(event) => setFind(event.target.value)} placeholder={people.length ? 'Add a person…' : 'No other users yet'} disabled={!people.length}
                  onKeyDown={(event) => { if (event.key === 'Enter' && matches[0]) { event.preventDefault(); setShared([...shared, matches[0]]); setFind(''); } }} />
                {find && matches.length > 0 && (
                  <ul className="sq-hints" role="listbox">
                    {matches.map((p) => <li key={p}><button type="button" onClick={() => { setShared([...shared, p]); setFind(''); }}>{p}</button></li>)}
                  </ul>
                )}
              </div>
            </div>
          )}
          <small className="muted">{everyone ? 'Everyone with the Query or Designer right sees it and can use it.' : shared.length ? 'They can use it and save a copy; only you can change it.' : 'Nobody else sees it.'}</small>
        </fieldset>
        {error && <p className="sq-error" role="alert">{error}</p>}
        <div className="sq-actions">
          <button type="button" className="btn" disabled={busy} onClick={onClose}>Cancel</button>
          {existing && <button type="button" className="btn" disabled={busy} onClick={() => save(true)}>Save as new</button>}
          <button type="submit" className="btn primary" disabled={busy || !name.trim()}>{busy ? 'Saving…' : existing ? 'Save changes' : 'Save query'}</button>
        </div>
      </form>
    </div>,
    document.body,
  );
}

/** "Delete the saved query?" */
export async function deleteSavedQuery(item) {
  // eslint-disable-next-line no-alert
  if (!window.confirm(`Delete your saved query “${item.name}”? People it is shared with lose it too.`)) return false;
  await api.deleteQuery(item.id);
  changed();
  return true;
}

/** The list of my queries and the ones shared with me, with a search. onOpen(item); onEdit(item) for mine. */
export function SavedQueryList({ saved, onOpen, onEdit, onDelete, filter = '', kinds = null, empty }) {
  const needle = filter.trim().toLowerCase();
  const pick = (items) => items.filter((q) => (!kinds || kinds.includes(q.kind)) && (!needle || `${q.name} ${q.description || ''} ${q.collection || ''}`.toLowerCase().includes(needle)));
  const mine = pick(saved.mine);
  const shared = pick(saved.shared);
  if (saved.loading) return <div className="sq-note"><span className="px-loader" aria-hidden="true"><i /><i /><i /></span> Loading…</div>;
  if (saved.error) return <div className="sq-note err">{saved.error}</div>;
  if (!mine.length && !shared.length) return <div className="sq-note">{needle ? 'No saved query matches.' : empty || 'No saved queries yet.'}</div>;
  const row = (q) => (
    <li key={q.id} className="sq-row">
      <button type="button" className="sq-open" onClick={() => onOpen(q)} title={q.description || `Open ${q.name}`}>
        <span className={`sq-kind k-${q.kind}`}>{q.kind === 'sql' ? 'SQL' : 'MDB'}</span>
        <span className="sq-name">{q.name}</span>
        {q.mine
          ? (q.sharedWith?.length ? <span className="sq-badge" title={q.sharedWith.includes('*') ? 'Shared with everyone' : `Shared with ${q.sharedWith.join(', ')}`}>{q.sharedWith.includes('*') ? '🌐' : `👥${q.sharedWith.length}`}</span> : null)
          : <span className="sq-badge from" title={`Shared by ${q.owner}`}>by {q.owner}</span>}
      </button>
      {q.mine && onEdit && <button type="button" className="sq-act" title="Rename, describe or share" aria-label={`Change ${q.name}`} onClick={() => onEdit(q)}>✎</button>}
      {q.mine && onDelete && <button type="button" className="sq-act danger" title="Delete" aria-label={`Delete ${q.name}`} onClick={() => onDelete(q)}>🗑</button>}
    </li>
  );
  return (
    <div className="sq-list">
      {mine.length > 0 && <><p className="sq-group">Mine <em>{mine.length}</em></p><ul>{mine.map(row)}</ul></>}
      {shared.length > 0 && <><p className="sq-group">Shared with me <em>{shared.length}</em></p><ul>{shared.map(row)}</ul></>}
    </div>
  );
}

/**
 * "Save" in a Query tab: the first time asks for a name and who may use it; after that saves the changes into the
 * same saved query (when it is mine). `saved` = the saved query this tab came from; onSaved(savedQuery).
 */
export function SaveQueryButton({ query, saved, onSaved, notify, disabled }) {
  const [asking, setAsking] = useState(null);     // 'new' | 'edit'
  const [busy, setBusy] = useState(false);
  const quick = async () => {
    const value = query();
    if (!saved?.mine) { setAsking('new'); return; }
    setBusy(true);
    try {
      const answer = await api.updateQuery(saved.id, { ...value, name: saved.name, description: saved.description, sharedWith: saved.sharedWith });
      changed(); onSaved(answer.query); notify?.(`Saved “${saved.name}”`);
    } catch (failure) { notify?.(failure.message); } finally { setBusy(false); }
  };
  return (
    <>
      <span className="sq-savebtn">
        <button type="button" className="btn sm" disabled={disabled || busy} onClick={quick}
          title={saved?.mine ? `Save the changes into “${saved.name}”` : saved ? `“${saved.name}” is shared by ${saved.owner}: save your own copy` : 'Keep this query in My queries'}>
          💾 {busy ? 'Saving…' : saved?.mine ? 'Save' : 'Save query'}
        </button>
        {saved && <button type="button" className="btn sm" disabled={disabled} onClick={() => setAsking(saved.mine ? 'edit' : 'new')} title={saved.mine ? 'Rename, describe, share, or save as new' : 'Save as my own query'}>⋯</button>}
      </span>
      {asking && <SaveQueryDialog query={query()} existing={asking === 'edit' ? saved : null} title={asking === 'new' && saved && !saved.mine ? `Save a copy of “${saved.name}”` : undefined}
        onClose={() => setAsking(null)} onSaved={(item) => { setAsking(null); onSaved(item); notify?.(`Saved “${item.name}” in My queries`); }} />}
    </>
  );
}

/**
 * The Designer's Query step: use one of My queries (or one shared with me), keep the query that is typed here in My
 * queries, or build one in Query (step by step) and come back. kind = 'mongo' | 'sql'; onUse(savedQuery).
 */
export function SavedQueryBar({ kind, current, onUse }) {
  const saved = useSavedQueries();
  const me = useMe();
  // the Query page only for those with the Query right (as the nav bar); hidden until we know
  const mayQuery = Boolean(me?.rights?.includes('query'));
  const [open, setOpen] = useState(false);
  const [find, setFind] = useState('');
  const [saving, setSaving] = useState(false);
  const box = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const outside = (event) => { if (!box.current?.contains(event.target)) setOpen(false); };
    const escape = (event) => { if (event.key === 'Escape') setOpen(false); };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, [open]);
  const count = [...saved.mine, ...saved.shared].filter((q) => q.kind === kind).length;
  const other = [...saved.mine, ...saved.shared].length - count;
  return (
    <div className="sq-bar" ref={box}>
      <button type="button" className={`sq-bar-btn main ${open ? 'on' : ''}`} aria-expanded={open} onClick={() => setOpen(!open)}>
        <span aria-hidden="true">★</span> Use a saved query <em>{saved.loading ? '…' : count}</em> <i aria-hidden="true">▾</i>
      </button>
      <button type="button" className="sq-bar-btn" disabled={!current.text.trim()} onClick={() => setSaving(true)} title="Keep this query in My queries (private, or shared with people you choose)">
        <span aria-hidden="true">💾</span> Save to My queries
      </button>
      {mayQuery && (
        <a className="sq-bar-btn" href="/query#mine" target="_blank" rel="noopener" title="Build a query step by step in Query, save it there, then pick it here">
          <span aria-hidden="true">✨</span> Create in Query ↗
        </a>
      )}
      {open && (
        <div className="sq-pop px-in">
          <input autoFocus value={find} onChange={(event) => setFind(event.target.value)} placeholder="Search my queries…" aria-label="Search my queries" />
          <div className="sq-pop-list">
            <SavedQueryList saved={saved} filter={find} kinds={[kind]} onOpen={(item) => { onUse(item); setOpen(false); setFind(''); }}
              empty={<>No {kind === 'sql' ? 'SQL' : 'MongoDB'} query saved yet{other ? ` (${other} of the other kind)` : ''}. Write one here and press <b>Save to My queries</b>{mayQuery ? <>, or <a href="/query#mine" target="_blank" rel="noopener">create one in Query ↗</a></> : ''}.</>} />
          </div>
          <button type="button" className="sq-pop-reload" onClick={saved.reload}>↻ Refresh the list</button>
        </div>
      )}
      {saving && <SaveQueryDialog query={{ kind, ...current }} existing={null} onClose={() => setSaving(false)} onSaved={() => setSaving(false)} />}
    </div>
  );
}
