// The product name and logo. Default "Analysis" with the built-in mark; a super admin changes both in
// Admin → Name & look (GET /api/branding; logo is "/brand/logo?v=…" for an own logo, else "").
// The last name seen is kept per browser, so pages show it at once instead of flashing the default.
import { useEffect, useState } from 'react';

const KEY = 'reporting-brand';
const DEFAULT = { name: 'Analysis', tagline: 'Design · Publish · Deliver', logo: '' };

function split(name) {
  const words = name.trim().split(/\s+/);
  return words.length > 1 ? { first: words[0], second: words.slice(1).join(' ') } : { first: name, second: '' };
}

function make(value) {
  const name = String(value?.name || DEFAULT.name);
  const logo = String(value?.logo || '');
  return { name, tagline: String(value?.tagline || DEFAULT.tagline), logo: logo.startsWith('/brand/logo') ? logo : '', ...split(name) };
}

function cached() {
  try { return make(JSON.parse(localStorage.getItem(KEY) || 'null') || DEFAULT); } catch { return make(DEFAULT); }
}

/** The name as last seen (use useBrand() in components so a rename shows up). */
export let BRAND = cached();

let loading = null;
const listeners = new Set();

function publish(value) {
  BRAND = make(value);
  try { localStorage.setItem(KEY, JSON.stringify({ name: BRAND.name, tagline: BRAND.tagline, logo: BRAND.logo })); } catch { /* private mode */ }
  retitle();
  refavicon();
  listeners.forEach((listener) => listener(BRAND));
}

/** Read the name from the server again (after a rename in Admin). */
export function refreshBrand() {
  loading = fetch('/api/branding', { credentials: 'same-origin' })
    .then((response) => (response.ok ? response.json() : null))
    .then((value) => { if (value) publish(value); return BRAND; })
    .catch(() => BRAND);
  return loading;
}

export function useBrand() {
  const [brand, setBrand] = useState(BRAND);
  useEffect(() => {
    listeners.add(setBrand);
    if (!loading) refreshBrand();
    return () => listeners.delete(setBrand);
  }, []);
  return brand;
}

/** "<part> · <name>" titles; the part is taken from the page's own <title> ("Designer · …"). */
function retitle() {
  if (typeof document === 'undefined') return;
  const part = document.title.split(' · ')[0] || '';
  document.title = part ? `${part} · ${BRAND.name}` : BRAND.name;
}

/** The browser-tab icon follows an own logo. */
function refavicon() {
  if (typeof document === 'undefined') return;
  const link = document.querySelector('link[rel~="icon"]');
  if (!link) return;
  if (!link.dataset.builtin) link.dataset.builtin = link.getAttribute('href') || '';
  link.setAttribute('href', BRAND.logo || link.dataset.builtin);
}

export const pageTitle = (part) => `${part} · ${BRAND.name}`;
retitle();
refavicon();
