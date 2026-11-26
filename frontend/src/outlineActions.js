// Actions behind the right-click menus of the Outline and the canvas (like classic report designers's).
import {
  BUILTIN_VARIABLES, NAME_PATTERN, PALETTE_GROUPS, addBandAfter, addElementToBand, bandKeys, bandNumber, moveBand, sectionBands, sectionOf, bandLabel, bandPresent, contentBottom, createBand, deleteBand,
  findElement, getBand, groupOfBand, mapElements, moveElementToBand, nextName, placeField, removeElement, removeGroup,
  renameCalculated, renameGroup, renameInElements, renameToken, reorderElement, setBand,
} from './designModel.js';
import {
  alignElements, copyToClipboard, distributeElements, matchSize as matchSizeOf, nudgeElements, pasteElements, readClipboard,
  removeElements, selectedElements, selectedIds, selectionOf, setLocked,
} from './arrange.js';
import { joinTexts } from './richText.js';

export const ELEMENT_TYPES = [
  ['staticText', 'T', 'Static Text'],
  ['textField', '𝑓', 'Text Field'],
  ['line', '─', 'Line'],
  ['rectangle', '▭', 'Rectangle'],
  ['image', '🖼', 'Image'],
  ['chart', '▦', 'Chart'],
];

const escapeRe = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const renameParamIn = (text, from, to) => (typeof text === 'string'
  ? renameToken(text, 'P', from, to).replace(new RegExp(`("\\$paramIn"\\s*:\\s*")${escapeRe(from)}(")`, 'g'), `$1${to}$2`)
  : text);

/** `base` itself when free, else base2, base3… (base made a valid name first). */
function freeName(base, taken) {
  let clean = String(base || 'Name').replace(/[^A-Za-z0-9_]/g, '_');
  if (!/^[A-Za-z_]/.test(clean)) clean = `_${clean}`;
  const used = new Set(taken);
  if (!used.has(clean)) return clean;
  let n = 2;
  while (used.has(`${clean}${n}`)) n += 1;
  return `${clean}${n}`;
}

/** `get()` returns the current { report, design }; menus read it when they are opened. */
export function makeActions({ get, dispatch, notify, ui = { current: {} } }) {
  // ui.current: App callbacks for dialogs and tabs (openQuery(tab), openTemplates(), showSource(), preview(), saveFile())
  const callUi = (name, ...args) => () => ui.current[name]?.(...args);
  const design$ = (update) => dispatch({ type: 'DESIGN', update });
  const select = (selection) => dispatch({ type: 'SELECT', selection });
  const fieldNames = () => { const { report, design } = get(); return [...report.columns, ...design.calculatedFields.map((c) => c.name)]; };
  const takenNames = (kind) => {
    const { report, design } = get();
    if (kind === 'param') return report.parameters.map((p) => p.name);
    if (kind === 'variable') return [...design.variables.map((v) => v.name), ...BUILTIN_VARIABLES];
    if (kind === 'calc') return [...design.calculatedFields.map((c) => c.name), ...report.columns];
    if (kind === 'group') return design.groups.map((g) => g.name);
    return [];
  };

  /* ---------- create ---------- */
  const createParameter = () => {
    const name = nextName('Parameter', takenNames('param'));
    const { report } = get();
    dispatch({ type: 'REPORT', patch: { parameters: [...report.parameters, { name, type: 'string', label: name, required: false }] } });
    select({ kind: 'param', name });
    return name;
  };
  const createVariable = (field = '') => {
    const name = field ? freeName(`${field}_SUM`, takenNames('variable')) : nextName('Variable', takenNames('variable'));
    design$((d) => ({ ...d, variables: [...d.variables, { name, calculation: field ? 'Sum' : 'Count', field: field || fieldNames()[0] || '', resetType: 'Report', resetGroup: '' }] }));
    select({ kind: 'variable', name });
    return name;
  };
  const createCalc = () => {
    const name = nextName('Calc', takenNames('calc'));
    design$((d) => ({ ...d, calculatedFields: [...d.calculatedFields, { name, expression: '' }] }));
    select({ kind: 'calc', name });
    return name;
  };
  const createGroup = (field) => {
    const target = field || fieldNames()[0];
    if (!target) { notify('Detect the fields first (Setup tab), then create a group', 'error'); return null; }
    const name = freeName(target, takenNames('group'));
    design$((d) => ({
      ...d,
      groups: [...d.groups, { name, field: target }],
      bands: { ...d.bands, groupHeaders: { ...d.bands.groupHeaders, [name]: { height: 22, elements: [] } }, groupFooters: { ...d.bands.groupFooters, [name]: { height: 22, elements: [] } } },
    }));
    select({ kind: 'band', key: `groupHeader:${name}` });
    notify(`Group ${name} created: its Group Header and Group Footer bands are on the canvas`, 'ok');
    return name;
  };
  const createBandAt = (key) => { design$((d) => createBand(d, key)); select({ kind: 'band', key }); notify(`${bandLabel(key)} band created`, 'ok'); };

  /** Another band in the same section (Detail 2, Summary 2…), right after `key`. */
  const addBandAt = (key) => {
    const { design } = get();
    if (!bandPresent(getBand(design, key))) { createBandAt(key); return; }
    const added = addBandAfter(design, key);
    if (added.key === key) { notify('This section has as many bands as it can have', 'warn'); return; }
    design$(() => added.design);
    select({ kind: 'band', key: added.key });
    notify(`${bandLabel(added.key)} band added`, 'ok');
  };
  const moveBandBy = (key, step) => {
    const moved = moveBand(get().design, key, step);
    design$(() => moved.design);
    select({ kind: 'band', key: moved.key });
  };

  /* ---------- delete ---------- */
  const deleteBandAt = (key) => {
    const count = getBand(get().design, key).elements.length;
    design$((d) => deleteBand(d, key));
    select({ kind: 'report' });
    notify(`${bandLabel(key)} band deleted${count ? ` with ${count} element(s)` : ''} — Ctrl+Z to undo`, 'ok');
  };
  const deleteElementById = (id) => {
    const found = findElement(get().design, id);
    if (!found) return;
    design$((d) => removeElement(d, found.bandKey, id));
    select({ kind: 'band', key: found.bandKey });
  };
  const deleteParameter = (name) => {
    if (!window.confirm(`Delete the parameter ${name}? Expressions and the query that use $P{${name}} must be changed by hand.`)) return;
    const { report } = get();
    dispatch({ type: 'REPORT', patch: { parameters: report.parameters.filter((p) => p.name !== name) } });
    select({ kind: 'report' });
  };
  const deleteVariable = (name) => { design$((d) => ({ ...d, variables: d.variables.filter((v) => v.name !== name) })); select({ kind: 'report' }); notify(`Variable ${name} deleted — Ctrl+Z to undo`, 'ok'); };
  const deleteCalc = (name) => { design$((d) => ({ ...d, calculatedFields: d.calculatedFields.filter((c) => c.name !== name) })); select({ kind: 'report' }); notify(`Calculated field ${name} deleted — Ctrl+Z to undo`, 'ok'); };
  const deleteGroup = (name) => { design$((d) => removeGroup(d, name)); select({ kind: 'report' }); notify(`Group ${name} and its bands deleted — Ctrl+Z to undo`, 'ok'); };

  /* ---------- arrange (multi-selection: toolbar, menus and keyboard) ---------- */
  const ids = () => selectedIds(get().selection);
  const arrange = (operation, message) => {
    const chosen = ids();
    if (!chosen.length) return;
    design$((d) => operation(d, chosen));
    if (message) notify(message, 'ok');
  };
  const align = (mode) => arrange((d, chosen) => alignElements(d, chosen, mode));
  const distribute = (axis) => arrange((d, chosen) => distributeElements(d, chosen, axis));
  const matchSize = (which) => arrange((d, chosen) => matchSizeOf(d, chosen, which));
  const nudge = (dx, dy) => {
    const chosen = ids();
    if (chosen.length) dispatch({ type: 'DESIGN', key: 'nudge', update: (d) => nudgeElements(d, chosen, dx, dy) });
  };
  const order = (toFront) => arrange((d, chosen) => selectedElements(d, chosen).reduce((next, { bandKey, element }) => reorderElement(next, bandKey, element.id, toFront), d));
  /** Ribbon → Home: font, size, bold, alignment, colours on every selected element. */
  const patchSelected = (values) => {
    const chosen = ids();
    if (!chosen.length) return;
    dispatch({ type: 'DESIGN', key: `ribbon-${chosen.join()}-${Object.keys(values).join()}`, update: (d) => mapElements(d, (el) => (chosen.includes(el.id) ? { ...el, ...values } : el)) });
  };
  const toggleLock = () => {
    const chosen = ids();
    if (!chosen.length) return;
    const lock = !selectedElements(get().design, chosen).every(({ element }) => element.locked);
    design$((d) => setLocked(d, chosen, lock));
    notify(lock ? `${chosen.length} element(s) locked: they cannot be moved or resized` : `${chosen.length} element(s) unlocked`, 'ok');
  };
  const copy = () => {
    const count = copyToClipboard(get().design, ids());
    if (count) notify(`${count} element(s) copied — Ctrl+V pastes them (also in another report)`, 'ok');
    return count;
  };
  const removeSelected = () => {
    const chosen = ids();
    if (!chosen.length) return;
    const first = selectedElements(get().design, chosen)[0];
    design$((d) => removeElements(d, chosen));
    select(first ? { kind: 'band', key: first.bandKey } : { kind: 'report' });
    if (chosen.length > 1) notify(`${chosen.length} elements deleted — Ctrl+Z to undo`, 'ok');
  };
  const cut = () => { if (copy()) removeSelected(); };
  /** Paste into `bandKey`, else into the selected band, else back into the elements' own bands. */
  const paste = (bandKey) => {
    const clipboard = readClipboard();
    if (!clipboard?.items?.length) { notify('The clipboard is empty: select elements and press Ctrl+C first', 'error'); return; }
    const { selection, design } = get();
    const target = bandKey || (selection.kind === 'band' ? selection.key : null);
    const result = pasteElements(design, clipboard, target);
    design$(() => result.design);
    select(selectionOf(result.ids));
    notify(`${result.ids.length} element(s) pasted${target ? ` in ${bandLabel(target)}` : ''}`, 'ok');
  };
  const duplicateSelected = () => {
    const { design } = get();
    const items = selectedElements(design, ids());
    if (!items.length) return;
    const result = pasteElements(design, { items }, null);
    design$(() => result.design);
    select(selectionOf(result.ids));
  };
  /** Ctrl+A: every element of the selected band, else of the band of the selected element, else all. */
  const selectAll = () => {
    const { selection, design } = get();
    const bandKey = selection.kind === 'band' ? selection.key : (selection.kind === 'element' ? findElement(design, selection.id)?.bandKey : null);
    const keys = bandKey && !(selection.kind === 'element' && getBand(design, bandKey).elements.every((el) => ids().includes(el.id))) ? [bandKey] : bandKeys(design);
    const all = keys.flatMap((key) => getBand(design, key).elements.map((el) => el.id));
    if (all.length) select(selectionOf(all));
  };
  /**
   * Select column: every element in any band that lies under the selected ones (header, detail, footers):
   * its horizontal middle falls inside a selected element's left..right. Picks a whole table column at once.
   */
  const selectColumn = () => {
    const { design, selection } = get();
    const chosen = selectedElements(design, ids());
    if (!chosen.length) { notify('Select a header or a cell first, then Select column', 'error'); return; }
    const spans = chosen.map(({ element }) => [element.x - 0.5, element.x + element.width + 0.5]);
    const found = bandKeys(design).flatMap((key) => getBand(design, key).elements)
      .filter((el) => spans.some(([left, right]) => el.x + el.width / 2 >= left && el.x + el.width / 2 <= right))
      .map((el) => el.id);
    select(selectionOf(found, selection.kind === 'element' ? selection.id : found[0]));
    notify(`${found.length} element(s) in this column selected`, 'ok');
  };
  /** Select similar: every element of the same kind in the same band as the selected one(s). */
  const selectSimilar = () => {
    const { design, selection } = get();
    const chosen = selectedElements(design, ids());
    if (!chosen.length) { notify('Select an element first, then Select similar', 'error'); return; }
    const wanted = new Set(chosen.map(({ bandKey, element }) => `${bandKey}|${element.type}`));
    const found = bandKeys(design).flatMap((key) => getBand(design, key).elements.filter((el) => wanted.has(`${key}|${el.type}`)).map((el) => el.id));
    select(selectionOf(found, selection.kind === 'element' ? selection.id : found[0]));
    notify(`${found.length} similar element(s) selected`, 'ok');
  };
  const BORDER_PRESETS = {
    all: { borderWidth: 0.5, borders: null },
    none: { borderWidth: 0, borders: null },
    bottom: { borderWidth: 0, borders: { top: 0, right: 0, bottom: 0.5, left: 0 } },
    top: { borderWidth: 0, borders: { top: 0.5, right: 0, bottom: 0, left: 0 } },
    sides: { borderWidth: 0, borders: { top: 0, right: 0.5, bottom: 0, left: 0.5 } },
  };
  /** Ribbon → Borders: the same border on every selected element (keeps each one's border colour). */
  const setBorders = (preset, width) => {
    const values = BORDER_PRESETS[preset];
    if (!values) return;
    const scaled = width && preset !== 'none'
      ? { borderWidth: values.borderWidth ? width : 0, borders: values.borders ? Object.fromEntries(Object.entries(values.borders).map(([s, v]) => [s, v ? width : 0])) : null }
      : values;
    patchSelected(scaled);
  };
  /** Ribbon → Hide / Show: Print When Expression `false` keeps the element in the design but never prints it. */
  const toggleHidden = () => {
    const chosen = selectedElements(get().design, ids());
    if (!chosen.length) return;
    const hide = !chosen.every(({ element }) => String(element.printWhenExpression || '').trim() === 'false');
    const custom = chosen.filter(({ element }) => !['', 'false'].includes(String(element.printWhenExpression || '').trim())).length;
    patchSelected({ printWhenExpression: hide ? 'false' : '' });
    notify(hide
      ? `${chosen.length} element(s) hidden: kept in the design, not printed${custom ? ` (${custom} had their own Print When Expression — Ctrl+Z to undo)` : ''}`
      : `${chosen.length} element(s) shown again`, 'ok');
  };
  /** Ribbon → Stretch: "Stretch with overflow" on the selected text elements (long data makes the row taller). */
  const toggleStretch = () => {
    const texts = selectedElements(get().design, ids()).filter(({ element }) => ['staticText', 'textField'].includes(element.type));
    if (!texts.length) { notify('Stretch works on Static Text and Text Field elements', 'error'); return; }
    const on = !texts.every(({ element }) => element.stretch);
    const textIds = texts.map(({ element }) => element.id);
    dispatch({ type: 'DESIGN', key: `stretch-${textIds.join()}`, update: (d) => mapElements(d, (el) => (textIds.includes(el.id) ? { ...el, stretch: on } : el)) });
    notify(on ? `${texts.length} text element(s) now stretch: long data makes the row taller instead of being cut` : `Stretch switched off on ${texts.length} element(s)`, 'ok');
  };
  const LOOK_KEYS = ['fontName', 'fontSize', 'bold', 'italic', 'underline', 'color', 'backColor', 'align', 'vAlign', 'padding',
    'borderWidth', 'borderColor', 'borders', 'lineStyle', 'markup', 'stretch'];
  /** Match format (format painter): the look of the primary (last clicked) element on all the others. */
  const matchFormat = () => {
    const { design, selection } = get();
    const chosen = ids();
    const source = selection.kind === 'element' ? findElement(design, selection.id)?.element : null;
    if (!source || chosen.length < 2) { notify('Select 2 or more elements; the last one clicked gives its look to the others', 'error'); return; }
    const look = Object.fromEntries(LOOK_KEYS.filter((key) => key in source).map((key) => [key, source[key]]));
    const textual = ['staticText', 'textField'];
    dispatch({ type: 'DESIGN', key: `format-${chosen.join()}`, update: (d) => mapElements(d, (el) => {
      if (!chosen.includes(el.id) || el.id === source.id) return el;
      const fit = textual.includes(el.type) ? look : Object.fromEntries(Object.entries(look).filter(([key]) => !['stretch', 'markup'].includes(key)));
      return { ...el, ...fit };
    }) });
    notify(`The look of the last clicked element was copied to ${chosen.length - 1} element(s)`, 'ok');
  };

  const arrangeMenu = (count) => ({
    label: 'Align', icon: '⇤',
    items: [
      { header: count > 1 ? 'To each other' : 'In the band' },
      { label: 'Left', onClick: () => align('left') }, { label: 'Center', onClick: () => align('hcenter') }, { label: 'Right', onClick: () => align('right') },
      '-',
      { label: 'Top', onClick: () => align('top') }, { label: 'Middle', onClick: () => align('vmiddle') }, { label: 'Bottom', onClick: () => align('bottom') },
    ],
  });

  /** Delete key on whatever is selected (element, band, parameter, variable, calculated field, group). */
  const deleteSelection = (selection) => {
    if (selection.kind === 'element') removeSelected();
    else if (selection.kind === 'band' && bandPresent(getBand(get().design, selection.key))) deleteBandAt(selection.key);
    else if (selection.kind === 'param') deleteParameter(selection.name);
    else if (selection.kind === 'variable') deleteVariable(selection.name);
    else if (selection.kind === 'calc') deleteCalc(selection.name);
    else if (selection.kind === 'group') deleteGroup(selection.name);
  };

  /* ---------- rename (returns an error text, or '' when done) ---------- */
  const rename = (kind, from, rawTo) => {
    const to = String(rawTo || '').trim();
    if (!to || to === from) return '';
    if (!NAME_PATTERN.test(to)) return 'Use letters, digits and _ only, starting with a letter or _';
    if (takenNames(kind).includes(to)) return `${to} is already used`;
    const { report } = get();
    if (kind === 'param') {
      dispatch({
        type: 'REPORT',
        patch: {
          parameters: report.parameters.map((p) => (p.name === from ? { ...p, name: to, label: p.label === from ? to : p.label } : { ...p, expression: renameParamIn(p.expression, from, to) })),
          querySource: renameParamIn(report.querySource, from, to),
          collection: renameToken(report.collection, 'P', from, to),
        },
      });
      design$((d) => renameInElements(d, 'P', from, to));
    } else if (kind === 'variable') {
      design$((d) => ({ ...renameInElements(d, 'V', from, to), variables: d.variables.map((v) => (v.name === from ? { ...v, name: to } : v)) }));
    } else if (kind === 'calc') {
      design$((d) => renameCalculated(d, from, to));
    } else if (kind === 'group') {
      design$((d) => renameGroup(d, from, to));
    }
    select({ kind, name: to });
    notify(`Renamed ${from} → ${to}; expressions that used it were updated`, 'ok');
    return '';
  };

  /* ---------- placing things in bands ---------- */
  const presentBands = () => bandKeys(get().design).filter((key) => bandPresent(getBand(get().design, key)));
  /** Run an operation that returns { design, id } on the current design, then select the new element. */
  /**
   * Static Text ⇄ Text Field, same place and style. "Muhammad Ali" becomes a Text Field whose expression is that text
   * (it prints the same until you change it); with one element the Expression Editor opens to put $F{…} / $P{…} in it.
   */
  const convertText = (targetIds, to) => {
    const from = to === 'textField' ? 'staticText' : 'textField';
    const { design } = get();
    const hits = targetIds.filter((id) => findElement(design, id)?.element.type === from);
    if (!hits.length) return;
    design$((d) => mapElements(d, (el) => {
      if (!hits.includes(el.id)) return el;
      if (to === 'textField') {
        const { text, ...rest } = el;
        return { ...rest, type: 'textField', expression: String(text ?? ''), pattern: '', blankWhenNull: true, stretch: el.stretch ?? false };
      }
      const { expression, pattern, blankWhenNull, evaluationTime, ...rest } = el;
      void pattern; void blankWhenNull; void evaluationTime;
      return { ...rest, type: 'staticText', text: String(expression ?? '') };
    }));
    if (hits.length === 1) {
      select({ kind: 'element', id: hits[0] });
      if (to === 'textField') ui.current.editExpression?.(hits[0]);
    }
    notify(to === 'textField'
      ? `${hits.length === 1 ? 'Now a Text Field' : `${hits.length} texts are now Text Fields`} — put $F{…}, $P{…} or a formula in the expression`
      : `${hits.length === 1 ? 'Now a Static Text' : `${hits.length} Text Fields are now Static Texts`} — it prints the text as written`, 'ok');
  };

  /**
   * Several texts → one paragraph (e.g. an imported "Your fee of" + bold "Rs 5,000" + "is due"): one box with
   * formatted words, each part keeping its bold / italic / colour / size, fields as data between the words.
   */
  const joinSelected = (targetIds) => {
    const items = selectedElements(get().design, targetIds).filter(({ element }) => ['staticText', 'textField'].includes(element.type));
    if (items.length < 2) { notify('Select 2 or more Static Texts / Text Fields to join them into one paragraph', 'error'); return; }
    if (new Set(items.map((item) => item.bandKey)).size > 1) { notify('The texts to join must be in the same band', 'error'); return; }
    const joined = joinTexts(items.map((item) => item.element));
    const drop = items.map((item) => item.element.id).filter((one) => one !== joined.id);
    design$((d) => mapElements(removeElements(d, drop), (el) => (el.id === joined.id ? joined : el)));
    select({ kind: 'element', id: joined.id });
    notify(`${items.length} texts are now one paragraph with formatted words — select words in Properties to change them (Ctrl+Z to undo)`, 'ok');
  };

  /** Imported design: a Static Text (e.g. "Muhammad Ali") becomes a Text Field of a field, same place and style. */
  const makeField = (id) => {
    const { report, design } = get();
    const found = findElement(design, id);
    if (!found || found.element.type !== 'staticText') return;
    const known = fieldNames();
    const sample = String(found.element.text || '').trim().slice(0, 40);
    const typed = window.prompt(`Which field fills “${sample}”?${known.length ? `\nFields: ${known.slice(0, 30).join(', ')}` : ''}\nA new name is added to Setup → Fields.`, known[0] || 'Name');
    const name = String(typed || '').trim().replace(/^\$F\{(.*)\}$/, '$1');
    if (!name) return;
    if (!/^[A-Za-z_][\w.]{0,79}$/.test(name)) { notify(`“${name}” is not a field name: letters, numbers, _ and . only`, 'error'); return; }
    design$((d) => mapElements(d, (el) => {
      if (el.id !== id) return el;
      const { text, ...rest } = el;
      void text;
      return { ...rest, type: 'textField', expression: `$F{${name}}`, pattern: '', blankWhenNull: true, stretch: false };
    }));
    if (!report.columns.includes(name) && !design.calculatedFields.some((c) => c.name === name)) {
      dispatch({ type: 'REPORT', patch: { columns: [...report.columns, name] } });
    }
    select({ kind: 'element', id });
    notify(`“${sample}” now shows $F{${name}}`, 'ok');
  };

  const withNewElement = (operation) => {
    const result = operation(get().design);
    if (!result.id) return;
    design$(() => result.design);
    select({ kind: 'element', id: result.id });
  };
  const addElement = (key, type, extra) => withNewElement((d) => addElementToBand(d, key, type, extra));
  const placeExpression = (key, expression) => addElement(key, 'textField', { expression, width: 140, height: 16 });
  const placeInMenu = (expression) => ({
    label: 'Place in band', icon: '⤵',
    items: presentBands().map((key) => ({ label: bandLabel(key), onClick: () => placeExpression(key, expression) })),
  });
  const addElementMenu = (key) => ({
    label: 'Add element', icon: '＋',
    items: PALETTE_GROUPS.flatMap((group, index) => [
      ...(index ? ['-'] : []),
      { header: group.title },
      ...group.items
        .filter(([type]) => type !== 'percentage') // needs a field: use the Palette
        .map(([type, icon, label]) => ({ label, icon, onClick: () => addElement(key, type) })),
    ]),
  });

  /* ---------- menus ---------- */
  const createBandMenu = () => {
    const { design } = get();
    const absent = bandKeys(design).filter((key) => !bandPresent(getBand(design, key)));
    const last = {};                                   // the last band of each section: another one goes after it
    bandKeys(design).forEach((key) => { if (bandPresent(getBand(design, key))) last[sectionOf(key)] = key; });
    const items = absent.map((key) => ({ label: bandLabel(key), onClick: () => createBandAt(key) }));
    if (Object.keys(last).length) {
      items.push(...(items.length ? ['-'] : []), { header: 'One more of' },
        ...Object.values(last).map((key) => ({ label: bandLabel(sectionOf(key)).replace(/ 1$/, ''), icon: '＋', onClick: () => addBandAt(key) })));
    }
    return { label: 'Create Band', icon: '▭', disabled: !items.length, items };
  };

  const reportMenu = () => [
    { header: get().report.title || 'Report' },
    { label: 'Undo', icon: '↶', shortcut: 'Ctrl+Z', disabled: !get().past.length, onClick: () => dispatch({ type: 'UNDO' }) },
    { label: 'Redo', icon: '↷', shortcut: 'Ctrl+Y', disabled: !get().future.length, onClick: () => dispatch({ type: 'REDO' }) },
    '-',
    { label: 'Show Properties', icon: '⚙', onClick: () => select({ kind: 'report' }) },
    { label: 'Dataset and Query…', icon: '🛢', onClick: callUi('openQuery', 'query') },
    { label: 'Sort Fields…', icon: '⇅', onClick: callUi('openQuery', 'sort') },
    { label: 'Filter Expression…', icon: '⏚', onClick: callUi('openQuery', 'filter') },
    '-',
    { label: 'Create Parameter', icon: '$P', onClick: createParameter },
    { label: 'Create Variable', icon: '$V', onClick: () => createVariable() },
    { label: 'Create Calculated Field', icon: 'ƒ', onClick: createCalc },
    { label: 'Create Group', icon: '⊞', disabled: !fieldNames().length, items: fieldNames().map((f) => ({ label: `Group by ${f}`, onClick: () => createGroup(f) })) },
    createBandMenu(),
    '-',
    { label: 'Apply a layout template…', icon: '▦', onClick: callUi('openTemplates') },
    { label: 'Show Source', icon: '</>', onClick: callUi('showSource') },
    { label: 'Preview', icon: '▶', onClick: callUi('preview') },
    { label: 'Save as file (.mrd)', icon: '💾', onClick: callUi('saveFile') },
    { label: 'Save as JRXML', icon: '⬇', onClick: callUi('saveJrxml') },
  ];

  const bandMenu = (key) => {
    const { design } = get();
    const band = getBand(design, key);
    const present = bandPresent(band);
    const group = groupOfBand(key);
    const items = [{ header: bandLabel(key) }];
    if (!present) {
      items.push({ label: 'Create Band', icon: '＋', onClick: () => createBandAt(key) });
    } else {
      items.push(
        addElementMenu(key),
        { label: 'Paste here', icon: '📋', shortcut: 'Ctrl+V', disabled: !readClipboard()?.items?.length, onClick: () => paste(key) },
        { label: 'Select all elements', icon: '⬚', disabled: !band.elements.length, onClick: () => select(selectionOf(band.elements.map((el) => el.id))) },
        { label: 'Fit band to content', icon: '↕', disabled: !band.elements.length, onClick: () => design$((d) => setBand(d, key, { ...getBand(d, key), height: contentBottom(getBand(d, key)) })) },
        { label: 'Properties', icon: '⚙', onClick: () => select({ kind: 'band', key }) },
        '-',
        { label: `Add another band after ${bandLabel(key)}`, icon: '＋', onClick: () => addBandAt(key) },
        ...(sectionBands(design, key).length > 1 ? [
          { label: 'Move band up', icon: '↑', disabled: bandNumber(key) === 1, onClick: () => moveBandBy(key, -1) },
          { label: 'Move band down', icon: '↓', disabled: bandNumber(key) === sectionBands(design, key).length, onClick: () => moveBandBy(key, 1) },
        ] : []),
        '-',
        { label: 'Delete Band', icon: '✕', danger: true, shortcut: 'Del', onClick: () => deleteBandAt(key) },
      );
    }
    if (group) {
      items.push('-', { label: `Group ${group} properties`, icon: '⊞', onClick: () => select({ kind: 'group', name: group }) }, { label: `Delete group ${group}`, icon: '✕', danger: true, onClick: () => deleteGroup(group) });
    }
    return items;
  };

  const elementMenu = (id) => {
    const found = findElement(get().design, id);
    if (!found) return [];
    const { bandKey } = found;
    const chosen = ids();
    const locked = selectedElements(get().design, chosen.includes(id) ? chosen : [id]).every(({ element }) => element.locked);
    const lockItem = { label: locked ? 'Unlock' : 'Lock', icon: locked ? '🔓' : '🔒', shortcut: 'Ctrl+L', onClick: toggleLock };
    const selectItems = [
      { label: 'Select column', icon: '▥', title: 'Everything under it in every band (header, detail, footer)', onClick: selectColumn },
      { label: 'Select similar', icon: '◫', onClick: selectSimilar },
    ];
    const formatItems = [
      { label: 'Hide / Show', icon: '◌', onClick: toggleHidden },
      { label: 'Stretch with long text', icon: '↕', onClick: toggleStretch },
      { label: 'Borders', icon: '▢', items: [
        { label: 'All sides', onClick: () => setBorders('all') }, { label: 'Bottom only', onClick: () => setBorders('bottom') },
        { label: 'Top only', onClick: () => setBorders('top') }, { label: 'Left and right', onClick: () => setBorders('sides') },
        { label: 'No border', onClick: () => setBorders('none') },
      ] },
    ];
    const clipboardItems = [
      { label: 'Copy', icon: '⧉', shortcut: 'Ctrl+C', onClick: copy },
      { label: 'Cut', icon: '✂', shortcut: 'Ctrl+X', onClick: cut },
      { label: 'Paste', icon: '📋', shortcut: 'Ctrl+V', disabled: !readClipboard()?.items?.length, onClick: () => paste() },
      { label: 'Duplicate', icon: '⧉', shortcut: 'Ctrl+D', onClick: duplicateSelected },
    ];
    if (chosen.length > 1 && chosen.includes(id)) {
      return [
        { header: `${chosen.length} elements selected` },
        ...clipboardItems,
        ...(() => {
          const types = chosen.map((one) => findElement(get().design, one)?.element.type);
          const statics = types.filter((t) => t === 'staticText').length;
          const fields = types.filter((t) => t === 'textField').length;
          return [
            ...(statics ? [{ label: `Convert ${statics} Static Text${statics > 1 ? 's' : ''} to Text Field${statics > 1 ? 's' : ''}`, icon: 'ƒx', onClick: () => convertText(chosen, 'textField') }] : []),
            ...(fields ? [{ label: `Convert ${fields} Text Field${fields > 1 ? 's' : ''} to Static Text`, icon: 'T', onClick: () => convertText(chosen, 'staticText') }] : []),
            ...(statics + fields > 1 ? [{ label: `Join ${statics + fields} texts into one paragraph`, icon: '¶', onClick: () => joinSelected(chosen) }] : []),
          ];
        })(),
        '-',
        ...selectItems,
        { label: 'Match format of the last clicked', icon: '🖌', onClick: matchFormat },
        ...formatItems,
        '-',
        arrangeMenu(chosen.length),
        { label: 'Distribute', icon: '⇹', disabled: chosen.length < 3, items: [{ label: 'Horizontally', onClick: () => distribute('h') }, { label: 'Vertically', onClick: () => distribute('v') }] },
        { label: 'Same size', icon: '▣', items: [{ label: 'Width', onClick: () => matchSize('width') }, { label: 'Height', onClick: () => matchSize('height') }, { label: 'Width and height', onClick: () => matchSize('both') }] },
        { label: 'Bring to Front', icon: '⬆', onClick: () => order(true) },
        { label: 'Send to Back', icon: '⬇', onClick: () => order(false) },
        lockItem,
        '-',
        { label: `Delete ${chosen.length} elements`, icon: '✕', danger: true, shortcut: 'Del', onClick: removeSelected },
      ];
    }
    const others = presentBands().filter((key) => key !== bandKey);
    return [
      { header: `${found.element.type} in ${bandLabel(bandKey)}` },
      { label: 'Properties', icon: '⚙', onClick: () => select({ kind: 'element', id }) },
      ...(found.element.type === 'staticText' ? [
        { label: 'Convert to Text Field', icon: 'ƒx', onClick: () => convertText([id], 'textField') },
        { label: 'Make it a field…', icon: '$F', onClick: () => makeField(id) },
      ] : []),
      ...(found.element.type === 'textField' ? [{ label: 'Convert to Static Text', icon: 'T', onClick: () => convertText([id], 'staticText') }] : []),
      ...clipboardItems,
      '-',
      ...selectItems,
      ...formatItems,
      '-',
      arrangeMenu(1),
      { label: 'Bring to Front', icon: '⬆', onClick: () => design$((d) => reorderElement(d, bandKey, id, true)) },
      { label: 'Send to Back', icon: '⬇', onClick: () => design$((d) => reorderElement(d, bandKey, id, false)) },
      { label: 'Move to band', icon: '⇅', disabled: !others.length, items: others.map((key) => ({ label: bandLabel(key), onClick: () => { design$((d) => moveElementToBand(d, bandKey, key, id)); select({ kind: 'element', id }); } })) },
      lockItem,
      '-',
      { label: 'Delete', icon: '✕', danger: true, shortcut: 'Del', onClick: () => deleteElementById(id) },
    ];
  };

  const parametersMenu = () => [{ label: 'Create Parameter', icon: '＋', onClick: createParameter }];
  const parameterMenu = (name, onRename) => [
    { header: `$P{${name}}` },
    { label: 'Properties', icon: '⚙', onClick: () => select({ kind: 'param', name }) },
    { label: 'Rename', icon: '✎', shortcut: 'F2', onClick: onRename },
    placeInMenu(`$P{${name}}`),
    '-',
    { label: 'Create Parameter', icon: '＋', onClick: createParameter },
    { label: 'Delete', icon: '✕', danger: true, shortcut: 'Del', onClick: () => deleteParameter(name) },
  ];

  const fieldsMenu = (onDetect) => [{ label: 'Detect fields from the query…', icon: '⟳', onClick: onDetect }];
  const fieldMenu = (name) => [
    { header: `$F{${name}}` },
    { label: 'Place in Detail (with label)', icon: '⤵', onClick: () => withNewElement((d) => placeField(d, name)) },
    placeInMenu(`$F{${name}}`),
    { label: `Group by ${name}`, icon: '⊞', onClick: () => createGroup(name) },
    { label: `Create Variable (Sum of ${name})`, icon: '$V', onClick: () => createVariable(name) },
  ];

  const variablesMenu = () => [{ label: 'Create Variable', icon: '＋', onClick: () => createVariable() }];
  const variableMenu = (name, onRename) => [
    { header: `$V{${name}}` },
    { label: 'Properties', icon: '⚙', onClick: () => select({ kind: 'variable', name }) },
    { label: 'Rename', icon: '✎', shortcut: 'F2', onClick: onRename },
    placeInMenu(`$V{${name}}`),
    '-',
    { label: 'Create Variable', icon: '＋', onClick: () => createVariable() },
    { label: 'Delete', icon: '✕', danger: true, shortcut: 'Del', onClick: () => deleteVariable(name) },
  ];
  const builtinMenu = (name) => [{ header: `$V{${name}} (built-in)` }, placeInMenu(name === 'PAGE_COUNT' ? 'Page $V{PAGE_NUMBER} of $V{PAGE_COUNT}' : `$V{${name}}`)];

  const calcsMenu = () => [{ label: 'Create Calculated Field', icon: '＋', onClick: createCalc }];
  const calcMenu = (name, onRename) => [
    { header: `ƒ $F{${name}}` },
    { label: 'Properties', icon: '⚙', onClick: () => select({ kind: 'calc', name }) },
    { label: 'Rename', icon: '✎', shortcut: 'F2', onClick: onRename },
    placeInMenu(`$F{${name}}`),
    '-',
    { label: 'Create Calculated Field', icon: '＋', onClick: createCalc },
    { label: 'Delete', icon: '✕', danger: true, shortcut: 'Del', onClick: () => deleteCalc(name) },
  ];

  const canvasMenu = () => [
    { header: 'Page' },
    { label: 'Report properties', icon: '⚙', onClick: () => select({ kind: 'report' }) },
    { label: 'Dataset and Query…', icon: '🛢', onClick: callUi('openQuery', 'query') },
    createBandMenu(),
    { label: 'Paste here', icon: '📋', shortcut: 'Ctrl+V', disabled: !readClipboard()?.items?.length, onClick: () => paste() },
    '-',
    { label: 'Apply a layout template…', icon: '▦', onClick: callUi('openTemplates') },
  ];

  return {
    makeField, convertText, joinSelected, rename, deleteSelection, createBandAt, addBandAt, moveBandBy, createGroup, createParameter, createVariable, createCalc,
    align, distribute, matchSize, nudge, order, toggleLock, patchSelected, copy, cut, paste, duplicateSelected, selectAll, removeSelected,
    selectColumn, selectSimilar, setBorders, toggleHidden, toggleStretch, matchFormat,
    reportMenu, bandMenu, elementMenu, parametersMenu, parameterMenu, fieldsMenu, fieldMenu,
    variablesMenu, variableMenu, builtinMenu, calcsMenu, calcMenu, canvasMenu, createBandMenu,
  };
}
