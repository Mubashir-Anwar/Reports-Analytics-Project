import { useEffect, useRef, useState } from 'react';
import { api } from './api.js';
import { AdapterOptions } from './panels.jsx';
import { reportPayload } from './store.js';
import { startValue } from './relativeDates.js';
import { reportIdFrom } from './SetupForm.jsx';

const ID_OK = /^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/;

const EXPORTS = [['pdf', 'PDF', '📄'], ['xlsx', 'Excel', '📊'], ['csv', 'CSV', '🧾']];
const LIMITS = [['20', 'First 20 records'], ['all', 'All records']];

/**
 * Like a classic report designer's Preview: a toolbar with the Data Adapter and the green Run button, the
 * Input Parameters panel on the left, the filled report in the viewer, and export buttons (PDF / Excel / CSV).
 * The draft on screen is used as it is — nothing has to be saved or published to preview it.
 */
export default function Preview({ state, notify, dispatch }) {
  const { report, design } = state;
  const [values, setValues] = useState({});
  const [adapter, setAdapter] = useState(''); // the report's own Data Adapter once the list is loaded
  const [adapters, setAdapters] = useState([]);
  const [limit, setLimit] = useState('20');
  const [busy, setBusy] = useState('');
  const [view, setView] = useState(null); // { url, info, run }
  const [showParams, setShowParams] = useState(true);
  const started = useRef(false);
  const [askName, setAskName] = useState(null); // the name typed when the report has none yet

  useEffect(() => () => { if (view?.url) URL.revokeObjectURL(view.url); }, [view]);
  useEffect(() => {
    api.connections('designer').then((r) => {
      setAdapters(r.connections);
      const usable = r.connections.filter((a) => a.configured);
      const own = usable.find((a) => a.id === report.connectionId);
      setAdapter((own || usable[0] || {}).id || '');
    }).catch(() => setAdapters([]));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- load once

  const asked = report.parameters.filter((p) => p.prompt !== false);
  const computed = report.parameters.filter((p) => p.prompt === false);
  const valueOf = (p) => values[p.name] ?? startValue(p);
  const parameterValues = () => Object.fromEntries(asked.map((p) => [p.name, valueOf(p)]));
  // a letter / certificate / form of fixed text: no collection and no query, so it needs no Data Adapter
  const noData = !String(report.querySource || '').trim() && !String(report.collection || '').trim();
  const live = Boolean(adapter) || noData;
  const adapterName = noData ? 'No data (fixed text)' : adapters.find((a) => a.id === adapter)?.name || adapter;
  const missing = asked.filter((p) => p.required && !String(valueOf(p)).trim());

  /** Fill the report and show it as PDF. Remembers how it was filled so the exports give the same data. */
  const run = async (given) => {
    const current = given ? { ...state, report: { ...report, ...given } } : state;
    if (!ID_OK.test(String(current.report.id || ''))) { setAskName(String(current.report.title || '').trim() === 'New report' ? '' : String(current.report.title || '')); return; }
    if (!live) { notify('Choose a Data Adapter first (add one on the Data Adapters tab)', 'error'); return; }
    if (missing.length) { notify(`Fill in: ${missing.map((p) => p.label || p.name).join(', ')}`, 'error'); setShowParams(true); return; }
    setBusy('run');
    const startedAt = performance.now();
    try {
      const parameters = parameterValues();
      let runInfo;
      let blob;
      if (limit === '20') {
        const data = await api.draftPreview(reportPayload(current), parameters, noData ? '' : adapter);
        runInfo = { mode: 'render', body: { design, columns: data.columns, rows: data.rows, parameters, title: current.report.title, reportId: current.report.id, folder: report.folder, connectionId: data.connectionId }, text: `${adapterName} · first ${data.rows.length} of ${data.rowCount} records` };
        blob = await api.renderFile({ ...runInfo.body, format: 'pdf' });
      } else {
        runInfo = { mode: 'run', definition: reportPayload(current), parameters, connectionId: noData ? '' : adapter, text: `${adapterName} · all records` };
        blob = await api.draftRun(runInfo.definition, { parameters, connectionId: adapter, format: 'pdf' });
      }
      const seconds = ((performance.now() - startedAt) / 1000).toFixed(1);
      setView({ url: URL.createObjectURL(blob), info: `${runInfo.text} · filled in ${seconds} s`, run: runInfo });
      notify(`Preview ready: ${runInfo.text}`, 'ok');
    } catch (error) {
      notify(`Preview failed: ${error.message}`, 'error');
    } finally { setBusy(''); }
  };

  // Like Studio, switching to Preview fills the report straight away with its Data Adapter (first 20 records).
  useEffect(() => {
    if (started.current || !live) return;
    started.current = true;
    if (!missing.length) run(); // asks for the name first when the report has none
  }, [adapter, live]); // eslint-disable-line react-hooks/exhaustive-deps

  /** "What should the report be called?" — the name gives the ID; then the preview runs. */
  const applyName = (event) => {
    event.preventDefault();
    const title = String(askName || '').trim();
    const id = reportIdFrom(title);
    if (!id) { notify('Type a name with at least one letter or number', 'error'); return; }
    const patch = { title, ...(report.isNew && !(report.idManual && ID_OK.test(String(report.id || ''))) ? { id, idManual: false } : {}) };
    dispatch?.({ type: 'REPORT', patch });
    setAskName(null);
    run(patch);
  };

  const exportAs = async (format) => {
    if (!view) return;
    setBusy(`export-${format}`);
    try {
      const { run: r } = view;
      // made on the server in the background: the file is saved when ready, even if you go to another page
      const name = `${report.id || 'report'}.${format}`;
      const { queued } = r.mode === 'render'
        ? await api.renderFileLater({ ...r.body, format }, name)
        : await api.draftRunLater(r.definition, { parameters: r.parameters, connectionId: r.connectionId, format });
      notify(queued ? `${name} is being made on the server — it downloads by itself when ready (you can keep working)` : `Exported ${name}`, 'ok');
    } catch (error) {
      notify(`Export failed: ${error.message}`, 'error');
    } finally { setBusy(''); }
  };

  return (
    <div className="preview studio">
      <div className="preview-toolbar">
        <button className={`btn sm quiet ${showParams ? 'on' : ''}`} onClick={() => setShowParams(!showParams)} title="Show or hide the Input Parameters">☰ Parameters</button>
        <label className="tb-field" title="The Data Adapter used to fill the report">
          <span>Data Adapter</span>
          <select value={noData ? '' : adapter} disabled={noData} onChange={(e) => setAdapter(e.target.value)}>
            {(!adapter || noData) && <option value="">{noData ? 'None needed (fixed text)' : adapters.length ? 'Choose a Data Adapter…' : 'No Data Adapter yet'}</option>}
            <AdapterOptions adapters={adapters} disableUnconfigured describe={(a) => `${a.name}${a.database ? ` · ${a.database}` : ''}${a.configured ? '' : ' — not configured'}`} />
          </select>
        </label>
        <label className="tb-field" title="How many records to read from the data adapter">
          <span>Records</span>
          <select value={limit} disabled={!live || noData} onChange={(e) => setLimit(e.target.value)}>
            {LIMITS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
          </select>
        </label>
        <button className="run-btn" onClick={() => run()} disabled={Boolean(busy) || !live} title="Run the report (fill it with data)">{busy === 'run' ? '…' : '▶'}</button>
        <span className="tb-sep" />
        <span className="tb-label">Export</span>
        {EXPORTS.map(([format, label, icon]) => (
          <button key={format} className="btn sm" disabled={!view || Boolean(busy)} onClick={() => exportAs(format)} title={`Save the filled report as ${label}`}>
            {busy === `export-${format}` ? '…' : icon} {label}
          </button>
        ))}
        {view?.url && <a className="btn sm quiet" href={view.url} target="_blank" rel="noreferrer" title="Open the PDF in a new tab">↗</a>}
      </div>
      <div className={`preview-main ${showParams ? 'with-params' : ''}`}>
        {showParams && (
          <aside className="preview-params">
            <div className="pp-head">Input Parameters</div>
            {!asked.length && <p className="hint">This report has no parameters to ask.</p>}
            {asked.map((p) => (
              <label key={p.name} className="pp-field">
                <span>{p.label || p.name}{p.required ? <b className="req"> *</b> : null}</span>
                {p.type === 'boolean' ? (
                  <select value={valueOf(p)} onChange={(e) => setValues({ ...values, [p.name]: e.target.value })}><option value="true">true</option><option value="false">false</option></select>
                ) : p.allowedValues?.length ? (
                  <select value={valueOf(p)} onChange={(e) => setValues({ ...values, [p.name]: e.target.value })}>
                    <option value="">— choose —</option>
                    {p.allowedValues.map((v) => <option key={String(v)} value={String(v)}>{String(v)}</option>)}
                  </select>
                ) : (
                  <input type={p.type === 'date' ? 'date' : ['integer', 'number'].includes(p.type) ? 'number' : 'text'} value={valueOf(p)} onChange={(e) => setValues({ ...values, [p.name]: e.target.value })} onKeyDown={(e) => { if (e.key === 'Enter') run(); }} />
                )}
                <small>$P&#123;{p.name}&#125; · {p.type}</small>
              </label>
            ))}
            {computed.length > 0 && <p className="hint">Computed (not asked): {computed.map((p) => p.name).join(', ')}</p>}
            <button className="btn primary pp-run" onClick={() => run()} disabled={Boolean(busy)}>▶ Run Report</button>
          </aside>
        )}
        <section className="viewer">
          {busy === 'run' && <div className="viewer-busy">Filling the report…</div>}
          {view?.url
            ? <iframe title="Report preview" src={view.url} className="pdf-frame" />
            : !busy && <div className="empty">{!live ? 'Choose a Data Adapter in the toolbar, then press ▶ Run.' : missing.length ? 'Fill in the Input Parameters, then press ▶ Run.' : 'Press ▶ Run to fill the report.'}</div>}
        </section>
      </div>
      {askName !== null && (
        <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && setAskName(null)}>
          <form className="modal ask-name" role="dialog" aria-label="Name the report" onSubmit={applyName}>
            <h3>What should the report be called?</h3>
            <p className="muted">It has no name yet. The name also gives its ID (the address apps use to run it).</p>
            <input autoFocus value={askName} onChange={(e) => setAskName(e.target.value)} placeholder="e.g. Course certificate" aria-label="Report name" />
            {askName.trim() && <small className="muted">ID: <code>{reportIdFrom(askName) || '—'}</code></small>}
            <div className="row">
              <button type="submit" className="btn primary" disabled={!reportIdFrom(askName)}>▶ Name it and preview</button>
              <button type="button" className="btn" onClick={() => setAskName(null)}>Cancel</button>
            </div>
          </form>
        </div>
      )}
      <div className="preview-statusbar">{view ? view.info : 'Not run yet'}{live ? '' : ' — choose a Data Adapter (add one on the Data Adapters tab)'}</div>
    </div>
  );
}
