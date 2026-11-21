import { useState } from 'react';
import { api } from './api.js';

const NAME = /^[A-Z][A-Za-z0-9_]{0,39}$/;
const INPUT = /^[a-z][A-Za-z0-9_]{0,29}$/;

/** Ready-made functions to start from (one click adds them, then they can be changed). */
export const FUNCTION_EXAMPLES = [
  { name: 'FullName', params: ['first', 'last'], body: '(first == null ? "" : first) + " " + (last == null ? "" : last)', description: 'First and last name with one space, empty instead of null' },
  { name: 'Percent', params: ['part', 'total'], body: 'total == 0 ? 0 : Math.round(part * 10000.0 / total) / 100.0', description: 'part as a percentage of total, 2 decimals (0 when total is 0)' },
  { name: 'WithTax', params: ['amount', 'rate'], body: 'amount + amount * rate / 100', description: 'Amount plus tax, e.g. WithTax($F{Amount}, 17)' },
  { name: 'Money', params: ['amount'], body: 'new java.text.DecimalFormat("#,##0.00").format(amount)', description: 'Number as 1,234.50' },
  { name: 'ShortDate', params: ['date'], body: 'date == null ? "" : new java.text.SimpleDateFormat("dd-MM-yyyy").format(date)', description: 'Date as 31-12-2026, empty when missing' },
  { name: 'OrDash', params: ['text'], body: 'text == null || text.trim().isEmpty() ? "—" : text', description: 'A dash instead of an empty value' },
];

/** Problems of one function before it is saved (the server checks the formula itself). */
export function functionProblems(fn, all) {
  const out = [];
  if (!NAME.test(fn.name || '')) out.push('Name: start with a capital letter, then letters, digits or _ (e.g. FullName).');
  if (all.filter((f) => f.name === fn.name).length > 1) out.push(`Another function is also called ${fn.name}.`);
  const bad = (fn.params || []).filter((p) => !INPUT.test(p));
  if (bad.length) out.push(`Inputs ${bad.join(', ')}: start with a small letter (e.g. amount).`);
  if (new Set(fn.params).size !== (fn.params || []).length) out.push('Two inputs have the same name.');
  if (!String(fn.body || '').trim()) out.push('Formula: write what the function returns.');
  return out;
}

const literal = (value) => {
  const text = String(value ?? '').trim();
  if (text === '') return 'null';
  if (/^-?\d+(\.\d+)?$/.test(text)) return text;
  if (text === 'true' || text === 'false') return text;
  return JSON.stringify(text);
};
const usage = (fn) => `${fn.name}(${(fn.params || []).map((p) => `$F{${p[0].toUpperCase()}${p.slice(1)}}`).join(', ')})`;

/**
 * Formulas → Custom Functions: the report's own functions. Name(input, …) = a formula over its inputs, usable in
 * every expression (Text Field, calculated field, Print When…) like a built-in one. Saved with the design.
 */
export default function FunctionsDialog({ functions = [], onSave, onClose }) {
  const [list, setList] = useState(() => functions.map((f) => ({ ...f, params: [...(f.params || [])] })));
  const [index, setIndex] = useState(functions.length ? 0 : -1);
  const [paramsText, setParamsText] = useState(() => (functions[0]?.params || []).join(', '));
  const [samples, setSamples] = useState({});
  const [result, setResult] = useState(null); // { state, text }
  const current = index >= 0 ? list[index] : null;

  const pick = (i, from = list) => { setIndex(i); setParamsText((from[i]?.params || []).join(', ')); setSamples({}); setResult(null); };
  const patch = (values) => { setList((l) => l.map((f, i) => (i === index ? { ...f, ...values } : f))); setResult(null); };
  const add = (example) => {
    let name = example?.name || 'MyFunction';
    for (let n = 2; list.some((f) => f.name === name); n += 1) name = `${example?.name || 'MyFunction'}${n}`;
    const fn = example ? { ...example, name, params: [...example.params] } : { name, params: ['value'], body: 'value', description: '' };
    const next = [...list, fn];
    setList(next);
    pick(next.length - 1, next);
  };
  const remove = () => { const next = list.filter((_, i) => i !== index); setList(next); pick(next.length ? 0 : -1, next); };
  const commitParams = (text) => patch({ params: text.split(',').map((p) => p.trim()).filter(Boolean) });

  const tryIt = async () => {
    if (!current) return;
    const problems = functionProblems(current, list);
    if (problems.length) { setResult({ state: 'error', text: problems[0] }); return; }
    setResult({ state: 'busy', text: 'Running…' });
    const call = `${current.name}(${current.params.map((p) => literal(samples[p])).join(', ')})`;
    try {
      const r = await api.tryFunction(list, call, {});
      setResult({ state: 'ok', text: r.value === null ? `${call} → (empty)` : `${call} → ${r.value}`, type: r.type });
    } catch (error) { setResult({ state: 'error', text: error.message }); }
  };

  const allProblems = list.flatMap((f) => functionProblems(f, list).map((p) => `${f.name || '(no name)'}: ${p}`));
  const save = () => { if (!allProblems.length) onSave(list); };

  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal fn-dialog" role="dialog" aria-label="Custom functions" onKeyDown={(e) => { e.stopPropagation(); if (e.key === 'Escape') onClose(); }}>
        <div className="wizard-head">
          <h3>Custom functions</h3>
          <p className="muted">Make your own function once, use it in every expression: <code>=FullName($F&#123;First&#125;, $F&#123;Last&#125;)</code>. The inputs are the values given between the brackets.</p>
        </div>
        <div className="fn-body">
          <aside className="fn-list">
            <ul>
              {list.map((f, i) => (
                // eslint-disable-next-line react/no-array-index-key -- names can be edited
                <li key={i}><button type="button" className={i === index ? 'on' : ''} onClick={() => pick(i)}>
                  <b>{f.name || '(no name)'}</b><small>({(f.params || []).join(', ')})</small>
                </button></li>
              ))}
              {!list.length && <li className="muted fn-empty">No functions yet. Start from an example below, or make a new one.</li>}
            </ul>
            <button type="button" className="btn sm" onClick={() => add()}>＋ New function</button>
            <p className="fn-examples-title">Start from an example</p>
            <div className="fn-examples">
              {FUNCTION_EXAMPLES.map((ex) => <button key={ex.name} type="button" className="btn sm" title={ex.description} onClick={() => add(ex)}>{ex.name}</button>)}
            </div>
          </aside>
          {current ? (
            <section className="fn-edit">
              <div className="fn-steps">
                <label><span className="step">1</span> Name <small>(capital first letter)</small>
                  <input value={current.name} onChange={(e) => patch({ name: e.target.value.replace(/[^A-Za-z0-9_]/g, '') })} placeholder="FullName" />
                </label>
                <label><span className="step">2</span> Inputs <small>(comma-separated, small first letter)</small>
                  <input value={paramsText} onChange={(e) => setParamsText(e.target.value)} onBlur={(e) => commitParams(e.target.value)} placeholder="first, last" />
                </label>
              </div>
              <label className="fn-formula"><span className="step">3</span> Formula <small>(what it returns; use the inputs by name — Java style like other expressions, no leading =)</small>
                <textarea rows={4} value={current.body} onChange={(e) => patch({ body: e.target.value })} spellCheck={false}
                  placeholder={'first + " " + last'} />
              </label>
              <label>Description <small>(optional: shown in the Expression editor)</small>
                <input value={current.description || ''} onChange={(e) => patch({ description: e.target.value })} />
              </label>
              {functionProblems(current, list).map((p) => <p key={p} className="fn-problem">✕ {p}</p>)}
              <div className="fn-try">
                <b><span className="step">4</span> Try it</b>
                <div className="fn-try-row">
                  {current.params.map((p) => (
                    <label key={p}>{p}<input value={samples[p] ?? ''} placeholder="value" onChange={(e) => setSamples((s) => ({ ...s, [p]: e.target.value }))}
                      onKeyDown={(e) => { if (e.key === 'Enter') tryIt(); }} /></label>
                  ))}
                  <button type="button" className="btn" onClick={tryIt} disabled={result?.state === 'busy'}>▶ Run</button>
                </div>
                {result && <p className={`test-result ${result.state}`}><span className="dot" />{result.text}{result.type ? <small className="fn-type"> ({result.type})</small> : null}</p>}
                <small className="muted">Use it in a Text Field: <code>={usage(current)}</code></small>
              </div>
              <div className="row fn-remove"><button type="button" className="btn sm danger" onClick={remove}>Delete {current.name}</button></div>
            </section>
          ) : (
            <section className="fn-edit fn-intro">
              <p><b>How it works</b></p>
              <ol>
                <li>Give the function a <b>name</b>, e.g. <code>WithTax</code>.</li>
                <li>List its <b>inputs</b>, e.g. <code>amount, rate</code>.</li>
                <li>Write the <b>formula</b> with those inputs: <code>amount + amount * rate / 100</code>.</li>
                <li><b>Try it</b> with sample values, then use it anywhere: <code>=WithTax($F&#123;Amount&#125;, 17)</code>.</li>
              </ol>
            </section>
          )}
        </div>
        {allProblems.length > 0 && <p className="fn-problem fn-save-note">Fix before saving: {allProblems[0]}</p>}
        <div className="row wizard-buttons">
          <span className="muted" style={{ marginRight: 'auto', fontSize: 12 }}>{list.length} function{list.length === 1 ? '' : 's'} · saved with the report</span>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="button" className="btn primary" onClick={save} disabled={allProblems.length > 0}>Save functions</button>
        </div>
      </div>
    </div>
  );
}
