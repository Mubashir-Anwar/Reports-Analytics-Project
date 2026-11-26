import { useEffect, useLayoutEffect, useRef, useState } from 'react';

/**
 * Right-click menu like classic report designers's. `items`: { label, icon, onClick, disabled, danger, hint, items } or '-'.
 * An item with `items` opens a submenu on hover.
 */
function MenuList({ items, onClose, style, className = '' }) {
  const [open, setOpen] = useState(-1);
  const ref = useRef(null);
  const [fit, setFit] = useState({ x: 0, y: 0, flip: false });
  const isSub = className === 'sub';
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    const rect = node.getBoundingClientRect();
    const overflowRight = rect.right > window.innerWidth - 4;
    setFit({
      flip: isSub && overflowRight,
      x: !isSub && overflowRight ? window.innerWidth - 4 - rect.right : 0,
      y: rect.bottom > window.innerHeight - 4 ? window.innerHeight - 4 - rect.bottom : 0,
    });
  }, [isSub]);
  const placed = fit.flip ? { ...style, left: 'auto', right: '100%' } : style;
  return (
    <div ref={ref} className={`ctx-menu ${className}`} style={{ ...placed, transform: `translate(${fit.x}px, ${fit.y}px)` }} role="menu" onContextMenu={(e) => e.preventDefault()}>
      {items.filter(Boolean).map((item, index) => {
        if (item === '-') return <div key={`sep-${index}`} className="ctx-sep" />;
        if (item.header) return <div key={`h-${index}`} className="ctx-header">{item.header}</div>;
        const hasSub = Array.isArray(item.items) && item.items.length > 0;
        return (
          <div key={`${item.label}-${index}`} className="ctx-item-wrap" onMouseEnter={() => setOpen(index)}>
            <button
              type="button"
              role="menuitem"
              className={`ctx-item ${item.danger ? 'danger' : ''}`}
              disabled={item.disabled}
              title={item.hint || ''}
              onClick={() => { if (hasSub) { setOpen(index); return; } onClose(); item.onClick?.(); }}
            >
              <span className="ctx-icon">{item.icon || ''}</span>
              <span className="ctx-label">{item.label}</span>
              {item.shortcut && <span className="ctx-short">{item.shortcut}</span>}
              {hasSub && <span className="ctx-arrow">▸</span>}
            </button>
            {hasSub && open === index && !item.disabled && <MenuList items={item.items} onClose={onClose} className="sub" style={{ left: '100%', top: -4 }} />}
          </div>
        );
      })}
    </div>
  );
}

export default function ContextMenu({ menu, onClose }) {
  useEffect(() => {
    if (!menu) return undefined;
    const close = (event) => { if (!event.target.closest?.('.ctx-menu')) onClose(); };
    const key = (event) => { if (event.key === 'Escape') onClose(); };
    const later = window.setTimeout(() => {
      document.addEventListener('mousedown', close);
      document.addEventListener('contextmenu', close);
    }, 0);
    window.addEventListener('keydown', key);
    window.addEventListener('blur', onClose);
    window.addEventListener('resize', onClose);
    return () => {
      window.clearTimeout(later);
      document.removeEventListener('mousedown', close);
      document.removeEventListener('contextmenu', close);
      window.removeEventListener('keydown', key);
      window.removeEventListener('blur', onClose);
      window.removeEventListener('resize', onClose);
    };
  }, [menu, onClose]);
  if (!menu) return null;
  return <MenuList key={`${menu.x},${menu.y}`} items={menu.items} onClose={onClose} style={{ position: 'fixed', left: menu.x, top: menu.y }} />;
}
