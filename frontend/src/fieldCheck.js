// Which $F{…} fields a report knows, which ones the design uses, and — for a name that is not known — the closest
// known names (spelling, capitals, "assignTo.Name" vs "assignTo"), so a missing or misspelled field is easy to fix.

const FIELD = /\$F\{([^{}]+)\}/g;

/** The fields an expression may use: the query fields plus the calculated fields. */
export function knownFields(report, design) {
  return [...(report?.columns || []), ...((design?.calculatedFields || []).map((c) => c?.name).filter(Boolean))];
}

/** Every $F{name} in a text, once each. */
export function fieldRefs(text) {
  return [...new Set([...String(text || '').matchAll(FIELD)].map((m) => m[1].trim()))];
}

function distance(a, b) {
  if (Math.abs(a.length - b.length) > 3) return 99;
  const row = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i += 1) {
    let prev = row[0];
    row[0] = i;
    for (let j = 1; j <= b.length; j += 1) {
      const keep = row[j];
      row[j] = Math.min(row[j] + 1, row[j - 1] + 1, prev + (a[i - 1] === b[j - 1] ? 0 : 1));
      prev = keep;
    }
  }
  return row[b.length];
}

/** Up to 3 known names that look like `name`, best first, each with why: { name, why }. */
export function similarFields(name, known) {
  const low = name.toLowerCase();
  const last = (n) => n.toLowerCase().split('.').pop();
  const found = [];
  for (const k of known) {
    const kl = k.toLowerCase();
    let score = 0;
    let why = '';
    if (kl === low) { score = 100; why = 'same name with other capitals'; }
    else if (low.startsWith(`${kl}.`)) { score = 80; why = `${k} is in Fields; ${name} is a part inside it`; }
    else if (kl.startsWith(`${low}.`)) { score = 70; why = 'a field inside it'; }
    else if (last(k) === last(name)) { score = 60; why = 'same last part of the name'; }
    else {
      const d = distance(low, kl);
      if (d <= Math.max(1, Math.min(3, Math.floor(low.length / 4)))) { score = 50 - d; why = 'similar spelling'; }
    }
    if (score) found.push({ name: k, why, score });
  }
  return found.sort((a, b) => b.score - a.score).slice(0, 3);
}

/** $F{…} names in `text` that the report does not have, each with its similar names. */
export function missingFields(text, report, design) {
  const known = knownFields(report, design);
  const have = new Set(known);
  return fieldRefs(text).filter((f) => !have.has(f)).map((name) => ({ name, similar: similarFields(name, known) }));
}

/** Text of the band elements (not tables / lists / crosstabs, which read their own dataset). */
function elementText(element) {
  if (!['textField', 'staticText', 'barcode', 'image', 'chart'].includes(element.type)) return '';
  return [element.expression, element.text, element.printWhenExpression, element.hyperlink?.target, ...(element.hyperlink?.parameters || []).map((p) => p.expression), element.categoryField && `$F{${element.categoryField}}`,
    element.valueField && `$F{${element.valueField}}`].filter(Boolean).join(' ');
}

/** { fieldName: [element ids] } for every field the design's band elements use. */
export function designFieldUse(design) {
  const use = {};
  const bands = design?.bands || {};
  const visit = (band) => {
    (band?.elements || []).forEach((el) => {
      fieldRefs(elementText(el)).forEach((f) => { (use[f] = use[f] || []).push(el.id); });
      if (el.elements) visit(el);                 // frames
    });
    (band?.more || []).forEach(visit);            // Detail 2, Summary 2…
  };
  Object.entries(bands).forEach(([key, band]) => {
    if (key === 'groupHeaders' || key === 'groupFooters') Object.values(band || {}).forEach(visit);
    else visit(band);
  });
  (design?.groups || []).forEach((g) => { if (g.field) (use[g.field] = use[g.field] || []).push(`group:${g.name}`); });
  return use;
}
