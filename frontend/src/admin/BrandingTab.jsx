// /admin → Name & look: the product name and logo shown in the navigation bar, the sign-in page, the browser tab and
// the Postman files. A super admin may change them (default "Analysis" with the built-in mark); everybody sees the
// change on their next page.
import { useEffect, useRef, useState } from 'react';
import { request } from '../api.js';
import { refreshBrand } from '../shared/brand.js';
import { Logo } from '../shared/NavBar.jsx';
import { LookPanel } from '../shared/ThemeMenu.jsx';

const MAX_LOGO_KB = 300;
const LOGO_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/svg+xml'];

const readAsDataUrl = (file) => new Promise((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(String(reader.result));
  reader.onerror = () => reject(new Error('The picture could not be read'));
  reader.readAsDataURL(file);
});

export default function BrandingTab({ who, notify }) {
  const [form, setForm] = useState(null);
  const [saved, setSaved] = useState(null);
  const [logo, setLogo] = useState(undefined);   // undefined: keep · null: back to the built-in mark · "data:…": a new one
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const picker = useRef(null);
  const mayChange = Boolean(who?.superAdmin);
  useEffect(() => {
    request('/api/branding').then((value) => { setForm(value); setSaved(value); }).catch((error) => notify(error.message, 'error'));
  }, [notify]);
  if (!form) return <section className="card"><p className="muted">Loading…</p></section>;
  const words = form.name.trim().split(/\s+/);
  const changed = saved && (form.name !== saved.name || form.tagline !== saved.tagline || logo !== undefined);
  const shown = logo === undefined ? (saved?.logo || '') : (logo || '');

  const choose = async (file) => {
    if (!file) return;
    if (!LOGO_TYPES.includes(file.type)) { notify('Choose a PNG, JPEG, WebP or SVG picture.', 'error'); return; }
    if (file.size > MAX_LOGO_KB * 1000) { notify(`The logo may be at most ${MAX_LOGO_KB} KB (this one is ${Math.round(file.size / 1000)} KB).`, 'error'); return; }
    try { setLogo(await readAsDataUrl(file)); } catch (error) { notify(error.message, 'error'); }
  };

  const save = async (event) => {
    event.preventDefault();
    setBusy(true);
    try {
      const body = { name: form.name, tagline: form.tagline, ...(logo !== undefined ? { logo } : {}) };
      const value = await request('/api/admin/branding', { method: 'PUT', body });
      setSaved(value); setForm(value); setLogo(undefined);
      await refreshBrand();
      notify(`Saved: “${value.name}”${value.logo ? ' with your logo' : ''}. Everybody sees it on their next page.`, 'ok');
    } catch (error) { notify(error.message, 'error'); } finally { setBusy(false); }
  };

  return (
    <div className="grid-2">
      <form className="card brand-card" onSubmit={save}>
        <div className="card-head"><div><p className="eyebrow">Branding</p><h2>Name &amp; logo</h2></div></div>
        <p className="muted">Shown in the navigation bar of every page, on the sign-in page, in the browser tab and in the Postman files. The default is <b>Analysis</b> with the built-in mark.</p>
        <label className="field"><span>Name</span>
          <input value={form.name} maxLength={40} disabled={!mayChange} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Analysis" required />
        </label>
        <label className="field"><span>Tagline (small text under the name)</span>
          <input value={form.tagline} maxLength={60} disabled={!mayChange} onChange={(e) => setForm({ ...form, tagline: e.target.value })} placeholder="Design · Publish · Deliver" />
        </label>

        <div className="field"><span>Logo</span>
          <div
            className={`logo-drop${drag ? ' is-drag' : ''}${mayChange ? '' : ' is-off'}`}
            role="button" tabIndex={mayChange ? 0 : -1} aria-label="Choose a logo picture"
            onClick={() => mayChange && picker.current?.click()}
            onKeyDown={(e) => { if (mayChange && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); picker.current?.click(); } }}
            onDragOver={(e) => { if (mayChange) { e.preventDefault(); setDrag(true); } }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => { e.preventDefault(); setDrag(false); if (mayChange) choose(e.dataTransfer.files?.[0]); }}
          >
            <span className="logo-drop-pic" key={shown || 'builtin'}><Logo size={52} className="fx-logo" src={shown} /></span>
            <span className="logo-drop-text">
              <b>{shown ? (logo ? 'New logo, not saved yet' : 'Your logo') : 'Built-in mark'}</b>
              <small>{mayChange ? `Drop a picture here or click · PNG, JPEG, WebP or SVG · up to ${MAX_LOGO_KB} KB · square works best` : 'Only a super admin may change the logo.'}</small>
            </span>
          </div>
          <input ref={picker} type="file" accept={LOGO_TYPES.join(',')} hidden onChange={(e) => { choose(e.target.files?.[0]); e.target.value = ''; }} />
          {mayChange && shown && <button type="button" className="btn ghost sm logo-reset" onClick={() => setLogo(null)}>Use the built-in mark</button>}
        </div>

        <div className="brand-preview" aria-label="Preview">
          <Logo size={34} className="fx-logo" src={shown} />
          <span className="fx-name"><b>{words[0] || 'Analysis'}{words.length > 1 && <span>{words.slice(1).join(' ')}</span>}</b><small>{form.tagline}</small></span>
        </div>
        {mayChange ? (
          <div className="row-actions">
            <button type="button" className="btn" disabled={!changed || busy} onClick={() => { setForm(saved); setLogo(undefined); }}>Undo changes</button>
            <button type="submit" className="btn primary" disabled={!changed || busy}>{busy ? 'Saving…' : 'Save name & logo'}</button>
          </div>
        ) : <p className="muted">Only a super admin may change the name and logo.</p>}
        <p className="muted" style={{ fontSize: 12 }}>The logo is kept in the system database, so every server behind a load balancer shows it. SVG files with scripts are refused.</p>
      </form>
      <section className="card">
        <div className="card-head"><div><p className="eyebrow">Your browser</p><h2>Theme</h2></div></div>
        <p className="muted">Each person picks their own theme (kept in their browser): also from the palette button in the top bar, and in the Designer under View → Theme.</p>
        <div className="lk-inline"><LookPanel /></div>
      </section>
    </div>
  );
}
