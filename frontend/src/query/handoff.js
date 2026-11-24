// "New report from this": the Query workspace hands a query to the Designer, which opens a new report with it.
export const HANDOFF_KEY = 'designer-from-query';

export function sendToDesigner(query) {
  try { localStorage.setItem(HANDOFF_KEY, JSON.stringify({ ...query, at: Date.now() })); } catch { /* storage off: nothing to hand over */ }
  window.open('/designer?fromQuery=1', '_blank', 'noopener');
}

/** The query handed over in the last 10 minutes (read once). */
export function takeHandoff() {
  try {
    const raw = localStorage.getItem(HANDOFF_KEY);
    localStorage.removeItem(HANDOFF_KEY);
    const value = raw ? JSON.parse(raw) : null;
    return value && Date.now() - (value.at || 0) < 600_000 ? value : null;
  } catch { return null; }
}
