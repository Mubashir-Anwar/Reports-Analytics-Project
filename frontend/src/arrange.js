// Multi-selection, alignment, distribution, clipboard and snapping for the canvas
// (Jaspersoft Studio's "Arrange" toolbar, plus Photoshop/Illustrator-like smart guides).
import {
  BACKDROP_TYPES, DEFAULT_BAND_HEIGHT, bandKeys, bandPresent, bandWidth, findElement, getBand, mapElements, newId, setBand,
} from './designModel.js';

/** Ids of the selected elements (the primary one, `selection.id`, is always included and last). */
export function selectedIds(selection) {
  if (!selection || selection.kind !== 'element') return [];
  const ids = (selection.ids || []).filter((id) => id !== selection.id);
  return [...ids, selection.id];
}

/** A selection of `ids` with `primary` (default: the last one) as the element shown in Properties. */
export function selectionOf(ids, primary) {
  const unique = [...new Set(ids)];
  if (!unique.length) return { kind: 'report' };
  const id = primary && unique.includes(primary) ? primary : unique[unique.length - 1];
  return unique.length === 1 ? { kind: 'element', id } : { kind: 'element', id, ids: unique };
}

/** Add or remove one element (Shift/Ctrl + click). */
export function toggleInSelection(selection, id) {
  const ids = selectedIds(selection);
  if (ids.includes(id)) {
    const rest = ids.filter((x) => x !== id);
    return rest.length ? selectionOf(rest, selection.id === id ? rest[rest.length - 1] : selection.id) : { kind: 'report' };
  }
  return selectionOf([...ids, id], id);
}

/** [{ bandKey, element }] for the ids that still exist, in selection order. */
export function selectedElements(design, ids) {
  return ids.map((id) => findElement(design, id)).filter(Boolean);
}

/** Apply `patches[id]` (object or element => object) to elements in any band. */
export function patchElements(design, patches) {
  return mapElements(design, (el) => {
    const patch = patches[el.id];
    if (!patch) return el;
    return { ...el, ...(typeof patch === 'function' ? patch(el) : patch) };
  });
}

/** Grow every band so its elements fit (after a move, align or paste) and keep y >= 0. */
export function settleBands(design, keys) {
  let next = design;
  for (const key of new Set(keys)) {
    const band = getBand(next, key);
    if (!band.elements.length) continue;
    const elements = band.elements.map((el) => (el.y < 0 ? { ...el, y: 0 } : el));
    const bottom = elements.reduce((b, el) => Math.max(b, Math.ceil(el.y + el.height)), 0);
    next = setBand(next, key, { ...band, elements, height: Math.max(band.height, bottom) });
  }
  return next;
}

const bounds = (items) => {
  const xs = items.map(({ element: e }) => e.x);
  const ys = items.map(({ element: e }) => e.y);
  const rs = items.map(({ element: e }) => e.x + e.width);
  const bs = items.map(({ element: e }) => e.y + e.height);
  return { left: Math.min(...xs), top: Math.min(...ys), right: Math.max(...rs), bottom: Math.max(...bs) };
};

/**
 * Align the selected elements. With one element it aligns to its band (like Jasper's "Align in container");
 * with more it aligns to the edges of the whole selection. y values are band-relative, as in Jasper.
 */
export function alignElements(design, ids, mode) {
  const items = selectedElements(design, ids).filter(({ element }) => !element.locked);
  if (!items.length) return design;
  const width = bandWidth(design);
  const box = items.length > 1 ? bounds(items) : null;
  const patches = {};
  for (const { bandKey, element: e } of items) {
    const band = getBand(design, bandKey);
    const area = box || { left: 0, top: 0, right: width, bottom: band.height };
    const value = {
      left: { x: area.left },
      right: { x: area.right - e.width },
      hcenter: { x: (area.left + area.right) / 2 - e.width / 2 },
      top: { y: area.top },
      bottom: { y: area.bottom - e.height },
      vmiddle: { y: (area.top + area.bottom) / 2 - e.height / 2 },
    }[mode];
    if (value) patches[e.id] = Object.fromEntries(Object.entries(value).map(([k, v]) => [k, Math.round(Math.max(0, v))]));
  }
  return settleBands(patchElements(design, patches), items.map((i) => i.bandKey));
}

/** Equal gaps between 3+ elements (horizontal: by x; vertical: by y). */
export function distributeElements(design, ids, axis) {
  const items = selectedElements(design, ids).filter(({ element }) => !element.locked);
  if (items.length < 3) return design;
  const pos = axis === 'h' ? 'x' : 'y';
  const size = axis === 'h' ? 'width' : 'height';
  const sorted = [...items].sort((a, b) => a.element[pos] - b.element[pos]);
  const first = sorted[0].element;
  const last = sorted[sorted.length - 1].element;
  const total = sorted.reduce((sum, { element }) => sum + element[size], 0);
  const gap = (last[pos] + last[size] - first[pos] - total) / (sorted.length - 1);
  const patches = {};
  let cursor = first[pos];
  for (const { element } of sorted) {
    patches[element.id] = { [pos]: Math.round(Math.max(0, cursor)) };
    cursor += element[size] + gap;
  }
  return settleBands(patchElements(design, patches), items.map((i) => i.bandKey));
}

/** Same width / height / size as the primary (last selected) element. */
export function matchSize(design, ids, which) {
  const items = selectedElements(design, ids);
  const primary = items[items.length - 1]?.element;
  if (!primary || items.length < 2) return design;
  const patches = {};
  for (const { element } of items) {
    if (element.id === primary.id || element.locked) continue;
    patches[element.id] = {
      ...(which !== 'height' ? { width: primary.width } : {}),
      ...(which !== 'width' ? { height: primary.height } : {}),
    };
  }
  return settleBands(patchElements(design, patches), items.map((i) => i.bandKey));
}

/** Move the selected (unlocked) elements by dx/dy, keeping them inside the band width. */
export function nudgeElements(design, ids, dx, dy) {
  const items = selectedElements(design, ids).filter(({ element }) => !element.locked);
  const width = bandWidth(design);
  const patches = {};
  for (const { element: e } of items) {
    patches[e.id] = { x: Math.round(Math.min(Math.max(0, e.x + dx), Math.max(0, width - e.width))), y: Math.round(Math.max(0, e.y + dy)) };
  }
  return settleBands(patchElements(design, patches), items.map((i) => i.bandKey));
}

export function removeElements(design, ids) {
  const drop = new Set(ids);
  let next = design;
  for (const key of bandKeys(design)) {
    const band = getBand(next, key);
    if (band.elements.some((el) => drop.has(el.id))) next = setBand(next, key, { ...band, elements: band.elements.filter((el) => !drop.has(el.id)) });
  }
  return next;
}

export function setLocked(design, ids, locked) {
  return patchElements(design, Object.fromEntries(ids.map((id) => [id, { locked }])));
}

/* ---------- clipboard (kept in localStorage, so it also works between reports and tabs) ---------- */
const CLIPBOARD_KEY = 'reporting-designer-clipboard';
let memoryClipboard = null;

export function copyToClipboard(design, ids) {
  const items = selectedElements(design, ids).map(({ bandKey, element }) => ({ bandKey, element }));
  if (!items.length) return 0;
  memoryClipboard = { items, copiedAt: Date.now() };
  try { localStorage.setItem(CLIPBOARD_KEY, JSON.stringify(memoryClipboard)); } catch { /* full or blocked: memory copy still works */ }
  return items.length;
}

export function readClipboard() {
  try {
    const stored = JSON.parse(localStorage.getItem(CLIPBOARD_KEY) || 'null');
    if (stored && Array.isArray(stored.items) && (!memoryClipboard || stored.copiedAt >= memoryClipboard.copiedAt)) return stored;
  } catch { /* ignore */ }
  return memoryClipboard;
}

/**
 * Paste the clipboard. `targetBand` given: everything goes into that band (keeping the relative layout);
 * otherwise each element returns to its own band when it is on the canvas, else to Detail.
 * Pasting over the originals shifts the copies by 10,10 so they can be seen.
 */
export function pasteElements(design, clipboard, targetBand) {
  if (!clipboard?.items?.length) return { design, ids: [] };
  const present = bandKeys(design).filter((key) => bandPresent(getBand(design, key)));
  const width = bandWidth(design);
  const top = Math.min(...clipboard.items.map((i) => i.element.y));
  const ids = [];
  let next = design;
  const touched = [];
  for (const { bandKey, element } of clipboard.items) {
    const key = targetBand || (present.includes(bandKey) ? bandKey : (present.includes('detail') ? 'detail' : present[0] || 'detail'));
    const band = getBand(next, key);
    const overlap = band.elements.some((el) => Math.abs(el.x - element.x) < 1 && Math.abs(el.y - (targetBand ? element.y - top : element.y)) < 1);
    const shift = overlap ? 10 : 0;
    const copy = {
      ...element,
      id: newId(),
      locked: false,
      x: Math.round(Math.min(Math.max(0, element.x + shift), Math.max(0, width - element.width))),
      y: Math.round(Math.max(0, (targetBand && targetBand !== bandKey ? element.y - top : element.y) + shift)),
    };
    const kind = key.split(':')[0];
    const height = Math.max(band.height, band.height ? 0 : DEFAULT_BAND_HEIGHT[kind] || 30, Math.ceil(copy.y + copy.height));
    const elements = BACKDROP_TYPES.has(copy.type) ? [copy, ...band.elements] : [...band.elements, copy];
    next = setBand(next, key, { ...band, height, elements });
    ids.push(copy.id);
    touched.push(key);
  }
  return { design: settleBands(next, touched), ids };
}

/* ---------- snapping ---------- */
export const snapToGrid = (value, size) => (size > 0 ? Math.round(value / size) * size : value);

/**
 * Smart-guide candidates: the left / center / right of other elements (any band) and the band edges for x;
 * top / middle / bottom of elements in the same band and the band edges for y. Ruler guides count for x.
 */
export function guideTargets(design, bandKey, excludeIds) {
  const skip = new Set(excludeIds);
  const width = bandWidth(design);
  const xs = new Set([0, width, width / 2, ...(design.page.guides || [])]);
  const ys = new Set();
  for (const key of bandKeys(design)) {
    const band = getBand(design, key);
    if (key === bandKey) { ys.add(0); ys.add(band.height); ys.add(band.height / 2); }
    for (const el of band.elements) {
      if (skip.has(el.id)) continue;
      xs.add(el.x); xs.add(el.x + el.width); xs.add(el.x + el.width / 2);
      if (key === bandKey) { ys.add(el.y); ys.add(el.y + el.height); ys.add(el.y + el.height / 2); }
    }
  }
  return { xs: [...xs], ys: [...ys] };
}

/** Best snap of a box's edges/center to `targets` within `threshold`: { delta, line } or null. */
export function nearestSnap(start, size, targets, threshold) {
  let best = null;
  for (const offset of [0, size / 2, size]) {
    for (const target of targets) {
      const delta = target - (start + offset);
      if (Math.abs(delta) <= threshold && (!best || Math.abs(delta) < Math.abs(best.delta))) best = { delta, line: target };
    }
  }
  return best;
}
