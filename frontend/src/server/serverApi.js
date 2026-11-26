// /server API: the login session is an HttpOnly cookie, so no token is kept in the page.
import { downloadLater, saveUrl, trackDownload } from '../shared/downloads.js';
import { reach } from '../shared/session.js';

let onSignedOut = () => {};
export const whenSignedOut = (handler) => { onSignedOut = handler; };

async function call(path, { method = 'GET', body, raw = false } = {}) {
  const options = { method, credentials: 'same-origin', headers: {} };
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json'; // the server refuses cookie writes that are not JSON
    options.body = JSON.stringify(body);
  }
  const response = await reach(path, options);
  if (!response.ok) {
    let detail = '';
    try { detail = (await response.json()).detail || ''; } catch { /* not JSON */ }
    // A bare "Not Found" means the route itself is missing: the running server is an older version.
    if (response.status === 404 && (!detail || detail === 'Not Found')) {
      detail = 'The server does not have this feature yet (HTTP 404): it is still running an older version. Restart the reporting service (uvicorn) and reload this page.';
    }
    const info = detail && typeof detail === 'object' ? detail : null;
    if (info) detail = info.message || '';
    const error = new Error(typeof detail === 'string' && detail ? detail : `Request failed (HTTP ${response.status})`);
    error.status = response.status;
    if (info && typeof info.attemptsLeft === 'number') {
      Object.assign(error, { attemptsLeft: info.attemptsLeft, lockMinutes: info.lockMinutes, failed: info.failed,
        maxFailures: info.maxFailures, retryAfter: info.retryAfter });
    }
    if (response.status === 401 && path !== '/api/server/login' && path !== '/api/server/me') onSignedOut();
    throw error;
  }
  return raw ? response : response.json();
}

/** A server report is <folder>/<ID> (the same ID may be published in several folders). The API takes the ID in the
 *  path and the folder as ?folder=; these helpers accept a ref ("cascade/KMBL/Reports/KFS") or a plain ID. */
export const splitRef = (ref) => { const s = String(ref || ''); const i = s.lastIndexOf('/'); return i > 0 ? [s.slice(0, i), s.slice(i + 1)] : ['', s]; };
export const idOf = (ref) => splitRef(ref)[1];
const reportUrl = (ref, suffix = '', extra = '') => {
  const [folder, id] = splitRef(ref);
  const query = [folder ? `folder=${encodeURIComponent(folder)}` : '', extra].filter(Boolean).join('&');
  return `/api/server/reports/${encodeURIComponent(id)}${suffix}${query ? `?${query}` : ''}`;
};

export const serverApi = {
  me: () => call('/api/server/me'),
  login: (username, password) => call('/api/server/login', { method: 'POST', body: { username, password } }),
  logout: () => call('/api/server/logout', { method: 'POST', body: {} }),
  repository: () => call('/api/server/repository'),
  createFolder: (path) => call('/api/server/folders', { method: 'POST', body: { path } }),
  /** answer: { recursive } or { moveTo } ('' = top level), from the Delete folder dialog */
  deleteFolder: (path, answer = {}) => call('/api/server/folders', { method: 'DELETE', body: { path, ...answer } }),
  folderContents: (path) => call(`/api/server/folders/contents?path=${encodeURIComponent(path)}`),
  deleteReport: (ref) => call(reportUrl(ref), { method: 'DELETE', body: {} }),
  details: (ref) => call(reportUrl(ref)),
  edit: (ref, changes) => call(reportUrl(ref), { method: 'PUT', body: changes }),
  copy: (ref, folder, newId) => call(reportUrl(ref, '/copy'), { method: 'POST', body: { folder, ...(newId ? { newId } : {}) } }),
  move: (ref, folder) => call(reportUrl(ref, '/move'), { method: 'POST', body: { folder } }),
  upload: (body) => call('/api/server/upload', { method: 'POST', body }),
  connections: () => call('/api/server/connections'),
  // dashboards (Analytics) published in the same folders
  dashboard: (id) => call(`/api/server/dashboards/${encodeURIComponent(id)}`),
  dashboardQuery: (id, queries, filters) => call(`/api/server/dashboards/${encodeURIComponent(id)}/query-batch`, { method: 'POST', body: { queries, filters } }),
  dashboardFile: (id, withData = false) => call(`/api/server/dashboards/${encodeURIComponent(id)}/file${withData ? '?data=true' : ''}`),
  moveDashboard: (id, folder) => call(`/api/server/dashboards/${encodeURIComponent(id)}/move`, { method: 'POST', body: { folder } }),
  deleteDashboard: (id) => call(`/api/server/dashboards/${encodeURIComponent(id)}`, { method: 'DELETE', body: {} }),
  uploadDashboard: (body) => call('/api/server/dashboards/upload', { method: 'POST', body }),
  testConnection: (id) => call(`/api/server/connections/${encodeURIComponent(id)}/test`, { method: 'POST', body: {} }),
  // folder permissions (administrators)
  permissions: (path) => call(`/api/server/permissions?path=${encodeURIComponent(path || '')}`),
  setPermissions: (path, rules) => call('/api/server/permissions', { method: 'PUT', body: { path, rules } }),
  // versions of a server report
  versions: (ref) => call(reportUrl(ref, '/versions')),
  version: (ref, number) => call(reportUrl(ref, `/versions/${number}`)),
  restoreVersion: (ref, number) => call(reportUrl(ref, `/versions/${number}/restore`), { method: 'POST', body: {} }),
  // schedules and background runs
  scheduleInfo: () => call('/api/server/schedules/info'),
  schedules: (ref = '') => {
    const [folder, id] = splitRef(ref);
    return call(`/api/server/schedules${id ? `?reportId=${encodeURIComponent(id)}${folder ? `&folder=${encodeURIComponent(folder)}` : ''}` : ''}`);
  },
  createSchedule: (body) => call('/api/server/schedules', { method: 'POST', body }),
  updateSchedule: (id, body) => call(`/api/server/schedules/${encodeURIComponent(id)}`, { method: 'PUT', body }),
  enableSchedule: (id, enabled) => call(`/api/server/schedules/${encodeURIComponent(id)}/enabled`, { method: 'POST', body: { enabled } }),
  deleteSchedule: (id, outputs) => call(`/api/server/schedules/${encodeURIComponent(id)}${outputs ? '?outputs=true' : ''}`, { method: 'DELETE', body: {} }),
  /** Try one delivery target (SFTP login, URL, folder) before saving; jobId = use that schedule's saved password */
  testDelivery: (target, jobId) => call('/api/server/schedules/delivery/test', { method: 'POST', body: { target, jobId: jobId || '' } }),
  runScheduleNow: (id) => call(`/api/server/schedules/${encodeURIComponent(id)}/run`, { method: 'POST', body: {} }),
  runInBackground: (ref, parameters, format) => call(reportUrl(ref, '/background'), { method: 'POST', body: { parameters, format } }),
  /** Report Server → Logs (admins): requests | runs | service | audit | downloads */
  logs: (kind, query) => call(`/api/server/logs/${kind}${query && String(query) ? `?${query}` : ''}`),
  runs: (jobId = '') => call(`/api/server/runs${jobId ? `?jobId=${encodeURIComponent(jobId)}` : ''}`),
  deleteRun: (id) => call(`/api/server/runs/${encodeURIComponent(id)}`, { method: 'DELETE', body: {} }),
  /** A finished background run's file: a normal browser download (nothing to wait for in the page). */
  runFile(id) { saveUrl(`/api/server/runs/${encodeURIComponent(id)}/file`); },
  /** The report's .mrd file: a normal browser download. */
  download(ref) { saveUrl(reportUrl(ref, '/file')); },
  /** Everything (or a folder and what is below it) as one ZIP, in the server's folders: a normal browser download. */
  exportZip({ folder = '', reports = true, dashboards = true }) {
    const query = new URLSearchParams({ reports, dashboards, ...(folder ? { folder } : {}) });
    saveUrl(`/api/server/export?${query}`);
  },
  /** Run now; the file is made on the server in the background and saved when ready (on any page). */
  runLater(ref, parameters, format, label) {
    return downloadLater(label || `${idOf(ref)}.${format || 'pdf'}`, reportUrl(ref, '/run'),
      { method: 'POST', body: { parameters, format } });
  },
  run(ref, parameters, format) {
    return trackDownload(`${idOf(ref)}.${format || 'pdf'}`, async () => {
      const response = await call(reportUrl(ref, '/run'), { method: 'POST', body: { parameters, format }, raw: true });
      return response.blob();
    });
  },
};

const ROLES = ['viewer', 'designer', 'admin'];
export const roleAtLeast = (user, minimum) => Boolean(user) && ROLES.indexOf(user.role) >= ROLES.indexOf(minimum);
