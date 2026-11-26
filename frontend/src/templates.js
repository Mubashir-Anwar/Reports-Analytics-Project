// Layout templates for a new design (like a classic report designer's New Report wizard templates).
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

/** Label: value rows for every field, one block per record (classic "Columnar" / form report). */
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

/* ---------- business layouts: invoice, challan, statement ---------- */

/** Which field plays which part in a layout (invoice number, amount…): a guess from the names, changed in the gallery. */
export const ROLES = {
  invoice: [
    ['number', 'Invoice number', /invoice|bill|voucher|order|challan|doc(ument)?_?no|^no$|number$|_no$/i, 'One invoice per value: a new page starts when it changes.'],
    ['customer', 'Customer', /customer|client|party|buyer|account_?name|^name$/i],
    ['date', 'Invoice date', /date/i],
    ['item', 'Item / description', /item|product|description|desc|service|particular|detail/i],
    ['qty', 'Quantity', /qty|quantity|units|pcs/i],
    ['rate', 'Rate / price', /rate|price/i],
    ['amount', 'Line amount', /amount|line_?total|value|net/i, 'Added up into the invoice total.'],
  ],
  statement: [
    ['account', 'Account', /account|acc_?no|customer|client|party|iban/i, 'One statement per value: a new page starts when it changes.'],
    ['date', 'Date', /date/i],
    ['description', 'Description', /description|narration|particular|remarks|detail|desc/i],
    ['amount', 'Amount (+ credit / − debit)', /amount|value|net/i, 'The running balance adds this up, row by row.'],
  ],
};

/** Guess a field for every role; each field is used once where possible. */
export function guessRoles(id, columns) {
  const used = new Set();
  const out = {};
  for (const [key, , pattern] of ROLES[id] || []) {
    const hit = columns.find((c) => !used.has(c) && pattern.test(c.split('.').pop()));
    out[key] = hit || '';
    if (hit) used.add(hit);
  }
  return out;
}

const PLACEHOLDER = { number: 'InvoiceNo', customer: 'Customer', date: 'InvoiceDate', item: 'Item', qty: 'Qty', rate: 'Rate', amount: 'Amount', account: 'Account', description: 'Description' };
const roleField = (roles, key) => roles?.[key] || PLACEHOLDER[key];
const GREY = '#64748b';
const HEAD = { bold: true, backColor: '#e2e8f0', fontSize: 9, vAlign: 'middle' };
const TOP_LINE = { top: 1, right: 0, bottom: 0, left: 0 };
const nameOr = (title, fallback) => (title && title !== 'New report' ? title : fallback);
const lefts = (cols) => cols.reduce((list, c, i) => [...list, i ? list[i - 1] + cols[i - 1][1] : 0], []);

/**
 * Helpers for a layout with field roles. A role the user left empty prints a grey "Qty?" placeholder text when the
 * report has fields (an unknown $F{…} would stop the report); without fields yet it stays $F{Qty} for later.
 */
function roleTools(id, columns, options) {
  const r = options.roles || guessRoles(id, columns);
  const known = (key) => Boolean(r[key]) || !columns.length;
  const value = (key, x, y, width, height, extra = {}) => (known(key)
    ? field(x, y, width, height, `$F{${roleField(r, key)}}`, extra)
    : text(x, y, width, height, `${PLACEHOLDER[key]}?`, { ...extra, italic: true, color: GREY, pattern: undefined }));
  return { r, known, value };
}

/** Header and footer go around each group when there is one, else once at the top (Title) and the end (Summary). */
function wrapInGroup(design, group, header, footer) {
  if (group) {
    design.bands.groupHeaders = { [group]: header };
    design.bands.groupFooters = { [group]: footer };
  } else {
    design.bands.title = header;
    design.bands.summary = { ...footer, elements: footer.elements.filter((el) => el.type !== 'break') };
  }
}

/** One invoice per invoice number: letterhead, bill-to, line items, total, signature; a new page for the next one. */
function invoice(columns, title, previous, options = {}) {
  const { r, known, value } = roleTools('invoice', columns, options);
  const design = base(A4);
  const w = usableWidth(design);
  const cols = [['#', 30, 'left'], ['Description', w - 280, 'left'], ['Qty', 60, 'right'], ['Rate', 90, 'right'], ['Amount', 100, 'right']];
  const xs = lefts(cols);
  const group = known('number') ? 'Invoice' : null;
  const reset = group ? { resetType: 'Group', resetGroup: group } : { resetType: 'Report', resetGroup: '' };
  design.groups = group ? [{ name: group, field: roleField(r, 'number') }] : [];
  design.variables = [
    { name: 'LineNo', calculation: 'Count', field: known('amount') ? roleField(r, 'amount') : columns[0] || 'Amount', ...reset },
    ...(known('amount') ? [{ name: 'InvoiceTotal', calculation: 'Sum', field: roleField(r, 'amount'), ...reset }] : []),
  ];
  const header = {
    height: 172,
    elements: [
      { ...defaultElement('image', 0, 0), width: 110, height: 46 },
      text(w - 300, 2, 300, 22, nameOr(title, 'Company name'), { align: 'right', bold: true, fontSize: 14 }),
      text(w - 300, 24, 300, 14, 'Address · Phone · NTN / Tax no.', { align: 'right', fontSize: 8, color: GREY }),
      { ...defaultElement('line', 0, 54), width: w, height: 1, borderColor: '#94a3b8' },
      text(0, 64, 200, 28, 'INVOICE', { bold: true, fontSize: 20, color: '#1e3a8a' }),
      text(w - 220, 66, 90, 16, 'Invoice no.', { color: GREY }),
      value('number', w - 130, 66, 130, 16, { bold: true, align: 'right' }),
      text(w - 220, 84, 90, 16, 'Date', { color: GREY }),
      r.date || !columns.length ? value('date', w - 130, 84, 130, 16, { pattern: 'dd/MM/yyyy', align: 'right' })
        : field(w - 130, 84, 130, 16, '$V{CURRENT_DATE}', { pattern: 'dd/MM/yyyy', align: 'right' }),
      text(0, 102, 200, 14, 'BILL TO', { fontSize: 8, bold: true, color: GREY }),
      value('customer', 0, 116, 300, 18, { bold: true, fontSize: 12 }),
      ...cols.map(([label, width, align], i) => text(xs[i], 150, width, 20, label, { ...HEAD, align })),
    ],
  };
  design.bands.detail = {
    height: 18,
    elements: [
      field(xs[0], 1, cols[0][1], 16, '$V{LineNo}', { fontSize: 9 }),
      value('item', xs[1], 1, cols[1][1], 16, { fontSize: 9, stretch: true }),
      value('qty', xs[2], 1, cols[2][1], 16, { fontSize: 9, align: 'right', pattern: '#,##0' }),
      value('rate', xs[3], 1, cols[3][1], 16, { fontSize: 9, align: 'right', pattern: '#,##0.00' }),
      value('amount', xs[4], 1, cols[4][1], 16, { fontSize: 9, align: 'right', pattern: '#,##0.00' }),
    ],
  };
  const total = { bold: true, fontSize: 11, align: 'right', pattern: '#,##0.00', vAlign: 'middle', backColor: '#f1f5f9' };
  const footer = {
    height: 120,
    elements: [
      { ...defaultElement('line', 0, 2), width: w, height: 1, borderColor: '#94a3b8' },
      text(w - 220, 8, 110, 20, 'Total', { bold: true, fontSize: 11, vAlign: 'middle' }),
      known('amount') ? field(w - 110, 8, 110, 20, '$V{InvoiceTotal}', total) : text(w - 110, 8, 110, 20, 'Total?', { ...total, italic: true, color: GREY, pattern: undefined }),
      text(0, 40, 280, 14, 'Thank you for your business.', { italic: true, color: GREY }),
      text(w - 180, 84, 180, 16, 'Authorised signature', { align: 'center', fontSize: 9, borders: TOP_LINE }),
      { ...defaultElement('break', 0, 118), width: w },
    ],
  };
  wrapInGroup(design, group, header, footer);
  design.bands.pageFooter = { height: 24, elements: [pageNumber(w)] };
  return design;
}

/** A fee challan / bank voucher: three copies side by side on a landscape page, one page per record. */
function challan(columns, title) {
  const design = base(A4_LANDSCAPE);
  const w = usableWidth(design);
  const h = fullHeight(design);
  const gap = 14;
  const cw = Math.floor((w - gap * 2) / 3);
  const list = (columns.length ? columns : ['ChallanNo', 'Name', 'RollNo', 'Class', 'DueDate', 'Amount']).slice(0, 8);
  const amount = list.find((c) => /amount|fee|total|payable/i.test(c)) || null;
  const rows = list.filter((c) => c !== amount);
  const copy = (x, label) => [
    { ...defaultElement('rectangle', x, 0), width: cw, height: h - 20, backColor: null, borderColor: '#334155', borderWidth: 1 },
    text(x, 8, cw, 18, nameOr(title, 'Institution / Bank name'), { align: 'center', bold: true, fontSize: 12 }),
    text(x, 26, cw, 12, 'Branch · Account no. 0000-0000000', { align: 'center', fontSize: 8, color: GREY }),
    text(x + 8, 44, cw - 16, 16, label, { align: 'center', bold: true, fontSize: 9, color: '#ffffff', backColor: '#334155', vAlign: 'middle' }),
    ...rows.flatMap((c, i) => [
      text(x + 8, 70 + i * 22, 90, 16, c.split('.').pop(), { fontSize: 8, color: GREY }),
      field(x + 100, 70 + i * 22, cw - 108, 16, `$F{${c}}`, { fontSize: 9, bold: true, borders: { top: 0, right: 0, bottom: 0.5, left: 0 }, ...(/date|time/i.test(c) ? { pattern: 'dd/MM/yyyy' } : {}) }),
    ]),
    text(x + 8, 80 + rows.length * 22, 90, 22, 'Amount', { bold: true, fontSize: 11, vAlign: 'middle' }),
    field(x + 100, 80 + rows.length * 22, cw - 108, 22, amount ? `$F{${amount}}` : '$F{Amount}', { bold: true, fontSize: 12, align: 'right', pattern: '#,##0.00', vAlign: 'middle', backColor: '#f1f5f9', borderWidth: 1, borderColor: '#334155' }),
    text(x + 8, h - 80, Math.floor(cw / 2) - 12, 14, 'Depositor', { align: 'center', fontSize: 8, borders: TOP_LINE }),
    text(x + Math.floor(cw / 2) + 4, h - 80, Math.floor(cw / 2) - 12, 14, 'Bank officer / stamp', { align: 'center', fontSize: 8, borders: TOP_LINE }),
    field(x + 8, h - 44, cw - 16, 12, '$V{CURRENT_DATE}', { fontSize: 7, color: GREY, pattern: "'Printed' dd/MM/yyyy" }),
  ];
  design.bands.detail = {
    height: h,
    elements: [...copy(0, 'BANK COPY'), ...copy(cw + gap, 'OFFICE COPY'), ...copy((cw + gap) * 2, 'CUSTOMER COPY'), { ...defaultElement('break', 0, h - 2), width: w }],
  };
  return design;
}

/** An account statement: one per account, the rows with a running balance, closing balance at the end. */
function statement(columns, title, previous, options = {}) {
  const { r, known, value } = roleTools('statement', columns, options);
  const design = base(A4);
  const w = usableWidth(design);
  const cols = [['Date', 90, 'left'], ['Description', w - 290, 'left'], ['Amount', 100, 'right'], ['Balance', 100, 'right']];
  const xs = lefts(cols);
  const money = '#,##0.00;(#,##0.00)';
  const group = known('account') ? 'Account' : null;
  design.groups = group ? [{ name: group, field: roleField(r, 'account') }] : [];
  design.variables = known('amount')
    ? [{ name: 'Balance', calculation: 'Sum', field: roleField(r, 'amount'), ...(group ? { resetType: 'Group', resetGroup: group } : { resetType: 'Report', resetGroup: '' }) }]
    : [];
  const balance = (x, y, width, height, extra) => (known('amount')
    ? field(x, y, width, height, '$V{Balance}', { ...extra, pattern: money })
    : text(x, y, width, height, 'Balance?', { ...extra, italic: true, color: GREY }));
  const header = {
    height: 118,
    elements: [
      text(0, 0, w - 200, 24, nameOr(title, 'Account statement'), { bold: true, fontSize: 16 }),
      field(w - 200, 4, 200, 14, '$V{CURRENT_DATE}', { align: 'right', fontSize: 8, color: GREY, pattern: "'Printed' dd/MM/yyyy" }),
      { ...defaultElement('rectangle', 0, 34), width: w, height: 52, backColor: '#f8fafc', borderColor: '#cbd5e1', borderWidth: 1 },
      text(10, 42, 100, 14, 'Account', { fontSize: 8, color: GREY }),
      value('account', 10, 56, Math.floor(w / 2), 20, { bold: true, fontSize: 13 }),
      ...cols.map(([label, width, align], i) => text(xs[i], 96, width, 20, label, { ...HEAD, align })),
    ],
  };
  design.bands.detail = {
    height: 18,
    elements: [
      value('date', xs[0], 1, cols[0][1], 16, { fontSize: 9, pattern: 'dd/MM/yyyy' }),
      value('description', xs[1], 1, cols[1][1], 16, { fontSize: 9, stretch: true }),
      value('amount', xs[2], 1, cols[2][1], 16, { fontSize: 9, align: 'right', pattern: money }),
      balance(xs[3], 1, cols[3][1], 16, { fontSize: 9, align: 'right', bold: true }),
    ],
  };
  const footer = {
    height: 44,
    elements: [
      { ...defaultElement('line', 0, 2), width: w, height: 1, borderColor: '#94a3b8' },
      text(xs[2] - 120, 8, 220, 20, 'Closing balance', { bold: true, align: 'right', vAlign: 'middle' }),
      balance(xs[3], 8, cols[3][1], 20, { bold: true, align: 'right', vAlign: 'middle', backColor: '#f1f5f9' }),
      { ...defaultElement('break', 0, 42), width: w },
    ],
  };
  wrapInGroup(design, group, header, footer);
  design.bands.pageFooter = { height: 24, elements: [pageNumber(w)] };
  return design;
}

export const TEMPLATES = [
  { id: 'blank', name: 'Blank A4', text: 'An empty portrait page with Title, Detail and Page Footer. Design everything yourself.', needsFields: false, build: () => blank(A4) },
  { id: 'blank-landscape', name: 'Blank A4 landscape', text: 'An empty landscape page.', needsFields: false, build: () => blank(A4_LANDSCAPE) },
  { id: 'letter', name: 'Letter', text: 'Letterhead with logo, date, address, body text and signature. One letter per record (e.g. acceptance letters).', needsFields: false, build: letter },
  { id: 'table', name: 'Table report', text: 'Column headings and one row per record, from the fields (the old “Auto layout”).', needsFields: true, build: (columns, title, previous, options) => autoLayout(columns, Object.fromEntries(Object.entries(previous?.fieldFormats || {}).filter(([, f]) => f.label).map(([k, f]) => [k, f.label])), title, previous, options), hasOptions: true },
  { id: 'form', name: 'Form (label : value)', text: 'Every field as “label : value”, one block per record — for detail sheets and profiles.', needsFields: false, build: form },
  { id: 'invoice', name: 'Invoice', text: 'Letterhead, bill-to, line items and the total — one invoice per page, by invoice number.', needsFields: false, build: invoice, hasRoles: true },
  { id: 'challan', name: 'Fee challan / voucher', text: 'Bank, office and customer copy side by side on one landscape page — one per record.', needsFields: false, build: challan },
  { id: 'statement', name: 'Account statement', text: 'One statement per account: every entry with a running balance and the closing balance.', needsFields: false, build: statement, hasRoles: true },
  { id: 'certificate', name: 'Certificate / image page', text: 'Landscape page with a border in the Background band and big centered text. One per record.', needsFields: false, build: certificate },
];

export function applyTemplate(id, columns, title, previous, options) {
  const template = TEMPLATES.find((t) => t.id === id);
  const design = template.build(columns, title, previous, options);
  // keep the report's own groups, variables, calculated fields and sort when they still fit the fields
  return {
    ...design,
    calculatedFields: previous.calculatedFields || [],
    functions: previous.functions || [],
    ...(previous.fieldFormats ? { fieldFormats: previous.fieldFormats } : {}),
    // the template's own variables (e.g. the invoice total) first, then the report's ones that still fit
    // (a variable that resets on a group the new layout does not have is dropped: it would stop the report)
    variables: [...(design.variables || []), ...(previous.variables || []).filter((v) => (!v.field || columns.includes(v.field))
      && !(design.variables || []).some((t) => t.name === v.name)
      && (v.resetType !== 'Group' || (design.groups || []).some((g) => g.name === v.resetGroup)))],
    sort: previous.sort || [],
    filterExpression: previous.filterExpression || '',
    bands: { ...design.bands, groupHeaders: design.bands.groupHeaders || {}, groupFooters: design.bands.groupFooters || {} },
    groups: design.groups || [],
  };
}

export { emptyBand };
