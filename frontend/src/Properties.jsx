import { bandLabel, findElement, getBand, setBand } from './designModel.js';

function Row({ label, children }) {
  return <label className="prop-row"><span>{label}</span>{children}</label>;
}

function Num({ value, onChange, min, max, step = 1 }) {
  return <input type="number" value={Number.isFinite(value) ? value : ''} min={min} max={max} step={step} onChange={(e) => onChange(e.target.value === '' ? 0 : Number(e.target.value))} />;
}

export default function Properties({ state, dispatch, notify }) {
  const { design, report, selection } = state;
  const design$ = (update, key) => dispatch({ type: 'DESIGN', update, key });

  /* ---------- element ---------- */
  if (selection.kind === 'element') {
    const found = findElement(design, selection.id);
    if (!found) return <div className="panel-body muted pad">Element nahi mila.</div>;
    const { element, bandKey } = found;
    const patch = (values, key) => design$((d) => {
      const band = getBand(d, bandKey);
      return setBand(d, bandKey, { ...band, elements: band.elements.map((el) => (el.id === element.id ? { ...el, ...values } : el)) });
    }, key || `el-${element.id}-${Object.keys(values).join()}`);
    const remove = () => {
      design$((d) => { const band = getBand(d, bandKey); return setBand(d, bandKey, { ...band, elements: band.elements.filter((el) => el.id !== element.id) }); });
      dispatch({ type: 'SELECT', selection: { kind: 'band', key: bandKey } });
    };
    const text = ['staticText', 'textField'].includes(element.type);
    const upload = (file) => {
      if (!file) return;
      if (file.size > 1_400_000) { notify('Image 1.4 MB se chhoti honi chahiye', 'error'); return; }
      const reader = new FileReader();
      reader.onload = () => patch({ src: String(reader.result) });
      reader.readAsDataURL(file);
    };
    const fieldNames = [...report.columns, ...design.calculatedFields.map((c) => c.name)];
    return (
      <div className="panel-body props">
        <div className="prop-title">{element.type} <small>in {bandLabel(bandKey)}</small></div>
        <div className="prop-grid">
          <Row label="X"><Num value={element.x} onChange={(x) => patch({ x })} /></Row>
          <Row label="Y"><Num value={element.y} onChange={(y) => patch({ y })} /></Row>
          <Row label="Width"><Num value={element.width} min={1} onChange={(width) => patch({ width })} /></Row>
          <Row label="Height"><Num value={element.height} min={0} onChange={(height) => patch({ height })} /></Row>
        </div>
        {element.type === 'staticText' && <Row label="Text"><textarea rows={3} value={element.text} onChange={(e) => patch({ text: e.target.value })} /></Row>}
        {element.type === 'textField' && (
          <>
            <Row label="Expression"><textarea rows={2} value={element.expression} placeholder="$F{Name}  ya  =$F{A} * 2" onChange={(e) => patch({ expression: e.target.value })} /></Row>
            <Row label="Pattern"><input value={element.pattern} placeholder="#,##0.00  |  dd/MM/yyyy" onChange={(e) => patch({ pattern: e.target.value })} /></Row>
            <Row label="Blank when null"><input type="checkbox" checked={element.blankWhenNull} onChange={(e) => patch({ blankWhenNull: e.target.checked })} /></Row>
          </>
        )}
        {text && (
          <>
            <div className="prop-grid">
              <Row label="Font size"><Num value={element.fontSize} min={4} max={96} onChange={(fontSize) => patch({ fontSize })} /></Row>
              <Row label="Align">
                <select value={element.align} onChange={(e) => patch({ align: e.target.value })}><option>left</option><option>center</option><option>right</option></select>
              </Row>
            </div>
            <Row label="Vertical align"><select value={element.vAlign || 'top'} onChange={(e) => patch({ vAlign: e.target.value })}><option>top</option><option>middle</option><option>bottom</option></select></Row>
            <div className="prop-grid">
              <Row label="Bold"><input type="checkbox" checked={element.bold} onChange={(e) => patch({ bold: e.target.checked })} /></Row>
              <Row label="Italic"><input type="checkbox" checked={element.italic} onChange={(e) => patch({ italic: e.target.checked })} /></Row>
            </div>
            <Row label="Text color"><input type="color" value={element.color} onChange={(e) => patch({ color: e.target.value })} /></Row>
          </>
        )}
        {element.type !== 'line' && element.type !== 'image' && (
          <div className="prop-grid">
            <Row label="Fill"><span className="inline">
              <input type="color" value={element.backColor || '#ffffff'} onChange={(e) => patch({ backColor: e.target.value })} />
              <button className="btn sm" onClick={() => patch({ backColor: null })}>none</button></span></Row>
            <Row label="Border"><Num value={element.borderWidth} min={0} max={20} onChange={(borderWidth) => patch({ borderWidth })} /></Row>
          </div>
        )}
        {(element.type === 'line' || element.borderWidth > 0) && <Row label="Line / border color"><input type="color" value={element.borderColor || '#000000'} onChange={(e) => patch({ borderColor: e.target.value })} /></Row>}
        {element.type === 'image' && (
          <Row label="Image (PNG/JPEG)"><input type="file" accept="image/png,image/jpeg" onChange={(e) => upload(e.target.files[0])} /></Row>
        )}
        {element.type === 'chart' && (
          <>
            <Row label="Type"><select value={element.chartType} onChange={(e) => patch({ chartType: e.target.value })}><option value="bar">bar</option><option value="line">line</option><option value="pie">pie</option></select></Row>
            <Row label="Category field"><select value={element.categoryField} onChange={(e) => patch({ categoryField: e.target.value })}><option value="">—</option>{fieldNames.map((f) => <option key={f}>{f}</option>)}</select></Row>
            <Row label="Value field"><select value={element.valueField} onChange={(e) => patch({ valueField: e.target.value })}><option value="">—</option>{fieldNames.map((f) => <option key={f}>{f}</option>)}</select></Row>
            <Row label="Aggregate"><select value={element.aggregate} onChange={(e) => patch({ aggregate: e.target.value })}><option>Sum</option><option>Count</option><option>Average</option></select></Row>
            <Row label="Title"><input value={element.title} onChange={(e) => patch({ title: e.target.value })} /></Row>
            <Row label="Series color"><input type="color" value={element.color === '#000000' ? '#2563eb' : element.color} onChange={(e) => patch({ color: e.target.value })} /></Row>
          </>
        )}
        <button className="btn danger" onClick={remove}>Delete element</button>
      </div>
    );
  }

  /* ---------- band ---------- */
  if (selection.kind === 'band') {
    const band = getBand(design, selection.key);
    return (
      <div className="panel-body props">
        <div className="prop-title">{bandLabel(selection.key)}</div>
        <Row label="Height (px)"><Num value={band.height} min={0} max={1500} onChange={(height) => design$((d) => setBand(d, selection.key, { ...getBand(d, selection.key), height }), `band-${selection.key}`)} /></Row>
        <p className="hint">{band.elements.length} element(s). Band ko neeche se kheench kar bhi resize kar sakte hain.</p>
      </div>
    );
  }

  /* ---------- group ---------- */
  if (selection.kind === 'group') {
    const group = design.groups.find((g) => g.name === selection.name);
    if (!group) return null;
    const rename = (name) => name && design$((d) => d); // names are fixed once created (bands are keyed by name)
    void rename;
    return (
      <div className="panel-body props">
        <div className="prop-title">Group · {group.name}</div>
        <Row label="Group by field"><select value={group.field} onChange={(e) => design$((d) => ({ ...d, groups: d.groups.map((g) => (g.name === group.name ? { ...g, field: e.target.value } : g)) }))}>{report.columns.map((c) => <option key={c}>{c}</option>)}{design.calculatedFields.map((c) => <option key={c.name}>{c.name}</option>)}</select></Row>
        <p className="hint">Rows is field ke hisaab se sort aur group hoti hain. Group Header/Footer bands Design tab mein aate hain.</p>
        <button className="btn danger" onClick={() => {
          design$((d) => {
            const headers = { ...d.bands.groupHeaders }; const footers = { ...d.bands.groupFooters };
            delete headers[group.name]; delete footers[group.name];
            return { ...d, groups: d.groups.filter((g) => g.name !== group.name), variables: d.variables.filter((v) => v.resetGroup !== group.name), bands: { ...d.bands, groupHeaders: headers, groupFooters: footers } };
          });
          dispatch({ type: 'SELECT', selection: { kind: 'report' } });
        }}>Delete group</button>
      </div>
    );
  }

  /* ---------- variable ---------- */
  if (selection.kind === 'variable') {
    const variable = design.variables.find((v) => v.name === selection.name);
    if (!variable) return null;
    const patch = (values) => design$((d) => ({ ...d, variables: d.variables.map((v) => (v.name === variable.name ? { ...v, ...values } : v)) }), `var-${variable.name}`);
    return (
      <div className="panel-body props">
        <div className="prop-title">Variable · {variable.name}</div>
        <Row label="Calculation"><select value={variable.calculation} onChange={(e) => patch({ calculation: e.target.value })}>{['Sum', 'Count', 'Average', 'Min', 'Max', 'First'].map((c) => <option key={c}>{c}</option>)}</select></Row>
        <Row label="Field"><select value={variable.field} onChange={(e) => patch({ field: e.target.value })}><option value="">— (Count only)</option>{[...report.columns, ...design.calculatedFields.map((c) => c.name)].map((c) => <option key={c}>{c}</option>)}</select></Row>
        <Row label="Reset on"><select value={variable.resetType === 'Group' ? `G:${variable.resetGroup}` : 'Report'} onChange={(e) => patch(e.target.value === 'Report' ? { resetType: 'Report', resetGroup: '' } : { resetType: 'Group', resetGroup: e.target.value.slice(2) })}><option value="Report">Report</option>{design.groups.map((g) => <option key={g.name} value={`G:${g.name}`}>Group {g.name}</option>)}</select></Row>
        <p className="hint">Use: <code>$V&#123;{variable.name}&#125;</code> textField mein. Group-reset variable ko Group Footer mein dikhayen.</p>
        <button className="btn danger" onClick={() => { design$((d) => ({ ...d, variables: d.variables.filter((v) => v.name !== variable.name) })); dispatch({ type: 'SELECT', selection: { kind: 'report' } }); }}>Delete variable</button>
      </div>
    );
  }

  /* ---------- calculated field ---------- */
  if (selection.kind === 'calc') {
    const calc = design.calculatedFields.find((c) => c.name === selection.name);
    if (!calc) return null;
    return (
      <div className="panel-body props">
        <div className="prop-title">Calculated field · {calc.name}</div>
        <Row label="Expression"><textarea rows={3} value={calc.expression} placeholder="$F{Amount} * 0.17" onChange={(e) => design$((d) => ({ ...d, calculatedFields: d.calculatedFields.map((c) => (c.name === calc.name ? { ...c, expression: e.target.value } : c)) }), `calc-${calc.name}`)} /></Row>
        <p className="hint">Sirf numbers aur $F tokens ke saath + − × ÷ % allowed hain. Koi code execute nahi hota.</p>
        <button className="btn danger" onClick={() => { design$((d) => ({ ...d, calculatedFields: d.calculatedFields.filter((c) => c.name !== calc.name) })); dispatch({ type: 'SELECT', selection: { kind: 'report' } }); }}>Delete</button>
      </div>
    );
  }

  /* ---------- parameter ---------- */
  if (selection.kind === 'param') {
    const param = report.parameters.find((p) => p.name === selection.name);
    if (!param) return null;
    const patch = (values) => dispatch({ type: 'REPORT', patch: { parameters: report.parameters.map((p) => (p.name === param.name ? { ...p, ...values } : p)) } });
    return (
      <div className="panel-body props">
        <div className="prop-title">Parameter · {param.name}</div>
        <Row label="Label"><input value={param.label || ''} onChange={(e) => patch({ label: e.target.value })} /></Row>
        <Row label="Type"><select value={param.type} onChange={(e) => patch({ type: e.target.value })}>{['date', 'string', 'integer', 'number', 'boolean'].map((t) => <option key={t}>{t}</option>)}</select></Row>
        <Row label="Required"><input type="checkbox" checked={Boolean(param.required)} onChange={(e) => patch({ required: e.target.checked })} /></Row>
        <p className="hint">URL key yahi naam hai. Query mein <code>$P&#123;{param.name}&#125;</code> use hota hai. Optional multi-value filter (comma se alag values, khali = sab) ke liye query mein <code>&#123;"$paramIn": "{param.name}"&#125;</code> likhein, misal: <code>&#123;"BranchCode": &#123;"$paramIn": "{param.name}"&#125;&#125;</code>.</p>
        <button className="btn danger" onClick={() => { dispatch({ type: 'REPORT', patch: { parameters: report.parameters.filter((p) => p.name !== param.name) } }); dispatch({ type: 'SELECT', selection: { kind: 'report' } }); }}>Delete parameter</button>
      </div>
    );
  }

  /* ---------- report / page ---------- */
  const { page } = design;
  const setPage = (values) => design$((d) => ({ ...d, page: { ...d.page, ...values } }), 'page');
  const setMargin = (side, value) => design$((d) => ({ ...d, page: { ...d.page, margins: { ...d.page.margins, [side]: value } } }), `margin-${side}`);
  return (
    <div className="panel-body props">
      <div className="prop-title">Report · page</div>
      <Row label="Name"><input value={report.title} onChange={(e) => dispatch({ type: 'REPORT', patch: { title: e.target.value } })} /></Row>
      <Row label="Page">
        <select value={page.width > page.height ? 'land' : 'port'} onChange={(e) => setPage(e.target.value === 'land' ? { width: 842, height: 595 } : { width: 595, height: 842 })}>
          <option value="port">A4 portrait (595×842)</option><option value="land">A4 landscape (842×595)</option>
        </select>
      </Row>
      <Row label="Ignore pagination (ek lambi page, Excel/CSV style)"><input type="checkbox" checked={Boolean(page.ignorePagination)} onChange={(e) => setPage({ ignorePagination: e.target.checked })} /></Row>
      <div className="prop-grid">
        {['top', 'right', 'bottom', 'left'].map((side) => <Row key={side} label={`Margin ${side}`}><Num value={page.margins[side]} min={0} max={300} onChange={(v) => setMargin(side, v)} /></Row>)}
      </div>
      <p className="hint">Koi element ya band chunein to uski properties yahan aayengi.</p>
    </div>
  );
}
