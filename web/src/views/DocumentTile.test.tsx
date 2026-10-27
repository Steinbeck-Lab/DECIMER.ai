import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { fixtureResults } from '../mocks/fixtures';
import { viewOf } from '../state/selectors';
import { DocumentTile } from './DocumentTile';

afterEach(cleanup);

const urls = (n: number) => Array.from({ length: n }, (_, i) => `/api/jobs/j/media/page-${i}.png`);
const views = (pages: number[]) =>
  fixtureResults(pages.length).map((result, i) => viewOf({ ...result, pageIndex: pages[i] ?? 0 }));

function show(props: Partial<Parameters<typeof DocumentTile>[0]> = {}) {
  render(
    <DocumentTile
      names={['paper.pdf']}
      pages={4}
      pageUrls={urls(4)}
      views={views([2, 2, 2, 0])}
      selectedPage={null}
      pdf
      {...props}
    />,
  );
}

test('the file name and the structures on each page, as labelled chips: "p. 2" over the count, "none" for a page without', () => {
  show();
  expect(screen.getByText('paper.pdf')).toBeTruthy();
  // A small heading above the strip names it.
  expect(screen.getByText('Structures per page').tagName).toBe('P');
  const pages = screen.getByRole('list', { name: 'Structures per page' }).querySelectorAll('li');
  expect([...pages].map((li) => li.querySelector('.dc-pages__no')?.textContent)).toEqual(['p. 1', 'p. 2', 'p. 3', 'p. 4']);
  expect([...pages].map((li) => li.querySelector('.dc-pages__n')?.textContent)).toEqual(['1', 'none', '3', 'none']);
  expect([...pages].map((li) => li.getAttribute('data-empty'))).toEqual(['false', 'true', 'false', 'true']);
});

test('a chip with structures is a button named by page and count; a page with none is not a button', () => {
  show({ onPageFilter: () => undefined });
  // Named by what it shows and the words that complete it (its name holds its visible text).
  const names = screen.getAllByRole('button', { name: /^p\. \d/ }).map((b) => b.textContent?.trim());
  expect(names).toEqual(['p. 1 1 structure', 'p. 3 3 structures']);
  // The empty pages are still said, in words, for a screen reader.
  const pages = screen.getByRole('list', { name: 'Structures per page' }).querySelectorAll('li');
  expect(pages[1]?.querySelector('button')).toBeNull();
  expect(pages[1]?.textContent).toContain('Page 2: no structures');
});

test('the page of the open structure is the current one: a tag, and its name says so', () => {
  show({ selectedPage: 2, onPageFilter: () => undefined });
  const pages = [...screen.getByRole('list', { name: 'Structures per page' }).querySelectorAll('li')];
  expect(pages.map((li) => li.getAttribute('data-current'))).toEqual(['false', 'false', 'true', 'false']);
  expect(screen.getByRole('button', { name: 'p. 3 3 structures, open in the editor' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'p. 1 1 structure' })).toBeTruthy();
});

test('pressing a chip chooses its page, pressing it again lets go; the chip of the chosen page is pressed', () => {
  const onPageFilter = vi.fn();
  show({ onPageFilter });
  const first = screen.getByRole('button', { name: 'p. 1 1 structure' });
  expect(first.getAttribute('aria-pressed')).toBe('false');
  fireEvent.click(first);
  expect(onPageFilter).toHaveBeenLastCalledWith(0);
  cleanup();
  show({ onPageFilter, pageFilter: 2 });
  const third = screen.getByRole('button', { name: 'p. 3 3 structures' });
  expect(third.getAttribute('aria-pressed')).toBe('true');
  expect(screen.getByRole('button', { name: 'p. 1 1 structure' }).getAttribute('aria-pressed')).toBe('false');
  fireEvent.click(third);
  expect(onPageFilter).toHaveBeenLastCalledWith(null);
});

test('a result on a page past the job\'s own count still has its place in the strip', () => {
  show({ pages: 2, views: views([0, 5]) });
  expect(screen.getByRole('list', { name: 'Structures per page' }).querySelectorAll('li')).toHaveLength(6);
});

test('an upload of images says "image" where a PDF says "page": the heading, the chips and their names', () => {
  show({ pdf: false, names: ['a.png', 'b.png', 'c.png', 'd.png'], pages: 4, onPageFilter: () => undefined });
  expect(screen.getByText('Structures per image').tagName).toBe('P');
  const list = screen.getByRole('list', { name: 'Structures per image' });
  expect([...list.querySelectorAll('.dc-pages__no')].map((el) => el.textContent)).toEqual(['img. 1', 'img. 2', 'img. 3', 'img. 4']);
  expect(screen.getAllByRole('button', { name: /^img\. \d/ }).map((b) => b.textContent?.trim())).toEqual(['img. 1 1 structure', 'img. 3 3 structures']);
  expect(list.querySelectorAll('li')[1]?.textContent).toContain('Image 2: no structures');
});

test('a PDF keeps the "Display uploaded document" toggle inside the tile; an image upload has none', () => {
  show();
  expect(screen.getByRole('button', { name: 'Display uploaded document' }).closest('.dc-doc__tile')).not.toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Display uploaded document' }));
  expect(screen.getAllByRole('img')).toHaveLength(4);
  cleanup();
  show({ pdf: false, names: ['a.png', 'b.png'] });
  expect(screen.queryByRole('button')).toBeNull();
  expect(screen.getByText('a.png and 1 more')).toBeTruthy();
});

test('an image pasted from the clipboard has no file name: the tile says so', () => {
  show({ names: [], pdf: false, pages: 1, views: views([0, 0]) });
  expect(screen.getByText('Pasted image')).toBeTruthy();
  // One page needs no strip.
  expect(screen.queryByRole('list', { name: 'Structures per page' })).toBeNull();
});
