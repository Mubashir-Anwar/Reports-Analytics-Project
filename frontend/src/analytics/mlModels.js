// The analyses of the ML lab and of "ML" visuals (the server does the work: backend/app/analytics/ml.py).
export const ML_MODELS = [
  { id: 'linear', group: 'Predict a number', name: 'Linear regression', help: 'A straight-line formula. Easy to explain.', target: 'number', params: ['test'] },
  { id: 'ridge', group: 'Predict a number', name: 'Ridge regression', help: 'A steadier line when columns overlap.', target: 'number', params: ['alpha', 'test'] },
  { id: 'poly', group: 'Predict a number', name: 'Polynomial regression', help: 'Curves and combinations of columns.', target: 'number', params: ['degree', 'alpha', 'test'] },
  { id: 'logistic', group: 'Predict a category', name: 'Logistic regression', help: 'Chances of each category (yes / no, gold / silver).', target: 'category', params: ['alpha', 'test'] },
  { id: 'tree', group: 'Predict a number or category', name: 'Decision tree', help: 'Yes / no questions, one after another.', target: 'any', params: ['depth', 'test'] },
  { id: 'forest', group: 'Predict a number or category', name: 'Random forest', help: 'Many trees voting. Usually the most accurate.', target: 'any', params: ['trees', 'depth', 'test'] },
  { id: 'knn', group: 'Predict a number or category', name: 'Nearest neighbours (KNN)', help: 'Looks at the most similar records.', target: 'any', params: ['k', 'test'] },
  { id: 'forecast', group: 'Look ahead', name: 'Forecast', help: 'The next periods of a value over time, with a range.', params: ['horizon', 'season'] },
  { id: 'kmeans', group: 'Find patterns', name: 'Groups (K-means)', help: 'Splits the records into similar groups.', params: ['clusters'] },
  { id: 'anomaly', group: 'Find patterns', name: 'Unusual records', help: 'Records far away from the rest.', params: ['sensitivity'] },
  { id: 'correlation', group: 'Find patterns', name: 'Correlation', help: 'Which numbers rise and fall together.', params: [] },
  { id: 'pca', group: 'Find patterns', name: 'Main directions (PCA)', help: 'Many columns drawn in two directions.', params: [] },
];
export const modelOf = (id) => ML_MODELS.find((m) => m.id === id);

export const ML_PARAMS = {
  test: ['Kept aside for the test', 0.25, [[0.15, '15%'], [0.2, '20%'], [0.25, '25%'], [0.33, '33%'], [0.5, '50%']]],
  alpha: ['Smoothing', 1, 0, 10000], degree: ['Degree', 2, 2, 4], depth: ['Depth of a tree', 6, 1, 12], trees: ['Trees', 25, 5, 60],
  k: ['Neighbours', 7, 1, 50], clusters: ['Groups (0 = find the best)', 0, 0, 12], sensitivity: ['Unusual above (lower finds more)', 3, 1.5, 6],
  horizon: ['Periods ahead', 6, 1, 60], season: ['Season length (0 = find it)', 0, 0, 52],
};

/** Columns this model may predict. */
export function targetsOf(model, columns) {
  const shown = columns.filter((c) => !c.hidden);
  if (model?.target === 'number') return shown.filter((c) => c.kind === 'number');
  if (model?.target === 'category') return shown.filter((c) => c.kind === 'text' || c.kind === 'bool');
  return model?.target ? shown.filter((c) => c.kind !== 'date') : [];
}
const only = (id, columns) => columns.filter((c) => !c.hidden && (['correlation', 'anomaly'].includes(id) ? c.kind === 'number' || c.kind === 'date' : true));
export const featuresOf = (id, columns) => only(id, columns);

/** The query part of an ML visual when another model is chosen: keeps what still fits, else picks sensible columns. */
export function setupFor(id, tile, columns) {
  const model = modelOf(id);
  const params = Object.fromEntries(Object.entries(tile.ml?.params || {}).filter(([name]) => model.params.includes(name)));
  if (id === 'forecast') {
    const day = columns.find((c) => c.kind === 'date' && !c.hidden);
    const number = columns.find((c) => c.kind === 'number' && !c.hidden && c.role === 'measure') || columns.find((c) => c.kind === 'number' && !c.hidden);
    const groupBy = tile.ml?.model === 'forecast' && tile.groupBy?.length ? tile.groupBy : day ? [{ column: day.name, bucket: 'month' }] : [];
    const values = tile.ml?.model === 'forecast' && tile.values?.length ? tile.values : [number ? { column: number.name, agg: 'sum' } : { column: '', agg: 'countrows' }];
    return { detail: false, columns: [], groupBy, values, ml: { model: id, features: [], target: '', params: { ...params, bucket: groupBy[0]?.bucket || 'month' } } };
  }
  const targets = targetsOf(model, columns);
  const kept = tile.ml?.target && targets.some((c) => c.name === tile.ml.target) ? tile.ml.target : '';
  const few = (c) => c.kind !== 'number' || c.role === 'measure';
  const target = model.target ? kept || (targets.find(few) || targets[0])?.name || '' : '';
  const usable = featuresOf(id, columns).filter((c) => c.name !== target);
  let features = (tile.ml?.features || []).filter((name) => usable.some((c) => c.name === name));
  if (!features.length) features = usable.filter((c) => c.kind === 'number' && !/(^|_)id$/i.test(c.name)).slice(0, 6).map((c) => c.name);
  return withColumns({ ml: { model: id, target, features, params } });
}

/** Detail columns of the query = features + target. */
export function withColumns(part) {
  const names = [...part.ml.features.filter((name) => name !== part.ml.target), ...(part.ml.target ? [part.ml.target] : [])];
  return { ...part, ml: { ...part.ml, features: part.ml.features.filter((name) => name !== part.ml.target) }, detail: true, groupBy: [], values: [], columns: names.map((column) => ({ column })) };
}

export const percent = (value) => (value === null || value === undefined ? '—' : `${(value * 100).toFixed(1)}%`);
const short = (value) => (typeof value === 'number' ? value.toLocaleString(undefined, { maximumSignificantDigits: 5 }) : value ?? '—');

/** [label, value, what it means] of a result's measures. */
export function metricsOf(ml) {
  const m = ml.metrics || {};
  if (ml.task === 'regression') return [['R²', percent(m.r2), 'How much of the differences is explained (100% = all)'], ['Average error', short(m.mae), 'How far off a prediction is, on average'], ['RMSE', short(m.rmse), 'Like the average error, big misses count more'], ['Trained / tested on', `${ml.trainRows.toLocaleString()} / ${ml.testRows.toLocaleString()}`, 'Records']];
  if (ml.task === 'classification') return [['Accuracy', percent(m.accuracy), 'Right guesses on records the model had not seen'], ['Just guessing', percent(m.baseline), 'Always guessing the commonest value'], ['F1', percent(m.f1), 'Accuracy that counts rare categories equally'], ['Trained / tested on', `${ml.trainRows.toLocaleString()} / ${ml.testRows.toLocaleString()}`, 'Records']];
  if (ml.task === 'forecast') return [['Average error', percent(m.mape), 'One period ahead'], ['RMSE', short(m.rmse), 'One period ahead'], ['Season', m.season ? `${m.season} periods` : 'None', 'Repeating pattern that was found']];
  if (ml.task === 'clustering') return [['Groups', m.groups, ''], ['Separation', short(m.silhouette), 'Silhouette: 1 = clearly apart, 0 = overlapping']];
  if (ml.task === 'anomaly') return [['Unusual', short(m.found), ''], ['Share', percent(m.share), '']];
  if (ml.task === 'correlation') return [['Strongest', short(m.strongest), '1 = together, -1 = opposite, 0 = nothing']];
  if (ml.task === 'pca') return [['Kept in two directions', percent(m.explained), '']];
  return [];
}
export const score = (ml) => (ml.task === 'classification' ? ml.metrics.accuracy : ml.metrics.r2);
export const cell = short;
