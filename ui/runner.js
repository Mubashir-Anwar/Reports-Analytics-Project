const $ = (selector) => document.querySelector(selector);
const reportList = $('#report-list');
const runForm = $('#run-form');
let reports = [];
let selectedReport = null;
let searchTerm = '';

const formatLabels = { csv: 'CSV', xlsx: 'Excel (.xlsx)', pdf: 'PDF' };

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
  content.append(title, detail);
  const arrow = document.createElement('span');
  arrow.className = 'report-item-arrow';
  arrow.textContent = '›';
  arrow.setAttribute('aria-hidden', 'true');
  button.append(icon, content, arrow);
  button.addEventListener('click', () => selectReport(report));
  return button;
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
  for (const [folder, items] of [...grouped.entries()].sort(([left], [right]) => left.localeCompare(right))) {
    const section = document.createElement('section');
    section.className = 'runner-folder-group';
    const heading = document.createElement('h3');
    heading.textContent = folder.replaceAll('/', '  /  ');
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
  selectedReport = report;
  $('#selection-empty').hidden = true;
  $('#report-workspace').hidden = false;
  $('#report-title').textContent = report.title || report.id;
  $('#report-location').textContent = (report.folder || 'General').replaceAll('/', '  /  ');
  $('#report-description').textContent = report.description || 'Enter the report questions below, then choose a file format.';
  $('#connection-tag').textContent = report.connectionId || 'MongoDB';
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
    const response = await fetch('/reports', { headers: { Accept: 'application/json' } });
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
    const response = await fetch(url, { headers: { Accept: 'application/octet-stream, application/json' } });
    if (!response.ok) throw new Error(await readError(response));
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = getDownloadName(response, `${selectedReport.id}.${format}`);
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    setRunMessage(`${formatLabels[format] || format.toUpperCase()} report generated and downloaded.`, 'success');
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

fetch('/health', { headers: { Accept: 'application/json' } })
  .then(async (response) => ({ response, data: await response.json() }))
  .then(({ response, data }) => {
    if (response.ok && data.status === 'ok') showServiceState(`Service ready · ${data.database}`, 'ready');
    else showServiceState(`Service ${data.status || 'unavailable'}`, 'error');
  })
  .catch(() => showServiceState('Service unavailable', 'error'));

loadReports();
