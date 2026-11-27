// The Analytics ribbon (like the Designer's): tabs Home, Insert, Query, Data, ETL & Python and View with grouped buttons.
// Buttons call the page's own actions; a dashboard and the query editor listen for window events
// ('bi-command' and 'bi-query'), so nothing here has its own logic.
import { useEffect, useRef, useState } from 'react';
import { RIcon } from '../Ribbon.jsx';
import { TypeIcon, VISUAL_TYPES } from './VisualEditor.jsx';
import '../ribbon.css';

const KEY = 'analytics-ribbon';
const send = (name, detail) => window.dispatchEvent(new CustomEvent(name, { detail }));

const Big = ({ icon, label, onClick, disabled, primary, title, children }) => (
  <button type="button" className={`rb-big ${primary ? 'primary' : ''}`} title={title || label} onClick={onClick} disabled={disabled}>
    {children || <RIcon name={icon} size={24} />}<span>{label}</span>
  </button>
);
const Small = ({ icon, label, onClick, disabled, title, children }) => (
  <button type="button" className="rb-sm" title={title || label} onClick={onClick} disabled={disabled}>
    {children || <RIcon name={icon} size={16} />}<span>{label}</span>
  </button>
);
const Group = ({ label, children }) => (
  <div className="rb-group" role="group" aria-label={label}>
    <div className="rb-items">{children}</div>
    <div className="rb-label">{label}</div>
  </div>
);
const Stack = ({ children }) => <div className="rb-stack">{children}</div>;

export default function AnalyticsRibbon({ info, view, go, onNewDashboard, onImport, onRefreshAll, onReload, readyCount, dashName, onDownload, datasets = [] }) {
  // the File menu on the Analytics button (like the Designer's): new, open, save, download, publish
  const [menu, setMenu] = useState(false);
  const menuBox = useRef(null);
  useEffect(() => {
    if (!menu) return undefined;
    const close = (event) => { if (!menuBox.current?.contains(event.target)) setMenu(false); };
    const esc = (event) => { if (event.key === 'Escape') setMenu(false); };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', esc); };
  }, [menu]);
  useEffect(() => {
    const save = (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's' && view.kind === 'dash') { event.preventDefault(); send('bi-command', { cmd: 'save' }); }
    };
    window.addEventListener('keydown', save);
    return () => window.removeEventListener('keydown', save);
  }, [view.kind]);
  const [ui, setUi] = useState(() => {
    try { return { tab: 'home', collapsed: false, ...JSON.parse(localStorage.getItem(KEY) || '{}') }; } catch { return { tab: 'home', collapsed: false }; }
  });
  const [dash, setDash] = useState({ open: false, editing: false, mayChange: false });
  const [queryLive, setQueryLive] = useState(false);
  useEffect(() => {
    const follow = (event) => setQueryLive(Boolean(event.detail?.live));
    window.addEventListener('bi-query-state', follow);
    return () => window.removeEventListener('bi-query-state', follow);
  }, []);
  useEffect(() => { try { localStorage.setItem(KEY, JSON.stringify(ui)); } catch { /* private mode */ } }, [ui]);
  useEffect(() => {
    const follow = (event) => setDash({ open: false, editing: false, mayChange: false, ...(event.detail || {}) });
    window.addEventListener('bi-dashboard-state', follow);
    return () => window.removeEventListener('bi-dashboard-state', follow);
  }, []);
  // opening a query shows the Query tab, a dashboard being edited the Insert tab
  useEffect(() => {
    if (view.kind === 'q') setUi((u) => ({ ...u, tab: 'query' }));
    if (view.kind === 'etl' || view.kind === 'py') setUi((u) => ({ ...u, tab: 'etl' }));
  }, [view.kind, view.id]);
  useEffect(() => { if (dash.editing) setUi((u) => ({ ...u, tab: u.tab === 'query' ? 'insert' : u.tab })); }, [dash.editing]);

  const model = info.mayModel;
  const onDash = view.kind === 'dash' && dash.open;
  const onQuery = view.kind === 'q';
  const tabs = [['home', 'Home', 'b'], ['insert', 'Insert', 'g'], ['query', 'Query', 'v'], ['data', 'Data', 'c'], ['etl', 'ETL & Python', 'v'], ['view', 'View', 'a']];
  const pipelines = datasets.filter((d) => d.sourceKind === 'pipeline').slice(0, 4);
  const scripts = datasets.filter((d) => d.sourceKind === 'python').slice(0, 4);
  const noPipelines = info.mayPipelines ? undefined : 'Needs the Pipelines (ETL) right (ask an administrator)';
  const noPython = info.mayPython ? undefined : 'Needs the Python scripts right (ask an administrator)';
  const pick = (tab) => setUi((u) => ({ tab, collapsed: u.tab === tab ? !u.collapsed : false }));

  const body = {
    home: (
      <>
        <Group label="New">
          <Big icon="newDoc" label="Dashboard" onClick={onNewDashboard} title="A new, empty dashboard" />
          {model && <Big icon="datasets" label="Dataset" onClick={() => go('ds/new')} title="Import data from a data adapter" />}
          {model && <Big icon="code" label="Query" onClick={() => go('q/new')} title="Write a DAX or SQL query over the datasets" />}
          {info.mayPipelines && <Big icon="datasets" label="Pipeline" onClick={() => go('etl/new')} title="ETL / ELT: clean, join and total datasets step by step into a new one" />}
          {info.mayPython && <Big icon="code" label="Python" onClick={() => go('py/new')} title="Clean or model datasets with a Python script (pandas, scikit-learn, scipy)" />}
        </Group>
        {model && (
          <Group label="Files">
            <Big icon="open" label="Import" onClick={onImport} title="A dashboard file (.mdash), a report file (.pbix) or template (.pbit)" />
          </Group>
        )}
        <Group label="Dashboard">
          <Big icon="canvas" label={dash.editing ? 'Editing' : 'Edit'} onClick={() => send('bi-command', { cmd: 'edit' })} disabled={!onDash || dash.editing || !dash.mayChange} />
          <Stack>
            <Small icon="save" label="Save" onClick={() => send('bi-command', { cmd: 'save' })} disabled={!onDash || !dash.editing} />
            <Small icon="undo" label="Cancel" onClick={() => send('bi-command', { cmd: 'cancel' })} disabled={!onDash || !dash.editing} />
            <Small icon="reset" label="Reload data" onClick={() => send('bi-command', { cmd: 'reload' })} disabled={!onDash} />
          </Stack>
        </Group>
        <div className="rb-note">{onDash ? (dash.editing ? 'Editing: add visuals from Insert, drag them on the grid, then Save.' : 'Click a bar or a slice to filter the rest of the dashboard.') : 'Open a dashboard on the left, or start a new one.'}</div>
      </>
    ),
    insert: (
      <>
        <Group label="Visuals">
          <div className="rb-gallery many">
            {VISUAL_TYPES.map(([id, name, path]) => (
              <button key={id} type="button" className="rb-tile" disabled={!onDash || !dash.mayChange} title={`Add a ${name.toLowerCase()} to the dashboard`}
                onClick={() => send('bi-command', { cmd: 'add', type: id })}>
                <span className="rb-tile-icon"><TypeIcon path={path} size={20} /></span><span>{name}</span>
              </button>
            ))}
          </div>
        </Group>
        <div className="rb-note">{onDash ? (dash.mayChange ? 'The visual is added below the others; choose its fields in the pane that opens.' : 'You may not change this dashboard.') : 'Open a dashboard first.'}</div>
      </>
    ),
    query: (
      <>
        <Group label="Query">
          <Big icon="code" label="New" onClick={() => go('q/new')} disabled={!model} title="A new DAX / SQL query" />
          <Big icon="demo" label="Run" primary onClick={() => send('bi-query', { cmd: 'run' })} disabled={!onQuery} title="Run (Ctrl+Enter)" />
          <Stack>
            <Small icon="save" label="Save" onClick={() => send('bi-query', { cmd: 'save' })} disabled={!onQuery || !model} />
            <Small icon="duplicate" label="Save as copy" onClick={() => send('bi-query', { cmd: 'saveAs' })} disabled={!onQuery || !model} />
            <Small icon="download" label="Rows as CSV" onClick={() => send('bi-query', { cmd: 'csv' })} disabled={!onQuery} />
          </Stack>
        </Group>
        <Group label="Over the datasets">
          <Stack>
            <Small label="DAX" onClick={() => send('bi-query', { cmd: 'mode', mode: 'dax' })} disabled={!onQuery || !model}><b className="rb-txt">ƒx</b></Small>
            <Small label="SQL" onClick={() => send('bi-query', { cmd: 'mode', mode: 'sql' })} disabled={!onQuery || !model}><RIcon name="db" size={16} /></Small>
          </Stack>
        </Group>
        <Group label="On a database (live)">
          <Stack>
            <Small label="Mongo Shell" onClick={() => send('bi-query', { cmd: 'mode', mode: 'src:mongosh' })} disabled={!onQuery || !model}><b className="rb-txt">{'{ }'}</b></Small>
            <Small label="Aggregation JSON" onClick={() => send('bi-query', { cmd: 'mode', mode: 'src:aggregation-json' })} disabled={!onQuery || !model}><b className="rb-txt">[ ]</b></Small>
            <Small label="Python pipeline" onClick={() => send('bi-query', { cmd: 'mode', mode: 'src:python' })} disabled={!onQuery || !model}><b className="rb-txt">py</b></Small>
          </Stack>
          <Stack>
            <Small label="JavaScript" onClick={() => send('bi-query', { cmd: 'mode', mode: 'src:javascript' })} disabled={!onQuery || !model}><b className="rb-txt">js</b></Small>
            <Small label="Database SQL" onClick={() => send('bi-query', { cmd: 'mode', mode: 'src:sql' })} disabled={!onQuery || !model}><RIcon name="db" size={16} /></Small>
            <Small icon="datasets" label="Make a dataset" onClick={() => send('bi-query', { cmd: 'dataset' })} disabled={!onQuery || !model || !queryLive} />
          </Stack>
        </Group>
        <div className="rb-note">DAX / SQL read the imported datasets (fast). MongoDB and Database SQL read the data adapter live (first rows); “Make a dataset” imports all rows for dashboards.</div>
      </>
    ),
    data: (
      <>
        {model && (
          <Group label="Datasets">
            <Big icon="datasets" label="New dataset" onClick={() => go('ds/new')} />
            <Big icon="reset" label="Import all again" onClick={onRefreshAll} disabled={!readyCount} title="Read every dataset again from its data adapter (in the background)" />
          </Group>
        )}
        <Group label="Model">
          <Big icon="plug" label="Relationships" onClick={() => go('model')} title="Relate datasets on a shared column" />
        </Group>
        <Group label="Refresh">
          <Big icon="reset" label="Refresh lists" onClick={onReload} />
        </Group>
      </>
    ),
    etl: (
      <>
        <Group label="Pipelines (ETL / ELT)">
          <Big icon="datasets" label="New pipeline" onClick={() => go('etl/new')} disabled={!info.mayPipelines}
            title={noPipelines || 'Steps that take datasets, clean, join and total them, and load a new dataset'} />
          {pipelines.length > 0 && (
            <Stack>
              {pipelines.map((d) => <Small key={d.id} icon="layouts" label={d.name} onClick={() => go(`etl/${d.id}`)} disabled={!info.mayPipelines} />)}
            </Stack>
          )}
        </Group>
        <Group label="Python scripts">
          <Big icon="code" label="New script" onClick={() => go('py/new')} disabled={!info.mayPython}
            title={noPython || 'A dataset made by a Python script (pandas, scikit-learn, scipy) from other datasets'} />
          {scripts.length > 0 && (
            <Stack>
              {scripts.map((d) => <Small key={d.id} icon="code" label={d.name} onClick={() => go(`py/${d.id}`)} disabled={!info.mayPython} />)}
            </Stack>
          )}
        </Group>
        <Group label="Run">
          <Big icon="reset" label="Import all again" onClick={onRefreshAll} disabled={!readyCount || !model} title="Read every dataset again (pipelines and scripts run again after their sources)" />
        </Group>
        {!info.mayPipelines && !info.mayPython && <div className="rb-note">Pipelines and Python scripts need their own rights: ask an administrator.</div>}
      </>
    ),
    view: (
      <>
        <Group label="Go to">
          <Big icon="layouts" label="Home" onClick={() => go('')} />
          <Big icon="plug" label="Model" onClick={() => go('model')} />
          {model && <Big icon="code" label="Queries" onClick={() => go('q/new')} />}
        </Group>
        <Group label="Other pages">
          <Big icon="explorer" label="Analytics Server" onClick={() => window.open('/server', '_self')} title="Folders, permissions, schedules and the published dashboards" />
        </Group>
      </>
    ),
  };

  return (
    <header className={`ribbon bi-ribbon ${ui.collapsed ? 'collapsed' : ''}`}>
      <div className="rb-bar">
        <div className="rb-filewrap" ref={menuBox}>
          <button type="button" className="rb-doc" aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu(!menu)}
            title={`File menu${onDash && dashName ? ` — ${dashName}` : ''}`}>
            <span className="rb-doc-icon" aria-hidden="true"><RIcon name="layouts" size={16} /></span>
            <span className="rb-doc-text"><span className="rb-doc-name"><b>{onDash && dashName ? dashName : 'Analytics'}</b>{dash.editing && <span className="rb-dirty" title="Editing">●</span>}</span>
              <small className="hide-narrow">{onDash ? 'Dashboard' : info.user}</small></span>
            <span className="rb-doc-file">File <span className="rb-caret" aria-hidden="true">▾</span></span>
          </button>
          {menu && (
            <div className="rb-menu" role="menu" aria-label="File" onClick={(event) => { if (event.target.closest('button')) setMenu(false); }}>
              <button type="button" role="menuitem" onClick={onNewDashboard} title="A new, empty dashboard"><RIcon name="newDoc" /><span>New dashboard</span></button>
              {model && <button type="button" role="menuitem" onClick={() => go('ds/new')} title="Import data from a data adapter"><RIcon name="datasets" /><span>New dataset</span></button>}
              {model && <button type="button" role="menuitem" onClick={() => go('q/new')} title="Write a DAX or SQL query over the datasets"><RIcon name="code" /><span>New query</span></button>}
              {info.mayPipelines && <button type="button" role="menuitem" onClick={() => go('etl/new')} title="ETL / ELT steps into a new dataset"><RIcon name="datasets" /><span>New pipeline (ETL)</span></button>}
              {info.mayPython && <button type="button" role="menuitem" onClick={() => go('py/new')} title="Clean or model datasets with pandas, scikit-learn and scipy"><RIcon name="code" /><span>New Python script</span></button>}
              {model && <button type="button" role="menuitem" onClick={onImport} title="Open a dashboard file (.mdash), a report file (.pbix) or template (.pbit)"><RIcon name="open" /><span>Open file (.mdash, .pbix, .pbit)</span></button>}
              <hr />
              <button type="button" role="menuitem" disabled={!onDash || !dash.editing} onClick={() => send('bi-command', { cmd: 'save' })} title="Save the changes to this dashboard"><RIcon name="save" /><span>Save</span><kbd>Ctrl S</kbd></button>
              <button type="button" role="menuitem" disabled={!onDash || dash.editing || !dash.mayChange} onClick={() => send('bi-command', { cmd: 'edit' })} title="Add, move and change visuals"><RIcon name="canvas" /><span>Edit dashboard</span></button>
              <button type="button" role="menuitem" disabled={!onDash} onClick={() => onDownload?.(true)} title="One .mdash file with the dashboard, its datasets and their records: opens on any server, even without the database"><RIcon name="download" /><span>Save as file, with data (.mdash)</span></button>
              <button type="button" role="menuitem" disabled={!onDash} onClick={() => onDownload?.(false)} title="A small .mdash file: the other server imports the data from its own database"><RIcon name="download" /><span>Save as file, definitions only</span></button>
              <button type="button" role="menuitem" disabled={!onDash} onClick={() => window.open('/server', '_blank', 'noopener')} title="Dashboards are on the Analytics Server already: open it to move, share or set permissions"><RIcon name="publish" /><span>Open on the Analytics Server ↗</span></button>
              <hr />
              <button type="button" role="menuitem" disabled={!onDash} onClick={() => send('bi-command', { cmd: 'reload' })} title="Run every visual again"><RIcon name="reset" /><span>Reload data</span></button>
              {model && <button type="button" role="menuitem" disabled={!readyCount} onClick={onRefreshAll} title="Import every dataset again from its data adapter"><RIcon name="reset" /><span>Refresh all datasets</span></button>}
              <button type="button" role="menuitem" onClick={() => go('model')} title="Relationships between datasets"><RIcon name="layouts" /><span>Model (relationships)</span></button>
              <button type="button" role="menuitem" onClick={() => go('embed')} title="Show a dashboard in your own website"><RIcon name="publish" /><span>Embed in a website</span></button>
            </div>
          )}
        </div>
        <div className="rb-tabs" role="tablist" aria-label="Ribbon">
          {tabs.map(([id, label, tint]) => (
            <button key={id} type="button" role="tab" aria-selected={ui.tab === id} className={`tint-${tint} ${ui.tab === id ? 'on' : ''}`} onClick={() => pick(id)}>{label}</button>
          ))}
        </div>
        <div className="rb-quickbar">
          <button type="button" className="rb-quick" title={ui.collapsed ? 'Show the ribbon' : 'Hide the ribbon'} aria-label={ui.collapsed ? 'Show the ribbon' : 'Hide the ribbon'}
            onClick={() => setUi((u) => ({ ...u, collapsed: !u.collapsed }))}><RIcon name={ui.collapsed ? 'chevronDown' : 'chevronUp'} /></button>
        </div>
      </div>
      {!ui.collapsed && <div className={`rb-body tint-${(tabs.find(([id]) => id === ui.tab) || tabs[0])[2]}`} role="tabpanel">{body[ui.tab]}</div>}
    </header>
  );
}
