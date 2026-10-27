import { MantineProvider } from '@mantine/core';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { memo } from 'react';
import { afterEach, expect, test, vi } from 'vitest';
import { STAGE_ORDER, type Job, type StageState } from '../api/contract';
import { MockApiClient } from '../mocks/MockApiClient';
import type { AppController } from '../state/controller';
import type { SourceFile } from '../state/model';
import { AppProvider, useController } from '../state/react';
import { strings } from '../strings';
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
  expect(screen.getByText('10 of 10 pages read')).toBeTruthy();
  act(() => c.store.dispatch({ type: 'jobUpdated', job: { ...overlapped('done', 'active'), pages: 14, pagesProcessed: 10 } }));
  expect(screen.queryByText(/^\d+ of \d+ (pages?|images?) read$/)).toBeNull();
});

test('the page count reads as progress, with the noun of the upload', () => {
  expect(strings.working.pages(0, 10, true)).toBe('0 of 10 pages read');
  expect(strings.working.pages(1, 1, true)).toBe('1 of 1 page read');
  expect(strings.working.pages(3, 12, false)).toBe('3 of 12 images read');
  expect(strings.working.pages(1, 1, false)).toBe('1 of 1 image read');
});

// The hint says what to expect for this upload, not "a 10-page PDF" for everything.
const note = () => document.querySelector('.dc-run__note')?.textContent;

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

test('images are not capped at the PDF page limit: 12 images are 12, in the hint and in "x of y images read"', () => {
  const images = Array.from({ length: 12 }, (_, i): SourceFile => ({ name: `${i}.png`, size: 1, kind: 'image' }));
  const c = mountWorking(images);
  expect(note()).toBe('12 images can take a minute or more.');
  act(() => c.store.dispatch({ type: 'jobUpdated', job: { ...overlapped('active', 'active'), pages: 12, pagesProcessed: 12 } }));
  expect(note()).toBe('12 images can take a minute or more.');
  expect(screen.getByText('12 of 12 images read')).toBeTruthy();
});

// What was uploaded sits in the inspection frame, whose label says the stage (decoration: the heading says it in words).
const frameLabel = () => document.querySelector('.dc-frame__label');

test('the frame names the file and labels the stage, "In queue" while the job waits; the frame is hidden from assistive technology', () => {
  const c = mountWorking([{ name: 'paper.pdf', size: 2_400_000, kind: 'pdf' }]);
  expect(screen.getByText('paper.pdf')).toBeTruthy();
  expect(screen.getByText('2.3 MB')).toBeTruthy();
  expect(frameLabel()?.textContent).toBe('Uploading');
  act(() => c.store.dispatch({ type: 'jobUpdated', job: overlapped('done', 'active') }));
  expect(frameLabel()?.textContent).toBe('Finding structures');
  expect(frameLabel()?.closest('[aria-hidden="true"]')).not.toBeNull();
  act(() => c.store.dispatch({ type: 'jobUpdated', job: { ...overlapped('active'), status: 'queued', queuePosition: 2 } }));
  expect(frameLabel()?.textContent).toBe('In queue');
  expect(document.querySelector('.dc-specimen')?.getAttribute('data-stopped')).toBe('false');
});

test('three files are named and the rest are counted, in words for a screen reader', () => {
  const files = Array.from({ length: 5 }, (_, i): SourceFile => ({ name: `${i}.png`, size: 1, kind: 'image' }));
  mountWorking(files);
  expect(document.querySelectorAll('.dc-specimen__item:not(.dc-specimen__item--more)')).toHaveLength(3);
  expect(screen.getByText('+2').getAttribute('aria-hidden')).toBe('true');
  expect(screen.getByText('2 more files')).toBeTruthy();
});

test('the stage names under the measuring line are for the eye: the progress bar and the heading say the same in words', () => {
  const c = mountWorking();
  act(() => c.store.dispatch({ type: 'jobUpdated', job: overlapped('done', 'active') }));
  const names = [...document.querySelectorAll('.dc-rule__name')];
  expect(names.map((el) => el.textContent)).toEqual(['Uploading', 'Reading pages', 'Finding structures', 'Recognising', 'Checking']);
  expect(names.every((el) => el.getAttribute('aria-hidden') === 'true')).toBe(true);
});

test('a queued job shows what the API says of its stages (the upload is done, the rest wait), and the bar says the same', () => {
  const c = mountWorking();
  act(() => c.store.dispatch({ type: 'jobUpdated', job: { ...overlapped(), status: 'queued', queuePosition: 2 } }));
  expect(segments()).toEqual(['done', 'pending', 'pending', 'pending', 'pending']);
  expect(screen.getByRole('progressbar', { name: 'Progress' }).getAttribute('aria-valuenow')).toBe('1');
});

// The structure that draws itself beside the file: one fragment per stage, in the state of its stage.
const fragmentStates = () => [...document.querySelectorAll('.dc-mol__frag')].map((el) => el.getAttribute('data-state'));

test('the structure is drawn one fragment per stage: before the job it starts, in the queue it has what is done, then it follows the heading', () => {
  const c = mountWorking();
  expect(fragmentStates()).toEqual(['active', 'pending', 'pending', 'pending', 'pending']);
  act(() => c.store.dispatch({ type: 'jobUpdated', job: { ...overlapped(), status: 'queued', queuePosition: 2 } }));
  expect(fragmentStates()).toEqual(['done', 'pending', 'pending', 'pending', 'pending']);
  act(() => c.store.dispatch({ type: 'jobUpdated', job: overlapped('active', 'pending') }));
  expect(fragmentStates()).toEqual(['done', 'active', 'pending', 'pending', 'pending']);
  // Stages overlap: the heading's stage is the one being drawn, the ones before it are done.
  act(() => c.store.dispatch({ type: 'jobUpdated', job: overlapped('active', 'active', 'active') }));
  expect(fragmentStates()).toEqual(['done', 'done', 'done', 'active', 'pending']);
  act(() => c.store.dispatch({ type: 'jobUpdated', job: overlapped('done', 'done', 'done', 'active') }));
  expect(fragmentStates()).toEqual(['done', 'done', 'done', 'done', 'active']);
});

test('the structure is decoration: hidden from assistive technology, with no text of its own for a screen reader to find', () => {
  mountWorking();
  const plate = document.querySelector('.dc-plate');
  expect(plate?.getAttribute('aria-hidden')).toBe('true');
  expect(plate?.closest('[aria-live]')).toBeNull();
});

// The calliper over the measuring line: where it stands and what it reads, from the job's own data only.
const readout = () => document.querySelector('.dc-cal__chip')?.textContent;
const calliperSpan = () => {
  const el = document.querySelector<HTMLElement>('.dc-cal');
  return [el?.style.getPropertyValue('--a'), el?.style.getPropertyValue('--b')];
};

test('the calliper stands across the stages that are running, closed in the queue', () => {
  const c = mountWorking();
  expect(calliperSpan()).toEqual(['var(--x0)', 'var(--x1)']); // the upload, before the job exists
  act(() => c.store.dispatch({ type: 'jobUpdated', job: { ...overlapped(), status: 'queued', queuePosition: 2 } }));
  expect(calliperSpan()).toEqual(['var(--x1)', 'var(--x1)']); // waiting after the upload
  act(() => c.store.dispatch({ type: 'jobUpdated', job: overlapped('active', 'pending') }));
  expect(calliperSpan()).toEqual(['var(--x1)', 'var(--x2)']);
  act(() => c.store.dispatch({ type: 'jobUpdated', job: overlapped('done', 'active', 'active') }));
  expect(calliperSpan()).toEqual(['var(--x2)', 'var(--x4)']);
  act(() => c.store.dispatch({ type: 'jobUpdated', job: overlapped('done', 'done', 'done', 'active') }));
  expect(calliperSpan()).toEqual(['var(--x4)', 'var(--x5)']);
});

test('the calliper reads the queue position, the pages, the structures found, or the stage\'s name: only what the job knows', () => {
  const c = mountWorking();
  expect(readout()).toBe('Uploading'); // nothing to count yet (the clock is below, as "Elapsed")
  act(() => c.store.dispatch({ type: 'jobUpdated', job: { ...overlapped(), status: 'queued', queuePosition: 2 } }));
  expect(readout()).toBe('queue 2');
  act(() => c.store.dispatch({ type: 'jobUpdated', job: { ...overlapped('active', 'pending'), pages: 14, pagesProcessed: 10 } }));
  expect(readout()).toBe('10 pages'); // a PDF's first 10
  act(() => c.store.dispatch({ type: 'jobUpdated', job: { ...overlapped('done', 'done', 'active'), pages: 14, pagesProcessed: 10 } }));
  expect(readout()).toBe('10 pages'); // recognising, but the number of structures is not known yet
  act(() => c.store.dispatch({ type: 'jobUpdated', job: { ...overlapped('done', 'done', 'active'), structuresFound: 12 } }));
  expect(readout()).toBe('12 structures');
  act(() => c.store.dispatch({ type: 'jobUpdated', job: { ...overlapped('done', 'done', 'done', 'active'), structuresFound: 1 } }));
  expect(readout()).toBe('1 structure');
  // Finding structures is not given a count of its own (the job has none): it reads the pages it searches.
  act(() => c.store.dispatch({ type: 'jobUpdated', job: { ...overlapped('done', 'active'), structuresFound: 0, pages: 3, pagesProcessed: 3 } }));
  expect(readout()).toBe('3 pages');
  // A job whose pages are not known (yet) has no count to read either: the stage's name.
  act(() => c.store.dispatch({ type: 'jobUpdated', job: { ...overlapped('done', 'active'), structuresFound: 0, pages: 0, pagesProcessed: 0 } }));
  expect(readout()).toBe('Finding structures');
});

test('the calliper counts images as images, and its readout is not in the announced region or the accessibility tree', () => {
  const c = mountWorking([
    { name: 'a.png', size: 1, kind: 'image' },
    { name: 'b.png', size: 1, kind: 'image' },
  ]);
  act(() => c.store.dispatch({ type: 'jobUpdated', job: { ...overlapped('active', 'pending'), pages: 2, pagesProcessed: 2 } }));
  expect(readout()).toBe('2 images');
  const calliper = document.querySelector('.dc-cal');
  expect(calliper?.getAttribute('aria-hidden')).toBe('true');
  expect(calliper?.closest('[aria-live]')).toBeNull();
  expect(screen.getByRole('status').textContent).not.toContain('2 images');
});

test('the announced region says only the heading and the two lines, never the text a slot only reserves room for', () => {
  const c = mountWorking();
  act(() => c.store.dispatch({ type: 'jobUpdated', job: overlapped('done', 'active', 'active') }));
  expect(screen.getByRole('status').textContent).toBe(
    'Recognising each structure' + 'Step 4 of 5. Next: Checking the results.' + 'Also running: finding structures on each page.',
  );
  // Each text is in the document once (the slots' other texts and the one that just left are drawn by CSS).
  expect(screen.getAllByText('Recognising each structure')).toHaveLength(1);
  act(() => c.store.dispatch({ type: 'jobUpdated', job: overlapped('done', 'done', 'done', 'active') }));
  expect(screen.getAllByText('Checking the results')).toHaveLength(1);
  expect(screen.queryByText('Recognising each structure')).toBeNull();
  expect(screen.getByRole('status').textContent).toBe('Checking the results' + 'Step 5 of 5. This is the last step.');
});

test('every line keeps its slot, empty or not, and the slot is sized for the longest text it can say', () => {
  const c = mountWorking();
  act(() => c.store.dispatch({ type: 'jobUpdated', job: overlapped('done', 'active') }));
  // No "Also running" line, yet the quiet line is there, with the room both of its texts need.
  const quiet = document.querySelector('.dc-run__detail--quiet');
  expect(quiet).not.toBeNull();
  expect(quiet?.textContent).toBe('');
  const sizers = [...(quiet?.querySelectorAll('.dc-steady__sizer') ?? [])].map((el) => el.getAttribute('data-text'));
  expect(sizers).toContain('Your job starts as soon as a worker is free. You can leave this tab open.');
  expect(sizers).toContain('Also running: reading the pages, finding structures on each page, and recognising each structure.');
  // The heading's slot holds all six headings, the frame's label all six labels.
  const heading = [...document.querySelectorAll('.dc-run__title .dc-steady__sizer')].map((el) => el.getAttribute('data-text'));
  expect(heading).toHaveLength(6);
  expect(heading).toContain('Finding structures on each page');
  expect(document.querySelectorAll('.dc-frame__label .dc-steady__sizer')).toHaveLength(6);
});

test('the clock and the counts have places of their own: the counts come and go and the clock stays where it is', () => {
  const c = mountWorking();
  const clock = () => document.querySelector('.dc-run__clock')?.textContent;
  expect(clock()).toBe('Elapsed 0:00');
  expect(document.querySelector('.dc-run__count')?.textContent).toBe('');
  act(() => c.store.dispatch({ type: 'jobUpdated', job: { ...overlapped('active', 'pending'), pages: 14, pagesProcessed: 4 } }));
  expect(document.querySelector('.dc-run__count')?.textContent).toBe('4 of 10 pages read');
  expect(clock()).toBe('Elapsed 0:00');
});

// The loops of the measuring line: in step, and paused with the tab.
const fills = () => [...document.querySelectorAll<HTMLElement>('[data-state="active"] > .dc-rule__fill')];

test('segments that run together are drawn in step: the same delay on the document clock, set through CSS and not the animation API', () => {
  const c = mountWorking();
  act(() => c.store.dispatch({ type: 'jobUpdated', job: overlapped('active', 'pending') }));
  expect(fills()).toHaveLength(1);
  const first = fills()[0]?.style.animationDelay;
  expect(first).toMatch(/^-?\d+(\.\d+)?ms$/);
  act(() => c.store.dispatch({ type: 'jobUpdated', job: overlapped('active', 'active') }));
  const delays = fills().map((el) => el.style.animationDelay);
  expect(delays).toHaveLength(2);
  expect(new Set(delays).size).toBe(1);
});

test('a hidden tab pauses the loops under the view, and a tab that is back lets them go on, drawn together again', () => {
  const c = mountWorking();
  act(() => c.store.dispatch({ type: 'jobUpdated', job: overlapped('active', 'active') }));
  const main = document.querySelector('main');
  expect(main?.hasAttribute('data-paused')).toBe(false);
  const before = fills();
  const hide = (hidden: boolean) =>
    act(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
      document.dispatchEvent(new Event('visibilitychange'));
    });
  hide(true);
  expect(main?.hasAttribute('data-paused')).toBe(true);
  expect(fills()).toEqual(before); // nothing is remade while it is paused
  hide(false);
  expect(main?.hasAttribute('data-paused')).toBe(false);
  // The drawings begin again, all of them at once (new elements, with one delay between them).
  const after = fills();
  expect(after).toHaveLength(2);
  expect(after.some((el) => before.includes(el))).toBe(false);
  expect(new Set(after.map((el) => el.style.animationDelay)).size).toBe(1);
  delete (document as { hidden?: boolean }).hidden;
});

// "Display uploaded document": the upload being processed, under the working block, on request.
const toggle = () => screen.getByRole('button', { name: /uploaded document$/ });
const panel = () => document.querySelector<HTMLElement>('.dc-workdoc')!;
const withPages = (job: Job, n: number, over: Partial<Job> = {}): Job => ({
  ...job,
  pages: n,
  pagesProcessed: n,
  pageUrls: Array.from({ length: n }, (_, i) => `/api/jobs/job-1/media/page-${i}.png`),
  ...over,
});

test('the document is shut by default: a toggle that says what it controls, and no picture asked for', () => {
  const c = mountWorking();
  act(() => c.store.dispatch({ type: 'jobUpdated', job: withPages(overlapped('active', 'pending'), 3) }));
  const button = toggle();
  expect(button.textContent).toBe('Display uploaded document');
  expect(button.getAttribute('aria-expanded')).toBe('false');
  const controlled = document.getElementById(button.getAttribute('aria-controls')!);
  expect(controlled).not.toBeNull();
  expect(panel().hidden).toBe(true);
  expect(panel().contains(controlled)).toBe(true);
  // Lazy: shut, there is not even an <img> to be fetched, with three pages listed.
  expect(panel().querySelectorAll('img')).toHaveLength(0);
  // Neither the toggle nor the document is in the announced region, and the heading is as it was.
  expect(button.closest('[aria-live]')).toBeNull();
  expect(panel().closest('[aria-live]')).toBeNull();
  expect(screen.getByRole('status').textContent).not.toMatch(/document/i);
});

test('opened, it shows the pages of a PDF as the job lists them, lazily, and shut again it takes them away', () => {
  const c = mountWorking();
  act(() => c.store.dispatch({ type: 'jobUpdated', job: withPages(overlapped('active', 'pending'), 3, { pages: 14 }) }));
  fireEvent.click(toggle());
  expect(toggle().textContent).toBe('Hide uploaded document');
  expect(toggle().getAttribute('aria-expanded')).toBe('true');
  expect(panel().hidden).toBe(false);
  const images = [...panel().querySelectorAll('img')];
  expect(images.map((img) => img.getAttribute('src'))).toEqual([0, 1, 2].map((i) => `/api/jobs/job-1/media/page-${i}.png`));
  expect(images.every((img) => img.getAttribute('loading') === 'lazy')).toBe(true);
  expect(images.map((img) => img.getAttribute('alt'))).toEqual(['Page 1 of 3', 'Page 2 of 3', 'Page 3 of 3']);
  // A box that scrolls can be reached with the keyboard, and says what it holds.
  const region = screen.getByRole('region', { name: /^Uploaded document, 3 pages/ });
  expect(region.getAttribute('tabindex')).toBe('0');
  fireEvent.click(toggle());
  expect(panel().hidden).toBe(true);
  expect(panel().querySelectorAll('img')).toHaveLength(0);
});

test('the document opens right under the toggle, inside the working block, whose content is pinned in its place meanwhile', () => {
  mountWorking();
  const main = document.querySelector('main')!;
  // Inside the block (so it starts a line under the toggle, not under the room the block leaves), after the toggle.
  expect(main.contains(panel())).toBe(true);
  expect(toggle().compareDocumentPosition(panel()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(main.style.justifyContent).toBe('');
  fireEvent.click(toggle());
  // The block is centred in what the window leaves: while the panel is open its content keeps the place it had.
  expect(main.style.justifyContent).toBe('flex-start');
  expect(main.style.paddingTop).not.toBe('');
  fireEvent.click(toggle());
  expect(main.style.justifyContent).toBe('');
  expect(main.style.paddingTop).toBe('');
});

test('each page has its number under it, and the pictures keep their names', () => {
  const c = mountWorking();
  act(() => c.store.dispatch({ type: 'jobUpdated', job: withPages(overlapped('active', 'pending'), 3) }));
  fireEvent.click(toggle());
  const captions = [...panel().querySelectorAll('.dc-workdoc__no')];
  expect(captions.map((el) => el.textContent)).toEqual(['Page 1 of 3', 'Page 2 of 3', 'Page 3 of 3']);
  expect(captions.every((el) => el.getAttribute('aria-hidden') === 'true')).toBe(true);
  expect([...panel().querySelectorAll('img')].map((img) => img.getAttribute('alt'))).toEqual(['Page 1 of 3', 'Page 2 of 3', 'Page 3 of 3']);
});

test('a page the worker has not rendered yet is a calm placeholder, asked for again until the pages are read', () => {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
  try {
    const c = mountWorking();
    act(() => c.store.dispatch({ type: 'jobUpdated', job: withPages(overlapped('active', 'pending'), 2) }));
    fireEvent.click(toggle());
    // The first page answers 404.
    fireEvent.error(panel().querySelectorAll('img')[0]!);
    expect(screen.getByText('Page 1 · not read yet')).toBeTruthy();
    expect(panel().querySelectorAll('img')).toHaveLength(1); // the other page is there
    // A few seconds on, it is asked for again (a new <img>).
    act(() => void vi.advanceTimersByTime(3100));
    expect(screen.queryByText('Page 1 · not read yet')).toBeNull();
    expect(panel().querySelectorAll('img')).toHaveLength(2);
    // Once the pages have been read, a page that is still missing will not come: it says so.
    fireEvent.error(panel().querySelectorAll('img')[0]!);
    act(() => c.store.dispatch({ type: 'jobUpdated', job: withPages(overlapped('done', 'active'), 2) }));
    fireEvent.error(panel().querySelectorAll('img')[0]!);
    expect(screen.getByText('Page 1 could not be shown.')).toBeTruthy();
    expect(screen.queryByText(/not read yet/)).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});

test('a PDF whose pages the job does not list yet says that they appear as they are read', () => {
  mountWorking();
  fireEvent.click(toggle());
  expect(panel().textContent).toContain('The pages appear here as they are read.');
  expect(panel().querySelectorAll('img')).toHaveLength(0);
});

test('images are shown from what the browser holds (the job\'s own pages stand in where it has no preview)', () => {
  const c = mountWorking([
    { name: 'a.png', size: 1, kind: 'image', previewUrl: 'blob:a' },
    { name: 'b.heic', size: 1, kind: 'image' },
  ]);
  fireEvent.click(toggle());
  // Before the job is known: the preview, and a placeholder for the picture the browser cannot show.
  expect([...panel().querySelectorAll('img')].map((img) => img.getAttribute('src'))).toEqual(['blob:a']);
  expect(screen.getByText('Page 2 · not read yet')).toBeTruthy();
  // Then the job's own page 2 takes its place.
  act(() => c.store.dispatch({ type: 'jobUpdated', job: withPages(overlapped('active', 'pending'), 2) }));
  expect([...panel().querySelectorAll('img')].map((img) => img.getAttribute('src'))).toEqual(['blob:a', '/api/jobs/job-1/media/page-1.png']);
});
