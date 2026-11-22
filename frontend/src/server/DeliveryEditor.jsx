// Schedule → "Send the file": after each run the report file also goes to an SFTP server, an API (HTTP POST)
// or a folder on the network. Passwords are sent once and never shown again (the server keeps them encrypted).
import PasswordInput from '../shared/PasswordInput.jsx';
import { useState } from 'react';
import { serverApi } from './serverApi.js';

const KINDS = {
  sftp: { icon: '🔐', title: 'SFTP server', hint: 'Upload to a bank / partner / your own SFTP server (port 22).' },
  http: { icon: '🔗', title: 'Another system’s API', hint: 'POST the file to a web address (ERP, document system, Power Automate, Zapier…).' },
  folder: { icon: '📂', title: 'Network folder', hint: 'Copy into a shared folder the server can reach (\\\\fileserver\\reports).' },
};
const EXAMPLES = {
  sftp: { label: 'Bank SFTP', host: 'sftp.bank.com', port: 22, username: 'reports', folder: '/incoming/daily', auth: 'password' },
  http: { label: 'ERP upload', url: 'https://erp.example.com/api/reports', method: 'POST', body: 'multipart', field: 'file', headers: [{ name: 'Authorization', value: '' }] },
  folder: { label: 'Finance share', folder: '' },
};
const newId = () => Math.random().toString(36).slice(2, 10);

/** What the file will be called, as the server makes it (same tokens). */
export function previewName(template, report, format) {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  const values = { reportId: report?.id || 'REPORT', title: report?.title || 'Report', date: `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`,
    time: `${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`, runId: 'run-…', format, label: '' };
  let name = String(template || '{reportId}_{date}.{format}').replace(/\{(\w+)\}/g, (m, k) => (k in values ? values[k] : m));
  name = name.replace(/[\\/:*?"<>|]+/g, '_').trim() || `report.${format}`;
  return name.toLowerCase().endsWith(`.${format}`) ? name : `${name}.${format}`;
}

function Target({ target, index, info, report, format, jobId, onChange, onRemove }) {
  const [test, setTest] = useState(null);
  const set = (patch) => { setTest(null); onChange({ ...target, ...patch }); };
  const kind = KINDS[target.type];
  const tokens = info?.delivery?.fileNameTokens || ['reportId', 'title', 'date', 'time', 'runId', 'format'];
  const runTest = async () => {
    setTest({ busy: true });
    try { setTest(await serverApi.testDelivery(target, jobId)); } catch (failure) { setTest({ ok: false, result: failure.message }); }
  };
  const headers = target.headers || [];
  return (
    <div className={`delivery-card ${target.enabled === false ? 'off' : ''}`}>
      <div className="delivery-head">
        <b>{kind.icon} {index + 1}. {target.label || kind.title}</b>
        <span className="row">
          <label className="check"><input type="checkbox" checked={target.enabled !== false} onChange={(e) => set({ enabled: e.target.checked })} /> On</label>
          <button type="button" className="btn quiet sm" onClick={onRemove} aria-label="Remove this delivery">✕</button>
        </span>
      </div>
      <p className="muted small">{kind.hint}</p>
      <div className="params">
        <label className="field"><span>Name (for you)</span><input value={target.label || ''} onChange={(e) => set({ label: e.target.value })} placeholder={EXAMPLES[target.type].label} /></label>
        {target.type === 'sftp' && (
          <>
            <label className="field"><span>Server</span><input value={target.host || ''} onChange={(e) => set({ host: e.target.value.trim() })} placeholder="sftp.bank.com" /></label>
            <label className="field"><span>Port</span><input type="number" min={1} max={65535} value={target.port || 22} onChange={(e) => set({ port: Number(e.target.value) || 22 })} /></label>
            <label className="field"><span>User name</span><input value={target.username || ''} onChange={(e) => set({ username: e.target.value })} placeholder="reports" autoComplete="off" /></label>
            <label className="field"><span>Folder on the server</span><input value={target.folder || ''} onChange={(e) => set({ folder: e.target.value })} placeholder="/incoming/daily" /></label>
            <label className="field"><span>Sign in with</span>
              <select value={target.auth || 'password'} onChange={(e) => set({ auth: e.target.value })}><option value="password">Password</option><option value="key">Private key (more secure)</option></select>
            </label>
            {(target.auth || 'password') === 'password'
              ? <label className="field"><span>Password {target.hasPassword && <small className="muted">(saved — leave empty to keep it)</small>}</span><PasswordInput value={target.password || ''} onChange={(e) => set({ password: e.target.value })} autoComplete="new-password" placeholder={target.hasPassword ? '••••••••' : ''} /></label>
              : (
                <>
                  <label className="field wide"><span>Private key {target.hasPrivateKey && <small className="muted">(saved — leave empty to keep it)</small>}</span>
                    <textarea rows={3} value={target.privateKey || ''} onChange={(e) => set({ privateKey: e.target.value })} placeholder="-----BEGIN OPENSSH PRIVATE KEY----- …" spellCheck={false} /></label>
                  <label className="field"><span>Key passphrase (if any)</span><PasswordInput value={target.passphrase || ''} onChange={(e) => set({ passphrase: e.target.value })} autoComplete="new-password" /></label>
                </>
              )}
            <label className="field wide"><span>Server key <small className="muted">(optional; remembered on the first upload)</small></span>
              <input value={target.hostKey || ''} onChange={(e) => set({ hostKey: e.target.value.trim(), hostKeyChanged: true })} placeholder="SHA256:… (from your SFTP provider)" /></label>
          </>
        )}
        {target.type === 'http' && (
          <>
            <label className="field wide"><span>Address (URL)</span><input value={target.url || ''} onChange={(e) => set({ url: e.target.value.trim() })} placeholder="https://erp.example.com/api/reports" /></label>
            <label className="field"><span>Method</span><select value={target.method || 'POST'} onChange={(e) => set({ method: e.target.value })}><option>POST</option><option>PUT</option></select></label>
            <label className="field"><span>Send as</span>
              <select value={target.body || 'multipart'} onChange={(e) => set({ body: e.target.value })}>
                <option value="multipart">Form upload (file + run details)</option>
                <option value="raw">Only the file (raw body)</option>
              </select>
            </label>
            {(target.body || 'multipart') === 'multipart' && <label className="field"><span>File field name</span><input value={target.field || 'file'} onChange={(e) => set({ field: e.target.value })} /></label>}
            <div className="field wide"><span>Headers <small className="muted">(e.g. Authorization: Bearer … — values are kept secret)</small></span>
              {headers.map((h, i) => (
                <span className="row header-row" key={i}>
                  <input value={h.name} onChange={(e) => set({ headers: headers.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)) })} placeholder="Authorization" aria-label="Header name" />
                  <PasswordInput value={h.value || ''} onChange={(e) => set({ headers: headers.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)) })} placeholder={h.saved ? '•••••• (saved)' : 'Bearer abc123'} aria-label="Header value" autoComplete="new-password" />
                  <button type="button" className="btn quiet sm" onClick={() => set({ headers: headers.filter((_, j) => j !== i) })} aria-label="Remove header">✕</button>
                </span>
              ))}
              {headers.length < 10 && <button type="button" className="btn sm" onClick={() => set({ headers: [...headers, { name: '', value: '' }] })}>+ Header</button>}
            </div>
          </>
        )}
        {target.type === 'folder' && (
          <label className="field wide"><span>Folder</span>
            <input value={target.folder || ''} onChange={(e) => set({ folder: e.target.value })} placeholder={info?.delivery?.folders?.[0] ? `${info.delivery.folders[0]}\\daily` : '\\\\fileserver\\reports\\daily'} list="delivery-roots" />
            {info?.delivery?.folders?.length > 0 && <small className="muted">Allowed: {info.delivery.folders.join(' · ')}</small>}
          </label>
        )}
        <label className="field wide"><span>File name</span>
          <input value={target.fileName || ''} onChange={(e) => set({ fileName: e.target.value })} placeholder={info?.delivery?.defaultFileName || '{reportId}_{date}.{format}'} />
          <small className="muted">Becomes <code>{previewName(target.fileName, report, format)}</code> · you can use {tokens.map((t) => <code key={t}>{`{${t}}`}</code>)}</small>
        </label>
        <label className="check wide"><input type="checkbox" checked={target.onlyOnSuccess !== false} onChange={(e) => set({ onlyOnSuccess: e.target.checked })} /> Only when the report ran without errors</label>
      </div>
      <div className="row delivery-test">
        <button type="button" className="btn sm" disabled={test?.busy} onClick={runTest}>{test?.busy ? 'Testing…' : target.type === 'sftp' ? 'Test the login' : 'Check'}</button>
        {test && !test.busy && <span className={test.ok ? 'ok small' : 'err small'}>{test.ok ? '✓ ' : '✕ '}{test.result}{test.hostKey && !target.hostKey ? ` · server key ${test.hostKey}` : ''}</span>}
      </div>
    </div>
  );
}

/** The list of targets of one schedule. value = form.deliver; onChange(next). */
export default function DeliveryEditor({ value = [], onChange, info, report, format, jobId }) {
  const types = info?.delivery?.types || ['sftp', 'http'];
  const add = (type) => onChange([...value, { id: newId(), type, enabled: true, onlyOnSuccess: true, fileName: '', ...EXAMPLES[type], label: '', ...(type === 'sftp' ? { host: '', username: '', folder: '/' } : type === 'http' ? { url: '' } : {}) }]);
  return (
    <div className="stack">
      <p className="muted">After every run the file is kept under <b>Runs</b> (and e-mailed if you chose that). Here it can also go to other systems, automatically.</p>
      {value.map((target, index) => (
        <Target key={target.id} target={target} index={index} info={info} report={report} format={format} jobId={jobId}
          onChange={(next) => onChange(value.map((t) => (t.id === target.id ? next : t)))} onRemove={() => onChange(value.filter((t) => t.id !== target.id))} />
      ))}
      {value.length < 5 && (
        <div className="delivery-add">
          {types.map((type) => (
            <button key={type} type="button" className="delivery-kind" onClick={() => add(type)}>
              <span>{KINDS[type].icon}</span><b>+ {KINDS[type].title}</b><small>{KINDS[type].hint}</small>
            </button>
          ))}
        </div>
      )}
      {!types.includes('folder') && <p className="muted small">Network folders: an administrator turns them on with REPORTING_DELIVERY_FOLDERS on the server.</p>}
      <p className="muted small">Each run shows what was sent and where under <b>Runs</b>. A failed upload never stops the report or the other deliveries.</p>
    </div>
  );
}
