// The model, drawn like Power BI's Model view: every dataset is a table card on a canvas, relationships are lines
// between the key columns with 1 / * at their ends. Drag a card by its title; drag a column onto a column of another
// table to relate them. Before a relationship is saved the server checks it on the data (how many keys are found on
// the other side, which side repeats its keys) and suggests the cardinality — so a wrong join is seen before a
// dashboard shows wrong totals. "Suggestions" lists column pairs that look like keys and do match.
// Card positions are kept in this browser.
import { useEffect, useMemo, useRef, useState } from 'react';
import { bi, KIND_ICON } from './api.js';

const LAYOUT_KEY = 'bi-model-layout';
const CARD_W = 230;
const HEAD_H = 46;
const ROW_H = 24;
const SHOWN = 12;
const CARDINALITIES = [['many-to-one', 'Many to one (*:1)'], ['one-to-one', 'One to one (1:1)'], ['one-to-many', 'One to many (1:*)'], ['many-to-many', 'Many to many (*:*)']];
const ENDS = { 'many-to-one': ['*', '1'], 'one-to-one': ['1', '1'], 'one-to-many': ['1', '*'], 'many-to-many': ['*', '*'] };

function loadLayout() {
  try { return JSON.parse(localStorage.getItem(LAYOUT_KEY) || '{}') || {}; } catch { return {}; }
}
function saveLayout(layout) {
  try { localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout)); } catch { /* private mode */ }
}

/** The column order on a card: related key columns first, then the others. */
function columnOrder(dataset, keys) {
  const visible = dataset.columns.filter((c) => !c.hidden);
  return [...visible.filter((c) => keys.has(c.name)), ...visible.filter((c) => !keys.has(c.name))];
}

function CheckResult({ check, names }) {
  if (!check) return null;
  if (check.error) return <p className="notice error">{check.error}</p>;
  const pct = (v) => `${v}%`;
  return (
    <div className={`rel-check ${check.quality}`}>
      <p className="rel-check-head">
        <b>{check.quality === 'good' ? '✓ The keys match' : check.quality === 'check' ? '⚠ Check before saving' : '✕ These columns do not join'}</b>
        <span>{check.matchedKeys.toLocaleString()} keys found on both sides · the data is <b>{check.cardinality.replace(/-/g, ' ')}</b></span>
      </p>
      <table className="rel-check-table">
        <thead><tr><th /><th>Rows</th><th>Different keys</th><th>Blank</th><th>Keys found on the other side</th></tr></thead>
        <tbody>
          <tr><th>{names.from}</th><td>{check.from.rows.toLocaleString()}</td><td>{check.from.distinct.toLocaleString()}{check.from.unique ? ' (unique)' : ''}</td><td>{check.from.blanks.toLocaleString()}</td><td>{pct(check.fromMatchedPct)}</td></tr>
          <tr><th>{names.to}</th><td>{check.to.rows.toLocaleString()}</td><td>{check.to.distinct.toLocaleString()}{check.to.unique ? ' (unique)' : ''}</td><td>{check.to.blanks.toLocaleString()}</td><td>{pct(check.toMatchedPct)}</td></tr>
        </tbody>
      </table>
      {check.missingExamples?.length > 0 && <p className="muted small">Not found in {names.to}: {check.missingExamples.map((v) => <code key={v}>{v}</code>)}</p>}
      {check.advice?.length > 0 && <ul className="rel-advice">{check.advice.map((a) => <li key={a}>{a}</li>)}</ul>}
    </div>
  );
}

export default function ModelView({ datasets, relationships, mayModel, notify, onChanged }) {
  const ready = datasets.filter((d) => d.columns.length);
  const [layout, setLayout] = useState(loadLayout);
  const [expanded, setExpanded] = useState({});
  const [selected, setSelected] = useState(null);       // a relationship id
  const [draft, setDraft] = useState(null);             // a new relationship being made
  const [check, setCheck] = useState(null);
  const [suggestions, setSuggestions] = useState(null);
  const [busy, setBusy] = useState(false);
  const [link, setLink] = useState(null);               // a column being dragged onto another: {from, x1, y1, x2, y2}
  const canvas = useRef(null);
  const name = (id) => datasets.find((d) => d.id === id)?.name || id;

  const keysOf = useMemo(() => {
    const map = {};
    relationships.forEach((r) => {
      (map[r.fromDataset] ||= new Set()).add(r.fromColumn);
      (map[r.toDataset] ||= new Set()).add(r.toColumn);
    });
    return map;
  }, [relationships]);

  // where each card is: saved position, else a grid
  const positions = useMemo(() => {
    const out = {};
    ready.forEach((d, i) => { out[d.id] = layout[d.id] || { x: 24 + (i % 4) * (CARD_W + 70), y: 24 + Math.floor(i / 4) * 360 }; });
    return out;
  }, [ready, layout]);
  const cards = ready.map((d) => {
    const keys = keysOf[d.id] || new Set();
    const order = columnOrder(d, keys);
    const shown = expanded[d.id] ? order : order.slice(0, Math.max(SHOWN, keys.size));
    return { d, keys, shown, more: order.length - shown.length, ...positions[d.id] };
  });
  const cardOf = (id) => cards.find((c) => c.d.id === id);
  /** The point of a column on a card's left or right edge (the card's title when the column is folded away). */
  const anchor = (datasetId, column, side) => {
    const card = cardOf(datasetId);
    if (!card) return null;
    const index = card.shown.findIndex((c) => c.name === column);
    const y = card.y + (index < 0 ? HEAD_H / 2 : HEAD_H + index * ROW_H + ROW_H / 2);
    return { x: card.x + (side === 'right' ? CARD_W : 0), y };
  };
  const width = Math.max(900, ...cards.map((c) => c.x + CARD_W + 60));
  const height = Math.max(520, ...cards.map((c) => c.y + HEAD_H + (c.shown.length + 1) * ROW_H + 60));

  // ---------------------------------------------------------------- dragging cards and columns
  const point = (event) => {
    const box = canvas.current.getBoundingClientRect();
    return { x: event.clientX - box.left + canvas.current.scrollLeft, y: event.clientY - box.top + canvas.current.scrollTop };
  };
  const moveCard = (event, id) => {
    if (event.button !== 0) return;
    event.preventDefault();
    const start = point(event);
    const origin = positions[id];
    const onMove = (e) => {
      const p = point(e);
      setLayout((l) => ({ ...l, [id]: { x: Math.max(0, origin.x + p.x - start.x), y: Math.max(0, origin.y + p.y - start.y) } }));
    };
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      setLayout((l) => { saveLayout(l); return l; });
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };
  const dragColumn = (event, datasetId, column) => {
    if (!mayModel || event.button !== 0) return;
    event.preventDefault();
    const start = point(event);
    setLink({ from: { datasetId, column }, x1: start.x, y1: start.y, x2: start.x, y2: start.y });
    const onMove = (e) => { const p = point(e); setLink((l) => l && { ...l, x2: p.x, y2: p.y }); };
    const onUp = (e) => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      setLink(null);
      const target = document.elementFromPoint(e.clientX, e.clientY)?.closest('[data-col]');
      if (!target) return;
      const [toDataset, toColumn] = [target.dataset.ds, target.dataset.col];
      if (toDataset === datasetId) { notify('Drop the column on a column of another table.', 'warn'); return; }
      startDraft({ fromDataset: datasetId, fromColumn: column, toDataset, toColumn });
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };
  const autoLayout = () => {
    // the tables that are the "one" side (lists) to the right of the tables that point at them
    const depth = {};
    ready.forEach((d) => { depth[d.id] = 0; });
    for (let i = 0; i < ready.length; i += 1) {
      relationships.filter((r) => r.active !== false).forEach((r) => {
        const [many, one] = r.cardinality === 'one-to-many' ? [r.toDataset, r.fromDataset] : [r.fromDataset, r.toDataset];
        if (depth[one] !== undefined && depth[many] !== undefined) depth[one] = Math.max(depth[one], depth[many] + 1);
      });
    }
    const columns = {};
    const next = {};
    ready.forEach((d) => {
      const col = Math.min(depth[d.id], 5);
      const row = (columns[col] = (columns[col] || 0) + 1) - 1;
      next[d.id] = { x: 24 + col * (CARD_W + 120), y: 24 + row * 330 };
    });
    setLayout(next);
    saveLayout(next);
  };

  // ---------------------------------------------------------------- relationships
  const runCheck = async (body) => {
    setCheck(null);
    try { setCheck(await bi.checkRelationship(body)); } catch (e) { setCheck({ error: e.message }); }
  };
  const startDraft = (body) => {
    setSelected(null);
    setDraft({ ...body, cardinality: 'many-to-one', active: true });
    bi.checkRelationship(body).then((result) => {
      setCheck(result);
      // the data says which side is the list: keep "many → one" by turning the relationship around
      if (result.cardinality === 'one-to-many') {
        setDraft({ fromDataset: body.toDataset, fromColumn: body.toColumn, toDataset: body.fromDataset, toColumn: body.fromColumn, cardinality: 'many-to-one', active: true });
        runCheck({ fromDataset: body.toDataset, fromColumn: body.toColumn, toDataset: body.fromDataset, toColumn: body.fromColumn });
      } else {
        setDraft((d) => d && { ...d, cardinality: result.cardinality });
      }
    }).catch((e) => setCheck({ error: e.message }));
  };
  const saveDraft = async () => {
    setBusy(true);
    try {
      await bi.relate(draft);
      notify(`${name(draft.fromDataset)} and ${name(draft.toDataset)} are now related.`, 'ok');
      setDraft(null); setCheck(null); setSuggestions(null);
      onChanged();
    } catch (e) { notify(e.message, 'error'); } finally { setBusy(false); }
  };
  const select = (rel) => {
    setDraft(null);
    setSelected(rel.id);
    runCheck({ fromDataset: rel.fromDataset, fromColumn: rel.fromColumn, toDataset: rel.toDataset, toColumn: rel.toColumn });
  };
  const current = relationships.find((r) => r.id === selected);
  const update = async (patch) => {
    try { await bi.updateRelationship(current.id, patch); onChanged(); notify('Relationship changed.', 'ok'); } catch (e) { notify(e.message, 'error'); }
  };
  const remove = async (rel) => {
    if (!window.confirm(`Remove the relationship ${name(rel.fromDataset)}[${rel.fromColumn}] → ${name(rel.toDataset)}[${rel.toColumn}]? Visuals using fields of both datasets stop working.`)) return;
    try { await bi.unrelate(rel.id); setSelected(null); setCheck(null); onChanged(); } catch (error) { notify(error.message, 'error'); }
  };
  const loadSuggestions = async () => {
    setBusy(true);
    try { setSuggestions((await bi.suggestRelationships()).suggestions); } catch (e) { notify(e.message, 'error'); } finally { setBusy(false); }
  };
  useEffect(() => { if (mayModel && ready.length >= 2 && relationships.length === 0 && suggestions === null) loadSuggestions(); }, [ready.length]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---------------------------------------------------------------- drawing
  const line = (rel) => {
    const a = cardOf(rel.fromDataset);
    const b = cardOf(rel.toDataset);
    if (!a || !b) return null;
    const leftToRight = a.x + CARD_W / 2 <= b.x + CARD_W / 2;
    const p1 = anchor(rel.fromDataset, rel.fromColumn, leftToRight ? 'right' : 'left');
    const p2 = anchor(rel.toDataset, rel.toColumn, leftToRight ? 'left' : 'right');
    const bend = Math.max(40, Math.abs(p2.x - p1.x) / 2);
    const d = `M ${p1.x} ${p1.y} C ${p1.x + (leftToRight ? bend : -bend)} ${p1.y}, ${p2.x + (leftToRight ? -bend : bend)} ${p2.y}, ${p2.x} ${p2.y}`;
    const [endA, endB] = ENDS[rel.cardinality || 'many-to-one'];
    const off = leftToRight ? 12 : -12;
    return (
      <g key={rel.id} className={`rel-line ${rel.active === false ? 'inactive' : ''} ${selected === rel.id ? 'on' : ''}`} onClick={() => select(rel)}>
        <path d={d} className="hit" />
        <path d={d} className="stroke" />
        <text x={p1.x + off} y={p1.y - 6} textAnchor="middle">{endA}</text>
        <text x={p2.x - off} y={p2.y - 6} textAnchor="middle">{endB}</text>
        <title>{`${name(rel.fromDataset)}[${rel.fromColumn}] ${endA}—${endB} ${name(rel.toDataset)}[${rel.toColumn}]${rel.active === false ? ' (inactive)' : ''}`}</title>
      </g>
    );
  };

  const draftNames = draft && { from: `${name(draft.fromDataset)}[${draft.fromColumn}]`, to: `${name(draft.toDataset)}[${draft.toColumn}]` };
  const currentNames = current && { from: `${name(current.fromDataset)}[${current.fromColumn}]`, to: `${name(current.toDataset)}[${current.toColumn}]` };

  return (
    <div className="stack model">
      <div className="page-head">
        <div><p className="eyebrow">Model</p><h1>Datasets and relationships</h1>
          <p className="muted">{mayModel ? 'Drag a column onto the matching column of another table to relate them; click a line to change or check it. ' : ''}A relationship lets one visual, DAX query or filter use fields of both tables — even when they come from different databases.</p></div>
        <div className="row">
          {mayModel && ready.length >= 2 && <button type="button" className="btn" onClick={loadSuggestions} disabled={busy}>{busy && !draft ? 'Looking…' : '✦ Suggestions'}</button>}
          <button type="button" className="btn" onClick={autoLayout} title="Lists (branches, products) to the right of the tables that point at them">Arrange</button>
        </div>
      </div>

      <div className="model-wrap">
        <div className="model-canvas" ref={canvas} aria-label="Model diagram">
          <div className="model-plane" style={{ width, height }}>
            <svg className="model-lines" width={width} height={height} aria-hidden="false">
              {relationships.map(line)}
              {link && <line x1={link.x1} y1={link.y1} x2={link.x2} y2={link.y2} className="rel-drag" />}
            </svg>
            {cards.map(({ d, keys, shown, more, x, y }) => (
              <section key={d.id} className="model-table" style={{ left: x, top: y, width: CARD_W }}>
                <header onMouseDown={(e) => moveCard(e, d.id)} title="Drag to move">
                  <b>{d.name}</b><small>{(d.rowCount || 0).toLocaleString()} rows · {d.columns.length} columns</small>
                </header>
                <ul>
                  {shown.map((c) => (
                    <li key={c.name} data-ds={d.id} data-col={c.name} className={`${keys.has(c.name) ? 'key' : ''} ${mayModel ? 'drag' : ''}`}
                      onMouseDown={(e) => dragColumn(e, d.id, c.name)} title={mayModel ? `Drag onto a column of another table to relate ${d.name}[${c.name}]` : c.name}>
                      <span className="kind">{KIND_ICON[c.kind] || '·'}</span><span className="col-name">{c.label || c.name}</span>
                      {keys.has(c.name) && <span className="key-icon" aria-label="key">🔑</span>}
                    </li>
                  ))}
                </ul>
                {(more > 0 || expanded[d.id]) && (
                  <button type="button" className="model-more" onClick={() => setExpanded((e) => ({ ...e, [d.id]: !e[d.id] }))}>
                    {expanded[d.id] ? 'Show fewer' : `+ ${more} more columns`}
                  </button>
                )}
              </section>
            ))}
            {ready.length === 0 && <p className="empty model-empty">Import datasets first; each one becomes a table here.</p>}
          </div>
        </div>

        <aside className="model-side">
          {draft && (
            <section className="card">
              <p className="eyebrow">New relationship</p>
              <h3 className="rel-title"><code>{draftNames.from}</code> → <code>{draftNames.to}</code></h3>
              {!check && <p className="muted">Checking the data…</p>}
              <CheckResult check={check} names={draftNames} />
              <label className="field"><span>Cardinality</span>
                <select value={draft.cardinality} onChange={(e) => setDraft({ ...draft, cardinality: e.target.value })}>
                  {CARDINALITIES.map(([id, label]) => <option key={id} value={id}>{label}{check && check.cardinality === id ? ' — matches the data' : ''}</option>)}
                </select>
              </label>
              <label className="check"><input type="checkbox" checked={draft.active} onChange={(e) => setDraft({ ...draft, active: e.target.checked })} /> Active (used by visuals and DAX)</label>
              <div className="row">
                <button type="button" className="btn primary" onClick={saveDraft} disabled={busy || check?.error || check?.quality === 'bad'}>Save relationship</button>
                <button type="button" className="btn" onClick={() => startDraft({ fromDataset: draft.toDataset, fromColumn: draft.toColumn, toDataset: draft.fromDataset, toColumn: draft.fromColumn })}>⇄ Turn around</button>
                <button type="button" className="btn" onClick={() => { setDraft(null); setCheck(null); }}>Cancel</button>
              </div>
            </section>
          )}

          {current && !draft && (
            <section className="card">
              <p className="eyebrow">Relationship</p>
              <h3 className="rel-title"><code>{currentNames.from}</code> → <code>{currentNames.to}</code></h3>
              {!check && <p className="muted">Checking the data…</p>}
              <CheckResult check={check} names={currentNames} />
              {mayModel && (
                <>
                  <label className="field"><span>Cardinality</span>
                    <select value={current.cardinality || 'many-to-one'} onChange={(e) => update({ cardinality: e.target.value })}>
                      {CARDINALITIES.map(([id, label]) => <option key={id} value={id}>{label}{check && check.cardinality === id ? ' — matches the data' : ''}</option>)}
                    </select>
                  </label>
                  <label className="check"><input type="checkbox" checked={current.active !== false} onChange={(e) => update({ active: e.target.checked })} /> Active (used by visuals and DAX)</label>
                  <div className="row">
                    <button type="button" className="btn danger" onClick={() => remove(current)}>Delete</button>
                    <button type="button" className="btn" onClick={() => { setSelected(null); setCheck(null); }}>Close</button>
                  </div>
                </>
              )}
            </section>
          )}

          {suggestions && !draft && (
            <section className="card">
              <p className="eyebrow">Suggestions</p>
              {suggestions.length === 0 ? <p className="muted">No more column pairs look like keys of each other. Drag a column onto another to relate them yourself.</p> : (
                <ul className="rel-suggest">
                  {suggestions.map((s) => (
                    <li key={`${s.fromDataset}-${s.fromColumn}-${s.toDataset}-${s.toColumn}`} className={s.quality}>
                      <div><code>{s.fromName}[{s.fromColumn}]</code> → <code>{s.toName}[{s.toColumn}]</code>
                        <small>{s.fromMatchedPct}% of keys found · {s.cardinality.replace(/-/g, ' ')}</small></div>
                      <button type="button" className="btn small" onClick={() => startDraft({ fromDataset: s.fromDataset, fromColumn: s.fromColumn, toDataset: s.toDataset, toColumn: s.toColumn })}>Use</button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

          <section className="card">
            <p className="eyebrow">Relationships ({relationships.length})</p>
            {relationships.length === 0 ? <p className="muted small">None yet.</p> : (
              <ul className="rel-list compact">
                {relationships.map((r) => (
                  <li key={r.id} className={`${selected === r.id ? 'on' : ''} ${r.active === false ? 'inactive' : ''}`}>
                    <button type="button" className="linkish" onClick={() => select(r)}>
                      <code>{name(r.fromDataset)}[{r.fromColumn}]</code> {ENDS[r.cardinality || 'many-to-one'].join(':')} <code>{name(r.toDataset)}[{r.toColumn}]</code>
                      {r.active === false && <small className="muted"> inactive</small>}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </aside>
      </div>
    </div>
  );
}
