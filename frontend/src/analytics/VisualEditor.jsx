// The editing pane of one visual: its type, dataset, the field wells (Axis, Legend, Values, …), filters and format.
// Fields can be dragged from the field list into a well, or chosen in the well's own list.
import { useMemo, useState } from 'react';
import { AGG_LABELS, BUCKET_LABELS, KIND_ICON } from './api.js';
import MlSetup from './MlSetup.jsx';

export const VISUAL_TYPES = [
  ['kpi', 'Card', 'M4 6h16v12H4zM8 13h8M8 10h3'],
  ['column', 'Column', 'M5 20V11M10 20V5M15 20v-6M20 20V9'],
  ['bar', 'Bar', 'M4 5h9M4 10h15M4 15h6M4 20h12'],
  ['line', 'Line', 'M3 17l5-6 4 3 4-7 5 5'],
  ['area', 'Area', 'M3 18l5-7 4 3 4-6 5 4v6H3z'],
  ['pie', 'Pie', 'M12 3a9 9 0 1 0 9 9h-9z'],
  ['donut', 'Donut', 'M12 3a9 9 0 1 0 9 9M12 8a4 4 0 1 0 4 4'],
  ['scatter', 'Scatter', 'M6 16h.01M10 10h.01M15 14h.01M18 6h.01M8 6h.01M4 20h16'],
  ['table', 'Table', 'M3 5h18v14H3zM3 10h18M3 15h18M9 5v14'],
  ['map', 'Map', 'M9 4L3 6v14l6-2 6 2 6-2V4l-6 2zM9 4v14M15 6v14'],
  ['ml', 'ML', 'M5 7a2 2 0 1 0 .01 0M5 17a2 2 0 1 0 .01 0M12 12a2 2 0 1 0 .01 0M19 8a2 2 0 1 0 .01 0M6.6 8.4l4 3M6.6 15.6l4-2.8M13.8 11.2l3.6-2'],
  ['slicer', 'Slicer', 'M4 6h16M7 12h10M10 18h4'],
];

const CHART = [{ key: 'groupBy', at: 0, max: 1, label: 'Axis', hint: 'categories along the axis' },
  { key: 'groupBy', at: 1, max: 1, label: 'Legend', hint: 'optional: one series per value' },
  { key: 'values', at: 0, max: 6, label: 'Values', hint: 'numbers to summarise' }];
const WELLS = {
  kpi: [{ key: 'values', at: 0, max: 2, label: 'Value', hint: 'the number (a second one is shown small)' }],
  column: CHART, bar: CHART, line: CHART, area: CHART,
  pie: [{ key: 'groupBy', at: 0, max: 1, label: 'Category' }, { key: 'values', at: 0, max: 1, label: 'Value' }],
  donut: [{ key: 'groupBy', at: 0, max: 1, label: 'Category' }, { key: 'values', at: 0, max: 1, label: 'Value' }],
  scatter: [{ key: 'groupBy', at: 0, max: 1, label: 'Details', hint: 'one dot per value' },
    { key: 'values', at: 0, max: 1, label: 'X axis' }, { key: 'values', at: 1, max: 1, label: 'Y axis' }],
  table: [{ key: 'groupBy', at: 0, max: 3, label: 'Rows' }, { key: 'values', at: 0, max: 8, label: 'Values' }],
  tableDetail: [{ key: 'columns', at: 0, max: 40, label: 'Columns', hint: 'records as they are' }],
  map: [{ key: 'groupBy', at: 0, max: 1, label: 'Place', hint: 'country (name or code) or a big city' }, { key: 'values', at: 0, max: 1, label: 'Value' }],
  mapPoints: [{ key: 'groupBy', at: 0, max: 3, label: 'Latitude, longitude, name', hint: 'in this order; the name is optional' }, { key: 'values', at: 0, max: 1, label: 'Value', hint: 'size of the bubble' }],
  ml: [],
  slicer: [{ key: 'groupBy', at: 0, max: 1, label: 'Field', hint: 'people pick values to filter the dashboard' }],
};
const OPERATORS = [['in', 'is one of'], ['not_in', 'is not one of'], ['eq', '='], ['ne', '≠'], ['gt', '>'], ['gte', '≥'], ['lt', '<'], ['lte', '≤'],
  ['between', 'between'], ['contains', 'contains'], ['starts', 'starts with'], ['is_null', 'is blank'], ['not_null', 'is not blank']];

export function TypeIcon({ path, size = 18 }) {
  return <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={path} /></svg>;
}

/** Datasets a visual on `baseId` can use: the base and every dataset reachable through relationships. */
export function reachable(baseId, relationships) {
  const seen = new Set([baseId]); const queue = [baseId];
  while (queue.length) {
    const node = queue.shift();
    for (const rel of relationships) {
      for (const [a, b] of [[rel.fromDataset, rel.toDataset], [rel.toDataset, rel.fromDataset]]) {
        if (a === node && !seen.has(b)) { seen.add(b); queue.push(b); }
      }
    }
  }
  return seen;
}

const fieldKey = (f) => `${f.dataset || ''}::${f.column}`;

export default function VisualEditor({ tile, datasets, relationships, onChange, onRemove, onClose }) {
  const [search, setSearch] = useState('');
  const base = datasets.find((d) => d.id === tile.dataset);
  const usable = useMemo(() => {
    if (!base) return [];
    const ids = reachable(base.id, relationships);
    return datasets.filter((d) => ids.has(d.id)).sort((a, b) => (a.id === base.id ? -1 : b.id === base.id ? 1 : a.name.localeCompare(b.name)));
  }, [base, datasets, relationships]);
  const columnOf = (f) => {
    const owner = datasets.find((d) => d.id === (f.dataset || tile.dataset));
    return owner?.columns.find((c) => c.name === f.column);
  };
  const ref = (datasetId, column) => (datasetId === tile.dataset ? { column } : { dataset: datasetId, column });
  const set = (change) => onChange({ ...tile, ...change });
  const wells = tile.type === 'table' && tile.detail ? WELLS.tableDetail : tile.type === 'map' && tile.options?.mapBy === 'points' ? WELLS.mapPoints : WELLS[tile.type];

  const itemsOf = (well) => {
    const list = tile[well.key] || [];
    const before = wells.filter((w) => w.key === well.key && w.at < well.at).reduce((sum, w) => sum + w.max, 0);
    return { list, start: well.at === 0 ? 0 : before, items: list.slice(well.at === 0 ? 0 : before, (well.at === 0 ? 0 : before) + well.max) };
  };

  const addTo = (well, field) => {
    const column = columnOf(field);
    if (!column) return;
    const { list, start, items } = itemsOf(well);
    if (items.length >= well.max) {                       // a full one-field well: replace its field
      if (well.max !== 1) return;
      const next = [...list];
      next[start] = well.key === 'values' ? { ...field, agg: column.kind === 'number' ? column.agg || 'sum' : 'count' } : { ...field, ...(column.kind === 'date' ? { bucket: 'month' } : {}) };
      set({ [well.key]: next });
      return;
    }
    let item = field;
    if (well.key === 'values') item = { ...field, agg: column.kind === 'number' ? (column.agg && column.agg !== 'count' ? column.agg : 'sum') : 'count' };
    if (well.key === 'groupBy' && column.kind === 'date') item = { ...field, bucket: 'month' };
    const next = [...list];
    next.splice(start + items.length, 0, item);
    if (well.key === 'groupBy' && start > list.length) return;   // a Legend before an Axis: fill the Axis first
    set({ [well.key]: next });
  };
  const removeFrom = (key, index) => set({ [key]: tile[key].filter((_, i) => i !== index) });
  const patchItem = (key, index, change) => set({ [key]: tile[key].map((item, i) => (i === index ? { ...item, ...change } : item)) });

  const dragField = (event, datasetId, column) => { event.dataTransfer.setData('application/x-bi-field', JSON.stringify(ref(datasetId, column))); event.dataTransfer.effectAllowed = 'copy'; };
  const dropOn = (well) => (event) => {
    event.preventDefault();
    event.currentTarget.classList.remove('over');
    try { addTo(well, JSON.parse(event.dataTransfer.getData('application/x-bi-field'))); } catch { /* not a field */ }
  };

  const fieldOptions = (pred = () => true) => usable.map((d) => (
    <optgroup key={d.id} label={d.name}>
      {d.columns.filter((c) => !c.hidden && pred(c)).map((c) => <option key={c.name} value={`${d.id}::${c.name}`}>{c.label || c.name}</option>)}
    </optgroup>
  ));
  const parse = (value) => { const [datasetId, ...rest] = value.split('::'); return ref(datasetId, rest.join('::')); };

  return (
    <aside className="viz-editor" aria-label="Visual settings">
      <div className="viz-editor-head">
        <strong>Visual</strong>
        <span className="spacer" />
        <button type="button" className="btn small" onClick={onRemove} title="Remove this visual">Remove</button>
        <button type="button" className="btn small" onClick={onClose} aria-label="Close the visual settings">Done</button>
      </div>

      <div className="viz-types" role="radiogroup" aria-label="Visual type">
        {VISUAL_TYPES.map(([id, name, path]) => (
          <button key={id} type="button" role="radio" aria-checked={tile.type === id} className={tile.type === id ? 'on' : ''} title={name}
            onClick={() => set({ type: id, ...(id !== 'table' ? { detail: false } : {}), ...((id === 'ml') !== (tile.type === 'ml') ? { groupBy: [], values: [], columns: [], sort: [], ml: undefined } : {}) })}>
            <TypeIcon path={path} /><span>{name}</span>
          </button>
        ))}
      </div>

      <label className="field"><span>Title</span><input value={tile.title} maxLength={120} onChange={(e) => set({ title: e.target.value })} placeholder="Shown on the tile" /></label>
      <label className="field"><span>Dataset</span>
        <select value={tile.dataset} onChange={(e) => set({ dataset: e.target.value, groupBy: [], values: [], filters: [], columns: [], sort: [], ml: undefined })}>
          <option value="">Choose…</option>
          {datasets.map((d) => <option key={d.id} value={d.id} disabled={!d.columns.length}>{d.name}{d.columns.length ? '' : ' (not imported yet)'}</option>)}
        </select>
      </label>

      {base && (
        <>
          {tile.type === 'table' && (
            <label className="check"><input type="checkbox" checked={!tile.detail} onChange={(e) => set({ detail: !e.target.checked, columns: [], groupBy: [], values: [] })} /> Summarise (group and total)</label>
          )}
          {tile.type === 'ml' && <MlSetup tile={tile} columns={base.columns} onChange={onChange} />}
          {tile.type === 'map' && (
            <label className="field"><span>Places are</span>
              <select value={tile.options?.mapBy || 'names'} onChange={(e) => set({ options: { ...tile.options, mapBy: e.target.value }, groupBy: [] })}>
                <option value="names">Country or city names</option><option value="points">Latitude and longitude columns</option>
              </select>
            </label>
          )}
          {wells.map((well, w) => {
            const { start, items } = itemsOf(well);
            return (
              <div key={w} className="well" onDragOver={(e) => { e.preventDefault(); e.currentTarget.classList.add('over'); }}
                onDragLeave={(e) => e.currentTarget.classList.remove('over')} onDrop={dropOn(well)}>
                <div className="well-head"><b>{well.label}</b>{well.hint && <small className="muted">{well.hint}</small>}</div>
                {items.map((item, i) => {
                  const index = start + i; const column = columnOf(item);
                  return (
                    <div key={`${fieldKey(item)}-${i}`} className="well-item">
                      <span className="kind">{item.agg === 'countrows' ? '#' : KIND_ICON[column?.kind] || '?'}</span>
                      <span className="name" title={item.dataset ? datasets.find((d) => d.id === item.dataset)?.name : base.name}>{item.agg === 'countrows' ? 'Count of rows' : column?.label || item.column}</span>
                      {well.key === 'values' && item.agg !== 'countrows' && (
                        <select aria-label="Summarise as" value={item.agg} onChange={(e) => patchItem('values', index, { agg: e.target.value })}>
                          {Object.entries(AGG_LABELS).filter(([agg]) => agg !== 'countrows' && (column?.kind === 'number' || !['sum', 'avg', 'median'].includes(agg)))
                            .map(([agg, name]) => <option key={agg} value={agg}>{name}</option>)}
                        </select>
                      )}
                      {well.key === 'groupBy' && column?.kind === 'date' && (
                        <select aria-label="Date level" value={item.bucket || ''} onChange={(e) => patchItem('groupBy', index, { bucket: e.target.value || undefined })}>
                          {Object.entries(BUCKET_LABELS).map(([b, name]) => <option key={b} value={b}>{name}</option>)}
                        </select>
                      )}
                      <button type="button" className="x" aria-label={`Remove ${item.column}`} onClick={() => removeFrom(well.key, index)}>×</button>
                    </div>
                  );
                })}
                {(items.length < well.max || well.max === 1) && (
                  <select className="well-add" value="" aria-label={`Add a field to ${well.label}`}
                    onChange={(e) => { if (e.target.value) addTo(well, parse(e.target.value)); }}>
                    <option value="">{items.length && well.max === 1 ? 'Replace…' : '+ Add field (or drag one here)'}</option>
                    {fieldOptions(well.key === 'values' && tile.type === 'scatter' ? (c) => c.kind === 'number' : undefined)}
                  </select>
                )}
                {well.key === 'values' && tile.type !== 'scatter' && items.length < well.max && (
                  <button type="button" className="link-btn" onClick={() => set({ values: [...tile.values, { agg: 'countrows', column: '' }] })}>+ Count of rows</button>
                )}
              </div>
            );
          })}

          <details className="viz-section">
            <summary>Filters on this visual ({tile.filters.length})</summary>
            {tile.filters.map((f, i) => (
              <div key={i} className="filter-row">
                <select aria-label="Field" value={`${f.dataset || tile.dataset}::${f.column}`} onChange={(e) => patchItem('filters', i, parse(e.target.value))}>{fieldOptions()}</select>
                <select aria-label="Condition" value={f.op} onChange={(e) => patchItem('filters', i, { op: e.target.value })}>
                  {OPERATORS.map(([op, name]) => <option key={op} value={op}>{name}</option>)}
                </select>
                {!['is_null', 'not_null'].includes(f.op) && (
                  <input aria-label="Values" placeholder={f.op === 'between' ? 'from, to' : 'value, value…'} value={(f.values || []).join(', ')}
                    onChange={(e) => patchItem('filters', i, { values: e.target.value.split(',').map((v) => v.trim()).filter((v) => v !== '') })} />
                )}
                <button type="button" className="x" aria-label="Remove filter" onClick={() => removeFrom('filters', i)}>×</button>
              </div>
            ))}
            <button type="button" className="link-btn" onClick={() => {
              const first = base.columns.find((c) => !c.hidden);
              if (first) set({ filters: [...tile.filters, { column: first.name, op: 'in', values: [] }] });
            }}>+ Add a filter</button>
          </details>

          <details className="viz-section">
            <summary>Format and order</summary>
            <label className="field"><span>Numbers</span>
              <select value={tile.options?.format || ''} onChange={(e) => set({ options: { ...tile.options, format: e.target.value } })}>
                <option value="">Automatic</option><option value="integer">Whole numbers</option><option value="compact">Short (1.2M)</option><option value="percent">Percent (0.25 → 25%)</option>
              </select>
            </label>
            {['column', 'bar', 'line', 'area', 'table'].includes(tile.type) && (
              <label className="field"><span>Order by</span>
                <select value={tile.sort[0] ? `${tile.sort[0].by}|${tile.sort[0].dir}` : ''} onChange={(e) => {
                  const [by, dir] = e.target.value.split('|'); set({ sort: by ? [{ by, dir }] : [] });
                }}>
                  <option value="">Automatic</option>
                  <option value="value:0|desc">Biggest value first</option><option value="value:0|asc">Smallest value first</option>
                  <option value="group:0|asc">Axis A → Z / oldest first</option><option value="group:0|desc">Axis Z → A / newest first</option>
                </select>
              </label>
            )}
            {!['kpi', 'slicer', 'ml'].includes(tile.type) && (
              <label className="field"><span>Show at most (rows / categories)</span>
                <input type="number" min="1" max="5000" value={tile.limit || ''} placeholder="All (up to the server limit)"
                  onChange={(e) => set({ limit: e.target.value ? Number(e.target.value) : null })} />
              </label>
            )}
            {['column', 'bar', 'line', 'area'].includes(tile.type) && (
              <>
                <label className="check"><input type="checkbox" checked={Boolean(tile.options?.stacked)} onChange={(e) => set({ options: { ...tile.options, stacked: e.target.checked } })} /> Stack the series</label>
                <label className="check"><input type="checkbox" checked={Boolean(tile.options?.labels)} onChange={(e) => set({ options: { ...tile.options, labels: e.target.checked } })} /> Show value labels</label>
              </>
            )}
          </details>

          {tile.type !== 'ml' && <div className="field-list">
            <div className="well-head"><b>Fields</b><small className="muted">drag into a well above</small></div>
            <input type="search" placeholder="Find a field…" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Find a field" />
            {usable.map((d) => (
              <div key={d.id} className="field-group">
                <div className="field-group-name">{d.name}{d.id !== base.id && <small className="muted"> · related</small>}</div>
                {d.columns.filter((c) => !c.hidden && (c.label || c.name).toLowerCase().includes(search.toLowerCase())).map((c) => (
                  <div key={c.name} className="field-chip" draggable onDragStart={(e) => dragField(e, d.id, c.name)} title={`${c.name} · ${c.type}`}>
                    <span className="kind">{KIND_ICON[c.kind]}</span>{c.label || c.name}
                  </div>
                ))}
              </div>
            ))}
          </div>}
        </>
      )}
    </aside>
  );
}
