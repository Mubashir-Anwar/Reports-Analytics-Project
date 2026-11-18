// A Power BI report (.pbix) is a ZIP package: its report part (pages and visuals) is readable JSON, its data model
// and data are in Microsoft's closed format. This takes only the report part out of the file, in the browser, so the
// data is never uploaded and a large .pbix is no problem: only the ZIP's directory and the wanted entries are read.

const MAX_PART = 40_000_000;               // the server's limit for the unpacked report part
const wanted = (name) => name === 'Report/Layout' || (name.startsWith('Report/definition/') && name.endsWith('.json'));

const bytes = async (file, start, end) => new Uint8Array(await file.slice(start, end).arrayBuffer());

function toBase64(data) {
  let text = '';
  for (let i = 0; i < data.length; i += 0x8000) text += String.fromCharCode.apply(null, data.subarray(i, i + 0x8000));
  return btoa(text);
}

async function inflate(blob) {
  if (typeof DecompressionStream === 'undefined') throw new Error('This browser cannot unpack the .pbix: use a current Chrome, Edge, Firefox or Safari');
  return new Uint8Array(await new Response(blob.stream().pipeThrough(new DecompressionStream('deflate-raw'))).arrayBuffer());
}

/** {name: base64} of the report parts of a .pbix file. Throws with a message people can act on. */
export async function readPbixReport(file) {
  // the end of central directory record: in the last 64 KB + 22 bytes
  const tailStart = Math.max(0, file.size - 65_557);
  const tail = await bytes(file, tailStart, file.size);
  let end = -1;
  for (let i = tail.length - 22; i >= 0; i -= 1) {
    if (tail[i] === 0x50 && tail[i + 1] === 0x4b && tail[i + 2] === 0x05 && tail[i + 3] === 0x06) { end = i; break; }
  }
  if (end < 0) throw new Error('This is not a Power BI file: it is not a ZIP package');
  const eocd = new DataView(tail.buffer, tail.byteOffset + end, 22);
  const count = eocd.getUint16(10, true);
  const dirSize = eocd.getUint32(12, true);
  const dirOffset = eocd.getUint32(16, true);
  if (dirOffset === 0xffffffff || count === 0xffff) {
    throw new Error('This .pbix is larger than 4 GB: export a Power BI template (.pbit) in Power BI Desktop and import that');
  }
  const dir = await bytes(file, dirOffset, dirOffset + dirSize);
  const view = new DataView(dir.buffer, dir.byteOffset, dir.byteLength);
  const decoder = new TextDecoder();
  const entries = [];
  let at = 0;
  for (let n = 0; n < count && at + 46 <= dir.length; n += 1) {
    if (view.getUint32(at, true) !== 0x02014b50) break;
    const method = view.getUint16(at + 10, true);
    const compressed = view.getUint32(at + 20, true);
    const size = view.getUint32(at + 24, true);
    const nameLength = view.getUint16(at + 28, true);
    const extra = view.getUint16(at + 30, true);
    const comment = view.getUint16(at + 32, true);
    const local = view.getUint32(at + 42, true);
    const name = decoder.decode(dir.subarray(at + 46, at + 46 + nameLength));
    if (wanted(name)) entries.push({ name, method, compressed, size, local });
    at += 46 + nameLength + extra + comment;
  }
  if (!entries.length) {
    throw new Error(file.name.toLowerCase().endsWith('.pbix') ? 'This .pbix has no report pages' : 'This is not a Power BI report: it has no report pages');
  }
  const parts = {};
  let total = 0;
  for (const entry of entries) {
    total += entry.size;
    if (total > MAX_PART) throw new Error('The report part of this .pbix is too big to import');
    const header = new DataView((await bytes(file, entry.local, entry.local + 30)).buffer);
    const start = entry.local + 30 + header.getUint16(26, true) + header.getUint16(28, true);
    const raw = file.slice(start, start + entry.compressed);
    let data;
    if (entry.method === 0) data = new Uint8Array(await raw.arrayBuffer());
    else if (entry.method === 8) data = await inflate(raw);
    else throw new Error(`The .pbix uses a packing method this page cannot read (${entry.method})`);
    parts[entry.name] = toBase64(data);
  }
  return parts;
}
