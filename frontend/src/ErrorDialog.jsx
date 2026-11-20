// A popup for errors: what went wrong, WHERE to fix it (tab → section → field / line), the steps, a "Go there" button
// that opens the place and highlights it, and every other problem the designer can see in the report.
import { useEffect, useRef } from 'react';
import { explainError } from './errorHelp.js';

export default function ErrorDialog({ message, problems = [], onAction, onGo, onClose }) {
  const info = explainError(message);
  const ok = useRef(null);
  useEffect(() => {
    ok.current?.focus();
    const onKey = (event) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  const go = (focus, note) => { onGo?.({ ...focus, note }); onClose(); };
  // the same problem as the error itself is not listed twice
  const others = info.importing ? [] : problems.filter((p) => !(info.focus && p.focus?.target === info.focus.target && p.focus?.name === info.focus.name));
  const nothingKnown = !info.focus && !info.steps.length;

  return (
    <div className="aw-back error-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="error-dialog" role="alertdialog" aria-labelledby="error-title" aria-describedby="error-text">
        <div className="error-head">
          <span className="error-icon" aria-hidden="true">!</span>
          <div>
            {info.stage && <p className="error-where">{info.stage}</p>}
            <h3 id="error-title">{info.title}</h3>
          </div>
        </div>
        <p id="error-text" className="error-text">{info.text}</p>

        {(info.place || info.steps.length > 0 || (info.hint && !nothingKnown)) && (
          <div className="error-hint">
            <b>How to fix it</b>
            {info.place && <p className="error-place"><span>Where</span><code>{info.place}</code></p>}
            {info.hint && !nothingKnown && <p>{info.hint}</p>}
            {info.steps.length > 0 && <ol className="error-steps">{info.steps.map((s) => <li key={s}>{s}</li>)}</ol>}
          </div>
        )}

        {others.length > 0 && (
          <div className="error-problems">
            <b>{nothingKnown ? `Found in this report (${others.length})` : `Also check (${others.length})`}</b>
            <ul>
              {others.slice(0, 8).map((p) => (
                <li key={`${p.text}-${p.place}`} className={p.level}>
                  <div><span>{p.text}</span><small>{p.fix}{p.place ? ` · ${p.place}` : ''}</small></div>
                  {p.focus && <button type="button" className="btn sm" onClick={() => go(p.focus, `${p.text}. ${p.fix}`)}>Go there</button>}
                </li>
              ))}
            </ul>
          </div>
        )}
        {nothingKnown && !others.length && (
          <div className="error-hint"><b>How to fix it</b><p>{info.hint}</p>
            <ol className="error-steps"><li>Check Setup → 2: the data adapter, the query (press Read fields) and the Fields.</li><li>Check Setup → 3: every $P&#123;…&#125; of the query is a parameter.</li><li>Then check the Design tab: every $F&#123;…&#125; is one of the Fields.</li></ol>
          </div>
        )}

        <div className="error-buttons">
          {info.focus && <button type="button" className="btn primary-soft" onClick={() => go(info.focus, `${info.title}. ${info.steps[0] || info.hint || ''}`)}>➜ Go there</button>}
          {!info.focus && nothingKnown && <button type="button" className="btn" onClick={() => go({ target: 'query' }, `The server said: “${String(info.text || message).slice(0, 300)}”. Compare it with the query below (line numbers on the left), then press Read fields.`)}>Open the query</button>}
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
