// Analytics → Pipelines (#etl/new, #etl/<dataset>): ETL / ELT as a list of steps. Sources are datasets (already
// imported), then blocks (filter, columns, join, group, …), SQL or Python steps; the last step is the result, saved
// as a dataset that runs every step again on refresh or on its schedule. Needs the Analytics and Pipelines rights;
// Python steps need the Python scripts right too.
import { useEffect, useMemo, useState } from 'react';
import { bi, describeBytes } from './api.js';
import { CodeBox, SCHEDULES } from './PythonLab.jsx';
import AnalyticsFolder from './AnalyticsFolder.jsx';

const KIND_INFO = {
  source: { icon: '⛁', label: 'Source', help: 'A dataset (data already imported from a data adapter)', group: 'Extract' },
  filter: { icon: '⧩', label: 'Filter rows', help: 'Keep the rows that match rules', group: 'Transform' },
  select: { icon: '▤', label: 'Choose columns', help: 'Keep, rename and order columns', group: 'Transform' },
  dedupe: { icon: '⧉', label: 'Remove duplicates', help: 'Drop repeated rows', group: 'Transform' },
  fill: { icon: '▧', label: 'Fill empty', help: 'Put a value where a column is empty', group: 'Transform' },
  cast: { icon: '⇆', label: 'Change type', help: 'Number, text, date, true/false', group: 'Transform' },
  column: { icon: 'ƒ', label: 'New column', help: 'A column from a formula (SQL expression)', group: 'Transform' },
  join: { icon: '⋈', label: 'Join', help: 'Combine two steps on shared columns', group: 'Combine' },
  union: { icon: '☰', label: 'Stack', help: 'Put the rows of steps on top of each other', group: 'Combine' },
  group: { icon: 'Σ', label: 'Group & total', help: 'One row per group, with sums and counts', group: 'Combine' },
  sort: { icon: '↧', label: 'Sort', help: 'Order the rows, keep the first N', group: 'Combine' },
  sql: { icon: '⌘', label: 'SQL', help: 'Any SELECT over the earlier steps (DuckDB)', group: 'Code' },
  python: { icon: 'Py', label: 'Python', help: 'A pandas script over earlier steps', group: 'Code' },
};
const OPS = [['eq', '='], ['ne', '≠'], ['gt', '>'], ['gte', '≥'], ['lt', '<'], ['lte', '≤'], ['contains', 'contains'], ['starts', 'starts with'],
  ['ends', 'ends with'], ['in', 'is one of'], ['is_null', 'is empty'], ['not_null', 'is not empty']];
const AGG_NAMES = { sum: 'Sum', avg: 'Average', min: 'Minimum', max: 'Maximum', count: 'Count', distinct: 'Count distinct', median: 'Median', countrows: 'Count rows', first: 'First' };
const TYPE_NAMES = { number: 'Number (decimal)', integer: 'Whole number', text: 'Text', date: 'Date', datetime: 'Date and time', bool: 'True / false' };

const DEFAULTS = {
  source: { dataset: '' }, filter: { input: '', match: 'all', rules: [{ column: '', op: 'eq', value: '' }] },
  select: { input: '', columns: [] }, dedupe: { input: '', columns: [] }, fill: { input: '', fills: [{ column: '', value: '' }] },
  cast: { input: '', casts: [{ column: '', to: 'number' }] }, column: { input: '', column: 'new_column', expression: '' },
  join: { left: '', right: '', how: 'left', on: [{ left: '', right: '' }] }, union: { inputs: [] },
  group: { input: '', by: [], aggs: [{ agg: 'sum', column: '', as: '' }] }, sort: { input: '', by: [{ column: '', dir: 'desc' }], limit: null },
  sql: { sql: 'SELECT *' + String.fromCharCode(10) + 'FROM previous_step' }, python: { inputs: [], script: `result = df.copy()${String.fromCharCode(10)}` },
};
const blank = (kind, name) => ({ kind, name, note: '', ...structuredClone(DEFAULTS[kind]) });

/** A small list editor: rows of fields with + and ✕. */
function Rows({ items, onChange, make, render, add = '+ Add' }) {
  return (
    <div className="etl-rows">
      {items.map((item, i) => (
        <div key={i} className="etl-row">
          {render(item, (next) => onChange(items.map((x, j) => (j === i ? { ...x, ...next } : x))))}
          <button type="button" className="fd-x" aria-label="Remove" onClick={() => onChange(items.filter((_, j) => j !== i))}>✕</button>
        </div>
      ))}
      <button type="button" className="btn small" onClick={() => onChange([...items, make()])}>{add}</button>
    </div>
  );
}

function ColumnPick({ value, onChange, columns, placeholder = 'column', id }) {
  return (
    <>
      <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} list={id} aria-label={placeholder} />
      <datalist id={id}>{columns.map((c) => <option key={c} value={c} />)}</datalist>
    </>
  );
}

function Checks({ options, value, onChange, empty }) {
  if (!options.length) return <p className="muted small">{empty}</p>;
  return (
    <div className="etl-checks">
      {options.map((name) => (
        <label key={name} className={value.includes(name) ? 'on' : ''}>
          <input type="checkbox" checked={value.includes(name)} onChange={(e) => onChange(e.target.checked ? [...value, name] : value.filter((x) => x !== name))} />{name}
        </label>
      ))}
    </div>
  );
}

function StepForm({ step, index, steps, datasets, colsOf, onChange, mayPython, readOnly }) {
  const earlier = steps.slice(0, index).map((s) => s.name);
  const set = (patch) => onChange({ ...step, ...patch });
  const inputOf = (field = 'input') => step[field] || earlier[earlier.length - 1] || '';
  const cols = colsOf(inputOf());
  const uid = `etl-${index}`;
  const inputSelect = ({ field = 'input', label = 'Reads' } = {}) => (
    <label className="field"><span>{label}</span>
      <select value={step[field] || ''} onChange={(e) => set({ [field]: e.target.value })}>
        <option value="">{earlier.length ? `${earlier[earlier.length - 1]} (the step before)` : '—'}</option>
        {earlier.map((n) => <option key={n} value={n}>{n}</option>)}
      </select></label>
  );
  const k = step.kind;
  return (
    <fieldset className="etl-form" disabled={readOnly}>
      <div className="etl-form-head">
        <label className="field"><span>Step name (its table)</span>
          <input value={step.name} onChange={(e) => set({ name: e.target.value.replace(/[^A-Za-z0-9_]/g, '_').slice(0, 40) })} /></label>
        <label className="field grow"><span>Note</span><input value={step.note || ''} onChange={(e) => set({ note: e.target.value })} placeholder="What this step is for" maxLength={300} /></label>
      </div>
      {k === 'source' && (
        <label className="field"><span>Dataset</span>
          <select value={step.dataset} onChange={(e) => set({ dataset: e.target.value })}>
            <option value="">Choose a dataset…</option>
            {datasets.map((d) => <option key={d.id} value={d.id}>{d.name}{d.status !== 'ready' ? ` (${d.status})` : ''}</option>)}
          </select></label>
      )}
      {k === 'filter' && (<>
        <div className="row">{inputSelect()}
          <label className="field"><span>Keep rows where</span><select value={step.match} onChange={(e) => set({ match: e.target.value })}><option value="all">all rules match</option><option value="any">any rule matches</option></select></label></div>
        <Rows items={step.rules} onChange={(rules) => set({ rules })} make={() => ({ column: '', op: 'eq', value: '' })} add="+ Rule"
          render={(rule, change) => (<>
            <ColumnPick value={rule.column} onChange={(column) => change({ column })} columns={cols} id={`${uid}-c`} />
            <select value={rule.op} onChange={(e) => change({ op: e.target.value })} aria-label="Operator">{OPS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select>
            {!['is_null', 'not_null'].includes(rule.op) && (
              <input value={Array.isArray(rule.value) ? rule.value.join(', ') : rule.value ?? ''} aria-label="Value"
                placeholder={rule.op === 'in' ? 'a, b, c' : 'value'}
                onChange={(e) => change({ value: rule.op === 'in' ? e.target.value.split(',').map((v) => v.trim()).filter(Boolean) : e.target.value })} />)}
          </>)} />
      </>)}
      {k === 'select' && (<>
        {inputSelect()}
        <Rows items={step.columns} onChange={(columns) => set({ columns })} make={() => ({ name: '', as: '' })} add="+ Column"
          render={(c, change) => (<><ColumnPick value={c.name} onChange={(name) => change({ name })} columns={cols} id={`${uid}-c`} /><span className="muted">as</span>
            <input value={c.as} onChange={(e) => change({ as: e.target.value })} placeholder="(same name)" aria-label="New name" /></>)} />
        {cols.length > 0 && <button type="button" className="btn small quiet" onClick={() => set({ columns: cols.map((name) => ({ name, as: '' })) })}>Take all {cols.length} columns</button>}
      </>)}
      {k === 'dedupe' && (<>
        {inputSelect()}
        <p className="muted small">Rows count as repeated when these columns are the same (none ticked: every column).</p>
        <Checks options={cols} value={step.columns} onChange={(columns) => set({ columns })} empty="Run the steps before to see their columns." />
      </>)}
      {k === 'fill' && (<>
        {inputSelect()}
        <Rows items={step.fills} onChange={(fills) => set({ fills })} make={() => ({ column: '', value: '' })} add="+ Column"
          render={(f, change) => (<><ColumnPick value={f.column} onChange={(column) => change({ column })} columns={cols} id={`${uid}-c`} /><span className="muted">empty →</span>
            <input value={f.value ?? ''} onChange={(e) => change({ value: e.target.value })} placeholder="value" aria-label="Value" /></>)} />
      </>)}
      {k === 'cast' && (<>
        {inputSelect()}
        <Rows items={step.casts} onChange={(casts) => set({ casts })} make={() => ({ column: '', to: 'number' })} add="+ Column"
          render={(c, change) => (<><ColumnPick value={c.column} onChange={(column) => change({ column })} columns={cols} id={`${uid}-c`} /><span className="muted">→</span>
            <select value={c.to} onChange={(e) => change({ to: e.target.value })} aria-label="Type">{Object.entries(TYPE_NAMES).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></>)} />
        <p className="muted small">A value that cannot be changed becomes empty.</p>
      </>)}
      {k === 'column' && (<>
        <div className="row">{inputSelect()}
          <label className="field"><span>Column name</span><input value={step.column} onChange={(e) => set({ column: e.target.value })} /></label></div>
        <label className="field"><span>Formula (SQL), e.g. <code>Amount * 0.17</code>, <code>upper(City)</code>, <code>CASE WHEN Amount &gt; 100 THEN 'big' ELSE 'small' END</code></span>
          <textarea className="mono" rows={3} value={step.expression} onChange={(e) => set({ expression: e.target.value })} /></label>
        {cols.length > 0 && <p className="muted small">Columns: {cols.map((c) => <code key={c}>{/^[A-Za-z_][A-Za-z0-9_]*$/.test(c) ? c : `"${c}"`}</code>).reduce((a, b) => [a, ' ', b])}</p>}
      </>)}
      {k === 'join' && (<>
        <div className="row">
          {inputSelect({ field: 'left', label: 'Left' })}
          <label className="field"><span>Join</span><select value={step.how} onChange={(e) => set({ how: e.target.value })}>
            <option value="left">Left (all of left)</option><option value="inner">Inner (only matches)</option><option value="right">Right (all of right)</option><option value="full">Full (all of both)</option></select></label>
          {inputSelect({ field: 'right', label: 'Right' })}
        </div>
        <Rows items={step.on} onChange={(on) => set({ on })} make={() => ({ left: '', right: '' })} add="+ Pair"
          render={(p, change) => (<><ColumnPick value={p.left} onChange={(left) => change({ left })} columns={colsOf(inputOf('left'))} placeholder="left column" id={`${uid}-l`} />
            <span className="muted">=</span><ColumnPick value={p.right} onChange={(right) => change({ right })} columns={colsOf(inputOf('right'))} placeholder="right column" id={`${uid}-r`} /></>)} />
      </>)}
      {k === 'union' && (<>
        <p className="muted small">Columns with the same name are put together; missing ones stay empty.</p>
        <Checks options={earlier} value={step.inputs} onChange={(inputs) => set({ inputs })} empty="Add steps before this one." />
      </>)}
      {k === 'group' && (<>
        {inputSelect()}
        <span className="etl-label">Group by</span>
        <Checks options={cols} value={step.by} onChange={(by) => set({ by })} empty="Run the steps before to see their columns." />
        <span className="etl-label">Totals</span>
        <Rows items={step.aggs} onChange={(aggs) => set({ aggs })} make={() => ({ agg: 'sum', column: '', as: '' })} add="+ Total"
          render={(a, change) => (<>
            <select value={a.agg} onChange={(e) => change({ agg: e.target.value })} aria-label="Total">{Object.entries(AGG_NAMES).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select>
            {a.agg !== 'countrows' && <ColumnPick value={a.column} onChange={(column) => change({ column })} columns={cols} id={`${uid}-c`} />}
            <input value={a.as} onChange={(e) => change({ as: e.target.value })} placeholder="name" aria-label="Name" /></>)} />
      </>)}
      {k === 'sort' && (<>
        <div className="row">{inputSelect()}
          <label className="field"><span>Keep the first</span><input type="number" min="1" value={step.limit || ''} placeholder="all" onChange={(e) => set({ limit: e.target.value ? Number(e.target.value) : null })} /></label></div>
        <Rows items={step.by} onChange={(by) => set({ by })} make={() => ({ column: '', dir: 'asc' })} add="+ Order by"
          render={(s, change) => (<><ColumnPick value={s.column} onChange={(column) => change({ column })} columns={cols} id={`${uid}-c`} />
            <select value={s.dir} onChange={(e) => change({ dir: e.target.value })} aria-label="Direction"><option value="asc">smallest first</option><option value="desc">largest first</option></select></>)} />
      </>)}
      {k === 'sql' && (<>
        <p className="muted small">One SELECT. Earlier steps are tables: {earlier.map((n) => <code key={n}>{n}</code>).reduce((a, b) => [a, ' ', b], [])}</p>
        <CodeBox language="sql" rows={10} value={step.sql} onChange={(sql) => set({ sql })} readOnly={readOnly} />
      </>)}
      {k === 'python' && (<>
        {!mayPython && <p className="notice warn">Changing a Python step needs the Python scripts right.</p>}
        <span className="etl-label">Steps it reads (the first is <code>df</code>, each is <code>dfs["name"]</code>; none ticked: the step before)</span>
        <Checks options={earlier} value={step.inputs} onChange={(inputs) => set({ inputs })} empty="Add steps before this one." />
        <CodeBox rows={14} value={step.script} onChange={(script) => set({ script })} readOnly={readOnly || !mayPython} />
      </>)}
    </fieldset>
  );
}

export default function PipelineEditor({ datasetId, info, datasets, folders, notify, onSaved, onDeleted, onFolders }) {
  const isNew = !datasetId || datasetId === 'new';
  const may = Boolean(info.mayPipelines);
  const mayPython = Boolean(info.mayPython);
  const [steps, setSteps] = useState([]);
  const [at, setAt] = useState(0);
  const [meta, setMeta] = useState({ name: '', folder: 'Analytics', description: '', refreshEveryMinutes: 0 });
  const [record, setRecord] = useState(null);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState('');
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    if (isNew) return;
    bi.dataset(datasetId).then(({ dataset }) => {
      setRecord(dataset);
      setSteps(dataset.steps || []);
      setMeta({ name: dataset.name, folder: dataset.folder || 'Analytics', description: dataset.description || '', refreshEveryMinutes: dataset.refreshEveryMinutes || 0 });
    }).catch((error) => notify(error.message, 'error'));
  }, [datasetId]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!record || !['queued', 'refreshing'].includes(record.status)) return undefined;
    const timer = setTimeout(() => bi.dataset(record.id).then(({ dataset }) => { setRecord(dataset); if (!['queued', 'refreshing'].includes(dataset.status)) onSaved(); }).catch(() => {}), 1500);
    return () => clearTimeout(timer);
  }, [record]); // eslint-disable-line react-hooks/exhaustive-deps

  const choices = useMemo(() => datasets.filter((d) => d.id !== datasetId), [datasets, datasetId]);
  const ran = useMemo(() => Object.fromEntries((result?.steps || []).map((s) => [s.name, s])), [result]);
  const colsOf = (name, depth = 0) => {
    if (!name || depth > 30) return [];
    if (ran[name]?.columns?.length) return ran[name].columns;
    const step = steps.find((s) => s.name === name);
    if (!step) return [];
    if (step.kind === 'source') return (datasets.find((d) => d.id === step.dataset)?.columns || []).map((c) => c.name);
    const index = steps.indexOf(step);
    const before = step.input || step.left || steps[index - 1]?.name;
    return colsOf(before, depth + 1);
  };
  const nameFor = (kind) => {
    const base = kind === 'source' ? 'source' : kind;
    let n = steps.filter((s) => s.kind === kind).length + 1;
    while (steps.some((s) => s.name === `${base}_${n}`)) n += 1;
    return `${base}_${n}`;
  };
  const add = (kind) => {
    const step = blank(kind, nameFor(kind));
    if (kind === 'sql' && steps.length) step.sql = `SELECT *\nFROM ${steps[steps.length - 1].name}`;
    const next = [...steps.slice(0, at + 1), step, ...steps.slice(at + 1)];
    setSteps(next); setAt(Math.min(at + 1, next.length - 1)); setAdding(false);
  };
  const move = (index, by) => {
    const next = [...steps]; const [item] = next.splice(index, 1); next.splice(index + by, 0, item);
    setSteps(next); setAt(index + by);
  };
  const run = async (until = '') => {
    if (!may || busy || !steps.length) return;
    setBusy(until ? `run:${until}` : 'run');
    try {
      const answer = await bi.runPipeline({ steps, ...(until ? { until } : {}), ...(isNew ? {} : { datasetId }) });
      setResult(answer);
      if (answer.failedStep) setAt(Math.max(0, steps.findIndex((s) => s.name === answer.failedStep)));
    } catch (error) { setResult({ ok: false, error: error.message, steps: [], columns: [], rows: [], rowCount: 0, durationMs: 0 }); }
    finally { setBusy(''); }
  };
  const save = async (event) => {
    event.preventDefault();
    if (!meta.name.trim()) { notify('Give the result dataset a name.', 'warn'); return; }
    setBusy('save');
    try {
      const body = { ...meta, sourceKind: 'pipeline', steps };
      if (isNew) {
        const { dataset } = await bi.createDataset(body);
        notify(`Saved “${dataset.name}”: every step ran${dataset.status === 'failed' ? `, but it failed: ${dataset.lastError}` : ''}.`, dataset.status === 'failed' ? 'warn' : 'ok');
        onSaved(dataset.id);
      } else {
        const { dataset, sourceChanged } = await bi.updateDataset(datasetId, body);
        setRecord(dataset);
        notify(sourceChanged ? 'Saved; the pipeline runs again with the new steps.' : 'Saved.', 'ok');
        onSaved();
      }
    } catch (error) { notify(error.message, 'error'); } finally { setBusy(''); }
  };
  const refresh = async () => { try { const { dataset } = await bi.refresh(datasetId); setRecord(dataset); } catch (error) { notify(error.message, 'error'); } };
  const remove = async () => {
    if (!window.confirm(`Delete the pipeline and its dataset “${record.name}”? Dashboards that use it lose those visuals.`)) return;
    try { await bi.deleteDataset(datasetId); onDeleted(); } catch (error) { notify(error.message, 'error'); }
  };

  if (!isNew && !record) return <p className="muted">Loading…</p>;
  if (isNew && !may) return <section className="card"><h1>Pipelines</h1><p className="muted">Pipelines (ETL / ELT) need the <b>Pipelines (ETL)</b> right as well as Analytics. Ask an administrator.</p></section>;
  const current = steps[at];
  const last = record?.lastSteps || [];
  const groups = ['Extract', 'Transform', 'Combine', 'Code'];
  return (
    <div className="etl stack">
      <div className="page-head bi-hero-px px-in py-head">
        <div>
          <p className="px-kicker"><b>ETL</b> — Analytics · Pipeline</p>
          <h1>{isNew ? 'New pipeline' : record.name}<span className="px-caret" aria-hidden="true" /></h1>
          <p className="muted">Take datasets, clean, join and total them step by step, and load the result as a dataset. Saved, it runs again on every refresh or on its schedule.</p>
        </div>
        {!isNew && (
          <div className="py-state">
            <span className={`py-pill ${record.status}`}>{record.status}</span>
            {record.status === 'ready' && <small>{(record.rowCount || 0).toLocaleString()} rows · {describeBytes(record.sizeBytes)}{record.refreshedAt ? ` · ${new Date(record.refreshedAt).toLocaleString()}` : ''}</small>}
            {record.lastError && <small className="py-last-error" title={record.lastError}>Last run: {record.lastError}</small>}
          </div>
        )}
      </div>
      {!may && <p className="notice warn">You can read this pipeline, but changing or running it needs the Pipelines (ETL) right.</p>}
      <div className="etl-split">
        <aside className="card etl-steps" aria-label="Steps">
          <div className="etl-steps-head"><b>Steps</b><em className="bi-count">{steps.length}</em>
            {may && <button type="button" className="btn small primary" onClick={() => run()} disabled={Boolean(busy) || !steps.length} title="Run every step">{busy === 'run' ? 'Running…' : '▶ Run all'}</button>}</div>
          <ol>
            {steps.map((step, i) => {
              const stat = ran[step.name] || (!result && last.find((s) => s.name === step.name));
              const meta2 = KIND_INFO[step.kind] || {};
              return (
                <li key={`${step.name}-${i}`} className={`etl-step ${i === at ? 'on' : ''} ${stat?.error ? 'bad' : stat ? 'ok' : ''}`}>
                  <button type="button" className="etl-step-main" onClick={() => setAt(i)}>
                    <i aria-hidden="true">{meta2.icon}</i>
                    <span><b>{step.name}</b><small>{meta2.label}{stat ? ` · ${stat.error ? 'failed' : `${(stat.rowCount || 0).toLocaleString()} rows`}` : ''}</small></span>
                  </button>
                  {may && i === at && (
                    <span className="etl-step-tools">
                      <button type="button" aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
                      <button type="button" aria-label="Move down" disabled={i === steps.length - 1} onClick={() => move(i, 1)}>↓</button>
                      <button type="button" aria-label="Delete step" onClick={() => { setSteps(steps.filter((_, j) => j !== i)); setAt(Math.max(0, i - 1)); }}>✕</button>
                    </span>
                  )}
                </li>
              );
            })}
          </ol>
          {may && (adding ? (
            <div className="etl-add">
              {groups.map((group) => (
                <div key={group}><span className="etl-label">{group}</span>
                  {Object.entries(KIND_INFO).filter(([kind, m]) => m.group === group && (kind !== 'python' || mayPython) && (kind === 'source' || steps.length)).map(([kind, m]) => (
                    <button key={kind} type="button" onClick={() => add(kind)} title={m.help}><i aria-hidden="true">{m.icon}</i><span><b>{m.label}</b><small>{m.help}</small></span></button>
                  ))}
                </div>
              ))}
              <button type="button" className="btn small quiet" onClick={() => setAdding(false)}>Cancel</button>
            </div>
          ) : <button type="button" className="btn etl-add-btn" onClick={() => setAdding(true)} disabled={steps.length >= (info.pipeline?.maxSteps || 30)}>＋ Add a step</button>)}
          {!steps.length && <p className="muted small">Start with a source: the dataset to take.</p>}
        </aside>
        <section className="card stack etl-main">
          {current ? (
            <>
              <div className="row py-bar">
                <b>{KIND_INFO[current.kind]?.label}</b><span className="muted small">{KIND_INFO[current.kind]?.help}</span><span className="spacer" />
                {may && <button type="button" className="btn" onClick={() => run(current.name)} disabled={Boolean(busy)}>{busy === `run:${current.name}` ? 'Running…' : '▶ Run to here'}</button>}
              </div>
              <StepForm key={at} step={current} index={at} steps={steps} datasets={choices} colsOf={colsOf} mayPython={mayPython} readOnly={!may}
                onChange={(next) => setSteps(steps.map((s, j) => (j === at ? next : s)))} />
            </>
          ) : <div className="viz-empty">Add a step on the left.</div>}
          {result && (
            <div className="py-result stack" aria-busy={Boolean(busy)}>
              <div className={`py-status ${result.ok ? 'ok' : 'bad'}`}>
                <b>{result.ok ? '✓ Ran' : '✕ Failed'}</b>
                {result.ok && <span>{(result.steps.at(-1)?.name) || ''}: {result.rowCount.toLocaleString()} rows · {result.columns.length} columns</span>}
                <span>{(result.durationMs / 1000).toFixed(1)} s</span>
              </div>
              {result.error && <pre className="py-error">{result.error}</pre>}
              {result.steps.length > 0 && (
                <div className="etl-timeline">{result.steps.map((s) => (
                  <span key={s.name} className={s.error ? 'bad' : 'ok'} title={s.error || `${s.columns.length} columns`}><b>{s.name}</b>{s.error ? '✕' : (s.rowCount || 0).toLocaleString()}<small>{s.durationMs} ms</small></span>
                ))}</div>
              )}
              {result.steps.filter((s) => s.output).map((s) => <div key={s.name}><small className="py-label">Printed by {s.name}</small><pre className="py-output">{s.output}</pre></div>)}
              {result.ok && result.rows.length > 0 && (
                <div className="viz-table-wrap py-table">
                  <table className="viz-table">
                    <thead><tr>{result.columns.map((name) => <th key={name}>{name}</th>)}</tr></thead>
                    <tbody>{result.rows.map((row, i) => <tr key={i}>{row.map((value, j) => <td key={j}>{value === null ? <i className="muted">null</i> : String(value)}</td>)}</tr>)}</tbody>
                  </table>
                </div>
              )}
            </div>
          )}
          <p className="muted small py-limits">Blocks and SQL run in DuckDB and can read only the source datasets: no files, no network. A run stops after {info.pipeline?.timeoutSeconds || 900} s. Every run is in the audit log.</p>
        </section>
      </div>
      {may && (
        <form className="card py-save" onSubmit={save}>
          <b>{isNew ? 'Load into a dataset' : 'Result dataset'}</b>
          <label className="field"><span>Name</span><input value={meta.name} onChange={(e) => setMeta({ ...meta, name: e.target.value })} placeholder="Sales by region" maxLength={120} required /></label>
          <div className="field"><span>Folder</span><AnalyticsFolder folders={folders} value={meta.folder} mayManage={info.mayModel} notify={notify} onFoldersChanged={onFolders}
            onChange={(folder) => setMeta((current) => ({ ...current, folder }))} /></div>
          <label className="field"><span>Run again</span>
            <select value={meta.refreshEveryMinutes} onChange={(e) => setMeta({ ...meta, refreshEveryMinutes: Number(e.target.value) })}>
              {SCHEDULES.map(([minutes, label]) => <option key={minutes} value={minutes}>{label}</option>)}
              {!SCHEDULES.some(([m]) => m === meta.refreshEveryMinutes) && <option value={meta.refreshEveryMinutes}>Every {meta.refreshEveryMinutes} minutes</option>}
            </select></label>
          <div className="row">
            <button type="submit" className="btn primary" disabled={Boolean(busy) || !steps.length}>{busy === 'save' ? 'Saving…' : isNew ? 'Save pipeline' : 'Save'}</button>
            {!isNew && <button type="button" className="btn" onClick={refresh} disabled={['queued', 'refreshing'].includes(record.status)}>↻ Run again now</button>}
            {!isNew && <button type="button" className="btn danger" onClick={remove}>Delete</button>}
          </div>
        </form>
      )}
    </div>
  );
}
