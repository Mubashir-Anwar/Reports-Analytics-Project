// "Advanced Connection Options" of the Data Adapter Wizard: tabs that build the connection URL, plus the options that
// cannot be in a URL (certificate files, an SSH tunnel), which go to the server as `options` (connectors/options.py).
import { useState } from 'react';
import PasswordInput from './PasswordInput.jsx';

/* ---------------------------------------------------------------- MongoDB URL <-> fields */
/** mongodb[+srv]://user:password@host1:port,host2/db?options → its parts (never throws; partial text is fine). */
export function parseMongoUrl(text) {
  const value = String(text || '').trim();
  const match = /^(mongodb(?:\+srv)?):\/\/(.*)$/.exec(value);
  const model = { scheme: 'mongodb', user: '', password: '', hosts: [], database: '', params: [] };
  if (!match) return model;
  model.scheme = match[1];
  let rest = match[2];
  const cut = rest.search(/[/?]/);
  const netloc = cut < 0 ? rest : rest.slice(0, cut);
  rest = cut < 0 ? '' : rest.slice(cut);
  const at = netloc.lastIndexOf('@');
  if (at >= 0) {
    const [user, ...pass] = netloc.slice(0, at).split(':');
    const decode = (s) => { try { return decodeURIComponent(s); } catch { return s; } };
    model.user = decode(user);
    model.password = decode(pass.join(':'));
  }
  model.hosts = netloc.slice(at + 1).split(',').filter(Boolean);
  const [path, query = ''] = rest.split('?');
  model.database = path.replace(/^\//, '');
  // options stay as written: the drivers read some of them (readPreferenceTags) without decoding
  model.params = query.split('&').filter(Boolean).map((piece) => { const i = piece.indexOf('='); return i < 0 ? [piece, ''] : [piece.slice(0, i), piece.slice(i + 1)]; });
  return model;
}

export function buildMongoUrl(m) {
  const auth = m.user ? `${encodeURIComponent(m.user)}${m.password ? `:${encodeURIComponent(m.password)}` : ''}@` : '';
  const query = m.params.filter(([k]) => k).map(([k, v]) => `${k}=${v}`).join('&');
  return `${m.scheme}://${auth}${m.hosts.join(',') || 'localhost:27017'}/${m.database}${query ? `?${query}` : ''}`;
}

const getParam = (m, key) => (m.params.find(([k]) => k.toLowerCase() === key.toLowerCase()) || [])[1] ?? '';
const setParams = (m, patch) => {
  let params = m.params;
  for (const [key, value] of Object.entries(patch)) {
    const at = params.findIndex(([k]) => k.toLowerCase() === key.toLowerCase());
    const text = value === '' || value == null ? null : encodeURIComponent(String(value)).replace(/%24/g, '$').replace(/%3A/gi, ':');
    if (text === null) params = params.filter((_, i) => i !== at);
    else if (at >= 0) params = params.map((p, i) => (i === at ? [p[0], text] : p));
    else params = [...params, [key, text]];
  }
  return { ...m, params };
};
const decoded = (value) => { try { return decodeURIComponent(value); } catch { return value; } };

/* ---------------------------------------------------------------- shared pieces: files and SSH */
const readBase64 = (file) => new Promise((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
  reader.onerror = () => reject(new Error(`${file.name} could not be read`));
  reader.readAsDataURL(file);
});

/** One certificate file: the saved one (by name), a new one, or none. value: undefined = keep saved, null = remove, {name,data}. */
function FileField({ label, hint, value, saved, onChange }) {
  const [error, setError] = useState('');
  const shown = value === undefined ? saved : value?.name;
  const pick = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    if (file.size > 64 * 1024) { setError(`${file.name} is larger than 64 KB`); return; }
    const data = await readBase64(file);
    if (!atob(data).includes('-----BEGIN')) { setError(`${file.name} is not a PEM text file (-----BEGIN …)`); return; }
    setError(''); onChange({ name: file.name, data });
  };
  return (
    <div className="aw-field co-file">
      <span>{label}</span>
      <div className="co-file-row">
        <span className={shown ? '' : 'muted'}>{shown ? `📄 ${shown}${value === undefined ? ' (saved)' : ''}` : 'No file'}</span>
        <label className="btn small">Choose…<input type="file" accept=".pem,.crt,.cer,.key,.txt" hidden onChange={pick} /></label>
        {shown && <button type="button" className="btn small" onClick={() => onChange(null)}>Remove</button>}
      </div>
      {hint && <small>{hint}</small>}
      {error && <small className="err">{error}</small>}
    </div>
  );
}

/** opts.ssh: undefined = keep the saved tunnel, null = none, object = this tunnel. saved: the summary the list gives. */
function SshTab({ opts, saved, onChange, allowSocks = false }) {
  const ssh = opts.ssh === undefined ? (saved ? { ...saved, password: '', privateKey: '', passphrase: '' } : null) : opts.ssh;
  const mode = !ssh ? 'none' : ssh.auth === 'key' ? 'key' : 'password';
  const set = (patch) => onChange({ ...opts, ssh: { host: '', port: 22, user: '', auth: 'password', ...ssh, ...patch } });
  const sameAsSaved = saved && ssh && saved.host === ssh.host && String(saved.port || 22) === String(ssh.port || 22) && saved.user === ssh.user;
  const keyFile = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (file) set({ privateKey: await file.text(), keyName: file.name });
  };
  return (
    <>
      <div className="co-choice" role="radiogroup" aria-label="SSH tunnel">
        {[['none', 'None'], ['password', 'SSH with password'], ['key', 'SSH with identity file']].map(([key, text]) => (
          <button key={key} type="button" role="radio" aria-checked={mode === key} className={mode === key ? 'on' : ''}
            onClick={() => (key === 'none' ? onChange({ ...opts, ssh: null }) : set({ auth: key }))}>{text}</button>))}
        {allowSocks && <button type="button" disabled title="The MongoDB driver of this server has no SOCKS5 proxy: use an SSH tunnel">Socks5 (not available)</button>}
      </div>
      {ssh && (
        <>
          <div className="pg-grid">
            <label className="aw-field">SSH hostname<input value={ssh.host} onChange={(e) => set({ host: e.target.value.trim() })} placeholder="bastion.example.com" /></label>
            <label className="aw-field">SSH port<input value={ssh.port} inputMode="numeric" onChange={(e) => set({ port: e.target.value.replace(/[^0-9]/g, '') })} /></label>
          </div>
          <label className="aw-field">SSH username<input value={ssh.user} autoComplete="off" onChange={(e) => set({ user: e.target.value.trim() })} /></label>
          {mode === 'password' ? (
            <label className="aw-field">SSH password<PasswordInput autoComplete="new-password" value={ssh.password || ''} onChange={(e) => set({ password: e.target.value })}
              placeholder={sameAsSaved && saved.auth === 'password' ? 'saved: leave empty to keep it' : ''} /></label>
          ) : (
            <>
              <div className="aw-field co-file">
                <span>SSH identity file (private key)</span>
                <div className="co-file-row">
                  <span className={ssh.privateKey || (sameAsSaved && saved.auth === 'key') ? '' : 'muted'}>
                    {ssh.privateKey ? `🔑 ${ssh.keyName || 'key'}` : sameAsSaved && saved.auth === 'key' ? '🔑 saved key' : 'No file'}</span>
                  <label className="btn small">Choose…<input type="file" hidden onChange={keyFile} /></label>
                </div>
                <small>OpenSSH or PEM: RSA, ECDSA or Ed25519 (id_ed25519, id_rsa).</small>
              </div>
              <label className="aw-field">Passphrase <small>(if the key has one)</small><PasswordInput autoComplete="new-password" value={ssh.passphrase || ''} onChange={(e) => set({ passphrase: e.target.value })} /></label>
            </>
          )}
          <p className="muted co-note">{ssh.hostKey ? <>Trusted host key: <code>{ssh.hostKey}</code></> : 'The first Test shows the SSH server’s host key: check it with the server’s administrator and trust it.'}
            {' '}The database host and port are reached from the SSH server.</p>
        </>
      )}
    </>
  );
}

function Tabs({ tabs, tab, setTab }) {
  return (
    <div className="co-tabs" role="tablist">
      {tabs.map(([key, text]) => <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? 'on' : ''} onClick={() => setTab(key)}>{text}</button>)}
    </div>
  );
}

function KeyValues({ rows, onChange, hint }) {
  return (
    <div className="aw-field">
      <span>URI options</span>
      {rows.map(([key, value], index) => (
        <div key={index} className="co-kv">
          <input value={key} placeholder="key" aria-label="Option name" onChange={(e) => onChange(rows.map((r, i) => (i === index ? [e.target.value.trim(), r[1]] : r)))} />
          <input value={decoded(value)} placeholder="value" aria-label="Option value" onChange={(e) => onChange(rows.map((r, i) => (i === index ? [r[0], encodeURIComponent(e.target.value)] : r)))} />
          <button type="button" className="btn small" aria-label="Remove option" onClick={() => onChange(rows.filter((_, i) => i !== index))}>−</button>
        </div>
      ))}
      <button type="button" className="btn small co-add" onClick={() => onChange([...rows, ['', '']])}>+ Add option</button>
      {hint && <small>{hint}</small>}
    </div>
  );
}

/* ---------------------------------------------------------------- MongoDB */
const MONGO_HANDLED = ['directconnection', 'authsource', 'authmechanism', 'tls', 'ssl', 'tlsallowinvalidhostnames', 'tlsallowinvalidcertificates',
  'tlsinsecure', 'tlscertificatekeyfilepassword', 'readpreference', 'replicaset', 'appname', 'connecttimeoutms', 'serverselectiontimeoutms', 'maxpoolsize'];

/** The tabs of a MongoDB adapter. uri/onUri: the URL text (both ways); opts/onOpts: files and SSH; saved: the saved summary. */
export function MongoOptions({ uri, onUri, opts, onOpts, saved = {} }) {
  const [tab, setTab] = useState('general');
  const [authPick, setAuthPick] = useState(''); // Username/Password chosen before a user name is typed
  const m = parseMongoUrl(uri);
  const typed = /^mongodb(\+srv)?:\/\//.test(uri.trim());
  const update = (next) => onUri(buildMongoUrl(next));
  const param = (key) => decoded(getParam(m, key));
  const put = (patch) => update(setParams(m, patch));
  const mechanism = param('authMechanism').toUpperCase();
  const method = mechanism === 'MONGODB-X509' ? 'x509' : mechanism === 'PLAIN' ? 'ldap' : mechanism === 'GSSAPI' ? 'kerberos'
    : mechanism === 'MONGODB-AWS' ? 'aws' : m.user || authPick === 'password' ? 'password' : 'none';
  const setMethod = (next) => {
    setAuthPick(next);
    const base = { ...m, user: next === 'none' || next === 'x509' ? '' : m.user, password: next === 'none' || next === 'x509' ? '' : m.password };
    const patch = { none: { authMechanism: '', authSource: '' }, password: { authMechanism: '', authSource: param('authSource') === '$external' ? '' : param('authSource') },
      x509: { authMechanism: 'MONGODB-X509', authSource: '$external', tls: 'true' }, ldap: { authMechanism: 'PLAIN', authSource: '$external' } }[next];
    update(setParams(base, patch));
  };
  const tlsValue = (param('tls') || param('ssl')).toLowerCase();
  const tlsMode = tlsValue === 'true' ? 'on' : tlsValue === 'false' ? 'off' : 'default';
  const files = opts.files || {};
  const setFile = (role, value) => onOpts({ ...opts, files: { ...files, [role]: value } });
  const others = m.params.filter(([k]) => !MONGO_HANDLED.includes(k.toLowerCase()));
  const setOthers = (rows) => update({ ...m, params: [...m.params.filter(([k]) => MONGO_HANDLED.includes(k.toLowerCase())), ...rows] });

  return (
    <details className="co-box" open={Boolean(saved.ssh || Object.keys(saved.files || {}).length) || undefined}>
      <summary>Advanced Connection Options</summary>
      <Tabs tab={tab} setTab={setTab} tabs={[['general', 'General'], ['auth', 'Authentication'], ['tls', 'TLS/SSL'], ['ssh', 'Proxy/SSH'], ['fle', 'In-Use Encryption'], ['advanced', 'Advanced']]} />
      <div className="co-panel" role="tabpanel">
        {!typed && tab !== 'ssh' && tab !== 'fle' && <p className="muted co-note">Fill these in to build the URL, or type the URL above: both stay the same.</p>}
        {tab === 'general' && (
          <>
            <div className="co-choice" role="radiogroup" aria-label="Connection string scheme">
              {[['mongodb', 'mongodb'], ['mongodb+srv', 'mongodb+srv']].map(([key, text]) => (
                <button key={key} type="button" role="radio" aria-checked={m.scheme === key} className={m.scheme === key ? 'on' : ''}
                  onClick={() => update({ ...m, scheme: key, hosts: key === 'mongodb+srv' ? m.hosts.slice(0, 1).map((h) => h.split(':')[0]) : m.hosts })}>{text}</button>))}
            </div>
            <small className="muted">{m.scheme === 'mongodb+srv' ? 'One host name: the servers and options are looked up in DNS (cloud clusters).' : 'One or more servers (host:port), for example the members of a replica set.'}</small>
            <div className="aw-field">
              <span>Host{m.scheme === 'mongodb' ? 's' : ''}</span>
              {(m.hosts.length ? m.hosts : ['']).map((host, index) => (
                <div key={index} className="co-kv">
                  <input value={host} placeholder={m.scheme === 'mongodb' ? 'localhost:27017' : 'cluster0.example.net'} aria-label={`Host ${index + 1}`}
                    onChange={(e) => update({ ...m, hosts: (m.hosts.length ? m.hosts : ['']).map((h, i) => (i === index ? e.target.value.trim() : h)) })} />
                  {m.scheme === 'mongodb' && m.hosts.length > 1 && <button type="button" className="btn small" aria-label="Remove host" onClick={() => update({ ...m, hosts: m.hosts.filter((_, i) => i !== index) })}>−</button>}
                </div>
              ))}
              {m.scheme === 'mongodb' && <button type="button" className="btn small co-add" onClick={() => update({ ...m, hosts: [...(m.hosts.length ? m.hosts : ['localhost:27017']), ''] })}>+ Add host</button>}
            </div>
            {m.scheme === 'mongodb' && (
              <label className="co-check"><input type="checkbox" checked={param('directConnection') === 'true'} onChange={(e) => put({ directConnection: e.target.checked ? 'true' : '' })} />
                Direct Connection <small className="muted">(only this server, not the whole replica set)</small></label>
            )}
          </>
        )}
        {tab === 'auth' && (
          <>
            <div className="co-choice" role="radiogroup" aria-label="Authentication method">
              {[['none', 'None'], ['password', 'Username/Password'], ['x509', 'X.509'], ['ldap', 'LDAP']].map(([key, text]) => (
                <button key={key} type="button" role="radio" aria-checked={method === key} className={method === key ? 'on' : ''} onClick={() => setMethod(key)}>{text}</button>))}
              <button type="button" disabled title="Needs the Kerberos package on the server (ask the administrator)">Kerberos</button>
              <button type="button" disabled title="Needs the AWS sign-in package on the server (ask the administrator)">AWS IAM</button>
            </div>
            {(method === 'password' || method === 'ldap') && (
              <>
                <label className="aw-field">Username<input value={m.user} autoComplete="off" onChange={(e) => update({ ...m, user: e.target.value })} /></label>
                <label className="aw-field">Password<PasswordInput autoComplete="new-password" value={m.password} onChange={(e) => update({ ...m, password: e.target.value })} /></label>
              </>
            )}
            {method === 'password' && (
              <div className="pg-grid">
                <label className="aw-field">Authentication database<input value={param('authSource')} placeholder="admin" onChange={(e) => put({ authSource: e.target.value.trim() })} /></label>
                <label className="aw-field">Mechanism
                  <select value={mechanism} onChange={(e) => put({ authMechanism: e.target.value })}>
                    <option value="">Default</option><option value="SCRAM-SHA-1">SCRAM-SHA-1</option><option value="SCRAM-SHA-256">SCRAM-SHA-256</option>
                  </select>
                </label>
              </div>
            )}
            {method === 'x509' && <p className="muted co-note">Signs in with the client certificate: add it in the TLS/SSL tab (certificate and key).</p>}
            {method === 'ldap' && <p className="muted co-note">LDAP sign-in (PLAIN, $external) is sent in clear text inside TLS: keep TLS on.</p>}
            {(method === 'kerberos' || method === 'aws') && <p className="aw-result err">This URL uses {method === 'kerberos' ? 'Kerberos' : 'AWS IAM'}: the package it needs is not installed on this server.</p>}
          </>
        )}
        {tab === 'tls' && (
          <>
            <div className="co-choice" role="radiogroup" aria-label="TLS/SSL connection">
              {[['default', 'Default'], ['on', 'On'], ['off', 'Off']].map(([key, text]) => (
                <button key={key} type="button" role="radio" aria-checked={tlsMode === key} className={tlsMode === key ? 'on' : ''}
                  onClick={() => put({ tls: key === 'default' ? '' : key === 'on' ? 'true' : 'false', ssl: '' })}>{text}</button>))}
            </div>
            <small className="muted">Default: on for mongodb+srv, off for mongodb://.</small>
            <FileField label="Certificate Authority (.pem)" hint="The CA that signed the server's certificate (when it is not a public one)." value={files.ca} saved={saved.files?.ca} onChange={(v) => setFile('ca', v)} />
            <FileField label="Client Certificate (.pem)" hint="Needed for X.509 sign-in or a server that asks for client certificates." value={files.cert} saved={saved.files?.cert} onChange={(v) => setFile('cert', v)} />
            <FileField label="Client Private Key (.pem)" hint="Leave empty when the key is inside the certificate file." value={files.key} saved={saved.files?.key} onChange={(v) => setFile('key', v)} />
            <label className="aw-field">Client Key Password <small>(if the key is encrypted)</small>
              <PasswordInput autoComplete="new-password" value={param('tlsCertificateKeyFilePassword')} onChange={(e) => put({ tlsCertificateKeyFilePassword: e.target.value })} /></label>
            <label className="co-check"><input type="checkbox" checked={param('tlsAllowInvalidHostnames') === 'true'} onChange={(e) => put({ tlsAllowInvalidHostnames: e.target.checked ? 'true' : '' })} />
              tlsAllowInvalidHostnames <small className="muted">(the certificate may name another host)</small></label>
            <label className="co-check"><input type="checkbox" checked={param('tlsAllowInvalidCertificates') === 'true'} onChange={(e) => put({ tlsAllowInvalidCertificates: e.target.checked ? 'true' : '' })} />
              tlsAllowInvalidCertificates <small className="err">(not safe: for tests only)</small></label>
          </>
        )}
        {tab === 'ssh' && <SshTab opts={opts} saved={saved.ssh} onChange={onOpts} allowSocks />}
        {tab === 'ssh' && opts.ssh && m.scheme === 'mongodb+srv' && <p className="aw-result err">An SSH tunnel needs a mongodb:// URL with one server: change the scheme in General.</p>}
        {tab === 'ssh' && opts.ssh && m.hosts.length > 1 && <p className="muted co-note">Through the tunnel only the first host is used (Direct Connection).</p>}
        {tab === 'fle' && (
          <p className="muted co-note">In-Use Encryption (fields encrypted on the client, keys in a key service) needs the
            encryption package on the server, which is not installed. Ask the administrator to add it; this tab will then have its settings.</p>
        )}
        {tab === 'advanced' && (
          <>
            <div className="pg-grid">
              <label className="aw-field">Read Preference
                <select value={param('readPreference')} onChange={(e) => put({ readPreference: e.target.value })}>
                  <option value="">Default (primary)</option><option value="primary">primary</option><option value="primaryPreferred">primaryPreferred</option>
                  <option value="secondary">secondary</option><option value="secondaryPreferred">secondaryPreferred</option><option value="nearest">nearest</option>
                </select>
              </label>
              <label className="aw-field">Replica Set Name<input value={param('replicaSet')} onChange={(e) => put({ replicaSet: e.target.value.trim() })} /></label>
              <label className="aw-field">Default Database<input value={m.database} onChange={(e) => update({ ...m, database: e.target.value.trim() })} /></label>
              <label className="aw-field">App Name<input value={param('appName')} placeholder="reports" onChange={(e) => put({ appName: e.target.value })} /></label>
              <label className="aw-field">Connect timeout (ms)<input value={param('connectTimeoutMS')} inputMode="numeric" onChange={(e) => put({ connectTimeoutMS: e.target.value.replace(/[^0-9]/g, '') })} /></label>
              <label className="aw-field">Server selection timeout (ms)<input value={param('serverSelectionTimeoutMS')} inputMode="numeric" onChange={(e) => put({ serverSelectionTimeoutMS: e.target.value.replace(/[^0-9]/g, '') })} /></label>
              <label className="aw-field">Max pool size<input value={param('maxPoolSize')} inputMode="numeric" onChange={(e) => put({ maxPoolSize: e.target.value.replace(/[^0-9]/g, '') })} /></label>
            </div>
            <KeyValues rows={others} onChange={setOthers} hint="Any other option of the MongoDB URL, for example w=majority or compressors=zstd." />
          </>
        )}
      </div>
    </details>
  );
}

/* ---------------------------------------------------------------- SQL and other databases */
const FILE_KINDS = { postgresql: true, mysql: true, mariadb: true, redis: true };
export const SSH_KINDS = ['postgresql', 'mysql', 'mariadb', 'sqlserver', 'oracle', 'clickhouse', 'redis', 'elasticsearch'];

/** TLS files, SSH tunnel and extra URL options of a SQL (or other) adapter. params/onParams: extra URL options (optional). */
export function SqlOptions({ type, opts, onOpts, saved = {}, params, onParams }) {
  const [tab, setTab] = useState(FILE_KINDS[type] ? 'tls' : 'ssh');
  if (!SSH_KINDS.includes(type)) return null;
  const files = opts.files || {};
  const setFile = (role, value) => onOpts({ ...opts, files: { ...files, [role]: value } });
  const tabs = [...(FILE_KINDS[type] ? [['tls', 'TLS/SSL']] : []), ['ssh', 'Proxy/SSH'], ...(onParams ? [['advanced', 'Advanced']] : [])];
  return (
    <details className="co-box" open={Boolean(saved.ssh || Object.keys(saved.files || {}).length) || undefined}>
      <summary>Advanced Connection Options</summary>
      <Tabs tab={tab} setTab={setTab} tabs={tabs} />
      <div className="co-panel" role="tabpanel">
        {tab === 'tls' && (
          <>
            <small className="muted">{type === 'postgresql' ? 'Choose the SSL mode above (verify-full checks the server with this CA).' : type === 'redis' ? 'Use a rediss:// URL for TLS.' : 'Choose “require TLS” above; with a CA file the server is checked.'}</small>
            <FileField label="Certificate Authority (.pem)" value={files.ca} saved={saved.files?.ca} onChange={(v) => setFile('ca', v)} />
            <FileField label="Client Certificate (.pem)" value={files.cert} saved={saved.files?.cert} onChange={(v) => setFile('cert', v)} />
            <FileField label="Client Private Key (.pem)" value={files.key} saved={saved.files?.key} onChange={(v) => setFile('key', v)} />
          </>
        )}
        {tab === 'ssh' && <SshTab opts={opts} saved={saved.ssh} onChange={onOpts} />}
        {tab === 'ssh' && opts.ssh && type === 'postgresql' && <p className="muted co-note">Through a tunnel the host is 127.0.0.1: SSL mode verify-full would fail the host name check; use require.</p>}
        {tab === 'advanced' && onParams && (
          <KeyValues rows={params} onChange={onParams} hint={{ postgresql: 'For example application_name=reports or target_session_attrs=read-write.',
            sqlserver: 'Sent as URL options of the connection.', oracle: 'Sent as URL options of the connection.' }[type] || 'Extra options of the connection URL.'} />
        )}
      </div>
    </details>
  );
}

/** What to send as `options`: only what was changed in the tabs (the server keeps the rest). */
export function optionsPayload(opts) {
  const out = {};
  if (opts.files && Object.keys(opts.files).length) out.files = Object.fromEntries(Object.entries(opts.files).filter(([, v]) => v !== undefined));
  if (opts.ssh !== undefined) out.ssh = opts.ssh && { host: opts.ssh.host, port: Number(opts.ssh.port) || 22, user: opts.ssh.user, auth: opts.ssh.auth,
    ...(opts.ssh.auth === 'key' ? { privateKey: opts.ssh.privateKey || '', passphrase: opts.ssh.passphrase || '' } : { password: opts.ssh.password || '' }),
    hostKey: opts.ssh.hostKey || '' };
  return Object.keys(out).length ? out : undefined;
}
