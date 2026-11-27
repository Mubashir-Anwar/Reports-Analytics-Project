// Calls of the /analytics page (the server checks the Analytics right, and Designer/Admin for datasets and the model).
import { request } from '../api.js';

const id = (value) => encodeURIComponent(value);

export const bi = {
  info: () => request('/api/analytics/info'),
  /** folders (shared with the Analytics Server): list, new, rename (what is inside goes with it), delete (moves it) */
  folders: () => request('/api/analytics/folders'),
  createFolder: (path) => request('/api/analytics/folders', { method: 'POST', body: { path } }),
  renameFolder: (path, newPath) => request('/api/analytics/folders/rename', { method: 'POST', body: { path, newPath } }),
  deleteFolder: (path, moveTo) => request('/api/analytics/folders', { method: 'DELETE', body: { path, moveTo } }),
  datasets: () => request('/api/analytics/datasets'),
  dataset: (datasetId) => request(`/api/analytics/datasets/${id(datasetId)}`),
  testSource: (source) => request('/api/analytics/datasets/test', { method: 'POST', body: source }),
  createDataset: (body) => request('/api/analytics/datasets', { method: 'POST', body }),
  updateDataset: (datasetId, body) => request(`/api/analytics/datasets/${id(datasetId)}`, { method: 'PUT', body }),
  deleteDataset: (datasetId, force = false) => request(`/api/analytics/datasets/${id(datasetId)}${force ? '?force=true' : ''}`, { method: 'DELETE' }),
  refresh: (datasetId) => request(`/api/analytics/datasets/${id(datasetId)}/refresh`, { method: 'POST' }),
  refreshes: (datasetId) => request(`/api/analytics/datasets/${id(datasetId)}/refreshes`),
  rows: (datasetId, limit = 50) => request(`/api/analytics/datasets/${id(datasetId)}/rows?limit=${limit}`),
  relationships: () => request('/api/analytics/relationships'),
  relate: (body) => request('/api/analytics/relationships', { method: 'POST', body }),
  unrelate: (relId) => request(`/api/analytics/relationships/${id(relId)}`, { method: 'DELETE' }),
  updateRelationship: (relId, body) => request(`/api/analytics/relationships/${id(relId)}`, { method: 'PUT', body }),
  checkRelationship: (body) => request('/api/analytics/relationships/check', { method: 'POST', body }),
  suggestRelationships: () => request('/api/analytics/relationships/suggest'),
  dashboards: () => request('/api/analytics/dashboards'),
  dashboard: (dashId) => request(`/api/analytics/dashboards/${id(dashId)}`),
  createDashboard: (body) => request('/api/analytics/dashboards', { method: 'POST', body }),
  saveDashboard: (dashId, body) => request(`/api/analytics/dashboards/${id(dashId)}`, { method: 'PUT', body }),
  deleteDashboard: (dashId) => request(`/api/analytics/dashboards/${id(dashId)}`, { method: 'DELETE' }),
  /** withData: also the imported records (Parquet), so it opens on a server without the database */
  dashboardFile: (dashId, withData = false) => request(`/api/analytics/dashboards/${id(dashId)}/file${withData ? '?data=true' : ''}`),
  importDashboard: (body) => request('/api/analytics/dashboards/import', { method: 'POST', body }),
  inspectPowerBi: (body) => request('/api/analytics/powerbi/inspect', { method: 'POST', body }),
  importPowerBi: (body) => request('/api/analytics/powerbi/import', { method: 'POST', body }),
  query: (visual) => request('/api/analytics/query', { method: 'POST', body: visual }),
  /** try a Python script on datasets: {script, inputs, datasetId?} → first rows, printed output or the error */
  runPython: (body) => request('/api/analytics/python/run', { method: 'POST', body }),
  /** try pipeline steps: {steps, until?, datasetId?} → each step's rows and time, the first rows of the last one */
  runPipeline: (body) => request('/api/analytics/pipelines/run', { method: 'POST', body }),
  queryBatch: (queries, filters) => request('/api/analytics/query-batch', { method: 'POST', body: { queries, filters } }),
};

export const AGG_LABELS = { sum: 'Sum', avg: 'Average', min: 'Minimum', max: 'Maximum', count: 'Count', distinct: 'Count (distinct)', countrows: 'Count of rows', median: 'Median' };
export const BUCKET_LABELS = { '': 'Exact value', year: 'Year', quarter: 'Quarter', month: 'Month', week: 'Week', day: 'Day' };
export const KIND_ICON = { number: '#', text: 'Aa', date: '📅', bool: '✓' };

/** The query part of a tile (what the server needs); slicers ask for their distinct values. */
export function tileQuery(tile) {
  if (tile.type === 'slicer') {
    return { tileId: tile.id, dataset: tile.dataset, groupBy: tile.groupBy.slice(0, 1), values: [{ agg: 'countrows', column: '' }],
      sort: [{ by: 'group:0', dir: 'asc' }], limit: 500, filters: tile.filters || [] };
  }
  return { tileId: tile.id, dataset: tile.dataset, groupBy: tile.groupBy || [], values: tile.values || [], filters: tile.filters || [],
    sort: tile.sort || [], limit: tile.limit || null, detail: Boolean(tile.detail), columns: tile.columns || [], ...(tile.type === 'ml' && tile.ml ? { ml: tile.ml } : {}) };
}

/** Can this tile be asked yet (has a dataset and the fields its type needs)? */
export function tileReady(tile) {
  if (!tile.dataset) return false;
  if (tile.type === 'ml') {
    if (!tile.ml?.model) return false;
    return tile.ml.model === 'forecast' ? tile.groupBy.length > 0 && tile.values.length > 0 : tile.ml.features.length > 0 && tile.columns.length > 0;
  }
  if (tile.type === 'map') return tile.groupBy.length >= (tile.options?.mapBy === 'points' ? 2 : 1) && tile.values.length > 0;
  if (tile.type === 'table') return tile.detail ? (tile.columns || []).length > 0 : (tile.groupBy.length + tile.values.length) > 0;
  if (tile.type === 'kpi') return tile.values.length > 0;
  if (tile.type === 'slicer') return tile.groupBy.length > 0;
  if (tile.type === 'scatter') return tile.groupBy.length > 0 && tile.values.length >= 2;
  return tile.groupBy.length > 0 && tile.values.length > 0;
}

export const newTileId = () => `t${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;

export function formatNumber(value, format = '') {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value !== 'number') return String(value);
  if (format === 'percent') return `${(value * 100).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
  if (format === 'integer') return Math.round(value).toLocaleString();
  if (format === 'compact' || (!format && Math.abs(value) >= 1e6)) {
    return new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(value);
  }
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

export const describeBytes = (bytes) => {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let size = bytes; let unit = 0;
  while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
  return `${size.toLocaleString(undefined, { maximumFractionDigits: 1 })} ${units[unit]}`;
};

/** Save a dashboard file (.mdash) that the server sent as JSON. */
export function saveDashboardFile(content, name) {
  const blob = new Blob([JSON.stringify(content, null, content.withData ? 0 : 2)], { type: 'application/json' });
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = `${(name || 'dashboard').replace(/[^\w\- ]+/g, '_').trim() || 'dashboard'}${content.withData ? ' (with data)' : ''}.mdash`;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(link.href), 60_000);
}

/**
 * Why a chosen file cannot be a dashboard file (.mdash), checked before anything is uploaded; '' when it may be one.
 * Power BI files (.pbix, .pbit, .pbip) are ZIP packages in Microsoft's own format: they cannot be opened here.
 */
export async function notADashboardFile(file) {
  const name = (file?.name || '').toLowerCase();
  const head = new Uint8Array(await file.slice(0, 4).arrayBuffer());
  const zip = head[0] === 0x50 && head[1] === 0x4b;                   // "PK": a ZIP package
  if (/\.pbi[tx]$/.test(name)) return 'Dashboard files (.pbix, .pbit) are imported in Analytics: Dashboards → Import.';
  if (/\.pbip$/.test(name)) return 'A .pbip project is a folder: export it as a template (.pbit) in the desktop tool that made it, and import that.';
  if (zip) return 'This is a ZIP package, not a dashboard file (.mdash). Choose a .mdash file downloaded from Analytics or the Report Server.';
  if (file.size > 2_000_000) return `This file is ${describeBytes(file.size)}: a dashboard file (.mdash) is at most 2 MB, because it holds no data.`;
  return '';
}

/** Analytics → Queries: DAX or SQL over the datasets, or MongoDB / SQL on a data adapter (live); run and saved. */
export const queries = {
  list: () => request('/api/analytics/queries'),
  get: (queryId) => request(`/api/analytics/queries/${id(queryId)}`),
  create: (body) => request('/api/analytics/queries', { method: 'POST', body }),
  save: (queryId, body) => request(`/api/analytics/queries/${id(queryId)}`, { method: 'PUT', body }),
  remove: (queryId) => request(`/api/analytics/queries/${id(queryId)}`, { method: 'DELETE' }),
  // { language: 'dax' | 'sql', text } or { language: 'source', queryLanguage, connectionId, collection, text, limit }
  run: (body) => request('/api/analytics/queries/run', { method: 'POST', body }),
  runSaved: (queryId) => request(`/api/analytics/queries/${id(queryId)}/run`, { method: 'POST', body: {} }),
};
