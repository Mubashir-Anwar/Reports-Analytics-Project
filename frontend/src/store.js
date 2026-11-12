import { emptyDesign } from './designModel.js';

export const blankReport = () => ({
  isNew: true,
  id: '',
  title: 'New report',
  description: '',
  folder: 'General',
  connectionId: '', // chosen by the user in Setup; nothing is preselected
  collection: '',
  queryLanguage: 'mongosh',
  querySource: '',
  parameters: [],
  columns: [],
  extra: {},
});

export const initialState = {
  report: blankReport(),
  design: emptyDesign(),
  past: [],
  future: [],
  selection: { kind: 'report' },
  tool: null,
  dirty: false,
  lastKey: '',
  lastTime: 0,
};

/** The report definition as it is saved in the workspace and published to the server. */
export function reportPayload({ report, design }) {
  const base = { ...report.extra };
  delete base.pipeline; // the backend re-parses the query source
  delete base.workspace;
  return {
    ...base,
    id: report.id, title: report.title, description: report.description, folder: report.folder,
    connectionId: report.connectionId, collection: report.collection,
    queryLanguage: report.queryLanguage, querySource: report.querySource,
    parameters: report.parameters, columns: report.columns,
    formats: base.formats || ['csv', 'xlsx', 'pdf'], defaultFormat: base.defaultFormat || 'pdf',
    design,
  };
}

const HISTORY_LIMIT = 80;

function withHistory(state, nextDesign, key) {
  const now = Date.now();
  const coalesce = key && key === state.lastKey && now - state.lastTime < 900;
  return {
    ...state,
    design: nextDesign,
    past: coalesce ? state.past : [...state.past, state.design].slice(-HISTORY_LIMIT),
    future: [],
    dirty: true,
    lastKey: key || '',
    lastTime: now,
  };
}

export function reducer(state, action) {
  switch (action.type) {
    case 'LOAD':
      return { ...initialState, report: action.report, design: action.design, dirty: false };
    case 'NEW':
      return { ...initialState, report: { ...blankReport(), ...(action.folder ? { folder: action.folder } : {}) }, design: emptyDesign() };
    case 'REPORT':
      return { ...state, report: { ...state.report, ...action.patch }, dirty: true };
    case 'DESIGN': // history-tracked; `update` is design => design, `key` coalesces rapid edits
      return withHistory(state, action.update(state.design), action.key);
    case 'LIVE': // drag/resize frames: no history entry
      return { ...state, design: action.update(state.design), dirty: true };
    case 'SNAPSHOT': // call once before a drag/resize starts
      return { ...state, past: [...state.past, state.design].slice(-HISTORY_LIMIT), future: [], lastKey: '' };
    case 'UNDO': {
      if (!state.past.length) return state;
      const previous = state.past[state.past.length - 1];
      return { ...state, design: previous, past: state.past.slice(0, -1), future: [state.design, ...state.future], dirty: true, lastKey: '' };
    }
    case 'REDO': {
      if (!state.future.length) return state;
      const [next, ...rest] = state.future;
      return { ...state, design: next, past: [...state.past, state.design], future: rest, dirty: true, lastKey: '' };
    }
    case 'SELECT':
      return { ...state, selection: action.selection };
    case 'TOOL':
      return { ...state, tool: action.tool };
    case 'SAVED':
      return { ...state, dirty: false, report: { ...state.report, isNew: false, id: action.id, status: action.status || state.report.status } };
    case 'STATUS': // workspace/publish status of the open report
      return { ...state, report: { ...state.report, status: action.status } };
    default:
      return state;
  }
}
