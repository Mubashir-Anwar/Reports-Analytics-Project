// Analytics → Python scripts (#py/new, #py/<dataset>): clean, reshape or model datasets with pandas, scikit-learn
// and scipy. Try the script on the data (first rows, printed output, errors), then save it as a dataset of its own;
// refreshing that dataset runs the script again. Writing and running need the Analytics and the Python right.
import { useEffect, useMemo, useRef, useState } from 'react';
import { bi, describeBytes } from './api.js';

const SNIPPETS = [
  ['🧹 Duplicates & blanks', 'Drop repeated rows and rows that are empty', `result = df.drop_duplicates().dropna(how="all")`],
  ['🩹 Fill missing', 'Numbers get the median, text gets "Unknown"', `result = df.copy()
for name in result.columns:
    if result[name].dtype.kind in "if":
        result[name] = result[name].fillna(result[name].median())
    elif result[name].dtype == object:
        result[name] = result[name].fillna("Unknown")`],
  ['🔤 Tidy text', 'Trim spaces and fix the case of text columns', `result = df.copy()
for name in result.select_dtypes("object").columns:
    result[name] = result[name].astype(str).str.strip().str.title()`],
  ['📅 Fix types', 'Turn text into numbers and dates where it fits', `result = df.copy()
for name in result.select_dtypes("object").columns:
    numbers = pd.to_numeric(result[name], errors="coerce")
    if numbers.notna().mean() > 0.9:
        result[name] = numbers
        continue
    dates = pd.to_datetime(result[name], errors="coerce")
    if dates.notna().mean() > 0.9:
        result[name] = dates`],
  ['📈 Outliers', 'Mark rows far from the rest (z-score, scipy)', `from scipy import stats
result = df.copy()
numbers = result.select_dtypes("number")
result["outlier"] = (abs(stats.zscore(numbers, nan_policy="omit")) > 3).any(axis=1)`],
  ['✦ Clusters', 'Group similar rows (KMeans, scikit-learn)', `from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
result = df.copy()
numbers = result.select_dtypes("number").fillna(0)
scaled = StandardScaler().fit_transform(numbers)
result["cluster"] = KMeans(n_clusters=4, n_init=5, random_state=0).fit_predict(scaled)`],
  ['Σ Group & total', 'One row per value of a column', `# change the column names to yours
result = df.groupby("Branch", as_index=False).agg(total=("Amount", "sum"), orders=("Amount", "count"))`],
  ['⇄ Join two', 'Combine two datasets on a shared column', `# dfs["name"] is any chosen dataset; change the column names to yours
right = dfs[{second}]
result = df.merge(right, how="left", left_on="Branch", right_on="Code")`],
];
const SCHEDULES = [[0, 'Only by hand'], [60, 'Every hour'], [360, 'Every 6 hours'], [1440, 'Every day'], [10080, 'Every week']];

/** A plain code box with line numbers: Tab indents, Ctrl+Enter runs. */
function CodeBox({ value, onChange, onRun, readOnly }) {
  const area = useRef(null);
  const gutter = useRef(null);
  const lines = value.split('\n').length;
  const key = (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); onRun(); return; }
    if (event.key !== 'Tab' || readOnly) return;
    event.preventDefault();
    const box = event.currentTarget;
    const { selectionStart: from, selectionEnd: to } = box;
    const next = `${value.slice(0, from)}    ${value.slice(to)}`;
    onChange(next);
    requestAnimationFrame(() => { box.selectionStart = box.selectionEnd = from + 4; });
  };
  return (
    <div className="py-code">
      <pre ref={gutter} className="py-gutter" aria-hidden="true">{Array.from({ length: lines }, (_, i) => i + 1).join('\n')}</pre>
      <textarea ref={area} value={value} onChange={(e) => onChange(e.target.value)} onKeyDown={key} readOnly={readOnly}
        onScroll={(e) => { if (gutter.current) gutter.current.scrollTop = e.currentTarget.scrollTop; }}
        spellCheck={false} autoCapitalize="off" autoComplete="off" aria-label="Python script" wrap="off" />
    </div>
  );
}

function Preview({ result }) {
  if (!result) return <div className="viz-empty">Run the script to see the first rows of its result.</div>;
  return (
    <div className="py-result stack">
      <div className={`py-status ${result.ok ? 'ok' : 'bad'}`}>
        <b>{result.ok ? '✓ Ran' : '✕ Failed'}</b>
        {result.ok && <span>{result.rowCount.toLocaleString()} rows · {result.columns.length} columns</span>}
        <span>{(result.durationMs / 1000).toFixed(1)} s</span>
      </div>
      {result.error && <pre className="py-error">{result.error}</pre>}
      {result.notes?.length > 0 && <p className="muted small">{result.notes.join(' · ')}</p>}
      {result.output && <><small className="py-label">Printed</small><pre className="py-output">{result.output}</pre></>}
      {result.ok && result.rows.length > 0 && (
        <div className="viz-table-wrap py-table">
          <table className="viz-table">
            <thead><tr>{result.columns.map((name) => <th key={name}>{name}</th>)}</tr></thead>
            <tbody>{result.rows.map((row, i) => <tr key={i}>{row.map((value, j) => <td key={j}>{value === null ? <i className="muted">null</i> : String(value)}</td>)}</tr>)}</tbody>
          </table>
        </div>
      )}
      {result.ok && result.rowCount > result.rows.length && <p className="muted small">The first {result.rows.length} of {result.rowCount.toLocaleString()} rows.</p>}
    </div>
  );
}

export default function PythonLab({ datasetId, info, datasets, folders, notify, onSaved, onDeleted }) {
  const isNew = !datasetId || datasetId === 'new';
  const may = Boolean(info.mayPython);
  const limits = info.python || {};
  const [script, setScript] = useState(limits.example || '');
  const [inputs, setInputs] = useState([]);
  const [meta, setMeta] = useState({ name: '', folder: 'Analytics', description: '', refreshEveryMinutes: 0 });
  const [record, setRecord] = useState(null);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState('');
  const [find, setFind] = useState('');

  useEffect(() => {
    if (isNew) return;
    bi.dataset(datasetId).then(({ dataset }) => {
      setRecord(dataset);
      setScript(dataset.script ?? '');
      setInputs(dataset.inputs || []);
      setMeta({ name: dataset.name, folder: dataset.folder || 'Analytics', description: dataset.description || '', refreshEveryMinutes: dataset.refreshEveryMinutes || 0 });
    }).catch((error) => notify(error.message, 'error'));
  }, [datasetId]); // eslint-disable-line react-hooks/exhaustive-deps
  // a refresh runs in the background: follow it until it is done
  useEffect(() => {
    if (!record || !['queued', 'refreshing'].includes(record.status)) return undefined;
    const timer = setTimeout(() => bi.dataset(record.id).then(({ dataset }) => { setRecord(dataset); if (!['queued', 'refreshing'].includes(dataset.status)) onSaved(); }).catch(() => {}), 1500);
    return () => clearTimeout(timer);
  }, [record]); // eslint-disable-line react-hooks/exhaustive-deps

  const choices = useMemo(() => datasets.filter((d) => d.id !== datasetId), [datasets, datasetId]);
  const shown = choices.filter((d) => inputs.includes(d.id) || !find.trim() || d.name.toLowerCase().includes(find.trim().toLowerCase()));
  const picked = inputs.map((id) => datasets.find((d) => d.id === id)).filter(Boolean);
  const toggle = (id) => setInputs((list) => (list.includes(id) ? list.filter((x) => x !== id) : list.length >= (limits.maxInputs || 5) ? list : [...list, id]));

  const run = async () => {
    if (!may || busy) return;
    if (!inputs.length) { notify('Choose at least one dataset for the script to read.', 'warn'); return; }
    setBusy('run');
    try { setResult(await bi.runPython({ script, inputs, ...(isNew ? {} : { datasetId }) })); }
    catch (error) { setResult({ ok: false, error: error.message, rowCount: 0, columns: [], rows: [], output: '', durationMs: 0 }); }
    finally { setBusy(''); }
  };
  const save = async (event) => {
    event.preventDefault();
    if (!meta.name.trim()) { notify('Give the dataset a name.', 'warn'); return; }
    setBusy('save');
    try {
      const body = { ...meta, sourceKind: 'python', script, inputs };
      if (isNew) {
        const { dataset } = await bi.createDataset(body);
        notify(`Saved “${dataset.name}”: the script ran on the data${dataset.status === 'failed' ? `, but failed: ${dataset.lastError}` : ''}.`, dataset.status === 'failed' ? 'warn' : 'ok');
        onSaved(dataset.id);
      } else {
        const { dataset, sourceChanged } = await bi.updateDataset(datasetId, body);
        setRecord(dataset);
        notify(sourceChanged ? 'Saved; the dataset is made again with the new script.' : 'Saved.', 'ok');
        onSaved();
      }
    } catch (error) { notify(error.message, 'error'); } finally { setBusy(''); }
  };
  const refresh = async () => {
    try { const { dataset } = await bi.refresh(datasetId); setRecord(dataset); } catch (error) { notify(error.message, 'error'); }
  };
  const remove = async () => {
    if (!window.confirm(`Delete the dataset “${record.name}”? Dashboards that use it lose those visuals.`)) return;
    try { await bi.deleteDataset(datasetId); onDeleted(); } catch (error) { notify(error.message, 'error'); }
  };
  const insert = (template) => {
    const code = template.replace('{second}', JSON.stringify(picked[1]?.name || 'Second dataset'));
    setScript((now) => (now.trim() && now.trim() !== (limits.example || '').trim() ? `${now.replace(/\s*$/, '')}\n\n${code}\n` : `${code}\n`));
  };

  if (!isNew && !record) return <p className="muted">Loading…</p>;
  if (isNew && !may) {
    return <section className="card"><h1>Python scripts</h1><p className="muted">Cleaning and modelling data with Python needs the <b>Python scripts</b> right as well as Analytics. Ask an administrator.</p></section>;
  }
  if (limits.problem) return <section className="card"><h1>Python scripts</h1><p className="muted">{limits.problem}</p></section>;
  return (
    <div className="py-lab stack">
      <div className="page-head bi-hero-px px-in py-head">
        <div>
          <p className="px-kicker"><b>PY</b> — Analytics · Python</p>
          <h1>{isNew ? 'New Python script' : record.name}<span className="px-caret" aria-hidden="true" /></h1>
          <p className="muted">Clean, reshape or model your datasets with <code>pandas</code>, <code>scikit-learn</code> and <code>scipy</code>. Run it to check the result, then save it as a dataset for dashboards.</p>
        </div>
        {!isNew && (
          <div className="py-state">
            <span className={`py-pill ${record.status}`}>{record.status}</span>
            {record.status === 'ready' && <small>{(record.rowCount || 0).toLocaleString()} rows · {describeBytes(record.sizeBytes)}</small>}
            {record.lastError && <small className="py-last-error" title={record.lastError}>Last run: {record.lastError}</small>}
          </div>
        )}
      </div>
      {!may && <p className="notice warn">You can read this script, but changing or running it needs the Python scripts right.</p>}
      <div className="py-split">
        <aside className="card stack py-inputs">
          <div><b>Datasets it reads</b><p className="muted small">Up to {limits.maxInputs || 5}. The first is <code>df</code>; every one is <code>dfs["name"]</code>.</p></div>
          {choices.length > 6 && <input type="search" value={find} onChange={(e) => setFind(e.target.value)} placeholder="Find a dataset…" aria-label="Find a dataset" />}
          <div className="py-choices">
            {shown.length === 0 && <p className="muted small">No datasets yet. Make one from a data adapter first.</p>}
            {shown.map((d) => {
              const at = inputs.indexOf(d.id);
              return (
                <label key={d.id} className={`py-choice ${at >= 0 ? 'on' : ''}`}>
                  <input type="checkbox" checked={at >= 0} disabled={!may} onChange={() => toggle(d.id)} />
                  <span><b>{d.name}</b><small>{d.status === 'ready' ? `${(d.rowCount || 0).toLocaleString()} rows` : d.status}</small></span>
                  {at >= 0 && <code>{at === 0 ? 'df' : `dfs[${JSON.stringify(d.name)}]`}</code>}
                </label>
              );
            })}
          </div>
          {may && (
            <div className="stack py-snippets">
              <b>Add a step</b>
              {SNIPPETS.map(([label, help, code]) => (
                <button key={label} type="button" className="py-snippet" title={help} onClick={() => insert(code)}><span>{label}</span><small>{help}</small></button>
              ))}
            </div>
          )}
        </aside>
        <section className="card stack py-editor">
          <div className="row py-bar">
            <b>script.py</b>
            <span className="muted small">{picked.length ? picked.map((d, i) => (i === 0 ? `df = ${d.name}` : d.name)).join(' · ') : 'no dataset chosen'}</span>
            <span className="spacer" />
            {may && <button type="button" className="btn primary" onClick={run} disabled={Boolean(busy)} title="Ctrl+Enter">{busy === 'run' ? 'Running…' : '▶ Run'}</button>}
          </div>
          <CodeBox value={script} onChange={setScript} onRun={run} readOnly={!may} />
          <p className="muted small py-limits">Runs in its own process: at most {limits.timeoutSeconds || 120} s, {limits.memoryMb ? `${limits.memoryMb.toLocaleString()} MB of memory, ` : ''}{(limits.maxRows || 0).toLocaleString()} rows per dataset. No network, no server files, no other programs. Every run is in the audit log.</p>
          <div aria-busy={busy === 'run'} className={busy === 'run' ? 'ml-busy' : ''}><Preview result={result} /></div>
        </section>
      </div>
      {may && (
        <form className="card py-save" onSubmit={save}>
          <b>{isNew ? 'Save as a dataset' : 'Dataset'}</b>
          <label className="field"><span>Name</span><input value={meta.name} onChange={(e) => setMeta({ ...meta, name: e.target.value })} placeholder="Clean orders" maxLength={120} required /></label>
          <label className="field"><span>Folder</span><input value={meta.folder} onChange={(e) => setMeta({ ...meta, folder: e.target.value })} list="py-folders" />
            <datalist id="py-folders">{folders.map((f) => <option key={f} value={f} />)}</datalist></label>
          <label className="field"><span>Run again</span>
            <select value={meta.refreshEveryMinutes} onChange={(e) => setMeta({ ...meta, refreshEveryMinutes: Number(e.target.value) })}>
              {SCHEDULES.map(([minutes, label]) => <option key={minutes} value={minutes}>{label}</option>)}
              {!SCHEDULES.some(([m]) => m === meta.refreshEveryMinutes) && <option value={meta.refreshEveryMinutes}>Every {meta.refreshEveryMinutes} minutes</option>}
            </select></label>
          <div className="row">
            <button type="submit" className="btn primary" disabled={Boolean(busy) || !inputs.length}>{busy === 'save' ? 'Saving…' : isNew ? 'Save dataset' : 'Save'}</button>
            {!isNew && <button type="button" className="btn" onClick={refresh} disabled={['queued', 'refreshing'].includes(record.status)}>↻ Run again now</button>}
            {!isNew && <button type="button" className="btn danger" onClick={remove}>Delete</button>}
          </div>
        </form>
      )}
    </div>
  );
}
