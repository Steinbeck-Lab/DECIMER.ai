import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type BrowserContext, type Download, type Page, type Response } from '@playwright/test';
import { editorPanel, headerDownload, layoutOnCanvas, waitForEditor, watchProblems } from '../helpers';
import { parseCsv, readZip } from './zip';

// The thin slice against the real stack (TODO, and the VM half of P4-08): the production build under the edge's
// CSP, the real API, workers and models. One journey, one job at a time, one page shared by the steps (serial: a step
// that fails ends the run, the later ones depend on its state). Run with `npm run e2e:live`; see e2e/live/README.md.
//
// What each step asserts is what a person sees and what the API returned for it: the tests compare the page with the
// job's own JSON, not with numbers baked in, because the models, not this spec, decide what is recognised.
// A failure that comes from the app or the API is evidence for a bug report, not something to code around.

const CROP = resolve(import.meta.dirname, 'fixtures/00_crop-0.png');
const GOLDEN_PDF = process.env.LIVE_PDF ?? resolve(import.meta.dirname, '../../../golden/zou2024_penicillium_shentong.pdf');
/** `golden/runs/next-20261023-071544/predictions.csv`, row 0: what the OCSR read from this crop inside the PDF run (informational). */
const GOLDEN_SMILES = 'C=CC(C)(C)[C@@H]1Nc2ccccc2C(=O)[C@H]1CN1C(=O)CC[C@@H]1OC';
const RESULTS_TIMEOUT_MS = 180_000;

interface WireResult {
  index: number;
  number: number;
  smiles: string;
  inchikey: string;
  valid: boolean;
  molfile: string;
  svg: string;
  cropUrl: string;
  name: string | null;
  nameTier: string | null;
  nameVerified: string | null;
}
interface WireJob {
  id: string;
  status: string;
  pages: number;
  pagesProcessed: number;
  structuresDropped: number;
  truncated: boolean;
  warnings: { code: string; message: string }[];
  results: WireResult[];
}
interface Validated {
  valid: boolean;
  smiles: string;
  inchikey: string;
  svg: string;
  name: string | null;
}

test.describe.configure({ mode: 'serial' });

let context: BrowserContext;
let page: Page;
let problems: string[];
/** Console, page errors and the API calls the page made, for the evidence of a failure (attached to every step). */
const evidence: string[] = [];
const started = Date.now();
const timings: { step: string; ms: number }[] = [];
// State that one step hands to the next.
let jobId = '';
/** Jobs this run created and has not seen deleted: afterAll deletes them, so a failed step leaves no job holding a slot. */
const openJobs = new Set<string>();
let job: WireJob;
let editedMolfile = '';
let validated: Validated;

const tile = (n: number) => page.getByRole('button', { name: new RegExp(`^Structure ${n}\\b`) });
const panel = () => editorPanel(page);
const smilesOf = () => panel().locator('.dc-code__text');
const normal = (molfile: string) => molfile.replace(/\r\n/g, '\n').trimEnd();
/** What a molfile is up to and including its `M  END` line (the export keeps the edit verbatim up to there). */
const throughEnd = (molfile: string) => normal(molfile).replace(/(^|\n)M {2}END[\s\S]*$/, '$1M  END');

/** Runs one named part of a step, and puts its wall time in the test's annotations, the console and the summary. */
async function timed<T>(label: string, body: () => Promise<T>): Promise<T> {
  const t0 = Date.now();
  try {
    return await test.step(label, body);
  } finally {
    const ms = Date.now() - t0;
    const step = `${test.info().title.split(' ')[0]} ${label}`;
    timings.push({ step, ms });
    test.info().annotations.push({ type: 'timing', description: `${label}: ${(ms / 1000).toFixed(2)} s` });
    console.log(`LIVE_TIMING ${step}: ${(ms / 1000).toFixed(2)} s`);
  }
}

const isApi = (response: Response, method: string, pathEnd: string) =>
  response.request().method() === method && new URL(response.url()).pathname.endsWith(pathEnd);

/** Pastes an image the way a browser does: a `paste` event with a File on the clipboard (as e2e/helpers.ts pasteImage, for any file). */
function pasteFile(file: string, name: string) {
  const bytes = [...readFileSync(file)];
  return page.evaluate(
    ({ data, fileName }) => {
      const transfer = new DataTransfer();
      transfer.items.add(new File([new Uint8Array(data)], fileName, { type: 'image/png' }));
      const event = new ClipboardEvent('paste', { clipboardData: transfer, bubbles: true, cancelable: true });
      document.dispatchEvent(event);
      return event.defaultPrevented;
    },
    { data: bytes, fileName: name },
  );
}

async function molfileText(): Promise<string> {
  const details = panel().locator('details.dc-molfile');
  if (!(await details.evaluate((el) => (el as HTMLDetailsElement).open))) await details.locator('summary').click();
  return (await details.locator('pre').textContent()) ?? '';
}

async function getJob(id: string): Promise<WireJob> {
  const response = await page.request.get(`/api/jobs/${id}`);
  expect(response.status(), `GET /api/jobs/${id}`).toBe(200);
  return (await response.json()) as WireJob;
}

async function expectGone(id: string) {
  const response = await page.request.get(`/api/jobs/${id}`);
  expect(response.status(), `GET /api/jobs/${id} after DELETE`).toBe(404);
  expect(((await response.json()) as { detail: { code: string } }).detail.code).toBe('not_found');
}

/** The header's Download menu, one format; returns what the page asked the API for and what it was given. */
async function exportAs(format: 'zip' | 'sdf' | 'csv', item: RegExp) {
  const answered = page.waitForResponse((r) => isApi(r, 'POST', `/api/jobs/${jobId}/export`), { timeout: 120_000 });
  await headerDownload(page).click();
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: 120_000 }),
    // `noWaitAfter`: the download starts after the API has answered, not at the click.
    page.getByRole('menuitem', { name: item }).click({ noWaitAfter: true }),
  ]);
  const response = await answered;
  const body = await readDownload(download);
  expect(response.status(), `${format} export`).toBe(200);
  return { download, response, body, request: response.request().postDataJSON() as { format: string; molfiles: Record<string, string> } };
}

async function readDownload(download: Download): Promise<Buffer> {
  const chunks: Buffer[] = [];
  for await (const chunk of await download.createReadStream()) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks);
}

test.beforeAll(async ({ browser }) => {
  const { baseURL, storageState, viewport, userAgent, deviceScaleFactor, isMobile, hasTouch } = test.info().project.use;
  context = await browser.newContext({ baseURL, storageState, viewport, userAgent, deviceScaleFactor, isMobile, hasTouch });
  await context.tracing.start({ screenshots: true, snapshots: true });
  page = await context.newPage();
  problems = watchProblems(page);
  page.on('console', (m) => evidence.push(`console.${m.type()}: ${m.text()}`));
  page.on('pageerror', (e) => evidence.push(`pageerror: ${e.message}`));
  page.on('response', (r) => {
    if (new URL(r.url()).pathname.startsWith('/api/')) {
      evidence.push(`${((Date.now() - started) / 1000).toFixed(1)}s ${r.request().method()} ${new URL(r.url()).pathname} -> ${r.status()}`);
    }
  });
});

// eslint-disable-next-line no-empty-pattern -- Playwright reads the fixtures a hook uses from this pattern; there are none
test.afterEach(async ({}, testInfo) => {
  // Files in the output folder, so that the evidence outlives the run (and is not only inside the JSON report).
  if (testInfo.status !== testInfo.expectedStatus) {
    const shot = testInfo.outputPath('screenshot.png');
    await page.screenshot({ path: shot, fullPage: true });
    await testInfo.attach('screenshot.png', { path: shot, contentType: 'image/png' });
  }
  const log = testInfo.outputPath('console-and-api-log.txt');
  writeFileSync(log, evidence.join('\n'));
  await testInfo.attach('console-and-api-log.txt', { path: log, contentType: 'text/plain' });
});

// eslint-disable-next-line no-empty-pattern -- as above
test.afterAll(async ({}, testInfo) => {
  if (!context) return; // beforeAll failed: its own error is the one to read
  for (const id of openJobs) {
    // A step failed before its DELETE: free the job's slot instead of leaving it for the 1 h TTL.
    const response = await context.request.delete(`/api/jobs/${id}`).catch(() => null);
    console.log(`LIVE cleanup: DELETE ${id} -> ${response?.status() ?? 'no answer'}`);
  }
  await context.tracing.stop({ path: resolve(testInfo.project.outputDir, 'trace.zip') });
  console.log(`LIVE_TIMING summary\n${timings.map((t) => `  ${t.step}: ${(t.ms / 1000).toFixed(2)} s`).join('\n')}`);
  await context.close();
});

test('1. paste one image: working view, then the SMILES, the SVG and the name hover of result 1', async () => {
  const document = await timed('open the app', async () => {
    const response = await page.goto('./');
    await expect(page.getByRole('heading', { level: 1, name: 'DECIMER.ai' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Choose files' })).toBeVisible();
    return response;
  });
  // The page is served with the production policy (the check that "no CSP violation" means something).
  expect(document?.headers()['content-security-policy']).toContain("default-src 'self'");

  const created = page.waitForResponse((r) => isApi(r, 'POST', '/api/jobs'), { timeout: 60_000 });
  // Watched from before the paste: with a fast job the working view is on screen for a moment only.
  const working = Promise.all([
    page.getByRole('progressbar', { name: 'Progress' }).waitFor({ timeout: 60_000 }),
    page.getByText('pasted-image.png').waitFor({ timeout: 60_000 }),
  ]);
  working.catch(() => undefined); // reported where it is awaited
  await timed('paste → job created', async () => {
    expect(await pasteFile(CROP, 'image.png')).toBe(true);
    const response = await created;
    expect(response.status()).toBe(202);
    jobId = ((await response.json()) as { id: string }).id;
    openJobs.add(jobId);
    expect(jobId).toMatch(/^[0-9a-f]{32}$/);
  });
  await timed('working view', () => working);
  await timed('results', async () => {
    await expect(page.getByRole('list', { name: 'Recognised structures' })).toBeVisible({ timeout: RESULTS_TIMEOUT_MS });
    await expect(page.getByRole('heading', { level: 1, name: 'Structures' })).toBeVisible();
  });

  job = await getJob(jobId);
  const first = job.results[0];
  expect(job.status).toBe('done');
  expect(first, 'the job has a result 1').toBeDefined();
  if (!first) return;
  test.info().annotations.push({ type: 'job', description: `${jobId}: ${job.results.length} result(s)` });
  test.info().annotations.push({
    type: 'info',
    description: `result 1 SMILES ${first.smiles === GOLDEN_SMILES ? 'equals' : 'differs from'} the golden PDF run's crop-0 (${first.smiles})`,
  });
  await expect(page.locator('.dc-tile')).toHaveCount(job.results.length);

  await timed('result 1: SVG and crop on the tile', async () => {
    // The depiction is the API's own SVG, drawn as an image.
    const structure = tile(1).locator('.dc-tile__structure');
    expect(first.svg.trimStart().startsWith('<')).toBe(true);
    const src = await structure.getAttribute('src');
    expect(src).toMatch(/^data:image\/svg\+xml/);
    expect(decodeURIComponent((src ?? '').split(',').slice(1).join(','))).toBe(first.svg);
    await expect.poll(() => structure.evaluate((img) => (img as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
    // The original crop, from the media URL the job gave.
    await expect.poll(() => tile(1).locator('.dc-tile__crop').evaluate((img) => (img as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
    expect((await page.request.get(first.cropUrl)).status()).toBe(200);
  });

  await timed('result 1: IUPAC name hover', async () => {
    expect(first.name, 'the API returned a name for result 1 (chem is up)').toBeTruthy();
    await expect(tile(1).locator('.dc-tile__name')).toHaveText(first.name ?? '');
    // Before the panel opens: a click closes the tooltip, and a hover at the very spot of that click does not bring it back.
    await tile(1).hover();
    const tooltip = page.getByRole('tooltip');
    await expect(tooltip).toBeVisible();
    const lines = await tooltip.locator('.dc-hover__line').allTextContents();
    expect(lines, 'the kind of name, then how it was checked').toHaveLength(2);
    expect(lines[0]).toMatch(/IUPAC name|Systematic name|Orthonym tier/);
    expect(lines[1]).toMatch(/OPSIN|curated list/);
    test.info().annotations.push({ type: 'info', description: `name tier ${first.nameTier}, verified ${first.nameVerified}: ${lines.join(' / ')}` });
  });

  await timed('result 1: SMILES text in the panel', async () => {
    await tile(1).click();
    await expect(panel().getByRole('heading', { name: 'Editing 1' })).toBeVisible();
    expect(first.valid).toBe(true);
    expect(first.smiles).not.toBe('');
    await expect(smilesOf()).toHaveText(first.smiles);
  });

  expect(problems).toEqual([]);
});

test('2. editor on result 1: Ketcher loads, a small edit is validated (200) and the tile says Edited', async () => {
  const first = job.results[0];
  if (!first) throw new Error('step 1 left no result');
  await page.mouse.move(0, 0); // off the tile: the hover of step 1 is done
  await timed('Ketcher loads', async () => {
    await waitForEditor(page);
    await expect(page.locator('.Ketcher-root')).toHaveCount(1);
    await expect(page.locator('.dc-editor__canvas[data-active=true]')).toBeVisible();
  });

  // Not through the panel's Molfile section: opening it scrolls the canvas out of the viewport, and the edit is made with the mouse.
  expect(first.molfile, 'the recognised molfile is the pipeline\'s').toContain('RDKit');

  const answered = page.waitForResponse((r) => isApi(r, 'POST', `/api/jobs/${jobId}/results/0/validate`), { timeout: 60_000 });
  await timed('edit on the canvas (select all, Layout)', async () => {
    await page.locator('.dc-editor__canvas[data-active=true]').scrollIntoViewIfNeeded();
    await layoutOnCanvas(page);
  });
  const response = await timed('validate answers', () => answered);
  expect(response.status()).toBe(200);
  validated = (await response.json()) as Validated;
  const sent = response.request().postDataJSON() as { molfile: string };

  await timed('tile marked edited', async () => {
    await expect(tile(1)).toContainText('Edited');
    await expect(page.getByRole('list', { name: 'Status of the structures' })).toContainText('1 edited');
    await expect(panel().getByText('Edited by you')).toBeVisible();
    await expect(page.locator('.dc-code__text[data-stale=true]')).toHaveCount(0);
  });

  editedMolfile = await molfileText();
  expect(editedMolfile, 'Ketcher wrote it, not the pipeline').not.toContain('RDKit');
  expect(normal(sent.molfile), 'validate was sent the edit that the panel shows').toBe(normal(editedMolfile));
  expect(validated.valid).toBe(true);
  expect(validated.smiles).not.toBe('');
  expect(validated.inchikey).toMatch(/^[A-Z]{14}-[A-Z]{10}-[A-Z]$/);
  expect(validated.svg.trimStart().startsWith('<')).toBe(true);
  // Re-laying out a structure does not change which molecule it is.
  expect(validated.smiles).toBe(first.smiles);
  expect(validated.inchikey).toBe(first.inchikey);
  await expect(smilesOf()).toHaveText(validated.smiles);
  test.info().annotations.push({ type: 'info', description: `validate name: ${validated.name ?? 'null'}` });
  expect(problems).toEqual([]);
});

test('3. export ZIP, SDF and CSV through the Download menu: the edit is in all three', async () => {
  const count = job.results.length;
  const stem = (n: number) => `structure-${String(n).padStart(Math.max(2, String(count).length), '0')}`;

  const zip = await timed('ZIP', () => exportAs('zip', /^ZIP/));
  expect(zip.download.suggestedFilename()).toMatch(/\.zip$/);
  expect(zip.response.headers()['content-type']).toContain('zip');
  expect(zip.request.format).toBe('zip');
  // The page sent only the edit, keyed by the 0-based result index, as the panel shows it.
  expect(Object.keys(zip.request.molfiles)).toEqual(['0']);
  const files = readZip(zip.body);
  // A result with no molfile has no .mol (the API leaves it out).
  const expectedNames = [
    ...job.results.flatMap((r) => [`${stem(r.number)}.png`, ...(r.molfile ? [`${stem(r.number)}.mol`] : [])]),
    'results.smiles.txt',
  ];
  expect([...files.keys()].sort()).toEqual(expectedNames.sort());
  const png = files.get('structure-01.png');
  expect(png?.subarray(0, 8).toString('hex'), 'a PNG').toBe('89504e470d0a1a0a');
  const crop = await page.request.get(job.results[0]?.cropUrl ?? '');
  expect(png?.equals(await crop.body()), 'the PNG is the job\'s crop').toBe(true);
  const mol = files.get('structure-01.mol')?.toString('utf8') ?? '';
  expect(throughEnd(mol), 'structure-01.mol is the edited molfile').toBe(throughEnd(editedMolfile));
  expect(mol).not.toContain('RDKit');
  for (const r of job.results.slice(1).filter((r) => r.molfile)) {
    expect(throughEnd(files.get(`${stem(r.number)}.mol`)?.toString('utf8') ?? ''), `${stem(r.number)}.mol is the stored one`).toBe(throughEnd(r.molfile));
  }
  const lines = (files.get('results.smiles.txt')?.toString('utf8') ?? '').split('\n').filter(Boolean);
  expect(lines).toHaveLength(count);
  const [image, smiles, name] = (lines[0] ?? '').split('\t');
  expect(image).toBe('structure-01.png');
  expect(smiles).toBe(validated.smiles);
  test.info().annotations.push({ type: 'info', description: `ZIP name for the edited structure: ${name || 'none'} (validate: ${validated.name ?? 'null'})` });

  const sdf = await timed('SDF', () => exportAs('sdf', /^SDF/));
  expect(sdf.download.suggestedFilename()).toMatch(/\.sdf$/);
  expect(sdf.request.format).toBe('sdf');
  expect(Object.keys(sdf.request.molfiles)).toEqual(['0']);
  const records = sdf.body.toString('utf8').split('$$$$\n').filter((r) => r.trim() !== '');
  expect(records).toHaveLength(job.results.filter((r) => r.molfile).length);
  const record = records[0] ?? '';
  expect(throughEnd(record.slice(0, record.indexOf('M  END') + 6))).toBe(throughEnd(editedMolfile));
  expect(record).toContain('> <NUMBER>\n1\n');
  expect(record).toContain(`> <SMILES>\n${validated.smiles}\n`);
  expect(record).toContain(`> <INCHIKEY>\n${validated.inchikey}\n`);
  expect(record).toContain('> <EDITED>\ntrue\n');
  for (const other of records.slice(1)) expect(other).toContain('> <EDITED>\nfalse\n');

  const csv = await timed('CSV', () => exportAs('csv', /^CSV/));
  expect(csv.download.suggestedFilename()).toMatch(/\.csv$/);
  expect(csv.request.format).toBe('csv');
  expect(Object.keys(csv.request.molfiles)).toEqual(['0']);
  const text = csv.body.toString('utf8');
  expect(text.split(/\r?\n/)[0]).toBe('number,smiles,inchikey,valid,name,edited');
  const rows = parseCsv(text);
  expect(rows[0]).toEqual(['number', 'smiles', 'inchikey', 'valid', 'name', 'edited']);
  expect(rows.slice(1)).toHaveLength(count);
  const row1 = rows[1] ?? [];
  expect(row1.slice(0, 4)).toEqual(['1', validated.smiles, validated.inchikey, 'true']);
  expect(row1[5], 'edited=true for row 1').toBe('true');
  for (const other of rows.slice(2)) expect(other[5]).toBe('false');

  // The page is as it was: exports do not change the work.
  await expect(tile(1)).toContainText('Edited');
  expect(problems).toEqual([]);
});

test('4. report a problem on result 1: 204 and the thank-you message', async () => {
  const reported = page.waitForResponse((r) => isApi(r, 'POST', `/api/jobs/${jobId}/report`), { timeout: 30_000 });
  await timed('report', async () => {
    await panel().getByRole('button', { name: 'Report a problem' }).click();
    const response = await reported;
    expect(response.status()).toBe(204);
    expect(response.request().postDataJSON()).toEqual({ index: 0 });
    await expect(panel().getByRole('status').filter({ hasText: 'Thank you for your report' })).toBeVisible();
  });
  await expect(panel().getByRole('button', { name: 'Report a problem' })).toHaveCount(0);
  expect(problems).toEqual([]);
});

test('5. golden PDF through the file picker: 20 tiles and the truncation warning; New upload deletes the job (404)', async () => {
  test.setTimeout(420_000);
  expect(existsSync(GOLDEN_PDF), `the golden PDF is at ${GOLDEN_PDF} (mount golden/ and set LIVE_PDF in a container)`).toBe(true);

  // Leave the edited job: the confirmation, then DELETE (a start-over of a job that is done).
  const firstDeleted = page.waitForResponse((r) => isApi(r, 'DELETE', `/api/jobs/${jobId}`), { timeout: 30_000 });
  await timed('New upload (edited work): confirm, DELETE', async () => {
    await page.getByRole('banner').getByRole('button', { name: 'New upload' }).click();
    const dialog = page.getByRole('dialog', { name: 'Start a new upload?' });
    await expect(dialog).toContainText('You have edited 1 structure.');
    await dialog.getByRole('button', { name: 'Start new upload' }).click();
    expect((await firstDeleted).status()).toBe(204);
    await expect(page.getByRole('button', { name: 'Choose files' })).toBeVisible();
  });
  await expectGone(jobId);
  openJobs.delete(jobId);

  const created = page.waitForResponse((r) => isApi(r, 'POST', '/api/jobs'), { timeout: 60_000 });
  await timed('file picker → job created', async () => {
    const [chooser] = await Promise.all([page.waitForEvent('filechooser'), page.getByRole('button', { name: 'Choose files' }).click()]);
    await chooser.setFiles(GOLDEN_PDF);
    const response = await created;
    expect(response.status()).toBe(202);
    jobId = ((await response.json()) as { id: string }).id;
    openJobs.add(jobId);
  });
  await timed('queued/working view', async () => {
    await expect(page.getByRole('progressbar', { name: 'Progress' })).toBeVisible();
    await expect(page.getByText('zou2024_penicillium_shentong.pdf')).toBeVisible();
  });
  await timed('PDF: segment, recognise, check → results', async () => {
    await expect(page.getByRole('list', { name: 'Recognised structures' })).toBeVisible({ timeout: RESULTS_TIMEOUT_MS });
  });

  await expect(page.locator('.dc-tile')).toHaveCount(20);
  await expect(page.getByText('20 of 20 structures · 10 of 10 pages')).toBeVisible();
  const warning = page.getByRole('note');
  await expect(warning).toContainText('12 more structures were found. Only the first 20 are shown.');
  const pdfJob = await getJob(jobId);
  expect(pdfJob.truncated).toBe(true);
  expect(pdfJob.structuresDropped).toBe(12);
  expect(pdfJob.results).toHaveLength(20);
  expect(pdfJob.warnings.map((w) => w.code)).toContain('structures_truncated');
  test.info().annotations.push({
    type: 'info',
    description: `PDF job ${jobId}: warnings ${JSON.stringify(pdfJob.warnings)}; ${pdfJob.results.filter((r) => r.svg).length}/20 with a depiction, ${pdfJob.results.filter((r) => r.name).length}/20 with a name`,
  });
  // Every crop loaded from its media URL.
  await expect.poll(() => page.locator('.dc-tile__crop').evaluateAll((imgs) => imgs.filter((i) => (i as HTMLImageElement).naturalWidth > 0).length)).toBe(20);

  // Nothing was edited: "New upload" starts over at once, and the job is deleted.
  const deleted = page.waitForResponse((r) => isApi(r, 'DELETE', `/api/jobs/${jobId}`), { timeout: 30_000 });
  await timed('New upload: DELETE', async () => {
    await page.getByRole('banner').getByRole('button', { name: 'New upload' }).click();
    expect((await deleted).status()).toBe(204);
    await expect(page.getByRole('button', { name: 'Choose files' })).toBeVisible();
  });
  await expectGone(jobId);
  openJobs.delete(jobId);
  expect((await page.request.get(pdfJob.results[0]?.cropUrl ?? '')).status(), 'its crops are gone too').toBe(404);
  expect(problems).toEqual([]);
});
