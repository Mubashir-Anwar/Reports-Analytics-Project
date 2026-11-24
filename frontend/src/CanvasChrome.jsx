// The new look's canvas helpers: the panel rail on the left edge (Files, Outline, Palette, Properties — the old
// View → Panels buttons) and the zoom pill at the bottom of the page (the old View → Zoom and Canvas buttons).
import { ZOOM_LIMITS, clampZoom } from './designModel.js';

const PANELS = [
  ['explorer', 'Files', 'M3 6h6l2 2h10v11H3zM3 10h18', 'Repository explorer: Workspace, Server and Data Adapters'],
  ['outline', 'Outline', 'M5 6h14M9 12h10M9 18h10M5 12h.01M5 18h.01', 'Outline of the report: bands and elements in print order'],
  ['palette', 'Palette', 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z', 'Palette of elements: drag one onto a band'],
  ['properties', 'Properties', 'M4 7h9M17 7h3M4 17h3M11 17h9M15 5v4M9 15v4', 'Properties of what is selected'],
];
const DEFAULT_SIDE = { explorer: 'left', outline: 'left', palette: 'right', properties: 'right' };
const narrow = () => typeof window !== 'undefined' && window.matchMedia('(max-width: 1100px)').matches;

export function PanelRail({ panels, setPanels, drawer, setDrawer, panelSide }) {
  const sideOf = (name) => panelSide?.(name) || DEFAULT_SIDE[name];
  const on = (name) => (narrow() ? drawer === sideOf(name) : panels[name]);
  const toggle = (name) => {
    if (narrow()) { setDrawer(drawer === sideOf(name) ? null : sideOf(name)); return; }
    setDrawer(null);
    setPanels((p) => ({ ...p, [name]: !p[name] }));
  };
  return (
    <nav className="panel-rail" aria-label="Panels">
      {PANELS.map(([name, label, path, title]) => (
        <button key={name} type="button" className={on(name) ? 'on' : ''} aria-pressed={on(name)} title={`${title} (${on(name) ? 'click to hide' : 'click to show'})`} onClick={() => toggle(name)}>
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={path} /></svg>
          <span>{label}</span>
        </button>
      ))}
    </nav>
  );
}

export function ZoomPill({ zoom, setZoom, prefs, setPrefs }) {
  const flip = (key) => setPrefs((p) => ({ ...p, [key]: !p[key] }));
  const toggles = [['showGrid', 'Grid', 'Show the grid'], ['snapGrid', 'Snap', 'Snap to grid (hold Alt while dragging to move freely)'], ['smartGuides', 'Guides', 'Smart guides: snap to the edges and centers of other elements'], ['showRulers', 'Rulers', 'Rulers']];
  return (
    <div className="zoom-pill" role="toolbar" aria-label="Zoom and canvas">
      <button type="button" aria-label="Zoom out" title="Zoom out (Ctrl −)" onClick={() => setZoom((z) => clampZoom(z - ZOOM_LIMITS.step))}>−</button>
      <button type="button" className="zp-pct" title="Back to 100% (Ctrl 0)" onClick={() => setZoom(1)}>{Math.round(zoom * 100)}%</button>
      <button type="button" aria-label="Zoom in" title="Zoom in (Ctrl +)" onClick={() => setZoom((z) => clampZoom(z + ZOOM_LIMITS.step))}>+</button>
      <span className="zp-sep" aria-hidden="true" />
      {toggles.map(([key, label, title]) => (
        <button key={key} type="button" className={`zp-flag ${prefs[key] ? 'on' : ''}`} aria-pressed={Boolean(prefs[key])} title={title} onClick={() => flip(key)}>{label}</button>
      ))}
    </div>
  );
}
