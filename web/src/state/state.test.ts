import { expect, test } from 'vitest';
import { STAGE_ORDER, type Job } from '../api/contract';
import { fixtureResults } from '../mocks/fixtures';
import { initialState, type State } from './model';
import { reducer, type Action } from './reducer';
import {
  buildExportRequest,
  countsOf,
  filterCounts,
  filterViews,
  stepList,
  viewOf,
  viewsOf,
  workSummary,
} from './selectors';

const results = fixtureResults(8); // indexes 0..7, numbers 1,2,4,5,6,7,11,12; #6 (index 4) low confidence, #12 (index 7) not a structure

const job: Job = {
  id: 'job-1',
  status: 'done',
  stages: STAGE_ORDER.map((name) => ({ name, state: 'done' })),
  pages: 10,
  pagesProcessed: 10,
  truncated: false,
  structuresFound: 8,
  structuresDropped: 0,
  results,
  queuePosition: null,
  warnings: [],
  error: null,
  createdAt: '2026-10-22T12:00:00.000Z',
  expiresAt: '2026-10-22T13:00:00.000Z',
};

function run(...actions: Action[]): State {
  const start: State = { ...initialState, phase: 'results', jobId: job.id, job };
  return actions.reduce(reducer, start);
}

const byIndex = (state: State, index: number) => {
  const r = results[index];
  if (!r) throw new Error('no result');
  return viewOf(r, state.edits[index]);
};

const molfileOf = (marker: string) => `\n  Ketcher\n\n  0  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n${marker}`;

test('edit marks the result modified and stores its molfile; the original is untouched', () => {
  const state = run({ type: 'edit', index: 1, molfile: molfileOf('edited-2') });
  const view = byIndex(state, 1);
  expect(view.modified).toBe(true);
  expect(view.status).toBe('edited');
  expect(view.molfile).toBe(molfileOf('edited-2'));
  expect(results[1]?.molfile).not.toContain('edited-2');
  expect(byIndex(state, 0).modified).toBe(false);
});

test('reset brings back the original molfile, values and status', () => {
  const original = byIndex(run(), 4);
  const state = run(
    { type: 'edit', index: 4, molfile: molfileOf('x') },
    { type: 'validated', index: 4, molfile: molfileOf('x'), derived: { smiles: 'CC', inchikey: 'AAAAAAAAAAAAAA-BBBBBBBBBB-C', svg: 'x.svg', valid: true, name: null, nameTier: null } },
    { type: 'reset', index: 4 },
  );
  expect(byIndex(state, 4)).toEqual(original);
  expect(state.edits[4]).toBeUndefined();
});

test('a new edit after "checked" needs checking again', () => {
  const state = run(
    { type: 'check', index: 0, checked: true },
    { type: 'edit', index: 0, molfile: molfileOf('a') },
  );
  expect(byIndex(state, 0).checked).toBe(false);
  expect(byIndex(state, 0).status).toBe('edited');
});

test('checking an edited result shows it as checked and it stays in the Edited filter', () => {
  const state = run(
    { type: 'edit', index: 2, molfile: molfileOf('a') },
    { type: 'check', index: 2, checked: true },
  );
  const views = viewsOf(state);
  expect(byIndex(state, 2).status).toBe('checked');
  expect(filterViews(views, 'edited').map((v) => v.index)).toEqual([2]);
});

test('unchecking a result that was never edited leaves no trace', () => {
  const state = run({ type: 'check', index: 3, checked: true }, { type: 'check', index: 3, checked: false });
  expect(state.edits).toEqual({});
});

test('statuses: low confidence and classifier doubt are flagged, the rest is unchecked', () => {
  const views = viewsOf(run());
  expect(views.map((v) => v.status)).toEqual([
    'unchecked',
    'unchecked',
    'unchecked',
    'unchecked',
    'review',
    'unchecked',
    'unchecked',
    'notsure',
  ]);
  expect(views[4]?.reasons).toEqual(['lowConfidence']);
  expect(views[7]?.reasons).toEqual(['notStructure']);
});

test('an invalid SMILES goes to review with its reason; an R group is named as such', () => {
  const base = results[0];
  if (!base) throw new Error('no result');
  expect(viewOf({ ...base, valid: false, inchikey: '' }).status).toBe('review');
  expect(viewOf({ ...base, valid: false }).reasons).toEqual(['invalid']);
  expect(viewOf({ ...base, smiles: '*CC(=O)O' }).reasons).toEqual(['rgroup']);
});

test('after validation the new SMILES, name and validity replace the recognised ones', () => {
  const edited = molfileOf('a');
  const state = run(
    { type: 'edit', index: 4, molfile: edited },
    {
      type: 'validated',
      index: 4,
      molfile: edited,
      derived: { smiles: 'CCO', inchikey: 'LFQSCWFLJHTTHZ-UHFFFAOYSA-N', svg: 'new.svg', valid: true, name: 'ethanol', nameTier: 'pin_verified' },
    },
  );
  const view = byIndex(state, 4);
  expect(view).toMatchObject({ smiles: 'CCO', name: 'ethanol', svg: 'new.svg', validating: false });
});

test('a validate answer for an older edit is dropped', () => {
  const first = molfileOf('first');
  const second = molfileOf('second');
  const derived = (smiles: string) => ({ smiles, inchikey: '', svg: '', valid: true, name: null, nameTier: null });
  const state = run(
    { type: 'edit', index: 1, molfile: first },
    { type: 'edit', index: 1, molfile: second },
    { type: 'validated', index: 1, molfile: first, derived: derived('STALE') },
  );
  expect(state.edits[1]?.derived).toBeNull();
  expect(state.edits[1]?.validating).toBe(true);
  const done = reducer(state, { type: 'validated', index: 1, molfile: second, derived: derived('FRESH') });
  expect(done.edits[1]?.derived?.smiles).toBe('FRESH');
});

test('counts and filter counts follow the statuses', () => {
  const state = run(
    { type: 'edit', index: 0, molfile: molfileOf('a') },
    { type: 'check', index: 1, checked: true },
    { type: 'check', index: 4, checked: true }, // the flagged one is resolved by the user
  );
  const views = viewsOf(state);
  expect(countsOf(views)).toEqual({ total: 8, checked: 2, edited: 1, review: 0, notsure: 1, unchecked: 4 });
  expect(filterCounts(views)).toEqual({ all: 8, review: 1, edited: 1 });
  expect(filterViews(views, 'review').map((v) => v.number)).toEqual([12]);
});

test('export request carries only edited molfiles, keyed by result index in result order', () => {
  // Edit in the opposite order to the results, and check one that was not edited.
  const state = run(
    { type: 'edit', index: 6, molfile: molfileOf('six') },
    { type: 'edit', index: 2, molfile: molfileOf('two') },
    { type: 'check', index: 3, checked: true },
    { type: 'edit', index: 5, molfile: molfileOf('five') },
    { type: 'reset', index: 5 },
  );
  const request = buildExportRequest(job, state.edits, 'zip');
  expect(request.format).toBe('zip');
  expect(request.molfiles).toEqual({ 2: molfileOf('two'), 6: molfileOf('six') });
  expect(Object.keys(request.molfiles)).toEqual(['2', '6']);
  expect(workSummary(state.edits)).toEqual({ edited: 2, checked: 1 });
});

test('export keeps the latest edit of a result, not an earlier one', () => {
  const state = run(
    { type: 'edit', index: 1, molfile: molfileOf('v1') },
    { type: 'edit', index: 1, molfile: molfileOf('v2') },
  );
  expect(buildExportRequest(job, state.edits, 'sdf').molfiles[1]).toBe(molfileOf('v2'));
});

test('a job update for another job, or after the results arrived, is ignored', () => {
  const working: State = { ...initialState, phase: 'working', jobId: 'job-1' };
  expect(reducer(working, { type: 'jobUpdated', job: { ...job, id: 'other' } })).toBe(working);
  const done = reducer(working, { type: 'jobUpdated', job });
  expect(done.phase).toBe('results');
  expect(reducer(done, { type: 'jobUpdated', job: { ...job, status: 'running', results: [] } })).toBe(done);
});

test('a failed job and a lost connection end in the failed phase with their reason', () => {
  const working: State = { ...initialState, phase: 'working', jobId: 'job-1' };
  expect(reducer(working, { type: 'jobUpdated', job: { ...job, status: 'failed', error: 'boom' } }).failure).toEqual({
    kind: 'job',
    message: 'boom',
  });
  expect(reducer(working, { type: 'connectionLost' }).failure).toEqual({ kind: 'connection' });
});

test('start over drops the job, the edits and the selection, but the editor stays mounted', () => {
  const state = run({ type: 'edit', index: 0, molfile: molfileOf('a') }, { type: 'select', index: 0 }, { type: 'startOver' });
  expect(state).toEqual({ ...initialState, editorMounted: true });
});

test('the editor is mounted by the first selection and never unmounted by closing it', () => {
  expect(run().editorMounted).toBe(false);
  const open = run({ type: 'select', index: 1 });
  expect(open.editorMounted).toBe(true);
  expect(reducer(open, { type: 'select', index: null }).editorMounted).toBe(true);
});

// ---- review round: chips, stepping, invalid, stale, memo ---------------------------------

test('the Edited chip counts every modified result, also one that was checked afterwards', () => {
  const state = run(
    { type: 'edit', index: 2, molfile: molfileOf('a') },
    { type: 'check', index: 2, checked: true },
  );
  const views = viewsOf(state);
  // The chip and the filter it opens must agree.
  expect(filterCounts(views).edited).toBe(1);
  expect(filterViews(views, 'edited')).toHaveLength(filterCounts(views).edited);
  // Checked is its own status, but does not take the result out of "Edited".
  expect(countsOf(views).edited).toBe(0);
});

test('every chip count equals the number of tiles its filter shows', () => {
  const state = run(
    { type: 'edit', index: 0, molfile: molfileOf('a') },
    { type: 'edit', index: 4, molfile: molfileOf('b') },
    { type: 'check', index: 4, checked: true },
    { type: 'check', index: 7, checked: true },
  );
  const views = viewsOf(state);
  for (const filter of ['all', 'review', 'edited'] as const) {
    expect(filterCounts(views)[filter]).toBe(filterViews(views, filter).length);
  }
});

test('Previous and Next step through the active filter, also after the open result has left it', () => {
  // Needs review = index 4 (number 6, low confidence) and index 7 (number 12, classifier doubt).
  const open = (...actions: Action[]) => {
    const state = run(...actions);
    return viewsOf(state);
  };
  const before = open();
  expect(stepList(before, 'review', 4).map((v) => v.index)).toEqual([4, 7]);

  // Checking result 4 takes it out of "Needs review", but it stays the current one, in its place.
  const after = open({ type: 'check', index: 4, checked: true });
  expect(filterViews(after, 'review').map((v) => v.index)).toEqual([7]);
  const list = stepList(after, 'review', 4);
  expect(list.map((v) => v.index)).toEqual([4, 7]);
  // Next from 4 is 7 (the next flagged one), not 5 (the next in the whole figure).
  expect(list[list.findIndex((v) => v.index === 4) + 1]?.index).toBe(7);
});

test('stepping with no filter walks the whole figure, and a selection outside the list is not invented', () => {
  const views = viewsOf(run());
  expect(stepList(views, 'all', 3)).toHaveLength(8);
  expect(stepList(views, 'edited', null).map((v) => v.index)).toEqual([]);
});

test('an invalid structure after an edit is flagged whatever the status: edited, then checked', () => {
  const edited = molfileOf('empty canvas');
  const invalid = { smiles: '', inchikey: '', svg: 'x.svg', valid: false, name: null, nameTier: null };
  const afterEdit = run(
    { type: 'edit', index: 1, molfile: edited },
    { type: 'validated', index: 1, molfile: edited, derived: invalid },
  );
  const v1 = byIndex(afterEdit, 1);
  expect(v1.status).toBe('edited');
  expect(v1.invalid).toBe(true);
  expect(v1.reasons).toContain('invalid');

  // "Mark as checked" is allowed on an invalid structure (the user may mean it), but the warning stays.
  const afterCheck = reducer(afterEdit, { type: 'check', index: 1, checked: true });
  const v2 = byIndex(afterCheck, 1);
  expect(v2.status).toBe('checked');
  expect(v2.invalid).toBe(true);
  expect(v2.reasons).toContain('invalid');
  expect(byIndex(run(), 1).invalid).toBe(false);
});

test('a result is stale from the edit until the server answers, and after a failed answer', () => {
  const molfile = molfileOf('x');
  const validating = run({ type: 'edit', index: 2, molfile });
  expect(byIndex(validating, 2).stale).toBe(true);
  const answered = reducer(validating, {
    type: 'validated',
    index: 2,
    molfile,
    derived: { smiles: 'CC', inchikey: 'LFQSCWFLJHTTHZ-UHFFFAOYSA-N', svg: 'x.svg', valid: true, name: null, nameTier: null },
  });
  expect(byIndex(answered, 2).stale).toBe(false);
  const failed = reducer(validating, { type: 'validationFailed', index: 2, molfile });
  expect(byIndex(failed, 2).stale).toBe(true);
  expect(byIndex(run(), 2).stale).toBe(false);
});

test('views keep their identity when only another result changed, so memoised tiles do not re-render', () => {
  const before = viewsOf(run());
  const after = viewsOf(run({ type: 'edit', index: 3, molfile: molfileOf('a') }));
  expect(after[3]).not.toBe(before[3]);
  for (const i of [0, 1, 2, 4, 5, 6, 7]) expect(after[i]).toBe(before[i]);
  // And the same inputs give the same view.
  const r = results[0];
  if (!r) throw new Error('no result');
  expect(viewOf(r)).toBe(viewOf(r));
});

test('work summary counts edited and checked results, once each', () => {
  expect(workSummary({})).toEqual({ edited: 0, checked: 0 });
  const state = run(
    { type: 'edit', index: 0, molfile: molfileOf('a') },
    { type: 'check', index: 0, checked: true },
    { type: 'check', index: 1, checked: true },
  );
  // Result 0 was edited and then checked: it counts as edited and as checked.
  expect(workSummary(state.edits)).toEqual({ edited: 1, checked: 2 });
});

test('an edit whose molfile is an empty string is still exported (it is not the recognised structure)', () => {
  const state = run({ type: 'edit', index: 1, molfile: '' });
  expect(buildExportRequest(job, state.edits, 'sdf').molfiles).toEqual({ 1: '' });
});

// ---- classifier unavailable (isStructure null) --------------------------------------------

test('a null isStructure means the classifier gave no answer: unknown, not "not sure it is a structure"', () => {
  const doubted = results[7]; // number 12: the classifier said it is not a structure
  const fine = results[0];
  const lowConfidence = results[4]; // number 6
  if (!doubted || !fine || !lowConfidence) throw new Error('no results');
  expect(viewOf(doubted).status).toBe('notsure'); // false is still a doubt

  const unknown = viewOf({ ...doubted, isStructure: null });
  expect(unknown.status).toBe('unchecked'); // no peach, no "not sure"
  expect(unknown.reasons).toEqual([]);

  // The rest of the review logic does not depend on the classifier.
  expect(viewOf({ ...lowConfidence, isStructure: null })).toMatchObject({ status: 'review', reasons: ['lowConfidence'] });
  expect(viewOf({ ...fine, isStructure: null, valid: false, inchikey: '' })).toMatchObject({ status: 'review', reasons: ['invalid'] });
});

test('with no classifier answers there is nothing "not sure": counts, chips and the review filter', () => {
  const noClassifier: Job = { ...job, results: results.map((r) => ({ ...r, isStructure: null })) };
  const state: State = { ...initialState, phase: 'results', jobId: job.id, job: noClassifier };
  const views = viewsOf(state);
  expect(countsOf(views).notsure).toBe(0);
  expect(views.map((v) => v.status)).toEqual(['unchecked', 'unchecked', 'unchecked', 'unchecked', 'review', 'unchecked', 'unchecked', 'unchecked']);
  // Only the low-confidence one (number 6) is for review; number 12 is not, now that its "doubt" is unknown.
  expect(filterViews(views, 'review').map((v) => v.number)).toEqual([6]);
  expect(filterCounts(views).review).toBe(1);
});

// ---- results arrive only at the end ---------------------------------------------------------

test('polls before the end have structuresFound but no results: the job stays in the working phase', () => {
  const working: State = { ...initialState, phase: 'working', jobId: job.id };
  const midway: Job = { ...job, status: 'running', structuresFound: 12, results: [], stages: job.stages.map((s) => (s.name === 'check' ? { ...s, state: 'active' } : s)) };
  const after = reducer(working, { type: 'jobUpdated', job: midway });
  expect(after.phase).toBe('working');
  expect(after.job?.structuresFound).toBe(12);
  expect(viewsOf(after)).toEqual([]);
  // And when the job is done, the results are there and the phase moves on.
  const done = reducer(after, { type: 'jobUpdated', job });
  expect(done.phase).toBe('results');
  expect(viewsOf(done)).toHaveLength(8);
});
