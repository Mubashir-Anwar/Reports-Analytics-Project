// One icon (and colour) per kind of database object, so a table, a view, a collection and a database never look alike:
//   database  a cylinder           schema  a folder             table  a grid with a header row
//   view      an eye over a table  collection  stacked documents  mongo view  an eye over documents
//   saved query (MongoDB / SQL)    column keys: primary 🔑-like key, foreign arrow
const PATHS = {
  database: <><ellipse cx="12" cy="6" rx="7" ry="2.8" /><path d="M5 6v12c0 1.6 3.1 2.8 7 2.8s7-1.2 7-2.8V6" /><path d="M5 12c0 1.6 3.1 2.8 7 2.8s7-1.2 7-2.8" /></>,
  schema: <path d="M3.5 7.5A2 2 0 0 1 5.5 5.5H9l2 2h7.5a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" />,
  table: <><rect x="3.5" y="4.5" width="17" height="15" rx="1.5" /><path d="M3.5 9.5h17M3.5 14.5h17M9.5 9.5v10" /></>,
  view: <><rect x="3.5" y="9.5" width="17" height="10" rx="1.5" /><path d="M3.5 14.5h17" /><path d="M2.5 5.5c2.4-3 6-4 9.5-4s7.1 1 9.5 4c-2.4 3-6 4-9.5 4s-7.1-1-9.5-4z" /><circle cx="12" cy="5.5" r="1.6" /></>,
  collection: <><path d="M7 3.5h8l3 3v11a1.5 1.5 0 0 1-1.5 1.5h-9.5A1.5 1.5 0 0 1 5.5 17.5V5A1.5 1.5 0 0 1 7 3.5z" /><path d="M15 3.5v3h3" /><path d="M8.5 10.5h7M8.5 13.5h7M8.5 16.5h4" /><path d="M3 7v12.5A1.5 1.5 0 0 0 4.5 21H14" /></>,
  mongoView: <><path d="M7 9.5h8l3 3v6a1.5 1.5 0 0 1-1.5 1.5h-9.5A1.5 1.5 0 0 1 5.5 18.5V11A1.5 1.5 0 0 1 7 9.5z" /><path d="M8.5 15h7M8.5 17.5h4" /><path d="M2.5 5.5c2.4-3 6-4 9.5-4s7.1 1 9.5 4c-2.4 3-6 4-9.5 4s-7.1-1-9.5-4z" /><circle cx="12" cy="5.5" r="1.6" /></>,
  mongo: <path d="M12 2.5c3.4 4 5 7.4 5 10.4 0 3.9-2.2 6.4-5 7.6-2.8-1.2-5-3.7-5-7.6 0-3 1.6-6.4 5-10.4zM12 6v15.5" />,
  sql: <><ellipse cx="12" cy="6" rx="7" ry="2.8" /><path d="M5 6v12c0 1.6 3.1 2.8 7 2.8s7-1.2 7-2.8V6" /><path d="M9 12.5h6M9 15.5h4" /></>,
};
const NAMES = { database: 'Database', schema: 'Schema', table: 'Table', view: 'View', collection: 'Collection', mongoView: 'View (MongoDB)', mongo: 'MongoDB query', sql: 'SQL query' };

export default function ObjectIcon({ kind, size = 16, title }) {
  const name = title || NAMES[kind] || kind;
  return (
    <svg className={`ob-ico ob-${kind}`} width={size} height={size} viewBox="0 0 24 24" role="img" aria-label={name}
      fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <title>{name}</title>
      {PATHS[kind] || PATHS.table}
    </svg>
  );
}

/** What the icons mean, shown above the tree. */
export function ObjectLegend({ sql }) {
  const kinds = sql ? ['table', 'view'] : ['collection', 'mongoView'];
  return (
    <span className="ob-legend">
      {kinds.map((kind) => <span key={kind}><ObjectIcon kind={kind} size={13} />{NAMES[kind].replace(' (MongoDB)', '')}</span>)}
    </span>
  );
}
