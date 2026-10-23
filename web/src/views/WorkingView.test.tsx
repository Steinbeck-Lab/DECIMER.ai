import { MantineProvider } from '@mantine/core';
import { act, cleanup, render, screen } from '@testing-library/react';
import { memo } from 'react';
import { afterEach, expect, test } from 'vitest';
import { STAGE_ORDER, type Job, type StageState } from '../api/contract';
import { MockApiClient } from '../mocks/MockApiClient';
import type { AppController } from '../state/controller';
import type { SourceFile } from '../state/model';
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
  pageUrls: [],
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

function mountWorking(sources: SourceFile[] = [{ name: 'paper.pdf', size: 1, kind: 'pdf' }]) {
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
    c.store.dispatch({ type: 'submit', sources, at: Date.now() });
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

/** A running job whose four pipeline stages are in the given states (upload is done). */
const overlapped = (...states: StageState[]): Job =>
  midway({
    stages: STAGE_ORDER.map((name, i) => ({ name, state: i === 0 ? 'done' : (states[i - 1] ?? 'pending') })),
    structuresFound: 0,
  });

const segments = () => [...document.querySelectorAll('.dc-rule__segment')].map((el) => el.getAttribute('data-state'));

test('with the stages overlapping the heading names the latest active stage and the line below names the others', () => {
  const c = mountWorking();
  act(() => c.store.dispatch({ type: 'jobUpdated', job: overlapped('done', 'active', 'active') }));
  expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Recognising each structure');
  expect(screen.getByText('Step 4 of 5. Next: Checking the results.')).toBeTruthy();
  expect(screen.getByText('Also running: finding structures on each page.')).toBeTruthy();
  // both lines are in the announced region, together with the heading
  const live = screen.getByRole('status');
  expect(live.textContent).toContain('Also running: finding structures on each page.');
});

test('three stages at once are listed as a sentence, in pipeline order', () => {
  const c = mountWorking();
  act(() => c.store.dispatch({ type: 'jobUpdated', job: overlapped('active', 'active', 'active') }));
  expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Recognising each structure');
  expect(screen.getByText('Also running: reading the pages and finding structures on each page.')).toBeTruthy();
});

test('one active stage says nothing about others, and neither does a queued job', () => {
  const c = mountWorking();
  act(() => c.store.dispatch({ type: 'jobUpdated', job: overlapped('done', 'active') }));
  expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Finding structures on each page');
  expect(screen.getByText('Step 3 of 5. Next: Recognising each structure.')).toBeTruthy();
  expect(screen.queryByText(/Also running/)).toBeNull();
  act(() => c.store.dispatch({ type: 'jobUpdated', job: { ...overlapped('active', 'active'), status: 'queued', queuePosition: 2 } }));
  expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Waiting for a free worker');
  expect(screen.queryByText(/Also running/)).toBeNull();
});

test('the last stage running together with an earlier one says it is the last step', () => {
  const c = mountWorking();
  act(() => c.store.dispatch({ type: 'jobUpdated', job: overlapped('done', 'done', 'active', 'active') }));
  expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Checking the results');
  expect(screen.getByText('Step 5 of 5. This is the last step.')).toBeTruthy();
  expect(screen.getByText('Also running: recognising each structure.')).toBeTruthy();
});

test('the progress rule shows each stage in its own state, and its value and its text say the same thing', () => {
  const c = mountWorking();
  act(() => c.store.dispatch({ type: 'jobUpdated', job: overlapped('done', 'active', 'active') }));
  expect(segments()).toEqual(['done', 'done', 'active', 'active', 'pending']);
  const bar = screen.getByRole('progressbar', { name: 'Progress' });
  expect(bar.getAttribute('aria-valuenow')).toBe('2'); // upload and render are done
  expect(bar.getAttribute('aria-valuetext')).toBe('2 of 5 steps done'); // says what the value says
});

test('the page count is shown as long as the render stage is not done, also beside another stage', () => {
  const c = mountWorking();
  act(() => c.store.dispatch({ type: 'jobUpdated', job: { ...overlapped('active', 'active'), pages: 14, pagesProcessed: 10 } }));
  expect(screen.getByText('Page 10 of 10')).toBeTruthy();
  act(() => c.store.dispatch({ type: 'jobUpdated', job: { ...overlapped('done', 'active'), pages: 14, pagesProcessed: 10 } }));
  expect(screen.queryByText(/^Page \d+ of \d+$/)).toBeNull();
});

// The hint says what to expect for this upload, not "a 10-page PDF" for everything.
const note = () => document.querySelector('.dc-working__note')?.textContent;

test('the hint for a single pasted image is short, and says nothing of PDFs', () => {
  const c = mountWorking([{ name: 'pasted-image.png', size: 1, kind: 'image' }]);
  expect(note()).toBe('A single image usually takes only a few seconds.'); // before the job is even known
  act(() => c.store.dispatch({ type: 'jobUpdated', job: midway({ pages: 1, pagesProcessed: 1 }) }));
  expect(note()).toBe('A single image usually takes only a few seconds.');
  expect(note()).not.toMatch(/PDF/);
});

test('the hint follows the job: a one-page PDF is quick, a few pages take under a minute, ten can take a minute or more', () => {
  const c = mountWorking([{ name: 'paper.pdf', size: 1, kind: 'pdf' }]);
  act(() => c.store.dispatch({ type: 'jobUpdated', job: midway({ pages: 1, pagesProcessed: 1 }) }));
  expect(note()).toBe('A one-page PDF usually takes only a few seconds.');
  act(() => c.store.dispatch({ type: 'jobUpdated', job: midway({ pages: 3, pagesProcessed: 3 }) }));
  expect(note()).toBe('3 pages usually take under a minute.');
  act(() => c.store.dispatch({ type: 'jobUpdated', job: midway({ pages: 10, pagesProcessed: 10 }) }));
  expect(note()).toBe('10 pages can take a minute or more.');
  // Only the first 10 pages are processed.
  act(() => c.store.dispatch({ type: 'jobUpdated', job: midway({ pages: 14, pagesProcessed: 10 }) }));
  expect(note()).toBe('10 pages can take a minute or more.');
});

test('several images are counted as images; a PDF whose pages are not known yet promises nothing', () => {
  const images = mountWorking([
    { name: 'a.png', size: 1, kind: 'image' },
    { name: 'b.png', size: 1, kind: 'image' },
  ]);
  expect(note()).toBe('2 images usually take under a minute.');
  act(() => images.store.dispatch({ type: 'jobUpdated', job: midway({ pages: 2, pagesProcessed: 2 }) }));
  expect(note()).toBe('2 images usually take under a minute.');
  cleanup();
  mountWorking([{ name: 'Paper.PDF', size: 1, kind: 'pdf' }]);
  expect(note()).toBe('How long this takes depends on the number of pages.');
});

test('images are not capped at the PDF page limit: 12 images are 12, in the hint and in "Page x of y"', () => {
  const images = Array.from({ length: 12 }, (_, i): SourceFile => ({ name: `${i}.png`, size: 1, kind: 'image' }));
  const c = mountWorking(images);
  expect(note()).toBe('12 images can take a minute or more.');
  act(() => c.store.dispatch({ type: 'jobUpdated', job: { ...overlapped('active', 'active'), pages: 12, pagesProcessed: 12 } }));
  expect(note()).toBe('12 images can take a minute or more.');
  expect(screen.getByText('Page 12 of 12')).toBeTruthy();
});
