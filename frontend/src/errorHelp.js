// Turn server errors into: a short title, the message, WHERE to fix it (tab → section → field / line), the steps,
// and a "Go there" button that opens that place and highlights it. checkSetup() lists every problem of a report it can
// see without the server, so several mistakes are shown at once instead of one after another.
//
// A focus is { target, name?, line?, note? }:
//   target = details | adapter | language | collection | query | fields | parameters | design | datasets
//   name   = the parameter / field it is about, line = the query line.

const SETUP = 'Setup → 2 Data adapter';
export const PLACES = {
  details: 'Setup → 1 Report details',
  adapter: SETUP,
  language: `${SETUP} → Query language`,
  collection: `${SETUP} → Collection name`,
  query: 'Setup → 3 Query',
  fields: 'Setup → 4 Fields',
  parameters: 'Setup → 5 Parameters',
  design: 'Design tab',
  datasets: 'Dataset and Query → Datasets',
};

export function placeOf(focus) {
  if (!focus) return '';
  let text = PLACES[focus.target] || '';
  if (focus.target === 'query' && focus.line) text += `, line ${focus.line}${focus.column ? `, column ${focus.column}` : ''}`;
  if (focus.target === 'parameters' && focus.name) text += ` → row “${focus.name}”`;
  if (focus.target === 'fields' && focus.name) text += ` → “${focus.name}”`;
  return text;
}

const go = (target, extra = {}) => ({ target, ...extra });

// [pattern, (match) => { title, hint, steps, focus, actions }]
const RULES = [
  [/Cannot reach the reporting server/i, () => ({
    title: 'The reporting server is not answering',
    hint: 'Nothing was sent: the service is stopped, is restarting, or the network is down. The report itself is fine.',
    steps: ['Start the reporting service again (uvicorn) or wait until it has restarted.', 'Then press the same button again; your changes are still in the designer.'],
  })],
  [/\(query line (\d+), column (\d+), near: (.*)\)\s*$/is, (m, text) => {
    const what = text.replace(/\s*\(query line.*$/is, '');
    const near = m[3].replace(/\s*>>>\s*/, ' ▶ ');
    const comma = /comma is missing/i.test(what);
    const colon = /colon : is missing/i.test(what);
    return {
      title: comma ? 'A comma is missing in the query' : colon ? 'A colon is missing in the query' : 'The query has a mistake',
      hint: what,
      steps: [
        `Go to line ${m[1]} of the query (the button below selects it).`,
        comma ? `Type a comma , where ▶ is: ${near}` : colon ? `Type a colon : where ▶ is: ${near}` : `Look at the text where ▶ is: ${near}`,
        'Press Read fields under the query to check it, then run the preview again.',
      ],
      focus: go('query', { line: Number(m[1]), column: Number(m[2]) }),
    };
  }],
  [/Pipeline JSON line (\d+), column (\d+): (.*?)\./i, (m) => ({
    title: 'The JSON of the query has a mistake',
    hint: `${m[3]}: usually a missing comma, a name without quotes, or a } too many.`,
    steps: [`Go to line ${m[1]}, column ${m[2]} of the query.`, 'Fix the comma / quote there. Names without quotes (Mongo Shell style) need Query language “Mongo Shell”.'],
    focus: go('query', { line: Number(m[1]), column: Number(m[2]) }),
    actions: [['use-mongosh', 'Use Mongo Shell']],
  })],
  [/^Fill in: (.*)$/i, (m) => ({
    title: 'Some report questions are empty',
    hint: `Empty: ${m[1]}.`,
    steps: ['Type a value for each parameter marked * on the left of the preview, then Run again.',
      'Or, if it is only a filter (empty = all records), press “Make it optional”: it unticks “Required” in Setup → 5 Parameters.'],
    focus: go('parameters', { name: m[1].split(',')[0].trim() }),
    actions: [[`make-optional:${m[1]}`, 'Make it optional']],
  })],
  [/Parameter (\w+) is not valid in a collection name/i, (m) => ({
    title: `Fill in ${m[1]} (part of the collection name)`,
    hint: `The collection name is built from ${m[1]}.`,
    steps: [`Type a value for ${m[1]} using only letters, numbers, _ and - (e.g. CAR).`, 'Run again.'],
    focus: go('parameters', { name: m[1] }),
  })],
  [/collection name is built from the parameter (\w+)|(\w+)\.?allowedValues|give (\w+) its allowedValues/i, (m) => {
    const name = m[1] || m[2] || m[3];
    return {
      title: 'List the allowed values for the collection name',
      hint: `The collection name is made only from ${name}; for safety the user may only pick from a list.`,
      steps: [`In Setup → 5 Parameters, row ${name}: type the values under “Allowed values”, separated by commas (e.g. CAR, BIKE).`, 'Or put fixed text around it in the collection name (e.g. Orders-$P{Branch}).'],
      focus: go('parameters', { name }),
    };
  }],
  [/Choose a Data Adapter first|Data adapter is not registered|Unknown connectionId|Choose a data adapter|not registered/i, () => ({
    title: 'Choose a data adapter',
    hint: 'The report does not know which database to read.',
    steps: ['Pick the connection under Data adapter.', 'No adapter yet? Make one in the left panel → Data Adapters.'],
    focus: go('adapter'), actions: [['adapters', 'Data adapters']],
  })],
  [/looks like a SQL query, but the query language is MongoDB/i, () => ({
    title: 'This is a SQL query',
    hint: 'The query language is set to MongoDB.',
    steps: ['Set Query language to “SQL”.', 'Choose a SQL data adapter (PostgreSQL, MySQL, SQL Server or Oracle).'],
    focus: go('language'), actions: [['use-sql', 'Switch to SQL']],
  })],
  [/Reports use aggregate, not find/i, () => ({
    title: 'Use aggregate instead of find',
    hint: 'Reports run an aggregation pipeline.',
    steps: ['Write db.getCollection("Orders").aggregate([ { "$match": { … } } ]).', 'The find filter goes into $match, the projection into $project.'],
    focus: go('query', { line: 1 }),
  })],
  [/one stage, not a pipeline/i, () => ({
    title: 'Put the stages in square brackets',
    hint: 'A pipeline is a list of stages.',
    steps: ['Write [ { "$match": { … } }, { "$project": { … } } ] — note the [ ] around the stages.'],
    focus: go('query', { line: 1 }),
  })],
  [/not in Mongo Shell form|literal format|pipeline = \[|The query is empty/i, () => ({
    title: 'The query could not be read',
    hint: 'The first line is not a MongoDB query the server knows.',
    steps: ['Write db.getCollection("Orders").aggregate([ … ]) or paste only the pipeline [ … ].', 'Plain JSON: Query language “Aggregation JSON”. SQL database: “SQL”.'],
    focus: go('query', { line: 1 }), actions: [['use-json', 'Use Aggregation JSON'], ['use-sql', 'Switch to SQL']],
  })],
  [/collection name is required|collection name was not found/i, () => ({
    title: 'The collection is missing',
    hint: 'The report does not know which collection to read.',
    steps: ['Type only the collection name (e.g. Orders) under Collection name,', 'or start the query with db.getCollection("Orders").'],
    focus: go('collection'),
  })],
  [/A collection name may contain only/i, () => ({
    title: 'The collection name has characters that are not allowed',
    hint: 'Allowed: letters, numbers, _ - . and $P{Parameter}.',
    steps: ['Remove spaces, quotes and other characters from Collection name.'],
    focus: go('collection'),
  })],
  [/is a SQL database; this report has a MongoDB query|this report has a SQL query and needs a SQL adapter|not MongoDB; this report needs a MongoDB adapter|same kind as the query/i, () => ({
    title: 'The data adapter and the query do not match',
    hint: 'A MongoDB query needs a MongoDB adapter; a SQL query needs a SQL adapter.',
    steps: ['Change Data adapter to the right kind,', 'or change Query language to match the adapter.'],
    focus: go('adapter'), actions: [['adapters', 'Data adapters']],
  })],
  [/Read the fields of the SQL query first|No output columns found|read its fields first/i, () => ({
    title: 'The fields are not known yet',
    hint: 'The report needs the list of columns the query returns.',
    steps: ['Press “Read fields” under the query.', 'If that fails, type the column names in Fields, separated by commas.'],
    focus: go('fields'),
  })],
  [/Define the parameter\(s\) used in the SQL query: (.*)$/i, (m) => ({
    title: 'Parameters are used in the query but not defined',
    hint: `Missing: ${m[1]}.`,
    steps: [`In Setup → 5 Parameters press “+ Add parameter” and name it exactly ${m[1].split(',')[0].trim()} (capital letters count).`, 'Or press Read fields: it adds the missing parameters for you.'],
    focus: go('parameters', { name: m[1].split(',')[0].trim() }),
  })],
  [/(?:Define the (?:query |collection )?parameter|Pipeline parameter is not defined:?|parameter of this report|is not a parameter)\s*\$?P?\{?(\w+)/i, (m) => ({
    title: `Parameter ${m[1]} is used but not defined`,
    hint: `Every $P{Name} in the query or the design must exist under Parameters with the same name.`,
    steps: [`Press “Add parameter ${m[1]}” below (or in Setup → 5 Parameters press “+ Add parameter” and name it ${m[1]} — capital letters count).`,
      `Or remove $P{${m[1]}} from the query / design.`],
    focus: go('parameters', { name: m[1] }),
    actions: [[`add-param:${m[1]}`, `Add parameter ${m[1]}`]],
  })],
  [/Parameter (\w+) is empty: /i, (m) => ({
    title: `${m[1]} has no value`,
    hint: `The query uses $P{${m[1]}}, so it needs a value to run.`,
    steps: [`Type a value for ${m[1]} on the left of the preview, then Run again.`,
      `If it is a filter that may stay empty (all records), write {"$paramIn": "${m[1]}"} in the query instead of $P{${m[1]}}, e.g. {"Occupation": {"$paramIn": "${m[1]}"}}.`],
    focus: go('query'),
  })],
  [/^(?:design: )?(.+?): \$([FPV])\{(\w+)\} is not defined/i, (m) => {
    const kind = { F: 'field', P: 'parameter', V: 'variable' }[m[2].toUpperCase()];
    return {
      title: `${m[1]} uses the ${kind} ${m[3]}, which the report does not have`,
      hint: `$${m[2]}{${m[3]}} is used in ${m[1]}.`,
      steps: kind === 'field'
        ? [`Add ${m[3]} to Setup → Fields (and to the $project of the query), or`, `in the Design tab, band ${m[1]}: change the expression to an existing field.`]
        : kind === 'parameter'
          ? [`Add the parameter ${m[3]} in Setup → 5 Parameters, or`, `change the expression in the Design tab, band ${m[1]}.`]
          : [`Create the variable ${m[3]} (Outline → Variables) or change the expression in band ${m[1]}.`],
      focus: kind === 'field' ? go('fields', { name: m[3] }) : kind === 'parameter' ? go('parameters', { name: m[3] }) : go('design'),
      actions: [['design', 'Open Design']],
    };
  }],
  [/Duplicate parameter name: (\w+)|Invalid parameter name: (\S+)|The type of (\w+) must be|(\w+) is not asked from the user|The expression of (\w+)/i, (m) => {
    const name = m[1] || m[2] || m[3] || m[4] || m[5];
    return {
      title: `Parameter ${name} needs a change`,
      hint: 'Parameter names are letters, numbers and _, each name once.',
      steps: [`In Setup → 5 Parameters, row ${name}: fix the name, type or default value as the message says.`],
      focus: go('parameters', { name }),
    };
  }],
  [/Reports may only read data|must start with SELECT|exactly one SQL statement|FOR UPDATE|\$P!\{/i, () => ({
    title: 'Reports may only read data',
    hint: 'Use one SELECT (or WITH … SELECT). INSERT, UPDATE, DELETE and several statements are not allowed.',
    steps: ['Keep only one SELECT in the query; use $P{Name} for values.'],
    focus: go('query', { line: 1 }),
  })],
  [/\$out and \$merge|run JavaScript|shows server information|system collections|same database/i, () => ({
    title: 'This pipeline stage is not allowed',
    hint: '$out, $merge, JavaScript ($function, $where), server information and system collections are blocked.',
    steps: ['Remove that stage from the query.'],
    focus: go('query'),
  })],
  [/report ID|Give the report a valid ID/i, () => ({
    title: 'The report ID is not valid',
    hint: 'Letters, numbers, _ and - only, starting with a letter or number (e.g. BRANCH_SALES).',
    steps: ['Fix Report ID (or press ↺ From name).'],
    focus: go('details'),
  })],
  [/^Dataset (\w+): (.*)$/i, (m) => ({
    title: `Dataset ${m[1]} needs a change`,
    hint: m[2],
    steps: [`Open Dataset and Query → Datasets → ${m[1]} and fix it as the message says (query, fields or parameters).`],
    focus: go('datasets', { name: m[1] }),
  })],
  [/^(?:design: )?(Title|Page Header|Column Header|Detail|Column Footer|Page Footer|Summary|Background|No Data|Last Page Footer|Group \w+ (?:Header|Footer)|[\w ]+ band)\b[^:]*: (.*)$/i, (m) => ({
    title: `Something in the ${m[1]} band needs a change`,
    hint: m[2],
    steps: [`Open the Design tab and select the element in the ${m[1]} band.`, 'Change it as the message says (its expression, field or image).'],
    focus: go('design', { name: m[1] }), actions: [['design', 'Open Design']],
  })],
  [/The field of (?:group|variable) '(\w+)' does not exist: '?(\w+)|The sort field|chart categoryField|existing fields/i, (m) => ({
    title: 'A group, sort or chart uses a field the report does not have',
    hint: m[2] ? `${m[1]} uses ${m[2]}.` : 'A field was renamed or removed.',
    steps: ['Add the field to Setup → Fields, or', 'choose an existing field in the Outline (Groups / Sort / Variables) or the chart.'],
    focus: m[2] ? go('fields', { name: m[2] }) : go('design'), actions: [['design', 'Open Design']],
  })],
  [/Excel\/CSV output needs a Text Field/i, () => ({
    title: 'Excel/CSV needs something in the Detail band',
    hint: 'Excel and CSV are made from the Detail band.',
    steps: ['Put a Text Field (e.g. $F{Branch}) in the Detail band, or', 'in Report properties set the Excel / CSV layout to “Page layout”.'],
    focus: go('design'), actions: [['design', 'Open Design']],
  })],
  [/No MongoDB URL|no saved connection URL|setup needed|has no saved MongoDB URL|No connection URL is saved/i, () => ({
    title: 'The data adapter has no connection saved',
    hint: 'The adapter exists but its connection URL is empty on this server.',
    steps: ['Open the adapter in Data Adapters (or /admin → Data sources).', 'Enter the connection again and press Test.'],
    focus: go('adapter'), actions: [['adapters', 'Data adapters']],
  })],
  [/Cannot connect|ServerSelectionTimeout|timed out|getaddrinfo|connection refused|actively refused|Login failed|password authentication failed|MongoDB connection failed/i, () => ({
    title: 'The database could not be reached',
    hint: 'The query is fine as far as we know; the database did not answer.',
    steps: ['Check that the database server runs and is reachable (VPN, firewall, host, port).', 'Check user name and password: Data Adapters → the adapter → Test.'],
    focus: go('adapter'), actions: [['adapters', 'Data adapters']],
  })],
  [/larger than the row limit|too many/i, () => ({
    title: 'The report has too many records',
    hint: 'Narrow it down or run it in the background.',
    steps: ['Add a $match / WHERE or a parameter (e.g. a date range).', 'Or run it from /server with “Run in background”.'],
  })],
  [/admin token|token is missing|needs the admin token/i, () => ({
    title: 'Not allowed with this sign-in',
    hint: 'Data adapters are created and changed by administrators.',
    steps: ['Ask an administrator, or sign in as one.'],
  })],
];

const PREFIXES = /^(Preview failed|Save failed|Publish failed|The fields could not be read|The query could not be parsed|Could not [^:]+|The draft is not complete yet|Run failed|JRXML import failed|Import failed)\s*:\s*/i;

/** { title, text, hint, steps, focus, place, actions, stage } for an error message. */
export function explainError(message) {
  let text = String(message || 'Something went wrong');
  let stage = '';
  for (let i = 0; i < 3; i += 1) {
    const found = text.match(PREFIXES);
    if (!found) break;
    if (!stage) stage = found[1];
    text = text.slice(found[0].length);
  }
  for (const [pattern, make] of RULES) {
    const m = text.match(pattern);
    if (m) {
      const info = { actions: [], steps: [], ...make(m, text) };
      if (/import/i.test(stage)) {
        // the report is not open yet: the fix is made in the .jrxml file itself
        const line = info.focus?.line ? ` at line ${info.focus.line} of the query` : '';
        return {
          ...info, text, stage, importing: true, focus: undefined, actions: [],
          place: `the .jrxml file → Dataset and Query${line}`,
          steps: [`Open the .jrxml in Jaspersoft Studio (or a text editor) → Dataset and Query${line}.`, ...info.steps.filter((s) => !/button below|Read fields/.test(s)), 'Save the file and import it again.'],
        };
      }
      return { ...info, text, stage, place: placeOf(info.focus) };
    }
  }
  return {
    title: stage || 'Something went wrong', text, stage, steps: [], actions: [],
    hint: 'The server did not accept the report. The list below shows what to check; the message above is what the server said.',
  };
}

// ------------------------------------------------------------------ checks without the server
/**
 * Asked parameters the report already treats as an optional filter: used in {"$paramIn": "X"} (empty = all) or
 * null/empty-checked by a computed parameter ($P{X} == null, $P{X}.trim().equals(""), $P{X}.isEmpty()).
 * Returns { name: 'where it is handled' }.
 */
export function optionalFilters(report) {
  const params = report?.parameters || [];
  const isComputed = (p) => p.prompt === false || p.computed === true;   // designer: prompt false; server: computed
  const asked = new Set(params.filter((p) => !isComputed(p)).map((p) => p.name));
  const out = {};
  for (const m of String(report?.querySource || '').matchAll(/"\$paramIn"\s*:\s*"(\w+)"/g)) if (asked.has(m[1])) out[m[1]] = 'the query ($paramIn)';
  params.filter(isComputed).forEach((p) => {
    const expr = String(p.expression || '');
    asked.forEach((name) => {
      const ref = `\\$P\\{${name}\\}`;
      const checks = new RegExp(`${ref}\\s*==\\s*null|${ref}(\\.trim\\(\\))?\\.(isEmpty\\(\\)|equals\\(""\\)|length\\(\\)\\s*==\\s*0)`);
      if (!out[name] && checks.test(expr)) out[name] = `the computed parameter ${p.name}`;
    });
  });
  return out;
}

const PARAM = /\$P!?\{(\w+)\}|\$X\{\s*\w+\s*,\s*[^,}]+,\s*(\w+)\s*\}/g;

/** Every problem the designer can see itself: [{ text, fix, focus, level: 'error' | 'warn' | 'todo' }]. */
export function checkSetup(report, design) {
  if (!report) return [];
  const out = [];
  const add = (level, text, fix, focus) => out.push({ level, text, fix, focus, place: placeOf(focus) });
  const isSql = report.queryLanguage === 'sql';
  const query = String(report.querySource || '');
  const params = report.parameters || [];
  const names = new Set(params.map((p) => p.name));
  Object.entries(optionalFilters(report)).forEach(([name, where]) => {
    if (params.find((p) => p.name === name)?.required) {
      add('warn', `${name} is Required, but it is a filter that may stay empty`, `${where[0].toUpperCase()}${where.slice(1)} already handles an empty ${name} (all records). Untick “Required” so the report runs without it.`, go('parameters', { name }));
    }
  });

  // A brand-new report that nobody has typed in yet shows "To do" (grey), not errors. A report with no
  // collection and no query is a page of fixed text (letter, certificate, form): it needs no data at all.
  const title = String(report.title || '').trim();
  const untouched = report.isNew && !String(report.id || '').trim() && (!title || title === 'New report');
  const noData = !query.trim() && !String(report.collection || '').trim();
  if (!title) add(report.isNew ? 'todo' : 'warn', 'The report has no name', 'Type a Report name (or Preview asks for it).', go('details'));
  if (report.isNew && !/^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/.test(String(report.id || ''))) {
    if (untouched || !String(report.id || '').trim()) add('todo', 'The report has no name / ID yet', 'Type a Report name; Preview asks for it if you skip this.', go('details'));
    else add('error', 'The report ID is not valid', 'Use letters, numbers, _ and - in the ID.', go('details'));
  }
  if (noData) {
    add('todo', 'No data yet — it prints once as a page of fixed text', 'Fine for a letter, certificate or form. To fill it from a database, pick a Data adapter and write the query.', go('adapter'));
    add('todo', 'No query (optional)', 'Without a query the report is one page of fixed text. Write one to print a page or row per record.', go('query', { line: 1 }));
    add('todo', 'No fields (optional)', 'Fields come from the query. A page of fixed text needs none; parameters ($P{…}) still work.', go('fields'));
  } else {
    if (!report.connectionId) add('error', 'No data adapter is chosen', 'Pick the database connection under Data adapter.', go('adapter'));
    if (!query.trim()) add('error', 'The query is empty', isSql ? 'Write one SELECT statement.' : 'Write db.getCollection("X").aggregate([ … ]) or paste the pipeline [ … ].', go('query', { line: 1 }));
    if (!isSql && !String(report.collection || '').trim() && !/getCollection\(|db\.\w+\.aggregate|aggregate["']?\s*:/.test(query)) {
      add('error', 'The collection name is empty', 'Type the collection name (e.g. Orders).', go('collection'));
    }
  }
  if (isSql && /^\s*db\.|aggregate\s*\(/.test(query)) add('error', 'The query is MongoDB but Query language is SQL', 'Set Query language to Mongo Shell.', go('language'));
  if (!isSql && /^\s*(select|with)\b/i.test(query)) add('error', 'The query is SQL but Query language is MongoDB', 'Set Query language to SQL and pick a SQL data adapter.', go('language'));

  const columns = report.columns || [];
  if (!columns.length && !noData) add('error', 'No fields yet', 'Press “Read fields” in 4 Fields, or type the field names.', go('fields'));
  const seen = new Map();
  columns.forEach((c) => {
    const key = c.toLowerCase();
    if (seen.has(key)) add('warn', `“${seen.get(key)}” and “${c}” differ only in capital letters`, `They are two different fields (MongoDB and $F{…} match capitals exactly), so both are read and exported; the Excel header of the second becomes “${c} (2)”. If the data only has one of them, remove the other in Fields.`, go('fields', { name: c }));
    else seen.set(key, c);
  });

  // parameters used but not defined (query, collection, design)
  const used = new Map();
  const collect = (text, where) => {
    for (const m of String(text || '').matchAll(PARAM)) {
      const name = m[1] || m[2];
      if (name && !used.has(name)) used.set(name, where);
    }
  };
  collect(query, 'the query');
  collect(report.collection, 'the collection name');
  const designText = design ? JSON.stringify(design) : '';
  collect(designText, 'the design');
  params.forEach((p) => collect(p.expression, `the default value of ${p.name}`));
  for (const [name, where] of used) {
    if (!names.has(name) && !['REPORT_CONNECTION', 'REPORT_PARAMETERS_MAP', 'REPORT_DATA_SOURCE'].includes(name)) {
      add('error', `$P{${name}} is used in ${where} but there is no parameter ${name}`, `Press “+ Add parameter” and name it ${name} (capital letters count).`, go('parameters', { name }));
    }
  }
  const counts = {};
  params.forEach((p) => { counts[p.name] = (counts[p.name] || 0) + 1; });
  Object.entries(counts).filter(([, n]) => n > 1).forEach(([name]) => add('error', `The parameter ${name} is listed twice`, 'Delete one of the two rows (✕).', go('parameters', { name })));
  params.filter((p) => !/^[A-Za-z_]\w{0,79}$/.test(p.name || '')).forEach((p) => add('error', `The parameter name “${p.name}” is not valid`, 'Use letters, numbers and _ (starting with a letter).', go('parameters', { name: p.name })));

  // fields used in the design that the report does not have
  if (designText && columns.length) {
    const have = new Set(columns);
    const calculated = new Set((design.calculatedFields || design.fields || []).map((f) => f?.name).filter(Boolean));
    const missing = [...new Set([...designText.matchAll(/\$F\{([^}]+)\}/g)].map((m) => m[1]))]
      .filter((f) => !have.has(f) && !calculated.has(f) && ![...have].some((c) => f.startsWith(`${c}.`)));
    missing.slice(0, 8).forEach((f) => add('warn', `The design uses the field ${f}, which is not in Fields`, `Add ${f} to Fields (and to the $project / SELECT of the query), or change that text field in the Design tab.`, go('fields', { name: f })));
  }
  return out;
}

// ------------------------------------------------------------------ JRXML import warnings, in plain words
const bandWords = (where) => {
  const w = String(where || '').trim();
  if (/^table\b/i.test(w)) return `the table (${w.replace(/^table\s*/i, '') || 'cell'} cells)`;
  if (/^groupHeader:/i.test(w)) return `the header of group ${w.split(':')[1]}`;
  if (/^groupFooter:/i.test(w)) return `the footer of group ${w.split(':')[1]}`;
  return `the ${w.replace(/([a-z])([A-Z])/g, '$1 $2').toLowerCase()} band`;
};

/**
 * One import warning → { level: 'fixed' | 'todo' | 'info', title, text, where, todo, focus }.
 * fixed = the importer already repaired it (only look), todo = something the user must do, info = nothing to do.
 */
export function explainWarning(warning, elementId) {
  const w = String(warning || '');
  let m = w.match(/^Query line (\d+): a missing '(.)' was added \(near: (.*)\)/s);
  if (m) {
    const near = m[3].replace(/\s*>>>\s*/, ' ▶ ').trim();
    return {
      level: 'fixed', title: `A missing “${m[2]}” was added on query line ${m[1]}`,
      text: `The query in the JRXML had a mistake: after ${near.replace(/ ▶.*$/, '')} there was no “${m[2]}”. It was added for you, so the report works.`,
      where: `${PLACES.query}, line ${m[1]}`, todo: `Nothing to do — line ${m[1]} now ends with the added “${m[2]}” (marked green). Open it only if you want to see the change.`,
      focus: go('query', { line: Number(m[1]), fixed: true }),
    };
  }
  m = w.match(/^(.*?): the Java expression could not be translated; write it again in the designer: (.*)$/s);
  if (m) {
    return {
      level: 'todo', title: `A text field in ${bandWords(m[1])} uses Java code that cannot run here`,
      text: 'The field now shows only its first $F{…} value instead of the result of this Java code:', code: m[2],
      where: `Design tab → ${bandWords(m[1])}`,
      todo: 'Go there: the field is selected. Double-click it (or ƒx in Properties) and write the same thing with the functions of the expression editor.',
      focus: go('design', { elementId }),
    };
  }
  m = w.match(/^Group (\S+): groups by "(.*)", which is not a single field/s);
  if (m) {
    return {
      level: 'todo', title: `Group ${m[1]} was left out`, text: `It grouped the rows by “${m[2]}”, which is not one field.`,
      where: 'Design tab → Outline → Groups', todo: 'If the report needs this grouping, add a group on the field it should group by.',
      focus: go('design'),
    };
  }
  m = w.match(/^Field '(.+)' is not in the query output/);
  if (m) {
    return {
      level: 'todo', title: `Field “${m[1]}” is not returned by the query`, text: 'It will print empty.',
      where: `${PLACES.fields} → “${m[1]}”`, todo: `Add ${m[1]} to the $project of the query, or remove it from the Fields and the design.`,
      focus: go('fields', { name: m[1] }),
    };
  }
  m = w.match(/^Parameter (\S+): the default expression could not be translated/);
  if (m) {
    return {
      level: 'todo', title: `Parameter ${m[1]} has no default value`, text: 'Its default was Java code that cannot run here.',
      where: `${PLACES.parameters} → row “${m[1]}”`, todo: 'Users must fill it in, or mark it not required.', focus: go('parameters', { name: m[1] }),
    };
  }
  if (/collection name (was not found|comes from a parameter)/i.test(w)) {
    return { level: 'todo', title: 'The collection name is missing', text: w, where: PLACES.collection, todo: 'Type the collection name (e.g. Orders).', focus: go('collection') };
  }
  m = w.match(/collection name is built from the parameter (\S+?);/);
  if (m) {
    return {
      level: 'todo', title: `${m[1]} needs its allowed values`, text: 'The collection name is made from this parameter, so only listed values may be used.',
      where: `${PLACES.parameters} → row “${m[1]}” → Allowed values`, todo: 'Type the allowed values, separated by commas.', focus: go('parameters', { name: m[1] }),
    };
  }
  m = w.match(/^(.*?): image (.*) was not included/);
  if (m) {
    return { level: 'todo', title: `Image ${m[2]} is missing`, text: `It is in ${bandWords(m[1])}.`, where: `Design tab → ${bandWords(m[1])}`, todo: 'Import again and choose the image together with the .jrxml, or select the image and upload it.', focus: go('design') };
  }
  m = w.match(/^(.*?): font '(.*)' is not available/);
  if (m) return { level: 'info', title: `Font ${m[2]} is replaced by SansSerif`, text: `Used in ${bandWords(m[1])}. Nothing to do unless the look matters.`, where: '', todo: '' };
  m = w.match(/^Variable (\S+): the calculation\/expression is not supported/);
  if (m) return { level: 'todo', title: `Variable ${m[1]} was left out`, text: 'Its calculation is not supported here.', where: 'Design tab → Outline → Variables', todo: `If a field prints $V{${m[1]}}, add the variable again with a supported calculation (Sum, Count, …).`, focus: go('design') };
  if (/is not MongoDB/.test(w)) return { level: 'todo', title: 'The query is not a MongoDB query', text: w, where: PLACES.language, todo: 'Choose the right query language and data adapter.', focus: go('language') };
  if (/sortField/.test(w)) return { level: 'todo', title: 'The sort was left out', text: w, where: PLACES.query, todo: 'Add a $sort stage to the query if the order matters.', focus: go('query') };
  return { level: /skipped|not imported|not supported/i.test(w) ? 'todo' : 'info', title: w, text: '', where: '', todo: '' };
}
