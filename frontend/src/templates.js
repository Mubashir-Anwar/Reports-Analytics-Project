// Layout templates for a new design (like Jaspersoft Studio's New Report wizard templates).
// Each one returns a complete design; the user then changes everything on the canvas.
import { autoLayout, defaultElement, emptyBand, emptyDesign } from './designModel.js';

const A4 = { width: 595, height: 842 };
const A4_LANDSCAPE = { width: 842, height: 595 };
const MARGINS = { top: 30, right: 30, bottom: 30, left: 30 };

const text = (x, y, width, height, value, extra = {}) => ({ ...defaultElement('staticText', x, y), width, height, text: value, ...extra });
const field = (x, y, width, height, expression, extra = {}) => ({ ...defaultElement('textField', x, y), width, height, expression, stretch: false, ...extra });
const pageNumber = (usable) => field(usable - 160, 4, 160, 14, 'Page $V{PAGE_NUMBER} of $V{PAGE_COUNT}', { align: 'right', fontSize: 8, color: '#64748b' });

function base(size, sheetLayout = 'grid') {
  const design = emptyDesign();
  design.page = { ...size, margins: { ...MARGINS }, sheetLayout };
  return design;
}
const usableWidth = (design) => design.page.width - design.page.margins.left - design.page.margins.right;
const fullHeight = (design) => design.page.height - design.page.margins.top - design.page.margins.bottom;
const tokenFor = (columns, index, fallback) => (columns[index] ? `$F{${columns[index]}}` : fallback);

function blank(size) {
  const design = base(size);
  const w = usableWidth(design);
  design.bands.title = { height: 60, elements: [] };
  design.bands.detail = { height: 40, elements: [] };
  design.bands.pageFooter = { height: 24, elements: [pageNumber(w)] };
  return design;
}

/** One letter per record: letterhead, date, address from the first fields, body text, signature. */
function letter(columns, title) {
  const design = base(A4);
  const w = usableWidth(design);
  const h = fullHeight(design);
  const name = tokenFor(columns, 0, 'Customer name');
  const second = tokenFor(columns, 1, 'Address line');
  design.bands.detail = {
    height: h,
    elements: [
      { ...defaultElement('image', 0, 0), width: 130, height: 50 },
      text(w - 300, 4, 300, 22, title || 'Company name', { align: 'right', bold: true, fontSize: 14 }),
      text(w - 300, 26, 300, 14, 'Address · Phone · Email', { align: 'right', fontSize: 8, color: '#64748b' }),
      { ...defaultElement('line', 0, 62), width: w, height: 1, borderColor: '#94a3b8' },
      field(w - 200, 76, 200, 16, '$V{CURRENT_DATE}', { pattern: 'dd MMMM yyyy', align: 'right' }),
      field(0, 100, 320, 16, name, { bold: true }),
      field(0, 116, 320, 16, second),
      text(0, 150, w, 18, 'Subject: …', { bold: true, underline: true }),
      field(0, 182, w, 300, `Dear ${name},\n\nWrite the letter here. Text and fields can be mixed, e.g. ${second}. Long text wraps and the box grows (stretch with overflow).\n\nYours sincerely,`, { stretch: true, align: 'justified', fontSize: 11 }),
      { ...defaultElement('line', 0, h - 120), width: 180, height: 1 },
      text(0, h - 114, 220, 16, 'Authorised signatory', { bold: true }),
      text(0, h - 30, w, 14, 'This is a system generated letter.', { align: 'center', fontSize: 8, color: '#64748b' }),
      { ...defaultElement('break', 0, h - 2), width: w },
    ],
  };
  design.page.sheetLayout = 'grid';
  return design;
}

/** Label: value rows for every field, one block per record (Jasper "Columnar" / form report). */
function form(columns, title) {
  const design = base(A4);
  const w = usableWidth(design);
  const list = columns.length ? columns : ['Field1', 'Field2', 'Field3'];
  design.bands.title = { height: 44, elements: [text(0, 6, w, 28, title || 'Report', { bold: true, fontSize: 18 })] };
  design.bands.detail = {
    height: list.length * 18 + 18,
    elements: [
      ...list.flatMap((col, i) => [
        text(0, 4 + i * 18, 160, 16, col, { bold: true, color: '#334155', backColor: '#f1f5f9' }),
        field(164, 4 + i * 18, w - 164, 16, `$F{${col}}`, { stretch: true }),
      ]),
      { ...defaultElement('line', 0, list.length * 18 + 10), width: w, height: 1, borderColor: '#cbd5e1' },
    ],
  };
  design.bands.pageFooter = { height: 24, elements: [pageNumber(w)] };
  return design;
}

/** Landscape certificate with a border, one per record. */
function certificate(columns, title) {
  const design = base(A4_LANDSCAPE);
  const w = usableWidth(design);
  const h = fullHeight(design);
  design.bands.background = {
    height: h,
    elements: [
      { ...defaultElement('rectangle', 0, 0), width: w, height: h, backColor: null, borderColor: '#1e3a8a', borderWidth: 3 },
      { ...defaultElement('rectangle', 10, 10), width: w - 20, height: h - 20, backColor: null, borderColor: '#93c5fd', borderWidth: 1 },
    ],
  };
  design.bands.detail = {
    height: h,
    elements: [
      text(40, 60, w - 80, 40, title || 'Certificate of Achievement', { align: 'center', bold: true, fontSize: 30, color: '#1e3a8a', fontName: 'Serif' }),
      text(40, 140, w - 80, 20, 'This is to certify that', { align: 'center', fontSize: 14, italic: true }),
      field(40, 175, w - 80, 36, tokenFor(columns, 0, 'Name'), { align: 'center', bold: true, fontSize: 26, fontName: 'Serif' }),
      field(80, 230, w - 160, 60, `has successfully completed ${tokenFor(columns, 1, 'the programme')}.`, { align: 'center', fontSize: 14, stretch: true }),
      { ...defaultElement('line', 60, h - 90), width: 200, height: 1 },
      text(60, h - 84, 200, 16, 'Date', { align: 'center' }),
      field(60, h - 108, 200, 16, '$V{CURRENT_DATE}', { pattern: 'dd/MM/yyyy', align: 'center' }),
      { ...defaultElement('line', w - 260, h - 90), width: 200, height: 1 },
      text(w - 260, h - 84, 200, 16, 'Signature', { align: 'center' }),
      { ...defaultElement('break', 0, h - 2), width: w },
    ],
  };
  return design;
}

export const TEMPLATES = [
  { id: 'blank', name: 'Blank A4', text: 'An empty portrait page with Title, Detail and Page Footer. Design everything yourself.', needsFields: false, build: () => blank(A4) },
  { id: 'blank-landscape', name: 'Blank A4 landscape', text: 'An empty landscape page.', needsFields: false, build: () => blank(A4_LANDSCAPE) },
  { id: 'letter', name: 'Letter', text: 'Letterhead with logo, date, address, body text and signature. One letter per record (e.g. acceptance letters).', needsFields: false, build: letter },
  { id: 'table', name: 'Table report', text: 'Column headings and one row per record, from the fields (the old “Auto layout”).', needsFields: true, build: (columns, title, previous, options) => autoLayout(columns, {}, title, previous, options), hasOptions: true },
  { id: 'form', name: 'Form (label : value)', text: 'Every field as “label : value”, one block per record — for detail sheets and profiles.', needsFields: false, build: form },
  { id: 'certificate', name: 'Certificate / image page', text: 'Landscape page with a border in the Background band and big centered text. One per record.', needsFields: false, build: certificate },
];

export function applyTemplate(id, columns, title, previous, options) {
  const template = TEMPLATES.find((t) => t.id === id);
  const design = template.build(columns, title, previous, options);
  // keep the report's own groups, variables, calculated fields and sort when they still fit the fields
  return {
    ...design,
    calculatedFields: previous.calculatedFields || [],
    variables: (previous.variables || []).filter((v) => !v.field || columns.includes(v.field)),
    sort: previous.sort || [],
    filterExpression: previous.filterExpression || '',
    bands: { ...design.bands, groupHeaders: design.bands.groupHeaders || {}, groupFooters: design.bands.groupFooters || {} },
    groups: design.groups || [],
  };
}

export { emptyBand };
