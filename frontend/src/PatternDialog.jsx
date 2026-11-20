import { useState } from 'react';

/** Text Field → Pattern: Jaspersoft Studio's "Format Pattern" dialog (Java DecimalFormat / SimpleDateFormat). */

const CATEGORIES = ['Number', 'Percentage', 'Currency', 'Scientific', 'Time', 'Date'];
const DATES = ['dd/MM/yyyy', 'dd-MM-yyyy', 'dd MMM yyyy', 'dd-MMM-yyyy', 'MMM-yy', 'MMMM yyyy', 'dd MMMM yyyy', 'yyyy-MM-dd', 'MM/dd/yyyy',
  'EEEE, dd MMMM yyyy', 'dd/MM/yyyy HH:mm', 'dd/MM/yyyy hh:mm a', 'yyyy-MM-dd HH:mm:ss'];
const TIMES = ['HH:mm', 'HH:mm:ss', 'hh:mm a', 'hh:mm:ss a', 'H:mm'];
const SAMPLE_NUMBER = -1234567.891;
const SAMPLE_DATE = new Date(2026, 10, 19, 14, 5, 9);

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

/** What kind of pattern this is (to open the dialog on the right category). */
export function patternCategory(pattern) {
  const p = String(pattern || '');
  if (!p) return 'Number';
  const bare = p.replace(/'[^']*'/g, '');
  if (/[#0]/.test(bare)) {
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

/** A Java number pattern applied in the browser, for the sample only (the server does the real formatting). */
export function formatNumberSample(value, pattern) {
  const [positive, negative] = String(pattern).split(';');
  const science = positive.match(/^(.*?)([#0,.]+)E(0+)(.*)$/);
  const m = science ? [null, science[1], science[2], science[4]] : positive.match(/^(.*?)([#0,.]+)(.*)$/);
  if (!m) return String(value);
  const [, prefix, core, suffix] = m;
  const [intPart, frac = ''] = core.split('.');
  const decimals = frac.length;
  let number = Math.abs(value) * (/%/.test(prefix + suffix) ? 100 : 1);
  let exponent = '';
  if (science) {
    const power = number ? Math.floor(Math.log10(number)) : 0;
    number /= 10 ** power;
    exponent = `E${power < 0 ? '-' : ''}${String(Math.abs(power)).padStart(science[3].length, '0')}`;
  }
  let text = number.toFixed(decimals);
  const minFrac = (frac.match(/0/g) || []).length;
  if (decimals > minFrac) text = text.replace(/0+$/, (z) => z.slice(0, Math.max(0, z.length - (decimals - minFrac)))).replace(/\.$/, '');
  if (intPart.includes(',')) text = text.replace(/^(\d+)/, (d) => d.replace(/\B(?=(\d{3})+(?!\d))/g, ','));
  text += exponent;
  if (value < 0) {
    if (negative) {
      const n = negative.match(/^(.*?)[#0,.]+(.*)$/);
      return n ? `${n[1]}${text}${n[2]}` : `-${prefix}${text}${suffix}`;
    }
    return `-${prefix}${text}${suffix}`;
  }
  return `${prefix}${text}${suffix}`;
}

/** A SimpleDateFormat pattern applied in the browser, for the sample only. */
export function formatDateSample(date, pattern) {
  const pad = (n, w) => String(n).padStart(w, '0');
  return String(pattern).replace(/'([^']*)'|y+|M+|d+|H+|h+|m+|s+|E+|a/g, (token, literal) => {
    if (literal !== undefined) return literal || "'";
    const n = token.length;
    switch (token[0]) {
      case 'y': return n === 2 ? pad(date.getFullYear() % 100, 2) : String(date.getFullYear());
      case 'M': return n >= 4 ? MONTHS[date.getMonth()] : n === 3 ? MONTHS[date.getMonth()].slice(0, 3) : pad(date.getMonth() + 1, n);
      case 'd': return pad(date.getDate(), n);
      case 'H': return pad(date.getHours(), n);
      case 'h': return pad(date.getHours() % 12 || 12, n);
      case 'm': return pad(date.getMinutes(), n);
      case 's': return pad(date.getSeconds(), n);
      case 'E': return n >= 4 ? DAYS[date.getDay()] : DAYS[date.getDay()].slice(0, 3);
      case 'a': return date.getHours() < 12 ? 'AM' : 'PM';
      default: return token;
    }
  });
}

export function patternSample(pattern) {
  if (!pattern) return '(no pattern: the value as it is)';
  const category = patternCategory(pattern);
  try {
    if (['Date', 'Time'].includes(category)) return formatDateSample(SAMPLE_DATE, pattern);
    return `${formatNumberSample(SAMPLE_NUMBER, pattern)}   ·   ${formatNumberSample(1234.5, pattern)}`;
  } catch {
    return '—';
  }
}

export default function PatternDialog({ value, onDone, onClose }) {
  const [category, setCategory] = useState(() => patternCategory(value));
  const [pattern, setPattern] = useState(value || '');
  const [decimals, setDecimals] = useState(() => ((String(value || '').split(';')[0].split('.')[1] || '').match(/[#0]/g) || []).length || 2);
  const [grouping, setGrouping] = useState(() => !value || String(value).includes(','));
  const [brackets, setBrackets] = useState(() => String(value || '').includes(';('));
  const [symbol, setSymbol] = useState(() => (patternCategory(value) === 'Currency' ? String(value).match(/^([^#0]*?)\s*[#0]/)?.[1] || 'Rs' : 'Rs'));

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
    if (next.category !== undefined) setCategory(next.category);
    if (next.decimals !== undefined) setDecimals(next.decimals);
    if (next.grouping !== undefined) setGrouping(next.grouping);
    if (next.brackets !== undefined) setBrackets(next.brackets);
    if (next.symbol !== undefined) setSymbol(next.symbol);
    const built = build(next);
    if (built !== null) setPattern(built);
    else if (next.category === 'Date') setPattern(DATES[0]);
    else if (next.category === 'Time') setPattern(TIMES[0]);
  };
  const numeric = ['Number', 'Percentage', 'Currency', 'Scientific'].includes(category);

  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal pattern-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Format pattern">
        <h3>Format Pattern</h3>
        <p className="hint">Specify the format pattern in Java style (DecimalFormat for numbers, SimpleDateFormat for dates).</p>
        <div className="pattern-body">
          <ul className="pattern-cats" role="listbox" aria-label="Category">
            {CATEGORIES.map((c) => (
              <li key={c} role="option" aria-selected={c === category} className={c === category ? 'on' : ''} onClick={() => change({ category: c })}>{c}</li>
            ))}
          </ul>
          <div className="pattern-options">
            {numeric && (
              <>
                <label className="prop-row"><span>Decimal places</span>
                  <input type="number" min={0} max={10} value={decimals} onChange={(e) => change({ decimals: Math.max(0, Math.min(10, Number(e.target.value) || 0)) })} />
                </label>
                {category !== 'Scientific' && <label className="check-row"><input type="checkbox" checked={grouping} onChange={(e) => change({ grouping: e.target.checked })} /> Use 1000 separator (,)</label>}
                {category === 'Currency' && (
                  <label className="prop-row"><span>Currency symbol</span>
                    <input value={symbol} maxLength={6} onChange={(e) => change({ symbol: e.target.value.replace(/[#0,.;'%]/g, '') })} />
                  </label>
                )}
                {['Number', 'Currency'].includes(category) && (
                  <label className="check-row"><input type="checkbox" checked={brackets} onChange={(e) => change({ brackets: e.target.checked })} /> Negative numbers in brackets (1,234.00)</label>
                )}
              </>
            )}
            {(category === 'Date' || category === 'Time') && (
              <div className="pattern-presets">
                {(category === 'Date' ? DATES : TIMES).map((p) => (
                  <button type="button" key={p} className={`btn sm ${p === pattern ? 'primary' : ''}`} onClick={() => setPattern(p)}>
                    <code>{p}</code> <small>{formatDateSample(SAMPLE_DATE, p)}</small>
                  </button>
                ))}
              </div>
            )}
            <label className="prop-row"><span>Pattern</span>
              <input value={pattern} spellCheck={false} onChange={(e) => setPattern(e.target.value)} placeholder="#,##0.00" />
            </label>
            <div className="pattern-sample"><span>Sample</span><b>{patternSample(pattern)}</b></div>
          </div>
        </div>
        <div className="row">
          <button type="button" className="btn" onClick={() => onDone('')}>No pattern</button>
          <span style={{ flex: 1 }} />
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="button" className="btn primary" onClick={() => onDone(pattern.trim())}>Finish</button>
        </div>
      </div>
    </div>
  );
}
