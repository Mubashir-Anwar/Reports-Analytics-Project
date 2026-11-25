// "New report" / "Use in Analytics": the Query workspace hands a query to the Designer (a new report) or to Analytics
// (a new dataset), which opens in another tab.
export const HANDOFF_KEY = 'designer-from-query';
const KEYS = { designer: HANDOFF_KEY, analytics: 'analytics-from-query' };
const taken = {};         // asked for again in the same page (React may run an effect twice): the same answer for a moment

function hand(to, query, address) {
  try { localStorage.setItem(KEYS[to], JSON.stringify({ ...query, at: Date.now() })); } catch { /* storage off: nothing to hand over */ }
  window.open(address, '_blank', 'noopener');
}
export const sendToDesigner = (query) => hand('designer', query, '/designer?fromQuery=1');
export const sendToAnalytics = (query) => hand('analytics', query, '/analytics#ds/new');

/** The query handed over in the last 10 minutes (read once). */
export function takeHandoff(to = 'designer') {
  if (taken[to] && Date.now() - taken[to].when < 1500) return taken[to].value;
  try {
    const raw = localStorage.getItem(KEYS[to]);
    localStorage.removeItem(KEYS[to]);
    const value = raw ? JSON.parse(raw) : null;
    const fresh = value && Date.now() - (value.at || 0) < 600_000 ? value : null;
    if (fresh) taken[to] = { value: fresh, when: Date.now() };
    return fresh;
  } catch { return null; }
}
