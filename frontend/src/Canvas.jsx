import { useRef } from 'react';
import { bandKeys, bandLabel, bandWidth, defaultElement, describeElement, getBand, setBand } from './designModel.js';

const MIN_W = 10;
const MIN_H = 1;

function ElementView({ element, selected, zoom, onPointerDown, onResizeDown }) {
  const style = {
    left: element.x * zoom,
    top: element.y * zoom,
    width: element.width * zoom,
    height: Math.max(element.height * zoom, 2),
    fontSize: element.fontSize * zoom,
    fontWeight: element.bold ? 700 : 400,
    fontStyle: element.italic ? 'italic' : 'normal',
    textAlign: element.align,
    ...(['staticText', 'textField'].includes(element.type) ? { display: 'flex', flexDirection: 'column', justifyContent: { top: 'flex-start', middle: 'center', bottom: 'flex-end' }[element.vAlign || 'top'] } : {}),
    color: element.color,
    background: element.backColor || undefined,
    border: element.borderWidth && element.type !== 'line' ? `${element.borderWidth}px solid ${element.borderColor || '#000'}` : undefined,
  };
  let body = null;
  if (element.type === 'line') {
    body = <div className="el-line" style={{ borderTop: `${Math.max(element.borderWidth || 1, 1)}px solid ${element.borderColor || element.color}` }} />;
  } else if (element.type === 'image') {
    body = element.src ? <img src={element.src} alt="" draggable={false} /> : <span className="el-hint">Image</span>;
  } else if (element.type === 'chart') {
    body = <span className="el-hint">▦ {element.chartType} chart{element.title ? ` · ${element.title}` : ''}</span>;
  } else if (element.type !== 'rectangle') {
    body = <span className={`el-text ${element.type === 'textField' ? 'is-field' : ''}`}>{describeElement(element)}</span>;
  }
  return (
    <div
      className={`el el-${element.type} ${selected ? 'selected' : ''}`}
      style={style}
      onPointerDown={onPointerDown}
      title={`${element.type} · ${Math.round(element.x)},${Math.round(element.y)} · ${Math.round(element.width)}×${Math.round(element.height)}`}
    >
      {body}
      {selected && <span className="handle" onPointerDown={onResizeDown} />}
    </div>
  );
}

export default function Canvas({ design, selection, tool, zoom, dispatch }) {
  const bandRefs = useRef({});
  const width = bandWidth(design);
  const { margins } = design.page;
  const selectedId = selection.kind === 'element' ? selection.id : null;

  const addElement = (bandKey, element) => {
    dispatch({
      type: 'DESIGN',
      update: (d) => {
        const band = getBand(d, bandKey);
        const height = Math.max(band.height, 30, Math.ceil(element.y + element.height));
        return setBand(d, bandKey, { ...band, height, elements: [...band.elements, element] });
      },
    });
    dispatch({ type: 'SELECT', selection: { kind: 'element', id: element.id } });
    dispatch({ type: 'TOOL', tool: null });
  };

  const localPoint = (bandKey, event) => {
    const rect = bandRefs.current[bandKey].getBoundingClientRect();
    return { x: Math.max(0, (event.clientX - rect.left) / zoom), y: Math.max(0, (event.clientY - rect.top) / zoom) };
  };

  const onBandClick = (bandKey, event) => {
    if (event.target !== event.currentTarget) return;
    if (tool) {
      const point = localPoint(bandKey, event);
      const element = defaultElement(tool, Math.round(point.x), Math.round(point.y));
      element.x = Math.min(element.x, Math.max(0, width - element.width));
      addElement(bandKey, element);
    } else {
      dispatch({ type: 'SELECT', selection: { kind: 'band', key: bandKey } });
    }
  };

  const onDrop = (bandKey, event) => {
    event.preventDefault();
    const point = localPoint(bandKey, event);
    const palette = event.dataTransfer.getData('application/x-palette');
    const field = event.dataTransfer.getData('application/x-field');
    let element = null;
    if (palette) element = defaultElement(palette, Math.round(point.x), Math.round(point.y));
    else if (field) {
      element = { ...defaultElement('textField', Math.round(point.x), Math.round(point.y)), expression: `$F{${field}}`, width: 110, height: 16 };
    }
    if (element) {
      element.x = Math.min(element.x, Math.max(0, width - element.width));
      addElement(bandKey, element);
    }
  };

  const beginDrag = (bandKey, element, mode) => (event) => {
    event.stopPropagation();
    event.preventDefault();
    dispatch({ type: 'SELECT', selection: { kind: 'element', id: element.id } });
    dispatch({ type: 'SNAPSHOT' });
    const start = { x: event.clientX, y: event.clientY, ex: element.x, ey: element.y, ew: element.width, eh: element.height };
    const bandHeight = getBand(design, bandKey).height;
    const move = (e) => {
      const dx = (e.clientX - start.x) / zoom;
      const dy = (e.clientY - start.y) / zoom;
      const patch = mode === 'move'
        ? { x: Math.round(Math.min(Math.max(0, start.ex + dx), width - start.ew)), y: Math.round(Math.min(Math.max(0, start.ey + dy), Math.max(bandHeight - start.eh, 0))) }
        : { width: Math.round(Math.min(Math.max(MIN_W, start.ew + dx), width - start.ex)), height: Math.round(Math.max(MIN_H, start.eh + dy)) };
      dispatch({
        type: 'LIVE',
        update: (d) => {
          const band = getBand(d, bandKey);
          return setBand(d, bandKey, { ...band, elements: band.elements.map((el) => (el.id === element.id ? { ...el, ...patch } : el)) });
        },
      });
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };

  const beginBandResize = (bandKey) => (event) => {
    event.stopPropagation();
    event.preventDefault();
    dispatch({ type: 'SNAPSHOT' });
    const startY = event.clientY;
    const startHeight = getBand(design, bandKey).height;
    const move = (e) => {
      const height = Math.round(Math.min(Math.max(0, startHeight + (e.clientY - startY) / zoom), 1500));
      dispatch({ type: 'LIVE', update: (d) => setBand(d, bandKey, { ...getBand(d, bandKey), height }) });
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };

  return (
    <div className="canvas-scroll" onClick={(e) => e.target === e.currentTarget && dispatch({ type: 'SELECT', selection: { kind: 'report' } })}>
      <div className="page" style={{ width: design.page.width * zoom, paddingTop: margins.top * zoom, paddingBottom: margins.bottom * zoom }}>
        {bandKeys(design).map((key) => {
          const band = getBand(design, key);
          const bandSelected = selection.kind === 'band' && selection.key === key;
          return (
            <div className="band-row" key={key} style={{ paddingLeft: margins.left * zoom }}>
              <div className={`band-label ${bandSelected ? 'selected' : ''}`} style={{ width: 0 }}>
                <span onClick={() => dispatch({ type: 'SELECT', selection: { kind: 'band', key } })}>{bandLabel(key)} · {band.height}px</span>
              </div>
              <div
                ref={(node) => { bandRefs.current[key] = node; }}
                className={`band ${bandSelected ? 'selected' : ''} ${tool ? 'armed' : ''}`}
                style={{ width: width * zoom, height: Math.max(band.height * zoom, 14) }}
                onClick={(event) => onBandClick(key, event)}
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => onDrop(key, event)}
              >
                {band.elements.map((element) => (
                  <ElementView
                    key={element.id}
                    element={element}
                    zoom={zoom}
                    selected={element.id === selectedId}
                    onPointerDown={beginDrag(key, element, 'move')}
                    onResizeDown={beginDrag(key, element, 'resize')}
                  />
                ))}
                <span className="band-resize" onPointerDown={beginBandResize(key)} title="Band height" />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
