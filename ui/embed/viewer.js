// Embedded report viewer (inside the iframe that /assets/embed/sdk.js or the website puts on its page).
// Talks only to this server, with the embed token in the X-Embed-Token header. Tells the host page about itself
// with postMessage (mrs:ready, mrs:height, mrs:ran, mrs:error) and accepts mrs:run / mrs:preview from it.
(function () {
  'use strict';
  const root = document.getElementById('mrs');
  if (!root) return;
  const token = root.dataset.token || '';
  const FORMAT_LABEL = { pdf: 'PDF', xlsx: 'Excel', csv: 'CSV' };
  let report = null;
  let pdfUrl = '';

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (key === 'text') node.textContent = value;
      else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else if (value !== undefined && value !== null && value !== false) node.setAttribute(key, value === true ? '' : value);
    });
    (children || []).forEach((child) => child && node.append(child));
    return node;
  }

  // ---- talking to the host page (only sizes and outcomes; never data or the token)
  let hostOrigin = '*';
  try { if (document.referrer) hostOrigin = new URL(document.referrer).origin; } catch (error) { hostOrigin = '*'; }
  function tell(type, detail) {
    if (window.parent !== window) window.parent.postMessage({ source: 'mrs-report', type, ...(detail || {}) }, hostOrigin);
  }
  window.addEventListener('message', (event) => {
    if (event.source !== window.parent || !event.data || typeof event.data !== 'object') return;
    if (event.data.type === 'mrs:run' && report) run(String(event.data.format || 'pdf'));
    if (event.data.type === 'mrs:preview' && report) preview();
  });
  new ResizeObserver(() => tell('mrs:height', { height: Math.ceil(document.documentElement.scrollHeight) })).observe(document.body);

  // ---- the server
  async function call(path, body, raw) {
    const response = await fetch(path, {
      method: body === undefined ? 'GET' : 'POST',
      headers: { 'X-Embed-Token': token, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
      body: body === undefined ? undefined : JSON.stringify(body), credentials: 'omit', cache: 'no-store',
    });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try { detail = (await response.json()).detail || detail; } catch (error) { /* not JSON */ }
      throw new Error(detail);
    }
    return raw ? response : response.json();
  }

  // ---- the page
  const status = el('p', { class: 'mrs-status', role: 'status' });
  const result = el('section', { class: 'mrs-result' });
  const form = el('form', { class: 'mrs-form', onsubmit: (event) => { event.preventDefault(); preview(); } });
  const buttons = el('div', { class: 'mrs-buttons' });

  function say(text, kind) {
    status.textContent = text || '';
    status.className = `mrs-status ${kind || ''}`;
  }

  function values() {
    const out = {};
    report.parameters.forEach((p) => {
      if (p.locked) return;
      const input = form.elements.namedItem(p.name);
      if (input && input.value !== '') out[p.name] = input.value;
    });
    return out;
  }

  function field(p) {
    const id = `mrs-p-${p.name}`;
    let input;
    if (p.locked) {
      input = el('output', { id, class: 'mrs-locked', title: 'Set by the website' , text: Array.isArray(p.value) ? p.value.join(', ') : String(p.value ?? '') });
    } else if (p.allowedValues) {
      input = el('select', { id, name: p.name, required: p.required }, [
        p.required ? null : el('option', { value: '', text: '(any)' }),
        ...p.allowedValues.map((v) => el('option', { value: String(v), text: String(v), selected: String(v) === String(p.value ?? '') })),
      ]);
    } else {
      const type = p.type === 'date' ? 'date' : ['integer', 'number'].includes(p.type) ? 'number' : 'text';
      input = el('input', { id, name: p.name, type, required: p.required, value: p.value ?? '', autocomplete: 'off' });
    }
    return el('label', { class: 'mrs-field', for: id }, [
      el('span', { text: `${p.label}${p.required && !p.locked ? ' *' : ''}${p.locked ? ' 🔒' : ''}` }), input,
      p.description ? el('small', { text: p.description }) : null,
    ]);
  }

  function table(data) {
    const head = el('tr', {}, data.columns.map((c) => el('th', { scope: 'col', text: data.labels[c] || c })));
    const rows = data.rows.map((row) => el('tr', {}, data.columns.map((c) => el('td', { text: row[c] === null || row[c] === undefined ? '' : String(row[c]) }))));
    return el('div', { class: 'mrs-table-wrap' }, [el('table', { class: 'mrs-table' }, [el('thead', {}, [head]), el('tbody', {}, rows)])]);
  }

  async function preview() {
    if (!form.reportValidity()) return;
    say('Loading…');
    try {
      const data = await call('/api/embed/preview', { parameters: values() });
      result.replaceChildren(data.rows.length ? table(data) : el('p', { class: 'mrs-muted', text: 'No records for these values.' }));
      say(data.rowCount >= data.limit ? `First ${data.limit} records — download the file for all of them.` : `${data.rowCount} records`, 'ok');
      tell('mrs:ran', { action: 'preview', rows: data.rowCount });
    } catch (error) { say(error.message, 'error'); tell('mrs:error', { message: error.message }); }
  }

  async function run(format) {
    if (!form.reportValidity()) return;
    say(`Making the ${FORMAT_LABEL[format] || format} file…`);
    try {
      const response = await call('/api/embed/run', { format, parameters: values() }, true);
      const blob = await response.blob();
      const name = (/filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') || '') || [])[1] || `${report.id}.${format}`;
      if (pdfUrl) URL.revokeObjectURL(pdfUrl);
      const url = URL.createObjectURL(blob);
      if (format === 'pdf') {
        pdfUrl = url;
        result.replaceChildren(el('iframe', { class: 'mrs-pdf', src: url, title: `${report.title} (PDF)` }),
          el('a', { class: 'mrs-link', href: url, download: name, text: `Download ${name}` }));
      } else {
        const link = el('a', { href: url, download: name });
        document.body.append(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 60000);
      }
      say(`${FORMAT_LABEL[format] || format} ready`, 'ok');
      tell('mrs:ran', { action: 'run', format });
    } catch (error) { say(error.message, 'error'); tell('mrs:error', { message: error.message }); }
  }

  async function start() {
    try {
      report = await call('/api/embed/report');
    } catch (error) {
      root.replaceChildren(el('main', { class: 'mrs-empty' }, [el('h1', { text: 'Report not available' }), el('p', { text: error.message })]));
      tell('mrs:error', { message: error.message });
      return;
    }
    document.title = report.title;
    report.parameters.forEach((p) => form.append(field(p)));
    buttons.append(el('button', { type: 'submit', class: 'mrs-primary', text: 'Show' }));
    report.formats.forEach((format) => buttons.append(el('button', { type: 'button', text: FORMAT_LABEL[format] || format, onclick: () => run(format) })));
    form.append(buttons);
    root.replaceChildren(el('main', { class: 'mrs-main' }, [
      el('header', { class: 'mrs-head' }, [el('h1', { text: report.title }), report.description ? el('p', { class: 'mrs-muted', text: report.description }) : null]),
      form, status, result,
    ]));
    tell('mrs:ready', { reportId: report.id, title: report.title });
    if (!report.parameters.some((p) => p.required && !p.locked && (p.value === null || p.value === undefined || p.value === ''))) preview();
  }

  start();
}());
