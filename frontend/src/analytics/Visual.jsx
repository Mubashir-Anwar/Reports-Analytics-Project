// One visual: a chart (ECharts), a KPI card, a table or a slicer, drawn from a query result {columns, rows}.
// Colours: the validated categorical palette (separate light and dark steps), assigned in fixed order by entity.
// Text uses the theme's text colours, never a series colour; one value axis only; a legend from 2 series on.
import { useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from 'echarts/core';
import { BarChart, LineChart, PieChart, ScatterChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { useLook } from '../shared/ThemeMenu.jsx';
import { formatNumber } from './api.js';

echarts.use([BarChart, LineChart, PieChart, ScatterChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

const SERIES = {
  light: ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948'],
  dark: ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#0ca30c', '#9085e9', '#e66767'],
};
const MAX_SLICES = 8;          // more categories fold into "Other" (never a generated 9th colour)
const BLANK = '(blank)';

const cssVar = (name, fallback) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
const label = (value) => (value === null || value === undefined || value === '' ? BLANK : String(value));

function themeColors(dark) {
  return {
    series: dark ? SERIES.dark : SERIES.light,
    text: cssVar('--text', dark ? '#e5e7eb' : '#0f172a'),
    muted: cssVar('--muted', dark ? '#94a3b8' : '#64748b'),
    line: cssVar('--line', dark ? '#334155' : '#e2e8f0'),
    panel: cssVar('--panel', dark ? '#0f172a' : '#ffffff'),
  };
}

/** Rows → categories and series. With a second group field (legend) the values are pivoted into one series per legend value. */
function shape(result) {
  const groups = result.columns.filter((c) => c.role === 'group');
  const values = result.columns.filter((c) => c.role === 'value');
  const g = groups.length;
  if (g >= 2) {
    const categories = []; const raw = []; const names = []; const cells = new Map();
    for (const row of result.rows) {
      const cat = label(row[0]); const name = label(row[1]);
      if (!categories.includes(cat)) { categories.push(cat); raw.push(row[0]); }
      if (!names.includes(name)) names.push(name);
      cells.set(`${cat}\u0000${name}`, row[g]);
    }
    return { categories, raw, series: names.map((name) => ({ name, data: categories.map((cat) => cells.get(`${cat}\u0000${name}`) ?? null) })), valueColumn: values[0] };
  }
  return {
    categories: result.rows.map((row) => label(row[0])),
    raw: result.rows.map((row) => row[0]),
    series: values.map((column, index) => ({ name: column.label, data: result.rows.map((row) => row[g + index]) })),
    valueColumn: values[0],
  };
}

function chartOption(tile, result, colors) {
  const format = tile.options?.format || '';
  const fmt = (v) => formatNumber(v, format);
  const axisText = { color: colors.muted, fontSize: 11 };
  const tooltip = { trigger: 'axis', backgroundColor: colors.panel, borderColor: colors.line, textStyle: { color: colors.text, fontSize: 12 },
    valueFormatter: fmt, axisPointer: { type: tile.type === 'line' || tile.type === 'area' ? 'line' : 'shadow', lineStyle: { color: colors.muted } } };
  const legend = { show: false, top: 0, type: 'scroll', textStyle: { color: colors.text, fontSize: 11 }, icon: 'roundRect', itemWidth: 10, itemHeight: 10 };

  if (tile.type === 'pie' || tile.type === 'donut') {
    const g = result.columns.filter((c) => c.role === 'group').length;
    let items = result.rows.map((row) => ({ name: label(row[0]), raw: row[0], value: row[g] }));
    items.sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
    if (items.length > MAX_SLICES) {
      const rest = items.slice(MAX_SLICES - 1);
      items = [...items.slice(0, MAX_SLICES - 1), { name: 'Other', raw: undefined, value: rest.reduce((s, i) => s + (i.value || 0), 0), other: true }];
    }
    return {
      color: colors.series,
      tooltip: { trigger: 'item', backgroundColor: colors.panel, borderColor: colors.line, textStyle: { color: colors.text, fontSize: 12 },
        formatter: (p) => `${p.marker}${p.name}: <b>${fmt(p.value)}</b> (${p.percent}%)` },
      legend: { ...legend, show: true, bottom: 0, top: 'auto' },
      series: [{ type: 'pie', radius: tile.type === 'donut' ? ['46%', '70%'] : [0, '70%'], center: ['50%', '45%'],
        itemStyle: { borderColor: colors.panel, borderWidth: 2, borderRadius: 4 },
        label: { color: colors.text, fontSize: 11, formatter: '{b}' }, labelLine: { lineStyle: { color: colors.muted } },
        data: items }],
    };
  }

  if (tile.type === 'scatter') {
    const g = result.columns.filter((c) => c.role === 'group').length;
    const values = result.columns.filter((c) => c.role === 'value');
    return {
      color: colors.series,
      grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
      tooltip: { trigger: 'item', backgroundColor: colors.panel, borderColor: colors.line, textStyle: { color: colors.text, fontSize: 12 },
        formatter: (p) => `<b>${label(p.data[2])}</b><br>${values[0]?.label}: ${fmt(p.data[0])}<br>${values[1]?.label}: ${fmt(p.data[1])}` },
      xAxis: { type: 'value', name: values[0]?.label, nameLocation: 'middle', nameGap: 26, nameTextStyle: axisText, axisLabel: { ...axisText, formatter: fmt },
        splitLine: { lineStyle: { color: colors.line, opacity: 0.6 } } },
      yAxis: { type: 'value', name: values[1]?.label, nameTextStyle: axisText, axisLabel: { ...axisText, formatter: fmt }, splitLine: { lineStyle: { color: colors.line, opacity: 0.6 } } },
      series: [{ type: 'scatter', symbolSize: 10, itemStyle: { borderColor: colors.panel, borderWidth: 2 },
        data: result.rows.map((row) => [row[g], row[g + 1], row[0]]) }],
    };
  }

  const { categories, series } = shape(result);
  const horizontal = tile.type === 'bar';
  const stacked = Boolean(tile.options?.stacked) && series.length > 1;
  const kind = tile.type === 'line' || tile.type === 'area' ? 'line' : 'bar';
  const categoryAxis = { type: 'category', data: categories, axisLabel: { ...axisText, hideOverlap: true }, axisLine: { lineStyle: { color: colors.line } }, axisTick: { show: false } };
  const valueAxis = { type: 'value', axisLabel: { ...axisText, formatter: fmt }, splitLine: { lineStyle: { color: colors.line, opacity: 0.6 } } };
  return {
    color: colors.series,
    grid: { left: 8, right: 16, top: series.length > 1 ? 30 : 12, bottom: 8, containLabel: true },
    tooltip,
    legend: { ...legend, show: series.length > 1 },
    xAxis: horizontal ? valueAxis : categoryAxis,
    yAxis: horizontal ? { ...categoryAxis, inverse: true } : valueAxis,
    series: series.map((item) => ({
      name: item.name, type: kind, data: item.data, stack: stacked ? 'all' : undefined,
      barMaxWidth: 36, barGap: '12%',
      itemStyle: kind === 'bar' ? { borderRadius: stacked ? 0 : (horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]), borderColor: colors.panel, borderWidth: stacked ? 1 : 0 } : undefined,
      lineStyle: kind === 'line' ? { width: 2 } : undefined,
      symbol: 'circle', symbolSize: 8, showSymbol: categories.length <= 24,
      areaStyle: tile.type === 'area' ? { opacity: stacked ? 0.55 : 0.18 } : undefined,
      smooth: false,
      label: tile.options?.labels ? { show: true, position: horizontal ? 'right' : 'top', color: colors.text, fontSize: 10, formatter: (p) => fmt(p.value) } : undefined,
    })),
  };
}

function Chart({ tile, result, selected, onPick }) {
  const box = useRef(null);
  const chart = useRef(null);
  const [look] = useLook();
  const dark = look.mode === 'dark' || (look.mode === 'system' && window.ReportingLook.isDark());
  // eslint-disable-next-line react-hooks/exhaustive-deps -- look: the theme's CSS colours changed
  const option = useMemo(() => chartOption(tile, result, themeColors(dark)), [tile, result, dark, look]);

  useEffect(() => {
    chart.current = echarts.init(box.current, null, { renderer: 'canvas' });
    const observer = new ResizeObserver(() => chart.current?.resize());
    observer.observe(box.current);
    return () => { observer.disconnect(); chart.current?.dispose(); chart.current = null; };
  }, []);
  useEffect(() => { chart.current?.setOption(option, true); }, [option]);
  useEffect(() => {
    const instance = chart.current;
    if (!instance) return undefined;
    const handler = (params) => {
      if (!onPick || tile.type === 'scatter') return;
      if (tile.type === 'pie' || tile.type === 'donut') { if (!params.data?.other) onPick(params.data?.raw ?? null); return; }
      const { raw } = shape(result);
      onPick(raw[params.dataIndex] ?? null);
    };
    instance.on('click', handler);
    return () => instance.off('click', handler);
  }, [onPick, result, tile.type]);
  // the picked category stays strong, the others fade (cross-filter highlight)
  useEffect(() => {
    const instance = chart.current;
    if (!instance) return;
    instance.dispatchAction({ type: 'downplay' });
    if (selected !== undefined) {
      const { raw } = shape(result);
      const index = tile.type === 'pie' || tile.type === 'donut'
        ? (option.series[0].data || []).findIndex((item) => item.raw === selected)
        : raw.findIndex((value) => value === selected);
      if (index >= 0) instance.dispatchAction({ type: 'highlight', dataIndex: index });
    }
  }, [selected, result, option, tile.type]);
  return <div ref={box} className="viz-chart" role="img" aria-label={tile.title || 'Chart'} />;
}

function Kpi({ tile, result }) {
  const value = result.rows[0]?.[0];
  const second = result.columns[1] ? result.rows[0]?.[1] : undefined;
  return (
    <div className="viz-kpi">
      <strong>{formatNumber(value, tile.options?.format)}</strong>
      <span>{result.columns[0]?.label}</span>
      {second !== undefined && <small>{result.columns[1].label}: {formatNumber(second, tile.options?.format)}</small>}
    </div>
  );
}

function Table({ tile, result, selected, onPick }) {
  const groupIndex = result.columns.findIndex((c) => c.role === 'group');
  return (
    <div className="viz-table-wrap">
      <table className="viz-table">
        <thead><tr>{result.columns.map((c, i) => <th key={i} className={c.kind === 'number' ? 'num' : ''}>{c.label}</th>)}</tr></thead>
        <tbody>
          {result.rows.map((row, r) => (
            <tr key={r} className={groupIndex >= 0 && selected !== undefined && row[groupIndex] === selected ? 'picked' : ''}
              onClick={groupIndex >= 0 && onPick ? () => onPick(row[groupIndex]) : undefined}>
              {row.map((value, i) => <td key={i} className={result.columns[i]?.kind === 'number' ? 'num' : ''}>
                {result.columns[i]?.kind === 'number' ? formatNumber(value, tile.options?.format) : label(value)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
      {result.truncated && <p className="muted small">Showing the first {result.rowCount.toLocaleString()} rows.</p>}
    </div>
  );
}

function Slicer({ result, chosen, onChange }) {
  const [search, setSearch] = useState('');
  const values = result.rows.map((row) => row[0]);
  const shown = values.filter((value) => label(value).toLowerCase().includes(search.toLowerCase()));
  const set = new Set(chosen || []);
  const toggle = (value) => { const next = new Set(set); if (next.has(value)) next.delete(value); else next.add(value); onChange([...next]); };
  return (
    <div className="viz-slicer">
      {values.length > 8 && <input type="search" placeholder="Search…" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search values" />}
      <div className="viz-slicer-list">
        {shown.map((value, i) => (
          <label key={i} className="check"><input type="checkbox" checked={set.has(value)} onChange={() => toggle(value)} /> {label(value)}
            <small className="muted">{result.rows[values.indexOf(value)]?.[1]?.toLocaleString()}</small></label>
        ))}
      </div>
      {set.size > 0 && <button type="button" className="link-btn" onClick={() => onChange([])}>Clear ({set.size})</button>}
    </div>
  );
}

/** The body of a tile. `picked` = the value this tile filtered the dashboard by (cross-filter), `onPick(value)` to change it. */
export default function Visual({ tile, result, picked, onPick, slicerValues, onSlicer }) {
  if (!result) return <div className="viz-empty">Loading…</div>;
  if (result.error) return <div className="viz-empty error" role="alert">{result.error}</div>;
  if (!result.rows.length && tile.type !== 'slicer') return <div className="viz-empty">No data for these filters</div>;
  if (tile.type === 'kpi') return <Kpi tile={tile} result={result} />;
  if (tile.type === 'table') return <Table tile={tile} result={result} selected={picked} onPick={onPick} />;
  if (tile.type === 'slicer') return <Slicer result={result} chosen={slicerValues} onChange={onSlicer} />;
  return <Chart tile={tile} result={result} selected={picked} onPick={onPick} />;
}
