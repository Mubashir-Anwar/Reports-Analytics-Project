import { useCallback, useEffect, useState } from 'react';
import { api } from '../api.js';
import { Notice, formatWhen } from '../portal/Shell.jsx';

const splitList = (text) => text.split(/[,\n]/).map((item) => item.trim()).filter(Boolean);

/** API keys: one per client website or system that runs reports by URL. */
export default function ApiKeysTab({ notify }) {
  const [keys, setKeys] = useState([]);
  const [form, setForm] = useState({ name: '', folders: '', allowedIps: '', expiresInDays: '' });
  const [created, setCreated] = useState(null); // { name, secret } shown once
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => api.apiKeys().then((data) => setKeys(data.keys)).catch((error) => notify(error.message, 'error')), [notify]);
  useEffect(() => { load(); }, [load]);

  const create = async (event) => {
    event.preventDefault();
    setBusy(true);
    try {
      const result = await api.createApiKey({ name: form.name.trim(), folders: splitList(form.folders), allowedIps: splitList(form.allowedIps),
        expiresInDays: form.expiresInDays ? Number(form.expiresInDays) : null });
      setCreated({ name: result.key.name, secret: result.secret });
      setMessage(null);
      setForm({ name: '', folders: '', allowedIps: '', expiresInDays: '' });
      await load();
    } catch (error) { setMessage({ text: error.message, kind: 'error' }); }
    finally { setBusy(false); }
  };
  const update = async (key, change, text) => {
    try { await api.updateApiKey(key.id, change); await load(); notify(text); } catch (error) { notify(error.message, 'error'); }
  };
  const setWebhook = async (key) => {
    const url = window.prompt(`Webhook URL for "${key.name}" (called when its background reports finish; empty = none):`, key.webhookUrl || 'https://');
    if (url === null) return;
    try {
      const result = await api.updateApiKey(key.id, { webhookUrl: url.trim() === 'https://' ? '' : url.trim() });
      if (result.webhookSecret) setCreated({ name: `${key.name} (webhook secret)`, secret: result.webhookSecret });
      await load();
      notify(url.trim() && url.trim() !== 'https://' ? `Webhook of ${key.name} saved.` : `Webhook of ${key.name} removed.`);
    } catch (error) { notify(error.message, 'error'); }
  };
  const setEmbedding = async (key) => {
    const value = window.prompt(`Websites that may show embedded reports for "${key.name}" (e.g. https://portal.example.com), separated by commas. Empty = no embedding:`, (key.embedOrigins || []).join(', '));
    if (value === null) return;
    update(key, { embedOrigins: splitList(value) }, splitList(value).length ? `Embedding allowed for ${splitList(value).join(', ')}.` : `Embedding switched off for ${key.name}.`);
  };
  const setRate = async (key) => {
    const value = window.prompt(`Requests per minute for "${key.name}" (empty = the server default):`, key.ratePerMinute || '');
    if (value === null) return;
    update(key, { ratePerMinute: value.trim() ? Number(value) : null }, `Rate limit of ${key.name} saved.`);
  };
  const remove = async (key) => {
    if (!window.confirm(`Delete the key "${key.name}"? The website using it stops working, and its links stop opening.`)) return;
    try { await api.deleteApiKey(key.id); await load(); notify(`Key ${key.name} deleted.`); } catch (error) { notify(error.message, 'error'); }
  };
  const copy = (text) => navigator.clipboard?.writeText(text).then(() => notify('Copied.'), () => notify('Copy it by hand.', 'warn'));
  const origin = window.location.origin;

  return (
    <div className="grid-2">
      <section className="card">
        <div className="card-head">
          <div><p className="eyebrow">Run reports by URL</p><h2>API keys</h2>
            <p className="muted">One key per client website or system. Its server sends the key; a browser never sees it. Limit a key to folders, addresses and a lifetime; disable or delete it to cut access at once.</p></div>
          <button className="btn" onClick={load}>Refresh</button>
        </div>
        {created && (
          <div className="notice warn key-once" role="status">
            <b>Key for {created.name}: copy it now, it is shown only once.</b>
            <code className="key-value">{created.secret}</code>
            <div className="row"><button type="button" className="btn sm" onClick={() => copy(created.secret)}>Copy key</button><button type="button" className="btn sm quiet" onClick={() => setCreated(null)}>I saved it</button></div>
          </div>
        )}
        <div className="stack">
          {!keys.length && <div className="empty">No API keys yet.</div>}
          {keys.map((key) => (
            <div key={key.id} className="item">
              <div>
                <strong>{key.name}{key.disabled ? ' (disabled)' : ''}</strong>
                <small><code>{key.prefix}</code> · folders: {key.folders?.length ? key.folders.join(', ') : 'all'} · addresses: {key.allowedIps?.length ? key.allowedIps.join(', ') : 'any'}</small>
                <small>Expires: {key.expiresAt ? formatWhen(key.expiresAt) : 'never'} · last used: {formatWhen(key.lastUsedAt)} · by {key.createdBy || '?'}</small>
                <small>Webhook: {key.webhookUrl || 'none'} · {key.rateLimit ? `${key.rateLimit} requests/minute` : 'no rate limit'}</small>
                <small>Embedding on: {key.embedOrigins?.length ? key.embedOrigins.join(', ') : 'no website'}</small>
              </div>
              <div className="row">
                <button className="btn sm quiet" onClick={() => setEmbedding(key)}>Embedding</button>
                <button className="btn sm quiet" onClick={() => setWebhook(key)}>Webhook</button>
                <button className="btn sm quiet" onClick={() => setRate(key)}>Rate limit</button>
                <button className="btn sm" onClick={() => update(key, { disabled: !key.disabled }, `${key.name} ${key.disabled ? 'enabled' : 'disabled'}.`)}>{key.disabled ? 'Enable' : 'Disable'}</button>
                <button className="btn sm danger" onClick={() => remove(key)}>Delete</button>
              </div>
            </div>
          ))}
        </div>
        <details className="howto">
          <summary>How a website uses a key</summary>
          <p className="muted">1. From the website's <b>server</b> (the key stays there):</p>
          <pre>{`curl -H "X-API-Key: mrk_…" "${origin}/generate/KFS_FAL.pdf?Identifier=123&ProductCode=CAR" -o letter.pdf`}</pre>
          <p className="muted">2. For a link or button in a browser, the server first asks for a signed link (valid a few minutes; the parameters cannot be changed):</p>
          <pre>{`POST ${origin}/api/v1/links
X-API-Key: mrk_…
{"report": "KFS_FAL", "format": "pdf", "parameters": {"Identifier": "123"}, "expiresInSeconds": 300}
→ {"url": "/generate/KFS_FAL.pdf?Identifier=123&KeyId=…&Expires=…&Signature=…"}`}</pre>
          <p className="muted">3. Or the public API v1: <code>GET {origin}/api/v1/reports</code>, <code>POST {origin}/api/v1/reports/&lt;ID&gt;/run</code> with {'{"format": "pdf", "parameters": {…}}'}, or <code>/jobs</code> for big reports (webhook when done). Description: <a href="/api/v1/openapi.json" target="_blank" rel="noreferrer">/api/v1/openapi.json</a>.</p>
          <p className="muted">4. Show a report inside the website's page: list the website under <b>Embedding</b>, let its server ask <code>POST {origin}/api/v1/embed-tokens</code> {'{"report": "<ID>", "parameters": {"Identifier": "123"}}'} and put this in the page:</p>
          <pre>{`<script src="${origin}/assets/embed/sdk.js"></script>
<mrs-report token="emb_…"></mrs-report>`}</pre>
          <p className="muted">5. Signed-in users with the Run reports right can also open <code>{origin}/generate/&lt;ID&gt;.pdf?…</code> directly.</p>
        </details>
      </section>
      <form className="card stack" onSubmit={create}>
        <div><p className="eyebrow">Add</p><h2>New API key</h2></div>
        <label className="field"><span>Name (who uses it)</span><input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} maxLength={64} placeholder="e.g. Customer portal" required /></label>
        <label className="field"><span>Folders (empty = every folder)</span><input value={form.folders} onChange={(e) => setForm({ ...form, folders: e.target.value })} placeholder="e.g. cascade/CAR/Letters, Finance" /></label>
        <label className="field"><span>Allowed addresses (empty = any)</span><input value={form.allowedIps} onChange={(e) => setForm({ ...form, allowedIps: e.target.value })} placeholder="e.g. 203.0.113.10, 10.0.0.0/8" /></label>
        <label className="field"><span>Expires after days (empty = never)</span><input type="number" min="1" max="3650" value={form.expiresInDays} onChange={(e) => setForm({ ...form, expiresInDays: e.target.value })} placeholder="e.g. 365" /></label>
        <div className="row"><button className="btn primary" type="submit" disabled={busy}>{busy ? 'Creating…' : 'Create key'}</button></div>
        <Notice message={message} />
      </form>
    </div>
  );
}
