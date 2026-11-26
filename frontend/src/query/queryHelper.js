// The query helper (Studio 3T's "Visual Query Builder"): columns dragged from the result become conditions, the
// columns to show and the sort order; this file turns that into a Mongo find (filter / projection / sort) or SQL.
export const FIELD_MIME = 'application/x-q-field';

export const OPS = [
  ['eq', 'equals'], ['ne', 'not equal'], ['gt', '>'], ['gte', '≥'], ['lt', '<'], ['lte', '≤'],
  ['contains', 'contains'], ['starts', 'starts with'], ['in', 'is one of'], ['exists', 'has a value'], ['missing', 'is empty'],
];
export const NO_VALUE = new Set(['exists', 'missing']);
export const emptyHelper = () => ({ match: 'and', filters: [], fields: [], sort: [] });
let seq = 0;
/** A filter row counts once it has a field and, unless the operator needs none, a value. */
// a filter row counts when it is switched on (its checkbox), has a field, and has a value when its operator needs one
export const ready = (f) => !f.off && !!f.field && (NO_VALUE.has(f.op) || String(f.value ?? "").trim() !== "");
/** `what`: a field name, or what was dragged: {field, type, value, op}. */
export const newFilter = (what) => {
  const { field, type = '', value = '', op = 'eq' } = typeof what === 'string' ? { field: what } : what;
  return { id: `f${Date.now().toString(36)}${seq++}`, field, op, value, type };
};

// ---------------------------------------------------------------- data types, read from the data itself
export const TYPES = [['', 'Auto'], ['str', 'Text'], ['num', 'Number'], ['bool', 'True / false'], ['date', 'Date'], ['oid', 'ObjectId']];
const TYPE_KEYS = new Set(TYPES.map(([key]) => key));

/** str, num, bool, date, oid, arr, obj or null: the kind of one (Extended JSON) value. */
export function valueType(value) {
  if (value === null || value === undefined) return 'null';
  if (typeof value === 'number') return 'num';
  if (typeof value === 'boolean') return 'bool';
  if (typeof value !== 'object') return 'str';
  if (Array.isArray(value)) return 'arr';
  if ('$oid' in value) return 'oid';
  if ('$date' in value) return 'date';
  if (['$numberDecimal', '$numberLong', '$numberInt', '$numberDouble'].some((key) => key in value)) return 'num';
  return 'obj';
}

/** A value as it is typed in a filter box. */
export function valueText(value) {
  const type = valueType(value);
  if (type === 'oid') return value.$oid;
  if (type === 'date') return typeof value.$date === 'string' ? value.$date : new Date(Number(value.$date?.$numberLong ?? value.$date)).toISOString();
  if (type === 'num' && typeof value === 'object') return String(Object.values(value)[0]);
  return ['null', 'arr', 'obj'].includes(type) ? '' : String(value);
}

/** {path: {str: 3, num: 1}}: every field of the documents (also nested ones) with the types it has. */
export function fieldTypes(documents) {
  const out = {};
  const walk = (value, path, depth) => {
    const type = valueType(value);
    if (path) { out[path] = out[path] || {}; out[path][type] = (out[path][type] || 0) + 1; }
    if (depth > 6) return;
    if (type === 'obj') Object.entries(value).forEach(([key, inner]) => walk(inner, path ? `${path}.${key}` : key, depth + 1));
    if (type === 'arr') value.slice(0, 20).forEach((inner) => {
      if (valueType(inner) === 'obj') Object.entries(inner).forEach(([key, v]) => walk(v, `${path}.${key}`, depth + 1));
      else { const t = valueType(inner); out[path][t] = (out[path][t] || 0) + 1; }
    });
  };
  documents.forEach((doc) => walk(doc, '', 0));
  return out;
}

/** The one type of a field, '' when it is mixed or unknown. */
export function onlyType(types) {
  const known = Object.keys(types || {}).filter((type) => TYPE_KEYS.has(type) && type);
  return known.length === 1 ? known[0] : '';
}

// ---------------------------------------------------------------- dragging a field (and its value)
/** What a dragged cell / tree node stands for: its field, the type of the value, and the value when there is one. */
export const asField = (field, value) => {
  const type = valueType(value);
  return { field, type: TYPE_KEYS.has(type) ? type : '', value: valueText(value), ...(type === 'null' ? { op: 'missing' } : {}) };
};
export function startDrag(event, what) {
  event.stopPropagation();
  event.dataTransfer.setData(FIELD_MIME, JSON.stringify(what));
  event.dataTransfer.setData('text/plain', what.field);
  event.dataTransfer.effectAllowed = 'copyMove';
}
export function readDrag(event) {
  try { const what = JSON.parse(event.dataTransfer.getData(FIELD_MIME)); return what?.field ? what : null; } catch { return null; }
}

/** A dragged field put into a Mongo text: { … } gets one more entry. */
export function addToText(text, what, kind = 'filter') {
  const entry = `${JSON.stringify(what.field)}: ${kind === 'filter' ? (ready({ op: 'eq', ...what }) ? mongoCondition({ op: 'eq', ...what }) : '{ $exists: true }') : '1'}`;
  const body = String(text || '').trim();
  if (!body || /^\{\s*\}$/.test(body)) return `{ ${entry} }`;
  if (!body.endsWith('}')) return `${body} ${entry}`;
  const inner = body.slice(0, -1).replace(/[\s,]+$/, '');
  return `${inner}, ${entry} }`;
}

const escapeRegex = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const number = (text) => /^-?\d+(\.\d+)?$/.test(text);
const list = (text) => String(text).split(',').map((s) => s.trim()).filter(Boolean);

const day = /^\d{4}-\d{2}-\d{2}$/;
const isoDate = (text) => {
  const full = day.test(text) ? `${text}T00:00:00Z` : text.replace(' ', 'T');
  const zoned = /(Z|[+-]\d{2}:?\d{2})$/.test(full) ? full : `${full}Z`;
  return Number.isNaN(Date.parse(zoned)) ? null : zoned;
};

/** A typed value as Mongo Shell text. With a type (read from the data) 123 in a text field is "123"; without one:
 *  12 → 12, true → true, ObjectId("…") as it is, anything else in quotes. */
export function mongoValue(raw, type = '') {
  const text = String(raw ?? '').trim();
  if (type === 'str') return JSON.stringify(text.replace(/^"(.*)"$/, '$1'));
  if (type === 'oid' && /^[0-9a-f]{24}$/i.test(text)) return `ObjectId("${text}")`;
  if (type === 'date' && isoDate(text)) return `ISODate("${isoDate(text)}")`;
  if (number(text) || ['true', 'false', 'null'].includes(text)) return text;
  if (/^(ObjectId|ISODate|NumberLong|NumberInt)\(.*\)$/.test(text)) return text;
  return JSON.stringify(text.replace(/^"(.*)"$/, '$1'));
}

function mongoCondition(f) {
  const v = f.value;
  switch (f.op) {
    case 'ne': return f.both && number(String(v).trim()) ? `{ $nin: [${mongoValue(v, 'str')}, ${mongoValue(v)}] }` : `{ $ne: ${mongoValue(v, f.type)} }`;
    case 'gt': case 'gte': case 'lt': case 'lte': return `{ $${f.op}: ${mongoValue(v, f.type)} }`;
    case 'contains': return `{ $regex: ${JSON.stringify(escapeRegex(String(v)))}, $options: "i" }`;
    case 'starts': return `{ $regex: ${JSON.stringify(`^${escapeRegex(String(v))}`)} }`;
    case 'in': return `{ $in: [${list(v).map((x) => mongoValue(x, f.type)).join(', ')}] }`;
    case 'exists': return '{ $exists: true, $ne: null }';
    case 'missing': return '{ $in: [null, ""] }';
    default:
      // a day picked for a date field means that whole day
      if (f.type === 'date' && day.test(String(v).trim())) {
        const start = new Date(`${String(v).trim()}T00:00:00Z`);
        if (!Number.isNaN(start.getTime())) return `{ $gte: ISODate("${String(v).trim()}T00:00:00Z"), $lt: ISODate("${new Date(start.getTime() + 86400000).toISOString().slice(0, 10)}T00:00:00Z") }`;
      }
      // the field holds text in some documents and numbers in others: find both
      if (f.both && !f.type && number(String(v).trim())) return `{ $in: [${mongoValue(v, 'str')}, ${mongoValue(v)}] }`;
      return mongoValue(v, f.type);
  }
}

/** A filter row with the type the data says, unless one was chosen; `both` = text and numbers are mixed there. */
export const withType = (f, types) => ({ ...f, type: f.type || onlyType(types[f.field]), both: !!(types[f.field]?.str && types[f.field]?.num) });

/** { filter, projection, sort } texts for the Documents view. */
export function buildMongo(helper, types = {}) {
  const live = helper.filters.filter(ready).map((f) => withType(f, types));
  const parts = live.map((f) => `${JSON.stringify(f.field)}: ${mongoCondition(f)}`);
  const fields = live.map((f) => f.field);
  const repeated = fields.some((f, i) => fields.indexOf(f) !== i);
  let filter = '';
  if (parts.length === 1) filter = `{ ${parts[0]} }`;
  else if (parts.length > 1) {
    filter = helper.match === 'or' || repeated
      ? `{ $${helper.match === 'or' ? 'or' : 'and'}: [${parts.map((p) => `{ ${p} }`).join(', ')}] }`
      : `{ ${parts.join(', ')} }`;
  }
  const projection = helper.fields.length ? `{ ${helper.fields.map((f) => `${JSON.stringify(f)}: 1`).join(', ')} }` : '';
  const sort = helper.sort.length ? `{ ${helper.sort.map((s) => `${JSON.stringify(s.field)}: ${s.dir === 'desc' ? -1 : 1}`).join(', ')} }` : '';
  return { filter, projection, sort };
}

export function quoteName(name, dialect) {
  if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) return name;
  if (dialect === 'mysql' || dialect === 'mariadb') return `\`${name.replace(/`/g, '``')}\``;
  if (dialect === 'sqlserver') return `[${name.replace(/]/g, ']]')}]`;
  return `"${name.replace(/"/g, '""')}"`;
}

const sqlValue = (raw, type = '') => {
  const text = String(raw ?? '').trim();
  if (type === 'str' && !/^\$P\{\w+\}$/.test(text)) return `'${text.replace(/^'(.*)'$/, '$1').replace(/'/g, "''")}'`;
  if (number(text) || /^\$P\{\w+\}$/.test(text)) return text;
  return `'${text.replace(/^'(.*)'$/, '$1').replace(/'/g, "''")}'`;
};

function sqlCondition(f, dialect) {
  const col = quoteName(f.field, dialect);
  const v = f.value;
  switch (f.op) {
    case 'ne': return `${col} <> ${sqlValue(v, f.type)}`;
    case 'gt': return `${col} > ${sqlValue(v, f.type)}`;
    case 'gte': return `${col} >= ${sqlValue(v, f.type)}`;
    case 'lt': return `${col} < ${sqlValue(v, f.type)}`;
    case 'lte': return `${col} <= ${sqlValue(v, f.type)}`;
    case 'contains': return `${col} LIKE ${sqlValue(`%${v}%`, 'str')}`;
    case 'starts': return `${col} LIKE ${sqlValue(`${v}%`, 'str')}`;
    case 'in': return `${col} IN (${list(v).map((x) => sqlValue(x, f.type)).join(', ') || 'NULL'})`;
    case 'exists': return `${col} IS NOT NULL`;
    case 'missing': return `${col} IS NULL`;
    default: return `${col} = ${sqlValue(v, f.type)}`;
  }
}

/** SELECT … FROM (the table, or the query the rows came from) WHERE … ORDER BY …  */
export function buildSql(helper, { source, base, dialect, types = {} }) {
  const cols = helper.fields.length ? helper.fields.map((f) => quoteName(f, dialect)).join(', ') : '*';
  let from;
  if (source) from = `${source.schema && !['public', 'dbo'].includes(source.schema) ? `${quoteName(source.schema, dialect)}.` : ''}${quoteName(source.name, dialect)}`;
  else from = `(\n  ${String(base || '').trim().replace(/;\s*$/, '').replace(/\n/g, '\n  ')}\n) q`;
  const where = helper.filters.filter(ready).map((f) => sqlCondition(withType(f, types), dialect));
  const order = helper.sort.map((s) => `${quoteName(s.field, dialect)}${s.dir === 'desc' ? ' DESC' : ''}`);
  return [`SELECT ${cols}`, `FROM ${from}`, where.length ? `WHERE ${where.join(helper.match === 'or' ? '\n   OR ' : '\n  AND ')}` : '', order.length ? `ORDER BY ${order.join(', ')}` : '']
    .filter(Boolean).join('\n');
}
