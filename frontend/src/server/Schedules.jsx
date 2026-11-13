// Report schedules and background runs (JasperReports Server: Schedule → Recurrence, Parameters, Output, Notifications;
// and the list of report outputs).
import { useCallback, useEffect, useState } from 'react';
import { Notice, downloadBlob, formatWhen } from '../portal/Shell.jsx';
import { FORMAT_LABELS } from './RunPanel.jsx';
import { serverApi } from './serverApi.js';

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const RELATIVE = [
  ['{today}', 'Today'], ['{yesterday}', 'Yesterday'], ['{today-7}', '7 days ago'], ['{weekStart}', 'Monday of this week'],
  ['{monthStart}', 'First day of this month'], ['{monthEnd}', 'Last day of this month'],
  ['{prevMonthStart}', 'First day of last month'], ['{prevMonthEnd}', 'Last day of last month'],
  ['{yearStart}', 'First day of this year'], ['{prevYearStart}', 'First day of last year'], ['{prevYearEnd}', 'Last day of last year'],
];
const browserZone = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; } };
const STATUS = { success: ['ok', '✓ Done'], failed: ['error', '✕ Failed'], running: ['', '⟳ Running'], queued: ['', '… Waiting'] };

export function StatusChip({ status }) {
  const [kind, text] = STATUS[status] || ['', status];
  return <span className={`status-chip ${kind || status}`}>{text}</span>;
}

function emptySchedule(report, info) {
  return {
    label: report?.title || '', reportId: report?.id || '', format: report?.defaultFormat || 'pdf',
    recurrence: { type: 'daily', time: '08:00', weekdays: [0], day: 1, every: 60, at: '' },
    timezone: info?.timezone && info.timezone !== 'UTC' ? info.timezone : browserZone(),
    parameters: Object.fromEntries((report?.parameters || []).filter((p) => !p.computed).map((p) => [p.name, p.type === 'date' ? '{yesterday}' : (p.default ?? '')])),
    keep: 10, enabled: true, email: { to: '', subject: '', message: '', attach: true },
  };
}

/** Create or edit one schedule. `report` = the report's details (parameters, formats). */
export function ScheduleDialog({ report, job, info, onClose, onSaved }) {
  const [form, setForm] = useState(() => (job ? {
    ...job, recurrence: { weekdays: [0], day: 1, every: 60, time: '08:00', at: '', ...job.recurrence }, email: { ...job.email, to: job.email.to.join(', ') },
  } : emptySchedule(report, info)));
  const [step, setStep] = useState('when');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const setRec = (patch) => setForm((f) => ({ ...f, recurrence: { ...f.recurrence, ...patch } }));
  const setEmail = (patch) => setForm((f) => ({ ...f, email: { ...f.email, ...patch } }));
  const rec = form.recurrence;
  const parameters = (report?.parameters || []).filter((p) => !p.computed);

  const save = async () => {
    setBusy(true);
    setError(null);
    const recurrence = { type: rec.type };
    if (rec.type === 'once') recurrence.at = rec.at;
    if (rec.type === 'minutes') recurrence.every = Number(rec.every);
    if (['daily', 'weekly', 'monthly'].includes(rec.type)) recurrence.time = rec.time;
    if (rec.type === 'weekly') recurrence.weekdays = rec.weekdays;
    if (rec.type === 'monthly') recurrence.day = rec.day === 'last' ? 'last' : Number(rec.day);
    const body = { ...form, recurrence, keep: Number(form.keep), email: { ...form.email, to: form.email.to } };
    try {
      const result = job ? await serverApi.updateSchedule(job.id, body) : await serverApi.createSchedule(body);
      onSaved(result.schedule);
    } catch (failure) { setError({ kind: 'error', text: failure.message }); } finally { setBusy(false); }
  };

  return (
    <div className="dialog-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="card dialog schedule-dialog" role="dialog" aria-label="Schedule">
        <div className="card-head">
          <div><p className="eyebrow">{job ? 'Edit schedule' : 'New schedule'}</p><h2>{report?.title || form.reportId}</h2></div>
          <button className="btn quiet sm" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="tabs">
          {[['when', '1 · When'], ['values', '2 · Parameters'], ['output', '3 · Output'], ['email', '4 · E-mail']].map(([id, label]) => (
            <button key={id} className={step === id ? 'on' : ''} onClick={() => setStep(id)}>{label}</button>
          ))}
        </div>

        {step === 'when' && (
          <div className="stack">
            <label className="field"><span>Name</span><input value={form.label} onChange={(e) => set({ label: e.target.value })} placeholder="Daily sales for the management" /></label>
            <div className="segmented wrap">
              {[['once', 'Once'], ['minutes', 'Every N minutes'], ['daily', 'Daily'], ['weekly', 'Weekly'], ['monthly', 'Monthly']].map(([id, label]) => (
                <button key={id} type="button" className={rec.type === id ? 'on' : ''} onClick={() => setRec({ type: id })}>{label}</button>
              ))}
            </div>
            <div className="params">
              {rec.type === 'once' && <label className="field"><span>Date and time</span><input type="datetime-local" value={rec.at} onChange={(e) => setRec({ at: e.target.value })} /></label>}
              {rec.type === 'minutes' && <label className="field"><span>Every (minutes, 5–1440)</span><input type="number" min={5} max={1440} value={rec.every} onChange={(e) => setRec({ every: e.target.value })} /></label>}
              {['daily', 'weekly', 'monthly'].includes(rec.type) && <label className="field"><span>Time</span><input type="time" value={rec.time} onChange={(e) => setRec({ time: e.target.value })} /></label>}
              {rec.type === 'monthly' && (
                <label className="field"><span>Day of the month</span>
                  <select value={rec.day} onChange={(e) => setRec({ day: e.target.value })}>
                    {Array.from({ length: 31 }, (_, i) => <option key={i + 1} value={i + 1}>{i + 1}</option>)}
                    <option value="last">Last day</option>
                  </select>
                </label>
              )}
              <label className="field"><span>Time zone</span><input value={form.timezone} onChange={(e) => set({ timezone: e.target.value })} placeholder="Asia/Karachi" /></label>
            </div>
            {rec.type === 'weekly' && (
              <div className="day-picker">
                {DAYS.map((d, i) => (
                  <button key={d} type="button" className={rec.weekdays.includes(i) ? 'on' : ''}
                    onClick={() => setRec({ weekdays: rec.weekdays.includes(i) ? rec.weekdays.filter((x) => x !== i) : [...rec.weekdays, i].sort() })}>{d}</button>
                ))}
              </div>
            )}
            {rec.type === 'monthly' && Number(rec.day) > 28 && <p className="muted">In shorter months the job runs on the last day.</p>}
          </div>
        )}

        {step === 'values' && (
          <div className="stack">
            {!parameters.length && <p className="muted">This report has no parameters.</p>}
            <datalist id="relative-dates">{RELATIVE.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</datalist>
            <div className="params">
              {parameters.map((p) => (
                <label key={p.name} className="field">
                  <span>{p.label || p.name}{p.required ? '' : ' (optional)'}</span>
                  {p.allowedValues?.length ? (
                    <select value={form.parameters[p.name] ?? ''} onChange={(e) => set({ parameters: { ...form.parameters, [p.name]: e.target.value } })}>
                      {!p.required && <option value="">—</option>}
                      {p.allowedValues.map((v) => <option key={String(v)} value={String(v)}>{String(v)}</option>)}
                    </select>
                  ) : (
                    <input list={p.type === 'date' ? 'relative-dates' : undefined} value={form.parameters[p.name] ?? ''}
                      placeholder={p.type === 'date' ? '{yesterday} or 2026-01-31' : ''}
                      onChange={(e) => set({ parameters: { ...form.parameters, [p.name]: e.target.value } })} />
                  )}
                </label>
              ))}
            </div>
            {parameters.some((p) => p.type === 'date') && <p className="muted">Dates can be <b>relative</b>, worked out on the day the job runs: <code>{'{yesterday}'}</code>, <code>{'{today-7}'}</code>, <code>{'{prevMonthStart}'}</code>… (pick from the list).</p>}
          </div>
        )}

        {step === 'output' && (
          <div className="params">
            <label className="field"><span>Format</span>
              <select value={form.format} onChange={(e) => set({ format: e.target.value })}>{(report?.formats || ['pdf', 'xlsx', 'csv']).map((f) => <option key={f} value={f}>{FORMAT_LABELS[f] || f}</option>)}</select>
            </label>
            <label className="field"><span>Keep the last … files</span><input type="number" min={1} max={100} value={form.keep} onChange={(e) => set({ keep: e.target.value })} /></label>
            <label className="check"><input type="checkbox" checked={form.enabled} onChange={(e) => set({ enabled: e.target.checked })} /> Active</label>
          </div>
        )}

        {step === 'email' && (
          info?.canEmail ? (
            <div className="stack">
              {!info.emailConfigured && <Notice message={{ kind: 'warn', text: 'E-mail is not set up on this server yet (SMTP_HOST, SMTP_FROM). The schedule still runs; the files are kept under Runs.' }} />}
              <label className="field"><span>Send to (comma separated)</span><input type="email" multiple value={form.email.to} onChange={(e) => setEmail({ to: e.target.value })} placeholder="manager@company.com, team@company.com" /></label>
              <label className="field"><span>Subject</span><input value={form.email.subject} onChange={(e) => setEmail({ subject: e.target.value })} placeholder={`${report?.title || 'Report'} — <date>`} /></label>
              <label className="field"><span>Message</span><textarea rows={3} value={form.email.message} onChange={(e) => setEmail({ message: e.target.value })} /></label>
              <label className="check"><input type="checkbox" checked={form.email.attach} onChange={(e) => setEmail({ attach: e.target.checked })} /> Attach the report file</label>
            </div>
          ) : <p className="muted">Only designers and administrators can send reports by e-mail.</p>
        )}

        <Notice message={error} />
        <div className="row" style={{ justifyContent: 'flex-end' }}>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={busy} onClick={save}>{busy ? 'Saving…' : job ? 'Save schedule' : 'Create schedule'}</button>
        </div>
      </div>
    </div>
  );
}

/** Schedules (all, or of one report). */
export function SchedulesList({ report, info, onMessage, version = 0 }) {
  const [items, setItems] = useState(null);
  const [editing, setEditing] = useState(null); // { job?, report? }
  const load = useCallback(() => serverApi.schedules(report?.id || '').then((r) => setItems(r.schedules)).catch((e) => { setItems([]); onMessage({ kind: 'error', text: e.message }); }), [report?.id, onMessage]);
  useEffect(() => { load(); }, [load, version]);
  const act = async (fn, text) => { try { await fn(); await load(); if (text) onMessage({ kind: 'ok', text }); } catch (e) { onMessage({ kind: 'error', text: e.message }); } };
  const edit = async (job) => {
    try { setEditing({ job, report: report || await serverApi.details(job.reportId) }); } catch (e) { onMessage({ kind: 'error', text: e.message }); }
  };

  return (
    <div className="stack">
      {report && info?.canSchedule && <div className="row"><button className="btn primary" onClick={() => setEditing({ report })}>＋ Schedule this report</button></div>}
      {info && !info.schedulerOn && <Notice message={{ kind: 'warn', text: 'The scheduler is switched off on this server (REPORTING_SCHEDULER=off); schedules wait until it is on.' }} />}
      {items === null && <div className="empty">Loading…</div>}
      {items?.length === 0 && <div className="empty">{report ? 'This report has no schedules.' : 'No schedules yet. Open a report → Schedules → “Schedule this report”.'}</div>}
      {items?.map((job) => (
        <div key={job.id} className={`item schedule-item ${job.enabled ? '' : 'off'}`}>
          <div className="schedule-main">
            <strong>{job.label}</strong>
            <small className="muted">{!report && <>{job.title} · </>}{job.describe} ({job.timezone}) · {job.format.toUpperCase()}{job.email?.to?.length ? ` · ✉ ${job.email.to.length}` : ''}</small>
            <small className="muted">Next: {job.enabled ? formatWhen(job.nextRunAt) : 'paused'} · Last: {job.lastRun ? <><StatusChip status={job.lastRun.status} /> {formatWhen(job.lastRun.at)}</> : 'never'}{info?.canSchedule && job.owner ? ` · by ${job.owner}` : ''}</small>
            {!job.published && <small className="err">The report is no longer published; the job will fail.</small>}
          </div>
          {info?.canSchedule && (
            <div className="row">
              <button className="btn sm" onClick={() => act(() => serverApi.runScheduleNow(job.id), `“${job.label}” started — see Runs`)}>▶ Run now</button>
              <button className="btn sm" onClick={() => act(() => serverApi.enableSchedule(job.id, !job.enabled), job.enabled ? 'Paused' : 'Resumed')}>{job.enabled ? '⏸ Pause' : '▶ Resume'}</button>
              <button className="btn sm" onClick={() => edit(job)}>✎ Edit</button>
              <button className="btn sm danger" onClick={() => { if (window.confirm(`Delete the schedule “${job.label}”?`)) act(() => serverApi.deleteSchedule(job.id, window.confirm('Also delete its saved output files?')), 'Schedule deleted'); }}>✕</button>
            </div>
          )}
        </div>
      ))}
      {editing && <ScheduleDialog report={editing.report} job={editing.job} info={info} onClose={() => setEditing(null)}
        onSaved={(job) => { setEditing(null); load(); onMessage({ kind: 'ok', text: `Saved “${job.label}” — next run ${formatWhen(job.nextRunAt)}` }); }} />}
    </div>
  );
}

/** Background runs and scheduled outputs; refreshes itself while something is running. */
export function RunsList({ onMessage, version = 0 }) {
  const [runs, setRuns] = useState(null);
  const load = useCallback(() => serverApi.runs().then((r) => setRuns(r.runs)).catch((e) => { setRuns([]); onMessage({ kind: 'error', text: e.message }); }), [onMessage]);
  useEffect(() => { load(); }, [load, version]);
  const busy = runs?.some((r) => r.status === 'queued' || r.status === 'running');
  useEffect(() => {
    if (!busy) return undefined;
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [busy, load]);
  const download = async (run) => {
    try { const { blob, name } = await serverApi.runFile(run.id); downloadBlob(blob, name); } catch (e) { onMessage({ kind: 'error', text: e.message }); }
  };
  const remove = async (run) => {
    try { await serverApi.deleteRun(run.id); await load(); } catch (e) { onMessage({ kind: 'error', text: e.message }); }
  };
  return (
    <div className="stack">
      <div className="row"><button className="btn sm" onClick={load}>↻ Refresh</button>{busy && <span className="muted">Updating every few seconds while a run is busy…</span>}</div>
      {runs === null && <div className="empty">Loading…</div>}
      {runs?.length === 0 && <div className="empty">No runs yet. Run a report “in the background”, or wait for a schedule.</div>}
      {runs?.length > 0 && (
        <div className="table-wrap">
          <table className="grid-table runs-table">
            <thead><tr><th>Report</th><th>Status</th><th>Started</th><th>Records</th><th>From</th><th /></tr></thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id}>
                  <td data-label="Report"><b>{run.title}</b><br /><small className="muted">{run.format.toUpperCase()}{run.size ? ` · ${(run.size / 1024).toFixed(0)} KB` : ''}</small></td>
                  <td data-label="Status"><StatusChip status={run.status} />{run.error && <div className="err small">{run.error}</div>}{run.email && <div className="muted small">✉ {run.email}</div>}</td>
                  <td data-label="Started">{formatWhen(run.startedAt || run.queuedAt)}</td>
                  <td data-label="Records">{run.status === 'success' ? run.rowCount : '—'}</td>
                  <td data-label="From">{run.jobLabel || (run.kind === 'background' ? 'Background run' : run.kind)}<br /><small className="muted">{run.owner}</small></td>
                  <td className="row">
                    <button className="btn sm primary" disabled={!run.file} onClick={() => download(run)}>⤓ Download</button>
                    <button className="btn sm quiet" disabled={run.status === 'queued' || run.status === 'running'} onClick={() => remove(run)} title="Delete this run and its file">✕</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
