import { useMemo, useRef, useState } from 'react';
import { BUILTIN_VARIABLES, safeMarkupHtml } from './designModel.js';
import { missingFields, similarFields, knownFields } from './fieldCheck.js';
import HighlightArea, { knownNames } from './ExprHighlight.jsx';
import RichTextEditor from './RichTextEditor.jsx';
import { HAS_DATA, htmlToPlain, isJavaExpression, plainToHtml } from './richText.js';

const TOKEN = /\$[FPV]\{[^{}]+\}/;

/** Java-style snippets the service understands after a leading "=" (see jexpr.py). */
const FUNCTIONS = [
  ['IF … ? … : …', '$F{Amount} > 0 ? "Yes" : "No"', 'Choose one of two values'],
  ['Join text', '$F{FirstName} + " " + $F{LastName}', 'Put texts together with +'],
  ['Number format', 'new java.text.DecimalFormat("#,##0.00").format($F{Amount})', 'Format a number'],
  ['Date format', 'new java.text.SimpleDateFormat("dd-MM-yyyy").format($F{Date})', 'Format a date'],
  ['Today', 'new java.util.Date()', 'The current date and time'],
  ['Null check', '$F{Name} == null ? "" : $F{Name}', 'Empty text instead of null'],
  ['Upper case', '$F{Name}.toUpperCase()', 'Text in capitals'],
  ['Trim', '$F{Name}.trim()', 'Remove spaces at both ends'],
  ['Substring', '$F{Name}.substring(0, 10)', 'Part of a text'],
  ['Equals', '$F{Status}.equals("Approved")', 'Compare a text'],
  ['Parse number', 'Double.parseDouble($F{Amount})', 'Text to number'],
  ['Round', 'Math.round($F{Amount})', 'Round a number'],
];

/**
 * Like Jaspersoft Studio's Expression Editor: the expression on top, and Fields, Parameters, Variables and
 * functions below; double-click (or Insert) puts the item at the cursor.
 * For a Static Text, inserting a field turns it into a Text Field (a static text cannot show data).
 */
export default function ExpressionEditor({ element, report, design, onSave, onClose, onAddField, field = null }) {
  // field = { key, label }: edit that property only (e.g. printWhenExpression), as a plain expression
  const isStatic = !field && element.type === 'staticText';
  const [text, setText] = useState(field ? element[field.key] || '' : isStatic ? element.text || '' : element.expression || '');
  // Formatted text: some words bold / coloured, data as chips between the words (markup HTML)
  const [formatted, setFormatted] = useState(!field && element.markup === 'html' && !isJavaExpression(isStatic ? element.text : element.expression));
  const rich = useRef(null);
  const [category, setCategory] = useState('fields');
  const [filter, setFilter] = useState('');
  const [picked, setPicked] = useState(null);
  const area = useRef(null);

  const groups = useMemo(() => ({
    fields: { label: 'Fields', items: report.columns.map((c) => ({ name: c, insert: `$F{${c}}`, note: 'Field from the query' })) },
    calc: { label: 'Calculated Fields', items: design.calculatedFields.map((c) => ({ name: c.name, insert: `$F{${c.name}}`, note: c.expression || 'Calculated field' })) },
    params: { label: 'Parameters', items: report.parameters.map((p) => ({ name: p.name, insert: `$P{${p.name}}`, note: `${p.label || p.name} · ${p.type}` })) },
    vars: {
      label: 'Variables',
      items: [
        ...design.variables.map((v) => ({ name: v.name, insert: `$V{${v.name}}`, note: `${v.calculation} of ${v.field || '—'}` })),
        ...BUILTIN_VARIABLES.map((v) => ({ name: v, insert: `$V{${v}}`, note: 'Built-in variable' })),
      ],
    },
    functions: { label: 'Functions', items: FUNCTIONS.map(([name, insert, note]) => ({ name, insert, note, java: true })) },
    mine: {
      label: 'My Functions',
      items: (design.functions || []).map((f) => ({ name: f.name, insert: `${f.name}(${(f.params || []).join(', ')})`, java: true,
        note: `${f.description ? `${f.description}. ` : ''}Replace ${(f.params || []).join(', ') || 'the inputs'} with fields or values, e.g. $F{…}. Formula: ${f.body}` })),
    },
  }), [report, design]);

  const items = groups[category].items.filter((item) => !filter || item.name.toLowerCase().includes(filter.toLowerCase()));
  const willConvert = isStatic && (formatted ? HAS_DATA.test(text) : TOKEN.test(text));
  const missing = missingFields(text, report, design);
  const typed = filter.trim().replace(/^\$F\{|\}$/g, '');
  const filterSimilar = category === 'fields' && typed && !items.length ? similarFields(typed, knownFields(report, design)) : [];
  /** Put `name` in place of $F{old} everywhere in the expression. */
  const useInstead = (old, name) => setText(text.split(`$F{${old}}`).join(`$F{${name}}`));
  const javaMode = text.trimStart().startsWith('=');
  const known = useMemo(() => knownNames(report, design, BUILTIN_VARIABLES), [report, design]);

  const insert = (item) => {
    if (formatted) {
      rich.current?.insertToken(item.java ? `{=${item.insert}}` : item.insert);
      return;
    }
    const node = area.current;
    let value = item.insert;
    let current = text;
    if (item.java && !current.trimStart().startsWith('=') && !isStatic) {
      current = current.trim() ? `=${current}` : '=';
    }
    const start = node ? node.selectionStart + (current.length - text.length) : current.length;
    const end = node ? node.selectionEnd + (current.length - text.length) : current.length;
    if (!item.java && javaMode && current.slice(0, start).trim() !== '=' && /[\w)}"]\s*$/.test(current.slice(0, start))) value = ` + ${value}`;
    const next = current.slice(0, start) + value + current.slice(end);
    setText(next);
    window.requestAnimationFrame(() => {
      if (!node) return;
      node.focus();
      const caret = start + value.length;
      node.setSelectionRange(caret, caret);
    });
  };

  const save = () => {
    if (field) { onSave({ [field.key]: text }); return; }
    const markup = formatted ? { markup: 'html' } : element.markup === 'html' ? { markup: 'none' } : {};
    if (isStatic && willConvert) onSave({ type: 'textField', expression: text, pattern: element.pattern || '', blankWhenNull: true, text: undefined, ...markup });
    else if (isStatic) onSave({ text, ...markup });
    else onSave({ expression: text, ...markup });
  };
  const switchMode = (on) => {
    if (on === formatted) return;
    if (on) {
      setText(element.markup === 'styled' ? safeMarkupHtml(text, 'styled') : plainToHtml(text));
    } else {
      if (!window.confirm('Back to plain text? The bold, italic, colours and sizes of the words are removed; the text stays.')) return;
      setText(htmlToPlain(text));
    }
    setFormatted(on);
  };

  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal expr-editor" role="dialog" aria-label="Expression Editor" onKeyDown={(e) => { e.stopPropagation(); if (e.key === 'Escape') onClose(); if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) save(); }}>
        <div className="wizard-head">
          <h3>{field ? field.label : isStatic ? 'Static Text' : 'Expression Editor'}</h3>
          <p className="muted">
            {field ? 'Printed only when this is true, e.g. $F{Status}.equals("Approved") or $V{PAGE_NUMBER} == 1. Empty = always. Double-click a field on the left to insert it.'
              : isStatic
              ? 'Type the text. Insert a field, parameter or variable to show data — the Static Text then becomes a Text Field.'
              : 'Text with $F{…} / $P{…} / $V{…} is filled in as written (e.g. “Branch: $F{Branch}”). Start with = for a Java-style expression (e.g. =$F{A} + " " + $F{B}).'}
          </p>
        </div>
        {!field && <div className="expr-mode" role="tablist" aria-label="How the text is written">
          <button type="button" role="tab" aria-selected={!formatted} className={!formatted ? 'on' : ''} onClick={() => switchMode(false)}>
            {isStatic ? 'Plain text' : 'Text / expression'}
          </button>
          <button type="button" role="tab" aria-selected={formatted} className={formatted ? 'on' : ''} disabled={!formatted && javaMode}
            title={javaMode ? 'A Java expression (starting with =) makes its own text. Remove the = to format words, and put formulas in ƒx.' : 'Bold, italic, colour on some words; data between the words'}
            onClick={() => switchMode(true)}><b>B</b> <i>I</i> <u>U</u> Formatted text</button>
        </div>}
        {formatted ? (
          <RichTextEditor ref={rich} value={text} onChange={setText} autoFocus rows={6} label="Formatted text" />
        ) : (
          <HighlightArea ref={area} className="expr-text" rows={5} autoFocus value={text} known={known} onChange={(e) => setText(e.target.value)} />
        )}
        {formatted ? (
          <p className="hl-legend"><span className="rt-chip rt-F">field</span><span className="rt-chip rt-P">parameter</span><span className="rt-chip rt-V">variable</span><span className="rt-chip rt-fx">ƒx formula</span>
            <span className="hl-plain">Double-click on the left to put one where the cursor is. Functions go in as ƒx formulas.</span></p>
        ) : <p className="hl-legend">
          <span className="tk-field">$F&#123;field&#125;</span><span className="tk-param">$P&#123;parameter&#125;</span><span className="tk-var">$V&#123;variable&#125;</span>
          <span className="hl-plain">plain text</span>{javaMode && <><span className="tk-string">"text"</span><span className="tk-number">123</span><span className="tk-call">method()</span></>}
          <span className="tk-field tk-missing">not in the report</span>
        </p>}
        <div className="expr-browser">
          <ul className="expr-cats">
            {Object.entries(groups).map(([key, group]) => (
              <li key={key} className={category === key ? 'on' : ''} onClick={() => { setCategory(key); setPicked(null); }}>{group.label} <small>({group.items.length})</small></li>
            ))}
          </ul>
          <div className="expr-items">
            <input type="search" placeholder="Filter…" value={filter} onChange={(e) => setFilter(e.target.value)} />
            <ul>
              {items.map((item) => (
                <li key={item.name} className={picked === item ? 'on' : ''} onClick={() => setPicked(item)} onDoubleClick={() => insert(item)} title="Double-click to insert">
                  {category === 'functions' || category === 'mine' ? item.name : item.insert}
                </li>
              ))}
              {!items.length && category === 'fields' && !report.columns.length && <li className="muted">No fields yet — press “Read fields” on the Setup tab, or add one: type its name in Filter.</li>}
              {!items.length && category === 'fields' && report.columns.length > 0 && typed && (
                <li className="expr-nomatch">
                  <span>No field is called <b>{typed}</b> (the report has {report.columns.length} fields).</span>
                  {filterSimilar.map((s) => (
                    <button key={s.name} type="button" className="btn sm" onClick={() => { setFilter(s.name); setPicked(null); }} title={s.why}>Did you mean {s.name}?</button>
                  ))}
                  {onAddField && <button type="button" className="btn sm primary-soft" onClick={() => onAddField(typed)}>＋ Add “{typed}” to Fields</button>}
                  <small className="muted">Adding it here is enough when the query returns it (also nested, like assignTo.Name). Otherwise add it to the $project of the query too.</small>
                </li>
              )}
              {!items.length && category === 'mine' && <li className="muted">No custom functions yet: make them in Formulas → Custom Functions (e.g. FullName, WithTax).</li>}
              {!items.length && category !== 'fields' && category !== 'mine' && <li className="muted">Nothing here yet.</li>}
            </ul>
          </div>
          <div className="expr-info">
            {picked ? (
              <>
                <b>{picked.name}</b>
                <code>{picked.insert}</code>
                <small>{picked.note}</small>
                <button className="btn sm" onClick={() => insert(picked)}>Insert ⤵</button>
              </>
            ) : <small className="muted">Click an item to see it; double-click to insert it at the cursor.</small>}
          </div>
        </div>
        {missing.length > 0 && (
          <div className="expr-missing" role="alert">
            {missing.map((m) => (
              <div key={m.name}>
                <p><b>⚠ $F&#123;{m.name}&#125; is not a field of this report</b> — it prints empty. Where: Setup → 4 Fields.</p>
                <div className="row">
                  {m.similar.map((s) => <button key={s.name} type="button" className="btn sm" title={s.why} onClick={() => useInstead(m.name, s.name)}>Use {s.name} instead <small>({s.why})</small></button>)}
                  {onAddField && <button type="button" className="btn sm primary-soft" onClick={() => onAddField(m.name)}>＋ Add {m.name} to Fields</button>}
                </div>
              </div>
            ))}
          </div>
        )}
        {willConvert && <p className="expr-note">This contains data ($F/$P/$V), so it will be saved as a <b>Text Field</b>.</p>}
        <div className="row wizard-buttons">
          <span className="muted" style={{ marginRight: 'auto', fontSize: 12 }}>Ctrl+Enter = OK</span>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" onClick={save}>OK</button>
        </div>
      </div>
    </div>
  );
}
