// Setup → Fields as a list you can read: fields grouped by the first part of their name (Fields.…, GridFields.…),
// how often the design uses each one, and at the top every field the design uses that is NOT in the list (with a
// similar name when it looks misspelled) — so a missing field is seen at once and added with one click.
import { useMemo, useState } from 'react';
import { designFieldUse, similarFields } from './fieldCheck.js';

/** Display formats a field can get once (Setup → Fields → Details); a text field showing only $F{X} then uses it. */
const FORMATS = [
  ['Numbers', [['#,##0.00', '1,234.50'], ['#,##0', '1,235'], ['0.00', '1234.50'], ['#,##0.00 PKR', '1,234.50 PKR']]],
  ['Dates', [['dd-MM-yyyy', '31-12-2026'], ['dd/MM/yyyy', '31/12/2026'], ['dd MMM yyyy', '31 Dec 2026'], ['dd-MM-yyyy HH:mm', '31-12-2026 14:05'], ['MMMM yyyy', 'December 2026']]],
];
const KNOWN_FORMATS = new Set(FORMATS.flatMap(([, list]) => list.map(([p]) => p)));

/** number | date | text | empty, guessed from a sample value. */
export function kindOf(value) {
  if (value === null || value === undefined || value === '') return 'empty';
  if (typeof value === 'number') return 'number';
  if (typeof value === 'boolean') return 'yes/no';
  const text = String(value);
  if (/^-?\d+(\.\d+)?$/.test(text)) return 'number';
  if (/^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}.*)?$/.test(text)) return 'date';
  if (typeof value === 'object') return 'object';
  return 'text';
}

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
/** The sample value as the chosen format shows it (the common patterns; the server does the real formatting). */
export function formatSample(value, pattern) {
  if (!pattern || value === null || value === undefined || value === '') return '';
  const kind = kindOf(value);
  if (kind === 'number' && /[#0]/.test(pattern)) {
    const decimals = (pattern.split('.')[1] || '').replace(/[^0#]/g, '').length;
    const grouped = pattern.includes(',');
    const suffix = pattern.replace(/^[#0,.]+/, '');
    return Number(value).toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals, useGrouping: grouped }) + suffix;
  }
  if (kind === 'date') {
    const d = new Date(String(value).replace(' ', 'T'));
    if (Number.isNaN(d.getTime())) return '';
    const two = (n) => String(n).padStart(2, '0');
    return pattern.replace(/yyyy|MMMM|MMM|MM|dd|HH|mm/g, (t) => ({
      yyyy: d.getFullYear(), MMMM: MONTHS[d.getMonth()], MMM: MONTHS[d.getMonth()].slice(0, 3), MM: two(d.getMonth() + 1),
      dd: two(d.getDate()), HH: two(d.getHours()), mm: two(d.getMinutes()),
    })[t]);
  }
  return '';
}

/** One field's format: a ready-made one or your own pattern. */
function FormatPicker({ value, onChange }) {
  const [custom, setCustom] = useState(Boolean(value) && !KNOWN_FORMATS.has(value));
  return (
    <span className="fe-format">
      <select value={custom ? '__custom' : value || ''} onChange={(e) => {
        if (e.target.value === '__custom') { setCustom(true); return; }
        setCustom(false);
        onChange(e.target.value);
      }}>
        <option value="">As it is</option>
        {FORMATS.map(([group, list]) => (
          <optgroup key={group} label={group}>{list.map(([pattern, example]) => <option key={pattern} value={pattern}>{example}</option>)}</optgroup>
        ))}
        <option value="__custom">Custom pattern…</option>
      </select>
      {custom && <input className="code" value={value || ''} placeholder="#,##0.000" onChange={(e) => onChange(e.target.value)} />}
    </span>
  );
}

/**
 * columns: the field names; formats / onFormats: { Field: { label, pattern } } kept in the design;
 * sample: the first record of the last "Try with data" (shows a real value and guesses the type).
 */
export default function FieldsEditor({ columns, design, calculated = [], onChange, focusName = 'fields', formats, onFormats, sample }) {
  const [search, setSearch] = useState('');
  const [adding, setAdding] = useState('');
  const [view, setView] = useState('chips'); // chips | details | text
  const asText = view === 'text';
  const use = useMemo(() => designFieldUse(design), [design]);
  const known = new Set([...columns, ...calculated]);
  const missing = Object.keys(use).filter((f) => !known.has(f));
  const lower = {};
  columns.forEach((c) => { const k = c.toLowerCase(); lower[k] = (lower[k] || 0) + 1; });

  const add = (names) => {
    const next = [...columns];
    names.map((n) => n.trim().replace(/^\$F\{|\}$/g, '')).filter(Boolean).forEach((n) => { if (!next.includes(n)) next.push(n); });
    if (next.length !== columns.length) onChange(next);
  };
  const remove = (name) => onChange(columns.filter((c) => c !== name));
  const submit = (event) => { event.preventDefault(); add(adding.split(/[,\n]/)); setAdding(''); };

  const shown = columns.filter((c) => !search || c.toLowerCase().includes(search.toLowerCase()));
  const groups = {};
  shown.forEach((c) => { const head = c.includes('.') ? c.split('.')[0] : ''; (groups[head] = groups[head] || []).push(c); });
  const unused = columns.filter((c) => !use[c]).length;

  return (
    <div className="block fields-editor" data-focus={focusName}>
      <div className="fe-head">
        <b>Fields <small>({columns.length}{columns.length ? ` · ${columns.length - unused} used in the design` : ''})</small></b>
        <input type="search" placeholder="Find a field…" value={search} onChange={(e) => setSearch(e.target.value)} />
        <span className="fe-views" role="group" aria-label="How to show the fields">
          <button type="button" className={view === 'chips' ? 'on' : ''} onClick={() => setView('chips')}>List</button>
          {onFormats && <button type="button" className={view === 'details' ? 'on' : ''} onClick={() => setView('details')} title="Label, format and a sample value for each field">Details</button>}
          <button type="button" className={view === 'text' ? 'on' : ''} onClick={() => setView('text')}>Text</button>
        </span>
      </div>

      {missing.length > 0 && (
        <div className="fe-missing" role="alert">
          <p><b>⚠ The design uses {missing.length} field{missing.length > 1 ? 's' : ''} that {missing.length > 1 ? 'are' : 'is'} not in this list</b> — {missing.length > 1 ? 'they print' : 'it prints'} empty (red on the canvas).</p>
          <ul>
            {missing.map((name) => {
              const like = similarFields(name, columns)[0];
              return (
                <li key={name} data-field={name}>
                  <code>{name}</code>
                  <small>used {use[name].length}×{like ? ` · looks like ${like.name} (${like.why})` : ''}</small>
                  <button type="button" className="btn sm primary-soft" onClick={() => add([name])}>＋ Add</button>
                </li>
              );
            })}
          </ul>
          {missing.length > 1 && <button type="button" className="btn sm" onClick={() => add(missing)}>＋ Add all {missing.length}</button>}
          <small className="muted">Add it when the query returns it (nested names like assignTo.Name are read from inside assignTo). If it is a spelling mistake, fix the text field in Design instead.</small>
        </div>
      )}

      {view === 'details' && onFormats ? (
        <div className="fe-details">
          {!columns.length && <p className="muted fe-none">No fields yet. Press <b>Read fields</b> above, or type names below.</p>}
          {columns.length > 0 && (
            <table className="grid">
              <thead><tr><th>Field</th><th>Label <small>(table headings)</small></th><th>Format</th><th>Sample{sample ? '' : ' *'}</th><th>Used</th><th /></tr></thead>
              <tbody>
                {shown.map((c) => {
                  const info = (formats || {})[c] || {};
                  const value = sample ? sample[c] : undefined;
                  const kind = sample ? kindOf(value) : '';
                  const shownValue = value === null || value === undefined ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value);
                  const formatted = formatSample(value, info.pattern);
                  const setInfo = (patch) => {
                    const next = { ...(formats || {}) };
                    const merged = { ...info, ...patch };
                    Object.keys(merged).forEach((k) => { if (!merged[k]) delete merged[k]; });
                    if (Object.keys(merged).length) next[c] = merged; else delete next[c];
                    onFormats(next);
                  };
                  return (
                    <tr key={c} data-field={c} className={use[c] ? '' : 'fe-unused'}>
                      <td><code>{c}</code>{kind && kind !== 'empty' && <small className={`fe-kind ${kind}`}>{kind}</small>}</td>
                      <td><input value={info.label || ''} placeholder={c.split('.').pop()} onChange={(e) => setInfo({ label: e.target.value })} /></td>
                      <td><FormatPicker value={info.pattern || ''} onChange={(pattern) => setInfo({ pattern })} /></td>
                      <td className="fe-sample" title={shownValue}>{formatted ? <><b>{formatted}</b><small>{shownValue}</small></> : shownValue || <span className="muted">—</span>}</td>
                      <td>{use[c] ? `${use[c].length}×` : <span className="muted">no</span>}</td>
                      <td><button type="button" className="icon" aria-label={`Remove ${c}`} onClick={() => remove(c)}>✕</button></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          {!sample && columns.length > 0 && <small className="muted">* Press “Try with data” in 3 Query to see a real value of each field here.</small>}
          <small className="muted">The format is used wherever the field is printed on its own (a text field with only $F&#123;…&#125; and no Pattern of its own), in PDF and Excel.</small>
        </div>
      ) : asText ? (
        <textarea className="code" rows={4} value={columns.join(', ')}
          onChange={(e) => onChange(e.target.value.split(',').map((s) => s.trim()).filter(Boolean))} />
      ) : (
        <div className="fe-groups">
          {!columns.length && <p className="muted fe-none">No fields yet. Press <b>Read fields</b> above, or type names below.</p>}
          {Object.entries(groups).sort(([a], [b]) => a.localeCompare(b)).map(([head, list]) => (
            <details key={head || '-'} open={search || Object.keys(groups).length < 4 ? true : undefined} className="fe-group">
              <summary>{head ? <><code>{head}.</code>…</> : 'Top level'} <small>({list.length})</small></summary>
              <ul className="fe-chips">
                {list.map((c) => (
                  <li key={c} data-field={c} className={`${use[c] ? 'used' : 'unused'} ${lower[c.toLowerCase()] > 1 ? 'dup' : ''}`}
                    title={`${c}${use[c] ? ` — used ${use[c].length}× in the design` : ' — not used in the design'}${lower[c.toLowerCase()] > 1 ? ' — another field differs only in capitals: they are 2 separate fields (capitals must match exactly)' : ''}`}>
                    <span>{head ? c.slice(head.length + 1) : c}</span>
                    {use[c] ? <small>{use[c].length}×</small> : null}
                    <button type="button" aria-label={`Remove ${c}`} onClick={() => remove(c)}>×</button>
                  </li>
                ))}
              </ul>
            </details>
          ))}
        </div>
      )}

      <form className="fe-add" onSubmit={submit}>
        <input value={adding} onChange={(e) => setAdding(e.target.value)} placeholder="Add fields: name, or several separated by commas (e.g. assignTo.Name)" />
        <button type="submit" className="btn sm" disabled={!adding.trim()}>＋ Add</button>
      </form>
      <p className="fe-legend" hidden={!columns.length}><span className="used">used in the design</span><span className="unused">not used</span><span className="dup">same name, other capitals</span></p>
    </div>
  );
}
