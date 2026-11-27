// Import a dashboard template (.pbit) or the report part of a .pbix: choose the pages, match every table of the file to a dataset here (or a new one
// on a data adapter), then each page becomes a dashboard. The server reads the file twice (inspect, then import),
// so nothing is kept between the steps.
import { useEffect, useMemo, useRef, useState } from 'react';
import { Notice } from '../portal/Shell.jsx';
import { bi } from './api.js';

const MONGO_QUERY = '[\n  { "$match": {} }\n]';

/** The file as base64 text (what the server expects for a .pbit). */
export function readBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).replace(/^data:[^,]*,/, ''));
    reader.onerror = () => reject(new Error('The file could not be read'));
    reader.readAsDataURL(file);
  });
}

function choiceFor(table, adapters) {
  const s = table.suggestion || {};
  if (s.dataset) return { mode: 'dataset', dataset: s.dataset };
  if (s.connectionId) return { mode: 'new', ...s, name: table.name };
  const first = adapters[0];
  if (!first) return { mode: 'skip' };
  return { mode: 'new', name: table.name, connectionId: first.id, queryLanguage: first.type === 'mongodb' ? 'aggregation-json' : 'sql',
    collection: first.type === 'mongodb' ? table.name : '', querySource: first.type === 'mongodb' ? MONGO_QUERY : `SELECT *\nFROM ${table.name}` };
}

export default function PowerBiImport({ file, content, parts, info, datasets, onDone, onCancel }) {
  const pbix = info.kind === 'pbix';
  const [pages, setPages] = useState(() => info.pages.filter((p) => p.visuals > 0).map((p) => p.index));
  const [choices, setChoices] = useState(() => Object.fromEntries(info.tables.map((t) => [t.name, choiceFor(t, info.adapters)])));
  const [folder, setFolder] = useState('Analytics');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const dialog = useRef(null);
  useEffect(() => {
    dialog.current?.focus();
    const close = (e) => { if (e.key === 'Escape' && !busy) onCancel(); };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [busy, onCancel]);

  const adapterType = Object.fromEntries(info.adapters.map((a) => [a.id, a.type]));
  const needed = useMemo(() => info.tables.filter((t) => t.pages.some((p) => pages.includes(p))), [info.tables, pages]);
  const set = (table, change) => setChoices((all) => ({ ...all, [table]: { ...all[table], ...change } }));
  const changeAdapter = (table, id) => {
    const mongo = adapterType[id] === 'mongodb';
    const now = choices[table];
    const wasMongo = now.queryLanguage !== 'sql';
    set(table, { connectionId: id, queryLanguage: mongo ? 'aggregation-json' : 'sql',
      ...(mongo === wasMongo ? {} : { collection: mongo ? table : '', querySource: mongo ? MONGO_QUERY : `SELECT *\nFROM ${table}` }) });
  };
  const visuals = info.pages.filter((p) => pages.includes(p.index)).reduce((n, p) => n + p.visuals, 0);

  const submit = async (event) => {
    event.preventDefault();
    setBusy(true); setError('');
    const tables = {};
    for (const t of needed) {
      const c = choices[t.name];
      tables[t.name] = c.mode === 'skip' ? { skip: true } : c.mode === 'dataset' ? { dataset: c.dataset }
        : { name: c.name, connectionId: c.connectionId, queryLanguage: c.queryLanguage, collection: c.collection, querySource: c.querySource };
    }
    try {
      onDone(await bi.importPowerBi({ ...(parts ? { parts } : { content }), name: file.name, pages, tables, folder: folder.trim() || 'Analytics' }));
    } catch (failure) { setError(failure.message); setBusy(false); }
  };

  return (
    <div className="pbi-back" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onCancel(); }}>
      <form className="pbi-dialog" role="dialog" aria-modal="true" aria-labelledby="pbi-title" tabIndex={-1} ref={dialog} onSubmit={submit}>
        <header>
          <h2 id="pbi-title">{pbix ? 'Import a report file (.pbix)' : 'Import a dashboard template (.pbit)'}</h2>
          <p className="muted">{file.name} · {info.pages.length} page{info.pages.length === 1 ? '' : 's'} · {info.tables.length} table{info.tables.length === 1 ? '' : 's'} used{info.relationships ? ` · ${info.relationships} relationship${info.relationships === 1 ? '' : 's'}` : ''}</p>
        </header>
        <div className="pbi-body">
          {pbix && (
            <p className="pbi-note">Only the report pages were read from this .pbix — its data model is in a closed format, so
              {' '}<b>measures, column types and relationships are not in it</b>. Visuals built on measures are left out; relate the
              datasets in <b>Model</b> if a visual uses two tables. For the full model, export the report as a template (.pbit) in the
              desktop tool that made it, and import that instead.</p>
          )}
          <section>
            <h3>1. Pages <small className="muted">each becomes a dashboard</small></h3>
            {info.pages.map((p) => (
              <div key={p.index} className="pbi-page">
                <label className="check">
                  <input type="checkbox" disabled={!p.visuals} checked={pages.includes(p.index)}
                    onChange={(e) => setPages((list) => (e.target.checked ? [...list, p.index].sort((a, b) => a - b) : list.filter((i) => i !== p.index)))} />
                  <b>{p.name}</b>
                  <span className="muted">{p.visuals} visual{p.visuals === 1 ? '' : 's'}{p.decorative ? `, ${p.decorative} text box/image left out` : ''}</span>
                </label>
                {p.notes.length > 0 && (
                  <details><summary>{p.notes.length} change{p.notes.length === 1 ? '' : 's'} on this page</summary>
                    <ul>{p.notes.map((note) => <li key={note}>{note}</li>)}</ul></details>
                )}
              </div>
            ))}
          </section>

          <section>
            <h3>2. Data <small className="muted">a dataset here for every table of the file</small></h3>
            {needed.length === 0 && <p className="muted">Choose a page first.</p>}
            {needed.map((t) => {
              const c = choices[t.name];
              const unconverted = t.measures.filter((m) => !m.converted);
              return (
                <fieldset key={t.name} className="pbi-table">
                  <legend><b>{t.name}</b> <span className="muted">{t.source.kind ? `from ${t.source.kind}${t.source.item ? ` · ${t.source.item}` : ''} · ` : ''}{t.columns.length} column{t.columns.length === 1 ? '' : 's'}{pbix ? ' used by the visuals' : ''}</span></legend>
                  <div className="pbi-row">
                    <select aria-label={`Data for ${t.name}`} value={c.mode} onChange={(e) => {
                      const mode = e.target.value;
                      if (mode === 'dataset') set(t.name, { mode, dataset: c.dataset || datasets[0]?.id || '' });
                      else if (mode === 'new') setChoices((all) => ({ ...all, [t.name]: { ...choiceFor({ ...t, suggestion: {} }, info.adapters), ...(t.suggestion?.connectionId ? t.suggestion : {}), name: t.name, mode } }));
                      else set(t.name, { mode });
                    }}>
                      <option value="dataset" disabled={!datasets.length}>Use a dataset here</option>
                      <option value="new" disabled={!info.adapters.length}>New dataset</option>
                      <option value="skip">Leave out (its visuals too)</option>
                    </select>
                    {c.mode === 'dataset' && (
                      <select aria-label={`Dataset for ${t.name}`} value={c.dataset} onChange={(e) => set(t.name, { dataset: e.target.value })}>
                        {datasets.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                      </select>
                    )}
                    {c.mode === 'new' && (
                      <select aria-label={`Data adapter for ${t.name}`} value={c.connectionId} onChange={(e) => changeAdapter(t.name, e.target.value)}>
                        {info.adapters.map((a) => <option key={a.id} value={a.id}>{a.id} ({a.type})</option>)}
                      </select>
                    )}
                  </div>
                  {c.mode === 'new' && (
                    <div className="pbi-new">
                      <label className="field"><span>Dataset name</span><input value={c.name} maxLength={120} onChange={(e) => set(t.name, { name: e.target.value })} required /></label>
                      {c.queryLanguage !== 'sql' && <label className="field"><span>Collection</span><input value={c.collection} onChange={(e) => set(t.name, { collection: e.target.value })} required /></label>}
                      <label className="field wide"><span>{c.queryLanguage === 'sql' ? 'SQL query' : 'Aggregation pipeline'}</span>
                        <textarea className="code-area" rows={3} spellCheck={false} value={c.querySource} onChange={(e) => set(t.name, { querySource: e.target.value })} required /></label>
                    </div>
                  )}
                  <p className="hint">Columns are matched by name ({t.columns.slice(0, 6).map((col) => col.name).join(', ')}{t.columns.length > 6 ? '…' : ''}).{unconverted.length ? ` Measures left out (${pbix ? 'a .pbix does not include their formulas' : 'their formulas cannot be converted'}): ${unconverted.map((m) => m.name).join(', ')}.` : ''}</p>
                </fieldset>
              );
            })}
          </section>

          <section>
            <h3>3. Folder</h3>
            <label className="field"><span>Save the dashboards in</span><input value={folder} onChange={(e) => setFolder(e.target.value)} maxLength={200} /></label>
          </section>
          <p className="muted small">Not converted: other formula measures, query (data shaping) steps, page and report filters, formatting, text boxes and images. New datasets are imported from their data adapter right after.</p>
          {error && <Notice message={{ text: error, kind: 'error' }} />}
        </div>
        <footer>
          <button type="button" className="btn" onClick={onCancel} disabled={busy}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy || !pages.length || !visuals}>
            {busy ? 'Importing…' : `Import ${pages.length} dashboard${pages.length === 1 ? '' : 's'}`}
          </button>
        </footer>
      </form>
    </div>
  );
}
