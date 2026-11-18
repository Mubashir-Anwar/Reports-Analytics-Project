/** Top bar of the React /server and /admin pages: the shared navigation bar. */
import NavBar from '../shared/NavBar.jsx';

export function Topbar({ current, children }) {
  return <NavBar current={current}>{children}</NavBar>;
}

/** A status line: { text, kind: '' | 'ok' | 'error' | 'warn' } or null. */
export function Notice({ message, children }) {
  if (!message?.text && !children) return null;
  return <div className={`notice ${message?.kind || ''}`} role="status">{message?.text}{children}</div>;
}

export const formatWhen = (iso) => (iso ? new Date(iso).toLocaleString() : 'never');

/** Save a Blob as a file in the browser. */
export function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 300_000);
  return url;
}
