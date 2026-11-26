// A drag handle that makes a side panel wider or narrower (Query's lists and helper, Analytics' contents).
// Drag it, or focus it and use ← →; a double click goes back to the normal width. Kept per browser.
import { useEffect, useRef, useState } from 'react';
import './resizer.css';

const read = (key, fallback) => { try { const n = Number(localStorage.getItem(key)); return Number.isFinite(n) && n > 0 ? n : fallback; } catch { return fallback; } };

/** [width, setWidth] kept in localStorage under `key`, always between min and max. */
export function useStoredWidth(key, normal, min, max) {
  const clamp = (n) => Math.round(Math.min(max, Math.max(min, n)));
  const [width, setWidth] = useState(() => clamp(read(key, normal)));
  useEffect(() => { try { localStorage.setItem(key, String(width)); } catch { /* private mode */ } }, [key, width]);
  return [width, (n) => setWidth(clamp(typeof n === 'function' ? n(width) : n))];
}

/** edge: which side of the panel the handle is on ('right': dragging right makes it wider; 'left': dragging left does). */
export default function Resizer({ width, onWidth, normal, min, max, edge = 'right', label = 'Panel width', className = '', style }) {
  const start = useRef(null);
  const [dragging, setDragging] = useState(false);
  const down = (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    start.current = { x: event.clientX, width };
    setDragging(true);
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };
  const move = (event) => {
    if (!start.current) return;
    const dx = event.clientX - start.current.x;
    onWidth(start.current.width + (edge === 'right' ? dx : -dx));
  };
  const up = () => { start.current = null; setDragging(false); };
  useEffect(() => {
    document.documentElement.classList.toggle('fx-resizing', dragging);
    return () => document.documentElement.classList.remove('fx-resizing');
  }, [dragging]);
  const key = (event) => {
    const step = event.shiftKey ? 64 : 16;
    const wider = edge === 'right' ? 'ArrowRight' : 'ArrowLeft';
    const narrower = edge === 'right' ? 'ArrowLeft' : 'ArrowRight';
    if (event.key === wider) { event.preventDefault(); onWidth(width + step); }
    if (event.key === narrower) { event.preventDefault(); onWidth(width - step); }
    if (event.key === 'Home') { event.preventDefault(); onWidth(normal); }
  };
  return (
    <div role="separator" aria-orientation="vertical" aria-label={label} aria-valuenow={width} aria-valuemin={min} aria-valuemax={max} tabIndex={0}
      className={`fx-resizer edge-${edge} ${dragging ? 'on' : ''} ${className}`} style={style}
      title="Drag to make it wider or narrower · double-click: normal width"
      onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up} onDoubleClick={() => onWidth(normal)} onKeyDown={key}>
      <span className="fx-resizer-grip" aria-hidden="true"><i /><i /><i /></span>
      {dragging && <span className="fx-resizer-size" aria-hidden="true">{width}px</span>}
    </div>
  );
}
