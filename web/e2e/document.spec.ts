import AxeBuilder from '@axe-core/playwright';
import { expect, type Locator, type Page } from '@playwright/test';
import { test } from './returning';
import { HTTP_API_PORT } from '../playwright.config';
import { apiError, fakeApi, JOB_ID, wireJob } from './fakeApi';
import { fixtureImage, fixturePdf, horizontalOverflow, watchProblems } from './helpers';

// "Display uploaded document" (ROADMAP §6, item 8): legacy's toggle that shows the pages of the upload.
const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const SIZES = [
  { label: 'desktop', width: 1440, height: 900 },
  { label: 'phone, 390 px', width: 390, height: 844 },
];
/** What the mock serves for a page of the document (a hashed asset of src/mocks/fixtures/page.png). */
const MOCK_PAGE = /\/assets\/page-[\w-]+\.png$/;

const naturalWidth = (image: Locator) => image.evaluate((el) => (el as HTMLImageElement).naturalWidth);
const scrollState = (strip: Locator) =>
  strip.evaluate((el) => ({ left: el.scrollLeft, overflows: el.scrollWidth > el.clientWidth, width: el.getBoundingClientRect().width }));

async function audit(page: Page, context: string) {
  const { violations } = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  expect(
    violations.map((v) => `${context}: ${v.id} (${v.impact}) ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`),
  ).toEqual([]);
}

for (const size of SIZES) {
  test(`display uploaded document, ${size.label}: shut until asked, a strip of lazy pages that scrolls in its own box`, async ({ page }) => {
    test.setTimeout(90_000);
    const problems = watchProblems(page);
    const pageRequests: string[] = [];
    page.on('request', (request) => {
      if (MOCK_PAGE.test(new URL(request.url()).pathname)) pageRequests.push(request.url());
    });
    await page.setViewportSize({ width: size.width, height: size.height });
    await page.goto('./?mockStepMs=150');
    await page.locator('input[type=file]').setInputFiles(fixturePdf());
    await page.getByRole('list', { name: 'Recognised structures' }).waitFor({ timeout: 40_000 });
    await page.locator('.dc-grid[data-arriving="false"]').waitFor({ timeout: 15_000 });

    // Shut: a button named like legacy's label, and not a single page fetched.
    const toggle = page.getByRole('button', { name: 'Display uploaded document' });
    await expect(toggle).toBeVisible();
    await expect(page.getByText('8 of 20 structures · 10 of 10 pages')).toBeVisible(); // a PDF is counted against its page limit
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(page.getByRole('img', { name: /^Page \d+ of 10$/ })).toHaveCount(0);
    expect(pageRequests).toEqual([]);

    // The keyboard opens it: Enter, and Space shuts it again.
    await toggle.focus();
    await page.keyboard.press('Enter');
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    const strip = page.getByRole('region', { name: /^Uploaded document, 10 pages/ });
    await expect(strip).toBeVisible();
    await page.keyboard.press('Space');
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(strip).toBeHidden();
    await page.keyboard.press('Space');
    await expect(strip).toBeVisible();

    // Ten pages, in order, each with its own name, lazy-loaded.
    const images = strip.getByRole('img');
    await expect(images).toHaveCount(10);
    await expect(images.first()).toHaveAttribute('alt', 'Page 1 of 10');
    await expect(images.last()).toHaveAttribute('alt', 'Page 10 of 10');
    expect(await images.evaluateAll((all) => all.map((img) => img.getAttribute('loading')))).toEqual(Array(10).fill('lazy'));
    await expect.poll(() => naturalWidth(images.first())).toBeGreaterThan(0);

    // The strip scrolls inside its own box: the page itself does not scroll sideways, and the box fits the screen.
    expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
    const before = await scrollState(strip);
    expect(before.overflows).toBe(true);
    expect(before.width).toBeLessThanOrEqual(size.width);
    const box = await strip.boundingBox();
    expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(size.width);

    // The box is reachable and scrolls with the keyboard; the last page loads when it is reached.
    await strip.focus();
    await expect(strip).toBeFocused();
    for (let i = 0; i < 5; i++) await page.keyboard.press('ArrowRight');
    await expect.poll(async () => (await scrollState(strip)).left).toBeGreaterThan(before.left);
    await strip.evaluate((el) => el.scrollTo({ left: el.scrollWidth }));
    await expect.poll(() => naturalWidth(images.last())).toBeGreaterThan(0);
    expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);

    await audit(page, `document open, ${size.label}`);

    // Shut again: the pages are taken out of the page.
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(page.getByRole('img', { name: /^Page \d+ of 10$/ })).toHaveCount(0);
    expect(problems).toEqual([]);
  });
}

test('a PDF with no structures still offers the uploaded document, so one can see why', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('./?mockStepMs=150');
  await page.locator('input[type=file]').setInputFiles(fixturePdf('empty.pdf'));
  await expect(page.getByRole('heading', { level: 1, name: 'No structures were detected' })).toBeVisible({ timeout: 30_000 });
  const toggle = page.getByRole('button', { name: 'Display uploaded document' });
  await toggle.click();
  await expect(page.getByRole('img', { name: 'Page 1 of 10' })).toBeVisible();
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
  await audit(page, 'no structures, document open');
});

// Legacy shows the document for a PDF only (its image list is empty for images): an image is its own picture.
test('an image upload has no uploaded-document toggle, with structures or without', async ({ page }) => {
  await page.goto('./?mockStepMs=150');
  await page.locator('input[type=file]').setInputFiles(fixtureImage('figure2.png'));
  await expect(page.getByRole('list', { name: 'Recognised structures' })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('button', { name: 'Display uploaded document' })).toHaveCount(0);

  await page.goto('./?mockStepMs=150');
  await page.locator('input[type=file]').setInputFiles(fixtureImage('empty.png'));
  await expect(page.getByRole('heading', { level: 1, name: 'No structures were detected' })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('button', { name: 'Display uploaded document' })).toHaveCount(0);
});

test.describe('against the job API (VITE_API=http)', () => {
  test.use({ baseURL: `http://127.0.0.1:${HTTP_API_PORT}/` });

  test('the pages come from the media URLs the job lists; a page the worker skipped says so', async ({ page }) => {
    test.setTimeout(60_000);
    const pageUrls = [0, 1, 2].map((i) => `/api/jobs/${JOB_ID}/media/page-${i}.png`);
    const seen = await fakeApi(page, async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (route.request().method() === 'POST' && path === '/api/jobs') {
        await route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify(wireJob('queued')) });
        return true;
      }
      if (route.request().method() === 'GET' && path === `/api/jobs/${JOB_ID}`) {
        const job = { ...wireJob('done'), pages: 3, pagesProcessed: 3, pageUrls };
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(job) });
        return true;
      }
      // Page 2 failed to render in the worker, so it has no file.
      if (path === pageUrls[1]) {
        await route.fulfill(apiError(404, 'not_found', 'Not found. The job may have expired.'));
        return true;
      }
      return false;
    });
    await page.goto('./');
    await page.locator('input[type=file]').setInputFiles(fixturePdf());
    await expect(page.getByRole('list', { name: 'Recognised structures' })).toBeVisible({ timeout: 30_000 });
    const pagePaths = () => seen.requests.filter((r) => r.path.includes('/media/page-')).map((r) => r.path);
    expect(pagePaths()).toEqual([]); // shut: nothing fetched

    await page.getByRole('button', { name: 'Display uploaded document' }).click();
    const strip = page.getByRole('region', { name: /^Uploaded document, 3 pages/ });
    await expect(strip.getByRole('img', { name: 'Page 1 of 3' })).toBeVisible();
    await expect.poll(() => naturalWidth(strip.getByRole('img', { name: 'Page 1 of 3' }))).toBeGreaterThan(0);
    await expect(strip.getByText('Page 2 could not be shown.')).toBeVisible();
    await expect(strip.getByRole('img', { name: 'Page 3 of 3' })).toBeVisible();
    expect(new Set(pagePaths())).toEqual(new Set(pageUrls));
  });
});
