import { useEffect, useState } from 'react';
import { Notice, downloadBlob } from '../portal/Shell.jsx';
import { serverApi } from './serverApi.js';
import { optionalFilters } from '../errorHelp.js';
import { startValue, valueProblem } from '../relativeDates.js';

const FORMAT_LABELS = { pdf: 'PDF', xlsx: 'Excel (.xlsx)', csv: 'CSV' };
const NUMBER_TYPES = ['int', 'integer', 'number', 'float', 'decimal', 'double', 'long'];

const initialValue = startValue;

function ParameterInput({ parameter, value, onChange }) {
  if (parameter.allowedValues?.length) {
    return (
      <select value={value} onChange={(e) => onChange(e.target.value)} required={parameter.required}>
        {!parameter.required && <option value="">—</option>}
        {parameter.allowedValues.map((item) => <option key={String(item)} value={String(item)}>{String(item)}</option>)}
      </select>
    );
  }
  if (parameter.type === 'boolean') {
    return <select value={value} onChange={(e) => onChange(e.target.value)}><option value="true">Yes</option><option value="false">No</option></select>;
  }
  const type = parameter.type === 'date' ? 'date' : NUMBER_TYPES.includes(parameter.type) ? 'number' : 'text';
  return <input type={type} value={value} onChange={(e) => onChange(e.target.value)} required={parameter.required}
    min={parameter.min ?? undefined} max={parameter.max ?? undefined} maxLength={type === 'text' ? parameter.maxLength : undefined}
    placeholder={parameter.patternMessage ? parameter.patternMessage.replace(/^Type (the |a )?/i, '') : undefined} />;
}

/** Input controls + format + Run (a report server's "Run" view). */
export default function RunPanel({ report, onBackground }) {
  const [values, setValues] = useState({});
  const [format, setFormat] = useState('pdf');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null); // { text, kind, url }
  const parameters = report.parameters.filter((p) => !p.computed);
  const filters = optionalFilters(report);

  useEffect(() => {
    setValues(Object.fromEntries(parameters.map((p) => [p.name, initialValue(p)])));
    setFormat(report.formats.includes(report.defaultFormat) ? report.defaultFormat : report.formats[0]);
    setResult(null);
  }, [report.id, JSON.stringify(report.parameters), report.formats.join()]); // eslint-disable-line react-hooks/exhaustive-deps

  const chosenValues = () => Object.fromEntries(Object.entries(values).filter(([, value]) => value !== ''));
  /** Big reports: the server makes the file in the background; it appears under Runs. */
  const background = async () => {
    setBusy(true);
    try {
      const { run: started } = await serverApi.runInBackground(report.ref || report.id, chosenValues(), format);
      setResult({ text: `Started in the background (${started.status}). The file will be under Runs when it is ready.`, kind: 'ok' });
      onBackground?.();
    } catch (error) { setResult({ text: error.message, kind: 'error' }); }
    finally { setBusy(false); }
  };

  const run = async (event) => {
    event.preventDefault();
    setBusy(true);
    setResult({ text: 'The report is running on the server…', kind: '' });
    try {
      const chosen = Object.fromEntries(Object.entries(values).filter(([, value]) => value !== ''));
      if (format === 'pdf') {                 // a PDF is shown here too, so this page fetches it
        const blob = await serverApi.run(report.ref || report.id, chosen, format);
        const url = downloadBlob(blob, `${report.id}.${format}`);
        setResult({ text: `Downloaded ${report.id}.${format}. `, kind: 'ok', url });
      } else {                                // Excel / CSV: made on the server, saved when ready, on any page
        const { queued } = await serverApi.runLater(report.ref || report.id, chosen, format, `${report.title || report.id}.${format}`);
        setResult({ text: queued ? `${report.id}.${format} is being made on the server. It downloads by itself when ready — you can run other reports or open other pages meanwhile (see the panel in the bottom corner).` : `Downloaded ${report.id}.${format}.`, kind: 'ok', url: '' });
      }
    } catch (error) { setResult({ text: error.message, kind: 'error' }); }
    finally { setBusy(false); }
  };

  return (
    <form onSubmit={run}>
      {parameters.length ? (
        <div className="params">
          {parameters.map((p) => (
            <label key={p.name} className="field">
              <span>{p.label || p.name}{p.required ? '' : ' (optional)'}</span>
              <ParameterInput parameter={p} value={values[p.name] ?? ''} onChange={(value) => setValues((v) => ({ ...v, [p.name]: value }))} />
              {p.description && <span className="hint">{p.description}</span>}
              {values[p.name] && valueProblem(p, values[p.name]) && <span className="hint warn-text">{valueProblem(p, values[p.name])}</span>}
              {p.required && filters[p.name] && <span className="hint warn-text">Empty gives all records in this report, but it is marked Required — untick Required on the report's Parameters tab to leave it empty.</span>}
            </label>
          ))}
        </div>
      ) : <p className="muted">This report has no questions.</p>}
      <div className="row" style={{ alignItems: 'flex-end', marginBottom: 12 }}>
        <label className="field" style={{ minWidth: 170 }}><span>Format</span>
          <select value={format} onChange={(e) => setFormat(e.target.value)}>{report.formats.map((f) => <option key={f} value={f}>{FORMAT_LABELS[f] || f}</option>)}</select>
        </label>
        <button className="btn primary" type="submit" disabled={busy} style={{ padding: '9px 16px' }}>{busy ? 'Running…' : 'Run report →'}</button>
        <button className="btn" type="button" disabled={busy} onClick={background} title="For big reports: keep working, download the file from Runs later">⟳ Run in background</button>
      </div>
      {result && <Notice message={result}>{result.url && <a href={result.url} target="_blank" rel="noopener noreferrer">Open the PDF in a new tab ↗</a>}</Notice>}
      <details style={{ marginTop: 12 }}><summary className="muted" style={{ cursor: 'pointer' }}>Report link for websites</summary><p className="muted">Websites call <code>/generate/{report.id}.{format}?Folder={report.folder}</code> with a signed link or the X-API-Key header (Folder chooses this report when the same ID is in several folders).</p></details>
    </form>
  );
}

export { FORMAT_LABELS };
