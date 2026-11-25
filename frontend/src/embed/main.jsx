// A dashboard inside another website (/embed/view?token=…#refresh=60&theme=dark), framed by <mrs-dashboard>
// (ui/embed/sdk.js) or an iframe. Talks to this server with the embed token only; no cookies.
import { StrictMode, useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import Dashboard from '../analytics/Dashboard.jsx';
import '../shared/theme.css';
import '../portal/portal.css';
import '../analytics/analytics.css';
import './embed.css';

let token = new URLSearchParams(window.location.search).get('token') || '';
const options = new URLSearchParams(window.location.hash.slice(1));
const seconds = Number(options.get('refresh')) || 0;
const refreshSeconds = seconds > 0 ? Math.max(10, Math.min(86_400, seconds)) : 0;
const theme = options.get('theme') || 'light';
const dark = theme === 'dark' || (theme === 'auto' && window.matchMedia?.('(prefers-color-scheme: dark)').matches);
document.documentElement.dataset.theme = dark ? 'dark' : 'light';

let hostOrigin = '*';
try { if (document.referrer) hostOrigin = new URL(document.referrer).origin; } catch { hostOrigin = '*'; }
const tell = (type, detail) => { if (window.parent !== window) window.parent.postMessage({ source: 'mrs-dashboard', type, ...(detail || {}) }, hostOrigin); };
window.addEventListener('message', (event) => {
  if (event.source !== window.parent || !event.data || typeof event.data !== 'object') return;
  if (event.data.type === 'mrs:token' && typeof event.data.token === 'string') token = event.data.token;
  if (event.data.type === 'mrs:reload') window.dispatchEvent(new CustomEvent('bi-command', { detail: { cmd: 'reload' } }));
});

async function call(path, body) {
  const response = await fetch(path, { method: body ? 'POST' : 'GET', credentials: 'omit', cache: 'no-store',
    headers: { 'X-Embed-Token': token, ...(body ? { 'Content-Type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try { detail = (await response.json()).detail || detail; } catch { /* not JSON */ }
    throw new Error(detail);
  }
  return response.json();
}

function Embedded() {
  const [problem, setProblem] = useState('');
  const source = useMemo(() => ({
    dashboard: async () => {
      const answer = await call('/api/embed/dashboard');
      tell('mrs:ready', { dashboardId: answer.dashboard.id, title: answer.dashboard.name });
      return answer;
    },
    queryBatch: async (queries, filters) => {
      try { return await call('/api/embed/dashboard/query', { tiles: queries.map((q) => q.tileId), filters }); }
      catch (error) { tell('mrs:error', { message: error.message }); throw error; }
    },
  }), []);
  useEffect(() => {
    const observer = new ResizeObserver(() => tell('mrs:height', { height: Math.ceil(document.getElementById('root').scrollHeight) }));
    observer.observe(document.getElementById('root'));
    return () => observer.disconnect();
  }, []);
  return (
    <main className="emb">
      {problem ? <div className="notice error" role="alert">{problem}</div>
        : <Dashboard dashboardId="embedded" source={source} refreshSeconds={refreshSeconds} notify={(text) => { setProblem(text); tell('mrs:error', { message: text }); }} embedded />}
    </main>
  );
}

createRoot(document.getElementById('root')).render(<StrictMode><Embedded /></StrictMode>);
