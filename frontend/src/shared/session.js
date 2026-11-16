// Who is signed in, for every page. The session is an HttpOnly cookie set by /api/server/login,
// so no token is kept in the page; the server checks the same rights on every API call.
import { useEffect, useState } from 'react';

let mePromise = null;

/** { username, rights: [...], superAdmin, role } or null when signed out. Asked once per page load. */
export function getMe() {
  if (!mePromise) {
    mePromise = fetch('/api/server/me', { credentials: 'same-origin', cache: 'no-store' })
      .then((response) => (response.ok ? response.json() : null))
      .catch(() => null);
  }
  return mePromise;
}

export function useMe() {
  const [me, setMe] = useState(undefined); // undefined = still asking
  useEffect(() => { let live = true; getMe().then((value) => { if (live) setMe(value); }); return () => { live = false; }; }, []);
  return me;
}

/** A local path to come back to after signing in ("/designer?open=X"); anything else is ignored. */
export function safeNext(value) {
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//') && !value.startsWith('/\\') ? value : '';
}

/** Send the browser to the sign-in page and come back here afterwards. */
export function goSignIn() {
  const here = window.location.pathname + window.location.search;
  window.location.assign(`/login?next=${encodeURIComponent(here)}`);
}

export async function signOut() {
  try {
    await fetch('/api/server/logout', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  } finally {
    mePromise = null;
    window.location.assign('/login');
  }
}
