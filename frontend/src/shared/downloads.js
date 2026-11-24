// The download panel shared by the React pages (ui/brand/downloads.js).
import '../../../ui/brand/downloads.js';

/** Work that runs in this page (the page fetches the file) while the "still downloading" panel is shown. */
export function trackDownload(label, work) {
  return window.fxDownloads ? window.fxDownloads.track(label, work) : work();
}

/**
 * Have the server make the file in the background and save it when it is ready, on whatever page the user is
 * then on. Resolves { queued: true }; rejects with the server's message when the request is refused.
 */
export function downloadLater(label, url, options) {
  return window.fxDownloads.later(label, url, options);
}

/** A normal browser download of a URL that answers with a file (no waiting in the page). */
export function saveUrl(url) {
  window.fxDownloads.saveUrl(url);
}
