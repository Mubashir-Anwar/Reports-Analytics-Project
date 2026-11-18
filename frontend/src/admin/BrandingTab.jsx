// /admin → Name & logo: the product name shown in the navigation bar, the sign-in page, page titles and the Postman
// files. A super admin may rename it (default "Analysis"); everybody sees the change on their next page.
import { useEffect, useState } from 'react';
import { request } from '../api.js';
import { refreshBrand } from '../shared/brand.js';
import { Logo } from '../shared/NavBar.jsx';
import { LookPanel } from '../shared/ThemeMenu.jsx';

export default function BrandingTab({ who, notify }) {
  const [form, setForm] = useState(null);
  const [saved, setSaved] = useState(null);
  const [busy, setBusy] = useState(false);
  const mayChange = Boolean(who?.superAdmin || who?.bootstrap);
  useEffect(() => {
    request('/api/branding').then((value) => { setForm(value); setSaved(value); }).catch((error) => notify(error.message, 'error'));
  }, [notify]);
  if (!form) return <section className="card"><p className="muted">Loading…</p></section>;
  const words = form.name.trim().split(/\s+/);
  const changed = saved && (form.name !== saved.name || form.tagline !== saved.tagline);

  const save = async (event) => {
    event.preventDefault();
    setBusy(true);
    try {
      const value = await request('/api/admin/branding', { method: 'PUT', body: form });
      setSaved(value); setForm(value);
      await refreshBrand();
      notify(`The product is now called “${value.name}”. Everybody sees it on their next page.`, 'ok');
    } catch (error) { notify(error.message, 'error'); } finally { setBusy(false); }
  };

  return (
    <div className="grid-2">
      <form className="card" onSubmit={save}>
        <div className="card-head"><div><p className="eyebrow">Branding</p><h2>Product name</h2></div></div>
        <p className="muted">Shown in the navigation bar of every page, on the sign-in page, in the browser tab and in the Postman files. Choose any name; the default is <b>Analysis</b>.</p>
        <label className="field"><span>Name</span>
          <input value={form.name} maxLength={40} disabled={!mayChange} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Analysis" required />
        </label>
        <label className="field"><span>Tagline (small text under the name)</span>
          <input value={form.tagline} maxLength={60} disabled={!mayChange} onChange={(e) => setForm({ ...form, tagline: e.target.value })} placeholder="Design · Publish · Deliver" />
        </label>
        <div className="brand-preview" aria-label="Preview">
          <Logo size={34} className="fx-logo" />
          <span className="fx-name"><b>{words[0] || 'Analysis'}{words.length > 1 && <span>{words.slice(1).join(' ')}</span>}</b><small>{form.tagline}</small></span>
        </div>
        {mayChange ? (
          <div className="row-actions">
            <button type="button" className="btn" disabled={!changed || busy} onClick={() => setForm(saved)}>Undo changes</button>
            <button type="submit" className="btn primary" disabled={!changed || busy}>{busy ? 'Saving…' : 'Save name'}</button>
          </div>
        ) : <p className="muted">Only a super admin may change the name.</p>}
        <p className="muted" style={{ fontSize: 12 }}>The logo files are in <code>ui/brand/</code> (logo.svg, favicon.svg, wordmark.svg): replace them to use your own logo.</p>
      </form>
      <section className="card">
        <div className="card-head"><div><p className="eyebrow">Your browser</p><h2>Theme</h2></div></div>
        <p className="muted">Each person picks their own theme (kept in their browser): also from the palette button in the top bar, and in the Designer under View → Theme.</p>
        <div className="lk-inline"><LookPanel /></div>
      </section>
    </div>
  );
}
