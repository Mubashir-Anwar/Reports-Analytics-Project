// /query — the Query workspace. Left: the data adapters as a tree (MongoDB: collections; SQL: schemas, tables,
// columns). Right: tabs like DBeaver / Studio 3T — a collection (Documents, Aggregate, Fields), a SQL editor, or
// the schema diagram. The tabs and what is typed in them are kept in this browser. Nothing here can change data.
import SiteFooter from '../shared/SiteFooter.jsx';
import Resizer, { useStoredWidth } from '../shared/Resizer.jsx';
import { useCallback, useEffect, useMemo, useState } from 'react';
import NavBar from '../shared/NavBar.jsx';
import { api } from '../api.js';
import MongoView from './MongoView.jsx';
import SqlView from './SqlView.jsx';
import Diagram from './Diagram.jsx';
import ContextMenu from '../ContextMenu.jsx';
import DataSourcesPanel from '../shared/DataSourcesPanel.jsx';
import { AdapterWizard } from '../shared/adapters.jsx';
import { quoteName } from './queryHelper.js';
import { SaveQueryDialog, SavedQueryList, deleteSavedQuery, useSavedQueries } from '../shared/SavedQueries.jsx';
import { newStage } from './MongoView.jsx';
import ObjectIcon, { ObjectLegend } from './ObjectIcon.jsx';
import SqlBuilder from './SqlBuilder.jsx';
import { limitRows } from './snippets.js';
import PixelField from '../shared/PixelField.jsx';

const TABS_KEY = 'query-tabs';
const SQL_PAGE = 60;            // tables shown per schema before "Show more"
const isSql = (a) => a && a.type && a.type !== 'mongodb';
const TYPE_LOOK = { mongodb: ['g', 'Mongo'], postgresql: ['b', 'Postgres'], mysql: ['a', 'MySQL'], mariadb: ['a', 'MariaDB'], sqlserver: ['v', 'SQL Server'], oracle: ['c', 'Oracle'],
  snowflake: ['b', 'Snowflake'], clickhouse: ['a', 'ClickHouse'], elasticsearch: ['g', 'Elastic'], redis: ['c', 'Redis'], sqlite: ['b', 'SQLite'], duckdb: ['a', 'DuckDB'],
  files: ['g', 'Files'], gsheets: ['g', 'Sheets'] };
let seq = 0;
const tabId = () => `t${Date.now().toString(36)}${seq++}`;

function loadTabs() {
  try {
    const saved = JSON.parse(localStorage.getItem(TABS_KEY) || 'null');
    if (saved && Array.isArray(saved.tabs)) return saved;
  } catch { /* ignore */ }
  return { tabs: [], active: null };
}

function TypeChip({ type }) {
  const [tint, label] = TYPE_LOOK[type] || ['b', type];
  return <span className={`q-chip tint-${tint}`}>{label}</span>;
}

/** One adapter in the tree, with what is inside it once opened. */
function AdapterNode({ adapter, open, onToggle, data, filter, onOpen, onMenu, onMore }) {
  const [openTables, setOpenTables] = useState({});
  const [shown, setShown] = useState({});          // schema -> how many tables are shown
  const toggleColumns = (key) => setOpenTables((o) => ({ ...o, [key]: !o[key] }));
  const sql = isSql(adapter);
  const q = filter.trim().toLowerCase();
  let body = null;
  if (open) {
    if (!data || data.loading) body = <div className="qt-note"><span className="q-spin" /> Loading…</div>;
    else if (data.error) body = <div className="qt-note err">{data.error}</div>;
    else if (!sql) {
      const items = data.collections.filter((c) => !q || c.name.toLowerCase().includes(q));
      const more = data.more ? (
        <button type="button" className="qt-more" disabled={data.loadingMore} onClick={() => onMore(adapter)}>
          {data.loadingMore ? <><span className="q-spin" /> Loading…</> : <>＋ Show {Math.min(50, data.total - data.collections.length)} more <em>{data.collections.length} of {data.total.toLocaleString()}</em></>}
        </button>
      ) : null;
      body = items.length ? [<div key="legend" className="qt-legend"><ObjectLegend /></div>, ...items.map((c) => (
        <button key={c.name} type="button" className="qt-leaf" onClick={() => onOpen({ kind: 'mongo', connectionId: adapter.id, collection: c.name })} title={`Open ${c.name} (right-click for more)`}
          onContextMenu={(e) => { e.preventDefault(); onMenu(e, { type: 'collection', adapter, collection: c }); }}>
          <span className="qt-ico"><ObjectIcon kind={c.type === 'view' ? 'mongoView' : 'collection'} /></span><span className="qt-name">{c.name}</span>
          {c.count != null && <span className="qt-count">{c.count.toLocaleString()}</span>}
        </button>
      )), more] : <div className="qt-note">No collections{q ? ` match “${filter.trim()}”` : ''}</div>;
    } else {
      body = (
        <>
          <div className="qt-actions">
            <ObjectLegend sql />
            <button type="button" className="btn sm" onClick={() => onOpen({ kind: 'builder', connectionId: adapter.id })} title="Drag tables, join them and pick the columns: the SQL is written for you">⊞ Builder</button>
            <button type="button" className="btn sm" onClick={() => onOpen({ kind: 'sql', connectionId: adapter.id })}>＋ SQL</button>
            <button type="button" className="btn sm" onClick={() => onOpen({ kind: 'diagram', connectionId: adapter.id })}>⧉ Diagram</button>
          </div>
          {data.schemas.map((s) => {
            const all = s.tables.filter((t) => !q || t.name.toLowerCase().includes(q));
            if (!all.length) return null;
            const limit = shown[s.name] || SQL_PAGE;
            const tables = all.slice(0, limit);
            return (
              <div key={s.name} className="qt-schema">
                {data.schemas.length > 1 && <div className="qt-schema-name"><ObjectIcon kind="schema" size={14} />{s.name || '(default)'}</div>}
                {tables.map((t) => {
                  const key = `${s.name}.${t.name}`;
                  return (
                    <div key={key}>
                      <div className="qt-row">
                        <button type="button" className="qt-caret" aria-label={openTables[key] ? 'Hide columns' : 'Show columns'} onClick={() => toggleColumns(key)}>{openTables[key] ? '▾' : '▸'}</button>
                        <button type="button" className="qt-leaf" title="Open the first rows (right-click for more)" onClick={() => onOpen({ kind: 'sql', connectionId: adapter.id, table: t, schemaName: s.name, dialect: adapter.type })}
                          onContextMenu={(e) => { e.preventDefault(); onMenu(e, { type: 'table', adapter, table: t, schemaName: s.name, showColumns: () => toggleColumns(key), columnsOpen: !!openTables[key] }); }}>
                          <span className="qt-ico"><ObjectIcon kind={t.kind === 'view' ? 'view' : 'table'} /></span><span className="qt-name">{t.name}</span><span className="qt-count">{t.columns.length}</span>
                        </button>
                      </div>
                      {openTables[key] && (
                        <ul className="qt-cols">
                          {t.columns.map((c) => <li key={c.name}><span className={c.pk ? 'pk' : c.ref ? 'fk' : ''}>{c.pk ? '🔑' : c.ref ? '↗' : '·'}</span>{c.name}<em>{c.type}</em></li>)}
                        </ul>
                      )}
                    </div>
                  );
                })}
                {all.length > tables.length && (
                  <button type="button" className="qt-more" onClick={() => setShown({ ...shown, [s.name]: limit + SQL_PAGE })}>
                    ＋ Show {Math.min(SQL_PAGE, all.length - tables.length)} more <em>{tables.length} of {all.length.toLocaleString()}</em>
                  </button>
                )}
              </div>
            );
          })}
        </>
      );
    }
  }
  return (
    <div className={`qt-adapter ${open ? 'open' : ''}`}>
      <button type="button" className="qt-head" aria-expanded={open} onClick={onToggle} onContextMenu={(e) => { e.preventDefault(); onMenu(e, { type: 'adapter', adapter, open }); }}>
        <span className="qt-caret-s">{open ? '▾' : '▸'}</span>
        <ObjectIcon kind="database" size={17} title={sql ? 'SQL database' : 'MongoDB database'} />
        <span className="qt-adapter-name">{adapter.name || adapter.id}</span>
        <TypeChip type={adapter.type} />
      </button>
      {open && <div className="qt-body">{body}</div>}
    </div>
  );
}

/** The part of `name` that matches the search, in bold. */
function highlight(name, filter) {
  const q = filter.trim();
  const at = name.toLowerCase().indexOf(q.toLowerCase());
  if (!q || at < 0) return name;
  return <>{name.slice(0, at)}<b>{name.slice(at, at + q.length)}</b>{name.slice(at + q.length)}</>;
}

function firstRowsSql(table, schemaName, dialect) {
  const name = `${schemaName && !['public', 'dbo', 'main'].includes(schemaName) ? `${quoteName(schemaName, dialect)}.` : ''}${quoteName(table.name, dialect)}`;
  const cols = table.columns.slice(0, 30).map((c) => quoteName(c.name, dialect)).join(', ') || '*';
  return limitRows(`SELECT ${cols}\nFROM ${name}`, 200, dialect);
}

function Home({ adapters, onOpen, onPick, onConnect }) {
  return (
    <div className="q-home">
      <section className="q-hero px-grid">
        <div className="q-hero-text">
          <p className="px-kicker"><b>03</b> — Query · read-only</p>
          <h1>Look inside your data<span className="px-caret" aria-hidden="true" /></h1>
          <p>Pick a data source below. MongoDB: browse documents or build an aggregation step by step. SQL: write a query, use the builder, or see the tables as a diagram.</p>
          <ul className="q-hero-facts">
            <li><b>{adapters.length}</b> data source{adapters.length === 1 ? '' : 's'}</li>
            <li><b>0</b> writes · nothing is changed</li>
            <li><b>Ctrl+Enter</b> runs</li>
          </ul>
        </div>
        <span className="q-hero-art" aria-hidden="true"><PixelField cell={9} gap={3} bars={8} /></span>
      </section>
      <div className="q-home-grid">
        {adapters.map((a, i) => (
          <button key={a.id} type="button" className="q-home-card" style={{ '--i': i }} onClick={() => onPick(a)}>
            <TypeChip type={a.type} />
            <b>{a.name || a.id}</b>
            <span className="muted">{a.database || a.id}</span>
            <span className="q-home-go">{isSql(a) ? 'Tables & SQL →' : 'Collections →'}</span>
          </button>
        ))}
        <button type="button" className="q-home-card add" style={{ '--i': adapters.length }} onClick={onConnect}>
          <b>＋ Connect a database</b><span className="muted">A database, a file (Excel, CSV, JSON) or a Google Sheet. Used only in Query.</span>
          <span className="q-home-go">Connect →</span>
        </button>
      </div>
      {adapters.some(isSql) && <p className="q-home-tip">Tip: <button type="button" className="linkish" onClick={() => onOpen({ kind: 'sql', connectionId: adapters.find(isSql).id })}>open a SQL editor</button> · Ctrl+Enter runs the query.</p>}
    </div>
  );
}

export default function QueryApp() {
  const [adapters, setAdapters] = useState([]);
  const [loadError, setLoadError] = useState('');
  const [open, setOpen] = useState({});
  const [data, setData] = useState({});
  const [filter, setFilter] = useState('');
  const [state, setState] = useState(loadTabs);
  const [toast, setToast] = useState('');
  const [side, setSide] = useState(true);
  const [sideWidth, setSideWidth] = useStoredWidth('q-side-width', 290, 200, 600);
  const [menu, setMenu] = useState(null);
  const [sideView, setSideView] = useState(() => (window.location.hash === '#mine' ? 'mine' : 'sources'));   // sources | mine
  const saved = useSavedQueries();
  const [editing, setEditing] = useState(null);     // a saved query being renamed / shared
  const [hintsOpen, setHintsOpen] = useState(false);
  const copy = (text, what) => { navigator.clipboard?.writeText(text); setToast(`Copied ${what}`); };

  /** Right-click on the tree: what can be done with that collection, table or data source. */
  const showMenu = (event, target) => {
    const { adapter } = target;
    let items;
    if (target.type === 'collection') {
      const base = { kind: 'mongo', connectionId: adapter.id, collection: target.collection.name };
      items = [
        { header: target.collection.name },
        { label: 'Open', icon: '▤', onClick: () => openTab({ ...base, mode: 'docs' }) },
        { label: 'Open in a new tab', icon: '⧉', onClick: () => openTab({ ...base, fresh: true }) },
        { label: 'Aggregate', icon: '⇶', onClick: () => openTab({ ...base, mode: 'aggregate' }) },
        { label: 'Fields', icon: '⌗', onClick: () => openTab({ ...base, mode: 'fields' }) },
        '-',
        { label: 'Copy name', icon: '⎘', onClick: () => copy(target.collection.name, 'the name') },
        { label: 'Copy find() code', icon: '{}', onClick: () => copy(`db.getCollection("${target.collection.name}").find({})`, 'the code') },
      ];
    } else if (target.type === 'table') {
      const base = { kind: 'sql', connectionId: adapter.id, table: target.table, schemaName: target.schemaName, dialect: adapter.type };
      const name = `${target.schemaName ? `${target.schemaName}.` : ''}${target.table.name}`;
      items = [
        { header: name },
        { label: 'Open (first rows)', icon: '▶', onClick: () => openTab(base) },
        { label: 'Open in SQL editor (don\'t run)', icon: '⌘', onClick: () => openTab({ ...base, run: false }) },
        { label: target.columnsOpen ? 'Hide columns' : 'Show columns', icon: '▥', onClick: target.showColumns },
        { label: 'Show in the diagram', icon: '⧉', onClick: () => openTab({ kind: 'diagram', connectionId: adapter.id }) },
        '-',
        { label: 'Copy name', icon: '⎘', onClick: () => copy(name, 'the name') },
        { label: 'Copy SELECT', icon: '{}', onClick: () => copy(firstRowsSql(target.table, target.schemaName, adapter.type), 'the SELECT') },
      ];
    } else {
      items = [
        { header: adapter.name || adapter.id },
        { label: target.open ? 'Collapse' : 'Expand', icon: target.open ? '▾' : '▸', onClick: () => toggle(adapter) },
        { label: 'Refresh', icon: '↻', onClick: () => { load(adapter, true); if (!open[adapter.id]) setOpen((o) => ({ ...o, [adapter.id]: true })); } },
        isSql(adapter) && '-',
        isSql(adapter) && { label: 'Query builder (drag & drop)', icon: '⊞', onClick: () => openTab({ kind: 'builder', connectionId: adapter.id }) },
        isSql(adapter) && { label: 'New SQL editor', icon: '⌘', onClick: () => openTab({ kind: 'sql', connectionId: adapter.id }) },
        isSql(adapter) && { label: 'Schema diagram', icon: '⧉', onClick: () => openTab({ kind: 'diagram', connectionId: adapter.id }) },
        '-',
        { label: 'Copy ID', icon: '⎘', onClick: () => copy(adapter.id, 'the ID') },
      ];
    }
    setMenu({ x: event.clientX, y: event.clientY, items });
  };
  const { tabs, active } = state;

  const [connecting, setConnecting] = useState(false);
  const [managing, setManaging] = useState(() => window.location.hash === '#sources');
  const loadAdapters = useCallback(() => api.connections('query').then((r) => { setAdapters(r.connections || []); setLoadError(''); }, (e) => setLoadError(e.message)), []);
  useEffect(() => { loadAdapters(); }, [loadAdapters]);
  const tell = useCallback((text) => setToast(text), []);
  useEffect(() => { try { localStorage.setItem(TABS_KEY, JSON.stringify(state)); } catch { /* ignore */ } }, [state]);
  useEffect(() => { if (!toast) return undefined; const t = setTimeout(() => setToast(''), 2200); return () => clearTimeout(t); }, [toast]);

  const load = async (adapter, force = false, search = filter.trim()) => {
    const known = data[adapter.id];
    if (known && !known.error && !force && (isSql(adapter) || (known.search || '') === search)) return known;
    setData((d) => ({ ...d, [adapter.id]: { loading: true } }));
    try {
      // MongoDB: one page of collections (the server lists names only and counts just this page)
      const value = isSql(adapter) ? await api.sqlSchema(adapter.id) : await api.mongoCollections(adapter.id, { search });
      setData((d) => ({ ...d, [adapter.id]: value }));
      return value;
    } catch (e) {
      setData((d) => ({ ...d, [adapter.id]: { error: e.message } }));
      return null;
    }
  };
  const loadMore = async (adapter) => {
    const known = data[adapter.id];
    if (!known?.more) return;
    setData((d) => ({ ...d, [adapter.id]: { ...known, loadingMore: true } }));
    try {
      const page = await api.mongoCollections(adapter.id, { search: known.search || '', offset: known.collections.length });
      setData((d) => ({ ...d, [adapter.id]: { ...page, collections: [...known.collections, ...page.collections] } }));
    } catch (e) { setData((d) => ({ ...d, [adapter.id]: { ...known, loadingMore: false } })); setToast(e.message); }
  };
  // typing in the filter asks the server for the matching MongoDB collections (a short pause first)
  useEffect(() => {
    const timer = setTimeout(() => {
      adapters.filter((a) => open[a.id] && !isSql(a) && (data[a.id]?.search || '') !== filter.trim()).forEach((a) => load(a, true));
    }, 350);
    return () => clearTimeout(timer);
  }, [filter]); // eslint-disable-line react-hooks/exhaustive-deps
  const toggle = (adapter) => {
    const next = !open[adapter.id];
    setOpen({ ...open, [adapter.id]: next });
    if (next) load(adapter);
  };
  const adapterOf = (id) => adapters.find((a) => a.id === id);

  /** Open a saved query: MongoDB in Aggregate with its steps, SQL in an editor. */
  const openSaved = (item) => {
    setManaging(false);
    const already = tabs.find((t) => t.saved?.id === item.id);
    if (already) { setState({ tabs, active: already.id }); return; }
    const tab = { id: tabId(), connectionId: item.connectionId, title: item.name, saved: item };
    if (item.kind === 'sql') Object.assign(tab, { kind: 'sql', sql: item.text, dialect: adapterOf(item.connectionId)?.type });
    else Object.assign(tab, { kind: 'mongo', collection: item.collection, mode: 'aggregate',
      stages: (item.stages || []).length ? item.stages.map((s) => ({ ...newStage(s.op, s.body), on: s.on !== false })) : undefined });
    setState({ tabs: [...tabs, tab], active: tab.id });
    if (!adapterOf(item.connectionId)) setToast(`The data source ${item.connectionId} is not in Query: ask an administrator, or connect it`);
  };
  const openTab = ({ kind, connectionId, collection, table, schemaName, dialect, mode, run = true, fresh = false }) => {
    setManaging(false);
    const adapter = adapterOf(connectionId);
    if ((kind === 'diagram' || kind === 'builder') && adapter && !data[adapter.id]) load(adapter);
    const same = !fresh && tabs.find((t) => !t.saved && t.kind === kind && t.connectionId === connectionId && (kind === 'mongo' ? t.collection === collection : kind === 'diagram'));
    if (same) { setState({ tabs: mode ? tabs.map((t) => (t.id === same.id ? { ...t, mode } : t)) : tabs, active: same.id }); return; }
    const tab = { id: tabId(), kind, connectionId };
    if (kind === 'mongo') Object.assign(tab, { collection, title: collection, mode: mode || 'docs' });
    if (kind === 'diagram') tab.title = `${adapter?.name || connectionId} diagram`;
    if (kind === 'builder') { tab.title = `Builder ${tabs.filter((t) => t.kind === 'builder').length + 1}`; tab.dialect = adapter?.type; }
    if (kind === 'sql') {
      const n = tabs.filter((t) => t.kind === 'sql').length + 1;
      tab.dialect = adapter?.type;
      Object.assign(tab, table ? { title: table.name, sql: firstRowsSql(table, schemaName, dialect || adapter?.type), autoRun: run, source: { schema: schemaName, name: table.name } } : { title: `SQL ${n}`, sql: '' });
    }
    setState({ tabs: [...tabs, tab], active: tab.id });
    if (window.matchMedia?.('(max-width: 800px)').matches) setSide(false);
  };
  const closeTab = (id) => {
    const index = tabs.findIndex((t) => t.id === id);
    const rest = tabs.filter((t) => t.id !== id);
    setState({ tabs: rest, active: active === id ? (rest[index] || rest[index - 1])?.id ?? null : active });
  };
  const patchTab = (id, patch) => setState((s) => ({ ...s, tabs: s.tabs.map((t) => (t.id === id ? { ...t, ...patch } : t)) }));
  const current = tabs.find((t) => t.id === active);
  const currentAdapter = current && adapterOf(current.connectionId);

  // a diagram tab needs the schema of its adapter
  useEffect(() => { if ((current?.kind === 'diagram' || current?.kind === 'builder') && currentAdapter && !data[currentAdapter.id]) load(currentAdapter); }, [current?.id, currentAdapter?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // the search box suggests names while typing: collections, tables and saved queries that contain the text
  const hints = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return [];
    const out = [];
    if (sideView === 'sources') {
      adapters.forEach((a) => {
        const d = data[a.id];
        if (!d || d.loading || d.error) return;
        if (!isSql(a)) d.collections.filter((c) => c.name.toLowerCase().includes(q)).forEach((c) => out.push({ key: `${a.id}/${c.name}`, icon: <ObjectIcon kind={c.type === 'view' ? 'mongoView' : 'collection'} size={14} />, name: c.name, where: a.name || a.id,
          open: () => openTab({ kind: 'mongo', connectionId: a.id, collection: c.name }) }));
        else d.schemas.forEach((s) => s.tables.filter((t) => t.name.toLowerCase().includes(q)).forEach((t) => out.push({ key: `${a.id}/${s.name}.${t.name}`, icon: <ObjectIcon kind={t.kind === 'view' ? 'view' : 'table'} size={14} />, name: t.name,
          where: `${a.name || a.id}${s.name ? ` · ${s.name}` : ''}`, open: () => openTab({ kind: 'sql', connectionId: a.id, table: t, schemaName: s.name, dialect: a.type }) })));
      });
    }
    [...saved.mine, ...saved.shared].filter((item) => item.name.toLowerCase().includes(q))
      .forEach((item) => out.push({ key: item.id, icon: <ObjectIcon kind={item.kind === 'sql' ? 'sql' : 'mongo'} size={14} />, name: item.name, where: item.mine ? 'my query' : `shared by ${item.owner}`, open: () => openSaved(item) }));
    return out.sort((a, b) => (a.name.toLowerCase().indexOf(q) - b.name.toLowerCase().indexOf(q)) || a.name.length - b.name.length).slice(0, 8);
  }, [filter, data, adapters, saved, sideView]); // eslint-disable-line react-hooks/exhaustive-deps

  const grouped = useMemo(() => [...adapters].sort((a, b) => (isSql(a) - isSql(b)) || String(a.name).localeCompare(String(b.name))), [adapters]);

  return (
    <div className="q-page">
      <NavBar current="/query" />
      <div className={`q-shell ${side ? '' : 'no-side'}`} style={{ '--q-side': `${sideWidth}px` }}>
        <aside className="q-side" aria-label="Data sources">
          <Resizer width={sideWidth} onWidth={setSideWidth} normal={290} min={200} max={600} label="Data sources width" />
          <div className="q-side-head">
            <b>Data sources</b>
            <span className="grow" />
            <button type="button" className="btn sm" title="Connect a database for Query" onClick={() => setConnecting(true)}>＋ Connect</button>
            <button type="button" className="icon-btn" title="Manage data sources: edit, test, delete" onClick={() => setManaging(true)}>⚙</button>
            <button type="button" className="icon-btn" title="Hide the list" onClick={() => setSide(false)}>⟨</button>
          </div>
          <div className="q-side-seg" role="tablist" aria-label="Show">
            <button type="button" role="tab" aria-selected={sideView === 'sources'} className={sideView === 'sources' ? 'on' : ''} onClick={() => setSideView('sources')}>▤ Sources</button>
            <button type="button" role="tab" aria-selected={sideView === 'mine'} className={sideView === 'mine' ? 'on' : ''} onClick={() => setSideView('mine')}>
              ★ Saved queries <em>{saved.mine.length + saved.shared.length}</em>
            </button>
          </div>
          <div className="q-filter-wrap">
            <input className="q-filter" type="search" value={filter} aria-label="Search" autoComplete="off"
              placeholder={sideView === 'mine' ? 'Search saved queries…' : 'Search tables, views, collections…'}
              onChange={(e) => { setFilter(e.target.value); setHintsOpen(true); }} onFocus={() => setHintsOpen(true)} onBlur={() => setTimeout(() => setHintsOpen(false), 150)}
              onKeyDown={(e) => { if (e.key === 'Escape') setHintsOpen(false); if (e.key === 'Enter' && hints[0]) { e.preventDefault(); hints[0].open(); setHintsOpen(false); } }} />
            {hintsOpen && filter.trim() && hints.length > 0 && (
              <ul className="q-hints" role="listbox" aria-label="Matches">
                {hints.map((h) => (
                  <li key={h.key}><button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => { h.open(); setHintsOpen(false); }}>
                    <span className="q-hint-ico">{h.icon}</span><span className="q-hint-name">{highlight(h.name, filter)}</span><small>{h.where}</small>
                  </button></li>
                ))}
              </ul>
            )}
          </div>
          {sideView === 'mine' && (
            <div className="q-tree q-mine">
              <SavedQueryList saved={saved} filter={filter} onOpen={openSaved} onEdit={setEditing}
                onDelete={async (item) => { try { if (await deleteSavedQuery(item)) { setToast(`Deleted “${item.name}”`); setState((s) => ({ ...s, tabs: s.tabs.map((t) => (t.saved?.id === item.id ? { ...t, saved: undefined } : t)) })); } } catch (e) { setToast(e.message); } }}
                empty={(
                  <div className="sq-empty">
                    <b>No saved queries yet</b>
                    <span>Write a query, then press <kbd>💾 Save query</kbd> to keep it here.</span>
                    <ol>
                      <li><ObjectIcon kind="collection" size={14} /> MongoDB: open a collection → <b>Aggregate</b></li>
                      <li><ObjectIcon kind="table" size={14} /> SQL: open a table or <b>＋ SQL</b></li>
                    </ol>
                  </div>
                )} />
            </div>
          )}
          <div className="q-tree" hidden={sideView !== 'sources'}>
            {loadError && <div className="qt-note err">{loadError}</div>}
            {!adapters.length && !loadError && <div className="qt-note">No data source yet. <button type="button" className="linkish" onClick={() => setConnecting(true)}>Connect one</button></div>}
            {grouped.map((a) => (
              <AdapterNode key={a.id} adapter={a} open={!!open[a.id]} onToggle={() => toggle(a)} data={data[a.id]} filter={filter} onOpen={openTab} onMenu={showMenu} onMore={loadMore} />
            ))}
          </div>
        </aside>
        <main className="q-main">
          <div className="q-tabs" role="tablist">
            {!side && <button type="button" className="icon-btn q-show-side" title="Show data sources" onClick={() => setSide(true)}>☰</button>}
            <button type="button" role="tab" aria-selected={!current} className={`q-tab home ${!current ? 'on' : ''}`} onClick={() => { setManaging(false); setState({ tabs, active: null }); }} title="Start">⌂</button>
            {tabs.map((t) => (
              <div key={t.id} role="tab" aria-selected={t.id === active} className={`q-tab ${t.id === active ? 'on' : ''} k-${t.kind}`} onClick={() => { setManaging(false); setState({ tabs, active: t.id }); }} onAuxClick={(e) => { if (e.button === 1) closeTab(t.id); }}>
                <span className="q-tab-ico">{t.kind === 'mongo' ? '▤' : t.kind === 'diagram' ? '⧉' : t.kind === 'builder' ? '⊞' : '⌘'}</span>
                <span className="q-tab-name">{t.title}</span>
                <button type="button" className="q-tab-x" aria-label={`Close ${t.title}`} onClick={(e) => { e.stopPropagation(); closeTab(t.id); }}>×</button>
              </div>
            ))}
          </div>
          {managing && <div className="q-body"><DataSourcesPanel area="query" notify={tell} onChanged={loadAdapters} /></div>}
          <div className="q-body" key={current?.id || 'home'} hidden={managing}>
            {!current && <Home onConnect={() => setConnecting(true)} adapters={grouped} onOpen={openTab} onPick={(a) => { setSide(true); if (!open[a.id]) toggle(a); }} />}
            {current && (
              <div className="q-crumbs">
                {currentAdapter ? <TypeChip type={currentAdapter.type} /> : <span className="q-chip">missing</span>}
                <span>{currentAdapter?.name || current.connectionId}</span>
                {current.collection && <><span className="sep">›</span><b>{current.collection}</b></>}
                {current.kind === 'sql' && isSql(currentAdapter) === false && <span className="q-error inline">This adapter is MongoDB</span>}
              </div>
            )}
            {current?.kind === 'mongo' && <MongoView tab={current} onChange={(p) => patchTab(current.id, p)} notify={setToast} />}
            {current?.kind === 'sql' && <SqlView key={current.id} tab={current} onChange={(p) => patchTab(current.id, p)} notify={setToast} schema={data[current.connectionId]} dialect={currentAdapter?.type} />}
            {current?.kind === 'builder' && (
              <SqlBuilder key={current.id} tab={current} onChange={(p) => patchTab(current.id, p)} schema={data[current.connectionId]} dialect={currentAdapter?.type} notify={setToast}
                onOpenSql={(text) => {
                  const tab = { id: tabId(), kind: 'sql', connectionId: current.connectionId, dialect: currentAdapter?.type, title: `${current.title} SQL`, sql: text, autoRun: true };
                  setState((s) => ({ tabs: [...s.tabs, tab], active: tab.id }));
                }} />
            )}
            {current?.kind === 'diagram' && (() => {
              const schema = data[current.connectionId];
              if (!schema || schema.loading) return <div className="q-loading"><span className="q-spin" /> Reading the schema…</div>;
              if (schema.error) return <div className="q-error">{schema.error}</div>;
              return <Diagram schema={schema} onOpenTable={(t) => openTab({ kind: 'sql', connectionId: current.connectionId, table: t, schemaName: t.schema, dialect: currentAdapter?.type })} />;
            })()}
          </div>
        </main>
      </div>
      {connecting && <AdapterWizard initial={{}} area="query" existingIds={adapters.map((a) => a.id)} notify={setToast} onClose={() => setConnecting(false)}
        onSaved={(saved) => { setConnecting(false); loadAdapters().then(() => { setSide(true); setOpen((o) => ({ ...o, [saved.id]: true })); load(saved); }); }} />}
      <ContextMenu menu={menu} onClose={() => setMenu(null)} />
      {editing && <SaveQueryDialog query={{ kind: editing.kind, connectionId: editing.connectionId, collection: editing.collection, text: editing.text, stages: editing.stages }}
        existing={editing} onClose={() => setEditing(null)}
        onSaved={(item) => { setEditing(null); setToast(`Saved “${item.name}”`); setState((s) => ({ ...s, tabs: s.tabs.map((t) => (t.saved?.id === item.id ? { ...t, saved: item, title: item.name } : t)) })); }} />}
      {toast && <div className="q-toast" role="status">{toast}</div>}
      <SiteFooter slim />
    </div>
  );
}
