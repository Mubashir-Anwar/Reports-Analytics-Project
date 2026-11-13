// Source tab (like Jaspersoft Studio's Design | Source | Preview): the whole report definition as text.
// It is the JSON inside the .mrd file. Edit it and Apply: the server checks it exactly like it will when
// the report is saved or run. Pictures are shown as "@image:<element id>" so the text stays readable.
import { useEffect, useMemo, useState } from 'react';
import { api } from './api.js';
import { mapElements } from './designModel.js';
import { buildMrd, downloadText } from './mrd.js';
import { reportPayload } from './store.js';

const IMAGE_TOKEN = '@image:';

function toSource(state) {
  const definition = reportPayload(state);
  const design = mapElements(definition.design, (el) => (el.src && String(el.src).startsWith('data:') ? { ...el, src: `${IMAGE_TOKEN}${el.id}` } : el));
  return JSON.stringify({ ...definition, design }, null, 2);
}

/** Line and column of a JSON.parse error ("… at position 123"). */
function where(text, message) {
  const match = String(message).match(/position (\d+)/);
  if (!match) return '';
  const before = text.slice(0, Number(match[1]));
  const line = before.split('\n').length;
  return ` (line ${line}, column ${before.length - before.lastIndexOf('\n')})`;
}

export default function SourceView({ state, dispatch, notify }) {
  const source = useMemo(() => toSource(state), [state.report, state.design]); // eslint-disable-line react-hooks/exhaustive-deps
  const [text, setText] = useState(source);
  const [edited, setEdited] = useState(false);
  const [problem, setProblem] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (!edited) setText(source); }, [source, edited]);

  const images = useMemo(() => {
    const found = {};
    mapElements(state.design, (el) => { if (el.src) found[el.id] = el.src; return el; });
    return found;
  }, [state.design]);

  const apply = async () => {
    let parsed;
    try { parsed = JSON.parse(text); } catch (error) { setProblem(`Not valid JSON: ${error.message}${where(text, error.message)}`); return; }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) { setProblem('The source must be one JSON object'); return; }
    const design = mapElements(parsed.design || { bands: {} }, (el) => (typeof el.src === 'string' && el.src.startsWith(IMAGE_TOKEN) ? { ...el, src: images[el.src.slice(IMAGE_TOKEN.length)] || '' } : el));
    if (!state.report.isNew && parsed.id !== state.report.id) { setProblem(`The id cannot change once the report is saved (it is ${state.report.id}). Use Save as file and open it as a new report instead.`); return; }
    setBusy(true);
    try {
      await api.validateDesign({ ...parsed, design });
      const { id, title, description, folder, connectionId, collection, queryLanguage, querySource, parameters, columns, design: _ignored, ...extra } = parsed;
      dispatch({
        type: 'REPORT',
        patch: {
          ...(state.report.isNew ? { id: String(id || '') } : {}), title: title || '', description: description || '', folder: folder || 'General',
          connectionId: connectionId || '', collection: collection || '', queryLanguage: queryLanguage || 'mongosh', querySource: querySource || '',
          parameters: parameters || [], columns: columns || [], extra: { ...state.report.extra, ...extra },
        },
      });
      dispatch({ type: 'DESIGN', update: () => ({ ...design, calculatedFields: design.calculatedFields || [], groups: design.groups || [], variables: design.variables || [] }) });
      setEdited(false);
      setProblem('');
      notify('Source applied — the design and the query were updated (Ctrl+Z undoes the design change)', 'ok');
    } catch (error) { setProblem(error.message); }
    finally { setBusy(false); }
  };

  return (
    <div className="source-view">
      <div className="source-toolbar">
        <b>Source</b><span className="muted">The report definition as JSON — the content of the .mrd file.</span>
        <span className="tb-spacer" />
        {edited && <span className="chip-warn">edited — not applied</span>}
        <button className="btn sm" disabled={!edited} onClick={() => { setEdited(false); setProblem(''); setText(source); }}>Revert</button>
        <button className="btn sm" onClick={() => { try { setText(JSON.stringify(JSON.parse(text), null, 2)); setProblem(''); } catch (error) { setProblem(`Not valid JSON: ${error.message}${where(text, error.message)}`); } }}>Format</button>
        <button className="btn sm" onClick={() => { navigator.clipboard?.writeText(text); notify('Source copied', 'ok'); }}>Copy</button>
        <button className="btn sm" onClick={() => { const payload = reportPayload(state); downloadText(buildMrd(payload), `${payload.id || 'report'}.mrd`); }}>Save as .mrd</button>
        <button className="btn sm primary" disabled={!edited || busy} onClick={apply}>{busy ? 'Checking…' : 'Apply'}</button>
      </div>
      {problem && <div className="source-problem" role="alert">⚠ {problem}</div>}
      <textarea className="source-text" spellCheck={false} value={text} onChange={(e) => { setText(e.target.value); setEdited(true); }}
        onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); apply(); } if (e.key === 'Tab') { e.preventDefault(); const t = e.target; const s = t.selectionStart; setText(`${text.slice(0, s)}  ${text.slice(t.selectionEnd)}`); setEdited(true); requestAnimationFrame(() => { t.selectionStart = t.selectionEnd = s + 2; }); } }} />
      <div className="source-status">{text.split('\n').length} lines · Ctrl+Enter = Apply · pictures appear as <code>@image:&lt;id&gt;</code> and are kept</div>
    </div>
  );
}
