// The schema of a SQL adapter as a diagram: each table a card with its columns (key, type), lines for foreign keys.
// Tables that are linked sit next to each other (a simple layered layout); drag a card to move it.
import { useMemo, useRef, useState } from 'react';

const W = 210;
const ROW = 18;
const HEAD = 28;
const GAP_X = 70;
const GAP_Y = 36;
const MAX_TABLES = 80;

function layout(tables, links) {
  const key = (t) => `${t.schema}.${t.name}`;
  const byKey = new Map(tables.map((t) => [key(t), t]));
  const degree = new Map(tables.map((t) => [key(t), 0]));
  const near = new Map(tables.map((t) => [key(t), new Set()]));
  links.forEach((l) => {
    const a = `${l.schema}.${l.table}`; const b = `${l.refSchema}.${l.refTable}`;
    if (!byKey.has(a) || !byKey.has(b) || a === b) return;
    degree.set(a, degree.get(a) + 1); degree.set(b, degree.get(b) + 1);
    near.get(a).add(b); near.get(b).add(a);
  });
  // breadth-first from the most linked table: linked tables land in neighbouring columns
  const order = [];
  const seen = new Set();
  const starts = [...tables].sort((x, y) => degree.get(key(y)) - degree.get(key(x)));
  const level = new Map();
  starts.forEach((start) => {
    if (seen.has(key(start))) return;
    const queue = [key(start)]; seen.add(key(start)); level.set(key(start), 0);
    while (queue.length) {
      const k = queue.shift(); order.push(k);
      [...near.get(k)].forEach((n) => { if (!seen.has(n)) { seen.add(n); level.set(n, level.get(k) + 1); queue.push(n); } });
    }
  });
  const columns = Math.max(3, Math.ceil(Math.sqrt(order.length * 1.6)));
  const heights = Array(columns).fill(20);
  const pos = {};
  order.forEach((k, i) => {
    const linked = degree.get(k) > 0;
    const col = linked ? Math.min(level.get(k), columns - 1) : i % columns;
    const c = heights.indexOf(Math.min(...heights)) === col ? col : (heights[col] > Math.min(...heights) + 400 ? heights.indexOf(Math.min(...heights)) : col);
    const t = byKey.get(k);
    pos[k] = { x: 20 + c * (W + GAP_X), y: heights[c] };
    heights[c] += HEAD + t.columns.length * ROW + 8 + GAP_Y;
  });
  return pos;
}

export default function Diagram({ schema, onOpenTable }) {
  const [filter, setFilter] = useState('');
  const [schemaName, setSchemaName] = useState(schema.schemas[0]?.name ?? '');
  const [moved, setMoved] = useState({});
  const drag = useRef(null);
  const tables = useMemo(() => {
    const all = (schema.schemas.find((s) => s.name === schemaName)?.tables || []);
    const q = filter.trim().toLowerCase();
    return all.filter((t) => !q || t.name.toLowerCase().includes(q)).slice(0, MAX_TABLES);
  }, [schema, schemaName, filter]);
  const links = schema.foreignKeys || [];
  const base = useMemo(() => layout(tables, links), [tables, links]);
  const pos = (t) => moved[`${t.schema}.${t.name}`] || base[`${t.schema}.${t.name}`] || { x: 0, y: 0 };
  const byKey = new Map(tables.map((t) => [`${t.schema}.${t.name}`, t]));
  const width = Math.max(600, ...tables.map((t) => pos(t).x + W + 40));
  const height = Math.max(400, ...tables.map((t) => pos(t).y + HEAD + t.columns.length * ROW + 40));

  const lines = links.map((l, i) => {
    const a = byKey.get(`${l.schema}.${l.table}`); const b = byKey.get(`${l.refSchema}.${l.refTable}`);
    if (!a || !b) return null;
    const pa = pos(a); const pb = pos(b);
    const ya = pa.y + HEAD + Math.max(0, a.columns.findIndex((c) => c.name === l.column)) * ROW + ROW / 2;
    const yb = pb.y + HEAD + Math.max(0, b.columns.findIndex((c) => c.name === l.refColumn)) * ROW + ROW / 2;
    const right = pb.x > pa.x;
    const xa = right ? pa.x + W : pa.x; const xb = right ? pb.x : pb.x + W;
    const bend = Math.max(40, Math.abs(xb - xa) / 2) * (right ? 1 : -1);
    const same = a === b;
    const d = same ? `M${pa.x + W} ${ya} C${pa.x + W + 50} ${ya} ${pa.x + W + 50} ${yb} ${pa.x + W} ${yb}` : `M${xa} ${ya} C${xa + bend} ${ya} ${xb - bend} ${yb} ${xb} ${yb}`;
    return <path key={i} d={d} className="qd-link" markerEnd="url(#qd-arrow)"><title>{`${l.table}.${l.column} → ${l.refTable}.${l.refColumn}`}</title></path>;
  });

  const down = (event, t) => {
    const p = pos(t);
    drag.current = { key: `${t.schema}.${t.name}`, dx: event.clientX - p.x, dy: event.clientY - p.y };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };
  const moveTo = (event) => {
    if (!drag.current) return;
    const { key, dx, dy } = drag.current;
    setMoved((m) => ({ ...m, [key]: { x: Math.max(0, event.clientX - dx), y: Math.max(0, event.clientY - dy) } }));
  };

  return (
    <div className="qd">
      <div className="qd-bar">
        {schema.schemas.length > 1 && (
          <select value={schemaName} onChange={(e) => { setSchemaName(e.target.value); setMoved({}); }}>
            {schema.schemas.map((s) => <option key={s.name} value={s.name}>{s.name || '(default)'} · {s.tables.length}</option>)}
          </select>
        )}
        <input type="search" placeholder="Find a table…" value={filter} onChange={(e) => setFilter(e.target.value)} />
        <span className="muted">{tables.length} tables · {links.length} links{tables.length >= MAX_TABLES ? ` · first ${MAX_TABLES}` : ''} · drag a card to move it, double-click to query it</span>
        <span className="grow" />
        <button type="button" className="btn sm" onClick={() => setMoved({})}>Tidy up</button>
      </div>
      <div className="qd-canvas">
        <svg width={width} height={height} onPointerMove={moveTo} onPointerUp={() => { drag.current = null; }}>
          <defs><marker id="qd-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" className="qd-arrow" /></marker></defs>
          {lines}
          {tables.map((t, ti) => {
            const p = pos(t);
            const h = HEAD + t.columns.length * ROW + 8;
            return (
              <g key={`${t.schema}.${t.name}`} transform={`translate(${p.x} ${p.y})`} className={`qd-table ${t.kind}`} style={{ '--d': `${Math.min(ti, 30) * 25}ms` }}
                onPointerDown={(e) => down(e, t)} onDoubleClick={() => onOpenTable(t)}>
                <rect width={W} height={h} rx="10" className="qd-card" />
                <rect width={W} height={HEAD} rx="10" className="qd-head" />
                <rect y={HEAD - 10} width={W} height="10" className="qd-head" />
                <text x="12" y="19" className="qd-title">{t.kind === 'view' ? '◇ ' : '▦ '}{t.name.length > 24 ? `${t.name.slice(0, 23)}…` : t.name}</text>
                {t.columns.map((c, i) => (
                  <g key={c.name} transform={`translate(0 ${HEAD + 4 + i * ROW})`}>
                    <text x="12" y="13" className={`qd-col ${c.pk ? 'pk' : ''} ${c.ref ? 'fk' : ''}`}>{c.pk ? '🔑 ' : c.ref ? '↗ ' : ''}{c.name.length > 20 ? `${c.name.slice(0, 19)}…` : c.name}</text>
                    <text x={W - 10} y="13" textAnchor="end" className="qd-type">{c.type.slice(0, 12)}</text>
                  </g>
                ))}
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}
