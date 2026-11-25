// The answer of an analysis: a sentence, its measures, a chart and the details. In a dashboard tile: the short form.
import { cell, metricsOf, percent } from './mlModels.js';

const axis = (colors) => ({ axisLabel: { color: colors.muted, fontSize: 11, hideOverlap: true }, nameTextStyle: { color: colors.muted, fontSize: 11 },
  splitLine: { lineStyle: { color: colors.line, opacity: 0.6 } }, axisLine: { lineStyle: { color: colors.line } } });
const number = (v) => (typeof v === 'number' ? v.toLocaleString(undefined, { maximumSignificantDigits: 4 }) : v);
const range = (values) => {
  const low = Math.min(...values); const high = Math.max(...values);
  const gap = (high - low) * 0.06 || Math.abs(high) * 0.1 || 1;
  return { min: Number((low - gap).toPrecision(4)), max: Number((high + gap).toPrecision(4)) };
};

/** ECharts option of an ML result. `dark`, `colors`: the theme (Visual.jsx). */
export function mlOption(ml, colors) {
  const chart = ml.chart;
  const tip = { backgroundColor: colors.panel, borderColor: colors.line, textStyle: { color: colors.text, fontSize: 12 } };
  const base = { color: colors.series, grid: { left: 10, right: 22, top: 30, bottom: 26, containLabel: true } };
  const numbers = (name, values, place) => ({ type: 'value', name, nameLocation: 'middle', nameGap: place === 'x' ? 26 : 46, ...axis(colors), ...range(values), axisLabel: { ...axis(colors).axisLabel, formatter: number, showMinLabel: false, showMaxLabel: false } });
  const legend = { top: 0, textStyle: { color: colors.text, fontSize: 11 }, icon: 'roundRect', itemWidth: 10, itemHeight: 10, type: 'scroll' };

  if (chart.kind === 'fit' || chart.kind === 'actual') {
    const xs = chart.points.map((p) => p[0]); const ys = chart.points.map((p) => p[1]);
    const line = chart.kind === 'fit' ? chart.line : (() => { const all = [...xs, ...ys]; const a = Math.min(...all); const b = Math.max(...all); return [[a, a], [b, b]]; })();
    return { ...base, grid: { ...base.grid, left: 30 }, legend: { ...legend, data: ['Records', chart.kind === 'fit' ? 'Model' : 'Perfect prediction'] },
      tooltip: { ...tip, trigger: 'item', formatter: (p) => (p.seriesType === 'scatter' ? `${chart.x}: <b>${number(p.data[0])}</b><br>${chart.y}: <b>${number(p.data[1])}</b>` : '') },
      xAxis: numbers(chart.x, [...xs, ...line.map((p) => p[0])], 'x'), yAxis: numbers(chart.y, [...ys, ...line.map((p) => p[1])], 'y'),
      series: [{ name: 'Records', type: 'scatter', data: chart.points, symbolSize: 7, itemStyle: { opacity: 0.55 } },
        { name: chart.kind === 'fit' ? 'Model' : 'Perfect prediction', type: 'line', data: line, showSymbol: false, smooth: false, lineStyle: { width: 2, type: chart.kind === 'fit' ? 'solid' : 'dashed' }, itemStyle: { color: colors.series[1] } }] };
  }
  if (chart.kind === 'groups') {
    const many = chart.names.length > 1;
    return { ...base, grid: { ...base.grid, left: 30 }, legend: { ...legend, show: many },
      tooltip: { ...tip, trigger: 'item', formatter: (p) => `${p.seriesName}<br>${chart.x}: <b>${number(p.data[0])}</b><br>${chart.y}: <b>${number(p.data[1])}</b>` },
      xAxis: numbers(chart.x, chart.points.map((p) => p[0]), 'x'), yAxis: numbers(chart.y, chart.points.map((p) => p[1]), 'y'),
      series: chart.names.map((name, i) => ({ name, type: 'scatter', symbolSize: ml.task === 'anomaly' && i ? 11 : 7, data: chart.points.filter((p) => p[2] === i),
        itemStyle: { color: colors.series[(ml.task === 'anomaly' && i ? 7 : i) % colors.series.length], opacity: 0.65 } })) };
  }
  if (chart.kind === 'confusion' || chart.kind === 'correlation') {
    const correlation = chart.kind === 'correlation';
    const cells = chart.matrix.flatMap((row, y) => row.map((value, x) => [x, y, value]));
    const most = Math.max(1, ...cells.map((c) => c[2]));
    return { ...base, grid: { left: 10, right: 10, top: correlation ? 8 : 24, bottom: correlation ? 8 : 24, containLabel: true },
      tooltip: { ...tip, formatter: (p) => (correlation ? `${chart.labels[p.data[1]]} and ${chart.labels[p.data[0]]}: <b>${p.data[2].toFixed(2)}</b>` : `Really <b>${chart.labels[p.data[1]]}</b>, guessed <b>${chart.labels[p.data[0]]}</b>: ${p.data[2].toLocaleString()}`) },
      xAxis: { type: 'category', data: chart.labels, name: correlation ? '' : 'Guessed', nameLocation: 'middle', nameGap: 26, ...axis(colors), axisTick: { show: false } },
      yAxis: { type: 'category', data: chart.labels, inverse: true, name: correlation ? '' : 'Really', ...axis(colors), axisTick: { show: false } },
      visualMap: { show: false, min: correlation ? -1 : 0, max: correlation ? 1 : most, inRange: { color: correlation ? [colors.series[7], colors.panel, colors.series[0]] : [colors.panel, colors.series[0]] } },
      series: [{ type: 'heatmap', data: cells, itemStyle: { borderColor: colors.line, borderWidth: 1 },
        label: { show: chart.labels.length <= 12, color: colors.text, fontSize: 11, formatter: (p) => (correlation ? p.data[2].toFixed(2) : p.data[2].toLocaleString()) } }] };
  }
  if (chart.kind === 'forecast') {
    const labels = [...chart.labels, ...chart.nextLabels];
    const pad = chart.labels.length - 1;
    const gap = Array(pad).fill(null);
    const last = chart.values[pad];
    return { ...base, legend: { ...legend, data: ['Real', 'Forecast', 'Range'] },
      tooltip: { ...tip, trigger: 'axis', formatter: (items) => { const at = items[0].dataIndex; const n = at - chart.labels.length;
        return n < 0 ? `${labels[at]}<br>Real: <b>${number(chart.values[at])}</b>` : `${labels[at]}<br>Forecast: <b>${number(chart.next[n])}</b><br>between ${number(chart.low[n])} and ${number(chart.high[n])}`; } },
      xAxis: { type: 'category', data: labels, boundaryGap: false, ...axis(colors), splitLine: { show: false } },
      yAxis: { type: 'value', scale: true, ...axis(colors), axisLabel: { ...axis(colors).axisLabel, formatter: number } },
      series: [
        { name: 'Real', type: 'line', data: chart.values, showSymbol: chart.values.length < 40, lineStyle: { width: 2 }, itemStyle: { color: colors.series[0] } },
        { name: 'Low', type: 'line', data: [...gap, last, ...chart.low], stack: 'range', showSymbol: false, lineStyle: { opacity: 0 }, itemStyle: { color: 'transparent' }, silent: true, stackStrategy: 'all' },
        { name: 'Range', type: 'line', data: [...gap, 0, ...chart.high.map((h, i) => h - chart.low[i])], stack: 'range', showSymbol: false, lineStyle: { opacity: 0 }, areaStyle: { color: colors.series[1], opacity: 0.2 }, itemStyle: { color: colors.series[1] }, silent: true, stackStrategy: 'all' },
        { name: 'Forecast', type: 'line', data: [...gap, last, ...chart.next], showSymbol: false, lineStyle: { width: 2, type: 'dashed' }, itemStyle: { color: colors.series[1] } },
      ] };
  }
  return base;
}

export function MlBars({ title, items, format = percent }) {
  const top = Math.max(...items.map((i) => Math.abs(i.value)), 1e-9);
  return (
    <div className="ml-bars"><b>{title}</b>
      {items.slice(0, 12).map((item) => (
        <div key={item.name}><span title={item.name}>{item.name}</span><i><em style={{ width: `${(Math.abs(item.value) / top) * 100}%` }} /></i><small>{format(item.value)}</small></div>
      ))}
    </div>
  );
}

export function MlTable({ table, limit = 100 }) {
  return (
    <div className="viz-table-wrap"><table className="viz-table">
      <thead><tr>{table.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
      <tbody>{table.rows.slice(0, limit).map((row, i) => <tr key={i}>{row.map((value, n) => <td key={n} className={typeof value === 'number' ? 'num' : ''}>{table.columns[n] === 'Share' ? percent(value) : cell(value)}</td>)}</tr>)}</tbody>
    </table></div>
  );
}

/** chart: the drawn chart (from Visual.jsx). full: everything (the lab); otherwise a tile. */
export default function MlResult({ ml, chart, full, truncated }) {
  return (
    <div className={`ml-result ${full ? 'full' : ''}`}>
      <p className="ml-summary">{ml.summary}</p>
      <div className="ml-metrics">{metricsOf(ml).slice(0, full ? 6 : 2).map(([name, value, help]) => <span key={name} title={help}><small>{name}</small><b>{value}</b></span>)}</div>
      <div className="ml-chart">{chart}</div>
      {full && ml.importance?.length > 0 && <MlBars title="What matters most" items={ml.importance} />}
      {full && ml.coefficients?.length > 0 && (
        <details className="viz-section"><summary>Formula ({ml.coefficients.length} parts)</summary>
          <MlTable table={{ columns: ['Column', 'Effect of +1', 'Weight (standardised)'], rows: ml.coefficients.map((c) => [c.name, c.perUnit, c.weight]) }} />
        </details>
      )}
      {full && ml.table?.rows.length > 0 && <MlTable table={ml.table} />}
      {full && <p className="muted small">{ml.rows.toLocaleString()} records{truncated ? ' (a random sample)' : ''}. The model is trained on the current data every time it is shown.</p>}
    </div>
  );
}
