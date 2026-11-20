// The ribbon's Help / Formulas / Review popups, like Excel's: Help, Keyboard Shortcuts, Function List, What's New,
// Feedback, Contact Support and Check report. They only show information (and copy text); nothing here changes the report.
import { useEffect, useMemo, useState } from 'react';
import { checkSetup } from './errorHelp.js';

/** What the expression engine (jexpr.py) really understands, grouped like Excel's Function Library. */
export const FUNCTION_LIBRARY = [
  ['Logical', [
    ['a ? b : c', '$F{Amount} > 0 ? "Credit" : "Debit"', 'IF: choose one of two values'],
    ['&&  ||  !', '$F{Amount} > 0 && $F{Status}.equals("OK")', 'AND, OR, NOT'],
    ['== null', '$F{Name} == null ? "" : $F{Name}', 'Empty text instead of null'],
    ['==  !=  <  >  <=  >=', '$F{Qty} >= 10', 'Compare two values'],
  ]],
  ['Text', [
    ['+', '$F{FirstName} + " " + $F{LastName}', 'Join texts'],
    ['toUpperCase()', '$F{Name}.toUpperCase()', 'Text in capitals'],
    ['toLowerCase()', '$F{Name}.toLowerCase()', 'Text in small letters'],
    ['trim()', '$F{Name}.trim()', 'Remove spaces at both ends'],
    ['substring(a, b)', '$F{Name}.substring(0, 10)', 'Part of a text'],
    ['length()', '$F{Name}.length()', 'Number of characters'],
    ['equals(x)', '$F{Status}.equals("Approved")', 'Same text?'],
    ['equalsIgnoreCase(x)', '$F{Status}.equalsIgnoreCase("approved")', 'Same text, any case'],
    ['contains(x)', '$F{Name}.contains("Ltd")', 'Has this text?'],
    ['startsWith(x) / endsWith(x)', '$F{Code}.startsWith("PK")', 'Starts or ends with'],
    ['indexOf(x)', '$F{Email}.indexOf("@")', 'Position of a text (-1 = none)'],
    ['replace(a, b)', '$F{Phone}.replace("-", "")', 'Replace every a by b'],
    ['replaceAll(regex, b)', '$F{Phone}.replaceAll("[^0-9]", "")', 'Replace with a regular expression'],
    ['matches(regex)', '$F{Code}.matches("[A-Z]{3}")', 'Does the text match?'],
    ['split(regex)', '$F{Tags}.split(",")', 'Cut a text into a list'],
    ['isEmpty()', '$F{Name}.isEmpty()', 'Empty text?'],
  ]],
  ['Math', [
    ['+  -  *  /  %', '$F{Price} * $F{Qty}', 'Arithmetic'],
    ['Math.round(x)', 'Math.round($F{Amount})', 'Round to a whole number'],
    ['Math.abs(x)', 'Math.abs($F{Balance})', 'Without the minus sign'],
    ['Math.floor(x) / Math.ceil(x)', 'Math.floor($F{Amount})', 'Round down / up'],
    ['Math.max(a, b) / Math.min(a, b)', 'Math.max($F{A}, $F{B})', 'Bigger / smaller value'],
    ['Math.pow(x, y)', 'Math.pow($F{Rate}, 2)', 'x to the power y'],
    ['Math.sqrt(x)', 'Math.sqrt($F{Area})', 'Square root'],
    ['Double.parseDouble(t)', 'Double.parseDouble($F{AmountText})', 'Text to number'],
    ['Integer.parseInt(t)', 'Integer.parseInt($F{QtyText})', 'Text to whole number'],
  ]],
  ['Date & time', [
    ['new java.util.Date()', 'new java.util.Date()', 'Now (UTC)'],
    ['getYear() + 1900', '$F{Date}.getYear() + 1900', 'Year'],
    ['getMonth() + 1', '$F{Date}.getMonth() + 1', 'Month (1-12)'],
    ['getDate()', '$F{Date}.getDate()', 'Day of the month'],
    ['getHours() / getMinutes()', '$F{Date}.getHours()', 'Hour / minute'],
    ['getTime()', '$F{Date}.getTime()', 'Milliseconds since 1970'],
    ['SimpleDateFormat', 'new java.text.SimpleDateFormat("dd-MM-yyyy").format($F{Date})', 'Date as text'],
  ]],
  ['Format', [
    ['DecimalFormat', 'new java.text.DecimalFormat("#,##0.00").format($F{Amount})', 'Number as text'],
    ['String.valueOf(x)', 'String.valueOf($F{Qty})', 'Anything as text'],
  ]],
  ['Report', [
    ['$F{Name}', '$F{Amount}', 'A field of the current record'],
    ['$P{Name}', '$P{StartDate}', 'A parameter value'],
    ['$V{PAGE_NUMBER}', '"Page " + $V{PAGE_NUMBER}', 'Page number (Evaluation time: Report for "of N")'],
    ['$V{REPORT_COUNT}', '$V{REPORT_COUNT}', 'Records so far'],
    ['$V{Name}', '$V{TotalAmount}', 'A variable (Sum, Count, Average…)'],
  ]],
];

export const SHORTCUTS = [
  ['File', [['Ctrl + S', 'Save in the workspace']]],
  ['Edit', [
    ['Ctrl + Z', 'Undo'], ['Ctrl + Y  /  Ctrl + Shift + Z', 'Redo'], ['Ctrl + C', 'Copy'], ['Ctrl + X', 'Cut'], ['Ctrl + V', 'Paste'],
    ['Ctrl + D', 'Duplicate'], ['Delete / Backspace', 'Delete the selected elements'], ['Ctrl + L', 'Lock / unlock'],
  ]],
  ['Select', [
    ['Ctrl + A', 'Select all in the band'], ['Shift / Ctrl + click', 'Add to the selection'], ['Drag on empty space', 'Select with a box'],
    ['Esc', 'Stop the insert tool, or select the band'],
  ]],
  ['Move and size', [
    ['Arrow keys', 'Move 1 px'], ['Shift + arrow', 'Move 10 px'], ['Ctrl + arrow', 'Move one grid step'], ['Alt while dragging', 'Move freely (no snapping)'],
  ]],
  ['View', [['Ctrl + +', 'Zoom in'], ['Ctrl + −', 'Zoom out'], ['Ctrl + 0', 'Zoom 100%'], ['Ctrl + mouse wheel', 'Zoom'], ['Double-click a ribbon tab', 'Hide / show the ribbon']]],
  ['Text', [['Double-click a text', 'Edit it on the canvas'], ['Enter', 'Finish editing'], ['Shift + Enter', 'New line'], ['Esc', 'Cancel editing']]],
];

const WHATS_NEW = [
  ['Ribbon', 'New Page Layout, Formulas, Review, Share and Help tabs, like Excel and Word.'],
  ['Appearance', 'Position Type, Stretch Type, Transparent, Print Repeated Values, Remove Line When Blank, Print In First Whole Band, Print When Group Changes.'],
  ['Text', 'Text Adjust (Cut / Stretch / Scale font), rotation, line spacing, indents, paragraph spacing and tab stops.'],
  ['Patterns', 'A Pattern dialog (Number, Percentage, Currency, Scientific, Date, Time) and Pattern Expression.'],
  ['Folders', 'The same report ID can be used in several folders; run it with ?Folder=… .'],
];

function Modal({ title, wide, onClose, children, buttons }) {
  useEffect(() => {
    const onKey = (event) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="modal-back" onClick={onClose}>
      <div className={`modal help-modal ${wide ? 'wide' : ''}`} onClick={(e) => e.stopPropagation()} role="dialog" aria-label={title}>
        <h3>{title}</h3>
        {children}
        <div className="row">{buttons}<button type="button" className="btn primary" onClick={onClose}>Close</button></div>
      </div>
    </div>
  );
}

const copyText = (text) => navigator.clipboard?.writeText(text).catch(() => {});

function FunctionList({ onClose }) {
  const [group, setGroup] = useState(FUNCTION_LIBRARY[0][0]);
  const [filter, setFilter] = useState('');
  const [copied, setCopied] = useState('');
  const rows = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const all = FUNCTION_LIBRARY.flatMap(([g, items]) => items.map((item) => [g, ...item]));
    return q ? all.filter(([, name, , note]) => `${name} ${note}`.toLowerCase().includes(q)) : all.filter(([g]) => g === group);
  }, [group, filter]);
  return (
    <Modal title="Function List" wide onClose={onClose}>
      <p className="hint">What an expression can use. Start a Java expression with <code>=</code> in a Text Field, or write it in the Expression editor. Click <b>Copy</b> and paste the example.</p>
      <input type="search" placeholder="Search functions…" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Search functions" />
      <div className="help-split">
        <ul className="pattern-cats" role="listbox" aria-label="Category">
          {FUNCTION_LIBRARY.map(([g]) => <li key={g} role="option" aria-selected={!filter && g === group} className={!filter && g === group ? 'on' : ''} onClick={() => { setFilter(''); setGroup(g); }}>{g}</li>)}
        </ul>
        <table className="help-table">
          <thead><tr><th>Function</th><th>What it does</th><th>Example</th><th /></tr></thead>
          <tbody>
            {rows.map(([g, name, example, note]) => (
              <tr key={`${g}${name}`}>
                <td><code>{name}</code></td><td>{note}</td><td><code className="help-example">{example}</code></td>
                <td><button type="button" className="btn sm" onClick={() => { copyText(example); setCopied(example); }}>{copied === example ? 'Copied' : 'Copy'}</button></td>
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={4} className="muted">No function matches “{filter}”.</td></tr>}
          </tbody>
        </table>
      </div>
    </Modal>
  );
}

function Shortcuts({ onClose }) {
  return (
    <Modal title="Keyboard Shortcuts" wide onClose={onClose}>
      <div className="help-columns">
        {SHORTCUTS.map(([group, keys]) => (
          <section key={group}>
            <h4>{group}</h4>
            <dl className="help-keys">{keys.map(([k, what]) => <div key={k}><dt><kbd>{k}</kbd></dt><dd>{what}</dd></div>)}</dl>
          </section>
        ))}
      </div>
    </Modal>
  );
}

function HelpTopics({ onClose, onOpen }) {
  const topics = [
    ['1. Data', 'Data → Dataset & Query: pick the data adapter, write the query, then Read Fields.'],
    ['2. Design', 'Insert → drag Text Fields and Static Texts onto the bands. Home and Format change the look; Properties has every option.'],
    ['3. Page', 'Page Layout → page size, orientation, margins and how the title and summary print.'],
    ['4. Expressions', 'Formulas → Expression editor and Function List: $F{field}, $P{parameter}, $V{variable}.'],
    ['5. Check', 'Review → Check report lists every problem the designer can see; Preview runs it with real records.'],
    ['6. Share', 'Save keeps it in your workspace. Publish copies it to a server folder; Share → Copy link gives the run URL.'],
  ];
  return (
    <Modal title="Help" onClose={onClose} buttons={<><button type="button" className="btn" onClick={() => onOpen('shortcuts')}>Keyboard Shortcuts</button><button type="button" className="btn" onClick={() => onOpen('functions')}>Function List</button></>}>
      <p className="hint">Making a report, step by step. Hover over any ribbon button to see what it does.</p>
      <dl className="help-topics">{topics.map(([t, text]) => <div key={t}><dt>{t}</dt><dd>{text}</dd></div>)}</dl>
    </Modal>
  );
}

function WhatsNew({ onClose }) {
  return (
    <Modal title="What's New" onClose={onClose}>
      <dl className="help-topics">{WHATS_NEW.map(([t, text]) => <div key={t}><dt>{t}</dt><dd>{text}</dd></div>)}</dl>
    </Modal>
  );
}

/** Feedback and Contact Support: there is no mail server here, so the text is written for the user to copy and send. */
function Feedback({ support, report, onClose }) {
  const [kind, setKind] = useState(support ? 'Problem' : 'Idea');
  const [text, setText] = useState('');
  const [copied, setCopied] = useState(false);
  const details = [
    `Type: ${kind}`, `Report: ${report.title || 'Untitled'} (${report.id || 'new'})${report.folder ? ` in ${report.folder}` : ''}`,
    `Page: ${window.location.pathname}`, `Browser: ${navigator.userAgent}`, `Time: ${new Date().toISOString()}`,
  ].join('\n');
  const message = `${text.trim()}\n\n---\n${details}`;
  return (
    <Modal title={support ? 'Contact Support' : 'Feedback'} onClose={onClose}
      buttons={<button type="button" className="btn" disabled={!text.trim()} onClick={() => { copyText(message); setCopied(true); }}>{copied ? 'Copied' : 'Copy message'}</button>}>
      <p className="hint">{support
        ? 'Describe what went wrong and what you expected. Copy the message and send it to your report server administrator — the report name and browser are added for them.'
        : 'Tell us what would make the designer better. Copy the message and send it to your report server administrator.'}</p>
      <div className="toggles" role="radiogroup" aria-label="Type">
        {(support ? ['Problem', 'Question'] : ['Idea', 'Like', 'Dislike']).map((k) => <button key={k} type="button" className={kind === k ? 'on' : ''} aria-pressed={kind === k} onClick={() => setKind(k)}>{k}</button>)}
      </div>
      <textarea rows={6} value={text} onChange={(e) => { setText(e.target.value); setCopied(false); }} placeholder={support ? 'What happened? Which steps?' : 'Your feedback…'} aria-label="Message" />
      <details><summary>Added to the message</summary><pre className="help-pre">{details}</pre></details>
    </Modal>
  );
}

/** Review → Check report: every problem checkSetup() finds, with "Go there". */
function CheckReport({ report, design, onGo, onClose }) {
  const problems = checkSetup(report, design);
  return (
    <Modal title="Check report" onClose={onClose}>
      {problems.length === 0
        ? <p className="help-ok">No problems found. Run <b>Preview</b> to see it with real records.</p>
        : (
          <ul className="help-problems">
            {problems.map((p) => (
              <li key={`${p.text}${p.place}`} className={p.level}>
                <b>{p.level === 'error' ? 'Error' : 'Warning'}:</b> {p.text}
                {p.fix && <div className="muted">{p.fix}</div>}
                {p.place && <div className="help-place"><code>{p.place}</code>{onGo && p.focus && <button type="button" className="btn sm" onClick={() => { onGo(p.focus); onClose(); }}>Go there</button>}</div>}
              </li>
            ))}
          </ul>
        )}
    </Modal>
  );
}

export default function HelpDialog({ which, report, design, onGo, onOpen, onClose }) {
  switch (which) {
    case 'functions': return <FunctionList onClose={onClose} />;
    case 'shortcuts': return <Shortcuts onClose={onClose} />;
    case 'help': return <HelpTopics onClose={onClose} onOpen={onOpen} />;
    case 'whatsnew': return <WhatsNew onClose={onClose} />;
    case 'feedback': return <Feedback report={report} onClose={onClose} />;
    case 'support': return <Feedback support report={report} onClose={onClose} />;
    case 'check': return <CheckReport report={report} design={design} onGo={onGo} onClose={onClose} />;
    default: return null;
  }
}
