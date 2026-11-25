// Analytics → Embed (#embed or #embed/<dashboard>): how to show a dashboard inside another website, with the code to
// copy for this server and this dashboard, and how often it refreshes by itself.
import { useState } from 'react';
import { useMe } from '../shared/session.js';

export const REFRESH = [[0, 'Never'], [10, 'Every 10 seconds'], [30, 'Every 30 seconds'], [60, 'Every minute'], [300, 'Every 5 minutes'], [900, 'Every 15 minutes'], [1800, 'Every 30 minutes'], [3600, 'Every hour']];
const SERVERS = ['curl', 'Node.js', 'Python', 'PHP', 'Java'];

function tokenCode(language, origin, id) {
  const body = `{"dashboard": "${id}", "expiresInSeconds": 3600}`;
  if (language === 'curl') return `curl -X POST ${origin}/api/v1/embed-tokens \\\n  -H "X-API-Key: YOUR_API_KEY" -H "Content-Type: application/json" \\\n  -d '${body}'`;
  if (language === 'Node.js') return `// Express: the page asks this address of your own website\napp.get('/dashboard-token', async (req, res) => {\n  // check here that YOUR user is signed in\n  const answer = await fetch('${origin}/api/v1/embed-tokens', {\n    method: 'POST',\n    headers: { 'X-API-Key': process.env.REPORTING_KEY, 'Content-Type': 'application/json' },\n    body: JSON.stringify({ dashboard: '${id}', expiresInSeconds: 3600 }),\n  });\n  res.status(answer.status).json(await answer.json());\n});`;
  if (language === 'Python') return `# Flask: the page asks this address of your own website\n@app.get("/dashboard-token")\ndef dashboard_token():\n    # check here that YOUR user is signed in\n    answer = requests.post("${origin}/api/v1/embed-tokens", timeout=10,\n        headers={"X-API-Key": os.environ["REPORTING_KEY"]},\n        json={"dashboard": "${id}", "expiresInSeconds": 3600})\n    return answer.json(), answer.status_code`;
  if (language === 'PHP') return `<?php // dashboard-token.php: check here that YOUR user is signed in\n$call = curl_init('${origin}/api/v1/embed-tokens');\ncurl_setopt_array($call, [CURLOPT_POST => true, CURLOPT_RETURNTRANSFER => true,\n  CURLOPT_HTTPHEADER => ['X-API-Key: ' . getenv('REPORTING_KEY'), 'Content-Type: application/json'],\n  CURLOPT_POSTFIELDS => '${body}']);\nheader('Content-Type: application/json');\necho curl_exec($call);`;
  return `// Java 11+\nHttpRequest ask = HttpRequest.newBuilder(URI.create("${origin}/api/v1/embed-tokens"))\n    .header("X-API-Key", System.getenv("REPORTING_KEY")).header("Content-Type", "application/json")\n    .POST(HttpRequest.BodyPublishers.ofString("{\\"dashboard\\":\\"${id}\\",\\"expiresInSeconds\\":3600}")).build();\nString json = HttpClient.newHttpClient().send(ask, HttpResponse.BodyHandlers.ofString()).body();`;
}

function Code({ text, notify }) {
  return <div className="eg-code"><pre>{text}</pre><button type="button" className="btn sm" onClick={() => { navigator.clipboard?.writeText(text); notify('Copied'); }}>Copy</button></div>;
}

export default function EmbedGuide({ dashboards, dashboardId, notify, go }) {
  const me = useMe();
  const [refresh, setRefresh] = useState(60);
  const [height, setHeight] = useState(640);
  const [theme, setTheme] = useState('light');
  const [language, setLanguage] = useState('Node.js');
  const [kind, setKind] = useState('tag');
  const origin = window.location.origin;
  const board = dashboards.find((d) => d.id === dashboardId) || dashboards[0];
  const admin = me?.rights?.includes('admin');
  if (!board) return <section className="card"><h1>Embed a dashboard</h1><p className="muted">Make a dashboard first; then you can show it inside another website.</p></section>;

  const tag = `<script src="${origin}/assets/embed/sdk.js"></script>\n\n<mrs-dashboard token-url="/dashboard-token"\n               refresh="${refresh}" theme="${theme}" height="${height}"></mrs-dashboard>`;
  const frame = `<!-- viewUrl is in the answer of step 2 (the token lives at most an hour) -->\n<iframe src="${origin}/embed/view?token=TOKEN#refresh=${refresh}&theme=${theme}"\n        style="width:100%;height:${height}px;border:0" title="${board.name.replace(/"/g, '')}"></iframe>`;

  return (
    <div className="stack eg">
      <div className="page-head"><div><p className="eyebrow">Analytics</p><h1>Embed in a website</h1>
        <p className="muted">Show a dashboard inside your own website. Visitors need no account here: your website's server asks for a short-lived token and the page shows the dashboard with it.</p></div></div>
      <section className="card">
        <div className="params">
          <label className="field"><span>Dashboard</span><select value={board.id} onChange={(e) => go(`embed/${e.target.value}`)}>{dashboards.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label>
          <label className="field"><span>Refresh by itself</span><select value={refresh} onChange={(e) => setRefresh(Number(e.target.value))}>{REFRESH.map(([s, text]) => <option key={s} value={s}>{text}</option>)}</select></label>
          <label className="field"><span>Height (px)</span><input type="number" min="200" max="3000" step="20" value={height} onChange={(e) => setHeight(Number(e.target.value) || 640)} /></label>
          <label className="field"><span>Look</span><select value={theme} onChange={(e) => setTheme(e.target.value)}><option value="light">Light</option><option value="dark">Dark</option><option value="auto">As the visitor's computer</option></select></label>
        </div>
        <p className="muted small">Refreshing asks the dataset again. How often the dataset itself brings new records from the database is its own setting: dataset → “Refresh”.</p>
      </section>

      <ol className="eg-steps">
        <li><b>Allow the website</b>
          <p>{admin ? <>In <a href="/admin#keys">Admin → API keys</a> make a key for the website, limited to the folder <code>{board.folder || 'Analytics'}</code>. Under <b>Embedding</b> list the website, for example <code>https://portal.example.com</code>. Any other website gets an empty frame.</>
            : <>Ask an administrator for an API key limited to the folder <code>{board.folder || 'Analytics'}</code>, with your website (for example <code>https://portal.example.com</code>) under its “Embedding”.</>}</p></li>
        <li><b>Your server asks for a token</b>
          <p>Keep the API key on your server, never in a page. A token shows only this dashboard and lives at most an hour.</p>
          <div className="segmented wrap" role="tablist">{SERVERS.map((s) => <button key={s} type="button" role="tab" aria-selected={language === s} className={language === s ? 'on' : ''} onClick={() => setLanguage(s)}>{s}</button>)}</div>
          <Code text={tokenCode(language, origin, board.id)} notify={notify} /></li>
        <li><b>Put it in the page</b>
          <div className="segmented" role="tablist"><button type="button" role="tab" aria-selected={kind === 'tag'} className={kind === 'tag' ? 'on' : ''} onClick={() => setKind('tag')}>Tag (recommended)</button><button type="button" role="tab" aria-selected={kind === 'frame'} className={kind === 'frame' ? 'on' : ''} onClick={() => setKind('frame')}>iframe</button></div>
          <Code text={kind === 'tag' ? tag : frame} notify={notify} />
          <p>{kind === 'tag' ? <><code>token-url</code> is the address of step 2 on your own website; the tag asks it again before a token ends, so the dashboard stays open all day. <code>refresh="0"</code> = never.</> : 'With an iframe your page must load a new token itself within the hour.'}</p></li>
        <li><b>Each visitor only their own rows (optional)</b>
          <p>Add fixed filters to the request of step 2. They apply to every visual and a visitor cannot remove them.</p>
          <Code text={`{"dashboard": "${board.id}",\n "filters": [{"column": "Branch", "op": "in", "values": ["LHR"]}],\n "onBehalfOf": "ali"}`} notify={notify} /></li>
      </ol>
      <p className="muted small">On your own network only? People with an account can also open <code>{origin}/analytics#dash/{board.id}</code>, or the Server when they have only the Server right.</p>
    </div>
  );
}
