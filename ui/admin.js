const $ = (selector) => document.querySelector(selector);
const tokenInput = $('#admin-token');
const messageBox = $('#admin-message');
const reportList = $('#admin-report-list');
const reportForm = $('#report-editor');
const tokenKey = 'mongoReportingAdminToken';
const types = [
  ['date', 'Date'], ['string', 'Text'], ['integer', 'Whole number'],
  ['number', 'Decimal number'], ['boolean', 'Yes / No'],
];
let token = sessionStorage.getItem(tokenKey) || '';
let reports = [];
let connections = [];
let folders = ['General'];
let currentReportId = null;
let activeFolder = '';
let adminConnected = false;
let columnLabelDraft = {};
let canvasParagraphs = [];
let canvasImageDataUrl = '';
let pendingImportXml = '';
let pendingImportDefinition = null;
let queryLanguage = 'aggregation-json';
let querySourcesByLanguage = {};
let validQueryLanguages = {};
let lastParsedPipeline = [];
let queryDirty = false;
let canvasPositions = {};
let canvasParameterFields = [];
let selectedCanvasObject = '';
let canvasZoom = 0.72;
let canvasDrag = null;
let canvasResize = null;
let canvasPan = null;
let spaceHeld = false;

const CANVAS_PAGE = { width: 794, height: 1123 };
const DEFAULT_CANVAS_POSITIONS = {
  title: { x: 64, y: 70, w: 666, h: 54 },
  intro: { x: 64, y: 132, w: 666, h: 62 },
  image: { x: 64, y: 210, w: 280, h: 170 },
  table: { x: 64, y: 650, w: 666, h: 390 },
};

function notice(message, kind = '') {
  messageBox.textContent = message;
  messageBox.className = `notice${kind ? ` ${kind}` : ''}`;
}
function inline(id, message, isError = false) {
  const target = $(`#${id}`);
  target.textContent = message;
  target.classList.toggle('error', isError);
}
function headers(json = false) {
  const result = { 'X-Admin-Token': token };
  if (json) result['Content-Type'] = 'application/json';
  return result;
}
async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { ...headers(Boolean(options.body)), ...(options.headers || {}) } });
  let body = {};
  try { body = await response.json(); } catch (_) { /* no JSON response */ }
  if (!response.ok) {
    const error = new Error(body.detail || `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return body;
}
function setAdminConnected(connected) {
  adminConnected = connected;
  $('#new-report').disabled = !connected;
  $('#new-folder').disabled = !connected;
  $('#import-report').disabled = !connected;
  $('#new-report').title = connected ? '' : 'Pehle admin token se Connect karein.';
  $('#new-folder').title = connected ? '' : 'Pehle admin token se Connect karein.';
  $('#token-entry').hidden = connected;
  $('#admin-auth-state').textContent = connected ? 'Connected' : 'Not connected';
  $('#admin-auth-state').classList.toggle('connected', connected);
  $('#admin-access-description').textContent = connected
    ? 'Admin setup ready hai. Local machine par token automatically use hota hai.'
    : 'Local setup token khud use karta hai; deployed server ke liye environment token dein.';
}
function folderError(message) {
  const box = $('#folder-error');
  box.textContent = message;
  box.hidden = !message;
}
function pretty(value) { return JSON.stringify(value, null, 2); }
function escapeHtml(value) { return String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;'); }
function pythonLiteral(value, depth = 0) {
  const indent = '  '.repeat(depth), inner = '  '.repeat(depth + 1);
  if (value === null) return 'None';
  if (value === true) return 'True';
  if (value === false) return 'False';
  if (Array.isArray(value)) return value.length ? `[\n${value.map((item) => `${inner}${pythonLiteral(item, depth + 1)}`).join(',\n')}\n${indent}]` : '[]';
  if (value && typeof value === 'object') {
    const entries = Object.entries(value);
    return entries.length ? `{\n${entries.map(([key, item]) => `${inner}${JSON.stringify(key)}: ${pythonLiteral(item, depth + 1)}`).join(',\n')}\n${indent}}` : '{}';
  }
  return JSON.stringify(value);
}
function formatPipelineForLanguage(pipeline, language, collection = '') {
  const json = pretty(pipeline);
  if (language === 'mongosh') return `db.getCollection(${JSON.stringify(collection || 'your-collection')}).aggregate(\n${json}\n);`;
  if (language === 'javascript') return `const pipeline = ${json};`;
  if (language === 'python') return `pipeline = ${pythonLiteral(pipeline)}`;
  return json;
}
function defaultCanvasPosition(id) {
  if (DEFAULT_CANVAS_POSITIONS[id]) return { ...DEFAULT_CANVAS_POSITIONS[id] };
  const paragraphMatch = /^paragraph-(\d+)$/.exec(id);
  if (paragraphMatch) return { x: 64, y: 220 + Number(paragraphMatch[1]) * 96, w: 666, h: 78 };
  const parameterMatch = /^parameter-(\d+)$/.exec(id);
  if (parameterMatch) return { x: 90, y: 245 + Number(parameterMatch[1]) * 42, w: 210, h: 34 };
  if (id.startsWith('parameter-')) {
    const fieldIndex = canvasParameterFields.findIndex((item) => `parameter-${item.id}` === id);
    return { x: 90, y: 245 + Math.max(0, fieldIndex) * 42, w: 210, h: 34 };
  }
  return { x: 64, y: 64, w: 300, h: 60 };
}
function canvasPosition(id) {
  if (!canvasPositions[id]) canvasPositions[id] = defaultCanvasPosition(id);
  return canvasPositions[id];
}
function applyCanvasPosition(id, element) {
  const position = canvasPosition(id);
  element.style.left = `${position.x}px`; element.style.top = `${position.y}px`;
  element.style.width = `${position.w}px`; element.style.height = `${position.h}px`;
}
function setCanvasZoom(value) {
  canvasZoom = Math.min(2.5, Math.max(0.35, value));
  const scale = $('#canvas-scale');
  scale.style.transform = 'none';
  scale.style.width = `${CANVAS_PAGE.width * canvasZoom}px`;
  scale.style.height = `${CANVAS_PAGE.height * canvasZoom}px`;
  $('#report-canvas').style.transform = `scale(${canvasZoom})`;
  $('#canvas-zoom-level').value = `${Math.round(canvasZoom * 100)}%`;
  $('#canvas-zoom-level').textContent = `${Math.round(canvasZoom * 100)}%`;
}
function refreshCanvasParameterChoices() {
  const select = $('#canvas-parameter-choice');
  if (!select) return;
  const current = select.value;
  const parameters = collectParameters();
  setSelectOptions(select, parameters.map((item) => ({ value: item.name, label: item.label || item.name })), current || parameters[0]?.name || '', parameters.length ? '' : 'Add report questions first');
  $('#add-canvas-parameter').disabled = !parameters.length;
}
function selectCanvasObject(id, element) {
  selectedCanvasObject = id;
  document.querySelectorAll('.canvas-object.is-selected,.canvas-parameter-field.is-selected').forEach((item) => item.classList.remove('is-selected'));
  if (element) element.classList.add('is-selected');
  const parameter = canvasParameterFields.find((item) => `parameter-${item.id}` === id);
  const labels = { title: 'Report heading', intro: 'Introduction', image: 'Image', table: 'Data table' };
  $('#canvas-selected-name').textContent = parameter ? `URL value · ${parameter.name}` : labels[id] || (id.startsWith('paragraph-') ? `Text block ${Number(id.slice(10)) + 1}` : 'Canvas object');
  $('#delete-canvas-parameter').disabled = !parameter;
  const position = canvasPosition(id);
  for (const [field, key] of [['canvas-x', 'x'], ['canvas-y', 'y'], ['canvas-width', 'w'], ['canvas-height', 'h']]) {
    const input = $(`#${field}`); input.value = position[key]; input.disabled = false;
  }
}
function updateCanvasObjectPosition(id, key, value) {
  const position = canvasPosition(id);
  const pageSize = key === 'x' || key === 'w' ? CANVAS_PAGE.width : CANVAS_PAGE.height;
  const sizeKey = key === 'x' ? 'w' : key === 'y' ? 'h' : key;
  const max = key === 'w' ? CANVAS_PAGE.width - position.x
    : key === 'h' ? CANVAS_PAGE.height - position.y
      : pageSize - position[sizeKey];
  position[key] = Math.min(max, Math.max(key === 'w' || key === 'h' ? 24 : 0, Math.round(value)));
  const element = document.querySelector(`[data-canvas-object="${CSS.escape(id)}"]`);
  if (element) applyCanvasPosition(id, element);
  for (const [field, property] of [['canvas-x', 'x'], ['canvas-y', 'y'], ['canvas-width', 'w'], ['canvas-height', 'h']]) $(`#${field}`).value = position[property];
}
function canvasParameterElement(field) {
  const element = document.createElement('div');
  const id = `parameter-${field.id}`;
  element.className = 'canvas-parameter-field canvas-object'; element.dataset.canvasObject = id;
  element.textContent = `{{${field.name}}}`; element.setAttribute('aria-label', `URL parameter ${field.name}`);
  applyCanvasPosition(id, element);
  if (selectedCanvasObject === id) element.classList.add('is-selected');
  return element;
}
function syncQueryHighlighter() {
  const editor = $('#report-pipeline'), highlight = $('#query-highlight');
  if (!editor || !highlight) return;
  const source = editor.value;
  const host = editor.closest('.syntax-editor');
  let lineNumbers = $('#query-line-numbers');
  if (!lineNumbers && host) {
    lineNumbers = document.createElement('pre');
    lineNumbers.id = 'query-line-numbers';
    lineNumbers.className = 'syntax-line-numbers';
    lineNumbers.setAttribute('aria-hidden', 'true');
    host.prepend(lineNumbers);
  }
  const tokens = /\/\/[^\n]*|\/\*[\s\S]*?\*\/|#[^\n]*|\{\s*["']\$param["']\s*:\s*["'][A-Za-z_][A-Za-z0-9_]*["'](?:\s*,\s*["']boundary["']\s*:\s*["'](?:start|end)["'])?\s*\}|\$P!?\{[A-Za-z_][A-Za-z0-9_]*\}|\$V\{[A-Za-z_][A-Za-z0-9_]*\}|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|(?:["'][^"'\n]+["']|[A-Za-z_$][\w$]*)\s*(?=:)|\$[A-Za-z_][\w]*|\b(?:const|let|var|pipeline|True|False|None|null|true|false|NumberInt|NumberLong|ISODate|ObjectId)\b|\b\d+(?:\.\d+)?\b/g;
  let result = '', last = 0, match;
  while ((match = tokens.exec(source))) {
    result += escapeHtml(source.slice(last, match.index));
    const token = match[0];
    let type = 'plain';
    const keyFollows = source.slice(tokens.lastIndex).trimStart().startsWith(':');
    if (/^(?:\/\/|\/\*|#)/.test(token)) type = 'comment';
    else if (/^\$V\{/.test(token)) type = 'variable';
    else if (/^(?:\{\s*["']\$param|\$P!?\{)/.test(token)) type = 'parameter';
    else if (/^\$/.test(token)) type = 'operator';
    else if (keyFollows) type = 'key';
    else if (/^(?:"|')/.test(token)) type = 'string';
    else if (/^(?:const|let|var|pipeline|True|False|None|null|true|false|NumberInt|NumberLong|ISODate|ObjectId)$/.test(token)) type = 'keyword';
    else if (/^\d/.test(token)) type = 'number';
    result += `<span class="syntax-${type}">${escapeHtml(token)}</span>`;
    last = tokens.lastIndex;
  }
  result += escapeHtml(source.slice(last));
  if (source.endsWith('\n')) result += ' ';
  highlight.innerHTML = result;
  highlight.scrollTop = editor.scrollTop;
  highlight.scrollLeft = editor.scrollLeft;
  if (lineNumbers) {
    lineNumbers.textContent = Array.from({ length: Math.max(1, source.split('\n').length) }, (_, index) => String(index + 1)).join('\n');
    lineNumbers.scrollTop = editor.scrollTop;
  }
}
function parseJsonField(selector, label, allowBlank = false) {
  const text = $(selector).value.trim();
  if (!text && allowBlank) return undefined;
  try { return JSON.parse(text); } catch (error) { throw new Error(`${label}: ${error.message}`); }
}
function toParameterName(label) {
  const words = String(label || 'Parameter').match(/[A-Za-z0-9]+/g) || ['Parameter'];
  const name = words.map((word) => word[0].toUpperCase() + word.slice(1)).join('');
  return /^[A-Za-z_]/.test(name) ? name.slice(0, 64) : `P${name}`.slice(0, 64);
}
function toReportId(title) {
  const words = String(title || 'Report').match(/[A-Za-z0-9]+/g) || ['Report'];
  return words.join('_').toUpperCase().slice(0, 80);
}
function toConnectionId(name) {
  const id = String(name || '').toLowerCase().replace(/[^a-z0-9_-]+/g, '_').replace(/^[_-]+|[_-]+$/g, '').slice(0, 40);
  return id || 'mongo_source';
}
function friendlyDefaultReport() {
  return {
    id: '', title: '', description: '', folder: activeFolder || 'General',
    connectionId: connections[0]?.id || 'default', collection: '', parameters: [],
    pipeline: [{ $project: { _id: 0 } }], queryLanguage: 'aggregation-json', columns: [], layout: { title: '', intro: '', columnLabels: {} },
  };
}
function setSelectOptions(select, values, selectedValue, emptyLabel = '') {
  select.replaceChildren();
  if (emptyLabel) {
    const empty = document.createElement('option'); empty.value = ''; empty.textContent = emptyLabel; select.append(empty);
  }
  for (const value of values) {
    const option = document.createElement('option');
    option.value = value.value;
    option.textContent = value.label;
    select.append(option);
  }
  if (selectedValue && [...select.options].some((option) => option.value === selectedValue)) select.value = selectedValue;
}
function renderFolderSelects(selectedFolder = $('#report-folder').value) {
  const reportFolder = $('#report-folder');
  const current = selectedFolder || 'General';
  setSelectOptions(reportFolder, folders.map((folder) => ({ value: folder, label: folder.replaceAll('/', '  /  ') })), current);
  const parentSelect = $('#folder-parent');
  setSelectOptions(parentSelect, folders.map((folder) => ({ value: folder, label: folder.replaceAll('/', '  /  ') })), '', 'Top level');
}
function renderConnectionChoices(selected = $('#report-connection').value) {
  const select = $('#report-connection');
  setSelectOptions(select, connections.map((connection) => ({
    value: connection.id,
    label: `${connection.name}${connection.configured ? '' : ' · needs setup'}`,
  })), selected || connections[0]?.id || 'default');
}
function renderImportConnectionChoices(selected = $('#import-connection').value) {
  setSelectOptions($('#import-connection'), connections.map((connection) => ({ value: connection.id, label: connection.name })), selected || connections[0]?.id || '', connections.length ? '' : 'Add a MongoDB data source first');
}
function selectedReportFormats() {
  return [...document.querySelectorAll('input[name="report-format"]:checked')].map((input) => input.value);
}
function renderDefaultFormatOptions(preferred = $('#default-report-format').value) {
  let formats = selectedReportFormats();
  if (!formats.length) {
    const csv = document.querySelector('input[name="report-format"][value="csv"]');
    if (csv) csv.checked = true;
    formats = selectedReportFormats();
  }
  const labels = { csv: 'CSV', xlsx: 'Excel (.xlsx)', pdf: 'PDF' };
  setSelectOptions($('#default-report-format'), formats.map((value) => ({ value, label: labels[value] || value.toUpperCase() })), formats.includes(preferred) ? preferred : formats[0]);
}
function setReportFormats(report = {}) {
  const formats = Array.isArray(report.formats) && report.formats.length ? report.formats : ['csv', 'xlsx', 'pdf'];
  for (const input of document.querySelectorAll('input[name="report-format"]')) input.checked = formats.includes(input.value);
  renderDefaultFormatOptions(report.defaultFormat || 'csv');
}
function makeField(labelText, input, hint = '') {
  const field = document.createElement('div'); field.className = 'field';
  const label = document.createElement('label'); label.textContent = labelText;
  field.append(label, input);
  if (hint) { const help = document.createElement('span'); help.className = 'help-text'; help.textContent = hint; field.append(help); }
  return field;
}
function renderParameters(parameters = []) {
  const list = $('#parameter-list');
  list.replaceChildren();
  if (!parameters.length) {
    const note = document.createElement('div'); note.className = 'empty-state';
    note.textContent = 'Is report ke liye abhi user se koi jawab nahi manga ja raha.';
    list.append(note);
  }
  for (const parameter of parameters) {
    const card = document.createElement('article');
    card.className = 'parameter-card';
    card.dataset.allowedValues = pretty(parameter.allowedValues || []);
    card.dataset.maxLength = String(parameter.maxLength || 200);

    const firstRow = document.createElement('div'); firstRow.className = 'parameter-main-row';
    const labelInput = document.createElement('input');
    labelInput.className = 'parameter-label'; labelInput.dataset.field = 'label';
    labelInput.placeholder = 'e.g. Start date'; labelInput.value = parameter.label || parameter.name || '';
    firstRow.append(makeField('Question shown to the user', labelInput, 'Yeh naam report form mein nazar aayega.'));

    const typeSelect = document.createElement('select'); typeSelect.dataset.field = 'type';
    for (const [value, labelText] of types) {
      const option = document.createElement('option'); option.value = value; option.textContent = labelText; typeSelect.append(option);
    }
    typeSelect.value = parameter.type || 'string';
    firstRow.append(makeField('Answer format', typeSelect));

    const requiredLabel = document.createElement('label'); requiredLabel.className = 'required-toggle';
    const required = document.createElement('input'); required.type = 'checkbox'; required.dataset.field = 'required'; required.checked = Boolean(parameter.required);
    const requiredText = document.createElement('span'); requiredText.textContent = 'Answer required';
    requiredLabel.append(required, requiredText);
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'button quiet mini remove-parameter'; remove.textContent = 'Remove question';
    firstRow.append(requiredLabel, remove);
    card.append(firstRow);

    const details = document.createElement('details'); details.className = 'parameter-extra';
    const summary = document.createElement('summary'); summary.textContent = 'Website link and default answer'; details.append(summary);
    const extraGrid = document.createElement('div'); extraGrid.className = 'form-grid two-col parameter-extra-grid';
    const nameInput = document.createElement('input'); nameInput.dataset.field = 'name'; nameInput.value = parameter.name || toParameterName(parameter.label || 'Parameter'); nameInput.maxLength = 64;
    if (parameter.name && parameter.name !== toParameterName(parameter.label || 'Parameter')) nameInput.dataset.edited = 'true';
    extraGrid.append(makeField('URL key', nameInput, 'Website links use this key, for example StartDate=2025-10-27.'));
    const defaultInput = document.createElement('input'); defaultInput.dataset.field = 'default'; defaultInput.value = parameter.default ?? '';
    defaultInput.placeholder = parameter.type === 'date' ? 'YYYY-MM-DD' : 'No default';
    if (parameter.type === 'date') defaultInput.type = 'date';
    extraGrid.append(makeField('Default answer', defaultInput));
    const descriptionInput = document.createElement('input'); descriptionInput.dataset.field = 'description'; descriptionInput.value = parameter.description || '';
    extraGrid.append(makeField('Help shown under the question', descriptionInput));
    details.append(extraGrid); card.append(details);

    let previousName = parameter.name || toParameterName(parameter.label || 'Parameter');
    const renameCanvasParameterFields = (nextName) => {
      if (previousName !== nextName) canvasParameterFields.forEach((field) => { if (field.name === previousName) field.name = nextName; });
      previousName = nextName;
    };
    labelInput.addEventListener('input', () => {
      if (!nameInput.dataset.edited) { nameInput.value = toParameterName(labelInput.value); renameCanvasParameterFields(nameInput.value); }
      renderPreviewFields(); renderQueryParameterShortcuts();
    });
    nameInput.addEventListener('input', () => { nameInput.dataset.edited = 'true'; renameCanvasParameterFields(nameInput.value.trim() || toParameterName(labelInput.value)); renderPreviewFields(); renderQueryParameterShortcuts(); renderCanvas(); });
    typeSelect.addEventListener('change', () => {
      if (typeSelect.value === 'date') { defaultInput.type = 'date'; defaultInput.placeholder = 'YYYY-MM-DD'; }
      else { defaultInput.type = 'text'; defaultInput.placeholder = 'No default'; }
      renderPreviewFields(); renderQueryParameterShortcuts();
    });
    required.addEventListener('change', renderPreviewFields);
    remove.addEventListener('click', () => { card.remove(); renderParameters(collectParameters()); });
    list.append(card);
  }
  renderParameterEmptyState();
  renderPreviewFields();
  renderQueryParameterShortcuts();
  const parameterNames = new Set(parameters.map((item) => item.name));
  canvasParameterFields = canvasParameterFields.filter((item) => parameterNames.has(item.name));
  refreshCanvasParameterChoices(); renderCanvas();
}
function renderParameterEmptyState() {
  const list = $('#parameter-list');
  const empty = list.querySelector('.empty-state');
  if (list.querySelector('.parameter-card') && empty) empty.remove();
  if (!list.querySelector('.parameter-card') && !empty) {
    const note = document.createElement('div'); note.className = 'empty-state';
    note.textContent = 'Is report ke liye abhi user se koi jawab nahi manga ja raha.';
    list.append(note);
  }
}
function collectParameters() {
  const usedNames = new Set();
  return [...document.querySelectorAll('#parameter-list .parameter-card')].map((card, index) => {
    const get = (field) => card.querySelector(`[data-field="${field}"]`);
    const label = get('label').value.trim();
    const baseName = get('name').value.trim() || toParameterName(label || `Question ${index + 1}`);
    let name = baseName.slice(0, 64);
    let suffix = 2;
    while (usedNames.has(name)) { name = `${baseName.slice(0, 60)}${suffix}`; suffix += 1; }
    usedNames.add(name);
    const type = get('type').value;
    const result = {
      name, type, label: label || name,
      description: get('description').value.trim(),
      required: get('required').checked,
      maxLength: Number(card.dataset.maxLength || 200),
    };
    const defaultValue = get('default').value.trim();
    if (defaultValue) result.default = defaultValue;
    try {
      const allowedValues = JSON.parse(card.dataset.allowedValues || '[]');
      if (allowedValues.length) result.allowedValues = allowedValues;
    } catch (_) { /* ignore invalid old metadata */ }
    return result;
  });
}
function renderPreviewFields() {
  const host = $('#preview-fields');
  if (!host) return;
  host.replaceChildren();
  let parameters = [];
  try { parameters = collectParameters(); } catch (_) { /* partial form while editing */ }
  for (const parameter of parameters) {
    const wrapper = document.createElement('div'); wrapper.className = 'field';
    const label = document.createElement('label'); label.textContent = `${parameter.label}${parameter.required ? ' *' : ''}`;
    const input = document.createElement('input'); input.dataset.previewName = parameter.name;
    input.type = parameter.type === 'date' ? 'date' : 'text';
    if (parameter.type === 'integer' || parameter.type === 'number') input.type = 'number';
    if (parameter.type === 'integer') input.step = '1';
    if (parameter.type === 'number') input.step = 'any';
    if (parameter.type === 'boolean') { input.type = 'checkbox'; input.value = 'true'; }
    if (input.type === 'checkbox') input.checked = parameter.default === true || parameter.default === 'true';
    else if (parameter.default !== undefined) input.value = String(parameter.default);
    wrapper.append(label, input); host.append(wrapper);
  }
  $('#preview-hint').textContent = parameters.length ? 'These answers are used for the small preview query.' : 'No report questions are configured.';
}
function previewValues() {
  const values = {};
  for (const input of document.querySelectorAll('[data-preview-name]')) {
    if (input.type === 'checkbox') {
      values[input.dataset.previewName] = input.checked ? 'true' : 'false';
    } else if (input.value !== '') values[input.dataset.previewName] = input.value;
  }
  return values;
}
function setReportEditor(report) {
  currentReportId = report.id || null;
  $('#editor-heading').textContent = report.id ? report.title || report.id : 'New report';
  $('#editor-subtitle').textContent = report.id ? `Saved in ${report.folder || 'General'}` : 'Set up the questions first. Database details can be added under Advanced.';
  $('#report-id').value = report.id || '';
  $('#report-title-input').value = report.title || '';
  $('#report-description-input').value = report.description || '';
  $('#layout-title').value = report.layout?.title || report.title || '';
  $('#layout-intro').value = report.layout?.intro ?? report.description ?? '';
  $('#show-layout-title').checked = report.layout?.showTitle !== false;
  $('#show-layout-intro').checked = report.layout?.showIntro !== false;
  canvasParagraphs = [...(report.layout?.paragraphs || [])];
  canvasImageDataUrl = report.layout?.imageDataUrl || '';
  canvasPositions = { ...(report.layout?.positions || {}) };
  canvasParameterFields = Array.isArray(report.layout?.parameterFields) ? report.layout.parameterFields.map((item, index) => ({ id: String(item.id || `field-${index + 1}`), name: item.name })) : [];
  if (Array.isArray(report.layout?.parameterFields)) report.layout.parameterFields.forEach((item, index) => {
    const id = String(item.id || `field-${index + 1}`);
    if (item.position) canvasPositions[`parameter-${id}`] = { ...item.position };
  });
  selectedCanvasObject = '';
  $('#remove-canvas-image').hidden = !canvasImageDataUrl;
  $('#canvas-image-file').value = '';
  setReportFormats(report);
  $('#report-collection').value = report.collection || '';
  queryLanguage = report.queryLanguage || 'aggregation-json';
  $('#query-language').value = queryLanguage;
  lastParsedPipeline = Array.isArray(report.pipeline) ? report.pipeline : [{ $project: { _id: 0 } }];
  const source = report.querySource || formatPipelineForLanguage(lastParsedPipeline, queryLanguage, report.collection || '');
  querySourcesByLanguage = { [queryLanguage]: source };
  validQueryLanguages = { [queryLanguage]: true };
  queryDirty = false;
  $('#report-pipeline').value = source;
  syncQueryHighlighter();
  $('#report-columns').value = report.columns?.length ? pretty(report.columns) : '';
  columnLabelDraft = { ...(report.layout?.columnLabels || {}) };
  $('#date-range-field').value = report.dateRange?.field || '';
  renderFolderSelects(report.folder || activeFolder || 'General');
  renderConnectionChoices(report.connectionId || 'default');
  renderParameters(report.parameters || []);
  renderParagraphEditor(); renderQuerySummary(); renderColumnLabels(); renderQueryParameterShortcuts(); refreshCanvasParameterChoices(); setCanvasZoom(canvasZoom);
  $('#delete-report').disabled = !report.id;
  $('#preview-report').disabled = !report.id;
  $('#export-report').disabled = !report.id;
  $('#preview-result').hidden = true;
  inline('save-report-message', '');
}
function renderFolderTree() {
  const tree = $('#folder-tree'); tree.replaceChildren();
  const entries = [{ path: '', label: 'All reports', level: 0 }, ...folders.map((path) => ({ path, label: path.split('/').at(-1), level: path.split('/').length }))];
  for (const entry of entries) {
    const row = document.createElement('div'); row.className = 'folder-row';
    const button = document.createElement('button'); button.type = 'button';
    button.className = `folder-item${activeFolder === entry.path ? ' selected' : ''}`;
    button.style.setProperty('--folder-level', String(entry.level));
    const icon = document.createElement('span'); icon.className = 'folder-icon'; icon.textContent = entry.path ? '▰' : '▦';
    const label = document.createElement('span'); label.textContent = entry.label;
    const count = reports.filter((report) => entry.path ? report.folder === entry.path : true).length;
    const amount = document.createElement('small'); amount.textContent = String(count);
    button.append(icon, label, amount);
    button.addEventListener('click', () => { activeFolder = entry.path; renderFolderTree(); renderReportList(); });
    row.append(button);
    if (entry.path && entry.path !== 'General') {
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'folder-delete'; remove.textContent = '×';
      remove.title = `Delete folder ${entry.path}`; remove.setAttribute('aria-label', `Delete folder ${entry.path}`);
      remove.addEventListener('click', async (event) => {
        event.stopPropagation();
        if (!window.confirm(`Delete empty folder “${entry.path}”? Reports aur subfolders ko pehle move ya delete karna hoga.`)) return;
        try {
          await api('/api/admin/folders', { method: 'DELETE', body: JSON.stringify({ path: entry.path }) });
          if (activeFolder === entry.path) activeFolder = '';
          await loadFolders(); await loadReports();
          notice(`Folder “${entry.path}” deleted.`, 'success');
        } catch (error) { notice(error.message, 'error'); }
      });
      row.append(remove);
    }
    tree.append(row);
  }
  renderFolderSelects();
}
function renderReportList() {
  const visible = reports.filter((report) => !activeFolder || (report.folder || 'General') === activeFolder);
  if (!visible.length) {
    reportList.innerHTML = '<div class="empty-state">No reports in this folder yet. Choose “New report” to add one.</div>';
    return;
  }
  reportList.replaceChildren(...visible.map((report) => {
    const button = document.createElement('button'); button.type = 'button';
    button.className = `report-item admin-report-item${report.id === currentReportId ? ' selected' : ''}`;
    const title = document.createElement('strong'); title.textContent = report.title || report.id;
    const info = document.createElement('small'); info.textContent = report.description || `${report.parameters?.length || 0} question(s)`;
    button.append(title, info);
    button.addEventListener('click', () => { setReportEditor(report); renderReportList(); });
    return button;
  }));
}
function renderConnections() {
  const container = $('#connection-list');
  renderConnectionChoices();
  if (!connections.length) { container.innerHTML = '<div class="empty-state">No data sources configured.</div>'; return; }
  container.replaceChildren(...connections.map((connection) => {
    const card = document.createElement('div'); card.className = 'connection-card';
    const details = document.createElement('div');
    const title = document.createElement('strong'); title.textContent = `${connection.name} · ${connection.id}`;
    const note = document.createElement('small');
    note.textContent = `${connection.database || 'Database not set'} · ${connection.credentialSource || 'Private local file'} · ${connection.configured ? 'ready to test' : 'setup needed'}`;
    const result = document.createElement('small'); result.className = 'connection-result'; result.setAttribute('aria-live', 'polite');
    details.append(title, note, result);
    const actions = document.createElement('div'); actions.className = 'connection-actions';
    const test = document.createElement('button'); test.className = 'button secondary mini'; test.textContent = 'Test connection';
    test.addEventListener('click', async () => {
      test.disabled = true; test.textContent = 'Testing…'; result.textContent = 'MongoDB server aur report read access check ho raha hai…'; result.classList.remove('error');
      try {
        const checked = await api(`/api/admin/connections/${encodeURIComponent(connection.id)}/test`, { method: 'POST' });
        const identity = checked.identityReported
          ? (checked.authenticatedUsers?.length ? ` · as ${checked.authenticatedUsers.join(', ')}` : ' · no authenticated user reported')
          : '';
        const databaseWarning = checked.databaseWarning ? ` · ${checked.databaseWarning}` : '';
        result.textContent = checked.readChecked?.length
          ? `Connected · report read verified: ${checked.readChecked.join(', ')}${identity}${databaseWarning}`
          : `${checked.message}${identity}${databaseWarning}`;
        notice(`${checked.message}${databaseWarning}`, 'success');
      } catch (error) { result.textContent = error.message; result.classList.add('error'); notice(error.message, 'error'); }
      finally { test.disabled = false; test.textContent = 'Test connection'; }
    }); actions.append(test);
    {
      const edit = document.createElement('button'); edit.className = 'button quiet mini'; edit.textContent = 'Update URL';
      edit.addEventListener('click', () => {
        $('#connection-id').value = connection.id;
        $('#connection-name').value = connection.name;
        $('#connection-database').value = connection.database || '';
        $('#connection-uri').value = '';
        $('#save-connection-message').textContent = 'Naya URL dein; purana URL replace hoga.';
        document.querySelector('[data-tab="connections"]').click();
        $('#connection-uri').focus();
      });
      actions.append(edit);
    }
    if (connection.id !== 'default') {
      const remove = document.createElement('button'); remove.className = 'button danger mini'; remove.textContent = 'Delete';
      remove.addEventListener('click', async () => {
        if (!window.confirm(`Delete data source “${connection.name}”?`)) return;
        try { await api(`/api/admin/connections/${encodeURIComponent(connection.id)}`, { method: 'DELETE' }); await loadConnections(); notice('Data source removed.', 'success'); }
        catch (error) { notice(error.message, 'error'); }
      }); actions.append(remove);
    }
    card.append(details, actions); return card;
  }));
}
async function loadReports() {
  const data = await api('/api/admin/reports'); reports = data.reports || [];
  renderFolderTree();
  const selected = reports.find((report) => report.id === currentReportId);
  if (selected) setReportEditor(selected);
  else if (!currentReportId && reports.length) setReportEditor(reports[0]);
  renderReportList();
}
async function loadFolders() {
  const data = await api('/api/admin/folders'); folders = data.folders || ['General'];
  if (!folders.includes('General')) folders.unshift('General');
  renderFolderTree();
}
async function loadConnections() {
  const data = await api('/api/admin/connections'); connections = data.connections || []; renderConnections(); renderImportConnectionChoices();
}
async function loadAll() {
  setAdminConnected(false);
  if (!token) { notice('Admin token available nahi. Local setup auto-connect karein ya deployed server ka token dein.', 'error'); return; }
  $('#save-token').disabled = true;
  $('#save-token').textContent = 'Connecting…';
  try {
    await Promise.all([loadConnections(), loadFolders(), loadReports()]);
    setAdminConnected(true);
    notice('Reports workspace is ready.', 'success');
  } catch (error) {
    if (error.status === 401 || error.status === 503) {
      $('#token-entry').hidden = false;
      $('#admin-auth-state').textContent = 'Token required';
      $('#admin-access-description').textContent = 'Deployed server par REPORTING_ADMIN_TOKEN environment setting dein.';
      if (error.status === 401) sessionStorage.removeItem(tokenKey);
    }
    notice(`Admin API connect nahi hui: ${error.message}`, 'error');
  } finally {
    $('#save-token').disabled = false;
    $('#save-token').textContent = 'Connect';
  }
}
function folderPathFromDialog() {
  const name = $('#folder-name').value.trim();
  const parent = $('#folder-parent').value;
  if (!/^[A-Za-z0-9][A-Za-z0-9 _-]{0,63}$/.test(name)) throw new Error('Folder name ko letters, numbers, spaces, underscore ya hyphen mein likhein.');
  return parent ? `${parent}/${name}` : name;
}
async function exportReportDefinition() {
  if (!currentReportId) return;
  const response = await fetch(`/api/admin/reports/${encodeURIComponent(currentReportId)}/definition.xml`, { headers: headers() });
  if (!response.ok) {
    let detail = `Export failed (${response.status})`;
    try { detail = (await response.json()).detail || detail; } catch (_) { /* response is not JSON */ }
    throw new Error(detail);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a'); link.href = url; link.download = `${currentReportId}.report.xml`; link.click();
  URL.revokeObjectURL(url);
}
async function prepareReportImport(file) {
  const error = $('#import-report-error'); error.hidden = true;
  if (!file || file.size > 5_000_000) throw new Error('Report definition XML file 5 MB se chhoti honi chahiye.');
  const xml = await file.text();
  const documentXml = new DOMParser().parseFromString(xml, 'application/xml');
  if (documentXml.querySelector('parsererror')) throw new Error('XML file ka format ghalat hai. Exported report XML choose karein.');
  const payloadText = documentXml.querySelector('mongoReportDefinition > definition[encoding="json"]')?.textContent;
  if (!payloadText) throw new Error('Is XML file mein Mongo report definition nahi mili.');
  const definition = JSON.parse(payloadText);
  if (!definition || !definition.id || !definition.title || !Array.isArray(definition.pipeline)) throw new Error('Report file mein zaroori report settings maujood nahi.');
  pendingImportXml = xml; pendingImportDefinition = definition;
  $('#import-report-summary').textContent = `${definition.title} · ${definition.id} · ${(definition.parameters || []).length} parameter(s) · ${(definition.formats || []).join(', ') || 'default formats'}`;
  $('#import-folder').value = definition.folder || 'General';
  renderImportConnectionChoices(connections.some((item) => item.id === definition.connectionId) ? definition.connectionId : connections[0]?.id || '');
  $('#import-report-dialog').showModal();
}
function makeReportPayload() {
  if (queryDirty) throw new Error('Query editor khol kar pehle “Apply query” dabayein, phir report save karein.');
  const title = $('#report-title-input').value.trim();
  const parameters = collectParameters();
  const dateParameters = parameters.filter((parameter) => parameter.type === 'date');
  const dateField = $('#date-range-field').value.trim();
  const pipeline = lastParsedPipeline;
  const querySource = $('#report-pipeline').value;
  const columns = parseJsonField('#report-columns', 'Output columns', true);
  const formats = selectedReportFormats();
  if (!formats.length) throw new Error('Report ke liye CSV, Excel ya PDF mein se kam az kam ek format chunein.');
  const designColumns = columns || detectedOutputColumns();
  const columnLabels = Object.fromEntries(designColumns.map((column) => [column, columnLabelDraft[column] || column]));
  const idInput = $('#report-id').value.trim();
  const id = idInput || toReportId(title);
  if (!$('#report-collection').value.trim()) throw new Error('Data and query setup khol kar MongoDB collection ka naam dein.');
  if (!$('#report-connection').value) throw new Error('Data sources tab mein pehle MongoDB connection add karein.');
  if (!Array.isArray(pipeline) || !pipeline.length) throw new Error('Advanced query section mein aggregation stages dein.');
  return {
    id, title, description: $('#report-description-input').value.trim(),
    layout: {
      title: $('#layout-title').value.trim() || title, intro: $('#layout-intro').value.trim(),
      showTitle: $('#show-layout-title').checked, showIntro: $('#show-layout-intro').checked,
      paragraphs: canvasParagraphs, imageDataUrl: canvasImageDataUrl, columnLabels,
      positions: Object.fromEntries(Object.entries(canvasPositions).filter(([key]) => !key.startsWith('parameter-'))),
      parameterFields: canvasParameterFields.map((item) => ({ ...item, position: canvasPosition(`parameter-${item.id}`) })),
    },
    formats, defaultFormat: $('#default-report-format').value,
    queryLanguage, querySource,
    folder: $('#report-folder').value || 'General',
    connectionId: $('#report-connection').value,
    collection: $('#report-collection').value.trim(),
    parameters, pipeline,
    ...(dateParameters.length >= 2 && dateField ? { dateRange: { field: dateField, startParameter: dateParameters[0].name, endParameter: dateParameters[1].name } } : {}),
    ...(columns ? { columns } : {}),
  };
}
function renderPreviewResult(result) {
  const target = $('#preview-result'); target.hidden = false; target.replaceChildren();
  const reportTitle = document.createElement('h3'); reportTitle.textContent = result.layout?.title || 'Report preview'; target.append(reportTitle);
  if (result.layout?.intro) { const intro = document.createElement('p'); intro.className = 'muted'; intro.textContent = result.layout.intro; target.append(intro); }
  const count = document.createElement('p'); count.className = 'help-text'; count.textContent = `Preview · ${result.rowCount} row${result.rowCount === 1 ? '' : 's'} (maximum 20)`; target.append(count);
  if (!result.rows.length) { const empty = document.createElement('p'); empty.className = 'muted'; empty.textContent = 'Query chali, lekin in answers ke liye koi row nahi mili.'; target.append(empty); return; }
  const table = document.createElement('table'); table.className = 'preview-table';
  const thead = document.createElement('thead'); const headerRow = document.createElement('tr');
  for (const column of result.columns) { const th = document.createElement('th'); th.textContent = result.labels?.[column] || column; headerRow.append(th); }
  thead.append(headerRow); table.append(thead);
  const tbody = document.createElement('tbody');
  for (const row of result.rows) {
    const tr = document.createElement('tr');
    for (const column of result.columns) { const td = document.createElement('td'); const value = row[column]; td.textContent = value === null || value === undefined ? '' : (typeof value === 'object' ? JSON.stringify(value) : String(value)); tr.append(td); }
    tbody.append(tr);
  }
  table.append(tbody); target.append(table);
}

function detectedOutputColumns() {
  try {
    const custom = JSON.parse($('#report-columns').value.trim() || 'null');
    if (Array.isArray(custom) && custom.every((item) => typeof item === 'string')) return custom;
  } catch (_) { /* show a clean empty state while the JSON is being edited */ }
  try {
    const pipeline = lastParsedPipeline;
    if (!Array.isArray(pipeline)) return [];
    for (const stage of [...pipeline].reverse()) {
      if (!stage || typeof stage !== 'object') continue;
      if (stage.$project && typeof stage.$project === 'object') {
        return Object.keys(stage.$project).filter((key) => key !== '_id' || stage.$project[key] !== 0);
      }
      if (stage.$group && typeof stage.$group === 'object') return Object.keys(stage.$group);
      if (typeof stage.$count === 'string' && stage.$count) return [stage.$count];
    }
    return [];
  } catch (_) { return []; }
}
function renderCanvas() {
  const title = $('#layout-title').value.trim();
  const intro = $('#layout-intro').value.trim();
  $('#canvas-title').textContent = title || 'Your report heading';
  $('#canvas-title').hidden = !$('#show-layout-title').checked;
  applyCanvasPosition('title', $('#canvas-title'));
  $('#canvas-intro').textContent = intro || 'Your short introduction will appear here.';
  $('#canvas-intro').hidden = !intro || !$('#show-layout-intro').checked;
  applyCanvasPosition('intro', $('#canvas-intro'));
  const image = $('#canvas-image');
  image.hidden = !canvasImageDataUrl;
  if (canvasImageDataUrl) image.src = canvasImageDataUrl;
  applyCanvasPosition('image', image);
  const paragraphHost = $('#canvas-paragraphs'); paragraphHost.replaceChildren();
  canvasParagraphs.forEach((text, index) => {
    const paragraph = document.createElement('p'); paragraph.className = 'canvas-text-block canvas-object'; paragraph.dataset.canvasObject = `paragraph-${index}`; paragraph.textContent = text;
    applyCanvasPosition(`paragraph-${index}`, paragraph); if (selectedCanvasObject === `paragraph-${index}`) paragraph.classList.add('is-selected'); paragraphHost.append(paragraph);
  });
  const parameterHost = $('#canvas-parameter-fields'); parameterHost.replaceChildren();
  for (const field of canvasParameterFields) parameterHost.append(canvasParameterElement(field));
  const table = document.querySelector('.canvas-table-wrap'); applyCanvasPosition('table', table);
  const columns = detectedOutputColumns();
  const host = $('#canvas-columns'); host.replaceChildren();
  const sample = $('#canvas-sample-row'); sample.replaceChildren();
  if (!columns.length) {
    const heading = document.createElement('th'); heading.className = 'canvas-placeholder'; heading.textContent = 'Report columns will appear here.'; host.append(heading);
    const cell = document.createElement('td'); cell.className = 'canvas-placeholder'; cell.textContent = 'Sample report data'; sample.append(cell);
  } else {
    for (const column of columns) {
      const item = document.createElement('th'); item.textContent = columnLabelDraft[column] || column; host.append(item);
      const cell = document.createElement('td'); cell.textContent = 'Sample value'; sample.append(cell);
    }
  }
  const selected = document.querySelector(`[data-canvas-object="${CSS.escape(selectedCanvasObject)}"]`);
  if (selectedCanvasObject && selected) selectCanvasObject(selectedCanvasObject, selected);
}
function renderColumnLabels() {
  const host = $('#column-label-editor'); host.replaceChildren();
  const columns = detectedOutputColumns();
  if (!columns.length) {
    const empty = document.createElement('p'); empty.className = 'empty-state'; empty.textContent = 'Add a $project, $group or $count stage, or enter output columns in MongoDB data setup to edit their names.'; host.append(empty);
    renderCanvas(); return;
  }
  for (const column of columns) {
    const row = document.createElement('div'); row.className = 'column-label-row';
    const source = document.createElement('code'); source.textContent = column;
    const input = document.createElement('input'); input.type = 'text'; input.maxLength = 200; input.value = columnLabelDraft[column] || column; input.setAttribute('aria-label', `Display name for ${column}`);
    input.addEventListener('input', () => { columnLabelDraft[column] = input.value.trim() || column; renderCanvas(); });
    row.append(source, input); host.append(row);
  }
  renderCanvas();
}
function renderParagraphEditor() {
  const host = $('#paragraph-editor'); host.replaceChildren();
  canvasParagraphs.forEach((text, index) => {
    const row = document.createElement('div'); row.className = 'paragraph-editor-row';
    const textarea = document.createElement('textarea'); textarea.maxLength = 2000; textarea.rows = 2; textarea.value = text; textarea.setAttribute('aria-label', `Report text block ${index + 1}`);
    textarea.addEventListener('input', () => { canvasParagraphs[index] = textarea.value; renderCanvas(); });
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'button quiet mini'; remove.textContent = 'Remove text';
    remove.addEventListener('click', () => { canvasParagraphs.splice(index, 1); renderParagraphEditor(); renderCanvas(); });
    row.append(textarea, remove); host.append(row);
  });
}
function renderQuerySummary() {
  const summary = $('#query-summary');
  summary.textContent = queryDirty
    ? 'Query text has changes. Apply it in the query editor before saving.'
    : `${lastParsedPipeline.length} aggregation stage${lastParsedPipeline.length === 1 ? '' : 's'} configured · ${queryLanguage}`;
}
function renderQueryContext() {
  const modeRow = $('#query-language')?.closest('.query-mode-row');
  if (!modeRow) return;
  let context = $('#query-context');
  if (!context) {
    context = document.createElement('div');
    context.id = 'query-context';
    context.className = 'query-context';
    modeRow.before(context);
  }
  context.replaceChildren();
  const selectedConnection = connections.find((item) => item.id === $('#report-connection').value);
  const items = [
    ['Data adapter', selectedConnection?.name || 'Choose a data source'],
    ['Collection', $('#report-collection').value.trim() || 'Choose a collection'],
  ];
  for (const [label, value] of items) {
    const item = document.createElement('div'); item.className = 'query-context-item';
    const caption = document.createElement('span'); caption.textContent = label;
    const content = document.createElement('strong'); content.textContent = value;
    item.append(caption, content); context.append(item);
  }
}

function renderQueryParameterShortcuts() {
  const host = $('#query-parameter-shortcuts'); host.replaceChildren();
  const parameters = collectParameters();
  const insertLabel = document.createElement('span');
  insertLabel.className = 'query-shortcut-label';
  insertLabel.textContent = parameters.length ? 'Insert URL parameter:' : 'No URL parameters yet. Add a report question to use a URL value.';
  host.append(insertLabel);
  for (const parameter of parameters) {
    const addMarker = (buttonLabel, markerObject) => {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'query-parameter-chip'; button.textContent = buttonLabel;
      button.addEventListener('click', () => {
        const editor = $('#report-pipeline');
        const marker = JSON.stringify(markerObject);
        editor.setRangeText(marker, editor.selectionStart, editor.selectionEnd, 'end');
        editor.dispatchEvent(new Event('input', { bubbles: true }));
        editor.focus();
      });
      host.append(button);
    };
    if (parameter.type === 'date') {
      addMarker(`${parameter.name} · day start`, { $param: parameter.name, boundary: 'start' });
      addMarker(`${parameter.name} · day end`, { $param: parameter.name, boundary: 'end' });
    } else addMarker(parameter.name, { $param: parameter.name });
  }
  const legend = document.createElement('div');
  legend.className = 'query-syntax-legend';
  const legendItems = [
    ['Mongo query', 'syntax-query-chip'],
    ['$P{Parameter} · URL input', 'syntax-parameter-chip'],
    ['$V{Variable} · report value', 'syntax-variable-chip'],
    ['// comment', 'syntax-comment-chip'],
  ];
  for (const [text, className] of legendItems) {
    const item = document.createElement('span'); item.className = `syntax-legend-item ${className}`; item.textContent = text; legend.append(item);
  }
  host.append(legend);
  const variableNote = document.createElement('span');
  variableNote.className = 'query-variable-note';
  variableNote.textContent = 'URL parameters provide MongoDB query values. Report variables are a separate output-level feature.';
  host.append(variableNote);
}

function toggleQueryComment() {
  const editor = $('#report-pipeline'), value = editor.value;
  const start = editor.selectionStart, end = editor.selectionEnd;
  const lineStart = value.lastIndexOf('\n', Math.max(0, start - 1)) + 1;
  let lineEnd = value.indexOf('\n', end);
  if (lineEnd < 0) lineEnd = value.length;
  const prefix = queryLanguage === 'python' ? '#' : '//';
  const lines = value.slice(lineStart, lineEnd).split('\n');
  const contentLines = lines.filter((line) => line.trim());
  const remove = contentLines.length > 0 && contentLines.every((line) => line.trimStart().startsWith(prefix));
  const updated = lines.map((line) => {
    if (!line.trim()) return line;
    const indentation = line.match(/^\s*/)?.[0] || '';
    const body = line.slice(indentation.length);
    return remove ? `${indentation}${body.startsWith(prefix) ? body.slice(prefix.length).replace(/^ ?/, '') : body}` : `${indentation}${prefix} ${body}`;
  }).join('\n');
  editor.setRangeText(updated, lineStart, lineEnd, 'select');
  editor.dispatchEvent(new Event('input', { bubbles: true })); editor.focus();
}

async function initializeAdminAccess() {
  $('#admin-auth-state').textContent = 'Connecting…';
  try {
    const response = await fetch('/api/admin/bootstrap', { cache: 'no-store' });
    if (!response.ok) throw new Error(`Local auto-connect unavailable (${response.status})`);
    const result = await response.json();
    token = result.token || '';
    if (!token) throw new Error('Local token response empty hai');
    sessionStorage.setItem(tokenKey, token);
  } catch (_) {
    if (!token) {
      $('#token-entry').hidden = false;
      $('#admin-auth-state').textContent = 'Deployment token required';
      $('#admin-access-description').textContent = 'Local app token khud use karta hai. Deployed server par environment setting se access dein.';
      setAdminConnected(false);
      notice('Local auto-connect nahi hua. Agar yeh deployed server hai to uska admin token enter karke Connect karein.', 'error');
      return;
    }
  }
  await loadAll();
}

$('#save-token').addEventListener('click', () => {
  token = tokenInput.value.trim();
  if (token) sessionStorage.setItem(tokenKey, token); else sessionStorage.removeItem(tokenKey);
  loadAll();
});
$('#clear-token').addEventListener('click', () => {
  token = ''; tokenInput.value = ''; sessionStorage.removeItem(tokenKey); setAdminConnected(false);
  notice('Admin session reset ho rahi hai.'); initializeAdminAccess();
});
document.querySelectorAll('.tab').forEach((tab) => tab.addEventListener('click', () => {
  document.querySelectorAll('.tab').forEach((item) => { item.classList.toggle('active', item === tab); item.setAttribute('aria-selected', String(item === tab)); });
  document.querySelectorAll('.tab-panel').forEach((panel) => panel.classList.toggle('active', panel.id === `${tab.dataset.tab}-tab`));
}));
$('#new-report').addEventListener('click', () => { setReportEditor(friendlyDefaultReport()); renderReportList(); });
$('#export-report').addEventListener('click', async () => {
  try { await exportReportDefinition(); notice('Report definition XML download ho gayi.', 'success'); }
  catch (error) { notice(error.message, 'error'); }
});
$('#import-report').addEventListener('click', () => $('#import-report-file').click());
$('#import-report-file').addEventListener('change', async (event) => {
  const file = event.currentTarget.files?.[0];
  try { await prepareReportImport(file); }
  catch (error) { notice(error.message, 'error'); }
  finally { event.currentTarget.value = ''; }
});
$('#close-import-dialog').addEventListener('click', () => $('#import-report-dialog').close());
$('#cancel-import-report').addEventListener('click', () => $('#import-report-dialog').close());
$('#confirm-import-report').addEventListener('click', async () => {
  const errorBox = $('#import-report-error'); errorBox.hidden = true;
  const connectionId = $('#import-connection').value;
  if (!pendingImportXml || !pendingImportDefinition) { errorBox.textContent = 'Pehle report definition XML file choose karein.'; errorBox.hidden = false; return; }
  if (!connectionId) { errorBox.textContent = 'Pehle is server ka MongoDB data source add karein.'; errorBox.hidden = false; return; }
  const existing = reports.find((item) => item.id === pendingImportDefinition.id);
  const replace = existing ? window.confirm(`Report “${pendingImportDefinition.title}” already exists. Kya aap usay imported file se replace karna chahte hain?`) : false;
  if (existing && !replace) return;
  const button = $('#confirm-import-report'); button.disabled = true; button.textContent = 'Importing…';
  try {
    const result = await api('/api/admin/reports/import', { method: 'POST', body: JSON.stringify({ xml: pendingImportXml, connectionId, folder: $('#import-folder').value.trim(), replace }) });
    currentReportId = result.report.id; activeFolder = result.report.folder || 'General';
    await loadFolders(); await loadReports();
    $('#import-report-dialog').close(); pendingImportXml = ''; pendingImportDefinition = null;
    notice(`Report “${result.report.title}” query, parameters aur format settings ke saath import ho gayi.`, 'success');
  } catch (error) { errorBox.textContent = error.message; errorBox.hidden = false; }
  finally { button.disabled = false; button.textContent = 'Import report'; }
});
$('#add-parameter').addEventListener('click', () => {
  const existing = collectParameters();
  const nextIndex = existing.length + 1;
  existing.push({ name: `Question${nextIndex}`, label: `Question ${nextIndex}`, type: 'string', required: false });
  renderParameters(existing);
  const inputs = document.querySelectorAll('#parameter-list .parameter-label'); inputs[inputs.length - 1]?.focus();
});
$('#layout-title').addEventListener('input', renderCanvas);
$('#layout-intro').addEventListener('input', renderCanvas);
$('#show-layout-title').addEventListener('change', renderCanvas);
$('#show-layout-intro').addEventListener('change', renderCanvas);
document.querySelectorAll('input[name="report-format"]').forEach((input) => input.addEventListener('change', () => {
  if (!selectedReportFormats().length) input.checked = true;
  renderDefaultFormatOptions();
}));
$('#add-canvas-paragraph').addEventListener('click', () => {
  if (canvasParagraphs.length >= 20) { notice('Ek report mein maximum 20 text blocks ho sakte hain.', 'error'); return; }
  canvasParagraphs.push(''); renderParagraphEditor(); renderCanvas();
  document.querySelector('#paragraph-editor textarea:last-of-type')?.focus();
});
$('#add-canvas-image').addEventListener('click', () => $('#canvas-image-file').click());
$('#canvas-image-file').addEventListener('change', (event) => {
  const file = event.currentTarget.files?.[0];
  if (!file) return;
  if (!['image/png', 'image/jpeg'].includes(file.type) || file.size > 2_000_000) {
    notice('Image PNG ya JPEG ho aur 2 MB se chhoti ho.', 'error'); event.currentTarget.value = ''; return;
  }
  const reader = new FileReader();
  reader.onload = () => { canvasImageDataUrl = String(reader.result || ''); $('#remove-canvas-image').hidden = false; renderCanvas(); notice('Image report canvas mein add ho gayi.', 'success'); };
  reader.onerror = () => notice('Image file read nahi ho saki.', 'error');
  reader.readAsDataURL(file);
});
$('#remove-canvas-image').addEventListener('click', () => {
  canvasImageDataUrl = ''; $('#canvas-image-file').value = ''; $('#remove-canvas-image').hidden = true; renderCanvas();
});
$('#canvas-zoom-in').addEventListener('click', () => setCanvasZoom(canvasZoom + 0.1));
$('#canvas-zoom-out').addEventListener('click', () => setCanvasZoom(canvasZoom - 0.1));
$('#canvas-zoom-fit').addEventListener('click', () => setCanvasZoom(Math.min(1.8, ($('#canvas-scroll').clientWidth - 48) / CANVAS_PAGE.width)));
$('#canvas-scroll').addEventListener('wheel', (event) => {
  if (!event.ctrlKey) return;
  event.preventDefault(); setCanvasZoom(canvasZoom + (event.deltaY < 0 ? 0.08 : -0.08));
}, { passive: false });
window.addEventListener('keydown', (event) => {
  const activeTag = document.activeElement?.tagName;
  if (event.code === 'Space' && $('#reports-tab').classList.contains('active') && !['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON', 'A'].includes(activeTag) && !document.activeElement?.isContentEditable) {
    spaceHeld = true; $('#canvas-scroll').classList.add('space-pan'); event.preventDefault();
  }
});
window.addEventListener('keyup', (event) => {
  if (event.code === 'Space') { spaceHeld = false; $('#canvas-scroll').classList.remove('space-pan'); }
});
$('#canvas-scroll').addEventListener('pointerdown', (event) => {
  if (!spaceHeld && event.button !== 1) return;
  canvasPan = { x: event.clientX, y: event.clientY, left: event.currentTarget.scrollLeft, top: event.currentTarget.scrollTop };
  event.currentTarget.classList.add('is-panning'); event.preventDefault();
});
$('#report-canvas').addEventListener('pointerdown', (event) => {
  if (spaceHeld || event.button === 1) return;
  const element = event.target.closest('[data-canvas-object]');
  if (!element) return;
  event.preventDefault();
  const id = element.dataset.canvasObject;
  selectCanvasObject(id, element);
  const bounds = element.getBoundingClientRect();
  if (event.clientX >= bounds.right - 16 && event.clientY >= bounds.bottom - 16) {
    canvasResize = { id, startX: event.clientX, startY: event.clientY, ...canvasPosition(id) };
    element.setPointerCapture?.(event.pointerId);
    return;
  }
  canvasDrag = { id, startX: event.clientX, startY: event.clientY, ...canvasPosition(id) };
  element.setPointerCapture?.(event.pointerId);
});
document.addEventListener('pointermove', (event) => {
  if (canvasPan) {
    const scroll = $('#canvas-scroll');
    scroll.scrollLeft = canvasPan.left - (event.clientX - canvasPan.x);
    scroll.scrollTop = canvasPan.top - (event.clientY - canvasPan.y);
    return;
  }
  if (canvasResize) {
    const page = $('#report-canvas').getBoundingClientRect();
    const scale = page.width / CANVAS_PAGE.width || canvasZoom;
    updateCanvasObjectPosition(canvasResize.id, 'w', canvasResize.w + (event.clientX - canvasResize.startX) / scale);
    updateCanvasObjectPosition(canvasResize.id, 'h', canvasResize.h + (event.clientY - canvasResize.startY) / scale);
    return;
  }
  if (!canvasDrag) return;
  const page = $('#report-canvas').getBoundingClientRect();
  const scale = page.width / CANVAS_PAGE.width || canvasZoom;
  updateCanvasObjectPosition(canvasDrag.id, 'x', canvasDrag.x + (event.clientX - canvasDrag.startX) / scale);
  updateCanvasObjectPosition(canvasDrag.id, 'y', canvasDrag.y + (event.clientY - canvasDrag.startY) / scale);
});
document.addEventListener('pointerup', () => { canvasDrag = null; canvasResize = null; canvasPan = null; $('#canvas-scroll').classList.remove('is-panning'); });
for (const [field, key] of [['canvas-x', 'x'], ['canvas-y', 'y'], ['canvas-width', 'w'], ['canvas-height', 'h']]) {
  $(`#${field}`).addEventListener('input', (event) => {
    if (selectedCanvasObject && event.currentTarget.value !== '') updateCanvasObjectPosition(selectedCanvasObject, key, Number(event.currentTarget.value));
  });
}
$('#add-canvas-parameter').addEventListener('click', () => {
  const name = $('#canvas-parameter-choice').value;
  if (!name) { notice('Pehle report question add karein.', 'error'); return; }
  const id = `field-${Date.now()}-${canvasParameterFields.length + 1}`;
  canvasParameterFields.push({ id, name });
  const objectId = `parameter-${id}`; canvasPosition(objectId);
  renderCanvas();
  const element = document.querySelector(`[data-canvas-object="${CSS.escape(objectId)}"]`);
  selectCanvasObject(objectId, element);
  notice(`${name} ka value field canvas par add ho gaya. Isay image par rakhne ke liye drag karein.`, 'success');
});
$('#delete-canvas-parameter').addEventListener('click', () => {
  const fieldId = selectedCanvasObject.replace(/^parameter-/, '');
  const before = canvasParameterFields.length;
  canvasParameterFields = canvasParameterFields.filter((field) => field.id !== fieldId);
  if (canvasParameterFields.length === before) return;
  delete canvasPositions[selectedCanvasObject]; selectedCanvasObject = ''; renderCanvas();
  $('#canvas-selected-name').textContent = 'Select an item on the page';
  $('#delete-canvas-parameter').disabled = true;
  for (const id of ['canvas-x', 'canvas-y', 'canvas-width', 'canvas-height']) $(`#${id}`).disabled = true;
});
$('#report-pipeline').addEventListener('input', () => {
  querySourcesByLanguage[queryLanguage] = $('#report-pipeline').value;
  validQueryLanguages[queryLanguage] = false;
  queryDirty = true;
  syncQueryHighlighter(); renderQuerySummary();
});
$('#report-pipeline').addEventListener('scroll', syncQueryHighlighter);
$('#report-pipeline').addEventListener('keydown', (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === '/') { event.preventDefault(); toggleQueryComment(); return; }
  if (event.key === 'Tab') {
    event.preventDefault();
    event.currentTarget.setRangeText('  ', event.currentTarget.selectionStart, event.currentTarget.selectionEnd, 'end');
    event.currentTarget.dispatchEvent(new Event('input', { bubbles: true }));
  }
});
$('#query-language').addEventListener('change', () => {
  const editor = $('#report-pipeline');
  querySourcesByLanguage[queryLanguage] = editor.value;
  const nextLanguage = $('#query-language').value;
  let source = querySourcesByLanguage[nextLanguage];
  if (!source) {
    source = formatPipelineForLanguage(lastParsedPipeline, nextLanguage, $('#report-collection').value.trim());
    querySourcesByLanguage[nextLanguage] = source;
    validQueryLanguages[nextLanguage] = true;
  }
  queryLanguage = nextLanguage;
  editor.value = source;
  queryDirty = validQueryLanguages[queryLanguage] !== true;
  syncQueryHighlighter(); renderQuerySummary(); renderQueryParameterShortcuts();
});
$('#toggle-query-comment').addEventListener('click', toggleQueryComment);
$('#report-columns').addEventListener('input', renderColumnLabels);
$('#open-query-editor').addEventListener('click', () => {
  $('#query-editor-error').hidden = true;
  renderQueryContext();
  renderQueryParameterShortcuts();
  $('#query-dialog').showModal();
});
$('#close-query-editor').addEventListener('click', () => $('#query-dialog').close());
$('#close-query-editor-secondary').addEventListener('click', () => $('#query-dialog').close());
$('#apply-query').addEventListener('click', async () => {
  const errorBox = $('#query-editor-error');
  const applyButton = $('#apply-query');
  applyButton.disabled = true; applyButton.textContent = 'Checking…';
  try {
    const result = await api('/api/admin/query/parse', { method: 'POST', body: JSON.stringify({ language: queryLanguage, source: $('#report-pipeline').value, parameters: collectParameters() }) });
    lastParsedPipeline = result.pipeline;
    querySourcesByLanguage[queryLanguage] = $('#report-pipeline').value;
    validQueryLanguages[queryLanguage] = true;
    queryDirty = false;
    errorBox.hidden = true;
    renderQuerySummary(); renderColumnLabels();
    $('#query-dialog').close();
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  } finally {
    applyButton.disabled = false; applyButton.textContent = 'Apply query';
  }
});
$('#folder-tree').addEventListener('click', () => renderFolderSelects());
$('#new-folder').addEventListener('click', () => {
  if (!adminConnected) { notice('Pehle admin token se Connect karein.', 'error'); return; }
  $('#folder-name').value = ''; renderFolderSelects();
  folderError('');
  $('#create-folder-submit').disabled = false;
  $('#create-folder-submit').textContent = 'Create folder';
  $('#folder-parent').value = activeFolder || '';
  $('#folder-dialog').showModal(); $('#folder-name').focus();
});
$('#close-folder-dialog').addEventListener('click', () => $('#folder-dialog').close());
$('#cancel-folder').addEventListener('click', () => $('#folder-dialog').close());
$('#folder-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!adminConnected) { folderError('Pehle admin token enter karke Connect karein.'); return; }
  const submit = $('#create-folder-submit');
  submit.disabled = true;
  submit.textContent = 'Creating…';
  folderError('');
  try {
    const path = folderPathFromDialog();
    await api('/api/admin/folders', { method: 'POST', body: JSON.stringify({ path }) });
    await loadFolders(); renderReportList();
    $('#folder-dialog').close(); activeFolder = path;
    renderFolderTree(); renderReportList();
    notice(`Folder “${path}” created.`, 'success');
  } catch (error) {
    let message = error.message;
    if (error.status === 401 || error.status === 403) message = 'Admin token qabool nahi hua. Sahi token enter karke Connect karein.';
    else if (error.status === 404) message = 'Folder API is server version mein nahi mili. Updated files ke baad service restart karein.';
    else if (error instanceof TypeError) message = 'Server se rabta nahi ho saka. Service chal rahi hai ya nahi, check karke dobara Connect karein.';
    folderError(message);
    notice(`Folder save nahi hua: ${message}`, 'error');
  } finally {
    submit.disabled = false;
    submit.textContent = 'Create folder';
  }
});
reportForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const payload = makeReportPayload();
    const method = currentReportId ? 'PUT' : 'POST';
    const path = currentReportId ? `/api/admin/reports/${encodeURIComponent(currentReportId)}` : '/api/admin/reports';
    const result = await api(path, { method, body: JSON.stringify(payload) });
    currentReportId = result.report.id; activeFolder = result.report.folder || 'General';
    await loadReports();
    notice(`Report “${result.report.title}” saved in ${activeFolder}.`, 'success');
    inline('save-report-message', 'Saved.');
  } catch (error) { inline('save-report-message', error.message, true); }
});
$('#delete-report').addEventListener('click', async () => {
  if (!currentReportId || !window.confirm(`Delete report “${$('#report-title-input').value}”?`)) return;
  try {
    await api(`/api/admin/reports/${encodeURIComponent(currentReportId)}`, { method: 'DELETE' });
    reports = reports.filter((report) => report.id !== currentReportId); currentReportId = null;
    setReportEditor(friendlyDefaultReport()); renderFolderTree(); renderReportList(); notice('Report deleted.', 'success');
  } catch (error) { notice(error.message, 'error'); }
});
$('#preview-report').addEventListener('click', async () => {
  if (!currentReportId) { notice('Save the report before previewing it.', 'error'); return; }
  try {
    const result = await api(`/api/admin/reports/${encodeURIComponent(currentReportId)}/preview`, { method: 'POST', body: JSON.stringify({ parameters: previewValues() }) });
    renderPreviewResult(result); notice('Preview completed.', 'success');
  } catch (error) { notice(error.message, 'error'); }
});
function connectionPayload() {
  const name = $('#connection-name').value.trim();
  return {
    id: $('#connection-id').value.trim() || toConnectionId(name),
    name,
    uri: $('#connection-uri').value.trim(),
    database: $('#connection-database').value.trim(),
  };
}
$('#toggle-connection-uri').addEventListener('click', (event) => {
  const input = $('#connection-uri');
  input.type = input.type === 'password' ? 'text' : 'password';
  event.currentTarget.textContent = input.type === 'password' ? 'Show' : 'Hide';
});
$('#test-connection-form').addEventListener('click', async (event) => {
  const button = event.currentTarget;
  const payload = connectionPayload();
  button.disabled = true; button.textContent = 'Testing…';
  inline('save-connection-message', 'MongoDB server aur report read access check ho raha hai…');
  try {
    const result = await api('/api/admin/connections/test', { method: 'POST', body: JSON.stringify(payload) });
    const identity = result.identityReported
      ? (result.authenticatedUsers?.length ? ` · as ${result.authenticatedUsers.join(', ')}` : ' · no authenticated user reported')
      : '';
    const databaseWarning = result.databaseWarning ? ` · ${result.databaseWarning}` : '';
    inline('save-connection-message', result.readChecked?.length
      ? `Connected · report read verified: ${result.readChecked.join(', ')}${identity}${databaseWarning}`
      : `${result.message}${identity}${databaseWarning}`);
    notice(`${result.message}${databaseWarning}`, 'success');
  } catch (error) {
    inline('save-connection-message', error.message, true);
    notice(error.message, 'error');
  } finally { button.disabled = false; button.textContent = 'Test connection'; }
});
$('#connection-editor').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = connectionPayload();
  const saveButton = form.querySelector('button[type="submit"]');
  saveButton.disabled = true; saveButton.textContent = 'Saving…';
  try {
    await api('/api/admin/connections', { method: 'POST', body: JSON.stringify(payload) });
    await loadConnections();
    form.reset();
    $('#connection-id').value = '';
    $('#connection-uri').type = 'password'; $('#toggle-connection-uri').textContent = 'Show';
    inline('save-connection-message', 'Saved. Ab list mein Test connection se dobara check kar sakte hain.');
    notice(`Data source “${payload.name}” private settings file mein save ho gaya.`, 'success');
  } catch (error) {
    inline('save-connection-message', error.message, true);
    notice(error.message, 'error');
  } finally { saveButton.disabled = false; saveButton.textContent = 'Save data source'; }
});
$('#refresh-connections').addEventListener('click', () => loadConnections().then(() => notice('Data sources refreshed.', 'success')).catch((error) => notice(error.message, 'error')));
document.querySelectorAll('.studio-tab').forEach((tab) => tab.addEventListener('click', () => {
  document.querySelectorAll('.studio-tab').forEach((item) => item.classList.toggle('active', item === tab));
  if (tab.dataset.studioAction === 'query') {
    $('#open-query-editor').click();
    return;
  }
  if (tab.dataset.studioAction === 'preview') {
    $('#preview-report').click();
    return;
  }
  document.getElementById(tab.dataset.studioTarget)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}));
initializeAdminAccess();
