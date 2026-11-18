/**
 * Ask before deleting a repository folder. `contents` = { subfolders, reports, dashboards, adapters } from the server.
 * Returns null (cancelled / not possible: `problem` is set) or { recursive } for the delete call.
 */
export function confirmFolderDelete(path, contents) {
  if (contents.adapters.length) {
    return { problem: `The folder ${path} has data adapters (${contents.adapters.join(', ')}). Move or delete them on the Data Adapters tab first.` };
  }
  const dashboards = contents.dashboards || [];
  const empty = !contents.subfolders.length && !contents.reports.length && !dashboards.length;
  if (empty) return window.confirm(`Delete the empty folder “${path}”?`) ? { recursive: false } : null;
  const lines = [`Delete the folder “${path}” with everything inside it?`, ''];
  if (contents.subfolders.length) lines.push(`• ${contents.subfolders.length} subfolder(s)`);
  if (contents.reports.length) {
    lines.push(`• ${contents.reports.length} published report(s): ${contents.reports.slice(0, 8).join(', ')}${contents.reports.length > 8 ? ', …' : ''}`);
    lines.push('', 'The reports are removed from the server (the designer workspace copies stay and can be published again).');
  }
  if (dashboards.length) {
    lines.push(`• ${dashboards.length} dashboard(s): ${dashboards.slice(0, 8).join(', ')}${dashboards.length > 8 ? ', …' : ''}`);
    lines.push('', 'The dashboards are deleted; their datasets stay in Analytics.');
  }
  lines.push('', 'This cannot be undone.');
  return window.confirm(lines.join('\n')) ? { recursive: true } : null;
}
