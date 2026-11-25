// The dialog of the Report Server pages: on <body>, so it covers the whole screen (the page itself is animated, which would
// keep a fixed box inside its column). Escape or a click outside closes it.
import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import './pop-dialog.css';

export default function PopDialog({ label, icon, tint = 'v', title, subtitle, wide = false, busy = false, onClose, onSubmit, children, footer }) {
  const box = useRef(null);
  const close = useRef(null);
  close.current = busy ? null : onClose;
  useEffect(() => {
    const before = document.activeElement;
    box.current?.focus();
    const onKey = (event) => { if (event.key === 'Escape') close.current?.(); };
    window.addEventListener('keydown', onKey);
    return () => { window.removeEventListener('keydown', onKey); before?.focus?.(); };
  }, []);
  const Body = onSubmit ? 'form' : 'div';
  return createPortal(
    <div className="pop-back" onMouseDown={(event) => event.target === event.currentTarget && close.current?.()}>
      <Body className={`pop ${wide ? 'wide' : ''}`} role="dialog" aria-modal="true" aria-label={label} ref={box} tabIndex={-1} onSubmit={onSubmit}>
        <header className={`pop-top ${tint}`}>
          <span className="pop-icon" aria-hidden="true">{icon}</span>
          <div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>
          <button type="button" className="pop-x" onClick={onClose} disabled={busy} aria-label="Close">✕</button>
        </header>
        <div className="pop-body">{children}</div>
        <footer className="pop-foot">{footer}</footer>
      </Body>
    </div>, document.body);
}
