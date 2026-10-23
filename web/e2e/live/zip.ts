import { inflateRawSync } from 'node:zlib';

/**
 * The files of a ZIP, by name. The API streams its ZIP (data descriptors, so the local headers carry no sizes):
 * read the central directory at the end instead. No ZIP64, no encryption: the export has neither, and a ZIP that has
 * either fails here with a clear message rather than reading wrongly.
 */
export function readZip(zip: Buffer): Map<string, Buffer> {
  let eocd = -1;
  for (let i = zip.length - 22; i >= Math.max(0, zip.length - 22 - 65_535); i--) {
    if (zip.readUInt32LE(i) === 0x06054b50) {
      eocd = i;
      break;
    }
  }
  if (eocd < 0) throw new Error('not a ZIP: no end-of-central-directory record');
  const count = zip.readUInt16LE(eocd + 10);
  let at = zip.readUInt32LE(eocd + 16);
  const files = new Map<string, Buffer>();
  for (let n = 0; n < count; n++) {
    if (zip.readUInt32LE(at) !== 0x02014b50) throw new Error(`bad central directory entry ${n}`);
    const method = zip.readUInt16LE(at + 10);
    const compressed = zip.readUInt32LE(at + 20);
    const nameLength = zip.readUInt16LE(at + 28);
    const extraLength = zip.readUInt16LE(at + 30);
    const commentLength = zip.readUInt16LE(at + 32);
    const local = zip.readUInt32LE(at + 42);
    if (compressed === 0xffffffff || local === 0xffffffff) throw new Error('ZIP64 is not supported by this reader');
    const name = zip.toString('utf8', at + 46, at + 46 + nameLength);
    if (zip.readUInt32LE(local) !== 0x04034b50) throw new Error(`bad local header for ${name}`);
    const start = local + 30 + zip.readUInt16LE(local + 26) + zip.readUInt16LE(local + 28);
    const raw = zip.subarray(start, start + compressed);
    if (method !== 0 && method !== 8) throw new Error(`${name}: unsupported compression method ${method}`);
    files.set(name, method === 0 ? Buffer.from(raw) : inflateRawSync(raw));
    at += 46 + nameLength + extraLength + commentLength;
  }
  return files;
}

/** RFC 4180 rows of a CSV: quoted cells may hold commas, quotes ("") and line breaks. */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (quoted) {
      if (char === '"' && text[i + 1] === '"') {
        cell += '"';
        i++;
      } else if (char === '"') quoted = false;
      else cell += char;
    } else if (char === '"') quoted = true;
    else if (char === ',') {
      row.push(cell);
      cell = '';
    } else if (char === '\n' || char === '\r') {
      if (char === '\r' && text[i + 1] === '\n') i++;
      row.push(cell);
      rows.push(row);
      row = [];
      cell = '';
    } else cell += char;
  }
  if (cell !== '' || row.length > 0) {
    row.push(cell);
    rows.push(row);
  }
  return rows;
}
