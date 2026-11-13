// "Dataset and Query" (Jaspersoft Studio's toolbar button / Outline → Dataset and Query):
// the data adapter, query and fields, the parameters, the dataset filter expression and the sort fields.
import { useState } from 'react';
import { ParametersEditor, QueryEditor } from './SetupForm.jsx';

const TABS = [['query', 'Query'], ['parameters', 'Parameters'], ['filter', 'Filter Expression'], ['sort', 'Sort Fields']];

function SortEditor({ state, dispatch }) {
  const { report, design } = state;
  const sort = design.sort || [];
  const fields = [...report.columns, ...design.calculatedFields.map((c) => c.name)];
  const setSort = (next) => dispatch({ type: 'DESIGN', update: (d) => ({ ...d, sort: next }) });
  return (
    <>
      <p className="muted">The records are sorted by these fields before the report is filled (after the query). Groups sort by their own field first.</p>
      <table className="grid">
        <thead><tr><th>Field</th><th>Order</th><th /></tr></thead>
        <tbody>
          {sort.map((s, i) => (
            // eslint-disable-next-line react/no-array-index-key -- the position is the identity
            <tr key={i}>
              <td><select value={s.field} onChange={(e) => setSort(sort.map((x, j) => (j === i ? { ...x, field: e.target.value } : x)))}>{fields.map((f) => <option key={f}>{f}</option>)}</select></td>
              <td><select value={s.order} onChange={(e) => setSort(sort.map((x, j) => (j === i ? { ...x, order: e.target.value } : x)))}><option>Ascending</option><option>Descending</option></select></td>
              <td><button className="icon" onClick={() => setSort(sort.filter((_, j) => j !== i))}>✕</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row"><button className="btn" disabled={!fields.length} onClick={() => setSort([...sort, { field: fields[0], order: 'Ascending' }])}>+ Add sort field</button>{!fields.length && <span className="muted">Read the fields first (Query tab).</span>}</div>
    </>
  );
}

function FilterEditor({ state, dispatch }) {
  const { design, report } = state;
  return (
    <>
      <p className="muted">Like Jasper's dataset <b>Filter Expression</b>: only records for which it is <code>true</code> are printed. Use <code>$F&#123;…&#125;</code> fields and <code>$P&#123;…&#125;</code> parameters. Empty = all records. (Filtering in the query itself is faster for big data.)</p>
      <textarea className="code" rows={5} spellCheck={false} value={design.filterExpression || ''} placeholder={`$F{${report.columns[0] || 'Status'}} != null && $F{${report.columns[0] || 'Status'}}.equals("Active")`} onChange={(e) => dispatch({ type: 'DESIGN', key: 'filter', update: (d) => ({ ...d, filterExpression: e.target.value }) })} />
      <p className="hint">Examples: <code>$F&#123;Amount&#125; &gt; 0</code> · <code>$F&#123;Branch&#125;.startsWith("KHI")</code> · <code>$P&#123;OnlyOpen&#125; == false || $F&#123;Status&#125;.equals("Open")</code></p>
    </>
  );
}

export default function DatasetQueryDialog({ state, dispatch, notify, onClose, initialTab = 'query' }) {
  const [tab, setTab] = useState(initialTab);
  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal dataset-modal" role="dialog" aria-label="Dataset and Query">
        <div className="dataset-head">
          <h3>Dataset and Query — {state.report.title || 'Report'}</h3>
          <button className="icon" onClick={onClose} title="Close">✕</button>
        </div>
        <div className="prop-tabs">{TABS.map(([id, label]) => <button key={id} className={tab === id ? 'on' : ''} onClick={() => setTab(id)}>{label}</button>)}</div>
        <div className="dataset-body setup">
          {tab === 'query' && <QueryEditor state={state} dispatch={dispatch} notify={notify} />}
          {tab === 'parameters' && <ParametersEditor state={state} dispatch={dispatch} />}
          {tab === 'filter' && <FilterEditor state={state} dispatch={dispatch} />}
          {tab === 'sort' && <SortEditor state={state} dispatch={dispatch} />}
        </div>
        <div className="row"><span className="muted">Changes apply straight away (Ctrl+Z undoes design changes). Save keeps them.</span><button className="btn primary" onClick={onClose}>OK</button></div>
      </div>
    </div>
  );
}
