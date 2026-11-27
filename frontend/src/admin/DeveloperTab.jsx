// /admin → API & Postman: how a website connects with an API key (step by step), the Postman collection and
// environment for this server, and ready-to-copy calls (curl, JavaScript, Python) for a chosen report. The API key is
// pasted by the admin and only used on this page; it is never shown again after it is created.
import { useEffect, useMemo, useState } from 'react';
import { api, request } from '../api.js';
import { downloadBlob } from '../portal/Shell.jsx';

const origin = window.location.origin;

async function download(path, name, notify) {
  try {
    const response = await request(path, { raw: true });
    downloadBlob(await response.blob(), name);
    notify(`Downloaded ${name}`);
  } catch (error) { notify(error.message, 'error'); }
}

const copy = (text, title, notify) => navigator.clipboard.writeText(text)
  .then(() => notify(`Copied: ${title}`), () => notify('Copy failed: select the text and copy it', 'warn'));

const KEY_STEPS = [
  ['🔑', 'Create a key', <>Admin → <a href="#keys">API keys</a> → <b>New key</b>. Choose the folders it may run. The secret (<code>mrk_…</code>) is shown <b>once</b>: copy it.</>],
  ['🧳', 'Keep it on the server', <>Put it in your website's server settings, e.g. <code>REPORTS_API_KEY</code>. Never in browser JavaScript or a public repository.</>],
  ['📨', 'Send it with each call', <>Header <code>X-API-Key: mrk_…</code> on every request to <code>/api/v1/…</code> or <code>/generate/…</code>.</>],
  ['🚀', 'Get the report', <>PDF, Excel, CSV or JSON comes back. Big files: add <code>X-Deliver: later</code> and collect them when ready.</>],
];

const POSTMAN_STEPS = [
  ['⬇', 'Download', 'Both files below: the collection (every API) and the environment for this server.'],
  ['📥', 'Import', 'Postman → Import → drop both files. Pick the environment at the top right.'],
  ['✏️', 'Fill in', 'In the environment: password (your sign-in) and apiKey (your key). baseUrl is already this server.'],
  ['▶', 'Send', 'One request at a time from folder 12 Public API v1, or Run the whole collection on a test server.'],
];

const ANSWERS = [
  ['200', 'ok', 'Done: the file or the data is in the answer.'],
  ['401', 'warn', 'No key, or a wrong one: check the X-API-Key header.'],
  ['403', 'warn', 'The key may not run this report: add its folder to the key.'],
  ['404', 'warn', 'No such report ID (the answer says “Did you mean …?”).'],
  ['422', 'warn', 'A parameter is missing or has the wrong type.'],
  ['429', 'err', 'Too many calls a minute for this key: wait (Retry-After) or raise its limit.'],
  ['503', 'err', 'Every report place is busy: try again shortly (Admin → Limits).'],
];

/** One ready call: the method and path up top, the code below, one Copy button. */
function Snippet({ title, text, notify }) {
  const method = /\b(GET|POST)\b/.exec(title)?.[1] || (/-X POST|method: "POST"|requests\.post/.test(text) ? 'POST' : 'GET');
  const path = (/https?:\/\/[^/"\s]+(\/[^"\s?]*)/.exec(text) || [])[1] || '';
  const name = title.replace(/\s*\((GET|POST)\)/, '');
  const [done, setDone] = useState(false);
  const onCopy = () => { copy(text, name, notify); setDone(true); setTimeout(() => setDone(false), 1400); };
  return (
    <div className="dev-snippet">
      <div className="dev-snippet-head">
        <span className={`dev-method ${method.toLowerCase()}`}>{method}</span>
        <b>{name}</b>
        {path && <code className="dev-path">{path}</code>}
        <button type="button" className={`btn sm dev-copy ${done ? 'done' : ''}`} onClick={onCopy}>{done ? '✓ Copied' : 'Copy'}</button>
      </div>
      <pre>{text}</pre>
    </div>
  );
}

/** The animated picture at the top: your website → key → this server → the report. */
function FlowHero() {
  return (
    <div className="dev-hero card px-grid">
      <div className="dev-hero-text">
        <p className="px-kicker"><b>API</b> — Admin · API &amp; Postman</p>
        <h2>Connect any website in 4 steps<span className="px-caret" aria-hidden="true" /></h2>
        <p className="muted">Your website's server sends an API key with each call; this server answers with the report. No sign-in, no cookies.</p>
      </div>
      <div className="dev-flow" aria-hidden="true">
        <span className="dev-node">🌐<small>Your site</small></span>
        <span className="dev-wire"><i /></span>
        <span className="dev-node key">🔑<small>X-API-Key</small></span>
        <span className="dev-wire"><i /></span>
        <span className="dev-node">🗄️<small>{new URL(origin).host}</small></span>
        <span className="dev-wire"><i /></span>
        <span className="dev-node">📄<small>PDF · Excel · JSON</small></span>
      </div>
    </div>
  );
}

function Steps({ items, className = '' }) {
  return (
    <ol className={`dev-steps ${className}`}>
      {items.map(([icon, title, text], index) => (
        <li key={title} style={{ '--i': index }}>
          <span className="dev-step-ico" aria-hidden="true">{icon}</span>
          <span><b><em>{index + 1}</em> {title}</b><small>{text}</small></span>
        </li>
      ))}
    </ol>
  );
}

/** Calls /api/v1/reports with the pasted key, so the admin sees at once whether the key works and what it may run. */
function KeyCheck({ apiKey, notify }) {
  const [state, setState] = useState(null);
  const check = async () => {
    if (!apiKey) { notify('Paste an API key first', 'warn'); return; }
    setState({ busy: true });
    try {
      const response = await fetch('/api/v1/reports', { headers: { 'X-API-Key': apiKey }, credentials: 'omit' });
      const data = await response.json().catch(() => ({}));
      setState({ status: response.status, count: (data.reports || []).length, detail: data.detail });
    } catch (error) { setState({ status: 0, detail: error.message }); }
  };
  const ok = state?.status === 200;
  return (
    <div className="dev-keycheck">
      <button type="button" className="btn" disabled={state?.busy} onClick={check}>{state?.busy ? 'Checking…' : '🧪 Test this key'}</button>
      {state && !state.busy && (
        <span className={`dev-verdict ${ok ? 'ok' : 'bad'}`} role="status">
          {ok ? `✓ Works: it may run ${state.count} report${state.count === 1 ? '' : 's'}` : `✕ HTTP ${state.status || '—'}: ${state.detail || 'not accepted'}`}
        </span>
      )}
    </div>
  );
}

const LANGS = [['curl', 'curl'], ['js', 'JavaScript (Node)'], ['py', 'Python']];

export default function DeveloperTab({ notify }) {
  const [reports, setReports] = useState([]);
  const [reportId, setReportId] = useState('');
  const [key, setKey] = useState('');
  const [values, setValues] = useState({});
  const [lang, setLang] = useState('curl');
  useEffect(() => {
    api.reports().then((data) => {
      const list = data.reports || [];
      setReports(list);
      if (list.length) setReportId(list[0].id);
    }).catch((error) => notify(error.message, 'error'));
  }, [notify]);

  const report = reports.find((item) => item.id === reportId);
  const params = useMemo(() => (report?.parameters || []).filter((p) => p.prompt !== false && !p.expression), [report]);
  const apiKey = key.trim() || 'mrk_…';
  const filled = Object.fromEntries(params.map((p) => [p.name, values[`${reportId}.${p.name}`] ?? (p.default ?? '')]));
  const query = new URLSearchParams(Object.entries(filled).filter(([, v]) => v !== '')).toString();
  const json = JSON.stringify({ parameters: filled, limit: 100, offset: 0 });
  const runJson = JSON.stringify({ parameters: filled, format: 'pdf' });
  const q = (text) => text.replace(/"/g, '\\"');
  const id = reportId || '<REPORT_ID>';

  const snippets = {
    curl: [
      ['PDF by URL (GET)', `curl -H "X-API-Key: ${apiKey}" "${origin}/generate/${id}.pdf${query ? `?${query}` : ''}" -o ${id}.pdf`],
      ['Data as JSON (POST)', `curl -X POST "${origin}/api/v1/reports/${id}/data" -H "X-API-Key: ${apiKey}" -H "Content-Type: application/json" -d "${q(json)}"`],
      ['File from JSON (POST)', `curl -X POST "${origin}/api/v1/reports/${id}/run" -H "X-API-Key: ${apiKey}" -H "Content-Type: application/json" -d "${q(runJson)}" -o ${id}.pdf`],
      ['Reports this key may run', `curl -H "X-API-Key: ${apiKey}" "${origin}/api/v1/reports"`],
    ],
    js: [
      ['File from JSON (Node 18+)', `const response = await fetch("${origin}/api/v1/reports/${id}/run", {
  method: "POST",
  headers: { "X-API-Key": process.env.REPORTS_API_KEY, "Content-Type": "application/json" },
  body: JSON.stringify(${runJson}),
});
if (!response.ok) throw new Error(\`Report failed: \${response.status} \${await response.text()}\`);
await require("fs/promises").writeFile("${id}.pdf", Buffer.from(await response.arrayBuffer()));`],
      ['Data as JSON', `const response = await fetch("${origin}/api/v1/reports/${id}/data", {
  method: "POST",
  headers: { "X-API-Key": process.env.REPORTS_API_KEY, "Content-Type": "application/json" },
  body: JSON.stringify(${json}),
});
const { rows } = await response.json();`],
    ],
    py: [
      ['File from JSON (requests)', `import os, requests

response = requests.post(
    "${origin}/api/v1/reports/${id}/run",
    headers={"X-API-Key": os.environ["REPORTS_API_KEY"]},
    json=${runJson},
    timeout=120,
)
response.raise_for_status()
open("${id}.pdf", "wb").write(response.content)`],
      ['Data as JSON', `import os, requests

rows = requests.post(
    "${origin}/api/v1/reports/${id}/data",
    headers={"X-API-Key": os.environ["REPORTS_API_KEY"]},
    json=${json},
    timeout=60,
).json()["rows"]`],
    ],
  };

  return (
    <div className="dev-page">
      <FlowHero />

      <section className="card dev-card">
        <div className="card-head"><div><p className="eyebrow">API key</p><h2>How a website connects</h2></div></div>
        <Steps items={KEY_STEPS} />
        <p className="dev-tip">💡 One key per website or system: you can then see its calls in Report Server → Logs → API calls, limit it, or turn it off alone.</p>
      </section>

      <section className="card dev-card">
        <div className="card-head"><div><p className="eyebrow">Postman</p><h2>Every API of this server</h2></div></div>
        <p className="muted">The collection has every endpoint (sign-in, data sources, Designer, Query, Server, Analytics, Admin, Public API v1, embedding) in an order that runs end to end. The environment is filled in for <b>{origin}</b>; secrets stay empty.</p>
        <div className="row-actions">
          <button className="btn primary dev-dl" onClick={() => download('/api/admin/postman/collection.json', 'Reporting-Service.postman_collection.json', notify)}>⬇ Collection</button>
          <button className="btn dev-dl" onClick={() => download('/api/admin/postman/environment.json', 'Reporting-Service.postman_environment.json', notify)}>⬇ Environment</button>
        </div>
        <Steps items={POSTMAN_STEPS} className="compact" />
        <div className="dev-vars">
          {[['baseUrl', origin, 'filled in'], ['username', 'your sign-in name', 'fill in'], ['password', 'your password', 'secret'], ['apiKey', 'mrk_…', 'secret'], ['adminToken', 'only for /reports/… admin calls', 'secret']].map(([name, value, kind]) => (
            <span key={name} className={`dev-var ${kind === 'secret' ? 'secret' : ''}`}><code>{`{{${name}}}`}</code><small>{value}</small><em>{kind}</em></span>
          ))}
        </div>
        <p className="dev-tip warn">⚠ Running the whole collection creates and deletes test data (<code>PM_…</code>) and changes a limit for a moment: use a test server, never production.</p>
      </section>

      <section className="card dev-card dev-wide">
        <div className="card-head"><div><p className="eyebrow">Try it</p><h2>Call a report from any website or server</h2></div></div>
        <div className="dev-try">
          <div className="dev-try-form">
            <label className="field"><span><em>1</em> Report</span>
              <select value={reportId} disabled={!reports.length} onChange={(e) => setReportId(e.target.value)}>
                {!reports.length && <option value="">No published reports yet</option>}
                {reports.map((item) => <option key={item.id} value={item.id}>{item.id} — {item.title} ({item.folder})</option>)}
              </select>
              {!reports.length && <small className="muted">Publish a report from the Designer; the calls below use <code>&lt;REPORT_ID&gt;</code> until then.</small>}
            </label>
            <label className="field"><span><em>2</em> API key <small>only used on this page</small></span>
              <input value={key} placeholder="Paste mrk_… to test it" autoComplete="off" spellCheck={false} onChange={(e) => setKey(e.target.value)} />
            </label>
            {params.map((p) => (
              <label key={p.name} className="field"><span>{p.label || p.name}</span>
                <input value={filled[p.name] ?? ''} onChange={(e) => setValues({ ...values, [`${reportId}.${p.name}`]: e.target.value })} />
              </label>
            ))}
            <KeyCheck apiKey={key.trim()} notify={notify} />
            <p className="dev-note">The report ID is the first column of the report list (not the title). Parameters go in <code>parameters</code> by their names.</p>
          </div>
          <div className="dev-try-code">
            <div className="dev-langs-row">
              <span className="dev-step-label"><em>3</em> Copy a call</span>
              <div className="dev-langs" role="tablist" aria-label="Language">
                {LANGS.map(([value, label]) => (
                  <button key={value} type="button" role="tab" aria-selected={lang === value} className={lang === value ? 'on' : ''} onClick={() => setLang(value)}>{label}</button>
                ))}
              </div>
            </div>
            <div className="dev-snippets" key={lang}>
              {snippets[lang].map(([title, text]) => <Snippet key={title} notify={notify} title={title} text={text} />)}
            </div>
          </div>
        </div>
      </section>

      <section className="card dev-card dev-wide">
        <div className="card-head"><div><p className="eyebrow">Answers</p><h2>What each status means</h2></div></div>
        <div className="dev-answers">
          {ANSWERS.map(([code, tone, text], index) => (
            <span key={code} className={`dev-answer ${tone}`} style={{ '--i': index }}><b>{code}</b><small>{text}</small></span>
          ))}
        </div>
      </section>
    </div>
  );
}
