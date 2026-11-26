// /home: where everyone lands after signing in. What this account can do (by its rights), the next step, and a way
// into each part. Administration is one small line, and only for administrators.
import SiteFooter from '../shared/SiteFooter.jsx';
import { useEffect, useState } from 'react';
import { request } from '../api.js';
import { Icon } from '../shared/NavBar.jsx';
import PixelField from '../shared/PixelField.jsx';
import { Topbar } from '../portal/Shell.jsx';
import { AdapterWizard } from '../shared/adapters.jsx';
import { SetupAdminBanner } from '../shared/SetupAdmin.jsx';
import { goSignIn, useMe } from '../shared/session.js';

const many = (n, word) => `${n.toLocaleString()} ${word}${n === 1 ? '' : 's'}`;

const PARTS = [
  { right: 'designer', tint: 'v', name: 'Designer', href: '/designer', text: 'Design a report, preview it and publish it.',
    fact: (d) => (d.reports ? `${many(d.reports, 'report')}${d.notPublished ? ` · ${d.notPublished} not published` : ''}` : 'No reports yet'),
    links: () => [['Open Designer', '/designer']] },
  { right: 'analytics', tint: 'c', name: 'Analytics', href: '/analytics', text: 'Dashboards, maps and machine learning on your data.',
    fact: (d) => (d.dashboards || d.datasets ? `${many(d.dashboards || 0, 'dashboard')} · ${many(d.datasets || 0, 'dataset')}` : 'No dashboards yet'),
    links: (d) => [['Open Analytics', '/analytics'], ...(d.mayModel ? [['New dataset', '/analytics#ds/new']] : []), ['ML lab', '/analytics#ml']] },
  { right: 'query', tint: 'g', name: 'Query', href: '/query', text: 'Look into your databases, try a query, then use it in a report or in Analytics.',
    fact: () => 'Read-only',
    links: () => [['Open Query', '/query']] },
  { right: 'server', tint: 'b', name: 'Server', href: '/server', text: 'Run, schedule and share published reports and dashboards.',
    fact: (d) => (d.reports ? `${many(d.reports, 'report')} in ${many(d.folders || 0, 'folder')}` : 'Nothing published yet'),
    links: () => [['Open Server', '/server']] },
];

/** Where each part keeps its data sources. */
const CONNECT = { designer: '/designer', analytics: '/analytics#sources', query: '/query#sources', server: '/server#sources' };
const mayConnect = (right, rights) => rights.includes(right) && (right !== 'analytics' || rights.includes('designer') || rights.includes('admin'));

/** The steps that make sense for these rights, with what is done already. */
function stepsFor(rights, data, lastPart) {
  const has = (right) => rights.includes(right);
  const out = [];
  const part = PARTS.find((p) => p.right === lastPart && mayConnect(p.right, rights)) || PARTS.find((p) => mayConnect(p.right, rights));
  if (part) {   // the part this person works in; the other parts connect theirs on their own card
    out.push({ done: data[part.right]?.dataSources > 0, title: `Connect data for ${part.name}`, text: `MongoDB, PostgreSQL, MySQL, SQL Server or Oracle. Only for ${part.name}.`,
      connect: part.right, href: CONNECT[part.right], go: 'Connect a database' });
  }
  if (has('query')) out.push({ optional: true, title: 'Look at it', text: 'Browse tables and try a query.', href: '/query' });
  if (has('designer')) out.push({ done: data.designer?.reports > 0, title: 'Make a report', text: 'Design it and publish it to the Server.', href: '/designer', go: 'Open Designer' });
  if (has('analytics')) out.push({ done: data.analytics?.dashboards > 0, title: 'Make a dashboard', text: 'Charts, maps and predictions.', href: '/analytics', go: 'Open Analytics' });
  if (has('server')) out.push({ optional: true, title: 'Run and share', text: 'Run reports, or let them run on a schedule.', href: '/server' });
  return out;
}

function greeting() {
  const hour = new Date().getHours();
  return hour < 5 ? 'Up late' : hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening';
}

export default function HomeApp() {
  const me = useMe();
  const [data, setData] = useState(null);
  const [problem, setProblem] = useState('');
  useEffect(() => { if (me === null) goSignIn(); }, [me]);
  useEffect(() => { if (me) request('/api/home').then(setData).catch((error) => setProblem(error.message)); }, [me]);

  const rights = me?.rights || [];
  const parts = PARTS.filter((part) => rights.includes(part.right));
  const [lastPart] = useState(() => { try { return localStorage.getItem('last-part') || ''; } catch { return ''; } });
  const [connecting, setConnecting] = useState('');       // the part a data source is being connected for
  const steps = data ? stepsFor(rights, data, lastPart) : [];
  const next = steps.find((step) => !step.done && !step.optional) || null;
  const recent = data ? [
    ...(data.designer?.recent || []).map((r) => ({ key: `r${r.folder}/${r.id}`, icon: 'designer', tint: 'v', name: r.title, note: r.folder ? `Report · /${r.folder}` : 'Report',
      href: `/designer?open=${encodeURIComponent(r.id)}&folder=${encodeURIComponent(r.folder || '')}` })),
    ...(data.analytics?.recent || []).map((d) => ({ key: d.id, icon: 'analytics', tint: 'c', name: d.name, note: `Dashboard · ${many(d.tiles, 'visual')}`, href: `/analytics#dash/${d.id}` })),
  ].slice(0, 6) : [];

  return (
    <>
      <Topbar current="/home" />
      <main className="page hm">
        {me?.temporary && <SetupAdminBanner me={me} />}
        {me && (
          <section className="hm-hero px-in">
            <div>
              <p className="hm-hi">{greeting()}, <b>{me.username}</b></p>
              <h1>{next ? next.title : 'What would you like to do?'}</h1>
              <p>{next ? next.text : 'Pick up where you left off, or start something new.'}</p>
              {next?.connect && <button type="button" className="btn primary hm-go" onClick={() => setConnecting(next.connect)}>{next.go} →</button>}
              {next && !next.connect && <a className="btn primary hm-go" href={next.href}>{next.go} →</a>}
              {!next && parts[0] && <a className="btn primary hm-go" href={parts[0].href}>Open {parts[0].name} →</a>}
            </div>
            <span className="hm-art"><PixelField cell={10} gap={3} bars={8} /></span>
          </section>
        )}
        {problem && <div className="notice error">{problem}</div>}

        {steps.length > 1 && (
          <ol className="hm-steps" aria-label="How it works">
            {steps.map((step, i) => (
              <li key={step.title} className={`${step.done ? 'done' : ''} ${step === next ? 'next' : ''}`}>
                <a href={step.href} onClick={(e) => { if (step.connect && !step.done) { e.preventDefault(); setConnecting(step.connect); } }}><span className="hm-num" aria-hidden="true">{step.done ? '✓' : i + 1}</span><span><b>{step.title}</b><small>{step.text}</small></span></a>
              </li>
            ))}
          </ol>
        )}

        <div className="hm-parts">
          {parts.map((part, i) => (
            <section key={part.right} className={`hm-part tint-${part.tint}`} style={{ '--i': i }}>
              {part.right === lastPart && <em className="hm-last">Last used</em>}
              <span className="hm-ico"><Icon name={part.right} size={22} /></span>
              <h2>{part.name}</h2>
              <p>{part.text}</p>
              <small className="hm-fact">{data?.[part.right] ? part.fact(data[part.right]) : ' '}</small>
              {data?.[part.right] && mayConnect(part.right, rights) && (
                <div className={`hm-data ${data[part.right].dataSources ? '' : 'none'}`}>
                  <a href={CONNECT[part.right]}>{data[part.right].dataSources ? many(data[part.right].dataSources, 'data source') : 'No data source'}</a>
                  <button type="button" onClick={() => setConnecting(part.right)} title={`Connect a database for ${part.name} only`}>＋ Connect data</button>
                </div>
              )}
              <div className="hm-links">{part.links(data?.[part.right] || {}).map(([text, href], n) => <a key={href} className={n ? 'hm-more' : 'hm-open'} href={href}>{text}{n ? '' : ' →'}</a>)}</div>
            </section>
          ))}
        </div>
        {me && !parts.length && !rights.includes('admin') && <div className="empty">Your account may not open any part yet. Ask an administrator.</div>}

        {recent.length > 0 && (
          <section className="hm-recent"><h2>Recent</h2>
            <div>{recent.map((item) => <a key={item.key} href={item.href}><span className={`hm-dot tint-${item.tint}`}><Icon name={item.icon} size={15} /></span><span><b>{item.name}</b><small>{item.note}</small></span></a>)}</div>
          </section>
        )}

        {connecting && (
          <AdapterWizard initial={{}} area={connecting} onClose={() => setConnecting('')}
            onSaved={() => { window.location.assign(connecting === 'query' ? '/query' : CONNECT[connecting]); }} />
        )}

        {rights.includes('admin') && (
          <section className="hm-admin"><Icon name="admin" size={18} />
            <span>{data?.admin ? <>{many(data.admin.users ?? 0, 'user')}{data.admin.failedRuns24h ? <> · <b className="hm-bad">{many(data.admin.failedRuns24h, 'failed run')}</b> in 24 hours</> : ' · no failed runs in 24 hours'}</> : 'Administration'}</span>
            <a href="/admin#users">Users</a>{data?.admin?.failedRuns24h > 0 && <a href="/admin#logs">Logs</a>}<a href="/admin">Admin →</a>
          </section>
        )}
      </main>
      <SiteFooter />
    </>
  );
}
