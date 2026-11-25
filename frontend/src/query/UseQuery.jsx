// What to do with a query that works: a new report (Designer) or a dataset for dashboards (Analytics), by right.
// Query's data sources work only in Query, so one of the part it goes to takes over.
import { useState } from 'react';
import { api } from '../api.js';
import { useMe } from '../shared/session.js';
import TakeAdapter from '../shared/TakeAdapter.jsx';
import { sendToAnalytics, sendToDesigner } from './handoff.js';

const SEND = { designer: sendToDesigner, analytics: sendToAnalytics };

export default function UseQuery({ query, disabled }) {
  const rights = useMe()?.rights || [];
  const analytics = rights.includes('analytics') && (rights.includes('designer') || rights.includes('admin'));
  const [asking, setAsking] = useState(null);       // { area, source, value }
  const [error, setError] = useState('');
  const take = async (area) => {
    const value = query();
    setError('');
    try {
      const source = await api.connectionTargets(value.connectionId, area);
      if (source.suggested) SEND[area]({ ...value, connectionId: source.suggested });
      else setAsking({ area, source, value });
    } catch (e) { setError(e.message); }
  };
  return (
    <>
      {rights.includes('designer') && <button type="button" className="btn sm" disabled={disabled} onClick={() => take('designer')} title="A new report in the Designer with this query">New report ↗</button>}
      {analytics && <button type="button" className="btn sm" disabled={disabled} onClick={() => take('analytics')} title="A new dataset in Analytics with this query: dashboards, maps, machine learning">Use in Analytics ↗</button>}
      {error && <span className="q-error inline">{error}</span>}
      {asking && <TakeAdapter source={asking.source} area={asking.area} onClose={() => setAsking(null)}
        onDone={(id) => { SEND[asking.area]({ ...asking.value, connectionId: id }); setAsking(null); }} />}
    </>
  );
}
