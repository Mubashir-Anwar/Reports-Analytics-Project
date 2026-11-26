// The SQL query builder (a tab of a SQL database in Query): drag tables onto the canvas, drag a column onto a
// column of another table to join them (foreign keys join by themselves), tick the columns to show, give them a
// total (SUM, COUNT …), filter, group, sort and limit. The SELECT is written live below (sqlBuilder.js) for this
// database's own dialect; Run shows the rows, "Open in SQL editor" continues by hand.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api.js';
import CodeArea from '../CodeArea.jsx';
import Grid from './Grid.jsx';
import ObjectIcon from './ObjectIcon.jsx';
import UseQuery from './UseQuery.jsx';
import { SaveQueryButton } from '../shared/SavedQueries.jsx';
import { AGGS, JOINS, NO_VALUE, OPS, aliasFor, buildSelect, emptyBuilder, fieldName, newId, suggestJoins } from './sqlBuilder.js';

const TABLE_MIME = 'application/x-qb-table';
const COLUMN_MIME = 'application/x-qb-column';
const CARD_W = 210;
const PARAM = /\$[PX]!?\{(?:IN|NOTIN)?\s*,?[^,}]*,?\s*([A-Za-z_]\w*)\}/g;

const readJson = (event, mime) => { try { return JSON.parse(event.dataTransfer.getData(mime) || 'null'); } catch { return null; } };
const hasType = (event, mime) => [...(event.dataTransfer?.types || [])].includes(mime);

/** One table on the canvas: move it by its title, tick columns, drag a column onto another table to join. */
function TableCard({ table, info, fields, onMove, onRemove, onToggle, onJoin, onFilter, onFront }) {
  const [over, setOver] = useState('');
  const drag = (event) => {
    if (event.button !== 0 || event.target.closest('button')) return;
    event.preventDefault();
    onFront();
    const start = { x: event.clientX, y: event.clientY, left: table.x, top: table.y };
    const move = (e) => onMove(Math.max(0, start.left + e.clientX - start.x), Math.max(0, start.top + e.clientY - start.y));
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };
  const columns = info?.columns || [];
  const chosen = new Set(fields.filter((f) => f.alias === table.alias && !f.agg).map((f) => f.column));
  return (
    <div className="qb-card" style={{ left: table.x, top: table.y, width: CARD_W, zIndex: table.z || 1 }} data-alias={table.alias}>
      <div className="qb-card-head" onPointerDown={drag} title="Drag to move">
        <ObjectIcon kind={info?.kind === 'view' ? 'view' : 'table'} size={15} />
        <b title={`${table.schema ? `${table.schema}.` : ''}${table.name}`}>{table.name}</b>
        <code title="Alias: the short name in the SQL">{table.alias}</code>
        <button type="button" className="qb-x" aria-label={`Remove ${table.name}`} title="Remove from the query" onClick={onRemove}>×</button>
      </div>
      <ul className="qb-cols" onScroll={() => window.dispatchEvent(new Event('qb-relayout'))}>
        <li className="qb-col all">
          <label><input type="checkbox" checked={columns.length > 0 && columns.every((c) => chosen.has(c.name))} onChange={(e) => onToggle(e.target.checked ? columns.map((c) => c.name) : [], true)} />
            <i>all columns</i></label>
        </li>
        {columns.map((c) => (
          <li key={c.name} className={`qb-col ${over === c.name ? 'over' : ''}`} data-col={`${table.alias}.${c.name}`} draggable
            onDragStart={(e) => { e.dataTransfer.setData(COLUMN_MIME, JSON.stringify({ alias: table.alias, column: c.name })); e.dataTransfer.setData('text/plain', `${table.alias}.${c.name}`); e.dataTransfer.effectAllowed = 'link'; }}
            onDragOver={(e) => { if (hasType(e, COLUMN_MIME)) { e.preventDefault(); e.dataTransfer.dropEffect = 'link'; setOver(c.name); } }}
            onDragLeave={() => setOver('')}
            onDrop={(e) => { const from = readJson(e, COLUMN_MIME); setOver(''); if (from && from.alias !== table.alias) { e.preventDefault(); e.stopPropagation(); onJoin(from, { alias: table.alias, column: c.name }); } }}
            title={`${c.name} · ${c.type || ''}${c.pk ? ' · primary key' : ''}${c.ref ? ` · refers to ${c.ref}` : ''} — drag onto a column of another table to join`}>
            <label>
              <input type="checkbox" checked={chosen.has(c.name)} onChange={() => onToggle([c.name])} />
              <span className={`qb-key ${c.pk ? 'pk' : c.ref ? 'fk' : ''}`} aria-hidden="true">{c.pk ? '🔑' : c.ref ? '↗' : ''}</span>
              <span className="qb-name">{c.name}</span>
            </label>
            <small>{c.type}</small>
            <button type="button" className="qb-col-filter" title={`Filter on ${c.name}`} aria-label={`Filter on ${c.name}`} onClick={() => onFilter(c.name)}>⊻</button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A rule row (filter or having): column, operator, value. */
function RuleRow({ rule, columns, onChange, onRemove, total = false }) {
  return (
    <div className="qb-rule">
      {total && (
        <select value={rule.agg || 'count'} onChange={(e) => onChange({ agg: e.target.value })} aria-label="Total">
          {AGGS.filter(([id]) => id).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
        </select>
      )}
      <select value={rule.alias && rule.column ? `${rule.alias}.${rule.column}` : ''} aria-label="Column"
        onChange={(e) => { const [alias, ...rest] = e.target.value.split('.'); onChange({ alias, column: rest.join('.') }); }}>
        {total && <option value="">* (rows)</option>}
        {!total && <option value="" disabled>Column…</option>}
        {columns.map((c) => <option key={c.key} value={c.key}>{c.key}</option>)}
      </select>
      <select value={rule.op} onChange={(e) => onChange({ op: e.target.value })} aria-label="Condition">
        {OPS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
      </select>
      {!NO_VALUE.has(rule.op) && (
        <input value={rule.value ?? ''} onChange={(e) => onChange({ value: e.target.value })} aria-label="Value"
          placeholder={rule.op === 'in' ? 'a, b, c' : rule.op === 'between' ? 'from, to' : 'value or $P{Name}'} />
      )}
      <button type="button" className="qb-x" aria-label="Remove" onClick={onRemove}>×</button>
    </div>
  );
}

export default function SqlBuilder({ tab, onChange, schema, dialect, notify, onOpenSql }) {
  const state = useMemo(() => tab.builder || emptyBuilder(), [tab.builder]);
  const set = (patch) => onChange({ builder: { ...state, ...patch } });
  const canvas = useRef(null);
  const [find, setFind] = useState('');
  const [panel, setPanel] = useState('fields');
  const [lines, setLines] = useState([]);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [params, setParams] = useState({});

  const all = useMemo(() => (schema?.schemas || []).flatMap((s) => s.tables.map((t) => ({ ...t, schema: s.name }))), [schema]);
  const infoOf = (t) => all.find((x) => x.name === t.name && (x.schema || '') === (t.schema || ''));
  const columnType = (t, column) => infoOf(t || {})?.columns.find((c) => c.name === column)?.type || '';
  const columnList = state.tables.flatMap((t) => (infoOf(t)?.columns || []).map((c) => ({ key: `${t.alias}.${c.name}`, alias: t.alias, column: c.name })));
  const { sql, problems } = buildSelect(state, { dialect, columnType });
  const names = [...new Set([...sql.matchAll(PARAM)].map((m) => m[1]))];

  // ---- tables
  const addTable = (t, x, y) => {
    const info = infoOf(t) || t;
    const alias = aliasFor(info.name, state.tables);
    const across = Math.max(1, Math.floor(((canvas.current?.clientWidth || 700) - 16) / (CARD_W + 40)));
    const n = state.tables.length;
    const place = x === undefined ? { x: 16 + (n % across) * (CARD_W + 40), y: 16 + Math.floor(n / across) * 270 } : { x, y };
    const entry = { alias, schema: info.schema || '', name: info.name, ...place, z: state.tables.length + 1 };
    const joins = suggestJoins({ ...entry, columns: info.columns }, state.tables.map((o) => ({ ...o, columns: infoOf(o)?.columns || [] })), schema?.foreignKeys || []);
    set({ tables: [...state.tables, entry], joins: [...state.joins, ...joins] });
    if (joins.length) notify?.(`Joined ${info.name} on ${joins[0].left.alias}.${joins[0].left.column} = ${joins[0].right.alias}.${joins[0].right.column} (from the keys)`);
  };
  const removeTable = (alias) => set({
    tables: state.tables.filter((t) => t.alias !== alias),
    joins: state.joins.filter((j) => j.left.alias !== alias && j.right.alias !== alias),
    fields: state.fields.filter((f) => f.alias !== alias),
    filters: state.filters.filter((f) => f.alias !== alias),
    having: state.having.filter((f) => f.alias !== alias),
    sort: state.sort.filter((s) => state.fields.some((f) => f.id === s.field && f.alias !== alias)),
  });
  const moveTable = (alias, x, y) => onChange({ builder: { ...state, tables: state.tables.map((t) => (t.alias === alias ? { ...t, x, y } : t)) } });
  const front = (alias) => {
    const top = Math.max(0, ...state.tables.map((t) => t.z || 1));
    set({ tables: state.tables.map((t) => (t.alias === alias ? { ...t, z: top + 1 } : t)) });
  };
  const toggleColumns = (alias, columns, replace = false) => {
    const plain = state.fields.filter((f) => f.alias === alias && !f.agg);
    let fields;
    if (replace) {
      fields = [...state.fields.filter((f) => f.alias !== alias || f.agg), ...columns.map((column) => plain.find((f) => f.column === column) || { id: newId('f'), alias, column })];
    } else {
      const [column] = columns;
      fields = plain.some((f) => f.column === column)
        ? state.fields.filter((f) => !(f.alias === alias && f.column === column && !f.agg))
        : [...state.fields, { id: newId('f'), alias, column }];
    }
    set({ fields, sort: state.sort.filter((s) => fields.some((f) => f.id === s.field)) });
  };
  const join = (a, b) => {
    if (state.joins.some((j) => (j.left.alias === a.alias && j.left.column === a.column && j.right.alias === b.alias && j.right.column === b.column)
      || (j.left.alias === b.alias && j.left.column === b.column && j.right.alias === a.alias && j.right.column === a.column))) return;
    set({ joins: [...state.joins, { id: newId('j'), how: 'inner', left: a, right: b }] });
    setPanel('joins');
  };

  // ---- the join lines, measured from the column rows on the canvas
  useLayoutEffect(() => {
    const measure = () => {
      const box = canvas.current;
      if (!box) return;
      const origin = box.getBoundingClientRect();
      const point = (end) => {
        const card = box.querySelector(`.qb-card[data-alias="${CSS.escape(end.alias)}"]`);
        const row = box.querySelector(`[data-col="${CSS.escape(`${end.alias}.${end.column}`)}"]`);
        if (!card) return null;
        const c = card.getBoundingClientRect();
        const r = row ? row.getBoundingClientRect() : c;
        const list = card.querySelector('.qb-cols')?.getBoundingClientRect() || c;
        const y = Math.min(Math.max(r.top + r.height / 2, list.top + 6), list.bottom - 6);
        return { left: c.left - origin.left + box.scrollLeft, right: c.right - origin.left + box.scrollLeft, y: y - origin.top + box.scrollTop };
      };
      const next = state.joins.map((j) => {
        const a = point(j.left); const b = point(j.right);
        if (!a || !b) return null;
        const leftToRight = a.right <= b.left || (a.left < b.left && a.right > b.left ? false : a.left < b.left);
        const x1 = leftToRight ? a.right : a.left; const x2 = leftToRight ? b.left : b.right;
        const bend = Math.max(30, Math.abs(x2 - x1) / 2) * (leftToRight ? 1 : -1);
        return { id: j.id, how: j.how, d: `M${x1} ${a.y} C${x1 + bend} ${a.y} ${x2 - bend} ${b.y} ${x2} ${b.y}`, mx: (x1 + x2) / 2, my: (a.y + b.y) / 2 };
      }).filter(Boolean);
      setLines((old) => (JSON.stringify(old) === JSON.stringify(next) ? old : next));
    };
    measure();
    window.addEventListener('qb-relayout', measure);
    window.addEventListener('resize', measure);
    return () => { window.removeEventListener('qb-relayout', measure); window.removeEventListener('resize', measure); };
  }, [state.tables, state.joins, schema]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- run
  const run = async () => {
    if (!sql) return;
    setBusy(true); setError('');
    try {
      const values = Object.fromEntries(names.filter((n) => params[n] !== undefined && params[n] !== '').map((n) => [n, params[n]]));
      setResult(await api.sqlRun({ connectionId: tab.connectionId, sql, parameters: values, limit: Math.max(1, Number(state.limit) || 200) }));
    } catch (e) { setError(e.message); setResult(null); } finally { setBusy(false); }
  };
  useEffect(() => {
    const key = (e) => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && canvas.current?.isConnected) { e.preventDefault(); run(); } };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  });

  const shown = all.filter((t) => !find.trim() || t.name.toLowerCase().includes(find.trim().toLowerCase())).slice(0, 300);
  const fieldsOf = state.fields;
  const counts = { fields: fieldsOf.length, joins: state.joins.length, filters: state.filters.length, totals: state.having.length + fieldsOf.filter((f) => f.agg).length, sort: state.sort.length };
  const panelTabs = [['fields', 'Fields'], ['joins', 'Joins'], ['filters', 'Filter (WHERE)'], ['totals', 'Group & totals'], ['sort', 'Sort & limit']];
  const patchField = (id, patch) => set({ fields: fieldsOf.map((f) => (f.id === id ? { ...f, ...patch } : f)) });
  const patchList = (key, id, patch) => set({ [key]: state[key].map((r) => (r.id === id ? { ...r, ...patch } : r)) });
  const moveField = (id, step) => {
    const index = fieldsOf.findIndex((f) => f.id === id);
    const next = [...fieldsOf];
    const [item] = next.splice(index, 1);
    next.splice(Math.min(next.length, Math.max(0, index + step)), 0, item);
    set({ fields: next });
  };

  if (!schema || schema.loading) return <div className="q-loading"><span className="q-spin" /> Reading the tables…</div>;
  if (schema.error) return <div className="q-error">{schema.error}</div>;

  return (
    <div className="qb">
      <div className="qs-toolbar">
        <button type="button" className="btn primary" disabled={busy || !sql} onClick={run} title="Ctrl+Enter">{busy ? 'Running…' : '▶ Run'}</button>
        <button type="button" className="btn sm" disabled={!sql} onClick={() => onOpenSql(sql)} title="Open this SELECT in a SQL editor tab and change it by hand">⌘ Open in SQL editor</button>
        <button type="button" className="btn sm" disabled={!sql} onClick={() => { navigator.clipboard?.writeText(sql); notify?.('Copied the SQL'); }}>Copy SQL</button>
        <span className="grow" />
        <SaveQueryButton disabled={!sql} saved={tab.saved} notify={notify} onSaved={(item) => onChange({ saved: item, title: item.name })}
          query={() => ({ kind: 'sql', connectionId: tab.connectionId, text: sql })} />
        <UseQuery disabled={!sql} query={() => ({ connectionId: tab.connectionId, queryLanguage: 'sql', querySource: sql })} />
        <button type="button" className="btn sm" disabled={!state.tables.length} onClick={() => { set(emptyBuilder()); setResult(null); }} title="Start again">Clear</button>
      </div>

      <div className="qb-top">
        <aside className="qb-palette" aria-label="Tables and views">
          <input type="search" value={find} onChange={(e) => setFind(e.target.value)} placeholder="Find a table or view…" aria-label="Find a table" />
          <ul>
            {shown.map((t) => (
              <li key={`${t.schema}.${t.name}`} draggable onDragStart={(e) => { e.dataTransfer.setData(TABLE_MIME, JSON.stringify({ schema: t.schema, name: t.name })); e.dataTransfer.effectAllowed = 'copy'; }}>
                <button type="button" onClick={() => addTable(t)} title={`Add ${t.name} (or drag it onto the canvas)`}>
                  <ObjectIcon kind={t.kind === 'view' ? 'view' : 'table'} size={14} />
                  <span>{t.name}</span>
                  {(schema.schemas || []).length > 1 && <small>{t.schema}</small>}
                </button>
              </li>
            ))}
            {!shown.length && <li className="qb-none">No table matches.</li>}
          </ul>
        </aside>
        <div className={`qb-canvas ${state.tables.length ? '' : 'empty'}`} ref={canvas}
          onDragOver={(e) => { if (hasType(e, TABLE_MIME)) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; } }}
          onDrop={(e) => {
            const t = readJson(e, TABLE_MIME);
            if (!t) return;
            e.preventDefault();
            const box = canvas.current.getBoundingClientRect();
            addTable(t, Math.max(0, e.clientX - box.left + canvas.current.scrollLeft - 40), Math.max(0, e.clientY - box.top + canvas.current.scrollTop - 14));
          }}>
          {!state.tables.length && (
            <div className="qb-hint">
              <b>Drag a table here</b>
              <span>…or click it on the left. Add a second table and drag a column onto a column of the other one to join them; foreign keys join by themselves.</span>
            </div>
          )}
          <svg className="qb-lines" aria-hidden="true">
            {lines.map((l) => (
              <g key={l.id} className={`qb-line ${l.how}`}>
                <path d={l.d} />
                <circle cx={l.mx} cy={l.my} r="9" />
                <text x={l.mx} y={l.my + 3.5} textAnchor="middle">{l.how === 'inner' ? '⋈' : l.how === 'left' ? '⟕' : l.how === 'right' ? '⟖' : '⟗'}</text>
              </g>
            ))}
          </svg>
          {state.tables.map((t) => (
            <TableCard key={t.alias} table={t} info={infoOf(t)} fields={fieldsOf} onMove={(x, y) => moveTable(t.alias, x, y)} onRemove={() => removeTable(t.alias)}
              onFront={() => front(t.alias)} onToggle={(columns, replace) => toggleColumns(t.alias, columns, replace)} onJoin={join}
              onFilter={(column) => { set({ filters: [...state.filters, { id: newId('w'), alias: t.alias, column, op: 'eq', value: '' }] }); setPanel('filters'); }} />
          ))}
        </div>
      </div>

      <div className="qb-panel">
        <div className="qb-tabs" role="tablist">
          {panelTabs.map(([id, label]) => (
            <button key={id} type="button" role="tab" aria-selected={panel === id} className={panel === id ? 'on' : ''} onClick={() => setPanel(id)}>
              {label}{counts[id] ? <em>{counts[id]}</em> : null}
            </button>
          ))}
        </div>
        <div className="qb-panel-body" role="tabpanel">
          {panel === 'fields' && (fieldsOf.length ? (
            <table className="qb-grid">
              <thead><tr><th>Column</th><th>Total</th><th>Name in the result</th><th /></tr></thead>
              <tbody>
                {fieldsOf.map((f, i) => (
                  <tr key={f.id}>
                    <td><code>{f.column === '*' ? '* (rows)' : `${f.alias}.${f.column}`}</code></td>
                    <td><select value={f.agg || ''} onChange={(e) => patchField(f.id, { agg: e.target.value })} aria-label="Total">{AGGS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></td>
                    <td><input value={f.as || ''} placeholder={fieldName({ ...f, as: '' })} onChange={(e) => patchField(f.id, { as: e.target.value.replace(/[^\w ]/g, '') })} aria-label="Name in the result" /></td>
                    <td className="qb-row-tools">
                      <button type="button" disabled={i === 0} onClick={() => moveField(f.id, -1)} aria-label="Up">↑</button>
                      <button type="button" disabled={i === fieldsOf.length - 1} onClick={() => moveField(f.id, 1)} aria-label="Down">↓</button>
                      <button type="button" className="qb-x" onClick={() => set({ fields: fieldsOf.filter((x) => x.id !== f.id), sort: state.sort.filter((s) => s.field !== f.id) })} aria-label="Remove">×</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <p className="muted">Tick columns in the tables above (none ticked = every column, <code>SELECT *</code>).</p>)}

          {panel === 'joins' && (state.joins.length ? state.joins.map((j) => (
            <div key={j.id} className="qb-rule">
              <code>{j.left.alias}.{j.left.column}</code>
              <select value={j.how} onChange={(e) => patchList('joins', j.id, { how: e.target.value })} aria-label="Join type">
                {JOINS.map(([id, label, help]) => <option key={id} value={id} title={help}>{label}</option>)}
              </select>
              <code>{j.right.alias}.{j.right.column}</code>
              <small className="muted">{JOINS.find(([id]) => id === j.how)?.[2]}</small>
              <button type="button" className="qb-x" onClick={() => set({ joins: state.joins.filter((x) => x.id !== j.id) })} aria-label="Remove the join">×</button>
            </div>
          )) : <p className="muted">Drag a column of one table onto a column of another table to join them on it.</p>)}

          {panel === 'filters' && (
            <>
              {state.filters.length > 1 && (
                <div className="qb-match">Rows that match
                  <select value={state.match} onChange={(e) => set({ match: e.target.value })}><option value="and">all of these</option><option value="or">any of these</option></select></div>
              )}
              {state.filters.map((r) => <RuleRow key={r.id} rule={r} columns={columnList} onChange={(p) => patchList('filters', r.id, p)} onRemove={() => set({ filters: state.filters.filter((x) => x.id !== r.id) })} />)}
              <button type="button" className="btn sm" disabled={!columnList.length} onClick={() => set({ filters: [...state.filters, { id: newId('w'), alias: columnList[0]?.alias, column: columnList[0]?.column, op: 'eq', value: '' }] })}>＋ Condition</button>
            </>
          )}

          {panel === 'totals' && (
            <>
              <p className="muted">Give a field a total in <b>Fields</b> (SUM, COUNT …): the other fields become the groups (<code>GROUP BY</code>). Conditions on totals (<code>HAVING</code>):</p>
              <button type="button" className="btn sm" disabled={!state.tables.length} onClick={() => set({ fields: [...fieldsOf, { id: newId('f'), alias: state.tables[0].alias, column: '*', agg: 'count' }] })}>＋ Count rows</button>
              {state.having.map((r) => <RuleRow key={r.id} total rule={r} columns={columnList} onChange={(p) => patchList('having', r.id, p)} onRemove={() => set({ having: state.having.filter((x) => x.id !== r.id) })} />)}
              <button type="button" className="btn sm" disabled={!state.tables.length} onClick={() => set({ having: [...state.having, { id: newId('h'), agg: 'count', alias: '', column: '', op: 'gt', value: '' }] })}>＋ Condition on a total</button>
            </>
          )}

          {panel === 'sort' && (
            <>
              {state.sort.map((s) => (
                <div key={s.id} className="qb-rule">
                  <select value={s.field} onChange={(e) => patchList('sort', s.id, { field: e.target.value })} aria-label="Sort by">
                    {fieldsOf.map((f) => <option key={f.id} value={f.id}>{fieldName(f)}</option>)}
                  </select>
                  <select value={s.dir} onChange={(e) => patchList('sort', s.id, { dir: e.target.value })} aria-label="Direction"><option value="asc">A → Z, small → big</option><option value="desc">Z → A, big → small</option></select>
                  <button type="button" className="qb-x" onClick={() => set({ sort: state.sort.filter((x) => x.id !== s.id) })} aria-label="Remove">×</button>
                </div>
              ))}
              <div className="qb-rule">
                <button type="button" className="btn sm" disabled={!fieldsOf.length} title={fieldsOf.length ? '' : 'Tick a column first'} onClick={() => set({ sort: [...state.sort, { id: newId('s'), field: fieldsOf[0].id, dir: 'asc' }] })}>＋ Sort by</button>
                <label className="qb-inline">At most <input type="number" min="1" max="100000" value={state.limit || ''} onChange={(e) => set({ limit: e.target.value ? Number(e.target.value) : 0 })} /> rows</label>
                <label className="qb-inline"><input type="checkbox" checked={state.distinct} onChange={(e) => set({ distinct: e.target.checked })} /> No repeated rows (DISTINCT)</label>
              </div>
            </>
          )}
        </div>
      </div>

      <div className="qb-sql">
        <div className="qb-sql-head"><b>SQL</b><small className="muted">written for {dialect || 'SQL'} · read-only</small>
          {problems.map((p) => <span key={p} className="qb-problem">⚠ {p}</span>)}</div>
        <CodeArea language="sql" rows={Math.min(14, Math.max(5, sql.split('\n').length + 1))} value={sql || '-- drag a table onto the canvas'} readOnly onChange={() => {}} />
      </div>
      {names.length > 0 && (
        <div className="qs-params">
          <span className="muted">Values</span>
          {names.map((n) => <label key={n}><code>{n}</code><input value={params[n] ?? ''} placeholder="empty = NULL" onChange={(e) => setParams({ ...params, [n]: e.target.value })} /></label>)}
        </div>
      )}
      {error && <div className="q-error" role="alert">{error}</div>}
      {result && <div className="qs-status">✓ {result.rows.length}{result.more ? '+' : ''} rows · {result.columns.length} columns · {result.ms} ms</div>}
      {(result || busy) && <Grid columns={result?.columns} rows={result?.rows} busy={busy} name="query-builder" empty="The query returned no rows" />}
    </div>
  );
}
