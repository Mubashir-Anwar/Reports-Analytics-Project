import { useMemo, useRef, useState } from 'react';
import { BUILTIN_VARIABLES } from './designModel.js';

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
export default function ExpressionEditor({ element, report, design, onSave, onClose }) {
  const isStatic = element.type === 'staticText';
  const [text, setText] = useState(isStatic ? element.text || '' : element.expression || '');
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
  }), [report, design]);

  const items = groups[category].items.filter((item) => !filter || item.name.toLowerCase().includes(filter.toLowerCase()));
  const willConvert = isStatic && TOKEN.test(text);
  const javaMode = text.trimStart().startsWith('=');

  const insert = (item) => {
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
    if (isStatic && willConvert) onSave({ type: 'textField', expression: text, pattern: element.pattern || '', blankWhenNull: true, text: undefined });
    else if (isStatic) onSave({ text });
    else onSave({ expression: text });
  };

  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal expr-editor" role="dialog" aria-label="Expression Editor" onKeyDown={(e) => { e.stopPropagation(); if (e.key === 'Escape') onClose(); if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) save(); }}>
        <div className="wizard-head">
          <h3>{isStatic ? 'Static Text' : 'Expression Editor'}</h3>
          <p className="muted">
            {isStatic
              ? 'Type the text. Insert a field, parameter or variable to show data — the Static Text then becomes a Text Field.'
              : 'Text with $F{…} / $P{…} / $V{…} is filled in as written (e.g. “Branch: $F{Branch}”). Start with = for a Java-style expression (e.g. =$F{A} + " " + $F{B}).'}
          </p>
        </div>
        <textarea ref={area} className="code expr-text" rows={5} spellCheck={false} autoFocus value={text} onChange={(e) => setText(e.target.value)} />
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
                  {category === 'functions' ? item.name : item.insert}
                </li>
              ))}
              {!items.length && <li className="muted">{category === 'fields' ? 'No fields yet — Detect fields on the Setup tab.' : 'Nothing here yet.'}</li>}
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
