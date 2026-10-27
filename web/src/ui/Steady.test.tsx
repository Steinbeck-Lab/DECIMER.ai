import { cleanup, render } from '@testing-library/react';
import { afterEach, expect, test } from 'vitest';
import { Steady } from './Steady';

afterEach(cleanup);

const SIZERS = ['One', 'A much longer text that wraps', 'One'];

test('the text is the only text in the document; the slot is sized by what can be said, which CSS writes', () => {
  const { container } = render(<Steady value="One" sizers={SIZERS} />);
  expect(container.textContent).toBe('One');
  // Each text once, however often it is listed.
  const sizers = [...container.querySelectorAll('.dc-steady__sizer')];
  expect(sizers.map((el) => el.getAttribute('data-text'))).toEqual(['One', 'A much longer text that wraps']);
  expect(sizers.every((el) => el.getAttribute('aria-hidden') === 'true' && el.textContent === '')).toBe(true);
});

test('the first text is simply there; a change brings the new text in beneath the one leaving, which is no longer text', () => {
  const { container, rerender } = render(<Steady value="One" sizers={SIZERS} />);
  expect(container.querySelector('.dc-steady__now--arriving')).toBeNull();
  expect(container.querySelector('.dc-steady__gone')).toBeNull();
  rerender(<Steady value="Two" sizers={SIZERS} />);
  expect(container.textContent).toBe('Two');
  expect(container.querySelector('.dc-steady__now--arriving')?.textContent).toBe('Two');
  const gone = container.querySelector('.dc-steady__gone');
  expect(gone?.getAttribute('data-text')).toBe('One');
  expect(gone?.getAttribute('aria-hidden')).toBe('true');
  expect(gone?.textContent).toBe('');
});

test('an empty text is a slot with nothing in it, and leaves nothing behind it', () => {
  const { container, rerender } = render(<Steady value="" sizers={SIZERS} />);
  expect(container.textContent).toBe('');
  rerender(<Steady value="One" sizers={SIZERS} />);
  expect(container.querySelector('.dc-steady__gone')).toBeNull();
  expect(container.textContent).toBe('One');
});
