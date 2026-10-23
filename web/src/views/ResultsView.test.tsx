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
