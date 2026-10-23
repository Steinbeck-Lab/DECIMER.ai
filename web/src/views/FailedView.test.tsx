import { MantineProvider } from '@mantine/core';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { memo } from 'react';
import { afterEach, expect, test, vi } from 'vitest';
import { MockApiClient } from '../mocks/MockApiClient';
import type { AppController } from '../state/controller';
import { AppProvider, useController } from '../state/react';
import { FailedView } from './FailedView';

afterEach(cleanup);

let controller: AppController | null = null;
const Capture = memo(function Capture() {
  controller = useController();
  return null;
});

/** The failed screen, reached the way the app reaches it: a job that was running, then the end. */
function mountFailed(end: Parameters<AppController['store']['dispatch']>[0]) {
  render(
    <MantineProvider>
      <AppProvider api={new MockApiClient({ latencyMs: 0 })}>
        <Capture />
        <FailedView />
      </AppProvider>
    </MantineProvider>,
  );
  const c = controller as AppController | null;
  if (!c) throw new Error('no controller');
  act(() => {
    c.store.dispatch({ type: 'submit', sources: [{ name: 'paper.pdf', size: 1, kind: 'pdf' }], at: Date.now() });
    c.store.dispatch({ type: 'jobCreated', id: 'job-1' });
    c.store.dispatch(end);
  });
  return c;
}

test('a lost connection offers "Try again", which picks the same job up again, and "Start over"', () => {
  const c = mountFailed({ type: 'connectionLost' });
  const resume = vi.spyOn(c, 'resume').mockImplementation(() => {});
  expect(screen.getByRole('alert').textContent).toContain('The connection to the server was lost');
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
  expect(resume).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('button', { name: 'Start over' })).toBeTruthy();
});

test('"Try again" on the real controller returns to the working screen on the same job', () => {
  const c = mountFailed({ type: 'connectionLost' });
  vi.spyOn(MockApiClient.prototype, 'getJob').mockReturnValue(new Promise(() => {}));
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
  expect(c.store.getState()).toMatchObject({ phase: 'working', jobId: 'job-1', failure: null });
  vi.restoreAllMocks();
});

test('a job the server reports as failed offers only "Start over": trying the same job again would end the same way', () => {
  mountFailed({
    type: 'jobUpdated',
    job: {
      id: 'job-1', status: 'failed', queuePosition: null, stages: [], pages: 0, pagesProcessed: 0, pageUrls: [], truncated: false,
      structuresFound: 0, structuresDropped: 0, results: [], warnings: [], error: 'boom',
      createdAt: '2026-10-22T12:00:00.000Z', expiresAt: '2026-10-22T13:00:00.000Z',
    },
  });
  expect(screen.getByRole('alert').textContent).toBe('boom');
  expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
  expect(screen.getByRole('button', { name: 'Start over' })).toBeTruthy();
});

test('an expired job says so and offers only "Start over"', () => {
  mountFailed({ type: 'jobExpired' });
  expect(screen.getByRole('alert').textContent).toContain('no longer');
  expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
  expect(screen.getByRole('button', { name: 'Start over' })).toBeTruthy();
});
