// The result as a tree (open what you need) or as JSON (Mongo Shell look). Every field can be dragged onto the query helper:
// its path, the data type of its value and the value itself go along; ⚲ does the same with a tap.
import { useState } from 'react';
import { cellText } from './Grid.jsx';
import { asField, startDrag, valueType } from './queryHelper.js';

const BADGE = { str: 'Text', num: 'Number', bool: 'T / F', date: 'Date', oid: 'ObjectId', arr: 'List', obj: 'Object', null: 'Empty' };
const join = (path, key) => (path ? `${path}.${key}` : key);
const inside = (value) => (valueType(value) === 'obj' ? Object.entries(value) : valueType(value) === 'arr' ? value.map((item, i) => [String(i), item]) : null);

function FilterButton({ what, onFilter }) {
  if (!onFilter) return null;
  return <button type="button" className="qt-filter" title={`Filter by ${what.field}`} aria-label={`Filter by ${what.field}`} onClick={(e) => { e.stopPropagation(); onFilter(what); }}>⚲</button>;
}

function Node({ name, path, value, depth, onFilter }) {
  const [open, setOpen] = useState(false);
  const kind = valueType(value);
  const kids = inside(value);
  const what = asField(path, value);
  return (
    <li role="treeitem" aria-expanded={kids ? open : undefined}>
      <div className={`qt-row ${kids ? 'has' : ''}`} style={{ '--d': depth }} draggable onDragStart={(e) => startDrag(e, what)}>
        <button type="button" className="qt-caret" aria-label={open ? 'Close' : 'Open'} tabIndex={kids ? 0 : -1} disabled={!kids} onClick={() => setOpen(!open)}>{kids ? (open ? '▾' : '▸') : ''}</button>
        <span className="qt-key" onClick={() => kids && setOpen(!open)}>{name}</span>
        <span className={`qt-value k-${kind}`} title={kids ? undefined : cellText(value)}>{kids ? (kind === 'arr' ? `${kids.length} item${kids.length === 1 ? '' : 's'}` : `${kids.length} field${kids.length === 1 ? '' : 's'}`) : kind === 'null' ? 'null' : kind === 'str' ? `"${value}"` : cellText(value)}</span>
        <FilterButton what={what} onFilter={onFilter} />
        <span className={`qt-type t-${kind}`}>{BADGE[kind]}</span>
      </div>
      {open && kids && <ul role="group">{kids.map(([key, inner]) => <Node key={key} name={key} path={kind === 'arr' ? path : join(path, key)} value={inner} depth={depth + 1} onFilter={onFilter} />)}</ul>}
    </li>
  );
}

function Doc({ doc, index, first, onFilter }) {
  const [open, setOpen] = useState(first);
  const keys = Object.keys(doc || {});
  const id = keys.length ? cellText(doc._id ?? doc[keys[0]]) : '';
  return (
    <li role="treeitem" aria-expanded={open}>
      <button type="button" className={`qt-doc ${open ? 'on' : ''}`} onClick={() => setOpen(!open)}><span className="qt-caret">{open ? '▾' : '▸'}</span><b>{index + 1}</b><span className="qt-id">{id}</span><small>{keys.length} fields</small></button>
      {open && <ul role="group">{Object.entries(doc || {}).map(([key, value]) => <Node key={key} name={key} path={key} value={value} depth={1} onFilter={onFilter} />)}</ul>}
    </li>
  );
}

export function TreeView({ documents, onFilter }) {
  return <ul className="qt" role="tree">{documents.map((doc, i) => <Doc key={i} doc={doc} index={i} first={i === 0} onFilter={onFilter} />)}</ul>;
}

const shell = (value) => {
  const kind = valueType(value);
  if (kind === 'oid') return `ObjectId("${value.$oid}")`;
  if (kind === 'date') return `ISODate("${typeof value.$date === 'string' ? value.$date : cellText(value)}")`;
  if (kind === 'num' && typeof value === 'object') return Object.values(value)[0];
  return JSON.stringify(value) ?? 'null';
};

function Lines({ value, path, depth, name, comma, onFilter }) {
  const kind = valueType(value);
  const kids = inside(value);
  const pad = '  '.repeat(depth);
  const what = path ? asField(path, value) : null;
  const key = name === undefined ? null : what
    ? <span className="qj-key" draggable onDragStart={(e) => startDrag(e, what)} onDoubleClick={onFilter ? () => onFilter(what) : undefined} title="Drag onto the query helper, or double-click">{JSON.stringify(name)}</span>
    : JSON.stringify(name);
  const head = <>{pad}{key}{key && ': '}</>;
  if (!kids) return <>{head}<span className={`k-${kind}`} draggable={!!what} onDragStart={what ? (e) => startDrag(e, what) : undefined}>{shell(value)}</span>{comma}{'\n'}</>;
  const [a, b] = kind === 'arr' ? ['[', ']'] : ['{', '}'];
  if (!kids.length) return <>{head}{a}{b}{comma}{'\n'}</>;
  return (
    <>{head}{a}{'\n'}{kids.map(([k, inner], i) => (
      <Lines key={k} value={inner} path={kind === 'arr' ? path : join(path, k)} name={kind === 'arr' ? undefined : k} depth={depth + 1} comma={i < kids.length - 1 ? ',' : ''} onFilter={onFilter} />
    ))}{pad}{b}{comma}{'\n'}</>
  );
}

export function JsonView({ documents, onFilter }) {
  return <div className="qg-json">{documents.map((doc, i) => <pre key={i}><span className="qg-n">{i + 1}</span><Lines value={doc} path="" depth={0} comma="" onFilter={onFilter} /></pre>)}</div>;
}
