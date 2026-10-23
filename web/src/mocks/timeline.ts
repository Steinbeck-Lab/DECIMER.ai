import { STAGE_ORDER, LIMITS, type Job, type JobWarning, type Result, type Stage, type StageState } from '../api/contract';
import { FIXTURE_PAGE_URL } from './fixtures';

export interface MockJobSpec {
  id: string;
  /** Pages in the upload. */
  pages: number;
  /** The upload is a PDF, of which only the first `LIMITS.maxPages` pages are processed; images are all processed. */
  pdf?: boolean;
  results: Result[];
  structuresFound: number;
  truncated: boolean;
  warnings?: JobWarning[];
  /** ISO 8601 times the API reports. */
  createdAt?: string;
  expiresAt?: string;
  /** When set the job ends `failed` with this message. */
  failure?: string;
}

/** Steps spent waiting in the queue (positions 2 then 1) before the first stage runs. */
export const QUEUE_STEPS = 2;

/**
 * What the four pipeline stages (render, segment, recognise, check) report at each step of a run. Like
 * the worker the stages overlap: a stage is active from its first piece of work and done
 * when its last piece ends and every stage before it is done. The upload stage is done from the start.
 */
export const RUN: readonly (readonly StageState[])[] = [
  ['active', 'pending', 'pending', 'pending'], // the first page is being rendered
  ['active', 'active', 'pending', 'pending'], // the first page is with seg while the others render
  ['done', 'active', 'pending', 'pending'], // every page rendered
  ['done', 'active', 'active', 'pending'], // the first structures are at ocsr, the rest of the pages at seg
  ['done', 'done', 'active', 'active'], // every page searched (the count is known); the first checks
  ['done', 'done', 'done', 'active'], // every structure recognised; the names
];
/** Steps of a run. The job is done at step `QUEUE_STEPS + RUN.length`. */
export const RUN_STEPS = RUN.length;
/** The step of `RUN` from which every page has been searched and the structure count is in the record. */
const COUNT_KNOWN_FROM = RUN.findIndex((states) => states[STAGE_ORDER.indexOf('segment') - 1] === 'done');
/** The steps in which pages are still being rendered. */
const RENDER_STEPS = RUN.filter((states) => states[0] !== 'done').length;
/** A failed job stops while recognising, with the stages as they were then. */
const FAILED_AT = RUN.findIndex((states) => states[2] === 'active');

/** One URL per processed page, as the API lists them (`pageUrls`); every page of a mock job is the same image. */
const pageUrlsOf = (processed: number): string[] => Array.from({ length: processed }, () => FIXTURE_PAGE_URL);

/** The record's stage list for the four pipeline stages of one step (upload is always done once the job runs). */
function stagesOf(states: readonly StageState[]): Stage[] {
  return STAGE_ORDER.map((name, i) => ({ name, state: i === 0 ? 'done' : (states[i - 1] ?? 'pending') }));
}

/**
 * The job as the API would report it `elapsedMs` after it was created. A pure
 * function of time, so replays are deterministic and tests need no timers:
 * queued (2, 1), then the steps of `RUN` (the stages overlap), then done (or failed).
 */
export function jobAt(spec: MockJobSpec, elapsedMs: number, stepMs: number): Job {
  const step = Math.max(0, Math.floor(elapsedMs / stepMs));
  const stepFraction = Math.min(1, Math.max(0, (elapsedMs - step * stepMs) / stepMs));
  const processed = spec.pdf ? Math.min(spec.pages, LIMITS.maxPages) : spec.pages;
  const base = {
    id: spec.id,
    pages: spec.pages,
    truncated: spec.truncated,
    structuresDropped: Math.max(0, spec.structuresFound - spec.results.length),
    warnings: spec.warnings ?? [],
    createdAt: spec.createdAt ?? '1970-01-01T00:00:00.000Z',
    expiresAt: spec.expiresAt ?? '1970-01-01T01:00:00.000Z',
  };
  const idleStages = stagesOf([]);

  if (step < QUEUE_STEPS) {
    return {
      ...base,
      status: 'queued',
      queuePosition: QUEUE_STEPS - step,
      error: null,
      stages: idleStages,
      pagesProcessed: 0,
      pageUrls: [],
      structuresFound: 0,
      results: [],
    };
  }

  const run = step - QUEUE_STEPS;
  const states = RUN[run];
  if (states) {
    // Pages processed grow while the pages are being rendered, over all the steps that render.
    const pagesProcessed =
      run >= RENDER_STEPS ? processed : Math.round(processed * ((run + stepFraction) / RENDER_STEPS));
    return {
      ...base,
      status: 'running',
      queuePosition: null,
      error: null,
      stages: stagesOf(states),
      pagesProcessed,
      pageUrls: pageUrlsOf(pagesProcessed),
      structuresFound: run >= COUNT_KNOWN_FROM ? spec.structuresFound : 0,
      results: [],
    };
  }

  if (spec.failure !== undefined) {
    return {
      ...base,
      status: 'failed',
      queuePosition: null,
      error: spec.failure,
      stages: stagesOf(RUN[FAILED_AT] ?? []),
      pagesProcessed: processed,
      pageUrls: pageUrlsOf(processed),
      structuresFound: 0,
      results: [],
    };
  }

  return {
    ...base,
    status: 'done',
    queuePosition: null,
    error: null,
    stages: STAGE_ORDER.map((name) => ({ name, state: 'done' })),
    pagesProcessed: processed,
    pageUrls: pageUrlsOf(processed),
    structuresFound: spec.structuresFound,
    results: spec.results,
  };
}
