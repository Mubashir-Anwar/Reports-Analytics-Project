// A query box with line numbers (like Notepad++): the numbers scroll with the text, the line an error points to is
// marked red, the status line shows where the cursor is, and "Go to line" jumps to a line and selects it.
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';

/** Select line `line` (1-based) of a textarea and scroll it into the middle of the box. */
export function selectLine(area, line) {
  if (!area) return;
  const lines = area.value.split('\n');
  const n = Math.min(Math.max(1, Number(line) || 1), lines.length);
  const start = lines.slice(0, n - 1).reduce((sum, l) => sum + l.length + 1, 0);
  area.focus({ preventScroll: true });
  area.setSelectionRange(start, start + lines[n - 1].length);
  const height = parseFloat(getComputedStyle(area).lineHeight) || 18;
  area.scrollTop = Math.max(0, (n - 1) * height - area.clientHeight / 2 + height);
}

const CodeArea = forwardRef(function CodeArea({ value, onChange, rows = 12, placeholder, markLine, markNote, markFixed, focusName, ...rest }, ref) {
  const area = useRef(null);
  const gutter = useRef(null);
  const [cursor, setCursor] = useState({ line: 1, column: 1 });
  const [jump, setJump] = useState('');
  useImperativeHandle(ref, () => area.current);
  const count = Math.max(1, String(value || '').split('\n').length);

  const sync = () => { if (gutter.current && area.current) gutter.current.scrollTop = area.current.scrollTop; };
  const track = () => {
    const el = area.current;
    if (!el) return;
    const before = el.value.slice(0, el.selectionStart);
    const line = before.split('\n').length;
    setCursor({ line, column: before.length - before.lastIndexOf('\n') });
  };
  useEffect(sync);

  const go = (event) => {
    event.preventDefault();
    if (!jump) return;
    selectLine(area.current, jump);
    sync();
    track();
  };
  const numbers = [];
  for (let i = 1; i <= count; i += 1) {
    const cls = i === markLine ? `mark${markFixed ? ' fixed' : ''}` : i === cursor.line ? 'here' : undefined;
    numbers.push(<div key={i} className={cls} title={i === markLine ? markNote || (markFixed ? 'Fixed on this line' : 'The error is on this line') : undefined}>{i}</div>);
  }
  return (
    <div className="code-area" data-focus={focusName}>
      <div className="code-area-box">
        <div className="code-gutter" ref={gutter} aria-hidden="true">{numbers}<div className="code-gutter-pad" /></div>
        <textarea ref={area} className="code" rows={rows} spellCheck={false} wrap="off" value={value} placeholder={placeholder}
          onChange={(e) => { onChange(e); track(); }} onScroll={sync} onKeyUp={track} onClick={track} onSelect={track} {...rest} />
      </div>
      <div className="code-status">
        <span>Line {cursor.line}, column {cursor.column} · {count} lines</span>
        {markLine ? (
          <button type="button" className={`linkish mark${markFixed ? ' fixed' : ''}`} onClick={() => { selectLine(area.current, markLine); sync(); track(); }}>
            {markFixed ? `✓ Fixed on line ${markLine} — show it` : `● Error on line ${markLine} — show it`}
          </button>
        ) : null}
        <form className="code-jump" onSubmit={go}>
          <label>Go to line <input type="number" min="1" max={count} value={jump} onChange={(e) => setJump(e.target.value)} /></label>
          <button type="submit" className="btn sm" disabled={!jump}>Go</button>
        </form>
      </div>
    </div>
  );
});

export default CodeArea;
