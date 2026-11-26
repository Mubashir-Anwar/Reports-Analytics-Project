// A code box that behaves like a small VS Code (written here, no editor package): coloured syntax for MongoDB
// (JSON / shell / JavaScript), SQL, Python and DAX, coloured bracket pairs and the matching bracket, the current line
// lit, line numbers that scroll with the text, an error line marked red, and the keys people expect:
//   Tab / Shift+Tab   indent / outdent (the selected lines)      Enter          keeps the indent, opens a block
//   ( [ { " ' `        closed for you, typed over when next       Backspace      removes an empty pair
//   Ctrl+/            comment / uncomment the lines               Alt+↑ / Alt+↓  move the lines
//   Ctrl+D            select the next copy of the word            "Go to line"   jumps to a line and selects it
// The text is a real <textarea> (undo, IME, screen readers and copy work as usual); the colours are a layer under it.
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
import './code-editor.css';

/** Select line `line` (1-based) of a textarea and scroll it into the middle of the box. */
export function selectLine(area, line) {
  if (!area) return;
  const lines = area.value.split('\n');
  const n = Math.min(Math.max(1, Number(line) || 1), lines.length);
  const start = lines.slice(0, n - 1).reduce((sum, l) => sum + l.length + 1, 0);
  area.focus({ preventScroll: true });
  area.setSelectionRange(start, start + lines[n - 1].length);
  const height = parseFloat(getComputedStyle(area).lineHeight) || 20;
  area.scrollTop = Math.max(0, (n - 1) * height - area.clientHeight / 2 + height);
}

/** Which colouring a query language gets. */
export function languageOf(queryLanguage) {
  const value = String(queryLanguage || '').toLowerCase();
  if (value === 'sql' || value === 'dax' || value === 'python' || value === 'json') return value;
  return 'mongo';                                     // aggregation-json, mongosh, javascript
}

const LABELS = { mongo: 'MongoDB', sql: 'SQL', python: 'Python', dax: 'DAX', json: 'JSON' };
const KEYWORDS = {
  mongo: new Set('var let const function return if else for while do break continue new this true false null undefined typeof instanceof in of try catch finally throw switch case default async await'.split(' ')),
  json: new Set(['true', 'false', 'null']),
  sql: new Set(('select from where and or not in is null as on join left right inner outer full cross group by order having limit offset union all distinct case when then else end '
    + 'insert update delete into values set create table view with asc desc between like ilike exists true false over partition window qualify using natural lateral '
    + 'cast try_cast interval date timestamp filter within top fetch first next rows only nulls last any some replace exclude pivot unpivot summarize describe').split(' ')),
  python: new Set(('def return if elif else for while in not and or is None True False import from as with try except finally raise class lambda pass break continue '
    + 'yield global nonlocal assert del async await match case').split(' ')),
  dax: new Set(('evaluate define measure var return order by asc desc start at true false blank in not and or').split(' ')),
};
const BUILTINS = {
  python: new Set('print len range list dict set tuple str int float bool sum min max abs round sorted enumerate zip map filter isinstance type any all open super object pd np df dfs result'.split(' ')),
  mongo: new Set('db ObjectId ISODate Date Math JSON NumberDecimal NumberLong NumberInt pipeline'.split(' ')),
  sql: new Set(), dax: new Set(), json: new Set(),
};
const COMMENT = { mongo: '//', json: '//', sql: '--', python: '#', dax: '//' };
const PAIRS = { '(': ')', '[': ']', '{': '}' };
const CLOSERS = { ')': '(', ']': '[', '}': '{' };
const QUOTES = { mongo: '"\'`', json: '"', sql: '\'"', python: '"\'', dax: '"\'' };

/** Split the text into coloured pieces: [{t: type, v: text, at: offset, depth?}]. */
function tokenize(text, language) {
  const out = [];
  const keywords = KEYWORDS[language] || KEYWORDS.mongo;
  const builtins = BUILTINS[language] || new Set();
  const lineComment = COMMENT[language] || '//';
  let i = 0;
  let depth = 0;
  const n = text.length;
  const push = (t, v, extra) => { out.push({ t, v, at: i, ...extra }); i += v.length; };
  while (i < n) {
    const ch = text[i];
    const rest = text.slice(i, i + 3);
    if (ch === '\n' || ch === ' ' || ch === '\t') {
      let j = i; while (j < n && (text[j] === ' ' || text[j] === '\t' || text[j] === '\n')) j += 1;
      push('ws', text.slice(i, j)); continue;
    }
    if (text.startsWith(lineComment, i) && !(language === 'json')) {
      const end = text.indexOf('\n', i); push('com', text.slice(i, end < 0 ? n : end)); continue;
    }
    if (rest.startsWith('/*') && language !== 'python') {
      const end = text.indexOf('*/', i + 2); push('com', text.slice(i, end < 0 ? n : end + 2)); continue;
    }
    if ((ch === '$') && /^\$[PXR]!?\{/.test(text.slice(i, i + 4))) {      // report parameters: $P{Name}, $P!{Name}, $X{IN, …}
      const end = text.indexOf('}', i); push('param', text.slice(i, end < 0 ? n : end + 1)); continue;
    }
    if (language === 'python' && (rest === '"""' || rest === "'''")) {
      const end = text.indexOf(rest, i + 3); push('str', text.slice(i, end < 0 ? n : end + 3)); continue;
    }
    if ((QUOTES[language] || '"\'').includes(ch)) {
      let j = i + 1;
      while (j < n && text[j] !== ch && (ch === '`' || text[j] !== '\n')) {
        if (text[j] === '\\') j += 1;
        else if (language === 'sql' && ch === "'" && text[j + 1] === "'" && text[j] === "'") j += 1;
        j += 1;
      }
      const v = text.slice(i, Math.min(n, j + 1));
      let k = i + v.length; while (k < n && (text[k] === ' ' || text[k] === '\t')) k += 1;
      const isKey = text[k] === ':' && language !== 'python' && language !== 'sql';
      const t = language === 'sql' && ch === '"' ? 'ident' : isKey ? (v[1] === '$' ? 'opkey' : 'key') : v[1] === '$' && language !== 'sql' ? 'op' : 'str';
      push(t, v); continue;
    }
    if (language === 'dax' && ch === '[') {
      const end = text.indexOf(']', i); push('key', text.slice(i, end < 0 ? n : end + 1)); continue;
    }
    const number = /^(?:0x[0-9a-f]+|\d+(?:\.\d+)?(?:e[+-]?\d+)?)/i.exec(text.slice(i, i + 40));
    if (number && !/[A-Za-z_$]/.test(text[i - 1] || '')) { push('num', number[0]); continue; }
    const word = /^[A-Za-z_$][A-Za-z0-9_$]*/.exec(text.slice(i, i + 200));
    if (word) {
      const v = word[0];
      let k = i + v.length; while (k < n && text[k] === ' ') k += 1;
      const lower = language === 'sql' || language === 'dax' ? v.toLowerCase() : v;
      let t = 'id';
      if (keywords.has(lower)) t = ['return', 'if', 'else', 'elif', 'for', 'while', 'import', 'from', 'try', 'except', 'catch', 'finally', 'raise', 'throw', 'with'].includes(lower) && language !== 'sql' ? 'ctrl' : 'kw';
      else if (text[k] === '(') t = 'fn';
      else if (builtins.has(v)) t = 'type';
      else if (text[k] === ':' && (language === 'mongo' || language === 'json')) t = v.startsWith('$') ? 'opkey' : 'key';
      else if (text[i - 1] === '.') t = 'prop';
      else if (v.startsWith('$') && language !== 'python') t = 'op';
      push(t, v); continue;
    }
    if (PAIRS[ch]) { push('br', ch, { depth: depth % 3 }); depth += 1; continue; }
    if (CLOSERS[ch]) { depth = Math.max(0, depth - 1); push('br', ch, { depth: depth % 3 }); continue; }
    push('pun', ch);
  }
  return out;
}

/** The bracket that pairs with the one at `at` (or -1). */
function partner(text, at) {
  const ch = text[at];
  const open = PAIRS[ch];
  const close = CLOSERS[ch];
  if (!open && !close) return -1;
  const step = open ? 1 : -1;
  const want = open || close;
  let level = 0;
  for (let i = at; i >= 0 && i < text.length; i += step) {
    if (text[i] === ch) level += 1;
    else if (text[i] === want) { level -= 1; if (level === 0) return i; }
  }
  return -1;
}

/** Replace [start, end) with `text` the way typing does (so Ctrl+Z undoes it), then select [from, to]. */
function edit(el, start, end, text, from, to = from) {
  el.focus();
  el.setSelectionRange(start, end);
  const ok = typeof document.execCommand === 'function' && document.execCommand('insertText', false, text);
  if (!ok) {
    el.setRangeText(text, start, end, 'end');
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }
  el.setSelectionRange(from, to);
}

const CodeArea = forwardRef(function CodeArea({ value, onChange, rows = 12, placeholder, markLine, markNote, markFixed, focusName,
  language = 'mongo', onKeyDown, readOnly, className = '', ...rest }, ref) {
  const area = useRef(null);
  const gutter = useRef(null);
  const layer = useRef(null);
  const [caret, setCaret] = useState({ line: 1, column: 1, at: 0, chosen: 0 });
  const [jump, setJump] = useState('');
  useImperativeHandle(ref, () => area.current);
  const text = String(value ?? '');
  const count = Math.max(1, text.split('\n').length);
  const lang = KEYWORDS[language] ? language : 'mongo';
  const unit = lang === 'python' ? '    ' : '  ';

  const sync = () => {
    const el = area.current;
    if (!el) return;
    if (gutter.current) gutter.current.scrollTop = el.scrollTop;
    if (layer.current) { layer.current.scrollTop = el.scrollTop; layer.current.scrollLeft = el.scrollLeft; }
  };
  const track = () => {
    const el = area.current;
    if (!el) return;
    const before = el.value.slice(0, el.selectionStart);
    setCaret({ line: before.split('\n').length, column: before.length - before.lastIndexOf('\n'), at: el.selectionStart,
      chosen: Math.abs(el.selectionEnd - el.selectionStart) });
  };
  useEffect(sync);

  // the coloured layer: one block per line, so the current line and the error line can be lit
  const lines = useMemo(() => {
    const tokens = tokenize(text, lang);
    const el = area.current;
    const at = el && el.selectionStart === el.selectionEnd ? caret.at : -1;
    let match = new Set();
    if (at >= 0) {
      const here = CLOSERS[text[at - 1]] || PAIRS[text[at - 1]] ? at - 1 : (CLOSERS[text[at]] || PAIRS[text[at]] ? at : -1);
      const other = here >= 0 ? partner(text, here) : -1;
      if (other >= 0) match = new Set([here, other]);
    }
    const rows2 = [[]];
    for (const token of tokens) {
      const parts = token.v.split('\n');
      parts.forEach((part, index) => {
        if (index > 0) rows2.push([]);
        if (part) rows2[rows2.length - 1].push({ ...token, v: part });
      });
    }
    return rows2.map((pieces, index) => (
      <div key={index} className={`ce-line${index + 1 === caret.line && !readOnly ? ' on' : ''}${index + 1 === markLine ? (markFixed ? ' fixed' : ' err') : ''}`}>
        {pieces.length ? pieces.map((p, k) => (
          <span key={k} className={`t-${p.t}${p.t === 'br' ? ` d${p.depth}` : ''}${p.t === 'br' && match.has(p.at) ? ' match' : ''}`}>{p.v}</span>
        )) : '​'}
      </div>
    ));
  }, [text, lang, caret.line, caret.at, markLine, markFixed, readOnly]);

  const lineStart = (s, at) => s.lastIndexOf('\n', at - 1) + 1;
  const keys = (event) => {
    onKeyDown?.(event);
    if (event.defaultPrevented || readOnly) return;
    const el = event.currentTarget;
    const s = el.value;
    const { selectionStart: a, selectionEnd: b } = el;
    const mod = event.ctrlKey || event.metaKey;
    const blockStart = lineStart(s, a);
    const blockEndIndex = s.indexOf('\n', b - (b > a && s[b - 1] === '\n' ? 1 : 0));
    const blockEnd = blockEndIndex < 0 ? s.length : blockEndIndex;

    if (event.key === 'Tab') {
      event.preventDefault();
      if (a === b && !event.shiftKey) { edit(el, a, b, unit, a + unit.length); return; }
      const block = s.slice(blockStart, blockEnd).split('\n');
      const changed = block.map((line) => (event.shiftKey ? line.replace(new RegExp(`^( {1,${unit.length}}|\\t)`), '') : unit + line));
      const joined = changed.join('\n');
      const first = changed[0].length - block[0].length;
      edit(el, blockStart, blockEnd, joined, Math.max(blockStart, a + first), blockStart + joined.length);
      return;
    }
    if (event.key === 'Enter' && !mod && !event.shiftKey && !event.altKey) {
      event.preventDefault();
      const line = s.slice(blockStart, a);
      const indent = /^[ \t]*/.exec(line)[0];
      const before = s.slice(blockStart, a).trimEnd().slice(-1);
      const after = s[b];
      const opens = '([{'.includes(before) || (lang === 'python' && before === ':');
      if (opens && PAIRS[before] && after === PAIRS[before]) {
        const middle = `\n${indent}${unit}`;
        edit(el, a, b, `${middle}\n${indent}`, a + middle.length);
        return;
      }
      const insert = `\n${indent}${opens ? unit : ''}`;
      edit(el, a, b, insert, a + insert.length);
      return;
    }
    if (event.key === 'Backspace' && a === b && a > 0) {
      const prev = s[a - 1];
      if ((PAIRS[prev] && s[a] === PAIRS[prev]) || ((QUOTES[lang] || '').includes(prev) && s[a] === prev)) {
        event.preventDefault(); edit(el, a - 1, a + 1, '', a - 1); return;
      }
      const line = s.slice(blockStart, a);
      if (line.length && /^ +$/.test(line) && line.length % unit.length === 0) {     // remove one indent step
        event.preventDefault(); edit(el, a - unit.length, a, '', a - unit.length); return;
      }
    }
    if (!mod && !event.altKey && event.key.length === 1) {
      const ch = event.key;
      if (CLOSERS[ch] && a === b && s[a] === ch) { event.preventDefault(); el.setSelectionRange(a + 1, a + 1); track(); return; }
      const quote = (QUOTES[lang] || '').includes(ch);
      if (quote && a === b && s[a] === ch) { event.preventDefault(); el.setSelectionRange(a + 1, a + 1); track(); return; }
      if (PAIRS[ch] || quote) {
        const close = PAIRS[ch] || ch;
        if (a !== b) { event.preventDefault(); edit(el, a, b, ch + s.slice(a, b) + close, a + 1, b + 1); return; }
        const next = s[a] || '';
        const prev = s[a - 1] || '';
        const free = !next || /[\s)\]},;:]/.test(next);
        if (free && (!quote || !/[\w$]/.test(prev))) { event.preventDefault(); edit(el, a, b, ch + close, a + 1); return; }
      }
    }
    if (mod && (event.key === '/' || event.code === 'Slash')) {
      event.preventDefault();
      const mark = COMMENT[lang];
      const block = s.slice(blockStart, blockEnd).split('\n');
      const all = block.filter((l) => l.trim()).every((l) => l.trimStart().startsWith(mark));
      const changed = block.map((l) => {
        if (!l.trim()) return l;
        if (all) return l.replace(new RegExp(`^(\\s*)${mark.replace(/[/]/g, '\\/')} ?`), '$1');
        const pad = /^\s*/.exec(l)[0];
        return `${pad}${mark} ${l.slice(pad.length)}`;
      });
      const joined = changed.join('\n');
      edit(el, blockStart, blockEnd, joined, blockStart, blockStart + joined.length);
      return;
    }
    if (event.altKey && (event.key === 'ArrowUp' || event.key === 'ArrowDown')) {
      event.preventDefault();
      const up = event.key === 'ArrowUp';
      if (up && blockStart === 0) return;
      if (!up && blockEnd >= s.length) return;
      const block = s.slice(blockStart, blockEnd);
      if (up) {
        const prevStart = lineStart(s, blockStart - 1);
        const prevLine = s.slice(prevStart, blockStart - 1);
        edit(el, prevStart, blockEnd, `${block}\n${prevLine}`, a - prevLine.length - 1, b - prevLine.length - 1);
      } else {
        const nextEnd = s.indexOf('\n', blockEnd + 1) < 0 ? s.length : s.indexOf('\n', blockEnd + 1);
        const nextLine = s.slice(blockEnd + 1, nextEnd);
        edit(el, blockStart, nextEnd, `${nextLine}\n${block}`, a + nextLine.length + 1, b + nextLine.length + 1);
      }
      return;
    }
    if (mod && (event.key === 'd' || event.key === 'D')) {             // select the word, then its next copy
      event.preventDefault();
      if (a === b) {
        let from = a; let to = a;
        while (from > 0 && /[\w$]/.test(s[from - 1])) from -= 1;
        while (to < s.length && /[\w$]/.test(s[to])) to += 1;
        el.setSelectionRange(from, to);
      } else {
        const word = s.slice(a, b);
        let next = s.indexOf(word, b);
        if (next < 0) next = s.indexOf(word);
        if (next >= 0) el.setSelectionRange(next, next + word.length);
      }
      track();
    }
  };

  const go = (event) => {
    event.preventDefault();
    if (!jump) return;
    selectLine(area.current, jump);
    sync();
    track();
  };
  const numbers = [];
  for (let i = 1; i <= count; i += 1) {
    const cls = i === markLine ? `mark${markFixed ? ' fixed' : ''}` : i === caret.line ? 'here' : undefined;
    numbers.push(<div key={i} className={cls} title={i === markLine ? markNote || (markFixed ? 'Fixed on this line' : 'The error is on this line') : undefined}>{i}</div>);
  }
  return (
    <div className={`ce ${className}`} data-focus={focusName}>
      <div className="ce-box" style={{ height: `${Math.max(4, rows) * 20 + 20}px` }}>
        <div className="ce-gutter" ref={gutter} aria-hidden="true">{numbers}<div className="ce-pad" /></div>
        <div className="ce-text">
          <div className="ce-layer" ref={layer} aria-hidden="true"><div className="ce-lines">{lines}<div className="ce-pad" /></div></div>
          <textarea ref={area} className="ce-input code" spellCheck={false} wrap="off" value={text} placeholder={placeholder} readOnly={readOnly}
            autoCapitalize="off" autoComplete="off" autoCorrect="off"
            onChange={(e) => { onChange?.(e); track(); }} onScroll={sync} onKeyDown={keys} onKeyUp={track} onClick={track} onSelect={track} onFocus={track} {...rest} />
        </div>
      </div>
      <div className="ce-status">
        <span>Ln {caret.line}, Col {caret.column}{caret.chosen ? ` (${caret.chosen} selected)` : ''}</span>
        <span>{count} line{count === 1 ? '' : 's'}</span>
        <span>{LABELS[lang]}</span>
        <span title="Tab indents by this much">Spaces: {unit.length}</span>
        {markLine ? (
          <button type="button" className={`linkish mark${markFixed ? ' fixed' : ''}`} onClick={() => { selectLine(area.current, markLine); sync(); track(); }}>
            {markFixed ? `✓ Fixed on line ${markLine} — show it` : `● Error on line ${markLine} — show it`}
          </button>
        ) : null}
        <form className="ce-jump" onSubmit={go}>
          <label>Go to line <input type="number" min="1" max={count} value={jump} onChange={(e) => setJump(e.target.value)} /></label>
          <button type="submit" disabled={!jump}>Go</button>
        </form>
      </div>
    </div>
  );
});

export default CodeArea;
