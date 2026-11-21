// Relative dates like the server's (backend/app/jobs/schedule.py): {today}, {today-7}, {monthStart}, {prevMonthEnd}…
// A parameter default or min/max may be one of these; the run form shows the real date of today.

export const RELATIVE_DATES = [
  ['{today}', 'Today'],
  ['{yesterday}', 'Yesterday'],
  ['{today-7}', '7 days ago'],
  ['{today-30}', '30 days ago'],
  ['{weekStart}', 'Start of this week'],
  ['{monthStart}', 'Start of this month'],
  ['{monthEnd}', 'End of this month'],
  ['{prevMonthStart}', 'Start of last month'],
  ['{prevMonthEnd}', 'End of last month'],
  ['{yearStart}', 'Start of this year'],
  ['{prevYearStart}', 'Start of last year'],
  ['{prevYearEnd}', 'End of last year'],
];

const RELATIVE = /^\{\s*(today|yesterday|weekStart|monthStart|monthEnd|prevMonthStart|prevMonthEnd|yearStart|prevYearStart|prevYearEnd)\s*(?:([+-])\s*(\d{1,4}))?\s*\}$/;
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

export const isRelativeDate = (value) => RELATIVE.test(String(value ?? '').trim());

/** {monthStart} → "2026-11-01" (local date); anything else is returned unchanged. */
export function resolveRelative(value, now = new Date()) {
  const found = RELATIVE.exec(String(value ?? '').trim());
  if (!found) return value;
  const [, name, sign, amount] = found;
  const y = now.getFullYear();
  const m = now.getMonth();
  const today = new Date(y, m, now.getDate());
  const base = {
    today,
    yesterday: new Date(y, m, now.getDate() - 1),
    weekStart: new Date(y, m, now.getDate() - ((now.getDay() + 6) % 7)),
    monthStart: new Date(y, m, 1),
    monthEnd: new Date(y, m + 1, 0),
    prevMonthStart: new Date(y, m - 1, 1),
    prevMonthEnd: new Date(y, m, 0),
    yearStart: new Date(y, 0, 1),
    prevYearStart: new Date(y - 1, 0, 1),
    prevYearEnd: new Date(y - 1, 11, 31),
  }[name];
  if (amount) base.setDate(base.getDate() + Number(amount) * (sign === '-' ? -1 : 1));
  return iso(base);
}

/** The value a run form starts with for a parameter. */
export function startValue(p) {
  if (p.default !== null && p.default !== undefined && p.default !== '') return String(p.type === 'date' ? resolveRelative(p.default) : p.default);
  if (p.allowedValues?.length && p.required) return String(p.allowedValues[0]);
  if (p.type === 'date') return resolveRelative('{today}');
  if (p.type === 'boolean') return 'true';
  return '';
}

/** Ready-made text checks for a parameter (Setup → Parameters → More options). */
export const PATTERN_PRESETS = [
  ['', 'No check'],
  ['\\d{5}-\\d{7}-\\d', 'CNIC (12345-1234567-1)', 'Type the CNIC like 12345-1234567-1'],
  ['03\\d{2}-?\\d{7}', 'Mobile (0300-1234567)', 'Type the mobile number like 0300-1234567'],
  ['[^@\\s]+@[^@\\s]+\\.[A-Za-z]{2,}', 'Email', 'Type a valid email address'],
  ['\\d+', 'Digits only', 'Use digits only'],
  ['[A-Z]{2,5}', 'Code in capitals (2–5 letters)', 'Use 2 to 5 capital letters'],
];

/** The problem with a typed value, the same checks as the server (min, max, pattern), or ''. */
export function valueProblem(p, raw) {
  const value = String(raw ?? '').trim();
  const label = p.label || p.name;
  if (!value) return p.required ? `${label} is required` : '';
  if (p.type === 'integer' || p.type === 'number') {
    const n = Number(value);
    if (!Number.isFinite(n)) return `${label} must be a number`;
    if (p.type === 'integer' && !Number.isInteger(n)) return `${label} must be a whole number`;
    if (p.min !== undefined && p.min !== null && p.min !== '' && n < Number(p.min)) return `${label} must be at least ${p.min}`;
    if (p.max !== undefined && p.max !== null && p.max !== '' && n > Number(p.max)) return `${label} must be at most ${p.max}`;
  }
  if (p.type === 'date') {
    const low = p.min ? resolveRelative(p.min) : '';
    const high = p.max ? resolveRelative(p.max) : '';
    if (low && value < low) return `${label} must be ${low} or later`;
    if (high && value > high) return `${label} must be ${high} or earlier`;
  }
  if (p.pattern && p.type === 'string') {
    try { if (!new RegExp(`^(?:${p.pattern})$`).test(value)) return p.patternMessage || `${label} is not in the right format`; } catch { /* bad pattern: the server says so */ }
  }
  return '';
}
