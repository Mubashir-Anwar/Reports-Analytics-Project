// A MongoDB collection in the Query workspace: Documents (find with filter / projection / sort, page by page),
// Aggregate (a pipeline built stage by stage, like Studio 3T: pick a stage to see what comes out of it) and Fields
// (what a sample of documents contains). Everything is read-only.
import { useEffect, useRef, useState } from 'react';
import { api } from '../api.js';
import Grid, { shellJson } from './Grid.jsx';
import UseQuery from './UseQuery.jsx';
import { SaveQueryButton } from '../shared/SavedQueries.jsx';
import QueryHelper from './QueryHelper.jsx';
import { FIELD_MIME, addToText, buildMongo, emptyHelper, fieldTypes, newFilter, readDrag } from './queryHelper.js';

export const STAGES = {
  $match: '{\n  Field: "value"\n}',
  $project: '{\n  _id: 0,\n  Field: 1\n}',
  $group: '{\n  _id: "$Field",\n  count: { $sum: 1 }\n}',
  $sort: '{\n  Field: -1\n}',
  $limit: '10',
  $skip: '0',
  $unwind: '"$ArrayField"',
  $lookup: '{\n  from: "otherCollection",\n  localField: "Field",\n  foreignField: "_id",\n  as: "joined"\n}',
  $addFields: '{\n  newField: { $concat: ["$A", " ", "$B"] }\n}',
  $set: '{\n  Field: "value"\n}',
  $unset: '"Field"',
  $count: '"total"',
  $sortByCount: '"$Field"',
  $replaceRoot: '{\n  newRoot: "$Field"\n}',
  $bucket: '{\n  groupBy: "$Amount",\n  boundaries: [0, 100, 1000],\n  default: "other"\n}',
  $facet: '{\n  byBranch: [{ $sortByCount: "$Branch" }]\n}',
  $sample: '{ size: 20 }',
};
/** The type names of the Fields sample → the types of the query helper. */
const FIELD_TYPE = { string: 'str', str: 'str', int: 'num', long: 'num', double: 'num', decimal: 'num', number: 'num', bool: 'bool', boolean: 'bool', date: 'date', objectId: 'oid', ObjectId: 'oid' };
let nextId = 1;
export const newStage = (op = '$match', body) => ({ id: `s${Date.now().toString(36)}${nextId++}`, op, body: body ?? STAGES[op] ?? '{}', on: true });
const stageText = (s) => `{ ${s.op}: ${s.body.trim() || '{}'} }`;

/** Props for a text box that takes a dragged field or value. */
function useDrop(onDropField) {
  const [over, setOver] = useState(false);
  return [over, {
    onDragOver: (e) => { if (e.dataTransfer.types.includes(FIELD_MIME)) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; setOver(true); } },
    onDragLeave: () => setOver(false),
    onDrop: (e) => { const what = readDrag(e); setOver(false); if (what) { e.preventDefault(); onDropField(what); } },
  }];
}

function Field({ label, value, onChange, placeholder, onEnter, kind }) {
  const [over, drop] = useDrop((what) => onChange(addToText(value, what, kind)));
  return (
    <label className={`qm-field ${over ? 'over' : ''}`} {...drop}>
      <span>{label}</span>
      <input className="mono" value={value} placeholder={placeholder} spellCheck={false}
        onChange={(e) => onChange(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') onEnter(); }} />
    </label>
  );
}

/** A find (filter, projection, sort, limit) as pipeline stages, so it saves and opens like any other MongoDB query. */
function findAsStages(q) {
  const stages = [];
  if (q.filter?.trim()) stages.push({ op: '$match', body: q.filter.trim(), on: true });
  if (q.sort?.trim()) stages.push({ op: '$sort', body: q.sort.trim(), on: true });
  if (q.projection?.trim()) stages.push({ op: '$project', body: q.projection.trim(), on: true });
  if (q.limit) stages.push({ op: '$limit', body: String(q.limit), on: true });
  return stages.length ? stages : [{ op: '$match', body: '{}', on: true }];
}

function Documents({ tab, onChange, notify }) {
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const q = tab.find || { filter: '', projection: '', sort: '', limit: 50 };
  const set = (patch) => onChange({ find: { ...q, ...patch } });
  const run = async (skip = 0, query = q) => {
    setBusy(true); setError('');
    try {
      setResult(await api.mongoFind({ connectionId: tab.connectionId, collection: tab.collection, ...query, skip }));
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  useEffect(() => { run(0); }, [tab.connectionId, tab.collection]); // eslint-disable-line react-hooks/exhaustive-deps
  const skip = result?.skip || 0;
  const shown = result?.documents?.length || 0;
  const helper = tab.helper || emptyHelper();
  const helperOpen = tab.helperOpen ?? true;
  // the types seen so far: a filter that finds nothing must not forget them
  const [types, setTypes] = useState({});
  useEffect(() => { if (result?.documents?.length) setTypes((old) => ({ ...old, ...fieldTypes(result.documents) })); }, [result]);
  useEffect(() => { setTypes({}); }, [tab.connectionId, tab.collection]);
  const built = buildMongo(helper, types);
  const columns = Object.keys(types);
  const addFilter = (what) => onChange({ helperOpen: true, helper: { ...helper, filters: [...helper.filters, newFilter(what)] } });
  const apply = () => { const next = { ...q, ...built }; onChange({ find: next }); run(0, next); };
  const grid = <Grid documents={result?.documents || []} busy={busy} name={tab.collection} empty="No documents match" onFilter={addFilter}
    extra={!helperOpen && <HelperButton onOpen={() => onChange({ helperOpen: true })} onDropField={addFilter} />} />;
  return (
    <div className="qm-docs">
      <div className="qm-find">
        <Field kind="filter" label="Filter" value={q.filter} onChange={(v) => set({ filter: v })} placeholder='{ Branch: "KHI", Amount: { $gt: 100 } }' onEnter={() => run(0)} />
        <Field kind="projection" label="Projection" value={q.projection} onChange={(v) => set({ projection: v })} placeholder="{ Name: 1, Amount: 1 }" onEnter={() => run(0)} />
        <Field kind="sort" label="Sort" value={q.sort} onChange={(v) => set({ sort: v })} placeholder="{ Amount: -1 }" onEnter={() => run(0)} />
        <label className="qm-field small"><span>Limit</span>
          <select value={q.limit} onChange={(e) => set({ limit: Number(e.target.value) })}>{[20, 50, 100, 500].map((n) => <option key={n}>{n}</option>)}</select>
        </label>
        <button type="button" className="btn primary qm-go" disabled={busy} onClick={() => run(0)}>{busy ? 'Finding…' : '▶ Find'}</button>
        <span className="qm-save">
          <SaveQueryButton saved={tab.saved} notify={notify} onSaved={(item) => onChange({ saved: item, title: item.name })}
            query={() => {
              const stages = findAsStages(q);
              const text = `db.getCollection("${tab.collection}").aggregate([\n${stages.map((s) => `  { ${s.op}: ${s.body} }`).join(',\n')}\n])`;
              return { kind: 'mongo', connectionId: tab.connectionId, collection: tab.collection, text, stages };
            }} />
        </span>
      </div>
      {error && <div className="q-error" role="alert">{error}</div>}
      {result && (
        <div className="qm-pager">
          <span>{shown ? `${skip + 1}–${skip + shown}` : '0'} of {result.total ?? 'many'} · {result.ms} ms</span>
          <button type="button" className="btn sm" disabled={busy || skip === 0} onClick={() => run(Math.max(0, skip - q.limit))}>‹ Previous</button>
          <button type="button" className="btn sm" disabled={busy || shown < q.limit || (result.total != null && skip + shown >= result.total)} onClick={() => run(skip + q.limit)}>Next ›</button>
        </div>
      )}
      <div className={`q-work ${helperOpen ? 'with-helper' : ''}`}>
        {grid}
        {helperOpen && (
          <QueryHelper kind="mongo" helper={helper} columns={columns} types={types} onChange={(h) => onChange({ helper: h })}
            preview={[built.filter && `filter: ${built.filter}`, built.projection && `projection: ${built.projection}`, built.sort && `sort: ${built.sort}`].filter(Boolean).join('\n')}
            onApply={apply} onClear={() => { onChange({ helper: emptyHelper() }); }} onClose={() => onChange({ helperOpen: false })} />
        )}
      </div>
    </div>
  );
}

/** When the helper is closed: a button that opens it, and takes a dropped column straight into the filter. */
export function HelperButton({ onOpen, onDropField }) {
  const [over, setOver] = useState(false);
  return (
    <button type="button" className={`btn sm qh-open ${over ? 'over' : ''}`} onClick={onOpen} title="Open the query helper (or drop a column here)"
      onDragOver={(e) => { if (e.dataTransfer.types.includes(FIELD_MIME)) { e.preventDefault(); setOver(true); } }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); const what = readDrag(e); if (what) onDropField(what); }}>🧩 Helper</button>
  );
}

/** A dropped field in a stage: a condition, a projection or a sort; in any other stage its "$name" at the cursor. */
function StageText({ stage, onChange, onFocus }) {
  const [over, drop] = useDrop((what) => {
    if (stage.op === '$match') onChange(addToText(stage.body, what, 'filter'));
    else if (['$project', '$sort'].includes(stage.op)) onChange(addToText(stage.body, what, 'sort'));
    else onChange(`${stage.body.trim()}${stage.body.trim() ? ' ' : ''}"$${what.field}"`);
  });
  return (
    <textarea className={`mono ${over ? 'over' : ''}`} spellCheck={false} rows={Math.min(10, Math.max(2, stage.body.split('\n').length))} value={stage.body}
      onFocus={onFocus} onChange={(e) => onChange(e.target.value)} {...drop} />
  );
}

function Aggregate({ tab, onChange, notify }) {
  const [first] = useState(() => [newStage('$match', '{}')]);   // the same empty stage until something is typed
  const stages = tab.stages?.length ? tab.stages : first;
  const [selected, setSelected] = useState(tab.selectedStage ?? stages.length - 1);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [showCode, setShowCode] = useState(false);
  const timer = useRef(null);
  const setStages = (next) => onChange({ stages: next });
  const update = (id, patch) => setStages(stages.map((s) => (s.id === id ? { ...s, ...patch } : s)));
  const upTo = Math.min(selected, stages.length - 1);
  const enabled = (n) => stages.slice(0, n + 1).filter((s) => s.on).map(stageText);

  const run = async (n = upTo) => {
    setBusy(true); setError('');
    try {
      setResult({ ...(await api.mongoAggregate({ connectionId: tab.connectionId, collection: tab.collection, stages: enabled(n), limit: tab.aggLimit || 50 })), upTo: n });
    } catch (e) { setError(e.message); setResult(null); } finally { setBusy(false); }
  };
  // a short pause after typing, then the output of the chosen stage is shown again
  useEffect(() => {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => run(upTo), 700);
    return () => clearTimeout(timer.current);
  }, [JSON.stringify(stages.slice(0, upTo + 1)), upTo, tab.collection]); // eslint-disable-line react-hooks/exhaustive-deps

  const add = (after) => {
    const stage = newStage('$match', '{}');
    const next = [...stages.slice(0, after + 1), stage, ...stages.slice(after + 1)];
    setStages(next); setSelected(after + 1); onChange({ stages: next, selectedStage: after + 1 });
  };
  const remove = (index) => { const next = stages.filter((_, i) => i !== index); setStages(next.length ? next : [newStage()]); setSelected(Math.max(0, index - 1)); };
  const move = (index, by) => {
    const to = index + by;
    if (to < 0 || to >= stages.length) return;
    const next = [...stages]; [next[index], next[to]] = [next[to], next[index]];
    setStages(next); setSelected(to);
  };
  const code = `db.getCollection("${tab.collection}").aggregate([\n${stages.filter((s) => s.on).map((s) => `  ${stageText(s).replace(/\n/g, '\n  ')}`).join(',\n')}\n])`;

  return (
    <div className="qm-agg">
      <div className="qm-stages">
        <div className="qm-stages-head">
          <b>Pipeline</b><span className="muted">{stages.filter((s) => s.on).length} stages</span>
          <span className="grow" />
          <SaveQueryButton saved={tab.saved} notify={notify} onSaved={(item) => onChange({ saved: item, title: item.name })}
            query={() => ({ kind: 'mongo', connectionId: tab.connectionId, collection: tab.collection, text: code, stages: stages.map(({ op, body, on }) => ({ op, body, on })) })} />
          <UseQuery query={() => ({ connectionId: tab.connectionId, collection: tab.collection, queryLanguage: 'mongosh', querySource: code })} />
          <button type="button" className="btn sm" onClick={() => setShowCode(!showCode)}>{showCode ? 'Stages' : '</> Code'}</button>
        </div>
        {showCode ? (
          <div className="qm-code">
            <pre>{code}</pre>
            <div className="qm-code-actions">
              <button type="button" className="btn sm" onClick={() => { navigator.clipboard?.writeText(code); notify('Copied the Mongo Shell code'); }}>Copy</button>
            </div>
          </div>
        ) : (
          <ol className="qm-stage-list">
            {stages.map((s, i) => (
              <li key={s.id} className={`qm-stage ${i === upTo ? 'on' : ''} ${s.on ? '' : 'off'}`} onClick={() => setSelected(i)} style={{ '--i': i }}>
                <div className="qm-stage-head">
                  <span className="qm-stage-n">{i + 1}</span>
                  <select value={s.op} onClick={(e) => e.stopPropagation()} onChange={(e) => update(s.id, { op: e.target.value, body: STAGES[e.target.value] })}>
                    {Object.keys(STAGES).map((op) => <option key={op}>{op}</option>)}
                  </select>
                  <span className="grow" />
                  <label className="qm-switch" title="Use this stage" onClick={(e) => e.stopPropagation()}><input type="checkbox" checked={s.on} onChange={(e) => update(s.id, { on: e.target.checked })} /><i /></label>
                  <button type="button" className="icon-btn" title="Move up" onClick={(e) => { e.stopPropagation(); move(i, -1); }}>↑</button>
                  <button type="button" className="icon-btn" title="Move down" onClick={(e) => { e.stopPropagation(); move(i, 1); }}>↓</button>
                  <button type="button" className="icon-btn danger" title="Remove" onClick={(e) => { e.stopPropagation(); remove(i); }}>✕</button>
                </div>
                <StageText stage={s} onFocus={() => setSelected(i)} onChange={(body) => update(s.id, { body })} />
                <button type="button" className="qm-add" title="Add a stage after this one" onClick={(e) => { e.stopPropagation(); add(i); }}>＋ stage</button>
              </li>
            ))}
          </ol>
        )}
      </div>
      <div className="qm-out">
        <div className="qm-out-head">
          <div>
            <b>{result ? `Output of stage ${result.upTo + 1}` : 'Output'}</b>
            <span className="muted"> {result ? `${result.documents.length}${result.more ? '+' : ''} documents · ${result.ms} ms` : ''}</span>
          </div>
          <span className="grow" />
          <select value={tab.aggLimit || 50} onChange={(e) => onChange({ aggLimit: Number(e.target.value) })} aria-label="How many documents">
            {[20, 50, 100, 500].map((n) => <option key={n} value={n}>{n} docs</option>)}
          </select>
          <button type="button" className="btn primary" disabled={busy} onClick={() => { setSelected(stages.length - 1); run(stages.length - 1); }}>▶ Run all</button>
        </div>
        {error && <div className="q-error" role="alert">{error}</div>}
        <Grid documents={result?.documents || []} busy={busy} name={`${tab.collection}-aggregate`} empty="Nothing comes out of this stage" />
      </div>
    </div>
  );
}

function Fields({ tab, onUse }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    setData(null); setError('');
    api.mongoFields(tab.connectionId, tab.collection).then(setData, (e) => setError(e.message));
  }, [tab.connectionId, tab.collection]);
  if (error) return <div className="q-error">{error}</div>;
  if (!data) return <div className="q-loading"><span className="q-spin" /> Reading a sample…</div>;
  return (
    <div className="qm-fields">
      <p className="muted">From a random sample of {data.sampled} documents. Click a field to filter by it.</p>
      <table>
        <thead><tr><th>Field</th><th>Types</th><th>In</th><th>Example</th></tr></thead>
        <tbody>
          {data.fields.map((f) => (
            <tr key={f.path} onClick={() => onUse(f)}>
              <td className="mono" style={{ paddingLeft: 10 + f.path.split('.').length * 10 - 10 }}>{f.path.split('.').pop()}<small>{f.path.includes('.') ? ` ${f.path}` : ''}</small></td>
              <td>{Object.entries(f.types).map(([t, n]) => <span key={t} className={`q-type t-${t}`}>{t}{Object.keys(f.types).length > 1 ? ` ${Math.round((n / f.count) * 100)}%` : ''}</span>)}</td>
              <td><span className="q-share"><i style={{ width: `${Math.round(f.share * 100)}%` }} /></span>{Math.round(f.share * 100)}%</td>
              <td className="mono muted">{f.example === null || f.example === undefined ? '' : shellJson(f.example).slice(0, 60)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function MongoView({ tab, onChange, notify }) {
  const mode = tab.mode || 'docs';
  const modes = [['docs', 'Documents', '▤'], ['aggregate', 'Aggregate', '⇶'], ['fields', 'Fields', '⌗']];
  return (
    <div className="qm">
      <div className="q-modes" role="tablist">
        {modes.map(([key, label, icon]) => (
          <button key={key} type="button" role="tab" aria-selected={mode === key} className={mode === key ? 'on' : ''} onClick={() => onChange({ mode: key })}><span aria-hidden="true">{icon}</span>{label}</button>
        ))}
      </div>
      <div className="q-mode-body" key={mode}>
        {mode === 'docs' && <Documents tab={tab} onChange={onChange} notify={notify} />}
        {mode === 'aggregate' && <Aggregate tab={tab} onChange={onChange} notify={notify} />}
        {mode === 'fields' && <Fields tab={tab} onUse={(f) => { const helper = tab.helper || emptyHelper(); onChange({ mode: 'docs', helperOpen: true, helper: { ...helper, filters: [...helper.filters, newFilter({ field: f.path, type: FIELD_TYPE[Object.keys(f.types)[0]] && Object.keys(f.types).length === 1 ? FIELD_TYPE[Object.keys(f.types)[0]] : '' })] } }); }} />}
      </div>
    </div>
  );
}
