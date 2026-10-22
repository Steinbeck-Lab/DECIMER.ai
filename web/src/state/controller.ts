import type { ApiClient } from '../api/client';
import {
  ApiError,
  isTerminal,
  type ExportFormat,
} from '../api/contract';
import { saveBlob } from '../download';
import { strings } from '../strings';
import { checkSelection } from '../upload/files';
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

/** Poll failures in a row before the page gives up on the job. */
const MAX_POLL_FAILURES = 3;

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
    let failures = 0;
    while (!abort.signal.aborted && generation === this.generation) {
      try {
        const job = await this.api.getJob(id, abort.signal);
        if (abort.signal.aborted || generation !== this.generation) return;
        failures = 0;
        this.store.dispatch({ type: 'jobUpdated', job });
        if (isTerminal(job)) return;
      } catch (error) {
        if (abort.signal.aborted || generation !== this.generation) return;
        failures++;
        // An expired job will not come back; anything else may be a hiccup.
        const gone = error instanceof ApiError && error.code === 'not_found';
        if (gone || failures >= MAX_POLL_FAILURES) {
          this.store.dispatch({ type: 'connectionLost' });
          return;
        }
      }
      await sleep(this.api.pollIntervalMs, abort.signal);
    }
  }

  startOver(): void {
    this.generation++;
    this.pollAbort?.abort();
    this.submitAbort?.abort();
    this.pollAbort = null;
    this.submitAbort = null;
    this.validateTimers.forEach((t) => clearTimeout(t));
    this.validateTimers.clear();
    this.validateAborts.forEach((a) => a.abort());
    this.validateAborts.clear();
    this.revokePreviews();
    this.store.dispatch({ type: 'startOver' });
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
      const derived = await this.api.validate(jobId, index, molfile, abort.signal);
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
      const { blob, filename } = await this.api.exportJob(job.id, request);
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
    this.pollAbort?.abort();
    this.submitAbort?.abort();
    this.validateTimers.forEach((t) => clearTimeout(t));
    this.validateAborts.forEach((a) => a.abort());
    if (this.noticeTimer) clearTimeout(this.noticeTimer);
  }
}
