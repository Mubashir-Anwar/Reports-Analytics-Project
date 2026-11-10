// Pure helpers for the band-based design model (same JSON the backend validates and renders).

export const BAND_LABELS = {
  title: 'Title',
  pageHeader: 'Page Header',
  columnHeader: 'Column Header',
  detail: 'Detail 1',
  columnFooter: 'Column Footer',
  pageFooter: 'Page Footer',
  summary: 'Summary',
};

export const ELEMENT_LABELS = {
  staticText: 'Static Text',
  textField: 'Text Field',
  line: 'Line',
  rectangle: 'Rectangle',
  image: 'Image',
  chart: 'Chart',
};

export const BUILTIN_VARIABLES = ['PAGE_NUMBER', 'PAGE_COUNT', 'REPORT_COUNT', 'CURRENT_DATE'];

export const emptyBand = () => ({ height: 0, elements: [] });

export function emptyDesign() {
  return {
    version: 1,
    page: { width: 595, height: 842, margins: { top: 30, right: 30, bottom: 30, left: 30 } },
    calculatedFields: [],
    groups: [],
    variables: [],
    bands: {
      title: emptyBand(), pageHeader: emptyBand(), columnHeader: emptyBand(), detail: emptyBand(),
      columnFooter: emptyBand(), pageFooter: emptyBand(), summary: emptyBand(),
      groupHeaders: {}, groupFooters: {},
    },
  };
}

let counter = 0;
export const newId = (prefix = 'el') => `${prefix}-${Date.now().toString(36)}${(counter++).toString(36)}`;

/** Band keys in print order: 'title', 'groupHeader:Name', 'detail', 'groupFooter:Name', ... */
export function bandKeys(design) {
  const keys = ['title', 'pageHeader', 'columnHeader'];
  design.groups.forEach((g) => keys.push(`groupHeader:${g.name}`));
  keys.push('detail');
  [...design.groups].reverse().forEach((g) => keys.push(`groupFooter:${g.name}`));
  keys.push('columnFooter', 'pageFooter', 'summary');
  return keys;
}

export function bandLabel(key) {
  if (key.startsWith('groupHeader:')) return `Group Header · ${key.split(':')[1]}`;
  if (key.startsWith('groupFooter:')) return `Group Footer · ${key.split(':')[1]}`;
  return BAND_LABELS[key] || key;
}

export function getBand(design, key) {
  if (key.startsWith('groupHeader:')) return design.bands.groupHeaders[key.split(':')[1]] || emptyBand();
  if (key.startsWith('groupFooter:')) return design.bands.groupFooters[key.split(':')[1]] || emptyBand();
  return design.bands[key] || emptyBand();
}

export function setBand(design, key, band) {
  const bands = { ...design.bands };
  if (key.startsWith('groupHeader:')) bands.groupHeaders = { ...bands.groupHeaders, [key.split(':')[1]]: band };
  else if (key.startsWith('groupFooter:')) bands.groupFooters = { ...bands.groupFooters, [key.split(':')[1]]: band };
  else bands[key] = band;
  return { ...design, bands };
}

export function findElement(design, id) {
  for (const key of bandKeys(design)) {
    const element = getBand(design, key).elements.find((el) => el.id === id);
    if (element) return { bandKey: key, element };
  }
  return null;
}

export function bandWidth(design) {
  const { page } = design;
  return page.width - page.margins.left - page.margins.right;
}

export function defaultElement(type, x = 0, y = 0) {
  const base = { id: newId(), type, x, y, width: 120, height: 20, fontSize: 10, bold: false, italic: false, align: 'left', vAlign: 'top', color: '#000000' };
  switch (type) {
    case 'staticText': return { ...base, text: 'Static text' };
    case 'textField': return { ...base, expression: '', pattern: '', blankWhenNull: true };
    case 'line': return { ...base, width: 200, height: 1, borderColor: '#000000', borderWidth: 1 };
    case 'rectangle': return { ...base, width: 140, height: 40, backColor: '#f1f5f9', borderColor: '#94a3b8', borderWidth: 1 };
    case 'image': return { ...base, width: 100, height: 60, src: '' };
    case 'chart': return { ...base, width: 300, height: 170, chartType: 'bar', categoryField: '', valueField: '', aggregate: 'Sum', title: '' };
    default: return base;
  }
}

/** Mirror of the backend's column inference: last $project / $group / $count stage. */
export function inferColumns(pipeline) {
  for (let i = pipeline.length - 1; i >= 0; i -= 1) {
    const stage = pipeline[i];
    if (stage.$project && typeof stage.$project === 'object') {
      return Object.entries(stage.$project)
        .filter(([key, value]) => !(key === '_id' && (value === 0 || value === false)))
        .map(([key]) => key);
    }
    if (stage.$group && typeof stage.$group === 'object') return Object.keys(stage.$group);
    if (typeof stage.$count === 'string' && stage.$count) return [stage.$count];
  }
  return [];
}

/** Form-first: build a sensible tabular design from the field list. */
export function autoLayout(columns, labels = {}, title = 'Report', previous = emptyDesign()) {
  const design = emptyDesign();
  design.groups = previous.groups.filter((g) => columns.includes(g.field));
  const landscape = columns.length > 8;
  design.page = landscape
    ? { width: 842, height: 595, margins: { top: 30, right: 30, bottom: 30, left: 30 } }
    : { ...design.page };
  const usable = bandWidth(design);
  const count = Math.max(columns.length, 1);
  const colWidth = Math.max(40, Math.floor(usable / count));
  const size = count > 12 ? 7 : count > 8 ? 8 : 9;
  const place = (builder, bandKey, height) => {
    const elements = columns.map((col, i) => builder(col, i * colWidth));
    Object.assign(getBandRef(design, bandKey), { height, elements });
  };
  design.bands.title = {
    height: 46,
    elements: [{ ...defaultElement('staticText', 0, 8), text: title, width: usable, height: 28, fontSize: 18, bold: true }],
  };
  place((col, x) => ({ ...defaultElement('staticText', x, 2), text: labels[col] || col, width: colWidth, height: 18, fontSize: size, bold: true, backColor: '#e2e8f0' }), 'columnHeader', 22);
  place((col, x) => ({ ...defaultElement('textField', x, 1), expression: `$F{${col}}`, width: colWidth, height: 16, fontSize: size }), 'detail', 18);
  design.bands.pageFooter = {
    height: 24,
    elements: [{ ...defaultElement('textField', usable - 160, 4), expression: 'Page $V{PAGE_NUMBER} of $V{PAGE_COUNT}', width: 160, height: 16, align: 'right', fontSize: 8, color: '#475569' }],
  };
  design.bands.summary = {
    height: 28,
    elements: [{ ...defaultElement('textField', 0, 6), expression: 'Total records: $V{REPORT_COUNT}', width: 220, height: 16, bold: true }],
  };
  design.groups.forEach((g) => {
    design.bands.groupHeaders[g.name] = { height: 20, elements: [{ ...defaultElement('textField', 0, 2), expression: `$F{${g.field}}`, width: usable, height: 16, bold: true, backColor: '#f1f5f9' }] };
    design.bands.groupFooters[g.name] = emptyBand();
  });
  design.variables = previous.variables.filter((v) => !v.field || columns.includes(v.field));
  design.calculatedFields = previous.calculatedFields;
  return design;
}

function getBandRef(design, key) {
  return design.bands[key];
}

export function describeElement(el) {
  if (el.type === 'staticText') return el.text || '(empty)';
  if (el.type === 'textField') return el.expression || '(expression)';
  if (el.type === 'chart') return `${el.chartType} chart`;
  return ELEMENT_LABELS[el.type];
}
