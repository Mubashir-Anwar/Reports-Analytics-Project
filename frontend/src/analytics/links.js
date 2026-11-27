// Links of a table visual (tile.options.links): a column's values open a web page, an e-mail, a report or another
// dashboard filtered by that value. Only http(s) and mailto: addresses are made; anything else gets no link.

/** The index of the result column a link is on (by field name, then by label). */
export function columnIndex(columns, name) {
  const at = columns.findIndex((c) => c.role !== 'value' && c.name === name);
  return at >= 0 ? at : columns.findIndex((c) => c.name === name || c.label === name);
}

/** {Field} in a template → that field's value in this row (encoded for a URL unless raw). */
export function fillTemplate(template, row, columns, raw = false) {
  return String(template || '').replace(/\{([^{}]+)\}/g, (all, name) => {
    const at = columnIndex(columns, name.trim());
    if (at < 0) return all;
    const value = row[at] == null ? '' : String(row[at]);
    return raw ? value : encodeURIComponent(value);
  });
}

const EMAIL = /^[^@\s<>"'()]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$/;

/** { href, newTab } for one cell, or null. */
export function linkHref(link, row, columns) {
  const at = columnIndex(columns, link.column);
  if (at < 0 || row[at] == null || row[at] === '') return null;
  const value = String(row[at]);
  if (link.kind === 'url') {
    let href = fillTemplate(link.template || `{${link.column}}`, row, columns, !link.template);
    if (/^www\./i.test(href)) href = `https://${href}`;
    return /^https?:\/\/[^\s<>"]+$/i.test(href) ? { href, newTab: true } : null;
  }
  if (link.kind === 'email') {
    const address = fillTemplate(link.template || `{${link.column}}`, row, columns, true).replace(/^mailto:/i, '');
    const [who, query] = address.split('?');
    return EMAIL.test(who) ? { href: `mailto:${who}${query ? `?${query}` : ''}`, newTab: false } : null;
  }
  if (link.kind === 'report' && /^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/.test(link.report || '')) {
    const query = new URLSearchParams();
    if (link.parameter) query.set(link.parameter, value);
    if (link.folder) query.set('Folder', link.folder);
    query.set('View', 'inline');
    return { href: `/generate/${link.report}.pdf?${query}`, newTab: true };
  }
  if (link.kind === 'dashboard' && /^[A-Za-z0-9_-]{1,80}$/.test(link.dashboard || '')) {
    const query = new URLSearchParams({ [link.filter || link.column]: value });
    return { href: `/analytics#dash/${link.dashboard}?${query}`, newTab: false };
  }
  return null;
}

/** "#dash/ID?Column=Value" → [{ column, value }] (the filter another dashboard's link opened this one with). */
export function drillFromHash(query) {
  if (!query) return [];
  return [...new URLSearchParams(query)].slice(0, 5).map(([column, value]) => ({ column, value }));
}
