// Analytics → ML lab (#ml): choose a dataset and an analysis, see how good it is, compare models, try "what if",
// and put it on a dashboard (an ML visual, trained again on fresh data every time it is shown).
import { useEffect, useMemo, useRef, useState } from 'react';
import { bi, newTileId, tileQuery, tileReady } from './api.js';
import { ML_MODELS, cell, modelOf, percent, score, setupFor, targetsOf } from './mlModels.js';
import MlSetup from './MlSetup.jsx';
import Visual from './Visual.jsx';

const START = { id: 'lab', type: 'ml', title: '', dataset: '', groupBy: [], values: [], filters: [], sort: [], limit: null, detail: false, columns: [], options: {} };

function WhatIf({ ml, onAsk, busy }) {
  const [values, setValues] = useState({});
  useEffect(() => { setValues(Object.fromEntries(ml.inputs.map((i) => [i.name, i.kind === 'number' ? Number(Number(i.typical).toPrecision(4)) : i.kind === 'date' ? new Date((i.typical || 0) * 86_400_000).toISOString().slice(0, 10) : i.typical]))); }, [JSON.stringify(ml.inputs.map((i) => i.name))]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <form className="ml-whatif" onSubmit={(e) => { e.preventDefault(); onAsk(values); }}>
      <b>What if…</b>
      <div className="params">
        {ml.inputs.map((input) => (
          <label key={input.name} className="field"><span>{input.name}</span>
            {input.values
              ? <select value={values[input.name] ?? ''} onChange={(e) => setValues({ ...values, [input.name]: e.target.value })}>{input.values.map((v) => <option key={v}>{v}</option>)}</select>
              : <input type={input.kind === 'date' ? 'date' : 'number'} step="any" value={values[input.name] ?? ''} onChange={(e) => setValues({ ...values, [input.name]: input.kind === 'date' ? e.target.value : e.target.value === '' ? '' : Number(e.target.value) })} />}
          </label>
        ))}
      </div>
      <div className="row"><button className="btn primary" type="submit" disabled={busy}>Predict {ml.target}</button>
        {ml.prediction && <span className="ml-answer">{ml.target} ≈ <b>{cell(ml.prediction.value)}</b>{ml.prediction.range ? <small> ± {cell(ml.prediction.range)}</small> : null}
          {ml.prediction.chances && <small> · {ml.prediction.chances.slice().sort((a, b) => b.value - a.value).slice(0, 3).map((c) => `${c.name} ${percent(c.value)}`).join(' · ')}</small>}</span>}
      </div>
    </form>
  );
}

export default function MlLab({ datasets, dashboards, notify, onAdded, go }) {
  const ready = useMemo(() => datasets.filter((d) => d.columns.length), [datasets]);
  const [tile, setTile] = useState(START);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState('');
  const [ranking, setRanking] = useState(null);
  const [board, setBoard] = useState('');
  const asked = useRef(0);
  const dataset = ready.find((d) => d.id === tile.dataset);
  const columns = dataset?.columns || [];

  const choose = (id) => {
    const found = ready.find((d) => d.id === id);
    const hasNumber = found.columns.filter((c) => c.kind === 'number').length > 1;
    const blank = { ...START, dataset: id };
    setTile({ ...blank, ...setupFor(tile.ml?.model || (hasNumber ? 'linear' : 'forecast'), blank, found.columns) });
  };
  useEffect(() => { if (!tile.dataset && ready.length) choose(ready[0].id); }, [ready]); // eslint-disable-line react-hooks/exhaustive-deps

  const ask = async (predict) => {
    const ticket = ++asked.current;
    setBusy('run');
    try {
      const { results } = await bi.queryBatch([tileQuery({ ...tile, ml: { ...tile.ml, predict } })], []);
      if (ticket === asked.current) setResult(results[0]);
    } catch (error) { if (ticket === asked.current) setResult({ error: error.message }); }
    finally { if (ticket === asked.current) setBusy(''); }
  };
  const key = JSON.stringify([tile.dataset, tile.ml, tile.groupBy, tile.values]);
  useEffect(() => {
    setRanking(null);
    if (!tileReady(tile)) { setResult(null); return undefined; }
    const timer = setTimeout(() => ask(), 500);
    return () => clearTimeout(timer);
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

  const model = modelOf(tile.ml?.model);
  const compare = async () => {
    setBusy('compare');
    const rivals = ML_MODELS.filter((m) => m.target && targetsOf(m, columns).some((c) => c.name === tile.ml.target));
    try {
      const { results } = await bi.queryBatch(rivals.map((m) => tileQuery({ ...tile, id: m.id, ml: { ...tile.ml, model: m.id, params: {}, predict: undefined } })), []);
      setRanking(rivals.map((m, i) => ({ m, ml: results[i].ml, error: results[i].error })).sort((a, b) => (b.ml ? score(b.ml) : -9) - (a.ml ? score(a.ml) : -9)));
    } catch (error) { notify(error.message, 'error'); } finally { setBusy(''); }
  };
  const add = async () => {
    try {
      const name = model.name + (tile.ml.target ? `: ${tile.ml.target}` : '');
      const own = { ...tile, id: newTileId(), title: name, x: 0, w: 6, h: 6, ml: { ...tile.ml, predict: undefined } };
      if (board) {
        const { dashboard } = await bi.dashboard(board);
        const y = dashboard.tiles.reduce((most, t) => Math.max(most, t.y + t.h), 0);
        await bi.saveDashboard(board, { tiles: [...dashboard.tiles, { ...own, y }] });
        await onAdded(); go(`dash/${board}`);
      } else {
        const { dashboard } = await bi.createDashboard({ name, tiles: [{ ...own, y: 0, w: 12, h: 8 }] });
        await onAdded(); go(`dash/${dashboard.id}`);
      }
    } catch (error) { notify(error.message, 'error'); }
  };

  if (!ready.length) return <section className="card"><h1>ML lab</h1><p className="muted">Machine learning works on an imported dataset. Make one first.</p><a className="btn primary" href="#ds/new">New dataset</a></section>;
  return (
    <div className="ml-lab">
      <div className="page-head"><div><p className="eyebrow">Analytics</p><h1>ML lab</h1><p className="muted">Predict, look ahead and find patterns in a dataset. Nothing to install and no code.</p></div></div>
      <div className="ml-split">
        <section className="card stack">
          <label className="field"><span>Dataset</span><select value={tile.dataset} onChange={(e) => choose(e.target.value)}>{ready.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label>
          <MlSetup tile={tile} columns={columns} onChange={setTile} />
          {model?.target && <button type="button" className="btn" onClick={compare} disabled={Boolean(busy) || !tileReady(tile)}>{busy === 'compare' ? 'Comparing…' : 'Compare all models'}</button>}
          <div className="ml-add">
            <b>Put it on a dashboard</b>
            <select value={board} onChange={(e) => setBoard(e.target.value)} aria-label="Dashboard"><option value="">New dashboard</option>{dashboards.filter((d) => d.mayChange).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select>
            <button type="button" className="btn primary" onClick={add} disabled={!result?.ml}>Add</button>
          </div>
        </section>
        <section className="card ml-out" aria-busy={Boolean(busy)}>
          {ranking && (
            <div className="ml-ranking"><b>Best first</b>
              {ranking.map(({ m, ml, error }, i) => (
                <button key={m.id} type="button" className={m.id === tile.ml.model ? 'on' : ''} onClick={() => setTile({ ...tile, ml: { ...tile.ml, model: m.id, params: {} } })}>
                  <span>{i === 0 && ml ? '🏆 ' : ''}{m.name}</span><small>{ml ? `${ml.task === 'classification' ? 'Accuracy' : 'R²'} ${percent(score(ml))}` : error}</small>
                </button>
              ))}
            </div>
          )}
          {!tileReady(tile) ? <div className="viz-empty">{model ? 'Choose the columns on the left' : 'Choose an analysis on the left'}</div>
            : <div className={busy === 'run' ? 'ml-busy' : ''}><Visual tile={tile} result={result} full /></div>}
          {result?.ml?.inputs && <WhatIf ml={result.ml} onAsk={ask} busy={Boolean(busy)} />}
        </section>
      </div>
    </div>
  );
}
