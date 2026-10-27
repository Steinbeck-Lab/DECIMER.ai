import {
  LIMITS,
  STAGE_ORDER,
  type ExportFormat,
  type ExportRequest,
  type Job,
  type Result,
  type StageName,
  type StageState,
  type ValidBy,
} from '../api/contract';
import type { EditEntry, Filter, SourceFile, State, Status } from './model';

/** Below this the model is not confident and the result goes to "review". */
const REVIEW_CONFIDENCE_BELOW = 0.8;

export type Reason = 'incomplete' | 'notStructure' | 'invalid' | 'cdkOnly' | 'rgroup' | 'lowConfidence';

export interface ResultView {
  index: number;
  number: number;
  result: Result;
  smiles: string;
  inchikey: string;
  svg: string;
  /** RDKit's word. A structure only CDK accepted is `valid: false` with `validBy: 'cdk'`. */
  valid: boolean;
  /** Who accepted the structure as it is now: after an edit, the server's answer says (RDKit, or nobody). */
  validBy: ValidBy | null;
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
  validBy?: ValidBy | null;
  confidence: number;
  smiles: string;
  /** The model stopped at its token limit: the SMILES is cut off (decision D25). */
  incomplete?: boolean;
}): Reason[] {
  const reasons: Reason[] = [];
  // A cut-off SMILES is never taken for a reading: until the UI shows the flag itself, this puts the result
  // under "Needs review" whatever RDKit, CDK and the model's confidence say about the text.
  if (r.incomplete) reasons.push('incomplete');
  // Only an answer of "no" is a doubt. `null` is a classifier that was not there: nothing to say.
  if (r.isStructure === false) reasons.push('notStructure');
  // A structure only CDK accepted stays a reason to look (RDKit, the stricter reader, refused it, so it is
  // "review", not "unchecked"), but it is not a plain "invalid SMILES": the wording says who accepted it.
  if (!r.valid) reasons.push(r.validBy === 'cdk' ? 'cdkOnly' : 'invalid');
  else if (R_GROUP.test(r.smiles)) reasons.push('rgroup');
  if (r.confidence < REVIEW_CONFIDENCE_BELOW) reasons.push('lowConfidence');
  return reasons;
}

function buildView(result: Result, edit?: EditEntry): ResultView {
  const derived = edit?.derived ?? null;
  const smiles = derived?.smiles ?? result.smiles;
  const valid = derived?.valid ?? result.valid;
  // The server's answer to an edit is RDKit's alone (validate has no CDK): valid, or neither accepts it.
  const validBy = derived ? (derived.valid ? 'rdkit' : null) : result.validBy;
  const modified = edit?.modified ?? false;
  const checked = edit?.checked ?? false;
  const reasons = reasonsFor({ isStructure: result.isStructure, valid, validBy, confidence: result.confidence, smiles, incomplete: result.incomplete });

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
    validBy,
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

function matchesFilter(view: ResultView, filter: Filter): boolean {
  if (filter === 'all') return true;
  if (filter === 'review') return view.status === 'review' || view.status === 'notsure';
  // "Edited" is everything the user changed, whether or not they have since checked it.
  return view.modified;
}

/** A page filter and a status filter together: both must hold. `page` is 0-based; null is every page. */
const onPage = (view: ResultView, page: number | null) => page === null || view.result.pageIndex === page;

export function filterViews(views: readonly ResultView[], filter: Filter, page: number | null = null): ResultView[] {
  return views.filter((v) => onPage(v, page) && matchesFilter(v, filter));
}

/** Filter chip counts. Each is the number of tiles its filter shows, by the same predicate (on the chosen page, if one is). */
export function filterCounts(views: readonly ResultView[], page: number | null = null): Record<Filter, number> {
  return {
    all: filterViews(views, 'all', page).length,
    review: filterViews(views, 'review', page).length,
    edited: filterViews(views, 'edited', page).length,
  };
}

/**
 * The list Previous and Next walk: what the active filter shows, plus the open
 * result itself where it sits in the figure, even if it has just left the filter
 * (checking a result under "Needs review" must not make Next jump back to the top).
 */
export function stepList(
  views: readonly ResultView[],
  filter: Filter,
  selected: number | null,
  page: number | null = null,
): ResultView[] {
  return views.filter((v) => v.index === selected || (onPage(v, page) && matchesFilter(v, filter)));
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

/** The results screen with at least one structure: the figure, the editor and the header's Download menu. */
export function hasResults(state: Pick<State, 'phase' | 'job'>): boolean {
  return state.phase === 'results' && (state.job?.results.length ?? 0) > 0;
}

export function hasWork(edits: Record<number, EditEntry>): boolean {
  const { edited, checked } = workSummary(edits);
  return edited + checked > 0;
}

/** The upload was a PDF (else images, one page each). Only a PDF has page limits and a document to show. */
export function isPdfUpload(sources: readonly Pick<SourceFile, 'kind'>[]): boolean {
  return sources.some((source) => source.kind === 'pdf');
}

/**
 * Pages the job processes: a PDF's first `LIMITS.maxPages`, but every image of an upload of images (the API takes up
 * to 50, and processes them all). The same number as the job's `pagesProcessed`, without waiting for the worker.
 */
export function pagesToProcess(job: Pick<Job, 'pages'>, pdf: boolean): number {
  return pdf ? Math.min(job.pages, LIMITS.maxPages) : job.pages;
}

/** Page indexes are 0-based in the API; people count from 1. */
export function pageNumber(view: Pick<ResultView, 'result'>): number {
  return view.result.pageIndex + 1;
}

export interface StageProgress {
  /** Each stage's own state. The worker overlaps its stages, so several can be active at once. */
  states: Record<StageName, StageState>;
  /** Index in `STAGE_ORDER` of the stage the page speaks of: the latest one that is active, else the first one that is not done. */
  current: number;
  /** Indexes of the stages that are active now, in pipeline order (`current` is the last of them, when there are any). */
  active: number[];
  /** The active stages other than the current one: what runs alongside it. */
  alongside: StageName[];
  doneCount: number;
}

/**
 * Where a running job is. A stage is done only when every stage before it is (the API's rule), so the done
 * stages are a prefix of `STAGE_ORDER`; the active ones follow, one or several. Before the job exists the
 * first stage is the current one.
 */
export function stageProgress(job: Pick<Job, 'stages'> | null): StageProgress {
  const states = Object.fromEntries(
    STAGE_ORDER.map((name) => [name, job?.stages.find((s) => s.name === name)?.state ?? 'pending']),
  ) as Record<StageName, StageState>;
  if (!job) return { states, current: 0, active: [], alongside: [], doneCount: 0 };
  const doneCount = STAGE_ORDER.filter((name) => states[name] === 'done').length;
  const activeNames = STAGE_ORDER.filter((name) => states[name] === 'active');
  const active = STAGE_ORDER.flatMap((name, i) => (states[name] === 'active' ? [i] : []));
  const current = active.at(-1) ?? Math.min(doneCount, STAGE_ORDER.length - 1);
  return { states, current, active, alongside: activeNames.slice(0, -1), doneCount };
}
