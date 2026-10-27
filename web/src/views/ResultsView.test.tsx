import { MantineProvider } from '@mantine/core';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { memo } from 'react';
import { afterEach, expect, test, vi } from 'vitest';
import { MockApiClient } from '../mocks/MockApiClient';
import { AppProvider, useController } from '../state/react';
import type { AppController } from '../state/controller';
import { ResultsView } from './ResultsView';

// Tile is replaced by a memoised stand-in that counts its renders, so the test sees exactly what memo lets through.
const renders = vi.hoisted(() => new Map<number, number>());
vi.mock('./Tile', async () => {
  const { memo: memoise } = await import('react');
  return {
    Tile: memoise(function Tile(props: { view: { index: number } }) {
      renders.set(props.view.index, (renders.get(props.view.index) ?? 0) + 1);
      return <li data-tile={props.view.index} />;
    }),
  };
});

afterEach(() => {
  cleanup();
  renders.clear();
});

let controller: AppController | null = null;
const Capture = memo(function Capture() {
  controller = useController();
  return null;
});

async function mountResults(file = new File(['x'], 'a.png', { type: 'image/png' })) {
  render(
    <MantineProvider>
      <AppProvider api={new MockApiClient({ stepMs: 5, latencyMs: 0 })}>
        <Capture />
        <ResultsView />
      </AppProvider>
    </MantineProvider>,
  );
  const c = controller as AppController | null;
  if (!c) throw new Error('no controller');
  await act(async () => {
    await c.submit([file]);
  });
  await vi.waitFor(() => expect(c.store.getState().phase).toBe('results'), { timeout: 3000 });
  await act(async () => {});
  return c;
}

const total = () => [...renders.values()].reduce((a, b) => a + b, 0);

test('a notice, or any change that does not touch a tile, does not re-render the tiles', async () => {
  const c = await mountResults();
  expect(renders.size).toBe(8);
  const before = total();

  act(() => c.showNotice('info', 'Your download has started.'));
  act(() => c.showNotice('error', 'Something else'));
  act(() => c.setFilter('all')); // already "all"
  expect(total()).toBe(before);
});

test('selecting a structure re-renders only the one that is now selected and the one that was', async () => {
  const c = await mountResults();
  act(() => c.select(2));
  const afterFirst = new Map(renders);
  const changed = [...afterFirst].filter(([, n]) => n > 1).map(([i]) => i);
  expect(changed).toEqual([2]);

  act(() => c.select(5));
  const changedNow = [...renders].filter(([i, n]) => n > (afterFirst.get(i) ?? 0)).map(([i]) => i).sort();
  expect(changedNow).toEqual([2, 5]);
});

test('an edit re-renders the tile that was edited and no other', async () => {
  const c = await mountResults();
  const before = new Map(renders);
  act(() => c.edit(3, '\n  Ketcher\n\n  0  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n'));
  const changed = [...renders].filter(([i, n]) => n > (before.get(i) ?? 0)).map(([i]) => i);
  expect(changed).toEqual([3]);
  c.dispose();
});

const pdf = () => new File(['x'], 'paper.pdf', { type: 'application/pdf' });

test('a PDF job offers the uploaded document, shut at first, with a page for every processed page', async () => {
  await mountResults(pdf());
  const toggle = screen.getByRole('button', { name: 'Display uploaded document' });
  expect(toggle.getAttribute('aria-expanded')).toBe('false');
  expect(document.querySelectorAll('.dc-doc img')).toHaveLength(0);
  fireEvent.click(toggle);
  // The mock's PDF has 10 pages.
  expect(screen.getAllByRole('img').map((img) => img.getAttribute('alt'))).toEqual(
    Array.from({ length: 10 }, (_, i) => `Page ${i + 1} of 10`),
  );
});

test('an image job has no uploaded-document toggle (legacy shows it for PDFs only), although the job lists its page', async () => {
  const c = await mountResults();
  expect(c.store.getState().job?.pageUrls).toHaveLength(1);
  expect(screen.queryByRole('button', { name: 'Display uploaded document' })).toBeNull();
});

test('the job notices are the second half of the green box: a note under the counts, in the top row, and nowhere else', async () => {
  await mountResults(new File(['x'], 'truncated.pdf', { type: 'application/pdf' }));
  const note = screen.getByRole('note');
  expect(screen.getAllByRole('note')).toHaveLength(1); // not repeated below the grid
  const figure = note.closest('.dc-figure')!;
  expect(note.textContent).toContain('3 more structures were found');
  expect(note.querySelectorAll('p').length).toBeGreaterThanOrEqual(2);
  const left = note.parentElement!;
  expect(left.classList.contains('dc-top__left')).toBe(true);
  expect(left.parentElement?.classList.contains('dc-top')).toBe(true);
  const stats = left.querySelector('.dc-stats')!;
  const follows = (a: Element, b: Element) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
  expect(follows(stats, note)).toBe(true);
  // The document tile is the other column of the row; the filters and the grid come after the whole row.
  expect(follows(note, figure.querySelector('.dc-doc__tile')!)).toBe(true);
  expect(follows(note, figure.querySelector('.dc-filters')!)).toBe(true);
  expect(follows(figure.querySelector('.dc-grid')!, note)).toBe(false);
  expect(note.classList.contains('dc-notices')).toBe(true);
});

test('a page chip narrows the results to its page: a pill in the filter row says so, "All" lets go of it, and the chip is pressed meanwhile', async () => {
  const c = await mountResults(new File(['x'], 'paper.pdf', { type: 'application/pdf' }));
  const chip = screen.getAllByRole('button', { name: /^p\. \d+ \d+ structures?/ })[0]!;
  const all = screen.getByRole('button', { name: /^All/ });
  expect(chip.getAttribute('aria-pressed')).toBe('false');
  expect(all.getAttribute('aria-pressed')).toBe('true');
  expect(screen.queryByRole('button', { name: /only\. Show every page$/ })).toBeNull();

  fireEvent.click(chip);
  expect(c.store.getState().pageFilter).not.toBeNull();
  expect(chip.getAttribute('aria-pressed')).toBe('true');
  expect(all.getAttribute('aria-pressed')).toBe('false');
  const pill = screen.getByRole('button', { name: /^Page \d+ only\. Show every page$/ });
  // A plain action (it only clears the page), so it is not a toggle either.
  expect(pill.hasAttribute('aria-pressed')).toBe(false);
  expect(pill.textContent).toMatch(/^Page \d+×$/);
  // "All" counts every page, whichever is chosen; "Needs review" counts the chosen page.
  expect(all.textContent).toMatch(/All\s*8/);
  // The status filters combine with it: the page stays while "Needs review" is chosen.
  fireEvent.click(screen.getByRole('button', { name: /^Needs review/ }));
  expect(c.store.getState()).toMatchObject({ filter: 'review' });
  expect(c.store.getState().pageFilter).not.toBeNull();
  // The pill lets go of the page and keeps the status filter; the focus does not fall to the page: it goes to the page's chip.
  fireEvent.click(pill);
  expect(c.store.getState()).toMatchObject({ filter: 'review', pageFilter: null });
  await vi.waitFor(() => expect(document.activeElement).toBe(chip));
  fireEvent.click(chip);
  fireEvent.click(screen.getByRole('button', { name: /^All/ }));
  expect(c.store.getState()).toMatchObject({ filter: 'all', pageFilter: null });
  expect(screen.queryByRole('button', { name: /only\. Show every page$/ })).toBeNull();
});

test('with no warnings there is no second tile: the counts have the whole left column', async () => {
  await mountResults();
  expect(screen.queryByRole('note')).toBeNull();
  const left = document.querySelector('.dc-top__left')!;
  expect(left.children).toHaveLength(1);
  expect(left.firstElementChild?.classList.contains('dc-stats')).toBe(true);
});
