// Turn server errors into a short title, a plain explanation and a hint, with buttons that fix the usual causes.
// Each rule: a pattern on the message, then { title, hint, actions: [[actionId, label], …] }.
// The action ids are handled in App.jsx (open the Setup tab, switch to SQL, open the data adapters, …).

const RULES = [
  [/^Fill in:/i, {
    title: 'Some report questions are empty',
    hint: 'Fill in the parameters marked * on the left of the preview, then press Run again. If a parameter may stay empty (e.g. an optional product code), untick “Required” for it under Parameters.',
    actions: [['parameters', 'Open Parameters']],
  }],
  [/Choose a Data Adapter first/i, {
    title: 'Choose a data adapter',
    hint: 'The preview reads real records: pick a data adapter in Setup, or make one in the Data Adapters panel.',
    actions: [['setup', 'Open Setup'], ['adapters', 'Data adapters']],
  }],
  [/looks like a SQL query, but the query language is MongoDB/i, {
    title: 'This is a SQL query',
    hint: 'The query language is set to MongoDB. Choose Query language “SQL” and a SQL data adapter (PostgreSQL, MySQL, SQL Server or Oracle).',
    actions: [['use-sql', 'Switch to SQL'], ['setup', 'Open Setup']],
  }],
  [/Reports use aggregate, not find/i, {
    title: 'Use aggregate instead of find',
    hint: 'Write db.getCollection("Orders").aggregate([ { "$match": { … } } ]). The find filter goes into $match and the projection into $project.',
    actions: [['setup', 'Open the query']],
  }],
  [/one stage, not a pipeline/i, {
    title: 'Put the stages in square brackets',
    hint: 'A pipeline is a list: [ { "$match": { … } }, { "$project": { … } } ].',
    actions: [['setup', 'Open the query']],
  }],
  [/not in Mongo Shell form|literal format|pipeline = \[/i, {
    title: 'The query could not be read',
    hint: 'Write db.getCollection("Orders").aggregate([ … ]) or paste only the pipeline [ … ]. For plain JSON choose Query language “Aggregation JSON”; for a SQL database choose “SQL”.',
    actions: [['use-json', 'Use Aggregation JSON'], ['use-sql', 'Switch to SQL'], ['setup', 'Open the query']],
  }],
  [/Pipeline JSON line \d+/i, {
    title: 'The JSON of the pipeline has a mistake',
    hint: 'Look at the line and column in the message: usually a missing comma, a missing quote around a name, or a } too many. Mongo Shell syntax (no quotes around names) needs Query language “Mongo Shell”.',
    actions: [['use-mongosh', 'Use Mongo Shell'], ['setup', 'Open the query']],
  }],
  [/collection name is required/i, {
    title: 'The collection is missing',
    hint: 'Enter the collection name in Setup (only the name, e.g. Orders), or start the query with db.getCollection("Orders").',
    actions: [['setup', 'Open Setup']],
  }],
  [/is a SQL database; this report has a MongoDB query|this report has a SQL query and needs a SQL adapter|not MongoDB; this report needs a MongoDB adapter|same kind as the query/i, {
    title: 'The data adapter and the query do not match',
    hint: 'A MongoDB query needs a MongoDB adapter; a SQL query needs a SQL adapter (PostgreSQL, MySQL, SQL Server, Oracle). Change one of the two in Setup.',
    actions: [['setup', 'Open Setup'], ['adapters', 'Data adapters']],
  }],
  [/Read the fields of the SQL query first|No output columns found|read its fields first/i, {
    title: 'The fields are not known yet',
    hint: 'Click “Read fields” under the query in Setup. If that fails, type the column names in “Fields”, separated by commas.',
    actions: [['setup', 'Open Setup']],
  }],
  [/Define the parameter\(s\) used|is not defined|Define the collection parameter/i, {
    title: 'A parameter is used but not defined',
    hint: 'Every $P{Name} in the query or the design must exist under Parameters with the same name (capital letters count).',
    actions: [['parameters', 'Open Parameters']],
  }],
  [/Reports may only read data|must start with SELECT|exactly one SQL statement|FOR UPDATE|\$P!\{/i, {
    title: 'Reports may only read data',
    hint: 'Use one SELECT (or WITH … SELECT) statement. INSERT, UPDATE, DELETE, several statements and $P!{} are not allowed; use $P{Name} for values.',
    actions: [['setup', 'Open the query']],
  }],
  [/\$out and \$merge|run JavaScript|shows server information|system collections|same database/i, {
    title: 'This pipeline stage is not allowed',
    hint: 'Report queries only read data from the report\'s own database: $out, $merge, JavaScript ($function, $where), server information ($currentOp …) and system collections are blocked.',
    actions: [['setup', 'Open the query']],
  }],
  [/report ID|Give the report a valid ID/i, {
    title: 'The report ID is not valid',
    hint: 'Use letters, numbers, _ and - only, starting with a letter or number (e.g. BRANCH_SALES). The ID is made from the name; you can also type one.',
    actions: [['setup', 'Open Setup']],
  }],
  [/Data adapter is not registered|Unknown connectionId|Choose a data adapter|not registered/i, {
    title: 'Choose a data adapter',
    hint: 'Pick the database connection in Setup → Data adapter. New adapters are made with the Data Adapter Wizard (left panel → Data Adapters).',
    actions: [['setup', 'Open Setup'], ['adapters', 'Data adapters']],
  }],
  [/No MongoDB URL|no saved connection URL|setup needed|has no saved MongoDB URL|No connection URL is saved/i, {
    title: 'The data adapter has no connection saved',
    hint: 'Open the adapter in Data Adapters (or /admin → Data sources), enter the connection again and press Test.',
    actions: [['adapters', 'Data adapters']],
  }],
  [/Cannot connect|ServerSelectionTimeout|timed out|getaddrinfo|connection refused|actively refused|Login failed|password authentication failed/i, {
    title: 'The database could not be reached',
    hint: 'Check that the database server is running and reachable from this server (VPN, firewall, host name and port), and that the user name and password are right. Test the adapter in Data Adapters.',
    actions: [['adapters', 'Data adapters']],
  }],
  [/larger than the row limit|too many/i, {
    title: 'The report has too many records',
    hint: 'Narrow it with parameters or a $match / WHERE, or run it from /server with “Run in background”.',
    actions: [],
  }],
  [/admin token|token is missing|needs the admin token/i, {
    title: 'Not allowed with this sign-in',
    hint: 'This step needs the admin token (data adapters are created and changed by administrators).',
    actions: [],
  }],
];

const PREFIXES = /^(Preview failed|Save failed|Publish failed|The fields could not be read|The query could not be parsed|Could not [^:]+|The draft is not complete yet|Run failed)\s*:\s*/i;

/** { title, text, hint, actions } for an error message; unknown messages still get a readable title. */
export function explainError(message) {
  let text = String(message || 'Something went wrong');
  let where = '';
  for (let i = 0; i < 3; i += 1) {
    const found = text.match(PREFIXES);
    if (!found) break;
    if (!where) where = found[1];
    text = text.slice(found[0].length);
  }
  const rule = RULES.find(([pattern]) => pattern.test(text));
  if (rule) return { ...rule[1], text, where };
  return { title: where || 'Something went wrong', text, hint: 'Check the Setup tab (data adapter, query and fields). The message above says what the server did not accept.', actions: [['setup', 'Open Setup']], where };
}
