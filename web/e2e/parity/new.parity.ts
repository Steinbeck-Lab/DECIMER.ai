import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type BrowserContext, type Page, type Response } from '@playwright/test';
import { editorPanel, downloadButton, layoutOnCanvas, waitForEditor, watchProblems } from '../helpers';
import { parseCsv, readZip } from '../live/zip';
import { markReturningTab } from '../returning';
import { blankImage, blankPdf, CROP, dir, dragEvents, duplicateIds, Facts, fileFrom, NEGATIVES, overflowX, pasteFile, shot, textImage, type FileSpec } from './shared';

// The new stack's half of the parity check: each item exercised against the running stack
// (the production build under the edge's CSP, the real API, workers and models) with a screenshot and a recorded
// observation, set beside what legacy.parity.ts recorded of the earlier Laravel version of decimer.ai for the same item. Opt-in: `*.parity.ts`, never in the
// default run or in CI. The stack is shared: ONE job at a time, and every job this run creates is deleted (afterAll
// deletes the ones a failed step left). Run (from web/, in the Playwright image, as in parity.config.ts):
//   PARITY_NEW_URL=http://127.0.0.1:8090 npx playwright test --config e2e/parity/parity.config.ts --project new

const GOLDEN_PDF = process.env.PARITY_PDF ?? resolve(import.meta.dirname, '../../../golden/zou2024_penicillium_shentong.pdf');
const RESULTS_TIMEOUT_MS = 720_000; // a shared stack can be busy: a job may wait in the queue for minutes
const facts = new Facts('new');

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
  bbox: [number, number, number, number];
  pageIndex: number;
  isStructure: boolean | null;
  confidence: number;
}
interface WireJob {
  id: string;
  createdAt: string;
  expiresAt: string;
  status: string;
  pages: number;
  pagesProcessed: number;
  structuresFound: number;
  structuresDropped: number;
  truncated: boolean;
  warnings: { code: string; message: string }[];
  results: WireResult[];
}

test.describe.configure({ mode: 'serial' });

let context: BrowserContext;
let page: Page;
let problems: string[];
const apiLog: string[] = [];
const hosts = new Set<string>();
const failedRequests: string[] = [];
const openJobs = new Set<string>();
let jobId = '';

const tile = (n: number) => page.getByRole('button', { name: new RegExp(`^Structure ${n}\\b`) });
const panel = () => editorPanel(page);
const isApi = (response: Response, method: string, pathEnd: string) =>
  response.request().method() === method && new URL(response.url()).pathname.endsWith(pathEnd);
const normal = (molfile: string) => molfile.replace(/\r\n/g, '\n').trimEnd();
const throughEnd = (molfile: string) => normal(molfile).replace(/(^|\n)M {2}END[\s\S]*$/, '$1M  END');

async function getJob(id: string): Promise<WireJob> {
  const response = await page.request.get(`/api/jobs/${id}`);
  expect(response.status(), `GET /api/jobs/${id}`).toBe(200);
  return (await response.json()) as WireJob;
}

/** Notes the job a POST /api/jobs created, so afterAll can delete it if a step fails before its own DELETE. */
function trackJobs() {
  page.on('response', async (r) => {
    if (isApi(r, 'POST', '/api/jobs') && r.status() === 202) {
      const id = ((await r.json().catch(() => ({}))) as { id?: string }).id;
      if (id) {
        openJobs.add(id);
        jobId = id;
      }
    }
  });
}

/**
 * Samples what the page says about a running job (a job of one small image is over in seconds, so this polls fast)
 * and takes a screenshot each time the text changes; call `stop()` to get the timeline.
 */
function watchStates(prefix: string, maxShots = 8) {
  const timeline: { t: number; text: string }[] = [];
  const started = Date.now();
  let running = true;
  let last = '';
  let shots = 0;
  const loop = (async () => {
    while (running) {
      const text = await page
        .evaluate(() => {
          const main = document.querySelector('main');
          const bar = document.querySelector('[role=progressbar]');
          return `${(main as HTMLElement | null)?.innerText.replace(/\s+/g, ' ').trim().slice(0, 260) ?? ''} || ${bar?.getAttribute('aria-valuetext') ?? ''}`;
        })
        .catch(() => last);
      if (text !== last) {
        last = text;
        timeline.push({ t: Math.round((Date.now() - started) / 100) / 10, text });
        if (shots < maxShots) await shot(page, 'new', `${prefix}-${String(++shots).padStart(2, '0')}`).catch(() => undefined);
      }
      await new Promise((done) => setTimeout(done, 120));
    }
  })();
  return {
    async stop() {
      running = false;
      await loop;
      return timeline;
    },
  };
}

/** Start over from the results or the none view: the dialog if there is work, then DELETE; the job is then gone (404). */
async function startOver(id: string, confirmShot?: string) {
  const deleted = page.waitForResponse((r) => isApi(r, 'DELETE', `/api/jobs/${id}`), { timeout: 30_000 });
  await page.getByRole('banner').getByRole('button', { name: 'New upload' }).click();
  const dialog = page.getByRole('dialog', { name: 'Start a new upload?' });
  // With work to lose the confirmation opens (it animates in); without work there is none and DELETE follows at once.
  const confirming = await dialog.waitFor({ state: 'visible', timeout: 2500 }).then(() => true, () => false);
  if (confirming) {
    if (confirmShot) await shot(page, 'new', confirmShot);
    await dialog.getByRole('button', { name: 'Start new upload' }).click();
  }
  expect((await deleted).status()).toBe(204);
  await expect(page.getByRole('button', { name: 'Choose files' })).toBeVisible();
  const gone = await page.request.get(`/api/jobs/${id}`);
  expect(gone.status(), 'the job is gone after DELETE').toBe(404);
  openJobs.delete(id);
}

/** A job that has been started from the empty screen: waits for its results (or the none view) and returns the job's JSON. */
async function finished(expected: 'results' | 'none'): Promise<WireJob> {
  if (expected === 'results') {
    await expect(page.getByRole('list', { name: 'Recognised structures' })).toBeVisible({ timeout: RESULTS_TIMEOUT_MS });
    await page.locator('.dc-grid[data-arriving="false"]').waitFor({ timeout: 10_000 });
  } else {
    await expect(page.getByRole('heading', { level: 1, name: 'No structures were detected' })).toBeVisible({ timeout: RESULTS_TIMEOUT_MS });
  }
  return getJob(jobId);
}

test.beforeAll(async ({ browser }) => {
  const { baseURL } = test.info().project.use;
  context = await browser.newContext({ baseURL, viewport: { width: 1280, height: 800 } });
  await markReturningTab(context); // the animation's flag is per tab (session storage): this one has shown it already
  page = await context.newPage();
  problems = watchProblems(page);
  trackJobs();
  page.on('request', (r) => {
    const { protocol, host } = new URL(r.url());
    if (protocol.startsWith('http')) hosts.add(host);
  });
  page.on('requestfailed', (r) => failedRequests.push(`${r.method()} ${r.url()} ${r.failure()?.errorText ?? ''}`));
  page.on('response', (r) => {
    const path = new URL(r.url()).pathname;
    if (path.startsWith('/api/')) apiLog.push(`${r.request().method()} ${path} -> ${r.status()}`);
    else if (r.status() >= 400) failedRequests.push(`${r.request().method()} ${path} -> ${r.status()}`);
  });
});

// eslint-disable-next-line no-empty-pattern -- Playwright reads the fixtures a hook uses from this pattern; there are none
test.afterAll(async ({}, testInfo) => {
  if (!context) return;
  for (const id of openJobs) {
    const response = await context.request.delete(`/api/jobs/${id}`).catch(() => null);
    console.log(`PARITY cleanup: DELETE ${id} -> ${response?.status() ?? 'no answer'}`);
  }
  facts.set('console.errors, page errors and CSP violations on the new stack (whole run)', problems);
  facts.set('network: hosts the page talked to (whole run)', [...hosts]);
  facts.set('network: failed requests and 4xx/5xx other than /api (whole run)', failedRequests);
  facts.set('network: /api calls (whole run)', apiLog);
  facts.flush();
  writeFileSync(resolve(testInfo.project.outputDir, 'api-log.txt'), apiLog.join('\n'));
  await context.close();
});

// ---------------------------------------------------------------------------------------------------------------
test('the empty screen: 16 first visit, 1 picker and keyboard, 2 drag highlight, 4 format and mix errors, 390 px', async ({ browser, baseURL }) => {
  // 16: a new tab (empty session storage) gets the animation, and the tab remembers it once shown.
  const first = await browser.newContext({ baseURL, viewport: { width: 1280, height: 800 } }); // nothing stored: a new tab
  const firstPage = await first.newPage();
  const firstProblems = watchProblems(firstPage);
  await firstPage.goto('./');
  await expect(firstPage.locator('img.dc-intro')).toBeVisible();
  await firstPage.waitForTimeout(1200);
  await shot(firstPage, 'new', 'n01-empty-first-visit');
  await expect.poll(() => firstPage.evaluate(() => sessionStorage.getItem('decimer.visited')), { timeout: 15_000 }).toBe('1');
  facts.set('16.first visit', {
    animation: await firstPage.locator('img.dc-intro').getAttribute('src'),
    alt: await firstPage.locator('img.dc-intro').getAttribute('alt'),
    visitedKeyAfter: await firstPage.evaluate(() => sessionStorage.getItem('decimer.visited')),
    taglineIsTextForAssistiveTechnology: await firstPage.getByText('Extract chemical structures from PDFs and images').getAttribute('class'),
    consoleProblems: firstProblems,
  });
  await firstPage.reload();
  await expect(firstPage.locator('img.dc-wordmark--hero')).toBeVisible();
  facts.set('16.return visit', { staticWordmark: true, animationImages: await firstPage.locator('img.dc-intro').count() });
  await first.close();

  await page.goto('./');
  const response = await page.request.get('./');
  facts.set('http.home', { status: response.status(), csp: response.headers()['content-security-policy'] ?? null });
  await expect(page.getByRole('heading', { level: 1, name: 'DECIMER.ai' })).toBeVisible();
  await shot(page, 'new', 'n02-empty-return-visit');

  // 1: the picker is multi-file and the way in is a button (keyboard).
  facts.set('1.upload area', {
    text: (await page.locator('.dc-upload').innerText()).replace(/\s+/g, ' ').trim(),
    inputMultiple: await page.locator('input[type=file]').evaluate((el) => (el as HTMLInputElement).multiple),
    inputAccept: await page.locator('input[type=file]').getAttribute('accept'),
  });
  await page.keyboard.press('Tab'); // skip link
  await page.keyboard.press('Tab');
  const focused = await page.evaluate(() => document.activeElement?.textContent?.trim() ?? '');
  facts.set('1.keyboard: the focused element after Tab, Tab', focused);
  await page.getByRole('button', { name: 'Choose files' }).focus();
  await shot(page, 'new', 'n03-choose-files-focus');
  const [chooser] = await Promise.all([page.waitForEvent('filechooser'), page.getByRole('button', { name: 'Choose files' }).click()]);
  facts.set('1.the file chooser (Choose files button)', { isMultiple: chooser.isMultiple() });
  // The dialog is left as it is (cancelled): no file is chosen.

  // 2: a drag over the area highlights it.
  const crop = fileFrom(CROP, 'crop.png');
  await dragEvents(page, '.dc-drop', [crop], ['dragenter', 'dragover']);
  facts.set('2.while dragging over', { active: await page.locator('.dc-drop').getAttribute('data-active'), text: (await page.locator('.dc-drop__title').innerText()).trim() });
  await shot(page, 'new', 'n04-drag-highlight');
  await dragEvents(page, '.dc-drop', [crop], ['dragleave']);
  facts.set('2.after the drag leaves', await page.locator('.dc-drop').getAttribute('data-active'));

  // 4: the format error and the PDF + image mix are refused in the browser, before anything is sent.
  const posts: string[] = [];
  const onRequest = (r: { method(): string; url(): string }) => {
    if (r.method() === 'POST' && new URL(r.url()).pathname.endsWith('/api/jobs')) posts.push(r.url());
  };
  page.on('request', onRequest);
  await page.locator('input[type=file]').setInputFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('not a structure') });
  await expect(page.getByRole('alert')).toContainText('notes.txt is not a supported file. Use PDF, PNG, JPG, WebP or HEIC.');
  facts.set('4.format error', (await page.getByRole('alert').innerText()).replace(/\s+/g, ' ').trim());
  await shot(page, 'new', 'n05-format-error');
  await page.locator('input[type=file]').setInputFiles([
    { name: 'paper.pdf', mimeType: 'application/pdf', buffer: blankPdf(1) },
    { name: 'structure.png', mimeType: 'image/png', buffer: readFileSync(CROP) },
  ]);
  await expect(page.getByRole('alert')).toContainText('A PDF and images cannot be uploaded together.');
  facts.set('4.PDF and image mix error', (await page.getByRole('alert').innerText()).replace(/\s+/g, ' ').trim());
  await shot(page, 'new', 'n06-mixed-error');
  await page.waitForTimeout(500);
  page.off('request', onRequest);
  facts.set('4.jobs created by the two refused selections', posts.length);
  expect(posts).toEqual([]);

  // Hidden fields, duplicate ids, the footer and the header, 390 px.
  facts.set('defects.hidden inputs on the empty screen (the file input is the only input)', await page.locator('input:not([type=file])').count());
  facts.set('defects.duplicate ids on the empty screen', await duplicateIds(page));
  facts.set('15.header', await page.locator('header a, header button').evaluateAll((els) => els.map((el) => `${el.textContent?.trim()} -> ${(el as HTMLAnchorElement).href ?? ''}`)));
  facts.set('15.footer', await page.locator('footer').evaluate((el) => ({
    text: (el as HTMLElement).innerText.replace(/\s+/g, ' ').trim(),
    links: [...el.querySelectorAll('a')].map((a) => `${a.textContent?.trim()} -> ${a.href}`),
  })));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  facts.set('defects.sideways scroll at 390 px, empty screen (px)', await overflowX(page));
  await shot(page, 'new', 'n07-empty-390');
  await page.setViewportSize({ width: 1280, height: 800 });
  expect(problems).toEqual([]);
});

test('a pasted image (job A): 3 paste, 5 stages, 9 result, 10-12 links and report, 13 editor, 14 exports, 18 name', async () => {
  test.setTimeout(900_000);
  await page.goto('./');
  const states = watchStates('n10-stage');
  const created = page.waitForResponse((r) => isApi(r, 'POST', '/api/jobs'), { timeout: 60_000 });
  const started = Date.now();
  expect(await pasteFile(page, fileFrom(CROP, 'image.png'))).toBe(true);
  expect((await created).status()).toBe(202);
  facts.set('3.the paste started the job with no further click', { jobCreated: true, msFromPaste: Date.now() - started });
  const job = await finished('results');
  const timeline = await states.stop();
  facts.set('5.stages seen (seconds since the paste, then what the page said)', timeline);
  facts.set('9.wall time from the paste to the results (s)', Math.round((Date.now() - started) / 100) / 10);
  await shot(page, 'new', 'n11-results', { fullPage: true });
  const first = job.results[0];
  if (!first) throw new Error('job A has no result');
  facts.set('defects.the 1-hour promise: the job record says when it is deleted (seconds after creation)', (Date.parse(job.expiresAt) - Date.parse(job.createdAt)) / 1000);
  facts.set('9.job A (API)', { results: job.results.length, status: job.status, pages: job.pages, bbox: first.bbox, isStructure: first.isStructure, valid: first.valid, confidence: first.confidence, name: first.name });

  // 9: the result block.
  facts.set('9.tile', {
    text: (await tile(1).innerText()).replace(/\s+/g, ' ').trim(),
    structureImage: ((await tile(1).locator('.dc-tile__structure').getAttribute('src')) ?? '').slice(0, 30),
    cropImageLoaded: await tile(1).locator('.dc-tile__crop').evaluate((img) => (img as HTMLImageElement).naturalWidth > 0),
    heading: await page.getByRole('heading', { level: 1 }).innerText(),
    source: await page.locator('.dc-doc__name').innerText(),
    counts: await page.getByRole('list', { name: 'Status of the structures' }).innerText().catch(() => ''),
    capacity: await page.locator('.dc-capacity').innerText(),
  });
  // 18: the name, and its hover.
  await tile(1).hover();
  const tooltip = page.getByRole('tooltip');
  await expect(tooltip).toBeVisible();
  facts.set('18.name on the tile and its hover', { name: await tile(1).locator('.dc-tile__name').innerText(), hover: await tooltip.locator('.dc-hover__line').allTextContents() });
  await shot(page, 'new', 'n12-name-hover');
  await page.mouse.move(0, 0);

  // 8: does the page offer the uploaded document (earlier version: a "Display uploaded document" checkbox, which for a single image shows that image)?
  const pageFile = await page.request.get(`/api/jobs/${jobId}/media/page-0.png`);
  facts.set('8.display the uploaded document, image job (A)', {
    controlsOnThePageMatching: await page.getByText(/uploaded document|page images|show pages|original page/i).allInnerTexts(),
    checkboxesOnThePage: await page.locator('input[type=checkbox]').count(),
    pageImageOnTheApi: { url: `/api/jobs/${jobId}/media/page-0.png`, status: pageFile.status(), type: pageFile.headers()['content-type'] ?? null },
    pageImagesInTheDom: await page.locator('img[src*="page-"]').count(),
  });

  // 13: the editor.
  await tile(1).click();
  await waitForEditor(page);
  await expect(panel().getByRole('heading', { name: 'Editing 1' })).toBeVisible();
  await shot(page, 'new', 'n13-editor');
  const molfile = first.molfile;
  const atomLines = molfile.split('\n').slice(4).filter((l) => /^\s*-?\d+\.\d+\s+-?\d+\.\d+/.test(l));
  facts.set('13.the recognised molfile (API)', {
    header: molfile.split('\n').slice(0, 4).map((l) => l.trim()),
    atoms: atomLines.length,
    withNonZeroXY: atomLines.filter((l) => /[1-9]/.test(l.slice(0, 20).replace(/[-.\s0]/g, ''))).length,
  });
  facts.set('9.panel text', {
    smiles: await panel().locator('.dc-code__text').first().innerText(),
    reasons: await panel().locator('.dc-reasons li').allInnerTexts(),
    instanceOfKetcher: await page.locator('.Ketcher-root').count(),
    iframes: await page.locator('iframe').count(),
  });

  // 10, 11: PubChem (with and without stereo) and COCONUT.
  const links = panel().locator('.dc-links');
  const pubchem = links.getByRole('link', { name: /^PubChem/ });
  const coconut = links.getByRole('link', { name: /^COCONUT/ });
  const before = await pubchem.getAttribute('href');
  const stereoSwitch = panel().getByRole('switch', { name: 'Search PubChem with stereochemistry' });
  const hasSwitch = (await stereoSwitch.count()) > 0;
  let after: string | null = null;
  if (hasSwitch) {
    await stereoSwitch.uncheck();
    after = await pubchem.getAttribute('href');
  }
  facts.set('10.PubChem links', { inchikey: first.inchikey, withStereoHref: before, switchPresent: hasSwitch, withoutStereoHref: after, label: await links.innerText() });
  facts.set('11.COCONUT link', { href: await coconut.getAttribute('href') });
  await shot(page, 'new', 'n14-links', { locator: panel().locator('.dc-followups') });
  if (hasSwitch) await stereoSwitch.check();

  // 12: the report.
  const reported = page.waitForResponse((r) => isApi(r, 'POST', `/api/jobs/${jobId}/report`), { timeout: 30_000 });
  await panel().getByRole('button', { name: 'Report a problem' }).click();
  const reportResponse = await reported;
  await expect(panel().getByRole('status').filter({ hasText: 'Thank you for your report' })).toBeVisible();
  facts.set('12.report', { status: reportResponse.status(), body: reportResponse.request().postDataJSON(), thanks: 'Thank you for your report' });
  await shot(page, 'new', 'n15-report-thanks', { locator: panel().locator('.dc-followups') });

  // 13: modified and reset.
  const validated = page.waitForResponse((r) => isApi(r, 'POST', `/api/jobs/${jobId}/results/0/validate`), { timeout: 60_000 });
  await layoutOnCanvas(page);
  expect((await validated).status()).toBe(200);
  await expect(tile(1)).toContainText('Edited');
  await expect(panel().getByText('Edited by you')).toBeVisible();
  facts.set('13.after an edit', { tile: (await tile(1).innerText()).replace(/\s+/g, ' ').trim(), change: 'Edited by you', resetEnabled: await panel().getByRole('button', { name: 'Reset' }).isEnabled() });
  await shot(page, 'new', 'n16-edited');
  await panel().getByRole('button', { name: 'Reset' }).click();
  await expect(panel().getByText('Same as recognised')).toBeVisible();
  await expect(tile(1)).not.toContainText('Edited');
  facts.set('13.after Reset', { tile: (await tile(1).innerText()).replace(/\s+/g, ' ').trim(), change: 'Same as recognised', resetEnabled: await panel().getByRole('button', { name: 'Reset' }).isEnabled() });
  await shot(page, 'new', 'n17-reset');

  // 14: the exports, started at once after an edit (the race of the earlier version's submit_with_updated_molfiles).
  await layoutOnCanvas(page);
  const exported = page.waitForResponse((r) => isApi(r, 'POST', `/api/jobs/${jobId}/export`), { timeout: 120_000 });
  await downloadButton(page).click();
  await page.waitForTimeout(500); // the menu fades in
  await shot(page, 'new', 'n18-download-menu');
  const [zipDownload] = await Promise.all([
    page.waitForEvent('download', { timeout: 120_000 }),
    page.getByRole('menuitem', { name: /^ZIP/ }).click({ noWaitAfter: true }),
  ]);
  const zipResponse = await exported;
  mkdirSync(resolve(dir('new'), 'exports'), { recursive: true });
  const zipPath = resolve(dir('new'), 'exports', 'new-download.zip');
  await zipDownload.saveAs(zipPath);
  const zipFiles = readZip(readFileSync(zipPath));
  const names = [...zipFiles.keys()];
  const mol = zipFiles.get('structure-01.mol')?.toString('utf8') ?? '';
  const smilesTxt = zipFiles.get('results.smiles.txt')?.toString('utf8') ?? '';
  const editedMolfile = await (async () => {
    const details = panel().locator('details.dc-molfile');
    if (!(await details.evaluate((el) => (el as HTMLDetailsElement).open))) await details.locator('summary').click();
    return (await details.locator('pre').textContent()) ?? '';
  })();
  facts.set('14.new ZIP (exported at once after an edit)', {
    suggestedFilename: zipDownload.suggestedFilename(),
    entries: names,
    smilesTxt,
    request: zipResponse.request().postDataJSON() ? { format: (zipResponse.request().postDataJSON() as { format: string }).format, molfileKeys: Object.keys((zipResponse.request().postDataJSON() as { molfiles: object }).molfiles) } : null,
    molIsTheEditNotTheRecognisedOne: !mol.includes('RDKit') && throughEnd(mol) === throughEnd(editedMolfile),
    cropIsTheJobsCrop: zipFiles.get('structure-01.png')?.equals(Buffer.from(await (await page.request.get(first.cropUrl)).body())) ?? false,
  });
  expect(names).toContain('structure-01.png');
  expect(names).toContain('structure-01.mol');
  expect(names).toContain('results.smiles.txt');
  expect(mol).not.toContain('RDKit');
  const sdfWait = page.waitForResponse((r) => isApi(r, 'POST', `/api/jobs/${jobId}/export`), { timeout: 120_000 });
  await downloadButton(page).click();
  const [sdfDownload] = await Promise.all([page.waitForEvent('download'), page.getByRole('menuitem', { name: /^SDF/ }).click({ noWaitAfter: true })]);
  await sdfWait;
  const sdfPath = resolve(dir('new'), 'exports', 'new-download.sdf');
  await sdfDownload.saveAs(sdfPath);
  const sdf = readFileSync(sdfPath, 'utf8');
  const csvWait = page.waitForResponse((r) => isApi(r, 'POST', `/api/jobs/${jobId}/export`), { timeout: 120_000 });
  await downloadButton(page).click();
  const [csvDownload] = await Promise.all([page.waitForEvent('download'), page.getByRole('menuitem', { name: /^CSV/ }).click({ noWaitAfter: true })]);
  await csvWait;
  const csvPath = resolve(dir('new'), 'exports', 'new-download.csv');
  await csvDownload.saveAs(csvPath);
  const csv = readFileSync(csvPath, 'utf8');
  facts.set('14.new SDF and CSV', {
    sdfFilename: sdfDownload.suggestedFilename(),
    sdfFields: [...sdf.matchAll(/^> <([A-Z_]+)>/gm)].map((m) => m[1]),
    csvFilename: csvDownload.suggestedFilename(),
    csvRows: parseCsv(csv),
  });
  facts.set('18.the name in the exports', { smilesTxtThirdColumn: smilesTxt.split('\n')[0]?.split('\t')[2] ?? null, csvName: parseCsv(csv)[1]?.[4] ?? null, jobName: first.name });

  // The defects that must not come back, measured on this page with the editor open.
  await page.mouse.move(0, 0);
  const dom = await page.evaluate(() => ({
    otherInputs: document.querySelectorAll('input:not([type=file])').length,
    hiddenInputs: document.querySelectorAll('input[type=hidden]').length,
    ketcherRoots: document.querySelectorAll('.Ketcher-root').length,
    iframes: document.querySelectorAll('iframe').length,
    serverPathsInDom: [...document.querySelectorAll('[src],[href],[value]')].map((e) => e.getAttribute('src') ?? e.getAttribute('href') ?? e.getAttribute('value') ?? '').filter((v) => /\/var\/|\/app\/|\/home\/|\/data\/|storage\//.test(v) && !v.startsWith('data:')).slice(0, 5),
  }));
  const apiText = JSON.stringify(job);
  facts.set('defects.DOM and API on the results page', {
    ...dom,
    duplicateIds: await duplicateIds(page),
    serverPathsInApiJson: apiText.match(/\/var\/[^"]*|\/app\/[^"]*|\/data\/[^"]*|\/home\/[^"]*/g)?.slice(0, 5) ?? [],
    cropUrlExample: first.cropUrl,
    cropUrlIsAnotherJobsGuessable: false,
  });
  const readableAfter = await page.request.get(first.cropUrl);
  facts.set('defects.crop media while the job exists', readableAfter.status());

  // 390 px with the results (panel closed) and with the editor open (a sheet).
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(500);
  facts.set('defects.sideways scroll at 390 px, results with the editor open (px)', await overflowX(page));
  await shot(page, 'new', 'n19-results-390-editor');
  await panel().getByRole('button', { name: 'Close' }).click();
  await page.waitForTimeout(500);
  facts.set('defects.sideways scroll at 390 px, results (px)', await overflowX(page));
  await shot(page, 'new', 'n20-results-390', { fullPage: true });
  await page.setViewportSize({ width: 1280, height: 800 });

  // Start over: the confirmation (there is an edit), then the job is deleted (404).
  const id = jobId;
  await startOver(id, 'n21-confirm-new-upload');
  facts.set('defects.the 1-hour promise: the job and its crops are deleted on request', { jobAfterDelete: (await page.request.get(`/api/jobs/${id}`)).status(), cropAfterDelete: (await page.request.get(first.cropUrl)).status() });
  expect(problems).toEqual([]);
});

test('a dropped image with no structure (job B): 2 a drop starts at once, 6 the single-image fallback, 9 the classifier warning', async () => {
  test.setTimeout(900_000);
  await page.goto('./');
  const image = await textImage(page, ['Figure 3. Isolation scheme of the extract', 'and the fractions that were collected.']);
  const created = page.waitForResponse((r) => isApi(r, 'POST', '/api/jobs'), { timeout: 60_000 });
  await dragEvents(page, '.dc-drop', [image], ['dragenter', 'dragover', 'drop']);
  expect((await created).status()).toBe(202);
  facts.set('2.a drop started the job with no further click', true);
  const job = await finished('results');
  await shot(page, 'new', 'n22-text-only-results', { fullPage: true });
  const first = job.results[0];
  facts.set('6.single image, no structure found: OCSR ran on the whole image', {
    results: job.results.length,
    bbox: first?.bbox,
    isStructure: first?.isStructure,
    valid: first?.valid,
    smiles: first?.smiles,
    warnings: job.warnings,
    structuresFound: job.structuresFound,
  });
  await tile(1).click();
  await waitForEditor(page, 800);
  facts.set('9.classifier warning and the combined invalid / R-group warning', {
    tile: (await tile(1).innerText()).replace(/\s+/g, ' ').trim(),
    reasons: await panel().locator('.dc-reasons li').allInnerTexts(),
    counts: await page.getByRole('list', { name: 'Status of the structures' }).innerText().catch(() => ''),
  });
  await shot(page, 'new', 'n23-text-only-editor');
  const id = jobId;
  await startOver(id);
  expect(problems).toEqual([]);
});

test('two images picked together (job C): 1 multi-file, and what each is read as (the earlier version reads each image whole)', async () => {
  test.setTimeout(900_000);
  await page.goto('./');
  const two = resolve(NEGATIVES, 'n09_two_5_6.png');
  expect(existsSync(two), `golden/single-negatives is mounted at ${NEGATIVES}`).toBe(true);
  const created = page.waitForResponse((r) => isApi(r, 'POST', '/api/jobs'), { timeout: 60_000 });
  const [chooser] = await Promise.all([page.waitForEvent('filechooser'), page.getByRole('button', { name: 'Choose files' }).click()]);
  await chooser.setFiles([
    { name: 'tight-crop.png', mimeType: 'image/png', buffer: readFileSync(CROP) },
    { name: 'two-structures.png', mimeType: 'image/png', buffer: readFileSync(two) },
  ]);
  expect((await created).status()).toBe(202);
  const job = await finished('results');
  await shot(page, 'new', 'n24-two-images', { fullPage: true });
  facts.set('1.two images in one upload', {
    results: job.results.length,
    pages: job.pages,
    perResult: job.results.map((r) => ({ number: r.number, page: r.pageIndex, bbox: r.bbox, smiles: r.smiles })),
    note: 'tight-crop.png is 379 x 342 px, two-structures.png is 877 x 375 px; the earlier version sends each uploaded image whole to OCSR when more than one is uploaded',
  });
  const id = jobId;
  await startOver(id);
  expect(problems).toEqual([]);
});

test('a figure with two structures pasted (job D): the defect "comes back as one whole-image result" does not', async () => {
  test.setTimeout(900_000);
  await page.goto('./');
  const two = resolve(NEGATIVES, 'n09_two_5_6.png');
  const created = page.waitForResponse((r) => isApi(r, 'POST', '/api/jobs'), { timeout: 60_000 });
  expect(await pasteFile(page, fileFrom(two, 'image.png'))).toBe(true);
  expect((await created).status()).toBe(202);
  const job = await finished('results');
  await shot(page, 'new', 'n25-pasted-figure', { fullPage: true });
  facts.set('defects.a pasted figure holding two structures (golden single-negatives/n09_two_5_6.png, 877 x 375 px)', {
    results: job.results.length,
    bboxes: job.results.map((r) => r.bbox),
    wholeImageResult: job.results.some((r) => r.bbox[0] === 0 && r.bbox[1] === 0 && r.bbox[2] >= 876 && r.bbox[3] >= 374),
  });
  expect(job.results.length).toBeGreaterThan(1);
  const id = jobId;
  await startOver(id);
  expect(problems).toEqual([]);
});

// Like the earlier version, a multi-image upload is not segmented; each image is read whole in its own
// slot, so two blank images give two results (the earlier version shows whatever DECIMER read), not "No structures".
test('two blank images (job E): 7 a multi-image upload reads each image whole', async () => {
  test.setTimeout(900_000);
  await page.goto('./');
  const a = await blankImage(page, 'blank-a.png');
  const b = await blankImage(page, 'blank-b.png', 320, 220);
  const created = page.waitForResponse((r) => isApi(r, 'POST', '/api/jobs'), { timeout: 60_000 });
  await page.locator('input[type=file]').setInputFiles([a, b].map((f: FileSpec) => ({ name: f.name, mimeType: f.mimeType, buffer: Buffer.from(f.bytes) })));
  expect((await created).status()).toBe(202);
  const job = await finished('results');
  await shot(page, 'new', 'n26-two-blank-images');
  expect(job.status).toBe('done');
  expect(job.results.length).toBe(2); // one slot per image, in upload order
  facts.set('7.two blank images (each read whole)', { results: job.results.length, status: job.status, smiles: job.results.map((r) => r.smiles), warnings: job.warnings.map((w) => w.code), jobName: 'two blank images' });
  const id = jobId;
  await startOver(id);
  expect(problems).toEqual([]);
});

test('an 11-page PDF (job F): 7 the 10-page warning', async () => {
  test.setTimeout(900_000);
  await page.goto('./');
  const created = page.waitForResponse((r) => isApi(r, 'POST', '/api/jobs'), { timeout: 60_000 });
  await page.locator('input[type=file]').setInputFiles({ name: 'eleven-pages.pdf', mimeType: 'application/pdf', buffer: blankPdf(11) });
  const response = await created;
  facts.set('7.11-page PDF: job created', { status: response.status(), request: 'one PDF of 11 blank pages' });
  // No structure on blank pages: the none view, which carries the page warning.
  await expect(page.getByRole('heading', { level: 1, name: 'No structures were detected' }).or(page.getByRole('list', { name: 'Recognised structures' }))).toBeVisible({ timeout: RESULTS_TIMEOUT_MS });
  const job = await getJob(jobId);
  await shot(page, 'new', 'n27-eleven-pages');
  facts.set('7.the 10-page warning', {
    pages: job.pages,
    pagesProcessed: job.pagesProcessed,
    truncated: job.truncated,
    warnings: job.warnings,
    shownOnPage: await page.getByRole('note').allInnerTexts(),
  });
  const id = jobId;
  await startOver(id);
  expect(problems).toEqual([]);
});

test('the golden PDF (job G): 7 the 20-structure warning, 8 the uploaded-document toggle, 5 stages of a long job', async () => {
  test.setTimeout(900_000);
  expect(existsSync(GOLDEN_PDF), `the golden PDF is at ${GOLDEN_PDF} (mount golden/ and set PARITY_PDF)`).toBe(true);
  await page.goto('./');
  const states = watchStates('n30-pdf-stage', 10);
  const created = page.waitForResponse((r) => isApi(r, 'POST', '/api/jobs'), { timeout: 60_000 });
  const started = Date.now();
  const [chooser] = await Promise.all([page.waitForEvent('filechooser'), page.getByRole('button', { name: 'Choose files' }).click()]);
  await chooser.setFiles(GOLDEN_PDF);
  expect((await created).status()).toBe(202);
  const job = await finished('results');
  const timeline = await states.stop();
  facts.set('5.stages of the PDF job (seconds since the picker, then what the page said)', timeline);
  facts.set('golden PDF wall time on the new stack (s)', Math.round((Date.now() - started) / 100) / 10);
  await shot(page, 'new', 'n31-pdf-results', { fullPage: true });
  facts.set('7.the 20-structure warning', {
    tiles: await page.locator('.dc-tile').count(),
    capacity: await page.locator('.dc-capacity').innerText(),
    structuresFound: job.structuresFound,
    structuresDropped: job.structuresDropped,
    truncated: job.truncated,
    warnings: job.warnings,
    shownOnPage: await page.getByRole('note').allInnerTexts(),
  });
  // 8: is there a way to see the uploaded document's pages?
  const toggles = await page.getByText(/uploaded document|page images|Show pages|Display/i).allInnerTexts();
  const pageUrl = `/api/jobs/${jobId}/media/page-0.png`;
  const pageFile = await page.request.get(pageUrl);
  facts.set('8.display the uploaded document (page images)', {
    controlsOnThePageMatching: toggles,
    checkboxes: await page.locator('input[type=checkbox]').count(),
    pageImageOnTheApi: { url: pageUrl, status: pageFile.status(), bytes: pageFile.status() === 200 ? (await pageFile.body()).length : 0 },
    pageImagesInTheDom: await page.locator('img[src*="page-"]').count(),
  });
  facts.set('defects.20 Ketcher instances: Ketcher roots with 20 results and the editor opened', await (async () => {
    await tile(1).click();
    await waitForEditor(page, 800);
    return { ketcherRoots: await page.locator('.Ketcher-root').count(), iframes: await page.locator('iframe').count() };
  })());
  const id = jobId;
  await panel().getByRole('button', { name: 'Close' }).click();
  await startOver(id);
  expect(problems).toEqual([]);
});

test('15: About, Privacy and Impressum on the new stack', async () => {
  for (const [path, name] of [['about', 'n40-about'], ['privacy', 'n41-privacy'], ['impressum', 'n42-impressum']] as const) {
    await page.goto(`./#/${path}`);
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    await page.waitForTimeout(300);
    const text = await page.locator('main').innerText();
    facts.set(`15.${path}`, {
      h1: await page.getByRole('heading', { level: 1 }).innerText(),
      headings: await page.getByRole('heading', { level: 2 }).allInnerTexts(),
      mentionsSTOUT: /STOUT/i.test(text),
      mentionsGoogleAnalytics: /Google Analytics/i.test(text),
      mentionsMatomo: /Matomo/i.test(text),
      mentionsOrthonym: /Orthonym/i.test(text),
      confirmMarkersShown: await page.locator('[data-confirm], .dc-confirm, mark').count(),
      confirmTexts: (text.match(/\[CONFIRM[^\]]*\]/g) ?? []).length,
      characters: text.length,
    });
    await shot(page, 'new', name, { fullPage: true });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  facts.set('defects.sideways scroll at 390 px, About (px)', await overflowX(page));
  await page.setViewportSize({ width: 1280, height: 800 });
  expect(problems).toEqual([]);
});
