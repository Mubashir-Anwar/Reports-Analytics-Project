/* The look of every page: mode (system / light / dark), accent colour, top bar (dark or in the accent colour),
   high contrast and density, plus named presets. Kept per browser (localStorage "reporting-look").
   A plain script on purpose: the Run reports page loads it in <head> (no flash of the wrong theme) and the React
   pages import it. It sets attributes on <html>; the colours are in theme.css (React) and runner-dark.css. */
(function () {
  var KEY = 'reporting-look';
  var OLD_KEY = 'reporting-theme';
  var ACCENTS = [
    { id: 'indigo', label: 'Indigo', color: '#4f46e5' },
    { id: 'blue', label: 'Blue', color: '#2563eb' },
    { id: 'green', label: 'Green', color: '#15803d' },
    { id: 'teal', label: 'Teal', color: '#0f766e' },
    { id: 'orange', label: 'Orange', color: '#d9480f' },
    { id: 'rose', label: 'Rose', color: '#e11d48' },
    { id: 'violet', label: 'Violet', color: '#7c3aed' },
  ];
  var PRESETS = [
    { id: 'classic', label: 'Classic', text: 'Light, indigo', look: { mode: 'light', accent: 'indigo', bar: 'dark', contrast: 'normal' } },
    { id: 'office', label: 'Office', text: 'Light, blue title bar', look: { mode: 'light', accent: 'blue', bar: 'accent', contrast: 'normal' } },
    { id: 'sheets', label: 'Sheets', text: 'Light, green title bar', look: { mode: 'light', accent: 'green', bar: 'accent', contrast: 'normal' } },
    { id: 'insight', label: 'Insight', text: 'Light, orange on slate', look: { mode: 'light', accent: 'orange', bar: 'dark', contrast: 'normal' } },
    { id: 'midnight', label: 'Midnight', text: 'Dark, violet', look: { mode: 'dark', accent: 'violet', bar: 'dark', contrast: 'normal' } },
    { id: 'ocean', label: 'Ocean', text: 'Dark, teal', look: { mode: 'dark', accent: 'teal', bar: 'accent', contrast: 'normal' } },
    { id: 'contrast', label: 'High contrast', text: 'Dark, strongest lines and text', look: { mode: 'dark', accent: 'indigo', bar: 'dark', contrast: 'high' } },
    { id: 'system', label: 'Follow my computer', text: 'Light or dark like Windows / macOS', look: { mode: 'system', accent: 'indigo', bar: 'dark', contrast: 'normal' } },
  ];
  var DEFAULT = { mode: 'system', accent: 'indigo', bar: 'dark', contrast: 'normal', density: 'comfortable' };
  var MODES = ['system', 'light', 'dark'];

  function clean(look) {
    look = look || {};
    return {
      mode: MODES.indexOf(look.mode) >= 0 ? look.mode : DEFAULT.mode,
      accent: ACCENTS.some(function (a) { return a.id === look.accent; }) ? look.accent : DEFAULT.accent,
      bar: look.bar === 'accent' ? 'accent' : 'dark',
      contrast: look.contrast === 'high' ? 'high' : 'normal',
      density: look.density === 'compact' ? 'compact' : 'comfortable',
    };
  }

  function read() {
    try {
      var saved = JSON.parse(localStorage.getItem(KEY) || 'null');
      if (saved) return clean(saved);
      var old = localStorage.getItem(OLD_KEY);           // the earlier light / dark / system switch
      if (old) return clean({ mode: old });
    } catch (e) { /* private mode or blocked storage */ }
    return clean(DEFAULT);
  }

  function apply(look) {
    look = clean(look || read());
    var root = document.documentElement;
    if (look.mode === 'system') root.removeAttribute('data-theme'); else root.setAttribute('data-theme', look.mode);
    root.setAttribute('data-accent', look.accent);
    root.setAttribute('data-bar', look.bar);
    root.setAttribute('data-contrast', look.contrast);
    root.setAttribute('data-density', look.density);
    return look;
  }

  var listeners = [];
  function save(change) {
    var look = clean(Object.assign({}, read(), change || {}));
    try { localStorage.setItem(KEY, JSON.stringify(look)); localStorage.setItem(OLD_KEY, look.mode); } catch (e) { /* ignore */ }
    apply(look);
    listeners.forEach(function (listener) { listener(look); });
    return look;
  }

  function presetOf(look) {
    look = clean(look);
    var found = PRESETS.filter(function (p) {
      return p.look.mode === look.mode && p.look.accent === look.accent && p.look.bar === look.bar && p.look.contrast === look.contrast;
    })[0];
    return found ? found.id : '';
  }

  /** Is the page dark now (dark mode, or "system" on a dark computer)? */
  function isDark() {
    var mode = read().mode;
    return mode === 'dark' || (mode === 'system' && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
  }

  window.addEventListener('storage', function (event) { if (event.key === KEY) { var look = apply(); listeners.forEach(function (l) { l(look); }); } });

  window.ReportingLook = {
    ACCENTS: ACCENTS, PRESETS: PRESETS, MODES: MODES, read: read, apply: apply, save: save, presetOf: presetOf, isDark: isDark,
    subscribe: function (listener) { listeners.push(listener); return function () { listeners = listeners.filter(function (l) { return l !== listener; }); }; },
  };
  apply();
}());
