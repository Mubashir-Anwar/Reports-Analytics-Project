import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { api } from './api.js';

/** Text Field → Pattern: "Format Pattern" (Java DecimalFormat / SimpleDateFormat), with own patterns saved on the server. */

const DATES = ['dd/MM/yyyy', 'dd-MM-yyyy', 'dd MMM yyyy', 'dd-MMM-yyyy', 'MMM-yy', 'MMMM yyyy', 'dd MMMM yyyy', 'yyyy-MM-dd', 'MM/dd/yyyy',
  'EEEE, dd MMMM yyyy', 'dd/MM/yyyy HH:mm', 'dd/MM/yyyy hh:mm a', 'yyyy-MM-dd HH:mm:ss'];
const TIMES = ['HH:mm', 'HH:mm:ss', 'hh:mm a', 'hh:mm:ss a', 'H:mm'];
const CATEGORIES = [
  ['Number', '123', 'Whole and decimal numbers'], ['Percentage', '%', '0.25 prints as 25%'], ['Currency', 'Rs', 'Amounts with a symbol'],
  ['Scientific', 'E0', 'Very big or small numbers'], ['Date', '31', 'Day, month and year'], ['Time', '◷', 'Hours and minutes'],
  ['Custom', '✎', 'Your own saved patterns'],
];
const SYMBOLS = ['Rs', 'PKR', '$', '€', '£', 'AED', 'SAR'];
const NUMBER_PARTS = [['#,##0', 'thousands'], ['0.00', 'two decimals'], ['0.##', 'up to two decimals'], ['00000', 'leading zeros'], ['%', 'percent (× 100)'],
  ['E0', 'scientific'], [';(#,##0.00)', 'how negatives look'], ["' text'", 'fixed words']];
const DATE_PARTS = [['dd', 'day 19'], ['EEE', 'Thu'], ['EEEE', 'Thursday'], ['MM', 'month 11'], ['MMM', 'Nov'], ['MMMM', 'November'], ['yy', '26'], ['yyyy', '2026'],
  ['HH', 'hour 14'], ['hh', 'hour 02'], ['mm', 'minutes'], ['ss', 'seconds'], ['a', 'AM / PM'], ["' text '", 'fixed words']];
const MAX_LENGTH = 60;
const SAMPLE_NUMBER = -1234567.891;
const SAMPLE_DATE = new Date(2026, 10, 19, 14, 5, 9);

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

const bareOf = (pattern) => String(pattern || '').replace(/'[^']*'/g, '');

/** "date", "number", or "" for a pattern that formats nothing (the same rule as the server). */
export function patternKind(pattern) {
  const bare = bareOf(pattern);
  if (/[#0]/.test(bare)) return 'number';
  return /[yMdHhmsEakS]/.test(bare) ? 'date' : '';
}

/** What kind of pattern this is (to open the dialog on the right category). */
export function patternCategory(pattern) {
  if (!pattern) return 'Number';
  const bare = bareOf(pattern);
  if (patternKind(pattern) !== 'date') {
    if (/E0/.test(bare)) return 'Scientific';
    if (/%/.test(bare)) return 'Percentage';
    if (/^[^#0]*[^\s#0,.;-][^#0]*[#0]/.test(bare.split(';')[0])) return 'Currency';
    return 'Number';
  }
  return /[yMdE]/.test(bare) ? 'Date' : 'Time';
}

function numberBody(decimals, grouping) {
  return (grouping ? '#,##0' : '0') + (decimals > 0 ? `.${'0'.repeat(decimals)}` : '');
}

/** [prefix, digits, suffix] of a number pattern; words between ' ' are fixed text. */
function splitNumber(pattern) {
  const out = ['', '', ''];
  let stage = 0;
  let quoted = false;
  for (let i = 0; i < pattern.length; i += 1) {
    const ch = pattern[i];
    if (ch === "'") {
      if (pattern[i + 1] === "'") { out[stage] += "'"; i += 1; } else quoted = !quoted;
    } else if (!quoted && '#0,.'.includes(ch) && stage < 2 && !(stage === 0 && ch === '.' && !/^\.[#0]/.test(pattern.slice(i)))) {
      stage = 1;
      out[1] += ch;
    } else {
      if (stage === 1) stage = 2;
      out[stage] += ch;
    }
  }
  return out;
}

/** A Java number pattern applied in the browser, for the sample only (the server does the real formatting). */
export function formatNumberSample(value, pattern) {
  let [positive, ...rest] = String(pattern).split(';');
  const negative = rest.join(';');
  const science = positive.includes("'") ? null : positive.match(/^([^E]*[#0.])E(0+)(.*)$/);
  if (science) positive = science[1] + science[3];
  const [prefix, core, suffix] = splitNumber(positive);
  if (!core) return String(value);
  const [intPart, frac = ''] = core.split('.');
  let number = Math.abs(value) * (/%/.test(prefix + suffix) ? 100 : 1);
  let exponent = '';
  if (science) {
    const power = number ? Math.floor(Math.log10(number)) : 0;
    number /= 10 ** power;
    exponent = `E${power < 0 ? '-' : ''}${String(Math.abs(power)).padStart(science[2].length, '0')}`;
  }
  let [whole, decimals = ''] = number.toFixed(Math.min(frac.length, 20)).split('.');
  decimals = decimals.replace(/0+$/, '').padEnd((frac.match(/0/g) || []).length, '0');
  whole = whole.replace(/^0+/, '').padStart((intPart.match(/0/g) || []).length, '0');
  const group = intPart.includes(',') ? intPart.length - intPart.lastIndexOf(',') - 1 : 0;
  if (group) whole = whole.replace(new RegExp(`\\B(?=(\\d{${group}})+(?!\\d))`, 'g'), ',');
  const body = (whole + (decimals ? `.${decimals}` : '') || '0') + exponent;
  if (value < 0 && /[1-9]/.test(body)) {
    if (!negative) return `-${prefix}${body}${suffix}`;
    const n = splitNumber(negative);
    return `${n[0]}${body}${n[2]}`;
  }
  return `${prefix}${body}${suffix}`;
}

/** A SimpleDateFormat pattern applied in the browser, for the sample only. */
export function formatDateSample(date, pattern) {
  const pad = (n, w) => String(n).padStart(w, '0');
  return String(pattern).replace(/'((?:[^']|'')*)'?|([A-Za-z])\2*/g, (token, literal) => {
    if (literal !== undefined) return literal.replace(/''/g, "'") || "'";
    const n = token.length;
    switch (token[0]) {
      case 'y': return n === 2 ? pad(date.getFullYear() % 100, 2) : pad(date.getFullYear(), n);
      case 'M': return n >= 4 ? MONTHS[date.getMonth()] : n === 3 ? MONTHS[date.getMonth()].slice(0, 3) : pad(date.getMonth() + 1, n);
      case 'd': return pad(date.getDate(), n);
      case 'H': return pad(date.getHours(), n);
      case 'k': return pad(date.getHours() || 24, n);
      case 'h': return pad(date.getHours() % 12 || 12, n);
      case 'm': return pad(date.getMinutes(), n);
      case 's': return pad(date.getSeconds(), n);
      case 'S': return n <= 3 ? pad(date.getMilliseconds(), 3).slice(0, n) : pad(date.getMilliseconds(), n);
      case 'E': return n >= 4 ? DAYS[date.getDay()] : DAYS[date.getDay()].slice(0, 3);
      case 'a': return date.getHours() < 12 ? 'AM' : 'PM';
      case 'z': return 'UTC';
      case 'Z': return '+0000';
      default: throw new Error(`The date letter “${token[0]}” is not supported. Put fixed words between ' '`);
    }
  });
}

export function patternSample(pattern) {
  if (!pattern) return '(no pattern: the value as it is)';
  try {
    const kind = patternKind(pattern);
    if (kind === 'date') return formatDateSample(SAMPLE_DATE, pattern);
    if (kind === 'number') return `${formatNumberSample(SAMPLE_NUMBER, pattern)}   ·   ${formatNumberSample(1234.5, pattern)}`;
  } catch { /* shown as — */ }
  return '—';
}

/** Why a pattern cannot be used, or ''. */
export function patternProblem(pattern) {
  if (!pattern.trim()) return '';
  if (!patternKind(pattern)) return 'This formats nothing: use # or 0 for a number, or date letters such as dd/MM/yyyy.';
  try { if (patternKind(pattern) === 'date') formatDateSample(SAMPLE_DATE, pattern); } catch (error) { return error.message; }
  return '';
}

const oneSample = (pattern) => {
  try { return patternKind(pattern) === 'date' ? formatDateSample(SAMPLE_DATE, pattern) : formatNumberSample(1234567.891, pattern); } catch { return '—'; }
};

/** Results for several values, so zero and negative numbers are seen too. */
function results(pattern, own) {
  const kind = patternKind(pattern);
  const run = (label, value, format) => { try { return [label, format(value, pattern)]; } catch { return [label, '—']; } };
  let list = [];
  if (kind === 'date') {
    const typed = own ? new Date(/^\d{4}-\d{2}-\d{2}$/.test(own) ? `${own}T00:00:00` : own) : null;
    list = [run('19 Nov 2026, 14:05', SAMPLE_DATE, formatDateSample), run('3 Jan 2026, 09:30', new Date(2026, 0, 3, 9, 30), formatDateSample)];
    if (typed) list.unshift(Number.isNaN(typed.getTime()) ? [own, 'not a date (2026-11-19)'] : run(own, typed, formatDateSample));
  } else if (kind === 'number') {
    const typed = Number(own.replace(/,/g, ''));
    list = (/%/.test(bareOf(pattern)) ? [0.256, -0.05, 0] : [1234567.891, -1234.5, 0]).map((v) => run(v.toLocaleString('en-US', { maximumFractionDigits: 3 }), v, formatNumberSample));
    if (own) list.unshift(Number.isFinite(typed) ? run(own, typed, formatNumberSample) : [own, 'not a number']);
  }
  return list;
}

const numberPresets = {
  Number: ['#,##0', '#,##0.00', '0', '0.00', '#,##0.##', '00000'],
  Percentage: ['0%', '0.0%', '0.00%', '#,##0.00%'],
  Currency: ['Rs #,##0', 'Rs #,##0.00', 'PKR #,##0.00', '$ #,##0.00', 'Rs #,##0.00;(Rs #,##0.00)'],
  Scientific: ['0E0', '0.00E0', '0.000E00'],
};

export default function PatternDialog({ value, onDone, onClose }) {
  const [category, setCategory] = useState(() => patternCategory(value));
  const [pattern, setPattern] = useState(value || '');
  const [decimals, setDecimals] = useState(() => (value ? ((bareOf(value).split(';')[0].split('.')[1] || '').match(/[#0]/g) || []).length : 2));
  const [grouping, setGrouping] = useState(() => !value || String(value).includes(','));
  const [brackets, setBrackets] = useState(() => String(value || '').includes(';('));
  const [symbol, setSymbol] = useState(() => (patternCategory(value) === 'Currency' ? String(value).match(/^([^#0]*?)\s*[#0]/)?.[1] || 'Rs' : 'Rs'));
  const [own, setOwn] = useState('');
  const [saved, setSaved] = useState(null);              // null: not read yet
  const [form, setForm] = useState(null);                // {id?, name}: the "save" line is open
  const [note, setNote] = useState(null);
  const [busy, setBusy] = useState(false);
  const [parts, setParts] = useState(() => (patternKind(value) === 'date' ? 'date' : 'number'));
  const input = useRef(null);
  const nameInput = useRef(null);

  useEffect(() => {
    let live = true;
    api.patterns().then((data) => {
      if (!live) return;
      setSaved(data.patterns);
      if (value && data.patterns.some((item) => item.pattern === value)) setCategory('Custom');
    }).catch((error) => { if (live) { setSaved([]); setNote({ kind: 'error', text: `Saved patterns could not be read: ${error.message}` }); } });
    return () => { live = false; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- only when the dialog opens
  useEffect(() => {
    const escape = (event) => { if (event.key === 'Escape') onClose(); };
    document.addEventListener('keydown', escape);
    return () => document.removeEventListener('keydown', escape);
  }, [onClose]);
  const cats = useRef(null);
  useEffect(() => { cats.current?.querySelector('.on')?.scrollIntoView({ block: 'nearest', inline: 'center' }); }, [category]);   // phone: one sliding row
  const formOpen = Boolean(form);
  useEffect(() => { if (formOpen) nameInput.current?.focus(); }, [formOpen]);

  const build = (next = {}) => {
    const c = next.category ?? category;
    const d = next.decimals ?? decimals;
    const g = next.grouping ?? grouping;
    const b = next.brackets ?? brackets;
    const s = next.symbol ?? symbol;
    const body = numberBody(d, g);
    const withNegative = (p) => (b ? `${p};(${p})` : p);
    if (c === 'Number') return withNegative(body);
    if (c === 'Percentage') return `${body}%`;
    if (c === 'Currency') return withNegative(`${s ? `${s} ` : ''}${body}`);
    if (c === 'Scientific') return `0${d > 0 ? `.${'0'.repeat(d)}` : ''}E0`;
    return null;
  };
  const change = (next) => {
    if (next.decimals !== undefined) setDecimals(next.decimals);
    if (next.grouping !== undefined) setGrouping(next.grouping);
    if (next.brackets !== undefined) setBrackets(next.brackets);
    if (next.symbol !== undefined) setSymbol(next.symbol);
    const built = build(next);
    if (built !== null) setPattern(built);
  };
  const open = (next) => {
    setForm(null); setNote(null); setCategory(next); setOwn('');
    if (next === 'Custom') return;                       // keeps the pattern: start from it, or choose a saved one
    if (next === 'Date' || next === 'Time') setPattern((next === 'Date' ? DATES : TIMES)[0]);
    else { change({ category: next }); }
    setParts(next === 'Date' || next === 'Time' ? 'date' : 'number');
  };
  const pick = (next) => {                               // a ready pattern: the options beside it follow
    setPattern(next);
    if (patternKind(next) !== 'number') return;
    const first = bareOf(next).split(';')[0];
    setDecimals((first.split('.')[1]?.match(/^[#0]*/)[0] || '').length);
    setGrouping(first.includes(','));
    setBrackets(next.includes(';('));
    if (category === 'Currency') setSymbol(next.match(/^([^#0]*?)\s*[#0]/)?.[1] || '');
  };
  const insert = (text) => {
    const box = input.current;
    const from = box?.selectionStart ?? pattern.length;
    const to = box?.selectionEnd ?? from;
    const next = (pattern.slice(0, from) + text + pattern.slice(to)).slice(0, MAX_LENGTH);
    setPattern(next);
    requestAnimationFrame(() => { box?.focus(); box?.setSelectionRange(from + text.length, from + text.length); });
  };

  const problem = patternProblem(pattern);
  const save = async () => {
    if (busy) return;
    setBusy(true); setNote(null);
    try {
      const body = { name: form.name, pattern: pattern.trim() };
      const result = form.id ? await api.changePattern(form.id, body) : await api.addPattern(body);
      setSaved(result.patterns); setForm(null); setCategory('Custom');
      setNote({ kind: 'ok', text: `Saved: ${result.pattern.name}` });
    } catch (error) { setNote({ kind: 'error', text: error.message }); }
    setBusy(false);
  };
  const remove = async (item) => {
    if (!window.confirm(`Remove “${item.name}” from the saved patterns? Reports that use it keep their format.`)) return;
    try { setSaved((await api.deletePattern(item.id)).patterns); setNote(null); if (form?.id === item.id) setForm(null); } catch (error) { setNote({ kind: 'error', text: error.message }); }
  };
  const create = () => { setPattern(''); setOwn(''); setForm({ name: '' }); setNote(null); };

  const numeric = Boolean(numberPresets[category]);
  const cards = category === 'Date' ? DATES : category === 'Time' ? TIMES : numberPresets[category] || [];
  const active = CATEGORIES.find(([name]) => name === category);

  return createPortal(   // on <body>: inside the Properties panel it would be cut off at the panel's edge
    <div className="pat-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="pat-dialog" role="dialog" aria-modal="true" aria-label="Format pattern">
        <header className="pat-top"><div><h2>Format pattern</h2><p>How a number, date or time is printed. Choose one, or write your own.</p></div>
          <button type="button" className="btn quiet sm" onClick={onClose} aria-label="Close">✕</button></header>
        <div className="pat-body">
          <nav ref={cats} className="pat-cats" aria-label="Category">
            {CATEGORIES.map(([name, icon]) => (
              <button type="button" key={name} aria-current={name === category ? 'true' : undefined} className={name === category ? 'on' : ''} onClick={() => open(name)}>
                <i aria-hidden="true">{icon}</i><span>{name}</span>{name === 'Custom' && saved?.length ? <em>{saved.length}</em> : null}
              </button>
            ))}
          </nav>
          <div className="pat-main">
            <div className="pat-head"><div><h3>{category}</h3><p>{active[2]}</p></div>
              {category === 'Custom' && <button type="button" className="btn sm" onClick={create}>＋ New pattern</button>}</div>

            {numeric && (
              <div className="pat-options">
                <div className="pat-field"><span>Decimal places</span>
                  <span className="pat-stepper">
                    <button type="button" onClick={() => change({ decimals: Math.max(0, decimals - 1) })} aria-label="Fewer decimal places">−</button>
                    <b>{decimals}</b>
                    <button type="button" onClick={() => change({ decimals: Math.min(10, decimals + 1) })} aria-label="More decimal places">＋</button>
                  </span>
                </div>
                {category === 'Currency' && (
                  <label className="pat-field"><span>Symbol</span>
                    <input list="pat-symbols" value={symbol} maxLength={6} onChange={(e) => change({ symbol: e.target.value.replace(/[#0,.;'%]/g, '') })} />
                    <datalist id="pat-symbols">{SYMBOLS.map((s) => <option key={s} value={s} />)}</datalist>
                  </label>
                )}
                {category !== 'Scientific' && <label className="pat-check"><input type="checkbox" checked={grouping} onChange={(e) => change({ grouping: e.target.checked })} /> 1,000 separator</label>}
                {['Number', 'Currency'].includes(category) && <label className="pat-check"><input type="checkbox" checked={brackets} onChange={(e) => change({ brackets: e.target.checked })} /> Negatives in (brackets)</label>}
              </div>
            )}

            {category !== 'Custom' && (
              <div className="pat-cards">
                {cards.map((p) => (
                  <button type="button" key={p} aria-pressed={p === pattern} className={p === pattern ? 'on' : ''} onClick={() => pick(p)} onDoubleClick={() => onDone(p)}>
                    <b>{category === 'Percentage' ? formatNumberSample(0.256, p) : p.includes(';(') ? formatNumberSample(-1234567.891, p) : oneSample(p)}</b><code>{p}</code>
                  </button>
                ))}
              </div>
            )}
            {category === 'Custom' && saved === null && <p className="pat-empty">Reading…</p>}
            {category === 'Custom' && saved?.length === 0 && !form && (
              <div className="pat-empty"><b>No saved patterns yet</b><span>Write one below and save it: it is here for every report and every designer.</span></div>
            )}
            {category === 'Custom' && saved?.length > 0 && (
              <ul className="pat-saved">
                {saved.map((item) => (
                  <li key={item.id} className={item.pattern === pattern ? 'on' : ''}>
                    <button type="button" className="pat-use" onClick={() => { setPattern(item.pattern); setParts(item.kind); setForm(null); }} onDoubleClick={() => onDone(item.pattern)}>
                      <span><b>{item.name}</b><code>{item.pattern}</code></span><small>{oneSample(item.pattern)}</small></button>
                    <button type="button" className="pat-icon" title="Change" aria-label={`Change ${item.name}`} onClick={() => { setPattern(item.pattern); setParts(item.kind); setForm({ id: item.id, name: item.name }); setNote(null); }}>✎</button>
                    <button type="button" className="pat-icon danger" title="Remove" aria-label={`Remove ${item.name}`} onClick={() => remove(item)}>✕</button>
                  </li>
                ))}
              </ul>
            )}

            <section className="pat-write">
              <label className="pat-label" htmlFor="pat-text">Pattern</label>
              <div className="pat-input">
                <input id="pat-text" ref={input} value={pattern} maxLength={MAX_LENGTH} spellCheck={false} onChange={(e) => setPattern(e.target.value)} placeholder={parts === 'date' ? 'dd MMM yyyy' : '#,##0.00'}
                  onKeyDown={(e) => { if (e.key === 'Enter' && !problem) onDone(pattern.trim()); }} aria-invalid={Boolean(problem)} aria-describedby={problem ? 'pat-problem' : undefined} />
                {!form && <button type="button" className="btn sm" disabled={!pattern.trim() || Boolean(problem)} onClick={() => setForm({ name: '' })}>☆ Save as custom</button>}
              </div>
              {problem && <p className="pat-problem" id="pat-problem">{problem}</p>}
              {form && (
                <div className="pat-form">
                  <input ref={nameInput} value={form.name} maxLength={60} placeholder="Name, e.g. Bank amount" aria-label="Name of the pattern" onChange={(e) => setForm({ ...form, name: e.target.value })}
                    onKeyDown={(e) => { if (e.key === 'Enter' && form.name.trim() && pattern.trim() && !problem) save(); }} />
                  <button type="button" className="btn sm primary" disabled={busy || !form.name.trim() || !pattern.trim() || Boolean(problem)} onClick={save}>{form.id ? 'Save changes' : 'Save'}</button>
                  <button type="button" className="btn sm" onClick={() => setForm(null)}>Cancel</button>
                </div>
              )}
              {note && <p className={`pat-note ${note.kind}`} role="status">{note.text}</p>}
              <div className="pat-parts">
                <span className="pat-switch" role="group" aria-label="Building blocks">
                  {[['number', 'Number'], ['date', 'Date & time']].map(([id, label]) => <button type="button" key={id} aria-pressed={parts === id} className={parts === id ? 'on' : ''} onClick={() => setParts(id)}>{label}</button>)}
                </span>
                {(parts === 'date' ? DATE_PARTS : NUMBER_PARTS).map(([text, meaning]) => (
                  <button type="button" className="pat-chip" key={text} onClick={() => insert(text)} title={`Insert ${text}`}><code>{text.trim()}</code>{meaning}</button>
                ))}
              </div>
            </section>

            <section className="pat-results" aria-label="Result">
              <div className="pat-results-head"><span className="pat-label">Result</span>
                <input value={own} onChange={(e) => setOwn(e.target.value)} placeholder={patternKind(pattern) === 'date' ? 'Try your own: 2026-12-31' : 'Try your own: 98765.4'} aria-label="Try your own value" /></div>
              {results(pattern, own.trim()).map(([from, to], index) => <div className="pat-result" key={index}><span>{from}</span><i aria-hidden="true">→</i><b>{to}</b></div>)}
              {!patternKind(pattern) && <p className="pat-empty">{pattern.trim() ? 'The value is printed as it is.' : 'No pattern: the value is printed as it is.'}</p>}
            </section>
          </div>
        </div>
        <footer className="pat-foot">
          <button type="button" className="btn" onClick={() => onDone('')}>No pattern</button>
          <span />
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="button" className="btn primary" disabled={Boolean(problem)} onClick={() => onDone(pattern.trim())}>Apply</button>
        </footer>
      </div>
    </div>, document.body);
}
