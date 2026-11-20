// Report downloads on every page (Designer, Server, Run reports, Admin, Analytics).
//
// Big report files are made on the server, not in the page: `later()` asks with the header "X-Deliver: later",
// the server answers at once with a ticket and makes the file in the background (waiting in a queue when it is
// busy). This script keeps the tickets in localStorage and checks them every couple of seconds on whatever page
// is open, and saves each file when it is ready. So the user can keep working, open other pages, start more
// downloads, or reload: nothing is cancelled.
//
// `track()` is for work that has to stay in the page (a file the page itself fetches): while it runs, links to
// other pages open in a new tab and the browser asks before closing.
//
// A small panel in the bottom corner shows everything that is still being made, and for how long.
// Plain script (no imports) so the plain-HTML Run reports page and the React pages share it:
//   window.fxDownloads.later('Sales.xlsx', '/generate/SALES.xlsx?Branch=LHR')
//   window.fxDownloads.later('Sales.xlsx', '/api/server/reports/SALES/run', { method: 'POST', body: {...} })
(function () {
  if (window.fxDownloads) return;
  const STORE = 'fx-deliveries';
  const POLL_MS = 1500;
  const local = new Map();      // work running in this page (track)
  const notes = new Map();      // finished/failed messages shown for a few seconds
  let seq = 0;
  let box = null;
  let clockTimer = null;
  let pollTimer = null;
  let polling = false;

  const STYLE = `
.fx-dl { position: fixed; right: 16px; bottom: 16px; z-index: 2147483000; width: min(380px, calc(100vw - 32px));
  background: #1f2633; color: #f5f7fa; border-radius: 10px; box-shadow: 0 8px 28px rgba(0,0,0,.28);
  font: 13px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; padding: 10px 14px; }
.fx-dl-head { display: flex; align-items: center; gap: 8px; font-weight: 600; }
.fx-dl-head button { margin-left: auto; background: none; border: 0; color: #aab4c3; cursor: pointer; font: inherit; padding: 0 2px; }
.fx-dl-spin { width: 14px; height: 14px; border-radius: 50%; border: 2px solid rgba(255,255,255,.3); border-top-color: #fff;
  animation: fx-dl-spin .8s linear infinite; flex: none; }
.fx-dl-row { display: flex; justify-content: space-between; gap: 12px; margin-top: 4px; color: #d3d9e2; }
.fx-dl-row span:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fx-dl-row span:last-child { font-variant-numeric: tabular-nums; flex: none; color: #aab4c3; }
.fx-dl-row.ok span:last-child { color: #7ee2a8; }
.fx-dl-row.bad span:first-child { white-space: normal; color: #ffb4ab; }
.fx-dl-note { margin-top: 6px; color: #aab4c3; font-size: 12px; }
.fx-dl.min .fx-dl-row, .fx-dl.min .fx-dl-note { display: none; }
@keyframes fx-dl-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .fx-dl-spin { animation: none; } }`;

  const clock = (ms) => {
    const seconds = Math.max(0, Math.floor(ms / 1000));
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
  };
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  // ------------------------------------------------------------ tickets kept across pages
  function readTickets() {
    try { const list = JSON.parse(window.localStorage.getItem(STORE) || '[]'); return Array.isArray(list) ? list : []; } catch { return []; }
  }
  function writeTickets(list) {
    try { window.localStorage.setItem(STORE, JSON.stringify(list)); } catch { /* private mode: this page still polls its own */ }
  }
  let memoryTickets = [];      // used when localStorage is not available
  const tickets = () => { const stored = readTickets(); return stored.length || !memoryTickets.length ? stored : memoryTickets; };
  function addTicket(ticket) { memoryTickets = [...tickets(), ticket]; writeTickets(memoryTickets); }
  /** Take a ticket off the list; false when another tab already took it. */
  function takeTicket(id) {
    const list = tickets();
    if (!list.some((t) => t.id === id)) return false;
    memoryTickets = list.filter((t) => t.id !== id);
    writeTickets(memoryTickets);
    return true;
  }
  function patchTicket(id, change) {
    memoryTickets = tickets().map((t) => (t.id === id ? { ...t, ...change } : t));
    writeTickets(memoryTickets);
  }

  // ------------------------------------------------------------ the panel
  let minimised = false;
  function render() {
    const waiting = tickets();
    if (!local.size && !waiting.length && !notes.size) {
      if (box) box.remove();
      box = null;
      if (clockTimer) window.clearInterval(clockTimer);
      clockTimer = null;
      return;
    }
    if (!document.body) return;
    if (!document.getElementById('fx-dl-style')) {
      const style = el('style');
      style.id = 'fx-dl-style';
      style.textContent = STYLE;
      document.head.append(style);
    }
    if (!box) {
      box = el('div', 'fx-dl');
      box.setAttribute('role', 'status');
      box.setAttribute('aria-live', 'polite');
      document.body.append(box);
    }
    box.classList.toggle('min', minimised);
    if (!clockTimer) clockTimer = window.setInterval(render, 1000);
    const count = local.size + waiting.length;
    const head = el('div', 'fx-dl-head');
    if (count) head.append(el('span', 'fx-dl-spin'));
    head.append(el('span', '', count ? (count === 1 ? 'Preparing 1 download' : `Preparing ${count} downloads`) : 'Downloads'));
    const toggle = el('button', '', minimised ? '▴' : '▾');
    toggle.type = 'button';
    toggle.title = minimised ? 'Show the list' : 'Make smaller';
    toggle.onclick = () => { minimised = !minimised; render(); };
    head.append(toggle);
    const rows = [];
    for (const t of waiting) {
      const row = el('div', 'fx-dl-row');
      const state = t.status === 'queued' ? (t.waited ? 'waiting for a free place' : 'in the queue') : 'being made';
      row.append(el('span', '', t.label), el('span', '', `${state} · ${clock(Date.now() - t.started)}`));
      rows.push(row);
    }
    for (const item of local.values()) {
      const row = el('div', 'fx-dl-row');
      row.append(el('span', '', item.label), el('span', '', clock(Date.now() - item.started)));
      rows.push(row);
    }
    for (const note of notes.values()) {
      const row = el('div', `fx-dl-row ${note.ok ? 'ok' : 'bad'}`);
      row.append(el('span', '', note.text), el('span', '', note.ok ? 'saved' : 'failed'));
      rows.push(row);
    }
    const hint = local.size
      ? 'Stay on this page until it finishes. Other pages open in a new tab.'
      : count ? 'Made on the server: keep working or open other pages — each file is saved when it is ready.' : '';
    box.replaceChildren(head, ...rows, ...(hint ? [el('div', 'fx-dl-note', hint)] : []));
  }

  function note(text, ok) {
    const id = ++seq;
    notes.set(id, { text, ok });
    render();
    window.setTimeout(() => { notes.delete(id); render(); }, ok ? 6000 : 15000);
  }

  // ------------------------------------------------------------ saving a file
  function saveUrl(url) {
    // a normal browser download (Content-Disposition: attachment): the page stays where it is
    const link = el('a');
    link.href = url;
    link.rel = 'noopener';
    link.style.display = 'none';
    document.body.append(link);
    link.click();
    link.remove();
  }
  function saveBlob(blob, name) {
    const url = URL.createObjectURL(blob);
    const link = el('a');
    link.href = url;
    link.download = name;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 4000);
  }
  const fileName = (response, fallback) => (response.headers.get('Content-Disposition') || '').match(/filename="?([^";]+)"?/)?.[1] || fallback;

  async function readError(response) {
    try {
      const data = await response.json();
      return typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
    } catch { return `HTTP ${response.status}`; }
  }

  // ------------------------------------------------------------ checking the tickets
  async function pollOnce() {
    if (polling) return;
    polling = true;
    try {
      for (const t of tickets()) {
        let response;
        try {
          response = await fetch(t.statusUrl, { credentials: 'same-origin', cache: 'no-store' });
        } catch { continue; }              // offline for a moment: try again next time
        if (response.status === 404 || response.status === 401) {
          if (takeTicket(t.id)) note(`${t.label}: the download was lost (the server was restarted, or you signed out). Run it again.`, false);
          continue;
        }
        if (!response.ok) continue;
        const { delivery } = await response.json();
        if (delivery.status === 'ready') {
          if (takeTicket(t.id)) { saveUrl(delivery.fileUrl); note(t.label, true); }
        } else if (delivery.status === 'failed') {
          if (takeTicket(t.id)) note(`${t.label}: ${delivery.error || 'the report failed'}`, false);
        } else if (delivery.status !== t.status || Boolean(delivery.waitedForPlace) !== Boolean(t.waited)) {
          patchTicket(t.id, { status: delivery.status, waited: Boolean(delivery.waitedForPlace) });
        }
      }
    } finally {
      polling = false;
      schedule();
      render();
    }
  }
  function schedule() {
    if (pollTimer) window.clearTimeout(pollTimer);
    pollTimer = tickets().length ? window.setTimeout(pollOnce, POLL_MS) : null;
  }
  window.addEventListener('storage', (event) => { if (event.key === STORE) { render(); schedule(); } });

  /**
   * Ask the server to make a report file in the background and save it when it is ready (on any page).
   * Resolves { queued: true } once the server has the request; rejects with the server's message when it
   * refuses (wrong parameter, no right…). A server that answers with the file at once still works: it is saved.
   */
  async function later(label, url, { method = 'GET', body, headers = {} } = {}) {
    const init = { method, credentials: 'same-origin', headers: { ...headers, 'X-Deliver': 'later' } };
    if (body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = typeof body === 'string' ? body : JSON.stringify(body);
    }
    const response = await fetch(url, init);
    if (response.status === 202) {
      const { delivery } = await response.json();
      addTicket({ id: delivery.id, label: String(label || delivery.label || 'Report'), statusUrl: delivery.statusUrl,
        started: Date.now(), status: delivery.status });
      render();
      schedule();
      return { queued: true, delivery };
    }
    if (!response.ok) {
      const error = new Error(await readError(response));
      error.status = response.status;
      throw error;
    }
    saveBlob(await response.blob(), fileName(response, String(label || 'report')));
    note(String(label || 'report'), true);
    return { queued: false };
  }

  /** Run `work` (a promise or a function returning one) in this page while the panel shows `label`. */
  async function track(label, work) {
    const id = ++seq;
    local.set(id, { label: String(label || 'Report'), started: Date.now() });
    render();
    try {
      return await (typeof work === 'function' ? work() : work);
    } finally {
      local.delete(id);
      render();
    }
  }

  // A click on a link to another page would unload this one and cancel work that runs in the page.
  document.addEventListener('click', (event) => {
    if (!local.size || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target && event.target.closest ? event.target.closest('a[href]') : null;
    if (!link || link.target === '_blank' || link.hasAttribute('download')) return;
    const url = new URL(link.href, window.location.href);
    if (url.origin !== window.location.origin || url.protocol === 'javascript:') return;
    if (url.pathname === window.location.pathname && url.search === window.location.search) return; // same page (#tab)
    event.preventDefault();
    const names = [...local.values()].map((item) => item.label).join(', ');
    const target = (link.textContent || '').trim() || url.pathname;
    if (window.confirm(`${names} is still downloading. Leaving this page would cancel it.\n\nOK: open "${target}" in a new tab (the download keeps going here).\nCancel: stay on this page.`)) {
      window.open(url.href, '_blank', 'noopener');
    }
  }, true);

  window.addEventListener('beforeunload', (event) => {
    if (!local.size) return undefined;   // server-made files carry on without this page
    event.preventDefault();
    event.returnValue = '';
    return '';
  });

  window.fxDownloads = { track, later, saveUrl, count: () => local.size + tickets().length, pending: tickets };
  // tickets started on another page (or before a reload) carry on here
  const start = () => { render(); if (tickets().length) pollOnce(); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
