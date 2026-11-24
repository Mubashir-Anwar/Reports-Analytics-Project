// Working together, in the navigation bar of every page:
//  - who else is working right now (avatars; a warning when someone has the same report or workbook open),
//  - the team chat (to everyone, or to one person), with unread counts and a short pop-up for new messages,
//  - the notice "your account signed in on another browser: this one is signed out in 10 s".
// Pages say what they have open with setTeamPlace({ place, title }) (e.g. the report ID and its name).
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import './team.css';

const listeners = new Set();
let current = { place: '', title: '' };
/** What this page has open, e.g. { place: 'SALES_DAILY', title: 'Daily sales' } ('' = nothing). */
export function setTeamPlace(next) {
  const value = { place: next?.place || '', title: next?.title || '' };
  if (value.place === current.place && value.title === current.title) return;
  current = value;
  listeners.forEach((fn) => fn(value));
}
function useTeamPlace() {
  const [value, setValue] = useState(current);
  useEffect(() => { listeners.add(setValue); return () => listeners.delete(setValue); }, []);
  return value;
}

const APP_NAMES = { designer: 'Designer', query: 'Query', analytics: 'Analytics', server: 'Report Server', admin: 'Admin', runner: 'Run reports by URL' };
const WHAT = { designer: 'report', analytics: 'workbook', server: 'item' };
const initials = (name = '') => name.split(/[\s._-]+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join('').toUpperCase() || '?';
const hue = (name = '') => [...name].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
const tabId = (() => {
  try {
    const found = sessionStorage.getItem('team-tab');
    if (found) return found;
    const made = Math.random().toString(36).slice(2, 12);
    sessionStorage.setItem('team-tab', made);
    return made;
  } catch { return Math.random().toString(36).slice(2, 12); }
})();
const readSeen = () => { try { return JSON.parse(localStorage.getItem('team-chat-read') || '{}'); } catch { return {}; } };
const writeSeen = (value) => { try { localStorage.setItem('team-chat-read', JSON.stringify(value)); } catch { /* private mode */ } };
const where = (p) => `${APP_NAMES[p.app] || p.app}${p.title || p.place ? ` · ${p.title || p.place}` : ''}`;
const time = (iso) => { try { return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); } catch { return ''; } };

async function call(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin', cache: 'no-store', ...options,
    headers: options.body ? { 'Content-Type': 'application/json' } : undefined,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (!response.ok) {
    const error = new Error((await response.json().catch(() => ({}))).detail || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function Avatar({ name, size = 26 }) {
  return <span className="tm-avatar" style={{ width: size, height: size, background: `hsl(${hue(name)} 62% 46%)` }} aria-hidden="true">{initials(name)}</span>;
}

/** The "signed in on another browser" notice with its countdown. */
function Replaced({ notice }) {
  const [left, setLeft] = useState(notice.secondsLeft);
  useEffect(() => {
    const timer = setInterval(() => setLeft((n) => Math.max(0, n - 1)), 1000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => { if (left <= 0) window.location.assign('/login?reason=elsewhere'); }, [left]);
  return (
    <div className="tm-replaced-back" role="alertdialog" aria-labelledby="tm-replaced-title">
      <div className="tm-replaced">
        <div className="tm-count">{left}</div>
        <h3 id="tm-replaced-title">Your account signed in on another browser</h3>
        <p>It signed in on <b>{notice.by || 'another browser'}</b>. One account works in one browser at a time, so this browser is signed out in <b>{left} second{left === 1 ? '' : 's'}</b>.</p>
        <p className="muted">Save nothing more here. If this was not you, sign in again and change your password.</p>
        <button type="button" className="btn primary" onClick={() => window.location.assign('/login?reason=elsewhere')}>Sign out now</button>
      </div>
    </div>
  );
}

function ChatPanel({ me, people, users, messages, chat, setChat, onSend, onClose, place }) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const list = useRef(null);
  const seen = readSeen();
  const online = new Set(people.map((p) => p.username.toLowerCase()));
  const talkedTo = [...new Set(messages.filter((m) => m.to).map((m) => (m.from.toLowerCase() === me.toLowerCase() ? m.to : m.from)))];
  const rooms = ['', ...new Set([...people.map((p) => p.username), ...talkedTo])];
  const unread = (room) => messages.filter((m) => m.from.toLowerCase() !== me.toLowerCase() && m.seq > (seen[room || '*'] || 0)
    && (room ? Boolean(m.to) && m.from.toLowerCase() === room.toLowerCase() : !m.to)).length;
  const shown = messages.filter((m) => (chat ? m.to && [m.from, m.to].some((u) => u.toLowerCase() === chat.toLowerCase()) : !m.to));
  useEffect(() => { list.current?.scrollTo({ top: list.current.scrollHeight }); }, [shown.length, chat]);
  useEffect(() => {
    const last = shown.length ? shown[shown.length - 1].seq : 0;
    if (last > (seen[chat || '*'] || 0)) writeSeen({ ...seen, [chat || '*']: last });
  });

  const send = async (event) => {
    event.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    setError('');
    try { await onSend(text.trim(), chat || null); setText(''); } catch (failure) { setError(failure.message); } finally { setBusy(false); }
  };
  const person = chat ? people.find((p) => p.username.toLowerCase() === chat.toLowerCase()) : null;
  return (
    <aside className="tm-chat" aria-label="Team chat">
      <header className="tm-chat-head">
        <b>Team chat</b>
        <select value={chat} onChange={(e) => setChat(e.target.value)} aria-label="Write to">
          <option value="">Everyone</option>
          {[...new Set([...users, ...talkedTo])].map((u) => <option key={u} value={u}>{u}{online.has(u.toLowerCase()) ? ' ●' : ''}</option>)}
        </select>
        <button type="button" className="tm-x" aria-label="Close chat" onClick={onClose}>✕</button>
      </header>
      <nav className="tm-rooms">
        {rooms.map((room) => {
          const n = unread(room);
          return (
            <button key={room || '*'} type="button" className={`tm-room ${room === chat ? 'on' : ''}`} onClick={() => setChat(room)}>
              {room ? <><Avatar name={room} size={18} />{room}{online.has(room.toLowerCase()) && <i className="tm-dot" />}</> : '# Everyone'}
              {n > 0 && room !== chat && <span className="tm-badge">{n}</span>}
            </button>
          );
        })}
      </nav>
      <p className="tm-chat-where">
        {chat ? (person ? <>● {chat} is in {person.places.map(where).join(', ')}</> : <>{chat} is not online; they see it when they come back.</>)
          : 'Messages to everyone who can sign in.'}
      </p>
      <div className="tm-messages" ref={list}>
        {shown.length === 0 && <p className="muted tm-empty">No messages yet. Say hello 👋</p>}
        {shown.map((m) => {
          const mine = m.from.toLowerCase() === me.toLowerCase();
          return (
            <div key={m.id} className={`tm-msg ${mine ? 'mine' : ''}`}>
              {!mine && <Avatar name={m.from} size={22} />}
              <div>
                <small>{mine ? 'You' : m.from} · {time(m.at)}{m.place ? ` · ${m.place}` : ''}</small>
                <p>{m.text}</p>
              </div>
            </div>
          );
        })}
      </div>
      <form className="tm-send" onSubmit={send}>
        <textarea rows={2} value={text} maxLength={2000} placeholder={chat ? `Message ${chat}…` : 'Message everyone…'}
          onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) send(e); }} />
        <button type="submit" className="btn primary" disabled={busy || !text.trim()}>Send</button>
      </form>
      {place?.title && <p className="tm-chat-foot">Sent from: {place.title}</p>}
      {error && <p className="tm-error">{error}</p>}
    </aside>
  );
}

/** app: designer | analytics | server | admin (which page this is). */
export default function TeamBar({ app }) {
  const place = useTeamPlace();
  const [state, setState] = useState({ me: '', people: [], lastMessage: 0, replaced: null });
  const [messages, setMessages] = useState([]);
  const [open, setOpen] = useState(false);
  const [chat, setChat] = useState('');
  const [users, setUsers] = useState([]);
  const [toast, setToast] = useState(null);
  const [note, setNote] = useState(() => { try { const n = sessionStorage.getItem('team-note'); sessionStorage.removeItem('team-note'); return n; } catch { return null; } });
  const loaded = useRef(0);
  const placeRef = useRef(place);
  placeRef.current = place;
  const replacedRef = useRef(false);

  const loadMessages = useCallback(async (after) => {
    const { messages: found } = await call(`/api/team/messages?after=${after}`);
    if (!found.length) return;
    loaded.current = Math.max(loaded.current, found[found.length - 1].seq);
    setMessages((old) => [...old, ...found.filter((m) => !old.some((o) => o.id === m.id))].slice(-500));
    return found;
  }, []);

  useEffect(() => {
    let stop = false;
    let timer = null;
    let first = true;
    const beat = async () => {
      try {
        const answer = await call('/api/team/heartbeat', { method: 'POST', body: { tab: tabId, app, place: placeRef.current.place, title: placeRef.current.title } });
        if (stop) return;
        replacedRef.current = Boolean(answer.session?.replaced);
        setState({ me: answer.me, people: answer.people, lastMessage: answer.lastMessage, replaced: answer.session?.replaced || null });
        if (answer.lastMessage > loaded.current) {
          const fresh = await loadMessages(loaded.current);
          const theirs = (fresh || []).filter((m) => m.from.toLowerCase() !== answer.me.toLowerCase());
          if (!first && theirs.length) setToast(theirs[theirs.length - 1]);
        }
        first = false;
      } catch (failure) {
        if (failure.status === 401) {
          if (replacedRef.current) window.location.assign('/login?reason=elsewhere');
          return;                                   // signed out: the page itself sends the user to sign in
        }
      }
      if (!stop) timer = setTimeout(beat, document.hidden ? 15000 : 4000);
    };
    beat();
    const wake = () => { if (!document.hidden) { clearTimeout(timer); beat(); } };
    document.addEventListener('visibilitychange', wake);
    const bye = () => { fetch(`/api/team/heartbeat?tab=${tabId}`, { method: 'DELETE', credentials: 'same-origin', keepalive: true }).catch(() => {}); };
    window.addEventListener('pagehide', bye);
    return () => { stop = true; clearTimeout(timer); document.removeEventListener('visibilitychange', wake); window.removeEventListener('pagehide', bye); };
  }, [app, loadMessages]);

  useEffect(() => { if (open && !users.length) call('/api/team/users').then((r) => setUsers(r.users)).catch(() => {}); }, [open, users.length]);
  useEffect(() => { if (!toast) return undefined; const t = setTimeout(() => setToast(null), 7000); return () => clearTimeout(t); }, [toast]);
  useEffect(() => { if (!note) return undefined; const t = setTimeout(() => setNote(null), 12000); return () => clearTimeout(t); }, [note]);

  const { me, people, replaced } = state;
  const seen = readSeen();
  const unread = useMemo(() => messages.filter((m) => me && m.from.toLowerCase() !== me.toLowerCase()
    && m.seq > (seen[m.to ? m.from : '*'] || 0)).length, [messages, me, open, chat]); // eslint-disable-line react-hooks/exhaustive-deps
  const same = place.place ? people.filter((p) => p.places.some((x) => x.app === app && x.place === place.place)) : [];

  const send = async (text, to) => {
    const message = await call('/api/team/messages', { method: 'POST', body: { text, to, place: placeRef.current.title || placeRef.current.place } });
    loaded.current = Math.max(loaded.current, message.seq);
    setMessages((old) => [...old, message]);
  };
  const talkTo = (name) => { setChat(name); setOpen(true); };
  if (!me) return replaced ? <Replaced notice={replaced} /> : null;

  return (
    <>
      {same.length > 0 && (
        <button type="button" className="tm-same" onClick={() => talkTo(same[0].username)}
          title={`Two people saving the same ${WHAT[app] || 'item'}: the last save wins. Click to message ${same[0].username}.`}>
          ⚠ {same.map((p) => p.username).join(', ')} {same.length > 1 ? 'are' : 'is'} also on this {WHAT[app] || 'page'}
        </button>
      )}
      <div className="tm-people" aria-label="Working now">
        {people.slice(0, 4).map((p) => (
          <button key={p.username} type="button" className="tm-person" onClick={() => talkTo(p.username)}
            title={`${p.username} — ${p.places.map(where).join('; ')}\nClick to send a message`}>
            <Avatar name={p.username} /><i className="tm-dot" />
          </button>
        ))}
        {people.length > 4 && <button type="button" className="tm-more" onClick={() => setOpen(true)} title={people.slice(4).map((p) => p.username).join(', ')}>+{people.length - 4}</button>}
      </div>
      <button type="button" className={`tm-chat-btn ${open ? 'on' : ''}`} onClick={() => setOpen(!open)}
        title={`Team chat${people.length ? ` · ${people.length} online` : ''}`} aria-label="Team chat">
        💬{unread > 0 && <span className="tm-badge">{unread > 99 ? '99+' : unread}</span>}
      </button>
      {open && <ChatPanel me={me} people={people} users={users} messages={messages} chat={chat} setChat={setChat} onSend={send} onClose={() => setOpen(false)} place={place} />}
      {toast && !open && (
        <button type="button" className="tm-toast" onClick={() => { talkTo(toast.to ? toast.from : ''); setToast(null); }}>
          <Avatar name={toast.from} size={22} /><span><b>{toast.from}{toast.to ? ' (to you)' : ''}</b>{toast.text.slice(0, 140)}</span>
        </button>
      )}
      {note && <div className="tm-toast info" role="status"><span>{note}</span><button type="button" className="tm-x" onClick={() => setNote(null)}>✕</button></div>}
      {replaced && <Replaced notice={replaced} />}
    </>
  );
}
