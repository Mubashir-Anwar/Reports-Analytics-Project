// Colours for expressions, like an IDE: $F{field} blue, $P{parameter} green, $V{variable} orange, plain words in the
// normal text colour; in a Java-style expression (starts with =) also "strings", numbers and Java words.
// A field / parameter / variable the report does not have is red with a wavy underline.
import { forwardRef, useImperativeHandle, useRef } from 'react';

const TOKEN = /(\$([FPV])\{([^{}]*)\}?)|("(?:[^"\\\n]|\\.)*"?)|('(?:[^'\\\n]|\\.)*'?)|(\b\d+(?:\.\d+)?\b)|(\b(?:new|null|true|false|instanceof|return)\b)|([A-Za-z_][\w.]*(?=\s*\())/g;
const KIND = { F: 'field', P: 'param', V: 'var' };

/** Split `text` into [{ text, cls, title }] pieces. `known` = { F: Set, P: Set, V: Set } (missing ones are marked). */
export function highlightPieces(text, known) {
  const java = String(text || '').trimStart().startsWith('=');
  const pieces = [];
  let last = 0;
  for (const m of String(text || '').matchAll(TOKEN)) {
    const [whole] = m;
    if (!m[1] && !java) continue;                     // template text: only $F/$P/$V are special
    if (m.index > last) pieces.push({ text: text.slice(last, m.index) });
    if (m[1]) {
      const kind = m[2];
      const name = m[3].trim();
      const missing = known && known[kind] && !known[kind].has(name);
      const word = { F: 'Field', P: 'Parameter', V: 'Variable' }[kind];
      pieces.push({ text: whole, cls: `tk-${KIND[kind]}${missing ? ' tk-missing' : ''}`, title: missing ? `${word} ${name} is not in this report` : `${word}: ${name}` });
    } else if (m[4] || m[5]) pieces.push({ text: whole, cls: 'tk-string' });
    else if (m[6]) pieces.push({ text: whole, cls: 'tk-number' });
    else if (m[7]) pieces.push({ text: whole, cls: 'tk-keyword' });
    else pieces.push({ text: whole, cls: 'tk-call' });
    last = m.index + whole.length;
  }
  if (last < String(text || '').length) pieces.push({ text: text.slice(last) });
  return pieces;
}

/** The names a report knows, for highlightPieces. */
export function knownNames(report, design, builtinVariables = []) {
  return {
    F: new Set([...(report?.columns || []), ...((design?.calculatedFields || []).map((c) => c.name))]),
    P: new Set((report?.parameters || []).map((p) => p.name)),
    V: new Set([...((design?.variables || []).map((v) => v.name)), ...builtinVariables]),
  };
}

/** Coloured text (read-only), e.g. a text field on the canvas. */
export function Highlighted({ text, known }) {
  return highlightPieces(text, known).map((p, i) => (p.cls ? <span key={i} className={p.cls} title={p.title}>{p.text}</span> : p.text));
}

/**
 * A textarea with coloured text: the colours are drawn in a layer behind a transparent-text textarea that keeps
 * the cursor, selection, typing and undo of a normal textarea.
 */
const HighlightArea = forwardRef(function HighlightArea({ value, known, className = '', ...rest }, ref) {
  const area = useRef(null);
  const layer = useRef(null);
  useImperativeHandle(ref, () => area.current);
  const sync = () => { if (layer.current && area.current) { layer.current.scrollTop = area.current.scrollTop; layer.current.scrollLeft = area.current.scrollLeft; } };
  return (
    <div className={`hl-wrap ${className}`}>
      <pre ref={layer} className="hl-layer" aria-hidden="true"><Highlighted text={value} known={known} />{'\n'}</pre>
      <textarea ref={area} className="hl-input" spellCheck={false} value={value} onScroll={sync} {...rest} />
    </div>
  );
});

export default HighlightArea;
