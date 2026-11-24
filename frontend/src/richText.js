// Formatted text in one Static Text / Text Field (markup "html"): some words bold, italic, underlined, coloured or
// highlighted, with $F{…} / $P{…} / $V{…} and {= … } formulas inside the paragraph. The server fills the values in
// and escapes them (backend/app/rendering/engine.py: fill_template), so data never adds formatting of its own.
import { BUILTIN_VARIABLES, safeMarkupHtml } from './designModel.js';

const TOKEN_SPLIT = /(\$[FPV]\{[^{}]+\})/;
const TOKEN_ONE = /^\$([FPV])\{([^{}]+)\}$/;
export const HAS_DATA = /\$[FPV]\{[^{}]+\}|\{=/;

/** A Java-style expression ("=$F{A} + …"): it makes its own text, so it is edited as an expression, not formatted. */
export const isJavaExpression = (text) => String(text ?? '').trimStart().startsWith('=');

/** "Pay {=$F{Fee} * 2} now" -> [["text","Pay "],["formula","$F{Fee} * 2"],["text"," now"]] (same rules as the server). */
export function templateParts(text) {
  const source = String(text ?? '');
  const parts = [];
  let start = 0;
  for (;;) {
    const open = source.indexOf('{=', start);
    if (open < 0) break;
    let depth = 0;
    let quote = '';
    let k = open + 2;
    for (; k < source.length; k += 1) {
      const ch = source[k];
      if (quote) {
        if (ch === '\\') { k += 1; continue; }
        if (ch === quote) quote = '';
      } else if (ch === '"' || ch === "'") quote = ch;
      else if (ch === '{') depth += 1;
      else if (ch === '}') { if (depth === 0) break; depth -= 1; }
    }
    if (k >= source.length) break;
    parts.push(['text', source.slice(start, open)], ['formula', source.slice(open + 2, k).trim()]);
    start = k + 1;
  }
  parts.push(['text', source.slice(start)]);
  return parts.filter(([kind, value]) => value || kind === 'formula');
}

const KIND_NAME = { F: 'Field', P: 'Parameter', V: 'Variable' };

/** The chip that stands for a token or a formula inside the editor (and on the canvas). */
export function chipNode(doc, token) {
  const chip = doc.createElement('span');
  chip.setAttribute('contenteditable', 'false');
  chip.dataset.token = token;
  const one = token.match(TOKEN_ONE);
  if (one) {
    chip.className = `rt-chip rt-${one[1]}`;
    chip.title = `${KIND_NAME[one[1]]} ${one[2]}: filled in from the data`;
    chip.textContent = one[2];
  } else {
    chip.className = 'rt-chip rt-fx';
    const body = token.slice(2, -1).trim();
    chip.title = `Formula: ${body}`;
    chip.textContent = `ƒx ${body.length > 38 ? `${body.slice(0, 36)}…` : body}`;
  }
  return chip;
}

export function chipHtml(token) {
  const holder = document.createElement('div');
  holder.append(chipNode(document, token));
  return holder.innerHTML;
}

/** Replace $F{…} and {= … } in the text nodes by chips. */
function chipify(root) {
  const doc = root.ownerDocument;
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) if (HAS_DATA.test(node.data)) nodes.push(node);
  nodes.forEach((node) => {
    const pieces = [];
    templateParts(node.data).forEach(([kind, value]) => {
      if (kind === 'formula') { pieces.push(chipNode(doc, `{=${value}}`)); return; }
      value.split(TOKEN_SPLIT).forEach((bit) => {
        if (!bit) return;
        pieces.push(TOKEN_ONE.test(bit) ? chipNode(doc, bit) : doc.createTextNode(bit));
      });
    });
    node.replaceWith(...pieces);
  });
}

const escapeHtml = (text) => String(text ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/** Plain text (markup "none") as formatted text: the same words, line breaks kept. */
export const plainToHtml = (text) => escapeHtml(text).replace(/\r?\n/g, '<br>');

/** Formatted text back to plain text (formatting dropped, line breaks kept). */
export function htmlToPlain(html) {
  const doc = new DOMParser().parseFromString(`<div>${safeMarkupHtml(html, 'html') || ''}</div>`, 'text/html');
  const root = doc.body.firstChild;
  root.querySelectorAll('br').forEach((br) => br.replaceWith('\n'));
  root.querySelectorAll('p, div, li, h1, h2, h3, h4, h5, h6').forEach((block) => block.append('\n'));
  return root.textContent.replace(/\n+$/, '');
}

/** The value as HTML for the editor / canvas: sanitised, with chips for the data. */
export function toEditorHtml(value, markup = 'html') {
  const html = markup === 'html' || markup === 'styled' ? safeMarkupHtml(value, markup) || '' : plainToHtml(value);
  const doc = new DOMParser().parseFromString(`<div>${html}</div>`, 'text/html');
  const root = doc.body.firstChild;
  chipify(root);
  return root.innerHTML;
}

/** What the editor shows -> the stored value (chips back to their tokens, sanitised, no trailing break). */
export function fromEditorHtml(root) {
  const copy = root.cloneNode(true);
  copy.querySelectorAll('[data-token]').forEach((chip) => chip.replaceWith(copy.ownerDocument.createTextNode(chip.dataset.token)));
  copy.querySelectorAll('font[size="7"]').forEach((font) => font.removeAttribute('size'));
  const html = safeMarkupHtml(copy.innerHTML, 'html') || '';
  return html.replace(/(<br\s*\/?>|\s)+$/i, '').replace(/^(<br\s*\/?>)+$/i, '');
}

/** Fields, parameters and variables the editor can put in the text. */
export function tokenGroups(report, design) {
  return [
    { label: 'Fields', items: [...(report?.columns || []), ...((design?.calculatedFields || []).map((c) => c.name))].map((name) => ({ name, token: `$F{${name}}` })) },
    { label: 'Parameters', items: (report?.parameters || []).map((p) => ({ name: p.label && p.label !== p.name ? `${p.name} (${p.label})` : p.name, token: `$P{${p.name}}` })) },
    {
      label: 'Variables',
      items: [...(design?.variables || []).map((v) => v.name), ...BUILTIN_VARIABLES].map((name) => ({ name, token: `$V{${name}}` })),
    },
  ].filter((group) => group.items.length);
}

/**
 * Several texts on the canvas -> one formatted paragraph, so "Your fee of", "Rs 5,000" (bold) and "is due" become
 * one box. Parts are read line by line (top to bottom, left to right); each keeps its own bold / italic / underline,
 * colour and size. A Java expression becomes a {= … } formula. Returns the new element (first one's id and place).
 */
export function joinTexts(elements) {
  const texts = [...elements].sort((a, b) => a.y - b.y || a.x - b.x);
  const lines = [];
  texts.forEach((el) => {
    const line = lines.find((l) => Math.abs(l.y - el.y) < Math.min(l.h, el.height) / 2);
    if (line) line.items.push(el);
    else lines.push({ y: el.y, h: el.height, items: [el] });
  });
  lines.forEach((l) => l.items.sort((a, b) => a.x - b.x));
  const ordered = lines.flatMap((l) => l.items);
  const base = ordered[0];
  const html = (el) => {
    if (el.type === 'staticText') return el.markup === 'html' || el.markup === 'styled' ? safeMarkupHtml(el.text, el.markup) || '' : plainToHtml(el.text);
    const expression = String(el.expression ?? '');
    if (isJavaExpression(expression)) return `{=${escapeHtml(expression.trimStart().slice(1))}}`;
    const single = expression.trim();
    if (el.pattern && TOKEN_ONE.test(single)) {
      // the joined text has one pattern for all its values: this value keeps its own as a formula
      const date = /[dMyHhms]/.test(el.pattern.replace(/'[^']*'/g, '')) && !/[#0]/.test(el.pattern);
      const maker = date ? 'java.text.SimpleDateFormat' : 'java.text.DecimalFormat';
      return `{=${escapeHtml(`new ${maker}("${el.pattern.replace(/"/g, '\\"')}").format(${single})`)}}`;
    }
    return el.markup === 'html' || el.markup === 'styled' ? safeMarkupHtml(expression, el.markup) || '' : plainToHtml(expression);
  };
  const styled = (el) => {
    let body = html(el);
    const css = [];
    if ((el.color || '#000000') !== (base.color || '#000000')) css.push(`color:${el.color}`);
    if (el.fontSize && el.fontSize !== base.fontSize) css.push(`font-size:${el.fontSize}px`);
    if (el.backColor && el.backColor !== base.backColor && !el.transparent) css.push(`background-color:${el.backColor}`);
    if (css.length) body = `<span style="${css.join(';')}">${body}</span>`;
    if (el.underline) body = `<u>${body}</u>`;
    if (el.italic) body = `<i>${body}</i>`;
    if (el.bold) body = `<b>${body}</b>`;
    return body;
  };
  const out = lines.map((l) => l.items.map(styled).reduce((all, part) => {
    if (!all) return part;
    return /\s$/.test(all) || /^\s/.test(part) ? all + part : `${all} ${part}`;
  }, '')).join('<br>');
  const left = Math.min(...texts.map((e) => e.x));
  const top = Math.min(...texts.map((e) => e.y));
  const right = Math.max(...texts.map((e) => e.x + e.width));
  const bottom = Math.max(...texts.map((e) => e.y + e.height));
  const { text, expression, pattern, blankWhenNull, evaluationTime, ...look } = base;
  void text; void expression; void pattern; void blankWhenNull; void evaluationTime;
  const common = { ...look, x: left, y: top, width: right - left, height: bottom - top, markup: 'html', bold: false, italic: false, underline: false };
  return HAS_DATA.test(out)
    ? { ...common, type: 'textField', expression: out, pattern: '', blankWhenNull: true, stretch: base.stretch ?? false }
    : { ...common, type: 'staticText', text: out };
}
