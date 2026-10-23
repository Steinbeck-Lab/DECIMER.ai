import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page, type Route } from '@playwright/test';
import { readZip } from '../live/zip';
import { CROP, dir, dragEvents, duplicateIds, Facts, fileFrom, overflowX, pasteFile, shot } from './shared';

// The legacy half of the parity sign-off (TODO, ROADMAP §6): how https://decimer.ai does each item, as
// screenshots and recorded facts. READ-ONLY and LIGHT: it is production.
//   - ONE real job: the one small image (e2e/live/fixtures/00_crop-0.png), through the file picker. Set
//     PARITY_LEGACY_UPLOAD=1 for it; without it the run only loads pages and exercises the page's own scripts.
//   - Everything else that would upload (paste, drop) is held and answered with 204 before it reaches the server: the page's
//     reaction (preview, status text, auto-submit) is what is observed. The format error is one request that carries the
//     same image under a `.txt` name (the server refuses it by name before it stores or processes anything).
//   - No PDF is ever sent (the legacy 10-page and 20-structure warnings, the page toggle and "No structures were
//     detected" are read from the blade view that the site serves, whose HTML is checked against the repository's).
//   - The problem report is held and answered with 204 (it would put a test report in the maintainers' data); the analytics
//     scripts (Google, Matomo) are blocked, so this run does not count as a visit.
// Run: PARITY_LEGACY_URL=https://decimer.ai PARITY_LEGACY_UPLOAD=1 npx playwright test --config e2e/parity/parity.config.ts --project legacy

const UPLOAD = process.env.PARITY_LEGACY_UPLOAD === '1';
const ANALYTICS = /googletagmanager\.com|google-analytics\.com|matomo\.nfdi4chem\.de/;
const facts = new Facts('legacy');

/** Console errors of every page of the run, attributed to the file that raised them. */
const consoleErrors: string[] = [];
const pageErrors: string[] = [];

test.describe.configure({ mode: 'serial' });

/**
 * Every text the page puts in its status line (`#loading_text`), kept in sessionStorage across the page loads of a job
 * (legacy navigates for every stage). `window.name` is not used: the page's own scripts do not touch either.
 */
const STATUS_LOG = () => {
  document.addEventListener('DOMContentLoaded', () => {
    const el = document.getElementById('loading_text');
    if (!el) return;
    new MutationObserver(() => {
      try {
        const log = JSON.parse(sessionStorage.getItem('parity.status') ?? '[]') as string[];
        log.push(`${Math.round(performance.timeOrigin + performance.now())}\t${location.pathname}\t${el.textContent ?? ''}`);
        sessionStorage.setItem('parity.status', JSON.stringify(log));
      } catch {
        // storage unavailable: the log is only evidence
      }
    }).observe(el, { childList: true, characterData: true, subtree: true });
  });
};

test.beforeEach(async ({ context, page }) => {
  await context.route(ANALYTICS, (route) => route.abort());
  await context.addInitScript(STATUS_LOG);
  watch(page);
});

test.afterAll(() => {
  facts.set('console.errors (all pages of the run, analytics blocked by this run excluded)', consoleErrors);
  facts.set('console.pageerrors (all pages of the run)', pageErrors);
  facts.flush();
});

function watch(page: Page) {
  page.on('console', (m) => {
    if (m.type() !== 'error') return;
    const url = m.location().url;
    if (ANALYTICS.test(url)) return;
    consoleErrors.push(`${m.text()} @ ${url.replace(/^https?:\/\/[^/]+/, '')}`);
  });
  page.on('pageerror', (e) => pageErrors.push(e.message));
}

const sleep = (ms: number) => new Promise((done) => setTimeout(done, ms));

/**
 * Holds a POST to `pattern` for `ms` (so its screenshot can be taken), then answers it with 204 No Content: nothing
 * reaches the server, and the page stays as it is (an aborted form post would replace it with Chrome's error page).
 */
async function holdAndSwallow(page: Page, pattern: string, ms: number, seen: { method: string; bytes: number; type: string }[]) {
  await page.route(pattern, async (route: Route) => {
    const request = route.request();
    if (request.method() !== 'POST') return route.continue();
    seen.push({ method: request.method(), bytes: request.postDataBuffer()?.length ?? 0, type: request.headers()['content-type']?.split(';')[0] ?? '' });
    await sleep(ms);
    await route.fulfill({ status: 204 });
  });
}

test('the page: first visit and return visit, logo, tagline, keyboard, hidden fields, duplicate ids', async ({ browser, baseURL }) => {
  const context = await browser.newContext({ baseURL, viewport: { width: 1280, height: 800 } });
  await context.route(ANALYTICS, (route) => route.abort());
  const page = await context.newPage();
  watch(page);
  const response = await page.goto('./');
  facts.set('http.home', { status: response?.status(), server: response?.headers().server, csp: response?.headers()['content-security-policy'] ?? null });
  await page.waitForLoadState('load');
  await page.waitForTimeout(1500);

  facts.set('16.first visit', {
    gifDisplay: await page.evaluate(() => getComputedStyle(document.getElementById('decimer_logo_gif') as Element).display),
    pngDisplay: await page.evaluate(() => getComputedStyle(document.getElementById('decimer_logo') as Element).display),
    visitedKey: await page.evaluate(() => localStorage.getItem('visited')),
    taglineElementExists: await page.evaluate(() => document.getElementById('logo_tagline') !== null),
  });
  await shot(page, 'legacy', 'l01-empty-first-visit');

  await page.reload();
  await page.waitForTimeout(800);
  facts.set('16.return visit', {
    gifDisplay: await page.evaluate(() => getComputedStyle(document.getElementById('decimer_logo_gif') as Element).display),
    pngDisplay: await page.evaluate(() => getComputedStyle(document.getElementById('decimer_logo') as Element).display),
  });
  await shot(page, 'legacy', 'l02-empty-return-visit');

  facts.set('1.upload area', await page.evaluate(() => {
    const area = document.getElementById('upload-area');
    const input = document.querySelector<HTMLInputElement>('input.file-input');
    return {
      text: area?.textContent?.replace(/\s+/g, ' ').trim(),
      tagName: area?.tagName,
      tabIndex: area?.tabIndex,
      role: area?.getAttribute('role'),
      inputMultiple: input?.multiple,
      inputAccept: input?.getAttribute('accept'),
      inputName: input?.name,
    };
  }));
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  facts.set('1.keyboard: element focused after 3 Tabs (upload area is not a control)', await page.evaluate(() => document.activeElement?.outerHTML.slice(0, 120)));

  facts.set('defects.hidden inputs on the empty page', await page.locator('input[type=hidden]').count());
  facts.set('defects.duplicate ids on the empty page', await duplicateIds(page));
  facts.set('15.header links', await page.locator('header a').evaluateAll((links) => links.map((a) => `${a.textContent?.trim()} -> ${(a as HTMLAnchorElement).href}`)));
  facts.set('15.footer', await page.locator('footer').evaluate((el) => ({
    text: (el as HTMLElement).innerText.replace(/\s+/g, ' ').trim(),
    links: [...el.querySelectorAll('a')].map((a) => `${a.textContent?.trim()} -> ${a.href}`),
  })));
  facts.set('analytics.scripts on the page', await page.evaluate(() => [...document.scripts].map((s) => s.src).filter((s) => /google|matomo/.test(s))));

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  facts.set('defects.sideways scroll at 390 px, empty page (px)', await overflowX(page));
  await shot(page, 'legacy', 'l03-empty-390');
  await context.close();
});

test('2 and 3: drag highlight, a drop and a paste submit at once (held and answered with 204: nothing is sent)', async ({ page }) => {
  await page.goto('./');
  const crop = fileFrom(CROP, 'crop.png');
  const area = '#upload-area';

  await dragEvents(page, area, [crop], ['dragenter', 'dragover']);
  await page.waitForTimeout(600); // the area has a CSS transition: read the colour once it has settled
  facts.set('2.highlight while dragging over', await page.locator(area).evaluate((el) => ({ classes: el.className, background: getComputedStyle(el).backgroundColor })));
  await shot(page, 'legacy', 'l04-drag-highlight');
  await dragEvents(page, area, [crop], ['dragleave']);
  facts.set('2.highlight after the drag leaves', await page.locator(area).evaluate((el) => el.className.includes('bg-blue-100')));

  // The drop: the page sets the file input and submits the form at once (no status text of its own).
  const dropped: { method: string; bytes: number; type: string }[] = [];
  await holdAndSwallow(page, '**/file-upload', 900, dropped);
  void dragEvents(page, area, [crop], ['drop']).catch(() => undefined); // the page navigates: do not wait for the call
  await page.waitForTimeout(400);
  facts.set('2.status text 400 ms after a drop (the drop handler sets none)', await page.locator('#loading_text').first().textContent());
  await shot(page, 'legacy', 'l05-drop-submits');
  await expect.poll(() => dropped.length, { timeout: 5000 }).toBe(1);
  facts.set('2.the drop submitted a POST at once', dropped);
  await page.unroute('**/file-upload');

  // The paste: preview shown, then submitted after 500 ms with a "Processing pasted image..." status.
  await page.goto('./');
  const pasted: { method: string; bytes: number; type: string }[] = [];
  await holdAndSwallow(page, '**/clipboard-paste', 1500, pasted);
  expect(await pasteFile(page, fileFrom(CROP, 'image.png'))).toBe(true);
  await page.waitForTimeout(250);
  facts.set('3.preview 250 ms after the paste', await page.evaluate(() => ({
    previewVisible: !document.getElementById('preview-area')?.classList.contains('hidden'),
    previewSrcIsDataUrl: (document.getElementById('preview-image') as HTMLImageElement | null)?.src.startsWith('data:image/png') ?? false,
    status: document.getElementById('loading_text')?.textContent,
  })));
  await shot(page, 'legacy', 'l06-paste-preview');
  await page.waitForTimeout(700);
  facts.set('3.status 950 ms after the paste', await page.locator('#loading_text').first().textContent());
  await shot(page, 'legacy', 'l07-paste-processing');
  await expect.poll(() => pasted.length, { timeout: 5000 }).toBe(1);
  facts.set('3.the paste submitted a POST by itself', pasted);
});

test('1, 4 and 5: the file picker is multi-file and says "Uploading files..."; a file the site does not accept', async ({ page }) => {
  await page.goto('./');
  const [chooser] = await Promise.all([page.waitForEvent('filechooser'), page.locator('#upload-area').click({ position: { x: 20, y: 20 } })]);
  facts.set('1.the file chooser opens on a click of the area and accepts several files', { isMultiple: chooser.isMultiple() });

  // The status line while the upload is in flight: the image is chosen, the page sets "Uploading files..." and posts;
  // the post is held and answered with 204, so nothing is sent and the page stays as it is.
  const picked: { method: string; bytes: number; type: string }[] = [];
  await holdAndSwallow(page, '**/file-upload', 1500, picked);
  await chooser.setFiles({ name: 'image.png', mimeType: 'image/png', buffer: readFileSync(CROP) });
  await page.waitForTimeout(500);
  facts.set('5.status text 500 ms after choosing a file', await page.locator('#loading_text').first().textContent());
  await shot(page, 'legacy', 'l08-uploading-status');
  await expect.poll(() => picked.length, { timeout: 5000 }).toBe(1);
  await page.waitForTimeout(1200);
  await page.unroute('**/file-upload');

  // The one real upload that is not the job: the same bytes under the name `crop.txt`, which the server refuses by name.
  await page.goto('./');
  const [second] = await Promise.all([page.waitForEvent('filechooser'), page.locator('#upload-area').click({ position: { x: 20, y: 20 } })]);
  await second.setFiles({ name: 'crop.txt', mimeType: 'text/plain', buffer: readFileSync(CROP) });
  await expect(page.locator('.alert-danger')).toBeVisible({ timeout: 30_000 });
  facts.set('5.status texts the page showed during that upload (time, path, text)', await page.evaluate(() => sessionStorage.getItem('parity.status')));
  facts.set('4.error for an unsupported file name', {
    text: (await page.locator('.alert-danger').innerText()).replace(/\s+/g, ' ').trim(),
    role: await page.locator('.alert-danger').getAttribute('role'),
  });
  await shot(page, 'legacy', 'l09-format-error');
});

test('15 and 17: About, Privacy Policy, Impressum, and the Safari warning (a Safari user agent)', async ({ page, browser, baseURL }) => {
  for (const [path, name] of [['about', 'l10-about'], ['privacy_policy', 'l11-privacy'], ['impressum', 'l12-impressum']] as const) {
    await page.goto(`./${path}`);
    await page.waitForLoadState('load');
    const text = await page.locator('body').innerText();
    facts.set(`15.${path}`, {
      title: await page.title(),
      mentionsSTOUT: /STOUT/i.test(text),
      mentionsGoogleAnalytics: /Google Analytics/i.test(text),
      mentionsMatomo: /Matomo/i.test(text),
      characters: text.length,
      headings: await page.locator('h1, h2, h3, h4').allInnerTexts(),
      hasHeaderAndFooter: { header: await page.locator('header').count(), footer: await page.locator('footer').count() },
    });
    await shot(page, 'legacy', name, { fullPage: true });
  }
  const safari = await browser.newContext({
    baseURL,
    viewport: { width: 1280, height: 800 },
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15',
  });
  await safari.route(ANALYTICS, (route) => route.abort());
  const sPage = await safari.newPage();
  await sPage.goto('./');
  await sPage.waitForLoadState('load');
  facts.set('17.Safari warning (Safari user agent)', {
    text: (await sPage.locator('#alert-if-safari').innerText()).replace(/\s+/g, ' ').trim(),
    className: await sPage.locator('#alert-if-safari').getAttribute('class'),
  });
  await shot(sPage, 'legacy', 'l13-safari-warning');
  await safari.close();
});

test('the live page serves the repository\'s views: the blade logic behind 6, 7 and 8 is the deployed one', async ({ page, baseURL }) => {
  // Not a proof of the server-side branches (they run on the server); it shows that the HTML, scripts and routes the
  // site serves are the repository's. The branches themselves are cited from resources/views/index.blade.php.
  const html = await (await page.request.get(`${baseURL}`)).text();
  const normalised = html.replace(/name="_token" value="[^"]*"/g, '').replace(/https:\/\/decimer\.ai/g, 'HOST');
  mkdirSync(dir('legacy'), { recursive: true });
  writeFileSync(resolve(dir('legacy'), 'home.html'), normalised);
  facts.set('served home page', { bytes: html.length, formAction: html.match(/id="upload_form" action="([^"]+)"/)?.[1], pasteAction: html.match(/id="paste_form" action="([^"]+)"/)?.[1] });
});

test('one job with the small image: the stages, the result block, the editor, the report, the ZIP, 390 px', async ({ page }) => {
  test.skip(!UPLOAD, 'set PARITY_LEGACY_UPLOAD=1 to make the one real upload');
  test.setTimeout(480_000);
  await page.goto('./');
  const [chooser] = await Promise.all([page.waitForEvent('filechooser'), page.locator('#upload-area').click({ position: { x: 20, y: 20 } })]);
  const started = Date.now();
  await chooser.setFiles({ name: 'image.png', mimeType: 'image/png', buffer: readFileSync(CROP) });

  // Every page of the job is a full page load (upload, segmentation, OCSR, results): poll what each one says.
  const timeline: { t: number; signature: string }[] = [];
  let last = '';
  let snap = 0;
  const probe = () =>
    page.evaluate(() => ({
      url: location.pathname,
      loading: [...document.querySelectorAll('#loading_text')].map((e) => e.textContent).join('|'),
      text: [...document.querySelectorAll('section p')].map((p) => (p as HTMLElement).innerText.replace(/\s+/g, ' ').trim()).join(' / ').slice(0, 220),
      structures: document.querySelectorAll('.chemical_structure_img').length,
      done: !!document.getElementById('main_loading_text')?.textContent?.includes('processed'),
      iframes: document.querySelectorAll('iframe[src*=ketcher]').length,
    }));
  const deadline = Date.now() + 360_000;
  let seen: Awaited<ReturnType<typeof probe>> | null = null;
  while (Date.now() < deadline) {
    try {
      seen = await probe();
    } catch {
      await sleep(150); // a navigation is under way
      continue;
    }
    const signature = `${seen.url} :: ${seen.loading} :: ${seen.text} :: ${seen.structures}`;
    if (signature !== last) {
      last = signature;
      timeline.push({ t: Math.round((Date.now() - started) / 100) / 10, signature });
      await shot(page, 'legacy', `l14-stage-${String(++snap).padStart(2, '0')}`).catch(() => undefined);
    }
    if (seen.done) break;
    await sleep(250);
  }
  facts.set('5.stages seen, in order (seconds since the picker, then URL :: header status :: page text :: structure images)', timeline);
  expect(seen?.done, 'the job finished').toBe(true);

  // Ketcher in the one iframe.
  const frame = page.frames().find((f) => f.name() === '1');
  expect(frame, 'the Ketcher iframe named "1"').toBeDefined();
  await frame?.waitForFunction(() => 'ketcher' in window, undefined, { timeout: 60_000 });
  await page.waitForTimeout(4000);
  const total = Math.round((Date.now() - started) / 100) / 10;
  facts.set('9.wall time from the picker to the result page (s)', total);
  facts.set('5.every text the status line showed during the job (epoch ms, path, text)', await page.evaluate(() => JSON.parse(sessionStorage.getItem('parity.status') ?? '[]')));
  await shot(page, 'legacy', 'l15-results', { fullPage: true });

  const block = await page.evaluate(() => {
    const text = (el: Element | null) => (el as HTMLElement | null)?.innerText.replace(/\s+/g, ' ').trim() ?? null;
    const grid = document.querySelector('.grid.grid-cols-3');
    return {
      header: text(document.querySelector('header')),
      gridText: text(grid),
      redWarnings: [...document.querySelectorAll('.text-red-800')].map((e) => text(e)),
      smilesBold: [...document.querySelectorAll('strong')].map((e) => text(e)),
      smiles: text(document.querySelector('a.break-words')),
      pubchemHref: (document.getElementById('pubchem-link-0') as HTMLAnchorElement | null)?.href ?? null,
      toggleExists: document.getElementById('toggle-0') !== null,
      searchTypeText: text(document.getElementById('search-type-0')),
      coconutHref: [...document.querySelectorAll('a')].find((a) => /COCONUT/.test(a.textContent ?? ''))?.href ?? null,
      coconutText: text([...document.querySelectorAll('a')].find((a) => /COCONUT/.test(a.textContent ?? '')) ?? null),
      reportText: text(document.getElementById('problem_report_link_0')),
      reportHref: document.getElementById('problem_report_link_0')?.getAttribute('href') ?? null,
      iupacShown: /IUPAC name/.test(document.body.innerText),
      structureImg: (document.querySelector('.chemical_structure_img') as HTMLImageElement | null)?.getAttribute('src') ?? null,
      pageImagesToggle: document.getElementById('page_image_checkbox') !== null,
      downloadButtons: [...document.querySelectorAll('button')].map((b) => text(b)),
      resultsAreaDownloadText: text(document.querySelector('main .bg-gray-300')),
      iframes: document.querySelectorAll('iframe[src*=ketcher]').length,
      iframeIds: [...document.querySelectorAll('iframe')].map((f) => f.id || f.name),
      hiddenInputs: document.querySelectorAll('input[type=hidden]').length,
      hiddenInputNames: [...document.querySelectorAll('input[type=hidden]')].map((i) => (i as HTMLInputElement).name || i.id).slice(0, 40),
      serverPathsInDom: [...document.querySelectorAll('input[type=hidden]')].map((i) => (i as HTMLInputElement).value).filter((v) => /storage\/|\/var\/|\/app\//.test(v)).slice(0, 5),
    };
  });
  facts.set('9.result block (DOM)', block);
  facts.set('defects.duplicate ids on the results page', await duplicateIds(page));
  facts.set('defects.the crop is a public file at a name-based URL (world-readable by filename)', block.structureImg);

  // 10: the PubChem toggle (only when the InChIKey has stereo, which this structure has).
  if (block.toggleExists) {
    await page.locator('#toggle-0').click(); // the checkbox sits over its label, as a user sees it
    facts.set('10.PubChem after the stereo toggle', {
      label: await page.locator('#search-type-0').innerText(),
      href: await page.locator('#pubchem-link-0').getAttribute('href'),
    });
    await shot(page, 'legacy', 'l16-pubchem-toggle', { locator: page.locator('.grid.grid-cols-3 > div').first() });
  }

  // 13: the editor: Ketcher in an iframe, with a molfile that has 2D coordinates.
  const molfile = await frame?.evaluate(async () => (await (window as unknown as { ketcher: { getMolfile(): Promise<string> } }).ketcher.getMolfile()) as string);
  const atomLines = (molfile ?? '').split('\n').slice(4).filter((l) => /^\s*-?\d+\.\d+\s+-?\d+\.\d+/.test(l));
  facts.set('13.Ketcher molfile on load', {
    version: (molfile ?? '').split('\n')[1]?.trim(),
    atoms: atomLines.length,
    withNonZeroXY: atomLines.filter((l) => /[1-9]/.test(l.slice(0, 20).replace(/[-.\s0]/g, ''))).length,
  });
  await shot(page, 'legacy', 'l17-ketcher-editor', { locator: page.locator('iframe[src*=ketcher]').first() });
  facts.set('13.editor chrome: any "edited" or "reset" control outside Ketcher', await page.evaluate(() => /edited|reset|modified/i.test(document.querySelector('.grid.grid-cols-3')?.textContent ?? '')));

  // 12: the problem report, held and answered with 204.
  const reports: { method: string; bytes: number; type: string }[] = [];
  await page.route('**/problem-report', async (route) => {
    reports.push({ method: route.request().method(), bytes: route.request().postDataBuffer()?.length ?? 0, type: route.request().headers()['content-type']?.split(';')[0] ?? '' });
    await route.fulfill({ status: 204 });
  });
  await page.locator('#problem_report_link_0').scrollIntoViewIfNeeded();
  await page.locator('#problem_report_link_0').click();
  await page.waitForTimeout(500);
  facts.set('12.report link after the click (client-side text; the POST was answered with 204 by this run)', { text: await page.locator('#problem_report_link_0').innerText(), href: await page.locator('#problem_report_link_0').getAttribute('href'), post: reports });
  await shot(page, 'legacy', 'l18-report-thanks', { locator: page.locator('.grid.grid-cols-3 > div.frame').first() });
  await page.unroute('**/problem-report');

  // 14: the ZIP. An edit first (select all, Layout), then Download at once: does the edit reach the file?
  await page.locator('iframe[src*=ketcher]').first().scrollIntoViewIfNeeded();
  const box = await page.locator('iframe[src*=ketcher]').first().boundingBox();
  if (box) {
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    await page.keyboard.press('Control+a');
    await page.keyboard.press('Control+l');
    await page.waitForTimeout(1500);
  }
  const beforeDownload = await frame?.evaluate(async () => (await (window as unknown as { ketcher: { getMolfile(): Promise<string> } }).ketcher.getMolfile()) as string);
  facts.set('13.edit made in Ketcher (select all, Layout): molfile changed', beforeDownload !== molfile);
  await shot(page, 'legacy', 'l19-after-edit', { fullPage: false });

  mkdirSync(resolve(dir('legacy'), 'zip'), { recursive: true });
  await page.evaluate(() => window.scrollTo(0, 0));
  const downloaded = Promise.all([
    page.waitForEvent('download', { timeout: 150_000 }),
    page.locator('header .btn-secondary').click(),
  ]);
  const [download] = await downloaded;
  const zipPath = resolve(dir('legacy'), 'zip', 'legacy-download.zip');
  await download.saveAs(zipPath);
  const files = readZip(readFileSync(zipPath));
  const names = [...files.keys()];
  const smilesFile = files.get('results.smiles.txt')?.toString('utf8') ?? null;
  const molName = names.find((n) => n.endsWith('.mol'));
  const mol = molName ? (files.get(molName)?.toString('utf8') ?? '') : null;
  facts.set('14.legacy ZIP', {
    suggestedFilename: download.suggestedFilename(),
    entries: names,
    smilesTxt: smilesFile,
    molEntry: molName ?? null,
    molBytes: mol?.length ?? 0,
    molIsTheEdit: mol !== null ? mol.replace(/\r\n/g, '\n').trim() === (beforeDownload ?? '').replace(/\r\n/g, '\n').trim() : false,
    molIsTheOriginalLoad: mol !== null ? mol.replace(/\r\n/g, '\n').trim() === (molfile ?? '').replace(/\r\n/g, '\n').trim() : false,
    molFirstLines: mol?.split('\n').slice(0, 6) ?? null,
  });
  await page.waitForLoadState('load');
  await page.waitForTimeout(3000);
  await shot(page, 'legacy', 'l20-after-download', { fullPage: true });
  facts.set('14.after the download the page reloaded all results (page navigations)', page.url());

  // 390 px on the results page.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(500);
  facts.set('defects.sideways scroll at 390 px, results page (px)', await overflowX(page));
  await shot(page, 'legacy', 'l21-results-390', { fullPage: true });
});
