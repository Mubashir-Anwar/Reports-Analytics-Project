// Thin client for the FastAPI admin API. Token: auto-bootstrap on localhost, else typed by the user.
let token = '';
try { token = sessionStorage.getItem('adminToken') || ''; } catch { /* storage unavailable */ }

export function setToken(value) {
  token = value;
  try { sessionStorage.setItem('adminToken', value); } catch { /* ignore */ }
}
export const hasToken = () => Boolean(token);

async function request(path, { method = 'GET', body, raw = false } = {}) {
  const headers = { 'X-Admin-Token': token };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const data = await response.json();
      detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
    } catch { /* not json */ }
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
  folders: () => request('/api/admin/folders'),
  createFolder: (path) => request('/api/admin/folders', { method: 'POST', body: { path } }),
  reports: () => request('/api/admin/reports'),
  saveReport: (definition, isNew) =>
    isNew
      ? request('/api/admin/reports', { method: 'POST', body: definition })
      : request(`/api/admin/reports/${encodeURIComponent(definition.id)}`, { method: 'PUT', body: definition }),
  deleteReport: (id) => request(`/api/admin/reports/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  connections: () => request('/api/admin/connections'),
  saveConnection: (payload) => request('/api/admin/connections', { method: 'POST', body: payload }),
  testConnectionValues: (payload) => request('/api/admin/connections/test', { method: 'POST', body: payload }),
  testConnection: (id) => request(`/api/admin/connections/${encodeURIComponent(id)}/test`, { method: 'POST' }),
  deleteConnection: (id) => request(`/api/admin/connections/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  importJrxml: (xml) => request('/api/admin/jrxml/import', { method: 'POST', body: { xml } }),
  parseQuery: (language, source, parameters) =>
    request('/api/admin/query/parse', { method: 'POST', body: { language, source, parameters } }),
  preview: (id, parameters) =>
    request(`/api/admin/reports/${encodeURIComponent(id)}/preview`, { method: 'POST', body: { parameters } }),
  async renderPdf(body) {
    const response = await request('/api/admin/designer/render', { method: 'POST', body, raw: true });
    return response.blob();
  },
};
