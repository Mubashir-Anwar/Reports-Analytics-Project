// Thin client for the FastAPI admin API. It uses the signed-in user's session cookie (set at /login);
// the server checks that account's rights (Designer, Admin, …) on every call. No token is kept in the page.
import { goSignIn, reach } from './shared/session.js';
import { downloadLater, trackDownload } from './shared/downloads.js';

export async function request(path, { method = 'GET', body, raw = false } = {}) {
  const headers = {};
  // Signed-in writes must be JSON (a cross-site form cannot send that), so DELETE/POST without a body send {}.
  const send = body === undefined && method !== 'GET' ? {} : body;
  if (send !== undefined) headers['Content-Type'] = 'application/json';
  const response = await reach(path, { method, headers, credentials: 'same-origin', body: send === undefined ? undefined : JSON.stringify(send) });
  if (response.status === 401) goSignIn(); // the session ended: sign in again and come back to this page
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

/** ?folder=…: a report is <folder>/<ID> — the same ID may be in several folders (workspace and server). */
const inFolder = (folder) => (folder ? `folder=${encodeURIComponent(folder)}` : '');
const withQuery = (path, ...parts) => { const q = parts.filter(Boolean).join('&'); return q ? `${path}?${q}` : path; };

export const api = {
  // designer workspace: internal saves; nothing here is on the server until it is published
  workspace: () => request('/api/admin/workspace'),
  workspaceReport: (id, folder) => request(withQuery(`/api/admin/workspace/${encodeURIComponent(id)}`, inFolder(folder))),
  /** fromFolder = where the open draft was saved before (Setup may have moved it to another folder). */
  saveWorkspace: (definition, isNew, fromFolder) =>
    request(withQuery(`/api/admin/workspace/${encodeURIComponent(definition.id)}`, isNew ? 'create=true' : inFolder(fromFolder)), { method: 'PUT', body: definition }),
  deleteWorkspace: (id, folder) => request(withQuery(`/api/admin/workspace/${encodeURIComponent(id)}`, inFolder(folder)), { method: 'DELETE' }),
  createWorkspaceFolder: (path) => request('/api/admin/workspace-folders', { method: 'POST', body: { path } }),
  deleteWorkspaceFolder: (path) => request('/api/admin/workspace-folders', { method: 'DELETE', body: { path } }),
  /** draftFolder = the workspace folder of the draft; folder = the server folder it goes to. */
  publish: (id, { folder, replace }, draftFolder) =>
    request(withQuery(`/api/admin/workspace/${encodeURIComponent(id)}/publish`, inFolder(draftFolder)), { method: 'POST', body: { folder, replace } }),
  draftPreview: (definition, parameters, connectionId) =>
    request('/api/admin/drafts/preview', { method: 'POST', body: { definition, parameters, connectionId } }),
  draftRun(definition, { parameters, connectionId, format }) {
    return trackDownload(`${definition?.title || definition?.id || 'Report'}.${format || 'pdf'}`, async () => {
      const response = await request('/api/admin/drafts/run', { method: 'POST', body: { definition, parameters, connectionId, format }, raw: true });
      return response.blob();
    });
  },
  /** Export the draft as a file made on the server in the background; saved when ready (leaving the page is fine). */
  draftRunLater(definition, { parameters, connectionId, format }) {
    return downloadLater(`${definition?.title || definition?.id || 'Report'}.${format || 'pdf'}`, '/api/admin/drafts/run',
      { method: 'POST', body: { definition, parameters, connectionId, format } });
  },
  // server repository (published reports)
  report: (id, folder) => request(withQuery(`/api/admin/reports/${encodeURIComponent(id)}`, inFolder(folder))),
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
  deleteReport: (id, folder) => request(withQuery(`/api/admin/reports/${encodeURIComponent(id)}`, inFolder(folder)), { method: 'DELETE' }),
  connections: () => request('/api/admin/connections'),
  systemStatus: () => request('/api/admin/system/status'),
  saveConnection: (payload) => request('/api/admin/connections', { method: 'POST', body: payload }),
  testConnectionValues: (payload) => request('/api/admin/connections/test', { method: 'POST', body: payload }),
  testConnection: (id) => request(`/api/admin/connections/${encodeURIComponent(id)}/test`, { method: 'POST' }),
  deleteConnection: (id) => request(`/api/admin/connections/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  /** A .jrxml and the pictures it uses ({ 'logo.png': dataURL }): image elements get the matching file. */
  importJrxml: (xml, resources = {}) => request('/api/admin/jrxml/import', { method: 'POST', body: { xml, resources } }),
  /** An existing design (PDF, .ai, Word, PNG/JPEG as a data URL) → { page, background, elements, fields, notes, pageCount }. */
  importDesign: (file, name, mode, page, textOnTop) => request('/api/admin/designer/import-design', { method: 'POST', body: { file, name, mode, page, textOnTop } }),
  /** SQL "Read fields": the result columns from the database (LIMIT 0, no rows read). */
  queryFields: (connectionId, querySource) => request('/api/admin/query/fields', { method: 'POST', body: { connectionId, querySource } }),
  parseQuery: (language, source, parameters) =>
    request('/api/admin/query/parse', { method: 'POST', body: { language, source, parameters } }),
  preview: (id, parameters, connectionId) =>
    request(`/api/admin/reports/${encodeURIComponent(id)}/preview`, { method: 'POST', body: { parameters, connectionId } }),
  /** Run the saved report in full on a chosen data adapter; returns the file Blob. */
  runReport(id, { parameters, connectionId, format }) {
    return downloadLater(`${id}.${format || 'pdf'}`, `/api/admin/reports/${encodeURIComponent(id)}/run`,
      { method: 'POST', body: { parameters, connectionId, format } });
  },
  // administration (admin token only)
  whoami: () => request('/api/admin/whoami'),
  users: () => request('/api/admin/users'),
  createUser: (payload) => request('/api/admin/users', { method: 'POST', body: payload }),
  updateUser: (username, change) => request(`/api/admin/users/${encodeURIComponent(username)}`, { method: 'PUT', body: change }),
  deleteUser: (username) => request(`/api/admin/users/${encodeURIComponent(username)}`, { method: 'DELETE' }),
  apiKeys: () => request('/api/admin/api-keys'),
  createApiKey: (payload) => request('/api/admin/api-keys', { method: 'POST', body: payload }),
  updateApiKey: (id, change) => request(`/api/admin/api-keys/${encodeURIComponent(id)}`, { method: 'PUT', body: change }),
  deleteApiKey: (id) => request(`/api/admin/api-keys/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  logs: ({ lines, level, search }) => request(`/api/admin/logs?${new URLSearchParams({ lines, level, search })}`),
  audit: (limit) => request(`/api/admin/audit?limit=${encodeURIComponent(limit)}`),
  auditEvents: (limit) => request(`/api/admin/audit-events?limit=${encodeURIComponent(limit)}`),
  verifyAudit: () => request('/api/admin/audit-events/verify'),
  async logFile() { return (await request('/api/admin/logs/download', { raw: true })).blob(); },
  /** Custom functions: check them and run one test expression ({ value, type }). */
  tryFunction: (functions, expression, values) => request('/api/admin/designer/functions/try', { method: 'POST', body: { functions, expression, values } }),
  validateDesign: (definition) => request('/api/admin/designer/validate', { method: 'POST', body: { definition } }),
  /** Render the design as 'pdf' | 'xlsx' | 'csv' (body.format) and return the file as a Blob. */
  renderFile(body) {
    const render = async () => {
      const response = await request('/api/admin/designer/render', { method: 'POST', body, raw: true });
      return response.blob();
    };
    // the live PDF preview is not a download; an Excel/CSV file is
    const format = String(body?.format || 'pdf').toLowerCase();
    return format === 'pdf' ? render() : trackDownload(`${body?.title || 'Design'}.${format}`, render);
  },
  /** The design (with the rows it was given) as a file made on the server in the background. */
  renderFileLater(body, label) {
    return downloadLater(label || `${body?.title || 'Design'}.${body?.format || 'pdf'}`, '/api/admin/designer/render', { method: 'POST', body });
  },
};
