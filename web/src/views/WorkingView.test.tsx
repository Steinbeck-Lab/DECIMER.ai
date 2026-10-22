import { MantineProvider } from '@mantine/core';
import { act, cleanup, render, screen } from '@testing-library/react';
import { memo } from 'react';
import { afterEach, expect, test } from 'vitest';
import { STAGE_ORDER, type Job } from '../api/contract';
import { MockApiClient } from '../mocks/MockApiClient';
import type { AppController } from '../state/controller';
import { AppProvider, useController } from '../state/react';
import { WorkingView } from './WorkingView';

afterEach(cleanup);

let controller: AppController | null = null;
const Capture = memo(function Capture() {
  controller = useController();
  return null;
});

const midway = (over: Partial<Job> = {}): Job => ({
  id: 'job-1',
  status: 'running',
  queuePosition: null,
  stages: STAGE_ORDER.map((name) => ({ name, state: name === 'check' ? 'active' : 'done' })),
  pages: 10,
  pagesProcessed: 10,
  truncated: false,
  structuresFound: 12,
  structuresDropped: 0,
  // The API fills `results` only when the job is done: until then a poll has the count and no results.
  results: [],
  warnings: [],
  error: null,
  createdAt: '2026-10-22T12:00:00.000Z',
  expiresAt: '2026-10-22T13:00:00.000Z',
  ...over,
});

function mountWorking() {
  render(
    <MantineProvider>
      <AppProvider api={new MockApiClient({ latencyMs: 0 })}>
        <Capture />
        <WorkingView />
      </AppProvider>
    </MantineProvider>,
  );
  const c = controller as AppController | null;
  if (!c) throw new Error('no controller');
  act(() => {
    c.store.dispatch({ type: 'submit', sources: [{ name: 'paper.pdf', size: 1 }], at: Date.now() });
    c.store.dispatch({ type: 'jobCreated', id: 'job-1' });
  });
  return c;
}

test('a poll with structuresFound and no results is a working view that says how many were found', () => {
  const c = mountWorking();
  act(() => c.store.dispatch({ type: 'jobUpdated', job: midway() }));
  expect(c.store.getState().phase).toBe('working');
  expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Checking the results');
  expect(screen.getByText('12 structures found')).toBeTruthy();
  expect(screen.queryByRole('list', { name: 'Recognised structures' })).toBeNull();
});

test('nothing is said about structures found before the pipeline has found any, and "1 structure" is singular', () => {
  const c = mountWorking();
  act(() => c.store.dispatch({ type: 'jobUpdated', job: midway({ structuresFound: 0 }) }));
  expect(screen.queryByText(/structures? found/)).toBeNull();
  act(() => c.store.dispatch({ type: 'jobUpdated', job: midway({ structuresFound: 1 }) }));
  expect(screen.getByText('1 structure found')).toBeTruthy();
});

test('the count is not part of the announced region (it would be read out on every poll)', () => {
  const c = mountWorking();
  act(() => c.store.dispatch({ type: 'jobUpdated', job: midway() }));
  const found = screen.getByText('12 structures found');
  expect(found.closest('[aria-live]')).toBeNull();
});
