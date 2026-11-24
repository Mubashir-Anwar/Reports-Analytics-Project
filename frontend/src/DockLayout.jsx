// Movable designer panels (Repository explorer, Outline, Palette, Properties), like Eclipse / Jaspersoft Studio views:
//   * drag a panel's title bar to the other side, to another place in a side, or onto the report to float it;
//   * each title bar: minimise, maximise (in its side), move up / down, move to the other side, float, hide;
//   * drag the inner edge of a side to make it wider or narrower;
//   * the layout is kept per browser (localStorage); View → Panels → Reset layout brings back the default.
// Tablets and phones keep the slide-in drawers: floating panels are shown in their side there.
import { useCallback, useEffect, useRef, useState } from 'react';

const KEY = 'designer-layout';
const TIPS_KEY = 'designer-panel-tips-hidden';
/** Each panel's own icon, colour and tips (shown under its title bar; ✕ hides them for that panel). */
export const PANEL_LOOK = {
  explorer: { icon: '🗂️', tint: 'blue', tips: [
    'Right-click a report or folder for open, rename, copy, publish and delete.',
    'Drag a report onto a folder to move it.',
    'Green dot = published and up to date · orange = changed since it was published.',
    'Data adapters live here too: right-click one to test it or browse its collections.',
  ] },
  outline: { icon: '🧭', tint: 'violet', tips: [
    'Click an element here to select it on the page, even when it is hidden behind another.',
    'Drag a field from Fields onto a band to make a Text Field.',
    'Double-click a name to rename it · right-click for more.',
    'Parameters, variables and groups are all edited from here.',
  ] },
  palette: { icon: '🎨', tint: 'pink', tips: [
    'Click an element, then click a band, or drag it straight onto a band.',
    'Type in “Find an element…” to filter: try “chart” or “barcode”.',
    'Esc puts the armed element away.',
    'Hold Shift while drawing to keep lines straight.',
  ] },
  properties: { icon: '🎛️', tint: 'amber', tips: [
    'Select several elements to change them all at once.',
    'Expressions: click ƒx to open the editor with fields, parameters and functions.',
    'Arrow keys nudge the selection by 1 px, Shift + arrow by 10 px.',
    'Nothing selected? You are editing the report itself (page, dataset, Excel).',
  ] },
};
const readTipsHidden = () => { try { return JSON.parse(localStorage.getItem(TIPS_KEY) || '{}') || {}; } catch { return {}; } };

/** A rotating hint under a panel's title bar. */
function PanelTip({ name }) {
  const tips = PANEL_LOOK[name]?.tips || [];
  const [hidden, setHidden] = useState(() => Boolean(readTipsHidden()[name]));
  const [index, setIndex] = useState(0);
  useEffect(() => {
    if (hidden || tips.length < 2) return undefined;
    const timer = setInterval(() => setIndex((i) => (i + 1) % tips.length), 9000);
    return () => clearInterval(timer);
  }, [hidden, tips.length]);
  if (hidden || !tips.length) return null;
  const hide = () => {
    setHidden(true);
    try { localStorage.setItem(TIPS_KEY, JSON.stringify({ ...readTipsHidden(), [name]: true })); } catch { /* storage blocked */ }
  };
  return (
    <div className="dock-tip" role="note">
      <span className="dock-tip-spark" aria-hidden="true">✨</span>
      <button type="button" className="dock-tip-text" key={index} title="Next tip" onClick={() => setIndex((i) => (i + 1) % tips.length)}>{tips[index]}</button>
      <span className="dock-tip-dots" aria-hidden="true">{tips.map((tip, i) => <i key={tip} className={i === index ? 'on' : ''} />)}</span>
      <button type="button" className="dock-tip-close" title="Hide the tips of this panel" aria-label="Hide the tips of this panel" onClick={hide}>✕</button>
    </div>
  );
}

/** Show the tips of every panel again (View → Panels). */
export function showPanelTips() {
  try { localStorage.removeItem(TIPS_KEY); } catch { /* storage blocked */ }
}
export const PANEL_TITLES = { explorer: 'Repository explorer', outline: 'Outline', palette: 'Palette', properties: 'Properties' };
const DEFAULT_LAYOUT = {
  order: { left: ['explorer', 'outline'], right: ['palette', 'properties'] },
  collapsed: {},
  maximized: { left: null, right: null },
  floating: {},                 // name -> { x, y, w, h, from }
  width: { left: 300, right: 300 },
};
const WIDTH = { min: 180, max: 640 };
const DOCK_TYPE = 'text/x-designer-panel';

function readLayout() {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || 'null');
    if (!saved?.order) return DEFAULT_LAYOUT;
    const known = Object.keys(PANEL_TITLES);
    const placed = [...(saved.order.left || []), ...(saved.order.right || []), ...Object.keys(saved.floating || {})];
    const missing = known.filter((name) => !placed.includes(name));          // a panel added in a later version
    return {
      ...DEFAULT_LAYOUT, ...saved,
      order: { left: (saved.order.left || []).filter((n) => known.includes(n)), right: [...(saved.order.right || []).filter((n) => known.includes(n)), ...missing] },
      width: { ...DEFAULT_LAYOUT.width, ...(saved.width || {}) },
      maximized: { ...DEFAULT_LAYOUT.maximized, ...(saved.maximized || {}) },
    };
  } catch { return DEFAULT_LAYOUT; }
}

export function useNarrow() {
  const query = '(max-width: 1100px)';
  const [narrow, setNarrow] = useState(() => typeof window !== 'undefined' && window.matchMedia(query).matches);
  useEffect(() => {
    const list = window.matchMedia(query);
    const change = () => setNarrow(list.matches);
    list.addEventListener('change', change);
    return () => list.removeEventListener('change', change);
  }, []);
  return narrow;
}

/** The layout state and every operation on it. */
export function useDockLayout() {
  const [layout, setLayout] = useState(readLayout);
  useEffect(() => { try { localStorage.setItem(KEY, JSON.stringify(layout)); } catch { /* private mode */ } }, [layout]);

  const without = (order, name) => ({ left: order.left.filter((n) => n !== name), right: order.right.filter((n) => n !== name) });
  const sideOf = (l, name) => (l.order.left.includes(name) ? 'left' : l.order.right.includes(name) ? 'right' : null);

  const moveTo = useCallback((name, side, index) => setLayout((l) => {
    const order = without(l.order, name);
    const list = [...order[side]];
    list.splice(index === undefined ? list.length : Math.max(0, Math.min(index, list.length)), 0, name);
    const floating = { ...l.floating };
    delete floating[name];
    return { ...l, order: { ...order, [side]: list }, floating, collapsed: { ...l.collapsed, [name]: false } };
  }), []);
  const shift = useCallback((name, step) => setLayout((l) => {
    const side = sideOf(l, name);
    if (!side) return l;
    const list = [...l.order[side]];
    const at = list.indexOf(name);
    const to = at + step;
    if (to < 0 || to >= list.length) return l;
    [list[at], list[to]] = [list[to], list[at]];
    return { ...l, order: { ...l.order, [side]: list } };
  }), []);
  const toggleCollapse = useCallback((name) => setLayout((l) => {
    const side = sideOf(l, name);
    const maximized = side && l.maximized[side] === name ? { ...l.maximized, [side]: null } : l.maximized;
    return { ...l, maximized, collapsed: { ...l.collapsed, [name]: !l.collapsed[name] } };
  }), []);
  const toggleMaximize = useCallback((name) => setLayout((l) => {
    const side = sideOf(l, name);
    if (!side) return l;
    return { ...l, maximized: { ...l.maximized, [side]: l.maximized[side] === name ? null : name }, collapsed: { ...l.collapsed, [name]: false } };
  }), []);
  const float = useCallback((name, at) => setLayout((l) => {
    const from = sideOf(l, name) || l.floating[name]?.from || 'right';
    const w = 320;
    const h = Math.min(480, Math.round(window.innerHeight * 0.6));
    const x = Math.round(Math.max(8, Math.min((at?.x ?? window.innerWidth / 2) - w / 2, window.innerWidth - w - 8)));
    const y = Math.round(Math.max(60, Math.min((at?.y ?? 140) - 14, window.innerHeight - h - 8)));
    return { ...l, order: without(l.order, name), floating: { ...l.floating, [name]: { x, y, w, h, from } } };
  }), []);
  const placeFloating = useCallback((name, box) => setLayout((l) => (l.floating[name]
    ? { ...l, floating: { ...l.floating, [name]: { ...l.floating[name], ...box } } } : l)), []);
  const setWidth = useCallback((side, value) => setLayout((l) => ({
    ...l, width: { ...l.width, [side]: Math.round(Math.max(WIDTH.min, Math.min(WIDTH.max, value))) } })), []);
  const reset = useCallback(() => setLayout(DEFAULT_LAYOUT), []);

  return { layout, moveTo, shift, toggleCollapse, toggleMaximize, float, placeFloating, setWidth, reset, sideOf: (name) => sideOf(layout, name) };
}

const BarButton = ({ title, onClick, children }) => (
  <button type="button" className="dock-btn" title={title} aria-label={title} onClick={(e) => { e.stopPropagation(); onClick(); }}>{children}</button>
);

/** A panel's title bar with its buttons; dragging it moves the panel. */
function DockBar({ name, side, index, count, ops, onHide, floating, onPointerDown }) {
  const collapsed = Boolean(ops.layout.collapsed[name]);
  const maximized = side && ops.layout.maximized[side] === name;
  const other = side === 'left' ? 'right' : 'left';
  return (
    <div className="dock-bar" draggable={!floating} onPointerDown={onPointerDown}
      onDragStart={(e) => { e.dataTransfer.setData(DOCK_TYPE, name); e.dataTransfer.effectAllowed = 'move'; document.body.classList.add('dock-dragging'); }}
      onDragEnd={() => { document.body.classList.remove('dock-dragging'); showTarget(null); }}
      onDoubleClick={() => (floating ? null : ops.toggleCollapse(name))}
      title={floating ? 'Drag to move · the corner makes it bigger' : 'Drag to move it: to the other side, up or down, or onto the report to float it · double-click to minimise'}>
      <span className="dock-grip" aria-hidden="true">⠿</span>
      <span className={`dock-ico tint-${PANEL_LOOK[name]?.tint || 'blue'}`} aria-hidden="true">{PANEL_LOOK[name]?.icon}</span>
      <span className="dock-title">{PANEL_TITLES[name]}</span>
      <span className="dock-btns">
        {!floating && (
          <>
            <BarButton title={collapsed ? 'Restore' : 'Minimise'} onClick={() => ops.toggleCollapse(name)}>{collapsed ? '▢' : '–'}</BarButton>
            <BarButton title={maximized ? 'Restore the other panels' : 'Maximise in this side'} onClick={() => ops.toggleMaximize(name)}>{maximized ? '❐' : '⤢'}</BarButton>
            {index > 0 && <BarButton title="Move up" onClick={() => ops.shift(name, -1)}>↑</BarButton>}
            {index < count - 1 && <BarButton title="Move down" onClick={() => ops.shift(name, 1)}>↓</BarButton>}
            <BarButton title={`Move to the ${other} side`} onClick={() => ops.moveTo(name, other)}>{side === 'left' ? '⇥' : '⇤'}</BarButton>
            <BarButton title="Float: a separate window over the report" onClick={() => ops.float(name)}>⧉</BarButton>
          </>
        )}
        {floating && (
          <>
            <BarButton title="Dock on the left" onClick={() => ops.moveTo(name, 'left')}>⇤</BarButton>
            <BarButton title="Dock on the right" onClick={() => ops.moveTo(name, 'right')}>⇥</BarButton>
          </>
        )}
        <BarButton title="Hide (View → Panels shows it again)" onClick={onHide}>✕</BarButton>
      </span>
    </div>
  );
}

/**
 * One side column. `render(name)` gives a panel's content; `visible[name]` says whether it is switched on.
 * `narrow`: drawers — floating panels are listed in their side.
 */
export function DockSide({ side, ops, visible, render, onHide, narrow }) {
  const ref = useRef(null);
  const [dropAt, setDropAt] = useState(null);
  const { layout } = ops;
  const floatingHere = narrow ? Object.entries(layout.floating).filter(([, f]) => (f.from || 'right') === side).map(([name]) => name) : [];
  const names = [...layout.order[side], ...floatingHere].filter((name) => visible[name]);
  const maximized = layout.maximized[side] && names.includes(layout.maximized[side]) ? layout.maximized[side] : null;

  const indexAt = (clientY) => {
    const bars = [...(ref.current?.querySelectorAll(':scope > .dock') || [])];
    const found = bars.findIndex((bar) => clientY < bar.getBoundingClientRect().top + bar.getBoundingClientRect().height / 2);
    return found < 0 ? bars.length : found;
  };
  const resize = (event) => {
    event.preventDefault();
    const startX = event.clientX;
    const startW = layout.width[side];
    const move = (e) => ops.setWidth(side, startW + (side === 'left' ? e.clientX - startX : startX - e.clientX));
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); document.body.classList.remove('dock-resizing'); };
    document.body.classList.add('dock-resizing');
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };

  return (
    <aside ref={ref} className={`${side} dock-side ${dropAt !== null ? 'drop-on' : ''}`}
      onDragOver={(e) => { if (e.dataTransfer.types.includes(DOCK_TYPE)) { e.preventDefault(); setDropAt(indexAt(e.clientY)); } }}
      onDragLeave={(e) => { if (!ref.current?.contains(e.relatedTarget)) setDropAt(null); }}
      onDrop={(e) => {
        const name = e.dataTransfer.getData(DOCK_TYPE);
        setDropAt(null);
        if (!name) return;
        e.preventDefault();
        const index = indexAt(e.clientY);
        const current = layout.order[side].indexOf(name);
        ops.moveTo(name, side, current >= 0 && current < index ? index - 1 : index);
      }}>
      {names.map((name, index) => {
        const collapsed = maximized ? maximized !== name : Boolean(layout.collapsed[name]);
        return (
          <div key={name} className={`dock dock-${name} ${collapsed ? 'collapsed' : ''} ${maximized === name ? 'maxed' : ''} ${dropAt === index ? 'drop-before' : ''}`} aria-label={PANEL_TITLES[name]}>
            <DockBar name={name} side={side} index={index} count={names.length} ops={ops} onHide={() => onHide(name)} />
            {!collapsed && <PanelTip name={name} />}
            {!collapsed && <div className="dock-body">{render(name)}</div>}
          </div>
        );
      })}
      {dropAt === names.length && <div className="dock-drop-end" />}
      {!narrow && <div className="side-resizer" role="separator" aria-orientation="vertical" aria-label={`Resize the ${side} panels`} title="Drag to make the panels wider or narrower" onPointerDown={resize} onDoubleClick={() => ops.setWidth(side, 300)} />}
    </aside>
  );
}

const EDGE = 80;   // px from the window edge where a dragged panel snaps into that side
/** Which side a panel dragged to (x, y) docks into: near the left/right edge, or over a side column. */
function dockTarget(x, y) {
  if (x <= EDGE) return 'left';
  if (x >= window.innerWidth - EDGE) return 'right';
  const aside = document.elementsFromPoint(x, y).find((n) => n.classList?.contains('dock-side'));
  if (aside) return aside.classList.contains('left') ? 'left' : 'right';
  return null;
}
/** Shows the side a dropped panel will dock into (a highlighted strip at that window edge). */
function showTarget(side) {
  if (side) document.body.dataset.dockTarget = side;
  else delete document.body.dataset.dockTarget;
}

/** A floated panel: drag its title bar to move it (to a window edge or a side: it docks there), its corner to resize it. */
export function FloatingPanel({ name, ops, render, onHide }) {
  const box = ops.layout.floating[name];
  const ref = useRef(null);
  useEffect(() => {
    const node = ref.current;
    if (!node || typeof ResizeObserver === 'undefined') return undefined;
    let timer = null;
    const observer = new ResizeObserver(() => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => ops.placeFloating(name, { w: Math.round(node.offsetWidth), h: Math.round(node.offsetHeight) }), 200);
    });
    observer.observe(node);
    return () => { observer.disconnect(); window.clearTimeout(timer); };
  }, [name, ops]);
  if (!box) return null;
  const drag = (event) => {
    if (event.button !== 0 || event.target.closest('button')) return;
    event.preventDefault();
    const start = { x: event.clientX, y: event.clientY, left: box.x, top: box.y };
    let target = null;
    document.body.classList.add('dock-dragging');
    const move = (e) => {
      ops.placeFloating(name, {
        x: Math.round(Math.max(0, Math.min(start.left + e.clientX - start.x, window.innerWidth - 80))),
        y: Math.round(Math.max(0, Math.min(start.top + e.clientY - start.y, window.innerHeight - 40))),
      });
      target = dockTarget(e.clientX, e.clientY);
      showTarget(target);
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      document.body.classList.remove('dock-dragging');
      showTarget(null);
      if (target) ops.moveTo(name, target);       // dropped on an edge or a side: it sticks there with the other panels
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };
  return (
    <div ref={ref} className={`dock dock-floating dock-${name}`} role="dialog" aria-label={PANEL_TITLES[name]}
      style={{ left: box.x, top: box.y, width: box.w, height: box.h }}>
      <DockBar name={name} ops={ops} floating onHide={() => onHide(name)} onPointerDown={drag} />
      <PanelTip name={name} />
      <div className="dock-body">{render(name)}</div>
    </div>
  );
}

/** The report area accepts a dropped panel title bar: the panel floats where it was dropped. */
export function floatDropProps(ops) {
  return {
    onDragOver: (e) => {
      if (!e.dataTransfer.types.includes(DOCK_TYPE)) return;
      e.preventDefault();
      showTarget(e.clientX <= EDGE ? 'left' : e.clientX >= window.innerWidth - EDGE ? 'right' : null);
    },
    onDrop: (e) => {
      const name = e.dataTransfer.getData(DOCK_TYPE);
      if (!name) return;
      e.preventDefault();
      showTarget(null);
      const side = e.clientX <= EDGE ? 'left' : e.clientX >= window.innerWidth - EDGE ? 'right' : null;
      if (side) ops.moveTo(name, side);          // dropped at a window edge: dock it on that side
      else ops.float(name, { x: e.clientX, y: e.clientY });
    },
  };
}
