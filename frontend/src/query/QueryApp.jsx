// /query — the Query workspace. Left: the data adapters as a tree (MongoDB: collections; SQL: schemas, tables,
// columns). Right: tabs like DBeaver / Studio 3T — a collection (Documents, Aggregate, Fields), a SQL editor, or
// the schema diagram. The tabs and what is typed in them are kept in this browser. Nothing here can change data.
import { useEffect, useMemo, useState } from 'react';
import NavBar from '../shared/NavBar.jsx';
import { api } from '../api.js';
import MongoView from './MongoView.jsx';
import SqlView from './SqlView.jsx';
import Diagram from './Diagram.jsx';
import ContextMenu from '../ContextMenu.jsx';
import { quoteName } from './queryHelper.js';

const TABS_KEY = 'query-tabs';
const isSql = (a) => a && a.type && a.type !== 'mongodb';
const TYPE_LOOK = { mongodb: ['g', 'Mongo'], postgresql: ['b', 'Postgres'], mysql: ['a', 'MySQL'], sqlserver: ['v', 'SQL Server'], oracle: ['c', 'Oracle'] };
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
function AdapterNode({ adapter, open, onToggle, data, filter, onOpen, onMenu }) {
  const [openTables, setOpenTables] = useState({});
  const toggleColumns = (key) => setOpenTables((o) => ({ ...o, [key]: !o[key] }));
  const sql = isSql(adapter);
  const q = filter.trim().toLowerCase();
  let body = null;
  if (open) {
    if (!data || data.loading) body = <div className="qt-note"><span className="q-spin" /> Loading…</div>;
    else if (data.error) body = <div className="qt-note err">{data.error}</div>;
    else if (!sql) {
      const items = data.collections.filter((c) => !q || c.name.toLowerCase().includes(q));
      body = items.length ? items.map((c) => (
        <button key={c.name} type="button" className="qt-leaf" onClick={() => onOpen({ kind: 'mongo', connectionId: adapter.id, collection: c.name })} title={`Open ${c.name} (right-click for more)`}
          onContextMenu={(e) => { e.preventDefault(); onMenu(e, { type: 'collection', adapter, collection: c }); }}>
          <span className="qt-ico">{c.type === 'view' ? '◇' : '▤'}</span><span className="qt-name">{c.name}</span>
          {c.count != null && <span className="qt-count">{c.count.toLocaleString()}</span>}
        </button>
      )) : <div className="qt-note">No collections{q ? ' match' : ''}</div>;
    } else {
      body = (
        <>
          <div className="qt-actions">
            <button type="button" className="btn sm" onClick={() => onOpen({ kind: 'sql', connectionId: adapter.id })}>＋ SQL</button>
            <button type="button" className="btn sm" onClick={() => onOpen({ kind: 'diagram', connectionId: adapter.id })}>⧉ Diagram</button>
          </div>
          {data.schemas.map((s) => {
            const tables = s.tables.filter((t) => !q || t.name.toLowerCase().includes(q));
            if (!tables.length) return null;
            return (
              <div key={s.name} className="qt-schema">
                {data.schemas.length > 1 && <div className="qt-schema-name">{s.name || '(default)'}</div>}
                {tables.map((t) => {
                  const key = `${s.name}.${t.name}`;
                  return (
                    <div key={key}>
                      <div className="qt-row">
                        <button type="button" className="qt-caret" aria-label={openTables[key] ? 'Hide columns' : 'Show columns'} onClick={() => toggleColumns(key)}>{openTables[key] ? '▾' : '▸'}</button>
                        <button type="button" className="qt-leaf" title="Open the first rows (right-click for more)" onClick={() => onOpen({ kind: 'sql', connectionId: adapter.id, table: t, schemaName: s.name, dialect: adapter.type })}
                          onContextMenu={(e) => { e.preventDefault(); onMenu(e, { type: 'table', adapter, table: t, schemaName: s.name, showColumns: () => toggleColumns(key), columnsOpen: !!openTables[key] }); }}>
                          <span className="qt-ico">{t.kind === 'view' ? '◇' : '▦'}</span><span className="qt-name">{t.name}</span><span className="qt-count">{t.columns.length}</span>
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
        <span className="qt-adapter-name">{adapter.name || adapter.id}</span>
        <TypeChip type={adapter.type} />
      </button>
      {open && <div className="qt-body">{body}</div>}
    </div>
  );
}

function firstRowsSql(table, schemaName, dialect) {
  const name = `${schemaName && !['public', 'dbo'].includes(schemaName) ? `${quoteName(schemaName, dialect)}.` : ''}${quoteName(table.name, dialect)}`;
  const cols = table.columns.slice(0, 30).map((c) => quoteName(c.name, dialect)).join(', ') || '*';
  return `SELECT ${cols}\nFROM ${name}`;
}

function Home({ adapters, onOpen, onPick }) {
  return (
    <div className="q-home">
      <div className="q-hero">
        <span className="q-hero-badge">Read-only · safe to explore</span>
        <h1>Look inside your data <span className="q-wave">✦</span></h1>
        <p>Open a collection to browse documents or build an aggregation step by step, or write SQL against a database and see its tables as a diagram.</p>
      </div>
      <div className="q-home-grid">
        {adapters.map((a, i) => (
          <button key={a.id} type="button" className="q-home-card" style={{ '--i': i }} onClick={() => onPick(a)}>
            <TypeChip type={a.type} />
            <b>{a.name || a.id}</b>
            <span className="muted">{a.database || a.id}</span>
            <span className="q-home-go">{isSql(a) ? 'Tables & SQL →' : 'Collections →'}</span>
          </button>
        ))}
        {!adapters.length && <p className="muted">No data adapters yet. Add one in the Designer (Setup → Data adapter) or in Admin → Data sources.</p>}
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
  const [menu, setMenu] = useState(null);
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
        isSql(adapter) && { label: 'New SQL editor', icon: '⌘', onClick: () => openTab({ kind: 'sql', connectionId: adapter.id }) },
        isSql(adapter) && { label: 'Schema diagram', icon: '⧉', onClick: () => openTab({ kind: 'diagram', connectionId: adapter.id }) },
        '-',
        { label: 'Copy ID', icon: '⎘', onClick: () => copy(adapter.id, 'the ID') },
      ];
    }
    setMenu({ x: event.clientX, y: event.clientY, items });
  };
  const { tabs, active } = state;

  useEffect(() => { api.connections().then((r) => setAdapters(r.connections || []), (e) => setLoadError(e.message)); }, []);
  useEffect(() => { try { localStorage.setItem(TABS_KEY, JSON.stringify(state)); } catch { /* ignore */ } }, [state]);
  useEffect(() => { if (!toast) return undefined; const t = setTimeout(() => setToast(''), 2200); return () => clearTimeout(t); }, [toast]);

  const load = async (adapter, force = false) => {
    if (data[adapter.id] && !data[adapter.id].error && !force) return data[adapter.id];
    setData((d) => ({ ...d, [adapter.id]: { loading: true } }));
    try {
      const value = isSql(adapter) ? await api.sqlSchema(adapter.id) : await api.mongoCollections(adapter.id);
      setData((d) => ({ ...d, [adapter.id]: value }));
      return value;
    } catch (e) {
      setData((d) => ({ ...d, [adapter.id]: { error: e.message } }));
      return null;
    }
  };
  const toggle = (adapter) => {
    const next = !open[adapter.id];
    setOpen({ ...open, [adapter.id]: next });
    if (next) load(adapter);
  };
  const adapterOf = (id) => adapters.find((a) => a.id === id);

  const openTab = ({ kind, connectionId, collection, table, schemaName, dialect, mode, run = true, fresh = false }) => {
    const adapter = adapterOf(connectionId);
    if (kind === 'diagram') load(adapter);
    const same = !fresh && tabs.find((t) => t.kind === kind && t.connectionId === connectionId && (kind === 'mongo' ? t.collection === collection : kind === 'diagram'));
    if (same) { setState({ tabs: mode ? tabs.map((t) => (t.id === same.id ? { ...t, mode } : t)) : tabs, active: same.id }); return; }
    const tab = { id: tabId(), kind, connectionId };
    if (kind === 'mongo') Object.assign(tab, { collection, title: collection, mode: mode || 'docs' });
    if (kind === 'diagram') tab.title = `${adapter?.name || connectionId} diagram`;
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
  useEffect(() => { if (current?.kind === 'diagram' && currentAdapter && !data[currentAdapter.id]) load(currentAdapter); }, [current?.id, currentAdapter?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const grouped = useMemo(() => [...adapters].sort((a, b) => (isSql(a) - isSql(b)) || String(a.name).localeCompare(String(b.name))), [adapters]);

  return (
    <div className="q-page">
      <NavBar current="/query" />
      <div className={`q-shell ${side ? '' : 'no-side'}`}>
        <aside className="q-side" aria-label="Data sources">
          <div className="q-side-head">
            <b>Data sources</b>
            <span className="grow" />
            <button type="button" className="icon-btn" title="Hide the list" onClick={() => setSide(false)}>⟨</button>
          </div>
          <input className="q-filter" type="search" placeholder="Filter collections, tables…" value={filter} onChange={(e) => setFilter(e.target.value)} />
          <div className="q-tree">
            {loadError && <div className="qt-note err">{loadError}</div>}
            {grouped.map((a) => (
              <AdapterNode key={a.id} adapter={a} open={!!open[a.id]} onToggle={() => toggle(a)} data={data[a.id]} filter={filter} onOpen={openTab} onMenu={showMenu} />
            ))}
          </div>
        </aside>
        <main className="q-main">
          <div className="q-tabs" role="tablist">
            {!side && <button type="button" className="icon-btn q-show-side" title="Show data sources" onClick={() => setSide(true)}>☰</button>}
            <button type="button" role="tab" aria-selected={!current} className={`q-tab home ${!current ? 'on' : ''}`} onClick={() => setState({ tabs, active: null })} title="Start">⌂</button>
            {tabs.map((t) => (
              <div key={t.id} role="tab" aria-selected={t.id === active} className={`q-tab ${t.id === active ? 'on' : ''} k-${t.kind}`} onClick={() => setState({ tabs, active: t.id })} onAuxClick={(e) => { if (e.button === 1) closeTab(t.id); }}>
                <span className="q-tab-ico">{t.kind === 'mongo' ? '▤' : t.kind === 'diagram' ? '⧉' : '⌘'}</span>
                <span className="q-tab-name">{t.title}</span>
                <button type="button" className="q-tab-x" aria-label={`Close ${t.title}`} onClick={(e) => { e.stopPropagation(); closeTab(t.id); }}>×</button>
              </div>
            ))}
          </div>
          <div className="q-body" key={current?.id || 'home'}>
            {!current && <Home adapters={grouped} onOpen={openTab} onPick={(a) => { setSide(true); if (!open[a.id]) toggle(a); }} />}
            {current && (
              <div className="q-crumbs">
                {currentAdapter ? <TypeChip type={currentAdapter.type} /> : <span className="q-chip">missing</span>}
                <span>{currentAdapter?.name || current.connectionId}</span>
                {current.collection && <><span className="sep">›</span><b>{current.collection}</b></>}
                {current.kind === 'sql' && isSql(currentAdapter) === false && <span className="q-error inline">This adapter is MongoDB</span>}
              </div>
            )}
            {current?.kind === 'mongo' && <MongoView tab={current} onChange={(p) => patchTab(current.id, p)} notify={setToast} />}
            {current?.kind === 'sql' && <SqlView key={current.id} tab={current} onChange={(p) => patchTab(current.id, p)} notify={setToast} />}
            {current?.kind === 'diagram' && (() => {
              const schema = data[current.connectionId];
              if (!schema || schema.loading) return <div className="q-loading"><span className="q-spin" /> Reading the schema…</div>;
              if (schema.error) return <div className="q-error">{schema.error}</div>;
              return <Diagram schema={schema} onOpenTable={(t) => openTab({ kind: 'sql', connectionId: current.connectionId, table: t, schemaName: t.schema, dialect: currentAdapter?.type })} />;
            })()}
          </div>
        </main>
      </div>
      <ContextMenu menu={menu} onClose={() => setMenu(null)} />
      {toast && <div className="q-toast" role="status">{toast}</div>}
    </div>
  );
}
