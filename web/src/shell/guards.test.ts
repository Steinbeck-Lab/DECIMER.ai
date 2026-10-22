import { expect, test, vi } from 'vitest';
import { confirmBody } from './confirm';
import { guardStrayDrop, guardUnload } from './guards';

function dragEvent(types: string[], type = 'dragover') {
  const event = {
    type,
    defaultPrevented: false,
    dataTransfer: { types, dropEffect: 'copy' },
    preventDefault: vi.fn(() => {
      event.defaultPrevented = true;
    }),
  };
  return event as unknown as DragEvent & { preventDefault: ReturnType<typeof vi.fn> };
}

test('a file dropped outside the drop zone is cancelled, so the browser does not open it and replace the page', () => {
  const over = dragEvent(['Files']);
  guardStrayDrop(over);
  expect(over.preventDefault).toHaveBeenCalled();
  expect(over.dataTransfer?.dropEffect).toBe('none'); // the cursor says no

  const drop = dragEvent(['Files'], 'drop');
  guardStrayDrop(drop);
  expect(drop.preventDefault).toHaveBeenCalled();
});

test('dragging text or a link is left alone (Ketcher and the browser handle those)', () => {
  const event = dragEvent(['text/plain', 'text/uri-list']);
  guardStrayDrop(event);
  expect(event.preventDefault).not.toHaveBeenCalled();
});

test('leaving the page asks for confirmation', () => {
  const event = { preventDefault: vi.fn(), returnValue: undefined as unknown } as unknown as BeforeUnloadEvent;
  guardUnload(event);
  expect(event.preventDefault).toHaveBeenCalled();
  expect(event.returnValue).toBe('');
});

test('the confirmation counts edited and checked results, and says what a new upload does', () => {
  expect(confirmBody(2, 0)).toContain('You have edited 2 structures.');
  expect(confirmBody(1, 0)).toContain('You have edited 1 structure.');
  expect(confirmBody(0, 3)).toContain('You have checked 3 structures.');
  expect(confirmBody(2, 3)).toContain('You have edited 2 structures and checked 3.');
  expect(confirmBody(0, 1)).toContain('You have checked 1 structure.');
  expect(confirmBody(1, 1)).toContain('clears');
});

test('a drag that something else on the page already handled is not touched (Ketcher\'s own drop zones)', () => {
  // react-dropzone in Ketcher's Open and Image dialogs has taken the drag: it prevented the default
  // and set dropEffect to "copy". The window listener runs after it and must not refuse the drop.
  for (const type of ['dragover', 'drop']) {
    const event = dragEvent(['Files'], type);
    (event as { defaultPrevented: boolean }).defaultPrevented = true;
    (event.dataTransfer as { dropEffect: string }).dropEffect = 'copy';
    event.preventDefault.mockClear();
    guardStrayDrop(event);
    expect(event.dataTransfer?.dropEffect, type).toBe('copy');
    expect(event.preventDefault, type).not.toHaveBeenCalled();
  }
});
