// Choosing an analysis: model, the column to predict, the columns to use and its settings (ML lab and ML visuals).
import { AGG_LABELS, BUCKET_LABELS, KIND_ICON } from './api.js';
import { ML_MODELS, ML_PARAMS, featuresOf, modelOf, setupFor, targetsOf, withColumns } from './mlModels.js';

export default function MlSetup({ tile, columns, onChange }) {
  const id = tile.ml?.model || '';
  const model = modelOf(id);
  const ml = tile.ml || { features: [], params: {} };
  const set = (part) => onChange({ ...tile, ...part });
  const setMl = (change) => set(withColumns({ ml: { ...ml, ...change } }));
  const setParam = (name, value) => set({ ml: { ...ml, params: { ...ml.params, [name]: value } } });
  const groups = [...new Set(ML_MODELS.map((m) => m.group))];
  const targets = targetsOf(model, columns);
  const features = model ? featuresOf(id, columns).filter((c) => c.name !== ml.target) : [];
  const toggle = (name) => setMl({ features: ml.features.includes(name) ? ml.features.filter((f) => f !== name) : [...ml.features, name].slice(0, 20) });

  return (
    <div className="ml-setup">
      <label className="field"><span>Analysis</span>
        <select value={id} onChange={(e) => set(setupFor(e.target.value, tile, columns))}>
          <option value="" disabled>Choose…</option>
          {groups.map((group) => <optgroup key={group} label={group}>{ML_MODELS.filter((m) => m.group === group).map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}</optgroup>)}
        </select>
        {model && <small className="hint">{model.help}</small>}
      </label>

      {id === 'forecast' && (
        <>
          <label className="field"><span>Date</span>
            <select value={tile.groupBy[0]?.column || ''} onChange={(e) => set({ groupBy: [{ column: e.target.value, bucket: tile.groupBy[0]?.bucket || 'month' }] })}>
              <option value="" disabled>{columns.some((c) => c.kind === 'date') ? 'Choose…' : 'This dataset has no date column'}</option>
              {columns.filter((c) => c.kind === 'date' && !c.hidden).map((c) => <option key={c.name} value={c.name}>{c.label || c.name}</option>)}
            </select>
          </label>
          <label className="field"><span>Step</span>
            <select value={tile.groupBy[0]?.bucket || 'month'} disabled={!tile.groupBy[0]} onChange={(e) => set({ groupBy: [{ ...tile.groupBy[0], bucket: e.target.value }], ml: { ...ml, params: { ...ml.params, bucket: e.target.value } } })}>
              {Object.entries(BUCKET_LABELS).filter(([b]) => b).map(([b, name]) => <option key={b} value={b}>{name}</option>)}
            </select>
          </label>
          <label className="field"><span>Value</span>
            <span className="row">
              <select value={tile.values[0]?.agg || 'sum'} aria-label="Summarise as" onChange={(e) => set({ values: [e.target.value === 'countrows' ? { agg: 'countrows', column: '' } : { agg: e.target.value, column: tile.values[0]?.column || columns.find((c) => c.kind === 'number')?.name || '' }] })}>
                {Object.entries(AGG_LABELS).map(([agg, name]) => <option key={agg} value={agg}>{name}</option>)}
              </select>
              {tile.values[0]?.agg !== 'countrows' && (
                <select value={tile.values[0]?.column || ''} aria-label="Value column" onChange={(e) => set({ values: [{ ...tile.values[0], column: e.target.value }] })}>
                  {columns.filter((c) => c.kind === 'number' && !c.hidden).map((c) => <option key={c.name} value={c.name}>{c.label || c.name}</option>)}
                </select>
              )}
            </span>
          </label>
        </>
      )}

      {model?.target && (
        <label className="field"><span>Predict</span>
          <select value={ml.target} onChange={(e) => setMl({ target: e.target.value })}>
            <option value="" disabled>{targets.length ? 'Choose…' : 'No suitable column in this dataset'}</option>
            {targets.map((c) => <option key={c.name} value={c.name}>{c.label || c.name}</option>)}
          </select>
        </label>
      )}

      {model && id !== 'forecast' && (
        <fieldset className="ml-features">
          <legend>{model.target ? 'From these columns' : 'Columns'} <small className="muted">{ml.features.length} chosen</small></legend>
          <div>
            {features.map((c) => (
              <label key={c.name} className={`right-chip ${ml.features.includes(c.name) ? 'on' : ''}`}>
                <input type="checkbox" checked={ml.features.includes(c.name)} onChange={() => toggle(c.name)} />
                <span className="kind">{KIND_ICON[c.kind]}</span>{c.label || c.name}
              </label>
            ))}
            {!features.length && <small className="muted">No suitable column in this dataset.</small>}
          </div>
        </fieldset>
      )}

      {model?.params.length > 0 && (
        <details className="viz-section"><summary>Settings</summary>
          {model.params.map((name) => {
            const [label, normal, low, high] = ML_PARAMS[name];
            const value = ml.params?.[name] ?? normal;
            return (
              <label key={name} className="field"><span>{label}</span>
                {Array.isArray(low)
                  ? <select value={value} onChange={(e) => setParam(name, Number(e.target.value))}>{low.map(([v, text]) => <option key={v} value={v}>{text}</option>)}</select>
                  : <input type="number" value={value} min={low} max={high} step={name === 'alpha' || name === 'sensitivity' ? 0.5 : 1} onChange={(e) => setParam(name, e.target.value === '' ? normal : Number(e.target.value))} />}
              </label>
            );
          })}
        </details>
      )}
    </div>
  );
}
