import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { test } from 'node:test';
import { pluginModule } from './pluginModule.mjs';

const { zipFiles } = await pluginModule('export-formats/frontend/zip.ts');

function readZip(bytes) {
  return JSON.parse(execFileSync('python3', ['-c', `
import base64, io, json, sys, zipfile
with zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())) as archive:
    assert archive.testzip() is None
    print(json.dumps([{
        'name': info.filename,
        'data': base64.b64encode(archive.read(info)).decode(),
        'crc': info.CRC,
        'method': info.compress_type,
        'date': info.date_time,
        'utf8': bool(info.flag_bits & 0x800),
    } for info in archive.infolist()]))
`], { input: bytes, encoding: 'utf8' }));
}

test('LaTeX project ZIP is readable by Python with binary data and UTF-8 paths', () => {
  const binary = new Uint8Array([0, 255, 128, 1, 10]);
  const files = new Map([
    ['main.tex', '123456789'],
    ['references.bib', '@article{a, title={Müller}}\n'],
    ['figures/épreuve.png', binary],
    ['notes/empty.tex', ''],
  ]);
  const bytes = zipFiles(files);
  assert.deepEqual(bytes, zipFiles(files));
  const result = readZip(bytes);
  assert.deepEqual(result.map(file => file.name), [...files.keys()]);
  for (const [index, value] of [...files.values()].entries()) {
    const expected = typeof value === 'string' ? Buffer.from(value) : Buffer.from(value);
    assert.deepEqual(Buffer.from(result[index].data, 'base64'), expected);
    assert.equal(result[index].method, 0);
    assert.equal(result[index].utf8, true);
    assert.deepEqual(result[index].date, [1980, 1, 1, 0, 0, 0]);
  }
  assert.equal(result[0].crc, 0xcbf43926);
});

test('empty ZIP and unsafe archive paths', () => {
  assert.deepEqual(readZip(zipFiles(new Map())), []);
  for (const name of ['', '/root.tex', '../x', 'notes/../x', './x', 'notes//x', 'notes/', 'C:/x', 'a\\b', 'a\u0000b']) {
    assert.throws(() => zipFiles(new Map([[name, 'x']])), /safe relative/);
  }
  assert.throws(() => zipFiles(new Map([['é'.repeat(32768), 'x']])), /name is too long/);
});

test('ZIP32 limits fail before allocating archive data', () => {
  const tooMany = new Map(Array.from({ length: 65535 }, (_, index) => [String(index), '']));
  assert.throws(() => zipFiles(tooMany), /Too many files/);
  const oversized = { length: 0xffffffff };
  assert.throws(() => zipFiles(new Map([['big', oversized]])), /File is too large/);
  assert.throws(() => zipFiles(new Map([['a', { length: 0x80000000 }], ['b', { length: 0x80000000 }]])), /Project is too large/);
});
