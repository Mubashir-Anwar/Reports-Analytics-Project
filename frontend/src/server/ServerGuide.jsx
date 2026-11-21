// /server → "Get started": the few steps that make the report server useful, each with an example and a button.
// A step is ticked by itself when it is done; the card can be hidden (it comes back from "? Get started").
import { useEffect, useState } from 'react';
import { serverApi } from './serverApi.js';

const HIDE_KEY = 'server-guide-hidden';
export const readGuideHidden = () => { try { return localStorage.getItem(HIDE_KEY) === '1'; } catch { return false; } };
const writeGuideHidden = (hidden) => { try { if (hidden) localStorage.setItem(HIDE_KEY, '1'); else localStorage.removeItem(HIDE_KEY); } catch { /* private mode */ } };
const PERM_KEY = 'server-guide-permissions';
export const markPermissionsSet = () => { try { localStorage.setItem(PERM_KEY, '1'); } catch { /* private mode */ } };

export default function ServerGuide({ folders, reports, designer, admin, onFolder, onUpload, onPermissions, onSchedules, onHide }) {
  const [schedules, setSchedules] = useState(null);
  useEffect(() => { serverApi.schedules().then((r) => setSchedules(r.schedules.length)).catch(() => setSchedules(0)); }, [reports.length]);
  let permissions = false;
  try { permissions = localStorage.getItem(PERM_KEY) === '1'; } catch { /* private mode */ }
  const steps = [
    { key: 'folder', done: folders.length > 0, title: 'Make folders for your reports',
      example: 'e.g. Finance / Monthly, HR / Letters, Branches / Karachi', show: designer,
      actions: [['+ New folder', onFolder]] },
    { key: 'report', done: reports.length > 0, title: 'Put a report on the server',
      example: 'In the Designer: Save, then Publish to server. Or upload a .mrd / .jrxml file here.', show: true,
      actions: [['🎨 Open the Designer', () => window.open('/designer', '_blank', 'noopener')], ...(designer ? [['⤒ Upload a file', onUpload]] : [])] },
    { key: 'schedule', done: Boolean(schedules), title: 'Let a report run by itself',
      example: 'e.g. every morning at 08:00 → PDF → e-mail to the manager and the bank’s SFTP', show: designer,
      actions: [['⏰ Schedules', onSchedules]] },
    { key: 'access', done: permissions, title: 'Choose who may see and run each folder',
      example: 'e.g. the Finance team may run /Finance; only admins may change it', show: admin,
      actions: [['🔒 Permissions', onPermissions]] },
  ].filter((s) => s.show);
  const doneCount = steps.filter((s) => s.done).length;
  const next = steps.find((s) => !s.done);
  if (!next) return null;
  return (
    <section className="card server-guide" aria-label="Get started">
      <div className="guide-head">
        <div><p className="eyebrow">Get started · {doneCount} of {steps.length} done</p><h2>{next.title}</h2></div>
        <button className="btn quiet sm" onClick={() => { writeGuideHidden(true); onHide(); }} title="Hide this guide (? Get started brings it back)">Hide</button>
      </div>
      <div className="guide-bar" aria-hidden="true"><span style={{ width: `${(doneCount / steps.length) * 100}%` }} /></div>
      <ol className="guide-steps">
        {steps.map((s, i) => (
          <li key={s.key} className={s.done ? 'done' : s === next ? 'now' : ''}>
            <span className="guide-dot">{s.done ? '✓' : i + 1}</span>
            <div>
              <b>{s.title}</b>
              <small>{s.example}</small>
              {!s.done && <span className="row">{s.actions.map(([label, run]) => <button key={label} type="button" className={`btn sm ${s === next ? 'primary' : ''}`} onClick={run}>{label}</button>)}</span>}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function showGuideAgain() { writeGuideHidden(false); }
