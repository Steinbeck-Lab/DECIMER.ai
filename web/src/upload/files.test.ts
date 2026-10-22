import { expect, test } from 'vitest';
import { LIMITS } from '../api/contract';
import { checkSelection, imagesFromClipboard, kindOf } from './files';

const file = (name: string, type = '', size = 10) => {
  const f = new File(['x'], name, { type });
  Object.defineProperty(f, 'size', { value: size });
  return f;
};

test('accepts one PDF', () => {
  const r = checkSelection([file('paper.pdf', 'application/pdf')]);
  expect(r).toMatchObject({ ok: true, kind: 'pdf' });
});

test('accepts several images of different formats, by extension when the type is empty (HEIC)', () => {
  const r = checkSelection([file('a.PNG', 'image/png'), file('b.heic'), file('c.jpeg'), file('d.webp')]);
  expect(r).toMatchObject({ ok: true, kind: 'image' });
});

test('rejects a PDF mixed with images (the legacy mixed-input error)', () => {
  const r = checkSelection([file('paper.pdf', 'application/pdf'), file('a.png', 'image/png')]);
  expect(r).toEqual({ ok: false, error: { code: 'mixed' } });
});

test('rejects two PDFs', () => {
  const r = checkSelection([file('a.pdf'), file('b.pdf')]);
  expect(r).toEqual({ ok: false, error: { code: 'manyPdfs' } });
});

test('names every unsupported file', () => {
  const r = checkSelection([file('a.png'), file('notes.txt'), file('x.docx')]);
  expect(r).toEqual({ ok: false, error: { code: 'format', names: ['notes.txt', 'x.docx'] } });
});

test('rejects more than 100 MB in total, accepts exactly 100 MB', () => {
  const half = LIMITS.maxUploadBytes / 2;
  expect(checkSelection([file('a.png', '', half), file('b.png', '', half)]).ok).toBe(true);
  const r = checkSelection([file('a.png', '', half), file('b.png', '', half + 1)]);
  expect(r).toEqual({ ok: false, error: { code: 'size', megabytes: 101 } });
});

test('more than 50 files is refused, 50 is fine (the API limit)', () => {
  const many = (n: number) => Array.from({ length: n }, (_, i) => file(`p${i}.png`, 'image/png'));
  expect(checkSelection(many(50)).ok).toBe(true);
  expect(checkSelection(many(51))).toEqual({ ok: false, error: { code: 'tooMany', max: 50 } });
});

test('nothing selected is an error, not a job', () => {
  expect(checkSelection([])).toEqual({ ok: false, error: { code: 'none' } });
});

test('a file with no extension is judged by its type', () => {
  expect(kindOf({ name: 'scan', type: 'application/pdf' })).toBe('pdf');
  expect(kindOf({ name: 'scan', type: 'image/png' })).toBe('image');
  expect(kindOf({ name: 'scan', type: 'text/plain' })).toBeNull();
});

function clipboard(items: { kind: string; type: string; file?: File }[]) {
  return {
    items: items.map((i) => ({ kind: i.kind, type: i.type, getAsFile: () => i.file ?? null })),
    files: items.flatMap((i) => (i.file ? [i.file] : [])),
  } as unknown as DataTransfer;
}

test('paste: an image is returned, with a readable name instead of image.png', () => {
  const png = new File(['x'], 'image.png', { type: 'image/png' });
  const out = imagesFromClipboard(clipboard([{ kind: 'file', type: 'image/png', file: png }]));
  expect(out).toHaveLength(1);
  expect(out[0]?.name).toBe('pasted-image.png');
  expect(out[0]?.type).toBe('image/png');
});

test('paste: plain text and HTML are ignored', () => {
  const out = imagesFromClipboard(
    clipboard([
      { kind: 'string', type: 'text/plain' },
      { kind: 'string', type: 'text/html' },
    ]),
  );
  expect(out).toEqual([]);
});

test('paste: a non-image file (a copied PDF) is ignored', () => {
  const pdf = new File(['x'], 'a.pdf', { type: 'application/pdf' });
  expect(imagesFromClipboard(clipboard([{ kind: 'file', type: 'application/pdf', file: pdf }]))).toEqual([]);
});

test('paste: several images on the clipboard give one file, the first', () => {
  const a = new File(['a'], 'image.png', { type: 'image/png' });
  const b = new File(['b'], 'image.png', { type: 'image/png' });
  const out = imagesFromClipboard(
    clipboard([
      { kind: 'file', type: 'image/png', file: a },
      { kind: 'file', type: 'image/png', file: b },
    ]),
    1,
  );
  expect(out).toHaveLength(1);
  expect(out[0]?.name).toBe('pasted-image.png'); // not "-1": it is the only one
});

test('paste: no clipboard data is ignored', () => {
  expect(imagesFromClipboard(null)).toEqual([]);
});
