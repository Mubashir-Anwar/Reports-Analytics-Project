// Setup → Fields as a list you can read: fields grouped by the first part of their name (Fields.…, GridFields.…),
// how often the design uses each one, and at the top every field the design uses that is NOT in the list (with a
// similar name when it looks misspelled) — so a missing field is seen at once and added with one click.
import { useMemo, useState } from 'react';
import { designFieldUse, similarFields } from './fieldCheck.js';

export default function FieldsEditor({ columns, design, calculated = [], onChange, focusName = 'fields' }) {
  const [search, setSearch] = useState('');
  const [adding, setAdding] = useState('');
  const [asText, setAsText] = useState(false);
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
        <button type="button" className="btn sm" onClick={() => setAsText(!asText)}>{asText ? 'Show as list' : 'Edit as text'}</button>
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

      {asText ? (
        <textarea className="code" rows={4} value={columns.join(', ')}
          onChange={(e) => onChange(e.target.value.split(',').map((s) => s.trim()).filter(Boolean))} />
      ) : (
        <div className="fe-groups">
          {!columns.length && <p className="muted">No fields yet. Press <b>Read fields</b> above, or type names below.</p>}
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
      <p className="fe-legend"><span className="used">used in the design</span><span className="unused">not used</span><span className="dup">same name, other capitals</span></p>
    </div>
  );
}
