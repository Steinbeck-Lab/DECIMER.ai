import { afterEach, expect, test, vi } from 'vitest';
import type { ApiClient } from '../api/client';
import {
  ApiError,
  STAGE_ORDER,
  type ExportRequest,
  type Job,
  type ValidateResponse,
} from '../api/contract';
import { fixtureResults } from '../mocks/fixtures';
import { MockApiClient } from '../mocks/MockApiClient';
import { AppController } from './controller';

const results = fixtureResults(8);

const doneJob = (id: string): Job => ({
  id,
  status: 'done',
  stages: STAGE_ORDER.map((name) => ({ name, state: 'done' })),
  pages: 1,
  pagesProcessed: 1,
  truncated: false,
  structuresFound: 8,
  structuresDropped: 0,
  results,
  queuePosition: null,
  warnings: [],
  error: null,
  createdAt: '2026-10-22T12:00:00.000Z',
  expiresAt: '2026-10-22T13:00:00.000Z',
});

const runningJob = (id: string): Job => ({ ...doneJob(id), status: 'running', results: [] });

const png = (name = 'a.png') => new File(['x'], name, { type: 'image/png' });
const derived = (smiles: string): ValidateResponse => ({ smiles, inchikey: '', svg: '', valid: true, name: null, nameTier: null });

type Calls = Record<'createJob' | 'getJob' | 'validate' | 'exportJob' | 'report', unknown[][]>;

function fakeApi(overrides: Partial<ApiClient> = {}): ApiClient & { calls: Calls } {
  const calls: Calls = { createJob: [], getJob: [], validate: [], exportJob: [], report: [] };
  const record = (name: keyof Calls, args: unknown[]) => calls[name].push(args);
  const api: ApiClient = {
    pollIntervalMs: 1,
    createJob: async (...a) => (record('createJob', a), { id: 'job-1' }),
    getJob: async (...a) => (record('getJob', a), doneJob('job-1')),
    validate: async (...a) => (record('validate', a), derived('CC')),
    exportJob: async (...a) => (record('exportJob', a), { blob: new Blob(['x']), filename: 'x.zip' }),
    report: async (...a) => void record('report', a),
    ...overrides,
  };
  return Object.assign(api, { calls });
}

let controllers: AppController[] = [];
function make(api: ApiClient, save = vi.fn(), flushTimeoutMs?: number) {
  const controller = new AppController(api, { validateDelayMs: 500, save, flushTimeoutMs });
  controllers.push(controller);
  return { controller, save };
}
afterEach(() => {
  controllers.forEach((c) => c.dispose());
  controllers = [];
  vi.useRealTimers();
});

// ---- upload and polling ---------------------------------------------------

test('a refused selection never reaches the API and keeps the empty screen with the reason', async () => {
  const api = fakeApi();
  const { controller } = make(api);
  await controller.submit([png(), new File(['x'], 'doc.pdf', { type: 'application/pdf' })]);
  const state = controller.store.getState();
  expect(state.phase).toBe('empty');
  expect(state.problem).toEqual({ kind: 'selection', error: { code: 'mixed' } });
  expect(api.calls.createJob).toHaveLength(0);
});

test('a valid upload goes through working to results and then stops polling', async () => {
  let polls = 0;
  const api = fakeApi({
    getJob: async () => (++polls < 3 ? runningJob('job-1') : doneJob('job-1')),
  });
  const { controller } = make(api);
  const phases: string[] = [];
  controller.store.subscribe(() => phases.push(controller.store.getState().phase));
  await controller.submit([png()]);
  await vi.waitFor(() => expect(controller.store.getState().phase).toBe('results'));
  expect(phases).toContain('working');
  const callsAtDone = polls;
  await new Promise((r) => setTimeout(r, 30));
  expect(polls).toBe(callsAtDone);
  expect(controller.store.getState().job?.results).toHaveLength(8);
});

test('429 from the API puts the user back on the empty screen with that reason', async () => {
  const api = fakeApi({ createJob: async () => { throw new ApiError(429, 'rate_limited'); } });
  const { controller } = make(api);
  await controller.submit([png()]);
  expect(controller.store.getState()).toMatchObject({ phase: 'empty', problem: { kind: 'api', code: 'rate_limited' } });
});

test('three failed polls in a row end the job as a lost connection; one hiccup does not', async () => {
  let n = 0;
  const hiccup = fakeApi({
    getJob: async () => {
      if (++n === 1) throw new ApiError(0, 'network');
      return doneJob('job-1');
    },
  });
  const a = make(hiccup).controller;
  await a.submit([png()]);
  await vi.waitFor(() => expect(a.store.getState().phase).toBe('results'));

  const dead = fakeApi({ getJob: async () => { throw new ApiError(0, 'network'); } });
  const b = make(dead).controller;
  await b.submit([png()]);
  await vi.waitFor(() => expect(b.store.getState().phase).toBe('failed'));
  expect(b.store.getState().failure).toEqual({ kind: 'connection' });
});

test('starting over while the upload is in flight ignores the late answer', async () => {
  let release: (v: { id: string }) => void = () => {};
  const api = fakeApi({ createJob: () => new Promise((resolve) => (release = resolve)) });
  const { controller } = make(api);
  const pending = controller.submit([png()]);
  expect(controller.store.getState().phase).toBe('working');
  controller.startOver();
  release({ id: 'job-1' });
  await pending;
  await new Promise((r) => setTimeout(r, 10));
  expect(controller.store.getState().phase).toBe('empty');
  expect(controller.store.getState().jobId).toBeNull();
});

test('a second submit while working is ignored (a double paste starts one job)', async () => {
  const api = fakeApi({ getJob: async () => runningJob('job-1') });
  const { controller } = make(api);
  await Promise.all([controller.submit([png('a.png')]), controller.submit([png('b.png')])]);
  expect(api.calls.createJob).toHaveLength(1);
});

// ---- edits and validation --------------------------------------------------

async function inResults(api: ApiClient) {
  const made = make(api);
  await made.controller.submit([png()]);
  await vi.waitFor(() => expect(made.controller.store.getState().phase).toBe('results'));
  return made;
}

test('validation is debounced: edits that follow each other send one request with the last molfile', async () => {
  const api = fakeApi();
  const { controller } = await inResults(api);
  vi.useFakeTimers();
  controller.edit(1, 'm1');
  vi.advanceTimersByTime(300);
  controller.edit(1, 'm2');
  vi.advanceTimersByTime(300);
  controller.edit(1, 'm3');
  expect(api.calls.validate).toHaveLength(0);
  await vi.advanceTimersByTimeAsync(500);
  expect(api.calls.validate).toHaveLength(1);
  expect(api.calls.validate[0]?.slice(0, 3)).toEqual(['job-1', 1, 'm3']);
  expect(controller.store.getState().edits[1]?.derived?.smiles).toBe('CC');
});

test('a slow answer for an older edit cannot overwrite the newer one', async () => {
  const pending: Array<(v: ValidateResponse) => void> = [];
  const api = fakeApi({ validate: () => new Promise((resolve) => pending.push(resolve)) });
  const { controller } = await inResults(api);
  vi.useFakeTimers();
  controller.edit(1, 'old');
  await vi.advanceTimersByTimeAsync(500);
  controller.edit(1, 'new');
  await vi.advanceTimersByTimeAsync(500);
  expect(pending).toHaveLength(2);
  pending[1]?.(derived('NEW'));
  await vi.advanceTimersByTimeAsync(0);
  pending[0]?.(derived('OLD'));
  await vi.advanceTimersByTimeAsync(0);
  expect(controller.store.getState().edits[1]?.derived?.smiles).toBe('NEW');
});

test('a failed validation keeps the edit and says so', async () => {
  const api = fakeApi({ validate: async () => { throw new ApiError(500, 'unknown'); } });
  const { controller } = await inResults(api);
  vi.useFakeTimers();
  controller.edit(2, 'm');
  await vi.advanceTimersByTimeAsync(500);
  expect(controller.store.getState().edits[2]).toMatchObject({ modified: true, molfile: 'm', validationFailed: true, validating: false });
});

test('reset cancels a pending validation', async () => {
  const api = fakeApi();
  const { controller } = await inResults(api);
  vi.useFakeTimers();
  controller.edit(2, 'm');
  controller.reset(2);
  await vi.advanceTimersByTimeAsync(2000);
  expect(api.calls.validate).toHaveLength(0);
  expect(controller.store.getState().edits[2]).toBeUndefined();
});

// ---- export ---------------------------------------------------------------

test('the request is keyed by result index: an edit goes to the result it was made on', async () => {
  const api = fakeApi();
  const { controller, save } = await inResults(api);
  controller.edit(6, 'six');
  controller.edit(0, 'zero');
  controller.edit(3, 'three');
  await controller.download('zip');
  const request = api.calls.exportJob[0]?.[1] as ExportRequest;
  expect(request.format).toBe('zip');
  expect(request.molfiles).toEqual({ 0: 'zero', 3: 'three', 6: 'six' });
  expect(save).toHaveBeenCalledTimes(1);
});

test('what comes out of the export is in result order with each edit on its own structure, whatever order the edits were made in', async () => {
  // Through the mock backend, which orders the file by the job's results and takes only the edits from
  // the request. The fixtures are numbered as in the paper (1, 2, 4, 5, 6, 7, 11, 12) while the result
  // indexes are 0..7, so an edit filed under the wrong key (the display number, a position in the list of
  // edits) lands on a different structure and shows up here.
  const mock = new MockApiClient({ stepMs: 5, latencyMs: 0 });
  let saved: Blob | null = null;
  const controller = new AppController(mock, { validateDelayMs: 500, save: (blob) => (saved = blob) });
  controllers.push(controller);
  await controller.submit([png()]);
  await vi.waitFor(() => expect(controller.store.getState().phase).toBe('results'), { timeout: 3000 });

  controller.edit(6, 'EDIT-ON-INDEX-6'); // number 11, edited first
  controller.edit(0, 'EDIT-ON-INDEX-0'); // number 1
  controller.edit(3, 'EDIT-ON-INDEX-3'); // number 5
  await controller.download('zip');

  const text = await (saved as Blob | null)?.text();
  if (text === undefined) throw new Error('nothing was saved');
  const sections = text
    .split('\n== structure ')
    .slice(1)
    .map((section) => [section.match(/^(\d+) \((\w+)\)/)?.[1], section.match(/^\d+ \((\w+)\)/)?.[1], section.match(/EDIT-ON-INDEX-\d|RDKit/)?.[0]]);
  expect(sections).toEqual([
    ['1', 'edited', 'EDIT-ON-INDEX-0'],
    ['2', 'original', 'RDKit'],
    ['4', 'original', 'RDKit'],
    ['5', 'edited', 'EDIT-ON-INDEX-3'],
    ['6', 'original', 'RDKit'],
    ['7', 'original', 'RDKit'],
    ['11', 'edited', 'EDIT-ON-INDEX-6'],
    ['12', 'original', 'RDKit'],
  ]);
});

test('an edit still sitting in the editor is committed before the export reads the edits (the legacy race)', async () => {
  const api = fakeApi();
  const { controller } = await inResults(api);
  // The editor's own change event has not fired yet: the canvas holds a molfile the store does not know.
  controller.registerEditorFlush(async () => {
    await new Promise((r) => setTimeout(r, 5));
    controller.edit(4, 'typed-a-moment-ago');
  });
  await controller.download('sdf');
  const request = api.calls.exportJob[0]?.[1] as ExportRequest;
  expect(request.molfiles).toEqual({ 4: 'typed-a-moment-ago' });
});

test('a failed export shows an error notice and does not save anything', async () => {
  const api = fakeApi({ exportJob: async () => { throw new ApiError(500, 'unknown'); } });
  const { controller, save } = await inResults(api);
  await controller.download('csv');
  expect(save).not.toHaveBeenCalled();
  expect(controller.store.getState().notice?.kind).toBe('error');
});

test('nothing can be downloaded before there are results', async () => {
  const api = fakeApi();
  const { controller } = make(api);
  await controller.download('zip');
  expect(api.calls.exportJob).toHaveLength(0);
});

// ---- report ---------------------------------------------------------------

test('a report is sent for one result and remembered', async () => {
  const api = fakeApi();
  const { controller } = await inResults(api);
  expect(await controller.report(4)).toBe(true);
  expect(api.calls.report[0]?.slice(0, 2)).toEqual(['job-1', 4]);
  expect(controller.store.getState().reported).toEqual({ 4: true });
});

// ---- superseded validation requests are aborted ------------------------------------------

function abortableValidate(signals: AbortSignal[], pending: Array<(v: ValidateResponse) => void>): ApiClient['validate'] {
  return (_job, _index, _molfile, signal) =>
    new Promise((resolve, reject) => {
      if (signal) signals.push(signal);
      pending.push(resolve);
      signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    });
}

test('a newer edit aborts the validate request still in flight for the older one', async () => {
  const signals: AbortSignal[] = [];
  const pending: Array<(v: ValidateResponse) => void> = [];
  const api = fakeApi({ validate: abortableValidate(signals, pending) });
  const { controller } = await inResults(api);
  vi.useFakeTimers();
  controller.edit(1, 'old');
  await vi.advanceTimersByTimeAsync(500);
  expect(signals).toHaveLength(1);
  expect(signals[0]?.aborted).toBe(false);

  controller.edit(1, 'new'); // the older answer is now moot
  expect(signals[0]?.aborted).toBe(true);
  await vi.advanceTimersByTimeAsync(500);
  expect(signals).toHaveLength(2);
  expect(signals[1]?.aborted).toBe(false);

  // The aborted request ended in an AbortError, which is not a failed validation.
  const entry = controller.store.getState().edits[1];
  expect(entry?.validationFailed).toBe(false);
  expect(entry?.validating).toBe(true);
  pending[1]?.(derived('NEW'));
  await vi.advanceTimersByTimeAsync(0);
  expect(controller.store.getState().edits[1]?.derived?.smiles).toBe('NEW');
});

test('Reset and starting over abort what is in flight', async () => {
  const signals: AbortSignal[] = [];
  const api = fakeApi({ validate: abortableValidate(signals, []) });
  const { controller } = await inResults(api);
  vi.useFakeTimers();
  controller.edit(1, 'a');
  controller.edit(2, 'b');
  await vi.advanceTimersByTimeAsync(500);
  expect(signals).toHaveLength(2);
  controller.reset(1);
  expect(signals[0]?.aborted).toBe(true);
  expect(signals[1]?.aborted).toBe(false);
  controller.startOver();
  expect(signals[1]?.aborted).toBe(true);
  await vi.advanceTimersByTimeAsync(0);
  expect(controller.store.getState().edits).toEqual({});
});

// ---- the editor flush cannot hang the export -----------------------------------------------

test('an editor that never answers does not hang the download: it times out, says so, exports nothing', async () => {
  const api = fakeApi();
  const save = vi.fn();
  const { controller } = make(api, save, 40);
  await controller.submit([png()]);
  await vi.waitFor(() => expect(controller.store.getState().phase).toBe('results'));
  controller.registerEditorFlush(() => new Promise<void>(() => undefined)); // Ketcher is stuck

  const started = Date.now();
  await controller.download('zip');
  expect(Date.now() - started).toBeLessThan(1500);
  expect(api.calls.exportJob).toHaveLength(0);
  expect(save).not.toHaveBeenCalled();
  expect(controller.store.getState().notice).toMatchObject({
    kind: 'error',
    text: 'The editor is not responding. Wait a moment and try the download again.',
  });
  expect(controller.store.getState().exporting).toBe(false); // the buttons come back
});

test('a flush that fails is reported the same way, and a slow one that finishes in time is waited for', async () => {
  const api = fakeApi();
  const { controller } = await inResults(api);
  controller.registerEditorFlush(async () => {
    await new Promise((r) => setTimeout(r, 30));
    controller.edit(3, 'late but in time');
  });
  await controller.download('sdf');
  expect((api.calls.exportJob[0]?.[1] as ExportRequest).molfiles).toEqual({ 3: 'late but in time' });

  const broken = fakeApi();
  const second = await inResults(broken);
  second.controller.registerEditorFlush(() => Promise.reject(new Error('ketcher died')));
  await second.controller.download('sdf');
  expect(broken.calls.exportJob).toHaveLength(0);
  expect(second.controller.store.getState().notice?.kind).toBe('error');
});

// ---- one export at a time ------------------------------------------------------------------

test('two downloads started together (the header menu and the one at the end of the figure) make one export', async () => {
  let release: () => void = () => undefined;
  const api = fakeApi({
    exportJob: (...a) => {
      (api as unknown as { calls: Calls }).calls.exportJob.push(a);
      return new Promise((resolve) => {
        release = () => resolve({ blob: new Blob(['x']), filename: 'x.zip' });
      });
    },
  });
  const { controller, save } = await inResults(api);
  const first = controller.download('zip');
  expect(controller.store.getState().exporting).toBe(true);
  await controller.download('sdf'); // the second menu: ignored while the first is running
  await vi.waitFor(() => expect(api.calls.exportJob).toHaveLength(1));
  release();
  await first;
  expect(api.calls.exportJob).toHaveLength(1);
  expect((api.calls.exportJob[0]?.[1] as ExportRequest).format).toBe('zip');
  expect(save).toHaveBeenCalledTimes(1);
  expect(controller.store.getState().exporting).toBe(false);

  // Afterwards the next download works.
  const again = controller.download('csv');
  await vi.waitFor(() => expect(api.calls.exportJob).toHaveLength(2));
  release();
  await again;
  expect(save).toHaveBeenCalledTimes(2);
});

test('a failed export frees the menus too', async () => {
  const api = fakeApi({ exportJob: async () => { throw new ApiError(500, 'unknown'); } });
  const { controller } = await inResults(api);
  await controller.download('zip');
  expect(controller.store.getState().exporting).toBe(false);
});
