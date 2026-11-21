// Pure helpers for the band-based design model (same JSON the backend validates and renders).

export const BAND_LABELS = {
  title: 'Title',
  pageHeader: 'Page Header',
  columnHeader: 'Column Header',
  detail: 'Detail 1',
  columnFooter: 'Column Footer',
  pageFooter: 'Page Footer',
  lastPageFooter: 'Last Page Footer',
  summary: 'Summary',
  noData: 'No Data',
  background: 'Background',
};

/** Height a band gets when it is created from the Outline (like Jaspersoft Studio's "Create Band"). */
export const DEFAULT_BAND_HEIGHT = {
  title: 50, pageHeader: 30, columnHeader: 22, detail: 20, columnFooter: 22, pageFooter: 24, lastPageFooter: 24,
  summary: 40, noData: 40, background: 120, groupHeader: 22, groupFooter: 22,
};

/** Images, boxes and frames go behind the other elements, so text and fields placed on them stay clickable. */
export const BACKDROP_TYPES = new Set(['image', 'rectangle', 'ellipse', 'frame', 'list']);
/** Components that print many records (Jasper Table, Crosstab, List, Subreport); they make their band grow. */
export const DATASET_TYPES = new Set(['table', 'crosstab', 'list', 'subreport']);
/** Container elements whose contents move with them on the canvas. */
export const CONTAINER_TYPES = new Set(['frame', 'list']);

/** A band "exists" like in a JRXML when it has a height or elements; absent bands are not shown or printed. */
export const bandPresent = (band) => Boolean(band && (band.height > 0 || band.elements.length));

export const ELEMENT_LABELS = {
  staticText: 'Static Text',
  textField: 'Text Field',
  line: 'Line',
  rectangle: 'Rectangle',
  image: 'Image',
  chart: 'Chart',
  ellipse: 'Ellipse',
  frame: 'Frame',
  break: 'Break',
  barcode: 'Barcode',
  table: 'Table',
  crosstab: 'Crosstab',
  list: 'List',
  subreport: 'Subreport',
  note: 'Note',
};

/**
 * Like Jaspersoft Studio's Palette. Basic elements are element types; composite elements are ready-made
 * Text Fields (Page Number, Page X of Y, Current Date…). `percentage` asks for a field when it is placed.
 */
export const PALETTE_GROUPS = [
  {
    title: 'Basic Elements',
    items: [
      ['textField', '𝑓', 'Text Field'], ['staticText', 'T', 'Static Text'], ['image', '🖼', 'Image'],
      ['break', '⤓', 'Break'], ['rectangle', '▭', 'Rectangle'], ['ellipse', '◯', 'Ellipse'], ['line', '─', 'Line'],
      ['frame', '⬚', 'Frame'], ['barcode', '▥', 'Barcode / QR'], ['chart', '▦', 'Chart'], ['spider', '✳', 'Spider Chart'],
    ],
  },
  {
    title: 'Data Components',
    items: [
      ['table', '▤', 'Table'], ['crosstab', '⊞', 'Crosstab'], ['list', '☰', 'List'], ['subreport', '⧉', 'Subreport'],
    ],
  },
  {
    title: 'Designer',
    items: [['note', '🗒', 'Note']],
  },
  {
    title: 'Composite Elements',
    items: [
      ['pageNumber', '#', 'Page Number'], ['totalPages', 'Σ', 'Total Pages'], ['pageXofY', '⅟', 'Page X of Y'],
      ['currentDate', '📅', 'Current Date'], ['currentTime', '🕒', 'Time'], ['percentage', '%', 'Percentage'],
    ],
  },
];
export const PALETTE_LABELS = Object.fromEntries(PALETTE_GROUPS.flatMap((g) => g.items.map(([type, , label]) => [type, label])));

export const BUILTIN_VARIABLES = ['PAGE_NUMBER', 'PAGE_COUNT', 'REPORT_COUNT', 'CURRENT_DATE'];

export const emptyBand = () => ({ height: 0, elements: [] });

export function emptyDesign() {
  return {
    version: 1,
    page: { width: 595, height: 842, margins: { top: 30, right: 30, bottom: 30, left: 30 }, sheetLayout: 'grid' },
    calculatedFields: [],
    functions: [],
    groups: [],
    variables: [],
    datasets: [],
    bands: {
      title: emptyBand(), pageHeader: emptyBand(), columnHeader: emptyBand(), detail: emptyBand(),
      columnFooter: emptyBand(), pageFooter: emptyBand(), lastPageFooter: emptyBand(), summary: emptyBand(),
      noData: emptyBand(), background: emptyBand(),
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
  keys.push('columnFooter', 'pageFooter', 'lastPageFooter', 'summary', 'noData', 'background');
  return keys;
}

export function bandLabel(key) {
  if (key.startsWith('groupHeader:')) return `${key.split(':')[1]} Group Header`;
  if (key.startsWith('groupFooter:')) return `${key.split(':')[1]} Group Footer`;
  return BAND_LABELS[key] || key;
}

export const groupOfBand = (key) => (key.includes(':') ? key.split(':')[1] : null);

/** "Create Band": give an absent band its default height. */
export function createBand(design, key) {
  const band = getBand(design, key);
  const kind = key.split(':')[0];
  return setBand(design, key, { ...band, height: Math.max(band.height, DEFAULT_BAND_HEIGHT[kind] || 30) });
}

/** "Delete Band": remove the band and everything in it (it is then not printed). */
export function deleteBand(design, key) {
  return setBand(design, key, emptyBand());
}

export const NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/;

/** Next free name like Parameter1, Parameter2… */
export function nextName(prefix, taken) {
  const used = new Set(taken);
  let n = 1;
  while (used.has(`${prefix}${n}`)) n += 1;
  return `${prefix}${n}`;
}

const escapeRe = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
/** Replace $P{old} / $V{old} / $F{old} tokens in a text. */
export function renameToken(text, letter, from, to) {
  if (typeof text !== 'string' || !text) return text;
  return text.replace(new RegExp(`\\$${letter}\\{${escapeRe(from)}\\}`, 'g'), `$${letter}{${to}}`);
}

/** Apply `fn` to every text/expression of every element in every band. */
export function mapElements(design, fn) {
  const mapBand = (band) => ({ ...band, elements: band.elements.map(fn) });
  const bands = { ...design.bands };
  Object.keys(bands).forEach((key) => {
    if (key === 'groupHeaders' || key === 'groupFooters') {
      bands[key] = Object.fromEntries(Object.entries(bands[key] || {}).map(([name, band]) => [name, mapBand(band)]));
    } else if (bands[key]?.elements) bands[key] = mapBand(bands[key]);
  });
  return { ...design, bands };
}

/** Rename a token in every element's text and expression (what Jaspersoft Studio's refactoring does). */
export function renameInElements(design, letter, from, to) {
  return mapElements(design, (el) => ({
    ...el,
    ...(el.text !== undefined ? { text: renameToken(el.text, letter, from, to) } : {}),
    ...(el.expression !== undefined ? { expression: renameToken(el.expression, letter, from, to) } : {}),
  }));
}

/** Rename a group: its bands, the variables that reset on it. */
export function renameGroup(design, from, to) {
  const move = (mapping) => Object.fromEntries(Object.entries(mapping).map(([name, band]) => [name === from ? to : name, band]));
  return {
    ...design,
    groups: design.groups.map((g) => (g.name === from ? { ...g, name: to } : g)),
    variables: design.variables.map((v) => (v.resetGroup === from ? { ...v, resetGroup: to } : v)),
    bands: { ...design.bands, groupHeaders: move(design.bands.groupHeaders), groupFooters: move(design.bands.groupFooters) },
  };
}

/** Rename a calculated field everywhere it is used ($F tokens, variables, groups, charts, other calculated fields). */
export function renameCalculated(design, from, to) {
  const swap = (value) => (value === from ? to : value);
  const next = mapElements(renameInElements(design, 'F', from, to), (el) => (el.type === 'chart' ? { ...el, categoryField: swap(el.categoryField), valueField: swap(el.valueField) } : el));
  return {
    ...next,
    calculatedFields: next.calculatedFields.map((c) => ({ ...c, name: swap(c.name), expression: renameToken(c.expression, 'F', from, to) })),
    variables: next.variables.map((v) => ({ ...v, field: swap(v.field) })),
    groups: next.groups.map((g) => ({ ...g, field: swap(g.field) })),
  };
}

export function removeGroup(design, name) {
  const headers = { ...design.bands.groupHeaders };
  const footers = { ...design.bands.groupFooters };
  delete headers[name];
  delete footers[name];
  return {
    ...design,
    groups: design.groups.filter((g) => g.name !== name),
    variables: design.variables.map((v) => (v.resetGroup === name ? { ...v, resetType: 'Report', resetGroup: '' } : v)),
    bands: { ...design.bands, groupHeaders: headers, groupFooters: footers },
  };
}

/** Element operations used by the Outline and the canvas context menus. */
export function removeElement(design, bandKey, id) {
  const band = getBand(design, bandKey);
  return setBand(design, bandKey, { ...band, elements: band.elements.filter((el) => el.id !== id) });
}
export function duplicateElement(design, bandKey, id) {
  const band = getBand(design, bandKey);
  const source = band.elements.find((el) => el.id === id);
  if (!source) return { design, id: null };
  const copy = { ...source, id: newId(), x: source.x + 10, y: source.y + 4 };
  const height = Math.max(band.height, Math.ceil(copy.y + copy.height));
  return { design: setBand(design, bandKey, { ...band, height, elements: [...band.elements, copy] }), id: copy.id };
}
/** Bring to front / send to back: the element order in a band is the drawing order. */
export function reorderElement(design, bandKey, id, toFront) {
  const band = getBand(design, bandKey);
  const element = band.elements.find((el) => el.id === id);
  if (!element) return design;
  const rest = band.elements.filter((el) => el.id !== id);
  return setBand(design, bandKey, { ...band, elements: toFront ? [...rest, element] : [element, ...rest] });
}
export function moveElementToBand(design, fromKey, toKey, id) {
  const source = getBand(design, fromKey);
  const element = source.elements.find((el) => el.id === id);
  if (!element || fromKey === toKey) return design;
  const next = setBand(design, fromKey, { ...source, elements: source.elements.filter((el) => el.id !== id) });
  const target = getBand(next, toKey);
  const kind = toKey.split(':')[0];
  const height = Math.max(target.height, DEFAULT_BAND_HEIGHT[kind] || 30, Math.ceil(element.y + element.height));
  return setBand(next, toKey, { ...target, height, elements: [...target.elements, element] });
}

/** Add an element of a type to a band at a free spot (below the existing elements). */
export function addElementToBand(design, bandKey, type, extra = {}) {
  const band = getBand(design, bandKey);
  const element = { ...defaultElement(type, 0, 0), ...extra };
  if (element.type === 'break') element.width = bandWidth(design);
  element.x = Math.min(element.x, Math.max(0, bandWidth(design) - element.width));
  const kind = bandKey.split(':')[0];
  const height = Math.max(band.height, DEFAULT_BAND_HEIGHT[kind] || 30, Math.ceil(element.y + element.height));
  // images and rectangles go behind the other elements (fields placed on them stay visible and clickable)
  const elements = BACKDROP_TYPES.has(element.type) ? [element, ...band.elements] : [...band.elements, element];
  return { design: setBand(design, bandKey, { ...band, height, elements }), id: element.id };
}

/** Like dropping a field from the Outline in Jaspersoft Studio: a text field in Detail and its label in Column Header. */
export function placeField(design, field) {
  const right = (key) => getBand(design, key).elements.reduce((r, el) => Math.max(r, el.x + el.width), 0);
  const x = Math.min(Math.max(right('detail'), right('columnHeader')), Math.max(0, bandWidth(design) - 100));
  const detail = addElementToBand(design, 'detail', 'textField', { expression: `$F{${field}}`, x, y: 1, width: 100, height: 16, fontSize: 9 });
  const header = addElementToBand(detail.design, 'columnHeader', 'staticText', { text: field, x, y: 2, width: 100, height: 18, fontSize: 9, bold: true });
  return { design: header.design, id: detail.id };
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

/** What each band does when the report fills (Jasper semantics, shown in the Properties panel). */
export function bandPurpose(key) {
  if (key.startsWith('groupHeader:')) return 'Printed once before each new group value (whenever the group field changes).';
  if (key.startsWith('groupFooter:')) return 'Printed once at the end of each group; put group totals (group-reset $V) here.';
  return {
    title: 'Printed once at the start of the report (first page only).',
    pageHeader: 'Printed at the top of every page.',
    columnHeader: 'Printed on every page before the Detail rows — put column headings here.',
    detail: 'Repeated once for every record (row).',
    columnFooter: 'Printed on every page after the Detail rows.',
    pageFooter: 'Printed at the bottom of every page — put the page number here.',
    lastPageFooter: 'Printed at the bottom of the last page instead of the Page Footer.',
    summary: 'Printed once at the end of the report — put grand totals and charts here.',
    noData: 'Printed instead of the whole report when the query returns no rows.',
    background: 'Printed behind the other bands on every page — for a watermark, frame or logo.',
  }[key] || '';
}

/** Lowest point used by the elements of a band (for "fit band to content"). */
export function contentBottom(band) {
  return band.elements.reduce((bottom, el) => Math.max(bottom, Math.ceil(el.y + el.height)), 0);
}

/** Right-most point used by any element in the design (for "fit page width to content"). */
export function contentRight(design) {
  return bandKeys(design).reduce(
    (right, key) => getBand(design, key).elements.reduce((r, el) => Math.max(r, Math.ceil(el.x + el.width)), right),
    0,
  );
}

export const PAGE_PRESETS = [
  ['a4p', 'A4 portrait (595×842)', 595, 842],
  ['a4l', 'A4 landscape (842×595)', 842, 595],
  ['a3p', 'A3 portrait (842×1191)', 842, 1191],
  ['a3l', 'A3 landscape (1191×842)', 1191, 842],
  ['ltrp', 'Letter portrait (612×792)', 612, 792],
  ['ltrl', 'Letter landscape (792×612)', 792, 612],
  ['lglp', 'Legal portrait (612×1008)', 612, 1008],
  ['lgll', 'Legal landscape (1008×612)', 1008, 612],
];

export function pagePreset(page) {
  const found = PAGE_PRESETS.find(([, , w, h]) => w === page.width && h === page.height);
  return found ? found[0] : 'custom';
}

export const PAGE_LIMITS = { min: 100, max: 14400 };

/** Font names offered like Jaspersoft Studio. PDF maps them to Helvetica / Times / Courier; Excel keeps the name. */
export const FONT_NAMES = ['SansSerif', 'Arial', 'Helvetica', 'Verdana', 'Tahoma', 'Calibri', 'Segoe UI', 'Serif', 'Times New Roman', 'Georgia', 'Monospaced', 'Courier New'];
export const CSS_FONT = {
  SansSerif: 'Arial, Helvetica, sans-serif', Arial: 'Arial, sans-serif', Helvetica: 'Helvetica, Arial, sans-serif', Verdana: 'Verdana, sans-serif',
  Tahoma: 'Tahoma, sans-serif', Calibri: 'Calibri, Carlito, sans-serif', 'Segoe UI': '"Segoe UI", sans-serif', Serif: '"Times New Roman", serif',
  'Times New Roman': '"Times New Roman", serif', Georgia: 'Georgia, serif', Monospaced: '"Courier New", monospace', 'Courier New': '"Courier New", monospace',
};
/** Jasper "Markup": none = text as-is, html = <b>, <i>, <u>, <br>, <font>, <span style>, lists…, styled = Jasper <style> tags. */
export const MARKUPS = [['none', 'None (plain text)'], ['html', 'HTML'], ['styled', 'Styled (Jasper <style>)']];

const ALLOWED_TAGS = new Set(['B', 'STRONG', 'I', 'EM', 'U', 'S', 'STRIKE', 'DEL', 'BR', 'P', 'DIV', 'SPAN', 'FONT', 'SUP', 'SUB', 'UL', 'OL', 'LI', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6']);
const DROPPED_TAGS = new Set(['SCRIPT', 'STYLE', 'IFRAME', 'OBJECT', 'EMBED', 'TEMPLATE', 'NOSCRIPT', 'SVG', 'MATH', 'TITLE', 'HEAD']);
const ALLOWED_STYLE =/^(color|background-color|font-size|font-weight|font-style|font-family|text-decoration)$/i;

/** Sanitised HTML for showing a markup text inside the designer canvas (no scripts, links, images or events). */
export function safeMarkupHtml(source, markup) {
  let text = String(source ?? '');
  if (markup === 'styled') {
    text = text.replace(/<style\b([^>]*)>/gi, (_, attrs) => {
      const get = (name) => (attrs.match(new RegExp(`${name}\\s*=\\s*"([^"]*)"`, 'i')) || [])[1];
      const css = [];
      if (get('forecolor')) css.push(`color:${get('forecolor')}`);
      if (get('backcolor')) css.push(`background-color:${get('backcolor')}`);
      if (get('size')) css.push(`font-size:${get('size')}px`);
      if (get('isBold') === 'true') css.push('font-weight:bold');
      if (get('isItalic') === 'true') css.push('font-style:italic');
      if (get('isUnderline') === 'true') css.push('text-decoration:underline');
      if (get('fontName')) css.push(`font-family:${get('fontName')}`);
      return `<span style="${css.join(';')}">`;
    }).replace(/<\/style>/gi, '</span>');
  } else if (markup !== 'html') {
    return null;
  }
  const doc = new DOMParser().parseFromString(`<div>${text}</div>`, 'text/html');
  const clean = (node) => {
    [...node.childNodes].forEach((child) => {
      if (child.nodeType === 3) return;
      if (child.nodeType !== 1 || DROPPED_TAGS.has(child.tagName)) { child.remove(); return; } // drop code blocks with their text
      clean(child);
      if (!ALLOWED_TAGS.has(child.tagName)) { child.replaceWith(...child.childNodes); return; }
      [...child.attributes].forEach((attr) => {
        const name = attr.name.toLowerCase();
        if (name === 'style') {
          const kept = attr.value.split(';').map((rule) => rule.trim()).filter((rule) => {
            const [prop, value = ''] = rule.split(':');
            return ALLOWED_STYLE.test((prop || '').trim()) && !/url\(|expression/i.test(value);
          });
          child.setAttribute('style', kept.join(';'));
        } else if (!(child.tagName === 'FONT' && ['color', 'size', 'face'].includes(name))) {
          child.removeAttribute(attr.name);
        }
      });
    });
  };
  const root = doc.body.firstChild;
  clean(root);
  return root.innerHTML;
}

/**
 * Resize an element. With `shiftFollowing` it behaves like a table column: the same column in other bands
 * (e.g. the header above a detail field) gets the same width, the columns to its right move by the same amount,
 * and elements spanning the column (a full-width line, a group header) grow with it. The page widens if needed.
 */
export function resizeElement(design, bandKey, elementId, size, shiftFollowing = true) {
  const band = getBand(design, bandKey);
  const target = band.elements.find((el) => el.id === elementId);
  if (!target) return design;
  const width = size.width ?? target.width;
  const delta = width - target.width;
  const oldRight = target.x + target.width;
  const isTwin = (el) => el.id !== elementId && Math.abs(el.x - target.x) <= 1 && Math.abs(el.width - target.width) <= 1;
  const keys = bandKeys(design);
  const affected = new Set(shiftFollowing && delta ? keys.filter((key) => key === bandKey || getBand(design, key).elements.some(isTwin)) : [bandKey]);
  let next = design;
  for (const key of affected) {
    const current = getBand(next, key);
    const elements = current.elements.map((el) => {
      if (el.id === elementId) return { ...el, ...size };
      if (!shiftFollowing || !delta) return el;
      if (key !== bandKey && isTwin(el)) return { ...el, width: Math.max(1, el.width + delta) };
      if (el.x >= oldRight - 1) return { ...el, x: Math.max(0, Math.round(el.x + delta)) };
      if (el.x <= target.x + 1 && el.x + el.width >= oldRight - 1) return { ...el, width: Math.max(1, el.width + delta) };
      return el;
    });
    next = setBand(next, key, { ...current, elements });
  }
  const { margins } = next.page;
  const needed = contentRight(next) + margins.left + margins.right;
  if (needed > next.page.width) next = { ...next, page: { ...next.page, width: Math.min(PAGE_LIMITS.max, Math.ceil(needed)) } };
  return next;
}

export const ZOOM_LIMITS = { min: 0.25, max: 3, step: 0.1 };
export const clampZoom = (value) => Math.round(Math.min(Math.max(value, ZOOM_LIMITS.min), ZOOM_LIMITS.max) * 100) / 100;

export function defaultElement(type, x = 0, y = 0) {
  const base = { id: newId(), type, x, y, width: 120, height: 20, fontSize: 10, fontName: 'SansSerif', bold: false, italic: false, underline: false, align: 'left', vAlign: 'top', color: '#000000', markup: 'none' };
  switch (type) {
    case 'staticText': return { ...base, text: 'Static text' };
    case 'textField': return { ...base, expression: '', pattern: '', blankWhenNull: true, stretch: true };
    case 'line': return { ...base, width: 200, height: 1, borderColor: '#000000', borderWidth: 1 };
    case 'rectangle': return { ...base, width: 140, height: 40, backColor: '#f1f5f9', borderColor: '#94a3b8', borderWidth: 1 };
    case 'image': return { ...base, width: 100, height: 60, src: '' };
    case 'chart': return { ...base, width: 300, height: 170, chartType: 'bar', categoryField: '', valueField: '', aggregate: 'Sum', title: '' };
    case 'ellipse': return { ...base, width: 80, height: 50, backColor: '#e0f2fe', borderColor: '#0284c7', borderWidth: 1 };
    case 'frame': return { ...base, width: 240, height: 80, backColor: null, borderColor: '#64748b', borderWidth: 1 };
    case 'break': return { ...base, x: 0, width: 200, height: 1 };
    case 'barcode': return { ...base, width: 70, height: 70, barcodeType: 'qr', expression: '', showText: true, fontSize: 8 };
    case 'spider': return { ...base, type: 'chart', width: 260, height: 220, chartType: 'spider', categoryField: '', valueField: '', aggregate: 'Sum', title: '' };
    // data components (a wizard asks for their fields when they are placed)
    case 'table': return { ...base, width: 400, height: 54, fontSize: 9, columns: [], headerHeight: 18, rowHeight: 16, footerHeight: 18, headerBackColor: '#e2e8f0', headerColor: '#000000', gridColor: '#94a3b8', gridWidth: 0.5, zebraColor: null, repeatHeader: true, scope: 'report', filterExpression: '', sortField: '', sortOrder: 'Ascending' };
    case 'crosstab': return { ...base, width: 400, height: 54, fontSize: 9, rowField: '', columnField: '', measureField: '', calculation: 'Sum', pattern: '#,##0', rowHeaderWidth: 100, cellWidth: 60, headerHeight: 18, rowHeight: 16, cornerText: '', headerBackColor: '#e2e8f0', headerColor: '#000000', totalBackColor: '#f1f5f9', gridColor: '#94a3b8', gridWidth: 0.5, showRowTotals: true, showColumnTotals: true, repeatHeader: true, scope: 'report', filterExpression: '', sortField: '', sortOrder: 'Ascending' };
    case 'list': return { ...base, width: 300, height: 24, backColor: null, borderColor: '#cbd5e1', borderWidth: 0, scope: 'report', filterExpression: '', sortField: '', sortOrder: 'Ascending' };
    case 'subreport': return { ...base, width: 400, height: 60, reportId: '', parameters: [], showTitle: true };
    case 'note': return { ...base, width: 160, height: 60, text: 'Note: only visible in the designer', backColor: '#fef3c7', fontSize: 9 };
    // composite elements (Jasper): ready-made Text Fields
    case 'pageNumber': return { ...base, type: 'textField', width: 60, expression: '$V{PAGE_NUMBER}', pattern: '', blankWhenNull: true, align: 'right' };
    case 'totalPages': return { ...base, type: 'textField', width: 60, expression: '$V{PAGE_COUNT}', pattern: '', blankWhenNull: true, evaluationTime: 'Report' };
    case 'pageXofY': return { ...base, type: 'textField', width: 140, expression: 'Page $V{PAGE_NUMBER} of $V{PAGE_COUNT}', pattern: '', blankWhenNull: true, align: 'right' };
    case 'currentDate': return { ...base, type: 'textField', width: 100, expression: '$V{CURRENT_DATE}', pattern: 'dd/MM/yyyy', blankWhenNull: true };
    case 'currentTime': return { ...base, type: 'textField', width: 80, expression: '=new java.util.Date()', pattern: 'HH:mm', blankWhenNull: true };
    case 'percentage': return { ...base, type: 'percentage', width: 80 }; // replaced by a Text Field once a field is chosen
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

/** The choices of the Table report (asked in the layout gallery); the defaults are what a table got before. */
export const TABLE_OPTIONS = {
  showTitle: true, titleAlign: 'center',          // the report name at the top: left / center / right
  infoBox: false,                                  // a bordered box top right: report ID, run date, page X of Y
  headAlign: 'center', headVAlign: 'middle',       // column headings: left / center / right · top / middle / bottom
  headBold: true, headFill: '#e2e8f0',             // '' = no background
  headBorder: true, dataBorder: true,              // a border on every heading cell / every data cell
  widths: 'text',                                  // 'text' = each column as wide as its heading, 'equal' = fill the page
  pageNumbers: true, totalRecords: true,
  sheetLayout: 'mixed',                            // Excel/CSV: mixed (headings pixel-perfect) / grid / table
};

/** Form-first: build a sensible tabular design from the field list (`options`: see TABLE_OPTIONS). */
export function autoLayout(columns, labels = {}, title = 'Report', previous = emptyDesign(), options = {}) {
  const o = { ...TABLE_OPTIONS, sheetLayout: previous.page?.sheetLayout || 'grid', ...options };
  const design = emptyDesign();
  design.groups = previous.groups.filter((g) => columns.includes(g.field));
  // Each column is as wide as its heading needs (the full field name, e.g. StageDDESales.Status), at least 60 pt;
  // the page grows to hold all of them instead of squeezing every column to the same narrow width.
  const size = columns.length > 12 ? 8 : 9;
  const textWidth = (value) => Math.ceil(String(value).length * size * 0.58 + 12);
  let widths = columns.map((col) => Math.min(260, Math.max(60, textWidth(labels[col] || col))));
  let total = widths.reduce((sum, w) => sum + w, 0);
  const landscape = columns.length > 8 || total > 535;
  const margins = landscape ? { top: 30, right: 30, bottom: 30, left: 30 } : design.page.margins;
  const base = landscape ? { width: 842, height: 595 } : { width: design.page.width, height: design.page.height };
  if (o.widths === 'equal' && columns.length) {        // the same width for every column, filling the page width
    const room = base.width - margins.left - margins.right;
    const each = Math.max(40, Math.floor(room / columns.length));
    widths = columns.map(() => each);
    total = each * columns.length;
  }
  design.page = { ...design.page, ...base, margins, width: Math.min(PAGE_LIMITS.max, Math.max(base.width, total + margins.left + margins.right)),
    sheetLayout: o.sheetLayout };
  const usable = bandWidth(design);
  const xs = widths.reduce((list, w, i) => [...list, i ? list[i - 1] + widths[i - 1] : 0], []);
  const place = (builder, bandKey, height) => {
    const elements = columns.map((col, i) => builder(col, xs[i], widths[i]));
    Object.assign(getBandRef(design, bandKey), { height, elements });
  };
  const line = { borderWidth: 0.5, borderColor: '#64748b' };
  const span = Math.min(usable, total || usable);
  const titleBand = [];
  if (o.infoBox) {                                     // label | value rows in a bordered box at the right
    const boxX = Math.max(0, span - 220);
    const rows = [['Report ID:', { type: 'staticText', text: o.reportId || title }],
      ['Run on:', { type: 'textField', expression: '$V{CURRENT_DATE}', pattern: 'dd-MMM-yyyy HH:mm' }],
      ['Page:', { type: 'textField', expression: '$V{PAGE_NUMBER} of $V{PAGE_COUNT}' }]];
    rows.forEach(([label, value], i) => {
      titleBand.push({ ...defaultElement('staticText', boxX, 4 + i * 15), text: label, width: 70, height: 15, fontSize: 8, bold: true, ...line, padding: 2, vAlign: 'middle' });
      const { type, ...rest } = value;
      titleBand.push({ ...defaultElement(type, boxX + 70, 4 + i * 15), ...rest, width: 150, height: 15, fontSize: 8, ...line, padding: 2, vAlign: 'middle' });
    });
  }
  if (o.showTitle) {
    const room = o.infoBox ? Math.max(120, span - 230) : span;  // never under the info box
    titleBand.push({ ...defaultElement('staticText', 0, 8), text: title, width: room, height: 28, fontSize: 18, bold: true, align: o.titleAlign, vAlign: 'middle' });
  }
  design.bands.title = { height: titleBand.length ? (o.infoBox ? 56 : 46) : 0, elements: titleBand };
  const headLine = o.headBorder ? line : { borderWidth: 0 };
  const dataLine = o.dataBorder ? line : { borderWidth: 0 };
  place((col, x, w) => ({ ...defaultElement('staticText', x, 0), text: labels[col] || col, width: w, height: 22, fontSize: size, bold: o.headBold, backColor: o.headFill || null, ...headLine, align: o.headAlign, vAlign: o.headVAlign, padding: 2 }), 'columnHeader', 22);
  place((col, x, w) => ({ ...defaultElement('textField', x, 0), expression: `$F{${col}}`, width: w, height: 18, fontSize: size, stretch: true, blankWhenNull: true, ...dataLine, vAlign: 'middle', padding: 2 }), 'detail', 18);
  design.bands.pageFooter = o.pageNumbers ? {
    height: 24,
    elements: [{ ...defaultElement('textField', usable - 160, 4), expression: 'Page $V{PAGE_NUMBER} of $V{PAGE_COUNT}', width: 160, height: 16, align: 'right', fontSize: 8, color: '#475569' }],
  } : emptyBand();
  design.bands.summary = o.totalRecords ? {
    height: 28,
    elements: [{ ...defaultElement('textField', 0, 6), expression: 'Total records: $V{REPORT_COUNT}', width: 220, height: 16, bold: true }],
  } : emptyBand();
  design.groups.forEach((g) => {
    design.bands.groupHeaders[g.name] = { height: 20, elements: [{ ...defaultElement('textField', 0, 2), expression: `$F{${g.field}}`, width: usable, height: 16, bold: true, backColor: '#f1f5f9' }] };
    design.bands.groupFooters[g.name] = emptyBand();
  });
  design.variables = previous.variables.filter((v) => !v.field || columns.includes(v.field));
  design.calculatedFields = previous.calculatedFields;
  design.functions = previous.functions || [];
  design.datasets = previous.datasets || [];
  return design;
}

function getBandRef(design, key) {
  return design.bands[key];
}

export function describeElement(el) {
  if (el.type === 'staticText') return el.text || '(empty)';
  if (el.type === 'textField') return el.expression || '(expression)';
  if (el.type === 'chart') return `${el.chartType} chart`;
  if (el.type === 'barcode') return `${(el.barcodeType || 'qr').toUpperCase()}: ${el.expression || '(value)'}`;
  if (el.type === 'table') return `Table · ${(el.columns || []).length} columns`;
  if (el.type === 'crosstab') return `Crosstab · ${el.rowField || '?'} × ${el.columnField || '?'}`;
  if (el.type === 'subreport') return `Subreport · ${el.reportId || '(choose a report)'}`;
  if (el.type === 'note') return el.text || 'Note';
  return ELEMENT_LABELS[el.type];
}
