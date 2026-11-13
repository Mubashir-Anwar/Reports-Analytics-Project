// Thin client for the FastAPI admin API. Token: auto-bootstrap on localhost, else typed by the user.
let token = '';
try { token = sessionStorage.getItem('adminToken') || ''; } catch { /* storage unavailable */ }

export function setToken(value) {
  token = value;
  try { sessionStorage.setItem('adminToken', value); } catch { /* ignore */ }
}
export const hasToken = () => Boolean(token);
export function clearToken() {
  token = '';
  try { sessionStorage.removeItem('adminToken'); } catch { /* ignore */ }
}

export async function request(path, { method = 'GET', body, raw = false } = {}) {
  const headers = { 'X-Admin-Token': token };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const data = await response.json();
      detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
    } catch { /* not json */ }
    // A bare "Not Found" means the route itself is missing: the running server is an older version.
    if (response.status === 404 && (!detail || detail === 'Not Found')) {
      detail = 'The server does not have this feature (HTTP 404). It is probably still running an older version: restart the reporting service and reload this page.';
    }
    const error = new Error(detail || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return raw ? response : response.json();
}

export const api = {
  async bootstrap() {
    const response = await fetch('/api/admin/bootstrap');
    if (!response.ok) return false;
    setToken((await response.json()).token);
    return true;
  },
  // designer workspace: internal saves; nothing here is on the server until it is published
  workspace: () => request('/api/admin/workspace'),
  workspaceReport: (id) => request(`/api/admin/workspace/${encodeURIComponent(id)}`),
  saveWorkspace: (definition, isNew) =>
    request(`/api/admin/workspace/${encodeURIComponent(definition.id)}${isNew ? '?create=true' : ''}`, { method: 'PUT', body: definition }),
  deleteWorkspace: (id) => request(`/api/admin/workspace/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  createWorkspaceFolder: (path) => request('/api/admin/workspace-folders', { method: 'POST', body: { path } }),
  deleteWorkspaceFolder: (path) => request('/api/admin/workspace-folders', { method: 'DELETE', body: { path } }),
  publish: (id, { folder, replace }) =>
    request(`/api/admin/workspace/${encodeURIComponent(id)}/publish`, { method: 'POST', body: { folder, replace } }),
  draftPreview: (definition, parameters, connectionId) =>
    request('/api/admin/drafts/preview', { method: 'POST', body: { definition, parameters, connectionId } }),
  async draftRun(definition, { parameters, connectionId, format }) {
    const response = await request('/api/admin/drafts/run', { method: 'POST', body: { definition, parameters, connectionId, format }, raw: true });
    return response.blob();
  },
  // server repository (published reports)
  report: (id) => request(`/api/admin/reports/${encodeURIComponent(id)}`),
  folders: () => request('/api/admin/folders'),
  createFolder: (path) => request('/api/admin/folders', { method: 'POST', body: { path } }),
  deleteFolder: (path, recursive = false) => request('/api/admin/folders', { method: 'DELETE', body: { path, recursive } }),
  folderContents: (path) => request(`/api/admin/folders/contents?path=${encodeURIComponent(path)}`),
  moveConnection: (id, folder) => request(`/api/admin/connections/${encodeURIComponent(id)}/folder`, { method: 'PUT', body: { folder } }),
  reports: () => request('/api/admin/reports'),
  saveReport: (definition, isNew) =>
    isNew
      ? request('/api/admin/reports', { method: 'POST', body: definition })
      : request(`/api/admin/reports/${encodeURIComponent(definition.id)}`, { method: 'PUT', body: definition }),
  deleteReport: (id) => request(`/api/admin/reports/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  connections: () => request('/api/admin/connections'),
  systemStatus: () => request('/api/admin/system/status'),
  saveConnection: (payload) => request('/api/admin/connections', { method: 'POST', body: payload }),
  testConnectionValues: (payload) => request('/api/admin/connections/test', { method: 'POST', body: payload }),
  testConnection: (id) => request(`/api/admin/connections/${encodeURIComponent(id)}/test`, { method: 'POST' }),
  deleteConnection: (id) => request(`/api/admin/connections/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  importJrxml: (xml) => request('/api/admin/jrxml/import', { method: 'POST', body: { xml } }),
  /** SQL "Read fields": the result columns from the database (LIMIT 0, no rows read). */
  queryFields: (connectionId, querySource) => request('/api/admin/query/fields', { method: 'POST', body: { connectionId, querySource } }),
  parseQuery: (language, source, parameters) =>
    request('/api/admin/query/parse', { method: 'POST', body: { language, source, parameters } }),
  preview: (id, parameters, connectionId) =>
    request(`/api/admin/reports/${encodeURIComponent(id)}/preview`, { method: 'POST', body: { parameters, connectionId } }),
  /** Run the saved report in full on a chosen data adapter; returns the file Blob. */
  async runReport(id, { parameters, connectionId, format }) {
    const response = await request(`/api/admin/reports/${encodeURIComponent(id)}/run`, { method: 'POST', body: { parameters, connectionId, format }, raw: true });
    return response.blob();
  },
  // administration (admin token only)
  whoami: () => request('/api/admin/whoami'),
  users: () => request('/api/admin/users'),
  createUser: (payload) => request('/api/admin/users', { method: 'POST', body: payload }),
  updateUser: (username, change) => request(`/api/admin/users/${encodeURIComponent(username)}`, { method: 'PUT', body: change }),
  deleteUser: (username) => request(`/api/admin/users/${encodeURIComponent(username)}`, { method: 'DELETE' }),
  logs: ({ lines, level, search }) => request(`/api/admin/logs?${new URLSearchParams({ lines, level, search })}`),
  audit: (limit) => request(`/api/admin/audit?limit=${encodeURIComponent(limit)}`),
  async logFile() { return (await request('/api/admin/logs/download', { raw: true })).blob(); },
  validateDesign: (definition) => request('/api/admin/designer/validate', { method: 'POST', body: { definition } }),
  /** Render the design as 'pdf' | 'xlsx' | 'csv' (body.format) and return the file as a Blob. */
  async renderFile(body) {
    const response = await request('/api/admin/designer/render', { method: 'POST', body, raw: true });
    return response.blob();
  },
};
