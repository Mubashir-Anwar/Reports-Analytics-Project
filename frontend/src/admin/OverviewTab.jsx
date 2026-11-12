import { useEffect, useState } from 'react';
import { api } from '../api.js';

export default function OverviewTab({ who, notify }) {
  const [data, setData] = useState(null);
  useEffect(() => {
    Promise.all([api.reports(), api.folders(), api.users(), api.connections()])
      .then(([reports, folders, users, connections]) => setData({ reports, folders, users, connections }))
      .catch((error) => notify(error.message, 'error'));
  }, [notify]);

  const value = (pick) => (data ? pick(data) : '–');
  return (
    <>
      <div className="stats">
        <section className="card stat"><p className="eyebrow">Server repository</p><strong>{value((d) => d.reports.reports.length)}</strong><span>published reports · {value((d) => d.folders.folders.length)} folders</span></section>
        <section className="card stat"><p className="eyebrow">Users</p><strong>{value((d) => d.users.users.length)}</strong><span>{value((d) => (d.users.bootstrapLogin ? 'No admin user yet: "admin" + admin token can sign in' : 'Admin user configured'))}</span></section>
        <section className="card stat"><p className="eyebrow">Data sources</p><strong>{value((d) => d.connections.connections.length)}</strong><span>{value((d) => `${d.connections.connections.filter((c) => c.configured).length} configured`)}</span></section>
        <section className="card stat"><p className="eyebrow">Service</p><strong>{who.version}</strong><span>Areas: {who.areas.join(', ')}</span></section>
      </div>
      <section className="card">
        <h2>How reports reach users</h2>
        <ol className="steps">
          <li><b>Designer</b> (<a href="/designer">/designer</a>) — reports are built and saved in the designer's own workspace. Saving never changes the server.</li>
          <li><b>Publish to server</b> — from the designer toolbar a report is copied into a server folder.</li>
          <li><b>Server</b> (<a href="/server">/server</a>) — users sign in with the accounts on the Users tab, browse the repository and run published reports. <code>/generate</code> links and <code>/ui</code> also serve only published reports.</li>
        </ol>
      </section>
    </>
  );
}
