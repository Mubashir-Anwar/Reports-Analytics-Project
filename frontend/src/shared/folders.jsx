// Folders for every page: a collapsible tree, breadcrumbs, subfolder tiles and the "New folder" dialog.
// Paths look like "cascade/CAR/Letters" ('' = the root).
import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import './folders.css';

const NAME = /^[A-Za-z0-9 _.()&-]+$/;

/** Is this a valid single folder name? ('' when fine, else the reason) */
export function folderNameProblem(name, siblings = []) {
  const value = name.trim();
  if (!value) return 'Give the folder a name';
  if (value.includes('/') || value.includes('\\')) return 'A name cannot contain / or \\ (choose the parent folder above instead)';
  if (!NAME.test(value)) return 'Use letters, digits, spaces and _ . - ( ) &';
  if (value.length > 60) return 'At most 60 characters';
  if (value === '.' || value === '..') return 'Not a valid name';
  if (siblings.some((item) => item.toLowerCase() === value.toLowerCase())) return 'A folder with this name is already there';
  if (value.toLowerCase() === 'general') return 'General is the top level (reports without a folder); choose another name';
  return '';
}

export function FolderIcon({ open = false, size = 16, tone = 'default' }) {
  return (
    <svg className={`fd-icon ${tone}`} width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      {open
        ? <path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2h7.5A2.5 2.5 0 0 1 21 9.5V10H7.2a2 2 0 0 0-1.9 1.4L3 18.5z M5.3 11.4A2 2 0 0 1 7.2 10H22l-2.6 8.1A2 2 0 0 1 17.5 19.5H3z" fill="currentColor" />
        : <path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2h7.5A2.5 2.5 0 0 1 21 9.5v8A2.5 2.5 0 0 1 18.5 20h-13A2.5 2.5 0 0 1 3 17.5z" fill="currentColor" />}
    </svg>
  );
}

const childrenOf = (folders, parent) => folders.filter((path) => (parent ? path.startsWith(`${parent}/`) && !path.slice(parent.length + 1).includes('/') : !path.includes('/')))
  .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base', numeric: true }));

/** Every folder with its parents (the server may list "a/b" without "a"). */
export function withParents(folders) {
  const all = new Set();
  folders.forEach((path) => { const parts = path.split('/'); parts.forEach((_, index) => all.add(parts.slice(0, index + 1).join('/'))); });
  return [...all];
}

function readOpen(key) {
  try { return JSON.parse(localStorage.getItem(key) || '{}') || {}; } catch { return {}; }
}

/**
 * folders: paths; selected: path ('' = root); count(path) → number shown on the right (optional);
 * onSelect(path); onMenu(event, path) for right-click and the ⋯ button; storageKey keeps what is open.
 */
export function FolderTree({ folders, selected, onSelect, count, onMenu, rootLabel = 'All reports', rootCount, storageKey = 'fd-open', filter = '' }) {
  const all = useMemo(() => withParents(folders), [folders]);
  const [open, setOpen] = useState(() => readOpen(storageKey));
  useEffect(() => { try { localStorage.setItem(storageKey, JSON.stringify(open)); } catch { /* private mode */ } }, [open, storageKey]);
  useEffect(() => {            // the selected folder and its parents open, so its subfolders show
    if (!selected) return;
    const parts = selected.split('/');
    const need = parts.map((_, index) => parts.slice(0, index + 1).join('/')).filter((path) => !open[path]);
    if (need.length) setOpen((current) => ({ ...current, ...Object.fromEntries(need.map((path) => [path, true])) }));
  }, [selected]); // eslint-disable-line react-hooks/exhaustive-deps
  const needle = filter.trim().toLowerCase();
  const matches = (path) => !needle || path.toLowerCase().includes(needle);
  const visible = (path) => matches(path) || all.some((other) => other.startsWith(`${path}/`) && matches(other));
  const toggle = (path) => setOpen((current) => ({ ...current, [path]: !current[path] }));

  const node = (path, depth) => {
    const kids = childrenOf(all, path).filter(visible);
    const isOpen = needle ? true : Boolean(open[path]);
    const on = selected === path;
    return (
      <li key={path} role="treeitem" aria-expanded={kids.length ? isOpen : undefined} aria-selected={on}>
        <div className={`fd-row ${on ? 'on' : ''}`} style={{ '--depth': depth }} onContextMenu={onMenu ? (event) => onMenu(event, path) : undefined}>
          <button type="button" className={`fd-caret ${kids.length ? '' : 'none'}`} tabIndex={-1} aria-label={isOpen ? 'Close' : 'Open'} onClick={() => kids.length && toggle(path)}>{kids.length ? (isOpen ? '▾' : '▸') : ''}</button>
          <button type="button" className="fd-label" title={path} onClick={() => onSelect(path)} onDoubleClick={() => kids.length && toggle(path)}
            onKeyDown={(event) => { if (event.key === 'ArrowRight' && kids.length && !isOpen) toggle(path); if (event.key === 'ArrowLeft' && isOpen) toggle(path); }}>
            <FolderIcon open={on || isOpen} />
            <span className="fd-name">{path.split('/').pop()}</span>
          </button>
          {count && <span className="fd-count">{count(path)}</span>}
          {onMenu && <button type="button" className="fd-more" aria-label={`Actions for ${path}`} onClick={(event) => onMenu(event, path)}>⋯</button>}
        </div>
        {kids.length > 0 && isOpen && <ul role="group">{kids.map((kid) => node(kid, depth + 1))}</ul>}
      </li>
    );
  };
  return (
    <ul className="fd-tree" role="tree">
      <li role="treeitem" aria-selected={selected === ''}>
        <div className={`fd-row root ${selected === '' ? 'on' : ''}`} style={{ '--depth': 0 }} onContextMenu={onMenu ? (event) => onMenu(event, '') : undefined}>
          <span className="fd-caret none" />
          <button type="button" className="fd-label" onClick={() => onSelect('')}>
            <svg className="fd-icon root" width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v4H4zM4 10.5h16v4H4zM4 16h16v3H4z" fill="currentColor" /></svg>
            <span className="fd-name">{rootLabel}</span>
          </button>
          {rootCount !== undefined && <span className="fd-count">{rootCount}</span>}
          {onMenu && <button type="button" className="fd-more" aria-label="Actions" onClick={(event) => onMenu(event, '')}>⋯</button>}
        </div>
      </li>
      {childrenOf(all, '').filter(visible).map((path) => node(path, 0))}
      {needle && !all.some(matches) && <li className="fd-empty">No folder matches “{filter}”.</li>}
    </ul>
  );
}

export function Breadcrumbs({ path, onSelect, rootLabel = 'All reports' }) {
  const parts = path ? path.split('/') : [];
  return (
    <nav className="fd-crumbs" aria-label="Folder path">
      <button type="button" onClick={() => onSelect('')} className={parts.length ? '' : 'here'}>{rootLabel}</button>
      {parts.map((part, index) => {
        const target = parts.slice(0, index + 1).join('/');
        const last = index === parts.length - 1;
        return (
          <span key={target} className="fd-crumb">
            <span className="fd-sep" aria-hidden="true">›</span>
            <button type="button" onClick={() => onSelect(target)} className={last ? 'here' : ''} aria-current={last ? 'page' : undefined}>{part}</button>
          </span>
        );
      })}
    </nav>
  );
}

/** The folders directly inside `parent`, as tiles (click opens, right-click for actions). */
export function SubfolderTiles({ folders, parent, count, onOpen, onMenu, itemWord = 'report' }) {
  const kids = childrenOf(withParents(folders), parent);
  if (!kids.length) return null;
  return (
    <div className="fd-tiles">
      {kids.map((path) => (
        <button key={path} type="button" className="fd-tile" onClick={() => onOpen(path)} onContextMenu={onMenu ? (event) => onMenu(event, path) : undefined} title={path}>
          <FolderIcon size={26} tone="tile" />
          <span><b>{path.split('/').pop()}</b><small>{count ? `${count(path)} ${itemWord}${count(path) === 1 ? '' : 's'}` : 'Folder'}{childrenOf(folders, path).length ? ` · ${childrenOf(folders, path).length} folder${childrenOf(folders, path).length === 1 ? '' : 's'}` : ''}</small></span>
        </button>
      ))}
    </div>
  );
}

/**
 * The "New folder" dialog: choose where (any folder or the top level) and a name; shows the full path.
 * onCreate(path) may throw: the message is shown in the dialog.
 */
export function NewFolderDialog({ folders, parent = '', onCreate, onClose, title = 'New folder', rootLabel = 'Top level' }) {
  const all = useMemo(() => withParents(folders).sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base', numeric: true })), [folders]);
  const [where, setWhere] = useState(parent);
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const input = useRef(null);
  useEffect(() => { input.current?.focus(); }, []);
  useEffect(() => {
    const escape = (event) => { if (event.key === 'Escape') onClose(); };
    document.addEventListener('keydown', escape);
    return () => document.removeEventListener('keydown', escape);
  }, [onClose]);
  const siblings = childrenOf(all, where).map((path) => path.split('/').pop());
  const problem = name ? folderNameProblem(name, siblings) : '';
  const full = [where, name.trim()].filter(Boolean).join('/');
  const submit = async (event) => {
    event.preventDefault();
    const reason = folderNameProblem(name, siblings);
    if (reason) { setError(reason); return; }
    setBusy(true);
    try { await onCreate(full); onClose(); } catch (failure) { setError(failure.message); } finally { setBusy(false); }
  };
  return createPortal(   // on <body>: a panel that is moved or hidden must not move or hide the dialog
    <div className="fd-modal-back" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <form className="fd-modal" role="dialog" aria-modal="true" aria-labelledby="fd-title" onSubmit={submit}>
        <div className="fd-modal-head"><FolderIcon size={22} tone="tile" /><h2 id="fd-title">{title}</h2><button type="button" className="fd-x" aria-label="Close" onClick={onClose}>✕</button></div>
        <label className="fd-field"><span>Inside</span>
          <select value={where} onChange={(event) => { setWhere(event.target.value); setError(''); }}>
            <option value="">{rootLabel}</option>
            {all.map((path) => <option key={path} value={path}>{'  '.repeat(path.split('/').length - 1)}{path.split('/').pop()}</option>)}
          </select>
        </label>
        <label className="fd-field"><span>Name</span>
          <input ref={input} value={name} maxLength={60} placeholder="e.g. Letters" onChange={(event) => { setName(event.target.value); setError(''); }} aria-invalid={Boolean(problem || error)} />
        </label>
        <div className="fd-preview"><FolderIcon size={14} /> <code>/{full || '…'}</code></div>
        {(error || problem) && <p className="fd-error" role="alert">{error || problem}</p>}
        <div className="fd-modal-actions">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy || !name.trim() || Boolean(problem)}>{busy ? 'Creating…' : 'Create folder'}</button>
        </div>
      </form>
    </div>,
    document.body,
  );
}

/**
 * "Delete this folder?" — shows what is inside (subfolders, reports, dashboards, data adapters) and asks what to do
 * with it: move everything to another folder (the default) or delete it. Data adapters are never deleted with a
 * folder. onConfirm({ moveTo }) or onConfirm({ recursive: true }) may throw: the message is shown in the dialog.
 * place: 'server' | 'workspace' changes the words.
 */
export function FolderDeleteDialog({ path, contents, folders, onConfirm, onClose, place = 'server', rootLabel = 'Top level (no folder)' }) {
  const subfolders = contents?.subfolders || [];
  const reports = contents?.reports || [];
  const dashboards = contents?.dashboards || [];
  const adapters = contents?.adapters || [];
  const empty = !subfolders.length && !reports.length && !dashboards.length && !adapters.length;
  const targets = useMemo(() => withParents(folders).filter((f) => f !== path && !f.startsWith(`${path}/`))
    .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base', numeric: true })), [folders, path]);
  const parent = path.split('/').slice(0, -1).join('/');
  const [choice, setChoice] = useState('move');
  const [moveTo, setMoveTo] = useState(parent);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const escape = (event) => { if (event.key === 'Escape' && !busy) onClose(); };
    document.addEventListener('keydown', escape);
    return () => document.removeEventListener('keydown', escape);
  }, [onClose, busy]);
  const list = (items) => `${items.slice(0, 6).join(', ')}${items.length > 6 ? `, … (+${items.length - 6})` : ''}`;
  const reportWord = place === 'workspace' ? 'report draft' : 'published report';
  const go = async (answer) => {
    setBusy(true); setError('');
    try { await onConfirm(answer); onClose(); } catch (failure) { setError(failure.message); setBusy(false); }
  };
  return createPortal(
    <div className="fd-modal-back" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
      <div className="fd-modal fd-delete" role="dialog" aria-modal="true" aria-labelledby="fd-del-title">
        <div className="fd-modal-head"><FolderIcon size={22} tone="tile" /><h2 id="fd-del-title">Delete the folder “{path.split('/').pop()}”?</h2><button type="button" className="fd-x" aria-label="Close" onClick={onClose}>✕</button></div>
        <div className="fd-preview"><FolderIcon size={14} /> <code>/{path}</code></div>
        {empty ? <p className="fd-note">It is empty, so nothing else changes.</p> : (
          <>
            <p className="fd-note">Inside it:</p>
            <ul className="fd-inside">
              {subfolders.length > 0 && <li><b>{subfolders.length}</b> subfolder{subfolders.length === 1 ? '' : 's'} <small>{list(subfolders.map((f) => f.slice(path.length + 1)))}</small></li>}
              {reports.length > 0 && <li><b>{reports.length}</b> {reportWord}{reports.length === 1 ? '' : 's'} <small>{list(reports)}</small></li>}
              {dashboards.length > 0 && <li><b>{dashboards.length}</b> dashboard{dashboards.length === 1 ? '' : 's'} <small>{list(dashboards)}</small></li>}
              {adapters.length > 0 && <li><b>{adapters.length}</b> data adapter{adapters.length === 1 ? '' : 's'} <small>{list(adapters)}</small></li>}
            </ul>
            <div className="fd-choices" role="radiogroup" aria-label="What happens to what is inside">
              <label className={choice === 'move' ? 'on' : ''}>
                <input type="radio" name="fd-choice" checked={choice === 'move'} onChange={() => setChoice('move')} />
                <span><b>Move everything to another folder</b> (recommended)
                  <select value={moveTo} disabled={choice !== 'move'} onChange={(event) => setMoveTo(event.target.value)} aria-label="Move to">
                    <option value="">{rootLabel}</option>
                    {targets.map((f) => <option key={f} value={f}>{'  '.repeat(f.split('/').length - 1)}{f.split('/').pop()}</option>)}
                  </select>
                  <small>Subfolders keep their names inside the new place.</small>
                </span>
              </label>
              <label className={`danger ${choice === 'delete' ? 'on' : ''}`}>
                <input type="radio" name="fd-choice" checked={choice === 'delete'} onChange={() => setChoice('delete')} />
                <span><b>Delete everything inside</b>
                  <small>{place === 'workspace'
                    ? 'The drafts are deleted. Copies already published stay on the server.'
                    : `The ${reports.length || dashboards.length ? 'reports and dashboards are' : 'folder is'} deleted for everyone and cannot be brought back.${adapters.length ? ' Data adapters are never deleted with a folder: they go to the top level.' : ''}`}</small>
                </span>
              </label>
            </div>
          </>
        )}
        {error && <p className="fd-error" role="alert">{error}</p>}
        <div className="fd-modal-actions">
          <button type="button" className="btn" disabled={busy} onClick={onClose}>No, keep it</button>
          {empty || choice === 'move'
            ? <button type="button" className="btn primary" disabled={busy} onClick={() => go(empty ? { recursive: false } : { moveTo })}>{busy ? 'Working…' : empty ? 'Yes, delete it' : `Yes, move to ${moveTo ? `/${moveTo}` : 'the top level'} and delete`}</button>
            : <button type="button" className="btn danger" disabled={busy} onClick={() => go({ recursive: true })}>{busy ? 'Deleting…' : 'Yes, delete it all'}</button>}
        </div>
      </div>
    </div>,
    document.body,
  );
}

/**
 * Choose a folder: a button that opens the tree right under it (with a search). With allowNew a folder that is not
 * there yet can be typed: it is made when the form is saved. '' = the top level.
 * manage = { create(path), rename(path, newPath), remove(path) } adds a bar under the tree for the chosen folder (or the
 * one whose ⋯ was clicked): New folder / Rename / Delete. Each may throw: the message is shown in the bar.
 */
export function FolderPicker({ folders, value, onChange, rootLabel = 'Top level', rootHint = 'not in a folder', allowNew = false, disabled = false, label = 'Folder', storageKey = 'fd-pick-open', manage = null }) {
  const [open, setOpen] = useState(false);
  const [find, setFind] = useState('');
  // the folder the manage bar works on, and what it is doing: { path, mode: 'menu' | 'new' | 'rename', name, error, busy }
  const [act, setAct] = useState(null);
  const box = useRef(null);
  const button = useRef(null);
  const all = useMemo(() => withParents(folders), [folders]);
  useEffect(() => {
    if (!open) return undefined;
    const outside = (event) => { if (!box.current?.contains(event.target)) setOpen(false); };
    document.addEventListener('pointerdown', outside);
    box.current?.querySelector('.fd-pick-panel')?.scrollIntoView({ block: 'nearest' });
    return () => document.removeEventListener('pointerdown', outside);
  }, [open]);
  const close = () => { setOpen(false); setFind(''); setAct(null); button.current?.focus(); };
  const target = act ? act.path : (all.includes(value) ? value : '');
  const editing = act && act.mode !== 'menu';
  const onMenu = manage ? (event, path) => { event.preventDefault(); event.stopPropagation(); setAct({ path, mode: 'menu' }); } : undefined;
  const startEdit = (mode) => setAct({ path: target, mode, name: mode === 'rename' ? target.split('/').pop() : '', error: '' });
  const saveEdit = async () => {
    const parent = act.mode === 'new' ? act.path : act.path.split('/').slice(0, -1).join('/');
    const siblings = childrenOf(all, parent).filter((path) => path !== act.path).map((path) => path.split('/').pop());
    const reason = folderNameProblem(act.name || '', siblings);
    if (reason) { setAct({ ...act, error: reason }); return; }
    const next = [parent, act.name.trim()].filter(Boolean).join('/');
    setAct({ ...act, busy: true, error: '' });
    try {
      if (act.mode === 'new') await manage.create(next); else await manage.rename(act.path, next);
      setAct({ path: next, mode: 'menu' });
    } catch (failure) { setAct({ ...act, busy: false, error: failure.message }); }
  };
  const pick = (path) => { onChange(path); close(); };
  const typed = find.trim().split('/').map((part) => part.trim()).filter(Boolean).join('/');
  const known = all.find((path) => path.toLowerCase() === typed.toLowerCase());
  const problem = typed && !known ? typed.split('/').map((part) => folderNameProblem(part)).find(Boolean) || '' : '';
  const parts = value ? value.split('/') : [];
  const isNew = allowNew && Boolean(value) && !all.includes(value);
  return (
    <div className={`fd-pick ${open ? 'open' : ''}`} ref={box} onKeyDown={(event) => {
      if (event.key !== 'Escape' || !open) return;
      event.stopPropagation();
      if (editing) setAct({ path: act.path, mode: 'menu' }); else close();
    }}>
      <button type="button" className="fd-pick-button" ref={button} disabled={disabled} aria-haspopup="tree" aria-expanded={open} aria-label={`${label}: ${value || rootLabel}`}
        onClick={() => { setOpen(!open); setFind(''); }}>
        <FolderIcon open={open} size={22} tone={value ? 'tile' : 'root'} />
        <span><b>{parts.length ? parts[parts.length - 1] : rootLabel}</b><small>{value ? `/${value}` : rootHint}</small></span>
        {isNew && <em>new</em>}
        <i aria-hidden="true">▾</i>
      </button>
      {open && (
        <div className="fd-pick-panel">
          <input autoFocus value={find} onChange={(event) => setFind(event.target.value)} aria-label="Find a folder" placeholder={allowNew ? 'Find a folder, or type a new one' : 'Find a folder'}
            onKeyDown={(event) => {
              if (event.key !== 'Enter') return;
              event.preventDefault();
              if (known) pick(known); else if (allowNew && typed && !problem) pick(typed);
            }} />
          <div className="fd-pick-list"><FolderTree folders={folders} selected={act ? act.path : value} onSelect={pick} storageKey={storageKey} filter={find} rootLabel={rootLabel} onMenu={onMenu} /></div>
          {allowNew && typed && !known && (problem
            ? <p className="fd-error">{problem}</p>
            : <button type="button" className="fd-pick-new" onClick={() => pick(typed)}>＋ New folder <b>/{typed}</b></button>)}
          {manage && (editing ? (
            <div className="fd-pick-manage editing">
              <span className="fd-pick-on">{act.mode === 'new' ? <>New folder in <b>{act.path ? `/${act.path}` : rootLabel}</b></> : <>Rename <b>/{act.path}</b></>}</span>
              <div className="fd-pick-edit">
                <input autoFocus value={act.name} maxLength={60} aria-label="Folder name" placeholder="Folder name" aria-invalid={Boolean(act.error)}
                  onChange={(event) => setAct({ ...act, name: event.target.value, error: '' })}
                  onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); saveEdit(); } }} />
                <button type="button" className="btn primary sm" disabled={act.busy || !act.name.trim()} onClick={saveEdit}>{act.busy ? 'Saving…' : act.mode === 'new' ? 'Create' : 'Save'}</button>
                <button type="button" className="btn sm" disabled={act.busy} onClick={() => setAct({ path: act.path, mode: 'menu' })}>Cancel</button>
              </div>
              {act.error && <p className="fd-error" role="alert">{act.error}</p>}
            </div>
          ) : (
            <div className="fd-pick-manage">
              <span className="fd-pick-on" title={target ? `/${target}` : rootLabel}><FolderIcon size={14} /> {target ? `/${target}` : rootLabel}</span>
              <button type="button" className="btn sm" onClick={() => startEdit('new')}>＋ {target ? 'Subfolder' : 'New folder'}</button>
              {target && <button type="button" className="btn sm" onClick={() => startEdit('rename')}>Rename</button>}
              {target && <button type="button" className="btn sm danger-text" onClick={() => { const path = target; close(); manage.remove(path); }}>Delete</button>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
