import type { ExportFormat, ExportRequest, Job, Result } from '../api/contract';
import type { EditEntry, Filter, State, Status } from './model';

/** Below this the model is not confident and the result goes to "review". */
export const REVIEW_CONFIDENCE_BELOW = 0.8;

export type Reason = 'notStructure' | 'invalid' | 'rgroup' | 'lowConfidence';

export interface ResultView {
  index: number;
  number: number;
  result: Result;
  smiles: string;
  inchikey: string;
  svg: string;
  valid: boolean;
  name: string | null;
  nameTier: string | null;
  nameVerified: string | null;
  /** The molfile the editor and the export use. */
  molfile: string;
  modified: boolean;
  checked: boolean;
  validating: boolean;
  validationFailed: boolean;
  /** The server rejected the current structure (also after an edit, whatever the status). */
  invalid: boolean;
  /** The values above are from before the last edit: the server has not answered yet, or failed to. */
  stale: boolean;
  status: Status;
  reasons: Reason[];
}

const R_GROUP = /\*|\[R\d*[^a-z]/;

export function reasonsFor(r: {
  isStructure: boolean | null;
  valid: boolean;
  confidence: number;
  smiles: string;
}): Reason[] {
  const reasons: Reason[] = [];
  // Only an answer of "no" is a doubt. `null` is a classifier that was not there: nothing to say.
  if (r.isStructure === false) reasons.push('notStructure');
  if (!r.valid) reasons.push('invalid');
  else if (R_GROUP.test(r.smiles)) reasons.push('rgroup');
  if (r.confidence < REVIEW_CONFIDENCE_BELOW) reasons.push('lowConfidence');
  return reasons;
}

function buildView(result: Result, edit?: EditEntry): ResultView {
  const derived = edit?.derived ?? null;
  const smiles = derived?.smiles ?? result.smiles;
  const valid = derived?.valid ?? result.valid;
  const modified = edit?.modified ?? false;
  const checked = edit?.checked ?? false;
  const reasons = reasonsFor({ isStructure: result.isStructure, valid, confidence: result.confidence, smiles });

  let status: Status;
  if (checked) status = 'checked';
  else if (modified) status = 'edited';
  else if (result.isStructure === false) status = 'notsure';
  else if (reasons.length > 0) status = 'review';
  else status = 'unchecked';

  return {
    index: result.index,
    number: result.number,
    result,
    smiles,
    inchikey: derived?.inchikey ?? result.inchikey,
    svg: derived?.svg ?? result.svg,
    valid,
    name: derived ? derived.name : result.name,
    nameTier: derived ? derived.nameTier : result.nameTier,
    nameVerified: derived ? derived.nameVerified : result.nameVerified,
    molfile: edit?.molfile ?? result.molfile,
    modified,
    checked,
    validating: edit?.validating ?? false,
    validationFailed: edit?.validationFailed ?? false,
    invalid: !valid,
    stale: (edit?.validating ?? false) || (edit?.validationFailed ?? false),
    status,
    reasons,
  };
}

const NO_EDIT: EditEntry = {
  molfile: null,
  modified: false,
  checked: false,
  derived: null,
  validating: false,
  validationFailed: false,
};
const viewCache = new WeakMap<Result, WeakMap<EditEntry, ResultView>>();

/**
 * The view of one result. The same result with the same edit entry gives the
 * very same object, and the reducer only replaces the entry of the result that
 * changed, so every other tile keeps its props and `memo` can skip it.
 */
export function viewOf(result: Result, edit?: EditEntry): ResultView {
  const key = edit ?? NO_EDIT;
  let byEdit = viewCache.get(result);
  if (!byEdit) {
    byEdit = new WeakMap();
    viewCache.set(result, byEdit);
  }
  let view = byEdit.get(key);
  if (!view) {
    view = buildView(result, edit);
    byEdit.set(key, view);
  }
  return view;
}

export function viewsOf(state: Pick<State, 'job' | 'edits'>): ResultView[] {
  return (state.job?.results ?? []).map((r) => viewOf(r, state.edits[r.index]));
}

export type Counts = Record<Status, number> & { total: number };

export function countsOf(views: readonly ResultView[]): Counts {
  const counts: Counts = { checked: 0, edited: 0, review: 0, notsure: 0, unchecked: 0, total: views.length };
  for (const v of views) counts[v.status]++;
  return counts;
}

export function matchesFilter(view: ResultView, filter: Filter): boolean {
  if (filter === 'all') return true;
  if (filter === 'review') return view.status === 'review' || view.status === 'notsure';
  // "Edited" is everything the user changed, whether or not they have since checked it.
  return view.modified;
}

export function filterViews(views: readonly ResultView[], filter: Filter): ResultView[] {
  return views.filter((v) => matchesFilter(v, filter));
}

/** Filter chip counts. Each is the number of tiles its filter shows, by the same predicate. */
export function filterCounts(views: readonly ResultView[]): Record<Filter, number> {
  return {
    all: filterViews(views, 'all').length,
    review: filterViews(views, 'review').length,
    edited: filterViews(views, 'edited').length,
  };
}

/**
 * The list Previous and Next walk: what the active filter shows, plus the open
 * result itself where it sits in the figure, even if it has just left the filter
 * (checking a result under "Needs review" must not make Next jump back to the top).
 */
export function stepList(views: readonly ResultView[], filter: Filter, selected: number | null): ResultView[] {
  return views.filter((v) => v.index === selected || matchesFilter(v, filter));
}

/**
 * The request for `/export`. Built from the store after the editor has been
 * flushed (see AppController.download), so it always carries what is on screen.
 * Keys are listed in the job's result order; results nobody edited are absent.
 */
export function buildExportRequest(
  job: Pick<Job, 'results'>,
  edits: Record<number, EditEntry>,
  format: ExportFormat,
): ExportRequest {
  const molfiles: Record<number, string> = {};
  for (const r of job.results) {
    const edit = edits[r.index];
    // `!== null`, not truthiness: an emptied canvas is still an edit and must not fall back to the recognised molfile.
    if (edit?.modified && edit.molfile !== null) molfiles[r.index] = edit.molfile;
  }
  return { format, molfiles };
}

/** What would be lost by starting over: results the user edited, and results they checked. */
export function workSummary(edits: Record<number, EditEntry>): { edited: number; checked: number } {
  const entries = Object.values(edits);
  return {
    edited: entries.filter((e) => e.modified).length,
    checked: entries.filter((e) => e.checked).length,
  };
}

export function hasWork(edits: Record<number, EditEntry>): boolean {
  const { edited, checked } = workSummary(edits);
  return edited + checked > 0;
}

/** Page indexes are 0-based in the API; people count from 1. */
export function pageNumber(view: Pick<ResultView, 'result'>): number {
  return view.result.pageIndex + 1;
}
