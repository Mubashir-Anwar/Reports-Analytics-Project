// Own fonts of this server (e.g. Urdu ones): loaded for the canvas, offered in every font list, added and removed here.
import { useEffect, useState, useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';
import { api } from './api.js';
import { FONT_NAMES } from './designModel.js';

let fonts = [];
let asked = false;
const listeners = new Set();
const loaded = new Set();                        // font ids the browser has
const STYLE_LABELS = { regular: 'Regular', bold: 'Bold', italic: 'Italic', boldItalic: 'Bold italic' };
const MAX_BYTES = 15_000_000;

function setFonts(list) {
  fonts = list || [];
  for (const font of fonts) {
    if (loaded.has(font.id) || typeof FontFace === 'undefined') continue;
    loaded.add(font.id);
    const face = new FontFace(font.family, `url(/api/admin/fonts/${font.id}/file)`, {
      weight: font.style.startsWith('bold') ? '700' : '400', style: /italic/i.test(font.style) ? 'italic' : 'normal' });
    face.load().then((ready) => document.fonts.add(ready)).catch(() => loaded.delete(font.id));
  }
  listeners.forEach((tell) => tell());
}
const subscribe = (tell) => { listeners.add(tell); return () => listeners.delete(tell); };

/** A right-to-left own font (Nastaliq needs taller lines than 1.2, as in the PDF). */
export const isRightToLeft = (family) => fonts.some((font) => font.family === family && font.rightToLeft);

export function useFonts() {
  useEffect(() => {
    if (asked) return;
    asked = true;
    api.fonts().then((data) => setFonts(data.fonts)).catch(() => { asked = false; });
  }, []);
  return useSyncExternalStore(subscribe, () => fonts);
}

const readFile = (file) => new Promise((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(String(reader.result));
  reader.onerror = () => reject(new Error('The file could not be read'));
  reader.readAsDataURL(file);
});

function FontsDialog({ onClose, onAdded }) {
  const list = useFonts();
  const [busy, setBusy] = useState('');
  const [message, setMessage] = useState(null);
  useEffect(() => {
    const escape = (event) => { if (event.key === 'Escape') onClose(); };
    document.addEventListener('keydown', escape);
    return () => document.removeEventListener('keydown', escape);
  }, [onClose]);

  const add = async (files) => {
    setMessage(null);
    const done = [];
    for (const file of files) {
      if (file.size > MAX_BYTES) { setMessage({ kind: 'error', text: `${file.name} is larger than 15 MB` }); return; }
      setBusy(`Adding ${file.name}…`);
      try {
        // eslint-disable-next-line no-await-in-loop -- one after another: files of one family belong together
        const result = await api.addFont({ file: await readFile(file), fileName: file.name });
        setFonts(result.fonts);
        done.push(result.font);
      } catch (error) { setMessage({ kind: 'error', text: `${file.name}: ${error.message}` }); setBusy(''); return; }
    }
    setBusy('');
    if (done.length) {
      setMessage({ kind: 'ok', text: `Added: ${done.map((f) => `${f.family} (${STYLE_LABELS[f.style]})`).join(', ')}` });
      onAdded?.(done[0].family);
    }
  };
  const remove = async (font) => {
    if (!window.confirm(`Remove ${font.family} (${STYLE_LABELS[font.style]})? Reports that use it will print in SansSerif.`)) return;
    try { setFonts((await api.deleteFont(font.id)).fonts); } catch (error) { setMessage({ kind: 'error', text: error.message }); }
  };
  const families = [...new Set(list.map((f) => f.family))];

  return createPortal(
    <div className="fonts-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="fonts-dialog" role="dialog" aria-label="Fonts">
        <header><div><h2>Fonts</h2><p>Add a TrueType file (.ttf) and it is in every font list, on the canvas, in PDF and Excel.</p></div>
          <button type="button" className="btn quiet sm" onClick={onClose} aria-label="Close">✕</button></header>
        <label className={`fonts-drop ${busy ? 'busy' : ''}`} onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); if (!busy) add(Array.from(e.dataTransfer.files)); }}>
          <b>{busy || '＋ Add font files'}</b>
          <small>Choose or drop .ttf files. Regular, bold and italic files of one font go together.</small>
          <input type="file" accept=".ttf,font/ttf" multiple hidden disabled={Boolean(busy)} onChange={(e) => { const files = Array.from(e.target.files || []); e.target.value = ''; add(files); }} />
        </label>
        {message && <p className={`fonts-note ${message.kind}`} role="status">{message.text}</p>}
        {!families.length && <p className="fonts-empty">No own fonts yet.</p>}
        <ul className="fonts-list">
          {families.map((family) => {
            const files = list.filter((f) => f.family === family);
            const urdu = files.some((f) => f.urdu);
            const rtl = files.some((f) => f.rightToLeft);
            return (
              <li key={family}>
                <div className="fonts-head"><b>{family}</b>{urdu ? <span className="fonts-chip">Urdu</span> : rtl ? <span className="fonts-chip">Arabic</span> : null}</div>
                <p className="fonts-sample" style={{ fontFamily: `"${family}"` }} dir={rtl ? 'rtl' : undefined}>{rtl ? 'یہ اردو رسم الخط کا نمونہ ہے ۱۲۳' : 'The quick brown fox 123'}</p>
                <div className="fonts-files">
                  {files.map((font) => (
                    <span key={font.id} title={font.fileName}>{STYLE_LABELS[font.style]} · {Math.max(1, Math.round(font.bytes / 1024)).toLocaleString()} KB
                      <button type="button" onClick={() => remove(font)} aria-label={`Remove ${font.family} ${STYLE_LABELS[font.style]}`} title="Remove">✕</button></span>
                  ))}
                </div>
              </li>
            );
          })}
        </ul>
        <p className="fonts-hint">Urdu, Arabic and Persian text is joined and written right to left by itself; align it right. Naskh and Nastaliq
          fonts are joined in PDF as well; give Nastaliq lines taller boxes. Use only fonts whose licence lets you embed them.</p>
      </div>
    </div>, document.body);
}

/** A font list: the designer's own names, the fonts added to this server, and "Add a font…". */
export function FontSelect({ value, onChange, ...props }) {
  const list = useFonts();
  const [adding, setAdding] = useState(false);
  const families = [...new Set(list.map((f) => f.family))].filter((name) => !FONT_NAMES.includes(name));
  const current = value || 'SansSerif';
  const unknown = !FONT_NAMES.includes(current) && !families.includes(current);
  return (
    <>
      <select {...props} value={current} onChange={(e) => { if (e.target.value === '+add') setAdding(true); else onChange(e.target.value); }}>
        {unknown && <option value={current}>{current} (file not added)</option>}
        {families.length > 0 && <optgroup label="Own fonts">{families.map((name) => <option key={name} style={{ fontFamily: `"${name}"` }}>{name}</option>)}</optgroup>}
        <optgroup label="Built in">{FONT_NAMES.map((name) => <option key={name}>{name}</option>)}</optgroup>
        <option value="+add">＋ Add a font (Urdu…)</option>
      </select>
      {adding && <FontsDialog onClose={() => setAdding(false)} onAdded={onChange} />}
    </>
  );
}

export { FontsDialog };
