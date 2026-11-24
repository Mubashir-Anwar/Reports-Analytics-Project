import { useEffect, useState } from 'react';
import { api } from './api.js';
import { defaultElement, emptyDesign } from './designModel.js';

// File → Import design: an existing letter / certificate / form (PDF, Illustrator, Word, PNG/JPEG) is rebuilt
// on the page; its text can then be turned into fields ("Make it a field").

const ACCEPT = '.pdf,.ai,.docx,.doc,.odt,.rtf,.png,.jpg,.jpeg,.webp,.gif,.bmp';
const MAX_MB = 15;

const kindOf = (name) => {
  const lower = name.toLowerCase();
  if (/\.(png|jpe?g|webp|gif|bmp)$/.test(lower)) return 'image';
  if (/\.(docx?|odt|rtf)$/.test(lower)) return 'word';
  if (lower.endsWith('.ai')) return 'illustrator';
  return 'pdf';
};
const KIND_LABEL = { pdf: 'PDF', word: 'Word document', illustrator: 'Illustrator file', image: 'Picture (scan / photo)' };

const readDataUrl = (file) => new Promise((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(String(reader.result));
  reader.onerror = () => reject(new Error('The file could not be read'));
  reader.readAsDataURL(file);
});

/** The imported page as the report design: page size, Background (the picture) and Detail (one page per record). */
export function designFromImport(result, previous) {
  const base = emptyDesign();
  const place = (list, shift = 0) => list.map(({ fields, region, ...el }) => {
    const { id, ...rest } = el; void id; void fields; void region;
    return { ...defaultElement(el.type, el.x, el.y), ...rest, y: Math.max(0, Math.round((el.y - shift) * 10) / 10) };
  });
  const height = result.page.height;
  // a scan's header / footer (found by the server) go to the Page Header / Page Footer bands: they repeat on every page
  const headerEnd = Math.min(result.regions?.header || 0, height);
  const footerStart = result.regions?.footer > headerEnd ? Math.min(result.regions.footer, height) : height;
  const inRegion = (name) => result.elements.filter((el) => el.region === name);
  const body = result.elements.filter((el) => el.region !== 'header' && el.region !== 'footer');
  return {
    ...base,
    calculatedFields: previous?.calculatedFields || [],
    functions: previous?.functions || [],
    variables: (previous?.variables || []).filter((v) => !v.resetGroup),
    datasets: previous?.datasets || [],
    ...(previous?.fieldFormats ? { fieldFormats: previous.fieldFormats } : {}),
    page: { ...base.page, width: result.page.width, height, margins: result.page.margins, sheetLayout: previous?.page?.sheetLayout || 'grid' },
    bands: {
      ...base.bands,
      background: { height: result.background.length ? height : 0, elements: place(result.background) },
      // whole points that add up to no more than the page (Jasper refuses bands taller than the page)
      pageHeader: { height: Math.round(headerEnd), elements: place(inRegion('header')) },
      detail: { height: Math.round(footerStart) - Math.round(headerEnd), elements: place(body, Math.round(headerEnd)) },
      pageFooter: { height: Math.max(0, Math.floor(height) - Math.round(footerStart)), elements: place(inRegion('footer'), Math.round(footerStart)) },
    },
  };
}

export default function ImportDesignDialog({ design, purpose = '', onApply, onClose }) {
  const [file, setFile] = useState(null);
  const [mode, setMode] = useState('elements');
  const [textOnTop, setTextOnTop] = useState(true);
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);
  const kind = file ? kindOf(file.name) : null;

  useEffect(() => {
    const onKey = (event) => { if (event.key === 'Escape' && !busy) onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [busy, onClose]);

  const choose = (chosen) => {
    setResult(null);
    setError('');
    setPage(1);
    if (!chosen) { setFile(null); return; }
    if (chosen.size > MAX_MB * 1024 * 1024) { setFile(null); setError(`${chosen.name} is larger than ${MAX_MB} MB. Save a smaller copy and import that.`); return; }
    setFile(chosen);
    setMode(kindOf(chosen.name) === 'image' ? 'picture' : 'elements');
  };

  const run = async () => {
    if (!file) return;
    setBusy(true);
    setError('');
    try {
      const data = await readDataUrl(file);
      const found = await api.importDesign(data, file.name, mode, page, textOnTop);
      setResult(found);
      setPage(found.pageNumber || 1);
    } catch (e) {
      setResult(null);
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const counts = result ? result.elements.reduce((acc, el) => ({ ...acc, [el.type]: (acc[el.type] || 0) + 1 }), {}) : {};
  const texts = (counts.staticText || 0) + (counts.textField || 0);

  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <div className="modal imp-dialog" role="dialog" aria-label="Import design">
        <h3>{purpose ? `Import your ${purpose} design` : 'Import design'}</h3>
        <p className="muted">Give {purpose ? `your existing ${purpose}` : 'an existing letter, certificate or form'}. It is rebuilt on the page as it looks; then click any text and choose <b>Make it a field</b> to fill it from the data. Write <code>{'{{Name}}'}</code> in the file and it becomes the field <code>Name</code> by itself.</p>

        <label className={`imp-drop ${file ? 'has-file' : ''}`}
          onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); choose(e.dataTransfer.files?.[0]); }}>
          <input type="file" accept={ACCEPT} hidden onChange={(e) => { choose(e.target.files?.[0]); e.target.value = ''; }} />
          {file ? <><b>{file.name}</b><span>{KIND_LABEL[kind]} · {(file.size / 1024 / 1024).toFixed(1)} MB · click to choose another</span></>
            : <><b>Choose a file or drop it here</b><span>PDF · Illustrator .ai · Word .docx · PNG / JPEG (up to {MAX_MB} MB)</span></>}
        </label>

        {file && kind !== 'image' && (
          <div className="imp-modes" role="radiogroup" aria-label="How to rebuild it">
            <label className={mode === 'elements' ? 'on' : ''}>
              <input type="radio" name="imp-mode" checked={mode === 'elements'} onChange={() => { setMode('elements'); setResult(null); }} />
              <b>Rebuild as elements</b>
              <span>Every text, line, box and picture becomes its own element. Sharp in PDF, everything editable. Curved drawings are left out.</span>
            </label>
            <label className={mode === 'picture' ? 'on' : ''}>
              <input type="radio" name="imp-mode" checked={mode === 'picture'} onChange={() => { setMode('picture'); setResult(null); }} />
              <b>Exact picture</b>
              <span>The page is the background exactly as it looks (best for certificates with artwork); the text is laid over it, editable.</span>
            </label>
          </div>
        )}
        {file && kind === 'image' && <p className="imp-info">The picture becomes the page background, and its text is read with OCR (English and numbers; Urdu text stays in the picture).</p>}
        {file && (mode === 'picture' || kind === 'image') && (
          <label className="imp-check"><input type="checkbox" checked={textOnTop} onChange={(e) => { setTextOnTop(e.target.checked); setResult(null); }} />
            Put the text over the picture as editable boxes (turn off to place fields yourself)</label>
        )}
        {file && kind !== 'image' && (
          <label className="imp-page">Page <input type="number" min={1} max={result?.pageCount || 999} value={page} onChange={(e) => { setPage(Math.max(1, Number(e.target.value) || 1)); setResult(null); }} />
            {result?.pageCount > 1 && <span className="muted">of {result.pageCount}</span>}</label>
        )}

        {error && <div className="imp-error" role="alert">{error}</div>}
        {result && (
          <div className="imp-result">
            <b>Ready: {result.page.width} × {result.page.height} pt page{result.background.length ? ', picture in the Background band' : ''}</b>
            <span>{texts} text{texts === 1 ? '' : 's'}{counts.textField ? ` (${counts.textField} with fields)` : ''} · {counts.line || 0} lines · {counts.rectangle || 0} boxes · {counts.image || 0} pictures</span>
            {result.fields.length > 0 && <span>Fields: {result.fields.map((f) => <code key={f}>{f}</code>)}</span>}
            {result.notes.length > 0 && <ul>{result.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
            <small className="muted">Applying replaces the current layout (bands and elements). Ctrl+Z brings it back.</small>
          </div>
        )}

        <div className="row">
          {!result && <button type="button" className="btn primary" disabled={!file || busy} onClick={run}>{busy ? (kind === 'image' ? 'Reading the text…' : kind === 'word' ? 'Opening in Word…' : 'Reading…') : 'Read the file'}</button>}
          {result && <button type="button" className="btn primary" onClick={() => onApply(designFromImport(result, design), result.fields, file.name)}>Apply to the page</button>}
          <button type="button" className="btn" disabled={busy} onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
