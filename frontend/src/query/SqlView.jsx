// A SQL editor tab (DBeaver style): the query with line numbers, Run (Ctrl+Enter runs the selection, or everything),
// values for $P{…} parameters, and the rows below. One read-only statement; the server rolls it back anyway.
import { useEffect, useRef, useState } from 'react';
import { api } from '../api.js';
import CodeArea from '../CodeArea.jsx';
import Grid from './Grid.jsx';
import { sendToDesigner } from './handoff.js';
import QueryHelper from './QueryHelper.jsx';
import { HelperButton } from './MongoView.jsx';
import { buildSql, emptyHelper, newFilter } from './queryHelper.js';

const PARAM = /\$[PX]!?\{(?:IN|NOTIN)?\s*,?[^,}]*,?\s*([A-Za-z_]\w*)\}/g;

export default function SqlView({ tab, onChange, notify }) {
  const area = useRef(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const sql = tab.sql ?? '';
  const names = [...new Set([...sql.matchAll(PARAM)].map((m) => m[1]))];
  const values = tab.params || {};

  const helper = tab.helper || emptyHelper();
  const helperOpen = tab.helperOpen ?? true;
  // the helper's FROM: the table this tab was opened from, else the query the rows came from (as a sub-query)
  const helperSql = buildSql(helper, { source: tab.source, base: tab.helperBase ?? sql, dialect: tab.dialect });
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
        <span className="grow" />
        <button type="button" className="btn sm" disabled={!sql.trim()} onClick={() => { navigator.clipboard?.writeText(sql); notify('Copied the SQL'); }}>Copy</button>
        <button type="button" className="btn sm" disabled={!sql.trim()} onClick={() => sendToDesigner({ connectionId: tab.connectionId, queryLanguage: 'sql', querySource: sql })}>New report from this ↗</button>
      </div>
      <CodeArea ref={area} rows={9} value={sql} onChange={(e) => onChange({ sql: e.target.value })}
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
        <Grid columns={result?.columns} rows={result?.rows} busy={busy} name="query" empty={result ? 'The query returned no rows' : 'Write a SELECT and press Run (Ctrl+Enter)'}
          extra={!helperOpen && <HelperButton onOpen={() => onChange({ helperOpen: true })} onDropField={(field) => onChange({ helperOpen: true, helper: { ...helper, filters: [...helper.filters, newFilter(field)] } })} />} />
        {helperOpen && (
          <QueryHelper kind="sql" helper={helper} columns={result?.columns || []} onChange={(h) => onChange({ helper: h })} preview={helperSql}
            onApply={apply} onClear={() => onChange({ helper: emptyHelper(), ...(tab.helperBase != null ? { sql: tab.helperBase, helperBase: undefined } : {}) })}
            onClose={() => onChange({ helperOpen: false })} />
        )}
      </div>
    </div>
  );
}
