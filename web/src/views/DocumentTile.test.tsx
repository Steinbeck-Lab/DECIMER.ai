import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test } from 'vitest';
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

test('the file name and the structures on each page; pages with none are marked empty', () => {
  show();
  expect(screen.getByText('paper.pdf')).toBeTruthy();
  const pages = screen.getByRole('list', { name: 'Structures per page' }).querySelectorAll('li');
  expect([...pages].map((li) => li.textContent)).toEqual([
    '11Page 1: 1 structure',
    '2Page 2: no structures',
    '33Page 3: 3 structures',
    '4Page 4: no structures',
  ]);
  expect([...pages].map((li) => li.getAttribute('data-empty'))).toEqual(['false', 'true', 'false', 'true']);
});

test('the page of the open structure is the current one', () => {
  show({ selectedPage: 2 });
  const pages = [...screen.getByRole('list', { name: 'Structures per page' }).querySelectorAll('li')];
  expect(pages.map((li) => li.getAttribute('data-current'))).toEqual(['false', 'false', 'true', 'false']);
});

test('a result on a page past the job\'s own count still has its place in the strip', () => {
  show({ pages: 2, views: views([0, 5]) });
  expect(screen.getByRole('list', { name: 'Structures per page' }).querySelectorAll('li')).toHaveLength(6);
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
