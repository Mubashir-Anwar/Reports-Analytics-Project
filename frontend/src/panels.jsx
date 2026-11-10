import { useEffect, useMemo, useState } from 'react';
import { api } from './api.js';
import { BUILTIN_VARIABLES, ELEMENT_LABELS, bandKeys, bandLabel, describeElement, getBand } from './designModel.js';

/* ---------------------------------------------------------------- Server (repository) */
export function ServerPanel({ report, onOpen, onNew, notify, refreshKey }) {
  const [folders, setFolders] = useState([]);
  const [reports, setReports] = useState([]);
  const [open, setOpen] = useState({});

  const load = async () => {
    try {
      const [f, r] = await Promise.all([api.folders(), api.reports()]);
      setFolders(f.folders);
      setReports(r.reports);
    } catch (error) {
      notify(`Server list load nahi hui: ${error.message}`, 'error');
    }
  };
  useEffect(() => { load(); }, [refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const allFolders = useMemo(() => {
    const set = new Set(folders);
    reports.forEach((item) => set.add(item.folder || 'General'));
    return [...set].sort((a, b) => a.localeCompare(b));
  }, [folders, reports]);

  const createFolder = async () => {
    const path = window.prompt('Naye folder ka path (misal: Cascade/Reports)');
    if (!path) return;
    try { await api.createFolder(path); await load(); } catch (error) { notify(error.message, 'error'); }
  };
  const remove = async (item) => {
    if (!window.confirm(`Report '${item.title}' delete karni hai?`)) return;
    try { await api.deleteReport(item.id); await load(); notify('Report delete ho gayi', 'ok'); } catch (error) { notify(error.message, 'error'); }
  };

  return (
    <div className="panel-body">
      <div className="panel-actions">
        <button className="btn primary sm" onClick={onNew}>+ New report</button>
        <button className="btn sm" onClick={createFolder}>+ Folder</button>
        <button className="btn sm" onClick={load} title="Refresh">↻</button>
      </div>
      <ul className="tree">
        {allFolders.map((folder) => {
          const items = reports.filter((item) => (item.folder || 'General') === folder);
          const depth = folder.split('/').length - 1;
          const isOpen = open[folder] !== false;
          return (
            <li key={folder} style={{ paddingLeft: depth * 12 }}>
              <div className="tree-row folder" onClick={() => setOpen({ ...open, [folder]: !isOpen })}>
                <span>{isOpen ? '▾' : '▸'}</span> 🗀 {folder.split('/').pop()} <small>({items.length})</small>
              </div>
              {isOpen && items.map((item) => (
                <div key={item.id} className={`tree-row report ${report.id === item.id && !report.isNew ? 'active' : ''}`} style={{ paddingLeft: 22 }} onDoubleClick={() => onOpen(item)} onClick={() => onOpen(item)}>
                  <span>▤ {item.title}</span>
                  <button className="icon" title="Delete" onClick={(e) => { e.stopPropagation(); remove(item); }}>✕</button>
                </div>
              ))}
            </li>
          );
        })}
        {!allFolders.length && <li className="muted pad">Koi report ya folder nahi. “+ New report” se shuru karein.</li>}
      </ul>
    </div>
  );
}

/* ---------------------------------------------------------------- Data adapters */
export function AdaptersPanel({ report, dispatch, notify }) {
  const [list, setList] = useState([]);
  const [form, setForm] = useState({ name: '', uri: '', database: '' });
  const [status, setStatus] = useState({});
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try { setList((await api.connections()).connections); } catch (error) { notify(`Data adapters load nahi hue: ${error.message}`, 'error'); }
  };
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const test = async (id) => {
    setStatus((s) => ({ ...s, [id]: 'Testing…' }));
    try { const r = await api.testConnection(id); setStatus((s) => ({ ...s, [id]: `✓ ${r.message || 'connected'}` })); }
    catch (error) { setStatus((s) => ({ ...s, [id]: `✕ ${error.message}` })); }
  };
  const save = async (testOnly) => {
    setBusy(true);
    try {
      const id = form.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 39);
      if (testOnly) {
        const r = await api.testConnectionValues({ id, uri: form.uri, database: form.database });
        notify(`Connection theek hai: ${r.message || 'ok'}`, 'ok');
      } else {
        await api.saveConnection({ id, name: form.name, uri: form.uri, database: form.database });
        setForm({ name: '', uri: '', database: '' });
        notify('Data adapter save ho gaya', 'ok');
        await load();
      }
    } catch (error) { notify(error.message, 'error'); } finally { setBusy(false); }
  };

  return (
    <div className="panel-body">
      <p className="hint">Har adapter ek MongoDB connection hai. Remote Mongo ke liye pehle VPN on karein. URI password ke saath private file mein save hoti hai, wapas nahi dikhai jati.</p>
      <ul className="tree">
        {list.map((item) => (
          <li key={item.id} className="adapter">
            <label className="tree-row">
              <input type="radio" name="adapter" checked={report.connectionId === item.id} onChange={() => dispatch({ type: 'REPORT', patch: { connectionId: item.id } })} />
              <span><b>{item.name}</b><br /><small>{item.database || 'database set nahi'} · {item.configured ? 'configured' : 'NOT configured'}</small></span>
            </label>
            <div className="adapter-actions">
              <button className="btn sm" onClick={() => test(item.id)}>Test</button>
              <small className={status[item.id]?.startsWith('✓') ? 'ok' : 'err'}>{status[item.id]}</small>
            </div>
          </li>
        ))}
      </ul>
      <details className="adapter-form">
        <summary>+ New data adapter</summary>
        <label>Name<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Branch reporting" /></label>
        <label>MongoDB URL<input type="password" autoComplete="new-password" value={form.uri} onChange={(e) => setForm({ ...form, uri: e.target.value })} placeholder="mongodb://user:pass@host:27017/db" /></label>
        <label>Database <small>(optional)</small><input value={form.database} onChange={(e) => setForm({ ...form, database: e.target.value })} /></label>
        <div className="row">
          <button className="btn sm" disabled={busy || !form.uri} onClick={() => save(true)}>Test connection</button>
          <button className="btn primary sm" disabled={busy || !form.uri || !form.name} onClick={() => save(false)}>Save adapter</button>
        </div>
      </details>
    </div>
  );
}

/* ---------------------------------------------------------------- Outline */
function Section({ title, count, actions, children, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <li>
      <div className="tree-row section" onClick={() => setOpen(!open)}>
        <span>{open ? '▾' : '▸'} {title} {count !== undefined && <small>({count})</small>}</span>
        <span onClick={(e) => e.stopPropagation()}>{actions}</span>
      </div>
      {open && <ul className="tree nested">{children}</ul>}
    </li>
  );
}

export function Outline({ report, design, selection, dispatch }) {
  const select = (s) => dispatch({ type: 'SELECT', selection: s });
  const is = (kind, key, value) => selection.kind === kind && selection[key] === value;
  const addGroup = () => {
    const field = report.columns[0];
    if (!field) return;
    const name = `Group${design.groups.length + 1}`;
    dispatch({
      type: 'DESIGN',
      update: (d) => ({ ...d, groups: [...d.groups, { name, field }], bands: { ...d.bands, groupHeaders: { ...d.bands.groupHeaders, [name]: { height: 0, elements: [] } }, groupFooters: { ...d.bands.groupFooters, [name]: { height: 0, elements: [] } } } }),
    });
    select({ kind: 'group', name });
  };
  const addVariable = () => {
    const name = `Variable${design.variables.length + 1}`;
    dispatch({ type: 'DESIGN', update: (d) => ({ ...d, variables: [...d.variables, { name, calculation: 'Sum', field: report.columns[0] || '', resetType: 'Report', resetGroup: '' }] }) });
    select({ kind: 'variable', name });
  };
  const addCalc = () => {
    const name = `Calc${design.calculatedFields.length + 1}`;
    dispatch({ type: 'DESIGN', update: (d) => ({ ...d, calculatedFields: [...d.calculatedFields, { name, expression: '' }] }) });
    select({ kind: 'calc', name });
  };
  const addParam = () => {
    const name = `Param${report.parameters.length + 1}`;
    dispatch({ type: 'REPORT', patch: { parameters: [...report.parameters, { name, type: 'string', label: name, required: false }] } });
    select({ kind: 'param', name });
  };

  return (
    <div className="panel-body">
      <ul className="tree">
        <li>
          <div className={`tree-row root ${selection.kind === 'report' ? 'active' : ''}`} onClick={() => select({ kind: 'report' })}>▤ <b>{report.title || 'Untitled'}</b></div>
          <ul className="tree nested">
            <Section title="Parameters" count={report.parameters.length} actions={<button className="icon" onClick={addParam} title="Add parameter">＋</button>}>
              {report.parameters.map((p) => (
                <li key={p.name} className={`tree-row leaf ${is('param', 'name', p.name) ? 'active' : ''}`} onClick={() => select({ kind: 'param', name: p.name })}>$P&#123;{p.name}&#125; <small>{p.type}</small></li>
              ))}
            </Section>
            <Section title="Fields" count={report.columns.length}>
              {report.columns.map((c) => (
                <li key={c} className="tree-row leaf draggable" draggable onDragStart={(e) => e.dataTransfer.setData('application/x-field', c)} title="Band par drag karein">$F&#123;{c}&#125;</li>
              ))}
              {!report.columns.length && <li className="muted pad">Setup tab mein “Detect fields” chalayen.</li>}
            </Section>
            <Section title="Calculated Fields" count={design.calculatedFields.length} actions={<button className="icon" onClick={addCalc} title="Add">＋</button>}>
              {design.calculatedFields.map((c) => (
                <li key={c.name} className={`tree-row leaf draggable ${is('calc', 'name', c.name) ? 'active' : ''}`} draggable onDragStart={(e) => e.dataTransfer.setData('application/x-field', c.name)} onClick={() => select({ kind: 'calc', name: c.name })}>ƒ $F&#123;{c.name}&#125;</li>
              ))}
            </Section>
            <Section title="Variables" count={design.variables.length + BUILTIN_VARIABLES.length} actions={<button className="icon" onClick={addVariable} title="Add">＋</button>}>
              {design.variables.map((v) => (
                <li key={v.name} className={`tree-row leaf ${is('variable', 'name', v.name) ? 'active' : ''}`} onClick={() => select({ kind: 'variable', name: v.name })}>$V&#123;{v.name}&#125; <small>{v.calculation}</small></li>
              ))}
              {BUILTIN_VARIABLES.map((v) => <li key={v} className="tree-row leaf builtin">$V&#123;{v}&#125;</li>)}
            </Section>
            <Section title="Groups" count={design.groups.length} actions={<button className="icon" onClick={addGroup} title="Add group">＋</button>}>
              {design.groups.map((g) => (
                <li key={g.name} className={`tree-row leaf ${is('group', 'name', g.name) ? 'active' : ''}`} onClick={() => select({ kind: 'group', name: g.name })}>⊞ {g.name} <small>by {g.field}</small></li>
              ))}
            </Section>
            <Section title="Bands">
              {bandKeys(design).map((key) => {
                const band = getBand(design, key);
                return (
                  <li key={key}>
                    <div className={`tree-row ${is('band', 'key', key) ? 'active' : ''}`} onClick={() => select({ kind: 'band', key })}>▭ {bandLabel(key)} <small>{band.height}px</small></div>
                    <ul className="tree nested">
                      {band.elements.map((el) => (
                        <li key={el.id} className={`tree-row leaf ${is('element', 'id', el.id) ? 'active' : ''}`} onClick={() => select({ kind: 'element', id: el.id })}>
                          <span className="chip">{ELEMENT_LABELS[el.type]}</span> {describeElement(el).slice(0, 28)}
                        </li>
                      ))}
                    </ul>
                  </li>
                );
              })}
            </Section>
          </ul>
        </li>
      </ul>
    </div>
  );
}

/* ---------------------------------------------------------------- Palette */
const PALETTE = [
  ['staticText', 'T', 'Static Text'],
  ['textField', '𝑓', 'Text Field'],
  ['line', '─', 'Line'],
  ['rectangle', '▭', 'Rectangle'],
  ['image', '🖼', 'Image'],
  ['chart', '▦', 'Chart'],
];

export function Palette({ tool, dispatch }) {
  return (
    <div className="panel-body">
      <p className="hint">Element chunein aur band par click karein, ya seedha band par drag karein.</p>
      <div className="palette">
        {PALETTE.map(([type, icon, label]) => (
          <button
            key={type}
            className={`palette-item ${tool === type ? 'armed' : ''}`}
            draggable
            onDragStart={(e) => e.dataTransfer.setData('application/x-palette', type)}
            onClick={() => dispatch({ type: 'TOOL', tool: tool === type ? null : type })}
          >
            <span className="p-icon">{icon}</span>{label}
          </button>
        ))}
      </div>
    </div>
  );
}
