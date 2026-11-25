// Sub-datasets (Jaspersoft Studio: Outline → Datasets → Create Dataset): a name, an own query, its fields and
// parameters. A Table or Crosstab can then read this dataset instead of the report's main query.
import { useEffect, useState } from 'react';
import { api } from './api.js';
import { inferColumns, mapElements, NAME_PATTERN, nextName } from './designModel.js';
import { AdapterOptions } from './panels.jsx';
import { cleanCollection } from './SetupForm.jsx';

const PARAM_TYPES = ['string', 'integer', 'number', 'date', 'boolean'];

/** Fields a Table / Crosstab may use: its dataset's fields, else the report's fields and calculated fields. */
export function componentFields(element, report, design) {
  const dataset = element?.datasetName ? (design.datasets || []).find((d) => d.name === element.datasetName) : null;
  if (dataset) return dataset.fields;
  return [...report.columns, ...design.calculatedFields.map((c) => c.name)];
}

export function newDataset(design, report) {
  const name = nextName('Dataset', (design.datasets || []).map((d) => d.name));
  const sql = report.queryLanguage === 'sql';
  return { name, connectionId: '', queryLanguage: sql ? 'sql' : 'mongosh', collection: '', querySource: '', fields: [], parameters: [] };
}

export default function DatasetsEditor({ state, dispatch, notify, initialName }) {
  const { design, report } = state;
  const datasets = design.datasets || [];
  const [current, setCurrent] = useState(initialName || datasets[0]?.name || '');
  const [adapters, setAdapters] = useState([]);
  const [busy, setBusy] = useState(false);
  const [nameDraft, setNameDraft] = useState('');
  useEffect(() => { api.connections('designer').then((r) => setAdapters(r.connections || [])).catch(() => setAdapters([])); }, []);
  const dataset = datasets.find((d) => d.name === current) || null;
  useEffect(() => { setNameDraft(dataset?.name || ''); }, [dataset?.name]);

  const save = (next, key) => dispatch({ type: 'DESIGN', key, update: (d) => ({ ...d, datasets: next(d.datasets || []) }) });
  const patch = (values) => save((list) => list.map((d) => (d.name === current ? { ...d, ...values } : d)), `ds-${current}-${Object.keys(values).join()}`);
  const users = (name) => {
    let count = 0;
    mapElements(design, (el) => { if (el.datasetName === name) count += 1; return el; });
    return count;
  };

  const add = () => {
    const created = newDataset(design, report);
    save((list) => [...list, created]);
    setCurrent(created.name);
  };
  const remove = () => {
    const used = users(current);
    if (!window.confirm(used ? `${used} component(s) use ${current}. They will read the report's main query again. Delete the dataset?` : `Delete the dataset ${current}?`)) return;
    dispatch({ type: 'DESIGN', update: (d) => mapElements({ ...d, datasets: (d.datasets || []).filter((x) => x.name !== current) },
      (el) => (el.datasetName === current ? { ...el, datasetName: '', datasetParameters: [] } : el)) });
    setCurrent(datasets.find((d) => d.name !== current)?.name || '');
  };
  const rename = () => {
    const to = nameDraft.trim();
    if (to === current) return;
    if (!NAME_PATTERN.test(to) || datasets.some((d) => d.name === to)) {
      notify('A dataset name needs letters, digits or _ (starting with a letter) and must be unique', 'error');
      setNameDraft(current);
      return;
    }
    dispatch({ type: 'DESIGN', update: (d) => mapElements({ ...d, datasets: (d.datasets || []).map((x) => (x.name === current ? { ...x, name: to } : x)) },
      (el) => (el.datasetName === current ? { ...el, datasetName: to } : el)) });
    setCurrent(to);
  };

  const adapterId = dataset?.connectionId || report.connectionId;
  const adapter = adapters.find((a) => a.id === adapterId);
  const isSql = dataset?.queryLanguage === 'sql';
  const chooseAdapter = (connectionId) => {
    const chosen = adapters.find((a) => a.id === (connectionId || report.connectionId));
    const sql = Boolean(chosen && chosen.type && chosen.type !== 'mongodb');
    patch({ connectionId, ...(sql && !isSql ? { queryLanguage: 'sql', collection: '' } : {}), ...(!sql && chosen && isSql ? { queryLanguage: 'mongosh' } : {}) });
  };
  const addParameters = (names) => {
    const known = new Set(dataset.parameters.map((p) => p.name));
    const added = names.filter((n) => !known.has(n));
    return { added, parameters: [...dataset.parameters, ...added.map((name) => ({ name, type: report.parameters.find((p) => p.name === name)?.type || 'string' }))] };
  };
  const readFields = async () => {
    setBusy(true);
    try {
      if (isSql) {
        const result = await api.queryFields(adapterId, dataset.querySource);
        const { added, parameters } = addParameters(result.parameterNames || []);
        patch({ fields: result.columns, parameters });
        notify(`${dataset.name}: ${result.columns.length} fields read${added.length ? ` · parameter(s) ${added.join(', ')} added` : ''}`, 'ok');
      } else {
        const result = await api.parseQuery(dataset.queryLanguage, dataset.querySource);
        const columns = inferColumns(result.pipeline);
        const { added, parameters } = addParameters(result.parameterNames || []);
        patch({ ...(columns.length ? { fields: columns } : {}), parameters, ...(!dataset.collection ? { collection: result.collection || (/getCollection\(|^db\./.test(dataset.querySource.trim()) ? cleanCollection(dataset.querySource) : '') } : {}) });
        notify(columns.length ? `${dataset.name}: ${columns.length} fields found${added.length ? ` · parameter(s) ${added.join(', ')} added` : ''}`
          : 'No fields found in the last $project/$group stage: type them below', columns.length ? 'ok' : 'error');
      }
    } catch (error) { notify(`The fields could not be read: ${error.message}`, 'error'); } finally { setBusy(false); }
  };
  const setParam = (index, values) => patch({ parameters: dataset.parameters.map((p, i) => (i === index ? { ...p, ...values } : p)) });

  return (
    <div className="datasets">
      <aside className="datasets-list">
        {datasets.map((d) => (
          <button key={d.name} className={`ds-item ${d.name === current ? 'on' : ''}`} onClick={() => setCurrent(d.name)}>
            <b>{d.name}</b><small>{d.fields.length} fields · {users(d.name)} used</small>
          </button>
        ))}
        {!datasets.length && <p className="muted">No datasets yet.</p>}
        <button className="btn primary" onClick={add}>＋ New dataset</button>
      </aside>
      {dataset ? (
        <section className="datasets-form">
          <div className="form-grid">
            <label>Name<input value={nameDraft} onChange={(e) => setNameDraft(e.target.value)} onBlur={rename} onKeyDown={(e) => e.key === 'Enter' && rename()} /></label>
            <label>Data adapter
              <select value={dataset.connectionId} onChange={(e) => chooseAdapter(e.target.value)}>
                <option value="">Same as the report ({report.connectionId || 'none'})</option>
                <AdapterOptions adapters={adapters} describe={(a) => `${a.name}${a.database ? ` · ${a.database}` : ''}`} />
              </select>
            </label>
            <label>Query language
              <select value={dataset.queryLanguage} onChange={(e) => patch({ queryLanguage: e.target.value, ...(e.target.value === 'sql' ? { collection: '' } : {}) })}>
                <optgroup label="MongoDB">
                  <option value="mongosh">Mongo Shell</option><option value="aggregation-json">Aggregation JSON</option>
                  <option value="javascript">JavaScript pipeline</option><option value="python">Python pipeline</option>
                </optgroup>
                <optgroup label="SQL databases"><option value="sql">SQL — PostgreSQL, MySQL, SQL Server, Oracle</option></optgroup>
              </select>
              {adapter && ((adapter.type || 'mongodb') !== 'mongodb') !== isSql && <small className="err">{isSql ? 'Choose a SQL data adapter for a SQL query.' : 'This adapter is a SQL database: choose a MongoDB adapter or SQL.'}</small>}
            </label>
            {!isSql && <label>Collection<input value={dataset.collection} onChange={(e) => patch({ collection: e.target.value })} placeholder="Collection name" /></label>}
          </div>
          <label className="block">Query <small>{isSql ? '(one SELECT; $P{Name} are this dataset\'s parameters)' : '(aggregation; $P{Name} are this dataset\'s parameters)'}</small>
            <textarea className="code" rows={8} spellCheck={false} value={dataset.querySource} onChange={(e) => patch({ querySource: e.target.value })}
              placeholder={isSql ? 'SELECT item, qty, price\nFROM order_lines\nWHERE order_id = $P{OrderId}' : 'db.getCollection("OrderLines").aggregate([\n  {"$match": {"OrderId": $P{OrderId}}},\n  {"$project": {"Item": 1, "Qty": 1}}\n])'} />
          </label>
          <div className="row">
            <button className="btn primary" disabled={busy || !dataset.querySource.trim() || (isSql && !adapterId)} onClick={readFields}>Read fields</button>
            {adapter && <span className="muted">{adapter.name}</span>}
          </div>
          <label className="block">Fields <small>(comma separated)</small>
            <input value={dataset.fields.join(', ')} onChange={(e) => patch({ fields: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) })} />
          </label>
          <div className="prop-section">Parameters <small>— the Table / Crosstab passes a value for each (Dataset tab of the component)</small></div>
          <table className="grid">
            <thead><tr><th>Name</th><th>Type</th><th /></tr></thead>
            <tbody>
              {dataset.parameters.map((p, i) => (
                // eslint-disable-next-line react/no-array-index-key -- the position is the identity
                <tr key={i}>
                  <td><input value={p.name} onChange={(e) => setParam(i, { name: e.target.value.replace(/[^A-Za-z0-9_]/g, '') })} /></td>
                  <td><select value={p.type} onChange={(e) => setParam(i, { type: e.target.value })}>{PARAM_TYPES.map((t) => <option key={t}>{t}</option>)}</select></td>
                  <td><button className="icon" title="Remove" onClick={() => patch({ parameters: dataset.parameters.filter((_, j) => j !== i) })}>✕</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="row">
            <button className="btn sm" onClick={() => patch({ parameters: [...dataset.parameters, { name: nextName('Param', dataset.parameters.map((p) => p.name)), type: 'string' }] })}>＋ Parameter</button>
            <span className="grow" />
            <button className="btn sm danger-text" onClick={remove}>Delete dataset</button>
          </div>
          <p className="hint">Like Jasper's sub-dataset: the query runs once for each different set of parameter values (e.g. once per customer when the Table is in the Detail band). A parameter without a value from the component takes the report parameter of the same name.</p>
        </section>
      ) : (
        <section className="datasets-form empty-state">
          <h4>Datasets</h4>
          <p className="muted">A dataset is a second query in the same report. Use it for a Table or Crosstab that shows other data than the main report, e.g. order lines next to the orders, or a summary from another collection or table.</p>
          <button className="btn primary" onClick={add}>＋ Create the first dataset</button>
        </section>
      )}
    </div>
  );
}
