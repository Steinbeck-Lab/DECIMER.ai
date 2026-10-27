import type { ApiClient } from '../api/client';
import {
  ApiError,
  isTerminal,
  type ExportFormat,
} from '../api/contract';
import { saveBlob } from '../download';
import { strings } from '../strings';
import { checkSelection, kindOf } from '../upload/files';
import { problemFromError } from '../upload/problems';
import { initialState, type Filter, type SourceFile } from './model';
import { reducer } from './reducer';
import { buildExportRequest } from './selectors';
import { createStore } from './store';

export interface ControllerOptions {
  /** Quiet time after the last edit before the server is asked to validate (ms). */
  validateDelayMs?: number;
  now?: () => number;
  /** Hands a finished export to the browser. */
  save?: (blob: Blob, filename: string) => void;
  /** How long a download waits for the editor to hand over a pending edit (ms). */
  flushTimeoutMs?: number;
}

/**
 * What went wrong with a poll, because each kind is followed for a different time. A kind is counted on its own, so
 * a run of "try again" answers does not use up the few tries an unexpected answer gets. All counts start again
 * with the next good answer.
 */
type FailureKind = 'network' | 'busy' | 'unexpected';
/** No answer: the network may come back. The server said "try again" (429, 502, 503, 504): it is there. */
const MAX_FAILURES: Record<FailureKind, number> = { network: 10, busy: 10, unexpected: 3 };
const BUSY_STATUS = new Set([429, 502, 503, 504]);
/** The longest wait a Retry-After can ask for, and the longest backoff without one. */
const MAX_RETRY_AFTER_MS = 30_000;
const MAX_BACKOFF_MS = 16_000;

function failureKind(error: ApiError | null): FailureKind {
  if (error?.code === 'network') return 'network';
  if (error && BUSY_STATUS.has(error.status)) return 'busy';
  return 'unexpected';
}

/** The longest a busy answer's Retry-After is waited for before the one retry. */
const MAX_BUSY_WAIT_MS = 5_000;

/**
 * The service answered 503 `busy`: every place for this kind of request is taken for a moment, and `Retry-After` says
 * when to ask again. Asks once more after that (at most 5 s), and returns what the second try gives, an error
 * included: a second "busy", a 429, any other answer is the caller's to show. `signal` ends the wait early when the
 * request is no longer wanted (a newer edit has taken its place), and then the first answer is thrown.
 */
async function retryWhenBusy<T>(call: () => Promise<T>, signal: AbortSignal): Promise<T> {
  try {
    return await call();
  } catch (error) {
    const busy = error instanceof ApiError && error.status === 503 && error.code === 'busy' && error.retryAfterMs !== null;
    if (!busy) throw error;
    await sleep(Math.min(error.retryAfterMs ?? 0, MAX_BUSY_WAIT_MS), signal);
    if (signal.aborted) throw error;
    return call();
  }
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const done = () => {
      clearTimeout(timer);
      signal.removeEventListener('abort', done);
      resolve();
    };
    const timer = setTimeout(done, ms);
    signal.addEventListener('abort', done);
  });
}

function toSource(file: File): SourceFile {
  const previewable = file.type.startsWith('image/') && !/heic|heif/.test(file.type);
  const canUrl = typeof URL !== 'undefined' && typeof URL.createObjectURL === 'function';
  return {
    name: file.name,
    size: file.size,
    kind: kindOf(file) ?? 'image',
    ...(previewable && canUrl ? { previewUrl: URL.createObjectURL(file) } : {}),
  };
}

/**
 * Everything that happens to the job: upload, polling, edits, validation,
 * export, report. Plain TypeScript around one synchronous store, so it is
 * tested without React. Components only read the store and call these methods.
 */
export class AppController {
  readonly store = createStore(reducer, initialState);

  private readonly validateDelayMs: number;
  private readonly now: () => number;
  private readonly save: (blob: Blob, filename: string) => void;
  private readonly flushTimeoutMs: number;
  private readonly validateTimers = new Map<number, ReturnType<typeof setTimeout>>();
  private readonly validateAborts = new Map<number, AbortController>();
  private pollAbort: AbortController | null = null;
  private submitAbort: AbortController | null = null;
  /** Bumped by startOver and every submit, so a late answer for an old job is ignored. */
  private generation = 0;
  private editorFlush: (() => Promise<void>) | null = null;
  private noticeId = 0;
  private noticeTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private readonly api: ApiClient,
    options: ControllerOptions = {},
  ) {
    this.validateDelayMs = options.validateDelayMs ?? 600;
    this.now = options.now ?? (() => Date.now());
    this.save = options.save ?? saveBlob;
    this.flushTimeoutMs = options.flushTimeoutMs ?? 5000;
  }

  // ---- upload and polling -------------------------------------------------

  /** Called by the drop zone, the file picker and the paste handler. Only the empty screen can start a job. */
  async submit(files: File[]): Promise<void> {
    if (this.store.getState().phase !== 'empty') return;
    const selection = checkSelection(files);
    if (!selection.ok) {
      this.store.dispatch({ type: 'rejected', problem: { kind: 'selection', error: selection.error } });
      return;
    }

    const generation = ++this.generation;
    const abort = new AbortController();
    this.submitAbort = abort;
    this.store.dispatch({ type: 'submit', sources: selection.files.map(toSource), at: this.now() });
    try {
      const { id } = await this.api.createJob(selection.files, abort.signal);
      if (generation !== this.generation) return;
      this.store.dispatch({ type: 'jobCreated', id });
      void this.poll(id, generation);
    } catch (error) {
      if (generation !== this.generation) return;
      this.revokePreviews();
      this.store.dispatch({ type: 'rejected', problem: problemFromError(error) });
    }
  }

  private async poll(id: string, generation: number): Promise<void> {
    this.pollAbort?.abort();
    const abort = new AbortController();
    this.pollAbort = abort;
    const failures: Record<FailureKind, number> = { network: 0, busy: 0, unexpected: 0 };
    while (!abort.signal.aborted && generation === this.generation) {
      try {
        const job = await this.api.getJob(id, abort.signal);
        if (abort.signal.aborted || generation !== this.generation) return;
        failures.network = failures.busy = failures.unexpected = 0;
        this.store.dispatch({ type: 'jobUpdated', job });
        if (isTerminal(job)) return;
      } catch (error) {
        if (abort.signal.aborted || generation !== this.generation) return;
        const apiError = error instanceof ApiError ? error : null;
        // An expired job will not come back; anything else may be a hiccup.
        if (apiError?.code === 'not_found') {
          this.store.dispatch({ type: 'jobExpired' });
          return;
        }
        const kind = failureKind(apiError);
        const count = ++failures[kind];
        if (count >= MAX_FAILURES[kind]) {
          this.store.dispatch({ type: 'connectionLost' });
          return;
        }
        await sleep(this.backoffMs(apiError, count), abort.signal);
        continue;
      }
      await sleep(this.api.pollIntervalMs, abort.signal);
    }
  }

  /**
   * After a lost connection: polls the same job again, at once, with fresh failure counts. Nothing is uploaded again.
   * Only a lost connection can be resumed; a job the server reports as failed or gone cannot.
   */
  resume(): void {
    const before = this.store.getState();
    this.store.dispatch({ type: 'resume' });
    const after = this.store.getState();
    if (before.phase === 'failed' && after.phase === 'working' && after.jobId) void this.poll(after.jobId, this.generation);
  }

  /**
   * How long to wait after a failed poll: what the server asked for in Retry-After (at most 30 s, and never
   * less than the poll interval), otherwise the poll interval doubled for each failure of that kind (at most 16 s).
   */
  private backoffMs(error: ApiError | null, count: number): number {
    const interval = this.api.pollIntervalMs;
    if (error?.retryAfterMs != null) return Math.min(Math.max(error.retryAfterMs, interval), MAX_RETRY_AFTER_MS);
    return Math.min(interval * 2 ** (count - 1), MAX_BACKOFF_MS);
  }

  /**
   * Back to the empty screen. A job the server still has is deleted, so that it stops counting against the
   * user's limit of active jobs at once, a running one is stopped and its files go now, not in an hour. Nothing
   * waits for the answer and nothing depends on it: a failed delete (the network, a job that is already gone)
   * leaves a job that ends and expires by itself.
   */
  startOver(): void {
    const jobId = this.store.getState().jobId;
    this.generation++;
    this.cancelRequests();
    this.revokePreviews();
    this.store.dispatch({ type: 'startOver' });
    if (jobId) void this.api.deleteJob(jobId).catch(() => undefined);
  }

  /** Stops the upload, the polling and every validation, waiting or in flight. */
  private cancelRequests(): void {
    this.pollAbort?.abort();
    this.submitAbort?.abort();
    this.pollAbort = null;
    this.submitAbort = null;
    this.validateTimers.forEach((t) => clearTimeout(t));
    this.validateTimers.clear();
    this.validateAborts.forEach((a) => a.abort());
    this.validateAborts.clear();
  }

  dismissProblem(): void {
    this.store.dispatch({ type: 'dismissProblem' });
  }

  private revokePreviews(): void {
    if (typeof URL === 'undefined' || typeof URL.revokeObjectURL !== 'function') return;
    for (const s of this.store.getState().sources) {
      if (s.previewUrl) URL.revokeObjectURL(s.previewUrl);
    }
  }

  // ---- review -------------------------------------------------------------

  select(index: number | null): void {
    this.store.dispatch({ type: 'select', index });
  }

  /** Narrow the results to one page (0-based), or to every page (null); it goes with the status filter. */
  setPageFilter(page: number | null): void {
    this.store.dispatch({ type: 'pageFilter', page });
  }

  setFilter(filter: Filter): void {
    this.store.dispatch({ type: 'filter', filter });
  }

  /** The editor reports a changed molfile. The server is asked to validate it once edits pause. */
  edit(index: number, molfile: string): void {
    this.store.dispatch({ type: 'edit', index, molfile });
    this.scheduleValidate(index, molfile);
  }

  reset(index: number): void {
    this.clearValidate(index);
    this.store.dispatch({ type: 'reset', index });
  }

  setChecked(index: number, checked: boolean): void {
    this.store.dispatch({ type: 'check', index, checked });
  }

  /** Forgets a waiting validation and aborts one in flight: its answer is about a structure that is gone. */
  private clearValidate(index: number): void {
    const timer = this.validateTimers.get(index);
    if (timer) clearTimeout(timer);
    this.validateTimers.delete(index);
    this.validateAborts.get(index)?.abort();
    this.validateAborts.delete(index);
  }

  private scheduleValidate(index: number, molfile: string): void {
    this.clearValidate(index);
    this.validateTimers.set(
      index,
      setTimeout(() => {
        this.validateTimers.delete(index);
        void this.runValidate(index, molfile);
      }, this.validateDelayMs),
    );
  }

  private async runValidate(index: number, molfile: string): Promise<void> {
    const jobId = this.store.getState().jobId;
    if (!jobId) return;
    const abort = new AbortController();
    this.validateAborts.set(index, abort);
    try {
      const derived = await retryWhenBusy(() => this.api.validate(jobId, index, molfile, abort.signal), abort.signal);
      this.store.dispatch({ type: 'validated', index, molfile, derived });
    } catch {
      // An aborted request is not a failed one: a newer edit has taken its place.
      if (!abort.signal.aborted) this.store.dispatch({ type: 'validationFailed', index, molfile });
    } finally {
      if (this.validateAborts.get(index) === abort) this.validateAborts.delete(index);
    }
  }

  // ---- export and report --------------------------------------------------

  /** The editor panel registers how to commit whatever is in the canvas to the store. */
  registerEditorFlush(flush: () => Promise<void>): () => void {
    this.editorFlush = flush;
    return () => {
      if (this.editorFlush === flush) this.editorFlush = null;
    };
  }

  /**
   * Exports the edited state. The editor is flushed first and the edits are read
   * from the store only afterwards, in one synchronous step, so an edit made a
   * moment ago is in the file. (The legacy page collected molfiles from 20
   * iframes while the ZIP request was already on its way.)
   */
  async download(format: ExportFormat): Promise<void> {
    const before = this.store.getState();
    // One export at a time, whichever Download menu asked (the header's or the one at the end of the figure).
    if (before.phase !== 'results' || before.exporting) return;
    this.store.dispatch({ type: 'exporting', value: true });
    try {
      if (!(await this.flushEditor())) {
        this.showNotice('error', strings.results.editorNotResponding);
        return;
      }
      const { job, edits } = this.store.getState();
      if (!job) return;
      const request = buildExportRequest(job, edits, format);
      // A download that finds the service busy is tried once more after Retry-After; nothing ends the wait early.
      const { blob, filename } = await retryWhenBusy(() => this.api.exportJob(job.id, request), new AbortController().signal);
      this.save(blob, filename);
      this.showNotice('info', strings.results.downloadStarted);
    } catch {
      this.showNotice('error', strings.results.downloadFailed);
    } finally {
      this.store.dispatch({ type: 'exporting', value: false });
    }
  }

  /** True when the editor has handed over everything (or there is no editor); false when it did not answer in time. */
  private async flushEditor(): Promise<boolean> {
    const flush = this.editorFlush;
    if (!flush) return true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const timeout = new Promise<'timeout'>((resolve) => {
      timer = setTimeout(() => resolve('timeout'), this.flushTimeoutMs);
    });
    try {
      return (await Promise.race([flush().then(() => 'done' as const), timeout])) === 'done';
    } finally {
      clearTimeout(timer);
    }
  }

  async report(index: number): Promise<boolean> {
    const jobId = this.store.getState().jobId;
    if (!jobId) return false;
    try {
      await this.api.report(jobId, index);
      this.store.dispatch({ type: 'reported', index });
      return true;
    } catch {
      this.showNotice('error', strings.editor.reportFailed);
      return false;
    }
  }

  // ---- notices ------------------------------------------------------------

  showNotice(kind: 'info' | 'error', text: string): void {
    if (this.noticeTimer) clearTimeout(this.noticeTimer);
    const id = ++this.noticeId;
    this.store.dispatch({ type: 'notice', notice: { id, kind, text } });
    this.noticeTimer = setTimeout(() => {
      if (this.store.getState().notice?.id === id) this.store.dispatch({ type: 'notice', notice: null });
    }, kind === 'error' ? 8000 : 4000);
  }

  dispose(): void {
    this.cancelRequests();
    if (this.noticeTimer) clearTimeout(this.noticeTimer);
  }
}
