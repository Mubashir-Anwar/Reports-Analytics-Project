import { useEffect, useRef } from 'react';

/** PixelFX: a canvas of square pixels that draws a live bar chart.
 *  - on load the pixels dissolve in, one by one in random order;
 *  - every few seconds the bars move to new values, a few pixels at a time;
 *  - pixels near the pointer light up.
 *  Colours come from the theme (--accent, --text, --line), so light, dark and every accent work.
 *  With prefers-reduced-motion the chart is drawn once, without motion. */
export default function PixelField({ cell = 14, gap = 2, bars = 0, className = '', label = '' }) {
  const ref = useRef(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return undefined;
    const draw = canvas.getContext('2d');
    if (!draw) return undefined;
    const still = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    let cols = 0; let rows = 0; let born = []; let target = []; let shown = []; let colors = {};
    let pointer = null; let frame = 0; let raf = 0; let started = performance.now();

    const readColors = () => {
      const css = getComputedStyle(canvas);
      colors = { on: css.getPropertyValue('--accent').trim() || '#3355ff', ink: css.getPropertyValue('--text').trim() || '#111',
        off: css.getPropertyValue('--line').trim() || '#e2e5ee' };
    };
    const values = () => {                       // a believable series: a trend with some noise
      const count = bars || cols;
      let level = 0.35 + Math.random() * 0.2;
      return Array.from({ length: count }, () => { level = Math.min(0.95, Math.max(0.12, level + (Math.random() - 0.42) * 0.22)); return level; });
    };
    const layout = () => {
      const box = canvas.getBoundingClientRect();
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.max(1, Math.round(box.width * ratio));
      canvas.height = Math.max(1, Math.round(box.height * ratio));
      draw.setTransform(ratio, 0, 0, ratio, 0, 0);
      cols = Math.max(1, Math.floor(box.width / (cell + gap)));
      rows = Math.max(1, Math.floor(box.height / (cell + gap)));
      born = Array.from({ length: cols * rows }, () => (still ? 0 : Math.random() * 900));
      target = values();
      shown = target.map((value) => Math.round(value * rows * 0.5));   // start half-way: the chart reads at once
      started = performance.now();
    };
    const heightOf = (col) => {
      const count = target.length;
      const index = Math.min(count - 1, Math.floor((col / cols) * count));
      return Math.round(target[index] * rows);
    };
    const paint = (now) => {
      const box = canvas.getBoundingClientRect();
      draw.clearRect(0, 0, box.width, box.height);
      const age = now - started;
      for (let col = 0; col < cols; col += 1) {
        const goal = heightOf(col);
        if (!still && frame % 2 === 0 && shown[col] !== goal) shown[col] += shown[col] < goal ? 1 : -1;   // one pixel per step
        if (still) shown[col] = goal;
        for (let row = 0; row < rows; row += 1) {
          const index = row * cols + col;
          if (age < born[index]) continue;
          const x = col * (cell + gap); const y = row * (cell + gap);
          const lit = rows - row <= shown[col];
          const top = lit && rows - row === shown[col];
          let near = 0;
          if (pointer) {
            const dx = (x + cell / 2) - pointer.x; const dy = (y + cell / 2) - pointer.y;
            near = Math.max(0, 1 - Math.hypot(dx, dy) / 90);
          }
          draw.globalAlpha = lit ? (top ? 1 : 0.28 + 0.5 * (row / rows)) : 0.35 + near * 0.65;
          draw.fillStyle = lit ? (top ? colors.ink : colors.on) : (near > 0.05 ? colors.on : colors.off);
          draw.fillRect(x, y, cell, cell);
        }
      }
      draw.globalAlpha = 1;
      frame += 1;
    };
    const loop = (now) => { paint(now); raf = requestAnimationFrame(loop); };

    readColors();
    layout();
    const resize = new ResizeObserver(() => { layout(); if (still) paint(performance.now() + 1000); });
    resize.observe(canvas);
    const theme = new MutationObserver(() => { readColors(); if (still) paint(performance.now() + 1000); });
    theme.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'data-accent', 'data-contrast'] });
    const move = (event) => { const box = canvas.getBoundingClientRect(); pointer = { x: event.clientX - box.left, y: event.clientY - box.top }; };
    const leave = () => { pointer = null; };
    let timer = 0;
    if (still) paint(performance.now() + 1000);
    else {
      raf = requestAnimationFrame(loop);
      timer = setInterval(() => { target = values(); }, 3200);
      window.addEventListener('pointermove', move, { passive: true });
      document.addEventListener('pointerleave', leave);
    }
    return () => {
      cancelAnimationFrame(raf); clearInterval(timer); resize.disconnect(); theme.disconnect();
      window.removeEventListener('pointermove', move); document.removeEventListener('pointerleave', leave);
    };
  }, [cell, gap, bars]);
  return <canvas ref={ref} className={`px-field ${className}`} role={label ? 'img' : undefined} aria-label={label || undefined} aria-hidden={label ? undefined : 'true'} />;
}
