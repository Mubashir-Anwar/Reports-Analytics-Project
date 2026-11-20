// A dashboard: tiles on a 12-column grid. View mode: slicers and clicks on a chart filter the other visuals.
// Edit mode: add visuals, drag a tile by its title to move it, drag its corner to resize, and set its fields.
// `source` says where the dashboard and its data come from (/analytics, or the Report Server's read-only viewer);
// `actions` are extra buttons for the header (Download, Back, …).
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { bi, newTileId, tileQuery, tileReady } from './api.js';
import Visual from './Visual.jsx';
import VisualEditor, { TypeIcon, VISUAL_TYPES } from './VisualEditor.jsx';

const ROW = 64;          // px per grid row
const GAP = 12;

function blankTile(type, y, dataset) {
  const size = { kpi: [3, 2], slicer: [3, 5], table: [6, 5] }[type] || [6, 5];
  return { id: newTileId(), type, title: '', x: 0, y, w: size[0], h: size[1], dataset: dataset || '', groupBy: [], values: [], filters: [],
    sort: [], limit: null, detail: false, columns: [], options: {} };
}

const sameValue = (a, b) => a === b || (a == null && b == null);

export default function Dashboard({ dashboardId, datasets = [], relationships = [], notify, onSaved, onDeleted, source = bi, actions = null }) {
  const [saved, setSaved] = useState(null);
  const [draft, setDraft] = useState(null);           // the dashboard being edited (null = view mode)
  const [selected, setSelected] = useState(null);     // tile id whose settings are open
  const [results, setResults] = useState({});
  const [picks, setPicks] = useState({});             // tileId → the value clicked in that visual
  const [slicers, setSlicers] = useState({});         // tileId → values chosen in that slicer
  const [busy, setBusy] = useState(false);
  const grid = useRef(null);
  const asked = useRef(0);
  const commands = useRef({});                         // what the Analytics ribbon can ask for (see below)
  const [pendingAdd, setPendingAdd] = useState(null);  // a visual the ribbon added before editing had started

  // The ribbon (AnalyticsRibbon.jsx) sends window events { cmd: 'edit' | 'save' | 'cancel' | 'reload' | 'add', type }.
  useEffect(() => {
    const handle = (event) => { const { cmd, type } = event.detail || {}; commands.current[cmd]?.(type); };
    window.addEventListener('bi-command', handle);
    return () => window.removeEventListener('bi-command', handle);
  }, []);
  useEffect(() => {
    window.dispatchEvent(new CustomEvent('bi-dashboard-state', { detail: { open: Boolean(saved), editing: Boolean(draft), mayChange: Boolean(saved?.mayChange) } }));
  }, [saved, draft]);
  useEffect(() => () => window.dispatchEvent(new CustomEvent('bi-dashboard-state', { detail: { open: false } })), []);
  useEffect(() => {                                   // editing has started: add the visual the ribbon asked for
    if (pendingAdd && draft) { setPendingAdd(null); commands.current.add?.(pendingAdd); }
  }, [pendingAdd, draft]);

  useEffect(() => {
    setSaved(null); setDraft(null); setSelected(null); setResults({}); setPicks({}); setSlicers({});
    source.dashboard(dashboardId).then((r) => setSaved(r.dashboard)).catch((e) => notify(e.message, 'error'));
  }, [dashboardId, notify, source]);

  const current = draft || saved;
  const tiles = useMemo(() => current?.tiles || [], [current]);

  // the dashboard's filters: saved ones, slicer choices and cross-filter clicks (each knows the tile it came from)
  const filters = useMemo(() => {
    const out = [...(current?.filters || [])];
    for (const tile of tiles) {
      const field = tile.groupBy?.[0];
      if (!field || !tile.dataset) continue;
      const base = { dataset: field.dataset || tile.dataset, column: field.column, ...(field.bucket ? { bucket: field.bucket } : {}), fromTile: tile.id };
      if (tile.type === 'slicer' && slicers[tile.id]?.length) out.push({ ...base, op: 'in', values: slicers[tile.id] });
      if (tile.type !== 'slicer' && tile.id in picks) out.push({ ...base, op: 'in', values: [picks[tile.id]] });
    }
    return out;
  }, [current, tiles, slicers, picks]);

  const load = useCallback(async () => {
    const ready = tiles.filter(tileReady);
    const ticket = ++asked.current;
    if (!ready.length) { setResults({}); return; }
    try {
      const { results: answers } = await source.queryBatch(ready.map(tileQuery), filters);
      if (ticket !== asked.current) return;          // a newer request is on its way
      setResults(Object.fromEntries(ready.map((tile, i) => [tile.id, answers[i]])));
    } catch (error) {
      if (ticket !== asked.current) return;
      setResults(Object.fromEntries(ready.map((tile) => [tile.id, { error: error.message }])));
    }
  }, [tiles, filters, source]);

  useEffect(() => {
    const timer = setTimeout(load, draft ? 350 : 0);   // while editing, wait until the changes settle
    return () => clearTimeout(timer);
  }, [load, draft]);

  if (!current) return <section className="card"><p className="muted">Loading the dashboard…</p></section>;

  const editing = Boolean(draft);
  const setTile = (tile) => setDraft((d) => ({ ...d, tiles: d.tiles.map((t) => (t.id === tile.id ? tile : t)) }));
  const bottom = tiles.reduce((max, t) => Math.max(max, t.y + t.h), 0);
  const addTile = (type) => {
    const tile = blankTile(type, bottom, tiles.find((t) => t.dataset)?.dataset || datasets.find((d) => d.columns.length)?.id);
    setDraft((d) => ({ ...d, tiles: [...d.tiles, tile] }));
    setSelected(tile.id);
  };
  const pick = (tile) => (value) => setPicks((p) => {
    const next = { ...p };
    if (tile.id in next && sameValue(next[tile.id], value)) delete next[tile.id]; else next[tile.id] = value;
    return next;
  });

  const save = async () => {
    setBusy(true);
    try {
      const { dashboard } = await bi.saveDashboard(saved.id, { name: draft.name, description: draft.description, folder: draft.folder, tiles: draft.tiles, filters: draft.filters });
      setSaved(dashboard); setDraft(null); setSelected(null);
      notify('Dashboard saved.', 'ok'); onSaved?.();
    } catch (error) { notify(error.message, 'error'); } finally { setBusy(false); }
  };
  commands.current = {
    edit: () => { if (!editing && saved.mayChange) setDraft(structuredClone(saved)); },
    save: () => { if (editing && draft.name.trim() && !busy) save(); },
    cancel: () => { if (editing) { setDraft(null); setSelected(null); } },
    reload: () => load(),
    add: (type) => {
      if (editing) addTile(type);
      else if (saved.mayChange) { setDraft(structuredClone(saved)); setPendingAdd(type); }
    },
  };
  const remove = async () => {
    if (!window.confirm(`Delete the dashboard “${saved.name}”? This cannot be undone.`)) return;
    try { await bi.deleteDashboard(saved.id); notify('Dashboard deleted.', 'ok'); onDeleted?.(); } catch (error) { notify(error.message, 'error'); }
  };

  // drag a tile (by its head) or its corner: pointer moves are turned into grid cells
  const startDrag = (tile, mode) => (event) => {
    if (!editing || event.button !== 0) return;
    if (mode === 'move' && event.target.closest('button, input, select')) return;
    event.preventDefault();
    const box = grid.current.getBoundingClientRect();
    const col = (box.width + GAP) / 12;
    const start = { x: event.clientX, y: event.clientY, tile };
    const move = (e) => {
      const dx = Math.round((e.clientX - start.x) / col);
      const dy = Math.round((e.clientY - start.y) / (ROW + GAP));
      const t = start.tile;
      if (mode === 'move') setTile({ ...t, x: Math.max(0, Math.min(12 - t.w, t.x + dx)), y: Math.max(0, t.y + dy) });
      else setTile({ ...t, w: Math.max(1, Math.min(12 - t.x, t.w + dx)), h: Math.max(1, Math.min(40, t.h + dy)) });
    };
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };
  const nudge = (tile) => (event) => {                 // keyboard: arrows move, shift+arrows resize
    if (!editing || event.target !== event.currentTarget) return;
    const step = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
    if (!step) return;
    event.preventDefault();
    if (event.shiftKey) setTile({ ...tile, w: Math.max(1, Math.min(12 - tile.x, tile.w + step[0])), h: Math.max(1, tile.h + step[1]) });
    else setTile({ ...tile, x: Math.max(0, Math.min(12 - tile.w, tile.x + step[0])), y: Math.max(0, tile.y + step[1]) });
  };

  const selectedTile = tiles.find((t) => t.id === selected);
  const activeFilters = filters.filter((f) => f.fromTile);
  const tileName = (id) => tiles.find((t) => t.id === id)?.title || 'a visual';

  return (
    <div className={`dash ${editing ? 'editing' : ''} ${selectedTile ? 'with-pane' : ''}`}>
      <div className="dash-head">
        <div className="dash-title">
          {editing ? (
            <input className="dash-name" value={draft.name} maxLength={120} aria-label="Dashboard name" onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
          ) : <h1>{saved.name}</h1>}
          {!editing && saved.description && <p className="muted">{saved.description}</p>}
          {editing && <input className="dash-desc" value={draft.description || ''} maxLength={500} placeholder="Description (optional)" aria-label="Description" onChange={(e) => setDraft({ ...draft, description: e.target.value })} />}
        </div>
        <div className="row">
          {!editing && actions}
          {!editing && <button type="button" className="btn" onClick={load} title="Ask the data again">Reload</button>}
          {!editing && saved.mayChange && <button type="button" className="btn primary" onClick={() => setDraft(structuredClone(saved))}>Edit</button>}
          {!editing && saved.mayChange && <button type="button" className="btn danger" onClick={remove}>Delete</button>}
          {editing && <button type="button" className="btn" onClick={() => { setDraft(null); setSelected(null); }} disabled={busy}>Cancel</button>}
          {editing && <button type="button" className="btn primary" onClick={save} disabled={busy || !draft.name.trim()}>{busy ? 'Saving…' : 'Save dashboard'}</button>}
        </div>
      </div>

      {editing && (
        <div className="dash-add" role="toolbar" aria-label="Add a visual">
          <span className="muted small">Add:</span>
          {VISUAL_TYPES.map(([id, name, path]) => (
            <button key={id} type="button" className="btn small" onClick={() => addTile(id)} title={`Add a ${name.toLowerCase()}`}><TypeIcon path={path} size={15} />{name}</button>
          ))}
        </div>
      )}

      {activeFilters.length > 0 && (
        <div className="dash-filters" role="status">
          <span className="muted small">Filtered by:</span>
          {activeFilters.map((f) => (
            <span key={f.fromTile} className="filter-chip">
              {f.column}{f.bucket ? ` (${f.bucket})` : ''} = {f.values.map((v) => (v == null ? '(blank)' : String(v))).join(', ')}
              <small className="muted"> from {tileName(f.fromTile)}</small>
              <button type="button" aria-label="Remove this filter" onClick={() => {
                setPicks((p) => { const n = { ...p }; delete n[f.fromTile]; return n; });
                setSlicers((s) => { const n = { ...s }; delete n[f.fromTile]; return n; });
              }}>×</button>
            </span>
          ))}
          <button type="button" className="link-btn" onClick={() => { setPicks({}); setSlicers({}); }}>Clear all</button>
        </div>
      )}

      <div className="dash-body">
        {tiles.length === 0 ? (
          <div className="empty dash-empty">
            {editing ? 'Add a visual with the buttons above.' : saved.mayChange ? 'This dashboard is empty: press Edit and add visuals.' : 'This dashboard is empty.'}
          </div>
        ) : (
          <div className="dash-grid" ref={grid} style={{ gridAutoRows: `${ROW}px`, gap: `${GAP}px` }}>
            {tiles.map((tile) => (
              <section key={tile.id} className={`tile ${selected === tile.id ? 'selected' : ''} ${tile.type}`}
                style={{ gridColumn: `${tile.x + 1} / span ${tile.w}`, gridRow: `${tile.y + 1} / span ${tile.h}`, "--rows": tile.h }}
                tabIndex={editing ? 0 : undefined} onKeyDown={nudge(tile)}
                aria-label={tile.title || VISUAL_TYPES.find(([id]) => id === tile.type)?.[1]}>
                <header className="tile-head" onPointerDown={startDrag(tile, 'move')}>
                  <h3>{tile.title || (editing ? <span className="muted">{VISUAL_TYPES.find(([id]) => id === tile.type)?.[1]} (no title)</span> : '')}</h3>
                  {editing && <button type="button" className="btn small" onClick={() => setSelected(tile.id === selected ? null : tile.id)}>{selected === tile.id ? 'Close' : 'Fields'}</button>}
                </header>
                <div className="tile-body">
                  {tileReady(tile) ? (
                    <Visual tile={tile} result={results[tile.id]} picked={tile.id in picks ? picks[tile.id] : undefined}
                      onPick={editing ? undefined : pick(tile)} slicerValues={slicers[tile.id]}
                      onSlicer={(values) => setSlicers((s) => ({ ...s, [tile.id]: values }))} />
                  ) : (
                    <div className="viz-empty">{editing ? <button type="button" className="link-btn" onClick={() => setSelected(tile.id)}>Choose the fields of this visual</button> : 'Not set up yet'}</div>
                  )}
                  {results[tile.id]?.ignoredFilters?.length > 0 && <small className="muted ignored" title="These filters use a dataset this visual is not related to">Some filters do not apply here</small>}
                </div>
                {editing && <span className="tile-resize" onPointerDown={startDrag(tile, 'resize')} aria-hidden="true" title="Drag to resize" />}
              </section>
            ))}
          </div>
        )}
        {editing && selectedTile && (
          <VisualEditor tile={selectedTile} datasets={datasets} relationships={relationships} onChange={setTile}
            onRemove={() => { setDraft((d) => ({ ...d, tiles: d.tiles.filter((t) => t.id !== selectedTile.id) })); setSelected(null); }}
            onClose={() => setSelected(null)} />
        )}
      </div>
    </div>
  );
}
