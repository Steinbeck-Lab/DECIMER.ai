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
const derived = (smiles: string): ValidateResponse => ({ smiles, inchikey: '', svg: '', valid: true, name: null, nameTier: null, nameVerified: null });

type Calls = Record<'createJob' | 'getJob' | 'deleteJob' | 'validate' | 'exportJob' | 'report', unknown[][]>;

function fakeApi(overrides: Partial<ApiClient> = {}): ApiClient & { calls: Calls } {
  const calls: Calls = { createJob: [], getJob: [], deleteJob: [], validate: [], exportJob: [], report: [] };
  const record = (name: keyof Calls, args: unknown[]) => calls[name].push(args);
  const api: ApiClient = {
    pollIntervalMs: 1,
    createJob: async (...a) => (record('createJob', a), { id: 'job-1' }),
    getJob: async (...a) => (record('getJob', a), doneJob('job-1')),
    deleteJob: async (...a) => void record('deleteJob', a),
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

test('three unexpected answers end the job as a lost connection; one hiccup does not', async () => {
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

  const dead = fakeApi({ getJob: async () => { throw new ApiError(500, 'unknown'); } });
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

// ---- starting over deletes the job on the server ----------------------

test('starting over mid-job deletes the job, so its slot is free at once', async () => {
  const api = fakeApi({ getJob: async () => runningJob('job-1') });
  const { controller } = make(api);
  await controller.submit([png()]);
  await vi.waitFor(() => expect(controller.store.getState().jobId).toBe('job-1'));
  controller.startOver();
  expect(api.calls.deleteJob.map((call) => call[0])).toEqual(['job-1']);
  expect(controller.store.getState().phase).toBe('empty');
});

test('starting over from the results deletes the job too (its files go now, not in an hour)', async () => {
  const api = fakeApi();
  const { controller } = make(api);
  await controller.submit([png()]);
  await vi.waitFor(() => expect(controller.store.getState().phase).toBe('results'));
  controller.startOver();
  expect(api.calls.deleteJob.map((call) => call[0])).toEqual(['job-1']);
});

test('starting over a second time, or before there is a job, deletes nothing', async () => {
  const api = fakeApi();
  const { controller } = make(api);
  controller.startOver(); // the empty screen
  await controller.submit([png()]);
  await vi.waitFor(() => expect(controller.store.getState().phase).toBe('results'));
  controller.startOver();
  controller.startOver(); // a double click
  expect(api.calls.deleteJob).toHaveLength(1);
});

test('starting over while the upload is in flight has no job id to delete (the late answer is ignored)', async () => {
  let release: (v: { id: string }) => void = () => {};
  const api = fakeApi({ createJob: () => new Promise((resolve) => (release = resolve)) });
  const { controller } = make(api);
  const pending = controller.submit([png()]);
  controller.startOver();
  release({ id: 'job-1' });
  await pending;
  expect(api.calls.deleteJob).toHaveLength(0);
});

test.each([
  ['the job is already gone', new ApiError(404, 'not_found')],
  ['the network is down', new ApiError(0, 'network')],
  ['the server is busy', new ApiError(503, 'unavailable')],
  ['anything else', new Error('boom')],
])('starting over does not wait for the delete and does not fail when %s', async (_label, failure) => {
  const api = fakeApi({ deleteJob: () => Promise.reject(failure) });
  const { controller } = make(api);
  await controller.submit([png()]);
  await vi.waitFor(() => expect(controller.store.getState().phase).toBe('results'));
  expect(() => controller.startOver()).not.toThrow();
  expect(controller.store.getState().phase).toBe('empty');
  await new Promise((r) => setTimeout(r, 5)); // an unhandled rejection would fail the run here
  expect(controller.store.getState().notice).toBeNull(); // and nothing is shown to the user
});

test('a job that expired or was lost is deleted on start over too (the server answers 404 and that is fine)', async () => {
  const api = fakeApi({ getJob: async () => { throw new ApiError(404, 'not_found'); } });
  const { controller } = make(api);
  await controller.submit([png()]);
  await vi.waitFor(() => expect(controller.store.getState().phase).toBe('failed'));
  controller.startOver();
  expect(api.calls.deleteJob.map((call) => call[0])).toEqual(['job-1']);
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

// ---- the service says "busy": try once more after Retry-After (review 3) -----------------------

const busy = (retryAfterMs: number | null) => new ApiError(503, 'busy', 'The service is busy.', retryAfterMs);

/** An API whose `validate` answers with each of `answers` in turn (an error is thrown), then the last again. */
function validateAnswers(answers: Array<ApiError | ValidateResponse>) {
  let n = 0;
  const api: ReturnType<typeof fakeApi> = fakeApi({
    validate: async (...a) => {
      api.calls.validate.push(a);
      const next = answers[Math.min(n++, answers.length - 1)];
      if (next instanceof ApiError) throw next;
      return next as ValidateResponse;
    },
  });
  return api;
}

test('validate: a busy answer is tried once more after Retry-After, and the retry is what is shown', async () => {
  const api = validateAnswers([busy(2000), derived('CCO')]);
  const { controller } = await inResults(api);
  vi.useFakeTimers();
  controller.edit(1, 'm');
  await vi.advanceTimersByTimeAsync(500); // the debounce: the first request goes out, and is told to wait
  expect(api.calls.validate).toHaveLength(1);
  await vi.advanceTimersByTimeAsync(1999);
  expect(api.calls.validate).toHaveLength(1); // not before Retry-After
  await vi.advanceTimersByTimeAsync(1);
  expect(api.calls.validate).toHaveLength(2);
  expect(controller.store.getState().edits[1]).toMatchObject({ validationFailed: false, validating: false });
  expect(controller.store.getState().edits[1]?.derived?.smiles).toBe('CCO');
});

test('validate: busy twice is a failure, after exactly two requests', async () => {
  const api = validateAnswers([busy(1000)]);
  const { controller } = await inResults(api);
  vi.useFakeTimers();
  controller.edit(1, 'm');
  await vi.advanceTimersByTimeAsync(500 + 1000 + 60_000);
  expect(api.calls.validate).toHaveLength(2);
  expect(controller.store.getState().edits[1]).toMatchObject({ validationFailed: true, validating: false });
});

test('validate: a Retry-After of 9 s waits 5 s, no longer', async () => {
  const api = validateAnswers([busy(9000), derived('CC')]);
  const { controller } = await inResults(api);
  vi.useFakeTimers();
  controller.edit(1, 'm');
  await vi.advanceTimersByTimeAsync(500 + 4999);
  expect(api.calls.validate).toHaveLength(1);
  await vi.advanceTimersByTimeAsync(1);
  expect(api.calls.validate).toHaveLength(2);
});

test.each([
  ['a 503 with another code', new ApiError(503, 'unavailable', 'x', 2000)],
  ['a 429', new ApiError(429, 'rate_limited', 'x', 2000)],
  ['a busy answer with no Retry-After', busy(null)],
  ['a 422', new ApiError(422, 'invalid_request', 'x', null)],
  ['no answer at all', new ApiError(0, 'network')],
])('validate: %s is not tried again', async (_label, error) => {
  const api = validateAnswers([error, derived('CC')]);
  const { controller } = await inResults(api);
  vi.useFakeTimers();
  controller.edit(1, 'm');
  await vi.advanceTimersByTimeAsync(500 + 60_000);
  expect(api.calls.validate).toHaveLength(1);
  expect(controller.store.getState().edits[1]).toMatchObject({ validationFailed: true });
});

test('validate: a newer edit while the old one waits to be tried again takes its place (no stale retry)', async () => {
  const api = validateAnswers([busy(3000), derived('NEW')]);
  const { controller } = await inResults(api);
  vi.useFakeTimers();
  controller.edit(1, 'old');
  await vi.advanceTimersByTimeAsync(500); // 'old' is out, told to wait 3 s
  controller.edit(1, 'new');
  await vi.advanceTimersByTimeAsync(500); // 'new' goes out and is answered
  await vi.advanceTimersByTimeAsync(10_000); // the old retry would have been due
  expect(api.calls.validate.map((call) => call[2])).toEqual(['old', 'new']);
  expect(controller.store.getState().edits[1]?.derived?.smiles).toBe('NEW');
});

function exportAnswers(answers: Array<ApiError | 'ok'>) {
  let n = 0;
  const api: ReturnType<typeof fakeApi> = fakeApi({
    exportJob: async (...a) => {
      api.calls.exportJob.push(a);
      const next = answers[Math.min(n++, answers.length - 1)];
      if (next instanceof ApiError) throw next;
      return { blob: new Blob(['x']), filename: 'x.zip' };
    },
  });
  return api;
}

test('export: a busy answer is tried once more after Retry-After, and the file is saved', async () => {
  const api = exportAnswers([busy(2000), 'ok']);
  const { controller, save } = await inResults(api);
  vi.useFakeTimers();
  const done = controller.download('zip');
  await vi.advanceTimersByTimeAsync(1999);
  expect(api.calls.exportJob).toHaveLength(1);
  expect(controller.store.getState().exporting).toBe(true); // still working: the second menu is ignored
  await controller.download('csv');
  expect(api.calls.exportJob).toHaveLength(1);
  await vi.advanceTimersByTimeAsync(1);
  await done;
  expect(api.calls.exportJob).toHaveLength(2);
  expect(save).toHaveBeenCalledTimes(1);
  expect(controller.store.getState().notice).toMatchObject({ kind: 'info' });
  expect(controller.store.getState().exporting).toBe(false);
});

test('export: busy twice shows the failure, after exactly two requests', async () => {
  const api = exportAnswers([busy(1000)]);
  const { controller, save } = await inResults(api);
  vi.useFakeTimers();
  const done = controller.download('sdf');
  await vi.advanceTimersByTimeAsync(1000);
  await done;
  expect(api.calls.exportJob).toHaveLength(2);
  expect(save).not.toHaveBeenCalled();
  expect(controller.store.getState().notice).toMatchObject({ kind: 'error' });
  await vi.advanceTimersByTimeAsync(60_000);
  expect(api.calls.exportJob).toHaveLength(2); // and no third
});

test('export: a Retry-After over 5 s waits 5 s', async () => {
  const api = exportAnswers([busy(20_000), 'ok']);
  const { controller } = await inResults(api);
  vi.useFakeTimers();
  const done = controller.download('zip');
  await vi.advanceTimersByTimeAsync(4999);
  expect(api.calls.exportJob).toHaveLength(1);
  await vi.advanceTimersByTimeAsync(1);
  await done;
  expect(api.calls.exportJob).toHaveLength(2);
});

test.each([
  ['a 503 with another code', new ApiError(503, 'unavailable', 'x', 2000)],
  ['a 429 (too many downloads)', new ApiError(429, 'rate_limited', 'x', 5000)],
  ['a busy answer with no Retry-After', busy(null)],
  ['a 409', new ApiError(409, 'no_results', 'x', null)],
])('export: %s is not tried again', async (_label, error) => {
  const api = exportAnswers([error, 'ok']);
  const { controller } = await inResults(api);
  vi.useFakeTimers();
  const done = controller.download('zip');
  await vi.advanceTimersByTimeAsync(10);
  await done;
  expect(api.calls.exportJob).toHaveLength(1);
  expect(controller.store.getState().notice).toMatchObject({ kind: 'error' });
  await vi.advanceTimersByTimeAsync(60_000);
  expect(api.calls.exportJob).toHaveLength(1);
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

// ---- polling backs off, and honours Retry-After --------------------------------------------

function pollingApi(responses: Array<Job | ApiError>, signals: AbortSignal[] = []) {
  const times: number[] = [];
  let n = 0;
  const api = fakeApi({
    pollIntervalMs: 1000,
    getJob: async (_id, signal) => {
      times.push(Date.now());
      if (signal) signals.push(signal);
      const next = responses[Math.min(n++, responses.length - 1)];
      if (next instanceof ApiError) throw next;
      if (!next) throw new Error('no response');
      return next;
    },
  });
  return { api, times, calls: () => times.length };
}

const unavailable = (retryAfterMs: number | null) => new ApiError(503, 'unavailable', 'Try again.', retryAfterMs);
const offline = () => new ApiError(0, 'network');

async function startPolling(api: ApiClient) {
  vi.useFakeTimers();
  const { controller } = make(api);
  await controller.submit([png()]);
  await vi.advanceTimersByTimeAsync(0);
  return controller;
}

test('a 503 with Retry-After is not polled again before that time', async () => {
  const { api, calls } = pollingApi([unavailable(7000), doneJob('job-1')]);
  const controller = await startPolling(api);
  expect(calls()).toBe(1);
  await vi.advanceTimersByTimeAsync(6999);
  expect(calls()).toBe(1);
  await vi.advanceTimersByTimeAsync(1);
  expect(calls()).toBe(2);
  expect(controller.store.getState().phase).toBe('results');
});

test('Retry-After is capped at 30 s and is never shorter than the poll interval', async () => {
  const long = pollingApi([unavailable(120_000), doneJob('job-1')]);
  await startPolling(long.api);
  await vi.advanceTimersByTimeAsync(29_999);
  expect(long.calls()).toBe(1);
  await vi.advanceTimersByTimeAsync(1);
  expect(long.calls()).toBe(2);
  controllers.forEach((c) => c.dispose());
  controllers = [];

  const short = pollingApi([unavailable(100), doneJob('job-1')]);
  await startPolling(short.api);
  await vi.advanceTimersByTimeAsync(999);
  expect(short.calls()).toBe(1); // 100 ms asked, one poll interval kept
  await vi.advanceTimersByTimeAsync(1);
  expect(short.calls()).toBe(2);
});

const broken = () => new ApiError(500, 'unknown');

test('without Retry-After, unexpected answers back off: 1 s, then 2 s, and the third ends the job as a lost connection', async () => {
  const { api, times } = pollingApi([broken()]);
  const controller = await startPolling(api);
  await vi.advanceTimersByTimeAsync(999);
  expect(times).toHaveLength(1);
  await vi.advanceTimersByTimeAsync(1);
  expect(times).toHaveLength(2);
  await vi.advanceTimersByTimeAsync(1999);
  expect(times).toHaveLength(2);
  await vi.advanceTimersByTimeAsync(1);
  expect(times).toHaveLength(3);
  expect(controller.store.getState()).toMatchObject({ phase: 'failed', failure: { kind: 'connection' } });
  await vi.advanceTimersByTimeAsync(60_000);
  expect(times).toHaveLength(3); // and it stopped asking
});

test('no answer at all is retried like a busy server: ten failures, backing off 1 s, 2 s, 4 s, 8 s and then 16 s at most', async () => {
  const { api, times } = pollingApi([offline()]);
  const controller = await startPolling(api);
  await vi.advanceTimersByTimeAsync(300_000);
  expect(times).toHaveLength(10);
  const gaps = times.slice(1).map((t, i) => t - times[i]!);
  expect(gaps).toEqual([1000, 2000, 4000, 8000, 16_000, 16_000, 16_000, 16_000, 16_000]);
  expect(controller.store.getState()).toMatchObject({ phase: 'failed', failure: { kind: 'connection' } });
});

test('a network that comes back within ten failures finishes the job', async () => {
  const { api } = pollingApi([...Array.from({ length: 9 }, offline), doneJob('job-1')]);
  const controller = await startPolling(api);
  await vi.advanceTimersByTimeAsync(300_000);
  expect(controller.store.getState().phase).toBe('results');
});

test('failures are counted per kind: busy answers and lost connections do not use up the three unexpected ones', async () => {
  // Six with no answer, six "try again", one unexpected answer, then the job: the unexpected one is the first of its kind.
  const mixed = pollingApi([...Array.from({ length: 6 }, offline), ...Array.from({ length: 6 }, () => unavailable(1000)), broken(), doneJob('job-1')]);
  const ok = await startPolling(mixed.api);
  await vi.advanceTimersByTimeAsync(600_000);
  expect(ok.store.getState().phase).toBe('results');
  controllers.forEach((c) => c.dispose());
  controllers = [];

  // The third unexpected answer ends it, however many other failures lie between them.
  const three = pollingApi([broken(), unavailable(1000), broken(), offline(), broken(), doneJob('job-1')]);
  const lost = await startPolling(three.api);
  await vi.advanceTimersByTimeAsync(600_000);
  expect(three.calls()).toBe(5);
  expect(lost.store.getState()).toMatchObject({ phase: 'failed', failure: { kind: 'connection' } });
});

test('a server that keeps answering "try again" is followed through ten failures in a row, not three', async () => {
  const nine = pollingApi([...Array.from({ length: 9 }, () => unavailable(1000)), doneJob('job-1')]);
  const ok = await startPolling(nine.api);
  await vi.advanceTimersByTimeAsync(60_000);
  expect(ok.store.getState().phase).toBe('results');
  controllers.forEach((c) => c.dispose());
  controllers = [];

  const never = pollingApi([unavailable(1000)]);
  const lost = await startPolling(never.api);
  await vi.advanceTimersByTimeAsync(60_000);
  expect(never.calls()).toBe(10);
  expect(lost.store.getState().failure).toEqual({ kind: 'connection' });
});

// One less than each kind's limit, a good answer, one less again: without the reset the second run would reach the limit.
test.each<[string, () => ApiError, number]>([
  ['unexpected answers (3 in a row end it)', broken, 2],
  ['no answer at all (10 in a row end it)', offline, 9],
  ['"try again" answers (10 in a row end it)', () => unavailable(1000), 9],
])('one good answer clears the count of failures: %s', async (_kind, fail, below) => {
  const running = { ...doneJob('job-1'), status: 'running' as const, results: [] };
  const { api } = pollingApi([...Array.from({ length: below }, fail), running, ...Array.from({ length: below }, fail), doneJob('job-1')]);
  const controller = await startPolling(api);
  await vi.advanceTimersByTimeAsync(600_000);
  expect(controller.store.getState().phase).toBe('results'); // never a whole run of failures in a row
});

test('a job that has expired is not polled again', async () => {
  const { api, calls } = pollingApi([new ApiError(404, 'not_found', 'Not found. The job may have expired.')]);
  const controller = await startPolling(api);
  await vi.advanceTimersByTimeAsync(60_000);
  expect(calls()).toBe(1);
  expect(controller.store.getState().failure).toEqual({ kind: 'expired' });
});

test('after a lost connection, resume polls the same job again and finishes it; no new upload', async () => {
  const { api, calls } = pollingApi([broken(), broken(), broken(), doneJob('job-1')]);
  const controller = await startPolling(api);
  await vi.advanceTimersByTimeAsync(60_000);
  expect(controller.store.getState()).toMatchObject({ phase: 'failed', jobId: 'job-1', failure: { kind: 'connection' } });
  expect(calls()).toBe(3);

  controller.resume();
  expect(controller.store.getState()).toMatchObject({ phase: 'working', failure: null, jobId: 'job-1' });
  await vi.advanceTimersByTimeAsync(0);
  expect(calls()).toBe(4); // asked at once, not after another wait
  expect(controller.store.getState().phase).toBe('results');
  expect(controller.store.getState().job?.id).toBe('job-1');
});

test('a resumed job gets its failure counts afresh: ten more tries for a lost connection, not one', async () => {
  const { api, calls } = pollingApi([offline()]);
  const controller = await startPolling(api);
  await vi.advanceTimersByTimeAsync(300_000);
  expect(calls()).toBe(10);
  expect(controller.store.getState().phase).toBe('failed');

  controller.resume();
  await vi.advanceTimersByTimeAsync(300_000);
  expect(calls()).toBe(20);
  expect(controller.store.getState().phase).toBe('failed');
});

test('resume does nothing unless the connection was lost: not for an expired job, not while working, not in results', async () => {
  const expired = pollingApi([new ApiError(404, 'not_found', 'Not found. The job may have expired.')]);
  const a = await startPolling(expired.api);
  a.resume();
  await vi.advanceTimersByTimeAsync(60_000);
  expect(expired.calls()).toBe(1);
  expect(a.store.getState().phase).toBe('failed');
  controllers.forEach((c) => c.dispose());
  controllers = [];

  const working = pollingApi([runningJob('job-1')]);
  const b = await startPolling(working.api);
  b.resume(); // already polling: a second loop would double the requests
  await vi.advanceTimersByTimeAsync(10_000);
  expect(working.calls()).toBe(11);
  controllers.forEach((c) => c.dispose());
  controllers = [];

  const done = pollingApi([doneJob('job-1')]);
  const c = await startPolling(done.api);
  c.resume();
  await vi.advanceTimersByTimeAsync(10_000);
  expect(done.calls()).toBe(1);
});

test('starting over after a lost connection forgets the job: resume then has nothing to resume', async () => {
  const { api, calls } = pollingApi([broken()]);
  const controller = await startPolling(api);
  await vi.advanceTimersByTimeAsync(60_000);
  controller.startOver();
  controller.resume();
  await vi.advanceTimersByTimeAsync(60_000);
  expect(calls()).toBe(3);
  expect(controller.store.getState().phase).toBe('empty');
});

test('starting over, or leaving the page, aborts the request in flight and the wait before the next one', async () => {
  const signals: AbortSignal[] = [];
  const { api, calls } = pollingApi([unavailable(20_000)], signals);
  const controller = await startPolling(api);
  expect(signals[0]?.aborted).toBe(false);
  controller.startOver();
  expect(signals[0]?.aborted).toBe(true);
  await vi.advanceTimersByTimeAsync(60_000);
  expect(calls()).toBe(1);

  const second = pollingApi([unavailable(20_000)], signals);
  const other = await startPolling(second.api);
  other.dispose(); // the provider's cleanup when the app unmounts
  expect(signals.at(-1)?.aborted).toBe(true);
  await vi.advanceTimersByTimeAsync(60_000);
  expect(second.calls()).toBe(1);
});
