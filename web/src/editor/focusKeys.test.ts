import { expect, test, vi } from 'vitest';
import { keepShiftTabForFocus } from './focusKeys';

function press(key: string, modifiers: { shiftKey?: boolean; ctrlKey?: boolean; metaKey?: boolean; altKey?: boolean } = {}) {
  const event = { key, shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, ...modifiers, stopPropagation: vi.fn(), preventDefault: vi.fn() };
  keepShiftTabForFocus(event);
  return event;
}

test('Shift+Tab does not reach Ketcher, and is not cancelled, so the browser moves focus', () => {
  const event = press('Tab', { shiftKey: true });
  expect(event.stopPropagation).toHaveBeenCalledTimes(1);
  expect(event.preventDefault).not.toHaveBeenCalled();
});

test('every other key goes to Ketcher as before: Tab, Escape, letters and the shortcuts of the canvas', () => {
  for (const event of [
    press('Tab'),
    press('Escape'),
    press('Escape', { shiftKey: true }),
    press('a'),
    press('A', { shiftKey: true }),
    press('a', { ctrlKey: true }),
    press('l', { ctrlKey: true }),
    press('Delete'),
    press('Tab', { shiftKey: true, ctrlKey: true }), // the browser's "previous tab"
    press('Tab', { shiftKey: true, altKey: true }),
    press('Tab', { shiftKey: true, metaKey: true }),
  ]) {
    expect(event.stopPropagation).not.toHaveBeenCalled();
    expect(event.preventDefault).not.toHaveBeenCalled();
  }
});
