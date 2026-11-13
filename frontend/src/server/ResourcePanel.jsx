// One report resource on the server, with tabs like JasperReports Server's resource page:
// Run · Overview · Parameters · Data Source · Query · History. Designers and admins can edit.
import { useCallback, useEffect, useState } from 'react';
import { Notice, formatWhen } from '../portal/Shell.jsx';
import RunPanel, { FORMAT_LABELS } from './RunPanel.jsx';
import { SchedulesList } from './Schedules.jsx';
import { serverApi } from './serverApi.js';

export const RESOURCE_TABS = [['run', 'Run'], ['overview', 'Overview'], ['parameters', 'Parameters'], ['datasource', 'Data Source'], ['query', 'Query'], ['schedules', 'Schedules'], ['versions', 'Versions'], ['history', 'History']];
const ACTION_ICONS = { published: '⇪', edited: '✎', copied: '⧉', moved: '➜', uploaded: '⤒', deleted: '✕' };

function OverviewTab({ info, onSave }) {
  const [form, setForm] = useState({ title: info.title, description: info.description, formats: info.formats, defaultFormat: info.defaultFormat });
  useEffect(() => setForm({ title: info.title, description: info.description, formats: info.formats, defaultFormat: info.defaultFormat }), [info]);
  const toggle = (f) => setForm((v) => ({ ...v, formats: v.formats.includes(f) ? v.formats.filter((x) => x !== f) : [...v.formats, f] }));
  const changed = JSON.stringify(form) !== JSON.stringify({ title: info.title, description: info.description, formats: info.formats, defaultFormat: info.defaultFormat });
  const d = info.design;
  return (
    <div className="stack">
      {info.canEdit ? (
        <>
          <label className="field"><span>Title</span><input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></label>
          <label className="field"><span>Description</span><textarea rows={2} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></label>
          <div className="field"><span>Output formats</span>
            <div className="row">
              {['pdf', 'xlsx', 'csv'].map((f) => <label key={f} className="check"><input type="checkbox" checked={form.formats.includes(f)} onChange={() => toggle(f)} /> {FORMAT_LABELS[f]}</label>)}
              <select value={form.defaultFormat} onChange={(e) => setForm({ ...form, defaultFormat: e.target.value })} title="Default format">{form.formats.map((f) => <option key={f} value={f}>default: {f}</option>)}</select>
            </div>
          </div>
          <div className="row"><button className="btn primary sm" disabled={!changed || !form.formats.length || !form.title.trim()} onClick={() => onSave(form)}>Save changes</button></div>
        </>
      ) : <p className="muted">{info.description || 'No description.'}</p>}
      <dl className="facts">
        <dt>ID</dt><dd><code>{info.id}</code></dd>
        <dt>Location</dt><dd>/{info.folder}</dd>
        <dt>File</dt><dd>{info.file.name}{info.file.size ? ` · ${(info.file.size / 1024).toFixed(1)} KB` : ''} · changed {formatWhen(info.file.modifiedAt)}</dd>
        <dt>Data source</dt><dd>{info.connection.name} <code>{info.connection.id}</code>{info.connection.missing ? ' (missing!)' : ''}</dd>
        <dt>Fields</dt><dd>{info.columns.join(', ') || '—'}</dd>
        <dt>Design</dt><dd>{d.bands.length ? `${d.bands.length} bands, ${d.elements} elements` : 'simple table layout (no band design)'}{Object.keys(d.components || {}).filter((k) => ['table', 'crosstab', 'list', 'chart', 'subreport', 'image'].includes(k)).map((k) => ` · ${d.components[k]} ${k}`).join('')}</dd>
        {d.subreports.length > 0 && <><dt>Subreports</dt><dd>{d.subreports.join(', ')}</dd></>}
        {info.usedBy.length > 0 && <><dt>Used by</dt><dd>{info.usedBy.join(', ')} (as a subreport)</dd></>}
      </dl>
    </div>
  );
}

function ParametersTab({ info, onSave }) {
  const toRows = () => info.parameters.map((p) => ({ ...p, allowedText: (p.allowedValues || []).join(', '), default: p.default ?? '' }));
  const [rows, setRows] = useState(toRows);
  useEffect(() => setRows(toRows()), [info]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!info.parameters.length) return <p className="muted">This report has no parameters (input controls).</p>;
  const change = (name, patch) => setRows((list) => list.map((r) => (r.name === name ? { ...r, ...patch } : r)));
  const save = () => onSave({
    parameters: rows.filter((r) => !r.computed).map((r) => ({
      name: r.name, label: r.label, description: r.description, required: r.required, default: r.default === '' ? null : r.default,
      allowedValues: r.allowedText.split(',').map((v) => v.trim()).filter(Boolean),
    })),
  });
  return (
    <div className="stack">
      <div className="table-wrap">
        <table className="grid-table">
          <thead><tr><th>Name</th><th>Type</th><th>Label (question)</th><th>Help text</th><th>Required</th><th>Default</th><th>Allowed values</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.name} className={r.computed ? 'muted' : ''}>
                <td><code>$P{'{'}{r.name}{'}'}</code></td>
                <td>{r.computed ? 'computed' : r.type}</td>
                {r.computed ? <td colSpan={5}><code>{r.expression}</code> <small className="muted">(calculated, not asked)</small></td> : (
                  <>
                    <td><input disabled={!info.canEdit} value={r.label || ''} onChange={(e) => change(r.name, { label: e.target.value })} /></td>
                    <td><input disabled={!info.canEdit} value={r.description || ''} onChange={(e) => change(r.name, { description: e.target.value })} /></td>
                    <td style={{ textAlign: 'center' }}><input type="checkbox" disabled={!info.canEdit} checked={r.required} onChange={(e) => change(r.name, { required: e.target.checked })} /></td>
                    <td><input disabled={!info.canEdit} value={r.default} onChange={(e) => change(r.name, { default: e.target.value })} placeholder={r.type === 'date' ? 'yyyy-mm-dd' : ''} /></td>
                    <td><input disabled={!info.canEdit} value={r.allowedText} onChange={(e) => change(r.name, { allowedText: e.target.value })} placeholder="a, b, c (a list)" /></td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {info.canEdit && <div className="row"><button className="btn primary sm" onClick={save}>Save parameters</button><span className="muted" style={{ fontSize: 12 }}>Names and types come from the design; change them in the Designer.</span></div>}
    </div>
  );
}

function DataSourceTab({ info, onSave }) {
  const [connections, setConnections] = useState([]);
  const [chosen, setChosen] = useState(info.connection.id);
  useEffect(() => setChosen(info.connection.id), [info]);
  useEffect(() => { if (info.canEdit) serverApi.connections().then((d) => setConnections(d.connections)).catch(() => {}); }, [info.canEdit]);
  const c = info.connection;
  return (
    <div className="stack">
      <dl className="facts">
        <dt>Data adapter</dt><dd>{c.name} <code>{c.id}</code>{c.missing ? ' — not registered any more!' : ''}</dd>
        {c.folder && <><dt>Adapter folder</dt><dd>/{c.folder}</dd></>}
        {c.database && <><dt>Database</dt><dd>{c.database}</dd></>}
        <dt>Collection</dt><dd><code>{info.collection}</code></dd>
      </dl>
      {info.canEdit && (
        <div className="row">
          <label className="field" style={{ minWidth: 240 }}><span>Use another data adapter</span>
            <select value={chosen} onChange={(e) => setChosen(e.target.value)}>
              {connections.map((x) => <option key={x.id} value={x.id}>{x.folder ? `${x.folder} / ` : ''}{x.name} ({x.id})</option>)}
              {!connections.some((x) => x.id === chosen) && <option value={chosen}>{chosen}</option>}
            </select>
          </label>
          <button className="btn primary sm" disabled={chosen === c.id} onClick={() => onSave({ connectionId: chosen })} style={{ alignSelf: 'flex-end' }}>Save</button>
        </div>
      )}
      <p className="muted" style={{ fontSize: 12 }}>Data adapters (connection strings) are managed in <a href="/admin">Admin → Data sources</a>; passwords are never shown here.</p>
    </div>
  );
}

function QueryTab({ info, onSave }) {
  const [text, setText] = useState(info.querySource || '');
  useEffect(() => setText(info.querySource || ''), [info]);
  if (info.querySource === null) return <p className="muted">Only designers and admins can see the query.</p>;
  return (
    <div className="stack">
      <p className="muted" style={{ fontSize: 12 }}>Language: <b>{info.queryLanguage}</b> · collection <code>{info.collection}</code>. The server checks the query again when you save (read-only stages only).</p>
      <textarea className="code-area" rows={14} spellCheck={false} readOnly={!info.canEdit} value={text} onChange={(e) => setText(e.target.value)} />
      {info.canEdit && <div className="row"><button className="btn primary sm" disabled={text === info.querySource} onClick={() => onSave({ querySource: text, queryLanguage: info.queryLanguage })}>Save query</button><button className="btn sm" disabled={text === info.querySource} onClick={() => setText(info.querySource)}>Undo</button></div>}
    </div>
  );
}

/** Every save of the report is kept; an older one can be looked at and made current again. */
function VersionsTab({ info, onRestored }) {
  const [versions, setVersions] = useState(null);
  const [shown, setShown] = useState(null); // { version, definition }
  const [error, setError] = useState('');
  useEffect(() => { serverApi.versions(info.id).then((r) => setVersions(r.versions)).catch((e) => { setVersions([]); setError(e.message); }); }, [info.id]);
  const show = async (number) => { try { setShown(await serverApi.version(info.id, number)); } catch (e) { setError(e.message); } };
  const restore = async (number) => {
    if (!window.confirm(`Make version ${number} the current version of “${info.title}”? The current state stays in the list as a version too.`)) return;
    try { const result = await serverApi.restoreVersion(info.id, number); setVersions(result.versions); setShown(null); onRestored(result.report, number); } catch (e) { setError(e.message); }
  };
  if (versions === null) return <p className="muted">Loading…</p>;
  return (
    <div className="stack">
      {error && <Notice message={{ kind: 'error', text: error }} />}
      {!versions.length && <p className="muted">No versions yet: they start with the next publish or change.</p>}
      <ul className="history versions">
        {versions.map((v, index) => (
          <li key={v.version}>
            <span className="hist-icon">{index === 0 ? '●' : '○'}</span>
            <b>v{v.version}</b> {v.action}{v.user ? <> by {v.user}</> : null}{index === 0 ? <span className="chip">current</span> : null}
            <small>{formatWhen(v.savedAt)} · {v.title}</small>
            <span className="row">
              <button className="btn sm quiet" onClick={() => show(v.version)}>View</button>
              {index > 0 && <button className="btn sm" onClick={() => restore(v.version)}>↺ Restore</button>}
            </span>
          </li>
        ))}
      </ul>
      {shown && (
        <div className="card version-view">
          <div className="card-head"><div><p className="eyebrow">Version {shown.version}</p><h3>{shown.title}</h3></div><button className="btn quiet sm" onClick={() => setShown(null)}>✕</button></div>
          <dl className="facts">
            <dt>Saved</dt><dd>{formatWhen(shown.savedAt)} ({shown.action}{shown.user ? `, ${shown.user}` : ''})</dd>
            <dt>Folder</dt><dd>{shown.folder}</dd>
            <dt>Data adapter</dt><dd>{shown.definition.connectionId}</dd>
            <dt>Fields</dt><dd>{(shown.definition.columns || []).join(', ')}</dd>
            <dt>Parameters</dt><dd>{(shown.definition.parameters || []).map((p) => p.name).join(', ') || '—'}</dd>
          </dl>
          <details><summary className="muted">Query</summary><pre className="code-area">{shown.definition.querySource || JSON.stringify(shown.definition.pipeline || [], null, 2)}</pre></details>
        </div>
      )}
    </div>
  );
}

function HistoryTab({ info }) {
  if (!info.history.length) return <p className="muted">No history yet (it starts with the next publish or change).</p>;
  return (
    <ul className="history">
      {info.history.map((h, i) => (
        // eslint-disable-next-line react/no-array-index-key
        <li key={i}><span className="hist-icon">{ACTION_ICONS[h.action] || '•'}</span><b>{h.action}</b> by {h.user}{h.detail ? <> — {h.detail}</> : null}<small>{formatWhen(h.at)}</small></li>
      ))}
    </ul>
  );
}

export default function ResourcePanel({ reportId, tab, setTab, onChanged, menu, scheduleInfo, onBackground, onBack }) {
  const [info, setInfo] = useState(null);
  const [message, setMessage] = useState(null);
  const load = useCallback(async () => {
    try { setInfo(await serverApi.details(reportId)); setMessage(null); }
    catch (error) { setInfo(null); setMessage({ text: error.message, kind: 'error' }); }
  }, [reportId]);
  useEffect(() => { setInfo(null); load(); }, [load]);

  if (!info) return <section className="card resource"><Notice message={message || { text: 'Loading…' }} /></section>;
  const save = async (changes) => {
    try {
      const next = await serverApi.edit(info.id, changes);
      setInfo(next);
      setMessage({ text: 'Saved on the server (and in the designer workspace copy).', kind: 'ok' });
      onChanged();
    } catch (error) { setMessage({ text: error.message, kind: 'error' }); }
  };
  return (
    <section className="card resource">
      {onBack && <button type="button" className="btn quiet sm only-mobile back-btn" onClick={onBack}>‹ All reports</button>}
      <div className="card-head">
        <div><p className="eyebrow">/{info.folder.split('/').join(' / ')}</p><h2>{info.title}</h2><p className="muted" style={{ fontSize: 12 }}><code>{info.id}</code> · {info.connection.name}</p></div>
        <button className="btn sm" onClick={(e) => menu(e, info)} title="Actions (also: right-click the report)">⋯ Actions</button>
      </div>
      <div className="tabs">
        {RESOURCE_TABS.map(([id, label]) => <button key={id} className={tab === id ? 'on' : ''} onClick={() => setTab(id)}>{label}</button>)}
      </div>
      {message && <div style={{ marginBottom: 10 }}><Notice message={message} /></div>}
      {tab === 'run' && <RunPanel report={info} onBackground={onBackground} />}
      {tab === 'overview' && <OverviewTab info={info} onSave={save} />}
      {tab === 'parameters' && <ParametersTab info={info} onSave={save} />}
      {tab === 'datasource' && <DataSourceTab info={info} onSave={save} />}
      {tab === 'query' && <QueryTab info={info} onSave={save} />}
      {tab === 'schedules' && <SchedulesList report={info} info={scheduleInfo} onMessage={setMessage} />}
      {tab === 'versions' && <VersionsTab info={info} onRestored={(report, number) => { setInfo(report); setMessage({ kind: 'ok', text: `Version ${number} is the current version again (the designer workspace copy too).` }); onChanged(); }} />}
      {tab === 'history' && <HistoryTab info={info} />}
    </section>
  );
}
