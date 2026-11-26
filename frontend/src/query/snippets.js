// Ready SQL for the Query page, written in the dialect of the data source (row limit, dates, text search differ).
// Each snippet uses `t` (a table) and `c` / `n` / `d` (a text, number and date column) from the schema when known.

const LIMIT = {
  sqlserver: (sql, n) => sql.replace(/^SELECT /, `SELECT TOP ${n} `),
  oracle: (sql, n) => `${sql}\nFETCH FIRST ${n} ROWS ONLY`,
};
export const limitRows = (sql, n, dialect) => (LIMIT[dialect] ? LIMIT[dialect](sql, n) : `${sql}\nLIMIT ${n}`);

const LAST_30 = {
  postgresql: (d) => `${d} >= CURRENT_DATE - INTERVAL '30 days'`,
  mysql: (d) => `${d} >= CURDATE() - INTERVAL 30 DAY`,
  mariadb: (d) => `${d} >= CURDATE() - INTERVAL 30 DAY`,
  sqlserver: (d) => `${d} >= DATEADD(day, -30, CAST(GETDATE() AS date))`,
  oracle: (d) => `${d} >= TRUNC(SYSDATE) - 30`,
  snowflake: (d) => `${d} >= DATEADD(day, -30, CURRENT_DATE())`,
  clickhouse: (d) => `${d} >= today() - 30`,
  elasticsearch: (d) => `${d} >= NOW() - INTERVAL 30 DAYS`,
  sqlite: (d) => `${d} >= date('now', '-30 days')`,
};
const lastDays = (dialect, d) => (LAST_30[dialect] || ((x) => `${x} >= CURRENT_DATE - INTERVAL 30 DAY`))(d);

const MONTH = {
  postgresql: (d) => `date_trunc('month', ${d})`,
  mysql: (d) => `DATE_FORMAT(${d}, '%Y-%m-01')`, mariadb: (d) => `DATE_FORMAT(${d}, '%Y-%m-01')`,
  sqlserver: (d) => `DATEFROMPARTS(YEAR(${d}), MONTH(${d}), 1)`,
  oracle: (d) => `TRUNC(${d}, 'MM')`, snowflake: (d) => `DATE_TRUNC('month', ${d})`,
  clickhouse: (d) => `toStartOfMonth(${d})`, elasticsearch: (d) => `DATE_TRUNC('month', ${d})`,
  sqlite: (d) => `strftime('%Y-%m-01', ${d})`,
};
const month = (dialect, d) => (MONTH[dialect] || ((x) => `date_trunc('month', ${x})`))(d);

const LIKE = { postgresql: 'ILIKE', snowflake: 'ILIKE', duckdb: 'ILIKE', files: 'ILIKE', gsheets: 'ILIKE', redis: 'ILIKE', clickhouse: 'ILIKE' };

/** The snippets for one dialect: [{ label, hint, sql }]. pick: { t, c, n, d } names (already quoted). */
export function snippets(dialect, pick = {}) {
  const t = pick.t || 'orders';
  const c = pick.c || 'city';
  const n = pick.n || 'amount';
  const d = pick.d || 'created_at';
  const like = LIKE[dialect] || 'LIKE';
  const out = [
    { label: 'First rows', hint: 'look at the data', sql: limitRows(`SELECT *\nFROM ${t}`, 100, dialect) },
    { label: 'Count per value', hint: 'GROUP BY with a count', sql: `SELECT ${c}, COUNT(*) AS records\nFROM ${t}\nGROUP BY ${c}\nORDER BY records DESC` },
    { label: 'Totals', hint: 'SUM / AVG / MIN / MAX', sql: `SELECT ${c},\n       SUM(${n}) AS total,\n       AVG(${n}) AS average,\n       MIN(${n}) AS smallest,\n       MAX(${n}) AS largest\nFROM ${t}\nGROUP BY ${c}\nORDER BY total DESC` },
    { label: 'Top 10', hint: 'the biggest values', sql: limitRows(`SELECT *\nFROM ${t}\nORDER BY ${n} DESC`, 10, dialect) },
    { label: 'Last 30 days', hint: 'a date filter in this database\'s words', sql: `SELECT *\nFROM ${t}\nWHERE ${lastDays(dialect, d)}\nORDER BY ${d} DESC` },
    { label: 'Per month', hint: 'a time series', sql: `SELECT ${month(dialect, d)} AS month, SUM(${n}) AS total\nFROM ${t}\nGROUP BY ${month(dialect, d)}\nORDER BY month` },
    { label: 'Text search', hint: `${like} with a parameter`, sql: `SELECT *\nFROM ${t}\nWHERE ${c} ${like} '%' || $P{Search} || '%'` },
    { label: 'Report parameters', hint: '$P{…} values and an IN list', sql: `SELECT *\nFROM ${t}\nWHERE ${d} BETWEEN $P{From} AND $P{To}\n  AND $X{IN, ${c}, Cities}` },
    { label: 'Missing values', hint: 'rows with an empty column', sql: `SELECT COUNT(*) AS records,\n       COUNT(${c}) AS with_value,\n       COUNT(*) - COUNT(${c}) AS empty\nFROM ${t}` },
  ];
  if (dialect === 'sqlserver' || dialect === 'mysql' || dialect === 'mariadb') {
    out[6] = { ...out[6], sql: `SELECT *\nFROM ${t}\nWHERE ${c} LIKE CONCAT('%', $P{Search}, '%')` };
  }
  if (dialect === 'elasticsearch') return out.filter((s) => !['Per month', 'Missing values'].includes(s.label));
  return out;
}

/** Dialect notes shown under the editor. */
export const DIALECT_TIPS = {
  postgresql: 'PostgreSQL · names with capitals need "quotes" · ILIKE for case-free search',
  mysql: 'MySQL · `back-ticks` around odd names · read-only transaction',
  mariadb: 'MariaDB · `back-ticks` around odd names · read-only transaction',
  sqlserver: 'SQL Server · [brackets] around odd names · TOP n instead of LIMIT',
  oracle: 'Oracle · FETCH FIRST n ROWS ONLY · names are UPPER CASE unless "quoted"',
  snowflake: 'Snowflake · names are UPPER CASE unless "quoted" · runs in your warehouse',
  clickhouse: 'ClickHouse · the server refuses writes (readonly) · toStartOfMonth(), today()',
  elasticsearch: 'Elasticsearch SQL · each index is a table · no sub-queries in FROM',
  redis: 'Redis · each key prefix is a table · _key, _id and the hash fields are columns',
  sqlite: 'SQLite file · read-only · date(\'now\') and strftime() for dates',
  duckdb: 'DuckDB file · read-only · ILIKE, date_trunc(), QUALIFY',
  files: 'Files · every file and sheet is a table (DuckDB SQL: ILIKE, date_trunc, QUALIFY)',
  gsheets: 'Google Sheet · every tab is a table · read again every 5 minutes',
};
