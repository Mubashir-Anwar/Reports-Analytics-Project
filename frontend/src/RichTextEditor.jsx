import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { safeMarkupHtml } from './designModel.js';
import { chipHtml, fromEditorHtml, toEditorHtml } from './richText.js';

const SIZES = [7, 8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 48];
const COLORS = ['#000000', '#475569', '#1e3a8a', '#2563eb', '#047857', '#b45309', '#b91c1c', '#7c3aed'];
const MARKS = ['#fef08a', '#bbf7d0', '#bfdbfe', '#fbcfe8', '#fed7aa'];

/**
 * One paragraph with formatting on some words (like a word processor): select words, then Bold / Italic /
 * Underline / colour / highlight / size. Fields, parameters and variables go in as chips where the cursor is, and
 * a ƒx formula ({= … }) is worked out while printing. The value is the HTML the service prints (markup "html").
 * Ctrl+B / Ctrl+I / Ctrl+U work too; Enter is a line break.
 */
const RichTextEditor = forwardRef(function RichTextEditor({ value, markup = 'html', onChange, groups = [], autoFocus = false, rows = 4, label }, ref) {
  const box = useRef(null);
  const last = useRef(null);
  const range = useRef(null);
  const [formula, setFormula] = useState(null);
  const [state, setState] = useState({});

  useEffect(() => {
    if (!box.current || value === last.current) return;
    box.current.innerHTML = toEditorHtml(value, markup);
    last.current = value;
  }, [value, markup]);
  useEffect(() => { if (autoFocus) box.current?.focus(); }, [autoFocus]);

  const emit = () => {
    const out = fromEditorHtml(box.current);
    last.current = out;
    onChange(out);
  };
  const remember = () => {
    const sel = window.getSelection();
    if (sel?.rangeCount && box.current?.contains(sel.anchorNode)) range.current = sel.getRangeAt(0).cloneRange();
    setState({
      bold: document.queryCommandState('bold'), italic: document.queryCommandState('italic'),
      underline: document.queryCommandState('underline'), strike: document.queryCommandState('strikeThrough'),
    });
  };
  const restore = () => {
    box.current.focus();
    if (range.current) {
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range.current);
    }
  };
  const run = (command, arg = null, css = false) => {
    restore();
    document.execCommand('styleWithCSS', false, css);
    document.execCommand(command, false, arg);
    emit();
    remember();
  };
  const setSize = (px) => {
    restore();
    document.execCommand('styleWithCSS', false, false);
    document.execCommand('fontSize', false, '7');
    box.current.querySelectorAll('font[size="7"]').forEach((font) => {
      const span = document.createElement('span');
      span.style.fontSize = `${px}px`;
      span.append(...font.childNodes);
      font.replaceWith(span);
    });
    emit();
  };
  const insert = (html) => {
    restore();
    document.execCommand('insertHTML', false, html);
    emit();
    remember();
  };
  const insertToken = (token) => insert(chipHtml(token));
  useImperativeHandle(ref, () => ({ insertToken, focus: () => box.current?.focus() }));

  const press = (fn) => ({ onMouseDown: (e) => e.preventDefault(), onClick: fn });

  return (
    <div className="rt" aria-label={label}>
      <div className="rt-bar" role="toolbar" aria-label="Text formatting">
        <button type="button" className={`rt-btn ${state.bold ? 'on' : ''}`} title="Bold (Ctrl+B)" {...press(() => run('bold'))}><b>B</b></button>
        <button type="button" className={`rt-btn ${state.italic ? 'on' : ''}`} title="Italic (Ctrl+I)" {...press(() => run('italic'))}><i>I</i></button>
        <button type="button" className={`rt-btn ${state.underline ? 'on' : ''}`} title="Underline (Ctrl+U)" {...press(() => run('underline'))}><u>U</u></button>
        <button type="button" className={`rt-btn ${state.strike ? 'on' : ''}`} title="Strike through" {...press(() => run('strikeThrough'))}><s>S</s></button>
        <button type="button" className="rt-btn" title="Superscript" {...press(() => run('superscript'))}>x²</button>
        <button type="button" className="rt-btn" title="Subscript" {...press(() => run('subscript'))}>x₂</button>
        <span className="rt-sep" />
        <span className="rt-swatches" title="Text colour of the selected words">
          {COLORS.map((c) => <button type="button" key={c} className="rt-swatch" style={{ background: c }} aria-label={`Colour ${c}`} {...press(() => run('foreColor', c))} />)}
          <label className="rt-pick" title="Another colour" onMouseDown={remember}>
            <input type="color" defaultValue="#dc2626" onChange={(e) => run('foreColor', e.target.value)} />A
          </label>
        </span>
        <span className="rt-swatches" title="Highlight the selected words">
          {MARKS.map((c) => <button type="button" key={c} className="rt-swatch mark" style={{ background: c }} aria-label={`Highlight ${c}`} {...press(() => run('hiliteColor', c, true))} />)}
          <button type="button" className="rt-btn" title="No highlight" {...press(() => run('hiliteColor', 'transparent', true))}>⌫</button>
        </span>
        <select className="rt-size" value="" title="Size of the selected words" onMouseDown={remember} onChange={(e) => e.target.value && setSize(Number(e.target.value))}>
          <option value="">Size</option>
          {SIZES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <button type="button" className="rt-btn" title="Remove the formatting of the selected words" {...press(() => run('removeFormat'))}>Tx</button>
        <span className="rt-sep" />
        {groups.length > 0 && (
          <select className="rt-insert" value="" title="Put a field, parameter or variable where the cursor is" onMouseDown={remember} onChange={(e) => e.target.value && insertToken(e.target.value)}>
            <option value="">＋ Field / parameter…</option>
            {groups.map((g) => (
              <optgroup key={g.label} label={g.label}>
                {g.items.map((item) => <option key={item.token} value={item.token}>{item.name}</option>)}
              </optgroup>
            ))}
          </select>
        )}
        <button type="button" className="rt-btn rt-fx-btn" title="A formula worked out while printing, e.g. $F{Fee} * 1.17 or $F{Name}.toUpperCase()" {...press(() => { remember(); setFormula(formula === null ? '' : null); })}>ƒx</button>
      </div>
      {formula !== null && (
        <div className="rt-formula">
          <input autoFocus value={formula} placeholder='e.g. $F{Fee} * 1.17   or   $F{Amount} > 0 ? "Paid" : "Due"' onChange={(e) => setFormula(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); if (formula.trim()) insertToken(`{=${formula.trim()}}`); setFormula(null); } if (e.key === 'Escape') { e.stopPropagation(); setFormula(null); } }} />
          <button type="button" className="btn sm primary" disabled={!formula.trim()} {...press(() => { insertToken(`{=${formula.trim()}}`); setFormula(null); })}>Insert</button>
          <button type="button" className="btn sm" {...press(() => setFormula(null))}>Cancel</button>
        </div>
      )}
      <div
        ref={box}
        className="rt-area"
        contentEditable
        suppressContentEditableWarning
        role="textbox"
        aria-multiline="true"
        style={{ minHeight: `${rows * 1.5}em` }}
        onInput={emit}
        onKeyUp={remember}
        onMouseUp={remember}
        onBlur={remember}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.ctrlKey && !e.metaKey) {
            e.preventDefault();
            document.execCommand('insertLineBreak');
            emit();
          }
        }}
        onPaste={(e) => {
          e.preventDefault();
          const html = e.clipboardData.getData('text/html');
          if (html) document.execCommand('insertHTML', false, toEditorHtml(safeMarkupHtml(html, 'html'), 'html'));
          else document.execCommand('insertText', false, e.clipboardData.getData('text/plain'));
          emit();
        }}
      />
    </div>
  );
});

export default RichTextEditor;
