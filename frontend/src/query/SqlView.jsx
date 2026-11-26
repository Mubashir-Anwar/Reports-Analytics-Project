// A SQL editor tab (DBeaver style): the query with line numbers, Run (Ctrl+Enter runs the selection, or everything),
// values for $P{…} parameters, and the rows below. One read-only statement; the server rolls it back anyway.
import { useEffect, useRef, useState } from 'react';
import { api } from '../api.js';
import CodeArea from '../CodeArea.jsx';
import Grid from './Grid.jsx';
import UseQuery from './UseQuery.jsx';
import { SaveQueryButton } from '../shared/SavedQueries.jsx';
import QueryHelper from './QueryHelper.jsx';
import { HelperButton } from './MongoView.jsx';
import { buildSql, emptyHelper, fieldTypes, newFilter, quoteName } from './queryHelper.js';
import { DIALECT_TIPS, snippets } from './snippets.js';

const PARAM = /\$[PX]!?\{(?:IN|NOTIN)?\s*,?[^,}]*,?\s*([A-Za-z_]\w*)\}/g;

/** The first table of the schema and a text, number and date column of it: what the examples are written on. */
function examplePick(schema, dialect) {
  const tables = (schema?.schemas || []).flatMap((s) => s.tables.map((t) => ({ ...t, schemaName: s.name })));
  const t = tables.find((x) => x.kind === 'table' && x.columns.length > 1) || tables[0];
  if (!t) return {};
  const of = (re) => t.columns.find((c) => re.test(c.type || ''))?.name;
  const q = (name) => (name ? quoteName(name, dialect) : undefined);
  const schemaPart = t.schemaName && !['public', 'dbo', 'main'].includes(t.schemaName) ? `${quoteName(t.schemaName, dialect)}.` : '';
  return {
    t: `${schemaPart}${quoteName(t.name, dialect)}`,
    c: q(of(/char|text|string|keyword|varchar/i) || t.columns[0]?.name),
    n: q(of(/int|num|dec|float|double|real|money|long/i)),
    d: q(of(/date|time/i)),
  };
}

export default function SqlView({ tab, onChange, notify, schema, dialect }) {
  const area = useRef(null);
  const [examples, setExamples] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const sql = tab.sql ?? '';
  const names = [...new Set([...sql.matchAll(PARAM)].map((m) => m[1]))];
  const values = tab.params || {};

  const helper = tab.helper || emptyHelper();
  const helperOpen = tab.helperOpen ?? true;
  // the helper's FROM: the table this tab was opened from, else the query the rows came from (as a sub-query)
  const [types, setTypes] = useState({});
  useEffect(() => { if (result?.rows?.length) setTypes((old) => ({ ...old, ...fieldTypes(result.rows.map((row) => Object.fromEntries(result.columns.map((c, i) => [c, row[i]])))) })); }, [result]);
  const helperSql = buildSql(helper, { source: tab.source, base: tab.helperBase ?? sql, dialect: tab.dialect, types });
  const addFilter = (what) => onChange({ helperOpen: true, helper: { ...helper, filters: [...helper.filters, newFilter(what)] } });
  const apply = () => {
    onChange({ sql: helperSql, helperBase: tab.source ? undefined : (tab.helperBase ?? sql) });
    run(helperSql);
  };

  const run = async (text) => {
    const el = area.current;
    const picked = typeof text === 'string' ? text : el && el.selectionEnd > el.selectionStart ? el.value.slice(el.selectionStart, el.selectionEnd) : sql;
    if (!picked.trim()) return;
    setBusy(true); setError('');
    try {
      const params = Object.fromEntries(names.filter((n) => values[n] !== undefined && values[n] !== '').map((n) => [n, values[n]]));
      setResult(await api.sqlRun({ connectionId: tab.connectionId, sql: picked, parameters: params, limit: tab.limit || 200 }));
    } catch (e) { setError(e.message); setResult(null); } finally { setBusy(false); }
  };
  useEffect(() => { if (tab.autoRun) { onChange({ autoRun: false }); run(); } }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="qs">
      <div className="qs-toolbar">
        <button type="button" className="btn primary" disabled={busy || !sql.trim()} onClick={run} title="Ctrl+Enter — runs the selected text, or all of it">{busy ? 'Running…' : '▶ Run'}</button>
        <label className="qs-limit">Rows <select value={tab.limit || 200} onChange={(e) => onChange({ limit: Number(e.target.value) })}>{[50, 200, 1000, 5000].map((n) => <option key={n}>{n}</option>)}</select></label>
        <div className="qs-examples">
          <button type="button" className="btn sm" aria-expanded={examples} onClick={() => setExamples(!examples)} title="Ready SQL in this database's own words">✨ Examples ▾</button>
          {examples && (
            <ul className="qs-examples-menu" role="menu" onMouseLeave={() => setExamples(false)}>
              {snippets(dialect || tab.dialect, examplePick(schema, dialect || tab.dialect)).map((s) => (
                <li key={s.label}><button type="button" role="menuitem" onClick={() => { onChange({ sql: s.sql }); setExamples(false); }}><b>{s.label}</b><small>{s.hint}</small></button></li>
              ))}
            </ul>
          )}
        </div>
        <span className="grow" />
        {DIALECT_TIPS[dialect || tab.dialect] && <span className="qs-tip" title={DIALECT_TIPS[dialect || tab.dialect]}>{DIALECT_TIPS[dialect || tab.dialect]}</span>}
        <button type="button" className="btn sm" disabled={!sql.trim()} onClick={() => { navigator.clipboard?.writeText(sql); notify('Copied the SQL'); }}>Copy</button>
        <SaveQueryButton disabled={!sql.trim()} saved={tab.saved} notify={notify} onSaved={(item) => onChange({ saved: item, title: item.name })}
          query={() => ({ kind: 'sql', connectionId: tab.connectionId, text: sql })} />
        <UseQuery disabled={!sql.trim()} query={() => ({ connectionId: tab.connectionId, queryLanguage: 'sql', querySource: sql })} />
      </div>
      <CodeArea ref={area} language="sql" rows={9} value={sql} onChange={(e) => onChange({ sql: e.target.value })}
        placeholder={'SELECT *\nFROM orders\nWHERE branch = $P{Branch}\nORDER BY placed_on DESC'}
        onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); run(); } }} />
      {names.length > 0 && (
        <div className="qs-params">
          <span className="muted">Values</span>
          {names.map((n) => (
            <label key={n}><code>{n}</code><input value={values[n] ?? ''} placeholder="empty = NULL" onChange={(e) => onChange({ params: { ...values, [n]: e.target.value } })} /></label>
          ))}
        </div>
      )}
      {error && <div className="q-error" role="alert">{error}</div>}
      {result && <div className="qs-status">✓ {result.rows.length}{result.more ? '+' : ''} rows · {result.columns.length} columns · {result.ms} ms{result.more ? ` · stopped at ${tab.limit || 200}` : ''}</div>}
      <div className={`q-work ${helperOpen ? 'with-helper' : ''}`}>
        <Grid onFilter={addFilter} columns={result?.columns} rows={result?.rows} busy={busy} name="query" empty={result ? 'The query returned no rows' : 'Write a SELECT and press Run (Ctrl+Enter)'}
          extra={!helperOpen && <HelperButton onOpen={() => onChange({ helperOpen: true })} onDropField={addFilter} />} />
        {helperOpen && (
          <QueryHelper kind="sql" helper={helper} columns={result?.columns || []} types={types} onChange={(h) => onChange({ helper: h })} preview={helperSql}
            onApply={apply} onClear={() => onChange({ helper: emptyHelper(), ...(tab.helperBase != null ? { sql: tab.helperBase, helperBase: undefined } : {}) })}
            onClose={() => onChange({ helperOpen: false })} />
        )}
      </div>
    </div>
  );
}
