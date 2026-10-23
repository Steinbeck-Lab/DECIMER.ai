import { expect, test } from 'vitest';
import { LIMITS, STAGE_ORDER } from '../api/contract';
import { MockApiClient, scenarioFor } from './MockApiClient';
import { fixtureResults, FIXTURE_COMPOUNDS } from './fixtures';
import { atomCount } from './MockApiClient';
import { jobAt, QUEUE_STEPS, RUN, RUN_STEPS } from './timeline';

const spec = { id: 'j', pages: 10, results: fixtureResults(8), structuresFound: 8, truncated: false };
const STEP = 100;

test('the fixtures are the paper numbers, with names where Orthonym verified one', () => {
  expect(fixtureResults(8).map((r) => r.number)).toEqual([1, 2, 4, 5, 6, 7, 11, 12]);
  expect(fixtureResults(8).filter((r) => r.name === null).map((r) => r.number)).toEqual([1, 2]);
  expect(FIXTURE_COMPOUNDS.every((c) => c.molfile.includes('V2000'))).toBe(true);
});

test('the job waits in the queue (2, then 1), runs the stages overlapped, then is done', () => {
  const at = (steps: number) => jobAt(spec, steps * STEP + 1, STEP);
  expect(at(0)).toMatchObject({ status: 'queued', queuePosition: 2 });
  expect(at(1)).toMatchObject({ status: 'queued', queuePosition: 1 });
  expect(at(0).results).toEqual([]);

  RUN.forEach((states, i) => {
    const job = at(QUEUE_STEPS + i);
    expect(job.status).toBe('running');
    expect(job.queuePosition).toBeNull();
    expect(job.error).toBeNull();
    expect(job.stages.map((s) => s.name)).toEqual([...STAGE_ORDER]);
    expect(job.stages.map((s) => s.state)).toEqual(['done', ...states]);
    expect(job.results).toEqual([]);
  });

  const done = at(QUEUE_STEPS + RUN_STEPS);
  expect(done.status).toBe('done');
  expect(done.stages.every((s) => s.state === 'done')).toBe(true);
  expect(done.results).toHaveLength(8);
});

test('the stages overlap as the worker lets them, and follow its rules at every step', () => {
  const rank = { pending: 0, active: 1, done: 2 } as const;
  let before: number[] = [];
  let overlapped = 0;
  for (let run = 0; run < RUN_STEPS; run++) {
    const states = jobAt(spec, (QUEUE_STEPS + run) * STEP + 1, STEP).stages.map((s) => s.state);
    states.forEach((state, i) => {
      // done only when every earlier stage is done; started only when the one before it has
      if (state === 'done') expect(states.slice(0, i).every((s) => s === 'done')).toBe(true);
      if (state !== 'pending') expect(states.slice(0, i).every((s) => s !== 'pending')).toBe(true);
    });
    // and a stage never goes back
    const ranks = states.map((s) => rank[s]);
    expect(ranks.every((r, i) => r >= (before[i] ?? 0))).toBe(true);
    before = ranks;
    if (states.filter((s) => s === 'active').length > 1) overlapped++;
  }
  expect(overlapped).toBeGreaterThanOrEqual(2);
});

test('pages processed grow while the pages are being rendered and never pass the page limit', () => {
  const first = QUEUE_STEPS;
  const early = jobAt(spec, first * STEP + 10, STEP).pagesProcessed;
  const late = jobAt(spec, (first + 1) * STEP + 90, STEP).pagesProcessed;
  expect(early).toBeGreaterThan(0);
  expect(early).toBeLessThan(late);
  expect(jobAt(spec, (first + 2) * STEP + 10, STEP).pagesProcessed).toBe(spec.pages); // every page rendered
  const big = { ...spec, pages: 14 };
  expect(jobAt(big, 100 * STEP, STEP).pagesProcessed).toBe(LIMITS.maxPages);
});

test('the nocls scenario: every result has isStructure null and a job warning says why', () => {
  const { spec } = scenarioFor([{ name: 'nocls.png' }]);
  expect(spec.results).toHaveLength(8);
  expect(spec.results.every((r) => r.isStructure === null)).toBe(true);
  expect(spec.warnings?.map((w) => w.code)).toEqual(['classifier_unavailable']);
  // The default scenario still has one answer of "not a structure" (number 12), and the rest say yes.
  const normal = scenarioFor([{ name: 'a.png' }]).spec.results;
  expect(normal.filter((r) => r.isStructure === false).map((r) => r.number)).toEqual([12]);
  expect(normal.filter((r) => r.isStructure === true)).toHaveLength(7);
});

test('the structure count is in the record once every page is searched, and the results only with the finished job', () => {
  const searched = RUN.findIndex((states) => states[1] === 'done');
  RUN.forEach((_, run) => {
    const job = jobAt(spec, (QUEUE_STEPS + run) * STEP + 1, STEP);
    expect(job.status).toBe('running');
    expect(job.structuresFound).toBe(run >= searched ? 8 : 0);
    expect(job.results).toEqual([]);
  });
});

test('file names pick the scenario', () => {
  expect(scenarioFor([{ name: 'a.png' }]).spec.results).toHaveLength(8);
  expect(scenarioFor([{ name: 'paper.pdf' }]).spec.pages).toBe(10);
  expect(scenarioFor([{ name: 'empty.png' }]).spec.results).toHaveLength(0);
  expect(scenarioFor([{ name: 'twenty.png' }]).spec.results).toHaveLength(20);
  const truncated = scenarioFor([{ name: 'truncated.pdf' }]).spec;
  expect(truncated).toMatchObject({ pages: 14, truncated: true, structuresFound: 23 });
  expect(truncated.results).toHaveLength(LIMITS.maxStructures);
  expect(scenarioFor([{ name: 'busy.png' }]).refuse?.status).toBe(429);
  expect(scenarioFor([{ name: 'full.png' }]).refuse?.status).toBe(503);
  expect(scenarioFor([{ name: 'fail.png' }]).spec.failure).toBeTruthy();
});

test('a created job can be polled to completion and exported in all three formats, edited molfiles in', async () => {
  let t = 0;
  const api = new MockApiClient({ stepMs: STEP, now: () => t, latencyMs: 0 });
  const { id } = await api.createJob([new File(['x'], 'a.png', { type: 'image/png' })]);
  expect((await api.getJob(id)).status).toBe('queued');
  t = 1000;
  const job = await api.getJob(id);
  expect(job.status).toBe('done');

  const edited = '\n  Ketcher\n\n  0  0  0  0  0  0  0  0  0  0999 V2000\nM  END\nEDITED-MARKER';
  const zip = await (await api.exportJob(id, { format: 'zip', molfiles: { 3: edited } })).blob.text();
  const order = [...zip.matchAll(/== structure (\d+) \((edited|original)\)/g)].map((m) => `${m[1]}:${m[2]}`);
  expect(order).toEqual(['1:original', '2:original', '4:original', '5:edited', '6:original', '7:original', '11:original', '12:original']);
  expect(zip).toContain('EDITED-MARKER');

  const sdf = await (await api.exportJob(id, { format: 'sdf', molfiles: { 3: edited } })).blob.text();
  expect(sdf.split('$$$$').length - 1).toBe(8);
  expect(sdf).toContain('EDITED-MARKER');

  const csv = (await (await api.exportJob(id, { format: 'csv', molfiles: {} })).blob.text()).trim().split('\n');
  expect(csv[0]).toBe('number,smiles,inchikey,valid,name,edited'); // the API's columns
  expect(csv).toHaveLength(9);
  expect(csv[3]?.startsWith('4,')).toBe(true);
  expect(csv.slice(1).map((row) => row.split(',')[3])).toEqual(Array(8).fill('true'));
});

test('the cdkonly scenario has one structure that only CDK accepted, and the CSV and SDF say so', async () => {
  let t = 0;
  const api = new MockApiClient({ stepMs: STEP, now: () => t, latencyMs: 0 });
  const { id } = await api.createJob([new File(['x'], 'cdkonly.png', { type: 'image/png' })]);
  t = 1000;
  const job = await api.getJob(id);
  expect(job.status).toBe('done');
  expect(job.results.map((r) => [r.valid, r.validBy])).toEqual([
    [true, 'rdkit'],
    [true, 'rdkit'],
    [false, 'cdk'], // RDKit's word is `valid: false`
    [true, 'rdkit'],
    [true, 'rdkit'],
    [true, 'rdkit'],
    [true, 'rdkit'],
    [true, 'rdkit'],
  ]);
  expect(job.results[2]).toMatchObject({ inchikey: '', name: null, nameTier: null, nameVerified: null });
  expect(job.results[2]?.smiles).not.toBe(''); // DECIMER's own SMILES stays
  const csv = (await (await api.exportJob(id, { format: 'csv', molfiles: {} })).blob.text()).trim().split('\n');
  expect(csv.slice(1).map((row) => row.split(',')[3])).toEqual(['true', 'true', 'cdk', 'true', 'true', 'true', 'true', 'true']);
  const edited = '\n  Ketcher\n\n  6  6  0  0  0  0  0  0  0  0999 V2000\nM  END\n';
  const fixed = (await (await api.exportJob(id, { format: 'csv', molfiles: { 2: edited } })).blob.text()).trim().split('\n');
  expect(fixed[3]?.split(',')[3]).toBe('true'); // an edit is RDKit's to judge, never `cdk`
  const sdf = await (await api.exportJob(id, { format: 'sdf', molfiles: {} })).blob.text();
  expect(sdf.match(/> <VALID>\ncdk\n/g)).toHaveLength(1);
});

test('the job has the fields of the API model: warnings, createdAt and expiresAt, null where absent', async () => {
  const api = new MockApiClient({ stepMs: STEP, now: () => 1_000_000, latencyMs: 0 });
  const { id } = await api.createJob([new File(['x'], 'truncated.pdf')]);
  const job = await api.getJob(id);
  expect(job.createdAt).toBe(new Date(1_000_000).toISOString());
  expect(Date.parse(job.expiresAt) - Date.parse(job.createdAt)).toBe(60 * 60 * 1000);
  expect(job.warnings.map((w) => w.code)).toEqual(['pages_truncated']);
  expect(job.queuePosition).toBe(2);
  expect(job.error).toBeNull();
});

test('refusals use the API codes and carry the server message', async () => {
  const api = new MockApiClient({ latencyMs: 0 });
  await expect(api.createJob([new File(['x'], 'corrupt.pdf')])).rejects.toMatchObject({
    status: 400,
    code: 'corrupt_file',
    serverMessage: expect.stringContaining('corrupt.pdf'),
  });
  await expect(api.createJob([new File(['x'], 'encrypted.pdf')])).rejects.toMatchObject({ code: 'encrypted_pdf' });
  await expect(api.createJob([new File(['x'], 'pixels.png')])).rejects.toMatchObject({ code: 'too_many_pixels' });
  await expect(api.createJob([new File(['x'], 'busy.png')])).rejects.toMatchObject({ status: 429, code: 'rate_limited' });
});

test('unknown jobs are 404s', async () => {
  const api = new MockApiClient({ latencyMs: 0 });
  await expect(api.getJob('nope')).rejects.toMatchObject({ status: 404, code: 'not_found' });
});

test('atomCount reads V2000 and V3000 counts lines', () => {
  expect(atomCount('\n  X\n\n  0  0  0  0  0  0  0  0  0  0999 V2000\nM  END')).toBe(0);
  expect(atomCount('\n  X\n\n 25 27  0  0  1  0  0  0  0  0999 V2000\n')).toBe(25);
  expect(atomCount('\n  X\n\n  0  0  0  0  0  0  0  0  0  0  0 V3000\nM  V30 BEGIN CTAB\nM  V30 COUNTS 21 22 0 0 0\n')).toBe(21);
  expect(atomCount('this is not a molfile')).toBeNull();
});

test('validate: an empty canvas is not a valid structure (no SMILES, no InChIKey), anything else is', async () => {
  const api = new MockApiClient({ stepMs: STEP, now: () => 10_000, latencyMs: 0 });
  const { id } = await api.createJob([new File(['x'], 'a.png')]);
  const empty = await api.validate(id, 2, '\n  Ketcher\n\n  0  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n');
  expect(empty).toMatchObject({ valid: false, smiles: '', inchikey: '', name: null });
  const drawn = await api.validate(id, 2, '\n  Ketcher\n\n  6  6  0  0  0  0  0  0  0  0999 V2000\nM  END\n');
  expect(drawn).toMatchObject({ valid: true });
  expect(drawn.inchikey).not.toBe('');
});

test('deleting a job forgets it: it is gone for getJob, validate, export and report, and a second delete is a 404', async () => {
  const api = new MockApiClient({ stepMs: STEP, latencyMs: 0 });
  const { id } = await api.createJob([new File(['x'], 'paper.png', { type: 'image/png' })]);
  await expect(api.deleteJob(id)).resolves.toBeUndefined();
  await expect(api.getJob(id)).rejects.toMatchObject({ status: 404, code: 'not_found' });
  await expect(api.exportJob(id, { format: 'csv', molfiles: {} })).rejects.toMatchObject({ code: 'not_found' });
  await expect(api.report(id, 0)).rejects.toMatchObject({ code: 'not_found' });
  await expect(api.deleteJob(id)).rejects.toMatchObject({ status: 404, code: 'not_found' });
  await expect(api.deleteJob('mock-999')).rejects.toMatchObject({ status: 404 });
});
