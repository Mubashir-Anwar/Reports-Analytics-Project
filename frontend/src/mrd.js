// The .mrd file ("Mongo Report Design"): one XML file with everything a report needs — query, parameters,
// fields, data adapter ID, band design and its images. Like a .jrxml it can be opened in the designer and
// uploaded to the report server (Add Resource). Same format as the server's report definition XML.

export const MRD_ACCEPT = '.mrd,.jrxml,.xml,application/xml,text/xml';

export function buildMrd(definition) {
  const doc = document.implementation.createDocument('', '', null);
  const root = doc.createElement('mongoReportDefinition');
  root.setAttribute('version', '1');
  root.setAttribute('format', 'mrd');
  root.setAttribute('reportId', definition.id || '');
  root.setAttribute('title', definition.title || '');
  root.setAttribute('folder', definition.folder || 'General');
  root.setAttribute('savedAt', new Date().toISOString());
  const payload = doc.createElement('definition');
  payload.setAttribute('encoding', 'json');
  payload.textContent = JSON.stringify(definition);
  root.appendChild(payload);
  doc.appendChild(root);
  return `<?xml version="1.0" encoding="UTF-8"?>\n${new XMLSerializer().serializeToString(doc)}`;
}

/** 'mrd' | 'jrxml' | null for the text of a chosen file. */
export function fileKind(text) {
  const doc = new DOMParser().parseFromString(text, 'application/xml');
  if (doc.getElementsByTagName('parsererror').length) return null;
  const tag = doc.documentElement.localName;
  if (tag === 'mongoReportDefinition') return 'mrd';
  if (tag === 'jasperReport') return 'jrxml';
  return null;
}

export function parseMrd(text) {
  const doc = new DOMParser().parseFromString(text, 'application/xml');
  const root = doc.documentElement;
  if (doc.getElementsByTagName('parsererror').length || root.localName !== 'mongoReportDefinition') throw new Error('This is not a .mrd report file');
  if (root.getAttribute('version') !== '1') throw new Error('This .mrd file was made by a newer version');
  const payload = root.getElementsByTagName('definition')[0];
  if (!payload || payload.getAttribute('encoding') !== 'json') throw new Error('The .mrd file has no report definition inside');
  const definition = JSON.parse(payload.textContent);
  if (!definition || typeof definition !== 'object' || !definition.id) throw new Error('The .mrd file has no report ID');
  return definition;
}

export function downloadText(text, filename, type = 'application/xml') {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
