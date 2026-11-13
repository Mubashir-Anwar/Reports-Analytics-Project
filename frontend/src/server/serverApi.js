// /server API: the login session is an HttpOnly cookie, so no token is kept in the page.
let onSignedOut = () => {};
export const whenSignedOut = (handler) => { onSignedOut = handler; };

async function call(path, { method = 'GET', body, raw = false } = {}) {
  const options = { method, credentials: 'same-origin', headers: {} };
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json'; // the server refuses cookie writes that are not JSON
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = '';
    try { detail = (await response.json()).detail || ''; } catch { /* not JSON */ }
    // A bare "Not Found" means the route itself is missing: the running server is an older version.
    if (response.status === 404 && (!detail || detail === 'Not Found')) {
      detail = 'The server does not have this feature yet (HTTP 404): it is still running an older version. Restart the reporting service (uvicorn) and reload this page.';
    }
    const error = new Error(typeof detail === 'string' && detail ? detail : `Request failed (HTTP ${response.status})`);
    error.status = response.status;
    if (response.status === 401 && path !== '/api/server/login' && path !== '/api/server/me') onSignedOut();
    throw error;
  }
  return raw ? response : response.json();
}

export const serverApi = {
  me: () => call('/api/server/me'),
  login: (username, password) => call('/api/server/login', { method: 'POST', body: { username, password } }),
  logout: () => call('/api/server/logout', { method: 'POST', body: {} }),
  repository: () => call('/api/server/repository'),
  createFolder: (path) => call('/api/server/folders', { method: 'POST', body: { path } }),
  deleteFolder: (path, recursive = false) => call('/api/server/folders', { method: 'DELETE', body: { path, recursive } }),
  folderContents: (path) => call(`/api/server/folders/contents?path=${encodeURIComponent(path)}`),
  deleteReport: (id) => call(`/api/server/reports/${encodeURIComponent(id)}`, { method: 'DELETE', body: {} }),
  details: (id) => call(`/api/server/reports/${encodeURIComponent(id)}`),
  edit: (id, changes) => call(`/api/server/reports/${encodeURIComponent(id)}`, { method: 'PUT', body: changes }),
  copy: (id, folder, newId) => call(`/api/server/reports/${encodeURIComponent(id)}/copy`, { method: 'POST', body: { folder, ...(newId ? { newId } : {}) } }),
  move: (id, folder) => call(`/api/server/reports/${encodeURIComponent(id)}/move`, { method: 'POST', body: { folder } }),
  upload: (body) => call('/api/server/upload', { method: 'POST', body }),
  connections: () => call('/api/server/connections'),
  testConnection: (id) => call(`/api/server/connections/${encodeURIComponent(id)}/test`, { method: 'POST', body: {} }),
  // folder permissions (administrators)
  permissions: (path) => call(`/api/server/permissions?path=${encodeURIComponent(path || '')}`),
  setPermissions: (path, rules) => call('/api/server/permissions', { method: 'PUT', body: { path, rules } }),
  // versions of a server report
  versions: (id) => call(`/api/server/reports/${encodeURIComponent(id)}/versions`),
  version: (id, number) => call(`/api/server/reports/${encodeURIComponent(id)}/versions/${number}`),
  restoreVersion: (id, number) => call(`/api/server/reports/${encodeURIComponent(id)}/versions/${number}/restore`, { method: 'POST', body: {} }),
  // schedules and background runs
  scheduleInfo: () => call('/api/server/schedules/info'),
  schedules: (reportId = '') => call(`/api/server/schedules${reportId ? `?reportId=${encodeURIComponent(reportId)}` : ''}`),
  createSchedule: (body) => call('/api/server/schedules', { method: 'POST', body }),
  updateSchedule: (id, body) => call(`/api/server/schedules/${encodeURIComponent(id)}`, { method: 'PUT', body }),
  enableSchedule: (id, enabled) => call(`/api/server/schedules/${encodeURIComponent(id)}/enabled`, { method: 'POST', body: { enabled } }),
  deleteSchedule: (id, outputs) => call(`/api/server/schedules/${encodeURIComponent(id)}${outputs ? '?outputs=true' : ''}`, { method: 'DELETE', body: {} }),
  runScheduleNow: (id) => call(`/api/server/schedules/${encodeURIComponent(id)}/run`, { method: 'POST', body: {} }),
  runInBackground: (id, parameters, format) => call(`/api/server/reports/${encodeURIComponent(id)}/background`, { method: 'POST', body: { parameters, format } }),
  runs: (jobId = '') => call(`/api/server/runs${jobId ? `?jobId=${encodeURIComponent(jobId)}` : ''}`),
  deleteRun: (id) => call(`/api/server/runs/${encodeURIComponent(id)}`, { method: 'DELETE', body: {} }),
  async runFile(id) {
    const response = await call(`/api/server/runs/${encodeURIComponent(id)}/file`, { raw: true });
    const name = (response.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/)?.[1] || id;
    return { blob: await response.blob(), name };
  },
  async download(id) {
    const response = await call(`/api/server/reports/${encodeURIComponent(id)}/file`, { raw: true });
    return response.blob();
  },
  async run(id, parameters, format) {
    const response = await call(`/api/server/reports/${encodeURIComponent(id)}/run`, { method: 'POST', body: { parameters, format }, raw: true });
    return response.blob();
  },
};

const ROLES = ['viewer', 'designer', 'admin'];
export const roleAtLeast = (user, minimum) => Boolean(user) && ROLES.indexOf(user.role) >= ROLES.indexOf(minimum);
