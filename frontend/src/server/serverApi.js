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
