// /server → a dashboard: shown read-only with the folder permissions of its folder. Filters, slicers and clicks on a
// chart work as in Analytics; changing it is done in Analytics. Loaded only when a dashboard is opened (charts are big).
import { useCallback, useMemo, useState } from 'react';
import '../analytics/analytics.css';
import Dashboard from '../analytics/Dashboard.jsx';
import { saveDashboardFile } from '../analytics/api.js';
import { Notice } from '../portal/Shell.jsx';
import { serverApi } from './serverApi.js';

export default function DashboardViewer({ entry, mayManage, onBack, onMessage }) {
  const [meta, setMeta] = useState(null);
  const [message, setMessage] = useState(null);
  const notify = useCallback((text, kind = 'ok') => setMessage(text ? { text, kind } : null), []);
  const source = useMemo(() => ({
    dashboard: async (id) => { const answer = await serverApi.dashboard(id); setMeta(answer); return answer; },
    queryBatch: (queries, filters) => serverApi.dashboardQuery(entry.id, queries, filters),
  }), [entry.id]);

  const download = async () => {
    try {
      saveDashboardFile(await serverApi.dashboardFile(entry.id), entry.name);
      onMessage({ text: `Downloaded ${entry.name}.mdash: the dashboard and its datasets' definitions (no data). Upload it on another server with Add Resource.`, kind: 'ok' });
    } catch (error) { notify(error.message, 'error'); }
  };

  const actions = (
    <>
      <button type="button" className="btn" onClick={onBack}>← Back</button>
      {meta?.mayOpenAnalytics && <a className="btn" href={`/analytics#dash/${encodeURIComponent(entry.id)}`} target="_blank" rel="noopener">Open in Analytics</a>}
      {mayManage && <button type="button" className="btn" onClick={download}>⤓ Download (.mdash)</button>}
    </>
  );
  return (
    <section className="card server-dashboard" aria-label={`Dashboard ${entry.name}`}>
      <p className="muted small" style={{ margin: '0 0 8px' }}>/{entry.folder} · dashboard · {meta?.access === 'manage' ? 'you may manage it' : 'view only'}</p>
      {message && <div style={{ marginBottom: 10 }}><Notice message={message} /></div>}
      <Dashboard dashboardId={entry.id} datasets={meta?.datasets || []} relationships={meta?.relationships || []} notify={notify}
        source={source} actions={actions} />
    </section>
  );
}
