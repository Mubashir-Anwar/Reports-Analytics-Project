// Report schedules and background runs (JasperReports Server: Schedule → Recurrence, Parameters, Output, Notifications;
// and the list of report outputs).
import { useCallback, useEffect, useRef, useState } from 'react';
import { Notice, formatWhen } from '../portal/Shell.jsx';
import { FORMAT_LABELS } from './RunPanel.jsx';
import { serverApi } from './serverApi.js';
import { optionalFilters } from '../errorHelp.js';
import DeliveryEditor from './DeliveryEditor.jsx';

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

/** One-click starting points for "When". */
const PRESETS = [
  ['Every morning', 'Every day at 08:00', { type: 'daily', time: '08:00' }],
  ['Every Monday', 'Monday at 09:00', { type: 'weekly', time: '09:00', weekdays: [0] }],
  ['Weekdays', 'Mon–Fri at 08:00', { type: 'weekly', time: '08:00', weekdays: [0, 1, 2, 3, 4] }],
  ['Start of month', 'Day 1 at 07:00', { type: 'monthly', time: '07:00', day: 1 }],
  ['End of month', 'Last day at 22:00', { type: 'monthly', time: '22:00', day: 'last' }],
  ['Every 30 minutes', 'All day long', { type: 'minutes', every: 30 }],
];
const STEPS = [['when', 'When'], ['values', 'Parameters'], ['output', 'Output'], ['email', 'E-mail'], ['deliver', 'Send file']];

/** The schedule in one sentence: when, what, and where it goes. */
export function scheduleSummary(form) {
  const r = form.recurrence || {};
  const when = r.type === 'once' ? (r.at ? `once on ${r.at.replace('T', ' at ')}` : 'once (choose the date)')
    : r.type === 'minutes' ? `every ${r.every || '…'} minutes`
      : r.type === 'daily' ? `every day at ${r.time}`
        : r.type === 'weekly' ? `every ${(r.weekdays || []).map((d) => DAYS[d]).join(', ') || '… (choose days)'} at ${r.time}`
          : `every month on ${r.day === 'last' ? 'the last day' : `day ${r.day}`} at ${r.time}`;
  const to = String(form.email?.to || '').split(/[,;\s]+/).filter(Boolean).length;
  const where = ['kept under Runs', ...(to ? [`e-mailed to ${to}`] : []), ...(form.deliver || []).filter((d) => d.enabled !== false).map((d) => `sent to ${d.label || d.host || d.url || d.folder || d.type}`)];
  return `Runs ${when} (${form.timezone}) as ${String(form.format || 'pdf').toUpperCase()}; ${where.join(', ')}.${form.enabled === false ? ' Paused.' : ''}`;
}

function emptySchedule(report, info) {
  return {
    label: report?.title || '', reportId: report?.id || '', folder: report?.folder || '', format: report?.defaultFormat || 'pdf',
    recurrence: { type: 'daily', time: '08:00', weekdays: [0], day: 1, every: 60, at: '' },
    timezone: info?.timezone && info.timezone !== 'UTC' ? info.timezone : browserZone(),
    parameters: Object.fromEntries((report?.parameters || []).filter((p) => !p.computed).map((p) => [p.name, p.type === 'date' ? '{yesterday}' : (p.default ?? '')])),
    keep: 10, enabled: true, email: { to: '', subject: '', message: '', attach: true }, deliver: [],
  };
}

/**
 * A schedule's date: every relative date in one list (a <datalist> would only show the entries matching the text
 * already typed, e.g. just "Yesterday"), or "Exact date…" with a calendar.
 */
function DateChoice({ value, onChange }) {
  const relative = RELATIVE.some(([v]) => v === value);
  const [exact, setExact] = useState(!relative && value !== '');
  const exactValue = /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : '';
  return (
    <span className="date-choice">
      <select value={exact ? 'exact' : value} onChange={(e) => {
        if (e.target.value === 'exact') { setExact(true); onChange(exactValue || new Date().toISOString().slice(0, 10)); } else { setExact(false); onChange(e.target.value); }
      }}>
        {!exact && !relative && <option value="">—</option>}
        {RELATIVE.map(([v, l]) => <option key={v} value={v}>{l}  ·  {v}</option>)}
        <option value="exact">Exact date…</option>
      </select>
      {exact && <input type="date" value={exactValue} onChange={(e) => onChange(e.target.value)} aria-label="Exact date" />}
    </span>
  );
}

/** Create or edit one schedule. `report` = the report's details (parameters, formats). */
export function ScheduleDialog({ report, job, info, onClose, onSaved }) {
  const [form, setForm] = useState(() => (job ? {
    ...job, recurrence: { weekdays: [0], day: 1, every: 60, time: '08:00', at: '', ...job.recurrence }, email: { ...job.email, to: job.email.to.join(', ') }, deliver: job.deliver || [],
  } : emptySchedule(report, info)));
  const [step, setStep] = useState('when');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const setRec = (patch) => setForm((f) => ({ ...f, recurrence: { ...f.recurrence, ...patch } }));
  const setEmail = (patch) => setForm((f) => ({ ...f, email: { ...f.email, ...patch } }));
  const rec = form.recurrence;
  const stepIndex = STEPS.findIndex(([id]) => id === step);
  const parameters = (report?.parameters || []).filter((p) => !p.computed);
  const filters = optionalFilters(report);

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
          {STEPS.map(([id, label], i) => (
            <button key={id} className={step === id ? 'on' : ''} onClick={() => setStep(id)}>{i + 1} · {label}{id === 'deliver' && form.deliver?.length ? ` (${form.deliver.length})` : ''}</button>
          ))}
        </div>

        {step === 'when' && (
          <div className="stack">
            <label className="field"><span>Name</span><input value={form.label} onChange={(e) => set({ label: e.target.value })} placeholder="Daily sales for the management" /></label>
            <div className="preset-row" role="group" aria-label="Quick choices">
              {PRESETS.map(([title, hint, value]) => {
                const on = rec.type === value.type && (!value.time || rec.time === value.time) && (!value.weekdays || String(rec.weekdays) === String(value.weekdays))
                  && (value.day === undefined || String(rec.day) === String(value.day)) && (!value.every || Number(rec.every) === value.every);
                return <button key={title} type="button" className={`preset ${on ? 'on' : ''}`} onClick={() => setRec(value)}><b>{title}</b><small>{hint}</small></button>;
              })}
            </div>
            <p className="muted small">…or set it yourself:</p>
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
                    p.type === 'date' ? (
                      <DateChoice value={form.parameters[p.name] ?? ''} onChange={(value) => set({ parameters: { ...form.parameters, [p.name]: value } })} />
                    ) : (
                      <input value={form.parameters[p.name] ?? ''} onChange={(e) => set({ parameters: { ...form.parameters, [p.name]: e.target.value } })} />
                    )
                  )}
                  {p.required && filters[p.name] && <span className="hint warn-text">Empty gives all records in this report, but it is marked Required — untick Required on the report's Parameters tab to leave it empty.</span>}
                </label>
              ))}
            </div>
            {parameters.some((p) => p.type === 'date') && <p className="muted">Dates can be <b>relative</b>, worked out on the day the job runs: <code>{'{yesterday}'}</code>, <code>{'{today-7}'}</code>, <code>{'{prevMonthStart}'}</code>… (pick from the list, or choose “Exact date…”).</p>}
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

        {step === 'deliver' && (
          info?.canSchedule
            ? <DeliveryEditor value={form.deliver || []} onChange={(deliver) => set({ deliver })} info={info} report={report} format={form.format} jobId={job?.id} />
            : <p className="muted">Only designers and administrators can send report files to other systems.</p>
        )}

        <p className="schedule-summary" aria-live="polite">🗓 {scheduleSummary(form)}</p>
        <Notice message={error} />
        <div className="row" style={{ justifyContent: 'flex-end' }}>
          <button className="btn" onClick={onClose}>Cancel</button>
          {stepIndex > 0 && <button className="btn" onClick={() => setStep(STEPS[stepIndex - 1][0])}>← Back</button>}
          {stepIndex < STEPS.length - 1 && <button className={job ? 'btn' : 'btn primary'} onClick={() => setStep(STEPS[stepIndex + 1][0])}>Next: {STEPS[stepIndex + 1][1]} →</button>}
          {(job || stepIndex === STEPS.length - 1) && <button className="btn primary" disabled={busy} onClick={save}>{busy ? 'Saving…' : job ? 'Save schedule' : 'Create schedule'}</button>}
          {!job && stepIndex < STEPS.length - 1 && <button className="btn quiet" disabled={busy} onClick={save} title="Save now; the other steps keep their defaults">Create now</button>}
        </div>
      </div>
    </div>
  );
}

const dueSoon = (items) => items?.some((job) => (job.enabled && job.nextRunAt && new Date(job.nextRunAt) - Date.now() < 15 * 60000)
  || ['queued', 'running'].includes(job.lastRun?.status));

/**
 * A scheduled run happens on the server, so nothing on the page would change: on a report's page this watches its
 * schedules near their time and says when one has run (records, empty file, or the error) — on whatever tab is open.
 */
export function ScheduleWatcher({ report, onMessage }) {
  const [items, setItems] = useState(null);
  const seen = useRef(null);
  const load = useCallback(() => serverApi.schedules(report?.ref || report?.id || '').then((r) => setItems(r.schedules)).catch(() => {}), [report?.ref, report?.id]);
  useEffect(() => {
    load();
    const every = setInterval(load, 60000);            // picks up schedules made after the page opened
    return () => clearInterval(every);
  }, [load]);
  const soon = dueSoon(items);
  useEffect(() => {
    if (!soon) return undefined;
    const timer = setInterval(load, 10000);
    return () => clearInterval(timer);
  }, [soon, load]);
  useEffect(() => {
    if (!items) return;
    if (items.some((job) => ['queued', 'running'].includes(job.lastRun?.status))) return;
    const now = Object.fromEntries(items.map((job) => [job.id, job.lastRun?.runId || '']));
    if (seen.current) {
      items.filter((job) => job.lastRun && seen.current[job.id] !== now[job.id]).forEach((job) => onMessage(job.lastRun.status === 'success'
        ? { kind: job.lastRun.rowCount ? 'ok' : 'warn', text: `Schedule “${job.label}” ran at ${formatWhen(job.lastRun.at)}: ${job.lastRun.rowCount ?? 0} records${job.lastRun.rowCount ? '' : ' — the file is empty; check the schedule’s parameter values'}. Download it on the Schedules tab or under Runs.` }
        : { kind: 'error', text: `Schedule “${job.label}” failed at ${formatWhen(job.lastRun.at)}: ${job.lastRun.error}` }));
    }
    seen.current = now;
  }, [items]); // eslint-disable-line react-hooks/exhaustive-deps -- only when the list changes
  return null;
}

/** Schedules (all, or of one report). */
export function SchedulesList({ report, info, onMessage, version = 0 }) {
  const [items, setItems] = useState(null);
  const [editing, setEditing] = useState(null); // { job?, report? }
  const load = useCallback(() => serverApi.schedules(report?.ref || report?.id || '').then((r) => setItems(r.schedules)).catch((e) => { setItems([]); onMessage({ kind: 'error', text: e.message }); }), [report?.ref, report?.id, onMessage]);
  useEffect(() => { load(); }, [load, version]);
  // near a job's time, and while one is running, the list refreshes itself (ScheduleWatcher says when one has run)
  const soon = dueSoon(items);
  useEffect(() => {
    if (!soon) return undefined;
    const timer = setInterval(load, 10000);
    return () => clearInterval(timer);
  }, [soon, load]);
  const act = async (fn, text) => { try { await fn(); await load(); if (text) onMessage({ kind: 'ok', text }); } catch (e) { onMessage({ kind: 'error', text: e.message }); } };
  const edit = async (job) => {
    try { setEditing({ job, report: report || await serverApi.details(job.folder ? `${job.folder}/${job.reportId}` : job.reportId) }); } catch (e) { onMessage({ kind: 'error', text: e.message }); }
  };

  return (
    <div className="stack">
      {report && info?.canSchedule && <div className="row"><button className="btn primary" onClick={() => setEditing({ report })}>＋ Schedule this report</button></div>}
      {info && !info.schedulerOn && <Notice message={{ kind: 'warn', text: 'The scheduler is switched off on this server (REPORTING_SCHEDULER=off); schedules wait until it is on.' }} />}
      {items === null && <div className="empty">Loading…</div>}
      {items?.length === 0 && (
        <div className="empty">
          <b>{report ? 'This report has no schedules yet.' : 'No schedules yet.'}</b>
          <p className="muted">A schedule runs a report by itself, for example <i>“Sales — every morning at 08:00 as PDF, e-mailed to the manager and uploaded to the bank SFTP”</i>.</p>
          {!report && <p className="muted">Open a report (Repository) → <b>Schedules</b> → <b>＋ Schedule this report</b>.</p>}
        </div>
      )}
      {items?.map((job) => (
        <div key={job.id} className={`item schedule-item ${job.enabled ? '' : 'off'}`}>
          <div className="schedule-main">
            <strong>{job.label}</strong>
            <small className="muted">{!report && <>{job.title} · </>}{job.describe} ({job.timezone}) · {job.format.toUpperCase()}{job.email?.to?.length ? ` · ✉ ${job.email.to.length}` : ''}{job.deliver?.length ? ` · ⇪ ${job.deliver.map((d) => d.label || d.type.toUpperCase()).join(', ')}` : ''}</small>
            <small className="muted">Next: {job.enabled ? formatWhen(job.nextRunAt) : job.recurrence?.type === 'once' && job.lastRun ? 'none (one-time job, done)' : 'paused'} · Last: {job.lastRun ? <><StatusChip status={job.lastRun.status} /> {formatWhen(job.lastRun.at)}
              {job.lastRun.status === 'success' && job.lastRun.rowCount !== undefined && <span className={job.lastRun.rowCount ? '' : 'warn-text'}> · {job.lastRun.rowCount} records{job.lastRun.rowCount ? '' : ' (empty — check the parameter values)'}</span>}</> : 'never'}{info?.canSchedule && job.owner ? ` · by ${job.owner}` : ''}</small>
            {job.lastRun?.status === 'failed' && job.lastRun.error && <small className="err">{job.lastRun.error}</small>}
            {!job.published && <small className="err">The report is no longer published; the job will fail.</small>}
          </div>
          {info?.canSchedule && (
            <div className="row">
              {job.lastRun?.status === 'success' && job.lastRun.file !== false && <button className="btn sm primary" onClick={() => serverApi.runFile(job.lastRun.runId)} title="Download the file of the last run">⤓ Last file</button>}
              <button className="btn sm" onClick={() => act(() => serverApi.runScheduleNow(job.id), `“${job.label}” started — it shows here when it has run`)}>▶ Run now</button>
              <button className="btn sm" onClick={() => act(() => serverApi.enableSchedule(job.id, !job.enabled), job.enabled ? 'Paused' : 'Resumed')}>{job.enabled ? '⏸ Pause' : '▶ Resume'}</button>
              <button className="btn sm" onClick={() => edit(job)}>✎ Edit</button>
              <button className="btn sm danger" onClick={() => { if (window.confirm(`Delete the schedule “${job.label}”?`)) act(() => serverApi.deleteSchedule(job.id, window.confirm('Also delete its saved output files?')), 'Schedule deleted'); }}>✕</button>
            </div>
          )}
        </div>
      ))}
      {editing && <ScheduleDialog report={editing.report} job={editing.job} info={info} onClose={() => setEditing(null)}
        onSaved={(job) => { setEditing(null); load(); onMessage({ kind: 'ok', text: `Saved “${job.label}” — next run ${formatWhen(job.nextRunAt)}. It runs on the server: its file shows on the Schedules tab and under Runs (or by e-mail).` }); }} />}
    </div>
  );
}

/** Background runs and scheduled outputs; refreshes itself while something is running. */
export function RunsList({ onMessage, version = 0 }) {
  const [runs, setRuns] = useState(null);
  const [show, setShow] = useState('all');
  const [find, setFind] = useState('');
  const load = useCallback(() => serverApi.runs().then((r) => setRuns(r.runs)).catch((e) => { setRuns([]); onMessage({ kind: 'error', text: e.message }); }), [onMessage]);
  useEffect(() => { load(); }, [load, version]);
  const busy = runs?.some((r) => r.status === 'queued' || r.status === 'running');
  useEffect(() => {
    if (!busy) return undefined;
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [busy, load]);
  const download = async (run) => {
    serverApi.runFile(run.id);
  };
  const remove = async (run) => {
    try { await serverApi.deleteRun(run.id); await load(); } catch (e) { onMessage({ kind: 'error', text: e.message }); }
  };
  const FILTERS = [['all', 'All', () => true], ['failed', '✕ Failed', (r) => r.status === 'failed' || (r.delivered || []).some((d) => !d.ok && !d.skipped)],
    ['busy', '⟳ Running', (r) => r.status === 'queued' || r.status === 'running'], ['schedule', '⏰ Scheduled', (r) => Boolean(r.jobId)],
    ['background', 'Background', (r) => !r.jobId], ['today', 'Today', (r) => new Date(r.queuedAt || r.startedAt).toDateString() === new Date().toDateString()]];
  const needle = find.trim().toLowerCase();
  const shown = (runs || []).filter(FILTERS.find(([id]) => id === show)[2]).filter((r) => !needle || `${r.title} ${r.jobLabel} ${r.owner} ${r.reportId}`.toLowerCase().includes(needle));
  return (
    <div className="stack">
      <div className="row runs-tools">
        <div className="segmented wrap" role="group" aria-label="Show">
          {FILTERS.map(([id, label, test]) => <button key={id} type="button" className={show === id ? 'on' : ''} onClick={() => setShow(id)}>{label} <small>{(runs || []).filter(test).length}</small></button>)}
        </div>
        <input type="search" placeholder="⌕  Report, schedule or user" value={find} onChange={(e) => setFind(e.target.value)} />
        <button className="btn sm" onClick={load}>↻ Refresh</button>{busy && <span className="muted">Updating every few seconds while a run is busy…</span>}
      </div>
      {runs === null && <div className="empty">Loading…</div>}
      {runs?.length === 0 && <div className="empty"><b>No runs yet.</b><p className="muted">A run appears here when a schedule runs, or when you open a report and press <b>⟳ Run in background</b> (good for big reports: you can leave the page).</p></div>}
      {runs?.length > 0 && !shown.length && <div className="empty">Nothing matches. <button className="btn sm" onClick={() => { setShow('all'); setFind(''); }}>Show all</button></div>}
      {shown.length > 0 && (
        <div className="table-wrap">
          <table className="grid-table runs-table">
            <thead><tr><th>Report</th><th>Status</th><th>Started</th><th>Records</th><th>From</th><th /></tr></thead>
            <tbody>
              {shown.map((run) => (
                <tr key={run.id}>
                  <td data-label="Report"><b>{run.title}</b><br /><small className="muted">{run.format.toUpperCase()}{run.size ? ` · ${(run.size / 1024).toFixed(0)} KB` : ''}</small></td>
                  <td data-label="Status"><StatusChip status={run.status} />{run.error && <div className="err small">{run.error}</div>}{run.email && <div className="muted small">✉ {run.email}</div>}{(run.delivered || []).map((d) => <div key={d.id} className={`small ${d.ok ? 'muted' : d.skipped ? 'muted' : 'err'}`} title={d.result}>{d.type === 'sftp' ? '🔐' : d.type === 'http' ? '🔗' : '📂'} {d.label}: {d.ok ? '✓ ' : d.skipped ? '' : '✕ '}{d.result}</div>)}</td>
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
