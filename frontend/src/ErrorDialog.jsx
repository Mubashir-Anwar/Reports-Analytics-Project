// A popup for errors: a short title, the server's message in plain words, a hint and buttons that fix the usual cause.
import { useEffect, useRef } from 'react';
import { explainError } from './errorHelp.js';

export default function ErrorDialog({ message, onAction, onClose }) {
  const info = explainError(message);
  const ok = useRef(null);
  useEffect(() => {
    ok.current?.focus();
    const onKey = (event) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="aw-back error-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="error-dialog" role="alertdialog" aria-labelledby="error-title" aria-describedby="error-text">
        <div className="error-head">
          <span className="error-icon" aria-hidden="true">!</span>
          <div>
            {info.where && <p className="error-where">{info.where}</p>}
            <h3 id="error-title">{info.title}</h3>
          </div>
        </div>
        <p id="error-text" className="error-text">{info.text}</p>
        {info.hint && <div className="error-hint"><b>How to fix it</b><p>{info.hint}</p></div>}
        <div className="error-buttons">
          {info.actions.map(([id, label]) => (
            <button key={id} type="button" className="btn" onClick={() => { onAction(id); onClose(); }}>{label}</button>
          ))}
          <span className="grow" />
          <button ref={ok} type="button" className="btn primary" onClick={onClose}>OK</button>
        </div>
      </div>
    </div>
  );
}
