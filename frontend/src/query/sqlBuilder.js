// The SQL query builder (Query → a SQL database → "Query builder"): tables put on a canvas and joined on their
// columns, the fields to show (with totals), filters, group, having, sort and a row limit. This file turns that
// state into one SELECT for PostgreSQL, MySQL, SQL Server or Oracle; it is shown live and can be run or edited.
import { quoteName } from './queryHelper.js';

export const JOINS = [['inner', 'INNER JOIN', 'only rows that match on both sides'], ['left', 'LEFT JOIN', 'every row on the left, matches when there are'],
  ['right', 'RIGHT JOIN', 'every row on the right'], ['full', 'FULL JOIN', 'every row of both']];
export const AGGS = [['', 'value'], ['sum', 'SUM'], ['count', 'COUNT'], ['countd', 'COUNT DISTINCT'], ['avg', 'AVG'], ['min', 'MIN'], ['max', 'MAX']];
export const OPS = [['eq', '='], ['ne', '≠'], ['gt', '>'], ['gte', '≥'], ['lt', '<'], ['lte', '≤'], ['contains', 'contains'], ['starts', 'starts with'],
  ['in', 'is one of'], ['between', 'between'], ['null', 'is empty'], ['notnull', 'has a value']];
export const NO_VALUE = new Set(['null', 'notnull']);

export const emptyBuilder = () => ({ tables: [], joins: [], fields: [], filters: [], having: [], sort: [], match: 'and', distinct: false, limit: 200 });

let seq = 0;
export const newId = (prefix) => `${prefix}${Date.now().toString(36)}${(seq++).toString(36)}`;

/** A short alias for a table that is not used yet: o, o2, …  */
export function aliasFor(name, tables) {
  const used = new Set(tables.map((t) => t.alias));
  const base = (String(name).replace(/[^A-Za-z]/g, '').slice(0, 1) || 't').toLowerCase();
  if (!used.has(base)) return base;
  for (let i = 2; ; i += 1) if (!used.has(`${base}${i}`)) return `${base}${i}`;
}

const isNumber = (text) => /^-?\d+(\.\d+)?$/.test(text);
const isParam = (text) => /^\$P\{\w+\}$/.test(text);
const NUMERIC = /int|numeric|decimal|real|double|float|money|number|serial/i;

/** A value as SQL: numbers and $P{…} stay, the rest is a quoted text. */
export function sqlLiteral(raw, columnType = '') {
  const text = String(raw ?? '').trim();
  if (isParam(text)) return text;
  if (isNumber(text) && (NUMERIC.test(columnType) || !columnType)) return text;
  if (/^(true|false)$/i.test(text) && /bool/i.test(columnType)) return text.toUpperCase();
  return `'${text.replace(/^'(.*)'$/, '$1').replace(/'/g, "''")}'`;
}

const listOf = (text) => String(text ?? '').split(',').map((s) => s.trim()).filter(Boolean);

/** "o"."Amount" (or o.Amount) */
export const colRef = (alias, column, dialect) => `${alias}.${quoteName(column, dialect)}`;

function tableRef(table, dialect) {
  const schema = table.schema && !['public', 'dbo', 'main'].includes(table.schema) ? `${quoteName(table.schema, dialect)}.` : '';
  return `${schema}${quoteName(table.name, dialect)} ${table.alias}`;
}

/** The expression of a field: o.Amount, SUM(o.Amount), COUNT(*), COUNT(DISTINCT o.Branch). */
export function fieldExpr(field, dialect) {
  const col = field.column === '*' ? '*' : colRef(field.alias, field.column, dialect);
  switch (field.agg) {
    case 'sum': return `SUM(${col})`;
    case 'count': return `COUNT(${col})`;
    case 'countd': return `COUNT(DISTINCT ${col})`;
    case 'avg': return `AVG(${col})`;
    case 'min': return `MIN(${col})`;
    case 'max': return `MAX(${col})`;
    default: return col;
  }
}

/** The name a field gets in the result: its "as", else total_Amount / Amount. */
export const fieldName = (field) => field.as || (field.agg ? `${field.agg === 'countd' ? 'distinct' : field.agg}_${field.column === '*' ? 'rows' : field.column}` : field.column);

function condition(expr, rule, columnType) {
  const v = rule.value;
  switch (rule.op) {
    case 'ne': return `${expr} <> ${sqlLiteral(v, columnType)}`;
    case 'gt': return `${expr} > ${sqlLiteral(v, columnType)}`;
    case 'gte': return `${expr} >= ${sqlLiteral(v, columnType)}`;
    case 'lt': return `${expr} < ${sqlLiteral(v, columnType)}`;
    case 'lte': return `${expr} <= ${sqlLiteral(v, columnType)}`;
    case 'contains': return `${expr} LIKE ${sqlLiteral(`%${String(v ?? '').trim()}%`, 'text')}`;
    case 'starts': return `${expr} LIKE ${sqlLiteral(`${String(v ?? '').trim()}%`, 'text')}`;
    case 'in': return `${expr} IN (${listOf(v).map((x) => sqlLiteral(x, columnType)).join(', ') || 'NULL'})`;
    case 'between': { const [a, b] = listOf(v); return `${expr} BETWEEN ${sqlLiteral(a, columnType)} AND ${sqlLiteral(b ?? a, columnType)}`; }
    case 'null': return `${expr} IS NULL`;
    case 'notnull': return `${expr} IS NOT NULL`;
    default: return `${expr} = ${sqlLiteral(v, columnType)}`;
  }
}

/** A filter row counts once it has a column and (unless the operator needs none) a value. */
export const ruleReady = (rule) => !rule.off && rule.alias && rule.column && (NO_VALUE.has(rule.op) || String(rule.value ?? '').trim() !== '');

/** Which tables cannot be reached from the first one through the joins (they would make a cross join). */
export function unjoined(state) {
  if (state.tables.length < 2) return [];
  const reached = new Set([state.tables[0].alias]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const j of state.joins) {
      if (reached.has(j.left.alias) && !reached.has(j.right.alias)) { reached.add(j.right.alias); grew = true; }
      if (reached.has(j.right.alias) && !reached.has(j.left.alias)) { reached.add(j.left.alias); grew = true; }
    }
  }
  return state.tables.filter((t) => !reached.has(t.alias)).map((t) => t.alias);
}

/** { sql, problems }: the SELECT for this builder state (problems: what keeps it from being right). */
export function buildSelect(state, { dialect = 'postgresql', columnType = () => '' } = {}) {
  const problems = [];
  const tables = state.tables || [];
  if (!tables.length) return { sql: '', problems: ['Drag a table onto the canvas'] };
  const byAlias = Object.fromEntries(tables.map((t) => [t.alias, t]));
  const fields = (state.fields || []).filter((f) => byAlias[f.alias] || f.column === '*');
  const typeOf = (alias, column) => columnType(byAlias[alias], column) || '';

  // SELECT
  const select = fields.length
    ? fields.map((f) => {
      const expr = fieldExpr(f, dialect);
      const name = fieldName(f);
      return f.agg || f.as ? `${expr} AS ${quoteName(name, dialect)}` : expr;
    })
    : ['*'];
  const top = dialect === 'sqlserver' && state.limit ? `TOP ${Number(state.limit)} ` : '';
  const lines = [`SELECT ${state.distinct ? 'DISTINCT ' : ''}${top}${select.join(',\n       ')}`];

  // FROM + JOINs: walk out from the first table so every join names a table that is already there
  lines.push(`FROM ${tableRef(tables[0], dialect)}`);
  const placed = new Set([tables[0].alias]);
  const steps = [];                                  // [{ alias, word, on: [conditions] }] in the order they join
  const pending = [...(state.joins || [])].filter((j) => byAlias[j.left.alias] && byAlias[j.right.alias] && j.left.alias !== j.right.alias);
  let progress = true;
  while (pending.length && progress) {
    progress = false;
    for (let i = 0; i < pending.length; i += 1) {
      const j = pending[i];
      const leftIn = placed.has(j.left.alias);
      const rightIn = placed.has(j.right.alias);
      if (!leftIn && !rightIn) continue;
      pending.splice(i, 1); i -= 1; progress = true;
      const on = `${colRef(j.left.alias, j.left.column, dialect)} = ${colRef(j.right.alias, j.right.column, dialect)}`;
      if (leftIn && rightIn) {                       // both are there already: one more condition on the later join
        const later = [...steps].reverse().find((s) => s.alias === j.left.alias || s.alias === j.right.alias);
        if (later) later.on.push(on);
        continue;
      }
      const joining = leftIn ? j.right.alias : j.left.alias;
      let how = j.how || 'inner';
      if (!leftIn && (how === 'left' || how === 'right')) how = how === 'left' ? 'right' : 'left';   // written from the other side
      steps.push({ alias: joining, word: JOINS.find(([id]) => id === how)?.[1] || 'INNER JOIN', on: [on] });
      placed.add(joining);
    }
  }
  steps.forEach((s) => lines.push(`${s.word} ${tableRef(byAlias[s.alias], dialect)}\n  ON ${s.on.join('\n AND ')}`));
  const loose = tables.filter((t) => !placed.has(t.alias));
  if (loose.length) {
    problems.push(`Join ${loose.map((t) => t.name).join(', ')}: drag one of its columns onto a column of another table`);
    loose.forEach((t) => lines.push(`CROSS JOIN ${tableRef(t, dialect)}`));
  }

  // WHERE
  const where = (state.filters || []).filter(ruleReady).map((r) => condition(colRef(r.alias, r.column, dialect), r, typeOf(r.alias, r.column)));
  if (where.length) lines.push(`WHERE ${where.join(state.match === 'or' ? '\n   OR ' : '\n  AND ')}`);

  // GROUP BY: every field without a total, when some field has one
  const totals = fields.some((f) => f.agg);
  if (totals) {
    const groups = fields.filter((f) => !f.agg && f.column !== '*').map((f) => colRef(f.alias, f.column, dialect));
    if (groups.length) lines.push(`GROUP BY ${groups.join(', ')}`);
  }
  const having = (state.having || []).filter((r) => ruleReady({ ...r, alias: r.alias || '*', column: r.column || '*' }))
    .map((r) => condition(fieldExpr({ alias: r.alias, column: r.column || '*', agg: r.agg || 'count' }, dialect), r, 'numeric'));
  if (having.length) {
    if (!totals) problems.push('HAVING needs a total (SUM, COUNT …) among the fields');
    lines.push(`HAVING ${having.join('\n   AND ')}`);
  }

  // ORDER BY: a field by its name in the result, or a column
  const order = (state.sort || []).filter((s) => s.field).map((s) => {
    const field = fields.find((f) => f.id === s.field);
    const expr = field ? (field.agg || field.as ? quoteName(fieldName(field), dialect) : fieldExpr(field, dialect)) : s.field;
    return `${expr}${s.dir === 'desc' ? ' DESC' : ''}`;
  });
  if (order.length) lines.push(`ORDER BY ${order.join(', ')}`);

  // the row limit, in the database's own words
  const limit = Number(state.limit) || 0;
  if (limit > 0) {
    if (dialect === 'oracle') lines.push(`FETCH FIRST ${limit} ROWS ONLY`);
    else if (dialect !== 'sqlserver') lines.push(`LIMIT ${limit}`);
  }
  return { sql: lines.join('\n'), problems };
}

/** The joins the database's foreign keys suggest between a new table and the ones on the canvas. */
export function suggestJoins(table, tables, foreignKeys = []) {
  const out = [];
  for (const other of tables) {
    for (const fk of foreignKeys) {
      const here = (s, t) => s === (table.schema || '') && t === table.name;
      const there = (s, t) => s === (other.schema || '') && t === other.name;
      if (here(fk.schema, fk.table) && there(fk.refSchema, fk.refTable)) {
        out.push({ id: newId('j'), how: 'inner', left: { alias: other.alias, column: fk.refColumn }, right: { alias: table.alias, column: fk.column } });
      } else if (there(fk.schema, fk.table) && here(fk.refSchema, fk.refTable)) {
        out.push({ id: newId('j'), how: 'inner', left: { alias: other.alias, column: fk.column }, right: { alias: table.alias, column: fk.refColumn } });
      }
    }
  }
  if (!out.length) {                                  // no keys: the same column name, or <table>_id / <table>id
    for (const other of tables) {
      const id = (table.columns || []).find((c) => c.pk)?.name;
      const match = id && (other.columns || []).find((c) => [`${table.name}_${id}`, `${table.name}${id}`].map((x) => x.toLowerCase()).includes(c.name.toLowerCase()));
      if (match) out.push({ id: newId('j'), how: 'inner', left: { alias: other.alias, column: match.name }, right: { alias: table.alias, column: id } });
    }
  }
  return out.slice(0, 1);
}
