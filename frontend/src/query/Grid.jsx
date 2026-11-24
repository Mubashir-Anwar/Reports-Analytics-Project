// The result grid of the Query workspace: rows with numbers, a sticky header, typed cells, a JSON view for documents,
// and Export (CSV / JSON) of what is shown. Column headers can be dragged: onto another header to rearrange the
// columns, or onto the query helper to filter / show / sort by them. "Columns" hides columns or resets the order.
import { useEffect, useMemo, useRef, useState } from 'react';
import { downloadText } from '../mrd.js';
import { FIELD_MIME } from './queryHelper.js';

/** One Extended JSON value as short text: {"$oid": …} → ObjectId("…"), {"$date": …} → the date. */
export function cellText(value) {
  if (value === null || value === undefined) return '';
  if (typeof value !== 'object') return String(value);
  if (Array.isArray(value)) return `[${value.length}] ${JSON.stringify(value).slice(0, 80)}`;
  if ('$oid' in value) return `ObjectId("${value.$oid}")`;
  if ('$date' in value) return typeof value.$date === 'string' ? value.$date.replace('T', ' ').replace(/\.000Z$|Z$/, '') : JSON.stringify(value.$date);
  if ('$numberDecimal' in value) return value.$numberDecimal;
  if ('$numberLong' in value) return value.$numberLong;
  return JSON.stringify(value).slice(0, 120);
}

function cellKind(value) {
  if (value === null || value === undefined) return 'null';
  if (typeof value === 'number') return 'num';
  if (typeof value === 'boolean') return 'bool';
  if (typeof value === 'object') return '$oid' in value ? 'oid' : '$date' in value ? 'date' : Array.isArray(value) ? 'arr' : 'obj';
  return 'str';
}

/** Mongo shell look for a document (ObjectId("…"), ISODate("…")), pretty-printed. */
export function shellJson(value) {
  return JSON.stringify(value, null, 2)
    .replace(/\{\s*"\$oid":\s*"([0-9a-f]{24})"\s*\}/g, 'ObjectId("$1")')
    .replace(/\{\s*"\$date":\s*"([^"]+)"\s*\}/g, 'ISODate("$1")');
}

const csvCell = (value) => {
  const text = value !== null && typeof value === 'object' ? cellText(value) : value ?? '';
  const s = String(text);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

function ColumnsMenu({ columns, hidden, onToggle, onShowAll, onReset }) {
  const [open, setOpen] = useState(false);
  const box = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => { if (!box.current?.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);
  return (
    <div className="qg-cols" ref={box}>
      <button type="button" className={`btn sm ${hidden.size ? 'has' : ''}`} aria-expanded={open} onClick={() => setOpen(!open)}>▥ Columns{hidden.size ? ` · ${hidden.size} hidden` : ''}</button>
      {open && (
        <div className="qg-cols-menu" role="menu">
          <div className="qg-cols-actions">
            <button type="button" className="linkish" onClick={onShowAll}>Show all</button>
            <button type="button" className="linkish" onClick={onReset}>Original order</button>
          </div>
          {columns.map((c) => (
            <label key={c}><input type="checkbox" checked={!hidden.has(c)} onChange={() => onToggle(c)} /> {c}</label>
          ))}
        </div>
      )}
    </div>
  );
}

/** columns + rows (SQL) or documents (Mongo: the top-level fields become columns). */
export default function Grid({ columns, rows, documents, name = 'result', busy, empty = 'No rows', extra }) {
  const [view, setView] = useState('table');
  const [order, setOrder] = useState([]);           // the user's column order (names)
  const [hidden, setHidden] = useState(() => new Set());
  const [dropAt, setDropAt] = useState(null);       // header the dragged column will land before
  const dragging = useRef(null);

  const table = useMemo(() => {
    if (!documents) return { columns: columns || [], rows: rows || [] };
    const keys = [];
    documents.forEach((doc) => Object.keys(doc || {}).forEach((key) => { if (!keys.includes(key)) keys.push(key); }));
    return { columns: keys, rows: documents.map((doc) => keys.map((key) => doc?.[key])) };
  }, [columns, rows, documents]);

  // shown columns: the user's order first (those still in the result), then the new ones; hidden ones left out
  const arranged = useMemo(() => {
    const index = table.columns.map((c, i) => [c, i]);
    const known = order.filter((c) => table.columns.includes(c));
    const rest = table.columns.filter((c) => !known.includes(c));
    const byName = new Map(index);
    return [...known, ...rest].filter((c) => !hidden.has(c)).map((c) => ({ name: c, at: byName.get(c) }));
  }, [table.columns, order, hidden]);

  const move = (from, before) => {
    if (from === before) return;
    const all = [...order.filter((c) => table.columns.includes(c)), ...table.columns.filter((c) => !order.includes(c))].filter((c) => c !== from);
    const at = before ? all.indexOf(before) : all.length;
    all.splice(at < 0 ? all.length : at, 0, from);
    setOrder(all);
  };
  const toggle = (c) => setHidden((h) => { const n = new Set(h); if (n.has(c)) n.delete(c); else n.add(c); return n; });

  const shownRows = table.rows.map((row) => arranged.map((c) => row[c.at]));
  const exportCsv = () => downloadText([arranged.map((c) => csvCell(c.name)).join(','), ...shownRows.map((row) => row.map(csvCell).join(','))].join('\r\n'), `${name}.csv`, 'text/csv');
  const exportJson = () => downloadText(JSON.stringify(shownRows.map((row) => Object.fromEntries(arranged.map((c, i) => [c.name, row[i]]))), null, 2), `${name}.json`, 'application/json');
  const count = table.rows.length;

  return (
    <div className={`qg ${busy ? 'busy' : ''}`}>
      <div className="qg-bar">
        <span className="qg-count">{count} {count === 1 ? 'row' : 'rows'}</span>
        {documents && (
          <div className="seg-toggle" role="group" aria-label="View">
            <button type="button" className={view === 'table' ? 'on' : ''} onClick={() => setView('table')}>Table</button>
            <button type="button" className={view === 'json' ? 'on' : ''} onClick={() => setView('json')}>JSON</button>
          </div>
        )}
        <span className="grow" />
        {table.columns.length > 0 && <ColumnsMenu columns={table.columns} hidden={hidden} onToggle={toggle} onShowAll={() => setHidden(new Set())} onReset={() => setOrder([])} />}
        {extra}
        <button type="button" className="btn sm" disabled={!count} onClick={exportCsv}>⬇ CSV</button>
        <button type="button" className="btn sm" disabled={!count} onClick={exportJson}>⬇ JSON</button>
      </div>
      {!count ? <div className="qg-empty">{busy ? 'Running…' : empty}</div>
        : view === 'json' && documents ? (
          <div className="qg-json">{documents.map((doc, i) => <pre key={i}><span className="qg-n">{i + 1}</span>{shellJson(doc)}</pre>)}</div>
        ) : (
          <div className="qg-scroll">
            <table>
              <thead>
                <tr>
                  <th className="qg-n">#</th>
                  {arranged.map((c) => (
                    <th key={c.name} draggable title="Drag: onto another column to move it, or onto the query helper"
                      className={`${dropAt === c.name ? 'drop-before' : ''} ${dragging.current === c.name ? 'dragging' : ''}`}
                      onDragStart={(e) => { dragging.current = c.name; e.dataTransfer.setData(FIELD_MIME, c.name); e.dataTransfer.setData('text/plain', c.name); e.dataTransfer.effectAllowed = 'copyMove'; }}
                      onDragEnd={() => { dragging.current = null; setDropAt(null); }}
                      onDragOver={(e) => { if (dragging.current && dragging.current !== c.name) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; setDropAt(c.name); } }}
                      onDragLeave={() => setDropAt((d) => (d === c.name ? null : d))}
                      onDrop={(e) => { e.preventDefault(); if (dragging.current) move(dragging.current, c.name); dragging.current = null; setDropAt(null); }}>
                      <span className="qg-grip" aria-hidden="true">⋮⋮</span>{c.name}
                      <button type="button" className="qg-hide" aria-label={`Hide ${c.name}`} title="Hide this column" onClick={() => toggle(c.name)}>✕</button>
                    </th>
                  ))}
                  <th className="qg-end" onDragOver={(e) => { if (dragging.current) { e.preventDefault(); setDropAt('__end'); } }}
                    onDrop={(e) => { e.preventDefault(); if (dragging.current) move(dragging.current, null); dragging.current = null; setDropAt(null); }} />
                </tr>
              </thead>
              <tbody>
                {shownRows.map((row, r) => (
                  <tr key={r}>
                    <td className="qg-n">{r + 1}</td>
                    {row.map((value, i) => {
                      const kind = cellKind(value);
                      return <td key={arranged[i].name} className={`k-${kind}`} title={kind === 'obj' || kind === 'arr' ? JSON.stringify(value, null, 2).slice(0, 2000) : undefined}>{kind === 'null' ? 'null' : cellText(value)}</td>;
                    })}
                    <td className="qg-end" />
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
    </div>
  );
}
