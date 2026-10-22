import { STAGE_ORDER, LIMITS, type Job, type JobWarning, type Result, type Stage } from '../api/contract';

export interface MockJobSpec {
  id: string;
  /** Pages in the document. */
  pages: number;
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
 * The job as the API would report it `elapsedMs` after it was created. A pure
 * function of time, so replays are deterministic and tests need no timers:
 * queued (2, 1) then one step per stage, then done (or failed).
 */
export function jobAt(spec: MockJobSpec, elapsedMs: number, stepMs: number): Job {
  const step = Math.max(0, Math.floor(elapsedMs / stepMs));
  const stepFraction = Math.min(1, Math.max(0, (elapsedMs - step * stepMs) / stepMs));
  const processed = Math.min(spec.pages, LIMITS.maxPages);
  const base = {
    id: spec.id,
    pages: spec.pages,
    truncated: spec.truncated,
    structuresDropped: Math.max(0, spec.structuresFound - spec.results.length),
    warnings: spec.warnings ?? [],
    createdAt: spec.createdAt ?? '1970-01-01T00:00:00.000Z',
    expiresAt: spec.expiresAt ?? '1970-01-01T01:00:00.000Z',
  };
  const idleStages: Stage[] = STAGE_ORDER.map((name) => ({ name, state: 'pending' }));

  if (step < QUEUE_STEPS) {
    return {
      ...base,
      status: 'queued',
      queuePosition: QUEUE_STEPS - step,
      error: null,
      stages: idleStages,
      pagesProcessed: 0,
      structuresFound: 0,
      results: [],
    };
  }

  const active = step - QUEUE_STEPS;
  if (active < STAGE_ORDER.length) {
    const renderIndex = STAGE_ORDER.indexOf('render');
    const segmentIndex = STAGE_ORDER.indexOf('segment');
    const pagesProcessed =
      active < renderIndex ? 0 : active === renderIndex ? Math.round(processed * stepFraction) : processed;
    return {
      ...base,
      status: 'running',
      queuePosition: null,
      error: null,
      stages: STAGE_ORDER.map((name, i) => ({
        name,
        state: i < active ? 'done' : i === active ? 'active' : 'pending',
      })),
      pagesProcessed,
      structuresFound: active > segmentIndex ? spec.structuresFound : 0,
      results: [],
    };
  }

  if (spec.failure !== undefined) {
    return {
      ...base,
      status: 'failed',
      queuePosition: null,
      error: spec.failure,
      stages: STAGE_ORDER.map((name, i) => ({
        name,
        state: i < STAGE_ORDER.length - 1 ? 'done' : 'active',
      })),
      pagesProcessed: processed,
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
    structuresFound: spec.structuresFound,
    results: spec.results,
  };
}
