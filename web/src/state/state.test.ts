import { expect, test } from 'vitest';
import { STAGE_ORDER, type Job, type Result } from '../api/contract';
import { fixtureResults } from '../mocks/fixtures';
import { initialState, type State } from './model';
import { reducer, type Action } from './reducer';
import { strings } from '../strings';
import {
  buildExportRequest,
  countsOf,
  filterCounts,
  filterViews,
  isPdfUpload,
  pagesToProcess,
  reasonsFor,
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
  pageUrls: [],
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
    { type: 'validated', index: 4, molfile: molfileOf('x'), derived: { smiles: 'CC', inchikey: 'AAAAAAAAAAAAAA-BBBBBBBBBB-C', svg: 'x.svg', valid: true, name: null, nameTier: null, nameVerified: null } },
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

// ---- a structure only CDK accepted --------------------------------------------------------------------------------

const cdkOnly = (over: Partial<Result> = {}): Result => {
  const base = results[0];
  if (!base) throw new Error('no result');
  return { ...base, valid: false, validBy: 'cdk', inchikey: '', name: null, nameTier: null, nameVerified: null, ...over };
};

test('a structure only CDK accepted goes to review for its own reason, not as an invalid SMILES', () => {
  const view = viewOf(cdkOnly());
  expect(view).toMatchObject({ valid: false, validBy: 'cdk', invalid: true, status: 'review' });
  expect(view.reasons).toEqual(['cdkOnly']);
  // Low confidence is a second reason, as it is for any other result.
  expect(viewOf(cdkOnly({ confidence: 0.5 })).reasons).toEqual(['cdkOnly', 'lowConfidence']);
  // And the classifier's doubt still decides the status ("not sure"), as for an invalid SMILES.
  expect(viewOf(cdkOnly({ isStructure: false })).status).toBe('notsure');
});

test('a structure nobody accepted is still an invalid SMILES, whoever recognised it', () => {
  expect(viewOf(cdkOnly({ validBy: null })).reasons).toEqual(['invalid']);
  expect(reasonsFor({ isStructure: true, valid: false, confidence: 0.9, smiles: 'C(' })).toEqual(['invalid']);
  expect(reasonsFor({ isStructure: true, valid: false, validBy: 'cdk', confidence: 0.9, smiles: 'C(' })).toEqual(['cdkOnly']);
});

test('CDK-only does not make an RDKit-valid result review', () => {
  expect(viewOf({ ...cdkOnly(), valid: true, validBy: 'rdkit', inchikey: 'AAAAAAAAAAAAAA-BBBBBBBBBB-C' })).toMatchObject({
    status: 'unchecked',
    reasons: [],
    validBy: 'rdkit',
  });
});

// ---- a structure the model cut off at its token limit -------------------------------------------------------------

const incomplete = (over: Partial<Result> = {}): Result => {
  const base = results[0];
  if (!base) throw new Error('no result');
  return { ...base, incomplete: true, name: null, nameTier: null, nameVerified: null, ...over };
};

test('an incomplete result is always "Needs review", however well RDKit and the model rate the cut-off text', () => {
  // Valid, a structure, confident: nothing else would flag it.
  const view = viewOf(incomplete());
  expect(view).toMatchObject({ valid: true, validBy: 'rdkit', status: 'review' });
  expect(view.reasons).toEqual(['incomplete']);
  expect(viewOf(incomplete({ incomplete: false }))).toMatchObject({ status: 'unchecked', reasons: [] });
  // It is a reason beside the others, and the first one.
  expect(viewOf(incomplete({ confidence: 0.5 })).reasons).toEqual(['incomplete', 'lowConfidence']);
  expect(viewOf(incomplete({ valid: false, validBy: 'cdk' })).reasons).toEqual(['incomplete', 'cdkOnly']);
  expect(viewOf(incomplete({ valid: false, validBy: null })).reasons).toEqual(['incomplete', 'invalid']);
  // The classifier's doubt still decides the status, as for any other reason.
  expect(viewOf(incomplete({ isStructure: false })).status).toBe('notsure');
  expect(reasonsFor({ isStructure: true, valid: true, confidence: 0.9, smiles: 'CC', incomplete: true })).toEqual(['incomplete']);
  expect(reasonsFor({ isStructure: true, valid: true, confidence: 0.9, smiles: 'CC' })).toEqual([]);
});

test('an incomplete result is listed under the review filter, and an edit or a check takes it out of review', () => {
  const cutOff = incomplete({ index: 0 });
  const start: State = { ...initialState, phase: 'results', jobId: job.id, job: { ...job, results: [cutOff, ...results.slice(1)] } };
  expect(filterViews(viewsOf(start), 'review').map((v) => v.index)).toContain(0);
  const edited = [{ type: 'edit', index: 0, molfile: molfileOf('x') } as Action].reduce(reducer, start);
  expect(viewsOf(edited)[0]?.status).toBe('edited');
  expect(filterViews(viewsOf(edited), 'review').map((v) => v.index)).not.toContain(0);
  const checked = [{ type: 'check', index: 0, checked: true } as Action].reduce(reducer, start);
  expect(viewsOf(checked)[0]?.status).toBe('checked');
});

test('the editor has wording for an incomplete result', () => {
  expect(strings.editor.reasons.incomplete).toBe('Incomplete: the model stopped at its length limit; the structure is cut off.');
});

test('after an edit the server answers for RDKit alone: valid is RDKit, an invalid answer is nobody', () => {
  const edited = molfileOf('a');
  const start: State = { ...initialState, phase: 'results', jobId: job.id, job: { ...job, results: [cdkOnly()] } };
  const answer = (valid: boolean): Action => ({
    type: 'validated',
    index: 0,
    molfile: edited,
    derived: { smiles: valid ? 'CCO' : '', inchikey: '', svg: '', valid, name: null, nameTier: null, nameVerified: null },
  });
  const after = (valid: boolean) =>
    viewsOf([{ type: 'edit', index: 0, molfile: edited } as Action, answer(valid)].reduce(reducer, start))[0];
  expect(after(true)).toMatchObject({ valid: true, validBy: 'rdkit', status: 'edited', reasons: [] });
  const refused = after(false);
  expect(refused).toMatchObject({ valid: false, validBy: null });
  expect(refused?.reasons).toEqual(['invalid']); // not "only CDK": the structure is the user's now
});

test('after validation the new SMILES, name and validity replace the recognised ones', () => {
  const edited = molfileOf('a');
  const state = run(
    { type: 'edit', index: 4, molfile: edited },
    {
      type: 'validated',
      index: 4,
      molfile: edited,
      derived: { smiles: 'CCO', inchikey: 'LFQSCWFLJHTTHZ-UHFFFAOYSA-N', svg: 'new.svg', valid: true, name: 'ethanol', nameTier: 'pin_verified', nameVerified: 'opsin' },
    },
  );
  const view = byIndex(state, 4);
  expect(view).toMatchObject({ smiles: 'CCO', name: 'ethanol', svg: 'new.svg', validating: false });
});

test('a validate answer for an older edit is dropped', () => {
  const first = molfileOf('first');
  const second = molfileOf('second');
  const derived = (smiles: string) => ({ smiles, inchikey: '', svg: '', valid: true, name: null, nameTier: null, nameVerified: null });
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
  expect(reducer(working, { type: 'jobExpired' }).failure).toEqual({ kind: 'expired' });
});

test('a lost connection keeps the job and its id: the page can pick the same job up again', () => {
  const running: State = { ...initialState, phase: 'working', jobId: 'job-1', startedAt: 5, job: { ...job, status: 'running', results: [] } };
  const lost = reducer(running, { type: 'connectionLost' });
  expect(lost).toMatchObject({ phase: 'failed', jobId: 'job-1', startedAt: 5, job: running.job });
});

test('resume goes back to working on the same job, but only from a lost connection', () => {
  const running: State = { ...initialState, phase: 'working', jobId: 'job-1', startedAt: 5, job: { ...job, status: 'running', results: [] } };
  const lost = reducer(running, { type: 'connectionLost' });
  expect(reducer(lost, { type: 'resume' })).toEqual({ ...running, failure: null });

  // A job the server reports as failed or gone would only fail again; so would a job that is not there.
  const failedJob = reducer(running, { type: 'jobUpdated', job: { ...job, status: 'failed', error: 'boom' } });
  expect(reducer(failedJob, { type: 'resume' })).toBe(failedJob);
  const expired = reducer(running, { type: 'jobExpired' });
  expect(reducer(expired, { type: 'resume' })).toBe(expired);
  expect(reducer(running, { type: 'resume' })).toBe(running);
  const noJob: State = { ...lost, jobId: null };
  expect(reducer(noJob, { type: 'resume' })).toBe(noJob);
});

test('an answer that arrives after the connection was given up on is not shown', () => {
  const lost = reducer({ ...initialState, phase: 'working', jobId: 'job-1' }, { type: 'connectionLost' });
  expect(reducer(lost, { type: 'jobUpdated', job })).toBe(lost);
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
  const invalid = { smiles: '', inchikey: '', svg: 'x.svg', valid: false, name: null, nameTier: null, nameVerified: null };
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
    derived: { smiles: 'CC', inchikey: 'LFQSCWFLJHTTHZ-UHFFFAOYSA-N', svg: 'x.svg', valid: true, name: null, nameTier: null, nameVerified: null },
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

test('a PDF is limited to its first 10 pages, images are all processed (the API takes up to 50)', () => {
  expect(isPdfUpload([{ kind: 'pdf' }])).toBe(true);
  expect(isPdfUpload([{ kind: 'image' }, { kind: 'image' }])).toBe(false);
  expect(isPdfUpload([])).toBe(false);
  expect(pagesToProcess({ pages: 14 }, true)).toBe(10);
  expect(pagesToProcess({ pages: 3 }, true)).toBe(3);
  expect(pagesToProcess({ pages: 12 }, false)).toBe(12);
  expect(pagesToProcess({ pages: 50 }, false)).toBe(50);
});

test('the capacity line counts a PDF against its page limit and an image upload against none', () => {
  expect(strings.results.capacity(8, 10, true)).toBe('8 of 20 structures · 10 of 10 pages');
  expect(strings.results.capacity(8, 1, false)).toBe('8 of 20 structures · 1 image');
  expect(strings.results.capacity(8, 12, false)).toBe('8 of 20 structures · 12 images');
});

// The page filter: a page and a status together; "All" lets go of the page.
test('a page filter narrows to one page and a status filter to a status: both must hold, and the counts follow the page', () => {
  const onPages = results.map((r, i) => ({ ...r, pageIndex: i < 5 ? 0 : 2 })); // indexes 0..4 on page 1, 5..7 on page 3
  const views = onPages.map((r) => viewOf(r));
  expect(filterViews(views, 'all', 2).map((v) => v.index)).toEqual([5, 6, 7]);
  expect(filterViews(views, 'all', null)).toHaveLength(8);
  // "Needs review": #6 (index 4, page 1) and #12 (index 7, page 3).
  expect(filterViews(views, 'review', 0).map((v) => v.index)).toEqual([4]);
  expect(filterViews(views, 'review', 2).map((v) => v.index)).toEqual([7]);
  expect(filterCounts(views, 2)).toEqual({ all: 3, review: 1, edited: 0 });
  expect(filterCounts(views)).toEqual({ all: 8, review: 2, edited: 0 });
  // Previous and Next stay on the page, and keep the open result where it sits.
  expect(stepList(views, 'all', 6, 2).map((v) => v.index)).toEqual([5, 6, 7]);
  expect(stepList(views, 'all', 0, 2).map((v) => v.index)).toEqual([0, 5, 6, 7]);
});

test('the reducer keeps the page with the status filter, lets go of it for "All", and starts afresh with a new job', () => {
  let state: State = { ...initialState, phase: 'results', job };
  state = reducer(state, { type: 'pageFilter', page: 2 });
  expect(state.pageFilter).toBe(2);
  state = reducer(state, { type: 'filter', filter: 'review' });
  expect(state).toMatchObject({ filter: 'review', pageFilter: 2 }); // both hold
  state = reducer(state, { type: 'filter', filter: 'all' });
  expect(state).toMatchObject({ filter: 'all', pageFilter: null }); // All is everything
  // Choosing a page, then "All" while the status filter is already all, still lets go of the page.
  state = reducer(state, { type: 'pageFilter', page: 1 });
  expect(reducer(state, { type: 'filter', filter: 'all' }).pageFilter).toBeNull();
  // The same page again is no change at all (the store notifies no one).
  expect(reducer(state, { type: 'pageFilter', page: 1 })).toBe(state);
  expect(reducer(state, { type: 'startOver' }).pageFilter).toBeNull();
});
