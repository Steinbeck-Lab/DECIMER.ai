import { expect, test, vi } from 'vitest';
import { handlePaste } from './paste';

function pasteEvent(items: { kind: string; type: string; file?: File }[]) {
  const preventDefault = vi.fn();
  const event = {
    defaultPrevented: false,
    preventDefault,
    clipboardData: {
      items: items.map((i) => ({ kind: i.kind, type: i.type, getAsFile: () => i.file ?? null })),
      files: items.flatMap((i) => (i.file ? [i.file] : [])),
    },
  } as unknown as ClipboardEvent;
  return { event, preventDefault };
}

test('an image on the clipboard is submitted at once and the paste is taken', () => {
  const submit = vi.fn();
  const png = new File(['x'], 'image.png', { type: 'image/png' });
  const { event, preventDefault } = pasteEvent([{ kind: 'file', type: 'image/png', file: png }]);
  expect(handlePaste(event, submit)).toBe(true);
  expect(submit).toHaveBeenCalledTimes(1);
  expect(submit.mock.calls[0]?.[0]).toHaveLength(1);
  expect(preventDefault).toHaveBeenCalled();
});

test('text is ignored: no job, and the browser still pastes it', () => {
  const submit = vi.fn();
  const { event, preventDefault } = pasteEvent([{ kind: 'string', type: 'text/plain' }]);
  expect(handlePaste(event, submit)).toBe(false);
  expect(submit).not.toHaveBeenCalled();
  expect(preventDefault).not.toHaveBeenCalled();
});

test('an event another handler already took is not submitted twice', () => {
  const submit = vi.fn();
  const png = new File(['x'], 'image.png', { type: 'image/png' });
  const { event } = pasteEvent([{ kind: 'file', type: 'image/png', file: png }]);
  Object.defineProperty(event, 'defaultPrevented', { value: true });
  expect(handlePaste(event, submit)).toBe(false);
  expect(submit).not.toHaveBeenCalled();
});

test('a paste with several images submits only the first', () => {
  const submit = vi.fn();
  const a = new File(['a'], 'image.png', { type: 'image/png' });
  const b = new File(['b'], 'image.png', { type: 'image/png' });
  const { event } = pasteEvent([
    { kind: 'file', type: 'image/png', file: a },
    { kind: 'file', type: 'image/png', file: b },
  ]);
  expect(handlePaste(event, submit)).toBe(true);
  expect(submit).toHaveBeenCalledTimes(1);
  expect(submit.mock.calls[0]?.[0]).toHaveLength(1);
});
