// The product name. Default "Analysis"; a super admin renames it in Admin → Branding (GET /api/branding).
// The last name seen is kept per browser, so pages show it at once instead of flashing the default.
import { useEffect, useState } from 'react';

const KEY = 'reporting-brand';
const DEFAULT = { name: 'Analysis', tagline: 'Design · Publish · Deliver' };

function split(name) {
  const words = name.trim().split(/\s+/);
  return words.length > 1 ? { first: words[0], second: words.slice(1).join(' ') } : { first: name, second: '' };
}

function make(value) {
  const name = String(value?.name || DEFAULT.name);
  return { name, tagline: String(value?.tagline || DEFAULT.tagline), ...split(name) };
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
  try { localStorage.setItem(KEY, JSON.stringify({ name: BRAND.name, tagline: BRAND.tagline })); } catch { /* private mode */ }
  retitle();
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

export const pageTitle = (part) => `${part} · ${BRAND.name}`;
retitle();
