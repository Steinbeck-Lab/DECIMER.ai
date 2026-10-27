import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, test } from 'vitest';
import { asIncomplete, fixtureResults } from '../mocks/fixtures';
import { viewOf } from '../state/selectors';
import { IncompleteNote } from './EditorPanel';

afterEach(cleanup);

const [first] = fixtureResults(1);
if (!first) throw new Error('no fixture');

test('a structure the model cut off says so, as a note, in the reason\'s own words', () => {
  render(<IncompleteNote view={viewOf(asIncomplete(first))} />);
  const note = screen.getByRole('note');
  expect(note.textContent).toBe('Incomplete: the model stopped at its length limit; the structure is cut off.');
  expect(note.getAttribute('data-kind')).toBe('problem');
});

test('it stays while the structure is the model\'s (also once checked) and goes with the first edit', () => {
  const edit = { molfile: 'x', modified: false, checked: true, derived: null, validating: false, validationFailed: false };
  render(<IncompleteNote view={viewOf(asIncomplete(first), edit)} />);
  expect(screen.getByRole('note')).toBeTruthy(); // checked, not edited: still cut off
  cleanup();
  const { container } = render(<IncompleteNote view={viewOf(asIncomplete(first), { ...edit, modified: true })} />);
  expect(container.textContent).toBe('');
});

test('a structure the model finished has no note', () => {
  const { container } = render(<IncompleteNote view={viewOf(first)} />);
  expect(container.textContent).toBe('');
});
