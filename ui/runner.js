const $ = (selector) => document.querySelector(selector);
const reportList = $('#report-list');
const runForm = $('#run-form');
let reports = [];
let selectedReport = null;
let searchTerm = '';

const formatLabels = { csv: 'CSV', xlsx: 'Excel (.xlsx)', pdf: 'PDF' };
const ACCESS_MESSAGE = 'Your sign-in has ended. Opening the sign-in page…';

// /reports and /generate use the signed-in account (session cookie); the server checks its Run reports right
// and folder permissions. A website runs reports with an API key or a signed link instead (see Admin → API keys).
function accessHeaders(extra = {}) {
  return extra;
}

function signInAgain() {
  window.location.assign(`/login?next=${encodeURIComponent('/ui')}`);
}

function showServiceState(text, kind = 'pending') {
  const pill = $('#service-state');
  pill.className = `status-pill ${kind}`;
  pill.replaceChildren(Object.assign(document.createElement('span'), { className: 'state-dot' }), document.createTextNode(text));
}

function showPageMessage(text, kind = 'error') {
  const notice = $('#page-message');
  notice.textContent = text;
  notice.className = `runner-notice ${kind}`;
  notice.hidden = !text;
}

function makeReportItem(report) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = `report-item${selectedReport?.id === report.id ? ' selected' : ''}`;
  button.setAttribute('aria-current', selectedReport?.id === report.id ? 'true' : 'false');

  const icon = document.createElement('span');
  icon.className = 'report-item-icon';
  icon.setAttribute('aria-hidden', 'true');
  icon.textContent = '▤';
  const content = document.createElement('span');
  content.className = 'report-item-copy';
  const title = document.createElement('strong');
  title.textContent = report.title || report.id;
  const detail = document.createElement('small');
  const parameterCount = (report.parameters || []).length;
  detail.textContent = `${parameterCount} question${parameterCount === 1 ? '' : 's'} · ${(report.formats || []).map((format) => format.toUpperCase()).join(', ') || 'CSV'}`;
  const id = document.createElement('code');
  id.className = 'report-item-id';
  id.textContent = report.id;
  id.title = 'Report ID: use it in links, e.g. /generate/' + report.id + '.pdf';
  content.append(title, detail, id);
  const arrow = document.createElement('span');
  arrow.className = 'report-item-arrow';
  arrow.textContent = '›';
  arrow.setAttribute('aria-hidden', 'true');
  button.append(icon, content, arrow);
  button.addEventListener('click', () => selectReport(report));
  return button;
}

function readClosedFolders() {
  try { return JSON.parse(localStorage.getItem('runner-closed-folders') || '[]'); } catch { return []; }
}

function saveClosedFolder(folder, isClosed) {
  const closed = new Set(readClosedFolders());
  if (isClosed) closed.add(folder); else closed.delete(folder);
  try { localStorage.setItem('runner-closed-folders', JSON.stringify([...closed])); } catch { /* private mode */ }
}

function renderCatalog() {
  const needle = searchTerm.trim().toLocaleLowerCase();
  const visibleReports = reports.filter((report) => {
    const searchable = [report.title, report.id, report.folder, report.description].join(' ').toLocaleLowerCase();
    return searchable.includes(needle);
  });
  $('#report-count').textContent = `${visibleReports.length} ${visibleReports.length === 1 ? 'report' : 'reports'}`;

  if (!visibleReports.length) {
    const empty = document.createElement('div');
    empty.className = 'empty-state catalog-empty';
    empty.textContent = reports.length ? 'No reports match that search.' : 'No reports have been saved yet. Open Manage to create one.';
    reportList.replaceChildren(empty);
    return;
  }

  const grouped = new Map();
  for (const report of visibleReports) {
    const folder = report.folder || 'General';
    if (!grouped.has(folder)) grouped.set(folder, []);
    grouped.get(folder).push(report);
  }
  const fragment = document.createDocumentFragment();
  const closed = readClosedFolders();
  for (const [folder, items] of [...grouped.entries()].sort(([left], [right]) => left.localeCompare(right))) {
    const section = document.createElement('details');
    section.className = 'runner-folder-group';
    section.open = Boolean(needle) || !closed.includes(folder);
    const heading = document.createElement('summary');
    heading.innerHTML = '<svg class="folder-icon" viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2h7.5A2.5 2.5 0 0 1 21 9.5v8A2.5 2.5 0 0 1 18.5 20h-13A2.5 2.5 0 0 1 3 17.5z" fill="currentColor"/></svg>';
    const name = document.createElement('span');
    name.className = 'folder-name';
    name.textContent = folder.split('/').join(' / ');
    const count = document.createElement('span');
    count.className = 'folder-count';
    count.textContent = String(items.length);
    heading.append(name, count);
    section.addEventListener('toggle', () => { if (!needle) saveClosedFolder(folder, !section.open); });
    section.append(heading, ...items.map(makeReportItem));
    fragment.append(section);
  }
  reportList.replaceChildren(fragment);
}

function setParameterControl(parameter) {
  const wrapper = document.createElement('div');
  wrapper.className = 'field parameter-field';
  const inputId = `param-${parameter.name}`;
  const label = document.createElement('label');
  label.htmlFor = inputId;
  label.textContent = `${parameter.label || parameter.name}${parameter.required ? ' *' : ''}`;

  let input;
  if (Array.isArray(parameter.allowedValues) && parameter.allowedValues.length) {
    input = document.createElement('select');
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = 'Choose an option';
    input.append(placeholder);
    for (const value of parameter.allowedValues) {
      const option = document.createElement('option');
      option.value = String(value);
      option.textContent = String(value);
      input.append(option);
    }
  } else if (parameter.type === 'boolean') {
    input = document.createElement('select');
    for (const [value, text] of [['', 'Choose an option'], ['true', 'Yes'], ['false', 'No']]) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = text;
      input.append(option);
    }
  } else {
    input = document.createElement('input');
    input.type = ({ date: 'date', integer: 'number', number: 'number' })[parameter.type] || 'text';
    if (parameter.type === 'integer') input.step = '1';
    if (parameter.type === 'number') input.step = 'any';
    if (parameter.type === 'string') input.maxLength = Number(parameter.maxLength || 200);
  }

  input.id = inputId;
  input.name = parameter.name;
  input.required = Boolean(parameter.required);
  if (parameter.type === 'integer' || parameter.type === 'number') input.inputMode = 'decimal';
  if (parameter.description) input.setAttribute('aria-describedby', `${inputId}-help`);
  if (parameter.default !== undefined && parameter.default !== null) input.value = String(parameter.default);
  wrapper.append(label, input);
  if (parameter.description) {
    const note = document.createElement('span');
    note.className = 'help-text';
    note.id = `${inputId}-help`;
    note.textContent = parameter.description;
    wrapper.append(note);
  }
  return wrapper;
}

function selectReport(report) {
  const previousReport = selectedReport;
  selectedReport = report;
  $('#selection-empty').hidden = true;
  $('#report-workspace').hidden = false;
  $('#report-title').textContent = report.title || report.id;
  $('#report-location').textContent = (report.folder || 'General').replaceAll('/', '  /  ');
  $('#report-description').textContent = report.description || 'Enter the report questions below, then choose a file format.';
  $('#connection-tag').textContent = `Data adapter: ${report.connectionId || 'default'}`;
  if (selectedReport !== previousReport || !previousReport) showAdapterState(report);
  $('#run-message').hidden = true;
  $('#run-message').textContent = '';

  const parameters = report.parameters || [];
  $('#parameter-fields').replaceChildren(...parameters.map(setParameterControl));
  $('#no-parameters').hidden = parameters.length > 0;
  const formats = (report.formats || ['csv', 'xlsx', 'pdf']).filter((format) => formatLabels[format]);
  const formatSelect = $('#output-format');
  formatSelect.replaceChildren(...formats.map((format) => {
    const option = document.createElement('option');
    option.value = format;
    option.textContent = formatLabels[format];
    return option;
  }));
  formatSelect.value = formats.includes(report.defaultFormat) ? report.defaultFormat : (formats[0] || 'csv');
  runForm.hidden = false;
  renderCatalog();
}

async function loadReports() {
  $('#report-count').textContent = 'Loading report list…';
  reportList.innerHTML = '<p class="muted">Loading reports…</p>';
  showPageMessage('');
  try {
    const response = await fetch('/reports', { headers: accessHeaders({ Accept: 'application/json' }), credentials: 'same-origin' });
    if (response.status === 401) { signInAgain(); throw new Error(ACCESS_MESSAGE); }
    if (!response.ok) throw new Error(`Could not load reports (${response.status}).`);
    const data = await response.json();
    reports = data.reports || [];
    if (!reports.length) {
      selectedReport = null;
      $('#report-workspace').hidden = true;
      $('#selection-empty').hidden = false;
    }
    renderCatalog();
    if (reports.length) {
      const nextSelected = reports.find((item) => item.id === selectedReport?.id) || reports[0];
      selectReport(nextSelected);
    }
  } catch (error) {
    reports = [];
    renderCatalog();
    showPageMessage(error.message);
  }
}

function setRunMessage(text, kind) {
  const message = $('#run-message');
  message.textContent = text;
  message.className = `run-result ${kind}`;
  message.hidden = !text;
}

function getDownloadName(response, fallback) {
  const disposition = response.headers.get('Content-Disposition') || '';
  const match = disposition.match(/filename\*?=(?:UTF-8''|\")?([^\";]+)/i);
  return match ? decodeURIComponent(match[1].replace(/\"/g, '').trim()) : fallback;
}

async function readError(response) {
  const body = await response.text();
  try {
    const parsed = JSON.parse(body);
    return parsed.detail || body || `Request failed (${response.status}).`;
  } catch {
    return body || `Request failed (${response.status}).`;
  }
}

runForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!selectedReport || !runForm.reportValidity()) return;

  const query = new URLSearchParams();
  for (const parameter of selectedReport.parameters || []) {
    const input = runForm.elements.namedItem(parameter.name);
    if (input && input.value !== '') query.set(parameter.name, input.value);
  }
  for (const [field, key] of [['generated-by', 'GeneratedBy'], ['generated-by-code', 'GeneratedByCode'], ['folder', 'Folder']]) {
    const value = $(`#${field}`).value.trim();
    if (value) query.set(key, value);
  }
  query.set('Generate', '');

  const format = $('#output-format').value;
  const button = $('#run-button');
  button.disabled = true;
  button.classList.add('is-loading');
  button.firstElementChild.textContent = 'Generating…';
  setRunMessage('Running the report and preparing your download…', 'pending');
  try {
    const url = `/generate/${encodeURIComponent(selectedReport.id)}.${format}?${query.toString()}`;
    // Made on the server in the background (X-Deliver: later): the file is saved when it is ready, on whatever
    // page is open then, so you can run more reports or leave this page meanwhile. The panel shows progress.
    const label = `${selectedReport.title || selectedReport.id}.${format}`;
    let queued = false;
    try {
      ({ queued } = await window.fxDownloads.later(label, url, { headers: accessHeaders({ Accept: 'application/octet-stream, application/json' }) }));
    } catch (error) {
      if (error.status === 401) { signInAgain(); throw new Error(ACCESS_MESSAGE); }
      throw error;
    }
    setRunMessage(queued
      ? `${formatLabels[format] || format.toUpperCase()} report is being made on the server. It downloads by itself when ready — you can run another report or open other pages meanwhile.`
      : `${formatLabels[format] || format.toUpperCase()} report generated and downloaded.`, 'success');
  } catch (error) {
    setRunMessage(error.message || 'Report could not be generated. Please try again.', 'error');
  } finally {
    button.disabled = false;
    button.classList.remove('is-loading');
    button.firstElementChild.textContent = 'Generate report';
  }
});

$('#report-search').addEventListener('input', (event) => {
  searchTerm = event.target.value;
  renderCatalog();
});
$('#refresh-reports').addEventListener('click', loadReports);

// The status pill shows the data adapter of the selected report (the one /generate will use).
let statusRequest = 0;
async function showAdapterState(report) {
  const requestId = ++statusRequest;
  const adapter = report.connectionId || 'default';
  showServiceState(`Checking data adapter · ${adapter}`, 'pending');
  try {
    const response = await fetch(`/reports/connections/${encodeURIComponent(adapter)}/status`, { headers: accessHeaders({ Accept: 'application/json' }), cache: 'no-store' });
    const data = await response.json().catch(() => ({}));
    if (requestId !== statusRequest) return; // another report was selected meanwhile
    if (response.ok && data.status === 'ok') {
      showServiceState(`Connected · ${data.name || adapter}${data.database ? ` (${data.database})` : ''}`, 'ready');
      $('#service-state').title = '';
    } else {
      showServiceState(`Not connected · ${data.name || adapter}`, 'error');
      $('#service-state').title = data.detail || `Status check failed (${response.status})`;
    }
  } catch {
    if (requestId === statusRequest) showServiceState(`Not connected · ${adapter}`, 'error');
  }
}

fetch('/health', { headers: { Accept: 'application/json' }, cache: 'no-store' })
  .then(() => { if (!selectedReport) showServiceState('Service running', 'ready'); })
  .catch(() => showServiceState('Service unavailable', 'error'));

loadReports();

// Phone menu of the navigation bar; the account menu closes when clicking elsewhere.
document.querySelector('[data-nav-toggle]')?.addEventListener('click', (event) => {
  const open = document.getElementById('fx-nav').classList.toggle('open');
  event.currentTarget.textContent = open ? '✕' : '☰';
});
document.addEventListener('click', (event) => {
  document.querySelectorAll('details.fx-user[open]').forEach((menu) => { if (!menu.contains(event.target)) menu.open = false; });
});

// Show only the parts this account may open (the server refuses the others anyway).
fetch('/api/server/me', { credentials: 'same-origin', cache: 'no-store' })
  .then((response) => (response.ok ? response.json() : null))
  .then((me) => {
    if (!me) return;
    document.querySelectorAll('[data-right]').forEach((link) => { link.hidden = !me.rights.includes(link.dataset.right); });
    document.querySelectorAll('[data-signout]').forEach((button) => { button.hidden = false; });
    const initials = me.username.split(/[\s._-]+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join('').toUpperCase() || '?';
    document.querySelectorAll('[data-initials]').forEach((node) => { node.textContent = initials; });
    document.querySelectorAll('[data-username]').forEach((node) => { node.textContent = me.username; });
    document.querySelectorAll('[data-user]').forEach((node) => { node.hidden = false; });
  })
  .catch(() => {});

// Product name (Admin → Branding) and the theme menu (same settings as the other pages: ui/brand/theme.js).
fetch('/api/branding', { credentials: 'same-origin' }).then((r) => (r.ok ? r.json() : null)).then((brand) => {
  if (!brand?.name) return;
  document.querySelectorAll('[data-brand]').forEach((el) => { el.textContent = brand.name; });
  document.title = `Run reports · ${brand.name}`;
}).catch(() => {});

(() => {
  const LOOK = window.ReportingLook;
  const box = document.querySelector('[data-look]');
  if (!LOOK || !box) return;
  const button = box.querySelector('[data-look-toggle]');
  const BAR = { indigo: '#312e81', blue: '#1a4fb4', green: '#107c41', teal: '#0f5e58', orange: '#b23c0a', rose: '#9f1239', violet: '#5b21b6' };
  const make = (tag, props = {}, children = []) => {
    const el = document.createElement(tag);
    Object.entries(props).forEach(([key, value]) => {
      if (key === 'class') el.className = value;
      else if (key === 'text') el.textContent = value;
      else if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
      else el.setAttribute(key, value);
    });
    children.forEach((child) => el.append(child));
    return el;
  };
  const seg = (label, options, value, key) => make('div', { class: 'lk-seg', role: 'group', 'aria-label': label },
    options.map(([id, text]) => make('button', { type: 'button', class: value === id ? 'on' : '', 'aria-pressed': String(value === id), text, onclick: () => render(LOOK.save({ [key]: id })) })));
  let panel = null;
  function render(look = LOOK.read()) {
    if (!panel) return;
    const preset = LOOK.presetOf(look);
    panel.replaceChildren(
      make('h3', { text: 'Themes' }),
      make('div', { class: 'lk-presets' }, LOOK.PRESETS.map((item) => {
        const dark = item.look.mode === 'dark' || (item.look.mode === 'system' && LOOK.isDark());
        const accent = (LOOK.ACCENTS.find((a) => a.id === item.look.accent) || {}).color;
        const swatch = make('span', { class: 'lk-swatch', 'aria-hidden': 'true', style: `background:${dark ? '#0b1120' : '#eef1f6'}` }, [
          make('i', { style: `background:${item.look.contrast === 'high' ? '#000' : item.look.bar === 'accent' ? BAR[item.look.accent] : '#0c1425'}` }),
          make('span', {}, [make('i', { style: `background:${dark ? '#1a2438' : '#fff'}` }), make('i', { style: `background:linear-gradient(${dark ? '#1a2438' : '#fff'} 0 55%, ${accent} 55% 75%, ${dark ? '#1a2438' : '#fff'} 75%)` })]),
        ]);
        return make('button', { type: 'button', class: `lk-preset ${preset === item.id ? 'on' : ''}`, 'aria-pressed': String(preset === item.id), title: item.text, onclick: () => render(LOOK.save(item.look)) },
          [swatch, make('b', { text: item.label }), make('small', { text: item.text })]);
      })),
      make('h3', { text: 'Mode' }), seg('Mode', [['system', '◐ Auto'], ['light', '☀ Light'], ['dark', '☾ Dark']], look.mode, 'mode'),
      make('h3', { text: 'Accent colour' }),
      make('div', { class: 'lk-row', role: 'group', 'aria-label': 'Accent colour' }, LOOK.ACCENTS.map((item) => make('button', {
        type: 'button', class: `lk-dot ${look.accent === item.id ? 'on' : ''}`, style: `background:${item.color}`, title: item.label, 'aria-label': item.label,
        'aria-pressed': String(look.accent === item.id), onclick: () => render(LOOK.save({ accent: item.id })),
      }))),
      make('h3', { text: 'Title bar' }), seg('Title bar', [['dark', 'Dark'], ['accent', 'Accent colour']], look.bar, 'bar'),
      make('label', { class: 'lk-check' }, [make('input', { type: 'checkbox', ...(look.contrast === 'high' ? { checked: '' } : {}), onchange: (e) => render(LOOK.save({ contrast: e.target.checked ? 'high' : 'normal' })) }), 'High contrast (strongest text and lines)']),
    );
  }
  const close = () => { panel?.remove(); panel = null; button.setAttribute('aria-expanded', 'false'); };
  button.addEventListener('click', () => {
    if (panel) { close(); return; }
    panel = make('div', { class: 'lk-panel', role: 'dialog', 'aria-label': 'Theme' });
    box.append(panel);
    button.setAttribute('aria-expanded', 'true');
    render();
  });
  document.addEventListener('mousedown', (event) => { if (panel && !box.contains(event.target)) close(); });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') close(); });
})();
