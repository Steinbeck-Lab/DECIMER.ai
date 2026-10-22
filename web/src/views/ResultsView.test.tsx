import { MantineProvider } from '@mantine/core';
import { act, cleanup, render } from '@testing-library/react';
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

async function mountResults() {
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
    await c.submit([new File(['x'], 'a.png', { type: 'image/png' })]);
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
