// Stored ZIP entries keep project downloads offline without a compression dependency.
const encoder = new TextEncoder();
const ZIP32_LIMIT = 0xffffffff;
const UTF8 = 0x0800;

function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function safeName(name: string): Uint8Array {
  if (!name || /[\\\u0000-\u001f\u007f:]/.test(name) || name.split('/').some(part => !part || part === '.' || part === '..')) {
    throw new Error('ZIP entries need safe relative file names.');
  }
  const bytes = encoder.encode(name);
  if (bytes.length > 0xffff) throw new Error('ZIP file name is too long.');
  return bytes;
}

/** Deterministic ZIP32 archive with UTF-8 names and uncompressed file data. */
export function zipFiles(files: Map<string, Uint8Array | string>): Uint8Array {
  if (files.size >= 0xffff) throw new Error('Too many files for a ZIP32 archive.');
  const entries = [...files].map(([name, value]) => ({
    name: safeName(name), data: typeof value === 'string' ? encoder.encode(value) : value,
  }));
  let dataSize = 0, directorySize = 0;
  for (const entry of entries) {
    if (entry.data.length >= ZIP32_LIMIT) throw new Error('File is too large for a ZIP32 archive.');
    dataSize += 30 + entry.name.length + entry.data.length;
    directorySize += 46 + entry.name.length;
  }
  const total = dataSize + directorySize + 22;
  if (total >= ZIP32_LIMIT) throw new Error('Project is too large for a ZIP32 archive.');
  const output = new Uint8Array(total);
  const view = new DataView(output.buffer);
  let local = 0, central = dataSize;
  for (const entry of entries) {
    const crc = crc32(entry.data);
    view.setUint32(local, 0x04034b50, true);
    view.setUint16(local + 4, 20, true);
    view.setUint16(local + 6, UTF8, true);
    view.setUint16(local + 12, 33, true); // 1980-01-01, reproducible across machines.
    view.setUint32(local + 14, crc, true);
    view.setUint32(local + 18, entry.data.length, true);
    view.setUint32(local + 22, entry.data.length, true);
    view.setUint16(local + 26, entry.name.length, true);
    output.set(entry.name, local + 30);
    output.set(entry.data, local + 30 + entry.name.length);

    view.setUint32(central, 0x02014b50, true);
    view.setUint16(central + 4, 20, true);
    view.setUint16(central + 6, 20, true);
    view.setUint16(central + 8, UTF8, true);
    view.setUint16(central + 14, 33, true);
    view.setUint32(central + 16, crc, true);
    view.setUint32(central + 20, entry.data.length, true);
    view.setUint32(central + 24, entry.data.length, true);
    view.setUint16(central + 28, entry.name.length, true);
    view.setUint32(central + 42, local, true);
    output.set(entry.name, central + 46);
    local += 30 + entry.name.length + entry.data.length;
    central += 46 + entry.name.length;
  }
  view.setUint32(central, 0x06054b50, true);
  view.setUint16(central + 8, entries.length, true);
  view.setUint16(central + 10, entries.length, true);
  view.setUint32(central + 12, directorySize, true);
  view.setUint32(central + 16, dataSize, true);
  return output;
}
