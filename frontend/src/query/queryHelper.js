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
export const ready = (f) => !!f.field && (NO_VALUE.has(f.op) || String(f.value ?? "").trim() !== "");
export const newFilter = (field) => ({ id: `f${Date.now().toString(36)}${seq++}`, field, op: 'eq', value: '' });

const escapeRegex = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const number = (text) => /^-?\d+(\.\d+)?$/.test(text);
const list = (text) => String(text).split(',').map((s) => s.trim()).filter(Boolean);

/** A typed value as Mongo Shell text: 12 → 12, true → true, ObjectId("…") as it is, anything else in quotes. */
export function mongoValue(raw) {
  const text = String(raw ?? '').trim();
  if (number(text) || ['true', 'false', 'null'].includes(text)) return text;
  if (/^(ObjectId|ISODate|NumberLong|NumberInt)\(.*\)$/.test(text)) return text;
  return JSON.stringify(text.replace(/^"(.*)"$/, '$1'));
}

function mongoCondition(f) {
  const v = f.value;
  switch (f.op) {
    case 'ne': return `{ $ne: ${mongoValue(v)} }`;
    case 'gt': case 'gte': case 'lt': case 'lte': return `{ $${f.op}: ${mongoValue(v)} }`;
    case 'contains': return `{ $regex: ${JSON.stringify(escapeRegex(String(v)))}, $options: "i" }`;
    case 'starts': return `{ $regex: ${JSON.stringify(`^${escapeRegex(String(v))}`)} }`;
    case 'in': return `{ $in: [${list(v).map(mongoValue).join(', ')}] }`;
    case 'exists': return '{ $exists: true, $ne: null }';
    case 'missing': return '{ $in: [null, ""] }';
    default: return mongoValue(v);
  }
}

/** { filter, projection, sort } texts for the Documents view. */
export function buildMongo(helper) {
  const live = helper.filters.filter(ready);
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
  if (dialect === 'mysql') return `\`${name.replace(/`/g, '``')}\``;
  if (dialect === 'sqlserver') return `[${name.replace(/]/g, ']]')}]`;
  return `"${name.replace(/"/g, '""')}"`;
}

const sqlValue = (raw) => {
  const text = String(raw ?? '').trim();
  if (number(text) || /^\$P\{\w+\}$/.test(text)) return text;
  return `'${text.replace(/^'(.*)'$/, '$1').replace(/'/g, "''")}'`;
};

function sqlCondition(f, dialect) {
  const col = quoteName(f.field, dialect);
  const v = f.value;
  switch (f.op) {
    case 'ne': return `${col} <> ${sqlValue(v)}`;
    case 'gt': return `${col} > ${sqlValue(v)}`;
    case 'gte': return `${col} >= ${sqlValue(v)}`;
    case 'lt': return `${col} < ${sqlValue(v)}`;
    case 'lte': return `${col} <= ${sqlValue(v)}`;
    case 'contains': return `${col} LIKE ${sqlValue(`%${v}%`)}`;
    case 'starts': return `${col} LIKE ${sqlValue(`${v}%`)}`;
    case 'in': return `${col} IN (${list(v).map(sqlValue).join(', ') || 'NULL'})`;
    case 'exists': return `${col} IS NOT NULL`;
    case 'missing': return `${col} IS NULL`;
    default: return `${col} = ${sqlValue(v)}`;
  }
}

/** SELECT … FROM (the table, or the query the rows came from) WHERE … ORDER BY …  */
export function buildSql(helper, { source, base, dialect }) {
  const cols = helper.fields.length ? helper.fields.map((f) => quoteName(f, dialect)).join(', ') : '*';
  let from;
  if (source) from = `${source.schema && !['public', 'dbo'].includes(source.schema) ? `${quoteName(source.schema, dialect)}.` : ''}${quoteName(source.name, dialect)}`;
  else from = `(\n  ${String(base || '').trim().replace(/;\s*$/, '').replace(/\n/g, '\n  ')}\n) q`;
  const where = helper.filters.filter(ready).map((f) => sqlCondition(f, dialect));
  const order = helper.sort.map((s) => `${quoteName(s.field, dialect)}${s.dir === 'desc' ? ' DESC' : ''}`);
  return [`SELECT ${cols}`, `FROM ${from}`, where.length ? `WHERE ${where.join(helper.match === 'or' ? '\n   OR ' : '\n  AND ')}` : '', order.length ? `ORDER BY ${order.join(', ')}` : '']
    .filter(Boolean).join('\n');
}
