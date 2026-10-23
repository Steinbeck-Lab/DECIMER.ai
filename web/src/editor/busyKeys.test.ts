import { expect, test, vi } from 'vitest';
import { blockKeysWhileBusy } from './busyKeys';

function press(key: string, modifiers: { ctrlKey?: boolean; metaKey?: boolean; altKey?: boolean } = {}) {
  const event = { key, ctrlKey: false, metaKey: false, altKey: false, ...modifiers, stopPropagation: vi.fn(), preventDefault: vi.fn() };
  blockKeysWhileBusy(event);
  return event;
}

test('Ketcher does not get a typed key, Delete or a shortcut of its own while the canvas is busy', () => {
  for (const event of [press('a'), press('Delete'), press('Backspace'), press('a', { ctrlKey: true }), press('l', { ctrlKey: true }), press('z', { metaKey: true })]) {
    expect(event.stopPropagation).toHaveBeenCalledTimes(1);
  }
});

test('typing is stopped altogether, a shortcut of the browser still does what it does', () => {
  expect(press('x').preventDefault).toHaveBeenCalledTimes(1);
  expect(press('r', { ctrlKey: true }).preventDefault).not.toHaveBeenCalled();
  expect(press('Enter').preventDefault).toHaveBeenCalledTimes(1);
});

test('Tab, Shift+Tab and Escape get through, so focus can leave the canvas and the panel can be closed', () => {
  for (const event of [press('Tab'), press('Escape')]) {
    expect(event.stopPropagation).not.toHaveBeenCalled();
    expect(event.preventDefault).not.toHaveBeenCalled();
  }
});
